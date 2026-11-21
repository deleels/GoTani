"""App date formats (D119): one short form on screens, one long form on printed documents.

Templates use the named formats (`|date:"SHORT_DATE_FORMAT"`), never a literal pattern, so every
page writes a date the same way; month names follow the active language (Nov / Agu / Mei …).
English uses the same day-first patterns (see ../en/formats.py). Number formats are not set here:
Django's own locale modules keep supplying them.
"""

SHORT_DATE_FORMAT = "d M Y"  # 21 Nov 2026 — tables, lists, cards
SHORT_DATETIME_FORMAT = "d M Y H:i"  # 21 Nov 2026 14:05
DATE_FORMAT = "d F Y"  # 21 November 2026 — receipts, invoices
DATETIME_FORMAT = "d F Y H:i"
MONTH_DAY_FORMAT = "d M"  # 21 Nov — chart axes, same-year detail
TIME_FORMAT = "H:i"
