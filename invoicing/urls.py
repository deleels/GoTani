from django.urls import path

from . import views

app_name = "invoicing"

urlpatterns = [
    path("", views.InvoicesView.as_view(), name="invoices"),
    path("baru/", views.InvoiceEditorView.as_view(), name="invoice_new"),
    path("<int:pk>/", views.InvoiceDetailView.as_view(), name="invoice_detail"),
    path("<int:pk>/ubah/", views.InvoiceEditorView.as_view(), name="invoice_edit"),
    path("<int:pk>/cetak/", views.InvoicePrintView.as_view(), name="invoice_print"),
    path("<int:pk>/<str:action>/", views.InvoiceActionView.as_view(), name="invoice_action"),
    path("pembeli/", views.BuyersView.as_view(), name="buyers"),
    path("pembeli/<int:pk>/", views.BuyerDetailView.as_view(), name="buyer_detail"),
    path("pembeli/<int:pk>/bayar/", views.PaymentCreateView.as_view(), name="buyer_payment_new"),
    path("pembayaran/<int:pk>/hapus/", views.PaymentDeleteView.as_view(), name="buyer_payment_delete"),
]
