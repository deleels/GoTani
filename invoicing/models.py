"""Wholesale / B2B invoicing (D49–D59). Money math lives in `invoicing/services.py`."""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Q, Sum
from django.utils import timezone

from farm.models import MONEY, QTY, PaymentMethod, VoidReason
from inventory.models import Product, StockLot

ZERO = Decimal("0.00")


class Buyer(models.Model):
    """Plain customer directory (D49) — no marketplace, no verification."""

    name = models.CharField("nama", max_length=120)
    company = models.CharField("perusahaan", max_length=120, blank=True)
    phone = models.CharField("telepon", max_length=40, blank=True)
    email = models.EmailField("email", blank=True)
    address = models.TextField("alamat", blank=True)
    payment_terms_days = models.PositiveIntegerField("termin (hari)", default=0, help_text="0 = jatuh tempo saat diterima")
    notes = models.TextField("catatan", blank=True)
    is_active = models.BooleanField("aktif", default=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]
        verbose_name = "pembeli"
        verbose_name_plural = "pembeli"

    def __str__(self):
        return f"{self.name} ({self.company})" if self.company else self.name


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Draf"
        ISSUED = "ISSUED", "Terbit"
        VOID = "VOID", "Batal"

    class DiscountType(models.TextChoices):
        NONE = "NONE", "Tanpa diskon"
        PERCENT = "PERCENT", "Persen"
        AMOUNT = "AMOUNT", "Nominal"

    class PaymentStatus(models.TextChoices):  # derived, never stored (D51)
        UNPAID = "UNPAID", "Belum dibayar"
        PARTIALLY_PAID = "PARTIALLY_PAID", "Dibayar sebagian"
        PAID = "PAID", "Lunas"
        OVERDUE = "OVERDUE", "Jatuh tempo"

    number = models.CharField("nomor", max_length=20, unique=True, null=True, blank=True)
    buyer = models.ForeignKey(Buyer, on_delete=models.PROTECT, related_name="invoices", verbose_name="pembeli")
    status = models.CharField("status", max_length=10, choices=Status.choices, default=Status.DRAFT)
    issue_date = models.DateField("tanggal terbit", null=True, blank=True)
    due_date = models.DateField("jatuh tempo", null=True, blank=True)
    discount_type = models.CharField("jenis diskon", max_length=10, choices=DiscountType.choices, default=DiscountType.NONE)
    discount_value = models.DecimalField("nilai diskon", default=0, **MONEY)
    shipping_idr = models.DecimalField("ongkos kirim", default=0, **MONEY)
    subtotal_idr = models.DecimalField("subtotal", default=0, **MONEY)
    discount_idr = models.DecimalField("diskon", default=0, **MONEY)
    tax_idr = models.DecimalField("pajak", default=0, **MONEY)
    total_idr = models.DecimalField("total", default=0, **MONEY)
    total_cogs_idr = models.DecimalField("total HPP", default=0, **MONEY)
    notes = models.TextField("catatan", blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    issued_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField("alasan batal", max_length=20, choices=VoidReason.choices, blank=True)
    void_note = models.TextField("catatan batal", blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "invoice"
        verbose_name_plural = "invoice"
        constraints = [
            models.CheckConstraint(condition=Q(shipping_idr__gte=0), name="invoice_shipping_nonneg"),
            models.CheckConstraint(condition=Q(discount_value__gte=0), name="invoice_discount_nonneg"),
            models.CheckConstraint(
                condition=Q(status="DRAFT") | (Q(number__isnull=False) & Q(issue_date__isnull=False)),
                name="invoice_issued_has_number",
            ),
        ]

    def __str__(self):
        return self.number or f"Draf #{self.pk}"

    @property
    def paid_idr(self) -> Decimal:
        return self.allocations.aggregate(s=Sum("amount_idr"))["s"] or ZERO

    # D102: UU 10/2020 — a document stating an amount above Rp5.000.000 carries Rp10.000 bea meterai.
    METERAI_THRESHOLD = Decimal("5000000")

    @property
    def needs_meterai(self) -> bool:
        return self.total_idr > self.METERAI_THRESHOLD

    @property
    def outstanding_idr(self) -> Decimal:
        if self.status != self.Status.ISSUED:
            return ZERO
        return self.total_idr - self.paid_idr

    def payment_status(self, today=None):
        """Rule 28. None for DRAFT/VOID."""
        if self.status != self.Status.ISSUED:
            return None
        paid = self.paid_idr
        if paid >= self.total_idr:
            return self.PaymentStatus.PAID
        today = today or timezone.localdate()
        if self.due_date and self.due_date < today:
            return self.PaymentStatus.OVERDUE
        return self.PaymentStatus.PARTIALLY_PAID if paid > 0 else self.PaymentStatus.UNPAID


class InvoiceLine(models.Model):
    invoice = models.ForeignKey(Invoice, on_delete=models.CASCADE, related_name="lines")
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+", verbose_name="produk")
    qty_kg = models.DecimalField("kg", **QTY)
    unit_price_idr = models.DecimalField("harga per kg", **MONEY)  # tax-exclusive (D47), overridable (D52)
    line_subtotal_idr = models.DecimalField("subtotal", default=0, **MONEY)
    discount_share_idr = models.DecimalField("bagian diskon", default=0, **MONEY)
    tax_rate_pct = models.DecimalField("tarif pajak (%)", max_digits=5, decimal_places=2, default=0)
    tax_idr = models.DecimalField("pajak", default=0, **MONEY)
    net_revenue_idr = models.DecimalField("pendapatan bersih", default=0, **MONEY)
    unit_cogs_idr = models.DecimalField("HPP per kg", default=0, **MONEY)

    class Meta:
        ordering = ["id"]
        constraints = [
            models.CheckConstraint(condition=Q(qty_kg__gt=0), name="invoiceline_qty_positive"),
            models.CheckConstraint(condition=Q(unit_price_idr__gt=0), name="invoiceline_price_positive"),
        ]

    def __str__(self):
        return f"{self.product} {self.qty_kg} kg"


class InvoiceLineLotAllocation(models.Model):
    line = models.ForeignKey(InvoiceLine, on_delete=models.CASCADE, related_name="allocations")
    lot = models.ForeignKey(StockLot, on_delete=models.PROTECT, related_name="invoice_allocations")
    qty_kg = models.DecimalField("kg", **QTY)
    unit_cogs_idr = models.DecimalField("HPP per kg", **MONEY)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(qty_kg__gt=0), name="invoicealloc_qty_positive")]


class InvoicePaymentMethod(models.TextChoices):
    """Invoice settlement methods: the POS methods plus PPh withheld by the buyer (D101) — not money received."""

    CASH = "CASH", "Tunai"
    QRIS = "QRIS", "QRIS"
    TRANSFER = "TRANSFER", "Transfer"
    PPH_WITHHELD = "PPH_WITHHELD", "Potongan PPh"


class InvoicePayment(models.Model):
    """Money received, recorded by hand (D55) — no gateway. Never deleted: voided with a reason (D100)."""

    buyer = models.ForeignKey(Buyer, on_delete=models.PROTECT, related_name="payments", verbose_name="pembeli")
    amount_idr = models.DecimalField("jumlah", **MONEY)
    method = models.CharField("metode", max_length=20, choices=InvoicePaymentMethod.choices)
    withholding_slip_no = models.CharField("nomor bukti potong", max_length=60, blank=True)
    voided_at = models.DateTimeField("dibatalkan pada", null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+", verbose_name="dibatalkan oleh")
    void_reason = models.TextField("alasan batal", blank=True)
    paid_on = models.DateField("tanggal bayar")
    reference = models.CharField("referensi", max_length=120, blank=True)
    proof = models.FileField("bukti", upload_to="payments/%Y/%m/", blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-paid_on", "-id"]
        verbose_name = "pembayaran"
        verbose_name_plural = "pembayaran"
        constraints = [models.CheckConstraint(condition=Q(amount_idr__gt=0), name="payment_amount_positive")]

    def __str__(self):
        return f"Bayar {self.buyer} {self.amount_idr}"

    @property
    def allocated_idr(self) -> Decimal:
        return self.allocations.aggregate(s=Sum("amount_idr"))["s"] or ZERO

    @property
    def unallocated_idr(self) -> Decimal:
        return ZERO if self.voided_at else self.amount_idr - self.allocated_idr


class PaymentAllocation(models.Model):
    payment = models.ForeignKey(InvoicePayment, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="allocations")
    amount_idr = models.DecimalField("jumlah", **MONEY)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount_idr__gt=0), name="allocation_amount_positive")]
