"""Read-only reporting services: traceability (D29) and analytics KPIs (D28).

All money math stays in Decimal; whole-Rupiah rounding happens only in templates.
Date ranges are Asia/Jakarta calendar days, inclusive on both ends.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal

from django.db.models import Prefetch, Sum
from django.utils import timezone

from farm.models import ActivityLog, CropCycle, Expense, HarvestBatch, InputPurchase
from inventory.models import SpoilageRecord, StockLot
from pos.models import LineLotAllocation, OrderLineItem, SaleOrder
from pos.services import JAKARTA, jakarta_date

ZERO = Decimal("0")
MONEY_STEP = Decimal("0.01")


def _money(value) -> Decimal:
    return Decimal(value or 0).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


def _local(dt):
    return timezone.localtime(dt, JAKARTA) if dt else None


# ---------------------------------------------------------------------------
# Forward traceability: cycle → activities → expenses → batches → lots → spoilage → sales
# ---------------------------------------------------------------------------


@dataclass
class LotTrace:
    lot: StockLot
    spoilages: list
    allocations: list  # LineLotAllocation with line.order
    sold_kg: Decimal = ZERO
    voided_kg: Decimal = ZERO
    spoiled_kg: Decimal = ZERO


@dataclass
class BatchTrace:
    batch: HarvestBatch
    expenses: list
    lots: list[LotTrace]


@dataclass
class ForwardTrace:
    cycle: CropCycle
    activities: list
    batches: list[BatchTrace]
    unabsorbed_expenses: list
    totals: dict = field(default_factory=dict)


def forward_trace(cycle: CropCycle) -> ForwardTrace:
    cycle = CropCycle.objects.select_related("element", "variety").get(pk=cycle.pk)
    activities = list(
        ActivityLog.objects.filter(cycle=cycle)
        .select_related("input_item", "recorded_by")
        .prefetch_related("usage_expenses")
        .order_by("logged_at", "id")
    )
    batches = (
        HarvestBatch.objects.filter(cycle=cycle)
        .order_by("harvested_at", "id")
        .prefetch_related(
            Prefetch("absorbed_expenses", queryset=Expense.objects.select_related("category").order_by("date_incurred", "id")),
            Prefetch(
                "lots",
                queryset=StockLot.objects.select_related("product__variety").prefetch_related(
                    Prefetch("spoilages", queryset=SpoilageRecord.objects.order_by("recorded_at", "id")),
                    Prefetch(
                        "allocations",
                        queryset=LineLotAllocation.objects.select_related("line__order", "line__product").order_by(
                            "line__order__created_at", "id"
                        ),
                    ),
                ).order_by("product__grade"),
            ),
        )
    )

    totals = defaultdict(Decimal)
    batch_traces = []
    for batch in batches:
        lot_traces = []
        for lot in batch.lots.all():
            allocations = list(lot.allocations.all())
            spoilages = list(lot.spoilages.all())
            lt = LotTrace(lot=lot, spoilages=spoilages, allocations=allocations)
            for a in allocations:
                if a.line.order.status == SaleOrder.Status.COMPLETED:
                    lt.sold_kg += a.qty_kg
                    totals["revenue"] += a.line.net_revenue_idr * a.qty_kg / a.line.qty_kg
                    totals["cogs"] += a.qty_kg * a.unit_cogs_idr
                else:
                    lt.voided_kg += a.qty_kg
            lt.spoiled_kg = sum((s.qty_kg for s in spoilages), ZERO)
            totals["sold_kg"] += lt.sold_kg
            totals["spoiled_kg"] += lt.spoiled_kg
            totals["spoilage_loss"] += sum((s.loss_idr for s in spoilages), ZERO)
            totals["remaining_kg"] += lot.qty_remaining_kg
            lot_traces.append(lt)
        totals["harvested_kg"] += batch.high_quality_kg + batch.low_quality_kg
        totals["absorbed_cost"] += batch.absorbed_cost_idr
        batch_traces.append(BatchTrace(batch=batch, expenses=list(batch.absorbed_expenses.all()), lots=lot_traces))

    unabsorbed = list(
        Expense.objects.filter(cycle=cycle, status=Expense.Status.APPROVED, absorbed_by__isnull=True)
        .select_related("category")
        .order_by("date_incurred", "id")
    )
    totals["unabsorbed_cost"] = sum((e.amount_idr for e in unabsorbed), ZERO)
    totals["revenue"] = _money(totals["revenue"])
    totals["cogs"] = _money(totals["cogs"])
    totals["gross_margin"] = totals["revenue"] - totals["cogs"]
    return ForwardTrace(cycle, activities, batch_traces, unabsorbed, dict(totals))


FORWARD_CSV_HEADER = ["Bagian", "Tanggal", "Referensi", "Keterangan", "Kg", "Nilai (Rp)"]


def forward_csv_rows(trace: ForwardTrace):
    c = trace.cycle
    yield FORWARD_CSV_HEADER
    yield ["SIKLUS", c.planting_start_date.isoformat(), f"CYC-{c.pk}",
           f"{c.variety} @ {c.element} ({c.element.get_element_type_display()}) — tahap {c.get_stage_display()}", "", ""]
    for a in trace.activities:
        cost = sum((e.amount_idr for e in a.usage_expenses.all()), ZERO)
        desc = a.get_activity_type_display()
        if a.input_item:
            desc += f" — {a.input_item.name}"
        yield ["KEGIATAN", _local(a.logged_at).isoformat(timespec="minutes"), f"ACT-{a.pk}",
               f"{desc} ({a.get_stage_at_activity_display()})", f"{a.quantity} {a.unit}".strip() if a.input_item else "",
               cost or ""]
    for bt in trace.batches:
        b = bt.batch
        for e in bt.expenses:
            yield ["BIAYA_TERSERAP", e.date_incurred.isoformat(), f"HB-{b.pk}",
                   f"{e.category} — {e.narration or e.vendor or e.get_source_display()}", "", e.amount_idr]
        yield ["PANEN", _local(b.harvested_at).isoformat(timespec="minutes"), f"HB-{b.pk}",
               f"HIGH {b.high_quality_kg} kg @ {b.unit_cogs_high_idr}; LOW {b.low_quality_kg} kg @ {b.unit_cogs_low_idr}; susut {b.harvest_loss_kg} kg",
               b.high_quality_kg + b.low_quality_kg, b.absorbed_cost_idr]
        for lt in bt.lots:
            lot = lt.lot
            yield ["LOT", _local(lot.harvested_at).date().isoformat(), f"LOT-{lot.pk}",
                   f"{lot.product} — HPP/kg {lot.unit_cogs_idr}; sisa {lot.qty_remaining_kg} kg", lot.qty_initial_kg, ""]
            for s in lt.spoilages:
                yield ["SUSUT", _local(s.recorded_at).isoformat(timespec="minutes"), f"LOT-{lot.pk}",
                       f"{s.get_reason_display()} {s.notes}".strip(), -s.qty_kg, s.loss_idr]
            for a in lt.allocations:
                order = a.line.order
                yield ["PENJUALAN", _local(order.created_at).isoformat(timespec="minutes"), order.order_number,
                       f"{a.line.product} @ {a.line.unit_price_idr}/kg — {order.get_status_display()}",
                       -a.qty_kg, _money(a.qty_kg * a.line.unit_price_idr)]
    for e in trace.unabsorbed_expenses:
        yield ["BIAYA_TAK_TERSERAP", e.date_incurred.isoformat(), f"EXP-{e.pk}",
               f"{e.category} — {e.narration or e.vendor or e.get_source_display()}", "", e.amount_idr]


# ---------------------------------------------------------------------------
# Backward traceability: receipt line → lots → batch → cycle → element
# ---------------------------------------------------------------------------


def backward_trace(order: SaleOrder) -> SaleOrder:
    """The order with lines → allocations → lot → batch → cycle → element prefetched."""
    return (
        SaleOrder.objects.select_related("cashier", "voided_by")
        .prefetch_related(
            Prefetch(
                "lines",
                queryset=OrderLineItem.objects.select_related("product__variety").prefetch_related(
                    Prefetch(
                        "allocations",
                        queryset=LineLotAllocation.objects.select_related(
                            "lot__product", "lot__harvest_batch__cycle__element", "lot__harvest_batch__cycle__variety"
                        ).order_by("lot__harvested_at", "lot_id"),
                    )
                ),
            )
        )
        .get(pk=order.pk)
    )


BACKWARD_CSV_HEADER = [
    "Nomor order", "Tanggal order", "Status", "Produk", "Kg baris", "Harga/kg", "Lot", "Kg dari lot",
    "HPP/kg lot", "Batch panen", "Tanggal panen", "Siklus", "Varietas", "Tahap siklus", "Elemen", "Jenis elemen",
]


def backward_csv_rows(order: SaleOrder):
    yield BACKWARD_CSV_HEADER
    for line in order.lines.all():
        for a in line.allocations.all():
            batch = a.lot.harvest_batch
            cycle = batch.cycle
            yield [
                order.order_number, _local(order.created_at).isoformat(timespec="minutes"), order.get_status_display(),
                str(line.product), line.qty_kg, line.unit_price_idr, f"LOT-{a.lot_id}", a.qty_kg, a.unit_cogs_idr,
                f"HB-{batch.pk}", _local(batch.harvested_at).date().isoformat(), f"CYC-{cycle.pk}", cycle.variety.name,
                cycle.get_stage_display(), cycle.element.name, cycle.element.get_element_type_display(),
            ]


# ---------------------------------------------------------------------------
# Analytics (D28)
# ---------------------------------------------------------------------------


def day_bounds(start, end):
    """[start 00:00, end+1 00:00) in Asia/Jakarta as aware datetimes."""
    lo = datetime.combine(start, time.min, tzinfo=JAKARTA)
    hi = datetime.combine(end + timedelta(days=1), time.min, tzinfo=JAKARTA)
    return lo, hi


def _sum(qs, field_name) -> Decimal:
    return qs.aggregate(s=Sum(field_name))["s"] or ZERO


def dashboard_metrics(start, end) -> dict:
    lo, hi = day_bounds(start, end)
    orders = SaleOrder.objects.filter(status=SaleOrder.Status.COMPLETED, created_at__gte=lo, created_at__lt=hi)

    revenue = _sum(orders, "total_revenue_idr")
    cogs = _sum(orders, "total_cogs_idr")
    gross = revenue - cogs
    spoilage = _sum(SpoilageRecord.objects.filter(recorded_at__gte=lo, recorded_at__lt=hi), "loss_idr")
    approved = Expense.objects.filter(status=Expense.Status.APPROVED)
    # Rule 8: leftovers of cycles closed in the period are written off.
    unabsorbed = _sum(
        approved.filter(absorbed_by__isnull=True, cycle__is_active=False, cycle__closed_at__gte=lo, cycle__closed_at__lt=hi),
        "amount_idr",
    )
    # Rule 7: GENERAL expenses and ELEMENT expenses with no cycle are operating overhead.
    overhead_qs = approved.filter(cycle__isnull=True, date_incurred__gte=start, date_incurred__lte=end)
    overhead = _sum(overhead_qs, "amount_idr")
    net = gross - spoilage - unabsorbed - overhead
    purchases = sum(
        (p.qty * p.cost_per_unit_idr for p in InputPurchase.objects.filter(purchased_at__gte=lo, purchased_at__lt=hi)),
        ZERO,
    )

    kpis = {
        "revenue": revenue,
        "cogs": cogs,
        "gross_margin": gross,
        "gross_margin_pct": (gross / revenue * 100) if revenue else None,
        "spoilage_loss": spoilage,
        "unabsorbed_cost": unabsorbed,
        "overhead": overhead,
        "overhead_general": _sum(overhead_qs.filter(scope=Expense.Scope.GENERAL), "amount_idr"),
        "net_profit": net,
        "orders": orders.count(),
        "input_purchases": _money(purchases),
    }

    # Daily revenue / COGS series.
    daily = {start + timedelta(days=i): [ZERO, ZERO] for i in range((end - start).days + 1)}
    for created_at, rev, cost in orders.values_list("created_at", "total_revenue_idr", "total_cogs_idr"):
        bucket = daily.setdefault(jakarta_date(created_at), [ZERO, ZERO])
        bucket[0] += rev
        bucket[1] += cost

    # Margin by variety × grade, from the exact lot allocations.
    by_product: dict[tuple, dict] = {}
    lines = OrderLineItem.objects.filter(order__in=orders).select_related("product__variety").prefetch_related("allocations")
    for line in lines:
        key = (line.product.variety.name, line.product.grade)
        row = by_product.setdefault(key, {"variety": key[0], "grade": key[1], "kg": ZERO, "revenue": ZERO, "cogs": ZERO})
        row["kg"] += line.qty_kg
        row["revenue"] += line.net_revenue_idr
        row["cogs"] += sum((a.qty_kg * a.unit_cogs_idr for a in line.allocations.all()), ZERO)
    margin_rows = []
    for row in sorted(by_product.values(), key=lambda r: (r["variety"], r["grade"])):
        row["revenue"], row["cogs"] = _money(row["revenue"]), _money(row["cogs"])
        row["margin"] = row["revenue"] - row["cogs"]
        row["margin_pct"] = (row["margin"] / row["revenue"] * 100) if row["revenue"] else None
        margin_rows.append(row)
    top_products = sorted(margin_rows, key=lambda r: r["revenue"], reverse=True)[:5]

    # Sellable (graded) kg per m² by element type, over elements that harvested in range.
    yield_by_type: dict[str, dict] = {}
    batches = HarvestBatch.objects.filter(harvested_at__gte=lo, harvested_at__lt=hi).select_related("cycle__element")
    for b in batches:
        element = b.cycle.element
        row = yield_by_type.setdefault(
            element.element_type,
            {"type": element.get_element_type_display(), "kg": ZERO, "elements": {}},
        )
        row["kg"] += b.high_quality_kg + b.low_quality_kg
        row["elements"][element.pk] = element.area_m2
    yield_rows = []
    for row in yield_by_type.values():
        area = sum(row["elements"].values(), ZERO)
        yield_rows.append({
            "type": row["type"], "kg": row["kg"], "area_m2": area,
            "kg_per_m2": (row["kg"] / area).quantize(Decimal("0.001")) if area else ZERO,
        })
    yield_rows.sort(key=lambda r: r["kg_per_m2"], reverse=True)

    expense_rows = [
        {"category": r["category__name"], "amount": r["s"]}
        for r in approved.filter(date_incurred__gte=start, date_incurred__lte=end)
        .values("category__name")
        .annotate(s=Sum("amount_idr"))
        .order_by("-s")
    ]

    return {
        "start": start,
        "end": end,
        "kpis": kpis,
        "daily": [{"date": d, "revenue": v[0], "cogs": v[1]} for d, v in sorted(daily.items())],
        "margin_by_product": margin_rows,
        "top_products": top_products,
        "yield_by_type": yield_rows,
        "expenses_by_category": expense_rows,
    }
