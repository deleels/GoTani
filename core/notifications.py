"""Live notifications (rule 32, D64): computed on every poll, never stored.

Each notification carries the moment it became true (`at`); it is unread when
`at` is after the user's `notifications_seen_at`.
"""

from farm.templatetags.gotani import rupiah
from django.utils.translation import gettext as _
from datetime import datetime, time, timedelta

from django.db.models import Max, Min
from django.urls import reverse
from django.utils import timezone
from django.utils.formats import date_format

from farm.models import CropCycle, Expense, InputItem
from inventory.models import Product


def _aware(day):
    return timezone.make_aware(datetime.combine(day, time.min))


def _weather_notifications():
    """D93: from the cached forecast only — the 60 s poll must never call Open-Meteo itself."""
    from datetime import date

    from geo import services as geo

    weather = geo.cached_weather(*geo.farm_location())
    open_field = CropCycle.objects.filter(is_active=True, element__element_type="OPEN_FIELD").exists()
    return [{"kind": a["kind"], "title": a["title"], "body": a["body"], "url": reverse("geo:map"),
             "at": _aware(date.fromisoformat(a["date"]))} for a in geo.weather_alerts(weather, open_field)]


def build_notifications(roles, today=None):
    today = today or timezone.localdate()
    out = []
    if roles.farm:
        low = [i for i in InputItem.objects.all() if i.is_low_stock]
        last_move = dict(InputItem.objects.filter(pk__in=[i.pk for i in low])
                         .annotate(t=Max("ledger__timestamp")).values_list("pk", "t"))
        for item in low:
            out.append({
                "kind": "low_stock", "title": _("Stok menipis: %(name)s") % {"name": item.name},
                "body": _("Sisa %(stock_qty)s %(unit)s (batas %(min_alert_qty)s)") % {"stock_qty": format(item.stock_qty.normalize(), 'f'), "unit": item.unit, "min_alert_qty": format(item.min_alert_qty.normalize(), 'f')},
                "url": reverse("farm:input_detail", args=[item.pk]),
                "at": last_move.get(item.pk) or _aware(today),
            })
        out.extend(_weather_notifications())
        from inventory.services import low_stock_products

        for product, stock in low_stock_products():  # D98
            out.append({
                "kind": "produce_low", "title": _("Stok produk menipis: %(product)s") % {"product": product},
                "body": _("Sisa %(stock)s kg (batas %(min_alert_kg)s kg)") % {"stock": format(stock.normalize(), 'f'), "min_alert_kg": format(product.min_alert_kg.normalize(), 'f')},
                "url": reverse("farm:opname"), "at": _aware(today),
            })
        for cycle in CropCycle.objects.filter(is_active=True, expected_harvest_date__lt=today).select_related("variety", "element"):
            if cycle.is_harvest_overdue(today):
                out.append({
                    "kind": "harvest_overdue", "title": _("Panen terlewat: %(variety)s @ %(element)s") % {"variety": cycle.variety, "element": cycle.element},
                    "body": _("Perkiraan %(expected_harvest_date)s, tahap masih %(stage)s") % {"expected_harvest_date": format(cycle.expected_harvest_date, '%d-%m-%Y'), "stage": cycle.get_stage_display()},
                    "url": reverse("farm:cycle_detail", args=[cycle.pk]),
                    "at": _aware(cycle.expected_harvest_date + timedelta(days=1)),
                })
    if roles.finance:
        from invoicing.models import Invoice

        pending = Expense.objects.filter(status=Expense.Status.PENDING)
        n = pending.count()
        if n:
            out.append({
                "kind": "pending_expense", "title": _("%(n)s biaya menunggu persetujuan") % {"n": n}, "body": _("Setujui atau tolak di halaman Biaya"),
                "url": reverse("farm:expenses"), "at": pending.aggregate(t=Max("created_at"))["t"],
            })
        for inv in Invoice.objects.filter(status=Invoice.Status.ISSUED, due_date__lt=today).select_related("buyer").prefetch_related("allocations"):
            outstanding = inv.total_idr - sum((a.amount_idr for a in inv.allocations.all()), 0)
            if outstanding > 0:
                out.append({
                    "kind": "overdue_invoice", "title": _("Invoice jatuh tempo: %(number)s") % {"number": inv.number},
                    "body": _("%(buyer)s · sisa %(left)s") % {"buyer": inv.buyer, "left": rupiah(outstanding)},
                    "url": reverse("invoicing:invoice_detail", args=[inv.pk]),
                    "at": _aware(inv.due_date + timedelta(days=1)),
                })
        unpriced = Product.objects.filter(selling_price_idr__isnull=True).annotate(t=Min("lots__harvested_at"))
        for p in unpriced:
            out.append({
                "kind": "unpriced", "title": _("Belum berharga: %(p)s") % {"p": p}, "body": _("Produk tersembunyi dari Kasir sampai diberi harga"),
                "url": reverse("farm:prices"), "at": p.t or _aware(today),
            })
    out.sort(key=lambda n: n["at"], reverse=True)
    return out


def notifications_payload(user, roles, today=None):
    from .services import get_user_profile

    seen = get_user_profile(user).notifications_seen_at
    items = build_notifications(roles, today)
    for n in items:
        n["unread"] = seen is None or n["at"] > seen
        n["at"] = date_format(timezone.localtime(n["at"]), "SHORT_DATETIME_FORMAT")  # month name in the user's language (D119)
    return {"items": items, "unread": sum(1 for n in items if n["unread"])}


def mark_seen(user, now=None):
    from .services import get_user_profile

    profile = get_user_profile(user)
    profile.notifications_seen_at = now or timezone.now()
    profile.save(update_fields=["notifications_seen_at"])
