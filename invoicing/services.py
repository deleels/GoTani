"""Invoicing services (rules 24–29, D49–D59).

Invariants:
- ISSUED invoices hold real stock via locked FIFO allocations (`INVOICE_SALE`); VOID restores it.
- Payment status and buyer credit are derived from `PaymentAllocation`, never stored.
"""

from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from farm.bootstrap import ROLE_MANAGER, ROLE_OWNER, user_has_role
from farm.models import PaymentMethod
from inventory.models import StockLedger, StockLot
from inventory.services import exclusive_tax, move_lot_stock
from pos.services import format_kg

from .models import (
    Buyer,
    Invoice,
    InvoiceLine,
    InvoiceLineLotAllocation,
    InvoicePayment,
    PaymentAllocation,
)

ZERO = Decimal("0.00")
CENT = Decimal("0.01")
KG = Decimal("0.001")


def q_money(value) -> Decimal:
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


def require_finance(actor):
    if not user_has_role(actor, ROLE_OWNER, ROLE_MANAGER):
        raise PermissionDenied("Hanya OWNER/MANAGER yang dapat mengelola invoice dan pembayaran.")


# ---------------------------------------------------------------------------
# Totals (rule 26) — pure function, no DB.
# ---------------------------------------------------------------------------


@dataclass
class LineCalc:
    subtotal: Decimal
    discount_share: Decimal
    tax: Decimal
    net_revenue: Decimal


@dataclass
class TotalsCalc:
    subtotal: Decimal
    discount: Decimal
    tax: Decimal
    shipping: Decimal
    total: Decimal
    lines: list


def compute_totals(lines, discount_type, discount_value, shipping) -> TotalsCalc:
    """`lines`: iterable of (qty_kg, unit_price_idr, tax_rate_pct).

    Discount is spread over lines proportionally to their subtotal; the last line takes the
    rounding remainder so Σ shares == discount exactly. Tax is charged on the discounted base.
    """
    lines = list(lines)
    discount_value = Decimal(discount_value or 0)
    shipping = q_money(shipping or 0)
    if shipping < 0:
        raise ValidationError("Ongkos kirim tidak boleh negatif.")
    subtotals = [q_money(Decimal(qty) * Decimal(price)) for qty, price, _ in lines]
    subtotal = sum(subtotals, ZERO)

    if discount_type == Invoice.DiscountType.NONE:
        discount = ZERO
    elif discount_type == Invoice.DiscountType.PERCENT:
        if not 0 <= discount_value <= 100:
            raise ValidationError("Diskon persen harus 0–100.")
        discount = q_money(subtotal * discount_value / 100)
    elif discount_type == Invoice.DiscountType.AMOUNT:
        discount = q_money(discount_value)
        if discount < 0 or discount > subtotal:
            raise ValidationError("Diskon nominal harus antara 0 dan subtotal.")
    else:
        raise ValidationError("Jenis diskon tidak dikenal.")

    shares = []
    remaining = discount
    for i, sub in enumerate(subtotals):
        if i == len(subtotals) - 1:
            share = remaining
        else:
            share = q_money(discount * sub / subtotal) if subtotal else ZERO
            remaining -= share
        shares.append(share)

    calcs = []
    for (qty, price, rate), sub, share in zip(lines, subtotals, shares):
        base = sub - share
        calcs.append(LineCalc(sub, share, exclusive_tax(base, rate), base))
    tax = sum((c.tax for c in calcs), ZERO)
    return TotalsCalc(subtotal, discount, tax, shipping, subtotal - discount + tax + shipping, calcs)


# ---------------------------------------------------------------------------
# Buyers & drafts (rule 24)
# ---------------------------------------------------------------------------


def create_buyer(actor, **fields) -> Buyer:
    require_finance(actor)
    buyer = Buyer(**fields)
    buyer.full_clean()
    buyer.save()
    return buyer


def _normalize_lines(raw_lines):
    """[(product, qty, price-or-None)] -> validated [(product, qty, price)]."""
    out = []
    for product, qty, price in raw_lines:
        qty = Decimal(qty).quantize(KG, rounding=ROUND_HALF_UP)
        if qty <= 0:
            raise ValidationError(f"Jumlah {product} harus lebih dari 0 kg.")
        if price in (None, ""):
            price = product.selling_price_idr
        if price is None:
            raise ValidationError(f"{product} belum memiliki harga; isi harga per kg.")
        price = q_money(price)
        if price <= 0:
            raise ValidationError(f"Harga {product} harus lebih dari 0.")
        out.append((product, qty, price))
    if not out:
        raise ValidationError("Invoice harus memiliki minimal satu baris.")
    return out


def _apply_totals(invoice, lines):
    """Write per-line and header totals; `lines` are saved InvoiceLine rows."""
    calc = compute_totals(
        [(ln.qty_kg, ln.unit_price_idr, ln.tax_rate_pct) for ln in lines],
        invoice.discount_type, invoice.discount_value, invoice.shipping_idr,
    )
    for ln, c in zip(lines, calc.lines):
        ln.line_subtotal_idr, ln.discount_share_idr, ln.tax_idr, ln.net_revenue_idr = (
            c.subtotal, c.discount_share, c.tax, c.net_revenue)
        ln.save(update_fields=["line_subtotal_idr", "discount_share_idr", "tax_idr", "net_revenue_idr"])
    invoice.subtotal_idr, invoice.discount_idr, invoice.tax_idr, invoice.total_idr = (
        calc.subtotal, calc.discount, calc.tax, calc.total)
    invoice.save(update_fields=["subtotal_idr", "discount_idr", "tax_idr", "total_idr"])
    return calc


@transaction.atomic
def save_draft(actor, buyer, raw_lines, *, discount_type=Invoice.DiscountType.NONE, discount_value=0,
               shipping_idr=0, notes="", invoice=None) -> Invoice:
    """Create a DRAFT, or replace an existing DRAFT's lines/terms. No stock moves."""
    require_finance(actor)
    lines = _normalize_lines(raw_lines)
    if invoice is None:
        invoice = Invoice(created_by=actor)
    elif invoice.status != Invoice.Status.DRAFT:
        raise ValidationError("Hanya invoice draf yang dapat diubah.")
    invoice.buyer = buyer
    invoice.discount_type = discount_type
    invoice.discount_value = Decimal(discount_value or 0)
    invoice.shipping_idr = q_money(shipping_idr or 0)
    invoice.notes = notes
    invoice.save()
    invoice.lines.all().delete()
    saved = [
        InvoiceLine.objects.create(invoice=invoice, product=p, qty_kg=qty, unit_price_idr=price,
                                   tax_rate_pct=p.tax_rate_pct)
        for p, qty, price in lines
    ]
    _apply_totals(invoice, saved)  # validates discount/shipping
    return invoice


def delete_draft(actor, invoice):
    require_finance(actor)
    if invoice.status != Invoice.Status.DRAFT:
        raise ValidationError("Hanya invoice draf yang dapat dihapus; invoice terbit harus dibatalkan.")
    invoice.delete()


# ---------------------------------------------------------------------------
# Issue & void (rules 25, 29)
# ---------------------------------------------------------------------------


def invoice_number_prefix(day) -> str:
    return f"INV-{day:%Y%m}-"


def next_invoice_number(day) -> str:
    prefix = invoice_number_prefix(day)
    last = (
        Invoice.objects.select_for_update()
        .filter(number__startswith=prefix)
        .order_by("-number")
        .values_list("number", flat=True)
        .first()
    )
    seq = int(last.rsplit("-", 1)[1]) + 1 if last else 1
    return f"{prefix}{seq:04d}"


@transaction.atomic
def issue_invoice(invoice, actor, *, issue_date=None, due_date=None, now=None) -> Invoice:
    require_finance(actor)
    invoice = Invoice.objects.select_for_update().select_related("buyer").get(pk=invoice.pk)
    if invoice.status != Invoice.Status.DRAFT:
        raise ValidationError("Invoice ini sudah terbit atau dibatalkan.")
    lines = list(invoice.lines.select_related("product__variety", "product__tax"))
    if not lines:
        raise ValidationError("Invoice harus memiliki minimal satu baris.")
    now = now or timezone.now()
    issue_date = issue_date or timezone.localdate(now)
    due_date = due_date or issue_date + timedelta(days=invoice.buyer.payment_terms_days)
    if due_date < issue_date:
        raise ValidationError("Jatuh tempo tidak boleh sebelum tanggal terbit.")

    # Re-snapshot tax at issue time (D46), then lock lots exactly like checkout (D17).
    for ln in lines:
        ln.tax_rate_pct = ln.product.tax_rate_pct
        ln.save(update_fields=["tax_rate_pct"])
    product_ids = sorted({ln.product_id for ln in lines})
    lots_by_product = {pid: [] for pid in product_ids}
    for lot in (StockLot.objects.select_for_update()
                .filter(product_id__in=product_ids, qty_remaining_kg__gt=0)
                .order_by("product_id", "harvested_at", "id")):
        lots_by_product[lot.product_id].append(lot)

    requested = {}
    for ln in lines:
        requested[ln.product_id] = requested.get(ln.product_id, ZERO) + ln.qty_kg
    shortages = []
    for ln in lines:
        pid = ln.product_id
        available = sum((lot.qty_remaining_kg for lot in lots_by_product[pid]), ZERO)
        if available < requested[pid] and pid not in [s[0] for s in shortages]:
            shortages.append((pid, f"Stok {ln.product} tidak cukup: tersedia {format_kg(available)} kg, "
                                   f"diminta {format_kg(requested[pid])} kg."))
    if shortages:
        raise ValidationError([msg for _, msg in shortages])

    invoice.number = next_invoice_number(issue_date)
    total_cogs = ZERO
    remaining_by_lot = {lot.pk: lot.qty_remaining_kg for lots in lots_by_product.values() for lot in lots}
    for ln in lines:
        remaining = ln.qty_kg
        line_cost = ZERO
        for lot in lots_by_product[ln.product_id]:  # FIFO
            if remaining == 0:
                break
            take = min(remaining, remaining_by_lot[lot.pk])
            if take <= 0:
                continue
            remaining_by_lot[lot.pk] -= take
            InvoiceLineLotAllocation.objects.create(line=ln, lot=lot, qty_kg=take, unit_cogs_idr=lot.unit_cogs_idr)
            move_lot_stock(lot.pk, -take, StockLedger.MovementType.INVOICE_SALE,
                           reference=invoice.number, timestamp=now)
            line_cost += take * lot.unit_cogs_idr
            remaining -= take
        ln.unit_cogs_idr = q_money(line_cost / ln.qty_kg)
        ln.save(update_fields=["unit_cogs_idr"])
        total_cogs += line_cost

    _apply_totals(invoice, lines)
    invoice.status = Invoice.Status.ISSUED
    invoice.issue_date, invoice.due_date = issue_date, due_date
    invoice.issued_by, invoice.issued_at = actor, now
    invoice.total_cogs_idr = q_money(total_cogs)
    invoice.save()
    return invoice


@transaction.atomic
def void_invoice(invoice, actor, *, now=None) -> Invoice:
    """Restore exact kg to exact lots; released allocations become buyer credit (D56)."""
    require_finance(actor)
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != Invoice.Status.ISSUED:
        raise ValidationError("Hanya invoice terbit yang dapat dibatalkan.")
    now = now or timezone.now()
    allocations = list(
        InvoiceLineLotAllocation.objects.filter(line__invoice=invoice).order_by("lot__product_id", "lot__harvested_at", "lot_id")
    )
    for a in allocations:
        move_lot_stock(a.lot_id, a.qty_kg, StockLedger.MovementType.INVOICE_VOID,
                       reference=f"VOID {invoice.number}", timestamp=now)
    invoice.allocations.all().delete()
    invoice.status = Invoice.Status.VOID
    invoice.voided_by, invoice.voided_at = actor, now
    invoice.save(update_fields=["status", "voided_by", "voided_at"])
    return invoice


# ---------------------------------------------------------------------------
# Payments, allocation, credit (rules 27–29)
# ---------------------------------------------------------------------------


def buyer_credit(buyer) -> Decimal:
    paid = InvoicePayment.objects.filter(buyer=buyer).aggregate(s=Sum("amount_idr"))["s"] or ZERO
    allocated = PaymentAllocation.objects.filter(payment__buyer=buyer).aggregate(s=Sum("amount_idr"))["s"] or ZERO
    return paid - allocated


@transaction.atomic
def allocate_payment(actor, payment, invoice, amount) -> PaymentAllocation:
    require_finance(actor)
    amount = q_money(amount)
    if amount <= 0:
        raise ValidationError("Jumlah alokasi harus lebih dari 0.")
    payment = InvoicePayment.objects.select_for_update().get(pk=payment.pk)
    invoice = Invoice.objects.select_for_update().get(pk=invoice.pk)
    if invoice.status != Invoice.Status.ISSUED:
        raise ValidationError(f"{invoice} belum terbit atau sudah dibatalkan.")
    if invoice.buyer_id != payment.buyer_id:
        raise ValidationError("Pembayaran dan invoice harus milik pembeli yang sama.")
    if amount > payment.unallocated_idr:
        raise ValidationError(f"Sisa pembayaran hanya {payment.unallocated_idr}.")
    if amount > invoice.outstanding_idr:
        raise ValidationError(f"Sisa tagihan {invoice} hanya {invoice.outstanding_idr}.")
    return PaymentAllocation.objects.create(payment=payment, invoice=invoice, amount_idr=amount)


@transaction.atomic
def remove_allocation(actor, allocation, reason: str):
    """OWNER/MANAGER take a payment allocation off an invoice, with a reason (D88).

    The money returns to the buyer's credit (it is still the same payment); the invoice's derived
    payment status follows. Logged in the audit log."""
    from core.audit import log_event

    require_finance(actor)
    if not (reason or "").strip():
        raise ValidationError("Alasan wajib diisi.")
    allocation = PaymentAllocation.objects.select_for_update().select_related("invoice", "payment").get(pk=allocation.pk)
    invoice = allocation.invoice
    summary = f"Lepas pembayaran {allocation.amount_idr} dari {invoice}"
    log_event(actor, "ALLOCATION_REMOVED", invoice, summary,
              before={"amount_idr": allocation.amount_idr, "payment_id": allocation.payment_id},
              after={"amount_idr": Decimal("0")}, reason=reason.strip())
    allocation.delete()
    return invoice


@transaction.atomic
def record_payment(actor, buyer, *, amount_idr, method, paid_on, reference="", proof=None,
                   allocations=()) -> InvoicePayment:
    """Record money received; split over `allocations` [(invoice, amount)]; the rest is credit."""
    require_finance(actor)
    amount = q_money(amount_idr)
    if amount <= 0:
        raise ValidationError("Jumlah pembayaran harus lebih dari 0.")
    if method not in PaymentMethod.values:
        raise ValidationError("Metode pembayaran tidak dikenal.")
    payment = InvoicePayment.objects.create(buyer=buyer, amount_idr=amount, method=method, paid_on=paid_on,
                                            reference=reference, proof=proof or "", recorded_by=actor)
    for invoice, alloc_amount in allocations:
        if alloc_amount and Decimal(alloc_amount) > 0:
            allocate_payment(actor, payment, invoice, alloc_amount)
    return payment


@transaction.atomic
def delete_payment(actor, payment):
    if not user_has_role(actor, ROLE_OWNER):
        raise PermissionDenied("Hanya OWNER yang dapat menghapus pembayaran.")
    payment = InvoicePayment.objects.select_for_update().get(pk=payment.pk)
    if payment.allocations.exists():
        raise ValidationError("Pembayaran yang sudah dialokasikan tidak dapat dihapus.")
    payment.delete()


def receivables_summary(today=None):
    """Outstanding and overdue totals over ISSUED invoices."""
    today = today or timezone.localdate()
    outstanding = overdue = ZERO
    for inv in Invoice.objects.filter(status=Invoice.Status.ISSUED).prefetch_related("allocations"):
        due = inv.total_idr - sum((a.amount_idr for a in inv.allocations.all()), ZERO)
        if due > 0:
            outstanding += due
            if inv.due_date and inv.due_date < today:
                overdue += due
    return {"outstanding": outstanding, "overdue": overdue}


@transaction.atomic
def apply_credit(actor, buyer, invoice, amount) -> list:
    """Spend the buyer's credit on `invoice`: allocate older payments' unallocated remainders, oldest first."""
    require_finance(actor)
    amount = q_money(amount)
    if amount <= 0:
        raise ValidationError("Jumlah kredit harus lebih dari 0.")
    if amount > buyer_credit(buyer):
        raise ValidationError(f"Kredit pembeli hanya {buyer_credit(buyer)}.")
    created = []
    remaining = amount
    for payment in InvoicePayment.objects.filter(buyer=buyer).order_by("paid_on", "id"):
        if remaining <= 0:
            break
        take = min(remaining, payment.unallocated_idr)
        if take > 0:
            created.append(allocate_payment(actor, payment, invoice, take))
            remaining -= take
    return created


def update_buyer(actor, buyer, **fields) -> Buyer:
    require_finance(actor)
    for key, value in fields.items():
        setattr(buyer, key, value)
    buyer.full_clean()
    buyer.save()
    return buyer


def open_invoices(buyer):
    """ISSUED invoices of `buyer` with an outstanding balance, oldest due first."""
    out = []
    for inv in (Invoice.objects.filter(buyer=buyer, status=Invoice.Status.ISSUED)
                .prefetch_related("allocations").order_by("due_date", "id")):
        paid = sum((a.amount_idr for a in inv.allocations.all()), ZERO)
        if paid < inv.total_idr:
            inv.outstanding = inv.total_idr - paid
            out.append(inv)
    return out
