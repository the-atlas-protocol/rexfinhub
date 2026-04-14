"""REX Product Pipeline Calendar — public, every-person-at-REX view.

Shows multiple event types on a single month calendar:
  - Filings (new 485APOS or similar from SEC pipeline)
  - Effectives (estimated effective date from rex_products)
  - Launches (target_listing_date or official_listed_date)
  - Distributions (ex-dates from fund_distributions)
  - Holidays (NYSE market-closed days)

All events are colored by type. Day cells show event counts. Click a day
to see a side panel with everything happening that day.

No admin auth — intentionally public so the whole REX team can see it.
"""
from __future__ import annotations

import calendar as cal_mod
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from webapp.dependencies import get_db

router = APIRouter(prefix="/pipeline", tags=["pipeline"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

# Suite → color (unchanged)
SUITE_COLORS = {
    "T-REX":            "#0f172a",
    "Premium Income":   "#2563eb",
    "Growth & Income":  "#059669",
    "IncomeMax":        "#d97706",
    "Crypto":           "#8b5cf6",
    "Thematic":         "#0891b2",
    "Autocallable":     "#dc2626",
    "T-Bill":           "#64748b",
    "MicroSectors ETN": "#0f766e",
}

# Event type → color + label
EVENT_TYPES = {
    "filing":       {"label": "Filing",       "color": "#2563eb"},
    "effective":    {"label": "Effective",    "color": "#0891b2"},
    "launch":       {"label": "Launch",       "color": "#059669"},
    "distribution": {"label": "Distribution", "color": "#d97706"},
    "holiday":      {"label": "NYSE Holiday", "color": "#94a3b8"},
}

# Event kinds allowed via ?types= filter
ALLOWED_TYPES = set(EVENT_TYPES.keys())


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def pipeline_home(
    request: Request,
    types: str = Query(default=""),
    db: Session = Depends(get_db),
):
    today = date.today()
    return _render_month(request, db, today.year, today.month, types)


@router.get("/summary", response_class=HTMLResponse)
def pipeline_summary(request: Request, db: Session = Depends(get_db)):
    """Pipeline activity summary — KPIs + recent-activity tables.

    Focused on "what's happening this month / next month" so the team
    can see T-REX launch urgency at a glance.
    """
    from webapp.models import RexProduct, FundDistribution

    today = date.today()
    week_ago = today - timedelta(days=7)
    month_ahead = today + timedelta(days=30)
    quarter_ahead = today + timedelta(days=90)

    # ---- KPIs ----
    total = db.query(RexProduct).count()
    listed = db.query(RexProduct).filter(RexProduct.status == "Listed").count()
    filed = db.query(RexProduct).filter(RexProduct.status == "Filed").count()
    awaiting = db.query(RexProduct).filter(RexProduct.status == "Awaiting Effective").count()
    research = db.query(RexProduct).filter(RexProduct.status.in_(["Research", "Target List"])).count()

    # Activity metrics
    filings_last_7d = (
        db.query(RexProduct)
        .filter(RexProduct.initial_filing_date.between(week_ago, today))
        .count()
    )
    launches_last_30d = (
        db.query(RexProduct)
        .filter(RexProduct.official_listed_date.between(today - timedelta(days=30), today))
        .count()
    )
    effectives_next_30d = (
        db.query(RexProduct)
        .filter(RexProduct.status.in_(["Filed", "Awaiting Effective"]))
        .filter(RexProduct.estimated_effective_date.between(today, month_ahead))
        .count()
    )
    effectives_next_90d = (
        db.query(RexProduct)
        .filter(RexProduct.status.in_(["Filed", "Awaiting Effective"]))
        .filter(RexProduct.estimated_effective_date.between(today, quarter_ahead))
        .count()
    )

    # Urgent: effectives within 14 days
    urgent_cutoff = today + timedelta(days=14)
    urgent_pending = (
        db.query(RexProduct)
        .filter(RexProduct.status.in_(["Filed", "Awaiting Effective"]))
        .filter(RexProduct.estimated_effective_date.between(today, urgent_cutoff))
        .order_by(RexProduct.estimated_effective_date)
        .limit(20)
        .all()
    )

    # Overdue: target_listing_date passed but not yet Listed
    overdue = (
        db.query(RexProduct)
        .filter(RexProduct.status != "Listed")
        .filter(RexProduct.status != "Delisted")
        .filter(RexProduct.target_listing_date.isnot(None))
        .filter(RexProduct.target_listing_date < today)
        .order_by(RexProduct.target_listing_date)
        .limit(20)
        .all()
    )

    # Recent filings (last 14 days)
    recent_filings = (
        db.query(RexProduct)
        .filter(RexProduct.initial_filing_date >= today - timedelta(days=14))
        .order_by(RexProduct.initial_filing_date.desc())
        .limit(30)
        .all()
    )

    # Upcoming distributions (next 14 days)
    upcoming_dists = (
        db.query(FundDistribution)
        .filter(FundDistribution.ex_date.between(today, today + timedelta(days=14)))
        .order_by(FundDistribution.ex_date, FundDistribution.ticker)
        .all()
    )

    # Cycle time stats — for already-listed products
    listed_products = (
        db.query(RexProduct)
        .filter(RexProduct.status == "Listed")
        .filter(RexProduct.initial_filing_date.isnot(None))
        .filter(RexProduct.official_listed_date.isnot(None))
        .all()
    )
    cycle_days = [
        (p.official_listed_date - p.initial_filing_date).days
        for p in listed_products
        if p.official_listed_date and p.initial_filing_date
    ]
    cycle_days = [d for d in cycle_days if 0 <= d <= 400]  # filter outliers
    avg_cycle = int(sum(cycle_days) / len(cycle_days)) if cycle_days else None
    min_cycle = min(cycle_days) if cycle_days else None
    max_cycle = max(cycle_days) if cycle_days else None

    # By-suite activity
    from sqlalchemy import func as _func
    suite_breakdown = {}
    for suite, cnt in (
        db.query(RexProduct.product_suite, _func.count(RexProduct.id))
        .group_by(RexProduct.product_suite)
        .all()
    ):
        if not suite:
            continue
        suite_breakdown[suite] = {"total": cnt}
    for suite in suite_breakdown:
        suite_breakdown[suite]["listed"] = (
            db.query(RexProduct)
            .filter(RexProduct.product_suite == suite)
            .filter(RexProduct.status == "Listed")
            .count()
        )
        suite_breakdown[suite]["filed"] = (
            db.query(RexProduct)
            .filter(RexProduct.product_suite == suite)
            .filter(RexProduct.status.in_(["Filed", "Awaiting Effective"]))
            .count()
        )

    return templates.TemplateResponse("pipeline_summary.html", {
        "request": request,
        "today": today,
        # KPIs
        "total": total,
        "listed": listed,
        "filed": filed,
        "awaiting": awaiting,
        "research": research,
        "filings_last_7d": filings_last_7d,
        "launches_last_30d": launches_last_30d,
        "effectives_next_30d": effectives_next_30d,
        "effectives_next_90d": effectives_next_90d,
        # Lists
        "urgent_pending": urgent_pending,
        "overdue": overdue,
        "recent_filings": recent_filings,
        "upcoming_dists": upcoming_dists,
        # Cycle time
        "avg_cycle": avg_cycle,
        "min_cycle": min_cycle,
        "max_cycle": max_cycle,
        "cycle_sample": len(cycle_days),
        # By suite
        "suite_breakdown": suite_breakdown,
        "suite_colors": SUITE_COLORS,
    })


@router.get("/{year}/{month}", response_class=HTMLResponse)
def pipeline_month(
    year: int,
    month: int,
    request: Request,
    types: str = Query(default=""),
    db: Session = Depends(get_db),
):
    if not (1 <= month <= 12) or not (2020 <= year <= 2035):
        return _render_month(request, db, date.today().year, date.today().month, types)
    return _render_month(request, db, year, month, types)


def _parse_types(types: str) -> set[str]:
    """Parse ?types=filing,effective into a set of allowed event types.
    Empty = show all."""
    if not types:
        return set(ALLOWED_TYPES)
    result = {t.strip().lower() for t in types.split(",") if t.strip()}
    return result & ALLOWED_TYPES or set(ALLOWED_TYPES)


def _render_month(
    request: Request,
    db: Session,
    year: int,
    month: int,
    types_filter: str,
) -> HTMLResponse:
    from webapp.models import RexProduct, FundDistribution, NyseHoliday

    active_types = _parse_types(types_filter)

    first_day = date(year, month, 1)
    last_day_num = cal_mod.monthrange(year, month)[1]
    last_day = date(year, month, last_day_num)
    today = date.today()

    # ---- Gather events by day ----
    events_by_day: dict[date, list[dict]] = defaultdict(list)
    counts_by_type = {k: 0 for k in EVENT_TYPES}

    # Effectives + Launches + Filings from rex_products
    if active_types & {"filing", "effective", "launch"}:
        products = (
            db.query(RexProduct)
            .filter(
                (RexProduct.estimated_effective_date.between(first_day, last_day))
                | (RexProduct.initial_filing_date.between(first_day, last_day))
                | (RexProduct.official_listed_date.between(first_day, last_day))
                | (RexProduct.target_listing_date.between(first_day, last_day))
            )
            .all()
        )
        for p in products:
            suite_color = SUITE_COLORS.get(p.product_suite or "", "#64748b")

            if "filing" in active_types and p.initial_filing_date and first_day <= p.initial_filing_date <= last_day:
                events_by_day[p.initial_filing_date].append({
                    "type": "filing",
                    "ticker": p.ticker or "",
                    "name": p.name,
                    "suite": p.product_suite or "",
                    "color": EVENT_TYPES["filing"]["color"],
                    "suite_color": suite_color,
                    "form": p.latest_form or "",
                    "link": p.latest_prospectus_link or "",
                })
                counts_by_type["filing"] += 1

            if "effective" in active_types and p.estimated_effective_date and first_day <= p.estimated_effective_date <= last_day:
                events_by_day[p.estimated_effective_date].append({
                    "type": "effective",
                    "ticker": p.ticker or "",
                    "name": p.name,
                    "suite": p.product_suite or "",
                    "color": EVENT_TYPES["effective"]["color"],
                    "suite_color": suite_color,
                    "status": p.status,
                })
                counts_by_type["effective"] += 1

            if "launch" in active_types:
                launch_d = p.official_listed_date or p.target_listing_date
                if launch_d and first_day <= launch_d <= last_day:
                    events_by_day[launch_d].append({
                        "type": "launch",
                        "ticker": p.ticker or "",
                        "name": p.name,
                        "suite": p.product_suite or "",
                        "color": EVENT_TYPES["launch"]["color"],
                        "suite_color": suite_color,
                        "listed": p.status == "Listed",
                    })
                    counts_by_type["launch"] += 1

    # Distributions (ex-date drives the calendar event)
    if "distribution" in active_types:
        dists = (
            db.query(FundDistribution)
            .filter(FundDistribution.ex_date.between(first_day, last_day))
            .order_by(FundDistribution.ex_date, FundDistribution.ticker)
            .all()
        )
        for d in dists:
            events_by_day[d.ex_date].append({
                "type": "distribution",
                "ticker": d.ticker,
                "name": d.fund_name or d.ticker,
                "color": EVENT_TYPES["distribution"]["color"],
                "payable_date": d.payable_date.isoformat() if d.payable_date else None,
                "declaration_date": d.declaration_date.isoformat() if d.declaration_date else None,
            })
            counts_by_type["distribution"] += 1

    # Holidays
    holiday_set: set[date] = set()
    if "holiday" in active_types:
        holidays = (
            db.query(NyseHoliday)
            .filter(NyseHoliday.holiday_date.between(first_day, last_day))
            .all()
        )
        for h in holidays:
            events_by_day[h.holiday_date].append({
                "type": "holiday",
                "name": h.name,
                "color": EVENT_TYPES["holiday"]["color"],
            })
            holiday_set.add(h.holiday_date)
            counts_by_type["holiday"] += 1

    # ---- Build KPIs (always, regardless of type filter) ----
    total = db.query(RexProduct).count()
    listed = db.query(RexProduct).filter(RexProduct.status == "Listed").count()
    filed = db.query(RexProduct).filter(RexProduct.status.in_(["Filed", "Awaiting Effective"])).count()
    dist_count_month = db.query(FundDistribution).filter(
        FundDistribution.ex_date.between(first_day, last_day)
    ).count()

    # ---- Build calendar grid ----
    cal = cal_mod.Calendar(firstweekday=6)
    weeks_raw = cal.monthdatescalendar(year, month)
    weeks = []
    for week in weeks_raw:
        days = []
        for d in week:
            events = events_by_day.get(d, [])
            type_breakdown: dict[str, int] = defaultdict(int)
            for e in events:
                type_breakdown[e["type"]] += 1
            days.append({
                "date": d,
                "iso": d.isoformat(),
                "in_month": d.month == month,
                "day": d.day,
                "events": events,
                "event_count": len(events),
                "type_breakdown": dict(type_breakdown),
                "is_today": d == today,
                "is_holiday": d in holiday_set,
                "is_weekend": d.weekday() >= 5,
            })
        weeks.append(days)

    # Prev / next month
    prev_month = (month - 1) if month > 1 else 12
    prev_year = year if month > 1 else year - 1
    next_month = (month + 1) if month < 12 else 1
    next_year = year if month < 12 else year + 1

    return templates.TemplateResponse("pipeline_calendar.html", {
        "request": request,
        "year": year,
        "month": month,
        "month_name": cal_mod.month_name[month],
        "weeks": weeks,
        "prev_year": prev_year,
        "prev_month": prev_month,
        "next_year": next_year,
        "next_month": next_month,
        # Summary stats
        "total": total,
        "listed": listed,
        "filed": filed,
        "this_month_total_events": sum(counts_by_type.values()),
        "counts_by_type": counts_by_type,
        "dist_count_month": dist_count_month,
        # UI state
        "event_types": EVENT_TYPES,
        "active_types": active_types,
        "types_query": ",".join(sorted(active_types)) if len(active_types) < len(ALLOWED_TYPES) else "",
        "suite_colors": SUITE_COLORS,
        "today": today,
    })
