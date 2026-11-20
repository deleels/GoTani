"""Field polygons (D73): GeoJSON in a JSONField (no PostGIS); area computed server-side."""

from django.utils.translation import gettext_lazy as _l
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from farm.models import FarmElement


class FieldPolygon(models.Model):
    name = models.CharField(_l("nama"), max_length=100)
    geojson = models.JSONField(_l("geometri (GeoJSON Polygon)"))
    area_ha = models.DecimalField(_l("luas (ha)"), max_digits=10, decimal_places=4)
    element = models.ForeignKey(FarmElement, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name="polygons", verbose_name=_l("elemen"))
    agro_polygon_id = models.CharField(_l("id Agromonitoring"), max_length=64, null=True, blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name", "id"]
        verbose_name = _l("poligon lahan")
        verbose_name_plural = _l("poligon lahan")
        constraints = [
            models.CheckConstraint(condition=Q(area_ha__gt=0), name="fieldpolygon_area_positive"),
        ]

    def __str__(self):
        return self.name

    @property
    def area_m2(self):
        return self.area_ha * 10000
