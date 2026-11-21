"""Ringkasan (home) numbers (D70). Read-only aggregates; money only for OWNER/MANAGER."""

from django.utils.translation import gettext_lazy as _l
from django.utils.translation import gettext as _
from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models import Count, Sum
from django.utils import timezone

from farm.models import ActivityLog, CropCycle, Expense, FarmElement, HarvestBatch, PreHarvestCheck
from inventory.models import SpoilageRecord

ZERO = Decimal("0")

WORK_TYPES = [
    # key (Laporan Kerja tab), label, ActivityType
    ("pemupukan", _l("Pemupukan"), ActivityLog.ActivityType.FERTILIZER),
    ("pestisida", _l("Pestisida"), ActivityLog.ActivityType.PESTICIDE),
    ("irigasi", _l("Irigasi"), ActivityLog.ActivityType.IRRIGATION),
    ("scouting", _l("Scouting"), ActivityLog.ActivityType.SCOUTING),
    ("penanaman", _l("Penanaman"), ActivityLog.ActivityType.SEEDING),
]


def weekly_work(now=None):
    """Last 7 days of field work by Laporan Kerja type."""
    now = now or timezone.now()
    since = now - timedelta(days=7)
    counts = dict(ActivityLog.objects.filter(logged_at__gte=since).values("activity_type")
                  .annotate(n=Count("id")).values_list("activity_type", "n"))
    rows = [{"key": k, "label": label, "count": counts.get(t, 0)} for k, label, t in WORK_TYPES]
    rows += [
        {"key": "prapanen", "label": _("Pra-panen"), "count": PreHarvestCheck.objects.filter(recorded_at__gte=since).count()},
        {"key": "panen", "label": _("Panen"), "count": HarvestBatch.objects.filter(harvested_at__gte=since).count()},
        {"key": "susut", "label": _("Susut"), "count": SpoilageRecord.objects.filter(recorded_at__gte=since).count()},
    ]
    return rows


def cycles_by_stage():
    counts = dict(CropCycle.objects.filter(is_active=True).values("stage").annotate(n=Count("id")).values_list("stage", "n"))
    return [{"stage": label, "count": counts.get(value, 0)} for value, label in CropCycle.Stage.choices
            if value != CropCycle.Stage.CLEARING]


def element_counters():
    counts = dict(FarmElement.objects.filter(is_active=True).values("element_type").annotate(n=Count("id"))
                  .values_list("element_type", "n"))
    return [{"type": label, "count": counts.get(value, 0)} for value, label in FarmElement.ElementType.choices
            if counts.get(value)]


def finance_card():
    """All-time sales (net of tax) and approved expenses, general vs element-specific."""
    from invoicing.models import Invoice, InvoiceLine
    from pos.models import SaleOrder

    pos = SaleOrder.objects.filter(status=SaleOrder.Status.COMPLETED).aggregate(s=Sum("total_revenue_idr"))["s"] or ZERO
    inv = InvoiceLine.objects.filter(invoice__status=Invoice.Status.ISSUED).aggregate(s=Sum("net_revenue_idr"))["s"] or ZERO
    approved = Expense.objects.filter(status=Expense.Status.APPROVED)
    general = approved.filter(scope=Expense.Scope.GENERAL).aggregate(s=Sum("amount_idr"))["s"] or ZERO
    element = approved.filter(scope=Expense.Scope.ELEMENT).aggregate(s=Sum("amount_idr"))["s"] or ZERO
    return {"sales": pos + inv, "sales_pos": pos, "sales_invoice": inv, "expenses": general + element,
            "expenses_general": general, "expenses_element": element}


DAYS = [_l("Senin"), _l("Selasa"), _l("Rabu"), _l("Kamis"), _l("Jumat"), _l("Sabtu"), _l("Minggu")]
MONTHS_LONG = [_l("Januari"), _l("Februari"), _l("Maret"), _l("April"), _l("Mei"), _l("Juni"), _l("Juli"),
               _l("Agustus"), _l("September"), _l("Oktober"), _l("November"), _l("Desember")]


def day_label(day):
    """"Kamis 19 November" / "Thursday 19 November", in the user's language."""
    return f"{DAYS[day.weekday()]} {day.day} {MONTHS_LONG[day.month - 1]}"


def greeting(now=None):
    """Greeting by Jakarta local hour (Ringkasan page head), in the user's language."""
    hour = timezone.localtime(now).hour
    if hour < 11:
        return _("Selamat pagi")
    if hour < 15:
        return _("Selamat siang")
    if hour < 18:
        return _("Selamat sore")
    return _("Selamat malam")


def staff_list(limit=6):
    """Active staff for the Ringkasan team card: name, initial, role label."""
    users = get_user_model().objects.filter(is_active=True).prefetch_related("groups").order_by("first_name", "username")
    rows = []
    for u in users[:limit]:
        name = u.get_full_name() or u.username
        group = next(iter(u.groups.all()), None)
        role = group.name if group else ("OWNER" if u.is_superuser else "")
        rows.append({"name": name, "initial": name[:1].upper(), "role": role.lower().capitalize()})
    return rows


def home_summary(roles):
    today = timezone.localdate()
    active = list(CropCycle.objects.filter(is_active=True).select_related("variety", "element"))
    for c in active:
        c.days = c.days_growing(today)
        c.overdue = c.is_harvest_overdue(today)
    ctx = {
        "work": weekly_work(), "stages": cycles_by_stage(), "elements": element_counters(),
        "staff_count": get_user_model().objects.filter(is_active=True).count(),
        "active_cycles": sorted(active, key=lambda c: (c.expected_harvest_date or today + timedelta(days=3650))),
        "low_stock": [], "today": today, "greeting": greeting(), "today_label": day_label(today), "staff": staff_list(),
    }
    work = ctx["work"]
    field = [w for w in work if w["key"] not in ("prapanen", "panen", "susut")]
    ctx["work_field"] = {"count": sum(w["count"] for w in field), "rows": field}
    ctx["work_other"] = [w for w in work if w["key"] in ("prapanen", "panen", "susut")]
    from farm.models import InputItem

    ctx["low_stock"] = [i for i in InputItem.objects.all() if i.is_low_stock]
    if roles.finance:
        ctx["finance"] = finance_card()
    return ctx
