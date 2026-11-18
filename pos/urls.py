from django.urls import path

from . import views

app_name = "pos"

urlpatterns = [
    path("", views.RegisterView.as_view(), name="register"),
    path("stock/", views.StockView.as_view(), name="stock"),
    path("checkout/", views.CheckoutView.as_view(), name="checkout"),
    path("orders/", views.OrdersView.as_view(), name="orders"),
    path("receipt/<int:pk>/", views.ReceiptView.as_view(), name="receipt"),
    path("orders/<int:pk>/void/", views.VoidView.as_view(), name="void"),
]
