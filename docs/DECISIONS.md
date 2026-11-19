# GoTani — Design Decision Log

Decisions locked during the spec grilling session (2026-11-19), informed by a REA inspection of FarmCloud (farmcloud.synnefa.io). Each entry: decision — reason.

## Scope & foundations
- **D1 Rubric priority:** correctness + tests → deployment → UI polish — engineering grade rewards explainable, tested domain logic over feature breadth.
- **D2 Single farm:** no `Farm` model or tenant filtering — multi-tenancy adds isolation cost with no grading value.
- **D6 Units & precision:** kg only, qty `Decimal(10,3)`, money `Decimal(14,2)`, whole-Rupiah `ROUND_HALF_UP` only for display — avoids rounding drift breaking balance tests.
- **D31 Language:** UI in Bahasa Indonesia, code in English — Indonesian context, conventional code.

## Costing
- **D4 Multi-batch COGS — incremental cost pool:** each harvest absorbs unabsorbed approved cycle expenses — costs absorbed exactly once; sold lots never get rewritten COGS.
- **D5 Grade split — relative sales value:** cost ∝ kg × price, equal-per-kg fallback — standard joint-product costing; prevents LOW grade looking falsely unprofitable.
- **D8 Absorption tracking:** `Expense.absorbed_by` FK — every rupiah of batch COGS traceable to expense rows.
- **D9 Cycle-less expenses:** ELEMENT → active cycle at approval; GENERAL → overhead, never COGS — avoids an area-allocation algorithm; clean gross vs net split.
- **D10 First-harvest skew accepted** — amortization would reintroduce estimation/true-up rejected in D4.
- **D11 Cycle close:** leftovers reported as "unabsorbed cycle cost" — lots stay immutable, no cost disappears.
- **D12 Zero-yield batch:** absorbs nothing, no lots — no division by zero, costs carry forward.
- **D13 Edit rules:** absorbed expenses immutable; batches delete-only before any non-harvest movement — protects COGS history.
- **D22 Input costing:** restock = inventory asset (`InputPurchase`); usage creates a cycle expense — cost recognized when consumed by a specific cycle (fixes a bug where restock-as-expense never reached COGS).
- **D34 Usage expenses auto-APPROVED, source=INPUT_USAGE** — already validated via inventory; double approval is busywork.
- **D35 Weighted-average input cost, no unit conversion, purchases undeletable after later usage** — simple, consistent cost history.
- **D37 `ExpenseCategory` model (seeded)** — enables cost-breakdown analytics; mirrors FarmCloud's expense-categories.

## Inventory & spoilage
- **D3 Lot-level stock:** `Product` catalog + `StockLot` per batch/grade, FIFO — real traceability and true per-lot COGS.
- **D14 Spoilage on a chosen lot** (oldest preselected) — spoilage is physical; auto-FIFO would falsify traceability.
- **D15 Spoilage cost = qty × lot COGS, separate loss line** — keeps waste out of margin of surviving stock.
- **D16 No HIGH→LOW downgrade** — needs lot transfer + cost carry-over; future work.
- **D20 Signed append-only ledger + mass-balance invariants + `check_mass_balance`** — makes "mass balance" provable live.
- **D23 `Variety` model; Product unique (variety, grade)** — prevents duplicate products from free-text typos.
- **D24 Auto-created products start unpriced and hidden from POS** — pricing is a management decision.
- **D25 No price history** — line snapshots suffice.
- **D36 Single stock-mutation services (`move_lot_stock`, `move_input_stock`)** — one locked, tested choke point.

## POS
- **D7 Browser cart, no reservation** — stock checked only at atomic checkout; avoids reservation expiry logic.
- **D17 `select_for_update` in deterministic order + `CHECK qty >= 0`** — pessimistic locking, deadlock-free, DB backstop.
- **D18 Server-side prices + `LineLotAllocation`** — tamper-proof pricing; sale → cycle traceability.
- **D19 Whole-order same-day void (OWNER/MANAGER)** — reuses allocations; second atomic op to test.
- **D30 `GT-YYYYMMDD-####` inside transaction, Asia/Jakarta, print-CSS receipt** — no duplicate numbers; no PDF library.

## Farm rules
- **D26 Harvest only at HARVESTING stage** + forward-only stages — clear, demoable validation.
- **D38 One active cycle per element (partial unique constraint); no deactivating elements with active cycles** — makes D9 well-defined at DB level.

## Access, reporting, quality
- **D27 Role matrix OWNER/MANAGER/WORKER/CASHIER via Groups + mixin, 403-tested** — cheap, gradeable access control.
- **D28 Analytics KPIs** (revenue, COGS, margin, spoilage, unabsorbed, overhead, net, variety×grade, kg/m² by element type) — each chart proves model math.
- **D29 Forward + backward traceability; print-CSS PDF + CSV** — answers auditor questions; no heavy deps.
- **D21 Tests:** SQLite service tests, Postgres-only concurrency test, GitHub Actions with Postgres 16, ≥85% services coverage.
- **D32 Idempotent `seed_demo`** — demo-ready dashboard; doubles as integration test.
- **D41 Build phases 1–6** — cost/stock logic proven before UI.

## Deployment
- **D33 Postgres on same VM, Caddy HTTPS, nightly `pg_dump` (7 days), Tailwind CDN accepted** — single machine, credit-friendly.
- **D39 DuckDNS + static public IP + update cron safety net** — stop/start of the VM won't break HTTPS on demo day.
- **D40 Spec in `CLAUDE.md`, rationale here** — instruction file for coding sessions; this log feeds the report/viva.

---

# Product expansion (grilling session 2, 2026-11-19)

Decided by comparing GoTani against the FarmCloud reverse-engineering report (`farmcloud.md`). Supersedes D1/D41 where they conflict.

## Direction & scope
- **D42 Product direction:** move toward a real product rather than rubric-minimal — but every new feature still ships with service tests; correctness stays the bar.
- **D43 Zero-cost rule:** nothing that makes the developer pay — no paid APIs, gateways, or subscriptions; free no-card signups are fine; Azure runs on student credit — a hard personal constraint.
- **D44 Still out of scope:** multi-farm, dryers, IoT, RFQ marketplace, Stripe/plans/plan gating (FarmCloud's SaaS billing serves paying tenants; GoTani has none), admin portals (Django admin suffices), self-signup/OTP/email reset, per-user finance toggle (4 roles already cover it), seedling lots, unit conversion, ratings, offline writes — large builds with no user in a single-farm app.
- **D45 Docs first, then phases 7–11:** shell → money (tax/invoicing) → farm-ops extras → external APIs → PWA — money logic gets the most test time; riskiest, least-graded work last.

## Tax & invoicing
- **D46 `Tax` model, optional per product, rate snapshotted on every line** — fresh produce is mostly PPN-exempt; snapshots keep history immutable (same idea as D25).
- **D47 POS tax-inclusive, invoices tax-exclusive** — shelf price is what a walk-in pays; B2B norm is subtotal + PPN.
- **D48 Analytics net of tax; "PPN collected" separate KPI** — tax is a liability, not income.
- **D49 `Buyer` back in scope as a plain customer directory** — invoicing needs a counterparty; RFQ/marketplace stays out.
- **D50 Invoices draw real stock (locked FIFO, `INVOICE_SALE` / `INVOICE_VOID`)** — otherwise mass balance, COGS, margin and traceability silently miss wholesale sales.
- **D51 Stored lifecycle DRAFT/ISSUED/VOID; payment status derived** — a derived status can never disagree with the payments.
- **D52 Invoice unit price defaults to catalog, overridable by OWNER/MANAGER** — wholesale is negotiated; the RSV grade split (D5) keeps using catalog price so COGS is unaffected.
- **D53 Discounts on invoices only (% or fixed), allocated to lines proportionally, last line takes the remainder, PPN on the discounted base** — Indonesian DPP is post-discount (FarmCloud taxes pre-discount and overstates tax); POS stays discount-free.
- **D54 Shipping untaxed, "other income" outside gross margin** — it isn't product revenue.
- **D55 `InvoicePayment` + `PaymentAllocation`; buyer credit = Σ payments − Σ allocations (derived)** — one model covers multi-invoice allocation and credit; balance can't drift.
- **D56 Void releases allocations to credit; no refunds; payment deletable only while unallocated** — consistent with "no partial refunds"; money never disappears.
- **D57 Due date = issue + `payment_terms_days` (editable); OVERDUE derived (Asia/Jakarta)** — no cron job needed.
- **D58 `INV-YYYYMM-####` assigned at issue inside the transaction; A4 print-CSS with FarmProfile logo** — mirrors D30.
- **D59 Accrual: invoice revenue/COGS on issue date; channel split + receivables KPIs** — matches when stock leaves the lots.

## Farm operations
- **D60 Cycle estimates:** `Variety.days_to_harvest`, `planted_qty` (plant count — deliberate exception to D6), `expected_harvest_date`, `expected_yield_kg` — enables estimate-vs-actual and the overdue-harvest alert.
- **D61 Pre-harvest checklist with % snapshot; soft warning before HARVESTING** — useful signal without blocking a real harvest.
- **D62 Activity extras (irrigation duration, scouting photo) + Laporan Kerja tabs per type** — FarmCloud's most-used screen pattern.
- **D63 Input usage history + restock shortcut; element detail page** — answers "where did this fertilizer go" and "what has this greenhouse produced".
- **D64 Notifications computed live, polled every 60 s; only `notifications_seen_at` stored** — no WebSocket, no rows to keep in sync.
- **D65 Staff page: OWNER creates users with a temp password, forced change; deactivate not delete; no email** — email needs SMTP (cost/setup); users are referenced by sales and logs.

## UI
- **D66 Copy FarmCloud's layout patterns, not its brand** — the patterns are generic shadcn conventions; copying colours/fonts/copy would look like a knock-off.
- **D67 Collapsible sidebar (grouped DASBOR/PENJUALAN/KEUANGAN/ALAT) + top bar (bell, theme, user menu) + dark mode** — scales to the new screen count.
- **D68 One page anatomy for every list (subtitle per role, KPI cards, tabs, toolbar, ⋯ menu, pagination 10)** — consistency; built once as includes.
- **D69 Short forms in right-side sheets; multi-line flows as full pages** — fast entry without cramming complex forms.
- **D70 Ringkasan home for farm roles; analytics moves to KEUANGAN → Analitik** — the old dashboard was finance-only, workers had no home.
- **D71 Detail pages with tabs + timelines; standard status pills; explanatory empty states; split-screen login without sign-up/reset** — timeline reuses `forward_trace`.

## Geo & PWA
- **D72 Singleton `FarmProfile` (name, address, lat/lon, logo)** — weather, map centre and invoice header need it; single farm (D2) so no tenant model.
- **D73 Open-Meteo (weather, no key) + Agromonitoring free tier (polygons/NDVI/soil, key server-side); Leaflet + Esri tiles; GeoJSON in JSONField + shapely; DB cache; mocked in tests; graceful "data tidak tersedia"; linking a polygon never silently overwrites `area_m2`** — $0, avoids FarmCloud's leaked client-side key, CI stays offline, `area_m2` feeds kg/m² analytics.
- **D74 PWA: installable + static cache + offline fallback page only** — offline writes would bypass row locks and validation.

## Build-time refinements (P8–P11)
- **D75 NDVI image is proxied through the server** — Agromonitoring image URLs embed the API key; `geo:polygon_ndvi` streams the cached PNG so the key never reaches the browser (completes D73).
- **D76 Geodesic polygon area on a sphere (no pyproj)** — spherical-excess ring area is within ~0.5% of WGS84 at farm scale and adds no dependency; shapely only validates.
- **D77 Notification "observed at" times** — low stock = last ledger movement, overdue invoice/harvest = the day after the due date, pending expense = newest `created_at`, unpriced = first lot; unread = later than `notifications_seen_at`.
- **D78 `/` is Ringkasan for every farm role (OWNER/MANAGER included); Analitik stays at `/dashboard/`; CASHIER still lands on the POS** — one home for everyone who works the farm (D70).
- **D79 Polygons under 1 ha are mapped but get no satellite data, with an explicit reason** — Agromonitoring only accepts polygons of 1–3000 ha, while greenhouses are far smaller; GoTani keeps the 0.02 ha minimum (rule 33) so every element can be plotted, never calls Agromonitoring for a < 1 ha polygon, and the map says why data is missing (no key / too small / key rejected / polygon rejected / service down / no imagery yet) instead of a bare "data tidak tersedia". A rejected registration is retried after 10 min (immediately when the key changes); an empty imagery result is cached 3 h instead of 24 h.

## P12 — tax status & POS polish
- **D80 PKP switch on `FarmProfile.is_pkp` (OWNER, Pengaturan), off by default** — an individual farm below the PKP threshold may not charge PPN at all. While off, `Product.tax_rate_pct` returns 0 so every new POS line and issued invoice snapshots 0%; stored `Tax` links are kept but ignored; past sales keep their snapshot (D46). UI hides "termasuk PPN", the invoice PPN row and the "PPN terkumpul" KPI unless the farm is PKP or the record itself carries PPN. `seed_demo` defines the PPN rate but no longer attaches it to cocoa. Logging the switch in the audit log follows with D82.
