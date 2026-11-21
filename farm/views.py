"""Farm operations screens (thin views over `farm.services` / `inventory.services`).

WORKER may run field operations but never sees money (D27): cost columns are
hidden in templates via `roles.finance`, and money pages are FINANCE_ROLES only.
"""

from django.utils.translation import gettext as _, gettext_lazy as _l
from datetime import datetime, timedelta
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from core.pagination import paginate
from inventory import services as inventory_services
from inventory.models import Product, SpoilageRecord, StockLot

from . import services

from .access import FARM_ROLES, FINANCE_ROLES, RoleRequiredMixin
from .forms import (
    ActivityForm,
    ElementEditForm,
    ElementForm,
    InputItemForm,
    MultiActivityForm,
    ActivityEditForm,
    VarietyForm,
    EstimatesForm,
    PreHarvestForm,
    ExpenseForm,
    HarvestForm,
    PriceForm,
    PurchaseForm,
    SpoilageForm,
    StageForm,
    StartCycleForm,
)
from .models import (
    ActivityLog,
    CropCycle,
    Expense,
    FarmElement,
    HarvestBatch,
    InputItem,
    InputPurchase,
    PreHarvestCheck,
    PreHarvestChecklistItem,
    Variety,
)
from .views_common import ActionView, ServiceFormView, error_text, safe_next


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
    success_message = _l("Siklus tanam dimulai.")

    def perform(self, form):
        d = form.cleaned_data
        return services.start_cycle(
            d["element"], d["variety"], d["planting_start_date"], planted_qty=d["planted_qty"],
            expected_harvest_date=d["expected_harvest_date"], expected_yield_kg=d["expected_yield_kg"],
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        active = {
            c.element_id: c
            for c in CropCycle.objects.filter(is_active=True).select_related("variety")
        }
        elements = list(FarmElement.objects.order_by("-is_active", "name"))
        today = timezone.localdate()
        for element in elements:
            cycle = active.get(element.pk)
            element.active_cycle = cycle
            if cycle:
                cycle.days = cycle.days_growing(today)
                cycle.overdue = cycle.is_harvest_overdue(today)
                cycle.panel = stage_panel(cycle, self.roles.finance)
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
    success_message = _l("Elemen kebun ditambahkan.")
    extra_context = {"page_title": _l("Tambah elemen kebun"), "back_url": "farm:cycles",
                     "page_help": _l("Elemen adalah satu bedengan, bak hidroponik atau area tanam. Luasnya dipakai untuk hasil per m² dan untuk membagi biaya.")}

    def perform(self, form):
        return FarmElement.objects.create(**form.cleaned_data)


class ElementEditView(FarmPage):
    """Name, area and (without an active cycle) type; audited (D124)."""

    template_name = "farm/form_page.html"
    form_class = ElementEditForm
    allowed_roles = FINANCE_ROLES
    tab = "cycles"
    success_message = _l("Elemen kebun diperbarui.")

    def dispatch(self, request, *args, **kwargs):
        self.element = get_object_or_404(FarmElement, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        el = self.element
        return {"name": el.name, "element_type": el.element_type, "area_m2": el.area_m2}

    def get_success_url(self):
        return reverse("farm:element_detail", args=[self.element.pk])

    def get_context_data(self, **kwargs):
        kwargs.update(page_title=_("Ubah elemen %(name)s") % {"name": self.element.name},
                      back_href=self.get_success_url(),
                      page_help=_("Jenis elemen hanya bisa diubah saat tidak ada siklus tanam aktif. Setiap perubahan tercatat di Log aktivitas."))
        return super().get_context_data(**kwargs)

    def perform(self, form):
        d = form.cleaned_data
        return services.update_element(self.element, name=d["name"], area_m2=d["area_m2"],
                                       element_type=d["element_type"], user=self.request.user)


class VarietyFormView(FarmPage):
    """New variety (from the start-cycle form) or a fix to an existing one (from Pengaturan), D124."""

    template_name = "farm/form_page.html"
    form_class = VarietyForm
    allowed_roles = FINANCE_ROLES
    tab = "cycles"

    def dispatch(self, request, *args, **kwargs):
        self.variety = get_object_or_404(Variety, pk=kwargs["pk"]) if "pk" in kwargs else None
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        v = self.variety
        return {"name": v.name, "crop": v.crop, "days_to_harvest": v.days_to_harvest, "kc": v.kc} if v else {}

    def get_success_url(self):
        return safe_next(self.request, reverse("farm:cycles"))

    @property
    def success_message(self):
        return _("Varietas diperbarui.") if self.variety else _("Varietas ditambahkan.")

    def get_context_data(self, **kwargs):
        title = _("Ubah varietas %(name)s") % {"name": self.variety.name} if self.variety else _("Varietas baru")
        kwargs.update(page_title=title, back_href=self.get_success_url(), next_url=self.get_success_url(),
                      page_help=_("Umur panen mengisi perkiraan tanggal panen siklus baru. Kc dipakai untuk kebutuhan air di Cuaca & Satelit."))
        return super().get_context_data(**kwargs)

    def perform(self, form):
        d = form.cleaned_data
        return services.save_variety(self.variety, name=d["name"], crop=d["crop"], days_to_harvest=d["days_to_harvest"],
                                     kc=d["kc"], user=self.request.user)


class ElementToggleView(ActionView):
    allowed_roles = FINANCE_ROLES
    model = FarmElement
    success_url_name = "farm:cycles"
    success_message = _l("Status elemen diperbarui.")

    def perform(self, element):
        return services.set_element_active(element, not element.is_active)


class UndoStageView(ActionView):
    """Same-day undo of the last stage advance, with a reason (D84)."""

    allowed_roles = FINANCE_ROLES
    model = CropCycle
    success_url_name = "farm:cycles"
    success_message = _l("Perpindahan tahap dibatalkan.")

    def get_success_url(self, cycle, result):
        return reverse("farm:cycle_detail", args=[cycle.pk])

    def perform(self, cycle):
        return services.undo_last_advance(cycle, self.request.user, self.request.POST.get("reason", ""))


def stage_panel(cycle, finance: bool):
    """Data for the Majukan tahap form + dialog (D84): forward stages, check result, write-off, undo."""
    stages = [{"value": v, "label": str(lbl)} for v, lbl in CropCycle.Stage.choices
              if CropCycle.STAGE_ORDER.index(v) > cycle.stage_index] if cycle.is_active else []
    check = services.latest_preharvest_check(cycle)
    panel = {
        "dom_id": f"stage-{cycle.pk}", "cycle": str(cycle), "current": cycle.get_stage_display(), "stages": stages,
        "check_pct": None if check is None else float(check.completed_pct),
        "harvesting_index": CropCycle.STAGE_ORDER.index(CropCycle.Stage.HARVESTING),
        "orders": {v: CropCycle.STAGE_ORDER.index(v) for v in CropCycle.STAGE_ORDER},
        "writeoff": float(services.unabsorbed_cycle_cost(cycle)) if finance else None,
        "undo": None,
    }
    if finance:
        entry, blocked = services.stage_undo_block(cycle)
        if entry is not None and entry.action == "STAGE_ADVANCE" and not blocked:
            panel["undo"] = {"from": CropCycle.Stage(entry.after["stage"]).label,
                             "to": CropCycle.Stage(entry.before["stage"]).label,
                             "reopens": entry.after["stage"] == CropCycle.Stage.CLEARING}
    return panel


class AdvanceStageView(ActionView):
    allowed_roles = FARM_ROLES
    model = CropCycle
    success_url_name = "farm:cycles"
    success_message = _l("Tahap siklus diperbarui.")

    def perform(self, cycle):
        form = StageForm(self.request.POST)
        if not form.is_valid():
            raise ValidationError(_("Tahap tidak valid."))
        warning = services.harvest_readiness_warning(cycle, form.cleaned_data["stage"])
        result = services.advance_cycle_stage(cycle, form.cleaned_data["stage"], actor=self.request.user,
                                              allow_skip=bool(self.request.POST.get("skip")))
        if warning:
            messages.warning(self.request, warning)  # rule 14: warn, never block
        return result


# --- Activities (rule 11) ------------------------------------------------------


REPORT_TABS = [
    # key, label, ActivityType or None
    ("pemupukan", _l("Pemupukan"), ActivityLog.ActivityType.FERTILIZER),
    ("pestisida", _l("Pestisida"), ActivityLog.ActivityType.PESTICIDE),
    ("irigasi", _l("Irigasi"), ActivityLog.ActivityType.IRRIGATION),
    ("scouting", _l("Scouting"), ActivityLog.ActivityType.SCOUTING),
    ("penanaman", _l("Penanaman"), ActivityLog.ActivityType.SEEDING),
    ("lainnya", _l("Lainnya"), ActivityLog.ActivityType.OTHER),
    ("prapanen", _l("Pra-panen"), None),
    ("panen", _l("Panen"), None),
    ("susut", _l("Susut"), None),
]
TAB_TYPES = {key: t for key, _, t in REPORT_TABS if t}

# D128: the "Laporan baru" picker, grouped, with one line per report type.
REPORT_GROUPS = [
    (_l("Perawatan"), [
        ("pemupukan", _l("Pupuk yang diberikan; memotong stok input.")),
        ("pestisida", _l("Penyemprotan; memotong stok input, cek waktu semprot.")),
        ("irigasi", _l("Volume air dan durasi penyiraman.")),
        ("penanaman", _l("Benih yang ditanam; memotong stok benih.")),
    ]),
    (_l("Pemantauan"), [
        ("scouting", _l("Hama, penyakit dan kondisi tanaman, dengan foto.")),
        ("prapanen", _l("Daftar periksa sebelum panen.")),
    ]),
    (_l("Panen & Lainnya"), [
        ("panen", _l("Hasil grade A dan B; menjadi lot stok.")),
        ("susut", _l("Produk busuk, rusak atau menyusut dari lot.")),
        ("lainnya", _l("Pekerjaan lain: penyiangan, pemangkasan, pasang ajir.")),
    ]),
]


def report_picker(cycle_id=None):
    """Picker groups with links; `cycle_id` presets the cycle (cycle page)."""
    labels = {k: label for k, label, _ in REPORT_TABS}
    extra = f"&cycle={cycle_id}" if cycle_id else ""
    q = f"?cycle={cycle_id}" if cycle_id else ""
    special = {"panen": reverse("farm:harvests") + q, "susut": reverse("farm:spoilage"),
               "prapanen": reverse("farm:preharvest_new") + q}
    return [
        {"label": group, "items": [
            {"label": labels[key], "help": help_text,
             "url": special.get(key) or f"{reverse('farm:activities')}?tab={key}&new=1{extra}"}
            for key, help_text in items
        ]}
        for group, items in REPORT_GROUPS
    ]


class ActivitiesView(FarmPage):
    """Laporan Kerja (D62): one tab per report type; "Laporan Baru" opens the per-type form in a sheet."""

    template_name = "farm/activities.html"
    form_class = ActivityForm
    tab = "activities"
    success_message = _l("Laporan kerja dicatat.")

    def current_tab(self):
        tab = self.request.GET.get("tab") or self.request.POST.get("tab") or "pemupukan"
        return tab if tab in dict((k, l) for k, l, _ in REPORT_TABS) else "pemupukan"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["activity_type"] = TAB_TYPES.get(self.current_tab())
        return kwargs

    def get_initial(self):
        return {"cycle": self.request.GET.get("cycle")}

    def get_success_url(self):
        return f"{reverse('farm:activities')}?tab={self.current_tab()}"

    def perform(self, form):
        d = form.cleaned_data
        return services.log_activity(
            d["cycle"], d["activity_type"], quantity=d.get("quantity") or 0, unit=d.get("unit") or "",
            input_item=d.get("input_item"), application_method=d.get("application_method") or "",
            notes=d.get("notes") or "", user=self.request.user,
            duration_minutes=d.get("duration_minutes"), photo=d.get("photo"), logged_at=d.get("logged_at"),
            task_name=d.get("task_name") or "", worker_count=d.get("worker_count"),
            finding=d.get("finding") or "", severity=d.get("severity") or "",
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        tab = self.current_tab()
        ctx.update(multi_tabs=[k for k, t in TAB_TYPES.items() if t in services.MULTI_TYPES],
                   report_groups=report_picker(), report_tab=tab, report_tabs=REPORT_TABS, tabs=[(k, l) for k, l, _ in REPORT_TABS], tab_key=tab,
                   report_label=next((l for k, l, _ in REPORT_TABS if k == tab), ""),
                   sheet_open=self.request.method == "POST" or self.request.GET.get("new") == "1")
        week_ago = timezone.now() - timedelta(days=7)
        counts = dict(ActivityLog.objects.filter(logged_at__gte=week_ago).values_list("activity_type")
                      .annotate(n=Count("id")).values_list("activity_type", "n"))
        ctx["week_counts"] = {
            "prapanen": PreHarvestCheck.objects.filter(recorded_at__gte=week_ago).count(),
            "panen": HarvestBatch.objects.filter(harvested_at__gte=week_ago).count(),
            "susut": SpoilageRecord.objects.filter(recorded_at__gte=week_ago).count(),
            **{k: counts.get(t, 0) for k, t in TAB_TYPES.items()},
        }
        if tab in TAB_TYPES:
            qs = ActivityLog.objects.filter(activity_type=TAB_TYPES[tab]).select_related(
                "cycle__element", "cycle__variety", "input_item", "recorded_by")
        elif tab == "prapanen":
            qs = PreHarvestCheck.objects.select_related("cycle__element", "cycle__variety", "recorded_by").prefetch_related("checked_items", "failed_items")
            ctx["preharvest_form"] = PreHarvestForm()
        elif tab == "panen":
            qs = HarvestBatch.objects.select_related("cycle__element", "cycle__variety", "recorded_by").order_by("-harvested_at", "-id")
        else:
            qs = SpoilageRecord.objects.select_related("lot__product__variety", "recorded_by")
        ctx["page"] = paginate(self.request, qs)
        ctx["activities"] = ctx["page"].object_list
        return ctx


class MultiActivityView(FarmPage):
    """D131: one report for several cycles (Pemupukan, Pestisida, Irigasi, Penanaman, Lainnya)."""

    template_name = "farm/activity_multi.html"
    form_class = MultiActivityForm
    success_message = _l("Laporan untuk beberapa siklus dicatat.")

    def dispatch(self, request, *args, **kwargs):
        self.key = request.GET.get("tab") or request.POST.get("tab") or "pemupukan"
        self.activity_type = TAB_TYPES.get(self.key)
        if self.activity_type not in services.MULTI_TYPES:
            return redirect(f"{reverse('farm:activities')}?tab={self.key}")
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["activity_type"] = self.activity_type
        return kwargs

    def get_success_url(self):
        return f"{reverse('farm:activities')}?tab={self.key}"

    def get_context_data(self, **kwargs):
        label = next(str(l) for k, l, _t in REPORT_TABS if k == self.key)
        kwargs.update(key=self.key, page_title=_("%(type)s untuk beberapa siklus") % {"type": label})
        return super().get_context_data(**kwargs)

    def perform(self, form):
        d = form.cleaned_data
        acts = services.log_activities_multi(
            self.activity_type, d["rows"], input_item=d.get("input_item"), unit=d["unit"],
            application_method=d.get("application_method") or "", notes=d.get("notes") or "",
            logged_at=d.get("logged_at"), task_name=d.get("task_name") or "", user=self.request.user)
        self.success_message = _("%(n)s laporan dicatat.") % {"n": len(acts)}
        return acts


class PreHarvestCheckView(FarmPage):
    template_name = "farm/form_page.html"
    form_class = PreHarvestForm
    success_message = _l("Cek pra-panen dicatat.")
    extra_context = {"page_title": _l("Cek pra-panen"), "back_url": "farm:activities", "back_query": "?tab=prapanen",
                     "page_help": _l("Jawab setiap butir Ya atau Tidak sebelum panen. Persentase Ya dicatat bersama siklusnya; jawaban Tidak tampil merah.")}

    def get_initial(self):
        return {"cycle": self.request.GET.get("cycle")}

    def get_success_url(self):
        return f"{reverse('farm:activities')}?tab=prapanen"

    def perform(self, form):
        d = form.cleaned_data
        check = services.record_preharvest_check(d["cycle"], d["items"], failed_items=d["failed_items"],
                                                 user=self.request.user, notes=d["notes"])
        self.success_message = _("Cek pra-panen dicatat (%(pct)s%% lengkap).") % {"pct": f"{check.completed_pct.normalize():f}"}
        return check


class ActivityEditView(FarmPage):
    """D132: "Ubah" next to Hapus; only fields that touch neither stock nor cost; audited."""

    template_name = "farm/form_page.html"
    form_class = ActivityEditForm
    allowed_roles = FINANCE_ROLES
    success_message = _l("Laporan diperbarui.")

    def dispatch(self, request, *args, **kwargs):
        self.activity = get_object_or_404(ActivityLog.objects.select_related("cycle__variety", "cycle__element"),
                                          pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def editable(self):
        return services.EDITABLE_ACTIVITY_FIELDS.get(self.activity.activity_type, ())

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["editable"] = self.editable()
        return kwargs

    def get_initial(self):
        return {f: getattr(self.activity, f) for f in self.editable() if f != "photo"}

    def tab_key(self):
        return next((k for k, t in TAB_TYPES.items() if t == self.activity.activity_type), "pemupukan")

    def get_success_url(self):
        return f"{reverse('farm:activities')}?tab={self.tab_key()}"

    def get_context_data(self, **kwargs):
        kwargs.update(page_title=_("Ubah laporan: %(what)s") % {"what": self.activity}, back_href=self.get_success_url(),
                      page_help=_("Jumlah, input dan siklus tidak bisa diubah karena memengaruhi stok dan biaya: hapus lalu catat ulang. Setiap perubahan tercatat di Log aktivitas."))
        return super().get_context_data(**kwargs)

    def perform(self, form):
        changes = {f: form.cleaned_data.get(f) or "" for f in self.editable() if f in form.cleaned_data}
        return services.update_activity_details(self.activity, user=self.request.user, **changes)


class ActivityDeleteView(ActionView):
    allowed_roles = FINANCE_ROLES
    model = ActivityLog
    success_url_name = "farm:activities"
    success_message = _l("Kegiatan dihapus; stok input dikembalikan.")

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
    success_message = _l("Pembelian input dicatat.")

    def perform(self, form):
        d = form.cleaned_data
        return services.record_input_purchase(
            d["item"], d["qty"], d["cost_per_unit_idr"], supplier=d["supplier"], user=self.request.user,
            payment_method=d.get("payment_method") or "CASH", receipt=d.get("receipt"),
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["items"] = InputItem.objects.all()
        ctx["input_costs"] = input_costs()
        ctx["low_count"] = sum(1 for i in ctx["items"] if i.is_low_stock)
        if self.roles.finance:
            ctx["purchases"] = InputPurchase.objects.select_related("item")[:30]
        return ctx


class InputCreateView(FarmPage):
    """New input item with an optional first purchase (D124)."""

    template_name = "farm/form_page.html"
    form_class = InputItemForm
    allowed_roles = FINANCE_ROLES
    tab = "inputs"
    success_url_name = "farm:inputs"
    success_message = _l("Input baru ditambahkan.")
    extra_context = {"page_title": _l("Input baru"), "back_url": "farm:inputs",
                     "page_help": _l("Satuan tidak bisa diubah nanti: setiap laporan kerja memakai satuan input ini. Pembelian pertama boleh dikosongkan.")}

    def perform(self, form):
        d = form.cleaned_data
        return services.create_input_item(
            name=d["name"], item_type=d["item_type"], unit=d["unit"], min_alert_qty=d["min_alert_qty"],
            first_qty=d["first_qty"], first_cost_per_unit_idr=d["first_cost_per_unit_idr"], supplier=d["supplier"],
            user=self.request.user,
        )


# --- Expenses (rules 7, 9) ---------------------------------------------------------


class ExpensesView(FarmPage):
    template_name = "farm/expenses.html"
    form_class = ExpenseForm
    allowed_roles = FINANCE_ROLES
    tab = "expenses"
    success_url_name = "farm:expenses"
    success_message = _l("Biaya dicatat dan menunggu persetujuan.")

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
        status = self.request.GET.get("status", "all")
        history = base.exclude(status=Expense.Status.PENDING)
        if status in (Expense.Status.APPROVED, Expense.Status.REJECTED):
            history = history.filter(status=status)
        q = self.request.GET.get("q", "").strip()
        if q:
            history = history.filter(Q(narration__icontains=q) | Q(vendor__icontains=q) | Q(category__name__icontains=q))
        ctx["page"] = paginate(self.request, history)
        ctx["recent"] = ctx["page"].object_list
        month_start = timezone.localdate().replace(day=1)
        approved_month = Expense.objects.filter(status=Expense.Status.APPROVED, date_incurred__gte=month_start)
        ctx.update(
            status=status, q=q, tabs=[("all", _("Semua")), ("APPROVED", _("Disetujui")), ("REJECTED", _("Ditolak"))],
            kpi_pending=ctx["pending"].aggregate(s=Sum("amount_idr"))["s"] or 0,
            kpi_month=approved_month.aggregate(s=Sum("amount_idr"))["s"] or 0,
            kpi_overhead=approved_month.filter(cycle__isnull=True).aggregate(s=Sum("amount_idr"))["s"] or 0,
        )
        return ctx


class ExpenseActionView(ActionView):
    allowed_roles = FINANCE_ROLES
    model = Expense
    success_url_name = "farm:expenses"
    action = ""
    MESSAGES = {
        "approve": _l("Biaya disetujui."),
        "reject": _l("Biaya ditolak."),
        "revert": _l("Biaya dikembalikan ke Menunggu."),
        "delete": _l("Biaya dihapus."),
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
    success_message = _l("Panen dicatat; lot stok dibuat.")

    REVIEW_FIELDS = ("cycle", "high_quality_kg", "low_quality_kg", "harvest_loss_kg", "number_of_bags",
                     "weight_per_bag_kg", "harvested_at")

    def get_initial(self):
        return {"cycle": self.request.GET.get("cycle")}

    def review_signature(self, data) -> str:
        import hashlib

        raw = "|".join(str(data.get(f) or "") for f in self.REVIEW_FIELDS)
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    def form_valid(self, form):
        """Review step (D85): the first submit shows the review; saving needs a submit of the same values."""
        sig = self.review_signature(form.cleaned_data)
        if self.request.POST.get("review_sig") != sig:
            d = form.cleaned_data
            review = services.preview_harvest(d["cycle"], d["high_quality_kg"], d["low_quality_kg"] or 0)
            return self.render_to_response(self.get_context_data(form=form, review=review, review_sig=sig))
        return super().form_valid(form)

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
        month_start = timezone.make_aware(datetime.combine(timezone.localdate().replace(day=1), datetime.min.time()))
        month = HarvestBatch.objects.filter(harvested_at__gte=month_start).aggregate(
            h=Sum("high_quality_kg"), lo=Sum("low_quality_kg"), x=Sum("harvest_loss_kg"), n=Count("id"))
        ctx["kpi"] = {"n": month["n"], "high": month["h"] or 0, "low": month["lo"] or 0, "loss": month["x"] or 0,
                      "harvesting": CropCycle.objects.filter(is_active=True, stage=CropCycle.Stage.HARVESTING).count()}
        return ctx


class HarvestDeleteView(ActionView):
    allowed_roles = FINANCE_ROLES
    model = HarvestBatch
    success_url_name = "farm:harvests"
    success_message = _l("Batch panen dihapus; biayanya kembali ke pool siklus.")

    def perform(self, batch):
        services.delete_harvest_batch(batch)


# --- Spoilage (rule 14, D14) ---------------------------------------------------------------


class ProduceStockView(RoleRequiredMixin, View):
    """Stok Hasil Panen (D125): kg per product, open lots, age of the oldest lot, low-stock flag."""

    allowed_roles = FARM_ROLES

    def get(self, request):
        from django.db.models import Min

        today = timezone.localdate()
        rows = []
        products = (Product.objects.select_related("variety")
                    .annotate(kg=Sum("lots__qty_remaining_kg", filter=Q(lots__qty_remaining_kg__gt=0)),
                              open_lots=Count("lots", filter=Q(lots__qty_remaining_kg__gt=0)),
                              oldest=Min("lots__harvested_at", filter=Q(lots__qty_remaining_kg__gt=0)))
                    .order_by("variety__name", "grade"))
        for p in products:
            p.kg = p.kg or Decimal("0")
            p.oldest_days = (today - timezone.localtime(p.oldest).date()).days if p.oldest else None
            p.low = p.min_alert_kg is not None and p.kg <= p.min_alert_kg
            rows.append(p)
        return render(request, "farm/produce.html", {
            "products": rows, "tab": "produce",
            "total_kg": sum((p.kg for p in rows), Decimal("0")),
            "low_count": sum(1 for p in rows if p.low),
        })


class SpoilageView(FarmPage):
    template_name = "farm/spoilage.html"
    form_class = SpoilageForm
    tab = "spoilage"
    success_url_name = "farm:spoilage"
    success_message = _l("Susut/rusak dicatat.")

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
        ctx["lot_remaining"] = {str(lot.pk): str(lot.qty_remaining_kg) for lot in lots}
        ctx["page"] = paginate(self.request, SpoilageRecord.objects.select_related("lot__product__variety", "recorded_by"))
        ctx["records"] = ctx["page"].object_list
        today = timezone.localdate()
        for r in ctx["records"]:
            r.can_reverse = (self.roles.finance and not r.reversed_at
                             and timezone.localtime(r.recorded_at).date() == today)
        return ctx


class SpoilageReverseView(ActionView):
    """Same-day reversal of a spoilage record with a reason (D86)."""

    allowed_roles = FINANCE_ROLES
    model = SpoilageRecord
    success_url_name = "farm:spoilage"
    success_message = _l("Catatan susut dibatalkan; stok dikembalikan ke lot.")

    def perform(self, record):
        return inventory_services.reverse_spoilage(record, self.request.user, self.request.POST.get("reason", ""))


# --- Stock opname & produce low stock (D98, D99) ---------------------------------------------


class OpnameView(RoleRequiredMixin, View):
    allowed_roles = FARM_ROLES

    def get(self, request):
        from core.pagination import paginate
        from inventory.models import StockCount

        products = list(Product.objects.select_related("variety").filter(lots__isnull=False).distinct())
        for p in products:
            p.stock = p.stock_kg
            p.low = p.min_alert_kg is not None and p.stock <= p.min_alert_kg
        page = paginate(request, StockCount.objects.select_related("product__variety", "counted_by", "decided_by"))
        return render(request, "farm/opname.html", {
            "nav": "farm", "tab": "opname", "products": products, "page": page,
            "roles": self.roles,
        })

    def post(self, request):
        from core.numbers import parse_id_decimal
        from inventory.models import StockCount

        action = request.POST.get("action", "count")
        try:
            if action == "count":
                product = get_object_or_404(Product, pk=request.POST.get("product"))
                try:
                    counted = parse_id_decimal(request.POST.get("counted_kg", ""), "kg")
                except Exception:
                    raise ValidationError(_("Stok fisik tidak valid."))
                inventory_services.record_count(product, counted, request.user, request.POST.get("note", ""))
                messages.success(request, _("Opname dicatat; menunggu persetujuan OWNER/MANAGER."))
            elif action in ("approve", "reject"):
                if not self.roles.finance:
                    raise PermissionDenied
                count = get_object_or_404(StockCount, pk=request.POST.get("count"))
                if action == "approve":
                    count = inventory_services.approve_count(count, request.user)
                    messages.success(request, _("Opname disetujui; %(written_off_kg)s kg dihapusbukukan.") % {"written_off_kg": format(count.written_off_kg.normalize(), 'f')})
                else:
                    inventory_services.reject_count(count, request.user, request.POST.get("reason", ""))
                    messages.success(request, _("Opname ditolak; stok tidak berubah."))
            else:
                raise ValidationError(_("Aksi tidak dikenal."))
        except ValidationError as exc:
            messages.error(request, error_text(exc))
        return redirect("farm:opname")


# --- Prices (D24) ----------------------------------------------------------------------------


def input_costs():
    """Average cost per input for the purchase-cost warning (D87)."""
    return {str(i.pk): {"avg": str(i.avg_cost_per_unit_idr), "name": i.name, "unit": i.unit}
            for i in InputItem.objects.all()}


class PricesView(FarmPage):
    template_name = "farm/prices.html"
    form_class = PriceForm
    allowed_roles = FINANCE_ROLES
    tab = "prices"
    success_url_name = "farm:prices"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        open_lot = Q(lots__qty_remaining_kg__gt=0)
        products = Product.objects.select_related("variety").annotate(
            open_lots=Count("lots", filter=open_lot),
            open_kg=Sum("lots__qty_remaining_kg", filter=open_lot),
            open_cost=Sum(F("lots__qty_remaining_kg") * F("lots__unit_cogs_idr"), filter=open_lot),
        )
        for p in products:  # D87: average unit COGS of what is still in stock, for the below-cost warning
            p.open_cogs = (p.open_cost / p.open_kg) if p.open_kg else None
        ctx["unpriced"] = [p for p in products if p.selling_price_idr is None]
        ctx["priced"] = [p for p in products if p.selling_price_idr is not None]
        return ctx

    def post(self, request, *args, **kwargs):
        product = get_object_or_404(Product, pk=request.POST.get("product"))
        form = PriceForm(request.POST, roles=self.roles)
        if request.POST.get("clear"):
            inventory_services.set_product_price(product, None, actor=request.user)
            messages.success(request, _("Harga %(product)s dihapus; produk disembunyikan dari POS.") % {"product": product})
        elif form.is_valid():
            try:
                inventory_services.set_product_price(product, form.cleaned_data["selling_price_idr"],
                                                     actor=request.user)
                if "min_alert_kg" in request.POST:  # D98: blank switches the alert off
                    inventory_services.set_min_alert(product, form.cleaned_data.get("min_alert_kg"), actor=request.user)
            except ValidationError as exc:
                messages.error(request, error_text(exc))
            else:
                messages.success(request, _("Harga %(product)s disimpan.") % {"product": product})
        else:
            messages.error(request, _("Harga tidak valid."))
        return redirect(reverse("farm:prices"))


# --- Detail pages (D63, D71) ---------------------------------------------------------------


def cycle_timeline(cycle, finance=False):
    """Chronological events for a cycle, reusing `forward_trace` (D71)."""
    from reports.services import forward_trace

    trace = forward_trace(cycle)
    events = [{"at": timezone.make_aware(datetime.combine(cycle.planting_start_date, datetime.min.time())),
               "kind": "start", "title": _("Mulai tanam %(variety)s") % {"variety": cycle.variety}, "body": _("di %(element)s") % {"element": cycle.element}}]
    for a in trace.activities:
        body = a.input_item.name + f" {a.quantity.normalize():f} {a.unit}" if a.input_item else (a.notes or a.application_method)
        if a.duration_minutes:
            body = f"{body} · {a.duration_minutes} menit".strip(" ·")
        events.append({"at": a.logged_at, "kind": a.activity_type.lower(), "title": a.get_activity_type_display(),
                       "body": body, "who": a.recorded_by, "photo": a.photo})
    for check in cycle.preharvest_checks.all():
        failed = [str(i) for i in check.failed_items.all()]
        body = _("%(completed_pct)s%% lengkap") % {"completed_pct": format(check.completed_pct.normalize(), 'f')}
        if failed:
            body = f"{body} · " + _("Tidak: %(lines)s") % {"lines": "; ".join(failed)}
        events.append({"at": check.recorded_at, "kind": "check", "title": _("Cek pra-panen"), "body": body,
                       "who": check.recorded_by, "danger": bool(failed)})
    for bt in trace.batches:
        b = bt.batch
        body = _("HIGH %(high)s kg · LOW %(low)s kg · susut %(loss)s kg") % {"high": f"{b.high_quality_kg.normalize():f}", "low": f"{b.low_quality_kg.normalize():f}", "loss": f"{b.harvest_loss_kg.normalize():f}"}
        events.append({"at": b.harvested_at, "kind": "harvest", "title": _("Panen #%(pk)s") % {"pk": b.pk}, "body": body,
                       "money": b.absorbed_cost_idr if finance else None, "who": b.recorded_by})
        for lt in bt.lots:
            for sp in lt.spoilages:
                events.append({"at": sp.recorded_at, "kind": "spoilage", "title": _("Susut %(product)s") % {"product": lt.lot.product},
                               "body": _("%(qty_kg)s kg · %(reason)s") % {"qty_kg": format(sp.qty_kg.normalize(), 'f'), "reason": sp.get_reason_display()},
                               "money": sp.loss_idr if finance else None})
    if cycle.closed_at:
        events.append({"at": cycle.closed_at, "kind": "close", "title": _("Siklus ditutup"), "body": ""})
    events.sort(key=lambda e: e["at"], reverse=True)
    return trace, events


class CycleDetailView(FarmPage):
    template_name = "farm/cycle_detail.html"
    form_class = EstimatesForm
    success_message = _l("Perkiraan siklus disimpan.")

    def dispatch(self, request, *args, **kwargs):
        self.cycle = get_object_or_404(CropCycle.objects.select_related("element", "variety"), pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        c = self.cycle
        return {"planted_qty": c.planted_qty, "expected_harvest_date": c.expected_harvest_date,
                "expected_yield_kg": c.expected_yield_kg}

    def get_success_url(self):
        return reverse("farm:cycle_detail", args=[self.cycle.pk])

    def perform(self, form):
        return services.update_cycle_estimates(self.cycle, **form.cleaned_data)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        c = self.cycle
        trace, events = cycle_timeline(c, finance=self.roles.finance)
        today = timezone.localdate()
        ctx["report_groups"] = report_picker(self.cycle.pk)
        ctx.update(
            cycle=c, trace=trace, events=events, yield_summary=services.cycle_yield_summary(c),
            days=c.days_growing(today), overdue=c.is_harvest_overdue(today),
            latest_check=services.latest_preharvest_check(c),
            checklist=PreHarvestChecklistItem.objects.filter(is_active=True),
            panel=stage_panel(c, self.roles.finance),
            stage_choices=CropCycle.Stage.choices, detail_tab=self.request.GET.get("tab", "timeline"),
            sheet_open=self.request.method == "POST",
        )
        if self.roles.finance:
            ctx["unabsorbed"] = services.unabsorbed_cycle_cost(c)
        return ctx


class ElementDetailView(FarmPage):
    template_name = "farm/element_detail.html"
    form_class = StartCycleForm
    success_message = _l("Siklus tanam dimulai.")

    def dispatch(self, request, *args, **kwargs):
        self.element = get_object_or_404(FarmElement, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {"element": self.element.pk}

    def get_success_url(self):
        return reverse("farm:element_detail", args=[self.element.pk])

    def perform(self, form):
        d = form.cleaned_data
        return services.start_cycle(
            self.element, d["variety"], d["planting_start_date"], planted_qty=d["planted_qty"],
            expected_harvest_date=d["expected_harvest_date"], expected_yield_kg=d["expected_yield_kg"],
        )

    def get_form(self, form_class=None):
        form = super().get_form(form_class)
        form.fields["element"].queryset = FarmElement.objects.filter(pk=self.element.pk)
        return form

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        el = self.element
        cycles = list(el.cycles.select_related("variety").order_by("-planting_start_date", "-id"))
        today = timezone.localdate()
        total_kg = Decimal("0")
        for c in cycles:
            c.summary = services.cycle_yield_summary(c)
            c.days = c.days_growing(today)
            c.kg_per_m2 = (c.summary["actual_kg"] / el.area_m2).quantize(Decimal("0.001"))
            total_kg += c.summary["actual_kg"]
        ctx.update(element=el, cycles=cycles, active_cycle=next((c for c in cycles if c.is_active), None),
                   total_kg=total_kg, sheet_open=self.request.method == "POST",
                   recent_activities=ActivityLog.objects.filter(cycle__element=el)
                   .select_related("cycle__variety", "input_item")[:10])
        try:
            from geo.models import FieldPolygon

            ctx["polygons"] = list(FieldPolygon.objects.filter(element=el))
        except (ImportError, LookupError):  # pragma: no cover - geo app not installed
            ctx["polygons"] = []
        return ctx


class InputDetailView(FarmPage):
    """Usage history ("where did this fertilizer go") + restock shortcut (D63)."""

    template_name = "farm/input_detail.html"
    form_class = PurchaseForm
    post_roles = FINANCE_ROLES
    success_message = _l("Stok input ditambah.")

    def dispatch(self, request, *args, **kwargs):
        self.item = get_object_or_404(InputItem, pk=kwargs["pk"])
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {"item": self.item.pk, "cost_per_unit_idr": self.item.avg_cost_per_unit_idr or None}

    def get_success_url(self):
        return reverse("farm:input_detail", args=[self.item.pk])

    def perform(self, form):
        d = form.cleaned_data
        return services.record_input_purchase(self.item, d["qty"], d["cost_per_unit_idr"], supplier=d["supplier"],
                                              user=self.request.user, payment_method=d.get("payment_method") or "CASH",
                                              receipt=d.get("receipt"))

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        rows = services.input_usage_history(self.item)
        rows.reverse()
        usage = [r for r in rows if r.movement_type == "USAGE"]
        by_cycle = {}
        for r in usage:
            if r.activity:
                key = r.activity.cycle
                by_cycle[key] = by_cycle.get(key, Decimal("0")) - r.qty
        ctx["input_costs"] = input_costs()
        ctx.update(item=self.item, page=paginate(self.request, rows), by_cycle=sorted(by_cycle.items(), key=lambda kv: -kv[1]),
                   used_total=-sum((r.qty for r in usage), Decimal("0")), sheet_open=self.request.method == "POST")
        if self.roles.finance:
            ctx["purchases"] = self.item.purchases.all()[:20]
        return ctx
