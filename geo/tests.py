"""Phase 10: polygon validation/area, weather + Agromonitoring with mocked HTTP (CI never hits the network)."""

import json
from decimal import Decimal
from unittest import mock

import requests
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from core.services import get_farm_profile
from farm.bootstrap import ROLE_CASHIER, ROLE_MANAGER, ROLE_OWNER, ROLE_WORKER
from farm.models import FarmElement
from geo import services
from geo.models import FieldPolygon
from pos.tests import make_user

D = Decimal


def square(lon=106.8, lat=-6.6, side=0.001):
    """~111 m × 110 m ≈ 1.23 ha near Bogor."""
    return {"type": "Polygon", "coordinates": [[[lon, lat], [lon + side, lat], [lon + side, lat + side],
                                                [lon, lat + side], [lon, lat]]]}


class FakeResponse:
    def __init__(self, data=None, status=200, content=b""):
        self._data, self.status_code, self.content = data, status, content

    def json(self):
        if isinstance(self._data, Exception):
            raise self._data
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


WEATHER = {
    "current": {"temperature_2m": 29.4, "relative_humidity_2m": 78, "precipitation": 0, "weather_code": 2, "wind_speed_10m": 8},
    "daily": {
        "time": ["2026-11-19", "2026-11-20", "2026-11-21", "2026-11-22", "2026-11-23", "2026-11-24", "2026-11-25"],
        "weather_code": [2, 65, 3, 3, 61, 1, 0], "temperature_2m_max": [31, 28, 30, 30, 29, 31, 32],
        "temperature_2m_min": [23, 22, 23, 23, 22, 23, 24], "precipitation_sum": [0, 35.2, 1, 0, 6, 0, 0],
        "precipitation_probability_max": [10, 90, 30, 20, 60, 10, 5],
    },
}


class GeometryTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user("owner", ROLE_OWNER)
        self.worker = make_user("worker", ROLE_WORKER)

    def test_area_computed_server_side(self):
        _, area = services.clean_geometry(square())
        self.assertAlmostEqual(float(area), 1.225, delta=0.01)  # 0.001° ≈ 110.6 m × 110.6 m at 6.6° S
        feature = {"type": "Feature", "geometry": square()}
        self.assertEqual(services.clean_geometry(feature)[1], area)

    def test_invalid_geometries_rejected(self):
        bowtie = {"type": "Polygon", "coordinates": [[[106.8, -6.6], [106.801, -6.599], [106.801, -6.6], [106.8, -6.599], [106.8, -6.6]]]}
        cases = [
            bowtie,  # self-intersection
            {"type": "Point", "coordinates": [106.8, -6.6]},
            {"type": "Polygon", "coordinates": [[[106.8, -6.6], [106.801, -6.6], [106.8, -6.6]]]},
            {"type": "Polygon", "coordinates": "nonsense"},
            square(side=0.0001),  # ~0.012 ha < 0.02
            square(side=0.5),  # far above 1000 ha
            square(lon=200),
            None,
        ]
        for geo in cases:
            with self.subTest(geo=geo), self.assertRaises(ValidationError):
                services.clean_geometry(geo)

    def test_total_cap_and_permissions(self):
        p = services.create_polygon(self.owner, "Blok A", square())
        self.assertEqual(p.created_by, self.owner)
        with mock.patch.object(services, "TOTAL_MAX_HA", D("2")):
            with self.assertRaises(ValidationError):
                services.create_polygon(self.owner, "Blok B", square(lon=106.81))
            services.update_polygon(self.owner, p, geojson=square(side=0.0012))  # replacing itself is fine
        with self.assertRaises(PermissionDenied):
            services.create_polygon(self.worker, "X", square())
        with self.assertRaises(ValidationError):
            services.create_polygon(self.owner, "  ", square())

    def test_link_never_overwrites_area_silently(self):
        element = FarmElement.objects.create(name="GH-1", element_type="GREENHOUSE", area_m2=D("500"))
        p = services.create_polygon(self.owner, "Blok A", square(), element=element)
        element.refresh_from_db()
        self.assertEqual(element.area_m2, D("500"))
        services.use_plotted_area(self.owner, p)
        element.refresh_from_db()
        self.assertEqual(element.area_m2, (p.area_ha * 10000).quantize(D("0.01")))
        services.update_polygon(self.owner, p, element=None)
        with self.assertRaises(ValidationError):
            services.use_plotted_area(self.owner, FieldPolygon.objects.get(pk=p.pk))


class WeatherTests(TestCase):
    def setUp(self):
        cache.clear()

    @mock.patch("geo.services.requests.get")
    def test_parsed_cached_and_rain_warning(self, get):
        get.return_value = FakeResponse(WEATHER)
        w = services.fetch_weather(D("-6.595"), D("106.816"))
        self.assertEqual(w["current"]["label"], "Berawan sebagian")
        self.assertEqual(len(w["days"]), 7)
        self.assertIn("20 Nov", w["rain_warning"])
        services.fetch_weather(D("-6.595"), D("106.816"))
        self.assertEqual(get.call_count, 1)  # cached 30 min
        self.assertIn("api.open-meteo.com", get.call_args[0][0])

    @mock.patch("geo.services.requests.get")
    def test_failures_return_none(self, get):
        self.assertIsNone(services.fetch_weather(None, None))
        get.side_effect = requests.ConnectionError("offline")
        self.assertIsNone(services.fetch_weather(1, 2))
        get.side_effect = None
        get.return_value = FakeResponse({"current": {}})
        self.assertIsNone(services.fetch_weather(3, 4))
        get.return_value = FakeResponse(ValueError("bad json"))
        self.assertIsNone(services.fetch_weather(5, 6))
        get.return_value = FakeResponse(status=500)
        self.assertIsNone(services.fetch_weather(7, 8))


@override_settings(AGRO_API_KEY="test-key")
class AgroTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user("owner", ROLE_OWNER)
        self.polygon = services.create_polygon(self.owner, "Blok A", square())

    def fake_get(self, url, params=None, timeout=None):
        if url.endswith("/image/search"):
            return FakeResponse([{"dt": 1763000000, "type": "Sentinel-2", "cl": 12.5, "dc": 100,
                                  "stats": {"ndvi": "https://stats/ndvi", "evi": "https://stats/evi", "ndwi": "https://stats/ndwi"},
                                  "image": {"ndvi": "https://img/ndvi.png?appid=test-key"}}])
        if url.startswith("https://stats/"):
            return FakeResponse({"min": 0.1, "mean": 0.55, "max": 0.8, "median": 0.56})
        if url.endswith("/ndvi/history"):
            return FakeResponse([{"dt": 1762000000, "data": {"mean": 0.5}}, {"dt": 1761000000, "data": {"mean": 0.4}}])
        if url.endswith("/soil"):
            return FakeResponse({"dt": 1763000000, "t0": 300.15, "t10": 298.15, "moisture": 0.31})
        if url.startswith("https://img/"):
            return FakeResponse(content=b"\x89PNG fake")
        return FakeResponse(status=404)

    @mock.patch("geo.services.requests.post")
    @mock.patch("geo.services.requests.get")
    def test_satellite_soil_and_image_proxy(self, get, post):
        get.side_effect = self.fake_get
        post.return_value = FakeResponse({"id": "agro-123"})
        sat = services.fetch_satellite(self.polygon)
        self.assertEqual(sat["passes"][0]["ndvi"], {"min": 0.1, "mean": 0.55, "max": 0.8})
        self.assertEqual([h["mean"] for h in sat["history"]], [0.4, 0.5])  # oldest first
        self.polygon.refresh_from_db()
        self.assertEqual(self.polygon.agro_polygon_id, "agro-123")
        self.assertEqual(services.fetch_soil(self.polygon), {"dt": 1763000000, "t0_c": 27.0, "t10_c": 25.0, "moisture": 0.31})
        public = services.public_satellite(sat)
        self.assertNotIn("test-key", json.dumps(public))  # key never reaches the browser
        self.assertTrue(public["has_image"])
        self.assertEqual(services.fetch_ndvi_image(self.polygon), b"\x89PNG fake")
        calls = get.call_count
        services.fetch_satellite(self.polygon)
        services.fetch_soil(self.polygon)
        self.assertEqual(get.call_count, calls)  # cached 24 h
        self.assertEqual(post.call_count, 1)

    @mock.patch("geo.services.requests.delete")
    @mock.patch("geo.services.requests.post")
    def test_geometry_change_reregisters_and_delete_cleans_up(self, post, delete):
        FieldPolygon.objects.filter(pk=self.polygon.pk).update(agro_polygon_id="old")
        self.polygon.refresh_from_db()
        services.update_polygon(self.owner, self.polygon, name="Blok A2")
        self.polygon.refresh_from_db()
        self.assertEqual(self.polygon.agro_polygon_id, "old")  # rename keeps remote id
        services.update_polygon(self.owner, self.polygon, geojson=square(side=0.0012))
        self.polygon.refresh_from_db()
        self.assertIsNone(self.polygon.agro_polygon_id)
        delete.assert_called_once()
        delete.side_effect = requests.ConnectionError("offline")
        FieldPolygon.objects.filter(pk=self.polygon.pk).update(agro_polygon_id="x")
        self.polygon.refresh_from_db()
        services.delete_polygon(self.owner, self.polygon)  # remote failure never blocks
        self.assertFalse(FieldPolygon.objects.exists())

    @mock.patch("geo.services.requests.post")
    @mock.patch("geo.services.requests.get")
    def test_api_failures_are_graceful(self, get, post):
        post.side_effect = requests.Timeout("slow")
        self.assertIsNone(services.fetch_satellite(self.polygon))
        self.assertIsNone(services.fetch_soil(self.polygon))
        post.side_effect = None
        post.return_value = FakeResponse({"id": "p1"})
        get.return_value = FakeResponse(status=401)
        self.assertIsNone(services.fetch_satellite(self.polygon))
        self.assertIsNone(services.fetch_soil(self.polygon))
        self.assertIsNone(services.fetch_ndvi_image(self.polygon))

    @override_settings(AGRO_API_KEY="")
    @mock.patch("geo.services.requests.post")
    def test_missing_key_means_no_calls(self, post):
        self.assertIsNone(services.fetch_satellite(self.polygon))
        self.assertIsNone(services.fetch_soil(self.polygon))
        post.assert_not_called()


class GeoViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.owner = make_user("owner", ROLE_OWNER)
        self.manager = make_user("manager", ROLE_MANAGER)
        self.worker = make_user("worker", ROLE_WORKER)
        self.cashier = make_user("cashier", ROLE_CASHIER)
        self.element = FarmElement.objects.create(name="GH-1", element_type="GREENHOUSE", area_m2=D("500"))

    def post_json(self, url, data):
        return self.client.post(url, json.dumps(data), content_type="application/json")

    def test_map_access_and_polygon_crud(self):
        self.client.force_login(self.cashier)
        self.assertEqual(self.client.get(reverse("geo:map")).status_code, 403)
        self.client.force_login(self.worker)
        self.assertContains(self.client.get(reverse("geo:map")), "leaflet")
        self.assertEqual(self.post_json(reverse("geo:polygon_create"), {"name": "A", "geojson": square()}).status_code, 403)

        self.client.force_login(self.manager)
        r = self.post_json(reverse("geo:polygon_create"), {"name": "Blok A", "geojson": square(), "element_id": self.element.pk})
        self.assertEqual(r.status_code, 201)
        pk = r.json()["id"]
        self.assertEqual(r.json()["element_area_m2"], "500.00")
        r = self.post_json(reverse("geo:polygon_create"), {"name": "Bad", "geojson": square(side=0.00001)})
        self.assertEqual(r.status_code, 400)
        self.assertIn("di luar batas", r.json()["error"])
        r = self.client.post(reverse("geo:polygon_create"), "not json", content_type="application/json")
        self.assertEqual(r.status_code, 400)
        detail = reverse("geo:polygon_detail", args=[pk])
        self.assertEqual(self.post_json(detail, {"name": "Blok Utama"}).json()["name"], "Blok Utama")
        r = self.post_json(detail + "?action=use_area", {})
        self.element.refresh_from_db()
        self.assertEqual(str(self.element.area_m2), r.json()["element_area_m2"])
        self.assertEqual(self.post_json(detail, {"element_id": None}).json()["element"], None)
        self.assertEqual(self.client.get(detail).status_code, 200)
        self.assertContains(self.client.get(reverse("farm:element_detail", args=[self.element.pk])), "Luas terpetakan")
        self.assertEqual(self.post_json(detail + "?action=delete", {}).json(), {"deleted": pk})

    @mock.patch("geo.services.requests.get")
    def test_weather_endpoint(self, get):
        self.client.force_login(self.worker)
        r = self.client.get(reverse("geo:weather")).json()
        self.assertIsNone(r["weather"])
        self.assertIn("Pengaturan", r["message"])
        profile = get_farm_profile()
        profile.latitude, profile.longitude = D("-6.595"), D("106.816")
        profile.save()
        get.return_value = FakeResponse(WEATHER)
        self.assertEqual(self.client.get(reverse("geo:weather")).json()["weather"]["current"]["temp"], 29.4)
        cache.clear()
        get.side_effect = requests.ConnectionError()
        self.assertEqual(self.client.get(reverse("geo:weather")).json()["message"], "data tidak tersedia")

    def test_polygon_data_unavailable_without_key(self):
        p = services.create_polygon(self.owner, "Blok A", square())
        self.client.force_login(self.worker)
        data = self.client.get(reverse("geo:polygon_data", args=[p.pk])).json()
        self.assertEqual(data["message"], "data tidak tersedia")
        self.assertFalse(data["agro_enabled"])
        self.assertEqual(self.client.get(reverse("geo:polygon_ndvi", args=[p.pk])).status_code, 404)

    def test_home_still_works_when_everything_external_fails(self):
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get("/").status_code, 200)
