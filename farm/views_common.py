"""Thin-view building blocks shared by every app: call a service, show its
ValidationError in Bahasa Indonesia, redirect (POST/redirect/GET)."""

from django.utils.translation import gettext_lazy as _l
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views import View
from django.views.generic import FormView

from .access import RoleRequiredMixin


def error_text(exc: ValidationError) -> str:
    return " ".join(exc.messages)


def safe_next(request, fallback: str) -> str:
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback


class ActionView(RoleRequiredMixin, View):
    """POST-only button action on one object (approve, void, advance stage …)."""

    http_method_names = ["post"]
    model = None
    success_url_name = "home"
    success_message = _l("Berhasil.")

    def perform(self, obj):  # pragma: no cover - abstract
        raise NotImplementedError

    def get_success_url(self, obj, result):
        return reverse(self.success_url_name)

    def post(self, request, *args, **kwargs):
        obj = get_object_or_404(self.model, pk=kwargs["pk"]) if self.model else None
        result = None
        try:
            result = self.perform(obj)
        except ValidationError as exc:
            messages.error(request, error_text(exc))
        else:
            messages.success(request, self.success_message)
        return redirect(safe_next(request, self.get_success_url(obj, result)))


class ServiceFormView(RoleRequiredMixin, FormView):
    """FormView whose `perform(form)` calls a service; domain errors become form errors."""

    success_url_name = "home"
    success_message = _l("Tersimpan.")
    nav = ""

    def perform(self, form):  # pragma: no cover - abstract
        raise NotImplementedError

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["roles"] = self.roles
        return kwargs

    def get_success_url(self):
        return reverse(self.success_url_name)

    def form_valid(self, form):
        try:
            self.result = self.perform(form)
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)
        messages.success(self.request, self.success_message)
        return redirect(self.get_success_url())

    def get_context_data(self, **kwargs):
        kwargs.setdefault("nav", self.nav)
        return super().get_context_data(**kwargs)
