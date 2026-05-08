"""REX Product Pipeline Calendar + Products — public, every-person-at-REX view.

Shows multiple event types on a single month calendar:
  - Filings (new 485APOS or similar from SEC pipeline)
  - Effectives (estimated effective date from rex_products)
  - Launches (target_listing_date or official_listed_date)
  - Distributions (ex-dates from fund_distributions)
  - Holidays (NYSE market-closed days)

All events are colored by type. Day cells show event counts. Click a day
to see a side panel with everything happening that day.

The pipeline products page is the "home of operations" combining summary
KPIs with the full product table, sortable columns, and CSV export.

No admin auth — intentionally public so the whole REX team can see it.

Phase 1 of the v3 URL migration: handler implementations have been
renamed to ``_*_impl`` and are imported by ``webapp.routers.operations``
to be mounted under ``/operations/{pipeline,calendar}``. The old
``/pipeline/*`` routes shrink to 301 redirects pointing at the new
canonical URLs.

Legacy URL → new canonical URL:
    GET /pipeline/                          → /operations/calendar
    GET /pipeline/summary                   → /operations/calendar/summary
    GET /pipeline/products                  → /operations/pipeline
    GET /pipeline/distributions/export.csv  → /operations/calendar/distributions/export.csv
    GET /pipeline/{year}/{month}            → /operations/calendar/{year}/{month}
"""
from __future__ import annotations

import calendar as cal_mod
import csv
import io
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, not_, or_, select
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

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


def _pipeline_root_impl(
    request: Request,
    types: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """Render the pipeline calendar at the current month.

    Mounted at /operations/calendar in PR 1.
    """
    today = date.today()
    return _render_month(request, db, today.year, today.month, types)


VALID_STATUSES = ["Research", "Target List", "Filed", "Awaiting Effective", "Listed", "Delisted"]
VALID_SUITES = list(SUITE_COLORS.keys())


def _rex_only_filter(query):
    """Restrict a RexProduct query to REX-branded products only.

    The rex_products table includes non-REX filings that share the same
    trust (e.g. ETF Opportunities Trust hosts Tuttle, GSR, Hedgeye funds
    alongside REX). Filter by name prefix + trust, and explicitly drop
    known non-REX issuers.
    """
    from webapp.models import RexProduct

    return (
        query.filter(or_(
            RexProduct.name.ilike("REX %"),
            RexProduct.name.ilike("T-REX %"),
            RexProduct.name.ilike("REX-OSPREY%"),
            RexProduct.name.ilike("REX-Osprey%"),
            RexProduct.name.ilike("REX- Osprey%"),  # some records have hyphen-space
            RexProduct.name.ilike("MICROSECTORS%"),
            RexProduct.name.ilike("MicroSectors%"),
            RexProduct.trust.ilike("%REX%"),
        ))
        .filter(not_(or_(
            RexProduct.trust.ilike("%tuttle%"),
            RexProduct.trust.ilike("%defiance%"),
            RexProduct.trust.ilike("%osprey bitcoin%"),
            RexProduct.name.ilike("Osprey Bitcoin%"),
            RexProduct.name.ilike("Tuttle%"),
            RexProduct.name.ilike("TUTTLE%"),
            RexProduct.name.ilike("Defiance%"),
            RexProduct.name.ilike("GSR %"),
            RexProduct.name.ilike("Hedgeye%"),
            RexProduct.name.ilike("GRANOLA%"),
            RexProduct.name.ilike("Gold Miners%"),
            RexProduct.name.ilike("Nuclear Equity%"),
            RexProduct.name.ilike("Nasdaq Dorsey%"),
            RexProduct.name.ilike("The Laddered%"),
        )))
    )


def _pipeline_summary_impl(request: Request):
    """Legacy alias that points users at the pipeline products page.

    Mounted at /operations/calendar/summary in PR 1; redirects forward
    to the canonical /operations/pipeline page.
    """
    return RedirectResponse(url="/operations/pipeline", status_code=301)


def _pipeline_products_impl(
    request: Request,
    status: str | None = None,
    suite: str | None = None,
    q: str | None = None,
    urgency: str | None = None,
    sort: str | None = None,
    dir: str | None = None,
    db: Session = Depends(get_db),
):
    """Pipeline Home of Operations — KPIs + full product table.

    Public (no admin auth). Edit controls hidden for non-admins.
    Mounted at /operations/pipeline in PR 1.
    """
    from webapp.models import RexProduct, FundDistribution

    today = date.today()
    week_ago = today - timedelta(days=7)
    month_ahead = today + timedelta(days=30)
    quarter_ahead = today + timedelta(days=90)

    # ---- KPIs (REX-branded products only) ----
    total = _rex_only_filter(db.query(RexProduct)).count()
    listed = _rex_only_filter(db.query(RexProduct)).filter(RexProduct.status == "Listed").count()
    filed = _rex_only_filter(db.query(RexProduct)).filter(RexProduct.status == "Filed").count()
    awaiting = _rex_only_filter(db.query(RexProduct)).filter(RexProduct.status == "Awaiting Effective").count()
    research = _rex_only_filter(db.query(RexProduct)).filter(RexProduct.status.in_(["Research", "Target List"])).count()

    # Activity metrics
    filings_last_7d = (
        _rex_only_filter(db.query(RexProduct))
        .filter(RexProduct.initial_filing_date.between(week_ago, today))
        .count()
    )
    launches_last_30d = (
        _rex_only_filter(db.query(RexProduct))
        .filter(RexProduct.official_listed_date.between(today - timedelta(days=30), today))
        .count()
    )
    effectives_next_30d = (
        _rex_only_filter(db.query(RexProduct))
        .filter(RexProduct.status.in_(["Filed", "Awaiting Effective"]))
        .filter(RexProduct.estimated_effective_date.between(today, month_ahead))
        .count()
    )
    effectives_next_90d = (
        _rex_only_filter(db.query(RexProduct))
        .filter(RexProduct.status.in_(["Filed", "Awaiting Effective"]))
        .filter(RexProduct.estimated_effective_date.between(today, quarter_ahead))
        .count()
    )

    # Next launches — Filed/Awaiting with effective date in next 90 days
    next_launches = (
        _rex_only_filter(db.query(RexProduct))
        .filter(RexProduct.status.in_(["Filed", "Awaiting Effective"]))
        .filter(RexProduct.estimated_effective_date.between(today, quarter_ahead))
        .order_by(RexProduct.estimated_effective_date.asc())
        .limit(5)
        .all()
    )

    # Cycle time stats
    listed_products = (
        _rex_only_filter(db.query(RexProduct))
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
    cycle_days = [d for d in cycle_days if 0 <= d <= 400]
    avg_cycle = int(sum(cycle_days) / len(cycle_days)) if cycle_days else None
    min_cycle = min(cycle_days) if cycle_days else None
    max_cycle = max(cycle_days) if cycle_days else None

    # ---- Urgency counts (unfiltered, for pill badges) ----
    urgency_counts = {
        "urgent": _rex_only_filter(db.query(RexProduct))
            .filter(RexProduct.status.in_(["Filed", "Awaiting Effective"]))
            .filter(RexProduct.estimated_effective_date.between(today, today + timedelta(days=14)))
            .count(),
        "upcoming": _rex_only_filter(db.query(RexProduct))
            .filter(RexProduct.status.in_(["Filed", "Awaiting Effective"]))
            .filter(RexProduct.estimated_effective_date.between(today, today + timedelta(days=60)))
            .count(),
        "overdue": _rex_only_filter(db.query(RexProduct))
            .filter(RexProduct.status.notin_(["Listed", "Delisted"]))
            .filter(RexProduct.target_listing_date.isnot(None))
            .filter(RexProduct.target_listing_date < today)
            .count(),
        "recent_filings": _rex_only_filter(db.query(RexProduct))
            .filter(RexProduct.initial_filing_date >= today - timedelta(days=14))
            .count(),
        "recent_launches": _rex_only_filter(db.query(RexProduct))
            .filter(RexProduct.official_listed_date >= today - timedelta(days=30))
            .count(),
    }

    # Status/suite counts (REX-branded only)
    status_counts = dict(
        _rex_only_filter(db.query(RexProduct.status, func.count(RexProduct.id)))
        .group_by(RexProduct.status).all()
    )
    suite_counts = dict(
        _rex_only_filter(db.query(RexProduct.product_suite, func.count(RexProduct.id)))
        .group_by(RexProduct.product_suite).all()
    )

    # ---- By-suite breakdown ----
    suite_breakdown = {}
    for s, cnt in (
        _rex_only_filter(db.query(RexProduct.product_suite, func.count(RexProduct.id)))
        .group_by(RexProduct.product_suite).all()
    ):
        if not s:
            continue
        suite_breakdown[s] = {"total": cnt}
    for s in suite_breakdown:
        suite_breakdown[s]["listed"] = (
            _rex_only_filter(db.query(RexProduct))
            .filter(RexProduct.product_suite == s, RexProduct.status == "Listed")
            .count()
        )
        suite_breakdown[s]["filed"] = (
            _rex_only_filter(db.query(RexProduct))
            .filter(RexProduct.product_suite == s, RexProduct.status.in_(["Filed", "Awaiting Effective"]))
            .count()
        )

    # ---- Build filtered product query (REX-branded only) ----
    query = _rex_only_filter(db.query(RexProduct))

    if status:
        query = query.filter(RexProduct.status == status)
    if suite:
        query = query.filter(RexProduct.product_suite == suite)
    if q:
        like = f"%{q}%"
        query = query.filter(or_(
            RexProduct.name.ilike(like),
            RexProduct.ticker.ilike(like),
            RexProduct.underlier.ilike(like),
            RexProduct.trust.ilike(like),
        ))

    if urgency == "urgent":
        cutoff = today + timedelta(days=14)
        query = query.filter(
            RexProduct.status.in_(["Filed", "Awaiting Effective"]),
            RexProduct.estimated_effective_date.between(today, cutoff),
        )
    elif urgency == "upcoming":
        cutoff = today + timedelta(days=60)
        query = query.filter(
            RexProduct.status.in_(["Filed", "Awaiting Effective"]),
            RexProduct.estimated_effective_date.between(today, cutoff),
        )
    elif urgency == "overdue":
        query = query.filter(
            RexProduct.status != "Listed",
            RexProduct.status != "Delisted",
            RexProduct.target_listing_date.isnot(None),
            RexProduct.target_listing_date < today,
        )
    elif urgency == "recent_filings":
        query = query.filter(RexProduct.initial_filing_date >= today - timedelta(days=14))
    elif urgency == "recent_launches":
        query = query.filter(RexProduct.official_listed_date >= today - timedelta(days=30))

    # ---- Server-side sort (default: status asc, effective asc, name asc) ----
    sort_col = sort or "status"
    sort_dir = dir or "asc"
    _asc = sort_dir != "desc"
    sort_map = {
        "status": RexProduct.status,
        "effective": RexProduct.estimated_effective_date,
        "name": RexProduct.name,
        "ticker": RexProduct.ticker,
        "suite": RexProduct.product_suite,
        "filed": RexProduct.initial_filing_date,
        "listed": RexProduct.official_listed_date,
    }
    col = sort_map.get(sort_col)
    if col is not None:
        query = query.order_by(col.asc().nulls_last() if _asc else col.desc().nulls_last())
    else:
        query = query.order_by(
            RexProduct.status.asc(),
            RexProduct.estimated_effective_date.asc().nulls_last(),
            RexProduct.name.asc(),
        )

    products = query.limit(1000).all()

    is_admin = request.session.get("is_admin", False)

    return templates.TemplateResponse("pipeline_products.html", {
        "request": request,
        "today": today,
        "is_admin": is_admin,
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
        "next_launches": next_launches,
        # Cycle time
        "avg_cycle": avg_cycle,
        "min_cycle": min_cycle,
        "max_cycle": max_cycle,
        "cycle_sample": len(cycle_days),
        # Counts
        "urgency_counts": urgency_counts,
        "status_counts": status_counts,
        "suite_counts": suite_counts,
        # Suite breakdown
        "suite_breakdown": suite_breakdown,
        "suite_colors": SUITE_COLORS,
        # Products
        "products": products,
        "filtered_count": len(products),
        # Filter state
        "valid_statuses": VALID_STATUSES,
        "valid_suites": VALID_SUITES,
        "filter_status": status or "",
        "filter_suite": suite or "",
        "filter_q": q or "",
        "filter_urgency": urgency or "",
        "sort_col": sort_col,
        "sort_dir": sort_dir,
    })


def _pipeline_distributions_impl(
    request: Request,
    year: int | None = None,
    db: Session = Depends(get_db),
):
    """Export distribution schedule as CSV, optionally filtered by year.

    Joins FundDistribution with MktMasterData (on normalized ticker) to
    include CUSIP. Mounted at /operations/calendar/distributions/export.csv
    in PR 1.
    """
    from webapp.models import FundDistribution, MktMasterData

    query = db.query(FundDistribution)
    if year:
        start = date(year, 1, 1)
        end = date(year, 12, 31)
        query = query.filter(FundDistribution.ex_date.between(start, end))
    query = query.order_by(FundDistribution.ex_date, FundDistribution.ticker)
    dists = query.all()

    # Build ticker -> CUSIP lookup from MktMasterData (strip " US" suffix)
    all_mkt = db.query(MktMasterData.ticker, MktMasterData.cusip).all()
    cusip_map: dict[str, str] = {}
    for mkt_ticker, mkt_cusip in all_mkt:
        if not mkt_ticker:
            continue
        normalized = mkt_ticker.replace(" US", "").replace(" us", "").strip()
        if mkt_cusip:
            cusip_map[normalized] = mkt_cusip

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([
        "Fund Name", "Ticker", "CUSIP", "Declaration Date",
        "Ex Date", "Record Date", "Payable Date",
    ])
    for d in dists:
        dist_ticker_norm = (d.ticker or "").replace(" US", "").replace(" us", "").strip()
        cusip = cusip_map.get(dist_ticker_norm, "")
        writer.writerow([
            d.fund_name or "",
            d.ticker or "",
            cusip,
            d.declaration_date.isoformat() if d.declaration_date else "",
            d.ex_date.isoformat() if d.ex_date else "",
            d.record_date.isoformat() if d.record_date else "",
            d.payable_date.isoformat() if d.payable_date else "",
        ])

    output.seek(0)
    filename = f"rex_distributions_{year or 'all'}.csv"
    return StreamingResponse(
        io.BytesIO(output.getvalue().encode("utf-8")),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def _pipeline_month_impl(
    year: int,
    month: int,
    request: Request,
    types: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """Render the pipeline calendar at a specific (year, month).

    Mounted at /operations/calendar/{year}/{month} in PR 1.
    """
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


# ---------------------------------------------------------------------------
# Phase 1 legacy redirects (old URL → new canonical URL).
# All five paths are GETs, so 301 is appropriate.
# ---------------------------------------------------------------------------


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def pipeline_root_redirect():
    return RedirectResponse("/operations/calendar", status_code=301)


@router.get("/summary")
def pipeline_summary_redirect():
    return RedirectResponse("/operations/calendar/summary", status_code=301)


@router.get("/products", response_class=HTMLResponse)
def pipeline_products_redirect():
    return RedirectResponse("/operations/pipeline", status_code=301)


@router.get("/distributions/export.csv")
def pipeline_distributions_redirect():
    return RedirectResponse("/operations/calendar/distributions/export.csv", status_code=301)


@router.get("/{year}/{month}", response_class=HTMLResponse)
def pipeline_month_redirect(year: int, month: int):
    return RedirectResponse(f"/operations/calendar/{year}/{month}", status_code=301)
