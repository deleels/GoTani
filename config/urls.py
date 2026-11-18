"""GoTani URL configuration."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path

from reports.views import DashboardView, HomeView

urlpatterns = [
    path("", HomeView.as_view(), name="home"),
    path("dashboard/", DashboardView.as_view(), name="dashboard"),
    path("farm/", include("farm.urls")),
    path("pos/", include("pos.urls")),
    path("traceability/", include("reports.urls")),
    path("accounts/", include("django.contrib.auth.urls")),
    path("admin/", admin.site.urls),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
