"""Indonesian number input (D81): `15.000` = fifteen thousand, `1,5` = one and a half.

Inputs are formatted live in the browser (`core/static/core/numbers.js`), which converts them back
to plain machine numbers on submit. The server still accepts either form so a request without JS
(or a test client) works the same.
"""

import re
from decimal import Decimal, InvalidOperation

from django import forms

_MULTI_DOT = re.compile(r"^-?\d{1,3}(\.\d{3}){2,}$")  # 1.500.000
_ONE_GROUP = re.compile(r"^-?\d{1,3}\.\d{3}$")  # 15.000 — thousands only in a whole-Rupiah field

# kind -> decimals allowed: money = whole Rupiah, money2 = Rupiah with ,xx, kg = up to 3 decimals.
KINDS = {"money": 0, "money2": 2, "kg": 3}


def parse_id_decimal(value, kind=None):
    """'1.500,25' -> 1500.25, '1.500.000' -> 1500000, '2,75' -> 2.75, '1.5' -> 1.5.

    A single dot without a comma is a decimal point (machine numbers, which the browser submits),
    except in a whole-Rupiah field (`kind="money"`) or after an "Rp" prefix (pasted display text,
    D105), where '15.000' means fifteen thousand.
    Raises InvalidOperation."""
    if isinstance(value, (Decimal, int)):
        return Decimal(value)
    text = str(value).strip().replace(" ", "").replace(" ", "")
    has_rp = text.lower().startswith("rp")
    if has_rp:
        text = text[2:]
    if "," in text:
        text = text.replace(".", "").replace(",", ".")
    elif _MULTI_DOT.match(text) or ((kind == "money" or has_rp) and _ONE_GROUP.match(text)):
        text = text.replace(".", "")
    if not text:
        raise InvalidOperation(value)
    return Decimal(text)


def format_id_decimal(value, places=3) -> str:
    """Decimal -> '1.500,25' for a pre-filled input (trailing zeros trimmed, no 'Rp')."""
    if value in (None, ""):
        return ""
    d = Decimal(str(value))
    sign = "-" if d < 0 else ""
    d = abs(d).quantize(Decimal(1).scaleb(-places))
    whole, _, frac = f"{d:f}".partition(".")
    frac = frac.rstrip("0")
    return sign + f"{int(whole):,}".replace(",", ".") + (f",{frac}" if frac else "")


class IDNumberInput(forms.TextInput):
    def __init__(self, kind="kg", attrs=None):
        self.kind = kind
        super().__init__({"inputmode": "decimal", "data-num": kind, "autocomplete": "off", **(attrs or {})})

    def format_value(self, value):
        # Re-rendered bound data is the submitted machine text ("1500.25"): show it Indonesian too,
        # because the browser reads every dot in an input as a thousands separator.
        if isinstance(value, str) and value.strip():
            try:
                value = parse_id_decimal(value, self.kind)
            except (InvalidOperation, ValueError):
                return value
        if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
            return format_id_decimal(value, KINDS[self.kind])
        return super().format_value(value)


class IDDecimalField(forms.DecimalField):
    """DecimalField that accepts Indonesian formatting; `kind` picks the live formatter."""

    def __init__(self, *args, kind="kg", **kwargs):
        kwargs.setdefault("widget", IDNumberInput(kind))
        super().__init__(*args, **kwargs)

    def to_python(self, value):
        if value in self.empty_values:
            return None
        try:
            value = parse_id_decimal(value, self.widget.kind if isinstance(self.widget, IDNumberInput) else None)
        except (InvalidOperation, ValueError):
            raise forms.ValidationError(self.error_messages["invalid"], code="invalid")
        return super().to_python(value)

    def widget_attrs(self, widget):
        return {}  # text input: no HTML min/max/step, the server validates
