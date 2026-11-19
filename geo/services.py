"""External data and polygon geometry (rules 33–34, D73).

The ONLY module that talks to the network. Every call is cached in Django's
database cache (weather 30 min, NDVI/soil 24 h); a missing key or any failure
returns None so the UI can show "data tidak tersedia" — never an exception.
"""

import hashlib
import logging
import math
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

import requests
from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone
from shapely.geometry import Polygon, shape
from shapely.validation import explain_validity

from farm.bootstrap import ROLE_MANAGER, ROLE_OWNER, user_has_role

from .models import FieldPolygon

log = logging.getLogger(__name__)

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
AGRO_BASE = "https://api.agromonitoring.com/agro/1.0"
TIMEOUT = 8
WEATHER_TTL = 30 * 60
SAT_TTL = 24 * 60 * 60

MIN_HA = Decimal("0.02")
MAX_HA = Decimal("1000")
TOTAL_MAX_HA = Decimal("1000")  # Agromonitoring free-tier cap
EARTH_R = 6371008.8  # mean Earth radius (m)

# Agromonitoring only accepts polygons of 1–3000 ha (D79). Smaller polygons are still
# mapped (greenhouses), they just get no satellite/soil data — with an explicit reason.
AGRO_MIN_HA = Decimal("1")
AGRO_MAX_HA = Decimal("3000")
AGRO_FAIL_TTL = 10 * 60  # back off re-registering a rejected polygon
SAT_EMPTY_TTL = 3 * 60 * 60  # no imagery yet → re-check sooner than 24 h

# Why satellite/soil data is unavailable (D79). None = available.
SAT_NO_KEY = "NO_KEY"
SAT_TOO_SMALL = "TOO_SMALL"
SAT_KEY_REJECTED = "KEY_REJECTED"
SAT_POLYGON_REJECTED = "POLYGON_REJECTED"
SAT_API_ERROR = "API_ERROR"
SAT_NO_IMAGERY = "NO_IMAGERY"
SAT_COVERED = "COVERED"

# D90: a satellite sees the roof, not the crop, over these element types — no vegetation indices.
COVERED_TYPES = {"GREENHOUSE", "SCREEN_HOUSE", "HYDROPONIC"}

WEATHER_CODES = {
    0: "Cerah", 1: "Cerah berawan", 2: "Berawan sebagian", 3: "Berawan", 45: "Berkabut", 48: "Berkabut",
    51: "Gerimis ringan", 53: "Gerimis", 55: "Gerimis lebat", 61: "Hujan ringan", 63: "Hujan", 65: "Hujan lebat",
    66: "Hujan beku", 67: "Hujan beku", 71: "Salju", 73: "Salju", 75: "Salju", 80: "Hujan lokal",
    81: "Hujan lokal", 82: "Hujan deras", 95: "Badai petir", 96: "Badai petir", 99: "Badai petir",
}
RAIN_CODES = {61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99}


def require_editor(actor):
    if not user_has_role(actor, ROLE_OWNER, ROLE_MANAGER):
        raise PermissionDenied("Hanya OWNER/MANAGER yang dapat mengubah poligon.")


# ---------------------------------------------------------------------------
# Geometry (rule 33)
# ---------------------------------------------------------------------------


def _ring_area_m2(coords) -> float:
    """Geodesic area of a lon/lat ring on a sphere (Chamberlain & Duquette)."""
    total = 0.0
    n = len(coords)
    for i in range(n):
        lon1, lat1 = coords[i][:2]
        lon2, lat2 = coords[(i + 1) % n][:2]
        total += math.radians(lon2 - lon1) * (2 + math.sin(math.radians(lat1)) + math.sin(math.radians(lat2)))
    return abs(total * EARTH_R * EARTH_R / 2)


def polygon_area_ha(geom: Polygon) -> Decimal:
    area = _ring_area_m2(list(geom.exterior.coords)) - sum(_ring_area_m2(list(r.coords)) for r in geom.interiors)
    return (Decimal(str(area)) / Decimal("10000")).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def clean_geometry(geojson) -> tuple[dict, Decimal]:
    """Validate a GeoJSON Polygon (or a Feature wrapping one) → (geometry dict, area ha)."""
    if isinstance(geojson, dict) and geojson.get("type") == "Feature":
        geojson = geojson.get("geometry")
    if not isinstance(geojson, dict) or geojson.get("type") != "Polygon":
        raise ValidationError("Geometri harus berupa satu poligon.")
    try:
        geom = shape(geojson)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        raise ValidationError("Koordinat poligon tidak valid.")
    if geom.is_empty or len(geom.exterior.coords) < 4:
        raise ValidationError("Poligon minimal memiliki 3 titik.")
    for lon, lat, *_ in geom.exterior.coords:
        if not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValidationError("Koordinat di luar jangkauan bujur/lintang.")
    if not geom.is_valid:
        raise ValidationError(f"Poligon tidak valid (berpotongan sendiri?): {explain_validity(geom)}.")
    area = polygon_area_ha(geom)
    if area < MIN_HA or area > MAX_HA:
        raise ValidationError(f"Luas poligon {area} ha di luar batas {MIN_HA}–{MAX_HA} ha.")
    return {"type": "Polygon", "coordinates": geojson["coordinates"]}, area


def _check_total(area, exclude_pk=None):
    qs = FieldPolygon.objects.all()
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    total = (qs.aggregate(s=Sum("area_ha"))["s"] or Decimal("0")) + area
    if total > TOTAL_MAX_HA:
        raise ValidationError(f"Total luas semua poligon ({total} ha) melebihi batas {TOTAL_MAX_HA} ha.")


@transaction.atomic
def create_polygon(actor, name, geojson, element=None) -> FieldPolygon:
    require_editor(actor)
    if not (name or "").strip():
        raise ValidationError("Nama poligon wajib diisi.")
    geometry, area = clean_geometry(geojson)
    _check_total(area)
    return FieldPolygon.objects.create(name=name.strip(), geojson=geometry, area_ha=area, element=element,
                                       created_by=actor)


@transaction.atomic
def update_polygon(actor, polygon, *, name=None, geojson=None, element=False) -> FieldPolygon:
    """`element=False` leaves the link alone; None unlinks. Never touches `element.area_m2` (rule 33)."""
    require_editor(actor)
    polygon = FieldPolygon.objects.select_for_update().get(pk=polygon.pk)
    if name is not None:
        if not name.strip():
            raise ValidationError("Nama poligon wajib diisi.")
        polygon.name = name.strip()
    if geojson is not None:
        geometry, area = clean_geometry(geojson)
        _check_total(area, exclude_pk=polygon.pk)
        if geometry != polygon.geojson:
            _agro_delete(polygon.agro_polygon_id)  # remote geometry is immutable → re-register lazily
            polygon.agro_polygon_id = None
            cache.delete(_fail_key(polygon))  # new shape gets a fresh registration attempt
        polygon.geojson, polygon.area_ha = geometry, area
    if element is not False:
        polygon.element = element
    polygon.save()
    return polygon


def delete_polygon(actor, polygon):
    require_editor(actor)
    _agro_delete(polygon.agro_polygon_id)
    polygon.delete()


def use_plotted_area(actor, polygon):
    """Explicit "use plotted area" action: copy the polygon area onto its element (rule 33)."""
    require_editor(actor)
    if polygon.element is None:
        raise ValidationError("Poligon belum ditautkan ke elemen.")
    element = polygon.element
    element.area_m2 = (polygon.area_ha * 10000).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    element.save(update_fields=["area_m2"])
    return element


def centroid(polygon: FieldPolygon):
    c = shape(polygon.geojson).centroid
    return c.y, c.x


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def _get_json(url, params):
    try:
        r = requests.get(url, params=params, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("GET %s gagal: %s", url, exc)
        return None


def _cached(key, ttl, fetch):
    """`ttl` may be a callable of the fetched value (e.g. shorter for empty results)."""
    hit = cache.get(key)
    if hit is not None:
        return hit
    value = fetch()
    if value is not None:
        cache.set(key, value, ttl(value) if callable(ttl) else ttl)
    return value


# ---------------------------------------------------------------------------
# Weather — Open-Meteo, no key (rule 34)
# ---------------------------------------------------------------------------


ID_MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]


def _id_date(iso: str) -> str:
    try:
        y, m, d = (int(x) for x in iso.split("-"))
        return f"{d} {ID_MONTHS[m - 1]}"
    except (ValueError, IndexError):
        return iso


# Spray window (D89): advice only, never blocks a pesticide report.
SPRAY_BAD_WIND = 15  # km/h
SPRAY_FAIR_WIND = 10
SPRAY_BAD_RAIN_PCT = 50  # within the next SPRAY_LOOKAHEAD_H hours
SPRAY_HOT_C = 32
SPRAY_LOOKAHEAD_H = 4
SPRAY_LABELS = {"GOOD": "Baik", "FAIR": "Cukup", "BAD": "Buruk"}


def spray_rating(now: dict, ahead: list) -> dict:
    """GOOD / FAIR / BAD for spraying from the current hour and the next few hours (D89).

    Bad: wind > 15 km/h, rain chance ≥ 50% in the next 4 h, or raining now.
    Fair: temperature > 32 °C or wind 10–15 km/h. Otherwise good."""
    reasons = []
    wind = now.get("wind") or 0
    raining = (now.get("rain_mm") or 0) > 0 or now.get("code") in RAIN_CODES
    max_pct = max([h.get("rain_pct") or 0 for h in ahead] or [0])
    if raining:
        reasons.append("sedang hujan")
    if wind > SPRAY_BAD_WIND:
        reasons.append(f"angin {round(wind)} km/j")
    if max_pct >= SPRAY_BAD_RAIN_PCT:
        reasons.append(f"peluang hujan {round(max_pct)}% dalam {SPRAY_LOOKAHEAD_H} jam")
    if reasons:
        level = "BAD"
    else:
        if (now.get("temp") or 0) > SPRAY_HOT_C:
            reasons.append(f"panas {round(now['temp'])} °C (semprot pagi/sore)")
        if SPRAY_FAIR_WIND <= wind <= SPRAY_BAD_WIND:
            reasons.append(f"angin {round(wind)} km/j")
        level = "FAIR" if reasons else "GOOD"
    return {"level": level, "label": SPRAY_LABELS[level], "reasons": reasons}


def _hours(data):
    """Hourly rows from now on (48 h)."""
    h = data.get("hourly") or {}
    times = h.get("time") or []
    now_key = (data.get("current") or {}).get("time", "")[:13]

    def col(name, i):
        values = h.get(name) or []
        return values[i] if i < len(values) else None

    rows = [{"time": t, "temp": col("temperature_2m", i), "rain_pct": col("precipitation_probability", i),
             "rain_mm": col("precipitation", i) or 0, "wind": col("wind_speed_10m", i), "gust": col("wind_gusts_10m", i),
             "code": col("weather_code", i)} for i, t in enumerate(times) if t[:13] >= now_key]
    for i, row in enumerate(rows):
        row["spray"] = spray_rating(row, rows[i + 1:i + 1 + SPRAY_LOOKAHEAD_H])["level"]
    return rows[:48]


def _parse_weather(data, today=None):
    try:
        cur, daily = data["current"], data["daily"]
        today = (today or timezone.localdate()).isoformat()
        n = len(daily["time"])

        def col(name):
            return daily.get(name) or [None] * n

        days, history = [], []
        for i, day in enumerate(daily["time"]):
            code = daily["weather_code"][i]
            row = {
                "date": day, "code": code, "label": WEATHER_CODES.get(code, "—"),
                "tmax": daily["temperature_2m_max"][i], "tmin": daily["temperature_2m_min"][i],
                "rain_mm": daily["precipitation_sum"][i] or 0, "rain_pct": col("precipitation_probability_max")[i],
                "et0_mm": col("et0_fao_evapotranspiration")[i], "gust_max": col("wind_gusts_10m_max")[i],
            }
            (history if day < today else days).append(row)
        rainy = [d for d in days[:3] if d["code"] in RAIN_CODES and ((d["rain_pct"] or 0) >= 70 or d["rain_mm"] >= 20)]
        current = {
            "temp": cur["temperature_2m"], "humidity": cur.get("relative_humidity_2m"),
            "wind": cur.get("wind_speed_10m"), "rain_mm": cur.get("precipitation"),
            "code": cur["weather_code"], "label": WEATHER_CODES.get(cur["weather_code"], "—"),
        }
        hours = _hours(data)
        return {
            "current": current,
            "days": days,
            "hours": hours,
            "history": history[-30:],
            "rain_30d_mm": round(sum(d["rain_mm"] for d in history[-30:]), 1),
            "spray": spray_rating(current, hours[1:1 + SPRAY_LOOKAHEAD_H]),
            "next_good_spray": next((h["time"] for h in hours if h["spray"] == "GOOD"), None),
            "rain_warning": (f"Hujan lebat diperkirakan {_id_date(rainy[0]['date'])} ({rainy[0]['rain_mm']} mm) — "
                             "tunda penyemprotan & siapkan drainase.") if rainy else None,
        }
    except (KeyError, IndexError, TypeError):
        return None


def _weather_key(lat, lon):
    return f"weather2:{round(float(lat), 3)}:{round(float(lon), 3)}"


def fetch_weather(lat, lon):
    """Current, 48 h hourly, 7-day forecast, 30 days of rain history, ET₀ and the spray window (D89).
    One Open-Meteo call, cached 30 minutes; None on failure."""
    if lat is None or lon is None:
        return None
    lat, lon = round(float(lat), 3), round(float(lon), 3)

    def fetch():
        data = _get_json(OPEN_METEO_URL, {
            "latitude": lat, "longitude": lon, "timezone": "Asia/Jakarta", "forecast_days": 7, "past_days": 30,
            "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
            "hourly": "temperature_2m,precipitation_probability,precipitation,weather_code,wind_speed_10m,wind_gusts_10m",
            "daily": ("weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,"
                      "precipitation_probability_max,et0_fao_evapotranspiration,wind_gusts_10m_max"),
        })
        return _parse_weather(data) if data else None

    return _cached(_weather_key(lat, lon), WEATHER_TTL, fetch)


def cached_weather(lat, lon):
    """Weather only if already cached — for the 60 s notification poll, which must never hit the network."""
    if lat is None or lon is None:
        return None
    return cache.get(_weather_key(lat, lon))


def farm_location():
    """(lat, lon) of the farm profile, else the first polygon's centre, else (None, None)."""
    from core.services import get_farm_profile

    profile = get_farm_profile()
    if profile.has_location:
        return profile.latitude, profile.longitude
    first = FieldPolygon.objects.first()
    return centroid(first) if first else (None, None)


# Soil — Open-Meteo model data for any field size (D90). Labelled "perkiraan model, bukan sensor".
SOIL_TEMPS = (("soil_temperature_0cm", "0 cm"), ("soil_temperature_6cm", "6 cm"), ("soil_temperature_18cm", "18 cm"))
SOIL_MOISTURES = (("soil_moisture_0_to_1cm", "0–1 cm"), ("soil_moisture_1_to_3cm", "1–3 cm"),
                  ("soil_moisture_3_to_9cm", "3–9 cm"), ("soil_moisture_9_to_27cm", "9–27 cm"))


def fetch_soil_model(lat, lon):
    """Soil temperature (°C) and volumetric moisture (m³/m³) at several depths for the current hour."""
    if lat is None or lon is None:
        return None
    lat, lon = round(float(lat), 3), round(float(lon), 3)
    names = [n for n, _ in SOIL_TEMPS + SOIL_MOISTURES]

    def fetch():
        data = _get_json(OPEN_METEO_URL, {"latitude": lat, "longitude": lon, "timezone": "Asia/Jakarta",
                                          "forecast_days": 1, "hourly": ",".join(names)})
        try:
            hourly = data["hourly"]
            now_key = timezone.localtime().strftime("%Y-%m-%dT%H")
            i = next((k for k, t in enumerate(hourly["time"]) if t[:13] == now_key), 0)
            temps = [{"depth": label, "value": hourly[n][i]} for n, label in SOIL_TEMPS if hourly.get(n)]
            moist = [{"depth": label, "value": hourly[n][i]} for n, label in SOIL_MOISTURES if hourly.get(n)]
        except (KeyError, IndexError, TypeError):
            return None
        return {"time": hourly["time"][i], "temperature": temps, "moisture": moist, "source": "Open-Meteo"}

    return _cached(f"soil-model:{lat}:{lon}", WEATHER_TTL, fetch)


# ---------------------------------------------------------------------------
# Satellite & soil — Agromonitoring free tier, key server-side (rule 34)
# ---------------------------------------------------------------------------


def agro_key():
    return getattr(settings, "AGRO_API_KEY", "") or ""


def _agro_delete(polygon_id):
    if not polygon_id or not agro_key():
        return
    try:
        requests.delete(f"{AGRO_BASE}/polygons/{polygon_id}", params={"appid": agro_key()}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        log.warning("Hapus poligon Agromonitoring gagal: %s", exc)


def satellite_eligible(polygon: FieldPolygon) -> bool:
    return AGRO_MIN_HA <= polygon.area_ha <= AGRO_MAX_HA


def _fail_key(polygon):
    # Keyed by the API key too, so fixing AGRO_API_KEY retries immediately.
    digest = hashlib.sha256(agro_key().encode()).hexdigest()[:10]
    return f"agro-fail:{polygon.pk}:{digest}"


def is_covered(polygon: FieldPolygon) -> bool:
    return bool(polygon.element_id) and polygon.element.element_type in COVERED_TYPES


def satellite_ok(polygon: FieldPolygon) -> bool:
    """Vegetation indices make sense here: 1–3000 ha and not under a roof (D79, D90)."""
    return satellite_eligible(polygon) and not is_covered(polygon)


def satellite_block_reason(polygon: FieldPolygon):
    """Why satellite data cannot be fetched right now, or None (D79, D90). Never calls the network."""
    if is_covered(polygon):
        return SAT_COVERED
    if not agro_key():
        return SAT_NO_KEY
    if not satellite_eligible(polygon):
        return SAT_TOO_SMALL
    if polygon.agro_polygon_id:
        return None
    return cache.get(_fail_key(polygon))


def satellite_message(reason, polygon: FieldPolygon):
    ha = f"{polygon.area_ha.normalize():f}".replace(".", ",")
    return {
        SAT_NO_KEY: "Data tidak tersedia: kunci Agromonitoring (AGRO_API_KEY) belum dipasang di server.",
        SAT_TOO_SMALL: (f"Data satelit tidak tersedia: poligon ini {ha} ha, sedangkan Agromonitoring memerlukan "
                        "1–3.000 ha. Gambar poligon yang lebih luas (mis. seluruh lahan terbuka) untuk NDVI."),
        SAT_COVERED: (f"Indeks vegetasi tidak ditampilkan: {polygon.element.get_element_type_display() if polygon.element_id else ''} "
                      "beratap, jadi satelit melihat atapnya, bukan tanaman. Data tanah dan cuaca tetap tersedia."),
        SAT_KEY_REJECTED: ("Data tidak tersedia: kunci Agromonitoring ditolak. Periksa AGRO_API_KEY; "
                           "kunci baru bisa butuh beberapa jam sampai aktif."),
        SAT_POLYGON_REJECTED: "Data tidak tersedia: Agromonitoring menolak poligon ini (periksa luas dan bentuknya).",
        SAT_API_ERROR: "Data tidak tersedia: layanan Agromonitoring tidak merespons. Coba lagi nanti.",
        SAT_NO_IMAGERY: ("Belum ada citra satelit untuk poligon ini. Citra pertama bisa butuh beberapa hari "
                         "setelah poligon didaftarkan."),
    }.get(reason, "Data tidak tersedia.")


def ensure_agro_polygon(polygon: FieldPolygon):
    """Register the polygon with Agromonitoring once; returns its id or None.

    A rejected registration is remembered for AGRO_FAIL_TTL (see `satellite_block_reason`).
    """
    if polygon.agro_polygon_id:
        return polygon.agro_polygon_id
    if satellite_block_reason(polygon):
        return None
    fail_key = _fail_key(polygon)
    try:
        r = requests.post(
            f"{AGRO_BASE}/polygons", params={"appid": agro_key(), "duplicated": "true"}, timeout=TIMEOUT,
            json={"name": polygon.name[:60], "geo_json": {"type": "Feature", "properties": {}, "geometry": polygon.geojson}},
        )
        if r.status_code in (401, 403):
            log.warning("Kunci Agromonitoring ditolak (%s)", r.status_code)
            cache.set(fail_key, SAT_KEY_REJECTED, AGRO_FAIL_TTL)
            return None
        if 400 <= r.status_code < 500:
            log.warning("Agromonitoring menolak poligon %s (%s)", polygon.pk, r.status_code)
            cache.set(fail_key, SAT_POLYGON_REJECTED, AGRO_FAIL_TTL)
            return None
        r.raise_for_status()
        pid = str(r.json()["id"])
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        log.warning("Daftar poligon Agromonitoring gagal: %s", exc)
        cache.set(fail_key, SAT_API_ERROR, AGRO_FAIL_TTL)
        return None
    FieldPolygon.objects.filter(pk=polygon.pk).update(agro_polygon_id=pid)
    polygon.agro_polygon_id = pid
    return pid


def _stat(stats):
    if not isinstance(stats, dict) or "mean" not in stats:
        return None
    return {k: stats.get(k) for k in ("min", "mean", "max")}


def fetch_satellite(polygon: FieldPolygon, days=60, now=None):
    """Per-pass NDVI/EVI/NDWI min/mean/max, NDVI history and latest NDVI image. Cached 24 h."""
    pid = ensure_agro_polygon(polygon)
    if not pid:
        return None
    now = now or timezone.now()
    end = int(now.timestamp())
    start = int((now - timedelta(days=days)).timestamp())

    def fetch():
        key = agro_key()
        passes = _get_json(f"{AGRO_BASE}/image/search", {"polyid": pid, "start": start, "end": end, "appid": key})
        if passes is None:
            return None
        rows = []
        for p in sorted(passes, key=lambda x: x.get("dt", 0), reverse=True)[:8]:
            stats_urls = p.get("stats") or {}
            row = {"dt": p.get("dt"), "type": p.get("type"), "cloud": p.get("cl"), "coverage": p.get("dc")}
            for index in ("ndvi", "evi", "ndwi"):
                url = stats_urls.get(index)
                row[index] = _stat(_get_json(url, {})) if url else None
            row["ndvi_image"] = (p.get("image") or {}).get("ndvi")
            rows.append(row)
        history = _get_json(f"{AGRO_BASE}/ndvi/history",
                            {"polyid": pid, "start": int((now - timedelta(days=365)).timestamp()), "end": end, "appid": key}) or []
        hist = [{"dt": h.get("dt"), "mean": (h.get("data") or {}).get("mean")} for h in history if (h.get("data") or {}).get("mean") is not None]
        hist.sort(key=lambda h: h["dt"])
        latest_image = next((r["ndvi_image"] for r in rows if r["ndvi_image"]), None)
        return {"passes": rows, "history": hist, "latest_image": latest_image}

    return _cached(f"sat:{pid}:{start // 86400}", lambda v: SAT_TTL if v["passes"] else SAT_EMPTY_TTL, fetch)


# Place search — Nominatim (OpenStreetMap), free and keyless (D92). Its usage policy allows at most
# 1 request/s with an identifying User-Agent, so calls are proxied here, cached for a week and throttled.
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_UA = "GoTani/1.0 (aplikasi manajemen kebun)"
GEOCODE_TTL = 7 * 24 * 60 * 60


def search_places(query: str):
    """Desa/kecamatan/kabupaten in Indonesia → [{name, lat, lon, bbox}] or None when unavailable."""
    import time

    q = " ".join((query or "").split())[:100]
    if len(q) < 3:
        return []
    key = "geocode:" + hashlib.sha256(q.lower().encode()).hexdigest()[:24]
    hit = cache.get(key)
    if hit is not None:
        return hit
    if not cache.add("nominatim-throttle", 1, 1):  # someone called within the last second
        time.sleep(1)
        if not cache.add("nominatim-throttle", 1, 1):
            return None
    try:
        r = requests.get(NOMINATIM_URL, timeout=TIMEOUT, headers={"User-Agent": NOMINATIM_UA},
                         params={"q": q, "format": "jsonv2", "countrycodes": "id", "accept-language": "id", "limit": 6})
        r.raise_for_status()
        rows = r.json()
        places = [{"name": p["display_name"], "lat": float(p["lat"]), "lon": float(p["lon"]),
                   "type": p.get("addresstype") or p.get("type"),
                   "bbox": [float(x) for x in p.get("boundingbox") or []]} for p in rows]
    except (requests.RequestException, ValueError, KeyError, TypeError) as exc:
        log.warning("Pencarian lokasi gagal: %s", exc)
        return None
    cache.set(key, places, GEOCODE_TTL)
    return places


ALERT_RAIN_MM = 50
ALERT_GUST_KMH = 40
ALERT_DRY_DAYS = 7
DRY_DAY_MM = 1


def weather_alerts(weather, has_open_field_cycle: bool):
    """Bell alerts for farm roles (D93): heavy rain or strong gusts tomorrow; a dry week while an
    open-field cycle is active. [{kind, title, body, date}]"""
    if not weather or len(weather.get("days") or []) < 2:
        return []
    out = []
    tomorrow = weather["days"][1]
    when = _id_date(tomorrow["date"])
    if (tomorrow.get("rain_mm") or 0) >= ALERT_RAIN_MM:
        out.append({"kind": "weather_rain", "title": f"Hujan lebat besok ({when})",
                    "body": f"Diperkirakan {round(tomorrow['rain_mm'])} mm — tunda penyemprotan, cek drainase.",
                    "date": weather["days"][0]["date"]})
    if (tomorrow.get("gust_max") or 0) >= ALERT_GUST_KMH:
        out.append({"kind": "weather_wind", "title": f"Angin kencang besok ({when})",
                    "body": f"Hembusan hingga {round(tomorrow['gust_max'])} km/j — periksa penyangga dan atap.",
                    "date": weather["days"][0]["date"]})
    recent = (weather.get("history") or [])[-ALERT_DRY_DAYS:]
    if has_open_field_cycle and len(recent) == ALERT_DRY_DAYS and all((d.get("rain_mm") or 0) < DRY_DAY_MM for d in recent):
        out.append({"kind": "weather_dry", "title": f"{ALERT_DRY_DAYS} hari tanpa hujan",
                    "body": "Lahan terbuka mungkin perlu irigasi tambahan — lihat kebutuhan air di Cuaca & Satelit.",
                    "date": recent[-1]["date"]})
    return out


def water_needs(weather, today_index: int = 0):
    """Today's crop water need per active cycle (D91): ET₀ × Kc (mm) − rain (rain ignored under a roof),
    litres = mm × element m². Without Kc only ET₀ is shown."""
    from farm.models import CropCycle

    if not weather or not weather.get("days"):
        return {"et0_mm": None, "rows": []}
    day = weather["days"][today_index]
    et0, rain = day.get("et0_mm"), day.get("rain_mm") or 0
    rows = []
    for cycle in CropCycle.objects.filter(is_active=True).select_related("element", "variety").order_by("element__name"):
        el, kc = cycle.element, cycle.variety.kc
        covered = el.element_type in COVERED_TYPES
        row = {"element": el.name, "variety": cycle.variety.name, "kc": kc, "covered": covered,
               "area_m2": el.area_m2, "need_mm": None, "deficit_mm": None, "litres": None}
        if kc is not None and et0 is not None:
            need = Decimal(str(et0)) * kc
            deficit = max(need - (Decimal("0") if covered else Decimal(str(rain))), Decimal("0"))
            row.update(need_mm=need.quantize(Decimal("0.1")), deficit_mm=deficit.quantize(Decimal("0.1")),
                       litres=(deficit * el.area_m2).quantize(Decimal("1")))
        rows.append(row)
    return {"et0_mm": et0, "rain_mm": rain, "rows": rows}


def fetch_ndvi_image(polygon: FieldPolygon):
    """Latest NDVI PNG bytes, proxied so the API key never reaches the browser (D73). Cached 24 h."""
    sat = fetch_satellite(polygon)
    url = sat and sat.get("latest_image")
    if not url:
        return None

    def fetch():
        try:
            r = requests.get(url, timeout=TIMEOUT)
            r.raise_for_status()
        except requests.RequestException as exc:
            log.warning("Unduh citra NDVI gagal: %s", exc)
            return None
        return r.content

    return _cached(f"ndvi-img:{polygon.agro_polygon_id}:{url[-40:]}", SAT_TTL, fetch)


def public_satellite(sat):
    """Strip upstream URLs (they embed the API key) before sending data to the browser."""
    if not sat:
        return None
    return {
        "passes": [{k: v for k, v in p.items() if k != "ndvi_image"} for p in sat["passes"]],
        "history": sat["history"],
        "has_image": bool(sat.get("latest_image")),
    }
