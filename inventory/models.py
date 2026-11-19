"""Lot-level mass-balance inventory (D3, D20).

Quantities change only through `inventory.services.move_lot_stock()` (rule 13).
"""

from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import F, Q, Sum
from django.utils import timezone

from farm.models import MONEY, QTY, AppendOnlyModel, HarvestBatch, Variety


class Grade(models.TextChoices):
    HIGH = "HIGH", "Grade A (HIGH)"
    LOW = "LOW", "Grade B (LOW)"


class Tax(models.Model):
    """Admin-editable tax rate (D46). Lines snapshot `rate_pct`; edits never rewrite history."""

    name = models.CharField("nama", max_length=40)
    rate_pct = models.DecimalField("tarif (%)", max_digits=5, decimal_places=2)
    is_active = models.BooleanField("aktif", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "pajak"
        verbose_name_plural = "pajak"
        constraints = [
            models.CheckConstraint(
                condition=Q(rate_pct__gte=0) & Q(rate_pct__lte=100), name="tax_rate_between_0_and_100"
            )
        ]

    def __str__(self):
        return f"{self.name} {self.rate_pct.normalize()}%"


class Product(models.Model):
    variety = models.ForeignKey(
        Variety, on_delete=models.PROTECT, related_name="products", verbose_name="varietas"
    )
    grade = models.CharField("grade", max_length=4, choices=Grade.choices)
    # NULL = unpriced: hidden from the POS until management sets a price (D24).
    selling_price_idr = models.DecimalField(
        "harga jual per kg", null=True, blank=True, **MONEY
    )
    # NULL = untaxed (fresh produce is generally PPN-exempt).
    tax = models.ForeignKey(
        Tax, on_delete=models.PROTECT, null=True, blank=True, related_name="products", verbose_name="pajak"
    )

    @property
    def tax_rate_pct(self) -> Decimal:
        """Rate to snapshot on a new sale: 0 unless the farm is a PKP (D80) and the tax is active."""
        if self.tax is None or not self.tax.is_active:
            return Decimal("0")
        from core.services import farm_is_pkp

        return self.tax.rate_pct if farm_is_pkp() else Decimal("0")

    class Meta:
        ordering = ["variety__name", "grade"]
        verbose_name = "produk"
        verbose_name_plural = "produk"
        constraints = [
            models.UniqueConstraint(fields=["variety", "grade"], name="product_unique_variety_grade"),
            models.CheckConstraint(
                condition=Q(selling_price_idr__isnull=True) | Q(selling_price_idr__gt=0),
                name="product_price_positive_or_null",
            ),
        ]

    def __str__(self):
        return f"{self.variety.name} — {self.get_grade_display()}"

    @property
    def stock_kg(self):
        """Derived stock: Σ remaining kg over open lots (never stored)."""
        total = self.lots.filter(qty_remaining_kg__gt=0).aggregate(s=Sum("qty_remaining_kg"))["s"]
        return total or Decimal("0.000")


class StockLot(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="lots", verbose_name="produk"
    )
    # CASCADE: an allowed batch delete (rule 10) removes its untouched lots + HARVEST_IN rows.
    harvest_batch = models.ForeignKey(
        HarvestBatch, on_delete=models.CASCADE, related_name="lots", verbose_name="batch panen"
    )
    qty_initial_kg = models.DecimalField("kg awal", **QTY)
    qty_remaining_kg = models.DecimalField("kg tersisa", **QTY)
    unit_cogs_idr = models.DecimalField("HPP per kg", **MONEY)
    harvested_at = models.DateTimeField("dipanen pada")

    class Meta:
        ordering = ["product_id", "harvested_at", "id"]  # FIFO / lock order (D17)
        verbose_name = "lot stok"
        verbose_name_plural = "lot stok"
        constraints = [
            # DB-level oversell backstop.
            models.CheckConstraint(
                condition=Q(qty_remaining_kg__gte=0), name="stocklot_remaining_nonnegative"
            ),
            models.CheckConstraint(condition=Q(qty_initial_kg__gt=0), name="stocklot_initial_positive"),
            models.CheckConstraint(
                condition=Q(qty_remaining_kg__lte=F("qty_initial_kg")),
                name="stocklot_remaining_lte_initial",
            ),
            models.CheckConstraint(condition=Q(unit_cogs_idr__gte=0), name="stocklot_cogs_nonnegative"),
            models.UniqueConstraint(
                fields=["harvest_batch", "product"], name="stocklot_one_per_batch_product"
            ),
        ]
        indexes = [models.Index(fields=["product", "harvested_at", "id"])]

    def __str__(self):
        return f"Lot #{self.pk} {self.product} ({self.qty_remaining_kg}/{self.qty_initial_kg} kg)"


class SpoilageRecord(models.Model):
    class Reason(models.TextChoices):
        ROT = "ROT", "Busuk"
        PHYSICAL_DAMAGE = "PHYSICAL_DAMAGE", "Rusak Fisik"
        PEST = "PEST", "Hama"
        SHRINKAGE = "SHRINKAGE", "Penyusutan"
        OTHER = "OTHER", "Lainnya"

    # PROTECT: a lot with spoilage blocks its batch's deletion (rule 10).
    lot = models.ForeignKey(
        StockLot, on_delete=models.PROTECT, related_name="spoilages", verbose_name="lot"
    )
    qty_kg = models.DecimalField("kg", **QTY)
    reason = models.CharField("alasan", max_length=20, choices=Reason.choices)
    notes = models.TextField("catatan", blank=True)
    loss_idr = models.DecimalField("kerugian (Rp)", **MONEY)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="dicatat oleh",
    )
    recorded_at = models.DateTimeField("dicatat pada", default=timezone.now)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        verbose_name = "catatan susut/rusak"
        verbose_name_plural = "catatan susut/rusak"
        constraints = [
            models.CheckConstraint(condition=Q(qty_kg__gt=0), name="spoilage_qty_positive"),
            models.CheckConstraint(condition=Q(loss_idr__gte=0), name="spoilage_loss_nonnegative"),
        ]

    def __str__(self):
        return f"Susut {self.qty_kg} kg — {self.lot}"


class StockLedger(AppendOnlyModel):
    class MovementType(models.TextChoices):
        HARVEST_IN = "HARVEST_IN", "Masuk Panen"
        POS_SALE = "POS_SALE", "Penjualan POS"
        SPOILAGE = "SPOILAGE", "Susut/Rusak"
        POS_VOID = "POS_VOID", "Pembatalan POS"
        INVOICE_SALE = "INVOICE_SALE", "Penjualan Invoice"
        INVOICE_VOID = "INVOICE_VOID", "Pembatalan Invoice"

    # Sign convention per movement type (D20).
    SIGN = {
        MovementType.HARVEST_IN: 1,
        MovementType.POS_SALE: -1,
        MovementType.SPOILAGE: -1,
        MovementType.POS_VOID: 1,
        MovementType.INVOICE_SALE: -1,
        MovementType.INVOICE_VOID: 1,
    }

    lot = models.ForeignKey(
        StockLot, on_delete=models.CASCADE, related_name="ledger", verbose_name="lot"
    )
    movement_type = models.CharField("jenis mutasi", max_length=20, choices=MovementType.choices)
    qty_kg = models.DecimalField("kg (bertanda)", **QTY)
    reference = models.CharField("referensi", max_length=100, blank=True)
    timestamp = models.DateTimeField("waktu", default=timezone.now)

    class Meta:
        ordering = ["timestamp", "id"]
        verbose_name = "buku besar stok"
        verbose_name_plural = "buku besar stok"
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(movement_type__in=["HARVEST_IN", "POS_VOID", "INVOICE_VOID"], qty_kg__gt=0)
                    | Q(movement_type__in=["POS_SALE", "SPOILAGE", "INVOICE_SALE"], qty_kg__lt=0)
                ),
                name="stockledger_sign_matches_type",
            ),
        ]

    def __str__(self):
        return f"Lot #{self.lot_id} {self.movement_type} {self.qty_kg}"
