"""POS register, checkout, receipt and void (thin views over `pos.services`)."""

from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, TemplateView

from farm.access import FINANCE_ROLES, POS_ROLES, RoleRequiredMixin
from farm.models import PaymentMethod
from farm.views_common import ActionView, error_text

from . import services
from .models import SaleOrder


def catalog_payload():
    big = services.pos_big_line_thresholds()
    return [
        {
            "id": p.pk,
            "name": p.variety.name,
            "grade": p.grade,
            "grade_label": p.get_grade_display(),
            "price": str(p.selling_price_idr),
            "available": str(p.available_kg),
            "step": str(p.pos_step_kg),
            "warn_kg": str(big.get(p.pk, services.POS_BIG_LINE_MIN_KG).quantize(Decimal("0.001"))),
        }
        for p in services.pos_catalog()
    ]


class RegisterView(RoleRequiredMixin, TemplateView):
    allowed_roles = POS_ROLES
    template_name = "pos/register.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            nav="pos",
            products=catalog_payload(),
            payment_methods=PaymentMethod.choices,
        )
        return ctx


class StockView(RoleRequiredMixin, View):
    """JSON polled by the register for live available kg."""

    allowed_roles = POS_ROLES

    def get(self, request):
        return JsonResponse({"products": catalog_payload()})


class CheckoutView(RoleRequiredMixin, View):
    """Accepts only product ids + kg (`product_id`/`qty_kg` pairs); prices are server-side."""

    allowed_roles = POS_ROLES
    http_method_names = ["post"]

    def post(self, request):
        ids, qtys = request.POST.getlist("product_id"), request.POST.getlist("qty_kg")
        if len(ids) != len(qtys):
            messages.error(request, "Isi keranjang tidak valid.")
            return redirect("pos:register")
        try:
            order = services.checkout(
                list(zip(ids, qtys)), request.POST.get("payment_method", ""), request.user
            )
        except ValidationError as exc:
            messages.error(request, error_text(exc))
            return redirect("pos:register")
        messages.success(request, f"Order {order.order_number} berhasil.")
        return redirect(f"{reverse('pos:receipt', args=[order.pk])}?new=1")


class ReceiptView(RoleRequiredMixin, DetailView):
    allowed_roles = POS_ROLES
    model = SaleOrder
    template_name = "pos/receipt.html"
    context_object_name = "order"

    def get_queryset(self):
        return SaleOrder.objects.select_related("cashier", "voided_by").prefetch_related("lines__product__variety")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["size"] = "a4" if self.request.GET.get("size") == "a4" else "58"
        ctx["is_new"] = self.request.GET.get("new") == "1"
        ctx["can_void"] = services.can_void(self.object, self.request.user)
        ctx["nav"] = "pos"
        return ctx


class OrdersView(RoleRequiredMixin, TemplateView):
    """Today's (Asia/Jakarta) orders with receipt links and same-day void."""

    allowed_roles = POS_ROLES
    template_name = "pos/orders.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = services.jakarta_date(timezone.now())
        try:
            day = date.fromisoformat(self.request.GET.get("date", "")) if self.request.GET.get("date") else today
        except ValueError:
            day = today
        day = min(day, today)
        orders = list(
            SaleOrder.objects.filter(order_number__startswith=services.order_number_prefix(day))
            .select_related("cashier")
            .order_by("-created_at", "-id")
        )
        for order in orders:
            order.voidable = services.can_void(order, self.request.user)
        done = [o for o in orders if o.status == SaleOrder.Status.COMPLETED]
        ctx.update(nav="pos", orders=orders, today=day, is_today=day == today, max_date=today,
                   kpi={"count": len(done), "gross": sum((o.total_gross_idr for o in done), 0),
                        "tax": sum((o.total_tax_idr for o in done), 0), "voided": len(orders) - len(done)})
        return ctx


class VoidView(ActionView):
    allowed_roles = FINANCE_ROLES
    model = SaleOrder
    success_url_name = "pos:orders"

    def perform(self, order):
        order = services.void_order(order, self.request.user)
        self.success_message = f"Order {order.order_number} dibatalkan; stok dikembalikan."
        return order

    def get_success_url(self, obj, result):
        return reverse("pos:receipt", args=[obj.pk])
