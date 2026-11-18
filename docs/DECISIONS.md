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
