"""Force a password change after an OWNER-issued temporary password (D65)."""

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import translation


class ForcePasswordChangeMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            profile = getattr(user, "profile", None) if hasattr(user, "profile") else None
            if profile is not None and profile.must_change_password:
                allowed = {reverse("core:password_change"), reverse("logout")}
                allowed |= {reverse("service_worker"), reverse("manifest"), reverse("offline")}
                if request.path not in allowed and not request.path.startswith(("/static/", "/media/")):
                    return redirect("core:password_change")
        return self.get_response(request)


class UserLanguageMiddleware:
    """Activate the user's language (Phase 7): profile → cookie → LANGUAGE_CODE. Ignores Accept-Language,
    so a phone set to English does not switch a cashier's screens."""

    def __init__(self, get_response):
        self.get_response = get_response
        self.codes = {code for code, _ in settings.LANGUAGES}

    def __call__(self, request):
        lang = None
        user = getattr(request, "user", None)
        if user is not None and user.is_authenticated:
            profile = getattr(user, "profile", None) if hasattr(user, "profile") else None
            lang = getattr(profile, "language", "") or None
        if not lang:
            lang = request.COOKIES.get(settings.LANGUAGE_COOKIE_NAME)
        if lang not in self.codes:
            lang = settings.LANGUAGE_CODE
        translation.activate(lang)
        request.LANGUAGE_CODE = lang
        response = self.get_response(request)
        response.headers.setdefault("Content-Language", lang)
        translation.deactivate()
        return response
