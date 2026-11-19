"""Display filters: `Rp 15.000` (whole Rupiah, ROUND_HALF_UP), kg and percentages."""

from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from django import template

register = template.Library()


def _dec(value):
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def _thousands(n: Decimal) -> str:
    return f"{int(n):,}".replace(",", ".")


@register.filter
def rupiah(value):
    d = _dec(value)
    if d is None:
        return "-"
    whole = d.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    sign = "-" if whole < 0 else ""
    return f"{sign}Rp {_thousands(abs(whole))}"


@register.filter
def kg(value):
    """1500.250 → '1.500,25'; trailing zeros trimmed."""
    d = _dec(value)
    if d is None:
        return "-"
    d = d.quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)
    sign = "-" if d < 0 else ""
    d = abs(d)
    whole = int(d)
    frac = f"{d - whole:f}"[2:].rstrip("0") if d != whole else ""
    return f"{sign}{_thousands(Decimal(whole))}" + (f",{frac}" if frac else "")


@register.filter
def idnum(value, places=3):
    """Pre-fill for a `data-num` input: `25000.00` → `25.000`, `0.500` → `0,5` (D81)."""
    from core.numbers import format_id_decimal

    return format_id_decimal(value, int(places)) if _dec(value) is not None else ""


@register.filter
def pct(value, places=1):
    d = _dec(value)
    if d is None:
        return "-"
    q = Decimal(1).scaleb(-int(places))
    return f"{d.quantize(q, rounding=ROUND_HALF_UP)}".replace(".", ",") + "%"
