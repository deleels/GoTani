# GoTani glossary (Phase 7)

The UI runs in English or Indonesian per user (Profile, or the EN | ID switch in the account menu).
OWNER/MANAGER accounts start in English, CASHIER/WORKER in Indonesian. Indonesian is the source text
in the code (msgid); English lives in `locale/en/tr_*.py` and is compiled with `python manage.py i18n`.
`core.test_i18n` fails if any string has no English.

Use these terms everywhere so one idea is never translated two ways.

## Money and business metrics

| Indonesian (source) | English |
|---|---|
| Pendapatan, Omzet | Revenue |
| HPP (harga pokok penjualan) | COGS |
| Laba kotor | Gross profit |
| Laba bersih | Net profit |
| Margin | Margin |
| Rugi | Loss |
| Overhead umum, Biaya umum | Overhead |
| Biaya | Expense(s) |
| Biaya terserap / tak terserap | Absorbed / unabsorbed cost |
| Dihapusbukukan | Written off |
| Piutang | Receivables |
| Sisa tagihan | Balance due |
| Kredit (pembeli) | Credit |
| Termin | Terms |
| Jatuh tempo | Due |
| Umur piutang | Receivables ageing |
| PPN | VAT |
| PKP | VAT-registered |
| PPh Final | Final tax |
| Potongan PPh / bukti potong | Tax withheld / tax slip |
| Bea meterai | Stamp duty |
| Uang masuk | Cash in |
| Modal awal | Starting cash |
| Uang seharusnya | Expected cash |
| Selisih | Difference |

## Sales

| Indonesian | English |
|---|---|
| Kasir (POS) | POS |
| Pesanan, Order | Orders |
| Struk | Receipt |
| Pembeli | Customer(s) |
| Invoice, Draf, Terbit, Lunas, Belum lunas, Batal | Invoice, Draft, Issued, Paid, Unpaid, Void |
| Catat pembayaran | Record payment |
| Alokasi / lepas | Apply / unapply |
| Shift & Kas, Buka/Tutup shift | Shift & cash, Open/Close shift |
| Keranjang | Cart |
| Uang pas | Exact amount |

## Farm

| Indonesian | English |
|---|---|
| Ringkasan | Overview |
| Analitik | Analytics |
| Elemen (kebun) | Plot |
| Siklus tanam | Crop cycle |
| Tahap, Majukan tahap | Stage, Next stage |
| Laporan Kerja | Work log |
| Panen, Batch panen | Harvest, Harvest batch |
| Susut / Rusak | Spoilage |
| Susut panen | Harvest loss |
| Opname stok | Stock count |
| Lot | Lot |
| Input produksi | Inputs |
| Pupuk, Pestisida, Benih | Fertiliser, Pesticide, Seed |
| Pemupukan, Penyemprotan, Irigasi, Pengamatan, Penanaman benih | Fertilising, Pesticide spraying, Irrigation, Scouting, Seeding |
| Cek pra-panen | Pre-harvest check |
| Poligon lahan | Field boundary |
| Waktu semprot: Baik / Cukup / Tidak direkomendasikan | Spray window: Good / Fair / Not recommended |
| Cuaca & Satelit | Weather & satellite |
| Telusuri, Ketertelusuran | Trace, Traceability |

## App

| Indonesian | English |
|---|---|
| Staf | Team |
| Pengaturan | Settings |
| Log aktivitas | Activity log |
| Pasang | Install |
| Simpan, Batal, Hapus, Ubah, Kembali | Save, Cancel, Delete, Change/Edit, Back |
| Setujui, Tolak, Kembalikan | Approve, Reject, Reopen |

## Numbers

| | Indonesian | English |
|---|---|---|
| Money | Rp15.000 | Rp15,000 |
| Kg | 0,5 kg | 0.5 kg |
| Dates | 31 Des 2026 | 31 Dec 2026 |
| Chart axis | Rp277,5 jt · Rp593rb | Rp277.5M · Rp593K |

Customer documents (invoice print) follow each customer's *document language* (default Indonesian);
POS receipts stay Indonesian.
