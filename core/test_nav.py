"""D125: one sidebar home per area; area pages share a tab strip."""

from django.test import TestCase
from django.urls import reverse

from farm.bootstrap import ROLE_CASHIER, ROLE_OWNER, ROLE_WORKER
from farm.tests import make_user


class NavigationTests(TestCase):
    def nav(self, user, url):
        self.client.force_login(user)
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200, url)
        return r.context

    def test_owner_sidebar_groups(self):
        ctx = self.nav(make_user("o", ROLE_OWNER), reverse("farm:produce"))
        groups = {str(g["title"]): [str(i["label"]) for i in g["items"]] for g in ctx["nav_groups"]}
        self.assertEqual(groups["KEBUN"], ["Elemen & Siklus", "Inventaris Input", "Stok Hasil Panen"])
        self.assertEqual(groups["PENJUALAN"], ["Kasir", "Grosir"])
        self.assertEqual(groups["KEUANGAN"], ["Biaya", "Analitik"])
        self.assertNotIn("Panen", sum(groups.values(), []))
        active = [str(i["label"]) for g in ctx["nav_groups"] for i in g["items"] if i["active"]]
        self.assertEqual(active, ["Stok Hasil Panen"])
        tabs = [str(t["label"]) for t in ctx["section_tabs"]["tabs"]]
        self.assertEqual(tabs, ["Stok", "Susut", "Opname", "Harga"])

    def test_worker_stock_tabs_hide_prices(self):
        ctx = self.nav(make_user("w", ROLE_WORKER), reverse("farm:spoilage"))
        tabs = [(str(t["label"]), t["active"]) for t in ctx["section_tabs"]["tabs"]]
        self.assertEqual(tabs, [("Stok", False), ("Susut", True), ("Opname", False)])

    def test_kasir_tabs_and_shift_chip(self):
        ctx = self.nav(make_user("c", ROLE_CASHIER), reverse("pos:orders"))
        self.assertEqual(ctx["section_tabs"]["key"], "kasir")
        self.assertFalse(ctx["section_tabs"]["shift_open"])
        self.assertContains(self.client.get(reverse("pos:orders")), "Shift belum dibuka")

    def test_grosir_and_analitik_tabs(self):
        owner = make_user("o", ROLE_OWNER)
        ctx = self.nav(owner, reverse("invoicing:buyers"))
        self.assertEqual([str(t["label"]) for t in ctx["section_tabs"]["tabs"]], ["Invoice", "Pembeli"])
        ctx = self.nav(owner, reverse("omzet_pph"))
        self.assertEqual([t["active"] for t in ctx["section_tabs"]["tabs"]], [False, True])
        active = [str(i["label"]) for g in ctx["nav_groups"] for i in g["items"] if i["active"]]
        self.assertEqual(active, ["Analitik"])

    def test_pages_outside_an_area_have_no_strip(self):
        ctx = self.nav(make_user("o", ROLE_OWNER), reverse("farm:cycles"))
        self.assertIsNone(ctx["section_tabs"])
