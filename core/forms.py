from django import forms

from farm.bootstrap import ROLES

from .models import FarmProfile

INPUT = "w-full rounded-lg border border-stone-300 bg-white px-3 py-2 text-sm"
ROLE_CHOICES = [(r, r) for r in ROLES]


class StyledMixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            if not isinstance(field.widget, (forms.CheckboxInput, forms.ClearableFileInput)):
                field.widget.attrs.setdefault("class", INPUT)


class StaffCreateForm(StyledMixin, forms.Form):
    username = forms.CharField(label="Nama pengguna", max_length=150)
    full_name = forms.CharField(label="Nama lengkap", max_length=150, required=False)
    role = forms.ChoiceField(label="Peran", choices=ROLE_CHOICES)
    temp_password = forms.CharField(label="Kata sandi sementara", widget=forms.PasswordInput(render_value=False),
                                    help_text="Pengguna wajib menggantinya saat pertama masuk.")


class StaffPasswordForm(StyledMixin, forms.Form):
    temp_password = forms.CharField(label="Kata sandi sementara baru", widget=forms.PasswordInput)


class FarmProfileForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = FarmProfile
        fields = ["name", "address", "phone", "latitude", "longitude", "logo"]


class OwnProfileForm(StyledMixin, forms.Form):
    first_name = forms.CharField(label="Nama depan", max_length=150)
    last_name = forms.CharField(label="Nama belakang", max_length=150, required=False)
