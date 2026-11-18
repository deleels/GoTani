"""Farm operations screens (thin views over `farm.services` / `inventory.services`).

WORKER may run field operations but never sees money (D27): cost columns are
hidden in templates via `roles.finance`, and money pages are FINANCE_ROLES only.
"""

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse

from core.pagination import paginate
from inventory import services as inventory_services
from inventory.models import Product, SpoilageRecord, StockLot

from . import services

from .access import FARM_ROLES, FINANCE_ROLES
from .forms import (
    ActivityForm,
    ElementForm,
    ExpenseForm,
    HarvestForm,
    PriceForm,
    PurchaseForm,
    SpoilageForm,
    StageForm,
    StartCycleForm,
)
from .models import ActivityLog, CropCycle, Expense, FarmElement, HarvestBatch, InputItem, InputPurchase
from .views_common import ActionView, ServiceFormView, error_text


class FarmPage(ServiceFormView):
    nav = "farm"
    tab = ""
    allowed_roles = FARM_ROLES

    def get_context_data(self, **kwargs):
        kwargs.setdefault("tab", self.tab)
        return super().get_context_data(**kwargs)


# --- Elements & cycles (rules 1, 8, 12) --------------------------------------


class CyclesView(FarmPage):
    template_name = "farm/cycles.html"
    form_class = StartCycleForm
    tab = "cycles"
    success_url_name = "farm:cycles"
    success_message = "Siklus tanam dimulai."

    def perform(self, form):
        d = form.cleaned_data
        return services.start_cycle(d["element"], d["variety"], d["planting_start_date"])

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        active = {
            c.element_id: c
            for c in CropCycle.objects.filter(is_active=True).select_related("variety")
        }
        elements = list(FarmElement.objects.order_by("-is_active", "name"))
        for element in elements:
            cycle = active.get(element.pk)
            element.active_cycle = cycle
            if cycle:
                cycle.next_stages = [
                    (value, label)
                    for value, label in CropCycle.Stage.choices
                    if CropCycle.STAGE_ORDER.index(value) > cycle.stage_index
                ]
        ctx["elements"] = elements
        ctx["closed_cycles"] = (
            CropCycle.objects.filter(is_active=False)
            .select_related("element", "variety")
            .annotate(n_harvests=Count("harvests"))[:20]
        )
        if self.roles.finance:
            for cycle in ctx["closed_cycles"]:
                cycle.unabsorbed = services.unabsorbed_cycle_cost(cycle)
        ctx["low_stock"] = [i for i in InputItem.objects.all() if i.is_low_stock]
        ctx["pending_expenses"] = Expense.objects.filter(status=Expense.Status.PENDING).count()
        ctx["unpriced"] = Product.objects.filter(selling_price_idr__isnull=True).count()
        return ctx


class ElementCreateView(FarmPage):
    template_name = "farm/form_page.html"
    form_class = ElementForm
    allowed_roles = FINANCE_ROLES
    tab = "cycles"
    success_url_name = "farm:cycles"
    success_message = "Elemen kebun ditambahkan."
    extra_context = {"page_title": "Tambah elemen kebun"}

    def perform(self, form):
        return FarmElement.objects.create(**form.cleaned_data)


class ElementToggleView(ActionView):
    allowed_roles = FINANCE_ROLES
    model = FarmElement
    success_url_name = "farm:cycles"
    success_message = "Status elemen diperbarui."

    def perform(self, element):
        return services.set_element_active(element, not element.is_active)


class AdvanceStageView(ActionView):
    allowed_roles = FARM_ROLES
    model = CropCycle
    success_url_name = "farm:cycles"
    success_message = "Tahap siklus diperbarui."

    def perform(self, cycle):
        form = StageForm(self.request.POST)
        if not form.is_valid():
            raise ValidationError("Tahap tidak valid.")
        return services.advance_cycle_stage(cycle, form.cleaned_data["stage"])


# --- Activities (rule 11) ------------------------------------------------------


class ActivitiesView(FarmPage):
    template_name = "farm/activities.html"
    form_class = ActivityForm
    tab = "activities"
    success_url_name = "farm:activities"
    success_message = "Kegiatan dicatat."

    def perform(self, form):
        d = form.cleaned_data
        return services.log_activity(
            d["cycle"], d["activity_type"], quantity=d["quantity"], unit=d["unit"],
            input_item=d["input_item"], application_method=d["application_method"],
            notes=d["notes"], user=self.request.user,
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["page"] = paginate(self.request, ActivityLog.objects.select_related(
            "cycle__element", "cycle__variety", "input_item", "recorded_by"
        ))
        ctx["activities"] = ctx["page"].object_list
        return ctx


class ActivityDeleteView(ActionView):
    allowed_roles = FINANCE_ROLES
    model = ActivityLog
    success_url_name = "farm:activities"
    success_message = "Kegiatan dihapus; stok input dikembalikan."

    def perform(self, activity):
        services.delete_activity(activity)


# --- Inputs (rule 11) ------------------------------------------------------------


class InputsView(FarmPage):
    """Stock + low-stock badges for everyone on the farm; purchases/costs for finance."""

    template_name = "farm/inputs.html"
    form_class = PurchaseForm
    post_roles = FINANCE_ROLES
    tab = "inputs"
    success_url_name = "farm:inputs"
    success_message = "Pembelian input dicatat."

    def perform(self, form):
        d = form.cleaned_data
        return services.record_input_purchase(
            d["item"], d["qty"], d["cost_per_unit_idr"], supplier=d["supplier"], user=self.request.user
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["items"] = InputItem.objects.all()
        if self.roles.finance:
            ctx["purchases"] = InputPurchase.objects.select_related("item")[:30]
        return ctx


# --- Expenses (rules 7, 9) ---------------------------------------------------------


class ExpensesView(FarmPage):
    template_name = "farm/expenses.html"
    form_class = ExpenseForm
    allowed_roles = FINANCE_ROLES
    tab = "expenses"
    success_url_name = "farm:expenses"
    success_message = "Biaya dicatat dan menunggu persetujuan."

    def perform(self, form):
        d = dict(form.cleaned_data)
        category, scope, amount = d.pop("category"), d.pop("scope"), d.pop("amount_idr")
        element, cycle = d.pop("element"), d.pop("cycle")
        if scope == Expense.Scope.GENERAL:
            element = cycle = None
        if not d.get("receipt"):
            d.pop("receipt", None)
        return services.create_expense(category, scope, amount, element=element, cycle=cycle, **d)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        base = Expense.objects.select_related("category", "element", "cycle__variety", "absorbed_by")
        ctx["pending"] = base.filter(status=Expense.Status.PENDING).order_by("date_incurred", "id")
        ctx["page"] = paginate(self.request, base.exclude(status=Expense.Status.PENDING))
        ctx["recent"] = ctx["page"].object_list
        return ctx


class ExpenseActionView(ActionView):
    allowed_roles = FINANCE_ROLES
    model = Expense
    success_url_name = "farm:expenses"
    action = ""
    MESSAGES = {
        "approve": "Biaya disetujui.",
        "reject": "Biaya ditolak.",
        "revert": "Biaya dikembalikan ke Menunggu.",
        "delete": "Biaya dihapus.",
    }

    def perform(self, expense):
        self.success_message = self.MESSAGES[self.action]
        user = self.request.user
        if self.action == "approve":
            return services.approve_expense(expense, user)
        if self.action == "reject":
            return services.reject_expense(expense, user)
        if self.action == "revert":
            return services.revert_expense_to_pending(expense, user)
        return services.delete_expense(expense)


# --- Harvests (rules 2–6, 10) ----------------------------------------------------------


class HarvestsView(FarmPage):
    template_name = "farm/harvests.html"
    form_class = HarvestForm
    tab = "harvests"
    success_url_name = "farm:harvests"
    success_message = "Panen dicatat; lot stok dibuat."

    def perform(self, form):
        d = form.cleaned_data
        return services.record_harvest(
            d["cycle"], d["high_quality_kg"], d["low_quality_kg"] or 0, d["harvest_loss_kg"] or 0,
            number_of_bags=d["number_of_bags"], weight_per_bag_kg=d["weight_per_bag_kg"],
            harvested_at=d["harvested_at"], user=self.request.user,
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["page"] = paginate(self.request, HarvestBatch.objects.select_related(
            "cycle__element", "cycle__variety"
        ).order_by("-harvested_at", "-id"))
        ctx["batches"] = ctx["page"].object_list
        return ctx


class HarvestDeleteView(ActionView):
    allowed_roles = FINANCE_ROLES
    model = HarvestBatch
    success_url_name = "farm:harvests"
    success_message = "Batch panen dihapus; biayanya kembali ke pool siklus."

    def perform(self, batch):
        services.delete_harvest_batch(batch)


# --- Spoilage (rule 14, D14) ---------------------------------------------------------------


class SpoilageView(FarmPage):
    template_name = "farm/spoilage.html"
    form_class = SpoilageForm
    tab = "spoilage"
    success_url_name = "farm:spoilage"
    success_message = "Susut/rusak dicatat."

    def get_initial(self):
        initial = super().get_initial()
        product = Product.objects.filter(pk=self.request.GET.get("product") or None).first()
        lot = inventory_services.oldest_open_lot(product) if product else None
        if lot is None:
            lot = self.get_form_class()().fields["lot"].queryset.first()
        if lot:
            initial["lot"] = lot.pk
        return initial

    def perform(self, form):
        d = form.cleaned_data
        return inventory_services.record_spoilage(
            d["lot"], d["qty_kg"], d["reason"], notes=d["notes"], user=self.request.user
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        lots = ctx["form"].fields["lot"].queryset
        products = {}
        for lot in lots:
            products.setdefault(lot.product_id, {"id": lot.product_id, "name": str(lot.product), "lots": []})
            products[lot.product_id]["lots"].append(lot.pk)
        ctx["lot_products"] = list(products.values())
        initial_lot = ctx["form"]["lot"].value()
        ctx["initial_product"] = next(
            (p["id"] for p in products.values() if initial_lot and int(initial_lot) in p["lots"]), None
        )
        ctx["page"] = paginate(self.request, SpoilageRecord.objects.select_related("lot__product__variety", "recorded_by"))
        ctx["records"] = ctx["page"].object_list
        return ctx


# --- Prices (D24) ----------------------------------------------------------------------------


class PricesView(FarmPage):
    template_name = "farm/prices.html"
    form_class = PriceForm
    allowed_roles = FINANCE_ROLES
    tab = "prices"
    success_url_name = "farm:prices"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        products = Product.objects.select_related("variety").annotate(
            open_lots=Count("lots", filter=Q(lots__qty_remaining_kg__gt=0))
        )
        ctx["unpriced"] = [p for p in products if p.selling_price_idr is None]
        ctx["priced"] = [p for p in products if p.selling_price_idr is not None]
        return ctx

    def post(self, request, *args, **kwargs):
        product = get_object_or_404(Product, pk=request.POST.get("product"))
        form = PriceForm(request.POST, roles=self.roles)
        if request.POST.get("clear"):
            inventory_services.set_product_price(product, None)
            messages.success(request, f"Harga {product} dihapus; produk disembunyikan dari POS.")
        elif form.is_valid():
            try:
                inventory_services.set_product_price(product, form.cleaned_data["selling_price_idr"])
            except ValidationError as exc:
                messages.error(request, error_text(exc))
            else:
                messages.success(request, f"Harga {product} disimpan.")
        else:
            messages.error(request, "Harga tidak valid.")
        return redirect(reverse("farm:prices"))
