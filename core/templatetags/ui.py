"""UI helpers: inline SVG icons (lucide-style strokes) and status pills (D71)."""

from django.utils.translation import gettext_lazy as _l
import json

from django import template
from django.utils.html import format_html
from django.utils.safestring import mark_safe

register = template.Library()

ICONS = {
    "home": '<path d="M3 10.5 12 3l9 7.5"/><path d="M5 9.5V21h14V9.5"/><path d="M9.5 21v-6h5v6"/>',
    "clipboard": '<rect x="8" y="2" width="8" height="4" rx="1"/><path d="M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2"/><path d="M9 12h6M9 16h6"/>',
    "sprout": '<path d="M7 20h10"/><path d="M12 20v-9"/><path d="M12 11C12 7 9 5 5 5c0 4 3 6 7 6Z"/><path d="M12 13c0-4 3-6 7-6 0 4-3 6-7 6Z"/>',
    "map": '<path d="m9 4-6 2v14l6-2 6 2 6-2V4l-6 2-6-2Z"/><path d="M9 4v14M15 6v14"/>',
    "cart": '<circle cx="9" cy="20" r="1.5"/><circle cx="18" cy="20" r="1.5"/><path d="M2 3h3l2.7 12.4a2 2 0 0 0 2 1.6h8.6a2 2 0 0 0 2-1.5L22 7H6"/>',
    "receipt": '<path d="M5 2v20l3-2 2 2 2-2 2 2 2-2 3 2V2l-3 2-2-2-2 2-2-2-2 2Z"/><path d="M9 8h6M9 12h6"/>',
    "file": '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/><path d="M14 2v6h6M9 13h6M9 17h6"/>',
    "users": '<circle cx="9" cy="8" r="4"/><path d="M2 21a7 7 0 0 1 14 0"/><path d="M16 4a4 4 0 0 1 0 8M22 21a7 7 0 0 0-4-6.3"/>',
    "wallet": '<path d="M19 7V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-3"/><path d="M21 8h-5a4 4 0 0 0 0 8h5Z"/>',
    "tag": '<path d="M20.6 13.4 13.4 20.6a2 2 0 0 1-2.8 0L3 13V3h10l7.6 7.6a2 2 0 0 1 0 2.8Z"/><circle cx="7.5" cy="7.5" r="1.5"/>',
    "chart": '<path d="M3 3v18h18"/><path d="m7 15 4-4 3 3 5-6"/>',
    "search": '<circle cx="11" cy="11" r="7"/><path d="m21 21-4.3-4.3"/>',
    "user-cog": '<circle cx="9" cy="7" r="4"/><path d="M3 21a6 6 0 0 1 9.5-4.9"/><circle cx="18" cy="17" r="3"/><path d="M18 12v2M18 20v2M13 17h2M21 17h2"/>',
    "settings": '<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"/>',
    "bell": '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.9 1.9 0 0 0 3.4 0"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "panel": '<rect x="3" y="3" width="18" height="18" rx="2"/><path d="M9 3v18"/>',
    "chevron": '<path d="m6 9 6 6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "dots": '<circle cx="5" cy="12" r="1"/><circle cx="12" cy="12" r="1"/><circle cx="19" cy="12" r="1"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "x": '<path d="M18 6 6 18M6 6l12 12"/>',
    "minus": '<path d="M5 12h14"/>',
    "trash": '<path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3"/>',
    "check": '<path d="m5 12.5 4.5 4.5L19 7.5"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
    "arrow": '<path d="M7 17 17 7M8 7h9v9"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',
    "key": '<circle cx="7.5" cy="15.5" r="5.5"/><path d="m21 2-9.6 9.6M15.5 7.5l3 3L22 7l-3-3"/>',
    "leaf": '<path d="M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.5 19 2c1 2 2 4.2 2 8 0 5.5-4.8 10-10 10Z"/><path d="M2 21c0-3 1.9-5.4 5.2-6.1C9.6 14.4 12 13 13 12"/>',
    "eye": '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z"/><circle cx="12" cy="12" r="3"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
    "lock": '<rect x="4" y="11" width="16" height="10" rx="2"/><path d="M8 11V7a4 4 0 0 1 8 0v4"/>',
    "alert": '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
}


@register.simple_tag
def icon(name, size=18, cls=""):
    body = ICONS.get(name, ICONS["dots"])
    return format_html(
        '<svg xmlns="http://www.w3.org/2000/svg" width="{0}" height="{0}" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round" '
        'class="shrink-0 gt-ico {1}" style="--ico:{0}px" aria-hidden="true">{2}</svg>',
        size, cls, mark_safe(body),
    )


# Design-system pill tones (D104): yellow/red/green are gt-pill modifiers, blue/teal/gray are app tokens.
PILL_TONES = {
    "yellow": "gt-pill--progress",
    "blue": "app-pill--info",
    "teal": "app-pill--partial",
    "red": "gt-pill--pending",
    "orange": "gt-pill--late",
    "green": "gt-pill--completed",
    "gray": "app-pill--neutral",
}
# Live states get the leading dot.
DOT_STATUSES = {"IN_PROGRESS", "OVERDUE"}
STATUS_TONES = {
    "PENDING": "yellow", "DRAFT": "gray", "UNPAID": "blue",
    "APPROVED": "blue", "ISSUED": "blue", "PARTIALLY_PAID": "teal",
    "OVERDUE": "orange", "VOID": "red", "VOIDED": "red", "REJECTED": "red", "INACTIVE": "red",
    "PAID": "green", "COMPLETED": "green", "ACTIVE": "green",
}


@register.simple_tag
def pill(text, status=None, tone=None):
    tone = tone or STATUS_TONES.get(str(status or text).upper(), "gray")
    dot = " gt-pill--dot" if str(status or text).upper() in DOT_STATUSES else ""
    return format_html(
        '<span class="gt-pill {} app-pill{}">{}</span>',
        PILL_TONES.get(tone, PILL_TONES["gray"]), dot, text,
    )


# KPI value tone from the old colour utility passed as `cls` (Phase 3).
@register.filter
def value_tone(cls):
    cls = str(cls or "")
    if "red-" in cls:
        return "app-value--danger"
    if "amber-" in cls:
        return "app-value--warning"
    return ""


@register.simple_tag(takes_context=True)
def subtitle(context, owner, manager=None, worker=None, cashier=None):
    """Role-specific page subtitle (D68)."""
    roles = context.get("roles")
    label = roles.label if roles else ""
    return {"OWNER": owner, "MANAGER": manager or owner, "WORKER": worker or manager or owner,
            "CASHIER": cashier or owner}.get(label, owner)


@register.simple_tag
def void_confirm(title, body, action, level=1):
    """Shared-dialog attributes for a void (D94): fixed reason list, note required for "Lainnya"."""
    from farm.models import VoidReason

    return format_html(
        'data-confirm-title="{}" data-confirm="{}" data-confirm-action="{}" data-confirm-level="{}" data-confirm-danger '
        'data-confirm-choices="{}" data-confirm-choice-name="void_reason" data-confirm-other="{}" data-confirm-reason="Catatan"',
        title, body, action, level, json.dumps([[v, str(lbl)] for v, lbl in VoidReason.choices]), VoidReason.OTHER,
    )


# Strings used by the static scripts (confirm.js, stage.js, warnings.js); `{% js_i18n %}` ships them as
# window.GT_I18N so `gtT("…")` can translate them in the browser (Phase 7).
JS_MSGIDS = [
    _l("Alasan"), _l("Saya sudah memeriksa data ini"), _l("Batal"), _l("Konfirmasi"), _l("Lanjutkan"),
    _l("Tahap yang dilewati: %(list)s."), _l("Peringatan: belum ada cek pra-panen."),
    _l("Cek pra-panen terakhir: %(pct)s%% lengkap."), _l("Cek pra-panen terakhir: %(pct)s%% lengkap (belum lengkap)."),
    _l("Pembersihan menutup siklus; tidak ada panen lagi setelah ini."),
    _l("Biaya belum terserap %(amount)s dicatat sebagai biaya siklus tak terserap (write-off)."),
    _l("Tidak ada biaya yang belum terserap."),
    _l("Pembatalan hanya bisa oleh OWNER/MANAGER di hari yang sama, selama belum ada catatan baru."),
    _l("Majukan tahap?"), _l("Majukan ke %(stage)s"),
    _l("Harga naik %(pct)s: %(old)s → %(new)s per kg."), _l("Harga turun %(pct)s: %(old)s → %(new)s per kg."),
    _l("Harga di bawah HPP rata-rata lot yang masih ada (%(cogs)s/kg); setiap kg terjual merugi."),
    _l("Periksa harga baru"), _l("Simpan harga"), _l("Harga beli tidak biasa"),
    _l("%(item)s: %(cost)s per %(unit)s, %(pct)s lebih mahal dari rata-rata %(avg)s."),
    _l("%(item)s: %(cost)s per %(unit)s, %(pct)s lebih murah dari rata-rata %(avg)s."),
    _l("Harga ini mengubah biaya rata-rata input dan biaya pemakaian berikutnya. Periksa satuan dan angkanya."),
    _l("Simpan pembelian"), _l("Wajib diisi."), _l("Angka tidak valid."), _l("Periksa kolom yang ditandai."),
    _l("Tutup"), _l("Email tidak valid."), _l("Minimal %(min)s."), _l("Maksimal %(max)s."),
    _l("Minimal %(n)s karakter."), _l("Maksimal %(n)s karakter."), _l("Format tidak sesuai."),
    # Catat pembayaran (D114): payment_form.html and warnings.js
    _l("%(invoice)s hanya bersisa %(balance)s."),
    _l("Invoice mendapat %(applied)s, tetapi uang yang diterima hanya %(amount)s."),
    _l("Pakai untuk %(invoices)s (jatuh tempo paling awal dulu)"), _l("Simpan sebagai kredit %(buyer)s"),
    _l("Sisa %(left)s belum dialokasikan"), _l("Dari %(amount)s yang diterima, %(applied)s masuk ke invoice."),
    _l("Simpan pembayaran"), _l("Simpan %(left)s sebagai kredit?"),
    _l("%(amount)s melebihi semua tagihan terbuka %(buyer)s sebesar %(left)s."),
    _l("Sisanya disimpan sebagai kredit %(buyer)s dan bisa dipakai untuk invoice berikutnya; tidak bisa dicairkan."),
    _l("Simpan dengan kredit"),
]


@register.simple_tag
def jt(text):
    """Translation for a JS string literal inside a template script: `'{% jt "Batal" %}'` (escapejs'd)."""
    from django.utils.html import escapejs
    from django.utils.translation import gettext

    return mark_safe(escapejs(gettext(text)))


@register.simple_tag
def js_i18n():
    """window.GT_I18N for the static scripts (only when the active language is not the source language)."""
    from django.utils.translation import get_language, gettext, override

    lang = (get_language() or "id")[:2]
    data = {}
    if lang != "id":
        with override("id"):  # Indonesian is the source text, so this yields the msgids
            keys = [str(m) for m in JS_MSGIDS]
        data = {k: gettext(k) for k in keys}
    return mark_safe("<script>window.GT_LANG=%s;window.GT_I18N=%s;</script>" % (json.dumps(lang), json.dumps(data, ensure_ascii=False).replace("</", "<\\/")))


@register.simple_tag
def info_tip(text, label=_l("Penjelasan")):
    """Footnote behind an info chip (user preference): shown on hover, focus or tap via confirm.js."""
    return format_html(
        '<button type="button" class="gt-tip" data-tip="{}" aria-label="{}" aria-describedby="gt-tip">{}</button>',
        text, label, icon("info", 14),
    )


def _arc_point(frac):
    """Point on the Gauge's half circle (centre 100,100, r 80) at `frac` of the way from left to right."""
    import math

    a = math.pi * (1 - max(0.0, min(1.0, frac)))
    return f"{100 + 80 * math.cos(a):.2f} {100 - 80 * math.sin(a):.2f}"


@register.inclusion_tag("components/gauge.html")
def gauge(parts, caption="", uid="g"):
    """Design-system Gauge (half donut) for a share of a whole (user preference: never a doughnut).

    `parts`: up to two (label, value) pairs → green-600, green-800; whatever is left of the whole stays hatched.
    The centre shows the first part's share."""
    values = [max(float(v or 0), 0.0) for _, v in parts]
    total = sum(values)
    fracs = [v / total for v in values] if total else [0.0 for _ in values]
    tones = ["done", "prog"]
    segs, acc = [], 0.0
    for (label, value), frac, tone in zip(parts, fracs, tones):
        acc += frac
        segs.append({"label": label, "value": value, "tone": tone, "end": acc, "pct": round(frac * 100)})
    # Draw the longest (cumulative) arc first so each earlier part sits on top with its round cap.
    paths = [{"tone": s["tone"], "d": f"M20 100 A80 80 0 0 1 {_arc_point(s['end'])}"} for s in reversed(segs) if s["end"] > 0]
    return {"segs": segs, "paths": paths, "lead": segs[0] if segs and total else None, "caption": caption,
            "uid": uid, "empty": not total}
