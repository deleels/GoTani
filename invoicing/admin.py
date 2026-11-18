from django.contrib import admin

from .models import Buyer, Invoice, InvoiceLine, InvoiceLineLotAllocation, InvoicePayment, PaymentAllocation


@admin.register(Buyer)
class BuyerAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "phone", "payment_terms_days", "is_active"]
    search_fields = ["name", "company"]


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ["__str__", "buyer", "status", "issue_date", "due_date", "total_idr"]
    list_filter = ["status"]
    inlines = [InvoiceLineInline]


@admin.register(InvoicePayment)
class InvoicePaymentAdmin(admin.ModelAdmin):
    list_display = ["buyer", "amount_idr", "method", "paid_on", "reference"]


admin.site.register(InvoiceLineLotAllocation)
admin.site.register(PaymentAllocation)
