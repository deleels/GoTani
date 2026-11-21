from django.utils.translation import gettext_lazy as _l
from django import forms
from django.conf import settings

from farm.bootstrap import ROLES

from .models import FarmProfile

INPUT = "app-field"  # D104: design-system field (gotani.css)
ROLE_CHOICES = [(r, r) for r in ROLES]


class StyledMixin:
    """`show_when = {"field": "other=A|B"}`: the field's row shows only while `other` has one of the values
    (forms.js hides and disables it otherwise, so it is not submitted)."""

    show_when: dict = {}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, cond in self.show_when.items():
            if name in self.fields:
                self.fields[name].show_when = cond
        for field in self.fields.values():
            if isinstance(field, forms.ModelChoiceField) and field.empty_label in ("---------", "- Select an option -"):
                field.empty_label = ""  # Phase 7: blank first option, no "pilih" text
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
