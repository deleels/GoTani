from django.contrib import admin

from farm.admin import ReadOnlyLedgerAdmin

from .models import Product, SpoilageRecord, StockLedger, StockLot, Tax


@admin.register(Tax)
class TaxAdmin(admin.ModelAdmin):
    list_display = ["name", "rate_pct", "is_active"]


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ["__str__", "variety", "grade", "selling_price_idr", "tax", "stock_kg"]
    list_filter = ["grade", "variety"]
    # Prices change only on Harga, which writes the audit log (D123).
    readonly_fields = ["selling_price_idr"]
    search_fields = ["variety__name"]

    @admin.display(description="stok (kg)")
    def stock_kg(self, obj):
        return obj.stock_kg

    AUDITED = ("selling_price_idr", "tax_id")

    def save_model(self, request, obj, form, change):
        """Admin edits land in the audit log too (D82)."""
        from core.audit import log_event

        before = dict(Product.objects.filter(pk=obj.pk).values(*self.AUDITED).first() or {}) if change else {}
        super().save_model(request, obj, form, change)
        obj.refresh_from_db()
        after = {f: getattr(obj, f) for f in self.AUDITED}
        changed = {f for f in self.AUDITED if before.get(f) != after[f]}
        if changed:
            log_event(request.user, "PRODUCT_ADMIN_EDIT", obj, f"Produk {obj} (admin)",
                      before={f: before.get(f) for f in changed}, after={f: after[f] for f in changed})


@admin.register(StockLot)
class StockLotAdmin(admin.ModelAdmin):
    list_display = ["__str__", "product", "harvest_batch", "qty_initial_kg", "qty_remaining_kg", "unit_cogs_idr", "harvested_at"]
    list_filter = ["product__grade", "product__variety"]
    date_hierarchy = "harvested_at"
    # Lots are created by harvest and mutated only by move_lot_stock (rule 13).
    readonly_fields = ["product", "harvest_batch", "qty_initial_kg", "qty_remaining_kg", "unit_cogs_idr", "harvested_at"]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SpoilageRecord)
class SpoilageRecordAdmin(ReadOnlyLedgerAdmin):
    list_display = ["recorded_at", "lot", "qty_kg", "reason", "loss_idr", "recorded_by"]
    list_filter = ["reason"]
    date_hierarchy = "recorded_at"


@admin.register(StockLedger)
class StockLedgerAdmin(ReadOnlyLedgerAdmin):
    list_display = ["timestamp", "lot", "movement_type", "qty_kg", "reference"]
    list_filter = ["movement_type"]
    search_fields = ["reference"]
    date_hierarchy = "timestamp"
