"""Buyers, invoices and payments screens (thin views over `invoicing.services`). OWNER/MANAGER only (rule 24)."""

from decimal import InvalidOperation
from farm.templatetags.gotani import rupiah
from django.utils.translation import gettext_lazy as _l
from django.utils.translation import gettext as _
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import F, Prefetch, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View

from core.numbers import format_id_decimal, parse_id_decimal
from core.pagination import paginate
from core.services import get_farm_profile
from farm.access import FINANCE_ROLES, RoleRequiredMixin
from farm.bootstrap import ROLE_OWNER
from farm.views_common import error_text
from inventory.models import Product

from . import services
from .forms import BuyerForm, CreditForm, InvoiceHeaderForm, IssueForm, PaymentForm, parse_invoice_lines
from .models import Buyer, Invoice, InvoiceLine, InvoiceLineLotAllocation, InvoicePayment, PaymentAllocation


def _id_text(raw, places):
    """Echo a submitted number back in Indonesian format; unparseable text is echoed as-is."""
    try:
        return format_id_decimal(parse_id_decimal(raw), places) if raw.strip() else ""
    except Exception:
        return raw

ZERO = services.ZERO


class FinanceView(RoleRequiredMixin, View):
    allowed_roles = FINANCE_ROLES


def _annotate_status(invoices, today):
    for inv in invoices:
        inv.pay_status = inv.payment_status(today)
        inv.pay_label = Invoice.PaymentStatus(inv.pay_status).label if inv.pay_status else inv.get_status_display()
        inv.pill_key = inv.pay_status or inv.status
    return invoices


# --- Buyers ---------------------------------------------------------------------------


class BuyersView(FinanceView):
    template_name = "invoicing/buyers.html"

    def context(self, request, form=None):
        tab = request.GET.get("tab", "active")
        q = request.GET.get("q", "").strip()
        sort = request.GET.get("sort", "name")
        qs = services.with_customer_value(Buyer.objects.filter(is_active=(tab != "inactive")))
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(company__icontains=q) | Q(phone__icontains=q))
        # D115: most valuable customers first on request; nulls (no orders yet) last.
        qs = {"ltgp": qs.order_by("-ltgp", "name"), "last": qs.order_by(F("last_order").desc(nulls_last=True), "name")}.get(sort, qs)
        page = paginate(request, qs)
        for b in page.object_list:
            b.credit = services.buyer_credit(b)
            b.outstanding = sum((i.outstanding for i in services.open_invoices(b)), ZERO)
            b.cadence = services.customer_cadence(b)
        summary = services.receivables_summary()
        return {
            "page": page, "tab": tab, "q": q, "sort": sort, "form": form or BuyerForm(),
            "sheet_open": bool(form is not None and form.errors) or self.request.GET.get("new") == "1",
            "stats": {"buyers": Buyer.objects.filter(is_active=True).count(), **summary},
            "ageing": services.receivables_ageing(),
        }

    def get(self, request):
        return render(request, self.template_name, self.context(request))

    def post(self, request):
        form = BuyerForm(request.POST)
        if form.is_valid():
            buyer = services.create_buyer(request.user, **form.cleaned_data)
            messages.success(request, _("Pembeli %(buyer)s ditambahkan.") % {"buyer": buyer})
            return redirect("invoicing:buyer_detail", pk=buyer.pk)
        return render(request, self.template_name, self.context(request, form), status=400)


class BuyerDetailView(FinanceView):
    template_name = "invoicing/buyer_detail.html"

    def context(self, request, buyer, form=None):
        today = timezone.localdate()
        buyer = services.with_customer_value(Buyer.objects.filter(pk=buyer.pk)).get()
        invoices = _annotate_status(list(buyer.invoices.order_by("-created_at", "-id")), today)
        payments = list(buyer.payments.prefetch_related("allocations__invoice").select_related("recorded_by"))
        open_inv = services.open_invoices(buyer)
        return {
            "buyer": buyer, "invoices": invoices, "payments": payments, "open_invoices": open_inv,
            "cadence": services.customer_cadence(buyer, today),
            "credit": services.buyer_credit(buyer),
            "outstanding": sum((i.outstanding for i in open_inv), ZERO),
            "overdue": sum((i.outstanding for i in open_inv if i.due_date and i.due_date < today), ZERO),
            "tab": request.GET.get("tab", "invoices"),
            "form": form or BuyerForm(instance=buyer), "sheet_open": bool(form is not None and form.errors),
        }

    def get(self, request, pk):
        buyer = get_object_or_404(Buyer, pk=pk)
        return render(request, self.template_name, self.context(request, buyer))

    def post(self, request, pk):
        buyer = get_object_or_404(Buyer, pk=pk)
        action = request.POST.get("action", "edit")
        if action == "toggle":
            services.update_buyer(request.user, buyer, is_active=not buyer.is_active)
            messages.success(request, _("Pembeli diaktifkan.") if buyer.is_active else _("Pembeli dinonaktifkan."))
            return redirect("invoicing:buyer_detail", pk=pk)
        if action == "credit":
            form = CreditForm(request.POST, buyer=buyer)
            if form.is_valid():
                try:
                    services.apply_credit(request.user, buyer, form.cleaned_data["invoice"], form.cleaned_data["amount_idr"])
                except ValidationError as exc:
                    messages.error(request, error_text(exc))
                else:
                    messages.success(request, _("Kredit diterapkan ke %(v)s.") % {"v": form.cleaned_data['invoice']})
            else:
                messages.error(request, _("Data kredit tidak valid."))
            return redirect("invoicing:buyer_detail", pk=pk)
        form = BuyerForm(request.POST, instance=Buyer.objects.get(pk=pk))
        if form.is_valid():
            services.update_buyer(request.user, buyer, **form.cleaned_data)
            messages.success(request, _("Data pembeli disimpan."))
            return redirect("invoicing:buyer_detail", pk=pk)
        return render(request, self.template_name, self.context(request, buyer, form), status=400)


# --- Invoices ---------------------------------------------------------------------------

INVOICE_TABS = [
    ("all", _l("Semua")), ("draft", _l("Draf")), ("unpaid", _l("Belum lunas")), ("overdue", _l("Jatuh tempo")),
    ("paid", _l("Lunas")), ("void", _l("Batal")),
]


class InvoicesView(FinanceView):
    template_name = "invoicing/invoices.html"

    def get(self, request):
        today = timezone.localdate()
        tab = request.GET.get("tab", "all")
        q = request.GET.get("q", "").strip()
        qs = Invoice.objects.select_related("buyer").prefetch_related("allocations")
        if q:
            qs = qs.filter(Q(number__icontains=q) | Q(buyer__name__icontains=q) | Q(buyer__company__icontains=q))
        if tab == "draft":
            qs = qs.filter(status=Invoice.Status.DRAFT)
        elif tab == "void":
            qs = qs.filter(status=Invoice.Status.VOID)
        items = _annotate_status(list(qs), today)
        wanted = {"unpaid": {"UNPAID", "PARTIALLY_PAID", "OVERDUE"}, "overdue": {"OVERDUE"}, "paid": {"PAID"}}.get(tab)
        if wanted:
            items = [i for i in items if i.pay_status in wanted]
        month_start = today.replace(day=1)
        issued_month = Invoice.objects.filter(status=Invoice.Status.ISSUED, issue_date__gte=month_start)
        ctx = {
            "page": paginate(request, items), "tab": tab, "tabs": INVOICE_TABS, "q": q,
            "stats": {
                **services.receivables_summary(today),
                "month_total": issued_month.aggregate(s=Sum("total_idr"))["s"] or ZERO,
                "month_count": issued_month.count(),
                "drafts": Invoice.objects.filter(status=Invoice.Status.DRAFT).count(),
            },
        }
        return render(request, self.template_name, ctx)


def _product_catalog():
    return [
        {"id": p.pk, "name": str(p), "price": str(p.selling_price_idr or ""), "tax": str(p.tax_rate_pct),
         "available": str(p.stock_kg)}
        for p in Product.objects.select_related("variety", "tax").order_by("variety__name", "grade")
    ]


class InvoiceEditorView(FinanceView):
    """Full-page editor (D69) for new and DRAFT invoices."""

    template_name = "invoicing/invoice_editor.html"

    def get_invoice(self, pk):
        if pk is None:
            return None
        invoice = get_object_or_404(Invoice, pk=pk)
        if invoice.status != Invoice.Status.DRAFT:
            raise PermissionDenied(_("Hanya invoice draf yang dapat diubah."))
        return invoice

    def render_editor(self, request, invoice, form, lines, status=200):
        return render(request, self.template_name, {
            "invoice": invoice, "form": form, "products": _product_catalog(), "lines": lines,
        }, status=status)

    def get(self, request, pk=None):
        invoice = self.get_invoice(pk)
        if invoice:
            form = InvoiceHeaderForm(initial={
                "buyer": invoice.buyer_id, "discount_type": invoice.discount_type,
                "discount_value": invoice.discount_value, "shipping_idr": invoice.shipping_idr, "notes": invoice.notes,
            })
            lines = [{"product": ln.product_id, "qty": format_id_decimal(ln.qty_kg, 3),
                      "price": format_id_decimal(ln.unit_price_idr, 2)}
                     for ln in invoice.lines.all()]
        else:
            form = InvoiceHeaderForm(initial={"buyer": request.GET.get("buyer")})
            lines = []
        return self.render_editor(request, invoice, form, lines)

    def post(self, request, pk=None):
        invoice = self.get_invoice(pk)
        form = InvoiceHeaderForm(request.POST)
        raw = list(zip(request.POST.getlist("product_id"), request.POST.getlist("qty_kg"),
                       request.POST.getlist("unit_price")))
        echo = [{"product": int(p) if p.isdigit() else "", "qty": _id_text(q, 3), "price": _id_text(pr, 2)}
                for p, q, pr in raw]
        if form.is_valid():
            try:
                lines = parse_invoice_lines(request.POST)
                d = form.cleaned_data
                invoice = services.save_draft(
                    request.user, d["buyer"], lines, discount_type=d["discount_type"],
                    discount_value=d["discount_value"] or 0, shipping_idr=d["shipping_idr"] or 0,
                    notes=d["notes"], invoice=invoice,
                )
            except (ValueError, ValidationError) as exc:
                form.add_error(None, error_text(exc) if isinstance(exc, ValidationError) else str(exc))
            else:
                messages.success(request, _("Draf invoice disimpan. Terbitkan untuk memotong stok."))
                return redirect("invoicing:invoice_detail", pk=invoice.pk)
        return self.render_editor(request, invoice, form, echo, status=400)


def _invoice_with_trace(pk):
    return get_object_or_404(
        Invoice.objects.select_related("buyer", "created_by", "issued_by", "voided_by").prefetch_related(
            Prefetch("lines", queryset=InvoiceLine.objects.select_related("product__variety").prefetch_related(
                Prefetch("allocations", queryset=InvoiceLineLotAllocation.objects.select_related(
                    "lot__harvest_batch__cycle__element", "lot__harvest_batch__cycle__variety",
                ).order_by("lot__harvested_at", "lot_id")),
            )),
            Prefetch("allocations", queryset=services.PaymentAllocation.objects.select_related("payment")),
        ),
        pk=pk,
    )


class InvoiceDetailView(FinanceView):
    template_name = "invoicing/invoice_detail.html"

    def get(self, request, pk):
        invoice = _invoice_with_trace(pk)
        _annotate_status([invoice], timezone.localdate())
        today = timezone.localdate()
        ctx = {
            "invoice": invoice, "paid": invoice.paid_idr, "outstanding": invoice.outstanding_idr,
            "credit": services.buyer_credit(invoice.buyer),
            "issue_form": IssueForm(initial={"issue_date": today}),
            "is_owner": self.roles.has(ROLE_OWNER),
        }
        return render(request, self.template_name, ctx)


class InvoicePrintView(FinanceView):
    template_name = "invoicing/invoice_print.html"

    def get(self, request, pk):
        invoice = _invoice_with_trace(pk)
        _annotate_status([invoice], timezone.localdate())
        return render(request, self.template_name, {"invoice": invoice, "profile": get_farm_profile(),
                                                    "paid": invoice.paid_idr, "outstanding": invoice.outstanding_idr})


class InvoiceActionView(FinanceView):
    http_method_names = ["post"]

    def post(self, request, pk, action):
        invoice = get_object_or_404(Invoice, pk=pk)
        target = reverse("invoicing:invoice_detail", args=[pk])
        try:
            if action == "issue":
                if request.POST.get("confirmed_checked") != "1":  # D88: level-2 confirmation
                    raise ValidationError(_('Centang "Saya sudah memeriksa data ini" untuk menerbitkan invoice.'))
                form = IssueForm(request.POST)
                if not form.is_valid():
                    raise ValidationError(_("Tanggal tidak valid."))
                invoice = services.issue_invoice(invoice, request.user, issue_date=form.cleaned_data["issue_date"],
                                                 due_date=form.cleaned_data["due_date"])
                msg = _("Invoice %(number)s diterbitkan; stok dipotong (FIFO).") % {"number": invoice.number}
            elif action == "void":
                services.void_invoice(invoice, request.user, reason=request.POST.get("void_reason", ""),
                                      note=request.POST.get("reason", ""))
                msg = _("Invoice %(number)s dibatalkan; stok dikembalikan dan pembayaran menjadi kredit.") % {"number": invoice.number}
            elif action == "delete":
                services.delete_draft(request.user, invoice)
                messages.success(request, _("Draf invoice dihapus."))
                return redirect("invoicing:invoices")
            else:
                raise ValidationError(_("Aksi tidak dikenal."))
        except ValidationError as exc:
            messages.error(request, error_text(exc))
        else:
            messages.success(request, msg)
        return redirect(target)


class AllocationRemoveView(FinanceView):
    """Take a payment allocation off an invoice; the money returns to buyer credit (D88)."""

    http_method_names = ["post"]

    def post(self, request, pk):
        allocation = get_object_or_404(PaymentAllocation, pk=pk)
        try:
            invoice = services.remove_allocation(request.user, allocation, request.POST.get("reason", ""))
        except ValidationError as exc:
            messages.error(request, error_text(exc))
            invoice = allocation.invoice
        else:
            messages.success(request, _("Alokasi dilepas; dananya kembali menjadi kredit pembeli."))
        return redirect("invoicing:invoice_detail", invoice.pk)


# --- Payments ---------------------------------------------------------------------------


class PaymentCreateView(FinanceView):
    """Full page (D69, D114): record money received and split it across open invoices; the rest is credit.

    Opens prefilled: with ?invoice=<pk> the amount is that invoice's balance (and it is listed first),
    otherwise the buyer's whole balance. The page spreads the amount over the invoices as it is typed."""

    template_name = "invoicing/payment_form.html"

    def open_rows(self, request, buyer):
        rows = services.open_invoices(buyer)
        pre = request.GET.get("invoice") or request.POST.get("invoice")
        rows.sort(key=lambda inv: str(inv.pk) != str(pre))  # the invoice we came from first; stable otherwise
        return rows, pre

    def render_page(self, request, buyer, form, status=200, entered=None):
        rows, pre = self.open_rows(request, buyer)
        if entered is None:  # first open: spread the prefilled amount
            left = form.initial.get("amount_idr") or ZERO
            entered = {}
            for inv in rows:
                take = min(left, inv.outstanding)
                entered[inv.pk] = take if take > 0 else ""
                left -= take
        for inv in rows:
            inv.entered = entered.get(inv.pk, "")
        return render(request, self.template_name, {
            "buyer": buyer, "form": form, "open_invoices": rows, "credit": services.buyer_credit(buyer),
            "preselect": pre or "", "manual": status != 200,
        }, status=status)

    def get(self, request, pk):
        buyer = get_object_or_404(Buyer, pk=pk)
        rows, pre = self.open_rows(request, buyer)
        chosen = [inv for inv in rows if str(inv.pk) == str(pre)]
        prefill = (chosen[0].outstanding if chosen else sum((inv.outstanding for inv in rows), ZERO)) or None
        form = PaymentForm(buyer=buyer, initial={"paid_on": timezone.localdate(), "amount_idr": prefill})
        return self.render_page(request, buyer, form)

    def post(self, request, pk):
        buyer = get_object_or_404(Buyer, pk=pk)
        form = PaymentForm(request.POST, request.FILES, buyer=buyer)
        entered, allocations, bad = {}, [], []
        for inv in services.open_invoices(buyer):
            raw = request.POST.get(f"alloc_{inv.pk}", "").strip()
            if not raw:
                continue
            try:
                value = parse_id_decimal(raw, "money")
            except (InvalidOperation, ValueError):
                entered[inv.pk] = raw
                bad.append(str(inv))
                continue
            entered[inv.pk] = value
            if value > 0:
                allocations.append((inv, value))
        if bad:
            form.add_error(None, _("Jumlah untuk %(invoices)s bukan angka.") % {"invoices": ", ".join(bad)})
        if form.is_valid():
            d = form.cleaned_data
            try:
                payment = services.record_payment(
                    request.user, buyer, amount_idr=d["amount_idr"], method=d["method"], paid_on=d["paid_on"],
                    reference=d["reference"], proof=d["proof"], allocations=allocations,
                    into_drawer=d.get("into_drawer", False), withholding_slip_no=d.get("withholding_slip_no", ""),
                    apply_leftover=request.POST.get("leftover") == "apply",
                )
            except ValidationError as exc:
                form.add_error(None, error_text(exc))
            else:
                left = payment.unallocated_idr
                msg = _("Pembayaran %(amount)s dari %(buyer)s dicatat.") % {"amount": rupiah(payment.amount_idr), "buyer": buyer}
                if left:
                    msg += " " + _("%(left)s disimpan sebagai kredit %(buyer)s.") % {"left": rupiah(left), "buyer": buyer}
                messages.success(request, msg)
                return redirect("invoicing:buyer_detail", pk=buyer.pk)
        return self.render_page(request, buyer, form, status=400, entered=entered)


class PaymentVoidView(FinanceView):
    """Payments are voided with a reason, never deleted (D100)."""

    http_method_names = ["post"]

    def post(self, request, pk):
        payment = get_object_or_404(InvoicePayment, pk=pk)
        try:
            services.void_payment(request.user, payment, request.POST.get("reason", ""))
        except ValidationError as exc:
            messages.error(request, error_text(exc))
        else:
            messages.success(request, _("Pembayaran dibatalkan; catatannya tetap disimpan."))
        return redirect(reverse("invoicing:buyer_detail", args=[payment.buyer_id]) + "?tab=payments")
