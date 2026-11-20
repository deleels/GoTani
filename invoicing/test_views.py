"""Invoicing screens: role gates (403), draft → issue → pay → credit → void through HTTP."""

from datetime import timedelta
from decimal import Decimal

from django.urls import reverse
from django.utils import timezone

from invoicing import services as inv
from invoicing.models import Buyer, Invoice, InvoicePayment
from invoicing.tests import InvoiceFixtures

D = Decimal


class InvoicingViewTests(InvoiceFixtures):
    def login(self, user):
        self.client.force_login(user)

    def test_finance_only(self):
        urls = [reverse("invoicing:invoices"), reverse("invoicing:buyers"), reverse("invoicing:invoice_new"),
                reverse("invoicing:buyer_detail", args=[self.buyer.pk]),
                reverse("invoicing:buyer_payment_new", args=[self.buyer.pk])]
        for user in (self.worker, self.cashier):
            self.login(user)
            for url in urls:
                self.assertEqual(self.client.get(url).status_code, 403, url)
        self.client.logout()
        self.assertEqual(self.client.get(urls[0]).status_code, 302)

    def test_list_pages_render_every_tab(self):
        self.issue(self.draft([(self.high, 1, None)]))
        self.draft([(self.low, 1, None)])
        self.login(self.manager)
        for tab in ("all", "draft", "unpaid", "overdue", "paid", "void"):
            r = self.client.get(reverse("invoicing:invoices"), {"tab": tab, "q": ""})
            self.assertEqual(r.status_code, 200)
        r = self.client.get(reverse("invoicing:invoices"), {"tab": "draft"})
        self.assertContains(r, "Draf #")
        self.assertContains(self.client.get(reverse("invoicing:buyers")), "Toko Segar")

    def test_create_buyer_and_edit(self):
        self.login(self.manager)
        r = self.client.post(reverse("invoicing:buyers"), {"name": "Resto Hijau", "payment_terms_days": 7})
        buyer = Buyer.objects.get(name="Resto Hijau")
        self.assertRedirects(r, reverse("invoicing:buyer_detail", args=[buyer.pk]))
        r = self.client.post(reverse("invoicing:buyers"), {"name": ""})
        self.assertEqual(r.status_code, 400)
        self.client.post(reverse("invoicing:buyer_detail", args=[buyer.pk]),
                         {"action": "edit", "name": "Resto Hijau", "payment_terms_days": 30, "phone": "0812"})
        buyer.refresh_from_db()
        self.assertEqual((buyer.payment_terms_days, buyer.phone), (30, "0812"))
        self.client.post(reverse("invoicing:buyer_detail", args=[buyer.pk]), {"action": "toggle"})
        buyer.refresh_from_db()
        self.assertFalse(buyer.is_active)

    def test_full_flow_draft_issue_pay_credit_void(self):
        self.login(self.manager)
        r = self.client.post(reverse("invoicing:invoice_new"), {
            "buyer": self.buyer.pk, "discount_type": "PERCENT", "discount_value": "10", "shipping_idr": "5000",
            "notes": "", "product_id": [self.high.pk, ""], "qty_kg": ["10", ""], "unit_price": ["", ""],
        })
        invoice = Invoice.objects.get()
        self.assertRedirects(r, reverse("invoicing:invoice_detail", args=[invoice.pk]))
        self.assertEqual(invoice.total_idr, D("185000.00"))  # 200k − 10% + 5k shipping
        self.assertContains(self.client.get(reverse("invoicing:invoice_edit", args=[invoice.pk])), "Simpan draf")

        today = timezone.localdate()
        self.client.post(reverse("invoicing:invoice_action", args=[invoice.pk, "issue"]),
                         {"issue_date": today.isoformat(), "confirmed_checked": "1"})
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "ISSUED")
        self.assertEqual(invoice.due_date, today + timedelta(days=14))
        r = self.client.get(reverse("invoicing:invoice_detail", args=[invoice.pk]))
        self.assertContains(r, f"LOT-{self.lot_h1.pk}")  # backward trace on the invoice
        self.assertContains(self.client.get(reverse("invoicing:invoice_print", args=[invoice.pk])), invoice.number)
        self.assertEqual(self.client.get(reverse("invoicing:invoice_edit", args=[invoice.pk])).status_code, 403)

        r = self.client.post(reverse("invoicing:buyer_payment_new", args=[self.buyer.pk]), {
            "amount_idr": "200000", "method": "TRANSFER", "paid_on": today.isoformat(), f"alloc_{invoice.pk}": "185000",
        })
        self.assertRedirects(r, reverse("invoicing:buyer_detail", args=[self.buyer.pk]))
        self.assertEqual(invoice.payment_status(), "PAID")
        self.assertEqual(inv.buyer_credit(self.buyer), D("15000.00"))

        second = self.issue(self.draft([(self.low, 5, None)]))  # 40,000
        self.client.post(reverse("invoicing:buyer_detail", args=[self.buyer.pk]),
                         {"action": "credit", "invoice": second.pk, "amount_idr": "15000"})
        self.assertEqual(inv.buyer_credit(self.buyer), D("0.00"))
        self.assertEqual(second.payment_status(), "PARTIALLY_PAID")

        self.client.post(reverse("invoicing:invoice_action", args=[invoice.pk, "void"]), {"void_reason": "WRONG_PRICE"})
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, "VOID")
        self.assertEqual(inv.buyer_credit(self.buyer), D("185000.00"))
        self.assertBalanced()

    def test_editor_errors_rerender(self):
        self.login(self.manager)
        r = self.client.post(reverse("invoicing:invoice_new"), {
            "buyer": self.buyer.pk, "discount_type": "NONE", "product_id": ["abc"], "qty_kg": ["1"], "unit_price": [""],
        })
        self.assertEqual(r.status_code, 400)
        r = self.client.post(reverse("invoicing:invoice_new"), {
            "buyer": self.buyer.pk, "discount_type": "AMOUNT", "discount_value": "999999999",
            "product_id": [self.high.pk], "qty_kg": ["1"], "unit_price": [""],
        })
        self.assertEqual(r.status_code, 400)
        self.assertFalse(Invoice.objects.exists())

    def test_issue_shortage_shows_message_and_stays_draft(self):
        d = self.draft([(self.high, 999, None)])
        self.login(self.manager)
        r = self.client.post(reverse("invoicing:invoice_action", args=[d.pk, "issue"]),
                             {"issue_date": timezone.localdate().isoformat(), "confirmed_checked": "1"}, follow=True)
        self.assertContains(r, "tidak cukup")
        d.refresh_from_db()
        self.assertEqual(d.status, "DRAFT")

    def test_delete_draft_and_payment(self):
        d = self.draft()
        self.login(self.owner)
        self.client.post(reverse("invoicing:invoice_action", args=[d.pk, "delete"]))
        self.assertFalse(Invoice.objects.exists())
        p = inv.record_payment(self.manager, self.buyer, amount_idr=1000, method="CASH", paid_on=timezone.localdate())
        self.client.post(reverse("invoicing:buyer_payment_void", args=[p.pk]), {"reason": "dobel"})
        p.refresh_from_db()
        self.assertIsNotNone(p.voided_at)  # D100: voided, kept
        page = self.client.get(reverse("invoicing:buyer_detail", args=[self.buyer.pk]), {"tab": "payments"})
        self.assertContains(page, "dobel")
        p = inv.record_payment(self.manager, self.buyer, amount_idr=1000, method="CASH", paid_on=timezone.localdate())
        self.login(self.manager)
        self.assertEqual(self.client.post(reverse("invoicing:buyer_payment_void", args=[p.pk]), {"reason": "x"}).status_code, 403)

    def test_payment_over_allocation_rejected(self):
        invoice = self.issue(self.draft([(self.high, 1, None)]))
        self.login(self.manager)
        r = self.client.post(reverse("invoicing:buyer_payment_new", args=[self.buyer.pk]), {
            "amount_idr": "100", "method": "CASH", "paid_on": timezone.localdate().isoformat(), f"alloc_{invoice.pk}": "500",
        })
        self.assertEqual(r.status_code, 400)
        self.assertFalse(InvoicePayment.objects.exists())
        self.assertContains(r, invoice.number if hasattr(invoice, "number") else str(invoice), status_code=400)


class PaymentPageTests(InvoiceFixtures):
    """Phase 7 step 4 (D114): prefilled balance, Indonesian-formatted partial amounts, leftover, tax opt-in."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.manager)
        self.a = self.issue(self.draft([(self.high, 1, None)]))
        self.b = self.issue(self.draft([(self.low, 1, None)]))
        self.url = reverse("invoicing:buyer_payment_new", args=[self.buyer.pk])

    def post(self, follow=False, **data):
        return self.client.post(self.url, {"method": "CASH", "paid_on": timezone.localdate().isoformat(), **data},
                                follow=follow)

    def test_opens_prefilled_with_the_invoice_balance(self):
        r = self.client.get(self.url, {"invoice": self.b.pk})
        form = r.context["form"]
        self.assertEqual(form.initial["amount_idr"], self.b.outstanding_idr)
        self.assertEqual(r.context["open_invoices"][0].pk, self.b.pk)  # the one we came from is first
        self.assertEqual(r.context["open_invoices"][0].entered, self.b.outstanding_idr)
        self.assertEqual(r.context["open_invoices"][1].entered, "")
        self.assertContains(r, 'class="app-money"')
        whole = self.client.get(self.url).context["form"].initial["amount_idr"]
        self.assertEqual(whole, self.a.outstanding_idr + self.b.outstanding_idr)

    def test_partial_payment_with_indonesian_thousands(self):
        part = (self.a.outstanding_idr / 2).quantize(D("1"))
        typed = f"{int(part):,}".replace(",", ".")  # what the field shows: "12.500"
        r = self.post(amount_idr=typed, **{f"alloc_{self.a.pk}": typed})
        self.assertEqual(r.status_code, 302)
        self.a.refresh_from_db()
        self.assertEqual(self.a.outstanding_idr, self.a.total_idr - part)
        self.assertEqual(self.a.payment_status(), "PARTIALLY_PAID")
        self.assertEqual(inv.buyer_credit(self.buyer), 0)

    def test_leftover_applied_to_other_open_invoices(self):
        total = self.a.outstanding_idr + self.b.outstanding_idr
        self.post(amount_idr=str(total), leftover="apply", **{f"alloc_{self.a.pk}": str(self.a.outstanding_idr)})
        self.a.refresh_from_db(); self.b.refresh_from_db()
        self.assertEqual((self.a.outstanding_idr, self.b.outstanding_idr), (0, 0))
        self.assertEqual(inv.buyer_credit(self.buyer), 0)

    def test_leftover_kept_as_credit_says_so(self):
        r = self.post(amount_idr=str(self.a.outstanding_idr + 1000), leftover="credit",
                      **{f"alloc_{self.a.pk}": str(self.a.outstanding_idr)}, follow=True)
        self.assertEqual(inv.buyer_credit(self.buyer), 1000)
        self.assertContains(r, "sebagai kredit")

    def test_tax_slip_method_only_for_buyers_that_withhold(self):
        r = self.client.get(self.url)
        self.assertNotContains(r, 'value="PPH_WITHHELD"')
        self.assertNotIn("withholding_slip_no", r.context["form"].fields)
        self.buyer.withholds_tax = True
        self.buyer.save()
        r = self.client.get(self.url)
        self.assertContains(r, 'value="PPH_WITHHELD"')
        self.assertContains(r, 'data-show-when="method=PPH_WITHHELD"')
        r = self.post(amount_idr="1000", method="PPH_WITHHELD")
        self.assertEqual(r.status_code, 400)
        self.assertContains(r, "nomor bukti potong", status_code=400)


class ConfirmationTests(InvoiceFixtures):
    """D88: issuing needs the level-2 tick; allocations can be taken off with a reason."""

    def login(self, user):
        self.client.force_login(user)

    def test_issue_requires_level_two_tick(self):
        d = self.draft([(self.high, 1, None)])
        self.login(self.manager)
        r = self.client.post(reverse("invoicing:invoice_action", args=[d.pk, "issue"]),
                             {"issue_date": timezone.localdate().isoformat()}, follow=True)
        self.assertContains(r, "Saya sudah memeriksa data ini")
        d.refresh_from_db()
        self.assertEqual(d.status, "DRAFT")

    def test_remove_allocation_returns_money_to_credit(self):
        from core.models import AuditLog
        from invoicing import services
        from invoicing.models import PaymentAllocation

        invoice = self.issue(self.draft([(self.high, 1, None)]))
        payment = services.record_payment(self.manager, invoice.buyer, amount_idr=invoice.total_idr, method="CASH",
                                          paid_on=timezone.localdate(), allocations=[(invoice, invoice.total_idr)])
        alloc = PaymentAllocation.objects.get(payment=payment)
        self.login(self.manager)
        url = reverse("invoicing:allocation_remove", args=[alloc.pk])
        self.client.post(url, {"reason": ""})
        self.assertTrue(PaymentAllocation.objects.filter(pk=alloc.pk).exists())  # reason required
        self.client.post(url, {"reason": "salah invoice"})
        self.assertFalse(PaymentAllocation.objects.filter(pk=alloc.pk).exists())
        self.assertEqual(services.buyer_credit(invoice.buyer), invoice.total_idr)
        self.assertEqual(AuditLog.objects.get(action="ALLOCATION_REMOVED").reason, "salah invoice")
