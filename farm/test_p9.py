"""Phase 9: cycle estimates, pre-harvest checklist, activity extras, detail pages, Laporan Kerja, notifications."""

from datetime import date, timedelta
from decimal import Decimal

from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from core.notifications import build_notifications, notifications_payload
from farm import services
from farm.access import UserRoles
from farm.bootstrap import ROLE_CASHIER, ROLE_MANAGER, ROLE_OWNER, ROLE_WORKER
from farm.models import ActivityLog, CropCycle, FarmElement, InputItem, PreHarvestChecklistItem, Unit, Variety
from pos.tests import make_user

D = Decimal
GIF = (b"GIF89a\x01\x00\x01\x00\x80\x00\x00\x00\x00\x00\xff\xff\xff!\xf9\x04\x01\x00\x00\x00\x00,"
       b"\x00\x00\x00\x00\x01\x00\x01\x00\x00\x02\x02D\x01\x00;")


class P9Fixtures(TestCase):
    def setUp(self):
        self.owner = make_user("owner", ROLE_OWNER)
        self.manager = make_user("manager", ROLE_MANAGER)
        self.worker = make_user("worker", ROLE_WORKER)
        self.cashier = make_user("cashier", ROLE_CASHIER)
        self.variety = Variety.objects.create(name="Melon Golden", crop="Melon", days_to_harvest=75)
        self.element = FarmElement.objects.create(name="GH-1", element_type="GREENHOUSE", area_m2=D("200"))
        self.cycle = services.start_cycle(self.element, self.variety, date(2026, 9, 1), planted_qty=400,
                                          expected_yield_kg=D("500"))
        self.npk = InputItem.objects.create(name="NPK", item_type="FERTILIZER", unit=Unit.KG, min_alert_qty=D("20"))
        services.record_input_purchase(self.npk, 50, 10000)
        self.items = list(PreHarvestChecklistItem.objects.filter(is_active=True))


class EstimateTests(P9Fixtures):
    def test_expected_harvest_defaults_from_variety(self):
        self.assertEqual(self.cycle.expected_harvest_date, date(2026, 9, 1) + timedelta(days=75))
        other = FarmElement.objects.create(name="GH-2", element_type="GREENHOUSE", area_m2=D("100"))
        c = services.start_cycle(other, self.variety, date(2026, 9, 1), expected_harvest_date=date(2026, 12, 1))
        self.assertEqual(c.expected_harvest_date, date(2026, 12, 1))  # explicit wins

    def test_estimate_validation_and_update(self):
        with self.assertRaises(ValidationError):
            services.update_cycle_estimates(self.cycle, planted_qty=0)
        with self.assertRaises(ValidationError):
            services.update_cycle_estimates(self.cycle, expected_harvest_date=date(2026, 1, 1))
        with self.assertRaises(ValidationError):
            services.update_cycle_estimates(self.cycle, expected_yield_kg=-1)
        services.update_cycle_estimates(self.cycle, planted_qty=500, expected_harvest_date=date(2026, 11, 30),
                                        expected_yield_kg=D("600"))
        self.cycle.refresh_from_db()
        self.assertEqual((self.cycle.planted_qty, self.cycle.expected_yield_kg), (500, D("600")))

    def test_days_growing_and_overdue(self):
        self.assertEqual(self.cycle.days_growing(date(2026, 9, 11)), 10)
        self.assertEqual(self.cycle.days_growing(date(2026, 8, 1)), 0)  # never negative
        self.assertTrue(self.cycle.is_harvest_overdue(date(2026, 12, 1)))
        self.assertFalse(self.cycle.is_harvest_overdue(date(2026, 10, 1)))
        services.advance_cycle_stage(self.cycle, CropCycle.Stage.HARVESTING, allow_skip=True)
        self.cycle.refresh_from_db()
        self.assertFalse(self.cycle.is_harvest_overdue(date(2026, 12, 1)))

    def test_closed_cycle_days_stop_at_close(self):
        services.close_cycle(self.cycle)
        self.cycle.refresh_from_db()
        CropCycle.objects.filter(pk=self.cycle.pk).update(closed_at=timezone.make_aware(timezone.datetime(2026, 9, 21, 12)))
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.days_growing(date(2027, 1, 1)), 20)

    def test_yield_summary(self):
        services.advance_cycle_stage(self.cycle, CropCycle.Stage.HARVESTING, allow_skip=True)
        services.record_harvest(self.cycle, 200, 50, 5)
        s = services.cycle_yield_summary(self.cycle)
        self.assertEqual((s["actual_kg"], s["pct"], s["per_plant_kg"]), (D("250"), D("50.0"), D("0.625")))


class ChecklistTests(P9Fixtures):
    def test_default_items_seeded_by_migration(self):
        self.assertEqual(len(self.items), 5)

    def test_pct_snapshot_and_latest_counts(self):
        c1 = services.record_preharvest_check(self.cycle, self.items[:2], user=self.worker)
        self.assertEqual(c1.completed_pct, D("40.00"))
        self.assertIn("40", services.harvest_readiness_warning(self.cycle, CropCycle.Stage.HARVESTING))
        services.record_preharvest_check(self.cycle, [i.pk for i in self.items])
        self.assertIsNone(services.harvest_readiness_warning(self.cycle, CropCycle.Stage.HARVESTING))
        # Snapshot: deactivating an item later does not rewrite history.
        self.items[0].is_active = False
        self.items[0].save()
        c1.refresh_from_db()
        self.assertEqual(c1.completed_pct, D("40.00"))

    def test_warning_without_check_but_never_blocks(self):
        self.assertIn("belum ada", services.harvest_readiness_warning(self.cycle, CropCycle.Stage.HARVESTING))
        self.assertIsNone(services.harvest_readiness_warning(self.cycle, CropCycle.Stage.FLOWERING))
        self.client.force_login(self.worker)
        r = self.client.post(reverse("farm:cycle_stage", args=[self.cycle.pk]), {"stage": "HARVESTING", "skip": "on"},
                             follow=True)
        self.assertContains(r, "Peringatan")
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.stage, CropCycle.Stage.HARVESTING)

    def test_invalid_checks(self):
        with self.assertRaises(ValidationError):
            services.record_preharvest_check(self.cycle, [999999])
        PreHarvestChecklistItem.objects.update(is_active=False)
        with self.assertRaises(ValidationError):
            services.record_preharvest_check(self.cycle, [])
        services.close_cycle(self.cycle)
        with self.assertRaises(ValidationError):
            services.record_preharvest_check(self.cycle, [])


class ActivityExtrasTests(P9Fixtures):
    def test_duration_only_for_irrigation(self):
        a = services.log_activity(self.cycle, "IRRIGATION", quantity=500, unit="L", duration_minutes=45)
        self.assertEqual(a.duration_minutes, 45)
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, "SCOUTING", duration_minutes=10)
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, "IRRIGATION", duration_minutes=0)

    def test_photo_only_for_scouting(self):
        photo = SimpleUploadedFile("leaf.gif", GIF, content_type="image/gif")
        a = services.log_activity(self.cycle, "SCOUTING", notes="Kutu", photo=photo)
        self.assertTrue(a.photo.name.startswith("scouting/"))
        a.photo.delete(save=False)
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, "IRRIGATION", photo=SimpleUploadedFile("x.gif", GIF, content_type="image/gif"))

    def test_usage_history_links_activities_and_running_balance(self):
        act = services.log_activity(self.cycle, "FERTILIZER", quantity=5, unit="KG", input_item=self.npk)
        rows = services.input_usage_history(self.npk)
        self.assertEqual([r.movement_type for r in rows], ["PURCHASE", "USAGE"])
        self.assertEqual([r.balance for r in rows], [D("50"), D("45")])
        self.assertEqual(rows[1].activity, act)
        self.assertIsNone(rows[0].activity)


class P9ViewTests(P9Fixtures):
    def test_laporan_kerja_tabs_and_per_type_forms(self):
        self.client.force_login(self.worker)
        url = reverse("farm:activities")
        for tab in ("pemupukan", "pestisida", "irigasi", "scouting", "penanaman", "prapanen", "panen", "susut", "bogus"):
            r = self.client.get(url, {"tab": tab, "new": "1"})
            self.assertEqual(r.status_code, 200, tab)
            self.assertNotContains(r, "Rp ")
        r = self.client.get(url, {"tab": "irigasi"})
        self.assertIn("duration_minutes", r.context["form"].fields)
        self.assertNotIn("input_item", r.context["form"].fields)
        r = self.client.post(url + "?tab=irigasi", {"tab": "irigasi", "cycle": self.cycle.pk, "quantity": "300",
                                                    "unit": "L", "duration_minutes": "30"})
        self.assertRedirects(r, url + "?tab=irigasi")
        self.assertEqual(ActivityLog.objects.get().duration_minutes, 30)
        photo = SimpleUploadedFile("leaf.gif", GIF, content_type="image/gif")
        r = self.client.post(url + "?tab=scouting", {"tab": "scouting", "cycle": self.cycle.pk, "notes": "Aman", "photo": photo})
        self.assertRedirects(r, url + "?tab=scouting")
        scouting = ActivityLog.objects.get(activity_type="SCOUTING")
        self.assertTrue(scouting.photo)
        self.assertContains(self.client.get(url, {"tab": "scouting"}), scouting.photo.url)
        scouting.photo.delete(save=False)

    def test_preharvest_view(self):
        self.client.force_login(self.worker)
        r = self.client.post(reverse("farm:preharvest_new"), {"cycle": self.cycle.pk, "items": [i.pk for i in self.items[:3]]})
        self.assertRedirects(r, reverse("farm:activities") + "?tab=prapanen")
        self.assertEqual(services.latest_preharvest_check(self.cycle).completed_pct, D("60.00"))
        self.assertContains(self.client.get(reverse("farm:activities"), {"tab": "prapanen"}), "60%")
        self.assertEqual(self.client.get(reverse("farm:preharvest_new"), {"cycle": self.cycle.pk}).status_code, 200)

    def test_detail_pages_and_money_hiding(self):
        services.log_activity(self.cycle, "FERTILIZER", quantity=5, unit="KG", input_item=self.npk)
        services.advance_cycle_stage(self.cycle, CropCycle.Stage.HARVESTING, allow_skip=True)
        services.record_harvest(self.cycle, 100, 20, 1)
        pages = [reverse("farm:cycle_detail", args=[self.cycle.pk]), reverse("farm:element_detail", args=[self.element.pk]),
                 reverse("farm:input_detail", args=[self.npk.pk])]
        self.client.force_login(self.worker)
        for url in pages:
            r = self.client.get(url)
            self.assertEqual(r.status_code, 200, url)
            self.assertNotContains(r, "Rp ")
        self.assertContains(self.client.get(pages[0]), "Panen #")  # timeline
        self.assertContains(self.client.get(pages[2]), "GH-1")  # where the fertilizer went
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(pages[0]), "Rp ")
        self.client.force_login(self.cashier)
        for url in pages:
            self.assertEqual(self.client.get(url).status_code, 403)

    def test_estimates_edit_restock_and_start_cycle_from_element(self):
        self.client.force_login(self.manager)
        url = reverse("farm:cycle_detail", args=[self.cycle.pk])
        r = self.client.post(url, {"planted_qty": "450", "expected_harvest_date": "2026-11-20", "expected_yield_kg": "700"})
        self.assertRedirects(r, url)
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.planted_qty, 450)
        r = self.client.post(reverse("farm:input_detail", args=[self.npk.pk]), {"item": self.npk.pk, "qty": "10", "cost_per_unit_idr": "12000"})
        self.assertRedirects(r, reverse("farm:input_detail", args=[self.npk.pk]))
        self.npk.refresh_from_db()
        self.assertEqual(self.npk.stock_qty, D("60"))
        self.client.force_login(self.worker)
        self.assertEqual(self.client.post(reverse("farm:input_detail", args=[self.npk.pk]),
                                          {"item": self.npk.pk, "qty": "1", "cost_per_unit_idr": "1"}).status_code, 403)
        el2 = FarmElement.objects.create(name="GH-2", element_type="GREENHOUSE", area_m2=D("100"))
        r = self.client.post(reverse("farm:element_detail", args=[el2.pk]),
                             {"element": el2.pk, "variety": self.variety.pk, "planting_start_date": "2026-11-01"})
        self.assertRedirects(r, reverse("farm:element_detail", args=[el2.pk]))
        self.assertEqual(el2.cycles.get().expected_harvest_date, date(2026, 11, 1) + timedelta(days=75))


class NotificationTests(P9Fixtures):
    def test_roles_see_their_notifications(self):
        from farm.models import Expense, ExpenseCategory
        from inventory.models import Grade, Product

        services.log_activity(self.cycle, "FERTILIZER", quantity=35, unit="KG", input_item=self.npk)  # 15 left ≤ 20
        services.create_expense(ExpenseCategory.objects.get(code="LAINNYA"), Expense.Scope.GENERAL, 1000)
        Product.objects.create(variety=self.variety, grade=Grade.LOW)
        late = date(2026, 9, 1) + timedelta(days=80)
        worker_kinds = {n["kind"] for n in build_notifications(UserRoles(self.worker), today=late)}
        manager_kinds = {n["kind"] for n in build_notifications(UserRoles(self.manager), today=late)}
        self.assertEqual(worker_kinds, {"low_stock", "harvest_overdue"})
        self.assertEqual(manager_kinds, {"low_stock", "harvest_overdue", "pending_expense", "unpriced"})
        self.assertEqual(build_notifications(UserRoles(self.cashier), today=late), [])

    def test_overdue_invoice_and_unread_tracking(self):
        from invoicing.services import create_buyer, issue_invoice, save_draft
        from inventory.models import Grade, Product

        services.advance_cycle_stage(self.cycle, CropCycle.Stage.HARVESTING, allow_skip=True)
        Product.objects.create(variety=self.variety, grade=Grade.HIGH, selling_price_idr=20000)
        Product.objects.create(variety=self.variety, grade=Grade.LOW, selling_price_idr=8000)
        services.record_harvest(self.cycle, 10, 5)
        buyer = create_buyer(self.manager, name="Resto")
        inv = issue_invoice(save_draft(self.manager, buyer, [(Product.objects.get(grade="HIGH"), 2, None)]),
                            self.manager, issue_date=timezone.localdate() - timedelta(days=10))
        kinds = {n["kind"] for n in build_notifications(UserRoles(self.manager))}
        self.assertIn("overdue_invoice", kinds)

        self.client.force_login(self.manager)
        data = self.client.get(reverse("core:notifications")).json()
        self.assertGreater(data["unread"], 0)
        self.assertTrue(any(inv.number in n["title"] for n in data["items"]))
        self.client.post(reverse("core:notifications"))
        self.assertEqual(self.client.get(reverse("core:notifications")).json()["unread"], 0)
        self.assertEqual(notifications_payload(self.manager, UserRoles(self.manager))["unread"], 0)

    def test_anonymous_gets_401(self):
        self.assertEqual(self.client.get(reverse("core:notifications")).status_code, 401)


class PageAnatomyTests(P9Fixtures):
    def test_expense_history_filters_and_kpis(self):
        from farm.models import Expense, ExpenseCategory

        cat = ExpenseCategory.objects.get(code="LAINNYA")
        ok = services.create_expense(cat, Expense.Scope.GENERAL, 1000, narration="Listrik kantor")
        services.approve_expense(ok, self.manager)
        bad = services.create_expense(cat, Expense.Scope.GENERAL, 2000, narration="Dobel input")
        services.reject_expense(bad, self.manager)
        self.client.force_login(self.manager)
        url = reverse("farm:expenses")
        self.assertEqual(list(self.client.get(url, {"status": "REJECTED"}).context["recent"]), [bad])
        self.assertEqual(list(self.client.get(url, {"q": "listrik"}).context["recent"]), [ok])
        self.assertContains(self.client.get(url), "Disetujui bulan ini")

    def test_orders_by_date_and_kpi_pages(self):
        self.client.force_login(self.cashier)
        url = reverse("pos:orders")
        r = self.client.get(url, {"date": "2026-01-05"})
        self.assertEqual(r.context["today"], date(2026, 1, 5))
        self.assertFalse(r.context["is_today"])
        self.assertTrue(self.client.get(url, {"date": "bogus"}).context["is_today"])
        self.assertTrue(self.client.get(url, {"date": "2999-01-01"}).context["is_today"])  # future clamps to today
        self.client.force_login(self.worker)
        for name in ("farm:harvests", "farm:inputs", "farm:spoilage", "farm:cycles"):
            r = self.client.get(reverse(name))
            self.assertContains(r, "text-2xl font-bold")
            self.assertNotContains(r, "Rp ")


class StageAdvanceUndoTests(TestCase):
    """D84: next stage only unless skipping is ticked; same-day undo of the last advance with a reason."""

    def setUp(self):
        from farm.bootstrap import ROLE_MANAGER, ROLE_WORKER

        self.manager = make_user("mgr", ROLE_MANAGER)
        self.worker = make_user("wrk", ROLE_WORKER)
        self.element = FarmElement.objects.create(name="GH", element_type="GREENHOUSE", area_m2=100)
        self.cycle = CropCycle.objects.create(element=self.element, variety=Variety.objects.create(name="V", crop="C"),
                                              planting_start_date=date(2026, 9, 1))

    def test_skip_requires_flag_and_is_logged(self):
        from core.models import AuditLog

        with self.assertRaises(ValidationError):
            services.advance_cycle_stage(self.cycle, CropCycle.Stage.VEGETATIVE)
        services.advance_cycle_stage(self.cycle, CropCycle.Stage.SPROUTING, actor=self.worker)
        entry = AuditLog.objects.get(action="STAGE_ADVANCE")
        self.assertEqual((entry.before["stage"], entry.after["stage"]), ("PLANTING", "SPROUTING"))

    def test_undo_same_day_with_reason(self):
        services.advance_cycle_stage(self.cycle, CropCycle.Stage.SPROUTING, actor=self.worker)
        with self.assertRaises(PermissionDenied):
            services.undo_last_advance(self.cycle, self.worker, "salah klik")
        with self.assertRaises(ValidationError):
            services.undo_last_advance(self.cycle, self.manager, " ")
        services.undo_last_advance(self.cycle, self.manager, "salah klik")
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.stage, CropCycle.Stage.PLANTING)
        with self.assertRaises(ValidationError):  # only the last advance, once
            services.undo_last_advance(self.cycle, self.manager, "lagi")

    def test_undo_blocked_after_new_record_or_next_day(self):
        services.advance_cycle_stage(self.cycle, CropCycle.Stage.SPROUTING, actor=self.worker)
        _, blocked = services.stage_undo_block(self.cycle, today=date.today() + timedelta(days=1))
        self.assertIn("hari yang sama", blocked)
        services.log_activity(self.cycle, ActivityLog.ActivityType.SCOUTING, quantity=0, unit="L")
        with self.assertRaises(ValidationError):
            services.undo_last_advance(self.cycle, self.manager, "salah")

    def test_undo_clearing_reopens_unless_new_cycle(self):
        services.close_cycle(self.cycle, actor=self.manager)
        services.undo_last_advance(self.cycle, self.manager, "belum selesai")
        self.cycle.refresh_from_db()
        self.assertTrue(self.cycle.is_active)
        self.assertIsNone(self.cycle.closed_at)
        services.close_cycle(self.cycle, actor=self.manager)
        CropCycle.objects.create(element=self.element, variety=self.cycle.variety, planting_start_date=date(2026, 11, 1))
        with self.assertRaises(ValidationError):
            services.undo_last_advance(self.cycle, self.manager, "belum selesai")

    def test_views(self):
        self.client.force_login(self.worker)
        self.client.post(reverse("farm:cycle_stage", args=[self.cycle.pk]), {"stage": "SPROUTING"})
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.stage, "SPROUTING")
        page = self.client.get(reverse("farm:cycle_detail", args=[self.cycle.pk])).content.decode()
        self.assertNotIn("Batalkan perpindahan terakhir", page)  # workers cannot undo
        self.assertEqual(self.client.post(reverse("farm:cycle_stage_undo", args=[self.cycle.pk]),
                                          {"reason": "x"}).status_code, 403)
        self.client.force_login(self.manager)
        self.assertContains(self.client.get(reverse("farm:cycle_detail", args=[self.cycle.pk])),
                            "Batalkan perpindahan terakhir")
        self.client.post(reverse("farm:cycle_stage_undo", args=[self.cycle.pk]), {"reason": "salah"})
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.stage, "PLANTING")


class HarvestPreviewTests(TestCase):
    """D85: harvest review numbers and soft warnings."""

    def setUp(self):
        self.cycle = CropCycle.objects.create(
            element=FarmElement.objects.create(name="L", element_type="OPEN_FIELD", area_m2=100),
            variety=Variety.objects.create(name="Cabai", crop="Cabai"), planting_start_date=date(2026, 8, 1),
            stage=CropCycle.Stage.HARVESTING, expected_yield_kg=Decimal("100"))

    def test_warnings_over_expected_and_batch_average(self):
        self.assertEqual(services.preview_harvest(self.cycle, 50, 10)["warnings"], [])
        services.record_harvest(self.cycle, 10, 0)
        p = services.preview_harvest(self.cycle, 150, 10)
        self.assertEqual(p["actual_after_kg"], Decimal("170"))
        self.assertEqual(len(p["warnings"]), 2)  # > 150% of expected, > 3x batch average

    def test_cost_per_kg_preview_matches_saved_batch(self):
        from farm.models import ExpenseCategory

        manager = make_user("m", ROLE_MANAGER)
        e = services.create_expense(ExpenseCategory.objects.get(code="TENAGA_KERJA"), "ELEMENT", 90000,
                                    element=self.cycle.element, cycle=self.cycle)
        services.approve_expense(e, manager)
        p = services.preview_harvest(self.cycle, 20, 10)
        batch = services.record_harvest(self.cycle, 20, 10)
        self.assertEqual((p["pool_idr"], p["unit_cogs_high"], p["unit_cogs_low"]),
                         (batch.absorbed_cost_idr, batch.unit_cogs_high_idr, batch.unit_cogs_low_idr))
