"""POS domain services (CLAUDE.md rules 16–18; D7, D17–D19, D30).

Checkout and void are the only writers of sale orders. Lot quantities still
change exclusively through `inventory.services.move_lot_stock()` (rule 13).
Domain violations raise `ValidationError` / `PermissionDenied` with Bahasa
Indonesia messages suitable for display.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from zoneinfo import ZoneInfo

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import DecimalField, Q, Sum, Value
from django.db.models.functions import Coalesce
from django.utils import timezone

from farm.bootstrap import ROLE_CASHIER, ROLE_MANAGER, ROLE_OWNER, user_has_role
from farm.models import PaymentMethod
from inventory.models import Product, StockLedger, StockLot
from inventory.services import move_lot_stock

from .models import LineLotAllocation, OrderLineItem, SaleOrder

JAKARTA = ZoneInfo("Asia/Jakarta")
ZERO = Decimal("0")
QTY_STEP = Decimal("0.001")
MONEY_STEP = Decimal("0.01")
ORDER_PREFIX = "GT"
MAX_DAILY_ORDERS = 9999
_NUMBER_RETRIES = 5


def _q_qty(value) -> Decimal:
    return Decimal(value).quantize(QTY_STEP, rounding=ROUND_HALF_UP)


def _q_money(value) -> Decimal:
    return Decimal(value).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def jakarta_date(dt):
    """Calendar date of `dt` in Asia/Jakarta (order numbering and void window)."""
    return timezone.localtime(dt, JAKARTA).date()


def format_kg(value) -> str:
    """Human kg display: trailing zeros trimmed, Indonesian decimal comma (2,5 kg)."""
    text = f"{Decimal(value).normalize():f}"
    return text.replace(".", ",")


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
        raise ValidationError("Batas nomor order harian (9999) telah tercapai.")
    return f"{prefix}{seq:04d}"


def _create_order(payment_method, cashier, created_at) -> SaleOrder:
    for _ in range(_NUMBER_RETRIES):
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
    raise ValidationError("Gagal membuat nomor order, silakan coba lagi.")


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
            raise ValidationError("Isi keranjang tidak valid.") from None
        if qty <= 0:
            raise ValidationError("Jumlah kg setiap produk harus lebih dari 0.")
        merged[product_id] = merged.get(product_id, ZERO) + qty
    if not merged:
        raise ValidationError("Keranjang kosong.")
    return [CartLine(pid, merged[pid]) for pid in sorted(merged)]


def require_cashier(user) -> None:
    if not user_has_role(user, ROLE_OWNER, ROLE_MANAGER, ROLE_CASHIER):
        raise PermissionDenied("Hanya KASIR, MANAGER, atau OWNER yang dapat melakukan penjualan.")


@transaction.atomic
def checkout(items, payment_method: str, cashier, created_at=None) -> SaleOrder:
    """Sell the cart atomically: lock lots, validate stock, allocate FIFO, log POS_SALE.

    All-or-nothing: if any product is short, nothing is written.
    """
    require_cashier(cashier)
    if payment_method not in PaymentMethod.values:
        raise ValidationError(f"Metode pembayaran tidak dikenal: {payment_method}.")
    cart = normalize_cart(items)
    created_at = created_at or timezone.now()

    product_ids = [line.product_id for line in cart]
    products = Product.objects.select_related("variety").in_bulk(product_ids)
    for line in cart:
        product = products.get(line.product_id)
        if product is None:
            raise ValidationError(f"Produk #{line.product_id} tidak ditemukan.")
        if product.selling_price_idr is None:
            raise ValidationError(f"{product} belum memiliki harga jual dan tidak dapat dijual.")

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
                f"Stok {products[line.product_id]} tidak cukup: tersedia "
                f"{format_kg(available)} kg, diminta {format_kg(line.qty_kg)} kg."
            )
    if shortages:
        raise ValidationError(shortages)

    order = _create_order(payment_method, cashier, created_at)
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

        order_line = OrderLineItem.objects.create(
            order=order,
            product=product,
            qty_kg=line.qty_kg,
            unit_price_idr=price,
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
        total_revenue += line.qty_kg * price
        total_cogs += line_cost

    order.total_revenue_idr = _q_money(total_revenue)
    order.total_cogs_idr = _q_money(total_cogs)
    order.save(update_fields=["total_revenue_idr", "total_cogs_idr"])
    return order


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
def void_order(order: SaleOrder, user, now=None) -> SaleOrder:
    """Cancel a whole order on its own Asia/Jakarta calendar day (OWNER/MANAGER).

    Restores the exact kg to the exact lots recorded in `LineLotAllocation`.
    """
    if not user_has_role(user, ROLE_OWNER, ROLE_MANAGER):
        raise PermissionDenied("Hanya OWNER atau MANAGER yang dapat membatalkan order.")
    now = now or timezone.now()

    order = SaleOrder.objects.select_for_update().get(pk=order.pk)
    if order.status != SaleOrder.Status.COMPLETED:
        raise ValidationError(f"Order {order.order_number} sudah dibatalkan.")
    if jakarta_date(order.created_at) != jakarta_date(now):
        raise ValidationError(
            f"Order {order.order_number} hanya dapat dibatalkan pada hari yang sama "
            f"({jakarta_date(order.created_at):%d-%m-%Y})."
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

    order.status = SaleOrder.Status.VOIDED
    order.voided_by = user
    order.voided_at = now
    order.save(update_fields=["status", "voided_by", "voided_at"])
    return order
