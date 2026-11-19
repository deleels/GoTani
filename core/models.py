"""Farm profile (singleton, D72) and per-user settings (D64, D65)."""

from django.conf import settings
from django.db import models


class FarmProfile(models.Model):
    """The one farm (D2). Always pk=1; use `core.services.get_farm_profile()`."""

    name = models.CharField("nama kebun", max_length=120, default="GoTani Farm")
    address = models.CharField("alamat", max_length=255, blank=True)
    phone = models.CharField("telepon", max_length=40, blank=True)
    latitude = models.DecimalField("lintang", max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField("bujur", max_digits=9, decimal_places=6, null=True, blank=True)
    logo = models.ImageField("logo", upload_to="farm/", blank=True)
    # D80: off = not a PKP; product taxes are kept but ignored, new sales snapshot 0%.
    is_pkp = models.BooleanField("Pengusaha Kena Pajak (PKP)", default=False,
                                 help_text="Aktifkan hanya bila kebun terdaftar sebagai PKP dan wajib memungut PPN.")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "profil kebun"
        verbose_name_plural = "profil kebun"
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

    class Meta:
        verbose_name = "profil pengguna"
        verbose_name_plural = "profil pengguna"

    def __str__(self):
        return f"Profil {self.user}"


class AuditLog(models.Model):
    """Append-only "Log aktivitas" (D82): who changed what, before → after, and why.

    Written only through `core.audit.log_event()`; never updated or deleted (10-year trail)."""

    actor = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                              related_name="audit_entries", verbose_name="oleh")
    action = models.CharField("aksi", max_length=40, db_index=True)
    target_type = models.CharField("jenis objek", max_length=40, db_index=True)
    target_id = models.PositiveIntegerField("id objek", null=True, blank=True)
    summary = models.CharField("ringkasan", max_length=255)
    before = models.JSONField("sebelum", null=True, blank=True)
    after = models.JSONField("sesudah", null=True, blank=True)
    reason = models.TextField("alasan", blank=True)
    created_at = models.DateTimeField("waktu", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "log aktivitas"
        verbose_name_plural = "log aktivitas"

    def __str__(self):
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action} {self.summary}"

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValueError("AuditLog is append-only.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError("AuditLog is append-only.")
