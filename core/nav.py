"""Sidebar navigation (D67), filtered by role. Items whose URL isn't built yet are skipped."""

from django.utils.translation import gettext_lazy as _l
from django.urls import NoReverseMatch, reverse

from farm.access import get_roles
from farm.bootstrap import ROLE_OWNER

# (label, url name, icon, flag, active url-name prefixes)
GROUPS = [
    (_l("DASBOR"), [
        (_l("Ringkasan"), "home", "home", "any", ("home",)),
        (_l("Laporan Kerja"), "farm:activities", "clipboard", "farm",
         ("farm:activities", "farm:preharvest", "farm:harvests")),
        (_l("Cuaca & Satelit"), "geo:map", "map", "farm", ("geo:",)),
    ]),
    # D125: one home per area; the pages inside an area share a tab strip (SECTIONS below).
    (_l("KEBUN"), [
        (_l("Elemen & Siklus"), "farm:cycles", "sprout", "farm",
         ("farm:cycles", "farm:element", "farm:cycle", "farm:variety")),
        (_l("Inventaris Input"), "farm:inputs", "leaf", "farm", ("farm:input",)),
        (_l("Stok Hasil Panen"), "farm:produce", "tag", "farm",
         ("farm:produce", "farm:spoilage", "farm:opname", "farm:prices")),
    ]),
    (_l("PENJUALAN"), [
        (_l("Kasir"), "pos:register", "cart", "pos", ("pos:",)),
        (_l("Grosir"), "invoicing:invoices", "file", "finance", ("invoicing:",)),
    ]),
    (_l("KEUANGAN"), [
        (_l("Biaya"), "farm:expenses", "wallet", "finance", ("farm:expense",)),
        (_l("Analitik"), "dashboard", "chart", "finance", ("dashboard", "omzet_pph")),
    ]),
    (_l("ALAT"), [
        (_l("Telusuri"), "reports:trace_index", "search", "finance", ("reports:",)),
        (_l("Log aktivitas"), "core:audit_log", "clipboard", "finance", ("core:audit_log",)),
        (_l("Staf"), "core:staff", "user-cog", "owner", ("core:staff",)),
        (_l("Pengaturan"), "core:settings", "settings", "owner", ("core:settings",)),
    ]),
]

# In-page tab strips (D125): (section key, [(label, url name, flag, active url-name prefixes)]).
SECTIONS = [
    ("kasir", [
        (_l("Kasir"), "pos:register", "pos", ("pos:register",)),
        (_l("Pesanan"), "pos:orders", "pos", ("pos:orders", "pos:receipt")),
        (_l("Shift & Kas"), "pos:shift", "pos", ("pos:shift",)),
    ]),
    ("grosir", [
        (_l("Invoice"), "invoicing:invoices", "finance", ("invoicing:invoice",)),
        (_l("Pembeli"), "invoicing:buyers", "finance", ("invoicing:buyer",)),
    ]),
    ("stok", [
        (_l("Stok"), "farm:produce", "farm", ("farm:produce",)),
        (_l("Susut"), "farm:spoilage", "farm", ("farm:spoilage",)),
        (_l("Opname"), "farm:opname", "farm", ("farm:opname",)),
        (_l("Harga"), "farm:prices", "finance", ("farm:prices",)),
    ]),
    ("analitik", [
        (_l("Ringkasan"), "dashboard", "finance", ("dashboard",)),
        (_l("Omzet & PPh"), "omzet_pph", "finance", ("omzet_pph",)),
    ]),
]


def _url(name):
    try:
        return reverse(name)
    except NoReverseMatch:
        return None


def _allowed(roles, flag):
    if flag == "any":
        return roles.farm or roles.finance
    if flag == "owner":
        return roles.has(ROLE_OWNER)
    return getattr(roles, flag)


def _active(view_name, prefixes):
    return bool(view_name) and any(view_name.startswith(p) for p in prefixes)


def build_nav(request):
    roles = get_roles(request)
    match = getattr(request, "resolver_match", None)
    view_name = match.view_name if match else ""
    groups = []
    for title, items in GROUPS:
        out = []
        for entry in items:
            label, name, icon, flag, prefixes = entry[:5]
            if not _allowed(roles, flag):
                continue
            if name is None:
                children = []
                for c_label, c_name, c_prefixes in entry[5]:
                    url = _url(c_name)
                    if url:
                        children.append({"label": c_label, "url": url, "active": _active(view_name, c_prefixes)})
                if children:
                    out.append({"label": label, "icon": icon, "children": children,
                                "active": any(c["active"] for c in children)})
                continue
            url = _url(name)
            if url:
                out.append({"label": label, "url": url, "icon": icon, "active": _active(view_name, prefixes)})
        if out:
            groups.append({"title": title, "items": out})
    return groups


def build_section_tabs(request):
    """The tab strip of the area the current page belongs to, or None (D125)."""
    roles = get_roles(request)
    match = getattr(request, "resolver_match", None)
    view_name = match.view_name if match else ""
    if not view_name:
        return None
    for key, tabs in SECTIONS:
        if not any(_active(view_name, prefixes) for _, _, _, prefixes in tabs):
            continue
        out = []
        for label, name, flag, prefixes in tabs:
            url = _url(name)
            if url and _allowed(roles, flag):
                out.append({"label": label, "url": url, "active": _active(view_name, prefixes)})
        if len(out) < 2:
            return None
        section = {"key": key, "tabs": out}
        if key == "kasir":
            from pos.models import Shift

            section["shift_open"] = Shift.objects.filter(is_open=True).exists()
        return section
    return None


def nav_context(request):
    if not request.user.is_authenticated:
        return {}
    from .services import get_farm_profile

    return {"nav_groups": build_nav(request), "section_tabs": build_section_tabs(request),
            "farm_profile": get_farm_profile()}
