"""Retail checkout models (D7, D17, D18, D19, D30)."""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

from farm.models import MONEY, QTY, PaymentMethod
from inventory.models import Product, StockLot


class SaleOrder(models.Model):
    class Status(models.TextChoices):
        COMPLETED = "COMPLETED", "Selesai"
        VOIDED = "VOIDED", "Dibatalkan"

    # GT-YYYYMMDD-#### (Asia/Jakarta date), assigned inside the checkout transaction.
    order_number = models.CharField("nomor order", max_length=20, unique=True)
    payment_method = models.CharField(
        "metode pembayaran", max_length=10, choices=PaymentMethod.choices
    )
    total_gross_idr = models.DecimalField("total dibayar", default=0, **MONEY)  # tax-inclusive
    total_tax_idr = models.DecimalField("total pajak", default=0, **MONEY)
    total_revenue_idr = models.DecimalField("total pendapatan", default=0, **MONEY)  # net of tax (D48)
    total_cogs_idr = models.DecimalField("total HPP", default=0, **MONEY)
    status = models.CharField(
        "status", max_length=10, choices=Status.choices, default=Status.COMPLETED
    )
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="sale_orders",
        verbose_name="kasir",
    )
    created_at = models.DateTimeField("dibuat pada", default=timezone.now)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="voided_orders",
        verbose_name="dibatalkan oleh",
    )
    voided_at = models.DateTimeField("dibatalkan pada", null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        verbose_name = "order penjualan"
        verbose_name_plural = "order penjualan"
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


class OrderLineItem(models.Model):
    order = models.ForeignKey(
        SaleOrder, on_delete=models.CASCADE, related_name="lines", verbose_name="order"
    )
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="order_lines", verbose_name="produk"
    )
    qty_kg = models.DecimalField("kg", **QTY)
    unit_price_idr = models.DecimalField("harga per kg", **MONEY)  # server-side snapshot, tax-inclusive
    tax_rate_pct = models.DecimalField("tarif pajak (%)", max_digits=5, decimal_places=2, default=0)
    tax_idr = models.DecimalField("pajak", default=0, **MONEY)
    net_revenue_idr = models.DecimalField("pendapatan bersih", default=0, **MONEY)
    unit_cogs_idr = models.DecimalField("HPP per kg", **MONEY)  # weighted avg of allocations

    class Meta:
        ordering = ["order_id", "id"]
        verbose_name = "baris order"
        verbose_name_plural = "baris order"
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
        OrderLineItem, on_delete=models.CASCADE, related_name="allocations", verbose_name="baris"
    )
    # PROTECT: a sold lot blocks its batch's deletion (rule 10).
    lot = models.ForeignKey(
        StockLot, on_delete=models.PROTECT, related_name="allocations", verbose_name="lot"
    )
    qty_kg = models.DecimalField("kg", **QTY)
    unit_cogs_idr = models.DecimalField("HPP per kg", **MONEY)

    class Meta:
        ordering = ["line_id", "id"]
        verbose_name = "alokasi lot"
        verbose_name_plural = "alokasi lot"
        constraints = [
            models.CheckConstraint(condition=Q(qty_kg__gt=0), name="allocation_qty_positive"),
            models.UniqueConstraint(fields=["line", "lot"], name="allocation_unique_line_lot"),
        ]

    def __str__(self):
        return f"{self.line} ← Lot #{self.lot_id} ({self.qty_kg} kg)"
