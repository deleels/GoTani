"""Phase 5 tests: role matrix (403s), view smoke tests, traceability, analytics."""

import csv
import io
from datetime import date, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.template import Context, Template
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from farm import services as farm
from farm.access import UserRoles
from farm.bootstrap import ROLE_CASHIER, ROLE_MANAGER, ROLE_OWNER, ROLE_WORKER
from farm.models import (
    ActivityLog,
    CropCycle,
    Expense,
    ExpenseCategory,
    FarmElement,
    HarvestBatch,
    InputItem,
    Unit,
    Variety,
)
from inventory import services as inventory
from inventory.models import Grade, Product, SpoilageRecord, StockLedger
from pos import services as pos
from pos.models import SaleOrder
from reports import services

D = Decimal
ROLES = (ROLE_OWNER, ROLE_MANAGER, ROLE_WORKER, ROLE_CASHIER)


def make_user(username, role=None):
    user = get_user_model().objects.create_user(username, password="x")
    if role:
        user.groups.add(Group.objects.get_or_create(name=role)[0])
    return user


class SiteFixtures(TestCase):
    """One melon cycle, fully exercised:
    - NPK purchase 100 kg @ 10.000; fertilizer activity 5 kg → usage expense 50.000
    - labour 1.150.000 approved → pool 1.200.000; harvest HIGH 100 / LOW 50 → COGS 10.000 / 4.000
    - spoilage 5 kg LOW (loss 20.000); sale 10 kg HIGH (revenue 200.000, COGS 100.000)
    - GENERAL utility overhead 300.000
    - a closed cacao cycle with 75.000 never absorbed
    """

    @classmethod
    def setUpTestData(cls):
        cls.users = {role: make_user(role.lower(), role) for role in ROLES}
        cls.nobody = make_user("nobody")
        cls.owner = cls.users[ROLE_OWNER]
        cat = {c.code: c for c in ExpenseCategory.objects.all()}

        cls.variety = Variety.objects.create(name="Melon Golden", crop="Melon")
        cls.element = FarmElement.objects.create(
            name="Greenhouse A", element_type=FarmElement.ElementType.GREENHOUSE, area_m2=D("500")
        )
        cls.cycle = farm.start_cycle(cls.element, cls.variety, date(2026, 8, 1))
        cls.high = Product.objects.create(variety=cls.variety, grade=Grade.HIGH, selling_price_idr=20000)
        cls.low = Product.objects.create(variety=cls.variety, grade=Grade.LOW, selling_price_idr=8000)

        cls.npk = InputItem.objects.create(
            name="NPK", item_type=InputItem.ItemType.FERTILIZER, unit=Unit.KG, min_alert_qty=D("200")
        )
        farm.record_input_purchase(cls.npk, 100, 10000, supplier="Toko Tani")
        cls.activity = farm.log_activity(
            cls.cycle, ActivityLog.ActivityType.FERTILIZER, quantity=5, unit=Unit.KG, input_item=cls.npk
        )
        labour = farm.create_expense(cat["TENAGA_KERJA"], "ELEMENT", 1150000, element=cls.element, cycle=cls.cycle)
        farm.approve_expense(labour, cls.owner)
        farm.advance_cycle_stage(cls.cycle, CropCycle.Stage.HARVESTING)
        cls.batch = farm.record_harvest(cls.cycle, 100, 50, harvested_at=timezone.now() - timedelta(days=1))
        cls.lot_high = cls.batch.lots.get(product=cls.high)
        cls.lot_low = cls.batch.lots.get(product=cls.low)
        inventory.record_spoilage(cls.lot_low, 5, "ROT")
        cls.order = pos.checkout({cls.high.pk: 10}, "CASH", cls.users[ROLE_CASHIER])

        general = farm.create_expense(cat["UTILITAS"], "GENERAL", 300000, narration="Listrik")
        farm.approve_expense(general, cls.owner)
        cls.pending = farm.create_expense(cat["PERALATAN"], "ELEMENT", 99000, element=cls.element)

        cacao = Variety.objects.create(name="Kakao Forastero", crop="Kakao")
        shade = FarmElement.objects.create(
            name="Shadenet C", element_type=FarmElement.ElementType.SHADENET, area_m2=D("800")
        )
        cls.closed_cycle = farm.start_cycle(shade, cacao, date(2026, 5, 1))
        leftover = farm.create_expense(cat["TENAGA_KERJA"], "ELEMENT", 75000, element=shade, cycle=cls.closed_cycle)
        farm.approve_expense(leftover, cls.owner)
        farm.close_cycle(cls.closed_cycle)

    def login(self, role):
        user = self.users[role] if role in self.users else role
        self.client.force_login(user)
        return user


# --- Role matrix (D27) ------------------------------------------------------------

FINANCE = {ROLE_OWNER, ROLE_MANAGER}
FARM = FINANCE | {ROLE_WORKER}
POS = FINANCE | {ROLE_CASHIER}


class RoleMatrixTests(SiteFixtures):
    def get_pages(self):
        return [
            (reverse("dashboard"), FINANCE),
            (reverse("farm:cycles"), FARM),
            (reverse("farm:activities"), FARM),
            (reverse("farm:inputs"), FARM),
            (reverse("farm:harvests"), FARM),
            (reverse("farm:spoilage"), FARM),
            (reverse("farm:expenses"), FINANCE),
            (reverse("farm:prices"), FINANCE),
            (reverse("farm:element_create"), FINANCE),
            (reverse("pos:register"), POS),
            (reverse("pos:stock"), POS),
            (reverse("pos:orders"), POS),
            (reverse("pos:receipt", args=[self.order.pk]), POS),
            (reverse("reports:trace_index"), FINANCE),
            (reverse("reports:trace_cycle", args=[self.cycle.pk]), FINANCE),
            (reverse("reports:trace_order", args=[self.order.order_number]), FINANCE),
        ]

    def test_get_access_matrix(self):
        for role in ROLES:
            self.login(role)
            for url, allowed in self.get_pages():
                with self.subTest(role=role, url=url):
                    expected = 200 if role in allowed else 403
                    self.assertEqual(self.client.get(url).status_code, expected)

    def test_user_without_role_gets_403_everywhere(self):
        self.client.force_login(self.nobody)
        for url, _ in self.get_pages():
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 403)
        self.assertEqual(self.client.get("/").status_code, 403)

    def test_anonymous_redirected_to_login(self):
        for url, _ in self.get_pages() + [("/", None)]:
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 302)
                self.assertTrue(response["Location"].startswith(reverse("login")))

    def test_post_actions_denied(self):
        denied = [
            (reverse("farm:expense_approve", args=[self.pending.pk]), {ROLE_WORKER, ROLE_CASHIER}),
            (reverse("farm:expense_reject", args=[self.pending.pk]), {ROLE_WORKER, ROLE_CASHIER}),
            (reverse("farm:harvest_delete", args=[self.batch.pk]), {ROLE_WORKER, ROLE_CASHIER}),
            (reverse("farm:activity_delete", args=[self.activity.pk]), {ROLE_WORKER, ROLE_CASHIER}),
            (reverse("farm:element_toggle", args=[self.element.pk]), {ROLE_WORKER, ROLE_CASHIER}),
            (reverse("farm:inputs"), {ROLE_WORKER, ROLE_CASHIER}),  # purchases are financial
            (reverse("farm:prices"), {ROLE_WORKER, ROLE_CASHIER}),
            (reverse("pos:void", args=[self.order.pk]), {ROLE_WORKER, ROLE_CASHIER}),
            (reverse("farm:cycle_stage", args=[self.cycle.pk]), {ROLE_CASHIER}),
            (reverse("farm:harvests"), {ROLE_CASHIER}),
            (reverse("farm:spoilage"), {ROLE_CASHIER}),
            (reverse("pos:checkout"), {ROLE_WORKER}),
        ]
        for url, roles in denied:
            for role in roles:
                self.login(role)
                with self.subTest(role=role, url=url):
                    self.assertEqual(self.client.post(url, {"stage": "CLEARING"}).status_code, 403)
        # Nothing changed.
        self.pending.refresh_from_db()
        self.order.refresh_from_db()
        self.assertEqual(self.pending.status, Expense.Status.PENDING)
        self.assertEqual(self.order.status, SaleOrder.Status.COMPLETED)
        self.assertTrue(HarvestBatch.objects.filter(pk=self.batch.pk).exists())

    def test_home_routes_by_role(self):
        self.login(ROLE_WORKER)
        self.assertRedirects(self.client.get("/"), reverse("farm:cycles"))
        self.login(ROLE_CASHIER)
        self.assertRedirects(self.client.get("/"), reverse("pos:register"))
        for role in (ROLE_OWNER, ROLE_MANAGER):
            self.login(role)
            response = self.client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertTemplateUsed(response, "reports/dashboard.html")

    def test_superuser_is_owner(self):
        root = get_user_model().objects.create_superuser("root", password="x")
        self.client.force_login(root)
        self.assertEqual(self.client.get(reverse("farm:expenses")).status_code, 200)
        self.assertEqual(UserRoles(root).label, ROLE_OWNER)

    def test_worker_sees_no_money(self):
        self.login(ROLE_WORKER)
        for name in ("farm:cycles", "farm:activities", "farm:inputs", "farm:harvests", "farm:spoilage"):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "Rp ")
                self.assertNotContains(response, "Biaya")
        # The same pages do show money to a manager.
        self.login(ROLE_MANAGER)
        self.assertContains(self.client.get(reverse("farm:harvests")), "Rp 10.000")

    def test_nav_reflects_role(self):
        self.login(ROLE_CASHIER)
        response = self.client.get(reverse("pos:register"))
        self.assertNotContains(response, reverse("farm:cycles"))
        self.assertNotContains(response, reverse("reports:trace_index"))
        self.login(ROLE_WORKER)
        response = self.client.get(reverse("farm:cycles"))
        self.assertNotContains(response, reverse("pos:register"))
        self.assertNotContains(response, reverse("farm:expenses"))

    def test_cashier_cannot_see_void_button(self):
        self.login(ROLE_CASHIER)
        self.assertNotContains(self.client.get(reverse("pos:orders")), reverse("pos:void", args=[self.order.pk]))
        self.login(ROLE_MANAGER)
        self.assertContains(self.client.get(reverse("pos:orders")), reverse("pos:void", args=[self.order.pk]))


# --- Farm operation flows --------------------------------------------------------------


class FarmViewTests(SiteFixtures):
    def test_login_page_and_login(self):
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)
        response = self.client.post(reverse("login"), {"username": "worker", "password": "x"})
        self.assertRedirects(response, "/", target_status_code=302)

    def test_cycles_page_shows_alerts_and_stage_form(self):
        self.login(ROLE_MANAGER)
        response = self.client.get(reverse("farm:cycles"))
        self.assertContains(response, "Stok menipis: NPK")
        self.assertContains(response, "1 biaya menunggu persetujuan")
        self.assertContains(response, "Majukan tahap")
        self.assertContains(response, "Rp 75.000")  # unabsorbed cost of the closed cycle

    def test_stage_advance_and_backward_rejected(self):
        self.login(ROLE_WORKER)
        url = reverse("farm:cycle_stage", args=[self.cycle.pk])
        response = self.client.post(url, {"stage": "FRUITING"}, follow=True)
        self.assertContains(response, "Tahap hanya boleh maju")
        self.client.post(url, {"stage": "CLEARING"})
        self.cycle.refresh_from_db()
        self.assertFalse(self.cycle.is_active)

    def test_start_cycle_and_create_element(self):
        self.login(ROLE_OWNER)
        response = self.client.post(reverse("farm:element_create"), {"name": "Hidro D", "element_type": "HYDROPONIC", "area_m2": "120"})
        self.assertRedirects(response, reverse("farm:cycles"))
        element = FarmElement.objects.get(name="Hidro D")
        response = self.client.post(reverse("farm:cycles"), {"element": element.pk, "variety": self.variety.pk, "planting_start_date": "2026-11-01"})
        self.assertRedirects(response, reverse("farm:cycles"))
        self.assertTrue(element.cycles.filter(is_active=True).exists())
        # Duplicate element name is a form error, not a crash.
        response = self.client.post(reverse("farm:element_create"), {"name": "hidro d", "element_type": "HYDROPONIC", "area_m2": "1"})
        self.assertContains(response, "sudah dipakai")

    def test_element_with_active_cycle_cannot_be_deactivated(self):
        self.login(ROLE_OWNER)
        response = self.client.post(reverse("farm:element_toggle", args=[self.element.pk]), follow=True)
        self.assertContains(response, "masih memiliki siklus tanam aktif")

    def test_log_activity_uses_item_unit_and_reports_errors(self):
        self.login(ROLE_WORKER)
        url = reverse("farm:activities")
        response = self.client.post(url, {"cycle": self.cycle.pk, "activity_type": "FERTILIZER", "input_item": self.npk.pk, "quantity": "2"})
        self.assertRedirects(response, url)
        self.npk.refresh_from_db()
        self.assertEqual(self.npk.stock_qty, D("93"))
        response = self.client.post(url, {"cycle": self.cycle.pk, "activity_type": "FERTILIZER", "input_item": self.npk.pk, "quantity": "1000"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["form"].non_field_errors())

    def test_input_purchase(self):
        self.login(ROLE_MANAGER)
        response = self.client.post(reverse("farm:inputs"), {"item": self.npk.pk, "qty": "50", "cost_per_unit_idr": "13000"})
        self.assertRedirects(response, reverse("farm:inputs"))
        self.npk.refresh_from_db()
        self.assertEqual(self.npk.stock_qty, D("145"))
        self.assertContains(self.client.get(reverse("farm:inputs")), "Stok menipis")

    def test_expense_create_approve_reject_revert(self):
        self.login(ROLE_MANAGER)
        cat = ExpenseCategory.objects.get(code="LAINNYA")
        response = self.client.post(reverse("farm:expenses"), {
            "category": cat.pk, "scope": "GENERAL", "element": self.element.pk, "amount_idr": "12500",
            "date_incurred": "2026-11-19", "payment_method": "CASH", "narration": "Air",
        })
        self.assertRedirects(response, reverse("farm:expenses"))
        created = Expense.objects.get(narration="Air")
        self.assertIsNone(created.element)  # GENERAL ignores the element field
        self.client.post(reverse("farm:expense_approve", args=[created.pk]))
        created.refresh_from_db()
        self.assertEqual(created.status, Expense.Status.APPROVED)
        self.client.post(reverse("farm:expense_revert", args=[created.pk]))
        self.client.post(reverse("farm:expense_reject", args=[created.pk]))
        created.refresh_from_db()
        self.assertEqual(created.status, Expense.Status.REJECTED)
        self.client.post(reverse("farm:expense_delete", args=[created.pk]))
        self.assertFalse(Expense.objects.filter(pk=created.pk).exists())

    def test_absorbed_expense_cannot_be_reverted(self):
        self.login(ROLE_OWNER)
        absorbed = Expense.objects.filter(absorbed_by=self.batch).first()
        response = self.client.post(reverse("farm:expense_revert", args=[absorbed.pk]), follow=True)
        self.assertContains(response, "sudah diserap")

    def test_harvest_entry_and_protected_delete(self):
        self.login(ROLE_WORKER)
        response = self.client.post(reverse("farm:harvests"), {"cycle": self.cycle.pk, "high_quality_kg": "20", "low_quality_kg": "4"})
        self.assertRedirects(response, reverse("farm:harvests"))
        new_batch = HarvestBatch.objects.latest("id")
        self.assertEqual(new_batch.lots.count(), 2)
        self.login(ROLE_MANAGER)
        response = self.client.post(reverse("farm:harvest_delete", args=[self.batch.pk]), follow=True)
        self.assertContains(response, "tidak dapat dihapus")
        self.client.post(reverse("farm:harvest_delete", args=[new_batch.pk]))
        self.assertFalse(HarvestBatch.objects.filter(pk=new_batch.pk).exists())

    def test_spoilage_preselects_oldest_lot(self):
        newer = farm.record_harvest(self.cycle, 30, 0, harvested_at=timezone.now())
        self.login(ROLE_WORKER)
        response = self.client.get(reverse("farm:spoilage"), {"product": self.high.pk})
        self.assertEqual(response.context["form"].initial["lot"], self.lot_high.pk)
        self.assertEqual(response.context["initial_product"], self.high.pk)
        lots = next(p["lots"] for p in response.context["lot_products"] if p["id"] == self.high.pk)
        self.assertEqual(lots, [self.lot_high.pk, newer.lots.get().pk])
        response = self.client.post(reverse("farm:spoilage"), {"lot": self.lot_high.pk, "qty_kg": "1.5", "reason": "PEST"})
        self.assertRedirects(response, reverse("farm:spoilage"))
        self.assertEqual(SpoilageRecord.objects.filter(lot=self.lot_high).get().qty_kg, D("1.5"))

    def test_spoilage_over_stock_is_form_error(self):
        self.login(ROLE_WORKER)
        response = self.client.post(reverse("farm:spoilage"), {"lot": self.lot_low.pk, "qty_kg": "999", "reason": "ROT"})
        self.assertEqual(response.status_code, 200)
        self.assertIn("tidak cukup", " ".join(response.context["form"].non_field_errors()))

    def test_set_and_clear_price(self):
        cabai = Variety.objects.create(name="Cabai Rawit", crop="Cabai")
        unpriced = Product.objects.create(variety=cabai, grade=Grade.HIGH)
        self.login(ROLE_MANAGER)
        self.assertContains(self.client.get(reverse("farm:prices")), "Cabai Rawit")
        self.client.post(reverse("farm:prices"), {"product": unpriced.pk, "selling_price_idr": "45000"})
        unpriced.refresh_from_db()
        self.assertEqual(unpriced.selling_price_idr, D("45000"))
        self.client.post(reverse("farm:prices"), {"product": unpriced.pk, "clear": "1"})
        unpriced.refresh_from_db()
        self.assertIsNone(unpriced.selling_price_idr)
        response = self.client.post(reverse("farm:prices"), {"product": unpriced.pk, "selling_price_idr": "-5"}, follow=True)
        self.assertContains(response, "Harga tidak valid")

    def test_delete_activity_restores_stock(self):
        self.login(ROLE_MANAGER)
        response = self.client.post(reverse("farm:activity_delete", args=[self.activity.pk]), follow=True)
        # The usage expense was absorbed by the harvest, so deletion is refused.
        self.assertContains(response, "sudah diserap")


# --- POS views -------------------------------------------------------------------------


class PosViewTests(SiteFixtures):
    def test_register_lists_only_priced_products(self):
        Product.objects.create(variety=Variety.objects.create(name="Cabai Rawit", crop="Cabai"), grade=Grade.HIGH)
        self.login(ROLE_CASHIER)
        response = self.client.get(reverse("pos:register"))
        names = {p["name"] for p in response.context["products"]}
        self.assertEqual(names, {"Melon Golden"})
        stock = self.client.get(reverse("pos:stock")).json()["products"]
        self.assertEqual({p["id"]: D(p["available"]) for p in stock}, {self.high.pk: D("90"), self.low.pk: D("45")})

    def test_checkout_ignores_client_price_and_shows_receipt(self):
        self.login(ROLE_CASHIER)
        response = self.client.post(reverse("pos:checkout"), {
            "product_id": [self.high.pk, self.low.pk], "qty_kg": ["2.5", "1"], "unit_price_idr": ["1", "1"],
            "payment_method": "QRIS",
        })
        order = SaleOrder.objects.latest("id")
        self.assertRedirects(response, reverse("pos:receipt", args=[order.pk]) + "?new=1")
        self.assertEqual(order.total_revenue_idr, D("58000"))
        receipt = self.client.get(reverse("pos:receipt", args=[order.pk]))
        self.assertContains(receipt, order.order_number)
        self.assertContains(receipt, "Rp 58.000")
        self.assertContains(receipt, "size: 58mm")
        self.assertNotContains(receipt, "HPP")
        a4 = self.client.get(reverse("pos:receipt", args=[order.pk]), {"size": "a4"})
        self.assertContains(a4, "size: A4")

    def test_checkout_shortage_redirects_with_message(self):
        self.login(ROLE_CASHIER)
        response = self.client.post(reverse("pos:checkout"), {"product_id": [self.high.pk], "qty_kg": ["91"], "payment_method": "CASH"}, follow=True)
        self.assertContains(response, "tersedia 90 kg")
        self.assertEqual(SaleOrder.objects.count(), 1)

    def test_checkout_malformed_cart(self):
        self.login(ROLE_CASHIER)
        response = self.client.post(reverse("pos:checkout"), {"product_id": [self.high.pk], "payment_method": "CASH"}, follow=True)
        self.assertContains(response, "Isi keranjang tidak valid")

    def test_void_via_view(self):
        self.login(ROLE_MANAGER)
        response = self.client.post(reverse("pos:void", args=[self.order.pk]))
        self.assertRedirects(response, reverse("pos:receipt", args=[self.order.pk]))
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SaleOrder.Status.VOIDED)
        self.assertContains(self.client.get(reverse("pos:receipt", args=[self.order.pk])), "DIBATALKAN")
        response = self.client.post(reverse("pos:void", args=[self.order.pk]), follow=True)
        self.assertContains(response, "sudah dibatalkan")
        self.assertEqual(inventory.check_mass_balance(), [])

    def test_void_rejects_unsafe_next(self):
        self.login(ROLE_MANAGER)
        response = self.client.post(reverse("pos:void", args=[self.order.pk]), {"next": "https://evil.example/"})
        self.assertEqual(response["Location"], reverse("pos:receipt", args=[self.order.pk]))

    def test_orders_today(self):
        self.login(ROLE_CASHIER)
        self.assertContains(self.client.get(reverse("pos:orders")), self.order.order_number)


# --- Traceability ------------------------------------------------------------------------


class TraceabilityTests(SiteFixtures):
    def test_forward_trace_totals(self):
        trace = services.forward_trace(self.cycle)
        t = trace.totals
        self.assertEqual(len(trace.activities), 1)
        self.assertEqual(len(trace.batches), 1)
        self.assertEqual(len(trace.batches[0].expenses), 2)  # usage + labour
        self.assertEqual(t["harvested_kg"], D("150"))
        self.assertEqual(t["sold_kg"], D("10"))
        self.assertEqual(t["spoiled_kg"], D("5"))
        self.assertEqual(t["remaining_kg"], D("135"))
        self.assertEqual(t["revenue"], D("200000"))
        self.assertEqual(t["cogs"], D("100000"))
        self.assertEqual(t["absorbed_cost"], D("1200000"))
        self.assertEqual(t["unabsorbed_cost"], D("0"))

    def test_forward_trace_excludes_voided_sales(self):
        pos.void_order(self.order, self.owner)
        trace = services.forward_trace(self.cycle)
        self.assertEqual(trace.totals["sold_kg"], D("0"))
        lot = next(lt for lt in trace.batches[0].lots if lt.lot.pk == self.lot_high.pk)
        self.assertEqual(lot.voided_kg, D("10"))

    def test_closed_cycle_unabsorbed(self):
        trace = services.forward_trace(self.closed_cycle)
        self.assertEqual(trace.totals["unabsorbed_cost"], D("75000"))

    def test_forward_page_and_csv(self):
        self.login(ROLE_MANAGER)
        url = reverse("reports:trace_cycle", args=[self.cycle.pk])
        page = self.client.get(url)
        self.assertContains(page, "Batch #%d" % self.batch.pk)
        self.assertContains(page, self.order.order_number)
        self.assertContains(page, "Cetak / Simpan PDF")
        response = self.client.get(url, {"format": "csv"})
        self.assertEqual(response["Content-Type"], "text/csv; charset=utf-8")
        self.assertIn("attachment", response["Content-Disposition"])
        rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
        self.assertEqual(rows[0], services.FORWARD_CSV_HEADER)
        sections = [r[0] for r in rows[1:]]
        for section in ("SIKLUS", "KEGIATAN", "BIAYA_TERSERAP", "PANEN", "LOT", "SUSUT", "PENJUALAN"):
            self.assertIn(section, sections)
        sale = next(r for r in rows if r[0] == "PENJUALAN")
        self.assertEqual(sale[2], self.order.order_number)
        self.assertEqual(sale[4], "-10.000")

    def test_backward_page_and_csv(self):
        self.login(ROLE_OWNER)
        url = reverse("reports:trace_order", args=[self.order.order_number])
        page = self.client.get(url)
        self.assertContains(page, "Greenhouse A")
        self.assertContains(page, f"#{self.lot_high.pk}")
        rows = list(csv.reader(io.StringIO(self.client.get(url, {"format": "csv"}).content.decode("utf-8-sig"))))
        self.assertEqual(rows[0], services.BACKWARD_CSV_HEADER)
        self.assertEqual(len(rows), 2)
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual(row["Lot"], f"LOT-{self.lot_high.pk}")
        self.assertEqual(row["Batch panen"], f"HB-{self.batch.pk}")
        self.assertEqual(row["Elemen"], "Greenhouse A")
        self.assertEqual(row["Varietas"], "Melon Golden")

    def test_index_order_search(self):
        self.login(ROLE_OWNER)
        response = self.client.get(reverse("reports:trace_index"), {"order": self.order.order_number.lower()})
        self.assertRedirects(response, reverse("reports:trace_order", args=[self.order.order_number]))
        response = self.client.get(reverse("reports:trace_index"), {"order": "GT-19990101-0001"})
        self.assertContains(response, "tidak ditemukan")

    def test_unknown_objects_404(self):
        self.login(ROLE_OWNER)
        self.assertEqual(self.client.get(reverse("reports:trace_order", args=["GT-X"])).status_code, 404)
        self.assertEqual(self.client.get(reverse("reports:trace_cycle", args=[999999])).status_code, 404)


# --- Analytics (D28) ------------------------------------------------------------------------


class AnalyticsTests(SiteFixtures):
    def metrics(self):
        today = timezone.localdate()
        return services.dashboard_metrics(today - timedelta(days=29), today)

    def test_kpis(self):
        k = self.metrics()["kpis"]
        self.assertEqual(k["revenue"], D("200000"))
        self.assertEqual(k["cogs"], D("100000"))
        self.assertEqual(k["gross_margin"], D("100000"))
        self.assertEqual(k["gross_margin_pct"], D("50"))
        self.assertEqual(k["spoilage_loss"], D("20000"))
        self.assertEqual(k["unabsorbed_cost"], D("75000"))
        self.assertEqual(k["overhead"], D("300000"))
        self.assertEqual(k["net_profit"], D("-295000"))
        self.assertEqual(k["input_purchases"], D("1000000"))
        self.assertEqual(k["orders"], 1)

    def test_voided_orders_excluded(self):
        pos.void_order(self.order, self.owner)
        k = self.metrics()["kpis"]
        self.assertEqual(k["revenue"], D("0"))
        self.assertIsNone(k["gross_margin_pct"])

    def test_breakdowns(self):
        m = self.metrics()
        self.assertEqual(
            [(r["variety"], r["grade"], r["kg"], r["revenue"], r["cogs"], r["margin"]) for r in m["margin_by_product"]],
            [("Melon Golden", "HIGH", D("10"), D("200000"), D("100000"), D("100000"))],
        )
        self.assertEqual(m["top_products"][0]["revenue"], D("200000"))
        self.assertEqual(m["yield_by_type"], [{"type": "Greenhouse", "kg": D("150"), "area_m2": D("500"), "kg_per_m2": D("0.300")}])
        by_cat = {r["category"]: r["amount"] for r in m["expenses_by_category"]}
        self.assertEqual(by_cat, {"Tenaga Kerja": D("1225000"), "Utilitas": D("300000"), "Pupuk": D("50000")})
        self.assertEqual(len(m["daily"]), 30)
        self.assertEqual(sum(d["revenue"] for d in m["daily"]), D("200000"))

    def test_out_of_range_is_empty(self):
        m = services.dashboard_metrics(date(2020, 1, 1), date(2020, 1, 31))
        self.assertEqual(m["kpis"]["revenue"], D("0"))
        self.assertEqual(m["margin_by_product"], [])
        self.assertEqual(m["yield_by_type"], [])

    def test_dashboard_page(self):
        self.login(ROLE_MANAGER)
        response = self.client.get("/")
        self.assertContains(response, "Rp 200.000")
        self.assertContains(response, "-Rp 295.000")
        self.assertContains(response, "chart.umd.min.js")
        self.assertIn("margin", response.context["charts"])
        today = timezone.localdate()
        response = self.client.get("/", {"start": "2020-01-01", "end": "2020-01-31"})
        self.assertEqual(response.context["m"]["start"], date(2020, 1, 1))
        response = self.client.get("/", {"start": today.isoformat(), "end": "2020-01-01"})
        self.assertContains(response, "Tanggal awal harus sebelum tanggal akhir")
        self.assertEqual(response.context["m"]["end"], today)


class FilterTests(TestCase):
    def render(self, tpl, **ctx):
        return Template("{% load gotani %}" + tpl).render(Context(ctx))

    def test_rupiah(self):
        self.assertEqual(self.render("{{ v|rupiah }}", v=D("15000.00")), "Rp 15.000")
        self.assertEqual(self.render("{{ v|rupiah }}", v=D("1234567.50")), "Rp 1.234.568")
        self.assertEqual(self.render("{{ v|rupiah }}", v=D("-2500.4")), "-Rp 2.500")
        self.assertEqual(self.render("{{ v|rupiah }}", v=None), "-")

    def test_kg_and_pct(self):
        self.assertEqual(self.render("{{ v|kg }}", v=D("1500.250")), "1.500,25")
        self.assertEqual(self.render("{{ v|kg }}", v=D("90.000")), "90")
        self.assertEqual(self.render("{{ v|pct }}", v=D("33.333")), "33,3%")
        self.assertEqual(self.render("{{ v|pct }}", v=None), "-")
