# Project: GoTani — Seed-to-Sale Agri-Production & Retail POS System

## Overview
An engineering programming class project modeling end-to-end agricultural production, three-tier post-harvest grading, lot-level mass-balance inventory, retail POS checkout, and wholesale invoicing in Python and Django. Single farm. Decision rationale lives in `docs/DECISIONS.md` (D1–D78); do not silently deviate from it.

**Direction (D42):** grow GoTani toward a real product, borrowing FarmCloud's UX and field features, while keeping domain correctness + tests as the bar for every feature. **Zero-cost rule (D43):** nothing may require the developer to pay — no paid APIs, no payment gateways, no subscriptions; only free, no-card services (Open-Meteo, Agromonitoring free tier). Azure VM runs on student credit.

**Out of scope:** multi-farm tenancy, dryers, IoT devices, RFQs/quotations/marketplace, Stripe/plans/plan gating, admin/superadmin portals, self-signup/OTP/email password reset, per-user finance toggle, seedling lots, POS discounts, refunds of any kind (POS partial refunds, buyer credit cash-out), grade downgrades (HIGH→LOW), bag/per-piece selling, unit conversion, multi-currency, Excel export, offline data entry, ratings.

## Tech Stack
- Python 3.12+, Django 5.2+ (or 6.x), PostgreSQL 16 (automatic SQLite fallback when `DATABASE_URL` is not set)
- Frontend: Django Templates + Tailwind CSS (CDN, accepted non-production compromise) + Alpine.js (CDN) + Chart.js (CDN) + Leaflet + Leaflet.draw (CDN, Esri World Imagery tiles)
- Python deps added for later phases: `shapely` (polygon validation/area), `Pillow` (photos, logo), `requests` (external APIs)
- Production: Gunicorn + WhiteNoise + Caddy reverse proxy on Ubuntu 24.04 LTS (Azure VM), PostgreSQL on the same VM
- `TIME_ZONE = "Asia/Jakarta"`, `USE_TZ = True`, `LANGUAGE_CODE = "id"` — UI text in Bahasa Indonesia; code, identifiers, and enums in English

## Units & Money
- Quantities: kg only, `Decimal(10,3)`. Inputs use their own fixed `unit`; no conversion — an activity's unit must equal its `InputItem.unit`. Single exception: `CropCycle.planted_qty` is a plant **count** (D60).
- Money: `Decimal(14,2)` everywhere (including unit COGS and tax). Round to whole Rupiah with `ROUND_HALF_UP` only for display, receipt and invoice totals. Format as `Rp 15.000`. IDR only.

## Django Apps & Data Models

### 0. `core` (Farm profile, staff, shell)
- `FarmProfile` (singleton, pk=1): `name`, `address`, `phone`, `latitude`, `longitude` (nullable Decimal), `logo` (optional ImageField) — used by weather, map centre, invoice/receipt header. OWNER edits in Pengaturan.
- `UserProfile` (one-to-one User): `must_change_password` (bool), `notifications_seen_at` (nullable datetime)

### 1. `farm` (Upstream Production)
- `Variety`: `name` (unique, e.g., Melon Golden, Cabai Rawit, Kakao Forastero), `crop`, `days_to_harvest` (nullable positive int)
- `FarmElement`: `name`, `element_type` (GREENHOUSE / OPEN_FIELD / HYDROPONIC / SCREEN_HOUSE / SHADENET), `area_m2`, `is_active`
- `CropCycle`: `element` (FK), `variety` (FK Variety), `stage` (PLANTING / SPROUTING / TRANSPLANTING / VEGETATIVE / FLOWERING / FRUITING / HARVESTING / CLEARING), `planting_start_date`, `planted_qty` (nullable positive int, plant count), `expected_harvest_date` (nullable; defaults to start + `variety.days_to_harvest`), `expected_yield_kg` (nullable Decimal), `is_active`, `closed_at` (nullable)
  - Constraint: `UniqueConstraint(fields=["element"], condition=Q(is_active=True))` — at most one active cycle per element.
- `InputItem`: `name`, `item_type` (FERTILIZER / PESTICIDE / SEED), `unit`, `stock_qty` (Decimal), `avg_cost_per_unit_idr` (Decimal), `min_alert_qty` (Decimal)
  - Constraint: `CheckConstraint(stock_qty >= 0)`
- `InputPurchase`: `item` (FK), `qty` (Decimal > 0), `cost_per_unit_idr` (Decimal > 0), `supplier`, `purchased_at`, `recorded_by` — inventory asset purchase; shown in cash-out report, NOT in P&L.
- `InputLedger`: `item` (FK), `movement_type` (PURCHASE / USAGE / USAGE_REVERSAL), `qty` (signed Decimal), `reference`, `timestamp` — append-only.
- `ActivityLog`: `cycle` (FK), `activity_type` (IRRIGATION / FERTILIZER / PESTICIDE / SCOUTING / SEEDING), `stage_at_activity` (snapshot of cycle stage), `input_item` (nullable FK), `quantity` (Decimal), `unit` (L / mL / KG / G), `application_method`, `duration_minutes` (nullable; IRRIGATION), `photo` (optional ImageField; SCOUTING), `notes`, `logged_at`, `recorded_by`
- `PreHarvestChecklistItem`: `text`, `order`, `is_active` — admin-editable.
- `PreHarvestCheck`: `cycle` (FK), `checked_items` (M2M PreHarvestChecklistItem), `completed_pct` (Decimal snapshot), `recorded_by`, `recorded_at` — several per cycle allowed; the latest counts.
- `ExpenseCategory`: `code` (unique), `name` — admin-editable, seeded with BIBIT, PUPUK, PESTISIDA, TENAGA_KERJA, UTILITAS, PERALATAN, LAINNYA.
- `Expense`: `category` (FK ExpenseCategory), `scope` (ELEMENT / GENERAL), `element` (nullable FK), `cycle` (nullable FK), `amount_idr` (Decimal > 0), `vendor`, `narration`, `date_incurred`, `payment_method` (CASH / QRIS / TRANSFER), `receipt` (optional FileField), `status` (PENDING / APPROVED / REJECTED), `source` (MANUAL / INPUT_USAGE), `source_activity` (nullable FK ActivityLog), `absorbed_by` (nullable FK HarvestBatch), `approved_by`, `approved_at`, `created_at`
- `HarvestBatch`: `cycle` (FK), `high_quality_kg`, `low_quality_kg`, `harvest_loss_kg` (Decimal), `number_of_bags` (optional), `weight_per_bag_kg` (optional), `absorbed_cost_idr` (Decimal), `unit_cogs_high_idr`, `unit_cogs_low_idr` (Decimal), `harvested_at`, `recorded_by`

### 2. `inventory` (Lot-Level Mass-Balance Ledger)
- `Tax`: `name` (e.g. PPN), `rate_pct` (Decimal(5,2), 0–100), `is_active` — admin-editable.
- `Product`: `variety` (FK), `grade` (HIGH / LOW), `selling_price_idr` (nullable Decimal), `tax` (nullable FK Tax; default none — fresh produce is generally PPN-exempt) — sellable catalog entry. `UniqueConstraint(variety, grade)`. `stock_kg` is derived (Σ open lots), never stored.
- `StockLot`: `product` (FK), `harvest_batch` (FK), `qty_initial_kg`, `qty_remaining_kg` (Decimal), `unit_cogs_idr` (Decimal), `harvested_at`
  - Constraint: `CheckConstraint(qty_remaining_kg >= 0)` — DB-level oversell backstop.
- `SpoilageRecord`: `lot` (FK), `qty_kg` (> 0), `reason` (ROT / PHYSICAL_DAMAGE / PEST / SHRINKAGE / OTHER), `notes`, `loss_idr` (= qty × lot.unit_cogs_idr), `recorded_by`, `recorded_at`
- `StockLedger`: `lot` (FK), `movement_type` (HARVEST_IN + / POS_SALE − / SPOILAGE − / POS_VOID + / INVOICE_SALE − / INVOICE_VOID +), `qty_kg` (signed Decimal), `reference`, `timestamp` — append-only (no update/delete except cascade from an allowed HarvestBatch delete).

### 3. `pos` (Downstream Retail Checkout)
- `SaleOrder`: `order_number` (unique, `GT-YYYYMMDD-####`, daily counter in Asia/Jakarta date), `payment_method` (CASH / QRIS / TRANSFER), `total_gross_idr` (what the customer pays), `total_tax_idr`, `total_revenue_idr` (net of tax), `total_cogs_idr` (Decimal), `status` (COMPLETED / VOIDED), `cashier`, `created_at`, `voided_by`, `voided_at`
- `OrderLineItem`: `order` (FK), `product` (FK), `qty_kg`, `unit_price_idr` (server snapshot, tax-inclusive), `tax_rate_pct` (snapshot), `tax_idr`, `net_revenue_idr`, `unit_cogs_idr` (weighted avg of allocations)
- `LineLotAllocation`: `line` (FK), `lot` (FK), `qty_kg`, `unit_cogs_idr`

### 4. `invoicing` (Wholesale / B2B Sales)
- `Buyer`: `name`, `company`, `phone`, `email` (optional), `address`, `payment_terms_days` (int ≥ 0; 0 = due on receipt), `notes`, `is_active` — plain customer directory, no marketplace.
- `Invoice`: `number` (unique, nullable until issue, `INV-YYYYMM-####`), `buyer` (FK), `status` (DRAFT / ISSUED / VOID — stored lifecycle), `issue_date`, `due_date`, `discount_type` (NONE / PERCENT / AMOUNT), `discount_value` (Decimal), `shipping_idr` (Decimal ≥ 0), `subtotal_idr`, `discount_idr`, `tax_idr`, `total_idr`, `total_cogs_idr`, `notes`, `created_by`, `issued_by`, `issued_at`, `voided_by`, `voided_at`
  - Payment status (UNPAID / PARTIALLY_PAID / PAID / OVERDUE) is **derived**, never stored.
- `InvoiceLine`: `invoice` (FK), `product` (FK), `qty_kg`, `unit_price_idr` (tax-exclusive; defaults to catalog price, overridable), `line_subtotal_idr`, `discount_share_idr`, `tax_rate_pct` (snapshot), `tax_idr`, `net_revenue_idr` (= subtotal − discount share), `unit_cogs_idr`
- `InvoiceLineLotAllocation`: `line` (FK), `lot` (FK), `qty_kg`, `unit_cogs_idr`
- `InvoicePayment`: `buyer` (FK), `amount_idr` (> 0), `method` (CASH / QRIS / TRANSFER), `paid_on`, `reference`, `proof` (optional FileField), `recorded_by`, `created_at` — manually recorded money received; no gateway.
- `PaymentAllocation`: `payment` (FK), `invoice` (FK), `amount_idr` (> 0)

### 5. `geo` (Field Map, Satellite & Weather)
- `FieldPolygon`: `name`, `geojson` (JSONField, no PostGIS), `area_ha` (Decimal, computed server-side), `element` (nullable FK FarmElement), `agro_polygon_id` (nullable, Agromonitoring id), `created_by`, `created_at`
- External data is fetched only in `geo/services.py` and cached via Django's database cache (`createcachetable`): weather 30 min, NDVI/soil 24 h.

## Business Rules (enforce in `services.py`, test each)

### Farm & cost pool
1. `CropCycle.stage` only advances forward in the listed order; backward transitions are rejected.
2. A `HarvestBatch` may be recorded only when `cycle.stage == HARVESTING`. Validate `high_quality_kg > 0`; `low_quality_kg, harvest_loss_kg >= 0`.
3. **Incremental cost pool:** on save, a batch absorbs every APPROVED expense of its cycle with `absorbed_by IS NULL` (set `absorbed_by = batch`). Absorbed cost is never re-spread; earlier lots' COGS never change. First-harvest cost skew is an accepted, documented limitation.
4. **Grade split — relative sales value:** cost share per grade ∝ `kg × selling_price` (catalog price; invoice price overrides never affect it). If either grade's product lacks a price, fall back to equal per-kg COGS. `unit_cogs_<grade> = share / kg_of_grade`.
5. **Zero-yield:** if `high + low == 0`, absorb nothing (costs stay in pool), create no lots, unit COGS = 0. No division by zero.
6. On save, create one `StockLot` per grade with kg > 0 (auto-create `Product(variety, grade)` with `selling_price_idr = NULL` if missing) and log `HARVEST_IN`.
7. **Cycle-less expenses:** an ELEMENT expense without a cycle attaches to that element's active cycle at approval; if none, it is overhead. GENERAL expenses never enter COGS — they are operating overhead in analytics.
8. **Cycle close** (stage CLEARING / `is_active=False`): remaining unabsorbed approved expenses are reported as "unabsorbed cycle cost" (write-off). Approving an expense on a closed cycle is allowed and lands there.
9. **Expense lifecycle:** only OWNER/MANAGER approve/reject. APPROVED-but-unabsorbed may be reverted to PENDING; absorbed expenses are immutable. `INPUT_USAGE` expenses are created APPROVED and are not editable in the expense UI.
10. **HarvestBatch is never edited in place.** Delete is allowed only if none of its lots have movements other than `HARVEST_IN`; delete un-absorbs its expenses in the same transaction.
11. **Inputs:** purchase updates `avg_cost = (stock×avg + qty×cost) / (stock+qty)` (if stock is 0, avg = purchase cost). FERTILIZER/PESTICIDE/SEEDING activity with an `input_item` deducts stock (reject if insufficient) and creates an APPROVED `INPUT_USAGE` cycle expense of `qty × avg_cost` (category PUPUK/PESTISIDA; SEED → BIBIT). Deleting that activity reverses stock and deletes the expense only while unabsorbed. A purchase cannot be deleted once usage of that item occurred after it. Low-stock alert when `stock_qty <= min_alert_qty`.
12. A `FarmElement` cannot be deactivated while it has an active cycle.
13. **Estimates:** `expected_harvest_date` defaults to `planting_start_date + variety.days_to_harvest` (editable). Days growing = `max(0, today − start)` (closed cycles: `closed_at − start`). Yield estimate vs actual = Σ batch sellable kg vs `expected_yield_kg`.
14. **Pre-harvest checklist:** `completed_pct = checked active items / total active items × 100`, snapshotted on save. Advancing to HARVESTING with no check or a latest check < 100% shows a **warning** but is allowed.
15. **Activity fields:** `duration_minutes` only for IRRIGATION (> 0 if given); `photo` only for SCOUTING.

### Inventory & tax
16. **All stock mutations go through** `inventory.services.move_lot_stock()` and `farm.services.move_input_stock()`: `select_for_update` the row, validate, mutate, append ledger. Nothing else touches quantities.
17. **Spoilage** targets a specific lot (UI preselects the oldest); cost is `qty × lot.unit_cogs_idr`, reported as spoilage loss. Lot unit COGS is unchanged.
18. **Invariants** (tests + `python manage.py check_mass_balance`): `lot.qty_remaining_kg == Σ StockLedger.qty_kg` per lot; `lot.qty_initial_kg` matches its batch grade kg; no negative stock; input `stock_qty == Σ InputLedger.qty`.
19. **Tax snapshot:** each POS/invoice line copies `tax_rate_pct` from `product.tax` at sale time (0 if none). Later rate changes never rewrite history. Tax per line is rounded to 2 dp `ROUND_HALF_UP`.

### POS
20. Cart lives in the browser (Alpine.js) — no reservation. Checkout posts product IDs + kg only; prices are read server-side from `Product.selling_price_idr` (client prices ignored). Products with no price are hidden from the POS. No discounts.
21. **POS prices are tax-inclusive:** line gross = qty × price; `tax = gross × rate / (100 + rate)`; `net_revenue = gross − tax`. Receipt shows "termasuk PPN Rp X".
22. Checkout runs inside `@transaction.atomic`: lock all open lots of the cart's products with `select_for_update()` ordered by `(product_id, harvested_at, id)`; validate total available ≥ requested per product (else fail the whole order with a message naming product and available kg); allocate FIFO into `LineLotAllocation`; log `POS_SALE`; assign `order_number` inside the transaction; render a printable receipt (HTML + print CSS, 58 mm thermal and A4).
23. **Void:** whole order only, OWNER/MANAGER, same Asia/Jakarta calendar day; restores exact kg to the exact lots via allocations, logs `POS_VOID`, sets status VOIDED. No partial refunds.

### Invoicing
24. **Lifecycle:** DRAFT (editable, deletable, no stock moved) → ISSUED → VOID. Only OWNER/MANAGER create, issue, void, and record payments.
25. **Issue** runs inside `@transaction.atomic` with the same locked FIFO allocation as checkout (rule 22) into `InvoiceLineLotAllocation`, logs `INVOICE_SALE`, assigns `number` (`INV-YYYYMM-####`, monthly Asia/Jakarta counter) inside the transaction, sets `issue_date` and `due_date` (default issue + `buyer.payment_terms_days`, editable). Every line needs `unit_price_idr > 0` and `qty_kg > 0`.
26. **Totals (tax-exclusive):** `subtotal = Σ qty × price`; discount PERCENT (0–100) → `round(subtotal × pct / 100, 2)`, AMOUNT → value (0 ≤ value ≤ subtotal). Discount is allocated to lines **proportionally to line subtotal**, the last line taking the rounding remainder so Σ shares == discount exactly. `line tax = (line subtotal − share) × rate / 100`. `total = subtotal − discount + Σ tax + shipping`. Shipping is untaxed.
27. **Payments & credit:** an `InvoicePayment` belongs to a buyer and may be split by `PaymentAllocation` across that buyer's ISSUED invoices. Σ allocations per payment ≤ payment amount; each allocation ≤ that invoice's outstanding (`total − Σ allocations`). Unallocated payment amount is the buyer's **credit**: `credit = Σ payments − Σ allocations` (derived, never stored, always ≥ 0). Applying credit = allocating an older payment's unallocated remainder to an invoice.
28. **Derived payment status:** PAID when outstanding == 0; PARTIALLY_PAID when 0 < allocated < total; OVERDUE when outstanding > 0 and `due_date <` today (Asia/Jakarta); otherwise UNPAID.
29. **Void:** OWNER/MANAGER, any date; in one transaction restores exact kg to exact lots (`INVOICE_VOID`), deletes its allocations (money returns to buyer credit), sets VOID. No refunds: credit is only usable on future invoices; a payment may be deleted (OWNER) only while fully unallocated.

### Analytics
30. Revenue, gross margin, margin by variety × grade, and net are **net of tax and of invoice discount** (line `net_revenue_idr`). Invoice revenue and COGS count on `issue_date` (accrual). Shipping is "other income" (in net, not in gross margin). Show separately: PPN collected, channel split (POS vs Invoice), receivables outstanding and overdue.

### Staff, notifications, external data
31. **Staff:** OWNER creates users (username, full name, role, temporary password → `must_change_password=True`; middleware forces a password change on next request). Users are deactivated, never deleted. Cannot deactivate yourself or the last active OWNER. No email anywhere.
32. **Notifications** are computed live (no notification rows), polled every 60 s: low stock (farm roles), pending expense approvals, overdue invoices, unpriced products (OWNER/MANAGER), cycles past `expected_harvest_date` without reaching HARVESTING (farm roles). Unread = created/observed after `notifications_seen_at`.
33. **Polygons:** valid (shapely), no self-intersection, 0.02 ≤ area ≤ 1000 ha, total of all polygons ≤ 1000 ha (free-tier cap). Area is computed server-side. Linking to an element never overwrites `area_m2` silently — UI shows plotted vs recorded area with a "use plotted area" action.
34. **External APIs** (Open-Meteo weather — no key; Agromonitoring NDVI/EVI/NDWI/soil — `AGRO_API_KEY` in `.env`) are called only from `geo/services.py`, cached, mocked in all tests (CI never hits the network). Missing key or API failure → UI shows "data tidak tersedia"; the rest of the app is unaffected.

## Roles (Django Groups + `RoleRequiredMixin`, 403 tested)
| Role | Access |
|---|---|
| OWNER | Everything + staff management, farm profile (Pengaturan), payment deletion |
| MANAGER | Everything incl. expense approval, voids, invoices, buyers, payments |
| WORKER | Farm ops (reports, harvests, spoilage, checklist, map/weather); no financial figures |
| CASHIER | POS register and receipts only |

## UI (FarmCloud layout, GoTani's own brand — D66)
- **Brand:** own palette, logo, free Google Fonts, Bahasa Indonesia copy. Light/dark mode (system default, toggle saved in localStorage; Chart.js colours follow theme).
- **Shell:** left sidebar 16rem, collapses to a 3rem icon rail (Ctrl/⌘+B, state in localStorage); mobile = slide-in sheet from a hamburger. Header slot shows FarmProfile name/logo + user's role. Groups (role-filtered):
  - **DASBOR:** Ringkasan · Laporan Kerja · Aset Kebun ▾ (Elemen, Siklus Tanam, Inventaris Input) · Cuaca & Satelit
  - **PENJUALAN:** Kasir (POS) · Pesanan · Invoice · Pembeli
  - **KEUANGAN:** Biaya · Harga Produk · Analitik
  - **ALAT:** Telusuri · Staf · Pengaturan
- **Top bar** (h-16, translucent blur): sidebar toggle left; right: 🔔 bell, ◐ theme toggle, divider, 👤 menu (Profil, Ganti Password, Keluar).
- **Page anatomy** (reusable template includes + Alpine): title + role-specific subtitle; primary action top-right; KPI stat cards; status tabs synced to `?tab=`; toolbar (search, date range, "Atur kolom" column visibility in localStorage); table with ⋯ row menu; footer "Baris per halaman · Halaman x dari y · « ‹ › »", default 10.
- **Forms:** short create/edit forms open in a right-side sheet (server validation errors reopen it); invoice editor, payment allocation, harvest entry and POS are full pages.
- **Ringkasan** (home for farm roles): weekly work-report strip by type + "Catat Kerja Hari Ini", weather widget, "Tanaman per Tahap" chart, counters per element type + staff; finance card (all-time sales, expenses, general vs element-specific) for OWNER/MANAGER only. CASHIER lands on POS.
- **Laporan Kerja:** tabs Pemupukan · Pestisida · Irigasi · Scouting · Penanaman · Pra-panen · Panen · Susut, per-type columns; "Laporan Baru" picker opens the per-type form.
- **Detail pages** (cycle, element, input item, buyer, invoice): overview card + tabs + timeline from ledgers/logs (cycle timeline reuses `forward_trace`).
- **Status pills:** pending = yellow, issued/approved = blue, partially paid = teal, overdue/void/rejected = red, paid/completed = green. Every empty state explains the feature + CTA; async widgets show "Tidak dapat memuat … · Coba lagi".
- **Login:** split screen — dark left panel (logo, headline, tagline), form right (username, password with eye toggle, "Masuk"); no sign-up, no forgot-password.
- **Existing screens** (kept, migrated onto the shell): POS register + receipt + void; Traceability forward/backward with print-CSS PDF + CSV; Analitik dashboard (date range, Chart.js: revenue, COGS, gross margin & %, spoilage, unabsorbed cycle cost, overhead, other income, net, PPN collected, channel split, receivables; margin by variety × grade; kg/m² by element type; top products; expenses by category).
- **Cuaca & Satelit:** Leaflet map with polygon list; draw/edit with Leaflet.draw; per polygon NDVI/EVI/NDWI min/mean/max per pass, historical NDVI chart, latest NDVI image overlay, soil temperature/moisture. Weather widget: current + 7-day forecast + rain warning.
- **PWA:** manifest + icons, installable; service worker caches static assets and serves an offline fallback page; HTML/data are network-only (no offline writes); "Versi baru tersedia · Muat ulang" banner on SW update.

## Architecture & Conventions
- Keep `views.py` thin; all domain math, COGS propagation, tax/discount math, and stock mutations live in each app's `services.py`.
- Register all models in `admin.py` for back-office setup.
- `python manage.py seed_demo` (idempotent): 3 elements, 4 cycles (Melon Golden, Cabai Rawit, Kakao Forastero) with estimates and checklist, input purchases/usage, multiple harvests, spoilage, ~60 POS sales over 30 days, buyers with issued/partly-paid/overdue/void invoices and a payment leaving credit, one sample polygon (no API call); must pass `check_mass_balance`.

## Testing
- Always write and run `python manage.py test` after implementing each phase.
- Service tests: `TestCase` (SQLite OK). Concurrency: `TransactionTestCase` with two threads racing for the last kg (POS vs POS, and POS vs invoice issue) — PostgreSQL only (skip on SQLite).
- External HTTP is always mocked. GitHub Actions CI with a Postgres 16 service container. Target ≥ 85% coverage on `services.py`.

## Build Phases
1. Models, constraints, migrations, admin, `seed_demo` skeleton ✅
2. Farm services: stage transitions, inputs + avg cost, expense approval, cost pool, RSV grade split ✅
3. Inventory: lots, `move_lot_stock`, spoilage, ledger, `check_mass_balance` ✅
4. POS: locked FIFO checkout, order numbers, receipt, void, Postgres concurrency test ✅
5. Traceability (forward/backward), analytics dashboard, role access ✅
6. Full `seed_demo`, CI, deployment ✅
7. Shell + component kit (sidebar, top bar, dark mode, page anatomy, sheets, pills, login), `FarmProfile`, Staf page + forced password change, pagination/search; migrate existing screens ✅
8. Tax (POS + invoices), buyers, invoices (discount, shipping, issue/void), payments + allocation + credit, analytics channel split/PPN/receivables ✅
9. Cycle estimates, pre-harvest checklist, activity extras + Laporan Kerja tabs, input usage history + restock, element/cycle detail pages + timelines, Ringkasan home, notifications bell ✅
10. Weather (Open-Meteo), polygons (Leaflet + shapely), Agromonitoring NDVI/soil ✅
11. PWA (manifest, service worker, offline page, update banner) ✅

## Azure Ubuntu VM Deployment (`deploy/`)
- Read `DATABASE_URL`, `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, and optional `AGRO_API_KEY` from `.env` via `python-dotenv` and `dj-database-url`.
- Include `deploy/setup_vm.sh`, `deploy/gotani.service`, and `deploy/Caddyfile`. Deploy runs `migrate`, `createcachetable`, `collectstatic`.
- PostgreSQL 16 on the same VM. Reserve a **static public IP**; DuckDNS hostname points to it, plus a 5-minute DuckDNS update cron in `setup_vm.sh` as a safety net. Caddy provides automatic HTTPS for the DuckDNS domain (required for PWA install).
- Nightly `pg_dump` cron with 7-day retention. Azure VM runs on student credit (zero-cost rule).
