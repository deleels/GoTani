from django.utils.translation import gettext_lazy as _l
from django.utils.translation import gettext as _
from django.conf import settings
from django.utils.http import url_has_allowed_host_and_scheme
from django.contrib import messages
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.contrib.auth import views as auth_views
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.views import View

from farm.access import RoleRequiredMixin, get_roles
from farm.bootstrap import ROLE_MANAGER, ROLE_OWNER, ROLES
from farm.views_common import error_text

from . import services
from .models import AuditLog
from .forms import FarmProfileForm, OwnProfileForm, StaffCreateForm, StaffPasswordForm
from .notifications import mark_seen, notifications_payload
from .pagination import paginate


class StaffView(RoleRequiredMixin, View):
    allowed_roles = (ROLE_OWNER,)
    template_name = "core/staff.html"

    def get_context(self, request, form=None):
        User = get_user_model()
        tab = request.GET.get("tab", "active")
        qs = User.objects.prefetch_related("groups").order_by("username")
        qs = qs.filter(is_active=(tab != "inactive"))
        q = request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q))
        page = paginate(request, qs)
        for u in page.object_list:
            u.role = services.user_role(u)
        all_users = User.objects.all()
        return {
            "page": page, "tab": tab, "q": q, "roles_list": ROLES,
            "form": form or StaffCreateForm(), "sheet_open": form is not None and form.errors,
            "stats": {
                "active": all_users.filter(is_active=True).count(),
                "inactive": all_users.filter(is_active=False).count(),
                "pending": all_users.filter(profile__must_change_password=True, is_active=True).count(),
            },
        }

    def get(self, request):
        return render(request, self.template_name, self.get_context(request))

    def post(self, request):
        form = StaffCreateForm(request.POST)
        if form.is_valid():
            try:
                user = services.create_staff_user(request.user, **form.cleaned_data)
            except ValidationError as exc:
                form.add_error(None, error_text(exc))
            else:
                messages.success(request, _("Akun %(username)s dibuat. Berikan kata sandi sementara kepadanya.") % {"username": user.username})
                return redirect("core:staff")
        return render(request, self.template_name, self.get_context(request, form), status=400)


class StaffActionView(RoleRequiredMixin, View):
    allowed_roles = (ROLE_OWNER,)
    http_method_names = ["post"]

    def post(self, request, pk, action):
        user = get_object_or_404(get_user_model(), pk=pk)
        try:
            if action == "role":
                services.set_staff_role(request.user, user, request.POST.get("role"))
                msg = _("Peran %(user)s diperbarui.") % {"user": user.username}
            elif action in ("activate", "deactivate"):
                services.set_staff_active(request.user, user, action == "activate")
                msg = (_("Akun %(user)s diaktifkan.") if action == "activate" else _("Akun %(user)s dinonaktifkan.")) % {"user": user.username}
            elif action == "password":
                form = StaffPasswordForm(request.POST)
                if not form.is_valid():
                    raise ValidationError(_("Kata sandi sementara wajib diisi."))
                services.reset_staff_password(request.user, user, form.cleaned_data["temp_password"])
                msg = _("Kata sandi sementara %(user)s diatur ulang.") % {"user": user.username}
            else:
                raise ValidationError(_("Aksi tidak dikenal."))
        except ValidationError as exc:
            messages.error(request, error_text(exc))
        else:
            messages.success(request, msg)
        return redirect(request.POST.get("next") or reverse("core:staff"))


class SettingsView(RoleRequiredMixin, View):
    allowed_roles = (ROLE_OWNER,)
    template_name = "core/settings.html"

    def get(self, request):
        return render(request, self.template_name, {"form": FarmProfileForm(instance=services.get_farm_profile())})

    def post(self, request):
        profile = services.get_farm_profile()
        form = FarmProfileForm(request.POST, request.FILES, instance=profile)
        if form.is_valid():
            try:
                services.update_farm_profile(request.user, **{f: form.cleaned_data[f] for f in form.Meta.fields})
            except ValidationError as exc:
                form.add_error(None, error_text(exc))
            else:
                messages.success(request, _("Profil kebun disimpan."))
                return redirect("core:settings")
        return render(request, self.template_name, {"form": form}, status=400)


class PasswordChangeView(auth_views.PasswordChangeView):
    template_name = "core/password_change.html"
    success_url = reverse_lazy("home")

    def form_valid(self, form):
        response = super().form_valid(form)
        services.mark_password_changed(self.request.user)
        update_session_auth_hash(self.request, form.user)
        messages.success(self.request, _("Kata sandi berhasil diganti."))
        return response

    def get_form(self, form_class=None):
        # Indonesian labels regardless of the installed Django locale files (Phase 5b).
        form = super().get_form(form_class)
        labels = {"old_password": _("Sandi lama"), "new_password1": _("Sandi baru"), "new_password2": _("Ulangi sandi baru")}
        for name, label in labels.items():
            if name in form.fields:
                form.fields[name].label = label
        if "new_password1" in form.fields:
            form.fields["new_password1"].help_text = _("Minimal 8 karakter, tidak mirip nama pengguna, tidak umum, dan tidak hanya angka.")
        if "new_password2" in form.fields:
            form.fields["new_password2"].help_text = ""
        return form

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        profile = getattr(self.request.user, "profile", None) if hasattr(self.request.user, "profile") else None
        ctx["forced"] = bool(profile and profile.must_change_password)
        return ctx


class NotificationsView(View):
    """GET: live notifications as JSON (polled every 60 s); POST: mark all as seen (D64)."""

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({"items": [], "unread": 0}, status=401)
        return super().dispatch(request, *args, **kwargs)

    def get(self, request):
        return JsonResponse(notifications_payload(request.user, get_roles(request)))

    def post(self, request):
        mark_seen(request.user)
        return JsonResponse({"ok": True, "unread": 0})


class ProfileView(View):
    """Profil: any signed-in user sees their account and edits their own name."""

    template_name = "core/profile.html"

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect(f"{reverse('login')}?next={request.path}")
        return super().dispatch(request, *args, **kwargs)

    def context(self, request, form=None):
        user = request.user
        lang = services.get_user_profile(user).language or settings.LANGUAGE_CODE
        return {"form": form or OwnProfileForm(initial={"first_name": user.first_name, "last_name": user.last_name,
                                                        "language": lang}),
                "role": services.user_role(user) or ("OWNER" if user.is_superuser else "-")}

    def get(self, request):
        return render(request, self.template_name, self.context(request))

    def post(self, request):
        form = OwnProfileForm(request.POST)
        if form.is_valid():
            try:
                data = dict(form.cleaned_data)
                language = data.pop("language")
                services.update_own_profile(request.user, **data)
                services.set_user_language(request.user, language)
            except ValidationError as exc:
                form.add_error(None, error_text(exc))
            else:
                messages.success(request, _("Profil disimpan."))
                return redirect("core:profile")
        return render(request, self.template_name, self.context(request, form), status=400)


class SetLanguageView(View):
    """POST language=en|id: saves it on the profile (signed in) and in the language cookie, then goes back."""

    def post(self, request):
        lang = request.POST.get("language", "")
        codes = {code for code, _name in settings.LANGUAGES}
        if lang not in codes:
            return redirect(request.POST.get("next") or "home")
        if request.user.is_authenticated:
            services.set_user_language(request.user, lang)
        nxt = request.POST.get("next") or reverse("home")
        if not url_has_allowed_host_and_scheme(nxt, allowed_hosts={request.get_host()}, require_https=request.is_secure()):
            nxt = reverse("home")
        response = redirect(nxt)
        response.set_cookie(settings.LANGUAGE_COOKIE_NAME, lang, max_age=365 * 24 * 3600, samesite="Lax")
        return response


class AuditLogView(RoleRequiredMixin, View):
    """"Log aktivitas" (D82): read-only, newest first, filter by kind of change and search."""

    allowed_roles = (ROLE_OWNER, ROLE_MANAGER)
    ACTIONS = {
        "PRICE_CHANGE": _l("Harga"), "POS_STEP_CHANGE": _l("Langkah kasir"), "PKP_CHANGE": _l("Status PKP"),
        "PRODUCT_ADMIN_EDIT": _l("Produk (admin)"), "STAGE_ADVANCE": _l("Tahap maju"), "STAGE_UNDO": _l("Tahap dibatalkan"),
        "SPOILAGE_REVERSAL": _l("Susut dibatalkan"), "ALLOCATION_REMOVED": _l("Alokasi dilepas"),
        "SHIFT_CLOSE": _l("Tutup shift"), "SHIFT_REVIEW": _l("Tinjau selisih kas"), "PAYMENT_VOID": _l("Pembayaran dibatalkan"),
        "STOCK_COUNT_APPROVED": _l("Opname disetujui"), "STOCK_COUNT_REJECTED": _l("Opname ditolak"),
    }

    FIELDS = {"selling_price_idr": _l("harga/kg"), "pos_step_kg": _l("langkah (kg)"), "is_pkp": "PKP", "tax_id": _l("pajak"),
              "stage": _l("tahap"), "qty_kg": "kg", "amount_idr": _l("jumlah (Rp)"), "payment_id": _l("pembayaran #"),
              "expected_idr": _l("seharusnya (Rp)"), "counted_idr": _l("dihitung (Rp)"), "difference_idr": _l("selisih (Rp)"),
              "system_kg": _l("stok sistem (kg)"), "counted_kg": _l("stok fisik (kg)"), "written_off_kg": _l("dihapus (kg)"),
              "is_active": _l("aktif")}

    def get(self, request):
        entries = AuditLog.objects.select_related("actor")
        action = request.GET.get("action", "")
        if action:
            entries = entries.filter(action=action)
        q = request.GET.get("q", "").strip()
        if q:
            entries = entries.filter(Q(summary__icontains=q) | Q(reason__icontains=q) | Q(actor__username__icontains=q))
        page = paginate(request, entries)
        for e in page.object_list:
            e.action_label = self.ACTIONS.get(e.action, e.action)
            keys = list((e.after or {}).keys() or (e.before or {}).keys())
            e.changes = [(self.FIELDS.get(k, k), _show((e.before or {}).get(k)), _show((e.after or {}).get(k)))
                         for k in keys]
        return render(request, "core/audit_log.html", {
            "page": page, "q": q, "action": action, "actions": sorted(self.ACTIONS.items(), key=lambda kv: kv[1]),
        })


def _show(value):
    """Audit values for display: numbers in Indonesian format, booleans as Ya/Tidak."""
    from decimal import Decimal, InvalidOperation

    from .numbers import format_id_decimal

    if isinstance(value, bool):
        return _("Ya") if value else _("Tidak")
    if isinstance(value, str):
        try:
            return format_id_decimal(Decimal(value), 3)
        except InvalidOperation:
            return value
    return value
