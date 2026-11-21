from django.utils.translation import gettext as _, gettext_lazy as _l
from django import forms
from django.conf import settings

from farm.bootstrap import ROLES

from .models import FarmProfile

INPUT = "app-field"  # D104: design-system field (gotani.css)
ROLE_CHOICES = [(r, r) for r in ROLES]


def select_prompts():
    """Prompt for an empty required select, by field name (D121); anything else reads "Pilih…"."""
    return {
        "cycle": _("Pilih siklus…"), "element": _("Pilih elemen…"), "variety": _("Pilih varietas…"),
        "input_item": _("Pilih input…"), "item": _("Pilih input…"), "category": _("Pilih kategori…"),
        "lot": _("Pilih lot…"), "buyer": _("Pilih pembeli…"), "invoice": _("Pilih invoice…"),
        "product": _("Pilih produk…"), "role": _("Pilih peran…"),
    }


def apply_select_prompts(form):
    """D121 (supersedes the Phase 7 blank first option): a required select starts on a prompt that names what to
    pick ("Pilih siklus…"); an optional one offers "Tidak ada" as a real choice."""
    prompts = None
    for name, field in form.fields.items():
        if isinstance(field.widget, (forms.RadioSelect, forms.CheckboxSelectMultiple, forms.HiddenInput)):
            continue
        if isinstance(field, forms.ModelChoiceField) and not isinstance(field, forms.ModelMultipleChoiceField):
            if field.empty_label is None:
                continue
            if getattr(field, "prompt_auto", False) or field.empty_label in ("", "---------", "- Select an option -"):
                prompts = prompts or select_prompts()
                field.empty_label = prompts.get(name, _("Pilih…")) if field.required else _("Tidak ada")
                field.prompt_auto = True  # a later call (after `required` changed) may rewrite it
        elif isinstance(field, forms.ChoiceField) and not isinstance(field, forms.MultipleChoiceField):
            choices = list(field.choices)
            if choices and choices[0][0] == "" and (getattr(field, "prompt_auto", False) or str(choices[0][1]) in ("", "-", "---------")):
                prompts = prompts or select_prompts()
                label = prompts.get(name, _("Pilih…")) if field.required else _("Tidak ada")
                field.choices = [("", label)] + choices[1:]
                field.prompt_auto = True


class StyledMixin:
    """`show_when = {"field": "other=A|B"}`: the field's row shows only while `other` has one of the values
    (forms.js hides and disables it otherwise, so it is not submitted)."""

    show_when: dict = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, cond in self.show_when.items():
            if name in self.fields:
                self.fields[name].show_when = cond
        apply_select_prompts(self)
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.ClearableFileInput, forms.RadioSelect, forms.CheckboxSelectMultiple)):
                field.widget.attrs.setdefault("class", INPUT)


class StaffCreateForm(StyledMixin, forms.Form):
    username = forms.CharField(label=_l("Nama pengguna"), max_length=150)
    full_name = forms.CharField(label=_l("Nama lengkap"), max_length=150, required=False)
    role = forms.ChoiceField(label=_l("Peran"), choices=ROLE_CHOICES)
    temp_password = forms.CharField(label=_l("Kata sandi sementara"), widget=forms.PasswordInput(render_value=False),
                                    help_text=_l("Pengguna wajib menggantinya saat pertama masuk."))


class StaffPasswordForm(StyledMixin, forms.Form):
    temp_password = forms.CharField(label=_l("Kata sandi sementara baru"), widget=forms.PasswordInput)


class FarmProfileForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = FarmProfile
        fields = ["name", "address", "phone", "latitude", "longitude", "logo", "is_pkp"]


class OwnProfileForm(StyledMixin, forms.Form):
    first_name = forms.CharField(label=_l("Nama depan"), max_length=150)
    last_name = forms.CharField(label=_l("Nama belakang"), max_length=150, required=False)
    language = forms.ChoiceField(label=_l("Bahasa"), choices=settings.LANGUAGES, widget=forms.RadioSelect)
