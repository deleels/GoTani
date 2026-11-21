"""Lot-level mass-balance inventory (D3, D20).

Quantities change only through `inventory.services.move_lot_stock()` (rule 13).
"""

from django.utils.translation import gettext_lazy as _l
from decimal import Decimal

from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import models
from django.db.models import F, Q, Sum
from django.utils import timezone

from farm.models import MONEY, QTY, AppendOnlyModel, HarvestBatch, Variety


class Grade(models.TextChoices):
    HIGH = "HIGH", _l("Grade A (HIGH)")
    LOW = "LOW", _l("Grade B (LOW)")


class Tax(models.Model):
    """Admin-editable tax rate (D46). Lines snapshot `rate_pct`; edits never rewrite history."""

    name = models.CharField(_l("nama"), max_length=40)
    rate_pct = models.DecimalField(_l("tarif (%)"), max_digits=5, decimal_places=2)
    is_active = models.BooleanField(_l("aktif"), default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _l("pajak")
        verbose_name_plural = _l("pajak")
        constraints = [
            models.CheckConstraint(
                condition=Q(rate_pct__gte=0) & Q(rate_pct__lte=100), name="tax_rate_between_0_and_100"
            )
        ]

    def __str__(self):
        return f"{self.name} {self.rate_pct.normalize()}%"


class Product(models.Model):
    variety = models.ForeignKey(
        Variety, on_delete=models.PROTECT, related_name="products", verbose_name=_l("varietas")
    )
    grade = models.CharField(_l("grade"), max_length=4, choices=Grade.choices)
    # NULL = unpriced: hidden from the POS until management sets a price (D24).
    selling_price_idr = models.DecimalField(
        _l("harga jual per kg"), null=True, blank=True, **MONEY
    )
    # D98: low-stock alert for sellable produce (NULL = no alert).
    min_alert_kg = models.DecimalField(_l("batas stok menipis (kg)"), max_digits=10, decimal_places=3, null=True, blank=True,
                                       validators=[MinValueValidator(Decimal("0"))])
    # NULL = untaxed (fresh produce is generally PPN-exempt).
    tax = models.ForeignKey(
        Tax, on_delete=models.PROTECT, null=True, blank=True, related_name="products", verbose_name=_l("pajak")
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
        verbose_name = _l("produk")
        verbose_name_plural = _l("produk")
        constraints = [
            models.UniqueConstraint(fields=["variety", "grade"], name="product_unique_variety_grade"),
            models.CheckConstraint(
                condition=Q(selling_price_idr__isnull=True) | Q(selling_price_idr__gt=0),
                name="product_price_positive_or_null",
            ),
        ]

    def __str__(self):
        return f"{self.variety.name} · {self.get_grade_display()}"

    @property
    def stock_kg(self):
        """Derived stock: Σ remaining kg over open lots (never stored)."""
        total = self.lots.filter(qty_remaining_kg__gt=0).aggregate(s=Sum("qty_remaining_kg"))["s"]
        return total or Decimal("0.000")


class StockLot(models.Model):
    product = models.ForeignKey(
        Product, on_delete=models.PROTECT, related_name="lots", verbose_name=_l("produk")
    )
    # CASCADE: an allowed batch delete (rule 10) removes its untouched lots + HARVEST_IN rows.
    harvest_batch = models.ForeignKey(
        HarvestBatch, on_delete=models.CASCADE, related_name="lots", verbose_name=_l("batch panen")
    )
    qty_initial_kg = models.DecimalField(_l("kg awal"), **QTY)
    qty_remaining_kg = models.DecimalField(_l("kg tersisa"), **QTY)
    unit_cogs_idr = models.DecimalField(_l("HPP per kg"), **MONEY)
    harvested_at = models.DateTimeField(_l("dipanen pada"))

    class Meta:
        ordering = ["product_id", "harvested_at", "id"]  # FIFO / lock order (D17)
        verbose_name = _l("lot stok")
        verbose_name_plural = _l("lot stok")
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
        ROT = "ROT", _l("Busuk")
        PHYSICAL_DAMAGE = "PHYSICAL_DAMAGE", _l("Rusak Fisik")
        PEST = "PEST", _l("Hama")
        SHRINKAGE = "SHRINKAGE", _l("Penyusutan")
        OTHER = "OTHER", _l("Lainnya")

    # PROTECT: a lot with spoilage blocks its batch's deletion (rule 10).
    lot = models.ForeignKey(
        StockLot, on_delete=models.PROTECT, related_name="spoilages", verbose_name=_l("lot")
    )
    qty_kg = models.DecimalField(_l("kg"), **QTY)
    reason = models.CharField(_l("alasan"), max_length=20, choices=Reason.choices)
    notes = models.TextField(_l("catatan"), blank=True)
    loss_idr = models.DecimalField(_l("kerugian (Rp)"), **MONEY)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_l("dicatat oleh"),
    )
    recorded_at = models.DateTimeField(_l("dicatat pada"), default=timezone.now)
    # D86: same-day reversal by OWNER/MANAGER (a SPOILAGE_REVERSAL ledger movement; the record stays).
    reversed_at = models.DateTimeField(_l("dibatalkan pada"), null=True, blank=True)
    reversed_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+", verbose_name=_l("dibatalkan oleh"))
    reversal_reason = models.TextField(_l("alasan pembatalan"), blank=True)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        verbose_name = _l("catatan susut/rusak")
        verbose_name_plural = _l("catatan susut/rusak")
        constraints = [
            models.CheckConstraint(condition=Q(qty_kg__gt=0), name="spoilage_qty_positive"),
            models.CheckConstraint(condition=Q(loss_idr__gte=0), name="spoilage_loss_nonnegative"),
        ]

    def __str__(self):
        return f"Susut {self.qty_kg} kg · {self.lot}"


class StockCount(models.Model):
    """Stock opname (D99): a physical count of one product. Only a shortfall is written off — FIFO from the
    oldest lots as SHRINKAGE spoilage — and only after OWNER/MANAGER approval. A surplus is recorded, not booked."""

    class Status(models.TextChoices):
        PENDING = "PENDING", _l("Menunggu persetujuan")
        APPROVED = "APPROVED", _l("Disetujui")
        REJECTED = "REJECTED", _l("Ditolak")

    product = models.ForeignKey(Product, on_delete=models.PROTECT, related_name="counts", verbose_name=_l("produk"))
    system_kg = models.DecimalField(_l("stok sistem saat dihitung"), **QTY)
    counted_kg = models.DecimalField(_l("stok fisik"), **QTY)
    note = models.TextField(_l("catatan"), blank=True)
    status = models.CharField(_l("status"), max_length=10, choices=Status.choices, default=Status.PENDING)
    counted_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="+",
                                   verbose_name=_l("dihitung oleh"))
    counted_at = models.DateTimeField(_l("dihitung pada"), default=timezone.now)
    decided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True,
                                   related_name="+", verbose_name=_l("diputuskan oleh"))
    decided_at = models.DateTimeField(_l("diputuskan pada"), null=True, blank=True)
    written_off_kg = models.DecimalField(_l("kg dihapusbukukan"), default=0, **QTY)
    loss_idr = models.DecimalField(_l("kerugian (Rp)"), default=0, **MONEY)

    class Meta:
        ordering = ["-counted_at", "-id"]
        verbose_name = _l("opname stok")
        verbose_name_plural = _l("opname stok")
        constraints = [
            models.CheckConstraint(condition=Q(counted_kg__gte=0, system_kg__gte=0), name="stockcount_nonnegative"),
        ]

    def __str__(self):
        return f"Opname #{self.pk} {self.product}"

    @property
    def shortfall_kg(self):
        return max(self.system_kg - self.counted_kg, Decimal("0"))

    @property
    def difference_kg(self):
        return self.counted_kg - self.system_kg


class StockLedger(AppendOnlyModel):
    class MovementType(models.TextChoices):
        HARVEST_IN = "HARVEST_IN", _l("Masuk Panen")
        POS_SALE = "POS_SALE", _l("Penjualan POS")
        SPOILAGE = "SPOILAGE", _l("Susut/Rusak")
        POS_VOID = "POS_VOID", _l("Pembatalan POS")
        INVOICE_SALE = "INVOICE_SALE", _l("Penjualan Invoice")
        INVOICE_VOID = "INVOICE_VOID", _l("Pembatalan Invoice")
        SPOILAGE_REVERSAL = "SPOILAGE_REVERSAL", _l("Pembatalan Susut")

    # Sign convention per movement type (D20).
    SIGN = {
        MovementType.HARVEST_IN: 1,
        MovementType.POS_SALE: -1,
        MovementType.SPOILAGE: -1,
        MovementType.POS_VOID: 1,
        MovementType.INVOICE_SALE: -1,
        MovementType.INVOICE_VOID: 1,
        MovementType.SPOILAGE_REVERSAL: 1,
    }

    lot = models.ForeignKey(
        StockLot, on_delete=models.CASCADE, related_name="ledger", verbose_name=_l("lot")
    )
    movement_type = models.CharField(_l("jenis mutasi"), max_length=20, choices=MovementType.choices)
    qty_kg = models.DecimalField(_l("kg (bertanda)"), **QTY)
    reference = models.CharField(_l("referensi"), max_length=100, blank=True)
    timestamp = models.DateTimeField(_l("waktu"), default=timezone.now)

    class Meta:
        ordering = ["timestamp", "id"]
        verbose_name = _l("buku besar stok")
        verbose_name_plural = _l("buku besar stok")
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(movement_type__in=["HARVEST_IN", "POS_VOID", "INVOICE_VOID", "SPOILAGE_REVERSAL"], qty_kg__gt=0)
                    | Q(movement_type__in=["POS_SALE", "SPOILAGE", "INVOICE_SALE"], qty_kg__lt=0)
                ),
                name="stockledger_sign_matches_type",
            ),
        ]

    def __str__(self):
        return f"Lot #{self.lot_id} {self.movement_type} {self.qty_kg}"
