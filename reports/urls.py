from django.urls import path

from . import views

app_name = "reports"

urlpatterns = [
    path("", views.TraceIndexView.as_view(), name="trace_index"),
    path("cycle/<int:pk>/", views.TraceCycleView.as_view(), name="trace_cycle"),
    path("order/<str:order_number>/", views.TraceOrderView.as_view(), name="trace_order"),
]
