"""Phase 10: polygon validation/area, weather + Agromonitoring with mocked HTTP (CI never hits the network)."""

import json
from datetime import timedelta
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


def make_weather(today=None, past_rain=1.0, gust_tomorrow=20, rain_tomorrow=35.2, hourly_wind=8, hourly_pct=10):
    """Open-Meteo-shaped payload around `today`: 30 past days, 7 forecast days, 48 hourly rows."""
    from datetime import datetime, timedelta

    from django.utils import timezone as tz

    today = today or tz.localdate()
    past = [(today - timedelta(days=30 - i)).isoformat() for i in range(30)]
    future = [(today + timedelta(days=i)).isoformat() for i in range(7)]
    start = datetime.combine(today, datetime.min.time()).replace(hour=tz.localtime().hour)
    hours = [(start + timedelta(hours=i)).strftime("%Y-%m-%dT%H:00") for i in range(60)]
    return {
        "current": {"time": hours[0], "temperature_2m": 29.4, "relative_humidity_2m": 78, "precipitation": 0,
                    "weather_code": 2, "wind_speed_10m": hourly_wind},
        "hourly": {"time": hours, "temperature_2m": [28] * 60, "precipitation_probability": [hourly_pct] * 60,
                   "precipitation": [0] * 60, "weather_code": [2] * 60, "wind_speed_10m": [hourly_wind] * 60,
                   "wind_gusts_10m": [15] * 60},
        "daily": {
            "time": past + future,
            "weather_code": [61] * 30 + [2, 65, 3, 3, 61, 1, 0],
            "temperature_2m_max": [30] * 30 + [31, 28, 30, 30, 29, 31, 32],
            "temperature_2m_min": [23] * 30 + [23, 22, 23, 23, 22, 23, 24],
            "precipitation_sum": [past_rain] * 30 + [0, rain_tomorrow, 1, 0, 6, 0, 0],
            "precipitation_probability_max": [50] * 30 + [10, 90, 30, 20, 60, 10, 5],
            "et0_fao_evapotranspiration": [4.0] * 37,
            "wind_gusts_10m_max": [20] * 30 + [20, gust_tomorrow, 20, 20, 20, 20, 20],
        },
    }


WEATHER = make_weather()


def make_soil():
    from django.utils import timezone as tz

    now = tz.localtime().strftime("%Y-%m-%dT%H:00")
    return {"hourly": {"time": [now], "soil_temperature_0cm": [27.0], "soil_temperature_6cm": [26.0],
                       "soil_temperature_18cm": [25.0], "soil_moisture_0_to_1cm": [0.31], "soil_moisture_1_to_3cm": [0.32],
                       "soil_moisture_3_to_9cm": [0.33], "soil_moisture_9_to_27cm": [0.34]}}


def open_meteo_or(fallback):
    """requests.get side effect: Open-Meteo soil for api.open-meteo.com, `fallback(url)` otherwise."""
    def get(url, params=None, timeout=None):
        if "open-meteo" in url:
            return FakeResponse(make_soil())
        return fallback(url)
    return get


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
        from django.utils import timezone as tz

        self.assertIn(services._id_date((tz.localdate() + timedelta(days=1)).isoformat()), w["rain_warning"])
        self.assertEqual((len(w["history"]), w["rain_30d_mm"], len(w["hours"])), (30, 30.0, 48))
        self.assertEqual(w["spray"]["level"], "GOOD")
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
        public = services.public_satellite(sat)
        self.assertNotIn("test-key", json.dumps(public))  # key never reaches the browser
        self.assertTrue(public["has_image"])
        self.assertEqual(services.fetch_ndvi_image(self.polygon), b"\x89PNG fake")
        calls = get.call_count
        services.fetch_satellite(self.polygon)
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
        self.assertEqual(services.satellite_block_reason(self.polygon), services.SAT_API_ERROR)
        self.assertEqual(post.call_count, 1)  # failure remembered → soil did not re-register
        cache.clear()
        post.side_effect = None
        post.return_value = FakeResponse({"id": "p1"})
        get.return_value = FakeResponse(status=401)
        self.assertIsNone(services.fetch_satellite(self.polygon))
        self.assertIsNone(services.fetch_ndvi_image(self.polygon))

    @override_settings(AGRO_API_KEY="")
    @mock.patch("geo.services.requests.post")
    def test_missing_key_means_no_calls(self, post):
        self.assertIsNone(services.fetch_satellite(self.polygon))
        post.assert_not_called()
        self.assertEqual(services.satellite_block_reason(self.polygon), services.SAT_NO_KEY)

    @mock.patch("geo.services.requests.post")
    def test_polygon_under_1_ha_never_calls_agromonitoring(self, post):
        small = services.create_polygon(self.owner, "Greenhouse", square(lon=106.82, side=0.0005))  # ~0.31 ha
        self.assertFalse(services.satellite_eligible(small))
        self.assertTrue(services.satellite_eligible(self.polygon))
        self.assertEqual(services.satellite_block_reason(small), services.SAT_TOO_SMALL)
        self.assertIsNone(services.fetch_satellite(small))
        post.assert_not_called()
        self.assertIn("1–3.000 ha", services.satellite_message(services.SAT_TOO_SMALL, small))

    @mock.patch("geo.services.requests.post")
    def test_rejected_registration_is_explained_and_backed_off(self, post):
        post.return_value = FakeResponse(status=401)
        self.assertIsNone(services.ensure_agro_polygon(self.polygon))
        self.assertEqual(services.satellite_block_reason(self.polygon), services.SAT_KEY_REJECTED)
        self.assertIsNone(services.ensure_agro_polygon(self.polygon))
        self.assertEqual(post.call_count, 1)  # backed off
        with override_settings(AGRO_API_KEY="fixed-key"):
            post.return_value = FakeResponse({"id": "agro-9"})
            self.assertEqual(services.ensure_agro_polygon(self.polygon), "agro-9")  # new key retries at once
        self.assertEqual(post.call_count, 2)

        other = services.create_polygon(self.owner, "Blok B", square(lon=106.83))
        post.return_value = FakeResponse(status=422)
        self.assertIsNone(services.ensure_agro_polygon(other))
        self.assertEqual(services.satellite_block_reason(other), services.SAT_POLYGON_REJECTED)
        services.update_polygon(self.owner, other, geojson=square(lon=106.83, side=0.0012))  # reshaped → retry
        self.assertIsNone(services.satellite_block_reason(other))

    @mock.patch("geo.services.requests.post")
    @mock.patch("geo.services.requests.get")
    def test_no_imagery_yet_is_cached_briefly(self, get, post):
        post.return_value = FakeResponse({"id": "agro-1"})
        get.side_effect = lambda url, params=None, timeout=None: FakeResponse([] if url.endswith("/image/search") else [])
        with mock.patch.object(services.cache, "set", wraps=services.cache.set) as cache_set:
            sat = services.fetch_satellite(self.polygon)
        self.assertEqual(sat["passes"], [])
        self.assertIn(services.SAT_EMPTY_TTL, [c.args[2] for c in cache_set.call_args_list])


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

    @mock.patch("geo.services.requests.get")
    def test_polygon_data_unavailable_without_key(self, get):
        get.side_effect = open_meteo_or(lambda url: FakeResponse(status=404))
        p = services.create_polygon(self.owner, "Blok A", square())
        self.client.force_login(self.worker)
        data = self.client.get(reverse("geo:polygon_data", args=[p.pk])).json()
        self.assertEqual(data["reason"], services.SAT_NO_KEY)
        self.assertIn("data tidak tersedia", data["message"].lower())
        self.assertIn("AGRO_API_KEY", data["message"])
        self.assertFalse(data["agro_enabled"])
        self.assertEqual(self.client.get(reverse("geo:polygon_ndvi", args=[p.pk])).status_code, 404)
        self.assertEqual(data["soil"]["moisture"][0], {"depth": "0–1 cm", "value": 0.31})  # D90: soil without a key
        self.assertTrue(all("open-meteo" in c.args[0] for c in get.call_args_list))

    @override_settings(AGRO_API_KEY="test-key")
    @mock.patch("geo.services.requests.post")
    @mock.patch("geo.services.requests.get")
    def test_polygon_data_reasons(self, get, post):
        self.client.force_login(self.worker)
        get.side_effect = open_meteo_or(lambda url: FakeResponse(status=404))
        small = services.create_polygon(self.owner, "GH", square(side=0.0005))
        data = self.client.get(reverse("geo:polygon_data", args=[small.pk])).json()
        self.assertEqual(data["reason"], services.SAT_TOO_SMALL)
        self.assertFalse(self.client.get(reverse("geo:polygon_detail", args=[small.pk])).json()["satellite_ok"])
        post.assert_not_called()

        big = services.create_polygon(self.owner, "Kebun", square(lon=106.81))
        self.assertTrue(self.client.get(reverse("geo:polygon_detail", args=[big.pk])).json()["satellite_ok"])
        post.return_value = FakeResponse({"id": "agro-1"})
        get.side_effect = open_meteo_or(lambda url: FakeResponse([]))
        data = self.client.get(reverse("geo:polygon_data", args=[big.pk])).json()
        self.assertEqual(data["reason"], services.SAT_NO_IMAGERY)  # soil yes, imagery not yet
        self.assertIsNone(data["satellite"])
        self.assertEqual(data["soil"]["temperature"][2], {"depth": "18 cm", "value": 25.0})

        gh = FarmElement.objects.create(name="Rumah kaca uji", element_type="GREENHOUSE", area_m2=20000)
        services.update_polygon(self.owner, big, element=gh)
        data = self.client.get(reverse("geo:polygon_data", args=[big.pk])).json()
        self.assertEqual(data["reason"], services.SAT_COVERED)  # D90: roofed → no indices, soil still shown
        self.assertIsNotNone(data["soil"])
        self.assertFalse(self.client.get(reverse("geo:polygon_detail", args=[big.pk])).json()["satellite_ok"])
        services.update_polygon(self.owner, big, element=None)

        cache.clear()
        FieldPolygon.objects.filter(pk=big.pk).update(agro_polygon_id=None)
        post.return_value = FakeResponse(status=401)
        data = self.client.get(reverse("geo:polygon_data", args=[big.pk])).json()
        self.assertEqual(data["reason"], services.SAT_KEY_REJECTED)

        cache.clear()
        post.return_value = FakeResponse({"id": "agro-2"})
        get.side_effect = requests.ConnectionError("offline")
        data = self.client.get(reverse("geo:polygon_data", args=[big.pk])).json()
        self.assertEqual(data["reason"], services.SAT_API_ERROR)
        self.assertIsNone(data["soil"])

    def test_home_still_works_when_everything_external_fails(self):
        self.client.force_login(self.worker)
        self.assertEqual(self.client.get("/").status_code, 200)


class WeatherFeatureTests(TestCase):
    """D89–D93: spray window, water need with Kc, alerts, place search."""

    def setUp(self):
        cache.clear()

    def test_spray_rating_rules(self):
        good = {"temp": 28, "wind": 5, "rain_mm": 0, "code": 1}
        self.assertEqual(services.spray_rating(good, [{"rain_pct": 20}])["level"], "GOOD")
        self.assertEqual(services.spray_rating({**good, "wind": 12}, [])["level"], "FAIR")
        self.assertEqual(services.spray_rating({**good, "temp": 33}, [])["level"], "FAIR")
        self.assertEqual(services.spray_rating({**good, "wind": 16}, [])["level"], "BAD")
        self.assertEqual(services.spray_rating(good, [{"rain_pct": 10}, {"rain_pct": 50}])["level"], "BAD")
        self.assertEqual(services.spray_rating({**good, "code": 63}, [])["level"], "BAD")

    @mock.patch("geo.services.requests.get")
    def test_bad_spray_window_from_payload(self, get):
        get.return_value = FakeResponse(make_weather(hourly_wind=20))
        w = services.fetch_weather(1, 2)
        self.assertEqual(w["spray"]["level"], "BAD")
        self.assertIsNone(w["next_good_spray"])

    def test_water_need_uses_kc_and_ignores_rain_under_roof(self):
        from datetime import date

        from farm.models import CropCycle, Variety

        open_el = FarmElement.objects.create(name="Lahan", element_type="OPEN_FIELD", area_m2=100)
        gh = FarmElement.objects.create(name="GH", element_type="GREENHOUSE", area_m2=50)
        v = Variety.objects.create(name="Cabai", crop="Cabai", kc=D("1.05"))
        nokc = Variety.objects.create(name="Kakao", crop="Kakao")
        CropCycle.objects.create(element=open_el, variety=v, planting_start_date=date(2026, 9, 1))
        CropCycle.objects.create(element=gh, variety=v, planting_start_date=date(2026, 9, 1))
        weather = {"days": [{"et0_mm": 4.0, "rain_mm": 1.2}]}
        rows = {r["element"]: r for r in services.water_needs(weather)["rows"]}
        self.assertEqual((rows["Lahan"]["need_mm"], rows["Lahan"]["deficit_mm"], rows["Lahan"]["litres"]),
                         (D("4.2"), D("3.0"), D("300")))
        self.assertEqual((rows["GH"]["deficit_mm"], rows["GH"]["litres"]), (D("4.2"), D("210")))
        CropCycle.objects.filter(element=gh).update(variety=nokc)
        self.assertIsNone({r["element"]: r for r in services.water_needs(weather)["rows"]}["GH"]["litres"])

    def test_alerts(self):
        w = services._parse_weather(make_weather(rain_tomorrow=60, gust_tomorrow=45, past_rain=0))
        kinds = [a["kind"] for a in services.weather_alerts(w, has_open_field_cycle=True)]
        self.assertEqual(kinds, ["weather_rain", "weather_wind", "weather_dry"])
        self.assertEqual([a["kind"] for a in services.weather_alerts(w, has_open_field_cycle=False)],
                         ["weather_rain", "weather_wind"])
        calm = services._parse_weather(make_weather())
        self.assertEqual(services.weather_alerts(calm, True), [])

    @mock.patch("geo.services.requests.get")
    def test_bell_uses_cached_weather_only(self, get):
        from datetime import date

        from core.notifications import build_notifications
        from farm.access import UserRoles
        from farm.models import CropCycle, Variety

        profile = get_farm_profile()
        profile.latitude, profile.longitude = D("-6.6"), D("106.8")
        profile.save()
        CropCycle.objects.create(element=FarmElement.objects.create(name="L", element_type="OPEN_FIELD", area_m2=1),
                                 variety=Variety.objects.create(name="V", crop="C"), planting_start_date=date(2026, 9, 1))
        worker = make_user("w", ROLE_WORKER)
        self.assertEqual([n for n in build_notifications(UserRoles(worker)) if n["kind"].startswith("weather")], [])
        get.assert_not_called()  # nothing cached → no alert, no network
        get.return_value = FakeResponse(make_weather(rain_tomorrow=80))
        services.fetch_weather(D("-6.6"), D("106.8"))
        kinds = [n["kind"] for n in build_notifications(UserRoles(worker))]
        self.assertIn("weather_rain", kinds)

    @mock.patch("geo.services.requests.get")
    def test_place_search_proxied_cached(self, get):
        get.return_value = FakeResponse([{"display_name": "Ciawi, Bogor, Jawa Barat", "lat": "-6.65", "lon": "106.85",
                                          "addresstype": "village", "boundingbox": ["-6.7", "-6.6", "106.8", "106.9"]}])
        self.assertEqual(services.search_places("ci"), [])
        places = services.search_places("Ciawi Bogor")
        self.assertEqual(places[0]["lat"], -6.65)
        services.search_places("ciawi   bogor")
        self.assertEqual(get.call_count, 1)  # cached, query normalised
        kwargs = get.call_args.kwargs
        self.assertEqual(kwargs["params"]["countrycodes"], "id")
        self.assertIn("GoTani", kwargs["headers"]["User-Agent"])
        worker = make_user("w2", ROLE_WORKER)
        self.client.force_login(worker)
        cache.delete("nominatim-throttle")
        self.assertEqual(self.client.get(reverse("geo:search"), {"q": "Ciawi Bogor"}).json()["places"][0]["type"], "village")
        get.side_effect = requests.ConnectionError("offline")
        cache.clear()
        self.assertIn("tidak tersedia", self.client.get(reverse("geo:search"), {"q": "Garut"}).json()["message"])


class GeoPagesTests(TestCase):
    """D89–D92 page wiring: map search/layers, element Satelit & Tanah section, pesticide spray hint."""

    def setUp(self):
        self.owner = make_user("o", ROLE_OWNER)
        self.client.force_login(self.owner)

    def test_map_and_element_section(self):
        el = FarmElement.objects.create(name="Lahan uji", element_type="OPEN_FIELD", area_m2=100)
        p = services.create_polygon(self.owner, "Blok", square(), element=el)
        page = self.client.get(reverse("geo:map")).content.decode()
        self.assertIn(reverse("geo:search"), page)
        self.assertIn("Satelit + label", page)
        detail = self.client.get(reverse("farm:element_detail", args=[el.pk])).content.decode()
        self.assertIn(reverse("geo:polygon_data", args=[p.pk]), detail)
        empty = FarmElement.objects.create(name="Kosong", element_type="OPEN_FIELD", area_m2=10)
        self.assertNotIn("Satelit &amp; Tanah", self.client.get(reverse("farm:element_detail", args=[empty.pk])).content.decode())

    def test_spray_hint_only_on_pesticide_form(self):
        url = reverse("farm:activities")
        self.assertIn("Waktu semprot saat ini", self.client.get(url, {"tab": "pestisida", "new": 1}).content.decode())
        self.assertNotIn("Waktu semprot saat ini", self.client.get(url, {"tab": "irigasi", "new": 1}).content.decode())
