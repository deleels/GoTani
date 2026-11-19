"""Inventory domain services (D3, D14–D16, D20, D23–D25, D36).

`move_lot_stock()` is the single choke point for lot quantity changes (rule 13):
lock the row, validate, mutate, append a ledger row. Harvest lots are created
by `farm.services.record_harvest()`, which feeds them through this function.
"""

from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import DecimalField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Grade, Product, SpoilageRecord, StockLedger, StockLot

QTY_STEP = Decimal("0.001")
MONEY_STEP = Decimal("0.01")


@transaction.atomic
def move_lot_stock(
    lot_id: int, qty_kg: Decimal, movement_type: str, reference: str = "", timestamp=None
) -> StockLot:
    """Apply a signed kg movement to one lot and log it. Returns the locked, updated lot.

    Sign convention: HARVEST_IN / POS_VOID / INVOICE_VOID positive; POS_SALE / SPOILAGE / INVOICE_SALE negative.
    """
    qty_kg = Decimal(qty_kg).quantize(QTY_STEP)
    if qty_kg == 0:
        raise ValidationError("Jumlah mutasi stok tidak boleh nol.")
    sign = StockLedger.SIGN.get(movement_type)
    if sign is None:
        raise ValidationError(f"Jenis mutasi stok tidak dikenal: {movement_type}.")
    if (qty_kg > 0) != (sign > 0):
        raise ValidationError(f"Tanda jumlah tidak sesuai untuk mutasi {movement_type}.")

    lot = StockLot.objects.select_for_update().get(pk=lot_id)
    new_remaining = lot.qty_remaining_kg + qty_kg
    if new_remaining < 0:
        raise ValidationError(
            f"Stok lot #{lot.pk} ({lot.product}) tidak cukup: tersedia "
            f"{lot.qty_remaining_kg} kg, diminta {-qty_kg} kg."
        )
    if new_remaining > lot.qty_initial_kg:
        raise ValidationError(
            f"Stok lot #{lot.pk} akan melebihi jumlah awal {lot.qty_initial_kg} kg."
        )

    lot.qty_remaining_kg = new_remaining
    lot.save(update_fields=["qty_remaining_kg"])
    StockLedger.objects.create(
        lot=lot,
        movement_type=movement_type,
        qty_kg=qty_kg,
        reference=reference,
        timestamp=timestamp or timezone.now(),
    )
    return lot


def open_lots(product: Product):
    """Lots with stock left, FIFO order (oldest harvest first)."""
    return product.lots.filter(qty_remaining_kg__gt=0).order_by("harvested_at", "id")


def oldest_open_lot(product: Product) -> StockLot | None:
    """Default lot preselected in the spoilage form (D14)."""
    return open_lots(product).first()


@transaction.atomic
def record_spoilage(
    lot: StockLot, qty_kg, reason: str, notes: str = "", user=None, recorded_at=None
) -> SpoilageRecord:
    """Write off kg from one specific lot (D14). Loss = qty × lot unit COGS (D15);
    the lot's unit COGS itself never changes."""
    qty_kg = Decimal(qty_kg).quantize(QTY_STEP)
    if qty_kg <= 0:
        raise ValidationError("Jumlah susut/rusak harus lebih dari 0 kg.")
    if reason not in SpoilageRecord.Reason.values:
        raise ValidationError(f"Alasan susut tidak dikenal: {reason}.")
    recorded_at = recorded_at or timezone.now()

    locked = StockLot.objects.select_for_update().get(pk=lot.pk)
    record = SpoilageRecord.objects.create(
        lot=locked,
        qty_kg=qty_kg,
        reason=reason,
        notes=notes,
        loss_idr=(qty_kg * locked.unit_cogs_idr).quantize(MONEY_STEP, rounding=ROUND_HALF_UP),
        recorded_by=user,
        recorded_at=recorded_at,
    )
    # Validates availability; any failure rolls back the record too.
    move_lot_stock(
        locked.pk,
        -qty_kg,
        StockLedger.MovementType.SPOILAGE,
        reference=f"SPL-{record.pk}",
        timestamp=recorded_at,
    )
    return record


@transaction.atomic
def set_product_price(product: Product, price_idr, actor=None, reason: str = "") -> Product:
    """Set (or clear with None) a product's selling price; unpriced = hidden from POS (D24).
    Every change is written to the audit log (price history, D82)."""
    if price_idr is not None:
        price_idr = Decimal(price_idr).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
        if price_idr <= 0:
            raise ValidationError("Harga jual harus lebih dari 0.")
    old = product.selling_price_idr
    product.selling_price_idr = price_idr
    product.save(update_fields=["selling_price_idr"])
    if old != price_idr:
        from core.audit import log_event

        log_event(actor, "PRICE_CHANGE", product, f"Harga {product}",
                  before={"selling_price_idr": old}, after={"selling_price_idr": price_idr}, reason=reason)
    return product


def set_pos_step(product: Product, step_kg, actor=None) -> Product:
    """Kasir quantity step (D81): − / + change the line by this many kg; chips are its multiples."""
    step_kg = Decimal(step_kg).quantize(QTY_STEP, rounding=ROUND_HALF_UP)
    if step_kg <= 0:
        raise ValidationError("Langkah kasir harus lebih dari 0 kg.")
    old = product.pos_step_kg
    product.pos_step_kg = step_kg
    product.save(update_fields=["pos_step_kg"])
    if old != step_kg:
        from core.audit import log_event

        log_event(actor, "POS_STEP_CHANGE", product, f"Langkah kasir {product}",
                  before={"pos_step_kg": old}, after={"pos_step_kg": step_kg})
    return product


# ---------------------------------------------------------------------------
# Mass-balance invariants (rule 15, D20)
# ---------------------------------------------------------------------------

_QTY_FIELD = DecimalField(max_digits=14, decimal_places=3)
_ZERO_QTY = Value(Decimal("0"), output_field=_QTY_FIELD)


def _sum_subquery(model, filter_kwargs, field):
    return Coalesce(
        Subquery(
            model.objects.filter(**filter_kwargs)
            .order_by()
            .values(*filter_kwargs.keys())
            .annotate(s=Sum(field))
            .values("s")[:1],
            output_field=_QTY_FIELD,
        ),
        _ZERO_QTY,
    )


def check_mass_balance() -> list[str]:
    """Return human-readable invariant violations; an empty list means balanced."""
    from farm.models import HarvestBatch, InputItem, InputLedger

    errors: list[str] = []

    lots = StockLot.objects.select_related("product", "harvest_batch__cycle").annotate(
        ledger_sum=_sum_subquery(StockLedger, {"lot": OuterRef("pk")}, "qty_kg"),
        spoiled_ledger=_sum_subquery(
            StockLedger,
            {"lot": OuterRef("pk"), "movement_type": StockLedger.MovementType.SPOILAGE},
            "qty_kg",
        ),
        spoiled_records=_sum_subquery(SpoilageRecord, {"lot": OuterRef("pk")}, "qty_kg"),
    )
    for lot in lots:
        tag = f"Lot #{lot.pk} ({lot.product})"
        if lot.qty_remaining_kg != lot.ledger_sum:
            errors.append(
                f"{tag}: sisa {lot.qty_remaining_kg} kg ≠ Σ ledger {lot.ledger_sum} kg."
            )
        if lot.qty_remaining_kg < 0:
            errors.append(f"{tag}: stok negatif {lot.qty_remaining_kg} kg.")
        if lot.qty_remaining_kg > lot.qty_initial_kg:
            errors.append(f"{tag}: sisa melebihi jumlah awal {lot.qty_initial_kg} kg.")
        batch = lot.harvest_batch
        expected = batch.high_quality_kg if lot.product.grade == Grade.HIGH else batch.low_quality_kg
        if lot.qty_initial_kg != expected:
            errors.append(
                f"{tag}: jumlah awal {lot.qty_initial_kg} kg ≠ kg grade batch #{batch.pk} ({expected} kg)."
            )
        if lot.product.variety_id != batch.cycle.variety_id:
            errors.append(f"{tag}: varietas produk tidak sama dengan varietas siklus batch #{batch.pk}.")
        if -lot.spoiled_ledger != lot.spoiled_records:
            errors.append(
                f"{tag}: Σ catatan susut {lot.spoiled_records} kg ≠ Σ ledger SPOILAGE {-lot.spoiled_ledger} kg."
            )

    # Every graded kg of every batch must have become a lot.
    for batch in HarvestBatch.objects.prefetch_related("lots__product"):
        grades = {lot.product.grade for lot in batch.lots.all()}
        for grade, kg in ((Grade.HIGH, batch.high_quality_kg), (Grade.LOW, batch.low_quality_kg)):
            if kg > 0 and grade not in grades:
                errors.append(f"Batch #{batch.pk}: tidak ada lot {grade} untuk {kg} kg.")

    items = InputItem.objects.annotate(
        ledger_sum=_sum_subquery(InputLedger, {"item": OuterRef("pk")}, "qty")
    )
    for item in items:
        if item.stock_qty != item.ledger_sum:
            errors.append(
                f"Input {item.name}: stok {item.stock_qty} {item.unit} ≠ Σ ledger {item.ledger_sum}."
            )
        if item.stock_qty < 0:
            errors.append(f"Input {item.name}: stok negatif {item.stock_qty} {item.unit}.")

    return errors


def mass_balance_summary() -> dict:
    """Counts reported by the management command."""
    from farm.models import InputItem

    return {
        "lots": StockLot.objects.count(),
        "ledger_rows": StockLedger.objects.count(),
        "open_kg": StockLot.objects.filter(qty_remaining_kg__gt=0).aggregate(
            s=Coalesce(Sum("qty_remaining_kg"), _ZERO_QTY)
        )["s"],
        "input_items": InputItem.objects.count(),
    }


# ---------------------------------------------------------------------------
# Tax math (rule 19, D46/D47). Money stays Decimal(14,2), ROUND_HALF_UP.
# ---------------------------------------------------------------------------

_CENT = Decimal("0.01")


def _cents(value) -> Decimal:
    return Decimal(value).quantize(_CENT, rounding=ROUND_HALF_UP)


def split_inclusive_tax(gross, rate_pct) -> tuple[Decimal, Decimal]:
    """POS: tax-inclusive gross -> (tax, net). tax = gross x rate / (100 + rate)."""
    gross = _cents(gross)
    rate = Decimal(rate_pct)
    if rate < 0 or rate > 100:
        raise ValidationError("Tarif pajak harus 0–100%.")
    tax = _cents(gross * rate / (Decimal(100) + rate)) if rate else Decimal("0.00")
    return tax, gross - tax


def exclusive_tax(base, rate_pct) -> Decimal:
    """Invoice: tax on a tax-exclusive base."""
    rate = Decimal(rate_pct)
    if rate < 0 or rate > 100:
        raise ValidationError("Tarif pajak harus 0–100%.")
    return _cents(Decimal(base) * rate / Decimal(100))
