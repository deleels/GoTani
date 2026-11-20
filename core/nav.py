"""Sidebar navigation (D67), filtered by role. Items whose URL isn't built yet are skipped."""

from django.utils.translation import gettext_lazy as _l
from django.urls import NoReverseMatch, reverse

from farm.access import get_roles
from farm.bootstrap import ROLE_OWNER

# (label, url name, icon, flag, active url-name prefixes)
GROUPS = [
    (_l("DASBOR"), [
        (_l("Ringkasan"), "home", "home", "any", ("home", "dashboard")),
        (_l("Laporan Kerja"), "farm:activities", "clipboard", "farm", ("farm:activities", "farm:preharvest")),
        (_l("Aset Kebun"), None, "sprout", "farm", (), [
            (_l("Elemen & Siklus"), "farm:cycles", ("farm:cycles", "farm:element", "farm:cycle")),
            (_l("Inventaris Input"), "farm:inputs", ("farm:input",)),
            (_l("Panen"), "farm:harvests", ("farm:harvests",)),
            (_l("Susut / Rusak"), "farm:spoilage", ("farm:spoilage",)),
            (_l("Opname Stok"), "farm:opname", ("farm:opname",)),
        ]),
        (_l("Cuaca & Satelit"), "geo:map", "map", "farm", ("geo:",)),
    ]),
    (_l("PENJUALAN"), [
        (_l("Kasir (POS)"), "pos:register", "cart", "pos", ("pos:register",)),
        (_l("Pesanan"), "pos:orders", "receipt", "pos", ("pos:orders", "pos:receipt")),
        (_l("Shift & Kas"), "pos:shift", "wallet", "pos", ("pos:shift",)),
        (_l("Invoice"), "invoicing:invoices", "file", "finance", ("invoicing:invoice",)),
        (_l("Pembeli"), "invoicing:buyers", "users", "finance", ("invoicing:buyer",)),
    ]),
    (_l("KEUANGAN"), [
        (_l("Biaya"), "farm:expenses", "wallet", "finance", ("farm:expense",)),
        (_l("Harga Produk"), "farm:prices", "tag", "finance", ("farm:prices",)),
        (_l("Analitik"), "dashboard", "chart", "finance", ("dashboard",)),
        (_l("Omzet & PPh"), "omzet_pph", "file", "finance", ("omzet_pph",)),
    ]),
    (_l("ALAT"), [
        (_l("Telusuri"), "reports:trace_index", "search", "finance", ("reports:",)),
        (_l("Log aktivitas"), "core:audit_log", "clipboard", "finance", ("core:audit_log",)),
        (_l("Staf"), "core:staff", "user-cog", "owner", ("core:staff",)),
        (_l("Pengaturan"), "core:settings", "settings", "owner", ("core:settings",)),
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


def nav_context(request):
    if not request.user.is_authenticated:
        return {}
    from .services import get_farm_profile

    return {"nav_groups": build_nav(request), "farm_profile": get_farm_profile()}
