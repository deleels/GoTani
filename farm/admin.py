from django.contrib import admin

from .models import (
    ActivityLog,
    CropCycle,
    Expense,
    ExpenseCategory,
    FarmElement,
    HarvestBatch,
    InputItem,
    InputLedger,
    InputPurchase,
    Variety,
)


class ReadOnlyLedgerAdmin(admin.ModelAdmin):
    """Ledgers and transaction data: viewable in the back office, never edited there (D123).

    Admin edits would skip the services (stock ledger, cost pool, credit, audit log)."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Variety)
class VarietyAdmin(admin.ModelAdmin):
    list_display = ["name", "crop", "days_to_harvest", "kc"]
    list_editable = ["kc"]
    search_fields = ["name", "crop"]


@admin.register(FarmElement)
class FarmElementAdmin(admin.ModelAdmin):
    list_display = ["name", "element_type", "area_m2", "is_active"]
    list_filter = ["element_type", "is_active"]
    search_fields = ["name"]


@admin.register(CropCycle)
class CropCycleAdmin(admin.ModelAdmin):
    # Stage moves only through farm.services (rule 1, D123).
    readonly_fields = ["stage", "closed_at"]
    list_display = ["__str__", "element", "variety", "stage", "planting_start_date", "is_active", "closed_at"]
    list_filter = ["stage", "is_active", "variety", "element"]
    autocomplete_fields = ["element", "variety"]
    date_hierarchy = "planting_start_date"


@admin.register(InputItem)
class InputItemAdmin(admin.ModelAdmin):
    list_display = ["name", "item_type", "unit", "stock_qty", "avg_cost_per_unit_idr", "min_alert_qty", "is_low_stock"]
    list_filter = ["item_type", "unit"]
    search_fields = ["name"]
    # Stock and average cost change only through farm.services (rule 13).
    readonly_fields = ["stock_qty", "avg_cost_per_unit_idr"]

    @admin.display(boolean=True, description="stok rendah")
    def is_low_stock(self, obj):
        return obj.is_low_stock


@admin.register(InputPurchase)
class InputPurchaseAdmin(ReadOnlyLedgerAdmin):
    list_display = ["item", "qty", "cost_per_unit_idr", "supplier", "purchased_at", "recorded_by"]
    list_filter = ["item__item_type", "item"]
    search_fields = ["item__name", "supplier"]
    date_hierarchy = "purchased_at"


@admin.register(InputLedger)
class InputLedgerAdmin(ReadOnlyLedgerAdmin):
    list_display = ["timestamp", "item", "movement_type", "qty", "reference"]
    list_filter = ["movement_type", "item"]
    search_fields = ["reference", "item__name"]
    date_hierarchy = "timestamp"


@admin.register(ActivityLog)
class ActivityLogAdmin(ReadOnlyLedgerAdmin):
    list_display = ["logged_at", "cycle", "activity_type", "stage_at_activity", "input_item", "quantity", "unit", "recorded_by"]
    list_filter = ["activity_type", "stage_at_activity", "cycle__element"]
    search_fields = ["notes", "application_method"]
    date_hierarchy = "logged_at"


@admin.register(ExpenseCategory)
class ExpenseCategoryAdmin(admin.ModelAdmin):
    list_display = ["code", "name"]
    search_fields = ["code", "name"]


@admin.register(Expense)
class ExpenseAdmin(ReadOnlyLedgerAdmin):
    list_display = ["date_incurred", "category", "scope", "element", "cycle", "amount_idr", "status", "source", "absorbed_by"]
    list_filter = ["status", "source", "scope", "category", "payment_method"]
    search_fields = ["vendor", "narration"]
    date_hierarchy = "date_incurred"
    readonly_fields = ["absorbed_by", "approved_by", "approved_at", "source", "source_activity", "created_at"]


@admin.register(HarvestBatch)
class HarvestBatchAdmin(ReadOnlyLedgerAdmin):
    list_display = ["__str__", "cycle", "high_quality_kg", "low_quality_kg", "harvest_loss_kg", "absorbed_cost_idr", "unit_cogs_high_idr", "unit_cogs_low_idr"]
    list_filter = ["cycle__variety", "cycle__element"]
    date_hierarchy = "harvested_at"
    # Costing is computed by farm.services and never edited in place (rule 10).
    readonly_fields = ["absorbed_cost_idr", "unit_cogs_high_idr", "unit_cogs_low_idr"]
