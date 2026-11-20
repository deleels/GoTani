"""Traceability (D29) and analytics dashboard (D28) — OWNER/MANAGER only."""

from django.utils.translation import gettext as _, gettext_lazy as _l
import csv
from datetime import timedelta

from django import forms
from django.http import HttpResponse
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import TemplateView

from farm.access import FINANCE_ROLES, RoleRequiredMixin, get_roles, landing_url_name
from farm.models import CropCycle
from pos.models import SaleOrder

from . import services


def csv_response(filename: str, rows) -> HttpResponse:
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    response.write("﻿")  # BOM so Excel opens UTF-8 correctly
    writer = csv.writer(response)
    for row in rows:
        writer.writerow(row)
    return response


class HomeView(View):
    """`/`: Ringkasan for farm roles (D70); CASHIER lands on the POS; no role → 403."""

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next=/")
        roles = get_roles(request)
        if roles.farm:
            from core.home import home_summary

            ctx = home_summary(roles)
            ctx["chart"] = {"labels": [s["stage"] for s in ctx["stages"]], "values": [s["count"] for s in ctx["stages"]]}
            return render(request, "core/home.html", ctx)
        target = landing_url_name(roles)
        if target is None:
            raise PermissionDenied(_("Akun Anda belum memiliki peran."))
        return redirect(target)


class DateRangeForm(forms.Form):
    start = forms.DateField(label=_l("Dari"), required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label=_l("Sampai"), required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        data = super().clean()
        today = timezone.localdate()
        end = data.get("end") or today
        start = data.get("start") or end - timedelta(days=29)
        if start > end:
            raise forms.ValidationError(_("Tanggal awal harus sebelum tanggal akhir."))
        if (end - start).days > 731:
            raise forms.ValidationError(_("Rentang maksimal 2 tahun."))
        data["start"], data["end"] = start, end
        return data


class DashboardView(RoleRequiredMixin, TemplateView):
    allowed_roles = FINANCE_ROLES
    template_name = "reports/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        form = DateRangeForm(self.request.GET or None)
        if form.is_bound and form.is_valid():
            start, end = form.cleaned_data["start"], form.cleaned_data["end"]
        else:
            end = timezone.localdate()
            start = end - timedelta(days=29)
            if not form.is_bound:
                form = DateRangeForm(initial={"start": start, "end": end})
        metrics = services.dashboard_metrics(start, end)
        ctx.update(nav="dashboard", form=form, m=metrics, k=metrics["kpis"], charts=self._chart_data(metrics),
                   channel_parts=[(_("Kasir (POS)"), metrics["kpis"]["revenue_pos"]), (_("Invoice"), metrics["kpis"]["revenue_invoice"])],
                   presets=self._presets(start, end))
        return ctx

    @staticmethod
    def _presets(start, end):
        """Quick ranges for the boxed range switch; the active one matches the shown range exactly."""
        today = timezone.localdate()
        ranges = [(_("7 hari"), today - timedelta(days=6), today), (_("30 hari"), today - timedelta(days=29), today),
                  (_("90 hari"), today - timedelta(days=89), today), (_("Tahun ini"), today.replace(month=1, day=1), today)]
        return [{"label": label, "start": a.isoformat(), "end": b.isoformat(), "active": (a, b) == (start, end)}
                for label, a, b in ranges]

    @staticmethod
    def _chart_data(m):
        f = float
        return {
            "daily": {
                "labels": [d["date"].strftime("%d/%m") for d in m["daily"]],
                "revenue": [f(d["revenue"]) for d in m["daily"]],
                "cogs": [f(d["cogs"]) for d in m["daily"]],
            },
            "margin": {
                "labels": [f"{r['variety']} {r['grade']}" for r in m["margin_by_product"]],
                "revenue": [f(r["revenue"]) for r in m["margin_by_product"]],
                "cogs": [f(r["cogs"]) for r in m["margin_by_product"]],
                "margin": [f(r["margin"]) for r in m["margin_by_product"]],
            },
            "yield": {
                "labels": [r["type"] for r in m["yield_by_type"]],
                "values": [f(r["kg_per_m2"]) for r in m["yield_by_type"]],
            },
            "top": {
                "labels": [f"{r['variety']} {r['grade']}" for r in m["top_products"]],
                "values": [f(r["revenue"]) for r in m["top_products"]],
            },
            "channel": {
                "labels": [_("Kasir (POS)"), _("Invoice")],
                "values": [f(m["kpis"]["revenue_pos"]), f(m["kpis"]["revenue_invoice"])],
            },
            "expenses": {
                "labels": [r["category"] for r in m["expenses_by_category"]],
                "values": [f(r["amount"]) for r in m["expenses_by_category"]],
            },
        }


class OmzetPphView(RoleRequiredMixin, TemplateView):
    """Omzet & PPh Final 0,5% per month (D103), with CSV."""

    allowed_roles = FINANCE_ROLES
    template_name = "reports/omzet_pph.html"

    def get(self, request, *args, **kwargs):
        try:
            year = int(request.GET.get("year") or timezone.localdate().year)
        except ValueError:
            year = timezone.localdate().year
        data = services.omzet_pph(year)
        if request.GET.get("format") == "csv":
            rows = [["bulan", "omzet_pos", "omzet_invoice", "omzet", "kumulatif", "dasar_pengenaan", "pph_final", "setor_paling_lambat"]]
            rows += [[f"{year}-{r['month']:02d}", r["pos"], r["invoice"], r["omzet"], r["cumulative"], r["taxable"], r["pph"],
                      r["due"].isoformat()] for r in data["rows"]]
            return csv_response(f"omzet-pph-{year}.csv", rows)
        return render(request, self.template_name, {"nav": "finance", "d": data, "years": range(year - 2, year + 2)})


class TraceIndexView(RoleRequiredMixin, TemplateView):
    allowed_roles = FINANCE_ROLES
    template_name = "reports/trace_index.html"

    def get(self, request, *args, **kwargs):
        number = request.GET.get("order", "").strip().upper()
        if number.startswith("INV-"):
            from invoicing.models import Invoice

            invoice = Invoice.objects.filter(number=number).first()
            if invoice and get_roles(request).finance:
                return redirect("invoicing:invoice_detail", pk=invoice.pk)
        if number:
            order = SaleOrder.objects.filter(order_number=number).first()
            if order:
                return redirect("reports:trace_order", order_number=order.order_number)
            kwargs["not_found"] = number
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(
            nav="trace",
            cycles=CropCycle.objects.select_related("element", "variety").order_by("-is_active", "-planting_start_date"),
            recent_orders=SaleOrder.objects.order_by("-created_at")[:15],
        )
        return ctx


class TraceCycleView(RoleRequiredMixin, TemplateView):
    allowed_roles = FINANCE_ROLES
    template_name = "reports/trace_cycle.html"

    def get(self, request, *args, **kwargs):
        cycle = get_object_or_404(CropCycle, pk=kwargs["pk"])
        trace = services.forward_trace(cycle)
        if request.GET.get("format") == "csv":
            return csv_response(f"telusur-siklus-{cycle.pk}.csv", services.forward_csv_rows(trace))
        return self.render_to_response(self.get_context_data(trace=trace, nav="trace"))


class TraceOrderView(RoleRequiredMixin, TemplateView):
    allowed_roles = FINANCE_ROLES
    template_name = "reports/trace_order.html"

    def get(self, request, *args, **kwargs):
        order = services.backward_trace(get_object_or_404(SaleOrder, order_number=kwargs["order_number"]))
        if request.GET.get("format") == "csv":
            return csv_response(f"telusur-{order.order_number}.csv", services.backward_csv_rows(order))
        return self.render_to_response(self.get_context_data(order=order, nav="trace"))
