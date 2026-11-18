from django.contrib import admin

from .models import LineLotAllocation, OrderLineItem, SaleOrder


class LineLotAllocationInline(admin.TabularInline):
    model = LineLotAllocation
    extra = 0
    can_delete = False
    readonly_fields = ["lot", "qty_kg", "unit_cogs_idr"]

    def has_add_permission(self, request, obj=None):
        return False


class OrderLineItemInline(admin.TabularInline):
    model = OrderLineItem
    extra = 0
    can_delete = False
    readonly_fields = ["product", "qty_kg", "unit_price_idr", "unit_cogs_idr"]

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(SaleOrder)
class SaleOrderAdmin(admin.ModelAdmin):
    """Orders are created by POS checkout and voided by the void service only."""

    list_display = ["order_number", "created_at", "cashier", "payment_method", "total_revenue_idr", "total_cogs_idr", "status"]
    list_filter = ["status", "payment_method", "cashier"]
    search_fields = ["order_number"]
    date_hierarchy = "created_at"
    inlines = [OrderLineItemInline]
    readonly_fields = [f.name for f in SaleOrder._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(OrderLineItem)
class OrderLineItemAdmin(admin.ModelAdmin):
    list_display = ["order", "product", "qty_kg", "unit_price_idr", "unit_cogs_idr"]
    list_filter = ["product"]
    search_fields = ["order__order_number"]
    inlines = [LineLotAllocationInline]
    readonly_fields = ["order", "product", "qty_kg", "unit_price_idr", "unit_cogs_idr"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LineLotAllocation)
class LineLotAllocationAdmin(admin.ModelAdmin):
    list_display = ["line", "lot", "qty_kg", "unit_cogs_idr"]
    search_fields = ["line__order__order_number"]
    readonly_fields = ["line", "lot", "qty_kg", "unit_cogs_idr"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
