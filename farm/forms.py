"""Input forms for farm operations. Forms only parse/clean input; every rule
is enforced by `farm.services` / `inventory.services`."""

from decimal import Decimal

from django import forms
from django.utils import timezone

from inventory.models import SpoilageRecord, StockLot

from .models import (
    ActivityLog,
    CropCycle,
    Expense,
    ExpenseCategory,
    FarmElement,
    InputItem,
    PaymentMethod,
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
    return forms.DecimalField(
        label=label, max_digits=10, decimal_places=3, min_value=min_value, required=required,
        widget=forms.NumberInput(attrs={"step": "0.001"}),
    )


def money_field(label, required=True):
    return forms.DecimalField(
        label=label, max_digits=14, decimal_places=2, min_value=Decimal("0.01"), required=required,
        widget=forms.NumberInput(attrs={"step": "0.01"}),
    )


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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["element"].queryset = FarmElement.objects.filter(is_active=True).exclude(
            cycles__is_active=True
        )


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
    notes = forms.CharField(label="Catatan", required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def clean(self):
        data = super().clean()
        item = data.get("input_item")
        if item and not data.get("unit"):
            data["unit"] = item.unit  # an input activity always uses the item's own unit
        if data.get("quantity") is None:
            data["quantity"] = Decimal("0")
        return data


class PurchaseForm(StyledForm):
    item = forms.ModelChoiceField(label="Input", queryset=InputItem.objects.all())
    qty = qty_field("Jumlah")
    cost_per_unit_idr = money_field("Harga per satuan (Rp)")
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


class StageForm(forms.Form):
    stage = forms.ChoiceField(choices=CropCycle.Stage.choices)
