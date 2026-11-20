"""Inventory domain services (D3, D14–D16, D20, D23–D25, D36).

`move_lot_stock()` is the single choke point for lot quantity changes (rule 13):
lock the row, validate, mutate, append a ledger row. Harvest lots are created
by `farm.services.record_harvest()`, which feeds them through this function.
"""

from django.utils.translation import gettext as _
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import DecimalField, OuterRef, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Grade, Product, SpoilageRecord, StockCount, StockLedger, StockLot

QTY_STEP = Decimal("0.001")
MONEY_STEP = Decimal("0.01")


@transaction.atomic
def move_lot_stock(
    lot_id: int, qty_kg: Decimal, movement_type: str, reference: str = "", timestamp=None
) -> StockLot:
    """Apply a signed kg movement to one lot and log it. Returns the locked, updated lot.

    Sign convention: HARVEST_IN / POS_VOID / INVOICE_VOID / SPOILAGE_REVERSAL positive;
    POS_SALE / SPOILAGE / INVOICE_SALE negative.
    """
    qty_kg = Decimal(qty_kg).quantize(QTY_STEP)
    if qty_kg == 0:
        raise ValidationError(_("Jumlah mutasi stok tidak boleh nol."))
    sign = StockLedger.SIGN.get(movement_type)
    if sign is None:
        raise ValidationError(_("Jenis mutasi stok tidak dikenal: %(movement_type)s.") % {"movement_type": movement_type})
    if (qty_kg > 0) != (sign > 0):
        raise ValidationError(_("Tanda jumlah tidak sesuai untuk mutasi %(movement_type)s.") % {"movement_type": movement_type})

    lot = StockLot.objects.select_for_update().get(pk=lot_id)
    new_remaining = lot.qty_remaining_kg + qty_kg
    if new_remaining < 0:
        raise ValidationError(
            _("Stok lot #%(pk)s (%(product)s) tidak cukup: tersedia %(qty_remaining_kg)s kg, diminta %(v)s kg.") % {"pk": lot.pk, "product": lot.product, "qty_remaining_kg": lot.qty_remaining_kg, "v": -qty_kg}
        )
    if new_remaining > lot.qty_initial_kg:
        raise ValidationError(
            _("Stok lot #%(pk)s akan melebihi jumlah awal %(qty_initial_kg)s kg.") % {"pk": lot.pk, "qty_initial_kg": lot.qty_initial_kg}
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
        raise ValidationError(_("Jumlah susut/rusak harus lebih dari 0 kg."))
    if reason not in SpoilageRecord.Reason.values:
        raise ValidationError(_("Alasan susut tidak dikenal: %(reason)s.") % {"reason": reason})
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


SPOILAGE_WARN_SHARE = Decimal("0.5")


@transaction.atomic
def reverse_spoilage(record: SpoilageRecord, actor, reason: str) -> SpoilageRecord:
    """Same-day reversal of a spoilage entry by OWNER/MANAGER, with a reason (D86).

    The kg go back to the same lot as a new SPOILAGE_REVERSAL movement; the record is kept and marked."""
    from core.audit import log_event
    from farm.bootstrap import ROLE_MANAGER, ROLE_OWNER, user_has_role

    if not user_has_role(actor, ROLE_OWNER, ROLE_MANAGER):
        raise PermissionDenied(_("Hanya OWNER atau MANAGER yang dapat membatalkan catatan susut."))
    if not (reason or "").strip():
        raise ValidationError(_("Alasan pembatalan wajib diisi."))
    record = SpoilageRecord.objects.select_for_update().get(pk=record.pk)
    if record.reversed_at:
        raise ValidationError(_("Catatan susut ini sudah dibatalkan."))
    if timezone.localtime(record.recorded_at).date() != timezone.localdate():
        raise ValidationError(_("Catatan susut hanya bisa dibatalkan di hari yang sama."))
    now = timezone.now()
    move_lot_stock(record.lot_id, record.qty_kg, StockLedger.MovementType.SPOILAGE_REVERSAL,
                   reference=f"SPL-{record.pk}-BATAL", timestamp=now)
    record.reversed_at, record.reversed_by, record.reversal_reason = now, actor, reason.strip()
    record.save(update_fields=["reversed_at", "reversed_by", "reversal_reason"])
    log_event(actor, "SPOILAGE_REVERSAL", record, f"Batal susut {record.qty_kg.normalize():f} kg — lot #{record.lot_id}",
              before={"qty_kg": record.qty_kg}, after={"qty_kg": Decimal("0")}, reason=record.reversal_reason)
    return record


@transaction.atomic
def set_product_price(product: Product, price_idr, actor=None, reason: str = "") -> Product:
    """Set (or clear with None) a product's selling price; unpriced = hidden from POS (D24).
    Every change is written to the audit log (price history, D82)."""
    if price_idr is not None:
        price_idr = Decimal(price_idr).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
        if price_idr <= 0:
            raise ValidationError(_("Harga jual harus lebih dari 0."))
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
        raise ValidationError(_("Langkah kasir harus lebih dari 0 kg."))
    old = product.pos_step_kg
    product.pos_step_kg = step_kg
    product.save(update_fields=["pos_step_kg"])
    if old != step_kg:
        from core.audit import log_event

        log_event(actor, "POS_STEP_CHANGE", product, f"Langkah kasir {product}",
                  before={"pos_step_kg": old}, after={"pos_step_kg": step_kg})
    return product


def set_min_alert(product: Product, min_kg, actor=None) -> Product:
    """Low-stock threshold for sellable produce (D98); None switches the alert off."""
    if min_kg is not None:
        min_kg = Decimal(min_kg).quantize(QTY_STEP, rounding=ROUND_HALF_UP)
        if min_kg < 0:
            raise ValidationError(_("Batas stok tidak boleh negatif."))
    if product.min_alert_kg != min_kg:
        product.min_alert_kg = min_kg
        product.save(update_fields=["min_alert_kg"])
    return product


def low_stock_products():
    """Products at or below their `min_alert_kg` (D98) as (product, stock_kg)."""
    out = []
    for p in Product.objects.filter(min_alert_kg__isnull=False).select_related("variety"):
        stock = p.stock_kg
        if stock <= p.min_alert_kg:
            out.append((p, stock))
    return out


# --- Stock opname (D99) ---------------------------------------------------------------------


def _require_decider(actor):
    from farm.bootstrap import ROLE_MANAGER, ROLE_OWNER, user_has_role

    if not user_has_role(actor, ROLE_OWNER, ROLE_MANAGER):
        raise PermissionDenied(_("Hanya OWNER atau MANAGER yang dapat menyetujui opname."))


@transaction.atomic
def record_count(product: Product, counted_kg, user, note: str = "", counted_at=None) -> StockCount:
    """A physical count; the system stock is snapshotted now. Nothing moves until approval."""
    counted = Decimal(counted_kg).quantize(QTY_STEP, rounding=ROUND_HALF_UP)
    if counted < 0:
        raise ValidationError(_("Stok fisik tidak boleh negatif."))
    if StockCount.objects.filter(product=product, status=StockCount.Status.PENDING).exists():
        raise ValidationError(_("Masih ada opname %(product)s yang menunggu persetujuan.") % {"product": product})
    return StockCount.objects.create(product=product, system_kg=product.stock_kg, counted_kg=counted,
                                     note=(note or "").strip(), counted_by=user, counted_at=counted_at or timezone.now())


@transaction.atomic
def approve_count(count: StockCount, actor) -> StockCount:
    """Write off only the shortfall, FIFO from the oldest lots, as SHRINKAGE spoilage (loss = qty × lot COGS).
    Capped at today's stock (sales since the count already took their kg). A surplus is not booked."""
    from core.audit import log_event

    _require_decider(actor)
    count = StockCount.objects.select_for_update().get(pk=count.pk)
    if count.status != StockCount.Status.PENDING:
        raise ValidationError(_("Opname ini sudah diputuskan."))
    remaining = count.shortfall_kg
    loss = Decimal("0")
    written = Decimal("0")
    lots = StockLot.objects.select_for_update().filter(product=count.product, qty_remaining_kg__gt=0) \
        .order_by("harvested_at", "id")
    for lot in lots:
        if remaining <= 0:
            break
        take = min(remaining, lot.qty_remaining_kg)
        record = record_spoilage(lot, take, SpoilageRecord.Reason.SHRINKAGE, notes=f"Opname #{count.pk}", user=actor)
        loss += record.loss_idr
        written += take
        remaining -= take
    count.status, count.decided_by, count.decided_at = StockCount.Status.APPROVED, actor, timezone.now()
    count.written_off_kg, count.loss_idr = written, loss
    count.save(update_fields=["status", "decided_by", "decided_at", "written_off_kg", "loss_idr"])
    log_event(actor, "STOCK_COUNT_APPROVED", count, f"Opname {count.product}",
              before={"system_kg": count.system_kg}, after={"counted_kg": count.counted_kg, "written_off_kg": written})
    return count


@transaction.atomic
def reject_count(count: StockCount, actor, reason: str = "") -> StockCount:
    from core.audit import log_event

    _require_decider(actor)
    count = StockCount.objects.select_for_update().get(pk=count.pk)
    if count.status != StockCount.Status.PENDING:
        raise ValidationError(_("Opname ini sudah diputuskan."))
    count.status, count.decided_by, count.decided_at = StockCount.Status.REJECTED, actor, timezone.now()
    count.save(update_fields=["status", "decided_by", "decided_at"])
    log_event(actor, "STOCK_COUNT_REJECTED", count, f"Opname {count.product} ditolak", reason=(reason or "").strip())
    return count


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
        raise ValidationError(_("Tarif pajak harus 0–100%."))
    tax = _cents(gross * rate / (Decimal(100) + rate)) if rate else Decimal("0.00")
    return tax, gross - tax


def exclusive_tax(base, rate_pct) -> Decimal:
    """Invoice: tax on a tax-exclusive base."""
    rate = Decimal(rate_pct)
    if rate < 0 or rate > 100:
        raise ValidationError(_("Tarif pajak harus 0–100%."))
    return _cents(Decimal(base) * rate / Decimal(100))
