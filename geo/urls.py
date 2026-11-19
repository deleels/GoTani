from django.urls import path

from . import views

app_name = "geo"

urlpatterns = [
    path("", views.MapView.as_view(), name="map"),
    path("cuaca/", views.WeatherView.as_view(), name="weather"),
    path("poligon/", views.PolygonCreateView.as_view(), name="polygon_create"),
    path("poligon/<int:pk>/", views.PolygonDetailView.as_view(), name="polygon_detail"),
    path("poligon/<int:pk>/data/", views.PolygonDataView.as_view(), name="polygon_data"),
    path("poligon/<int:pk>/ndvi.png", views.NdviImageView.as_view(), name="polygon_ndvi"),
]
