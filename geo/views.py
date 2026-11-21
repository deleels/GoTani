"""Cuaca & Satelit (D73): map page + small JSON endpoints. External calls only via `geo.services`."""

from django.utils.translation import gettext_lazy as _l
from django.utils.translation import gettext as _
import json
from decimal import Decimal

from django.core.exceptions import ValidationError
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, render
from django.views import View

from core.services import get_farm_profile
from farm.access import FARM_ROLES, FINANCE_ROLES, RoleRequiredMixin
from farm.models import FarmElement
from farm.views_common import error_text

from . import services
from .models import FieldPolygon

UNAVAILABLE = _l("data tidak tersedia")


def polygon_payload(p: FieldPolygon):
    lat, lon = services.centroid(p)
    return {
        "id": p.pk, "name": p.name, "geojson": p.geojson, "area_ha": str(p.area_ha), "area_m2": str(p.area_m2.quantize(1)),
        "element_id": p.element_id, "element": p.element.name if p.element else None,
        "element_area_m2": str(p.element.area_m2) if p.element else None, "center": [lat, lon],
        "registered": bool(p.agro_polygon_id), "satellite_ok": services.satellite_ok(p),
        "covered": services.is_covered(p), "too_small": not services.satellite_eligible(p),
    }


class MapView(RoleRequiredMixin, View):
    allowed_roles = FARM_ROLES

    def get(self, request):
        profile = get_farm_profile()
        polygons = [polygon_payload(p) for p in FieldPolygon.objects.select_related("element")]
        center = ([float(profile.latitude), float(profile.longitude)] if profile.has_location
                  else (polygons[0]["center"] if polygons else [-6.595, 106.816]))
        return render(request, "geo/map.html", {
            "map_data": {
                "polygons": polygons, "center": center, "can_edit": self.roles.finance,
                "elements": [{"id": e.pk, "name": e.name, "area_m2": str(e.area_m2)} for e in FarmElement.objects.filter(is_active=True)],
                "agro_enabled": bool(services.agro_key()),
            },
            "has_location": profile.has_location,
        })


class JsonBodyMixin:
    def body(self, request):
        try:
            return json.loads(request.body or b"{}")
        except ValueError:
            raise ValidationError(_("Data JSON tidak valid."))


def _element(value):
    if value in (None, ""):
        return None
    return get_object_or_404(FarmElement, pk=value)


class PolygonCreateView(JsonBodyMixin, RoleRequiredMixin, View):
    allowed_roles = FINANCE_ROLES
    http_method_names = ["post"]

    def post(self, request):
        try:
            data = self.body(request)
            polygon = services.create_polygon(request.user, data.get("name", ""), data.get("geojson"),
                                              element=_element(data.get("element_id")))
        except ValidationError as exc:
            return JsonResponse({"error": error_text(exc)}, status=400)
        return JsonResponse(polygon_payload(polygon), status=201)


class PolygonDetailView(JsonBodyMixin, RoleRequiredMixin, View):
    """POST = update (name / geojson / element_id); DELETE-like action via ?action=delete or use_area."""

    allowed_roles = FARM_ROLES
    post_roles = FINANCE_ROLES

    def get(self, request, pk):
        return JsonResponse(polygon_payload(get_object_or_404(FieldPolygon.objects.select_related("element"), pk=pk)))

    def post(self, request, pk):
        polygon = get_object_or_404(FieldPolygon, pk=pk)
        action = request.GET.get("action", "update")
        try:
            if action == "delete":
                services.delete_polygon(request.user, polygon)
                return JsonResponse({"deleted": pk})
            if action == "use_area":
                element = services.use_plotted_area(request.user, polygon)
                polygon.refresh_from_db()
                return JsonResponse({**polygon_payload(polygon), "element_area_m2": str(element.area_m2)})
            data = self.body(request)
            kwargs = {"name": data.get("name"), "geojson": data.get("geojson")}
            if "element_id" in data:
                kwargs["element"] = _element(data["element_id"])
            polygon = services.update_polygon(request.user, polygon, **kwargs)
        except ValidationError as exc:
            return JsonResponse({"error": error_text(exc)}, status=400)
        return JsonResponse(polygon_payload(polygon))


class PolygonDataView(RoleRequiredMixin, View):
    """NDVI/EVI/NDWI per pass, NDVI history and soil for one polygon."""

    allowed_roles = FARM_ROLES

    def get(self, request, pk):
        return JsonResponse(polygon_data(get_object_or_404(FieldPolygon.objects.select_related("element"), pk=pk)))


def polygon_data(polygon):
    """Vegetation indices (Agromonitoring, open fields ≥ 1 ha) + model soil data (Open-Meteo, any polygon) — D90."""
    sat = None
    reason = services.satellite_block_reason(polygon)  # covered / no key / < 1 ha → no Agromonitoring call
    if reason is None:
        sat = services.public_satellite(services.fetch_satellite(polygon))
        reason = services.satellite_block_reason(polygon)  # registration may have just been rejected
        if reason is None and not (sat and sat["passes"]):
            reason = services.SAT_NO_IMAGERY if sat is not None else services.SAT_API_ERROR
    lat, lon = services.centroid(polygon)
    soil = services.fetch_soil_model(lat, lon)
    return {
        "satellite": sat if sat and sat["passes"] else None, "soil": soil, "reason": reason,
        "message": services.satellite_message(reason, polygon) if reason else None,
        "agro_enabled": bool(services.agro_key()),
    }


class NdviImageView(RoleRequiredMixin, View):
    allowed_roles = FARM_ROLES

    def get(self, request, pk):
        content = services.fetch_ndvi_image(get_object_or_404(FieldPolygon, pk=pk))
        if not content:
            return HttpResponse(UNAVAILABLE, status=404, content_type="text/plain; charset=utf-8")
        response = HttpResponse(content, content_type="image/png")
        response["Cache-Control"] = "private, max-age=3600"
        return response


class PlaceSearchView(RoleRequiredMixin, View):
    """Map search box: proxied Nominatim lookup (D92)."""

    allowed_roles = FARM_ROLES

    def get(self, request):
        places = services.search_places(request.GET.get("q", ""))
        if places is None:
            return JsonResponse({"places": [], "message": _("Pencarian lokasi tidak tersedia, coba lagi sebentar.")})
        return JsonResponse({"places": places, "message": None if places else _("Lokasi tidak ditemukan.")})


class WeatherView(RoleRequiredMixin, View):
    allowed_roles = FARM_ROLES

    def get(self, request):
        profile = get_farm_profile()
        lat, lon = profile.latitude, profile.longitude
        if not profile.has_location:
            first = FieldPolygon.objects.first()
            if first:
                lat, lon = services.centroid(first)
        weather = services.fetch_weather(lat, lon)
        if weather is None:
            reason = str(UNAVAILABLE) if lat is not None else _("Lokasi kebun belum diatur di Pengaturan")
            return JsonResponse({"weather": None, "message": reason})
        water = services.water_needs(weather)
        water["rows"] = [{k: (str(v) if isinstance(v, Decimal) else v) for k, v in r.items()} for r in water["rows"]]
        return JsonResponse({"weather": weather, "water": water, "message": None})
