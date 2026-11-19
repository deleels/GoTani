"""Input forms for farm operations. Forms only parse/clean input; every rule
is enforced by `farm.services` / `inventory.services`."""

from decimal import Decimal

from django import forms
from django.utils import timezone

from core.numbers import IDDecimalField
from inventory.models import SpoilageRecord, StockLot

from .models import (
    ActivityLog,
    CropCycle,
    Expense,
    ExpenseCategory,
    FarmElement,
    InputItem,
    PaymentMethod,
    PreHarvestChecklistItem,
    Unit,
    Variety,
)

INPUT_CSS = "w-full rounded border border-stone-300 px-2 py-1.5 text-sm"


class StyledForm(forms.Form):
    """Applies Tailwind classes; accepts (and ignores by default) the `roles` kwarg."""

    def __init__(self, *args, roles=None, **kwargs):
        self.roles = roles
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if isinstance(field.widget, forms.CheckboxInput):
                continue
            field.widget.attrs.setdefault("class", INPUT_CSS)


def qty_field(label, required=True, min_value=Decimal("0.001")):
    return IDDecimalField(label=label, max_digits=10, decimal_places=3, min_value=min_value, required=required,
                          kind="kg")


def money_field(label, required=True, kind="money"):
    """`kind="money"` = whole-Rupiah input (D81); `"money2"` also allows `,xx` (input purchase costs)."""
    return IDDecimalField(label=label, max_digits=14, decimal_places=2, min_value=Decimal("0.01"),
                          required=required, kind=kind)


class DateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%dT%H:%M", **kwargs)


class ElementForm(StyledForm):
    name = forms.CharField(label="Nama elemen", max_length=100)
    element_type = forms.ChoiceField(label="Jenis", choices=FarmElement.ElementType.choices)
    area_m2 = forms.DecimalField(label="Luas (m²)", max_digits=10, decimal_places=2, min_value=Decimal("0.01"))

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if FarmElement.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError("Nama elemen sudah dipakai.")
        return name


class StartCycleForm(StyledForm):
    element = forms.ModelChoiceField(label="Elemen", queryset=FarmElement.objects.none())
    variety = forms.ModelChoiceField(label="Varietas", queryset=Variety.objects.all())
    planting_start_date = forms.DateField(
        label="Tanggal mulai tanam", initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    planted_qty = forms.IntegerField(label="Jumlah tanaman", min_value=1, required=False)
    expected_harvest_date = forms.DateField(
        label="Perkiraan panen", required=False, widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Kosongkan untuk memakai umur panen varietas.",
    )
    expected_yield_kg = qty_field("Perkiraan hasil (kg)", required=False, min_value=Decimal("0"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["element"].queryset = FarmElement.objects.filter(is_active=True).exclude(
            cycles__is_active=True
        )


class EstimatesForm(StyledForm):
    planted_qty = forms.IntegerField(label="Jumlah tanaman", min_value=1, required=False)
    expected_harvest_date = forms.DateField(label="Perkiraan panen", required=False,
                                            widget=forms.DateInput(attrs={"type": "date"}))
    expected_yield_kg = qty_field("Perkiraan hasil (kg)", required=False, min_value=Decimal("0"))


class PreHarvestForm(StyledForm):
    cycle = forms.ModelChoiceField(label="Siklus", queryset=CropCycle.objects.none())
    items = forms.ModelMultipleChoiceField(label="Butir yang sudah dipenuhi", required=False,
                                           queryset=PreHarvestChecklistItem.objects.filter(is_active=True),
                                           widget=forms.CheckboxSelectMultiple)
    notes = forms.CharField(label="Catatan", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cycle"].queryset = CropCycle.objects.filter(is_active=True).select_related("element", "variety")
        self.fields["items"].queryset = PreHarvestChecklistItem.objects.filter(is_active=True)


class ActivityForm(StyledForm):
    cycle = forms.ModelChoiceField(
        label="Siklus", queryset=CropCycle.objects.filter(is_active=True).select_related("element", "variety")
    )
    activity_type = forms.ChoiceField(label="Jenis kegiatan", choices=ActivityLog.ActivityType.choices)
    input_item = forms.ModelChoiceField(
        label="Input (opsional)", queryset=InputItem.objects.all(), required=False
    )
    quantity = qty_field("Jumlah", required=False, min_value=Decimal("0"))
    unit = forms.ChoiceField(label="Satuan", choices=[("", "-")] + list(Unit.choices), required=False)
    application_method = forms.CharField(label="Metode aplikasi", max_length=100, required=False)
    duration_minutes = forms.IntegerField(label="Durasi (menit)", min_value=1, required=False)
    photo = forms.ImageField(label="Foto", required=False)
    notes = forms.CharField(label="Catatan", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    # Fields shown per activity type (D62); everything else is dropped for that type.
    TYPE_FIELDS = {
        ActivityLog.ActivityType.FERTILIZER: ["cycle", "input_item", "quantity", "application_method", "notes"],
        ActivityLog.ActivityType.PESTICIDE: ["cycle", "input_item", "quantity", "application_method", "notes"],
        ActivityLog.ActivityType.SEEDING: ["cycle", "input_item", "quantity", "application_method", "notes"],
        ActivityLog.ActivityType.IRRIGATION: ["cycle", "quantity", "unit", "duration_minutes", "application_method", "notes"],
        ActivityLog.ActivityType.SCOUTING: ["cycle", "photo", "notes"],
    }
    INPUT_TYPE = {
        ActivityLog.ActivityType.FERTILIZER: InputItem.ItemType.FERTILIZER,
        ActivityLog.ActivityType.PESTICIDE: InputItem.ItemType.PESTICIDE,
        ActivityLog.ActivityType.SEEDING: InputItem.ItemType.SEED,
    }

    def __init__(self, *args, activity_type=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fixed_type = activity_type
        if activity_type in self.TYPE_FIELDS:
            self.fields["activity_type"].initial = activity_type
            self.fields["activity_type"].widget = forms.HiddenInput()
            self.fields["activity_type"].required = False  # fixed by the tab, not by the client
            keep = set(self.TYPE_FIELDS[activity_type]) | {"activity_type"}
            for name in list(self.fields):
                if name not in keep:
                    del self.fields[name]
            if "input_item" in self.fields:
                self.fields["input_item"].queryset = InputItem.objects.filter(item_type=self.INPUT_TYPE[activity_type])
                self.fields["input_item"].label = "Input"
                self.fields["input_item"].required = activity_type == ActivityLog.ActivityType.SEEDING
            if activity_type == ActivityLog.ActivityType.IRRIGATION:
                self.fields["quantity"].label = "Volume air"
                self.fields["unit"].initial = Unit.L

    def clean(self):
        data = super().clean()
        if self.fixed_type:
            data["activity_type"] = self.fixed_type
        for name in ("input_item", "unit", "application_method", "notes", "duration_minutes", "photo"):
            data.setdefault(name, None if name in ("input_item", "duration_minutes", "photo") else "")
        item = data.get("input_item")
        if item and not data.get("unit"):
            data["unit"] = item.unit  # an input activity always uses the item's own unit
        if data.get("quantity") is None:
            data["quantity"] = Decimal("0")
        return data


class PurchaseForm(StyledForm):
    item = forms.ModelChoiceField(label="Input", queryset=InputItem.objects.all())
    qty = qty_field("Jumlah")
    cost_per_unit_idr = money_field("Harga per satuan (Rp)", kind="money2")
    supplier = forms.CharField(label="Pemasok", max_length=150, required=False)


class ExpenseForm(StyledForm):
    category = forms.ModelChoiceField(label="Kategori", queryset=ExpenseCategory.objects.all())
    scope = forms.ChoiceField(label="Cakupan", choices=Expense.Scope.choices)
    element = forms.ModelChoiceField(label="Elemen", queryset=FarmElement.objects.all(), required=False)
    cycle = forms.ModelChoiceField(
        label="Siklus (opsional)",
        queryset=CropCycle.objects.select_related("element", "variety"),
        required=False,
    )
    amount_idr = money_field("Jumlah (Rp)")
    vendor = forms.CharField(label="Vendor", max_length=150, required=False)
    narration = forms.CharField(label="Keterangan", max_length=255, required=False)
    date_incurred = forms.DateField(
        label="Tanggal", initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"})
    )
    payment_method = forms.ChoiceField(label="Pembayaran", choices=PaymentMethod.choices)
    receipt = forms.FileField(label="Bukti (opsional)", required=False)


class HarvestForm(StyledForm):
    cycle = forms.ModelChoiceField(label="Siklus (tahap Panen)", queryset=CropCycle.objects.none())
    high_quality_kg = qty_field("Grade A / HIGH (kg)")
    low_quality_kg = qty_field("Grade B / LOW (kg)", required=False, min_value=Decimal("0"))
    harvest_loss_kg = qty_field("Susut panen (kg)", required=False, min_value=Decimal("0"))
    number_of_bags = forms.IntegerField(label="Jumlah karung", min_value=1, required=False)
    weight_per_bag_kg = qty_field("Berat per karung (kg)", required=False)
    harvested_at = forms.DateTimeField(
        label="Waktu panen", required=False, widget=DateTimeInput(),
        help_text="Kosongkan untuk waktu sekarang.",
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cycle"].queryset = CropCycle.objects.filter(
            is_active=True, stage=CropCycle.Stage.HARVESTING
        ).select_related("element", "variety")


class LotChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, lot):
        return (
            f"Lot #{lot.pk} — {lot.product} — panen {timezone.localtime(lot.harvested_at):%d-%m-%Y} "
            f"— sisa {lot.qty_remaining_kg.normalize():f} kg"
        )


class SpoilageForm(StyledForm):
    lot = LotChoiceField(label="Lot", queryset=StockLot.objects.none(), empty_label="— pilih lot —")
    qty_kg = qty_field("Jumlah (kg)")
    reason = forms.ChoiceField(label="Alasan", choices=SpoilageRecord.Reason.choices)
    notes = forms.CharField(label="Catatan", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # FIFO order: the first lot of each product is its oldest (D14 preselect).
        self.fields["lot"].queryset = (
            StockLot.objects.filter(qty_remaining_kg__gt=0)
            .select_related("product__variety")
            .order_by("product__variety__name", "product__grade", "harvested_at", "id")
        )


class PriceForm(StyledForm):
    selling_price_idr = money_field("Harga jual per kg (Rp)")
    pos_step_kg = qty_field("Langkah kasir (kg)", required=False)


class StageForm(forms.Form):
    stage = forms.ChoiceField(choices=CropCycle.Stage.choices)
