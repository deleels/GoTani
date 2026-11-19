"""Force a password change after an OWNER-issued temporary password (D65)."""

from django.shortcuts import redirect
from django.urls import reverse


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
