"""Farm profile (singleton, D72) and per-user settings (D64, D65)."""

from django.utils.translation import gettext_lazy as _l
from django.conf import settings
from django.db import models


class FarmProfile(models.Model):
    """The one farm (D2). Always pk=1; use `core.services.get_farm_profile()`."""

    name = models.CharField(_l("nama kebun"), max_length=120, default="GoTani Farm")
    address = models.CharField(_l("alamat"), max_length=255, blank=True)
    phone = models.CharField(_l("telepon"), max_length=40, blank=True)
    latitude = models.DecimalField(_l("lintang"), max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(_l("bujur"), max_digits=9, decimal_places=6, null=True, blank=True)
    logo = models.ImageField(_l("logo"), upload_to="farm/", blank=True)
    # D80: off = not a PKP; product taxes are kept but ignored, new sales snapshot 0%.
    is_pkp = models.BooleanField(_l("Pengusaha Kena Pajak (PKP)"), default=False,
                                 help_text=_l("Aktifkan hanya bila kebun terdaftar sebagai PKP dan wajib memungut PPN."))
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _l("profil kebun")
        verbose_name_plural = _l("profil kebun")
        constraints = [models.CheckConstraint(condition=models.Q(pk=1), name="farmprofile_singleton")]

    def __str__(self):
        return self.name

    @property
    def has_location(self) -> bool:
        return self.latitude is not None and self.longitude is not None


class UserProfile(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    must_change_password = models.BooleanField(default=False)
    notifications_seen_at = models.DateTimeField(null=True, blank=True)
    # UI language (Phase 7). Blank = the site default (LANGUAGE_CODE, Indonesian). New OWNER/MANAGER
    # accounts start in English, CASHIER/WORKER in Indonesian; everyone can switch in Profil.
    language = models.CharField(_l("bahasa"), max_length=5, blank=True, choices=settings.LANGUAGES)

    class Meta:
        verbose_name = _l("profil pengguna")
        verbose_name_plural = _l("profil pengguna")

    def __str__(self):
        return f"Profil {self.user}"


class AuditLog(models.Model):
    """Append-only "Log aktivitas" (D82): who changed what, before → after, and why.

    Written only through `core.audit.log_event()`; never updated or deleted (10-year trail)."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                              related_name="audit_entries", verbose_name=_l("oleh"))
    action = models.CharField(_l("aksi"), max_length=40, db_index=True)
    target_type = models.CharField(_l("jenis objek"), max_length=40, db_index=True)
    target_id = models.PositiveIntegerField(_l("id objek"), null=True, blank=True)
    summary = models.CharField(_l("ringkasan"), max_length=255)
    before = models.JSONField(_l("sebelum"), null=True, blank=True)
    after = models.JSONField(_l("sesudah"), null=True, blank=True)
    reason = models.TextField(_l("alasan"), blank=True)
    created_at = models.DateTimeField(_l("waktu"), auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _l("log aktivitas")
        verbose_name_plural = _l("log aktivitas")

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.summary}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("AuditLog is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditLog is append-only.")
