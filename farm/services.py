"""Farm domain services (CLAUDE.md rules 1–13; D4–D13, D22–D26, D34–D38).

Views stay thin: every rule, cost computation and stock mutation lives here.
Domain violations raise `django.core.exceptions.ValidationError` with a
Bahasa Indonesia message suitable for display.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from inventory.models import Grade, Product, StockLedger, StockLot
from inventory.services import move_lot_stock

from .bootstrap import ROLE_MANAGER, ROLE_OWNER, user_has_role
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
    PreHarvestCheck,
    PreHarvestChecklistItem,
    Variety,
)

ZERO = Decimal("0")
MONEY_STEP = Decimal("0.01")
QTY_STEP = Decimal("0.001")


def q_money(value) -> Decimal:
    return Decimal(value).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def q_qty(value) -> Decimal:
    return Decimal(value).quantize(QTY_STEP, rounding=ROUND_HALF_UP)


def require_approver(user) -> None:
    """Only OWNER/MANAGER may approve, reject or revert expenses (rule 9)."""
    if not user_has_role(user, ROLE_OWNER, ROLE_MANAGER):
        raise PermissionDenied("Hanya OWNER atau MANAGER yang dapat melakukan tindakan ini.")


# ---------------------------------------------------------------------------
# Elements & cycles (rules 1, 8, 12; D26, D38)
# ---------------------------------------------------------------------------


@transaction.atomic
def set_element_active(element: FarmElement, is_active: bool) -> FarmElement:
    element = FarmElement.objects.select_for_update().get(pk=element.pk)
    if not is_active and element.cycles.filter(is_active=True).exists():
        raise ValidationError(
            f"Elemen {element.name} masih memiliki siklus tanam aktif; tutup siklusnya terlebih dahulu."
        )
    element.is_active = is_active
    element.save(update_fields=["is_active"])
    return element


@transaction.atomic
def start_cycle(element: FarmElement, variety: Variety, planting_start_date, *, planted_qty=None,
                expected_harvest_date=None, expected_yield_kg=None) -> CropCycle:
    element = FarmElement.objects.select_for_update().get(pk=element.pk)
    if not element.is_active:
        raise ValidationError(f"Elemen {element.name} tidak aktif.")
    if element.cycles.filter(is_active=True).exists():
        raise ValidationError(f"Elemen {element.name} sudah memiliki siklus tanam aktif.")
    if expected_harvest_date is None and variety.days_to_harvest:
        expected_harvest_date = planting_start_date + timedelta(days=variety.days_to_harvest)  # rule 13
    cycle = CropCycle(element=element, variety=variety, planting_start_date=planting_start_date)
    _set_estimates(cycle, planted_qty, expected_harvest_date, expected_yield_kg)
    cycle.save()
    return cycle


def _set_estimates(cycle, planted_qty, expected_harvest_date, expected_yield_kg):
    if planted_qty is not None and int(planted_qty) <= 0:
        raise ValidationError("Jumlah tanaman harus lebih dari 0.")
    if expected_yield_kg is not None and Decimal(expected_yield_kg) < 0:
        raise ValidationError("Perkiraan hasil tidak boleh negatif.")
    if expected_harvest_date and expected_harvest_date < cycle.planting_start_date:
        raise ValidationError("Perkiraan panen tidak boleh sebelum tanggal mulai tanam.")
    cycle.planted_qty = planted_qty
    cycle.expected_harvest_date = expected_harvest_date
    cycle.expected_yield_kg = q_qty(expected_yield_kg) if expected_yield_kg is not None else None


def update_cycle_estimates(cycle: CropCycle, *, planted_qty=None, expected_harvest_date=None,
                           expected_yield_kg=None) -> CropCycle:
    _set_estimates(cycle, planted_qty, expected_harvest_date, expected_yield_kg)
    cycle.save(update_fields=["planted_qty", "expected_harvest_date", "expected_yield_kg"])
    return cycle


def cycle_yield_summary(cycle: CropCycle) -> dict:
    """Rule 13: sellable kg over all batches vs `expected_yield_kg`."""
    agg = cycle.harvests.aggregate(h=Sum("high_quality_kg"), lo=Sum("low_quality_kg"), x=Sum("harvest_loss_kg"))
    high, low = agg["h"] or ZERO, agg["lo"] or ZERO
    actual = high + low
    expected = cycle.expected_yield_kg
    return {
        "actual_kg": actual, "high_kg": high, "low_kg": low, "loss_kg": agg["x"] or ZERO,
        "expected_kg": expected,
        "pct": (actual / expected * 100).quantize(Decimal("0.1")) if expected else None,
        "per_plant_kg": (actual / cycle.planted_qty).quantize(QTY_STEP) if cycle.planted_qty else None,
    }


# --- Pre-harvest checklist (rule 14, D61) -------------------------------------


@transaction.atomic
def record_preharvest_check(cycle: CropCycle, checked_items, user=None, notes="", recorded_at=None) -> PreHarvestCheck:
    if not cycle.is_active:
        raise ValidationError("Siklus sudah ditutup.")
    active = list(PreHarvestChecklistItem.objects.filter(is_active=True))
    if not active:
        raise ValidationError("Belum ada butir cek pra-panen yang aktif.")
    active_ids = {i.pk for i in active}
    chosen = {int(getattr(i, "pk", i)) for i in checked_items}
    if chosen - active_ids:
        raise ValidationError("Butir cek tidak dikenal atau tidak aktif.")
    pct = (Decimal(len(chosen)) / Decimal(len(active)) * 100).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    check = PreHarvestCheck.objects.create(cycle=cycle, completed_pct=pct, notes=notes, recorded_by=user,
                                           recorded_at=recorded_at or timezone.now())
    check.checked_items.set(chosen)
    return check


def latest_preharvest_check(cycle: CropCycle):
    return cycle.preharvest_checks.order_by("-recorded_at", "-id").first()


def harvest_readiness_warning(cycle: CropCycle, new_stage: str) -> str | None:
    """Soft warning (never a block) before HARVESTING without a complete checklist (rule 14)."""
    if new_stage != CropCycle.Stage.HARVESTING:
        return None
    check = latest_preharvest_check(cycle)
    if check is None:
        return "Peringatan: belum ada cek pra-panen untuk siklus ini."
    if check.completed_pct < 100:
        return f"Peringatan: cek pra-panen terakhir baru {check.completed_pct.normalize():f}% lengkap."
    return None


@transaction.atomic
def advance_cycle_stage(cycle: CropCycle, new_stage: str) -> CropCycle:
    """Move a cycle strictly forward in `CropCycle.STAGE_ORDER` (rule 1).

    Skipping intermediate stages is allowed; staying put or going back is not.
    Reaching CLEARING closes the cycle (rule 8).
    """
    cycle = CropCycle.objects.select_for_update().get(pk=cycle.pk)
    if new_stage not in CropCycle.STAGE_ORDER:
        raise ValidationError(f"Tahap tidak dikenal: {new_stage}.")
    if not cycle.is_active:
        raise ValidationError("Siklus sudah ditutup; tahap tidak dapat diubah.")
    if CropCycle.STAGE_ORDER.index(new_stage) <= cycle.stage_index:
        raise ValidationError(
            f"Tahap hanya boleh maju: {cycle.get_stage_display()} → "
            f"{CropCycle.Stage(new_stage).label} ditolak."
        )
    cycle.stage = new_stage
    fields = ["stage"]
    if new_stage == CropCycle.Stage.CLEARING:
        cycle.is_active = False
        cycle.closed_at = timezone.now()
        fields += ["is_active", "closed_at"]
    cycle.save(update_fields=fields)
    return cycle


def close_cycle(cycle: CropCycle) -> CropCycle:
    return advance_cycle_stage(cycle, CropCycle.Stage.CLEARING)


def unabsorbed_cycle_cost(cycle: CropCycle) -> Decimal:
    """Approved expenses never absorbed by a harvest — write-off once closed (rule 8, D11)."""
    total = Expense.objects.filter(
        cycle=cycle, status=Expense.Status.APPROVED, absorbed_by__isnull=True
    ).aggregate(s=Sum("amount_idr"))["s"]
    return total or ZERO


# ---------------------------------------------------------------------------
# Input inventory (rules 11, 13; D22, D34, D35)
# ---------------------------------------------------------------------------


def weighted_average_cost(stock_qty, avg_cost, qty, cost_per_unit) -> Decimal:
    """avg = (stock×avg + qty×cost) / (stock+qty); purchase cost if stock was 0."""
    stock_qty, avg_cost, qty, cost_per_unit = map(Decimal, (stock_qty, avg_cost, qty, cost_per_unit))
    if stock_qty <= 0:
        return q_money(cost_per_unit)
    return q_money((stock_qty * avg_cost + qty * cost_per_unit) / (stock_qty + qty))


_INPUT_SIGN_RULES = {
    InputLedger.MovementType.USAGE: lambda q: q < 0,
    InputLedger.MovementType.USAGE_REVERSAL: lambda q: q > 0,
    # Positive = restock; negative = cancellation of a deleted purchase.
    InputLedger.MovementType.PURCHASE: lambda q: q != 0,
}


@transaction.atomic
def move_input_stock(
    item_id: int,
    qty: Decimal,
    movement_type: str,
    reference: str = "",
    timestamp=None,
    new_avg_cost: Decimal | None = None,
) -> InputItem:
    """Single choke point for input stock (rule 13): lock, validate, mutate, log."""
    qty = q_qty(qty)
    check = _INPUT_SIGN_RULES.get(movement_type)
    if check is None:
        raise ValidationError(f"Jenis mutasi input tidak dikenal: {movement_type}.")
    if qty == 0 or not check(qty):
        raise ValidationError(f"Jumlah {qty} tidak valid untuk mutasi {movement_type}.")

    item = InputItem.objects.select_for_update().get(pk=item_id)
    new_stock = item.stock_qty + qty
    if new_stock < 0:
        raise ValidationError(
            f"Stok {item.name} tidak cukup: tersedia {item.stock_qty} {item.unit}, "
            f"dibutuhkan {-qty} {item.unit}."
        )
    item.stock_qty = new_stock
    fields = ["stock_qty"]
    if new_avg_cost is not None:
        item.avg_cost_per_unit_idr = q_money(max(new_avg_cost, ZERO))
        fields.append("avg_cost_per_unit_idr")
    item.save(update_fields=fields)
    InputLedger.objects.create(
        item=item,
        movement_type=movement_type,
        qty=qty,
        reference=reference,
        timestamp=timestamp or timezone.now(),
    )
    return item


@transaction.atomic
def record_input_purchase(
    item: InputItem, qty, cost_per_unit_idr, supplier: str = "", purchased_at=None, user=None
) -> InputPurchase:
    qty, cost = q_qty(qty), q_money(cost_per_unit_idr)
    if qty <= 0:
        raise ValidationError("Jumlah pembelian harus lebih dari 0.")
    if cost <= 0:
        raise ValidationError("Harga per satuan harus lebih dari 0.")
    purchased_at = purchased_at or timezone.now()

    locked = InputItem.objects.select_for_update().get(pk=item.pk)
    avg = weighted_average_cost(locked.stock_qty, locked.avg_cost_per_unit_idr, qty, cost)
    purchase = InputPurchase.objects.create(
        item=locked,
        qty=qty,
        cost_per_unit_idr=cost,
        supplier=supplier,
        purchased_at=purchased_at,
        recorded_by=user,
    )
    move_input_stock(
        locked.pk,
        qty,
        InputLedger.MovementType.PURCHASE,
        reference=f"PUR-{purchase.pk}",
        timestamp=purchased_at,
        new_avg_cost=avg,
    )
    return purchase


@transaction.atomic
def delete_input_purchase(purchase: InputPurchase) -> None:
    """Allowed only while no (still-existing) usage of the item happened after it (D35)."""
    purchase = InputPurchase.objects.select_for_update().get(pk=purchase.pk)
    item = InputItem.objects.select_for_update().get(pk=purchase.item_id)
    if ActivityLog.objects.filter(input_item=item, logged_at__gte=purchase.purchased_at).exists():
        raise ValidationError(
            "Pembelian tidak dapat dihapus karena input ini sudah dipakai setelah pembelian tersebut."
        )
    remaining = item.stock_qty - purchase.qty
    if remaining < 0:
        raise ValidationError(f"Stok {item.name} tidak cukup untuk membatalkan pembelian ini.")
    # Reverse the weighted average; when stock returns to 0 the next purchase resets it.
    if remaining > 0:
        new_avg = (
            item.stock_qty * item.avg_cost_per_unit_idr - purchase.qty * purchase.cost_per_unit_idr
        ) / remaining
    else:
        new_avg = item.avg_cost_per_unit_idr
    move_input_stock(
        item.pk,
        -purchase.qty,
        InputLedger.MovementType.PURCHASE,
        reference=f"PUR-{purchase.pk}-DEL",
        new_avg_cost=new_avg,
    )
    purchase.delete()


# Which activity consumes which kind of input item, and where its cost lands.
ACTIVITY_INPUT_TYPE = {
    ActivityLog.ActivityType.FERTILIZER: InputItem.ItemType.FERTILIZER,
    ActivityLog.ActivityType.PESTICIDE: InputItem.ItemType.PESTICIDE,
    ActivityLog.ActivityType.SEEDING: InputItem.ItemType.SEED,
}
INPUT_EXPENSE_CATEGORY = {
    InputItem.ItemType.FERTILIZER: "PUPUK",
    InputItem.ItemType.PESTICIDE: "PESTISIDA",
    InputItem.ItemType.SEED: "BIBIT",
}


@transaction.atomic
def log_activity(
    cycle: CropCycle,
    activity_type: str,
    quantity=ZERO,
    unit: str = "",
    input_item: InputItem | None = None,
    application_method: str = "",
    notes: str = "",
    logged_at=None,
    user=None,
    duration_minutes=None,
    photo=None,
) -> ActivityLog:
    """Log a field activity; consuming an input deducts stock and books an
    APPROVED INPUT_USAGE cycle expense of qty × avg cost (rule 11, D22, D34)."""
    cycle = CropCycle.objects.select_for_update().get(pk=cycle.pk)
    if not cycle.is_active:
        raise ValidationError("Kegiatan tidak dapat dicatat pada siklus yang sudah ditutup.")
    quantity = q_qty(quantity)
    if quantity < 0:
        raise ValidationError("Jumlah tidak boleh negatif.")
    logged_at = logged_at or timezone.now()

    expected_type = ACTIVITY_INPUT_TYPE.get(activity_type)
    if input_item is not None:
        if expected_type is None:
            raise ValidationError("Kegiatan ini tidak menggunakan input produksi.")
        if input_item.item_type != expected_type:
            raise ValidationError(
                f"{input_item.name} bukan input jenis {InputItem.ItemType(expected_type).label}."
            )
        if unit != input_item.unit:
            raise ValidationError(
                f"Satuan kegiatan ({unit or '-'}) harus sama dengan satuan input ({input_item.unit})."
            )
        if quantity <= 0:
            raise ValidationError("Jumlah pemakaian input harus lebih dari 0.")
    elif activity_type == ActivityLog.ActivityType.SEEDING:
        raise ValidationError("Penanaman benih harus memilih input benih.")
    # Rule 15: duration only for irrigation, photo only for scouting.
    if duration_minutes is not None:
        if activity_type != ActivityLog.ActivityType.IRRIGATION:
            raise ValidationError("Durasi hanya untuk kegiatan irigasi.")
        if int(duration_minutes) <= 0:
            raise ValidationError("Durasi irigasi harus lebih dari 0 menit.")
    if photo and activity_type != ActivityLog.ActivityType.SCOUTING:
        raise ValidationError("Foto hanya untuk kegiatan pengamatan (scouting).")

    activity = ActivityLog.objects.create(
        cycle=cycle,
        activity_type=activity_type,
        stage_at_activity=cycle.stage,
        input_item=input_item,
        quantity=quantity,
        unit=unit,
        application_method=application_method,
        notes=notes,
        logged_at=logged_at,
        recorded_by=user,
        duration_minutes=duration_minutes,
        photo=photo or "",
    )

    if input_item is not None:
        item = move_input_stock(
            input_item.pk,
            -quantity,
            InputLedger.MovementType.USAGE,
            reference=f"ACT-{activity.pk}",
            timestamp=logged_at,
        )
        amount = q_money(quantity * item.avg_cost_per_unit_idr)
        if amount > 0:
            Expense.objects.create(
                category=ExpenseCategory.objects.get(code=INPUT_EXPENSE_CATEGORY[item.item_type]),
                scope=Expense.Scope.ELEMENT,
                element=cycle.element,
                cycle=cycle,
                amount_idr=amount,
                narration=f"Pemakaian {item.name} {quantity} {item.unit}",
                date_incurred=timezone.localdate(logged_at),
                status=Expense.Status.APPROVED,
                source=Expense.Source.INPUT_USAGE,
                source_activity=activity,
                approved_by=user,
                approved_at=timezone.now(),
            )
    return activity


@transaction.atomic
def delete_activity(activity: ActivityLog) -> None:
    """Reverse input stock and delete the usage expense — only while unabsorbed (rule 11)."""
    activity = ActivityLog.objects.select_for_update().get(pk=activity.pk)
    expenses = list(Expense.objects.select_for_update().filter(source_activity=activity))
    if any(e.absorbed_by_id for e in expenses):
        raise ValidationError(
            "Kegiatan tidak dapat dihapus: biaya pemakaian inputnya sudah diserap oleh panen."
        )
    for expense in expenses:
        expense.delete()
    if activity.input_item_id:
        move_input_stock(
            activity.input_item_id,
            activity.quantity,
            InputLedger.MovementType.USAGE_REVERSAL,
            reference=f"ACT-{activity.pk}-DEL",
        )
    activity.delete()


# ---------------------------------------------------------------------------
# Expenses (rules 7, 8, 9; D9, D13, D37)
# ---------------------------------------------------------------------------


def _validate_expense_scope(scope, element, cycle) -> None:
    if scope == Expense.Scope.GENERAL:
        if element is not None or cycle is not None:
            raise ValidationError("Biaya umum tidak boleh terkait elemen atau siklus.")
    elif scope == Expense.Scope.ELEMENT:
        if element is None:
            raise ValidationError("Biaya elemen harus memilih elemen.")
        if cycle is not None and cycle.element_id != element.pk:
            raise ValidationError("Siklus tidak berada pada elemen yang dipilih.")
    else:
        raise ValidationError(f"Cakupan biaya tidak dikenal: {scope}.")


def create_expense(
    category: ExpenseCategory,
    scope: str,
    amount_idr,
    element: FarmElement | None = None,
    cycle: CropCycle | None = None,
    **fields,
) -> Expense:
    """Create a MANUAL expense in PENDING state."""
    _validate_expense_scope(scope, element, cycle)
    amount = q_money(amount_idr)
    if amount <= 0:
        raise ValidationError("Jumlah biaya harus lebih dari 0.")
    return Expense.objects.create(
        category=category,
        scope=scope,
        element=element,
        cycle=cycle,
        amount_idr=amount,
        status=Expense.Status.PENDING,
        source=Expense.Source.MANUAL,
        **fields,
    )


EDITABLE_EXPENSE_FIELDS = {
    "category", "scope", "element", "cycle", "amount_idr", "vendor", "narration",
    "date_incurred", "payment_method", "receipt",
}


@transaction.atomic
def update_expense(expense: Expense, **changes) -> Expense:
    """Edit a MANUAL expense while it is PENDING or REJECTED (approved ones must be reverted first)."""
    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    if expense.source != Expense.Source.MANUAL:
        raise ValidationError("Biaya pemakaian input tidak dapat diubah dari menu biaya.")
    if expense.status == Expense.Status.APPROVED:
        raise ValidationError("Biaya yang sudah disetujui tidak dapat diubah; kembalikan ke Menunggu dulu.")
    unknown = set(changes) - EDITABLE_EXPENSE_FIELDS
    if unknown:
        raise ValidationError(f"Field tidak dapat diubah: {', '.join(sorted(unknown))}.")
    for field, value in changes.items():
        setattr(expense, field, value)
    _validate_expense_scope(expense.scope, expense.element, expense.cycle)
    expense.amount_idr = q_money(expense.amount_idr)
    if expense.amount_idr <= 0:
        raise ValidationError("Jumlah biaya harus lebih dari 0.")
    expense.status = Expense.Status.PENDING
    expense.save()
    return expense


@transaction.atomic
def approve_expense(expense: Expense, user) -> Expense:
    """PENDING → APPROVED. A cycle-less ELEMENT expense attaches to the element's
    active cycle; with none it stays element overhead (rule 7, D9)."""
    require_approver(user)
    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    if expense.status != Expense.Status.PENDING:
        raise ValidationError("Hanya biaya berstatus Menunggu yang dapat disetujui.")
    if expense.scope == Expense.Scope.ELEMENT and expense.cycle_id is None:
        expense.cycle = expense.element.cycles.filter(is_active=True).first()
    expense.status = Expense.Status.APPROVED
    expense.approved_by = user
    expense.approved_at = timezone.now()
    expense.save(update_fields=["cycle", "status", "approved_by", "approved_at"])
    return expense


@transaction.atomic
def reject_expense(expense: Expense, user) -> Expense:
    require_approver(user)
    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    if expense.status != Expense.Status.PENDING:
        raise ValidationError("Hanya biaya berstatus Menunggu yang dapat ditolak.")
    expense.status = Expense.Status.REJECTED
    expense.approved_by = user
    expense.approved_at = timezone.now()
    expense.save(update_fields=["status", "approved_by", "approved_at"])
    return expense


@transaction.atomic
def revert_expense_to_pending(expense: Expense, user) -> Expense:
    """APPROVED-but-unabsorbed MANUAL expense → PENDING (rule 9). Absorbed ones are immutable."""
    require_approver(user)
    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    if expense.status != Expense.Status.APPROVED:
        raise ValidationError("Hanya biaya yang disetujui yang dapat dikembalikan ke Menunggu.")
    if expense.absorbed_by_id:
        raise ValidationError("Biaya sudah diserap oleh panen dan tidak dapat diubah.")
    if expense.source != Expense.Source.MANUAL:
        raise ValidationError("Biaya pemakaian input dikelola melalui log kegiatan.")
    expense.status = Expense.Status.PENDING
    expense.approved_by = None
    expense.approved_at = None
    expense.save(update_fields=["status", "approved_by", "approved_at"])
    return expense


@transaction.atomic
def delete_expense(expense: Expense) -> None:
    expense = Expense.objects.select_for_update().get(pk=expense.pk)
    if expense.source != Expense.Source.MANUAL:
        raise ValidationError("Biaya pemakaian input dihapus melalui log kegiatan.")
    if expense.status == Expense.Status.APPROVED:
        raise ValidationError("Biaya yang sudah disetujui tidak dapat dihapus.")
    expense.delete()


# ---------------------------------------------------------------------------
# Harvest & cost pool (rules 2–6, 10; D4, D5, D8, D10, D12, D13, D24)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GradeSplit:
    unit_cogs_high: Decimal
    unit_cogs_low: Decimal
    method: str  # "RSV", "EQUAL", "SINGLE_GRADE", "ZERO_YIELD"


def split_cost_by_grade(total_cost, high_kg, low_kg, high_price=None, low_price=None) -> GradeSplit:
    """Joint-cost split per kg (rule 4, 5; D5, D12).

    * zero yield → nothing to split (0, 0); caller must not absorb cost;
    * one grade only → it carries the whole cost;
    * both priced → relative sales value: share ∝ kg × price;
    * otherwise → equal per-kg COGS.
    """
    total_cost, high_kg, low_kg = Decimal(total_cost), Decimal(high_kg), Decimal(low_kg)
    if high_kg + low_kg == 0:
        return GradeSplit(ZERO, ZERO, "ZERO_YIELD")
    if low_kg == 0:
        return GradeSplit(q_money(total_cost / high_kg), ZERO, "SINGLE_GRADE")
    if high_kg == 0:
        return GradeSplit(ZERO, q_money(total_cost / low_kg), "SINGLE_GRADE")
    if high_price and low_price:
        value_high = high_kg * Decimal(high_price)
        value_low = low_kg * Decimal(low_price)
        share_high = total_cost * value_high / (value_high + value_low)
        return GradeSplit(
            q_money(share_high / high_kg),
            q_money((total_cost - share_high) / low_kg),
            "RSV",
        )
    unit = q_money(total_cost / (high_kg + low_kg))
    return GradeSplit(unit, unit, "EQUAL")


def _price_of(variety, grade):
    return (
        Product.objects.filter(variety=variety, grade=grade)
        .values_list("selling_price_idr", flat=True)
        .first()
    )


@transaction.atomic
def record_harvest(
    cycle: CropCycle,
    high_quality_kg,
    low_quality_kg=ZERO,
    harvest_loss_kg=ZERO,
    number_of_bags: int | None = None,
    weight_per_bag_kg=None,
    harvested_at=None,
    user=None,
) -> HarvestBatch:
    """Record a harvest, absorb the cycle's open cost pool and create lots (rules 2–6)."""
    cycle = CropCycle.objects.select_for_update().select_related("variety").get(pk=cycle.pk)
    if cycle.stage != CropCycle.Stage.HARVESTING or not cycle.is_active:
        raise ValidationError("Panen hanya dapat dicatat saat siklus berada pada tahap Panen.")
    high, low, loss = q_qty(high_quality_kg), q_qty(low_quality_kg), q_qty(harvest_loss_kg)
    if high <= 0:
        raise ValidationError("Berat grade HIGH harus lebih dari 0 kg.")
    if low < 0 or loss < 0:
        raise ValidationError("Berat grade LOW dan susut panen tidak boleh negatif.")
    if number_of_bags is not None and number_of_bags <= 0:
        raise ValidationError("Jumlah karung harus lebih dari 0.")
    if weight_per_bag_kg is not None and q_qty(weight_per_bag_kg) <= 0:
        raise ValidationError("Berat per karung harus lebih dari 0 kg.")
    harvested_at = harvested_at or timezone.now()

    batch = HarvestBatch.objects.create(
        cycle=cycle,
        high_quality_kg=high,
        low_quality_kg=low,
        harvest_loss_kg=loss,
        number_of_bags=number_of_bags,
        weight_per_bag_kg=weight_per_bag_kg,
        harvested_at=harvested_at,
        recorded_by=user,
    )
    _absorb_and_stock(batch, cycle)
    return batch


def _absorb_and_stock(batch: HarvestBatch, cycle: CropCycle) -> None:
    high, low = batch.high_quality_kg, batch.low_quality_kg
    if high + low == 0:
        # Zero yield (rule 5, D12): costs stay in the pool, no lots, unit COGS 0.
        return

    pool = list(
        Expense.objects.select_for_update().filter(
            cycle=cycle, status=Expense.Status.APPROVED, absorbed_by__isnull=True
        )
    )
    total = sum((e.amount_idr for e in pool), ZERO)
    split = split_cost_by_grade(
        total, high, low, _price_of(cycle.variety, Grade.HIGH), _price_of(cycle.variety, Grade.LOW)
    )

    Expense.objects.filter(pk__in=[e.pk for e in pool]).update(absorbed_by=batch)
    batch.absorbed_cost_idr = q_money(total)
    batch.unit_cogs_high_idr = split.unit_cogs_high
    batch.unit_cogs_low_idr = split.unit_cogs_low
    batch.save(update_fields=["absorbed_cost_idr", "unit_cogs_high_idr", "unit_cogs_low_idr"])

    for grade, kg, unit_cogs in (
        (Grade.HIGH, high, split.unit_cogs_high),
        (Grade.LOW, low, split.unit_cogs_low),
    ):
        if kg <= 0:
            continue
        # Auto-created products start unpriced and hidden from POS (D24).
        product, _ = Product.objects.get_or_create(
            variety=cycle.variety, grade=grade, defaults={"selling_price_idr": None}
        )
        lot = StockLot.objects.create(
            product=product,
            harvest_batch=batch,
            qty_initial_kg=kg,
            qty_remaining_kg=ZERO,
            unit_cogs_idr=unit_cogs,
            harvested_at=batch.harvested_at,
        )
        move_lot_stock(
            lot.pk,
            kg,
            StockLedger.MovementType.HARVEST_IN,
            reference=f"HB-{batch.pk}",
            timestamp=batch.harvested_at,
        )


@transaction.atomic
def delete_harvest_batch(batch: HarvestBatch) -> None:
    """Delete only while every lot has nothing but its HARVEST_IN movement;
    un-absorbs the batch's expenses in the same transaction (rule 10, D13)."""
    batch = HarvestBatch.objects.select_for_update().get(pk=batch.pk)
    lots = list(StockLot.objects.select_for_update().filter(harvest_batch=batch))
    if StockLedger.objects.filter(lot__in=lots).exclude(
        movement_type=StockLedger.MovementType.HARVEST_IN
    ).exists():
        raise ValidationError(
            "Batch panen tidak dapat dihapus: stoknya sudah terjual, rusak, atau dimutasi."
        )
    Expense.objects.filter(absorbed_by=batch).update(absorbed_by=None)
    batch.delete()  # cascades lots and their HARVEST_IN ledger rows


# --- Input usage history (D63) ------------------------------------------------


def _activity_id(reference: str):
    if reference.startswith("ACT-") and reference[4:].isdigit():
        return int(reference[4:])
    return None


def input_usage_history(item: InputItem):
    """Ledger rows (oldest first) with the activity -> cycle behind each usage and a running balance."""
    rows = list(item.ledger.order_by("timestamp", "id"))
    ids = [i for i in (_activity_id(r.reference) for r in rows) if i]
    activities = ActivityLog.objects.select_related("cycle__element", "cycle__variety").in_bulk(ids)
    balance = ZERO
    for row in rows:
        balance += row.qty
        row.balance = balance
        row.activity = activities.get(_activity_id(row.reference))
    return rows
