"""Idempotent demo dataset (D32).

Builds a rich, deterministic ~4-month farm history ending today, with ~60 POS
sales over the last 30 days:

* 3 farm elements, 4 crop cycles (a closed Melon Golden cycle with unabsorbed
  cost, a fresh Melon Golden cycle, and harvesting Cabai Rawit / Kakao
  Forastero cycles);
* input purchases and usage activities (weighted-average cost, INPUT_USAGE
  expenses, one low-stock item);
* manual expenses in every state (approved, pending, rejected, general
  overhead, a cycle-less element expense, cost landing on a closed cycle);
* multiple graded harvest batches (RSV split where both grades are priced,
  equal split where one is not), spoilage, priced and unpriced products;
* completed and voided POS orders;
* wholesale buyers with paid, partly-paid, overdue, voided and draft invoices,
  a PPN rate defined but not charged (non-PKP farm, D80), and a payment that leaves buyer credit.

Every quantity is routed through the domain services in chronological order,
so the result always passes `check_mass_balance`. Reference data (users,
varieties, elements, input items) is located by natural key; the operational
history is written once — a re-run detects it and leaves it untouched.
"""

import os
import random
from datetime import datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from farm import services as farm
from farm.bootstrap import (
    ROLE_CASHIER,
    ROLE_MANAGER,
    ROLE_OWNER,
    ROLE_WORKER,
    ensure_expense_categories,
    ensure_preharvest_checklist,
    ensure_role_groups,
)
from farm.models import (
    ActivityLog,
    CropCycle,
    Expense,
    ExpenseCategory,
    FarmElement,
    HarvestBatch,
    InputItem,
    PreHarvestChecklistItem,
    Unit,
    Variety,
)
from inventory import services as inventory
from inventory.models import Grade, Product, SpoilageRecord
from pos import services as pos
from inventory.models import Tax
from invoicing import services as invoicing
from invoicing.models import Buyer, Invoice
from pos.models import SaleOrder

DEFAULT_DEMO_PASSWORD = "gotani-demo-2026"
D = Decimal

DEMO_USERS = [
    # username, role, first name
    ("owner", ROLE_OWNER, "Pemilik"),
    ("manager", ROLE_MANAGER, "Manajer"),
    ("worker", ROLE_WORKER, "Pekerja"),
    ("cashier", ROLE_CASHIER, "Kasir"),
]

VARIETIES = [
    # name, crop, days to harvest
    ("Melon Golden", "Melon", 75),
    ("Cabai Rawit", "Cabai", 90),
    ("Kakao Forastero", "Kakao", 110),
]

# Cycle estimates (D60): plant count and expected sellable kg.
ESTIMATES = {
    "melon_old": (600, "450"),
    "melon_new": (600, "480"),
    "cabai": (1500, "380"),
    "kakao": (220, "260"),
}

FARM_PROFILE = {"name": "Kebun GoTani", "address": "Jl. Raya Ciapus No. 12, Bogor, Jawa Barat",
                "phone": "0251-555-0123", "latitude": "-6.595000", "longitude": "106.806000"}
# A ~0.05 ha greenhouse footprint near the farm centre (no API call at seed time).
SAMPLE_POLYGON = {"type": "Polygon", "coordinates": [[
    [106.80580, -6.59520], [106.80605, -6.59520], [106.80605, -6.59500], [106.80580, -6.59500], [106.80580, -6.59520],
]]}

ELEMENTS = [
    ("Greenhouse A", FarmElement.ElementType.GREENHOUSE, D("500.00")),
    ("Lahan Terbuka B", FarmElement.ElementType.OPEN_FIELD, D("1200.00")),
    ("Shadenet C", FarmElement.ElementType.SHADENET, D("800.00")),
]

NPK = "NPK Mutiara 16-16-16"
POC = "Pupuk Organik Cair"
ABAMEKTIN = "Insektisida Abamektin"
BENIH_MELON = "Benih Melon Golden"

INPUT_ITEMS = [
    # name, type, unit, min_alert_qty
    (NPK, InputItem.ItemType.FERTILIZER, Unit.KG, D("10.000")),
    (POC, InputItem.ItemType.FERTILIZER, Unit.L, D("5.000")),
    (ABAMEKTIN, InputItem.ItemType.PESTICIDE, Unit.ML, D("200.000")),
    (BENIH_MELON, InputItem.ItemType.SEED, Unit.G, D("50.000")),
]

# (variety, grade) → selling price. Kakao LOW is deliberately left unpriced:
# it stays hidden from the POS and forces the equal-per-kg COGS fallback.
PRICES = {
    ("Melon Golden", Grade.HIGH): D("35000"),
    ("Melon Golden", Grade.LOW): D("18000"),
    ("Cabai Rawit", Grade.HIGH): D("60000"),
    ("Cabai Rawit", Grade.LOW): D("32000"),
    ("Kakao Forastero", Grade.HIGH): D("48000"),
}

S = CropCycle.Stage
A = ActivityLog.ActivityType
GROWTH_STAGES = [S.SPROUTING, S.TRANSPLANTING, S.VEGETATIVE, S.FLOWERING, S.FRUITING, S.HARVESTING]

# Cycle keys used throughout the timeline.
MELON_OLD, MELON_NEW, CABAI, KAKAO = "melon_old", "melon_new", "cabai", "kakao"
CYCLES = {
    # key: (element, variety)
    MELON_OLD: ("Greenhouse A", "Melon Golden"),
    MELON_NEW: ("Greenhouse A", "Melon Golden"),
    CABAI: ("Lahan Terbuka B", "Cabai Rawit"),
    KAKAO: ("Shadenet C", "Kakao Forastero"),
}

SALES_DAYS = 30
ORDERS_PER_DAY = 2
SALE_HOURS = [(9, 15), (15, 40)]
VOID_DAYS = {26, 19, 12, 5}  # first order of these days is voided the same day
CART_KG = [D("0.5"), D("1"), D("1.5"), D("2"), D("2.5"), D("3"), D("4"), D("5")]
PAYMENTS = ["CASH", "CASH", "QRIS", "QRIS", "TRANSFER"]


class Command(BaseCommand):
    help = "Seed an idempotent GoTani demo dataset (≈4 months of farm history, ~60 POS sales)."

    @transaction.atomic
    def handle(self, *args, **options):
        self.now = timezone.now()
        ensure_expense_categories(ExpenseCategory)
        ensure_role_groups(Group)
        self.users = self._seed_users()
        self.varieties = {
            name: Variety.objects.get_or_create(name=name, defaults={"crop": crop, "days_to_harvest": days})[0]
            for name, crop, days in VARIETIES
        }
        ensure_preharvest_checklist(PreHarvestChecklistItem)
        self.elements = {
            name: FarmElement.objects.get_or_create(
                name=name, defaults={"element_type": etype, "area_m2": area}
            )[0]
            for name, etype, area in ELEMENTS
        }
        self.items = {
            name: InputItem.objects.get_or_create(
                name=name,
                defaults={"item_type": itype, "unit": unit, "min_alert_qty": min_alert},
            )[0]
            for name, itype, unit, min_alert in INPUT_ITEMS
        }
        self.categories = {c.code: c for c in ExpenseCategory.objects.all()}
        self._seed_profile_and_map()

        if HarvestBatch.objects.exists() or SaleOrder.objects.exists():
            self.stdout.write("Riwayat operasional sudah ada — dilewati (idempoten).")
        else:
            if CropCycle.objects.exists():
                raise CommandError(
                    "Database berisi siklus tanam tanpa riwayat panen. Kosongkan dulu "
                    "(`python manage.py flush`) lalu jalankan seed_demo lagi."
                )
            self._seed_history()

        errors = inventory.check_mass_balance()
        if errors:
            raise CommandError("seed_demo menghasilkan pelanggaran mass balance: " + "; ".join(errors))
        self.stdout.write(self.style.SUCCESS(self._summary()))

    # ------------------------------------------------------------------
    # Reference data
    # ------------------------------------------------------------------

    def _seed_profile_and_map(self):
        from core.services import get_farm_profile
        from geo.models import FieldPolygon
        from geo.services import clean_geometry

        profile = get_farm_profile()
        if profile.latitude is None:
            for key, value in FARM_PROFILE.items():
                setattr(profile, key, value)
            profile.save()
        if not FieldPolygon.objects.exists():
            geometry, area = clean_geometry(SAMPLE_POLYGON)
            FieldPolygon.objects.create(name="Greenhouse A (batas)", geojson=geometry, area_ha=area,
                                        element=self.elements["Greenhouse A"])

    def _seed_users(self):
        User = get_user_model()
        password = os.environ.get("DEMO_PASSWORD", DEFAULT_DEMO_PASSWORD)
        users = {}
        for username, role, first_name in DEMO_USERS:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    "first_name": first_name,
                    "is_staff": role in (ROLE_OWNER, ROLE_MANAGER),
                    "is_superuser": role == ROLE_OWNER,
                },
            )
            if created:
                user.set_password(password)
                user.save(update_fields=["password"])
            user.groups.add(Group.objects.get(name=role))
            users[role] = user
        return users

    # ------------------------------------------------------------------
    # Timeline helpers
    # ------------------------------------------------------------------

    def at(self, days_ago: int, hour: int = 8, minute: int = 0):
        """Aware Asia/Jakarta datetime `days_ago` days before today, never in the future."""
        day = timezone.localdate(self.now) - timedelta(days=days_ago)
        ts = timezone.make_aware(datetime.combine(day, time(hour, minute)))
        if ts > self.now:
            # Squeeze today's not-yet-reached events into the minutes before now,
            # preserving their relative order.
            ts = self.now - timedelta(minutes=(24 * 60 - hour * 60 - minute) / 10)
        return ts

    def on(self, days_ago, hour, fn, minute=0):
        """Queue `fn(ts)` on the timeline; events run in chronological order."""
        self.events.append((self.at(days_ago, hour, minute), len(self.events), fn))

    def _seed_history(self):
        self.events = []
        self.cycles = {}
        self.rng = random.Random(2026)
        self.order_count = 0

        self._plan_prices()
        self._plan_inputs()
        self._plan_cycles()
        self._plan_expenses()
        self._plan_harvests()
        self._plan_spoilage()
        self._plan_sales()
        self._plan_invoices()

        for _ts, _seq, fn in sorted(self.events, key=lambda e: (e[0], e[1])):
            fn(_ts)

    # ------------------------------------------------------------------
    # Plan: products & inputs
    # ------------------------------------------------------------------

    def _plan_prices(self):
        def set_prices(ts):
            for (variety, grade), price in PRICES.items():
                product, _ = Product.objects.get_or_create(
                    variety=self.varieties[variety], grade=grade
                )
                inventory.set_product_price(product, price)

        self.on(115, 7, set_prices)

    def _plan_inputs(self):
        purchases = [
            # days ago, item, qty, cost per unit, supplier
            (112, NPK, "100", "12000", "Toko Tani Makmur"),
            (112, POC, "40", "25000", "Toko Tani Makmur"),
            (112, ABAMEKTIN, "1000", "450", "CV Agro Sentosa"),
            (112, BENIH_MELON, "200", "1500", "PT Benih Unggul"),
            (60, NPK, "50", "13000", "Toko Tani Makmur"),
            (40, POC, "20", "27000", "Toko Tani Makmur"),
            (30, BENIH_MELON, "100", "1600", "PT Benih Unggul"),
        ]
        for days, item, qty, cost, supplier in purchases:
            self.on(
                days,
                7,
                lambda ts, item=item, qty=qty, cost=cost, supplier=supplier: farm.record_input_purchase(
                    self.items[item], D(qty), D(cost), supplier=supplier, purchased_at=ts,
                    user=self.users[ROLE_MANAGER],
                ),
            )

    # ------------------------------------------------------------------
    # Plan: cycles, stages, activities
    # ------------------------------------------------------------------

    def _start(self, key, days_ago):
        element, variety = CYCLES[key]

        def run(ts):
            planted, expected = ESTIMATES[key]
            self.cycles[key] = farm.start_cycle(
                self.elements[element], self.varieties[variety], timezone.localdate(ts),
                planted_qty=planted, expected_yield_kg=D(expected),
            )

        self.on(days_ago, 6, run)

    def _stages(self, key, start_days_ago, end_days_ago, stages=GROWTH_STAGES):
        step = (start_days_ago - end_days_ago) / max(len(stages) - 1, 1)
        for i, stage in enumerate(stages):
            days = round(start_days_ago - i * step)
            self.on(days, 6, lambda ts, stage=stage: farm.advance_cycle_stage(self.cycles[key], stage, allow_skip=True), 30)

    def _activity(self, key, days_ago, activity_type, item=None, qty="0", method="", notes=""):
        def run(ts):
            input_item = self.items[item] if item else None
            farm.log_activity(
                self.cycles[key],
                activity_type,
                quantity=D(qty),
                unit=input_item.unit if input_item else (Unit.L if activity_type == A.IRRIGATION else ""),
                input_item=input_item,
                application_method=method,
                notes=notes,
                duration_minutes=(45 if activity_type == A.IRRIGATION else None),
                logged_at=ts,
                user=self.users[ROLE_WORKER],
            )

        self.on(days_ago, 7, run, 30)

    def _check(self, key, days_ago, n_items):
        def run(ts):
            items = list(PreHarvestChecklistItem.objects.filter(is_active=True))[:n_items]
            farm.record_preharvest_check(self.cycles[key], items, user=self.users[ROLE_WORKER], recorded_at=ts)

        self.on(days_ago, 7, run)

    def _close(self, key, days_ago):
        def run(ts):
            cycle = farm.close_cycle(self.cycles[key])
            CropCycle.objects.filter(pk=cycle.pk).update(closed_at=ts)  # backdate for the demo

        self.on(days_ago, 17, run)

    def _plan_cycles(self):
        # Closed Melon Golden cycle in Greenhouse A.
        self._start(MELON_OLD, 110)
        self._activity(MELON_OLD, 108, A.SEEDING, BENIH_MELON, "60", "Semai tray")
        self._stages(MELON_OLD, 100, 36)
        for days in (95, 80, 65, 50):
            self._activity(MELON_OLD, days, A.FERTILIZER, NPK, "8", "Kocor")
        for days in (45, 36):
            self._activity(MELON_OLD, days, A.FERTILIZER, POC, "3", "Fertigasi")
        for days in (70, 40):
            self._activity(MELON_OLD, days, A.PESTICIDE, ABAMEKTIN, "100", "Semprot daun")
        for days in (90, 60, 33):
            self._activity(MELON_OLD, days, A.IRRIGATION, qty="1500", method="Tetes")
        self._activity(MELON_OLD, 55, A.SCOUTING, notes="Kutu kebul ringan di sisi timur")
        self._check(MELON_OLD, 37, 5)
        self._close(MELON_OLD, 27)

        # Fresh Melon Golden cycle in the same greenhouse.
        self._start(MELON_NEW, 26)
        self._activity(MELON_NEW, 26, A.SEEDING, BENIH_MELON, "60", "Semai tray")
        self._stages(MELON_NEW, 22, 10, [S.SPROUTING, S.TRANSPLANTING, S.VEGETATIVE])
        self._activity(MELON_NEW, 15, A.FERTILIZER, NPK, "5", "Kocor")
        self._activity(MELON_NEW, 8, A.FERTILIZER, POC, "2", "Fertigasi")
        self._activity(MELON_NEW, 6, A.IRRIGATION, qty="1200", method="Tetes")
        self._activity(MELON_NEW, 3, A.SCOUTING, notes="Pertumbuhan seragam")

        # Cabai Rawit in the open field, harvesting weekly.
        self._start(CABAI, 112)
        self._check(CABAI, 36, 3)
        self._stages(CABAI, 105, 35)
        for days in (100, 85, 70, 55, 40, 25, 10):
            self._activity(CABAI, days, A.FERTILIZER, NPK, "10", "Tugal")
        for days in (50, 30, 12):
            self._activity(CABAI, days, A.FERTILIZER, POC, "4", "Kocor")
        for days in (75, 45, 18, 5):
            self._activity(CABAI, days, A.PESTICIDE, ABAMEKTIN, "150", "Semprot")
        for days in (88, 58, 28, 8):
            self._activity(CABAI, days, A.IRRIGATION, qty="4000", method="Leb")
        for days in (65, 20):
            self._activity(CABAI, days, A.SCOUTING, notes="Pantau thrips dan antraknosa")

        # Kakao Forastero under shade net.
        self._start(KAKAO, 114)
        self._stages(KAKAO, 104, 25)
        for days in (90, 45, 15):
            self._activity(KAKAO, days, A.FERTILIZER, NPK, "6", "Tabur melingkar")
        for days in (60, 20):
            self._activity(KAKAO, days, A.FERTILIZER, POC, "3", "Kocor")
        self._activity(KAKAO, 30, A.PESTICIDE, ABAMEKTIN, "100", "Semprot buah")
        self._activity(KAKAO, 35, A.SCOUTING, notes="Gejala busuk buah minim")

    # ------------------------------------------------------------------
    # Plan: manual expenses
    # ------------------------------------------------------------------

    def _expense(self, days_ago, code, amount, narration, key=None, element=None,
                 decision="approve", vendor="", payment="CASH"):
        def run(ts):
            if key:
                cycle = self.cycles[key]
                scope, el = Expense.Scope.ELEMENT, cycle.element
            elif element:
                cycle, scope, el = None, Expense.Scope.ELEMENT, self.elements[element]
            else:
                cycle, scope, el = None, Expense.Scope.GENERAL, None
            expense = farm.create_expense(
                self.categories[code], scope, D(amount), element=el, cycle=cycle,
                vendor=vendor, narration=narration, date_incurred=timezone.localdate(ts),
                payment_method=payment,
            )
            manager = self.users[ROLE_MANAGER]
            if decision == "approve":
                farm.approve_expense(expense, manager)
            elif decision == "reject":
                farm.reject_expense(expense, manager)
            if decision != "pending":
                Expense.objects.filter(pk=expense.pk).update(approved_at=ts)

        self.on(days_ago, 12, run)

    def _plan_expenses(self):
        e = self._expense
        # Closed melon cycle: the last two land after its final harvest → unabsorbed write-off.
        e(100, "TENAGA_KERJA", "1800000", "Upah tanam & perawatan bulan 1", MELON_OLD)
        e(60, "UTILITAS", "450000", "Listrik pompa greenhouse", MELON_OLD, payment="TRANSFER")
        e(31, "TENAGA_KERJA", "650000", "Upah panen tahap 1", MELON_OLD)
        e(28, "PERALATAN", "380000", "Tali & klip lanjaran", MELON_OLD, vendor="Toko Tani Makmur")
        e(25, "LAINNYA", "220000", "Pembersihan lahan pasca panen", MELON_OLD)  # after close
        # Fresh melon cycle: one approved, one awaiting approval.
        e(20, "TENAGA_KERJA", "500000", "Upah pindah tanam", MELON_NEW)
        e(3, "PERALATAN", "275000", "Mulsa plastik perak", MELON_NEW, decision="pending",
          vendor="CV Agro Sentosa", payment="QRIS")
        # Cabai: set-up costs plus weekly picking labor (incremental cost pool).
        e(105, "PERALATAN", "600000", "Mulsa & ajir", CABAI, vendor="CV Agro Sentosa")
        e(90, "TENAGA_KERJA", "1200000", "Upah olah tanah & tanam", CABAI)
        for days in (24, 17, 10, 4):
            e(days, "TENAGA_KERJA", "300000", "Upah petik mingguan", CABAI)
        e(9, "LAINNYA", "150000", "Konsumsi pekerja (dobel input)", CABAI, decision="reject")
        # Kakao: incl. a cycle-less element expense that attaches to the active cycle.
        e(80, "TENAGA_KERJA", "1200000", "Pemangkasan & sanitasi kebun", KAKAO)
        e(40, "UTILITAS", "300000", "Air irigasi", KAKAO, payment="TRANSFER")
        e(12, "PERALATAN", "260000", "Gunting pangkas & karung", element="Shadenet C")
        # General overhead — never enters COGS.
        e(28, "UTILITAS", "750000", "Listrik & internet kantor", payment="TRANSFER")
        e(15, "LAINNYA", "300000", "Administrasi & ATK", payment="QRIS")
        e(1, "UTILITAS", "780000", "Listrik & internet kantor", payment="TRANSFER")

    # ------------------------------------------------------------------
    # Plan: harvests & spoilage
    # ------------------------------------------------------------------

    def _harvest(self, key, days_ago, high, low, loss, bags=None, per_bag=None):
        def run(ts):
            farm.record_harvest(
                self.cycles[key], D(high), D(low), D(loss),
                number_of_bags=bags, weight_per_bag_kg=D(per_bag) if per_bag else None,
                harvested_at=ts, user=self.users[ROLE_WORKER],
            )

        self.on(days_ago, 6, run, 45)

    def _plan_harvests(self):
        self._harvest(MELON_OLD, 34, "180", "60", "8")
        self._harvest(MELON_OLD, 29, "140", "50", "6")
        for days, high, low, loss in (
            (28, "70", "25", "3"), (21, "55", "20", "2.5"), (14, "60", "22", "2"),
            (7, "50", "17", "1.5"), (2, "42", "15", "1"),
        ):
            self._harvest(CABAI, days, high, low, loss, bags=None)
        self._harvest(KAKAO, 20, "85", "30", "4", bags=23, per_bag="5")
        self._harvest(KAKAO, 6, "70", "25", "3", bags=19, per_bag="5")

    def _spoil(self, days_ago, variety, grade, qty, reason, notes):
        def run(ts):
            product = Product.objects.get(variety=self.varieties[variety], grade=grade)
            lot = inventory.oldest_open_lot(product)
            if lot is None:
                return
            inventory.record_spoilage(
                lot, min(D(qty), lot.qty_remaining_kg), reason, notes=notes,
                user=self.users[ROLE_WORKER], recorded_at=ts,
            )

        self.on(days_ago, 18, run)

    def _plan_spoilage(self):
        R = SpoilageRecord.Reason
        self._spoil(25, "Melon Golden", Grade.HIGH, "4", R.ROT, "Busuk pangkal buah")
        self._spoil(19, "Melon Golden", Grade.LOW, "2.5", R.PHYSICAL_DAMAGE, "Retak saat bongkar muat")
        self._spoil(10, "Cabai Rawit", Grade.HIGH, "1.5", R.SHRINKAGE, "Layu di gudang")
        self._spoil(9, "Cabai Rawit", Grade.LOW, "2", R.ROT, "Busuk lunak")
        self._spoil(4, "Kakao Forastero", Grade.LOW, "2", R.PEST, "Penggerek buah kakao")
        self._spoil(3, "Melon Golden", Grade.HIGH, "3", R.ROT, "Busuk pangkal buah")

    # ------------------------------------------------------------------
    # Plan: POS sales & voids
    # ------------------------------------------------------------------

    def _plan_sales(self):
        for days in range(SALES_DAYS - 1, -1, -1):
            for n, (hour, minute) in enumerate(SALE_HOURS[:ORDERS_PER_DAY]):
                void = n == 0 and days in VOID_DAYS
                self.on(days, hour, lambda ts, void=void: self._sell(ts, void), minute)

    def _sell(self, ts, void):
        catalog = [p for p in pos.pos_catalog() if p.available_kg >= D("0.5")]
        if not catalog:
            return
        picks = self.rng.sample(catalog, k=min(len(catalog), self.rng.choice([1, 1, 2, 2, 3])))
        cart = {
            p.pk: min(self.rng.choice(CART_KG), p.available_kg) for p in picks
        }
        order = pos.checkout(cart, self.rng.choice(PAYMENTS), self.users[ROLE_CASHIER], created_at=ts)
        self.order_count += 1
        if void:
            pos.void_order(order, self.users[ROLE_MANAGER], now=ts + timedelta(minutes=20))

    # ------------------------------------------------------------------
    # Plan: wholesale invoices & payments (P8)
    # ------------------------------------------------------------------

    BUYERS = [
        # key, name, company, phone, terms
        ("resto", "Ibu Sari", "Resto Sari Rasa", "0812-1111-2222", 14),
        ("hotel", "Pak Hendra", "Hotel Puncak Asri", "0813-3333-4444", 30),
        ("pasar", "Pak Budi", "Lapak Pasar Induk", "0815-5555-6666", 7),
    ]

    def _product(self, variety, grade):
        return Product.objects.get(variety=self.varieties[variety], grade=grade)

    def _invoice(self, days_ago, key, buyer, lines, issue=True, **terms):
        """`lines`: [(variety, grade, kg, price-or-None)]; kg is capped to stock at that moment."""
        def run(ts):
            manager = self.users[ROLE_MANAGER]
            raw = []
            for variety, grade, kg, price in lines:
                product = self._product(variety, grade)
                qty = min(D(kg), product.stock_kg)
                if qty > 0:
                    raw.append((product, qty, D(price) if price else None))
            if not raw:
                return
            invoice = invoicing.save_draft(manager, self.buyers[buyer], raw, **terms)
            Invoice.objects.filter(pk=invoice.pk).update(created_at=ts)
            if issue:
                invoice = invoicing.issue_invoice(invoice, manager, issue_date=timezone.localdate(ts), now=ts)
            self.invoices[key] = invoice

        self.on(days_ago, 11, run)

    def _pay(self, days_ago, buyer, method, amount_fn, allocate_to=()):
        def run(ts):
            invoices = [self.invoices[k] for k in allocate_to if k in self.invoices]
            for inv in invoices:
                inv.refresh_from_db()
            amount, allocations = amount_fn(invoices)
            invoicing.record_payment(self.users[ROLE_MANAGER], self.buyers[buyer], amount_idr=amount, method=method,
                                     paid_on=timezone.localdate(ts), reference="Demo", allocations=allocations)

        self.on(days_ago, 16, run)

    def _plan_invoices(self):
        self.invoices = {}

        def setup(ts):
            self.buyers = {
                key: Buyer.objects.get_or_create(
                    name=name, defaults={"company": company, "phone": phone, "payment_terms_days": terms})[0]
                for key, name, company, phone, terms in self.BUYERS
            }
            # D80: the demo farm is an individual, non-PKP farm, so it charges no PPN. The PPN rate
            # stays defined (inactive use) so switching PKP on in Pengaturan is a one-step demo.
            Tax.objects.get_or_create(name="PPN", defaults={"rate_pct": D("11")})

        self.on(25, 7, setup)
        self._invoice(24, "resto_paid", "resto", [("Melon Golden", Grade.HIGH, "20", None),
                                                    ("Melon Golden", Grade.LOW, "10", "17000")])
        self._invoice(18, "hotel_partial", "hotel", [("Cabai Rawit", Grade.HIGH, "15", "58000"),
                                                       ("Kakao Forastero", Grade.HIGH, "10", None)],
                      discount_type="PERCENT", discount_value=5, shipping_idr=50000, notes="Kirim ke dapur utama.")
        self._invoice(16, "pasar_overdue", "pasar", [("Cabai Rawit", Grade.LOW, "20", None)])
        self._invoice(10, "pasar_void", "pasar", [("Melon Golden", Grade.LOW, "8", None)])
        self.on(8, 10, lambda ts: "pasar_void" in self.invoices and invoicing.void_invoice(
            self.invoices["pasar_void"], self.users[ROLE_MANAGER], now=ts))
        self._invoice(2, "resto_draft", "resto", [("Cabai Rawit", Grade.HIGH, "5", None)], issue=False,
                      discount_type="AMOUNT", discount_value=10000)
        # Overpays the first invoice → Rp 100.000 buyer credit.
        self._pay(12, "resto", "TRANSFER", lambda inv: (inv[0].total_idr + 100000, [(inv[0], inv[0].total_idr)])
                  if inv else (D("100000"), []), allocate_to=["resto_paid"])
        self._pay(5, "hotel", "TRANSFER", lambda inv: (D("500000"), [(inv[0], min(D("500000"), inv[0].total_idr))])
                  if inv else (D("500000"), []), allocate_to=["hotel_partial"])

    # ------------------------------------------------------------------

    def _summary(self) -> str:
        orders = SaleOrder.objects
        return (
            f"seed_demo OK: {len(self.users)} pengguna, {FarmElement.objects.count()} elemen, "
            f"{CropCycle.objects.count()} siklus, {HarvestBatch.objects.count()} panen, "
            f"{Product.objects.count()} produk "
            f"({Product.objects.filter(selling_price_idr__isnull=True).count()} tanpa harga), "
            f"{Expense.objects.count()} biaya, {SpoilageRecord.objects.count()} susut, "
            f"{orders.count()} order ({orders.filter(status=SaleOrder.Status.VOIDED).count()} dibatalkan), "
            f"{Buyer.objects.count()} pembeli, {Invoice.objects.count()} invoice."
        )
