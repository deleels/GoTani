"""Upstream production models: elements, cycles, inputs, expenses, harvests.

Business rules live in `farm/services.py`; models only carry structure and
DB-level invariants (constraints) that must hold regardless of code path.
"""

from django.utils.translation import gettext as _
from django.utils.translation import gettext_lazy as _l
from decimal import Decimal

from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone

# Shared precisions (CLAUDE.md "Units & Money").
QTY = {"max_digits": 10, "decimal_places": 3}
MONEY = {"max_digits": 14, "decimal_places": 2}


class Unit(models.TextChoices):
    L = "L", _l("Liter")
    ML = "mL", _l("Mililiter")
    KG = "KG", _l("Kilogram")
    G = "G", _l("Gram")


class PaymentMethod(models.TextChoices):
    CASH = "CASH", _l("Tunai")
    QRIS = "QRIS", _l("QRIS")
    TRANSFER = "TRANSFER", _l("Transfer")


class VoidReason(models.TextChoices):
    """Why a POS order or an invoice was voided (D94). OTHER needs a note."""

    WRONG_ENTRY = "WRONG_ENTRY", _l("Salah input")
    WRONG_PRICE = "WRONG_PRICE", _l("Salah harga / produk")
    CUSTOMER_CANCELLED = "CUSTOMER_CANCELLED", _l("Pembeli batal")
    PAYMENT_FAILED = "PAYMENT_FAILED", _l("Pembayaran gagal")
    OTHER = "OTHER", _l("Lainnya")


class Variety(models.Model):
    name = models.CharField(_l("nama"), max_length=100, unique=True)
    crop = models.CharField(_l("tanaman"), max_length=100)
    # D91: optional FAO crop coefficient; water need = ET₀ × Kc. Empty → only ET₀ is shown.
    kc = models.DecimalField(_l("koefisien tanaman (Kc)"), max_digits=4, decimal_places=2, null=True, blank=True,
                             validators=[MinValueValidator(Decimal("0.1")), MaxValueValidator(Decimal("2"))],
                             help_text=_l("Contoh: cabai 1,05 · melon 1,0 · kakao 1,0 (FAO-56, fase tengah)."))
    days_to_harvest = models.PositiveIntegerField(_l("umur panen (hari)"), null=True, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _l("varietas")
        verbose_name_plural = _l("varietas")

    def __str__(self):
        return self.name


class FarmElement(models.Model):
    class ElementType(models.TextChoices):
        GREENHOUSE = "GREENHOUSE", _l("Greenhouse")
        OPEN_FIELD = "OPEN_FIELD", _l("Lahan Terbuka")
        HYDROPONIC = "HYDROPONIC", _l("Hidroponik")
        SCREEN_HOUSE = "SCREEN_HOUSE", _l("Screen House")
        SHADENET = "SHADENET", _l("Shadenet")

    name = models.CharField(_l("nama"), max_length=100, unique=True)
    element_type = models.CharField(_l("jenis"), max_length=20, choices=ElementType.choices)
    area_m2 = models.DecimalField(_l("luas (m²)"), max_digits=10, decimal_places=2)
    is_active = models.BooleanField(_l("aktif"), default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = _l("elemen kebun")
        verbose_name_plural = _l("elemen kebun")
        constraints = [
            models.CheckConstraint(condition=Q(area_m2__gt=0), name="farmelement_area_positive"),
        ]

    def __str__(self):
        return self.name


class CropCycle(models.Model):
    class Stage(models.TextChoices):
        PLANTING = "PLANTING", _l("Penanaman")
        SPROUTING = "SPROUTING", _l("Perkecambahan")
        TRANSPLANTING = "TRANSPLANTING", _l("Pindah Tanam")
        VEGETATIVE = "VEGETATIVE", _l("Vegetatif")
        FLOWERING = "FLOWERING", _l("Pembungaan")
        FRUITING = "FRUITING", _l("Pembuahan")
        HARVESTING = "HARVESTING", _l("Panen")
        CLEARING = "CLEARING", _l("Pembersihan")

    # Forward-only order used by stage transition rules (rule 1).
    STAGE_ORDER = [s.value for s in Stage]

    element = models.ForeignKey(
        FarmElement, on_delete=models.PROTECT, related_name="cycles", verbose_name=_l("elemen")
    )
    variety = models.ForeignKey(
        Variety, on_delete=models.PROTECT, related_name="cycles", verbose_name=_l("varietas")
    )
    stage = models.CharField(_l("tahap"), max_length=20, choices=Stage.choices, default=Stage.PLANTING)
    planting_start_date = models.DateField(_l("tanggal mulai tanam"))
    # Plant count — the one deliberate non-kg quantity (D60).
    planted_qty = models.PositiveIntegerField(_l("jumlah tanaman"), null=True, blank=True)
    expected_harvest_date = models.DateField(_l("perkiraan panen"), null=True, blank=True)
    expected_yield_kg = models.DecimalField(_l("perkiraan hasil (kg)"), null=True, blank=True, **QTY)
    is_active = models.BooleanField(_l("aktif"), default=True)
    closed_at = models.DateTimeField(_l("ditutup pada"), null=True, blank=True)

    class Meta:
        ordering = ["-planting_start_date", "-id"]
        verbose_name = _l("siklus tanam")
        verbose_name_plural = _l("siklus tanam")
        constraints = [
            models.UniqueConstraint(
                fields=["element"],
                condition=Q(is_active=True),
                name="cropcycle_one_active_per_element",
            ),
            models.CheckConstraint(
                condition=Q(expected_yield_kg__isnull=True) | Q(expected_yield_kg__gte=0),
                name="cropcycle_expected_yield_nonneg",
            ),
        ]

    def __str__(self):
        return f"{self.variety} @ {self.element} ({self.planting_start_date:%Y-%m-%d})"

    @property
    def stage_index(self) -> int:
        return self.STAGE_ORDER.index(self.stage)

    def days_growing(self, today=None) -> int:
        """Rule 13: max(0, today − start); closed cycles stop at `closed_at`."""
        if not self.is_active and self.closed_at:
            end = timezone.localdate(self.closed_at)
        else:
            end = today or timezone.localdate()
        return max(0, (end - self.planting_start_date).days)

    def is_harvest_overdue(self, today=None) -> bool:
        """Past `expected_harvest_date` without reaching HARVESTING (rule 32)."""
        today = today or timezone.localdate()
        return bool(
            self.is_active and self.expected_harvest_date and self.expected_harvest_date < today
            and self.stage_index < self.STAGE_ORDER.index(self.Stage.HARVESTING)
        )


class InputItem(models.Model):
    class ItemType(models.TextChoices):
        FERTILIZER = "FERTILIZER", _l("Pupuk")
        PESTICIDE = "PESTICIDE", _l("Pestisida")
        SEED = "SEED", _l("Benih")

    name = models.CharField(_l("nama"), max_length=100, unique=True)
    item_type = models.CharField(_l("jenis"), max_length=20, choices=ItemType.choices)
    unit = models.CharField(_l("satuan"), max_length=4, choices=Unit.choices)
    stock_qty = models.DecimalField(_l("stok"), default=0, **QTY)
    avg_cost_per_unit_idr = models.DecimalField(_l("biaya rata-rata per satuan"), default=0, **MONEY)
    min_alert_qty = models.DecimalField(_l("batas peringatan stok"), default=0, **QTY)

    class Meta:
        ordering = ["name"]
        verbose_name = _l("input produksi")
        verbose_name_plural = _l("input produksi")
        constraints = [
            models.CheckConstraint(condition=Q(stock_qty__gte=0), name="inputitem_stock_nonnegative"),
            models.CheckConstraint(
                condition=Q(avg_cost_per_unit_idr__gte=0), name="inputitem_avg_cost_nonnegative"
            ),
            models.CheckConstraint(
                condition=Q(min_alert_qty__gte=0), name="inputitem_min_alert_nonnegative"
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.unit})"

    @property
    def is_low_stock(self) -> bool:
        return self.stock_qty <= self.min_alert_qty


class InputPurchase(models.Model):
    """Inventory asset purchase — cash-out report only, never P&L (D22)."""

    item = models.ForeignKey(
        InputItem, on_delete=models.PROTECT, related_name="purchases", verbose_name=_l("input")
    )
    qty = models.DecimalField(_l("jumlah"), **QTY)
    cost_per_unit_idr = models.DecimalField(_l("harga per satuan"), **MONEY)
    supplier = models.CharField(_l("pemasok"), max_length=150, blank=True)
    purchased_at = models.DateTimeField(_l("dibeli pada"), default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_l("dicatat oleh"),
    )

    class Meta:
        ordering = ["-purchased_at", "-id"]
        verbose_name = _l("pembelian input")
        verbose_name_plural = _l("pembelian input")
        constraints = [
            models.CheckConstraint(condition=Q(qty__gt=0), name="inputpurchase_qty_positive"),
            models.CheckConstraint(
                condition=Q(cost_per_unit_idr__gt=0), name="inputpurchase_cost_positive"
            ),
        ]

    def __str__(self):
        from farm.templatetags.gotani import kg, rupiah

        return f"{self.item.name} +{kg(self.qty)} {self.item.unit} @ {rupiah(self.cost_per_unit_idr)}"

    @property
    def total_cost_idr(self):
        return self.qty * self.cost_per_unit_idr


class AppendOnlyModel(models.Model):
    """Ledger rows may be inserted, never updated or deleted individually.

    Queryset-level cascades (e.g. an allowed HarvestBatch delete) bypass
    `Model.delete()` and are therefore still possible, by design.
    """

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError(f"{type(self).__name__} is append-only; rows cannot be updated.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValueError(f"{type(self).__name__} is append-only; rows cannot be deleted.")


class InputLedger(AppendOnlyModel):
    class MovementType(models.TextChoices):
        PURCHASE = "PURCHASE", _l("Pembelian")
        USAGE = "USAGE", _l("Pemakaian")
        USAGE_REVERSAL = "USAGE_REVERSAL", _l("Pembatalan Pemakaian")

    item = models.ForeignKey(
        InputItem, on_delete=models.PROTECT, related_name="ledger", verbose_name=_l("input")
    )
    movement_type = models.CharField(_l("jenis mutasi"), max_length=20, choices=MovementType.choices)
    qty = models.DecimalField(_l("jumlah (bertanda)"), **QTY)
    reference = models.CharField(_l("referensi"), max_length=100, blank=True)
    timestamp = models.DateTimeField(_l("waktu"), default=timezone.now)

    class Meta:
        ordering = ["timestamp", "id"]
        verbose_name = _l("buku besar input")
        verbose_name_plural = _l("buku besar input")
        constraints = [
            models.CheckConstraint(condition=~Q(qty=0), name="inputledger_qty_nonzero"),
        ]

    def __str__(self):
        return f"{self.item.name} {self.movement_type} {self.qty}"


class ActivityLog(models.Model):
    class ActivityType(models.TextChoices):
        IRRIGATION = "IRRIGATION", _l("Irigasi")
        FERTILIZER = "FERTILIZER", _l("Pemupukan")
        PESTICIDE = "PESTICIDE", _l("Penyemprotan Pestisida")
        # Consumes a SEED input item → BIBIT usage expense (rule 11).
        SEEDING = "SEEDING", _l("Penanaman Benih")
        SCOUTING = "SCOUTING", _l("Pengamatan")

    cycle = models.ForeignKey(
        CropCycle, on_delete=models.PROTECT, related_name="activities", verbose_name=_l("siklus")
    )
    activity_type = models.CharField(_l("jenis kegiatan"), max_length=20, choices=ActivityType.choices)
    stage_at_activity = models.CharField(
        _l("tahap saat kegiatan"), max_length=20, choices=CropCycle.Stage.choices
    )
    input_item = models.ForeignKey(
        InputItem,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="activities",
        verbose_name=_l("input"),
    )
    quantity = models.DecimalField(_l("jumlah"), default=0, **QTY)
    unit = models.CharField(_l("satuan"), max_length=4, choices=Unit.choices, blank=True)
    application_method = models.CharField(_l("metode aplikasi"), max_length=100, blank=True)
    duration_minutes = models.PositiveIntegerField(_l("durasi (menit)"), null=True, blank=True)  # IRRIGATION only
    photo = models.ImageField(_l("foto"), upload_to="scouting/%Y/%m/", blank=True)  # SCOUTING only
    notes = models.TextField(_l("catatan"), blank=True)
    logged_at = models.DateTimeField(_l("waktu"), default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_l("dicatat oleh"),
    )

    class Meta:
        ordering = ["-logged_at", "-id"]
        verbose_name = _l("log kegiatan")
        verbose_name_plural = _l("log kegiatan")
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gte=0), name="activitylog_quantity_nonnegative"
            ),
        ]

    def __str__(self):
        return f"{self.get_activity_type_display()} — {self.cycle} ({self.logged_at:%Y-%m-%d})"


class PreHarvestChecklistItem(models.Model):
    """Admin-editable pre-harvest checklist line (D61)."""

    text = models.CharField(_l("butir"), max_length=200)
    order = models.PositiveIntegerField(_l("urutan"), default=0)
    is_active = models.BooleanField(_l("aktif"), default=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = _l("butir cek pra-panen")
        verbose_name_plural = _l("butir cek pra-panen")

    def __str__(self):
        return self.text


class PreHarvestCheck(models.Model):
    """One checklist pass; several per cycle allowed, the latest counts (rule 14)."""

    cycle = models.ForeignKey(CropCycle, on_delete=models.PROTECT, related_name="preharvest_checks",
                              verbose_name=_l("siklus"))
    checked_items = models.ManyToManyField(PreHarvestChecklistItem, blank=True, related_name="+",
                                           verbose_name=_l("butir tercentang"))
    completed_pct = models.DecimalField(_l("persen selesai"), max_digits=5, decimal_places=2, default=0)
    notes = models.TextField(_l("catatan"), blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+", verbose_name=_l("dicatat oleh"))
    recorded_at = models.DateTimeField(_l("waktu"), default=timezone.now)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        verbose_name = _l("cek pra-panen")
        verbose_name_plural = _l("cek pra-panen")
        constraints = [
            models.CheckConstraint(condition=Q(completed_pct__gte=0, completed_pct__lte=100),
                                   name="preharvestcheck_pct_range"),
        ]

    def __str__(self):
        return _("Cek pra-panen %(cycle)s %(pct)s%%") % {"cycle": self.cycle, "pct": self.completed_pct}


class ExpenseCategory(models.Model):
    code = models.CharField(_l("kode"), max_length=30, unique=True)
    name = models.CharField(_l("nama"), max_length=100)

    class Meta:
        ordering = ["code"]
        verbose_name = _l("kategori biaya")
        verbose_name_plural = _l("kategori biaya")

    def __str__(self):
        return self.name


class Expense(models.Model):
    class Scope(models.TextChoices):
        ELEMENT = "ELEMENT", _l("Elemen")
        GENERAL = "GENERAL", _l("Umum")

    class Status(models.TextChoices):
        PENDING = "PENDING", _l("Menunggu")
        APPROVED = "APPROVED", _l("Disetujui")
        REJECTED = "REJECTED", _l("Ditolak")

    class Source(models.TextChoices):
        MANUAL = "MANUAL", _l("Manual")
        INPUT_USAGE = "INPUT_USAGE", _l("Pemakaian Input")

    category = models.ForeignKey(
        ExpenseCategory, on_delete=models.PROTECT, related_name="expenses", verbose_name=_l("kategori")
    )
    scope = models.CharField(_l("cakupan"), max_length=10, choices=Scope.choices)
    element = models.ForeignKey(
        FarmElement,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expenses",
        verbose_name=_l("elemen"),
    )
    cycle = models.ForeignKey(
        CropCycle,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expenses",
        verbose_name=_l("siklus"),
    )
    amount_idr = models.DecimalField(_l("jumlah (Rp)"), **MONEY)
    vendor = models.CharField(_l("vendor"), max_length=150, blank=True)
    narration = models.CharField(_l("keterangan"), max_length=255, blank=True)
    date_incurred = models.DateField(_l("tanggal biaya"), default=timezone.localdate)
    payment_method = models.CharField(
        _l("metode pembayaran"), max_length=10, choices=PaymentMethod.choices, default=PaymentMethod.CASH
    )
    receipt = models.FileField(_l("bukti"), upload_to="receipts/%Y/%m/", blank=True)
    status = models.CharField(_l("status"), max_length=10, choices=Status.choices, default=Status.PENDING)
    source = models.CharField(_l("sumber"), max_length=15, choices=Source.choices, default=Source.MANUAL)
    source_activity = models.ForeignKey(
        ActivityLog,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="usage_expenses",
        verbose_name=_l("kegiatan sumber"),
    )
    # SET_NULL: deleting an (allowed) batch un-absorbs its expenses (rule 10).
    absorbed_by = models.ForeignKey(
        "HarvestBatch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="absorbed_expenses",
        verbose_name=_l("diserap oleh panen"),
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_l("disetujui oleh"),
    )
    approved_at = models.DateTimeField(_l("disetujui pada"), null=True, blank=True)
    created_at = models.DateTimeField(_l("dibuat pada"), default=timezone.now)

    class Meta:
        ordering = ["-date_incurred", "-id"]
        verbose_name = _l("biaya")
        verbose_name_plural = _l("biaya")
        constraints = [
            models.CheckConstraint(condition=Q(amount_idr__gt=0), name="expense_amount_positive"),
            # ELEMENT expenses name an element; GENERAL expenses are farm-wide overhead.
            models.CheckConstraint(
                condition=(
                    Q(scope="ELEMENT", element__isnull=False)
                    | Q(scope="GENERAL", element__isnull=True, cycle__isnull=True)
                ),
                name="expense_scope_consistent",
            ),
            # Only APPROVED expenses can be absorbed into a harvest batch.
            models.CheckConstraint(
                condition=Q(absorbed_by__isnull=True) | Q(status="APPROVED"),
                name="expense_absorbed_only_if_approved",
            ),
            models.CheckConstraint(
                condition=Q(source="MANUAL", source_activity__isnull=True)
                | Q(source="INPUT_USAGE", source_activity__isnull=False),
                name="expense_source_consistent",
            ),
        ]
        indexes = [models.Index(fields=["cycle", "status", "absorbed_by"])]

    def __str__(self):
        from farm.templatetags.gotani import rupiah

        return f"{self.category.code} {rupiah(self.amount_idr)} ({self.get_status_display()})"


class HarvestBatch(models.Model):
    cycle = models.ForeignKey(
        CropCycle, on_delete=models.PROTECT, related_name="harvests", verbose_name=_l("siklus")
    )
    high_quality_kg = models.DecimalField(_l("grade HIGH (kg)"), **QTY)
    low_quality_kg = models.DecimalField(_l("grade LOW (kg)"), default=0, **QTY)
    harvest_loss_kg = models.DecimalField(_l("susut panen (kg)"), default=0, **QTY)
    number_of_bags = models.PositiveIntegerField(_l("jumlah karung"), null=True, blank=True)
    weight_per_bag_kg = models.DecimalField(_l("berat per karung (kg)"), null=True, blank=True, **QTY)
    absorbed_cost_idr = models.DecimalField(_l("biaya terserap"), default=0, **MONEY)
    unit_cogs_high_idr = models.DecimalField(_l("HPP/kg HIGH"), default=0, **MONEY)
    unit_cogs_low_idr = models.DecimalField(_l("HPP/kg LOW"), default=0, **MONEY)
    harvested_at = models.DateTimeField(_l("dipanen pada"), default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_l("dicatat oleh"),
    )

    class Meta:
        ordering = ["-harvested_at", "-id"]
        verbose_name = _l("batch panen")
        verbose_name_plural = _l("batch panen")
        constraints = [
            # Rule 2's strict `high > 0` is a service validation; the DB only forbids negatives
            # so that the zero-yield path (rule 5) stays representable.
            models.CheckConstraint(
                condition=Q(high_quality_kg__gte=0, low_quality_kg__gte=0, harvest_loss_kg__gte=0),
                name="harvestbatch_kg_nonnegative",
            ),
            models.CheckConstraint(
                condition=Q(absorbed_cost_idr__gte=0, unit_cogs_high_idr__gte=0, unit_cogs_low_idr__gte=0),
                name="harvestbatch_cost_nonnegative",
            ),
        ]

    def __str__(self):
        return _("Panen #%(pk)s — %(cycle)s (%(date)s)") % {"pk": self.pk, "cycle": self.cycle, "date": f"{self.harvested_at:%Y-%m-%d}"}

    @property
    def sellable_kg(self):
        return self.high_quality_kg + self.low_quality_kg
