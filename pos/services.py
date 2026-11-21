"""POS domain services (CLAUDE.md rules 16–18; D7, D17–D19, D30).

Checkout and void are the only writers of sale orders. Lot quantities still
change exclusively through `inventory.services.move_lot_stock()` (rule 13).
Domain violations raise `ValidationError` / `PermissionDenied` with Bahasa
Indonesia messages suitable for display.
"""

from django.utils.translation import gettext as _
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Avg, DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from farm.bootstrap import ROLE_CASHIER, ROLE_MANAGER, ROLE_OWNER, user_has_role
from farm.models import PaymentMethod, VoidReason
from core.numbers import parse_id_decimal
from inventory.models import Product, StockLedger, StockLot
from inventory.services import move_lot_stock, split_inclusive_tax

from .models import CashMovement, LineLotAllocation, OrderLineItem, SaleOrder, Shift

JAKARTA = ZoneInfo("Asia/Jakarta")
ZERO = Decimal("0")
QTY_STEP = Decimal("0.001")
MONEY_STEP = Decimal("0.01")
ORDER_PREFIX = "GT"
MAX_DAILY_ORDERS = 9999
_NUMBER_RETRIES = 5


def _q_qty(value) -> Decimal:
    return parse_id_decimal(value).quantize(QTY_STEP, rounding=ROUND_HALF_UP)


def _q_money(value) -> Decimal:
    return Decimal(value).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def jakarta_date(dt):
    """Calendar date of `dt` in Asia/Jakarta (order numbering and void window)."""
    return timezone.localtime(dt, JAKARTA).date()


def format_kg(value) -> str:
    """Human kg display: trailing zeros trimmed, decimal mark of the active language (2,5 / 2.5 kg)."""
    from farm.templatetags.gotani import kg

    return kg(value)


# ---------------------------------------------------------------------------
# Catalog (rule 16, D24)
# ---------------------------------------------------------------------------


def pos_catalog():
    """Priced products with their available kg (Σ open lots). Unpriced are hidden."""
    return (
        Product.objects.filter(selling_price_idr__isnull=False)
        .select_related("variety")
        .annotate(
            available_kg=Coalesce(
                Sum("lots__qty_remaining_kg", filter=Q(lots__qty_remaining_kg__gt=0)),
                Value(ZERO),
                output_field=DecimalField(max_digits=14, decimal_places=3),
            )
        )
        .order_by("variety__name", "grade")
    )


POS_BIG_LINE_FACTOR = Decimal("3")
POS_BIG_LINE_MIN_KG = Decimal("20")


def pos_big_line_thresholds(days: int = 30) -> dict:
    """Per product: kg above which a cart line triggers the "jumlah tidak biasa" warning (D87) —
    3x the average completed line of the last `days` days, never below 20 kg."""
    since = timezone.now() - timedelta(days=days)
    rows = (OrderLineItem.objects.filter(order__status=SaleOrder.Status.COMPLETED, order__created_at__gte=since)
            .values("product_id").annotate(avg=Avg("qty_kg")))
    out = {r["product_id"]: max(r["avg"] * POS_BIG_LINE_FACTOR, POS_BIG_LINE_MIN_KG) for r in rows}
    return out


# ---------------------------------------------------------------------------
# Order numbers (D30)
# ---------------------------------------------------------------------------


def order_number_prefix(day) -> str:
    return f"{ORDER_PREFIX}-{day:%Y%m%d}-"


def next_order_number(created_at) -> str:
    """Next free `GT-YYYYMMDD-####` for the Asia/Jakarta date of `created_at`.

    Must be called inside the checkout transaction; a concurrent duplicate is
    caught by the unique constraint and retried by `_create_order()`.
    """
    prefix = order_number_prefix(jakarta_date(created_at))
    last = (
        SaleOrder.objects.filter(order_number__startswith=prefix)
        .order_by("-order_number")
        .values_list("order_number", flat=True)
        .first()
    )
    seq = int(last[len(prefix):]) + 1 if last else 1
    if seq > MAX_DAILY_ORDERS:
        raise ValidationError(_("Batas nomor order harian (9999) telah tercapai."))
    return f"{prefix}{seq:04d}"


def _create_order(payment_method, cashier, created_at) -> SaleOrder:
    for _i in range(_NUMBER_RETRIES):
        try:
            with transaction.atomic():  # savepoint: a duplicate number only undoes this insert
                return SaleOrder.objects.create(
                    order_number=next_order_number(created_at),
                    payment_method=payment_method,
                    cashier=cashier,
                    created_at=created_at,
                )
        except IntegrityError:
            continue
    raise ValidationError(_("Gagal membuat nomor order, silakan coba lagi."))


# ---------------------------------------------------------------------------
# Checkout (rules 16–17; D7, D17, D18)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CartLine:
    product_id: int
    qty_kg: Decimal


def normalize_cart(items) -> list[CartLine]:
    """Accept `{product_id: kg}` or an iterable of `(product_id, kg)` / dicts with
    `product_id` + `qty_kg`. Duplicate products are merged. Any client-supplied
    price is ignored by construction (D18)."""
    if isinstance(items, Mapping):
        pairs: Iterable = items.items()
    else:
        pairs = (
            (item["product_id"], item["qty_kg"]) if isinstance(item, Mapping) else item
            for item in items
        )

    merged: dict[int, Decimal] = {}
    for product_id, qty in pairs:
        try:
            product_id = int(product_id)
            qty = _q_qty(qty)
        except (TypeError, ValueError, InvalidOperation):
            raise ValidationError(_("Isi keranjang tidak valid.")) from None
        if qty <= 0:
            raise ValidationError(_("Jumlah kg setiap produk harus lebih dari 0."))
        merged[product_id] = merged.get(product_id, ZERO) + qty
    if not merged:
        raise ValidationError(_("Keranjang kosong."))
    return [CartLine(pid, merged[pid]) for pid in sorted(merged)]


def require_cashier(user) -> None:
    if not user_has_role(user, ROLE_OWNER, ROLE_MANAGER, ROLE_CASHIER):
        raise PermissionDenied(_("Hanya KASIR, MANAGER, atau OWNER yang dapat melakukan penjualan."))


@transaction.atomic
def checkout(items, payment_method: str, cashier, created_at=None, cash_received=None) -> SaleOrder:
    """Sell the cart atomically: lock lots, validate stock, allocate FIFO, log POS_SALE.

    All-or-nothing: if any product is short, nothing is written. Needs an open shift (D96); a CASH sale
    stores the cash received and the change (blank = exact amount, D95).
    """
    require_cashier(cashier)
    if payment_method not in PaymentMethod.values:
        raise ValidationError(_("Metode pembayaran tidak dikenal: %(payment_method)s.") % {"payment_method": payment_method})
    shift = Shift.objects.select_for_update().filter(is_open=True).first()
    if shift is None:
        raise ValidationError(_("Buka shift kasir terlebih dahulu sebelum berjualan."))
    cart = normalize_cart(items)
    created_at = created_at or timezone.now()

    product_ids = [line.product_id for line in cart]
    products = Product.objects.select_related("variety", "tax").in_bulk(product_ids)
    for line in cart:
        product = products.get(line.product_id)
        if product is None:
            raise ValidationError(_("Produk #%(product_id)s tidak ditemukan.") % {"product_id": line.product_id})
        if product.selling_price_idr is None:
            raise ValidationError(_("%(product)s belum memiliki harga jual dan tidak dapat dijual.") % {"product": product})

    # D17: lock every open lot of the cart's products in one deterministic order.
    lots = list(
        StockLot.objects.select_for_update()
        .filter(product_id__in=product_ids, qty_remaining_kg__gt=0)
        .order_by("product_id", "harvested_at", "id")
    )
    lots_by_product: dict[int, list[StockLot]] = {pid: [] for pid in product_ids}
    for lot in lots:
        lots_by_product[lot.product_id].append(lot)

    shortages = []
    for line in cart:
        available = sum((lot.qty_remaining_kg for lot in lots_by_product[line.product_id]), ZERO)
        if available < line.qty_kg:
            shortages.append(
                _("Stok %(product)s tidak cukup: tersedia %(available)s kg, diminta %(requested)s kg.")
                % {"product": products[line.product_id], "available": format_kg(available), "requested": format_kg(line.qty_kg)}
            )
    if shortages:
        raise ValidationError(shortages)

    order = _create_order(payment_method, cashier, created_at)
    total_gross = ZERO
    total_tax = ZERO
    total_revenue = ZERO
    total_cogs = ZERO
    for line in cart:
        product = products[line.product_id]
        price = product.selling_price_idr  # server-side snapshot (D18)
        allocations = []
        remaining = line.qty_kg
        line_cost = ZERO
        for lot in lots_by_product[line.product_id]:  # FIFO
            if remaining == 0:
                break
            take = min(remaining, lot.qty_remaining_kg)
            allocations.append((lot, take))
            line_cost += take * lot.unit_cogs_idr
            remaining -= take

        rate = product.tax_rate_pct  # snapshot (D46); prices are tax-inclusive (D47)
        gross = _q_money(line.qty_kg * price)
        tax, net = split_inclusive_tax(gross, rate)
        order_line = OrderLineItem.objects.create(
            order=order,
            product=product,
            qty_kg=line.qty_kg,
            unit_price_idr=price,
            tax_rate_pct=rate,
            tax_idr=tax,
            net_revenue_idr=net,
            unit_cogs_idr=_q_money(line_cost / line.qty_kg),
        )
        for lot, take in allocations:
            LineLotAllocation.objects.create(
                line=order_line, lot=lot, qty_kg=take, unit_cogs_idr=lot.unit_cogs_idr
            )
            move_lot_stock(
                lot.pk,
                -take,
                StockLedger.MovementType.POS_SALE,
                reference=order.order_number,
                timestamp=created_at,
            )
        total_gross += gross
        total_tax += tax
        total_revenue += net
        total_cogs += line_cost

    order.total_gross_idr = total_gross
    order.total_tax_idr = total_tax
    order.total_revenue_idr = total_revenue
    order.total_cogs_idr = _q_money(total_cogs)
    order.shift = shift
    fields = ["total_gross_idr", "total_tax_idr", "total_revenue_idr", "total_cogs_idr", "shift"]
    if payment_method == PaymentMethod.CASH:
        due = whole_rupiah(order.total_gross_idr)
        received = due if cash_received in (None, "") else whole_rupiah(parse_id_decimal(cash_received, "money"))
        if received < due:
            raise ValidationError(_("Uang diterima %(format_rp)s kurang dari total %(format_rp2)s.") % {"format_rp": format_rp(received), "format_rp2": format_rp(due)})
        order.cash_received_idr, order.change_idr = received, received - due
        fields += ["cash_received_idr", "change_idr"]
    order.save(update_fields=fields)
    return order


def whole_rupiah(value) -> Decimal:
    """Receipt/drawer amount: whole Rupiah, ROUND_HALF_UP (units rule)."""
    return Decimal(value).quantize(Decimal("1"), rounding=ROUND_HALF_UP)


def format_rp(value) -> str:
    from farm.templatetags.gotani import rupiah

    return rupiah(whole_rupiah(value))


def validate_void_reason(reason: str, note: str) -> tuple[str, str]:
    """D94: a fixed reason; "Lainnya" needs a note."""
    note = (note or "").strip()
    if reason not in VoidReason.values:
        raise ValidationError(_("Pilih alasan pembatalan."))
    if reason == VoidReason.OTHER and not note:
        raise ValidationError(_('Alasan "Lainnya" wajib diberi catatan.'))
    return reason, note


# ---------------------------------------------------------------------------
# Shift & cash drawer (D96)
# ---------------------------------------------------------------------------

DENOMINATIONS = [100000, 50000, 20000, 10000, 5000, 2000, 1000, 500, 200, 100]
MANUAL_MOVES = (CashMovement.Kind.TOP_UP, CashMovement.Kind.DEPOSIT, CashMovement.Kind.EXPENSE)


def current_shift():
    return Shift.objects.filter(is_open=True).select_related("opened_by").first()


def suggested_float() -> Decimal:
    """Opening float prefilled from the last count."""
    last = Shift.objects.filter(is_open=False, counted_idr__isnull=False).order_by("-closed_at", "-id").first()
    return last.counted_idr if last else ZERO


@transaction.atomic
def open_shift(user, opening_float, now=None) -> Shift:
    require_cashier(user)
    amount = _q_money(parse_id_decimal(opening_float, "money"))
    if amount < 0:
        raise ValidationError(_("Modal awal tidak boleh negatif."))
    if Shift.objects.select_for_update().filter(is_open=True).exists():
        raise ValidationError(_("Masih ada shift yang terbuka; tutup dulu sebelum membuka shift baru."))
    try:
        with transaction.atomic():
            return Shift.objects.create(opened_by=user, opened_at=now or timezone.now(), opening_float_idr=amount)
    except IntegrityError:
        raise ValidationError(_("Masih ada shift yang terbuka.")) from None


def shift_summary(shift: Shift) -> dict:
    """Expected drawer cash = opening float + cash sales + top-ups/invoice cash − deposits/expenses/refunds.

    Cash sales count every CASH order of the shift (a later void is a separate refund movement)."""
    orders = list(shift.orders.all())
    cash_sales = sum((whole_rupiah(o.total_gross_idr) for o in orders if o.payment_method == PaymentMethod.CASH), ZERO)
    by_method = {}
    for o in orders:
        if o.status == SaleOrder.Status.COMPLETED:
            by_method[o.payment_method] = by_method.get(o.payment_method, ZERO) + whole_rupiah(o.total_gross_idr)
    moves = list(shift.movements.all())
    by_kind = {}
    for m in moves:
        by_kind[m.kind] = by_kind.get(m.kind, ZERO) + m.amount_idr
    expected = shift.opening_float_idr + cash_sales + sum((m.signed_idr for m in moves), ZERO)
    return {
        "opening": shift.opening_float_idr, "cash_sales": cash_sales, "by_kind": by_kind, "expected": expected,
        "orders": len(orders), "voided": sum(1 for o in orders if o.status == SaleOrder.Status.VOIDED),
        "sales_by_method": [(PaymentMethod(k).label, v) for k, v in sorted(by_method.items())],
        "movements": moves,
    }


def _locked_open(shift) -> Shift:
    shift = Shift.objects.select_for_update().get(pk=shift.pk)
    if not shift.is_open:
        raise ValidationError(_("Shift ini sudah ditutup."))
    return shift


@transaction.atomic
def add_cash_movement(shift, user, kind, amount, note="", category=None, now=None) -> CashMovement:
    """Manual drawer moves: Tambah modal / Setor ke pemilik / Bayar biaya (creates a PENDING CASH expense)."""
    from farm import services as farm

    require_cashier(user)
    if kind not in MANUAL_MOVES:
        raise ValidationError(_("Jenis mutasi kas tidak dikenal."))
    shift = _locked_open(shift)
    amount = whole_rupiah(parse_id_decimal(amount, "money"))
    if amount <= 0:
        raise ValidationError(_("Jumlah harus lebih dari 0."))
    if CashMovement.SIGN[kind] < 0 and amount > shift_summary(shift)["expected"]:
        raise ValidationError(_("Jumlah melebihi uang yang seharusnya ada di laci."))
    expense = None
    now = now or timezone.now()
    if kind == CashMovement.Kind.EXPENSE:
        if category is None:
            raise ValidationError(_("Pilih kategori biaya."))
        if not (note or "").strip():
            raise ValidationError(_("Tulis keperluan biaya."))
        expense = farm.create_expense(category, "GENERAL", amount, narration=note.strip(), payment_method="CASH",
                                      date_incurred=timezone.localdate(now))
    return CashMovement.objects.create(shift=shift, kind=kind, amount_idr=amount, note=(note or "").strip()[:255],
                                       expense=expense, created_by=user, created_at=now)


@transaction.atomic
def close_shift(shift, user, denominations: dict, note: str = "", now=None) -> Shift:
    """Blind count by denomination (D96). Closing is always allowed; a difference stays "Perlu ditinjau"
    until a manager adds a note. The opener closes; OWNER/MANAGER may force-close anyone's shift."""
    from core.audit import log_event

    require_cashier(user)
    shift = _locked_open(shift)
    manager = user_has_role(user, ROLE_OWNER, ROLE_MANAGER)
    if user.pk != shift.opened_by_id and not manager:
        raise PermissionDenied(_("Hanya pembuka shift, MANAGER, atau OWNER yang dapat menutup shift ini."))
    counts = {}
    for denom in DENOMINATIONS:
        raw = (denominations or {}).get(denom, (denominations or {}).get(str(denom), 0)) or 0
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise ValidationError(_("Jumlah lembar/keping Rp%(denom)s tidak valid.") % {"denom": denom}) from None
        if n < 0:
            raise ValidationError(_("Jumlah lembar/keping tidak boleh negatif."))
        if n:
            counts[str(denom)] = n
    counted = Decimal(sum(int(d) * n for d, n in counts.items()))
    expected = shift_summary(shift)["expected"]
    shift.is_open = False
    shift.closed_by, shift.closed_at = user, now or timezone.now()
    shift.force_closed = user.pk != shift.opened_by_id
    shift.denominations, shift.counted_idr, shift.expected_idr = counts, counted, expected
    shift.difference_idr = counted - expected
    shift.close_note = (note or "").strip()
    shift.save()
    log_event(user, "SHIFT_CLOSE", shift, f"Tutup {shift}" + (" (paksa)" if shift.force_closed else ""),
              before={"expected_idr": expected}, after={"counted_idr": counted, "difference_idr": shift.difference_idr},
              reason=shift.close_note)
    return shift


@transaction.atomic
def review_shift(shift, user, note: str) -> Shift:
    from core.audit import log_event

    if not user_has_role(user, ROLE_OWNER, ROLE_MANAGER):
        raise PermissionDenied(_("Hanya OWNER atau MANAGER yang dapat meninjau selisih kas."))
    if not (note or "").strip():
        raise ValidationError(_("Tulis catatan tinjauan."))
    shift = Shift.objects.select_for_update().get(pk=shift.pk)
    if shift.is_open:
        raise ValidationError(_("Shift masih terbuka."))
    shift.review_note, shift.reviewed_by, shift.reviewed_at = note.strip(), user, timezone.now()
    shift.save(update_fields=["review_note", "reviewed_by", "reviewed_at"])
    log_event(user, "SHIFT_REVIEW", shift, f"Tinjau selisih {shift}", after={"difference_idr": shift.difference_idr},
              reason=shift.review_note)
    return shift


# ---------------------------------------------------------------------------
# Void (rule 18, D19)
# ---------------------------------------------------------------------------


def can_void(order: SaleOrder, user, now=None) -> bool:
    """UI helper: whether the void button should be offered."""
    now = now or timezone.now()
    return (
        order.status == SaleOrder.Status.COMPLETED
        and jakarta_date(order.created_at) == jakarta_date(now)
        and user_has_role(user, ROLE_OWNER, ROLE_MANAGER)
    )


@transaction.atomic
def void_order(order: SaleOrder, user, reason: str = "", note: str = "", now=None) -> SaleOrder:
    """Cancel a whole order on its own Asia/Jakarta calendar day (OWNER/MANAGER), with a reason (D94).

    Restores the exact kg to the exact lots recorded in `LineLotAllocation`. A CASH order needs an open
    shift: the refund is a cash-out of the current shift (D96).
    """
    if not user_has_role(user, ROLE_OWNER, ROLE_MANAGER):
        raise PermissionDenied(_("Hanya OWNER atau MANAGER yang dapat membatalkan order."))
    reason, note = validate_void_reason(reason, note)
    now = now or timezone.now()

    order = SaleOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != SaleOrder.Status.COMPLETED:
        raise ValidationError(_("Order %(order_number)s sudah dibatalkan.") % {"order_number": order.order_number})
    if jakarta_date(order.created_at) != jakarta_date(now):
        raise ValidationError(
            _("Order %(order_number)s hanya dapat dibatalkan pada hari yang sama (%(jakarta_date)s).") % {"order_number": order.order_number, "jakarta_date": format(jakarta_date(order.created_at), '%d-%m-%Y')}
        )

    allocations = list(
        LineLotAllocation.objects.filter(line__order=order).select_related("lot")
    )
    # Same lock order as checkout (D17) so a void and a checkout cannot deadlock.
    list(
        StockLot.objects.select_for_update()
        .filter(pk__in={a.lot_id for a in allocations})
        .order_by("product_id", "harvested_at", "id")
    )
    for allocation in sorted(
        allocations, key=lambda a: (a.lot.product_id, a.lot.harvested_at, a.lot_id)
    ):
        move_lot_stock(
            allocation.lot_id,
            allocation.qty_kg,
            StockLedger.MovementType.POS_VOID,
            reference=order.order_number,
            timestamp=now,
        )

    if order.payment_method == PaymentMethod.CASH:
        shift = Shift.objects.select_for_update().filter(is_open=True).first()
        if shift is None:
            raise ValidationError(_("Buka shift kasir dulu: uang tunai order ini dikembalikan dari laci."))
        CashMovement.objects.create(shift=shift, kind=CashMovement.Kind.SALE_REFUND, order=order,
                                    amount_idr=whole_rupiah(order.total_gross_idr), note=order.order_number,
                                    created_by=user, created_at=now)
    order.status = SaleOrder.Status.VOIDED
    order.void_reason, order.void_note = reason, note
    order.voided_by = user
    order.voided_at = now
    order.save(update_fields=["status", "void_reason", "void_note", "voided_by", "voided_at"])
    return order
