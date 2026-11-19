"""Invoicing forms: parse input only; rules live in `invoicing.services`."""

from decimal import Decimal, InvalidOperation

from django import forms
from django.utils import timezone

from core.forms import StyledMixin
from core.numbers import IDDecimalField, parse_id_decimal
from farm.models import PaymentMethod
from invoicing.models import InvoicePaymentMethod
from inventory.models import Product

from .models import Buyer, Invoice


class BuyerForm(StyledMixin, forms.ModelForm):
    class Meta:
        model = Buyer
        fields = ["name", "company", "phone", "email", "address", "payment_terms_days", "notes"]
        widgets = {"address": forms.Textarea(attrs={"rows": 2}), "notes": forms.Textarea(attrs={"rows": 2})}


class InvoiceHeaderForm(StyledMixin, forms.Form):
    buyer = forms.ModelChoiceField(label="Pembeli", queryset=Buyer.objects.filter(is_active=True))
    discount_type = forms.ChoiceField(label="Diskon", choices=Invoice.DiscountType.choices, initial="NONE")
    discount_value = IDDecimalField(label="Nilai diskon", min_value=0, max_digits=14, decimal_places=2,
                                    required=False, initial=0, kind="money2")
    shipping_idr = IDDecimalField(label="Ongkos kirim", min_value=0, max_digits=14, decimal_places=2,
                                  required=False, initial=0, kind="money")
    notes = forms.CharField(label="Catatan", required=False, widget=forms.Textarea(attrs={"rows": 2}))


def parse_invoice_lines(post):
    """`product_id[]`, `qty_kg[]`, `unit_price[]` → [(Product, qty, price|None)]; raises ValueError."""
    ids, qtys, prices = post.getlist("product_id"), post.getlist("qty_kg"), post.getlist("unit_price")
    if not (len(ids) == len(qtys) == len(prices)):
        raise ValueError("Baris invoice tidak valid.")
    products = Product.objects.select_related("variety", "tax").in_bulk([i for i in ids if i.isdigit()])
    lines = []
    for pid, qty, price in zip(ids, qtys, prices):
        if not pid and not qty:
            continue  # blank row
        product = products.get(int(pid)) if pid.isdigit() else None
        if product is None:
            raise ValueError("Pilih produk untuk setiap baris.")
        try:
            qty_d = parse_id_decimal(qty)
            price_d = parse_id_decimal(price) if price.strip() else None
        except (InvalidOperation, AttributeError):
            raise ValueError(f"Angka tidak valid pada baris {product}.")
        lines.append((product, qty_d, price_d))
    return lines


class IssueForm(StyledMixin, forms.Form):
    issue_date = forms.DateField(label="Tanggal terbit", widget=forms.DateInput(attrs={"type": "date"}))
    due_date = forms.DateField(label="Jatuh tempo", required=False, widget=forms.DateInput(attrs={"type": "date"}),
                               help_text="Kosongkan untuk memakai termin pembeli.")


class PaymentForm(StyledMixin, forms.Form):
    amount_idr = IDDecimalField(label="Jumlah diterima (Rp)", min_value=Decimal("0.01"), max_digits=14,
                                decimal_places=2, kind="money")
    method = forms.ChoiceField(label="Metode", choices=InvoicePaymentMethod.choices, widget=forms.RadioSelect, initial="CASH")
    paid_on = forms.DateField(label="Tanggal bayar", initial=timezone.localdate,
                              widget=forms.DateInput(attrs={"type": "date"}))
    reference = forms.CharField(label="Referensi", max_length=120, required=False)
    proof = forms.FileField(label="Bukti", required=False)
    withholding_slip_no = forms.CharField(label="Nomor bukti potong", max_length=60, required=False,
                                          help_text="Wajib untuk metode Potongan PPh (PPh yang dipotong pembeli).")
    into_drawer = forms.BooleanField(label="Masuk laci kasir", required=False,
                                     help_text="Tunai yang disimpan di laci shift yang sedang buka (D96).")


class CreditForm(StyledMixin, forms.Form):
    invoice = forms.ModelChoiceField(queryset=Invoice.objects.none())
    amount_idr = IDDecimalField(label="Jumlah", min_value=Decimal("0.01"), max_digits=14, decimal_places=2,
                                kind="money")

    def __init__(self, *args, buyer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["invoice"].queryset = Invoice.objects.filter(buyer=buyer, status=Invoice.Status.ISSUED)
