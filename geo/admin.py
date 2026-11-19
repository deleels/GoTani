from django.contrib import admin

from .models import FieldPolygon


@admin.register(FieldPolygon)
class FieldPolygonAdmin(admin.ModelAdmin):
    list_display = ("name", "area_ha", "element", "agro_polygon_id", "created_at")
    readonly_fields = ("area_ha", "agro_polygon_id", "created_at")
