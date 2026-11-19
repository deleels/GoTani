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


def _parse_weather(data):
    try:
        cur, daily = data["current"], data["daily"]
        days = []
        for i, day in enumerate(daily["time"]):
            code = daily["weather_code"][i]
            days.append({
                "date": day, "code": code, "label": WEATHER_CODES.get(code, "—"),
                "tmax": daily["temperature_2m_max"][i], "tmin": daily["temperature_2m_min"][i],
                "rain_mm": daily["precipitation_sum"][i] or 0,
                "rain_pct": (daily.get("precipitation_probability_max") or [None] * len(daily["time"]))[i],
            })
        rainy = [d for d in days[:3] if d["code"] in RAIN_CODES and ((d["rain_pct"] or 0) >= 70 or d["rain_mm"] >= 20)]
        return {
            "current": {
                "temp": cur["temperature_2m"], "humidity": cur.get("relative_humidity_2m"),
                "wind": cur.get("wind_speed_10m"), "rain_mm": cur.get("precipitation"),
                "code": cur["weather_code"], "label": WEATHER_CODES.get(cur["weather_code"], "—"),
            },
            "days": days,
            "rain_warning": (f"Hujan lebat diperkirakan {_id_date(rainy[0]['date'])} ({rainy[0]['rain_mm']} mm) — "
                             "tunda penyemprotan & siapkan drainase.") if rainy else None,
        }
    except (KeyError, IndexError, TypeError):
        return None


def fetch_weather(lat, lon):
    """Current + 7-day forecast, or None. Cached 30 minutes."""
    if lat is None or lon is None:
        return None
    lat, lon = round(float(lat), 3), round(float(lon), 3)

    def fetch():
        data = _get_json(OPEN_METEO_URL, {
            "latitude": lat, "longitude": lon, "timezone": "Asia/Jakarta", "forecast_days": 7,
            "current": "temperature_2m,relative_humidity_2m,precipitation,weather_code,wind_speed_10m",
            "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_sum,precipitation_probability_max",
        })
        return _parse_weather(data) if data else None

    return _cached(f"weather:{lat}:{lon}", WEATHER_TTL, fetch)


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


def satellite_block_reason(polygon: FieldPolygon):
    """Why satellite/soil data cannot be fetched right now, or None (D79). Never calls the network."""
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
                        "1–3.000 ha. Gambar poligon yang lebih luas (mis. seluruh kebun) untuk NDVI dan data tanah."),
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


def fetch_soil(polygon: FieldPolygon):
    """Soil temperature (°C) at surface/10 cm and moisture (m³/m³). Cached 24 h."""
    pid = ensure_agro_polygon(polygon)
    if not pid:
        return None

    def fetch():
        data = _get_json(f"{AGRO_BASE}/soil", {"polyid": pid, "appid": agro_key()})
        if not data or "t10" not in data:
            return None
        return {"dt": data.get("dt"), "t0_c": round(data["t0"] - 273.15, 1) if data.get("t0") is not None else None,
                "t10_c": round(data["t10"] - 273.15, 1), "moisture": data.get("moisture")}

    return _cached(f"soil:{pid}", SAT_TTL, fetch)


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
