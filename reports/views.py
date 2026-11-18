"""Traceability (D29) and analytics dashboard (D28) — OWNER/MANAGER only."""

import csv
from datetime import timedelta

from django import forms
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect
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
    """`/`: the dashboard for OWNER/MANAGER; others go to their own landing page."""

    def get(self, request):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next=/")
        roles = get_roles(request)
        if roles.finance:
            return DashboardView.as_view()(request)
        target = landing_url_name(roles)
        if target is None:
            return DashboardView.as_view()(request)  # no role → 403 via the mixin
        return redirect(target)


class DateRangeForm(forms.Form):
    start = forms.DateField(label="Dari", required=False, widget=forms.DateInput(attrs={"type": "date"}))
    end = forms.DateField(label="Sampai", required=False, widget=forms.DateInput(attrs={"type": "date"}))

    def clean(self):
        data = super().clean()
        today = timezone.localdate()
        end = data.get("end") or today
        start = data.get("start") or end - timedelta(days=29)
        if start > end:
            raise forms.ValidationError("Tanggal awal harus sebelum tanggal akhir.")
        if (end - start).days > 731:
            raise forms.ValidationError("Rentang maksimal 2 tahun.")
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
        ctx.update(nav="dashboard", form=form, m=metrics, k=metrics["kpis"], charts=self._chart_data(metrics))
        return ctx

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
            "expenses": {
                "labels": [r["category"] for r in m["expenses_by_category"]],
                "values": [f(r["amount"]) for r in m["expenses_by_category"]],
            },
        }


class TraceIndexView(RoleRequiredMixin, TemplateView):
    allowed_roles = FINANCE_ROLES
    template_name = "reports/trace_index.html"

    def get(self, request, *args, **kwargs):
        number = request.GET.get("order", "").strip().upper()
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
