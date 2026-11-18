from django.contrib import admin

from farm.admin import ReadOnlyLedgerAdmin

from .models import Product, SpoilageRecord, StockLedger, StockLot


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["__str__", "variety", "grade", "selling_price_idr", "stock_kg"]
    list_filter = ["grade", "variety"]
    list_editable = ["selling_price_idr"]
    search_fields = ["variety__name"]

    @admin.display(description="stok (kg)")
    def stock_kg(self, obj):
        return obj.stock_kg


@admin.register(StockLot)
class StockLotAdmin(admin.ModelAdmin):
    list_display = ["__str__", "product", "harvest_batch", "qty_initial_kg", "qty_remaining_kg", "unit_cogs_idr", "harvested_at"]
    list_filter = ["product__grade", "product__variety"]
    date_hierarchy = "harvested_at"
    # Lots are created by harvest and mutated only by move_lot_stock (rule 13).
    readonly_fields = ["product", "harvest_batch", "qty_initial_kg", "qty_remaining_kg", "unit_cogs_idr", "harvested_at"]

    def has_add_permission(self, request):
        return False


@admin.register(SpoilageRecord)
class SpoilageRecordAdmin(admin.ModelAdmin):
    list_display = ["recorded_at", "lot", "qty_kg", "reason", "loss_idr", "recorded_by"]
    list_filter = ["reason"]
    date_hierarchy = "recorded_at"
    readonly_fields = ["loss_idr"]

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(StockLedger)
class StockLedgerAdmin(ReadOnlyLedgerAdmin):
    list_display = ["timestamp", "lot", "movement_type", "qty_kg", "reference"]
    list_filter = ["movement_type"]
    search_fields = ["reference"]
    date_hierarchy = "timestamp"
