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
