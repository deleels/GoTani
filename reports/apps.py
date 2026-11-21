from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ReportsConfig(AppConfig):
    """Read-only reporting: traceability (D29) and analytics (D28). No models."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "reports"
    verbose_name = _("Laporan")
