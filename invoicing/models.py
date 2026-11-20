"""Wholesale / B2B invoicing (D49–D59). Money math lives in `invoicing/services.py`."""

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _l
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

    name = models.CharField(_l("nama"), max_length=120)
    company = models.CharField(_l("perusahaan"), max_length=120, blank=True)
    phone = models.CharField(_l("telepon"), max_length=40, blank=True)
    email = models.EmailField(_l("email"), blank=True)
    address = models.TextField(_l("alamat"), blank=True)
    payment_terms_days = models.PositiveIntegerField(_l("termin (hari)"), default=0, help_text=_l("0 = jatuh tempo saat diterima"))
    notes = models.TextField(_l("catatan"), blank=True)
    is_active = models.BooleanField(_l("aktif"), default=True)
    document_language = models.CharField(_l("bahasa dokumen"), max_length=5, default="id", choices=settings.LANGUAGES,
                                         help_text=_l("Bahasa invoice yang dicetak untuk pembeli ini."))
    # Phase 7 (D114): only buyers that withhold income tax (PPh 22/23) get the "Tax withheld" payment method.
    withholds_tax = models.BooleanField(
        _l("memotong PPh"), default=False,
        help_text=_l("Centang untuk pembeli besar (BUMN, pabrik, distributor) yang memotong PPh dari pembayaran dan "
                     "memberi bukti potong."))
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["name"]
        verbose_name = _l("pembeli")
        verbose_name_plural = _l("pembeli")

    def __str__(self):
        return f"{self.name} ({self.company})" if self.company else self.name


class Invoice(models.Model):
    class Status(models.TextChoices):
        DRAFT = "DRAFT", _l("Draf")
        ISSUED = "ISSUED", _l("Terbit")
        VOID = "VOID", _l("Batal")

    class DiscountType(models.TextChoices):
        NONE = "NONE", _l("Tanpa diskon")
        PERCENT = "PERCENT", _l("Persen")
        AMOUNT = "AMOUNT", _l("Nominal")

    class PaymentStatus(models.TextChoices):  # derived, never stored (D51)
        UNPAID = "UNPAID", _l("Belum dibayar")
        PARTIALLY_PAID = "PARTIALLY_PAID", _l("Dibayar sebagian")
        PAID = "PAID", _l("Lunas")
        OVERDUE = "OVERDUE", _l("Jatuh tempo")

    number = models.CharField(_l("nomor"), max_length=20, unique=True, null=True, blank=True)
    buyer = models.ForeignKey(Buyer, on_delete=models.PROTECT, related_name="invoices", verbose_name=_l("pembeli"))
    status = models.CharField(_l("status"), max_length=10, choices=Status.choices, default=Status.DRAFT)
    issue_date = models.DateField(_l("tanggal terbit"), null=True, blank=True)
    due_date = models.DateField(_l("jatuh tempo"), null=True, blank=True)
    discount_type = models.CharField(_l("jenis diskon"), max_length=10, choices=DiscountType.choices, default=DiscountType.NONE)
    discount_value = models.DecimalField(_l("nilai diskon"), default=0, **MONEY)
    shipping_idr = models.DecimalField(_l("ongkos kirim"), default=0, **MONEY)
    subtotal_idr = models.DecimalField(_l("subtotal"), default=0, **MONEY)
    discount_idr = models.DecimalField(_l("diskon"), default=0, **MONEY)
    tax_idr = models.DecimalField(_l("pajak"), default=0, **MONEY)
    total_idr = models.DecimalField(_l("total"), default=0, **MONEY)
    total_cogs_idr = models.DecimalField(_l("total HPP"), default=0, **MONEY)
    notes = models.TextField(_l("catatan"), blank=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)
    issued_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    issued_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name="+")
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(_l("alasan batal"), max_length=20, choices=VoidReason.choices, blank=True)
    void_note = models.TextField(_l("catatan batal"), blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = _l("invoice")
        verbose_name_plural = _l("invoice")
        constraints = [
            models.CheckConstraint(condition=Q(shipping_idr__gte=0), name="invoice_shipping_nonneg"),
            models.CheckConstraint(condition=Q(discount_value__gte=0), name="invoice_discount_nonneg"),
            models.CheckConstraint(
                condition=Q(status="DRAFT") | (Q(number__isnull=False) & Q(issue_date__isnull=False)),
                name="invoice_issued_has_number",
            ),
        ]

    def __str__(self):
        return self.number or _("Draf #%(pk)s") % {"pk": self.pk}

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
    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="+", verbose_name=_l("produk"))
    qty_kg = models.DecimalField(_l("kg"), **QTY)
    unit_price_idr = models.DecimalField(_l("harga per kg"), **MONEY)  # tax-exclusive (D47), overridable (D52)
    line_subtotal_idr = models.DecimalField(_l("subtotal"), default=0, **MONEY)
    discount_share_idr = models.DecimalField(_l("bagian diskon"), default=0, **MONEY)
    tax_rate_pct = models.DecimalField(_l("tarif pajak (%)"), max_digits=5, decimal_places=2, default=0)
    tax_idr = models.DecimalField(_l("pajak"), default=0, **MONEY)
    net_revenue_idr = models.DecimalField(_l("pendapatan bersih"), default=0, **MONEY)
    unit_cogs_idr = models.DecimalField(_l("HPP per kg"), default=0, **MONEY)

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
    qty_kg = models.DecimalField(_l("kg"), **QTY)
    unit_cogs_idr = models.DecimalField(_l("HPP per kg"), **MONEY)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(qty_kg__gt=0), name="invoicealloc_qty_positive")]


class InvoicePaymentMethod(models.TextChoices):
    """Invoice settlement methods: the POS methods plus PPh withheld by the buyer (D101) — not money received."""

    CASH = "CASH", _l("Tunai")
    QRIS = "QRIS", _l("QRIS")
    TRANSFER = "TRANSFER", _l("Transfer")
    PPH_WITHHELD = "PPH_WITHHELD", _l("Potongan PPh")


class InvoicePayment(models.Model):
    """Money received, recorded by hand (D55) — no gateway. Never deleted: voided with a reason (D100)."""

    buyer = models.ForeignKey(Buyer, on_delete=models.PROTECT, related_name="payments", verbose_name=_l("pembeli"))
    amount_idr = models.DecimalField(_l("jumlah"), **MONEY)
    method = models.CharField(_l("metode"), max_length=20, choices=InvoicePaymentMethod.choices)
    withholding_slip_no = models.CharField(_l("nomor bukti potong"), max_length=60, blank=True)
    voided_at = models.DateTimeField(_l("dibatalkan pada"), null=True, blank=True)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                  related_name="+", verbose_name=_l("dibatalkan oleh"))
    void_reason = models.TextField(_l("alasan batal"), blank=True)
    paid_on = models.DateField(_l("tanggal bayar"))
    reference = models.CharField(_l("referensi"), max_length=120, blank=True)
    proof = models.FileField(_l("bukti"), upload_to="payments/%Y/%m/", blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-paid_on", "-id"]
        verbose_name = _l("pembayaran")
        verbose_name_plural = _l("pembayaran")
        constraints = [models.CheckConstraint(condition=Q(amount_idr__gt=0), name="payment_amount_positive")]

    def __str__(self):
        return _("Bayar %(buyer)s %(amount)s") % {"buyer": self.buyer, "amount": self.amount_idr}

    @property
    def allocated_idr(self) -> Decimal:
        return self.allocations.aggregate(s=Sum("amount_idr"))["s"] or ZERO

    @property
    def unallocated_idr(self) -> Decimal:
        return ZERO if self.voided_at else self.amount_idr - self.allocated_idr


class PaymentAllocation(models.Model):
    payment = models.ForeignKey(InvoicePayment, on_delete=models.CASCADE, related_name="allocations")
    invoice = models.ForeignKey(Invoice, on_delete=models.PROTECT, related_name="allocations")
    amount_idr = models.DecimalField(_l("jumlah"), **MONEY)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.CheckConstraint(condition=Q(amount_idr__gt=0), name="allocation_amount_positive")]
