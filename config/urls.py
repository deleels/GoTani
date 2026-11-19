"""GoTani URL configuration."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from core import pwa
from reports.views import DashboardView, HomeView, OmzetPphView

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("dashboard/omzet-pph/", OmzetPphView.as_view(), name="omzet_pph"),
    path("manifest.webmanifest", pwa.manifest, name="manifest"),
    path("sw.js", pwa.service_worker, name="service_worker"),
    path("offline/", pwa.offline, name="offline"),
    path("farm/", include("farm.urls")),
    path("pos/", include("pos.urls")),
    path("traceability/", include("reports.urls")),
    path("invoice/", include("invoicing.urls")),
    path("peta/", include("geo.urls")),
    path("", include("core.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
