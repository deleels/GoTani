"""PWA (D74): installable manifest, root-scoped service worker, offline fallback page.

The service worker caches static assets only; HTML and data are network-only, so
no write can ever happen offline (offline writes would bypass row locks).
"""

import hashlib
from functools import lru_cache
from pathlib import Path

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from django.templatetags.static import static
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .services import get_farm_profile

STATIC_DIR = Path(settings.BASE_DIR) / "core" / "static" / "core"
SW_TEMPLATE = Path(settings.BASE_DIR) / "templates" / "core" / "sw.js"

# Same-origin static files pre-cached at install time.
PRECACHE = [
    "core/gotani.css",
    "core/tailwind.css",
    "core/pwa.js",
    "core/icons/icon-192.png",
    "core/icons/icon-512.png",
    "core/icons/favicon-32.png",
]


@lru_cache(maxsize=1)
def asset_version() -> str:
    """Changes whenever any app static file or the worker itself changes → new cache + update banner.

    Every same-origin /static/ file is served cache-first, so all of them must feed the version,
    not only the pre-cached ones; otherwise an edited script would never reach installed clients."""
    digest = hashlib.sha256()
    files = sorted(p for p in STATIC_DIR.rglob("*") if p.is_file())
    for path in [*files, SW_TEMPLATE]:
        try:
            digest.update(path.read_bytes())
        except OSError:
            digest.update(str(path).encode())
    return digest.hexdigest()[:12]


@require_GET
def manifest(request):
    profile = get_farm_profile()
    return JsonResponse({
        "name": profile.name or "GoTani",
        "short_name": "GoTani",
        "description": "Produksi kebun, stok per lot, kasir dan invoice.",
        "lang": "id",
        "start_url": "/?source=pwa",
        "scope": "/",
        "display": "standalone",
        "background_color": "#f7f7f7",
        "theme_color": "#175738",
        "icons": [
            {"src": static("core/icons/icon-192.png"), "sizes": "192x192", "type": "image/png"},
            {"src": static("core/icons/icon-512.png"), "sizes": "512x512", "type": "image/png"},
            {"src": static("core/icons/maskable-192.png"), "sizes": "192x192", "type": "image/png", "purpose": "maskable"},
            {"src": static("core/icons/maskable-512.png"), "sizes": "512x512", "type": "image/png", "purpose": "maskable"},
        ],
    }, content_type="application/manifest+json", json_dumps_params={"ensure_ascii": False})


@require_GET
@never_cache
def service_worker(request):
    body = SW_TEMPLATE.read_text(encoding="utf-8")
    body = body.replace("__VERSION__", asset_version())
    body = body.replace("__PRECACHE__", ", ".join(f'"{static(p)}"' for p in PRECACHE))
    body = body.replace("__OFFLINE__", "/offline/")
    body = body.replace("__STATIC_PREFIX__", settings.STATIC_URL if settings.STATIC_URL.startswith("/") else "/" + settings.STATIC_URL)
    response = HttpResponse(body, content_type="application/javascript; charset=utf-8")
    response["Service-Worker-Allowed"] = "/"
    return response


@require_GET
def offline(request):
    return render(request, "core/offline.html", {"farm_name": get_farm_profile().name})
