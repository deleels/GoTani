"""Farm tests.

Phase 1: DB constraints, reference data, seed_demo idempotency.
Phase 2: farm services — stages, inputs, expenses, cost pool, grade split, harvests.
"""

from datetime import date, timedelta
from decimal import Decimal
from io import StringIO

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.test import TestCase
from django.utils import timezone

from farm import services
from farm.bootstrap import (
    EXPENSE_CATEGORIES,
    ROLE_CASHIER,
    ROLE_MANAGER,
    ROLE_OWNER,
    ROLE_WORKER,
    ROLES,
)
from farm.models import (
    ActivityLog,
    CropCycle,
    Expense,
    ExpenseCategory,
    FarmElement,
    HarvestBatch,
    InputItem,
    InputLedger,
    InputPurchase,
    Unit,
    Variety,
)
from inventory.models import Grade, Product, StockLedger, StockLot
from inventory.services import move_lot_stock

D = Decimal
St = CropCycle.Stage
Act = ActivityLog.ActivityType


class Fixtures(TestCase):
    def setUp(self):
        self.variety = Variety.objects.create(name="Melon Golden", crop="Melon")
        self.element = FarmElement.objects.create(
            name="GH-1", element_type=FarmElement.ElementType.GREENHOUSE, area_m2=D("100")
        )
        self.cycle = CropCycle.objects.create(
            element=self.element, variety=self.variety, planting_start_date=date(2026, 1, 1)
        )

    def assertViolates(self, fn):
        with self.assertRaises(IntegrityError), transaction.atomic():
            fn()


# ===========================================================================
# Phase 1 — schema
# ===========================================================================


class ReferenceDataTests(TestCase):
    def test_migration_seeds_categories_and_groups(self):
        self.assertEqual(
            set(ExpenseCategory.objects.values_list("code", flat=True)),
            {code for code, _ in EXPENSE_CATEGORIES},
        )
        self.assertEqual(set(Group.objects.values_list("name", flat=True)), set(ROLES))


class FarmConstraintTests(Fixtures):
    def test_one_active_cycle_per_element(self):
        self.assertViolates(
            lambda: CropCycle.objects.create(
                element=self.element, variety=self.variety, planting_start_date=date(2026, 2, 1)
            )
        )

    def test_inactive_cycles_may_coexist(self):
        self.cycle.is_active = False
        self.cycle.save()
        CropCycle.objects.create(
            element=self.element, variety=self.variety, planting_start_date=date(2026, 2, 1)
        )
        CropCycle.objects.create(
            element=self.element,
            variety=self.variety,
            planting_start_date=date(2026, 3, 1),
            is_active=False,
        )
        self.assertEqual(self.element.cycles.count(), 3)

    def test_input_stock_cannot_go_negative(self):
        self.assertViolates(
            lambda: InputItem.objects.create(
                name="NPK", item_type=InputItem.ItemType.FERTILIZER, unit=Unit.KG, stock_qty=-1
            )
        )

    def test_expense_amount_positive_and_scope_consistent(self):
        cat = ExpenseCategory.objects.get(code="LAINNYA")
        self.assertViolates(
            lambda: Expense.objects.create(
                category=cat, scope=Expense.Scope.ELEMENT, element=self.element, amount_idr=0
            )
        )
        self.assertViolates(
            lambda: Expense.objects.create(category=cat, scope=Expense.Scope.ELEMENT, amount_idr=1)
        )
        self.assertViolates(
            lambda: Expense.objects.create(
                category=cat, scope=Expense.Scope.GENERAL, element=self.element, amount_idr=1
            )
        )

    def test_only_approved_expenses_can_be_absorbed(self):
        cat = ExpenseCategory.objects.get(code="PUPUK")
        batch = HarvestBatch.objects.create(cycle=self.cycle, high_quality_kg=D("10"))
        self.assertViolates(
            lambda: Expense.objects.create(
                category=cat,
                scope=Expense.Scope.ELEMENT,
                element=self.element,
                cycle=self.cycle,
                amount_idr=1,
                absorbed_by=batch,
            )
        )

    def test_input_ledger_is_append_only(self):
        item = InputItem.objects.create(
            name="NPK", item_type=InputItem.ItemType.FERTILIZER, unit=Unit.KG
        )
        row = InputLedger.objects.create(
            item=item, movement_type=InputLedger.MovementType.PURCHASE, qty=D("1")
        )
        row.reference = "edit"
        with self.assertRaises(ValueError):
            row.save()
        with self.assertRaises(ValueError):
            row.delete()


class InventoryConstraintTests(Fixtures):
    def setUp(self):
        super().setUp()
        self.product = Product.objects.create(variety=self.variety, grade=Grade.HIGH)
        self.batch = HarvestBatch.objects.create(cycle=self.cycle, high_quality_kg=D("10"))

    def make_lot(self, **kw):
        values = dict(
            product=self.product,
            harvest_batch=self.batch,
            qty_initial_kg=D("10"),
            qty_remaining_kg=D("10"),
            unit_cogs_idr=D("1000"),
            harvested_at=self.batch.harvested_at,
        )
        values.update(kw)
        return StockLot.objects.create(**values)

    def test_product_unique_per_variety_grade(self):
        self.assertViolates(lambda: Product.objects.create(variety=self.variety, grade=Grade.HIGH))

    def test_lot_remaining_cannot_go_negative(self):
        self.assertViolates(lambda: self.make_lot(qty_remaining_kg=D("-0.001")))

    def test_lot_remaining_cannot_exceed_initial(self):
        self.assertViolates(lambda: self.make_lot(qty_remaining_kg=D("10.001")))

    def test_ledger_sign_must_match_movement_type(self):
        lot = self.make_lot()
        self.assertViolates(
            lambda: StockLedger.objects.create(
                lot=lot, movement_type=StockLedger.MovementType.POS_SALE, qty_kg=D("1")
            )
        )
        self.assertViolates(
            lambda: StockLedger.objects.create(
                lot=lot, movement_type=StockLedger.MovementType.HARVEST_IN, qty_kg=D("-1")
            )
        )

    def test_stock_kg_is_derived_from_open_lots(self):
        self.assertEqual(self.product.stock_kg, D("0"))
        self.make_lot(qty_remaining_kg=D("4.500"))
        self.assertEqual(self.product.stock_kg, D("4.500"))


class SeedDemoTests(TestCase):
    def counts(self):
        return (
            get_user_model().objects.count(),
            FarmElement.objects.count(),
            CropCycle.objects.count(),
            Variety.objects.count(),
            InputItem.objects.count(),
        )

    def test_seed_demo_is_idempotent(self):
        call_command("seed_demo", stdout=StringIO())
        first = self.counts()
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual(first, self.counts())
        self.assertEqual(first[1:4], (3, 4, 3))
        self.assertTrue(Variety.objects.filter(name="Kakao Forastero").exists())
        for role in ROLES:
            self.assertTrue(get_user_model().objects.filter(groups__name=role).exists())

    def test_seed_demo_history_is_rich_balanced_and_written_once(self):
        from farm.models import Expense, HarvestBatch, InputPurchase
        from farm.services import unabsorbed_cycle_cost
        from inventory.models import Product, SpoilageRecord
        from inventory.services import check_mass_balance
        from pos.models import SaleOrder

        call_command("seed_demo", stdout=StringIO())
        snapshot = lambda: (  # noqa: E731
            HarvestBatch.objects.count(), SaleOrder.objects.count(), Expense.objects.count(),
            InputPurchase.objects.count(), SpoilageRecord.objects.count(),
        )
        first = snapshot()
        call_command("seed_demo", stdout=StringIO())
        self.assertEqual(first, snapshot())

        self.assertEqual(check_mass_balance(), [])
        self.assertGreaterEqual(HarvestBatch.objects.count(), 5)
        self.assertGreater(SpoilageRecord.objects.count(), 0)
        self.assertGreater(InputPurchase.objects.count(), 0)
        self.assertTrue(Expense.objects.filter(source=Expense.Source.INPUT_USAGE).exists())
        self.assertTrue(Expense.objects.filter(status=Expense.Status.PENDING).exists())
        self.assertTrue(Product.objects.filter(selling_price_idr__isnull=True).exists())
        self.assertTrue(Product.objects.filter(selling_price_idr__isnull=False).exists())

        orders = SaleOrder.objects
        self.assertTrue(55 <= orders.count() <= 65)
        self.assertTrue(orders.filter(status=SaleOrder.Status.VOIDED).exists())
        days = {timezone.localdate(o.created_at) for o in orders.all()}
        self.assertGreaterEqual(len(days), 28)

        closed = CropCycle.objects.get(is_active=False)
        self.assertEqual(closed.stage, CropCycle.Stage.CLEARING)
        self.assertGreater(unabsorbed_cycle_cost(closed), 0)
        self.assertEqual(CropCycle.objects.filter(is_active=True).count(), 3)

    def test_seed_demo_refuses_cycles_without_history(self):
        from django.core.management.base import CommandError

        element = FarmElement.objects.create(
            name="Greenhouse A", element_type=FarmElement.ElementType.GREENHOUSE, area_m2=1
        )
        variety = Variety.objects.create(name="Melon Golden", crop="Melon")
        CropCycle.objects.create(element=element, variety=variety, planting_start_date="2026-01-01")
        with self.assertRaises(CommandError):
            call_command("seed_demo", stdout=StringIO())


# ===========================================================================
# Phase 2 — farm services
# ===========================================================================


def make_user(username, role=None):
    user = get_user_model().objects.create_user(username=username, password="x")
    if role:
        user.groups.add(Group.objects.get(name=role))
    return user


class ServiceFixtures(Fixtures):
    def setUp(self):
        super().setUp()
        self.owner = make_user("owner", ROLE_OWNER)
        self.manager = make_user("manager", ROLE_MANAGER)
        self.worker = make_user("worker", ROLE_WORKER)
        self.cashier = make_user("cashier", ROLE_CASHIER)
        self.cat = {c.code: c for c in ExpenseCategory.objects.all()}
        self.npk = InputItem.objects.create(
            name="NPK", item_type=InputItem.ItemType.FERTILIZER, unit=Unit.KG, min_alert_qty=D("5")
        )

    def to_harvesting(self, cycle=None):
        return services.advance_cycle_stage(cycle or self.cycle, St.HARVESTING)

    def approved_expense(self, amount, cycle=None, category="TENAGA_KERJA"):
        cycle = cycle or self.cycle
        e = services.create_expense(
            self.cat[category], Expense.Scope.ELEMENT, amount, element=cycle.element, cycle=cycle
        )
        return services.approve_expense(e, self.manager)

    def set_price(self, grade, price, variety=None):
        p, _ = Product.objects.get_or_create(variety=variety or self.variety, grade=grade)
        p.selling_price_idr = D(price)
        p.save()
        return p

    def ledger_sum(self, item):
        return item.ledger.aggregate(s=Sum("qty"))["s"] or D("0")


# --- Rules 1, 8, 12: stages, closing, elements --------------------------------


class CycleStageTests(ServiceFixtures):
    def test_advances_forward_one_step(self):
        self.assertEqual(services.advance_cycle_stage(self.cycle, St.SPROUTING).stage, St.SPROUTING)

    def test_forward_skip_allowed(self):
        self.assertEqual(self.to_harvesting().stage, St.HARVESTING)

    def test_backward_rejected(self):
        services.advance_cycle_stage(self.cycle, St.FLOWERING)
        with self.assertRaises(ValidationError):
            services.advance_cycle_stage(self.cycle, St.VEGETATIVE)
        self.cycle.refresh_from_db()
        self.assertEqual(self.cycle.stage, St.FLOWERING)

    def test_same_stage_rejected(self):
        with self.assertRaises(ValidationError):
            services.advance_cycle_stage(self.cycle, St.PLANTING)

    def test_unknown_stage_rejected(self):
        with self.assertRaises(ValidationError):
            services.advance_cycle_stage(self.cycle, "ROTTING")

    def test_clearing_closes_cycle_and_freezes_it(self):
        cycle = services.close_cycle(self.cycle)
        self.assertFalse(cycle.is_active)
        self.assertIsNotNone(cycle.closed_at)
        with self.assertRaises(ValidationError):
            services.advance_cycle_stage(cycle, St.CLEARING)

    def test_start_cycle_requires_free_active_element(self):
        with self.assertRaises(ValidationError):
            services.start_cycle(self.element, self.variety, date(2026, 6, 1))
        services.close_cycle(self.cycle)
        self.assertTrue(services.start_cycle(self.element, self.variety, date(2026, 6, 1)).is_active)

    def test_start_cycle_rejects_inactive_element(self):
        off = FarmElement.objects.create(
            name="Off", element_type=FarmElement.ElementType.OPEN_FIELD, area_m2=1, is_active=False
        )
        with self.assertRaises(ValidationError):
            services.start_cycle(off, self.variety, date(2026, 6, 1))


class ElementDeactivationTests(ServiceFixtures):
    def test_cannot_deactivate_with_active_cycle(self):
        with self.assertRaises(ValidationError):
            services.set_element_active(self.element, False)
        self.element.refresh_from_db()
        self.assertTrue(self.element.is_active)

    def test_can_deactivate_after_cycle_closed_and_reactivate(self):
        services.close_cycle(self.cycle)
        self.assertFalse(services.set_element_active(self.element, False).is_active)
        self.assertTrue(services.set_element_active(self.element, True).is_active)


# --- Rules 11, 13: inputs ------------------------------------------------------


class WeightedAverageTests(TestCase):
    def test_first_purchase_uses_purchase_cost(self):
        self.assertEqual(services.weighted_average_cost(0, 0, 10, 5000), D("5000.00"))

    def test_blends_existing_stock(self):
        # (10×5000 + 30×7000) / 40 = 6500
        self.assertEqual(services.weighted_average_cost(10, 5000, 30, 7000), D("6500.00"))

    def test_rounds_half_up_to_cents(self):
        # (1×1 + 2×2) / 3 = 1.666… → 1.67
        self.assertEqual(services.weighted_average_cost(1, 1, 2, 2), D("1.67"))


class InputStockTests(ServiceFixtures):
    def test_purchase_updates_stock_avg_and_ledger(self):
        services.record_input_purchase(self.npk, 10, 5000, supplier="Toko A")
        services.record_input_purchase(self.npk, 30, 7000)
        self.npk.refresh_from_db()
        self.assertEqual(self.npk.stock_qty, D("40.000"))
        self.assertEqual(self.npk.avg_cost_per_unit_idr, D("6500.00"))
        self.assertEqual(self.ledger_sum(self.npk), self.npk.stock_qty)
        self.assertEqual(
            list(self.npk.ledger.values_list("movement_type", flat=True)), ["PURCHASE", "PURCHASE"]
        )

    def test_purchase_rejects_nonpositive_qty_or_cost(self):
        with self.assertRaises(ValidationError):
            services.record_input_purchase(self.npk, 0, 5000)
        with self.assertRaises(ValidationError):
            services.record_input_purchase(self.npk, 1, 0)
        self.assertFalse(InputPurchase.objects.exists())

    def test_purchase_after_stock_depleted_resets_avg(self):
        services.record_input_purchase(self.npk, 2, 1000)
        services.log_activity(self.cycle, Act.FERTILIZER, 2, Unit.KG, self.npk)
        services.record_input_purchase(self.npk, 1, 9000)
        self.npk.refresh_from_db()
        self.assertEqual(self.npk.avg_cost_per_unit_idr, D("9000.00"))

    def test_move_input_stock_blocks_negative_and_bad_signs(self):
        bad = [
            (D("-1"), InputLedger.MovementType.USAGE),  # insufficient stock
            (D("1"), InputLedger.MovementType.USAGE),
            (D("-1"), InputLedger.MovementType.USAGE_REVERSAL),
            (D("0"), InputLedger.MovementType.PURCHASE),
            (D("1"), "GIFT"),
        ]
        for qty, mtype in bad:
            with self.assertRaises(ValidationError):
                services.move_input_stock(self.npk.pk, qty, mtype)
        self.assertFalse(self.npk.ledger.exists())

    def test_low_stock_alert(self):
        services.record_input_purchase(self.npk, 6, 1000)
        self.npk.refresh_from_db()
        self.assertFalse(self.npk.is_low_stock)
        services.log_activity(self.cycle, Act.FERTILIZER, 1, Unit.KG, self.npk)
        self.npk.refresh_from_db()
        self.assertTrue(self.npk.is_low_stock)  # 5 <= 5


class PurchaseDeletionTests(ServiceFixtures):
    def test_delete_without_later_usage_reverses_stock_and_avg(self):
        services.record_input_purchase(self.npk, 10, 5000)
        p2 = services.record_input_purchase(self.npk, 30, 7000)
        services.delete_input_purchase(p2)
        self.npk.refresh_from_db()
        self.assertEqual(self.npk.stock_qty, D("10.000"))
        self.assertEqual(self.npk.avg_cost_per_unit_idr, D("5000.00"))
        self.assertEqual(self.ledger_sum(self.npk), self.npk.stock_qty)
        self.assertFalse(InputPurchase.objects.filter(pk=p2.pk).exists())

    def test_delete_only_purchase_empties_stock(self):
        p = services.record_input_purchase(self.npk, 10, 5000)
        services.delete_input_purchase(p)
        self.npk.refresh_from_db()
        self.assertEqual(self.npk.stock_qty, D("0"))
        self.assertEqual(self.ledger_sum(self.npk), D("0"))

    def test_delete_blocked_after_later_usage(self):
        p = services.record_input_purchase(
            self.npk, 10, 5000, purchased_at=timezone.now() - timedelta(days=2)
        )
        services.log_activity(self.cycle, Act.FERTILIZER, 1, Unit.KG, self.npk)
        with self.assertRaises(ValidationError):
            services.delete_input_purchase(p)
        self.assertTrue(InputPurchase.objects.filter(pk=p.pk).exists())

    def test_delete_allowed_if_usage_was_before_purchase(self):
        now = timezone.now()
        services.record_input_purchase(self.npk, 5, 1000, purchased_at=now - timedelta(days=5))
        services.log_activity(
            self.cycle, Act.FERTILIZER, 1, Unit.KG, self.npk, logged_at=now - timedelta(days=4)
        )
        later = services.record_input_purchase(self.npk, 5, 3000, purchased_at=now - timedelta(days=1))
        services.delete_input_purchase(later)
        self.npk.refresh_from_db()
        self.assertEqual(self.npk.stock_qty, D("4.000"))
        self.assertEqual(self.npk.avg_cost_per_unit_idr, D("1000.00"))

    def test_delete_blocked_when_backdated_usage_consumed_its_stock(self):
        # Usage is back-dated before the purchase but was only possible thanks to it:
        # cancelling the purchase would drive stock negative.
        now = timezone.now()
        services.record_input_purchase(self.npk, 5, 1000, purchased_at=now - timedelta(days=5))
        later = services.record_input_purchase(self.npk, 10, 2000, purchased_at=now - timedelta(days=1))
        services.log_activity(
            self.cycle, Act.FERTILIZER, 12, Unit.KG, self.npk, logged_at=now - timedelta(days=3)
        )
        with self.assertRaisesMessage(ValidationError, "tidak cukup untuk membatalkan"):
            services.delete_input_purchase(later)
        self.npk.refresh_from_db()
        self.assertEqual(self.npk.stock_qty, D("3.000"))
        self.assertTrue(InputPurchase.objects.filter(pk=later.pk).exists())

    def test_delete_allowed_again_once_later_usage_is_deleted(self):
        p = services.record_input_purchase(self.npk, 10, 5000)
        act = services.log_activity(self.cycle, Act.FERTILIZER, 1, Unit.KG, self.npk)
        services.delete_activity(act)
        services.delete_input_purchase(p)
        self.npk.refresh_from_db()
        self.assertEqual(self.npk.stock_qty, D("0"))


class ActivityUsageTests(ServiceFixtures):
    def setUp(self):
        super().setUp()
        services.record_input_purchase(self.npk, 10, 5000)
        self.pest = InputItem.objects.create(
            name="Abamektin", item_type=InputItem.ItemType.PESTICIDE, unit=Unit.ML
        )
        services.record_input_purchase(self.pest, 500, 120)
        self.seed = InputItem.objects.create(
            name="Benih Melon", item_type=InputItem.ItemType.SEED, unit=Unit.G
        )
        services.record_input_purchase(self.seed, 100, 2500)

    def test_fertilizer_usage_deducts_stock_and_books_approved_expense(self):
        act = services.log_activity(
            self.cycle, Act.FERTILIZER, D("2.5"), Unit.KG, self.npk, user=self.worker
        )
        self.npk.refresh_from_db()
        self.assertEqual(self.npk.stock_qty, D("7.500"))
        self.assertEqual(self.ledger_sum(self.npk), self.npk.stock_qty)
        exp = Expense.objects.get(source_activity=act)
        self.assertEqual(exp.amount_idr, D("12500.00"))
        self.assertEqual(exp.status, Expense.Status.APPROVED)
        self.assertEqual(exp.source, Expense.Source.INPUT_USAGE)
        self.assertEqual(exp.category.code, "PUPUK")
        self.assertEqual((exp.scope, exp.element, exp.cycle), ("ELEMENT", self.element, self.cycle))
        self.assertEqual(act.stage_at_activity, St.PLANTING)

    def test_pesticide_and_seed_categories(self):
        a1 = services.log_activity(self.cycle, Act.PESTICIDE, 50, Unit.ML, self.pest)
        a2 = services.log_activity(self.cycle, Act.SEEDING, 10, Unit.G, self.seed)
        e1 = Expense.objects.get(source_activity=a1)
        self.assertEqual((e1.category.code, e1.amount_idr), ("PESTISIDA", D("6000.00")))
        self.assertEqual(Expense.objects.get(source_activity=a2).category.code, "BIBIT")

    def test_stage_snapshot_recorded(self):
        services.advance_cycle_stage(self.cycle, St.VEGETATIVE)
        act = services.log_activity(self.cycle, Act.SCOUTING)
        services.advance_cycle_stage(self.cycle, St.FLOWERING)
        act.refresh_from_db()
        self.assertEqual(act.stage_at_activity, St.VEGETATIVE)

    def test_insufficient_stock_rejects_whole_activity(self):
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, Act.FERTILIZER, 11, Unit.KG, self.npk)
        self.assertFalse(ActivityLog.objects.exists())
        self.assertFalse(Expense.objects.exists())
        self.npk.refresh_from_db()
        self.assertEqual(self.npk.stock_qty, D("10.000"))

    def test_unit_must_match_item_unit(self):
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, Act.FERTILIZER, 1, Unit.G, self.npk)

    def test_item_type_must_match_activity(self):
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, Act.PESTICIDE, 1, Unit.KG, self.npk)
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, Act.IRRIGATION, 1, Unit.KG, self.npk)

    def test_quantity_validation(self):
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, Act.SEEDING, 1, Unit.G)  # seeding needs an item
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, Act.FERTILIZER, 0, Unit.KG, self.npk)
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, Act.IRRIGATION, -1, Unit.L)

    def test_activity_without_input_has_no_expense(self):
        services.log_activity(self.cycle, Act.IRRIGATION, 200, Unit.L)
        services.log_activity(self.cycle, Act.FERTILIZER)  # e.g. manure from own farm, no item
        self.assertFalse(Expense.objects.exists())

    def test_closed_cycle_rejects_activity(self):
        services.close_cycle(self.cycle)
        with self.assertRaises(ValidationError):
            services.log_activity(self.cycle, Act.SCOUTING)

    def test_delete_unabsorbed_activity_reverses_stock_and_expense(self):
        act = services.log_activity(self.cycle, Act.FERTILIZER, 3, Unit.KG, self.npk)
        services.delete_activity(act)
        self.npk.refresh_from_db()
        self.assertEqual(self.npk.stock_qty, D("10.000"))
        self.assertEqual(self.ledger_sum(self.npk), self.npk.stock_qty)
        self.assertIn("USAGE_REVERSAL", self.npk.ledger.values_list("movement_type", flat=True))
        self.assertFalse(Expense.objects.exists())
        self.assertFalse(ActivityLog.objects.exists())

    def test_delete_activity_blocked_once_expense_absorbed(self):
        act = services.log_activity(self.cycle, Act.FERTILIZER, 3, Unit.KG, self.npk)
        self.to_harvesting()
        services.record_harvest(self.cycle, 10)
        with self.assertRaises(ValidationError):
            services.delete_activity(act)
        self.npk.refresh_from_db()
        self.assertEqual(self.npk.stock_qty, D("7.000"))
        self.assertTrue(ActivityLog.objects.filter(pk=act.pk).exists())

    def test_delete_activity_without_input(self):
        act = services.log_activity(self.cycle, Act.SCOUTING, notes="aman")
        services.delete_activity(act)
        self.assertFalse(ActivityLog.objects.exists())

    def test_usage_expense_not_editable_via_expense_services(self):
        act = services.log_activity(self.cycle, Act.FERTILIZER, 1, Unit.KG, self.npk)
        exp = Expense.objects.get(source_activity=act)
        with self.assertRaises(ValidationError):
            services.update_expense(exp, narration="x")
        with self.assertRaises(ValidationError):
            services.revert_expense_to_pending(exp, self.manager)
        with self.assertRaises(ValidationError):
            services.delete_expense(exp)


# --- Rules 7, 8, 9: expense lifecycle ------------------------------------------


class ExpenseLifecycleTests(ServiceFixtures):
    def new(self, amount=100000, **kw):
        kw.setdefault("element", self.element)
        kw.setdefault("cycle", self.cycle)
        return services.create_expense(self.cat["TENAGA_KERJA"], Expense.Scope.ELEMENT, amount, **kw)

    def test_create_is_pending_manual(self):
        e = self.new()
        self.assertEqual((e.status, e.source), (Expense.Status.PENDING, Expense.Source.MANUAL))

    def test_create_validates_amount_and_scope(self):
        with self.assertRaises(ValidationError):
            self.new(amount=0)
        with self.assertRaises(ValidationError):
            services.create_expense(self.cat["UTILITAS"], Expense.Scope.ELEMENT, 1)
        with self.assertRaises(ValidationError):
            services.create_expense(self.cat["UTILITAS"], Expense.Scope.GENERAL, 1, element=self.element)
        with self.assertRaises(ValidationError):
            services.create_expense(self.cat["UTILITAS"], "OTHER", 1)
        other = FarmElement.objects.create(
            name="GH-2", element_type=FarmElement.ElementType.GREENHOUSE, area_m2=1
        )
        with self.assertRaises(ValidationError):
            self.new(element=other, cycle=self.cycle)  # cycle belongs to another element

    def test_approve_and_reject_by_owner_or_manager(self):
        a = services.approve_expense(self.new(), self.owner)
        self.assertEqual((a.status, a.approved_by), (Expense.Status.APPROVED, self.owner))
        self.assertIsNotNone(a.approved_at)
        r = services.reject_expense(self.new(), self.manager)
        self.assertEqual(r.status, Expense.Status.REJECTED)

    def test_superuser_may_approve(self):
        su = get_user_model().objects.create_superuser("root", password="x")
        self.assertEqual(services.approve_expense(self.new(), su).status, Expense.Status.APPROVED)

    def test_worker_cashier_and_anonymous_cannot_approve_or_reject(self):
        e = self.new()
        for user in (self.worker, self.cashier, None):
            with self.assertRaises(PermissionDenied):
                services.approve_expense(e, user)
            with self.assertRaises(PermissionDenied):
                services.reject_expense(e, user)
        e.refresh_from_db()
        self.assertEqual(e.status, Expense.Status.PENDING)

    def test_only_pending_can_be_approved_or_rejected(self):
        e = services.approve_expense(self.new(), self.manager)
        with self.assertRaises(ValidationError):
            services.approve_expense(e, self.manager)
        with self.assertRaises(ValidationError):
            services.reject_expense(e, self.manager)

    def test_revert_unabsorbed_approved_to_pending(self):
        e = services.approve_expense(self.new(), self.manager)
        e = services.revert_expense_to_pending(e, self.owner)
        self.assertEqual(e.status, Expense.Status.PENDING)
        self.assertIsNone(e.approved_by)
        self.assertIsNone(e.approved_at)

    def test_revert_requires_approved_status_and_role(self):
        e = self.new()
        with self.assertRaises(ValidationError):
            services.revert_expense_to_pending(e, self.manager)
        e = services.approve_expense(e, self.manager)
        with self.assertRaises(PermissionDenied):
            services.revert_expense_to_pending(e, self.worker)

    def test_absorbed_expense_is_immutable(self):
        e = services.approve_expense(self.new(), self.manager)
        self.to_harvesting()
        services.record_harvest(self.cycle, 10)
        e.refresh_from_db()
        self.assertIsNotNone(e.absorbed_by)
        with self.assertRaises(ValidationError):
            services.revert_expense_to_pending(e, self.manager)
        with self.assertRaises(ValidationError):
            services.update_expense(e, amount_idr=1)
        with self.assertRaises(ValidationError):
            services.delete_expense(e)

    def test_update_pending_and_rejected_returns_to_pending(self):
        e = services.update_expense(self.new(), amount_idr=D("250000"), vendor="CV Tani")
        self.assertEqual((e.amount_idr, e.vendor), (D("250000.00"), "CV Tani"))
        r = services.reject_expense(e, self.manager)
        r = services.update_expense(r, narration="koreksi")
        self.assertEqual(r.status, Expense.Status.PENDING)

    def test_update_validates(self):
        e = self.new()
        with self.assertRaises(ValidationError):
            services.update_expense(e, amount_idr=0)
        with self.assertRaises(ValidationError):
            services.update_expense(e, status="APPROVED")
        with self.assertRaises(ValidationError):
            services.update_expense(e, scope=Expense.Scope.GENERAL)  # element still set

    def test_delete_pending_or_rejected_only(self):
        services.delete_expense(self.new())
        services.delete_expense(services.reject_expense(self.new(), self.manager))
        e = services.approve_expense(self.new(), self.manager)
        with self.assertRaises(ValidationError):
            services.delete_expense(e)
        self.assertEqual(Expense.objects.count(), 1)

    def test_cycleless_element_expense_attaches_to_active_cycle(self):
        e = services.approve_expense(self.new(cycle=None), self.manager)
        self.assertEqual(e.cycle, self.cycle)

    def test_cycleless_element_expense_without_active_cycle_stays_overhead(self):
        services.close_cycle(self.cycle)
        e = services.approve_expense(self.new(cycle=None), self.manager)
        self.assertIsNone(e.cycle)
        self.assertEqual(e.status, Expense.Status.APPROVED)

    def test_general_expense_never_enters_cogs(self):
        g = services.approve_expense(
            services.create_expense(self.cat["UTILITAS"], Expense.Scope.GENERAL, 50000), self.manager
        )
        self.assertIsNone(g.cycle)
        self.to_harvesting()
        b = services.record_harvest(self.cycle, 10)
        g.refresh_from_db()
        self.assertIsNone(g.absorbed_by)
        self.assertEqual(b.absorbed_cost_idr, D("0"))

    def test_approval_on_closed_cycle_lands_there_as_unabsorbed(self):
        e = self.new(amount=70000)
        services.close_cycle(self.cycle)
        e = services.approve_expense(e, self.manager)
        self.assertEqual(e.cycle, self.cycle)
        self.assertEqual(services.unabsorbed_cycle_cost(self.cycle), D("70000.00"))


# --- Rules 2–6, 10: harvest, cost pool, grade split ------------------------------


class GradeSplitTests(TestCase):
    def test_rsv_split(self):
        # values 100×20000 = 2.0M vs 50×8000 = 0.4M → HIGH gets 5/6 of 1.2M
        s = services.split_cost_by_grade(D("1200000"), 100, 50, 20000, 8000)
        self.assertEqual(s.method, "RSV")
        self.assertEqual((s.unit_cogs_high, s.unit_cogs_low), (D("10000.00"), D("4000.00")))

    def test_rsv_unit_cogs_proportional_to_price(self):
        s = services.split_cost_by_grade(D("90000"), 3, 7, 30000, 10000)
        self.assertEqual(s.unit_cogs_high / s.unit_cogs_low, D("3"))

    def test_equal_fallback_when_any_price_missing(self):
        for hp, lp in ((None, 8000), (20000, None), (None, None)):
            s = services.split_cost_by_grade(D("1500000"), 100, 50, hp, lp)
            self.assertEqual(s.method, "EQUAL")
            self.assertEqual((s.unit_cogs_high, s.unit_cogs_low), (D("10000.00"), D("10000.00")))

    def test_single_grade_carries_everything(self):
        s = services.split_cost_by_grade(D("500000"), 50, 0, 20000, None)
        self.assertEqual((s.unit_cogs_high, s.unit_cogs_low, s.method), (D("10000.00"), 0, "SINGLE_GRADE"))
        s = services.split_cost_by_grade(D("500000"), 0, 25)
        self.assertEqual((s.unit_cogs_high, s.unit_cogs_low), (0, D("20000.00")))

    def test_zero_yield_no_division(self):
        s = services.split_cost_by_grade(D("500000"), 0, 0, 20000, 8000)
        self.assertEqual((s.unit_cogs_high, s.unit_cogs_low, s.method), (0, 0, "ZERO_YIELD"))

    def test_zero_cost(self):
        s = services.split_cost_by_grade(D("0"), 10, 5, 20000, 8000)
        self.assertEqual((s.unit_cogs_high, s.unit_cogs_low), (0, 0))


class HarvestTests(ServiceFixtures):
    def lots(self, batch):
        return {lot.product.grade: lot for lot in batch.lots.select_related("product")}

    def test_harvest_only_in_harvesting_stage(self):
        services.advance_cycle_stage(self.cycle, St.FRUITING)
        with self.assertRaises(ValidationError):
            services.record_harvest(self.cycle, 10)
        self.to_harvesting()
        services.record_harvest(self.cycle, 10)
        services.close_cycle(self.cycle)
        with self.assertRaises(ValidationError):
            services.record_harvest(self.cycle, 10)
        self.assertEqual(HarvestBatch.objects.count(), 1)

    def test_kg_and_bag_validation(self):
        self.to_harvesting()
        for args in ((0,), (-1,), (10, -1), (10, 0, -1)):
            with self.assertRaises(ValidationError):
                services.record_harvest(self.cycle, *args)
        with self.assertRaises(ValidationError):
            services.record_harvest(self.cycle, 10, number_of_bags=0)
        with self.assertRaises(ValidationError):
            services.record_harvest(self.cycle, 10, weight_per_bag_kg=0)
        self.assertFalse(HarvestBatch.objects.exists())

    def test_rsv_harvest_absorbs_pool_and_creates_lots(self):
        self.set_price(Grade.HIGH, 20000)
        self.set_price(Grade.LOW, 8000)
        e1 = self.approved_expense(700000)
        e2 = self.approved_expense(500000, category="PERALATAN")
        pending = services.create_expense(
            self.cat["LAINNYA"], Expense.Scope.ELEMENT, 99, element=self.element, cycle=self.cycle
        )
        self.to_harvesting()
        batch = services.record_harvest(
            self.cycle, 100, 50, 5, number_of_bags=15, weight_per_bag_kg=10, user=self.worker
        )

        self.assertEqual(batch.absorbed_cost_idr, D("1200000.00"))
        self.assertEqual((batch.unit_cogs_high_idr, batch.unit_cogs_low_idr), (D("10000.00"), D("4000.00")))
        for e in (e1, e2):
            e.refresh_from_db()
            self.assertEqual(e.absorbed_by, batch)
        pending.refresh_from_db()
        self.assertIsNone(pending.absorbed_by)

        lots = self.lots(batch)
        self.assertEqual(
            (lots[Grade.HIGH].qty_initial_kg, lots[Grade.HIGH].qty_remaining_kg, lots[Grade.HIGH].unit_cogs_idr),
            (D("100.000"), D("100.000"), D("10000.00")),
        )
        self.assertEqual(
            (lots[Grade.LOW].qty_initial_kg, lots[Grade.LOW].unit_cogs_idr), (D("50.000"), D("4000.00"))
        )
        for lot in lots.values():
            self.assertEqual(lot.harvested_at, batch.harvested_at)
            self.assertEqual(
                list(lot.ledger.values_list("movement_type", "qty_kg")),
                [("HARVEST_IN", lot.qty_initial_kg)],
            )

    def test_cogs_times_kg_reconciles_to_absorbed_cost(self):
        self.set_price(Grade.HIGH, 25000)
        self.set_price(Grade.LOW, 9000)
        self.approved_expense(D("1000000"))
        self.to_harvesting()
        b = services.record_harvest(self.cycle, D("33.3"), D("12.7"))
        allocated = b.high_quality_kg * b.unit_cogs_high_idr + b.low_quality_kg * b.unit_cogs_low_idr
        self.assertLess(abs(allocated - b.absorbed_cost_idr), D("1"))  # cent rounding only

    def test_equal_fallback_and_auto_created_unpriced_products(self):
        self.approved_expense(1500000)
        self.to_harvesting()
        batch = services.record_harvest(self.cycle, 100, 50)
        self.assertEqual((batch.unit_cogs_high_idr, batch.unit_cogs_low_idr), (D("10000.00"), D("10000.00")))
        products = Product.objects.filter(variety=self.variety)
        self.assertEqual(products.count(), 2)
        self.assertTrue(all(p.selling_price_idr is None for p in products))

    def test_only_high_priced_falls_back_to_equal(self):
        self.set_price(Grade.HIGH, 20000)
        self.approved_expense(1500000)
        self.to_harvesting()
        batch = services.record_harvest(self.cycle, 100, 50)
        self.assertEqual(batch.unit_cogs_high_idr, batch.unit_cogs_low_idr)

    def test_no_lot_or_product_for_zero_kg_grade(self):
        self.approved_expense(500000)
        self.to_harvesting()
        batch = services.record_harvest(self.cycle, 50, 0)
        self.assertEqual(batch.lots.count(), 1)
        self.assertFalse(Product.objects.filter(grade=Grade.LOW).exists())
        self.assertEqual((batch.unit_cogs_high_idr, batch.unit_cogs_low_idr), (D("10000.00"), 0))

    def test_existing_product_reused_and_price_kept(self):
        p = self.set_price(Grade.HIGH, 20000)
        self.to_harvesting()
        services.record_harvest(self.cycle, 10)
        services.record_harvest(self.cycle, 10)
        self.assertEqual(Product.objects.filter(variety=self.variety, grade=Grade.HIGH).count(), 1)
        p.refresh_from_db()
        self.assertEqual(p.selling_price_idr, D("20000.00"))
        self.assertEqual(p.stock_kg, D("20.000"))

    def test_harvest_with_empty_pool_has_zero_cogs(self):
        self.to_harvesting()
        batch = services.record_harvest(self.cycle, 10, 5)
        self.assertEqual(batch.absorbed_cost_idr, 0)
        self.assertEqual(batch.lots.count(), 2)

    def test_incremental_pool_never_restates_earlier_lots(self):
        self.approved_expense(1000000)
        self.to_harvesting()
        b1 = services.record_harvest(self.cycle, 100)
        lot1 = b1.lots.get()
        self.approved_expense(200000)
        b2 = services.record_harvest(self.cycle, 100)
        lot1.refresh_from_db()
        b1.refresh_from_db()
        self.assertEqual(lot1.unit_cogs_idr, D("10000.00"))
        self.assertEqual(b1.absorbed_cost_idr, D("1000000.00"))
        self.assertEqual(b2.absorbed_cost_idr, D("200000.00"))
        self.assertEqual(b2.lots.get().unit_cogs_idr, D("2000.00"))  # first-harvest skew (D10)
        b3 = services.record_harvest(self.cycle, 10)
        self.assertEqual(b3.absorbed_cost_idr, 0)
        self.assertFalse(Expense.objects.filter(absorbed_by__isnull=True).exists())

    def test_pool_is_per_cycle(self):
        other_el = FarmElement.objects.create(
            name="GH-2", element_type=FarmElement.ElementType.GREENHOUSE, area_m2=10
        )
        other = CropCycle.objects.create(
            element=other_el, variety=self.variety, planting_start_date=date(2026, 1, 1)
        )
        foreign = self.approved_expense(999999, cycle=other)
        own = self.approved_expense(100000)
        self.to_harvesting()
        b = services.record_harvest(self.cycle, 10)
        self.assertEqual(b.absorbed_cost_idr, D("100000.00"))
        foreign.refresh_from_db()
        own.refresh_from_db()
        self.assertIsNone(foreign.absorbed_by)
        self.assertEqual(own.absorbed_by, b)

    def test_rejected_and_pending_expenses_not_absorbed(self):
        rej = services.reject_expense(
            services.create_expense(
                self.cat["LAINNYA"], Expense.Scope.ELEMENT, 5, element=self.element, cycle=self.cycle
            ),
            self.manager,
        )
        self.to_harvesting()
        b = services.record_harvest(self.cycle, 10)
        rej.refresh_from_db()
        self.assertIsNone(rej.absorbed_by)
        self.assertEqual(b.absorbed_cost_idr, 0)

    def test_zero_yield_batch_absorbs_nothing_and_creates_no_lots(self):
        # record_harvest enforces HIGH > 0 (rule 2), so the zero-yield guard
        # (rule 5, D12) is exercised directly on the absorption step.
        e = self.approved_expense(400000)
        self.to_harvesting()
        batch = HarvestBatch.objects.create(
            cycle=self.cycle, high_quality_kg=0, low_quality_kg=0, harvest_loss_kg=30
        )
        services._absorb_and_stock(batch, self.cycle)
        batch.refresh_from_db()
        e.refresh_from_db()
        self.assertIsNone(e.absorbed_by)
        self.assertEqual(batch.lots.count(), 0)
        self.assertEqual(
            (batch.absorbed_cost_idr, batch.unit_cogs_high_idr, batch.unit_cogs_low_idr), (0, 0, 0)
        )
        # The cost carries forward to the next real harvest.
        self.assertEqual(services.record_harvest(self.cycle, 40).absorbed_cost_idr, D("400000.00"))

    def test_unabsorbed_cycle_cost_after_close(self):
        self.approved_expense(100000)
        self.to_harvesting()
        services.record_harvest(self.cycle, 10)
        self.approved_expense(30000)
        services.close_cycle(self.cycle)
        self.assertEqual(services.unabsorbed_cycle_cost(self.cycle), D("30000.00"))


class HarvestDeletionTests(ServiceFixtures):
    def setUp(self):
        super().setUp()
        self.exp = self.approved_expense(600000)
        self.to_harvesting()
        self.batch = services.record_harvest(self.cycle, 40, 20)

    def test_delete_untouched_batch_unabsorbs_and_removes_lots(self):
        lot_ids = list(self.batch.lots.values_list("pk", flat=True))
        services.delete_harvest_batch(self.batch)
        self.assertFalse(HarvestBatch.objects.exists())
        self.assertFalse(StockLot.objects.filter(pk__in=lot_ids).exists())
        self.assertFalse(StockLedger.objects.filter(lot_id__in=lot_ids).exists())
        self.exp.refresh_from_db()
        self.assertIsNone(self.exp.absorbed_by)
        # Pool is intact: the next harvest re-absorbs it.
        self.assertEqual(services.record_harvest(self.cycle, 60).absorbed_cost_idr, D("600000.00"))

    def test_delete_blocked_after_non_harvest_movement(self):
        lot = self.batch.lots.first()
        move_lot_stock(lot.pk, D("-1"), StockLedger.MovementType.SPOILAGE, reference="test")
        with self.assertRaises(ValidationError):
            services.delete_harvest_batch(self.batch)
        self.assertTrue(HarvestBatch.objects.filter(pk=self.batch.pk).exists())
        self.exp.refresh_from_db()
        self.assertEqual(self.exp.absorbed_by, self.batch)
