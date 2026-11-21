from django.contrib import admin

from farm.admin import ReadOnlyLedgerAdmin

from .models import Buyer, Invoice, InvoiceLine, InvoiceLineLotAllocation, InvoicePayment, PaymentAllocation


@admin.register(Buyer)
class BuyerAdmin(admin.ModelAdmin):
    list_display = ["name", "company", "phone", "payment_terms_days", "is_active"]
    search_fields = ["name", "company"]


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


# Invoices, payments and allocations change only through invoicing.services (D123).
@admin.register(Invoice)
class InvoiceAdmin(ReadOnlyLedgerAdmin):
    list_display = ["__str__", "buyer", "status", "issue_date", "due_date", "total_idr"]
    list_filter = ["status"]
    inlines = [InvoiceLineInline]


@admin.register(InvoicePayment)
class InvoicePaymentAdmin(ReadOnlyLedgerAdmin):
    list_display = ["buyer", "amount_idr", "method", "paid_on", "reference"]


@admin.register(InvoiceLineLotAllocation)
class InvoiceLineLotAllocationAdmin(ReadOnlyLedgerAdmin):
    list_display = ["line", "lot", "qty_kg", "unit_cogs_idr"]


@admin.register(PaymentAllocation)
class PaymentAllocationAdmin(ReadOnlyLedgerAdmin):
    list_display = ["payment", "invoice", "amount_idr"]
