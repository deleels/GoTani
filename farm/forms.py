"""Input forms for farm operations. Forms only parse/clean input; every rule
is enforced by `farm.services` / `inventory.services`."""

from django.utils.translation import gettext as _, gettext_lazy as _l
from decimal import Decimal

from datetime import timedelta

from django import forms
from django.db.models import Q
from django.utils import timezone

from core.forms import apply_select_prompts
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

INPUT_CSS = "app-field"  # D104: design-system field (gotani.css)


class StyledForm(forms.Form):
    """Applies Tailwind classes; accepts (and ignores by default) the `roles` kwarg.

    `show_when = {"field": "other=VALUE|VALUE2"}` shows a field's row only while another field has one of the
    values (forms.js hides and disables it otherwise, so it is not submitted)."""

    show_when: dict = {}

    def __init__(self, *args, roles=None, **kwargs):
        self.roles = roles
        super().__init__(*args, **kwargs)
        for name, cond in self.show_when.items():
            if name in self.fields:
                self.fields[name].show_when = cond
        apply_select_prompts(self)
        for field in self.fields.values():
            if isinstance(field.widget, (forms.CheckboxInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
                continue
            field.widget.attrs.setdefault("class", INPUT_CSS)


def qty_field(label, required=True, min_value=Decimal("0.001")):
    return IDDecimalField(label=label, max_digits=10, decimal_places=3, min_value=min_value, required=required,
                          kind="kg")


def money_field(label, required=True, kind="money"):
    """`kind="money"` = whole-Rupiah input (D81); `"money2"` also allows `,xx` (input purchase costs)."""
    return IDDecimalField(label=label, max_digits=14, decimal_places=2, min_value=Decimal("0.01"),
                          required=required, kind=kind)


# D121: short unit names shown next to a quantity (the stored values stay L / mL / KG / G).
UNIT_SHORT = {Unit.L: "L", Unit.ML: "mL", Unit.KG: "kg", Unit.G: "g"}


def follow_unit(field, select_id, items):
    """The quantity's unit suffix follows the chosen input item (its own unit; no conversion, rule 11)."""
    field.widget.unit = None
    field.widget.unit_from = select_id
    field.widget.units = {str(i.pk): UNIT_SHORT.get(i.unit, i.unit) for i in items}


def join_unit(form, number, unit):
    """Draw a choosable unit as a compact select joined to the number field (_form_fields.html)."""
    form.fields[number].widget.unit = None
    form.fields[number].unit_field = unit
    form.fields[unit].joined_into = number
    form.fields[unit].widget.attrs.update({"class": "app-field app-field--unit", "aria-label": str(form.fields[unit].label)})


class DateTimeInput(forms.DateTimeInput):
    input_type = "datetime-local"

    def __init__(self, **kwargs):
        super().__init__(format="%Y-%m-%dT%H:%M", **kwargs)


class ElementForm(StyledForm):
    name = forms.CharField(label=_l("Nama elemen"), max_length=100)
    element_type = forms.ChoiceField(label=_l("Jenis"), choices=FarmElement.ElementType.choices)
    area_m2 = IDDecimalField(label=_l("Luas (m²)"), max_digits=10, decimal_places=2, min_value=Decimal("0.01"), kind="m2")

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if FarmElement.objects.filter(name__iexact=name).exists():
            raise forms.ValidationError(_("Nama elemen sudah dipakai."))
        return name


class ElementEditForm(ElementForm):
    """Edit name, area and (without an active cycle) type (D124); uniqueness is checked by the service."""

    def clean_name(self):
        return self.cleaned_data["name"].strip()


class InputItemForm(StyledForm):
    name = forms.CharField(label=_l("Nama input"), max_length=100)
    item_type = forms.ChoiceField(label=_l("Jenis"), choices=InputItem.ItemType.choices)
    unit = forms.ChoiceField(label=_l("Satuan"), choices=Unit.choices)
    min_alert_qty = IDDecimalField(label=_l("Batas stok menipis"), max_digits=10, decimal_places=3,
                                   min_value=Decimal("0"), required=False, kind="kg")
    first_qty = qty_field(_l("Pembelian pertama: jumlah (opsional)"), required=False)
    first_cost_per_unit_idr = money_field(_l("Harga per satuan"), required=False, kind="money2")
    supplier = forms.CharField(label=_l("Pemasok (opsional)"), max_length=100, required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in ("min_alert_qty", "first_qty"):  # in the item's own unit, not kg
            self.fields[name].widget.unit = None


class VarietyForm(StyledForm):
    name = forms.CharField(label=_l("Nama varietas"), max_length=100)
    crop = forms.CharField(label=_l("Tanaman"), max_length=100)
    days_to_harvest = forms.IntegerField(label=_l("Umur panen (hari, opsional)"), min_value=1, required=False)
    kc = IDDecimalField(label=_l("Koefisien tanaman Kc (opsional)"), max_digits=4, decimal_places=2,
                        min_value=Decimal("0.1"), max_value=Decimal("2"), required=False, kind="kg",
                        help_text=_l("Contoh: cabai 1,05 · melon 1,0 · kakao 1,0 (FAO-56, fase tengah)."))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["kc"].widget.unit = None

    notes = forms.CharField(label=_l("Catatan budidaya (opsional)"), required=False,
                            widget=forms.Textarea(attrs={"rows": 3}),
                            help_text=_l("Jarak tanam, pupuk dasar, hama yang sering muncul. Tampil di halaman siklus."))


class StartCycleForm(StyledForm):
    element = forms.ModelChoiceField(label=_l("Elemen"), queryset=FarmElement.objects.none())
    variety = forms.ModelChoiceField(label=_l("Varietas"), queryset=Variety.objects.all())
    planting_start_date = forms.DateField(
        label=_l("Tanggal mulai tanam"), initial=timezone.localdate,
        widget=forms.DateInput(attrs={"type": "date"}),
    )
    planted_qty = forms.IntegerField(label=_l("Jumlah tanaman"), min_value=1, required=False)
    planted_unit = forms.ChoiceField(label=_l("Satuan"), choices=CropCycle.PlantedUnit.choices,
                                     initial=CropCycle.PlantedUnit.PLANT, required=False)
    expected_harvest_date = forms.DateField(
        label=_l("Perkiraan panen"), required=False, widget=forms.DateInput(attrs={"type": "date"}),
        help_text=_l("Kosongkan untuk memakai umur panen varietas."),
    )
    expected_yield_kg = qty_field(_l("Perkiraan hasil (kg)"), required=False, min_value=Decimal("0"))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        join_unit(self, "planted_qty", "planted_unit")
        self.fields["element"].queryset = FarmElement.objects.filter(is_active=True).exclude(
            cycles__is_active=True
        )


class EstimatesForm(StyledForm):
    planted_qty = forms.IntegerField(label=_l("Jumlah tanaman"), min_value=1, required=False)
    expected_harvest_date = forms.DateField(label=_l("Perkiraan panen"), required=False,
                                            widget=forms.DateInput(attrs={"type": "date"}))
    expected_yield_kg = qty_field(_l("Perkiraan hasil (kg)"), required=False, min_value=Decimal("0"))


class PreHarvestForm(StyledForm):
    cycle = forms.ModelChoiceField(label=_l("Siklus"), queryset=CropCycle.objects.none())
    notes = forms.CharField(label=_l("Catatan"), required=False, widget=forms.Textarea(attrs={"rows": 2}))
    YES, NO = "YES", "NO"

    def __init__(self, *args, **kwargs):
        """D130: one required Ya / Tidak question per active checklist line."""
        super().__init__(*args, **kwargs)
        self.fields["cycle"].queryset = CropCycle.objects.filter(is_active=True).select_related("element", "variety")
        notes = self.fields.pop("notes")
        self.lines = list(PreHarvestChecklistItem.objects.filter(is_active=True))
        for line in self.lines:
            self.fields[f"line_{line.pk}"] = forms.ChoiceField(
                label=str(line), choices=[(self.YES, _("Ya")), (self.NO, _("Tidak"))], widget=forms.RadioSelect,
                error_messages={"required": _("Jawab Ya atau Tidak.")})
        self.fields["notes"] = notes

    def clean(self):
        data = super().clean()
        data["items"] = [line for line in self.lines if data.get(f"line_{line.pk}") == self.YES]
        data["failed_items"] = [line for line in self.lines if data.get(f"line_{line.pk}") == self.NO]
        return data


class ActivityForm(StyledForm):
    cycle = forms.ModelChoiceField(
        label=_l("Siklus"), queryset=CropCycle.objects.filter(is_active=True).select_related("element", "variety")
    )
    activity_type = forms.ChoiceField(label=_l("Jenis kegiatan"), choices=ActivityLog.ActivityType.choices)
    input_item = forms.ModelChoiceField(
        label=_l("Input (opsional)"), queryset=InputItem.objects.all(), required=False
    )
    quantity = qty_field(_l("Jumlah"), required=False, min_value=Decimal("0"))
    unit = forms.ChoiceField(label=_l("Satuan"), choices=[("", "-")] + list(UNIT_SHORT.items()), required=False)
    application_method = forms.CharField(label=_l("Metode aplikasi"), max_length=100, required=False)
    duration_minutes = forms.IntegerField(label=_l("Durasi"), min_value=1, required=False)
    photo = forms.ImageField(label=_l("Foto"), required=False)
    notes = forms.CharField(label=_l("Catatan"), required=False, widget=forms.Textarea(attrs={"rows": 2}))
    finding = forms.ChoiceField(label=_l("Temuan"), choices=ActivityLog.Finding.choices, required=False)
    severity = forms.ChoiceField(label=_l("Tingkat"), choices=ActivityLog.Severity.choices, required=False,
                                 help_text=_l("Wajib kecuali temuan Normal."))
    task_name = forms.CharField(label=_l("Pekerjaan"), max_length=80, required=False,
                                help_text=_l("Contoh: penyiangan, pemangkasan, pasang ajir."))
    worker_count = forms.IntegerField(label=_l("Jumlah pekerja (opsional)"), min_value=1, required=False)
    logged_at = forms.DateTimeField(label=_l("Waktu kegiatan"), required=False, widget=DateTimeInput(),
                                    help_text=_l("Kosongkan untuk sekarang. Paling lama 3 hari ke belakang."))

    # Fields shown per activity type (D62); everything else is dropped for that type.
    TYPE_FIELDS = {
        ActivityLog.ActivityType.FERTILIZER: ["cycle", "logged_at", "input_item", "quantity", "application_method", "notes"],
        ActivityLog.ActivityType.PESTICIDE: ["cycle", "logged_at", "input_item", "quantity", "application_method", "notes"],
        ActivityLog.ActivityType.SEEDING: ["cycle", "logged_at", "input_item", "quantity", "application_method", "notes"],
        ActivityLog.ActivityType.IRRIGATION: ["cycle", "logged_at", "quantity", "unit", "duration_minutes", "application_method", "notes"],
        ActivityLog.ActivityType.SCOUTING: ["cycle", "logged_at", "finding", "severity", "photo", "notes"],
        ActivityLog.ActivityType.OTHER: ["cycle", "logged_at", "task_name", "duration_minutes", "worker_count",
                                         "photo", "notes"],
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
                items = InputItem.objects.filter(item_type=self.INPUT_TYPE[activity_type])
                self.fields["input_item"].queryset = items
                self.fields["input_item"].label = _("Input")
                self.fields["input_item"].required = activity_type == ActivityLog.ActivityType.SEEDING
                follow_unit(self.fields["quantity"], "id_input_item", items)
            if activity_type == ActivityLog.ActivityType.IRRIGATION:
                self.fields["quantity"].label = _("Volume air")
                self.fields["unit"].initial = Unit.L
            if activity_type == ActivityLog.ActivityType.SCOUTING:
                self.fields["finding"].required = True
                self.fields["notes"].label = _("Pengamatan")
                self.fields["notes"].help_text = _("Wajib kecuali temuan Normal.")
                self.fields["severity"].show_when = "finding=" + "|".join(
                    v for v in ActivityLog.Finding.values if v != ActivityLog.Finding.NORMAL)
            if activity_type == ActivityLog.ActivityType.OTHER:
                from .services import ACTIVITY_BACKDATE_DAYS

                since = timezone.now() - timedelta(days=ACTIVITY_BACKDATE_DAYS)
                self.fields["task_name"].required = True
                self.fields["duration_minutes"].label = _("Durasi (opsional)")
                self.fields["cycle"].queryset = CropCycle.objects.filter(
                    Q(is_active=True) | Q(stage=CropCycle.Stage.CLEARING, closed_at__gte=since)
                ).select_related("element", "variety")
        if "unit" in self.fields and "quantity" in self.fields:
            join_unit(self, "quantity", "unit")
        if "duration_minutes" in self.fields:
            self.fields["duration_minutes"].suffix = _("menit")
        apply_select_prompts(self)  # again: the fields above changed after StyledForm ran

    def clean(self):
        data = super().clean()
        if self.fixed_type:
            data["activity_type"] = self.fixed_type
        nullable = ("input_item", "duration_minutes", "photo", "logged_at", "worker_count")
        for name in nullable + ("unit", "application_method", "notes", "task_name", "finding", "severity"):
            data.setdefault(name, None if name in nullable else "")
        item = data.get("input_item")
        if item and not data.get("unit"):
            data["unit"] = item.unit  # an input activity always uses the item's own unit
        if data.get("quantity") is None:
            data["quantity"] = Decimal("0")
        return data


class ActivityEditForm(StyledForm):
    """D132: the non-stock, non-cost fields of one report."""

    finding = forms.ChoiceField(label=_l("Temuan"), choices=ActivityLog.Finding.choices, required=False)
    severity = forms.ChoiceField(label=_l("Tingkat"), choices=[("", "-")] + list(ActivityLog.Severity.choices),
                                 required=False, help_text=_l("Wajib kecuali temuan Normal."))
    task_name = forms.CharField(label=_l("Pekerjaan"), max_length=80, required=False)
    application_method = forms.CharField(label=_l("Metode aplikasi"), max_length=100, required=False)
    photo = forms.ImageField(label=_l("Foto baru (opsional)"), required=False)
    notes = forms.CharField(label=_l("Catatan"), required=False, widget=forms.Textarea(attrs={"rows": 3}))

    def __init__(self, *args, editable=(), **kwargs):
        super().__init__(*args, **kwargs)
        for name in list(self.fields):
            if name not in editable:
                del self.fields[name]


class MultiActivityForm(StyledForm):
    """D131: shared fields once, then up to 10 rows of cycle + quantity (+ duration). Blank rows are skipped."""

    input_item = forms.ModelChoiceField(label=_l("Input"), queryset=InputItem.objects.none(), required=False)
    unit = forms.ChoiceField(label=_l("Satuan air"), choices=list(UNIT_SHORT.items()), required=False)
    task_name = forms.CharField(label=_l("Pekerjaan"), max_length=80, required=False)
    application_method = forms.CharField(label=_l("Metode aplikasi"), max_length=100, required=False)
    logged_at = forms.DateTimeField(label=_l("Waktu kegiatan"), required=False, widget=DateTimeInput(),
                                    help_text=_l("Kosongkan untuk sekarang. Paling lama 3 hari ke belakang."))
    notes = forms.CharField(label=_l("Catatan"), required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, activity_type=None, **kwargs):
        from .services import MULTI_MAX_ROWS

        super().__init__(*args, **kwargs)
        self.activity_type = activity_type
        A = ActivityLog.ActivityType
        self.uses_input = activity_type in ActivityForm.INPUT_TYPE
        self.uses_qty = activity_type != A.OTHER
        self.uses_duration = activity_type in (A.IRRIGATION, A.OTHER)
        if self.uses_input:
            self.fields["input_item"].queryset = InputItem.objects.filter(item_type=ActivityForm.INPUT_TYPE[activity_type])
            self.fields["input_item"].required = True
        else:
            del self.fields["input_item"]
        if activity_type != A.IRRIGATION:
            del self.fields["unit"]
        else:
            self.fields["unit"].initial = Unit.L
        if activity_type != A.OTHER:
            del self.fields["task_name"]
        else:
            self.fields["task_name"].required = True
            del self.fields["application_method"]
        cycles = CropCycle.objects.filter(is_active=True).select_related("element", "variety")
        self.rows = []
        for i in range(MULTI_MAX_ROWS):
            row = {"cycle": f"cycle_{i}", "qty": f"qty_{i}", "dur": f"dur_{i}"}
            self.fields[row["cycle"]] = forms.ModelChoiceField(label=_("Siklus"), queryset=cycles, required=False,
                                                              empty_label=_("Pilih siklus…"))
            if self.uses_qty:
                self.fields[row["qty"]] = qty_field(_l("Jumlah"), required=False, min_value=Decimal("0"))
                self.fields[row["qty"]].widget.unit = None
            if self.uses_duration:
                self.fields[row["dur"]] = forms.IntegerField(label=_("Durasi (menit)"), min_value=1, required=False)
            self.rows.append(row)
        apply_select_prompts(self)
        for name, field in self.fields.items():
            field.widget.attrs.setdefault("class", INPUT_CSS)

    def shared_fields(self):
        return [self[n] for n in ("input_item", "unit", "task_name", "application_method", "logged_at", "notes")
                if n in self.fields]

    def row_fields(self):
        return [[self[r[k]] for k in ("cycle", "qty", "dur") if r[k] in self.fields] for r in self.rows]

    def clean(self):
        data = super().clean()
        rows = []
        for r in self.rows:
            cycle = data.get(r["cycle"])
            qty, dur = data.get(r["qty"]), data.get(r["dur"])
            if cycle is None:
                if qty or dur:
                    raise forms.ValidationError(_("Pilih siklus untuk setiap baris yang diisi."))
                continue
            if self.uses_qty and not qty and self.activity_type != ActivityLog.ActivityType.IRRIGATION:
                raise forms.ValidationError(_("Isi jumlah untuk %(cycle)s.") % {"cycle": cycle})
            rows.append((cycle, qty, dur))
        data["rows"] = rows
        item = data.get("input_item")
        data["unit"] = item.unit if item else (data.get("unit") or "")
        return data


class PurchaseForm(StyledForm):
    item = forms.ModelChoiceField(label=_l("Input"), queryset=InputItem.objects.all())
    qty = qty_field(_l("Jumlah"))
    cost_per_unit_idr = money_field(_l("Harga per satuan (Rp)"), kind="money2")
    supplier = forms.CharField(label=_l("Pemasok"), max_length=150, required=False)
    payment_method = forms.ChoiceField(label=_l("Pembayaran"), choices=PaymentMethod.choices, required=False,
                                       initial=PaymentMethod.CASH)
    receipt = forms.FileField(label=_l("Bukti (opsional)"), required=False)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        follow_unit(self.fields["qty"], "id_item", self.fields["item"].queryset)


class ExpenseForm(StyledForm):
    show_when = {"element": "scope=ELEMENT", "cycle": "scope=ELEMENT"}
    category = forms.ModelChoiceField(label=_l("Kategori"), queryset=ExpenseCategory.objects.all())
    scope = forms.ChoiceField(label=_l("Cakupan"), choices=Expense.Scope.choices)
    element = forms.ModelChoiceField(label=_l("Elemen"), queryset=FarmElement.objects.all(), required=False)
    cycle = forms.ModelChoiceField(
        label=_l("Siklus (opsional)"),
        queryset=CropCycle.objects.select_related("element", "variety"),
        required=False,
    )
    amount_idr = money_field(_l("Jumlah (Rp)"))
    vendor = forms.CharField(label=_l("Vendor"), max_length=150, required=False)
    narration = forms.CharField(label=_l("Keterangan"), max_length=255, required=False)
    date_incurred = forms.DateField(
        label=_l("Tanggal"), initial=timezone.localdate, widget=forms.DateInput(attrs={"type": "date"})
    )
    payment_method = forms.ChoiceField(label=_l("Pembayaran"), choices=PaymentMethod.choices)
    receipt = forms.FileField(label=_l("Bukti (opsional)"), required=False)


class HarvestForm(StyledForm):
    cycle = forms.ModelChoiceField(label=_l("Siklus (tahap Panen)"), queryset=CropCycle.objects.none())
    high_quality_kg = qty_field(_l("Grade A / HIGH (kg)"))
    low_quality_kg = qty_field(_l("Grade B / LOW (kg)"), required=False, min_value=Decimal("0"))
    harvest_loss_kg = qty_field(_l("Susut panen (kg)"), required=False, min_value=Decimal("0"))
    number_of_bags = forms.IntegerField(label=_l("Jumlah karung"), min_value=1, required=False)
    weight_per_bag_kg = qty_field(_l("Berat per karung (kg)"), required=False)
    harvested_at = forms.DateTimeField(
        label=_l("Waktu panen"), required=False, widget=DateTimeInput(),
        help_text=_l("Kosongkan untuk waktu sekarang."),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["cycle"].queryset = CropCycle.objects.filter(
            is_active=True, stage=CropCycle.Stage.HARVESTING
        ).select_related("element", "variety")


class LotChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, lot):
        return (
            _("Lot #%(pk)s · %(product)s · panen %(date)s · sisa %(left)s kg") % {
                "pk": lot.pk, "product": lot.product, "date": f"{timezone.localtime(lot.harvested_at):%d-%m-%Y}",
                "left": f"{lot.qty_remaining_kg.normalize():f}"}
        )


class SpoilageForm(StyledForm):
    lot = LotChoiceField(label=_l("Lot"), queryset=StockLot.objects.none(), empty_label="")
    qty_kg = qty_field(_l("Jumlah (kg)"))
    reason = forms.ChoiceField(label=_l("Alasan"), choices=SpoilageRecord.Reason.choices)
    notes = forms.CharField(label=_l("Catatan"), required=False, widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # FIFO order: the first lot of each product is its oldest (D14 preselect).
        self.fields["lot"].queryset = (
            StockLot.objects.filter(qty_remaining_kg__gt=0)
            .select_related("product__variety")
            .order_by("product__variety__name", "product__grade", "harvested_at", "id")
        )


class PriceForm(StyledForm):
    selling_price_idr = money_field(_l("Harga jual per kg (Rp)"))
    min_alert_kg = qty_field(_l("Batas stok menipis (kg)"), required=False, min_value=Decimal("0"))


class StageForm(forms.Form):
    stage = forms.ChoiceField(choices=CropCycle.Stage.choices)
