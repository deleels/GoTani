"""Phase 3 tests: lot stock choke point, spoilage, mass-balance invariants."""

from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from farm import services as farm
from farm.models import (
    CropCycle,
    ExpenseCategory,
    FarmElement,
    HarvestBatch,
    InputItem,
    Unit,
    Variety,
)
from inventory import services
from inventory.models import Grade, Product, SpoilageRecord, StockLedger, StockLot

D = Decimal
MT = StockLedger.MovementType
Reason = SpoilageRecord.Reason


class InventoryFixtures(TestCase):
    """A melon cycle harvested once: HIGH 100 kg @ 10.000, LOW 50 kg @ 4.000 (RSV)."""

    def setUp(self):
        self.user = get_user_model().objects.create_superuser("boss", password="x")
        self.variety = Variety.objects.create(name="Melon Golden", crop="Melon")
        self.element = FarmElement.objects.create(
            name="GH-1", element_type=FarmElement.ElementType.GREENHOUSE, area_m2=D("100")
        )
        self.cycle = CropCycle.objects.create(
            element=self.element,
            variety=self.variety,
            planting_start_date=date(2026, 1, 1),
            stage=CropCycle.Stage.HARVESTING,
        )
        self.high = Product.objects.create(variety=self.variety, grade=Grade.HIGH, selling_price_idr=20000)
        self.low = Product.objects.create(variety=self.variety, grade=Grade.LOW, selling_price_idr=8000)
        expense = farm.create_expense(
            ExpenseCategory.objects.get(code="TENAGA_KERJA"),
            "ELEMENT",
            1200000,
            element=self.element,
            cycle=self.cycle,
        )
        farm.approve_expense(expense, self.user)
        self.batch = farm.record_harvest(
            self.cycle, 100, 50, harvested_at=timezone.now() - timedelta(days=3)
        )
        self.lot_high = self.batch.lots.get(product=self.high)
        self.lot_low = self.batch.lots.get(product=self.low)

    def ledger_sum(self, lot):
        return lot.ledger.aggregate(s=Sum("qty_kg"))["s"]

    def assertBalanced(self):
        self.assertEqual(services.check_mass_balance(), [])


# --- Harvest wiring (rule 6, 13) -----------------------------------------------


class HarvestLotTests(InventoryFixtures):
    def test_harvest_creates_lots_through_ledger(self):
        for lot, kg, cogs in ((self.lot_high, D("100"), D("10000")), (self.lot_low, D("50"), D("4000"))):
            self.assertEqual(lot.qty_initial_kg, kg)
            self.assertEqual(lot.qty_remaining_kg, kg)
            self.assertEqual(lot.unit_cogs_idr, cogs)
            self.assertEqual(
                list(lot.ledger.values_list("movement_type", "qty_kg", "reference")),
                [(MT.HARVEST_IN, kg, f"HB-{self.batch.pk}")],
            )
            self.assertEqual(lot.ledger.get().timestamp, self.batch.harvested_at)
        self.assertBalanced()

    def test_derived_product_stock(self):
        self.assertEqual(self.high.stock_kg, D("100"))
        farm.record_harvest(self.cycle, 30)
        self.assertEqual(self.high.stock_kg, D("130"))


# --- move_lot_stock (rule 13, D36) ---------------------------------------------


class MoveLotStockTests(InventoryFixtures):
    def test_sale_and_void_round_trip(self):
        lot = services.move_lot_stock(self.lot_high.pk, D("-12.345"), MT.POS_SALE, "GT-1")
        self.assertEqual(lot.qty_remaining_kg, D("87.655"))
        lot = services.move_lot_stock(self.lot_high.pk, D("12.345"), MT.POS_VOID, "GT-1")
        self.assertEqual(lot.qty_remaining_kg, D("100"))
        self.assertEqual(self.ledger_sum(lot), lot.qty_remaining_kg)
        self.assertEqual(
            list(lot.ledger.values_list("movement_type", flat=True)),
            [MT.HARVEST_IN, MT.POS_SALE, MT.POS_VOID],
        )
        self.assertBalanced()

    def test_can_sell_exactly_to_zero(self):
        lot = services.move_lot_stock(self.lot_low.pk, D("-50"), MT.POS_SALE)
        self.assertEqual(lot.qty_remaining_kg, D("0"))
        self.assertEqual(services.open_lots(self.low).count(), 0)

    def test_rejects_oversell_without_side_effects(self):
        with self.assertRaises(ValidationError):
            services.move_lot_stock(self.lot_low.pk, D("-50.001"), MT.POS_SALE)
        self.lot_low.refresh_from_db()
        self.assertEqual(self.lot_low.qty_remaining_kg, D("50"))
        self.assertEqual(self.lot_low.ledger.count(), 1)

    def test_rejects_exceeding_initial(self):
        with self.assertRaises(ValidationError):
            services.move_lot_stock(self.lot_low.pk, D("0.001"), MT.POS_VOID)

    def test_rejects_wrong_sign_zero_and_unknown_type(self):
        bad = [
            (D("1"), MT.POS_SALE),
            (D("1"), MT.SPOILAGE),
            (D("-1"), MT.POS_VOID),
            (D("-1"), MT.HARVEST_IN),
            (D("0"), MT.POS_SALE),
            (D("0.0001"), MT.POS_VOID),  # rounds to zero at kg precision
            (D("-1"), "THEFT"),
        ]
        for qty, mtype in bad:
            with self.assertRaises(ValidationError, msg=f"{qty} {mtype}"):
                services.move_lot_stock(self.lot_high.pk, qty, mtype)
        self.assertEqual(self.lot_high.ledger.count(), 1)

    def test_ledger_rows_are_append_only(self):
        row = self.lot_high.ledger.get()
        row.qty_kg = D("1")
        with self.assertRaises(ValueError):
            row.save()
        with self.assertRaises(ValueError):
            row.delete()


# --- Spoilage (D14, D15) ---------------------------------------------------------


class SpoilageTests(InventoryFixtures):
    def test_spoilage_targets_lot_and_costs_at_lot_cogs(self):
        rec = services.record_spoilage(self.lot_high, D("2.5"), Reason.ROT, "busuk", user=self.user)
        self.assertEqual(rec.loss_idr, D("25000.00"))
        self.lot_high.refresh_from_db()
        self.assertEqual(self.lot_high.qty_remaining_kg, D("97.5"))
        self.assertEqual(self.lot_high.unit_cogs_idr, D("10000.00"))  # unchanged
        self.lot_low.refresh_from_db()
        self.assertEqual(self.lot_low.qty_remaining_kg, D("50"))  # other lot untouched
        led = self.lot_high.ledger.get(movement_type=MT.SPOILAGE)
        self.assertEqual((led.qty_kg, led.reference), (D("-2.5"), f"SPL-{rec.pk}"))
        self.assertEqual(led.timestamp, rec.recorded_at)
        self.assertBalanced()

    def test_loss_rounds_half_up_to_cents(self):
        rec = services.record_spoilage(self.lot_low, D("0.001"), Reason.SHRINKAGE)
        self.assertEqual(rec.loss_idr, D("4.00"))  # 4000 × 0.001
        # 0.1 kg × Rp0.05 = 0.005 → rounds half-up to 0.01
        lot = StockLot.objects.create(
            product=self.low,
            harvest_batch=farm.record_harvest(self.cycle, 1),
            qty_initial_kg=D("1"),
            qty_remaining_kg=D("0"),
            unit_cogs_idr=D("0.05"),
            harvested_at=timezone.now(),
        )
        services.move_lot_stock(lot.pk, D("1"), MT.HARVEST_IN)
        self.assertEqual(services.record_spoilage(lot, D("0.1"), Reason.OTHER).loss_idr, D("0.01"))

    def test_spoilage_cannot_exceed_lot_stock(self):
        with self.assertRaises(ValidationError):
            services.record_spoilage(self.lot_low, D("50.5"), Reason.PEST)
        self.assertFalse(SpoilageRecord.objects.exists())  # rolled back
        self.lot_low.refresh_from_db()
        self.assertEqual(self.lot_low.qty_remaining_kg, D("50"))
        self.assertBalanced()

    def test_spoilage_validation(self):
        for qty, reason in ((0, Reason.ROT), (-1, Reason.ROT), (1, "STOLEN")):
            with self.assertRaises(ValidationError):
                services.record_spoilage(self.lot_high, qty, reason)
        self.assertFalse(SpoilageRecord.objects.exists())

    def test_oldest_open_lot_is_preselected(self):
        newer = farm.record_harvest(self.cycle, 10).lots.get(product=self.high)
        self.assertEqual(services.oldest_open_lot(self.high), self.lot_high)
        services.move_lot_stock(self.lot_high.pk, D("-100"), MT.POS_SALE)
        self.assertEqual(services.oldest_open_lot(self.high), newer)
        services.move_lot_stock(newer.pk, D("-10"), MT.POS_SALE)
        self.assertIsNone(services.oldest_open_lot(self.high))

    def test_spoiled_lot_blocks_batch_deletion(self):
        services.record_spoilage(self.lot_low, 1, Reason.PHYSICAL_DAMAGE)
        with self.assertRaises(ValidationError):
            farm.delete_harvest_batch(self.batch)


class ProductPriceTests(InventoryFixtures):
    def test_set_and_clear_price(self):
        p = Product.objects.create(variety=Variety.objects.create(name="Cabai Rawit", crop="Cabai"), grade=Grade.HIGH)
        self.assertEqual(services.set_product_price(p, "45000.4").selling_price_idr, D("45000.40"))
        self.assertIsNone(services.set_product_price(p, None).selling_price_idr)
        with self.assertRaises(ValidationError):
            services.set_product_price(p, 0)


# --- Mass balance (rule 15, D20) -------------------------------------------------


class MassBalanceTests(InventoryFixtures):
    def setUp(self):
        super().setUp()
        self.npk = InputItem.objects.create(
            name="NPK", item_type=InputItem.ItemType.FERTILIZER, unit=Unit.KG
        )
        farm.record_input_purchase(self.npk, 20, 5000)
        farm.log_activity(self.cycle, "FERTILIZER", 3, Unit.KG, self.npk)
        services.record_spoilage(self.lot_high, 4, Reason.ROT)
        services.move_lot_stock(self.lot_low.pk, D("-10"), MT.POS_SALE, "GT-x")

    def test_full_flow_balances(self):
        self.assertBalanced()

    def test_detects_lot_remaining_tampering(self):
        StockLot.objects.filter(pk=self.lot_high.pk).update(qty_remaining_kg=D("95"))
        errors = services.check_mass_balance()
        self.assertEqual(len(errors), 1)
        self.assertIn(f"Lot #{self.lot_high.pk}", errors[0])
        self.assertIn("Σ ledger", errors[0])

    def test_detects_initial_mismatch_with_batch(self):
        HarvestBatch.objects.filter(pk=self.batch.pk).update(low_quality_kg=D("49"))
        errors = services.check_mass_balance()
        self.assertTrue(any("jumlah awal" in e for e in errors))

    def test_detects_missing_lot_for_graded_kg(self):
        b = farm.record_harvest(self.cycle, 5)
        HarvestBatch.objects.filter(pk=b.pk).update(low_quality_kg=D("2"))
        errors = services.check_mass_balance()
        self.assertTrue(any(f"Batch #{b.pk}" in e and "LOW" in e for e in errors))

    def test_detects_product_variety_mismatch(self):
        other = Variety.objects.create(name="Cabai Rawit", crop="Cabai")
        Product.objects.filter(pk=self.low.pk).update(variety=other)
        errors = services.check_mass_balance()
        self.assertTrue(any("varietas produk" in e for e in errors))

    def test_detects_spoilage_record_without_ledger(self):
        SpoilageRecord.objects.create(lot=self.lot_low, qty_kg=1, reason=Reason.ROT, loss_idr=4000)
        errors = services.check_mass_balance()
        self.assertTrue(any("catatan susut" in e for e in errors))

    def test_detects_input_stock_tampering(self):
        InputItem.objects.filter(pk=self.npk.pk).update(stock_qty=D("18"))
        errors = services.check_mass_balance()
        self.assertEqual(len(errors), 1)
        self.assertIn("Input NPK", errors[0])

    def test_input_without_ledger_must_be_zero(self):
        InputItem.objects.create(name="Kosong", item_type="SEED", unit=Unit.G)
        self.assertBalanced()

    def test_command_success(self):
        out = StringIO()
        call_command("check_mass_balance", stdout=out)
        self.assertIn("Mass balance OK", out.getvalue())
        self.assertIn("2 lot", out.getvalue())

    def test_command_fails_with_violations(self):
        StockLot.objects.filter(pk=self.lot_low.pk).update(qty_remaining_kg=D("1"))
        err = StringIO()
        with self.assertRaises(CommandError):
            call_command("check_mass_balance", stdout=StringIO(), stderr=err)
        self.assertIn(f"Lot #{self.lot_low.pk}", err.getvalue())

    def test_seed_demo_passes_mass_balance(self):
        call_command("seed_demo", stdout=StringIO())
        call_command("check_mass_balance", stdout=StringIO())


class SpoilageReversalTests(TestCase):
    """D86: same-day spoilage reversal by OWNER/MANAGER as a new ledger movement."""

    def setUp(self):
        from datetime import date

        from farm import services as farm_services
        from farm.models import CropCycle, FarmElement, Variety
        from pos.tests import make_user
        from farm.bootstrap import ROLE_MANAGER, ROLE_WORKER

        self.manager, self.worker = make_user("m", ROLE_MANAGER), make_user("w", ROLE_WORKER)
        cycle = CropCycle.objects.create(element=FarmElement.objects.create(name="E", element_type="OPEN_FIELD", area_m2=1),
                                         variety=Variety.objects.create(name="V", crop="C"),
                                         planting_start_date=date(2026, 8, 1), stage=CropCycle.Stage.HARVESTING)
        self.lot = farm_services.record_harvest(cycle, 10, 0).lots.get()

    def test_reverse_restores_lot_and_keeps_balance(self):
        from django.core.exceptions import PermissionDenied, ValidationError

        from core.models import AuditLog
        from inventory.services import check_mass_balance, record_spoilage, reverse_spoilage

        rec = record_spoilage(self.lot, 4, "ROT")
        with self.assertRaises(PermissionDenied):
            reverse_spoilage(rec, self.worker, "salah")
        with self.assertRaises(ValidationError):
            reverse_spoilage(rec, self.manager, "")
        reverse_spoilage(rec, self.manager, "salah lot")
        self.lot.refresh_from_db()
        self.assertEqual(self.lot.qty_remaining_kg, Decimal("10"))
        self.assertEqual(self.lot.ledger.filter(movement_type="SPOILAGE_REVERSAL").count(), 1)
        self.assertEqual(check_mass_balance(), [])
        self.assertTrue(AuditLog.objects.filter(action="SPOILAGE_REVERSAL").exists())
        with self.assertRaises(ValidationError):
            reverse_spoilage(rec, self.manager, "lagi")

    def test_three_day_window_and_opname_guard(self):
        from datetime import timedelta

        from django.core.exceptions import ValidationError
        from django.utils import timezone

        from inventory.services import record_spoilage, reverse_spoilage

        rec = record_spoilage(self.lot, 1, "ROT", recorded_at=timezone.now() - timedelta(days=4))
        with self.assertRaises(ValidationError):
            reverse_spoilage(rec, self.manager, "telat")
        # D134: up to 3 days back is fine, unless an approved opname of that product came after it.
        ok = record_spoilage(self.lot, 1, "ROT", recorded_at=timezone.now() - timedelta(days=2))
        from inventory.models import StockCount

        StockCount.objects.create(product=self.lot.product, system_kg=1, counted_kg=1, status="APPROVED",
                                  counted_by=self.manager, decided_by=self.manager, decided_at=timezone.now())
        with self.assertRaisesMessage(ValidationError, "opname"):
            reverse_spoilage(ok, self.manager, "salah lot")
        StockCount.objects.all().delete()
        reverse_spoilage(ok, self.manager, "salah lot")

    def test_view_and_analytics_exclude_reversed(self):
        from django.urls import reverse

        from inventory.services import record_spoilage

        rec = record_spoilage(self.lot, 2, "ROT")
        self.client.force_login(self.manager)
        page = self.client.get(reverse("farm:spoilage")).content.decode()
        self.assertIn(reverse("farm:spoilage_reverse", args=[rec.pk]), page)
        self.client.post(reverse("farm:spoilage_reverse", args=[rec.pk]), {"reason": "salah"})
        rec.refresh_from_db()
        self.assertIsNotNone(rec.reversed_at)
        from django.utils import timezone

        from reports.services import dashboard_metrics

        today = timezone.localdate()
        self.assertEqual(dashboard_metrics(today, today)["kpis"]["spoilage_loss"], Decimal("0"))


class OpnameAndLowStockTests(TestCase):
    """D98–D99: produce low-stock alerts; opname writes off only the shortfall, FIFO, after approval."""

    def setUp(self):
        from datetime import date, timedelta

        from django.utils import timezone

        from farm import services as farm_services
        from farm.bootstrap import ROLE_MANAGER, ROLE_WORKER
        from farm.models import CropCycle, FarmElement, Variety
        from pos.tests import make_user

        self.manager, self.worker = make_user("m", ROLE_MANAGER), make_user("w", ROLE_WORKER)
        cycle = CropCycle.objects.create(element=FarmElement.objects.create(name="E", element_type="OPEN_FIELD", area_m2=1),
                                         variety=Variety.objects.create(name="V", crop="C"),
                                         planting_start_date=date(2026, 8, 1), stage=CropCycle.Stage.HARVESTING)
        now = timezone.now()
        self.old = farm_services.record_harvest(cycle, 5, 0, harvested_at=now - timedelta(days=3)).lots.get()
        self.new = farm_services.record_harvest(cycle, 10, 0, harvested_at=now).lots.get()
        self.product = self.old.product

    def test_low_stock_alert(self):
        from core.notifications import build_notifications
        from farm.access import UserRoles
        from inventory.services import low_stock_products, set_min_alert

        self.assertEqual(low_stock_products(), [])
        set_min_alert(self.product, Decimal("15"))
        self.assertEqual(low_stock_products()[0][1], Decimal("15"))
        kinds = [n["kind"] for n in build_notifications(UserRoles(self.worker))]
        self.assertIn("produce_low", kinds)
        set_min_alert(self.product, None)
        self.assertEqual(low_stock_products(), [])

    def test_shortfall_written_off_fifo_after_approval(self):
        from django.core.exceptions import PermissionDenied, ValidationError

        from inventory.services import approve_count, check_mass_balance, record_count

        count = record_count(self.product, Decimal("8"), self.worker)
        self.assertEqual((count.system_kg, count.shortfall_kg), (Decimal("15"), Decimal("7")))
        self.assertEqual(self.product.stock_kg, Decimal("15"))  # nothing moves before approval
        with self.assertRaises(ValidationError):  # one pending count per product
            record_count(self.product, Decimal("9"), self.worker)
        with self.assertRaises(PermissionDenied):
            approve_count(count, self.worker)
        count = approve_count(count, self.manager)
        self.old.refresh_from_db()
        self.new.refresh_from_db()
        self.assertEqual((self.old.qty_remaining_kg, self.new.qty_remaining_kg), (Decimal("0"), Decimal("8")))
        self.assertEqual(count.written_off_kg, Decimal("7"))
        self.assertEqual(SpoilageRecord.objects.filter(notes=f"Opname #{count.pk}", reason="SHRINKAGE").count(), 2)
        self.assertEqual(check_mass_balance(), [])

    def test_surplus_not_booked_and_reject(self):
        from inventory.services import approve_count, record_count, reject_count

        surplus = approve_count(record_count(self.product, Decimal("20"), self.worker), self.manager)
        self.assertEqual((surplus.written_off_kg, self.product.stock_kg), (Decimal("0"), Decimal("15")))
        rejected = reject_count(record_count(self.product, Decimal("1"), self.worker), self.manager, "salah hitung")
        self.assertEqual((rejected.status, self.product.stock_kg), ("REJECTED", Decimal("15")))

    def test_opname_page(self):
        from django.urls import reverse

        from inventory.models import StockCount

        self.client.force_login(self.worker)
        self.client.post(reverse("farm:opname"), {"product": self.product.pk, "counted_kg": "14,5"})
        count = StockCount.objects.get()
        self.assertEqual(count.counted_kg, Decimal("14.5"))
        self.client.post(reverse("farm:opname"), {"action": "approve", "count": count.pk})
        count.refresh_from_db()
        self.assertEqual(count.status, "PENDING")  # workers cannot approve
        self.client.force_login(self.manager)
        page = self.client.get(reverse("farm:opname")).content.decode()
        self.assertIn("Setujui", page)
        self.client.post(reverse("farm:opname"), {"action": "approve", "count": count.pk})
        count.refresh_from_db()
        self.assertEqual(count.status, "APPROVED")
