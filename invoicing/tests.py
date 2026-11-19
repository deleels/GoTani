"""Phase 8: invoicing — totals math, issue (locked FIFO), void, payments, credit, derived status."""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError

from inventory.models import StockLedger, Tax
from inventory.services import check_mass_balance
from invoicing import services as inv
from invoicing.models import Invoice
from pos.tests import NOON, PosFixtures

D = Decimal
TODAY = date(2026, 11, 19)


class TotalsMathTests(PosFixtures):
    def test_no_discount_no_tax(self):
        c = inv.compute_totals([(D("10"), D("20000"), 0)], "NONE", 0, 0)
        self.assertEqual((c.subtotal, c.discount, c.tax, c.total), (D("200000.00"), 0, 0, D("200000.00")))

    def test_percent_discount_allocated_and_taxed_after_discount(self):
        c = inv.compute_totals([(D("1"), D("100"), D("11")), (D("2"), D("100"), 0)], "PERCENT", D("10"), D("5000"))
        self.assertEqual(c.discount, D("30.00"))
        self.assertEqual([ln.discount_share for ln in c.lines], [D("10.00"), D("20.00")])
        self.assertEqual(c.lines[0].tax, D("9.90"))  # (100 - 10) x 11%
        self.assertEqual(c.total, D("300") - D("30") + D("9.90") + D("5000"))

    def test_rounding_remainder_goes_to_last_line(self):
        c = inv.compute_totals([(1, D("1"), 0), (1, D("1"), 0), (1, D("1"), 0)], "AMOUNT", D("1"), 0)
        self.assertEqual(sum(ln.discount_share for ln in c.lines), D("1.00"))
        self.assertEqual([ln.discount_share for ln in c.lines], [D("0.33"), D("0.33"), D("0.34")])

    def test_invalid_discounts_and_shipping(self):
        for dtype, val, ship in (("PERCENT", 101, 0), ("AMOUNT", 999, 0), ("BOGUS", 0, 0), ("NONE", 0, -1)):
            with self.assertRaises(ValidationError):
                inv.compute_totals([(1, D("100"), 0)], dtype, val, ship)


class InvoiceFixtures(PosFixtures):
    def setUp(self):
        super().setUp()
        self.buyer = inv.create_buyer(self.manager, name="Toko Segar", payment_terms_days=14)

    def draft(self, lines=None, **kw):
        lines = lines or [(self.high, 120, None), (self.low, 10, D("7000"))]
        return inv.save_draft(self.manager, self.buyer, lines, **kw)

    def issue(self, invoice=None, **kw):
        kw.setdefault("now", NOON)
        return inv.issue_invoice(invoice or self.draft(), self.manager, **kw)


class DraftTests(InvoiceFixtures):
    def test_draft_moves_no_stock_and_defaults_catalog_price(self):
        before = StockLedger.objects.count()
        d = self.draft()
        self.assertEqual(StockLedger.objects.count(), before)
        self.assertEqual(d.status, Invoice.Status.DRAFT)
        self.assertIsNone(d.number)
        line = d.lines.get(product=self.high)
        self.assertEqual(line.unit_price_idr, D("20000"))  # catalog default
        self.assertEqual(d.lines.get(product=self.low).unit_price_idr, D("7000"))  # override
        self.assertEqual(d.subtotal_idr, D("2470000.00"))

    def test_validation(self):
        with self.assertRaises(ValidationError):
            inv.save_draft(self.manager, self.buyer, [])
        with self.assertRaises(ValidationError):
            inv.save_draft(self.manager, self.buyer, [(self.high, 0, None)])
        with self.assertRaises(ValidationError):
            inv.save_draft(self.manager, self.buyer, [(self.high, 1, D("-5"))])
        self.high.selling_price_idr = None
        self.high.save()
        with self.assertRaises(ValidationError):
            inv.save_draft(self.manager, self.buyer, [(self.high, 1, None)])

    def test_roles(self):
        for user in (self.worker, self.cashier):
            with self.assertRaises(PermissionDenied):
                inv.save_draft(user, self.buyer, [(self.high, 1, None)])

    def test_edit_and_delete_only_drafts(self):
        d = self.draft()
        d = inv.save_draft(self.manager, self.buyer, [(self.high, 5, None)], invoice=d)
        self.assertEqual(d.lines.count(), 1)
        issued = self.issue(d)
        with self.assertRaises(ValidationError):
            inv.save_draft(self.manager, self.buyer, [(self.high, 1, None)], invoice=issued)
        with self.assertRaises(ValidationError):
            inv.delete_draft(self.manager, issued)


class IssueTests(InvoiceFixtures):
    def test_issue_allocates_fifo_and_logs_invoice_sale(self):
        invoice = self.issue()
        self.assertEqual(invoice.status, Invoice.Status.ISSUED)
        self.assertEqual(invoice.number, "INV-202611-0001")
        self.assertEqual(invoice.due_date, invoice.issue_date + timedelta(days=14))
        high_line = invoice.lines.get(product=self.high)
        allocs = list(high_line.allocations.order_by("lot__harvested_at"))
        self.assertEqual([(a.lot_id, a.qty_kg) for a in allocs], [(self.lot_h1.pk, D("100")), (self.lot_h2.pk, D("20"))])
        self.reload(self.lot_h1, self.lot_h2)
        self.assertEqual((self.lot_h1.qty_remaining_kg, self.lot_h2.qty_remaining_kg), (D("0"), D("30")))
        sale_rows = StockLedger.objects.filter(movement_type="INVOICE_SALE", reference=invoice.number)
        self.assertEqual(sale_rows.count(), 3)
        expected_cogs = 100 * self.lot_h1.unit_cogs_idr + 20 * self.lot_h2.unit_cogs_idr + 10 * self.lot_l1.unit_cogs_idr
        self.assertEqual(invoice.total_cogs_idr, inv.q_money(expected_cogs))
        self.assertEqual(check_mass_balance(), [])

    def test_shortage_fails_whole_invoice(self):
        d = self.draft([(self.high, 151, None), (self.low, 1, None)])
        with self.assertRaises(ValidationError) as ctx:
            self.issue(d)
        self.assertIn("tersedia 150", str(ctx.exception))
        self.assertFalse(StockLedger.objects.filter(movement_type="INVOICE_SALE").exists())
        d.refresh_from_db()
        self.assertEqual(d.status, Invoice.Status.DRAFT)

    def test_monthly_numbering_and_double_issue_rejected(self):
        a = self.issue(self.draft([(self.high, 1, None)]))
        b = self.issue(self.draft([(self.high, 1, None)]))
        c = self.issue(self.draft([(self.high, 1, None)]), issue_date=date(2026, 12, 1))
        self.assertEqual([a.number, b.number, c.number], ["INV-202611-0001", "INV-202611-0002", "INV-202612-0001"])
        with self.assertRaises(ValidationError):
            self.issue(a)

    def test_tax_snapshot_at_issue(self):
        from core.models import FarmProfile
        FarmProfile.objects.update_or_create(pk=1, defaults={"is_pkp": True})
        self.high.tax = Tax.objects.create(name="PPN", rate_pct=D("11"))
        self.high.save()
        invoice = self.issue(self.draft([(self.high, 10, None)], discount_type="PERCENT", discount_value=10,
                                        shipping_idr=50000))
        line = invoice.lines.get()
        self.assertEqual(line.tax_rate_pct, D("11"))
        self.assertEqual(line.net_revenue_idr, D("180000.00"))
        self.assertEqual(invoice.tax_idr, D("19800.00"))
        self.assertEqual(invoice.total_idr, D("200000") - D("20000") + D("19800") + D("50000"))

    def test_due_date_before_issue_rejected(self):
        with self.assertRaises(ValidationError):
            self.issue(issue_date=TODAY, due_date=TODAY - timedelta(days=1))


class VoidTests(InvoiceFixtures):
    def test_void_restores_exact_lots_and_releases_credit(self):
        invoice = self.issue()
        inv.record_payment(self.manager, self.buyer, amount_idr=1000000, method="TRANSFER", paid_on=TODAY,
                           allocations=[(invoice, 1000000)])
        self.assertEqual(inv.buyer_credit(self.buyer), D("0"))
        inv.void_invoice(invoice, self.owner, now=NOON + timedelta(days=40), reason="WRONG_ENTRY")
        self.reload(self.lot_h1, self.lot_h2, self.lot_l1)
        self.assertEqual((self.lot_h1.qty_remaining_kg, self.lot_h2.qty_remaining_kg, self.lot_l1.qty_remaining_kg),
                         (D("100"), D("50"), D("50")))
        self.assertEqual(StockLedger.objects.filter(movement_type="INVOICE_VOID").count(), 3)
        self.assertEqual(inv.buyer_credit(self.buyer), D("1000000.00"))
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.VOID)
        self.assertIsNone(invoice.payment_status())
        self.assertEqual(check_mass_balance(), [])
        with self.assertRaises(ValidationError):
            inv.void_invoice(invoice, self.owner, reason="WRONG_ENTRY")

    def test_void_requires_finance_role(self):
        invoice = self.issue()
        with self.assertRaises(PermissionDenied):
            inv.void_invoice(invoice, self.cashier, reason="WRONG_ENTRY")


class PaymentTests(InvoiceFixtures):
    def setUp(self):
        super().setUp()
        self.a = self.issue(self.draft([(self.high, 10, None)]), issue_date=TODAY)  # 200,000 due +14d
        self.b = self.issue(self.draft([(self.low, 10, None)]), issue_date=TODAY)  # 80,000

    def test_split_payment_and_credit(self):
        inv.record_payment(self.manager, self.buyer, amount_idr=300000, method="CASH", paid_on=TODAY,
                           allocations=[(self.a, 200000), (self.b, 50000)])
        self.assertEqual(self.a.payment_status(TODAY), Invoice.PaymentStatus.PAID)
        self.assertEqual(self.b.payment_status(TODAY), Invoice.PaymentStatus.PARTIALLY_PAID)
        self.assertEqual(self.b.outstanding_idr, D("30000.00"))
        self.assertEqual(inv.buyer_credit(self.buyer), D("50000.00"))

    def test_credit_applied_to_later_invoice(self):
        p = inv.record_payment(self.manager, self.buyer, amount_idr=100000, method="QRIS", paid_on=TODAY)
        self.assertEqual(inv.buyer_credit(self.buyer), D("100000.00"))
        inv.allocate_payment(self.manager, p, self.b, 80000)
        self.assertEqual(self.b.payment_status(TODAY), Invoice.PaymentStatus.PAID)
        self.assertEqual(inv.buyer_credit(self.buyer), D("20000.00"))

    def test_over_allocation_rejected(self):
        p = inv.record_payment(self.manager, self.buyer, amount_idr=100000, method="CASH", paid_on=TODAY)
        with self.assertRaises(ValidationError):  # more than invoice outstanding
            inv.allocate_payment(self.manager, p, self.b, 90000)
        with self.assertRaises(ValidationError):  # more than payment remainder
            inv.allocate_payment(self.manager, p, self.a, 150000)
        other = inv.create_buyer(self.manager, name="Lain")
        p2 = inv.record_payment(self.manager, other, amount_idr=10, method="CASH", paid_on=TODAY)
        with self.assertRaises(ValidationError):  # different buyer
            inv.allocate_payment(self.manager, p2, self.a, 10)
        draft = self.draft([(self.high, 1, None)])
        with self.assertRaises(ValidationError):  # draft
            inv.allocate_payment(self.manager, p, draft, 10)

    def test_overdue_is_derived(self):
        self.assertEqual(self.a.payment_status(TODAY), Invoice.PaymentStatus.UNPAID)
        self.assertEqual(self.a.payment_status(TODAY + timedelta(days=15)), Invoice.PaymentStatus.OVERDUE)
        summary = inv.receivables_summary(TODAY + timedelta(days=15))
        self.assertEqual(summary, {"outstanding": D("280000.00"), "overdue": D("280000.00")})

    def test_delete_payment_rules(self):
        p = inv.record_payment(self.manager, self.buyer, amount_idr=1000, method="CASH", paid_on=TODAY,
                               allocations=[(self.a, 500)])
        with self.assertRaises(PermissionDenied):
            inv.delete_payment(self.manager, p)
        with self.assertRaises(ValidationError):
            inv.delete_payment(self.owner, p)
        free = inv.record_payment(self.manager, self.buyer, amount_idr=1000, method="CASH", paid_on=TODAY)
        inv.delete_payment(self.owner, free)
        self.assertEqual(inv.buyer_credit(self.buyer), D("500.00"))

    def test_invalid_payment(self):
        with self.assertRaises(ValidationError):
            inv.record_payment(self.manager, self.buyer, amount_idr=0, method="CASH", paid_on=TODAY)
        with self.assertRaises(ValidationError):
            inv.record_payment(self.manager, self.buyer, amount_idr=10, method="BITCOIN", paid_on=TODAY)


class CreditTests(InvoiceFixtures):
    def test_apply_credit_spans_payments_oldest_first(self):
        a = self.issue(self.draft([(self.high, 10, None)]), issue_date=TODAY)  # 200,000
        p1 = inv.record_payment(self.manager, self.buyer, amount_idr=50000, method="CASH", paid_on=TODAY - timedelta(days=2))
        p2 = inv.record_payment(self.manager, self.buyer, amount_idr=100000, method="CASH", paid_on=TODAY)
        allocs = inv.apply_credit(self.manager, self.buyer, a, 120000)
        self.assertEqual([(x.payment_id, x.amount_idr) for x in allocs], [(p1.pk, D("50000.00")), (p2.pk, D("70000.00"))])
        self.assertEqual(inv.buyer_credit(self.buyer), D("30000.00"))
        self.assertEqual([i.pk for i in inv.open_invoices(self.buyer)], [a.pk])
        self.assertEqual(inv.open_invoices(self.buyer)[0].outstanding, D("80000.00"))

    def test_apply_credit_validation(self):
        a = self.issue(self.draft([(self.high, 1, None)]), issue_date=TODAY)
        with self.assertRaises(ValidationError):
            inv.apply_credit(self.manager, self.buyer, a, 10)  # no credit
        with self.assertRaises(ValidationError):
            inv.apply_credit(self.manager, self.buyer, a, 0)
        with self.assertRaises(PermissionDenied):
            inv.apply_credit(self.worker, self.buyer, a, 10)

    def test_update_buyer(self):
        inv.update_buyer(self.manager, self.buyer, phone="0812", payment_terms_days=30)
        self.buyer.refresh_from_db()
        self.assertEqual((self.buyer.phone, self.buyer.payment_terms_days), ("0812", 30))
        with self.assertRaises(PermissionDenied):
            inv.update_buyer(self.cashier, self.buyer, phone="x")


class AgeingAndSplitTests(InvoiceFixtures):
    """D97: receivables ageing buckets and money received per payment method."""

    def test_ageing_buckets(self):
        invoice = self.issue(self.draft([(self.high, 1, None)]))  # due = issue + 14 days
        due = invoice.due_date
        for days_late, key in ((-1, "current"), (0, "current"), (1, "d1_30"), (45, "d31_60"), (75, "d61_90"), (91, "d90p")):
            ageing = inv.receivables_ageing(today=due + timedelta(days=days_late))
            self.assertEqual(ageing["rows"][0][key], invoice.total_idr, (days_late, key))
            self.assertEqual(ageing["totals"]["total"], invoice.total_idr)
        inv.record_payment(self.manager, self.buyer, amount_idr=invoice.total_idr, method="QRIS", paid_on=due,
                           allocations=[(invoice, invoice.total_idr)])
        self.assertEqual(inv.receivables_ageing(today=due)["rows"], [])

    def test_payment_split(self):
        from reports.services import payment_split

        invoice = self.issue(self.draft([(self.high, 1, None)]))
        inv.record_payment(self.manager, self.buyer, amount_idr=1000, method="TRANSFER", paid_on=invoice.issue_date)
        rows = {r["method"]: r for r in payment_split(invoice.issue_date, invoice.issue_date)}
        self.assertEqual(rows["Transfer"]["invoice"], D("1000"))
        self.assertEqual(rows["Transfer"]["total"], D("1000"))
