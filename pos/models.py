"""Retail checkout models (D7, D17, D18, D19, D30)."""

from django.utils.translation import gettext_lazy as _l
from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from farm.models import MONEY, QTY, PaymentMethod, VoidReason
from inventory.models import Product, StockLot


class SaleOrder(models.Model):
    class Status(models.TextChoices):
        COMPLETED = "COMPLETED", _l("Selesai")
        VOIDED = "VOIDED", _l("Dibatalkan")

    # GT-YYYYMMDD-#### (Asia/Jakarta date), assigned inside the checkout transaction.
    order_number = models.CharField(_l("nomor order"), max_length=20, unique=True)
    payment_method = models.CharField(
        _l("metode pembayaran"), max_length=10, choices=PaymentMethod.choices
    )
    total_gross_idr = models.DecimalField(_l("total dibayar"), default=0, **MONEY)  # tax-inclusive
    total_tax_idr = models.DecimalField(_l("total pajak"), default=0, **MONEY)
    total_revenue_idr = models.DecimalField(_l("total pendapatan"), default=0, **MONEY)  # net of tax (D48)
    total_cogs_idr = models.DecimalField(_l("total HPP"), default=0, **MONEY)
    status = models.CharField(
        _l("status"), max_length=10, choices=Status.choices, default=Status.COMPLETED
    )
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sale_orders",
        verbose_name=_l("kasir"),
    )
    created_at = models.DateTimeField(_l("dibuat pada"), default=timezone.now)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="voided_orders",
        verbose_name=_l("dibatalkan oleh"),
    )
    voided_at = models.DateTimeField(_l("dibatalkan pada"), null=True, blank=True)
    void_reason = models.CharField(_l("alasan batal"), max_length=20, choices=VoidReason.choices, blank=True)
    void_note = models.TextField(_l("catatan batal"), blank=True)
    # D95: CASH only — what the customer handed over and the change given (printed on the receipt).
    cash_received_idr = models.DecimalField(_l("uang diterima"), null=True, blank=True, **MONEY)
    change_idr = models.DecimalField(_l("kembalian"), null=True, blank=True, **MONEY)
    # D96: every sale belongs to the open shift (nullable only for sales made before shifts existed).
    shift = models.ForeignKey("Shift", on_delete=models.PROTECT, null=True, blank=True, related_name="orders",
                              verbose_name=_l("shift"))

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _l("order penjualan")
        verbose_name_plural = _l("order penjualan")
        constraints = [
            models.CheckConstraint(
                condition=Q(total_revenue_idr__gte=0, total_cogs_idr__gte=0),
                name="saleorder_totals_nonnegative",
            ),
            models.CheckConstraint(
                condition=(
                    Q(status="COMPLETED", voided_at__isnull=True, voided_by__isnull=True)
                    | Q(status="VOIDED", voided_at__isnull=False)
                ),
                name="saleorder_void_fields_consistent",
            ),
        ]
        indexes = [models.Index(fields=["created_at"])]

    def __str__(self):
        return self.order_number


class Shift(models.Model):
    """One cash drawer, at most one open shift (D96). Blind count by denomination at close."""

    opened_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
                                  verbose_name=_l("dibuka oleh"))
    opened_at = models.DateTimeField(_l("dibuka pada"), default=timezone.now)
    opening_float_idr = models.DecimalField(_l("modal awal"), **MONEY)
    is_open = models.BooleanField(_l("terbuka"), default=True)
    closed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+", verbose_name=_l("ditutup oleh"))
    closed_at = models.DateTimeField(_l("ditutup pada"), null=True, blank=True)
    force_closed = models.BooleanField(_l("ditutup paksa"), default=False)
    denominations = models.JSONField(_l("hitungan pecahan"), default=dict, blank=True)
    counted_idr = models.DecimalField(_l("uang dihitung"), null=True, blank=True, **MONEY)
    expected_idr = models.DecimalField(_l("uang seharusnya"), null=True, blank=True, **MONEY)
    difference_idr = models.DecimalField(_l("selisih"), null=True, blank=True, **MONEY)
    close_note = models.TextField(_l("catatan tutup"), blank=True)
    review_note = models.TextField(_l("catatan tinjauan"), blank=True)
    reviewed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                    related_name="+", verbose_name=_l("ditinjau oleh"))
    reviewed_at = models.DateTimeField(_l("ditinjau pada"), null=True, blank=True)

    class Meta:
        ordering = ["-opened_at", "-id"]
        verbose_name = _l("shift kasir")
        verbose_name_plural = _l("shift kasir")
        constraints = [
            # One drawer → at most one open shift. A partial index on a boolean holds on SQLite and PostgreSQL
            # alike (a unique index over NULL closed_at would not: NULLs are distinct).
            models.UniqueConstraint(fields=["is_open"], condition=Q(is_open=True), name="one_open_shift"),
            models.CheckConstraint(condition=Q(opening_float_idr__gte=0), name="shift_float_nonnegative"),
        ]

    def __str__(self):
        return f"Shift #{self.pk} ({timezone.localtime(self.opened_at):%d-%m-%Y %H:%M})"

    @property
    def needs_review(self):
        """Closed with a difference and no manager note yet → "Perlu ditinjau"."""
        return (not self.is_open and self.difference_idr not in (None, 0) and not self.review_note)


class CashMovement(models.Model):
    """Money in/out of the drawer during a shift other than POS sales (D96)."""

    class Kind(models.TextChoices):
        TOP_UP = "TOP_UP", _l("Tambah modal")
        DEPOSIT = "DEPOSIT", _l("Setor ke pemilik")
        EXPENSE = "EXPENSE", _l("Bayar biaya")
        SALE_REFUND = "SALE_REFUND", _l("Pengembalian order batal")
        INVOICE_PAYMENT = "INVOICE_PAYMENT", _l("Pembayaran invoice")
        PAYMENT_VOID = "PAYMENT_VOID", _l("Batal pembayaran invoice")

    SIGN = {Kind.TOP_UP: 1, Kind.INVOICE_PAYMENT: 1, Kind.DEPOSIT: -1, Kind.EXPENSE: -1, Kind.SALE_REFUND: -1,
            Kind.PAYMENT_VOID: -1}

    shift = models.ForeignKey(Shift, on_delete=models.PROTECT, related_name="movements", verbose_name=_l("shift"))
    kind = models.CharField(_l("jenis"), max_length=20, choices=Kind.choices)
    amount_idr = models.DecimalField(_l("jumlah"), **MONEY)
    note = models.CharField(_l("catatan"), max_length=255, blank=True)
    order = models.ForeignKey(SaleOrder, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    expense = models.ForeignKey("farm.Expense", on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    payment = models.ForeignKey("invoicing.InvoicePayment", on_delete=models.PROTECT, null=True, blank=True,
                                related_name="cash_movements")
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(_l("waktu"), default=timezone.now)

    class Meta:
        ordering = ["created_at", "id"]
        verbose_name = _l("mutasi kas")
        verbose_name_plural = _l("mutasi kas")
        constraints = [models.CheckConstraint(condition=Q(amount_idr__gt=0), name="cash_movement_positive")]

    @property
    def signed_idr(self):
        return self.amount_idr * self.SIGN[self.kind]


class OrderLineItem(models.Model):
    order = models.ForeignKey(
        SaleOrder, on_delete=models.CASCADE, related_name="lines", verbose_name=_l("order")
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="order_lines", verbose_name=_l("produk")
    )
    qty_kg = models.DecimalField(_l("kg"), **QTY)
    unit_price_idr = models.DecimalField(_l("harga per kg"), **MONEY)  # server-side snapshot, tax-inclusive
    tax_rate_pct = models.DecimalField(_l("tarif pajak (%)"), max_digits=5, decimal_places=2, default=0)
    tax_idr = models.DecimalField(_l("pajak"), default=0, **MONEY)
    net_revenue_idr = models.DecimalField(_l("pendapatan bersih"), default=0, **MONEY)
    unit_cogs_idr = models.DecimalField(_l("HPP per kg"), **MONEY)  # weighted avg of allocations

    class Meta:
        ordering = ["order_id", "id"]
        verbose_name = _l("baris order")
        verbose_name_plural = _l("baris order")
        constraints = [
            models.CheckConstraint(condition=Q(qty_kg__gt=0), name="orderline_qty_positive"),
            models.CheckConstraint(
                condition=Q(unit_price_idr__gte=0, unit_cogs_idr__gte=0),
                name="orderline_amounts_nonnegative",
            ),
            models.UniqueConstraint(fields=["order", "product"], name="orderline_unique_product"),
        ]

    def __str__(self):
        return f"{self.product} × {self.qty_kg} kg"

    @property
    def subtotal_idr(self):
        return self.qty_kg * self.unit_price_idr


class LineLotAllocation(models.Model):
    line = models.ForeignKey(
        OrderLineItem, on_delete=models.CASCADE, related_name="allocations", verbose_name=_l("baris")
    )
    # PROTECT: a sold lot blocks its batch's deletion (rule 10).
    lot = models.ForeignKey(
        StockLot, on_delete=models.PROTECT, related_name="allocations", verbose_name=_l("lot")
    )
    qty_kg = models.DecimalField(_l("kg"), **QTY)
    unit_cogs_idr = models.DecimalField(_l("HPP per kg"), **MONEY)

    class Meta:
        ordering = ["line_id", "id"]
        verbose_name = _l("alokasi lot")
        verbose_name_plural = _l("alokasi lot")
        constraints = [
            models.CheckConstraint(condition=Q(qty_kg__gt=0), name="allocation_qty_positive"),
            models.UniqueConstraint(fields=["line", "lot"], name="allocation_unique_line_lot"),
        ]

    def __str__(self):
        return f"{self.line} ← Lot #{self.lot_id} ({self.qty_kg} kg)"
