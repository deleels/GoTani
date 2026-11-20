"""Phase 7 step 5 (D115): customer value (LTGP), buying rhythm, at-risk flag, LTGP sort, Analytics AOV."""

from datetime import date, timedelta
from decimal import Decimal

from django.urls import reverse

from invoicing import services as inv
from invoicing.models import Buyer
from invoicing.tests import InvoiceFixtures

D = Decimal


class CustomerValueTests(InvoiceFixtures):
    def issued_on(self, day, product=None):
        return self.issue(self.draft([(product or self.high, 1, None)]), issue_date=day)

    def value(self, buyer=None):
        return inv.with_customer_value(Buyer.objects.filter(pk=(buyer or self.buyer).pk)).get()

    def test_ltgp_counts_issued_invoices_only(self):
        a = self.issued_on(date(2026, 9, 1))
        self.draft([(self.low, 1, None)])  # drafts never count
        b = self.value()
        revenue = sum(ln.net_revenue_idr for ln in a.lines.all())
        self.assertEqual(b.ltgp, revenue - a.total_cogs_idr)
        self.assertEqual((b.orders, b.first_order, b.last_order), (1, date(2026, 9, 1), date(2026, 9, 1)))

    def test_cadence_and_at_risk(self):
        for d in (date(2026, 9, 1), date(2026, 9, 11), date(2026, 9, 21)):
            self.issued_on(d)
        b = self.value()
        c = inv.customer_cadence(b, today=date(2026, 10, 10))  # 19 days since, usual gap 10
        self.assertEqual((c.avg_gap_days, c.days_since_last, c.at_risk), (10.0, 19, False))
        self.assertTrue(inv.customer_cadence(b, today=date(2026, 10, 12)).at_risk)  # 21 > 2 × 10

    def test_one_order_is_never_at_risk(self):
        self.issued_on(date(2026, 1, 1))
        c = inv.customer_cadence(self.value(), today=date(2026, 11, 1))
        self.assertIsNone(c.avg_gap_days)
        self.assertFalse(c.at_risk)

    def test_buyers_list_sorts_by_ltgp_and_page_shows_value(self):
        small = inv.create_buyer(self.manager, name="Aaa Kecil")
        self.issued_on(date(2026, 9, 1))
        self.client.force_login(self.manager)
        names = [b.name for b in self.client.get(reverse("invoicing:buyers"), {"sort": "ltgp"}).context["page"].object_list]
        self.assertLess(names.index("Toko Segar"), names.index(small.name))
        r = self.client.get(reverse("invoicing:buyer_detail", args=[self.buyer.pk]))
        self.assertContains(r, "LTGP")
        self.assertContains(r, "Jeda antar order")

    def test_analytics_aov(self):
        from reports.services import dashboard_metrics

        self.issued_on(self.today if hasattr(self, "today") else date.today())
        k = dashboard_metrics(date.today() - timedelta(days=400), date.today() + timedelta(days=1))["kpis"]
        self.assertEqual(k["aov"], (k["revenue"] / k["transactions"]).quantize(D("0.01")))
