# Project: GoTani — Seed-to-Sale Agri-Production & Retail POS System

## Overview
An engineering programming class project modeling end-to-end agricultural production, three-tier post-harvest grading, lot-level mass-balance inventory, and retail POS checkout in Python and Django. Single farm. Decision rationale lives in `docs/DECISIONS.md` (D1–D41); do not silently deviate from it.

**Grading priorities (in order):** domain correctness + automated tests → deployment → UI polish. Out of scope: multi-farm tenancy, dryers, RFQs/buyers, discounts, partial refunds, grade downgrades (HIGH→LOW), bag/per-piece selling, unit conversion, Excel export.

## Tech Stack
- Python 3.12+, Django 5.2+ (or 6.x), PostgreSQL 16 (automatic SQLite fallback when `DATABASE_URL` is not set)
- Frontend: Django Templates + Tailwind CSS (CDN, accepted non-production compromise) + Alpine.js (CDN) + Chart.js (CDN)
- Production: Gunicorn + WhiteNoise + Caddy reverse proxy on Ubuntu 24.04 LTS (Azure VM), PostgreSQL on the same VM
- `TIME_ZONE = "Asia/Jakarta"`, `USE_TZ = True`, `LANGUAGE_CODE = "id"` — UI text in Bahasa Indonesia; code, identifiers, and enums in English

## Units & Money
- Quantities: kg only, `Decimal(10,3)`. Inputs use their own fixed `unit`; no conversion — an activity's unit must equal its `InputItem.unit`.
- Money: `Decimal(14,2)` everywhere (including unit COGS). Round to whole Rupiah with `ROUND_HALF_UP` only for display and receipt totals. Format as `Rp 15.000`.

## Django Apps & Data Models

### 1. `farm` (Upstream Production)
- `Variety`: `name` (unique, e.g., Melon Golden, Cabai Rawit, Kakao Forastero), `crop`
- `FarmElement`: `name`, `element_type` (GREENHOUSE / OPEN_FIELD / HYDROPONIC / SCREEN_HOUSE / SHADENET), `area_m2`, `is_active`
- `CropCycle`: `element` (FK), `variety` (FK Variety), `stage` (PLANTING / SPROUTING / TRANSPLANTING / VEGETATIVE / FLOWERING / FRUITING / HARVESTING / CLEARING), `planting_start_date`, `is_active`, `closed_at` (nullable)
  - Constraint: `UniqueConstraint(fields=["element"], condition=Q(is_active=True))` — at most one active cycle per element.
- `InputItem`: `name`, `item_type` (FERTILIZER / PESTICIDE / SEED), `unit`, `stock_qty` (Decimal), `avg_cost_per_unit_idr` (Decimal), `min_alert_qty` (Decimal)
  - Constraint: `CheckConstraint(stock_qty >= 0)`
- `InputPurchase`: `item` (FK), `qty` (Decimal > 0), `cost_per_unit_idr` (Decimal > 0), `supplier`, `purchased_at`, `recorded_by` — inventory asset purchase; shown in cash-out report, NOT in P&L.
- `InputLedger`: `item` (FK), `movement_type` (PURCHASE / USAGE / USAGE_REVERSAL), `qty` (signed Decimal), `reference`, `timestamp` — append-only.
- `ActivityLog`: `cycle` (FK), `activity_type` (IRRIGATION / FERTILIZER / PESTICIDE / SCOUTING), `stage_at_activity` (snapshot of cycle stage), `input_item` (nullable FK), `quantity` (Decimal), `unit` (L / mL / KG / G), `application_method`, `notes`, `logged_at`, `recorded_by`
- `ExpenseCategory`: `code` (unique), `name` — admin-editable, seeded with BIBIT, PUPUK, PESTISIDA, TENAGA_KERJA, UTILITAS, PERALATAN, LAINNYA.
- `Expense`: `category` (FK ExpenseCategory), `scope` (ELEMENT / GENERAL), `element` (nullable FK), `cycle` (nullable FK), `amount_idr` (Decimal > 0), `vendor`, `narration`, `date_incurred`, `payment_method` (CASH / QRIS / TRANSFER), `receipt` (optional FileField), `status` (PENDING / APPROVED / REJECTED), `source` (MANUAL / INPUT_USAGE), `source_activity` (nullable FK ActivityLog), `absorbed_by` (nullable FK HarvestBatch), `approved_by`, `approved_at`, `created_at`
- `HarvestBatch`: `cycle` (FK), `high_quality_kg`, `low_quality_kg`, `harvest_loss_kg` (Decimal), `number_of_bags` (optional), `weight_per_bag_kg` (optional), `absorbed_cost_idr` (Decimal), `unit_cogs_high_idr`, `unit_cogs_low_idr` (Decimal), `harvested_at`, `recorded_by`

### 2. `inventory` (Lot-Level Mass-Balance Ledger)
- `Product`: `variety` (FK), `grade` (HIGH / LOW), `selling_price_idr` (nullable Decimal) — sellable catalog entry. `UniqueConstraint(variety, grade)`. `stock_kg` is derived (Σ open lots), never stored.
- `StockLot`: `product` (FK), `harvest_batch` (FK), `qty_initial_kg`, `qty_remaining_kg` (Decimal), `unit_cogs_idr` (Decimal), `harvested_at`
  - Constraint: `CheckConstraint(qty_remaining_kg >= 0)` — DB-level oversell backstop.
- `SpoilageRecord`: `lot` (FK), `qty_kg` (> 0), `reason` (ROT / PHYSICAL_DAMAGE / PEST / SHRINKAGE / OTHER), `notes`, `loss_idr` (= qty × lot.unit_cogs_idr), `recorded_by`, `recorded_at`
- `StockLedger`: `lot` (FK), `movement_type` (HARVEST_IN + / POS_SALE − / SPOILAGE − / POS_VOID +), `qty_kg` (signed Decimal), `reference`, `timestamp` — append-only (no update/delete except cascade from an allowed HarvestBatch delete).

### 3. `pos` (Downstream Retail Checkout)
- `SaleOrder`: `order_number` (unique, `GT-YYYYMMDD-####`, daily counter in Asia/Jakarta date), `payment_method` (CASH / QRIS / TRANSFER), `total_revenue_idr`, `total_cogs_idr` (Decimal), `status` (COMPLETED / VOIDED), `cashier`, `created_at`, `voided_by`, `voided_at`
- `OrderLineItem`: `order` (FK), `product` (FK), `qty_kg`, `unit_price_idr` (server snapshot), `unit_cogs_idr` (weighted avg of allocations)
- `LineLotAllocation`: `line` (FK), `lot` (FK), `qty_kg`, `unit_cogs_idr`

## Business Rules (enforce in `services.py`, test each)

### Farm & cost pool
1. `CropCycle.stage` only advances forward in the listed order; backward transitions are rejected.
2. A `HarvestBatch` may be recorded only when `cycle.stage == HARVESTING`. Validate `high_quality_kg > 0`; `low_quality_kg, harvest_loss_kg >= 0`.
3. **Incremental cost pool:** on save, a batch absorbs every APPROVED expense of its cycle with `absorbed_by IS NULL` (set `absorbed_by = batch`). Absorbed cost is never re-spread; earlier lots' COGS never change. First-harvest cost skew is an accepted, documented limitation.
4. **Grade split — relative sales value:** cost share per grade ∝ `kg × selling_price`. If either grade's product lacks a price, fall back to equal per-kg COGS. `unit_cogs_<grade> = share / kg_of_grade`.
5. **Zero-yield:** if `high + low == 0`, absorb nothing (costs stay in pool), create no lots, unit COGS = 0. No division by zero.
6. On save, create one `StockLot` per grade with kg > 0 (auto-create `Product(variety, grade)` with `selling_price_idr = NULL` if missing) and log `HARVEST_IN`.
7. **Cycle-less expenses:** an ELEMENT expense without a cycle attaches to that element's active cycle at approval; if none, it is overhead. GENERAL expenses never enter COGS — they are operating overhead in analytics.
8. **Cycle close** (stage CLEARING / `is_active=False`): remaining unabsorbed approved expenses are reported as "unabsorbed cycle cost" (write-off). Approving an expense on a closed cycle is allowed and lands there.
9. **Expense lifecycle:** only OWNER/MANAGER approve/reject. APPROVED-but-unabsorbed may be reverted to PENDING; absorbed expenses are immutable. `INPUT_USAGE` expenses are created APPROVED and are not editable in the expense UI.
10. **HarvestBatch is never edited in place.** Delete is allowed only if none of its lots have movements other than `HARVEST_IN`; delete un-absorbs its expenses in the same transaction.
11. **Inputs:** purchase updates `avg_cost = (stock×avg + qty×cost) / (stock+qty)` (if stock is 0, avg = purchase cost). FERTILIZER/PESTICIDE activity with an `input_item` deducts stock (reject if insufficient) and creates an APPROVED `INPUT_USAGE` cycle expense of `qty × avg_cost` (category PUPUK/PESTISIDA; SEED → BIBIT). Deleting that activity reverses stock and deletes the expense only while unabsorbed. A purchase cannot be deleted once usage of that item occurred after it. Low-stock alert when `stock_qty <= min_alert_qty`.
12. A `FarmElement` cannot be deactivated while it has an active cycle.

### Inventory
13. **All stock mutations go through** `inventory.services.move_lot_stock()` and `farm.services.move_input_stock()`: `select_for_update` the row, validate, mutate, append ledger. Nothing else touches quantities.
14. **Spoilage** targets a specific lot (UI preselects the oldest); cost is `qty × lot.unit_cogs_idr`, reported as spoilage loss. Lot unit COGS is unchanged.
15. **Invariants** (tests + `python manage.py check_mass_balance`): `lot.qty_remaining_kg == Σ StockLedger.qty_kg` per lot; `lot.qty_initial_kg` matches its batch grade kg; no negative stock; input `stock_qty == Σ InputLedger.qty`.

### POS
16. Cart lives in the browser (Alpine.js) — no reservation. Checkout posts product IDs + kg only; prices are read server-side from `Product.selling_price_idr` (client prices ignored). Products with no price are hidden from the POS.
17. Checkout runs inside `@transaction.atomic`: lock all open lots of the cart's products with `select_for_update()` ordered by `(product_id, harvested_at, id)`; validate total available ≥ requested per product (else fail the whole order with a message naming product and available kg); allocate FIFO into `LineLotAllocation`; log `POS_SALE`; assign `order_number` inside the transaction; render a printable receipt (HTML + print CSS, 58 mm thermal and A4).
18. **Void:** whole order only, OWNER/MANAGER, same Asia/Jakarta calendar day; restores exact kg to the exact lots via allocations, logs `POS_VOID`, sets status VOIDED. No partial refunds.

## Roles (Django Groups + `RoleRequiredMixin`, 403 tested)
| Role | Access |
|---|---|
| OWNER | Everything + user management |
| MANAGER | Everything incl. expense approval and voids |
| WORKER | Farm ops (activities, harvests, spoilage); no financial figures |
| CASHIER | POS register and receipts only |

## UI Screens
- **Farm Operations:** elements, cycles + stage advance, activity log, input inventory/purchases, expenses + approval queue, harvest entry, spoilage entry, "set price" list for unpriced products.
- **POS Register:** product grid with available kg, Alpine cart, checkout, receipt, void.
- **Traceability Report:** forward (cycle → activities → absorbed expenses per batch → lots → spoilage → sales) and backward (receipt line → lots → batch → cycle → element). Export: print-CSS PDF and CSV (`csv` module).
- **Analytics Dashboard** (date-range filter, Chart.js): revenue, COGS, gross margin & %, spoilage loss, unabsorbed cycle cost, general overhead, net; margin by variety × grade; sellable kg/m² by element type; top products; expense breakdown by category.

## Architecture & Conventions
- Keep `views.py` thin; all domain math, COGS propagation, and stock mutations live in each app's `services.py`.
- Register all models in `admin.py` for back-office setup.
- `python manage.py seed_demo` (idempotent): 3 elements, 4 cycles (Melon Golden, Cabai Rawit, Kakao Forastero), input purchases/usage, multiple harvests, spoilage, ~60 sales over 30 days; must pass `check_mass_balance`.

## Testing
- Always write and run `python manage.py test` after implementing each phase.
- Service tests: `TestCase` (SQLite OK). Concurrency: one `TransactionTestCase` with two threads racing for the last kg — PostgreSQL only (skip on SQLite).
- GitHub Actions CI with a Postgres 16 service container. Target ≥ 85% coverage on `services.py`.

## Build Phases
1. Models, constraints, migrations, admin, `seed_demo` skeleton
2. Farm services: stage transitions, inputs + avg cost, expense approval, cost pool, RSV grade split
3. Inventory: lots, `move_lot_stock`, spoilage, ledger, `check_mass_balance`
4. POS: locked FIFO checkout, order numbers, receipt, void, Postgres concurrency test
5. Traceability (forward/backward), analytics dashboard, role access
6. Full `seed_demo`, CI, deployment

## Azure Ubuntu VM Deployment (`deploy/`)
- Read `DATABASE_URL`, `SECRET_KEY`, `DEBUG`, and `ALLOWED_HOSTS` from `.env` via `python-dotenv` and `dj-database-url`.
- Include `deploy/setup_vm.sh`, `deploy/gotani.service`, and `deploy/Caddyfile`.
- PostgreSQL 16 on the same VM. Reserve a **static public IP**; DuckDNS hostname points to it, plus a 5-minute DuckDNS update cron in `setup_vm.sh` as a safety net. Caddy provides automatic HTTPS for the DuckDNS domain.
- Nightly `pg_dump` cron with 7-day retention.
