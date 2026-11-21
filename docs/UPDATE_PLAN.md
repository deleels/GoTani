# GoTani update plan (from the FarmCloud comparison, 2026-11-21)

Source: a feature-by-feature comparison with FarmCloud (Work Reports, Farm Assets, Weather & Satellite,
Staff, Settings; finance treated as covered) and a grilling session that settled every choice below.
Decisions D123 to D137 in `DECISIONS.md` are the binding record; this file is the build order.

**Working rules for every step**
- One commit per step, pushed to `main`, with tests (`python manage.py test` green) and English strings in
  `locale/en/tr_*.py` (`python manage.py i18n`). No "—" in UI text (D122).
- Domain logic stays in `services.py`; views stay thin. Update `CLAUDE.md` where a business rule changes.
- `check_mass_balance` must still pass on `seed_demo` after each step.

---

## 1. Lock down Django admin (D123) ✅
- Read-only (view only, no add/change/delete) for transaction data: `HarvestBatch`, `ActivityLog`,
  `SpoilageRecord`, `Invoice` + lines, `InvoicePayment`, `PaymentAllocation`, `InvoiceLineLotAllocation`;
  `CropCycle.stage` read-only.
- `Product.selling_price_idr` read-only in admin (prices change only on Harga, which writes the audit log).
- `User` / `Group` read-only in admin (staff changes only on Staf, which enforces the last-OWNER rule).
- Setup tables stay editable: Variety, FarmElement, Tax, ExpenseCategory, PreHarvestChecklistItem, Buyer.
- Test: walks every registered ModelAdmin and fails if a transaction model allows add/change/delete.

## 2. In-app setup (D124) ✅
- Inventaris Input: "+ Input baru" (name, type, unit, low-stock level, optional supplier), optional first
  purchase in the same form. OWNER/MANAGER.
- Start-cycle form: "+ Varietas baru" opens a small sheet (name, crop, days to harvest, optional Kc).
  Pengaturan gets a Varietas list to rename and fix days/Kc.
- Element page: "Ubah" for name and area (keeps "use plotted area"); type changes only without an active
  cycle; every change goes to the audit log.
- Pengaturan: farm location picked on a small Leaflet map (click to drop a pin + existing Nominatim
  search); latitude/longitude fields stay as a fallback.

## 3. Navigation restructure (D125) ✅
```
DASBOR     Ringkasan · Laporan Kerja · Cuaca & Satelit
KEBUN      Elemen & Siklus · Inventaris Input · Stok Hasil Panen
PENJUALAN  Kasir · Grosir
KEUANGAN   Biaya · Analitik
ALAT       Telusuri · Log aktivitas · Staf · Pengaturan
```
- **Kasir**: one sidebar link; POS pages share a tab strip Kasir · Pesanan · Shift & Kas with the shift
  status chip beside it.
- **Grosir**: one sidebar link; tabs Invoice · Pembeli (payments stay under each buyer).
- **Stok Hasil Panen** (new page): tabs Stok (per product: kg, open lots, oldest lot age, low-stock flag) ·
  Susut · Opname · Harga (OWNER/MANAGER only). Harga Produk leaves KEUANGAN.
- Panen and Susut leave the old Aset Kebun dropdown (Panen stays in Laporan Kerja and on the cycle page).
- Omzet & PPh becomes an Analitik tab (built as a shared tab strip over the existing URLs, so no redirects
  were needed).
- Elemen & Siklus stays one combined page.
- Old URLs keep working (redirects); role filtering unchanged (a WORKER still reaches Susut and Opname).
- Update the shell section of `CLAUDE.md`.

## 4. When the work was done (D126) ✅
- Every work report gets "Waktu kegiatan": default now, at most 3 days back, never in the future, never
  before the cycle start.
- Table shows the activity time, with "dicatat <time>" underneath when it differs from the record time.
- `stage_at_activity` stays the cycle's current stage (no stage history per timestamp).

## 5. "Lainnya" work type (D127) ✅
- New `OTHER` activity type: `task_name` (required, 80 chars), optional `duration_minutes` (> 0),
  new nullable `worker_count` (> 0, OTHER only), optional photo; never uses an input item; any stage,
  including CLEARING. Own "Lainnya" tab in Laporan Kerja. Labour cost stays a manual TENAGA_KERJA expense.
- Update rule 15 in `CLAUDE.md`.

## 6. Grouped report picker (D128) ✅
- "Laporan Baru" picker in groups with one-line descriptions: Perawatan (Pemupukan, Pestisida, Irigasi,
  Penanaman) · Pemantauan (Scouting, Pra-panen) · Panen & Lainnya (Panen, Susut, Lainnya).
- Same picker on the cycle page, preset to that cycle.

## 7. Structured scouting (D129) ✅
- New fields: `finding` (Normal / Hama / Penyakit / Gulma / Kekurangan hara / Lainnya, required),
  `severity` (Ringan / Sedang / Berat, required unless Normal), observation text required unless Normal.
  Photo stays optional.
- Bell alert for OWNER/MANAGER while a cycle has a "Berat" finding in the last 3 days and no newer
  Normal or Ringan scouting on that cycle.

## 8. Pre-harvest Ya / Tidak (D130) ✅
- Each active checklist line is answered Ya or Tidak; unanswered lines block saving.
- `completed_pct` = Ya answers / active lines × 100. Tidak answers show red on the check and in the cycle
  timeline. The list stays admin-editable; seed food-safety lines (clean water, toilets and handwashing,
  no sick workers, clean containers) next to the current ones.
- Update rule 14 in `CLAUDE.md`.

## 9. Several reports in one form (D131) ✅
- Types: Pemupukan, Pestisida, Irigasi, Penanaman, Lainnya (not Scouting, Panen, Pra-panen).
- Shared fields once (type, time, input item, method, notes); one row per cycle with its quantity
  (plus duration for irrigation); at most 10 rows.
- One atomic save: all or nothing. Each row runs the normal stock and usage-expense rules; if the input
  stock is short for the combined total the batch is rejected with the shortfall named.

## 10. Limited editing of reports (D132) ✅
- "Ubah" next to Hapus edits only fields that touch neither stock nor cost: method, notes, photo,
  scouting finding/severity/observation, Lainnya task name. Quantity, input item and cycle still need
  delete and re-enter. Every edit goes to the audit log.

## 11. Payment details on input purchases (D133)
- Purchase form gets payment method (Tunai / QRIS / Transfer) and an optional receipt file, like Biaya.
  No link to the cash drawer.

## 12. Three-day correction window (D134)
- Spoilage reversal and stage undo: allowed up to 3 calendar days (Asia/Jakarta) after the event,
  OWNER/MANAGER, reason required. Stage undo still needs "nothing recorded since".
- Spoilage reversal is blocked when an approved opname for that product is newer than the spoilage
  (message names that opname).
- POS void stays same-day only.
- Update rules 1 and 17 in `CLAUDE.md`.

## 13. First-harvest cost warning (D135)
- On Analitik's margin by variety × grade and on the harvest review: "Biaya awal siklus terbebankan ke
  panen pertama" when a cycle has one harvest batch, or its first batch absorbed more than 60% of the
  cycle's absorbed cost. Warning only; COGS math unchanged.

## 14. UX bundle (D136)
- `Variety.notes` shown as a card on the cycle page.
- `CropCycle.planted_unit` label (tanaman / tray / polybag / lubang); count stays an integer, no conversion.
- "Telusuri" button on Laporan Kerja.
- "Catat Panen" call-to-action card on the cycle page at HARVESTING.
- Search box on the Laporan Kerja tabs (element or cycle name, input name, notes).

## 15. Satellite history and export (D137)
- Date range on the NDVI history (default 60 days).
- "Ekspor CSV" of per-pass index statistics.
- Median and pass count added to the stats.
- Hourly forecast, per-hour spray mark and 30-day rain already exist; no change there.
- Under-1 ha polygons stay without indices (D79 confirmed by a live test on 2026-11-21: Agromonitoring
  answered HTTP 422 "must be from 1 to 3000 ha" for 0.65 ha and 0.10 ha).

---

## Considered and dropped
- NDVI for plots under 1 ha (1 ha proxy square misleads; Copernicus switch too large for now).
- Shift "simple mode", caching the bell, offline data entry (still out of scope).
- Extra satellite indices (DSWI, EVI2, NRI), splitting Elemen & Siklus, FarmCloud's
  "create expense on purchase" toggle (would double-count with usage costing).
