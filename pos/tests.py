"""Phase 4 tests: locked FIFO checkout, order numbers, void, concurrency (rules 16–18)."""

import threading
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import connection
from django.test import TestCase, TransactionTestCase

from farm import services as farm
from farm.bootstrap import ROLE_CASHIER, ROLE_MANAGER, ROLE_OWNER, ROLE_WORKER
from farm.models import CropCycle, ExpenseCategory, FarmElement, Variety
from inventory.models import Grade, Product, StockLedger, StockLot
from inventory.services import check_mass_balance, record_spoilage
from pos import services
from pos.models import LineLotAllocation, OrderLineItem, SaleOrder

D = Decimal
WIB = ZoneInfo("Asia/Jakarta")
MT = StockLedger.MovementType
NOON = datetime(2026, 11, 19, 12, 0, tzinfo=WIB)


def make_user(username, role=None, superuser=False):
    User = get_user_model()
    if superuser:
        return User.objects.create_superuser(username, password="x")
    user = User.objects.create_user(username, password="x")
    if role:
        user.groups.add(Group.objects.get_or_create(name=role)[0])
    return user


class PosFixtures(TestCase):
    """Melon Golden, two harvests:
    batch 1 (Nov 10): HIGH 100 kg @ 10.000, LOW 50 kg @ 4.000 (RSV of 1.200.000)
    batch 2 (Nov 15): HIGH  50 kg @ 12.000 (600.000, no LOW)
    Prices: HIGH 20.000/kg, LOW 8.000/kg.
    """

    def setUp(self):
        self.owner = make_user("owner", ROLE_OWNER)
        self.manager = make_user("manager", ROLE_MANAGER)
        self.cashier = make_user("cashier", ROLE_CASHIER)
        self.worker = make_user("worker", ROLE_WORKER)
        services.open_shift(self.cashier, 0)  # D96: no sales without an open shift

        self.variety = Variety.objects.create(name="Melon Golden", crop="Melon")
        self.element = FarmElement.objects.create(
            name="GH-1", element_type=FarmElement.ElementType.GREENHOUSE, area_m2=D("100")
        )
        self.cycle = CropCycle.objects.create(
            element=self.element,
            variety=self.variety,
            planting_start_date=date(2026, 8, 1),
            stage=CropCycle.Stage.HARVESTING,
        )
        self.high = Product.objects.create(variety=self.variety, grade=Grade.HIGH, selling_price_idr=20000)
        self.low = Product.objects.create(variety=self.variety, grade=Grade.LOW, selling_price_idr=8000)
        labor = ExpenseCategory.objects.get(code="TENAGA_KERJA")

        self._expense(labor, 1200000)
        self.batch1 = farm.record_harvest(self.cycle, 100, 50, harvested_at=datetime(2026, 11, 10, 8, tzinfo=WIB))
        self._expense(labor, 600000)
        self.batch2 = farm.record_harvest(self.cycle, 50, 0, harvested_at=datetime(2026, 11, 15, 8, tzinfo=WIB))

        self.lot_h1 = self.batch1.lots.get(product=self.high)
        self.lot_l1 = self.batch1.lots.get(product=self.low)
        self.lot_h2 = self.batch2.lots.get(product=self.high)

    def _expense(self, category, amount):
        expense = farm.create_expense(
            category, "ELEMENT", amount, element=self.element, cycle=self.cycle
        )
        farm.approve_expense(expense, self.owner)

    def sell(self, items, at=NOON, user=None, method="CASH"):
        return services.checkout(items, method, user or self.cashier, created_at=at)

    def reload(self, *lots):
        for lot in lots:
            lot.refresh_from_db()

    def assertBalanced(self):
        self.assertEqual(check_mass_balance(), [])


# --- Fixture sanity --------------------------------------------------------------


class FixtureTests(PosFixtures):
    def test_lot_costs(self):
        self.assertEqual(
            [(lot.qty_initial_kg, lot.unit_cogs_idr) for lot in (self.lot_h1, self.lot_l1, self.lot_h2)],
            [(D("100"), D("10000")), (D("50"), D("4000")), (D("50"), D("12000"))],
        )


# --- Checkout (rules 16, 17) ---------------------------------------------------


class CheckoutTests(PosFixtures):
    def test_fifo_across_lots_with_weighted_cogs(self):
        order = self.sell({self.high.pk: "120", self.low.pk: "10"})
        self.reload(self.lot_h1, self.lot_h2, self.lot_l1)

        self.assertEqual(order.status, SaleOrder.Status.COMPLETED)
        self.assertEqual(order.payment_method, "CASH")
        self.assertEqual(order.cashier, self.cashier)
        self.assertEqual(order.created_at, NOON)
        # Oldest lot drained first, remainder from the newer lot.
        self.assertEqual(self.lot_h1.qty_remaining_kg, D("0"))
        self.assertEqual(self.lot_h2.qty_remaining_kg, D("30"))
        self.assertEqual(self.lot_l1.qty_remaining_kg, D("40"))

        high_line = order.lines.get(product=self.high)
        self.assertEqual(high_line.qty_kg, D("120"))
        self.assertEqual(high_line.unit_price_idr, D("20000"))
        # (100×10.000 + 20×12.000) / 120 = 10.333,33
        self.assertEqual(high_line.unit_cogs_idr, D("10333.33"))
        self.assertEqual(
            list(high_line.allocations.order_by("lot__harvested_at").values_list("lot_id", "qty_kg", "unit_cogs_idr")),
            [(self.lot_h1.pk, D("100"), D("10000")), (self.lot_h2.pk, D("20"), D("12000"))],
        )
        # Revenue 120×20.000 + 10×8.000; COGS 1.240.000 + 40.000 (exact, not from rounded line COGS).
        self.assertEqual(order.total_revenue_idr, D("2480000.00"))
        self.assertEqual(order.total_cogs_idr, D("1280000.00"))
        self.assertBalanced()

    def test_pos_sale_ledger_rows_reference_order(self):
        order = self.sell({self.high.pk: "120"})
        rows = StockLedger.objects.filter(movement_type=MT.POS_SALE).order_by("lot__harvested_at")
        self.assertEqual(
            list(rows.values_list("lot_id", "qty_kg", "reference", "timestamp")),
            [
                (self.lot_h1.pk, D("-100"), order.order_number, NOON),
                (self.lot_h2.pk, D("-20"), order.order_number, NOON),
            ],
        )

    def test_fractional_kg_and_rounding(self):
        order = self.sell({self.low.pk: "0.333"})
        line = order.lines.get()
        self.assertEqual(line.qty_kg, D("0.333"))
        self.assertEqual(order.total_revenue_idr, D("2664.00"))
        self.assertEqual(order.total_cogs_idr, D("1332.00"))

    def test_sells_exactly_all_available(self):
        self.sell({self.high.pk: 150})
        self.assertEqual(self.high.stock_kg, D("0"))
        self.assertBalanced()

    def test_price_read_server_side_and_snapshotted(self):
        # Client-supplied price keys are ignored; only product id + kg are read.
        order = self.sell([{"product_id": self.high.pk, "qty_kg": "1", "unit_price_idr": "1"}])
        self.assertEqual(order.lines.get().unit_price_idr, D("20000"))
        self.high.selling_price_idr = D("25000")
        self.high.save()
        line = OrderLineItem.objects.get(order=order)
        self.assertEqual(line.unit_price_idr, D("20000"))
        self.assertEqual(line.subtotal_idr, D("20000"))

    def test_duplicate_products_are_merged(self):
        order = self.sell([(self.high.pk, "2"), (self.high.pk, "3")])
        self.assertEqual(order.lines.get().qty_kg, D("5"))

    def test_skips_empty_lots(self):
        record_spoilage(self.lot_h1, 100, "ROT")
        order = self.sell({self.high.pk: 5})
        self.assertEqual(list(LineLotAllocation.objects.filter(line__order=order).values_list("lot_id", flat=True)), [self.lot_h2.pk])

    def test_insufficient_stock_fails_whole_order(self):
        with self.assertRaises(ValidationError) as ctx:
            self.sell({self.low.pk: 5, self.high.pk: "150.5"})
        msg = " ".join(ctx.exception.messages)
        self.assertIn("Melon Golden · Grade A (HIGH)", msg)
        self.assertIn("tersedia 150 kg", msg)
        self.assertIn("diminta 150,5 kg", msg)
        # Nothing written: the in-stock LOW line was rolled back too.
        self.assertFalse(SaleOrder.objects.exists())
        self.assertFalse(StockLedger.objects.filter(movement_type=MT.POS_SALE).exists())
        self.reload(self.lot_l1)
        self.assertEqual(self.lot_l1.qty_remaining_kg, D("50"))

    def test_reports_every_short_product(self):
        with self.assertRaises(ValidationError) as ctx:
            self.sell({self.low.pk: 51, self.high.pk: 151})
        self.assertEqual(len(ctx.exception.messages), 2)

    def test_out_of_stock_product_reports_zero(self):
        self.sell({self.low.pk: 50})
        with self.assertRaisesMessage(ValidationError, "tersedia 0 kg"):
            self.sell({self.low.pk: 1})

    def test_unpriced_product_rejected(self):
        self.low.selling_price_idr = None
        self.low.save()
        with self.assertRaisesMessage(ValidationError, "belum memiliki harga"):
            self.sell({self.low.pk: 1})

    def test_unknown_product_rejected(self):
        with self.assertRaisesMessage(ValidationError, "tidak ditemukan"):
            self.sell({999999: 1})

    def test_invalid_cart_rejected(self):
        for cart in ({}, [], {self.high.pk: 0}, {self.high.pk: "-1"}, {self.high.pk: "abc"}, [("x", 1)]):
            with self.subTest(cart=cart), self.assertRaises(ValidationError):
                self.sell(cart)
        self.assertFalse(SaleOrder.objects.exists())

    def test_invalid_payment_method(self):
        with self.assertRaisesMessage(ValidationError, "Metode pembayaran"):
            self.sell({self.high.pk: 1}, method="BITCOIN")

    def test_payment_methods(self):
        for method in ("CASH", "QRIS", "TRANSFER"):
            self.assertEqual(self.sell({self.high.pk: 1}, method=method).payment_method, method)

    def test_roles_allowed_to_sell(self):
        for user in (self.owner, self.manager, self.cashier, make_user("root", superuser=True)):
            with self.subTest(user=user.username):
                self.sell({self.high.pk: 1}, user=user)

    def test_worker_and_anonymous_cannot_sell(self):
        for user in (self.worker, None):
            with self.assertRaises(PermissionDenied):
                services.checkout({self.high.pk: 1}, "CASH", user, created_at=NOON)
        self.assertFalse(SaleOrder.objects.exists())

    def test_default_timestamp_is_now(self):
        order = services.checkout({self.high.pk: 1}, "CASH", self.cashier)
        self.assertTrue(order.order_number.startswith(services.order_number_prefix(services.jakarta_date(order.created_at))))


class CatalogTests(PosFixtures):
    def test_hides_unpriced_and_reports_available_kg(self):
        self.low.selling_price_idr = None
        self.low.save()
        other = Variety.objects.create(name="Cabai Rawit", crop="Cabai")
        Product.objects.create(variety=other, grade=Grade.HIGH, selling_price_idr=50000)
        self.sell({self.high.pk: 30})

        catalog = {p.pk: p.available_kg for p in services.pos_catalog()}
        self.assertNotIn(self.low.pk, catalog)
        self.assertEqual(catalog[self.high.pk], D("120"))
        self.assertEqual(len(catalog), 2)
        self.assertEqual(min(catalog.values()), D("0"))


# --- Order numbers (D30) ----------------------------------------------------------


class OrderNumberTests(PosFixtures):
    def test_sequential_within_day(self):
        numbers = [self.sell({self.high.pk: 1}, at=NOON + timedelta(minutes=i)).order_number for i in range(3)]
        self.assertEqual(numbers, ["GT-20261119-0001", "GT-20261119-0002", "GT-20261119-0003"])

    def test_counter_resets_each_day(self):
        self.sell({self.high.pk: 1}, at=NOON)
        self.sell({self.high.pk: 1}, at=NOON)
        order = self.sell({self.high.pk: 1}, at=NOON + timedelta(days=1))
        self.assertEqual(order.order_number, "GT-20261120-0001")

    def test_uses_jakarta_calendar_date(self):
        # 2026-11-19 17:30 UTC is already 2026-11-20 00:30 WIB.
        at = datetime(2026, 11, 19, 17, 30, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(self.sell({self.high.pk: 1}, at=at).order_number, "GT-20261120-0001")
        # 2026-11-19 16:59 UTC is still 23:59 WIB on the 19th.
        at = datetime(2026, 11, 19, 16, 59, tzinfo=ZoneInfo("UTC"))
        self.assertEqual(self.sell({self.high.pk: 1}, at=at).order_number, "GT-20261119-0001")

    def test_failed_checkout_does_not_consume_number(self):
        with self.assertRaises(ValidationError):
            self.sell({self.high.pk: 1000})
        self.assertEqual(self.sell({self.high.pk: 1}).order_number, "GT-20261119-0001")

    def test_voided_orders_keep_their_number(self):
        first = self.sell({self.high.pk: 1})
        services.void_order(first, self.manager, now=NOON, reason="WRONG_ENTRY")
        self.assertEqual(self.sell({self.high.pk: 1}).order_number, "GT-20261119-0002")

    def test_retries_on_duplicate_number(self):
        self.sell({self.high.pk: 1})
        original = services.next_order_number
        calls = []

        def stale_then_fresh(created_at):
            calls.append(created_at)
            return "GT-20261119-0001" if len(calls) == 1 else original(created_at)

        services.next_order_number = stale_then_fresh
        try:
            order = self.sell({self.high.pk: 1})
        finally:
            services.next_order_number = original
        self.assertEqual(order.order_number, "GT-20261119-0002")
        self.assertEqual(len(calls), 2)

    def test_gives_up_after_repeated_collisions(self):
        self.sell({self.high.pk: 1})
        original = services.next_order_number
        services.next_order_number = lambda created_at: "GT-20261119-0001"
        try:
            with self.assertRaisesMessage(ValidationError, "nomor order"):
                self.sell({self.high.pk: 1})
        finally:
            services.next_order_number = original
        self.assertEqual(SaleOrder.objects.count(), 1)
        self.assertBalanced()

    def test_daily_limit(self):
        SaleOrder.objects.create(order_number="GT-20261119-9999", payment_method="CASH", cashier=self.cashier, created_at=NOON)
        with self.assertRaisesMessage(ValidationError, "9999"):
            self.sell({self.high.pk: 1})


# --- Void (rule 18, D19) -----------------------------------------------------------


class VoidTests(PosFixtures):
    def setUp(self):
        super().setUp()
        self.order = self.sell({self.high.pk: 120, self.low.pk: 10})

    def test_restores_exact_lots(self):
        record_spoilage(self.lot_h2, 5, "ROT")  # unrelated movement after the sale
        voided = services.void_order(self.order, self.manager, now=NOON + timedelta(hours=3), reason="WRONG_ENTRY")
        self.reload(self.lot_h1, self.lot_h2, self.lot_l1)

        self.assertEqual(voided.status, SaleOrder.Status.VOIDED)
        self.assertEqual(voided.voided_by, self.manager)
        self.assertEqual(voided.voided_at, NOON + timedelta(hours=3))
        self.assertEqual(self.lot_h1.qty_remaining_kg, D("100"))
        self.assertEqual(self.lot_h2.qty_remaining_kg, D("45"))
        self.assertEqual(self.lot_l1.qty_remaining_kg, D("50"))
        rows = StockLedger.objects.filter(movement_type=MT.POS_VOID, reference=self.order.order_number)
        self.assertEqual(
            sorted(rows.values_list("lot_id", "qty_kg")),
            sorted([(self.lot_h1.pk, D("100")), (self.lot_h2.pk, D("20")), (self.lot_l1.pk, D("10"))]),
        )
        self.assertBalanced()

    def test_keeps_lines_and_allocations_for_traceability(self):
        services.void_order(self.order, self.owner, now=NOON, reason="WRONG_ENTRY")
        self.assertEqual(self.order.lines.count(), 2)
        self.assertEqual(LineLotAllocation.objects.filter(line__order=self.order).count(), 3)

    def test_restored_stock_is_sellable_again_fifo(self):
        services.void_order(self.order, self.owner, now=NOON, reason="WRONG_ENTRY")
        again = self.sell({self.high.pk: 100})
        self.assertEqual(again.lines.get().allocations.get().lot, self.lot_h1)

    def test_only_owner_or_manager(self):
        for user in (self.cashier, self.worker, None):
            with self.assertRaises(PermissionDenied):
                services.void_order(self.order, user, now=NOON, reason="WRONG_ENTRY")
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, SaleOrder.Status.COMPLETED)

    def test_cannot_void_twice(self):
        services.void_order(self.order, self.owner, now=NOON, reason="WRONG_ENTRY")
        with self.assertRaisesMessage(ValidationError, "sudah dibatalkan"):
            services.void_order(self.order, self.owner, now=NOON, reason="WRONG_ENTRY")
        self.assertEqual(StockLedger.objects.filter(movement_type=MT.POS_VOID).count(), 3)
        self.assertBalanced()

    def test_same_jakarta_day_only(self):
        with self.assertRaisesMessage(ValidationError, "hari yang sama"):
            services.void_order(self.order, self.owner, now=datetime(2026, 11, 20, 0, 1, tzinfo=WIB), reason="WRONG_ENTRY")
        self.assertFalse(StockLedger.objects.filter(movement_type=MT.POS_VOID).exists())
        # 23:59 WIB on the same day is still allowed.
        services.void_order(self.order, self.owner, now=datetime(2026, 11, 19, 23, 59, tzinfo=WIB), reason="WRONG_ENTRY")

    def test_can_void_helper(self):
        tomorrow = NOON + timedelta(days=1)
        self.assertTrue(services.can_void(self.order, self.manager, now=NOON))
        self.assertFalse(services.can_void(self.order, self.cashier, now=NOON))
        self.assertFalse(services.can_void(self.order, self.manager, now=tomorrow))
        services.void_order(self.order, self.manager, now=NOON, reason="WRONG_ENTRY")
        self.order.refresh_from_db()
        self.assertFalse(services.can_void(self.order, self.manager, now=NOON))

    def test_sold_lot_blocks_batch_delete(self):
        with self.assertRaises(ValidationError):
            farm.delete_harvest_batch(self.batch1)


class FormatTests(TestCase):
    def test_format_kg(self):
        self.assertEqual(services.format_kg(D("150.000")), "150")
        self.assertEqual(services.format_kg(D("2.500")), "2,5")
        self.assertEqual(services.format_kg(D("0")), "0")


# --- Concurrency (D17, D21) ---------------------------------------------------------


@unittest.skipIf(connection.vendor == "sqlite", "Row locking (select_for_update) requires PostgreSQL.")
class ConcurrentCheckoutTests(TransactionTestCase):
    """Two cashiers race for the last kilogram: exactly one sale may succeed."""

    def setUp(self):
        self.users = [make_user(f"kasir{i}", ROLE_CASHIER) for i in range(2)]
        services.open_shift(self.users[0], 0)
        variety = Variety.objects.create(name="Cabai Rawit", crop="Cabai")
        element = FarmElement.objects.create(
            name="OF-1", element_type=FarmElement.ElementType.OPEN_FIELD, area_m2=D("200")
        )
        cycle = CropCycle.objects.create(
            element=element, variety=variety, planting_start_date=date(2026, 8, 1), stage=CropCycle.Stage.HARVESTING
        )
        self.product = Product.objects.create(variety=variety, grade=Grade.HIGH, selling_price_idr=50000)
        farm.record_harvest(cycle, 1, 0, harvested_at=datetime(2026, 11, 10, tzinfo=WIB))

    def test_two_threads_race_for_last_kg(self):
        barrier = threading.Barrier(2)
        results: list = [None, None]

        def attempt(i):
            try:
                barrier.wait(timeout=10)
                results[i] = services.checkout({self.product.pk: 1}, "CASH", self.users[i])
            except Exception as exc:  # noqa: BLE001 - recorded for assertion
                results[i] = exc
            finally:
                connection.close()

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        orders = [r for r in results if isinstance(r, SaleOrder)]
        errors = [r for r in results if isinstance(r, ValidationError)]
        self.assertEqual(len(orders), 1, results)
        self.assertEqual(len(errors), 1, results)
        self.assertIn("tersedia 0 kg", " ".join(errors[0].messages))

        lot = StockLot.objects.get(product=self.product)
        self.assertEqual(lot.qty_remaining_kg, D("0"))
        self.assertEqual(SaleOrder.objects.count(), 1)
        self.assertEqual(StockLedger.objects.filter(movement_type=MT.POS_SALE).count(), 1)
        self.assertEqual(check_mass_balance(), [])

    def test_concurrent_orders_get_distinct_numbers(self):
        farm_product = self.product
        barrier = threading.Barrier(2)
        results: list = [None, None]
        # Give each thread its own lot product so both succeed.
        other = Product.objects.create(variety=farm_product.variety, grade=Grade.LOW, selling_price_idr=20000)
        cycle = CropCycle.objects.get()
        farm.record_harvest(cycle, 1, 1, harvested_at=datetime(2026, 11, 11, tzinfo=WIB))
        carts = [{farm_product.pk: 1}, {other.pk: 1}]

        def attempt(i):
            try:
                barrier.wait(timeout=10)
                results[i] = services.checkout(carts[i], "CASH", self.users[i])
            except Exception as exc:  # noqa: BLE001
                results[i] = exc
            finally:
                connection.close()

        threads = [threading.Thread(target=attempt, args=(i,)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertTrue(all(isinstance(r, SaleOrder) for r in results), results)
        self.assertEqual(len({r.order_number for r in results}), 2)


class TaxTests(PosFixtures):
    """Rules 19 & 21: tax-inclusive POS lines, snapshotted rate, analytics net of tax."""

    def test_split_inclusive_tax_math(self):
        from inventory.services import exclusive_tax, split_inclusive_tax

        self.assertEqual(split_inclusive_tax(D("111000"), D("11")), (D("11000.00"), D("100000.00")))
        self.assertEqual(split_inclusive_tax(D("15000"), 0), (D("0.00"), D("15000.00")))
        self.assertEqual(split_inclusive_tax(D("10"), D("11")), (D("0.99"), D("9.01")))
        self.assertEqual(exclusive_tax(D("100000"), D("11")), D("11000.00"))
        with self.assertRaises(ValidationError):
            split_inclusive_tax(D("1"), D("101"))

    def test_untaxed_sale_keeps_gross_equal_net(self):
        order = self.sell({self.high.pk: 10})
        self.assertEqual((order.total_gross_idr, order.total_tax_idr, order.total_revenue_idr),
                         (D("200000.00"), D("0.00"), D("200000.00")))

    def test_taxed_line_snapshot_and_totals(self):
        from inventory.models import Tax

        from core.models import FarmProfile
        FarmProfile.objects.update_or_create(pk=1, defaults={"is_pkp": True})
        ppn = Tax.objects.create(name="PPN", rate_pct=D("11"))
        self.high.tax = ppn
        self.high.save()
        order = self.sell({self.high.pk: 10, self.low.pk: 5})
        line = order.lines.get(product=self.high)
        self.assertEqual(line.tax_rate_pct, D("11"))
        self.assertEqual(line.tax_idr, D("19819.82"))  # 200000 x 11 / 111
        self.assertEqual(line.net_revenue_idr, D("180180.18"))
        self.assertEqual(order.total_gross_idr, D("240000.00"))
        self.assertEqual(order.total_tax_idr, D("19819.82"))
        self.assertEqual(order.total_revenue_idr, D("220180.18"))
        ppn.rate_pct = D("12")
        ppn.save()
        line.refresh_from_db()
        self.assertEqual(line.tax_rate_pct, D("11"))  # history unchanged

    def test_non_pkp_farm_ignores_product_tax(self):
        """D80: a non-PKP farm snapshots 0% even when the product has an active tax; history is kept."""
        from core.models import FarmProfile
        from inventory.models import Tax

        self.high.tax = Tax.objects.create(name="PPN", rate_pct=D("11"))
        self.high.save()
        untaxed = self.sell({self.high.pk: 1})
        self.assertEqual((untaxed.total_tax_idr, untaxed.lines.get().tax_rate_pct), (D("0.00"), D("0")))
        FarmProfile.objects.update_or_create(pk=1, defaults={"is_pkp": True})
        self.assertEqual(self.sell({self.high.pk: 1}).lines.get().tax_rate_pct, D("11"))
        FarmProfile.objects.filter(pk=1).update(is_pkp=False)
        self.assertEqual(self.sell({self.high.pk: 1}).total_tax_idr, D("0.00"))
        untaxed.refresh_from_db()
        self.assertEqual(untaxed.total_tax_idr, D("0.00"))

    def test_inactive_tax_is_not_applied(self):
        from inventory.models import Tax

        self.high.tax = Tax.objects.create(name="PPN", rate_pct=D("11"), is_active=False)
        self.high.save()
        self.assertEqual(self.sell({self.high.pk: 1}).total_tax_idr, D("0.00"))


class NumberFormatAndStepTests(PosFixtures):
    """D81: Indonesian quantities at checkout and the per-product kasir step."""

    def test_checkout_accepts_indonesian_and_machine_qty(self):
        from django.urls import reverse

        self.client.force_login(self.cashier)
        self.client.post(reverse("pos:checkout"), {"product_id": [self.high.pk, self.low.pk],
                                                   "qty_kg": ["1,5", "2.25"], "payment_method": "CASH"})
        order = SaleOrder.objects.get()
        self.assertEqual(order.lines.get(product=self.high).qty_kg, D("1.5"))
        self.assertEqual(order.lines.get(product=self.low).qty_kg, D("2.25"))

    def test_price_page_sets_price_and_pos_steps_half_kg(self):
        """Phase 7 (D113): no per-product step; the POS − / + and the first tap are always 0.5 kg."""
        from django.urls import reverse

        self.client.force_login(self.manager)
        self.client.post(reverse("farm:prices"), {"product": self.high.pk, "selling_price_idr": "25.000"})
        self.high.refresh_from_db()
        self.assertEqual(self.high.selling_price_idr, D("25000"))
        self.client.force_login(self.cashier)
        products = self.client.get(reverse("pos:stock"), HTTP_ACCEPT="application/json").json()["products"]
        self.assertNotIn("step", products[0])
        page = self.client.get(reverse("pos:register")).content.decode()
        self.assertIn('data-num="kg"', page)
        self.assertIn("const STEP_KG = 0.5;", page)


class BigLineWarningTests(PosFixtures):
    """D87: the POS warns when a line is > 3x the 30-day average line, never below 20 kg."""

    def test_thresholds(self):
        from django.utils import timezone

        now = timezone.now()
        self.sell({self.high.pk: 10}, at=now)
        self.sell({self.high.pk: 2}, at=now)
        self.sell({self.low.pk: 1}, at=now)
        t = services.pos_big_line_thresholds()
        self.assertEqual(t[self.high.pk], D("20"))  # avg 6 x 3 = 18 → floor 20
        self.sell({self.high.pk: 30}, at=now)
        self.assertEqual(services.pos_big_line_thresholds()[self.high.pk], D("42"))  # avg 14 x 3
        self.client.force_login(self.cashier)
        from django.urls import reverse

        products = self.client.get(reverse("pos:stock"), HTTP_ACCEPT="application/json").json()["products"]
        self.assertEqual({p["id"]: p["warn_kg"] for p in products}[self.low.pk], "20.000")


class ShiftAndCashTests(PosFixtures):
    """D94–D96: void reasons, cash received/change, one open shift, drawer maths, blind close, review."""

    def shift(self):
        return services.current_shift()

    def test_no_sales_without_open_shift(self):
        services.close_shift(self.shift(), self.cashier, {})
        with self.assertRaises(ValidationError):
            self.sell({self.high.pk: 1})
        with self.assertRaises(ValidationError):  # one drawer: a second open shift is refused
            services.open_shift(self.cashier, 0)
            services.open_shift(self.manager, 0)

    def test_cash_received_and_change(self):
        order = services.checkout({self.high.pk: D("1.5")}, "CASH", self.cashier, cash_received="50.000")
        self.assertEqual((order.cash_received_idr, order.change_idr), (D("50000"), D("20000")))
        exact = services.checkout({self.high.pk: 1}, "CASH", self.cashier)
        self.assertEqual(exact.change_idr, D("0"))
        with self.assertRaises(ValidationError):
            services.checkout({self.high.pk: 1}, "CASH", self.cashier, cash_received=10000)
        qris = services.checkout({self.high.pk: 1}, "QRIS", self.cashier, cash_received=99999)
        self.assertIsNone(qris.cash_received_idr)

    def test_void_reason_and_cash_refund_in_current_shift(self):
        order = self.sell({self.high.pk: 2})  # CASH 40.000
        with self.assertRaises(ValidationError):
            services.void_order(order, self.manager, reason="")
        with self.assertRaises(ValidationError):
            services.void_order(order, self.manager, reason="OTHER")
        first = self.shift()
        services.close_shift(first, self.cashier, {})
        with self.assertRaises(ValidationError):  # cash refund needs an open drawer
            services.void_order(order, self.manager, reason="CUSTOMER_CANCELLED", now=NOON)
        second = services.open_shift(self.cashier, 100000)
        services.void_order(order, self.manager, reason="CUSTOMER_CANCELLED", now=NOON)
        refund = second.movements.get()
        self.assertEqual((refund.kind, refund.amount_idr), ("SALE_REFUND", D("40000")))
        self.assertEqual(services.shift_summary(second)["expected"], D("60000"))

    def test_expected_cash_moves_and_blind_close(self):
        from farm.models import Expense, ExpenseCategory

        shift = self.shift()
        self.sell({self.high.pk: 1})  # cash 20.000
        self.sell({self.low.pk: 1}, method="QRIS")  # not in the drawer
        services.add_cash_movement(shift, self.cashier, "TOP_UP", "50.000")
        services.add_cash_movement(shift, self.cashier, "DEPOSIT", 30000, "setor")
        services.add_cash_movement(shift, self.cashier, "EXPENSE", 5000, "tali",
                                   category=ExpenseCategory.objects.get(code="LAINNYA"))
        expense = Expense.objects.get(narration="tali")
        self.assertEqual((expense.status, expense.payment_method, expense.scope), ("PENDING", "CASH", "GENERAL"))
        with self.assertRaises(ValidationError):  # cannot take out more than the drawer holds
            services.add_cash_movement(shift, self.cashier, "DEPOSIT", 999999)
        self.assertEqual(services.shift_summary(shift)["expected"], D("35000"))
        with self.assertRaises(PermissionDenied):  # another cashier cannot close it
            services.close_shift(shift, make_user("kasir2", ROLE_CASHIER), {})
        closed = services.close_shift(shift, self.cashier, {20000: 1, "10000": 1, 2000: 2})
        self.assertEqual((closed.counted_idr, closed.difference_idr), (D("34000"), D("-1000")))
        self.assertTrue(closed.needs_review)
        with self.assertRaises(PermissionDenied):
            services.review_shift(closed, self.cashier, "ok")
        services.review_shift(closed, self.manager, "Uang receh terselip")
        closed.refresh_from_db()
        self.assertFalse(closed.needs_review)
        self.assertEqual(services.suggested_float(), D("34000"))

    def test_manager_force_close(self):
        closed = services.close_shift(self.shift(), self.manager, {})
        self.assertTrue(closed.force_closed)

    def test_invoice_cash_into_drawer(self):
        from invoicing import services as inv

        buyer = inv.create_buyer(self.manager, name="Resto")
        with self.assertRaises(ValidationError):
            inv.record_payment(self.manager, buyer, amount_idr=10000, method="QRIS", paid_on=date(2026, 11, 19),
                               into_drawer=True)
        inv.record_payment(self.manager, buyer, amount_idr=10000, method="CASH", paid_on=date(2026, 11, 19),
                           into_drawer=True)
        self.assertEqual(services.shift_summary(self.shift())["by_kind"]["INVOICE_PAYMENT"], D("10000"))

    def test_shift_page_and_register(self):
        from django.urls import reverse

        self.client.force_login(self.cashier)
        page = self.client.get(reverse("pos:shift")).content.decode()
        self.assertIn("hitung buta", page)  # cashier does not see the expected amount
        self.client.post(reverse("pos:shift"), {"action": "move", "kind": "TOP_UP", "amount": "10.000"})
        self.client.post(reverse("pos:shift"), {"action": "close", "d10000": "1"})
        self.assertIsNone(services.current_shift())
        self.assertContains(self.client.get(reverse("pos:register")), "Shift kasir belum dibuka")
        r = self.client.post(reverse("pos:shift"), {"action": "open", "opening_float": "10.000",
                                                    "next": reverse("pos:register")})
        self.assertRedirects(r, reverse("pos:register"))
        self.assertEqual(services.current_shift().opening_float_idr, D("10000"))
