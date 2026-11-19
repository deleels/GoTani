"""Upstream production models: elements, cycles, inputs, expenses, harvests.

Business rules live in `farm/services.py`; models only carry structure and
DB-level invariants (constraints) that must hold regardless of code path.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone

# Shared precisions (CLAUDE.md "Units & Money").
QTY = {"max_digits": 10, "decimal_places": 3}
MONEY = {"max_digits": 14, "decimal_places": 2}


class Unit(models.TextChoices):
    L = "L", "Liter"
    ML = "mL", "Mililiter"
    KG = "KG", "Kilogram"
    G = "G", "Gram"


class PaymentMethod(models.TextChoices):
    CASH = "CASH", "Tunai"
    QRIS = "QRIS", "QRIS"
    TRANSFER = "TRANSFER", "Transfer"


class Variety(models.Model):
    name = models.CharField("nama", max_length=100, unique=True)
    crop = models.CharField("tanaman", max_length=100)
    days_to_harvest = models.PositiveIntegerField("umur panen (hari)", null=True, blank=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "varietas"
        verbose_name_plural = "varietas"

    def __str__(self):
        return self.name


class FarmElement(models.Model):
    class ElementType(models.TextChoices):
        GREENHOUSE = "GREENHOUSE", "Greenhouse"
        OPEN_FIELD = "OPEN_FIELD", "Lahan Terbuka"
        HYDROPONIC = "HYDROPONIC", "Hidroponik"
        SCREEN_HOUSE = "SCREEN_HOUSE", "Screen House"
        SHADENET = "SHADENET", "Shadenet"

    name = models.CharField("nama", max_length=100, unique=True)
    element_type = models.CharField("jenis", max_length=20, choices=ElementType.choices)
    area_m2 = models.DecimalField("luas (m²)", max_digits=10, decimal_places=2)
    is_active = models.BooleanField("aktif", default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "elemen kebun"
        verbose_name_plural = "elemen kebun"
        constraints = [
            models.CheckConstraint(condition=Q(area_m2__gt=0), name="farmelement_area_positive"),
        ]

    def __str__(self):
        return self.name


class CropCycle(models.Model):
    class Stage(models.TextChoices):
        PLANTING = "PLANTING", "Penanaman"
        SPROUTING = "SPROUTING", "Perkecambahan"
        TRANSPLANTING = "TRANSPLANTING", "Pindah Tanam"
        VEGETATIVE = "VEGETATIVE", "Vegetatif"
        FLOWERING = "FLOWERING", "Pembungaan"
        FRUITING = "FRUITING", "Pembuahan"
        HARVESTING = "HARVESTING", "Panen"
        CLEARING = "CLEARING", "Pembersihan"

    # Forward-only order used by stage transition rules (rule 1).
    STAGE_ORDER = [s.value for s in Stage]

    element = models.ForeignKey(
        FarmElement, on_delete=models.PROTECT, related_name="cycles", verbose_name="elemen"
    )
    variety = models.ForeignKey(
        Variety, on_delete=models.PROTECT, related_name="cycles", verbose_name="varietas"
    )
    stage = models.CharField("tahap", max_length=20, choices=Stage.choices, default=Stage.PLANTING)
    planting_start_date = models.DateField("tanggal mulai tanam")
    # Plant count — the one deliberate non-kg quantity (D60).
    planted_qty = models.PositiveIntegerField("jumlah tanaman", null=True, blank=True)
    expected_harvest_date = models.DateField("perkiraan panen", null=True, blank=True)
    expected_yield_kg = models.DecimalField("perkiraan hasil (kg)", null=True, blank=True, **QTY)
    is_active = models.BooleanField("aktif", default=True)
    closed_at = models.DateTimeField("ditutup pada", null=True, blank=True)

    class Meta:
        ordering = ["-planting_start_date", "-id"]
        verbose_name = "siklus tanam"
        verbose_name_plural = "siklus tanam"
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
        FERTILIZER = "FERTILIZER", "Pupuk"
        PESTICIDE = "PESTICIDE", "Pestisida"
        SEED = "SEED", "Benih"

    name = models.CharField("nama", max_length=100, unique=True)
    item_type = models.CharField("jenis", max_length=20, choices=ItemType.choices)
    unit = models.CharField("satuan", max_length=4, choices=Unit.choices)
    stock_qty = models.DecimalField("stok", default=0, **QTY)
    avg_cost_per_unit_idr = models.DecimalField("biaya rata-rata per satuan", default=0, **MONEY)
    min_alert_qty = models.DecimalField("batas peringatan stok", default=0, **QTY)

    class Meta:
        ordering = ["name"]
        verbose_name = "input produksi"
        verbose_name_plural = "input produksi"
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
        InputItem, on_delete=models.PROTECT, related_name="purchases", verbose_name="input"
    )
    qty = models.DecimalField("jumlah", **QTY)
    cost_per_unit_idr = models.DecimalField("harga per satuan", **MONEY)
    supplier = models.CharField("pemasok", max_length=150, blank=True)
    purchased_at = models.DateTimeField("dibeli pada", default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="dicatat oleh",
    )

    class Meta:
        ordering = ["-purchased_at", "-id"]
        verbose_name = "pembelian input"
        verbose_name_plural = "pembelian input"
        constraints = [
            models.CheckConstraint(condition=Q(qty__gt=0), name="inputpurchase_qty_positive"),
            models.CheckConstraint(
                condition=Q(cost_per_unit_idr__gt=0), name="inputpurchase_cost_positive"
            ),
        ]

    def __str__(self):
        return f"{self.item.name} +{self.qty} @ {self.cost_per_unit_idr}"

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
        PURCHASE = "PURCHASE", "Pembelian"
        USAGE = "USAGE", "Pemakaian"
        USAGE_REVERSAL = "USAGE_REVERSAL", "Pembatalan Pemakaian"

    item = models.ForeignKey(
        InputItem, on_delete=models.PROTECT, related_name="ledger", verbose_name="input"
    )
    movement_type = models.CharField("jenis mutasi", max_length=20, choices=MovementType.choices)
    qty = models.DecimalField("jumlah (bertanda)", **QTY)
    reference = models.CharField("referensi", max_length=100, blank=True)
    timestamp = models.DateTimeField("waktu", default=timezone.now)

    class Meta:
        ordering = ["timestamp", "id"]
        verbose_name = "buku besar input"
        verbose_name_plural = "buku besar input"
        constraints = [
            models.CheckConstraint(condition=~Q(qty=0), name="inputledger_qty_nonzero"),
        ]

    def __str__(self):
        return f"{self.item.name} {self.movement_type} {self.qty}"


class ActivityLog(models.Model):
    class ActivityType(models.TextChoices):
        IRRIGATION = "IRRIGATION", "Irigasi"
        FERTILIZER = "FERTILIZER", "Pemupukan"
        PESTICIDE = "PESTICIDE", "Penyemprotan Pestisida"
        # Consumes a SEED input item → BIBIT usage expense (rule 11).
        SEEDING = "SEEDING", "Penanaman Benih"
        SCOUTING = "SCOUTING", "Pengamatan"

    cycle = models.ForeignKey(
        CropCycle, on_delete=models.PROTECT, related_name="activities", verbose_name="siklus"
    )
    activity_type = models.CharField("jenis kegiatan", max_length=20, choices=ActivityType.choices)
    stage_at_activity = models.CharField(
        "tahap saat kegiatan", max_length=20, choices=CropCycle.Stage.choices
    )
    input_item = models.ForeignKey(
        InputItem,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="activities",
        verbose_name="input",
    )
    quantity = models.DecimalField("jumlah", default=0, **QTY)
    unit = models.CharField("satuan", max_length=4, choices=Unit.choices, blank=True)
    application_method = models.CharField("metode aplikasi", max_length=100, blank=True)
    duration_minutes = models.PositiveIntegerField("durasi (menit)", null=True, blank=True)  # IRRIGATION only
    photo = models.ImageField("foto", upload_to="scouting/%Y/%m/", blank=True)  # SCOUTING only
    notes = models.TextField("catatan", blank=True)
    logged_at = models.DateTimeField("waktu", default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="dicatat oleh",
    )

    class Meta:
        ordering = ["-logged_at", "-id"]
        verbose_name = "log kegiatan"
        verbose_name_plural = "log kegiatan"
        constraints = [
            models.CheckConstraint(
                condition=Q(quantity__gte=0), name="activitylog_quantity_nonnegative"
            ),
        ]

    def __str__(self):
        return f"{self.get_activity_type_display()} — {self.cycle} ({self.logged_at:%Y-%m-%d})"


class PreHarvestChecklistItem(models.Model):
    """Admin-editable pre-harvest checklist line (D61)."""

    text = models.CharField("butir", max_length=200)
    order = models.PositiveIntegerField("urutan", default=0)
    is_active = models.BooleanField("aktif", default=True)

    class Meta:
        ordering = ["order", "id"]
        verbose_name = "butir cek pra-panen"
        verbose_name_plural = "butir cek pra-panen"

    def __str__(self):
        return self.text


class PreHarvestCheck(models.Model):
    """One checklist pass; several per cycle allowed, the latest counts (rule 14)."""

    cycle = models.ForeignKey(CropCycle, on_delete=models.PROTECT, related_name="preharvest_checks",
                              verbose_name="siklus")
    checked_items = models.ManyToManyField(PreHarvestChecklistItem, blank=True, related_name="+",
                                           verbose_name="butir tercentang")
    completed_pct = models.DecimalField("persen selesai", max_digits=5, decimal_places=2, default=0)
    notes = models.TextField("catatan", blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name="+", verbose_name="dicatat oleh")
    recorded_at = models.DateTimeField("waktu", default=timezone.now)

    class Meta:
        ordering = ["-recorded_at", "-id"]
        verbose_name = "cek pra-panen"
        verbose_name_plural = "cek pra-panen"
        constraints = [
            models.CheckConstraint(condition=Q(completed_pct__gte=0, completed_pct__lte=100),
                                   name="preharvestcheck_pct_range"),
        ]

    def __str__(self):
        return f"Cek pra-panen {self.cycle} {self.completed_pct}%"


class ExpenseCategory(models.Model):
    code = models.CharField("kode", max_length=30, unique=True)
    name = models.CharField("nama", max_length=100)

    class Meta:
        ordering = ["code"]
        verbose_name = "kategori biaya"
        verbose_name_plural = "kategori biaya"

    def __str__(self):
        return self.name


class Expense(models.Model):
    class Scope(models.TextChoices):
        ELEMENT = "ELEMENT", "Elemen"
        GENERAL = "GENERAL", "Umum"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Menunggu"
        APPROVED = "APPROVED", "Disetujui"
        REJECTED = "REJECTED", "Ditolak"

    class Source(models.TextChoices):
        MANUAL = "MANUAL", "Manual"
        INPUT_USAGE = "INPUT_USAGE", "Pemakaian Input"

    category = models.ForeignKey(
        ExpenseCategory, on_delete=models.PROTECT, related_name="expenses", verbose_name="kategori"
    )
    scope = models.CharField("cakupan", max_length=10, choices=Scope.choices)
    element = models.ForeignKey(
        FarmElement,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expenses",
        verbose_name="elemen",
    )
    cycle = models.ForeignKey(
        CropCycle,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="expenses",
        verbose_name="siklus",
    )
    amount_idr = models.DecimalField("jumlah (Rp)", **MONEY)
    vendor = models.CharField("vendor", max_length=150, blank=True)
    narration = models.CharField("keterangan", max_length=255, blank=True)
    date_incurred = models.DateField("tanggal biaya", default=timezone.localdate)
    payment_method = models.CharField(
        "metode pembayaran", max_length=10, choices=PaymentMethod.choices, default=PaymentMethod.CASH
    )
    receipt = models.FileField("bukti", upload_to="receipts/%Y/%m/", blank=True)
    status = models.CharField("status", max_length=10, choices=Status.choices, default=Status.PENDING)
    source = models.CharField("sumber", max_length=15, choices=Source.choices, default=Source.MANUAL)
    source_activity = models.ForeignKey(
        ActivityLog,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="usage_expenses",
        verbose_name="kegiatan sumber",
    )
    # SET_NULL: deleting an (allowed) batch un-absorbs its expenses (rule 10).
    absorbed_by = models.ForeignKey(
        "HarvestBatch",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="absorbed_expenses",
        verbose_name="diserap oleh panen",
    )
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="disetujui oleh",
    )
    approved_at = models.DateTimeField("disetujui pada", null=True, blank=True)
    created_at = models.DateTimeField("dibuat pada", default=timezone.now)

    class Meta:
        ordering = ["-date_incurred", "-id"]
        verbose_name = "biaya"
        verbose_name_plural = "biaya"
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
        return f"{self.category.code} Rp {self.amount_idr} ({self.get_status_display()})"


class HarvestBatch(models.Model):
    cycle = models.ForeignKey(
        CropCycle, on_delete=models.PROTECT, related_name="harvests", verbose_name="siklus"
    )
    high_quality_kg = models.DecimalField("grade HIGH (kg)", **QTY)
    low_quality_kg = models.DecimalField("grade LOW (kg)", default=0, **QTY)
    harvest_loss_kg = models.DecimalField("susut panen (kg)", default=0, **QTY)
    number_of_bags = models.PositiveIntegerField("jumlah karung", null=True, blank=True)
    weight_per_bag_kg = models.DecimalField("berat per karung (kg)", null=True, blank=True, **QTY)
    absorbed_cost_idr = models.DecimalField("biaya terserap", default=0, **MONEY)
    unit_cogs_high_idr = models.DecimalField("HPP/kg HIGH", default=0, **MONEY)
    unit_cogs_low_idr = models.DecimalField("HPP/kg LOW", default=0, **MONEY)
    harvested_at = models.DateTimeField("dipanen pada", default=timezone.now)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="dicatat oleh",
    )

    class Meta:
        ordering = ["-harvested_at", "-id"]
        verbose_name = "batch panen"
        verbose_name_plural = "batch panen"
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
        return f"Panen #{self.pk} — {self.cycle} ({self.harvested_at:%Y-%m-%d})"

    @property
    def sellable_kg(self):
        return self.high_quality_kg + self.low_quality_kg
