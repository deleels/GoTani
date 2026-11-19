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
- **D81 Indonesian number input + kasir quantity step** — inputs marked `data-num` are formatted while typing (`15.000`, `1,5`; the "." key types a comma; whole Rupiah except input purchase cost, which allows `,xx`) by `core/static/core/numbers.js`, which submits plain machine numbers. The server (`core.numbers.parse_id_decimal`, `IDDecimalField`) accepts both: a comma or 2+ dot groups means Indonesian; a single dot is a decimal point except in a whole-Rupiah field. Displays stay Indonesian, CSV stays plain. POS lines get − / + buttons and quick-pick chips driven by `Product.pos_step_kg` (default 0,5 kg, set on Harga Produk; chips = step × 1, 2, 4, 10); typed values keep 3 decimals.
- **D82 One append-only audit log (`core.AuditLog`, written only by `core.audit.log_event`) replaces a separate price history** — who, when, what, before → after (JSON), and an optional reason; save/delete of an existing row raise, the admin is read-only. Logged so far: price changes (Harga Produk and admin edits), kasir step, PKP switch. The "Log aktivitas" page (ALAT, OWNER/MANAGER) and the confirm-dialog actions (stage undo, spoilage reversal, allocation removal, voids) write to it as they are built.
- **D83 One shared confirmation dialog replaces every `confirm()`** — `core/static/core/confirm.js`: level 1 = summary + a button named after the action; level 2 adds "Saya sudah memeriksa data ini"; optional required reason (posted as `reason`). Declarative `data-confirm*` attributes on forms/buttons, `gtConfirm()` for scripts. The service worker's cache version now hashes every app static file, not just the pre-cached ones — before, an edited script was served stale forever by the cache-first worker.
- **D84 Majukan tahap = next stage only; skipping needs "Lewati tahap"; same-day undo of the last advance (exception to rule 1)** — the dialog shows from → to, skipped stages, the pre-harvest check result and the CLEARING write-off. OWNER/MANAGER may undo the *last* advance on the same Asia/Jakarta day, with a reason, only while nothing was recorded on the cycle since (activity, harvest, check and expense ids are snapshotted in the STAGE_ADVANCE audit entry). Undoing CLEARING reopens the cycle unless the element already has a newer cycle. Both directions are in the audit log.
- **D85 Harvest review step** — the first "Tinjau panen" submit only previews (`farm.services.preview_harvest`): sellable kg, cycle total vs expected, previous batch average, and for OWNER/MANAGER the cost pool it would absorb and HPP/kg per grade (same RSV split as the save). Soft warnings at > 150% of expected yield or > 3× the batch average turn the save into a level-2 confirmation. Saving needs a resubmit whose values match the reviewed signature, so edited numbers are always re-reviewed.
- **D86 Spoilage: level-2 confirmation (warns above 50% of the lot) and same-day reversal by OWNER/MANAGER** — the reversal is a new `SPOILAGE_REVERSAL` (+) ledger movement back into the same lot; the record stays, marked `reversed_at/by/reason`, and is excluded from spoilage loss in analytics and traceability (CSV shows a "BATAL SUSUT" row). Mass balance holds because the ledger still sums to the lot.
- **D87 Soft data-entry warnings, never blocks** — price change > 30% or below the open lots' average HPP/kg (Harga Produk); a POS line > 3× the product's 30-day average completed line, never below 20 kg (catches a missing decimal comma); an input purchase unit cost > 50% away from the current average cost. All via the shared dialog's `data-confirm-if`.
- **D88 Invoice issue is level 2 (server checks the tick); a payment allocation can be taken off by OWNER/MANAGER with a reason** — the money returns to buyer credit (same payment, now unallocated) and the invoice's derived payment status follows; logged as ALLOCATION_REMOVED.

## Weather & satellite (agreed 2026-11-19)
- **D89 Spray window from Open-Meteo hourly data** — BAD: wind > 15 km/h, rain chance ≥ 50% within 4 h, or raining now; FAIR: > 32 °C or wind 10–15 km/h; else GOOD. Shown with reasons and the next good hour on the weather widget, per hour on the 48 h strip, and as a hint on the pesticide report. Advice only, never blocks. One Open-Meteo call (current, 48 h hourly, 7 days, 30 past days, ET₀, gusts) cached 30 min.
- **D90 Vegetation indices only where the satellite sees the crop; soil from Open-Meteo for every polygon** — polygons linked to GREENHOUSE / SCREEN_HOUSE / HYDROPONIC elements get a "beratap" reason instead of NDVI/EVI/NDWI (Sentinel-2's 10 m pixels see the roof). Agromonitoring stays for indices only (≥ 1 ha, key). Soil temperature (0/6/18 cm) and moisture (0–27 cm) come from Open-Meteo for any size: both sources are model output, Open-Meteo works without a key or size limit and is labelled "perkiraan model, bukan sensor". The element detail page shows the same data when a polygon is linked.
- **D91 Water need = ET₀ × optional `Variety.kc`** — deficit subtracts today's forecast rain except under a roof; litres = mm × element m² (1 mm on 1 m² = 1 L). Without Kc only ET₀ is shown. Kc is edited in the admin; `seed_demo` sets FAO-56 mid-season values.
- **D92 Map stays Leaflet + Esri (no Mapbox, D43)** — three base maps (Satelit + label, Satelit, OpenStreetMap with local names), a "Lokasi saya" GPS button, areas as "0,06 ha (614 m²)", and a desa/kecamatan/kabupaten search through Nominatim proxied in `geo/services.py` (country `id`, Indonesian names, generic User-Agent, cached 7 days, throttled to 1 request/s per its usage policy).
- **D93 Weather alerts in the bell are computed from the cached forecast only** — heavy rain (≥ 50 mm) or gusts (≥ 40 km/h) tomorrow, and 7 dry days while an open-field cycle is active; farm roles only. The 60 s poll never calls Open-Meteo itself; alerts appear once any page has loaded the forecast.

## Step 4 — counter & stock operations
- **D94 Void reasons from a fixed list (Salah input / Salah harga atau produk / Pembeli batal / Pembayaran gagal / Lainnya), "Lainnya" needs a note** — for POS orders and invoices; picked in the shared dialog, stored on the record and printed on the voided receipt. Free text alone is unreportable; a list alone hides the odd case.
- **D95 Cash received → change, CASH only, stored and printed** — blank means exact money; less than the whole-Rupiah total is refused. Quick chips: exact amount and the next round notes.
- **D96 One drawer, one open shift, no sales without it** — `Shift` + `CashMovement` (Tambah modal / Setor ke pemilik / Bayar biaya → a PENDING CASH GENERAL expense / invoice cash "Masuk laci" / refund of a voided cash order). A cash void needs an open shift; its refund is a cash-out of the *current* shift, while the original sale stays in its own shift's cash-in. Blind count by denomination (cashiers don't see the expected amount until after closing); close is always allowed and any difference stays "Perlu ditinjau" until a manager notes it. The opener closes, a manager may force-close; both are audit-logged. Opening float is prefilled from the last count. The open-shift uniqueness is a partial unique index on `is_open=True` (a unique index over NULL `closed_at` would not hold on PostgreSQL).
- **D97 Payment-method split and receivables ageing** — Analitik shows money received per method: POS completed orders by sale date plus invoice payments by paid date (cash basis, unlike accrual revenue). Pembeli shows ageing per buyer: 0 / 1–30 / 31–60 / 61–90 / > 90 days past due.
- **D98 `Product.min_alert_kg` low-stock alert for produce** — set on Harga Produk (blank = off); bell alert for farm roles when stock ≤ threshold.
- **D99 Stock opname: shortfall only, FIFO write-off as SHRINKAGE spoilage, after OWNER/MANAGER approval** — the count snapshots system stock; approval writes off `system − counted` (capped at current stock, since sales after the count already took their kg) lot by lot from the oldest, so loss = kg × each lot's COGS and mass balance holds. A surplus is recorded but never booked (stock cannot appear without a harvest lot).
