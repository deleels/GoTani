from django.apps import AppConfig


class ReportsConfig(AppConfig):
    """Read-only reporting: traceability (D29) and analytics (D28). No models."""

    default_auto_field = "django.db.models.BigAutoField"
    name = "reports"
    verbose_name = "Laporan"
