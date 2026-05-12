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


# Lifecycle status enum — collapsed back from 15 to 6 values on 2026-05-12
# per Ryu's REX Ops review. Of the 15-value enum only 6 were ever actually
# populated, and the operational vocabulary the team uses day-to-day is the
# shorter 6-stage funnel. The full Counsel/Board granularity now lives in
# the upcoming rex_status_history audit table (see scripts/migrate_rex_
# status_2026-05-12.py) instead of polluting the primary status column.
#
# Lifecycle order (left-to-right) drives:
#   • the dropdown on /operations/pipeline
#   • the funnel widget column order
#   • the status filter chips at the top of the products table
#
# Mapping summary (old -> new):
#   Research, Counsel*, Pending Board, Board*, Not Approved by Board
#       -> Under Consideration
#   Target List -> Target List
#   Filed, Filed (485A), Filed (485B), Awaiting Effective (no eff date)
#       -> Filed
#   Awaiting Effective (eff date set), Effective -> Effective
#   Listed -> Listed
#   Delisted, LIQU, INAC, EXPD, DLST -> Delisted
#
# 485A/B distinction lives in rex_products.latest_form, NOT in status.
VALID_STATUSES = [
    "Under Consideration",  # was: Research / Counsel / Board / Pending Board
    "Target List",          # formally targeted for build, pre-counsel
    "Filed",                # was: Filed / Filed (485A/B) / Awaiting (no eff date)
    "Effective",            # was: Effective / Awaiting (eff date set)
    "Listed",               # was: Listed / ACTV — actively trading
    "Delisted",             # was: Delisted / LIQU / INAC / EXPD / DLST
]

# Color palette for status badges — 6 distinct lifecycle stages.
# Grey for early consideration, slate for target, blue for filed, teal for
# effective-pre-launch, green for live trading, dim grey for retired.
STATUS_COLORS = {
    "Under Consideration":   "#94a3b8",  # slate
    "Target List":           "#64748b",  # darker slate
    "Filed":                 "#2563eb",  # blue
    "Effective":             "#0d9488",  # teal
    "Listed":                "#059669",  # dark green
    "Delisted":              "#6b7280",  # dim grey
}

VALID_SUITES = list(SUITE_COLORS.keys())


def _rex_only_filter(query):
    """Restrict a RexProduct query to REX-branded products only.

    The rex_products table includes non-REX filings that share the same
    trust (e.g. ETF Opportunities Trust hosts Tuttle, GSR, Hedgeye funds
    alongside REX). Filter by name prefix + trust, and explicitly drop
    known non-REX issuers.

    NOTE: includes product_suite IN VALID_SUITES — catches REX products
    whose name doesn't contain "REX" (e.g. TLDR "The Laddered T-Bill ETF",
    suite="T-Bill"). Otherwise the suite KPIs would silently zero out
    legitimate REX products that don't follow the REX/T-REX naming convention.
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
            RexProduct.name.ilike("The Laddered%"),  # TLDR — REX product, no REX in name
            RexProduct.trust.ilike("%REX%"),
            RexProduct.product_suite.in_(VALID_SUITES),  # catches edge cases via REX-known suite
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


# "Filed but not yet effective" — set of statuses that mean a product has
# been filed but has NOT yet been declared Effective by the SEC. Drives the
# Upcoming Effectiveness KPI. Under the collapsed 6-value enum (2026-05-12)
# this is simply ["Filed"]; pre-filing pipeline stages all live under
# "Under Consideration" which is NOT pending-effective.
PENDING_EFFECTIVE_STATUSES = [
    "Filed",
]

# Statuses that mean "done / no longer in the active pipeline". Used to
# default-show Listed/Delisted instead of hiding them. Note: "Effective" is
# included because once a product is Effective it's no longer something the
# ops team needs to action — the next move is Listed which is a launch event.
TERMINAL_STATUSES = ["Listed", "Delisted", "Effective"]


def _pipeline_products_impl(
    request: Request,
    status: str | None = None,
    suite: str | None = None,
    q: str | None = None,
    urgency: str | None = None,
    sort: str | None = None,
    dir: str | None = None,
    page: int = 1,
    per_page: str | int = 50,
    hide_terminal: int = 0,
    show_cold: int | None = None,  # legacy alias — ignored if hide_terminal supplied
    recent_days: int = 14,
    db: Session = Depends(get_db),
):
    """Pipeline Home of Operations — PM dashboard + paginated table.

    Public (no admin auth). Edit controls hidden for non-admins.
    Mounted at /operations/pipeline in PR 1.

    Default view excludes "cold" rows (Delisted; Listed > 365d ago) so the
    funnel and table focus on what's actively in motion. ``?show_cold=1``
    re-includes them.

    Phase 2 additions:
      - ``per_page`` accepts 20/50/100/all (string ``"all"`` => no LIMIT)
      - ``recent_days`` (7/14/30/90) drives Recent Activity window
      - ``sort`` map extended to include lifecycle dates + days_in_stage
    """
    from webapp.models import RexProduct, FundDistribution

    today = date.today()
    week_ago = today - timedelta(days=7)
    month_ahead = today + timedelta(days=30)
    quarter_ahead = today + timedelta(days=90)
    cold_cutoff = today - timedelta(days=365)

    # Pagination guards — accept "all" as a sentinel for no LIMIT.
    try:
        page = max(1, int(page))
    except (TypeError, ValueError):
        page = 1

    per_page_raw = str(per_page).strip().lower() if per_page is not None else "50"
    show_all = per_page_raw == "all"
    if show_all:
        per_page_value = 0  # unused when show_all=True
    else:
        try:
            per_page_value = int(per_page_raw)
        except (TypeError, ValueError):
            per_page_value = 50
        if per_page_value not in (20, 50, 100):
            per_page_value = 50

    # Recent Activity window guard
    try:
        recent_days_value = int(recent_days)
    except (TypeError, ValueError):
        recent_days_value = 14
    if recent_days_value not in (7, 14, 30, 90):
        recent_days_value = 14

    # New default (May 2026): SHOW everything (Listed + Delisted included).
    # User can opt to hide them via ?hide_terminal=1. Legacy ?show_cold=
    # param is now silently ignored — bookmarks that included it will just
    # see the new default (which is what the REX team wants per the May
    # ops review). No harm: terminal stages are still toggleable.
    hide_terminal_flag = bool(hide_terminal)

    def _apply_terminal_filter(qry):
        """Hide Listed + Delisted + Effective rows when hide_terminal=1."""
        if not hide_terminal_flag:
            return qry
        return qry.filter(
            RexProduct.status.notin_(TERMINAL_STATUSES),
        )

    # ---- KPIs (REX-branded products only) ----
    # Under the collapsed 6-value enum (2026-05-12), "Awaiting Effective" is
    # gone — pre-effective filings are just "Filed", and rows with an SEC-set
    # effective date were migrated to "Effective". We keep the `awaiting`
    # template variable populated with the Effective count for now so the
    # existing pipeline_products.html KPI block doesn't error; O1 will rename
    # the variable when the template gets its v6-enum pass.
    total = _rex_only_filter(db.query(RexProduct)).count()
    listed = _rex_only_filter(db.query(RexProduct)).filter(RexProduct.status == "Listed").count()
    filed = _rex_only_filter(db.query(RexProduct)).filter(RexProduct.status == "Filed").count()
    awaiting = _rex_only_filter(db.query(RexProduct)).filter(RexProduct.status == "Effective").count()
    research = _rex_only_filter(db.query(RexProduct)).filter(RexProduct.status.in_(["Under Consideration", "Target List"])).count()

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
    # "Effectives in next N days" — under the 6-value enum, that's any
    # product currently Filed or Effective (i.e. SEC has either accepted the
    # filing or already declared it effective but it hasn't listed yet) with
    # an estimated_effective_date in the target window.
    pre_launch_statuses = ["Filed", "Effective"]
    effectives_next_30d = (
        _rex_only_filter(db.query(RexProduct))
        .filter(RexProduct.status.in_(pre_launch_statuses))
        .filter(RexProduct.estimated_effective_date.between(today, month_ahead))
        .count()
    )
    effectives_next_90d = (
        _rex_only_filter(db.query(RexProduct))
        .filter(RexProduct.status.in_(pre_launch_statuses))
        .filter(RexProduct.estimated_effective_date.between(today, quarter_ahead))
        .count()
    )

    # Next launches — Filed/Effective with effective date in next 90 days
    next_launches = (
        _rex_only_filter(db.query(RexProduct))
        .filter(RexProduct.status.in_(pre_launch_statuses))
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
    # "Urgent" used to require estimated_effective_date inside 14 days, but
    # est_effective_date population is unreliable (412/435 filed-state rows
    # have one, but data is often stale or backfilled to a year-old date).
    # When est_effective_date hasn't been refreshed, the strict window
    # filter silently zeroes out the KPI even though hundreds of products
    # are genuinely awaiting effectiveness — exactly the bug surfaced in
    # the May 2026 ops review.
    #
    # New definition: "Upcoming Effectiveness" counts EVERY product in a
    # filed-but-not-yet-effective lifecycle status, using the full
    # PENDING_EFFECTIVE_STATUSES set so future enum additions (Filed
    # (485A/B), Counsel/Board approvals) flow through automatically. The
    # date window becomes a TIE-BREAKER subtotal exposed via "next 14d /
    # 60d" sub-cards rather than a gating condition on the headline.
    pending_q = lambda: (
        _rex_only_filter(db.query(RexProduct))
        .filter(RexProduct.status.in_(PENDING_EFFECTIVE_STATUSES))
    )
    urgency_counts = {
        # Headline: every product in a pending-effective state. Honest
        # answer to "how many funds are awaiting effectiveness?".
        "urgent": pending_q().count(),
        # Sub-cohort: pending AND have an est_effective_date set in the
        # next 14 days (still useful for the truly-imminent slice when
        # the data is fresh).
        "urgent_dated_14d": pending_q()
            .filter(RexProduct.estimated_effective_date.between(today, today + timedelta(days=14)))
            .count(),
        "upcoming": pending_q().count(),  # alias — same headline cohort
        "upcoming_dated_60d": pending_q()
            .filter(RexProduct.estimated_effective_date.between(today, today + timedelta(days=60)))
            .count(),
        # "Stuck" / Past Effective Date: filings where est_effective_date
        # has passed BUT latest_form is NOT 485BPOS yet (485BPOS = post-
        # effective amendment, by definition means the fund IS effective
        # per SEC rules). Excluding auto-effective rows here so the number
        # reflects genuine stuck filings, not data-lag rows we already
        # auto-promoted to "Effective" in the funnel above.
        "overdue": _rex_only_filter(db.query(RexProduct))
            .filter(RexProduct.estimated_effective_date.isnot(None))
            .filter(RexProduct.estimated_effective_date < today)
            .filter(RexProduct.status.notin_(TERMINAL_STATUSES))
            .filter((RexProduct.latest_form != "485BPOS") | (RexProduct.latest_form.is_(None)))
            .count(),
        "recent_filings": _rex_only_filter(db.query(RexProduct))
            .filter(RexProduct.initial_filing_date >= today - timedelta(days=14))
            .count(),
        "recent_launches": _rex_only_filter(db.query(RexProduct))
            .filter(RexProduct.official_listed_date >= today - timedelta(days=30))
            .count(),
    }

    # ---- Pipeline funnel (lifecycle stages) ----
    # 6-value enum order, LEFT-TO-RIGHT life-cycle (2026-05-12). Reads as a
    # PM funnel from idea to retired:
    #
    #   Under Consideration -> Target List -> Filed -> Effective ->
    #   Listed -> Delisted
    #
    # The granular Counsel / Board / Awaiting-Effective splits previously
    # surfaced here are gone — they were noise (only 6 of 15 values were
    # ever populated) and they now live in the status-history audit table
    # for anyone who needs the deeper trail.
    n_under_consideration = (
        _rex_only_filter(db.query(RexProduct))
        .filter(RexProduct.status == "Under Consideration").count()
    )
    n_target = (
        _rex_only_filter(db.query(RexProduct))
        .filter(RexProduct.status == "Target List").count()
    )
    n_filed = (
        _rex_only_filter(db.query(RexProduct))
        .filter(RexProduct.status == "Filed").count()
    )
    n_effective_total = (
        _rex_only_filter(db.query(RexProduct))
        .filter(RexProduct.status == "Effective").count()
    )
    n_live = (
        _rex_only_filter(db.query(RexProduct))
        .filter(RexProduct.status == "Listed").count()
    )
    n_delisted = (
        _rex_only_filter(db.query(RexProduct))
        .filter(RexProduct.status == "Delisted").count()
    )

    funnel = [
        {"label": "Under Consideration", "count": n_under_consideration, "statuses": ["Under Consideration"]},
        {"label": "Target List",         "count": n_target,              "statuses": ["Target List"]},
        {"label": "Filed",               "count": n_filed,               "statuses": ["Filed"]},
        {"label": "Effective",           "count": n_effective_total,     "statuses": ["Effective"]},
        {"label": "Listed",              "count": n_live,                "statuses": ["Listed"]},
        {"label": "Delisted",            "count": n_delisted,            "statuses": ["Delisted"]},
    ]
    funnel_max = max((f["count"] for f in funnel), default=1) or 1

    # ---- Recent Activity (last N days of any updated_at touch) ----
    # Phase-1 proxy: there's no rex_product_status_history table yet, so we
    # use updated_at as a stand-in for "something changed". When the audit
    # log lands (audit doc Phase 2), swap this to the history join.
    # Phase 2: window is now configurable via ``?recent_days=N`` (7/14/30/90).
    # Phase 2.1 (2026-05): the brand filter (_rex_only_filter) was previously
    # applied here, which silently dropped legitimate activity whenever a bulk
    # DB sync touched non-REX rows that happen to live in the same trusts
    # (e.g. ETF Opportunities Trust). For an "activity feed" the user wants
    # to see ALL pipeline movement, not just REX-branded — so we no longer
    # restrict by brand. The empty-state copy still references the REX
    # last-updated timestamp via ``last_updated_overall``.
    cutoff_dt = datetime.combine(today - timedelta(days=recent_days_value), datetime.min.time())
    activity_rows = (
        db.query(RexProduct)
        .filter(RexProduct.updated_at.isnot(None))
        .filter(RexProduct.updated_at >= cutoff_dt)
        .order_by(RexProduct.updated_at.desc())
        .limit(20)
        .all()
    )
    # Latest updated_at across the whole table — surfaced in the empty
    # state so users understand WHY there's no recent activity (e.g. the
    # whole table hasn't been touched in weeks).
    last_updated_overall = (
        db.query(func.max(RexProduct.updated_at)).scalar()
    )
    recent_activity = []
    now_dt = datetime.utcnow()
    for p in activity_rows:
        delta = now_dt - p.updated_at
        if delta.days >= 1:
            ago = f"Touched {delta.days}d ago"
        elif delta.seconds >= 3600:
            ago = f"Touched {delta.seconds // 3600}h ago"
        elif delta.seconds >= 60:
            ago = f"Touched {delta.seconds // 60}m ago"
        else:
            ago = "Touched just now"
        recent_activity.append({
            "id": p.id,
            "ticker": p.ticker or "",
            "name": p.name,
            "status": p.status,
            "suite": p.product_suite or "",
            # "ago" reflects the LAST WRITE to the row (any column), not a
            # status-change event specifically. True status-history requires
            # the rex_product_status_history table (task #114). Until then
            # "Touched X ago" is the most honest label we can show.
            "ago": ago,
            "updated_at": p.updated_at,
            "latest_prospectus_link": p.latest_prospectus_link,
        })

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
            .filter(RexProduct.product_suite == s, RexProduct.status.in_(["Filed", "Effective"]))
            .count()
        )

    # ---- Build filtered product query (REX-branded only) ----
    query = _rex_only_filter(db.query(RexProduct))

    # Terminal-stage filter — applied only when user explicitly opts in
    # via ?hide_terminal=1. Default (May 2026) shows EVERYTHING including
    # Listed/Delisted so the table reflects the full product universe.
    # Counts above (KPIs, status_counts, funnel) always stay full.
    query = _apply_terminal_filter(query)

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
        # New definition matches the urgency_counts['urgent'] headline:
        # everything in a pending-effective lifecycle status. The 14-day
        # date window is exposed as a separate sub-filter when needed.
        query = query.filter(RexProduct.status.in_(PENDING_EFFECTIVE_STATUSES))
    elif urgency == "upcoming":
        query = query.filter(RexProduct.status.in_(PENDING_EFFECTIVE_STATUSES))
    elif urgency == "overdue":
        query = query.filter(
            RexProduct.estimated_effective_date.isnot(None),
            RexProduct.estimated_effective_date < today,
            RexProduct.status.notin_(TERMINAL_STATUSES),
        )
    elif urgency == "recent_filings":
        query = query.filter(RexProduct.initial_filing_date >= today - timedelta(days=14))
    elif urgency == "recent_launches":
        query = query.filter(RexProduct.official_listed_date >= today - timedelta(days=30))

    # ---- Server-side sort (default: status asc, effective asc, name asc) ----
    sort_col = sort or "status"
    sort_dir = dir or "asc"
    _asc = sort_dir != "desc"
    # Sort map accepts BOTH the short legacy keys (filed/effective/listed)
    # and the long explicit keys that match the th data-sort attributes
    # in the template (initial_filing_date, estimated_effective_date,
    # official_listed_date, latest_form). This keeps Phase-1 URLs working
    # while enabling Phase-2 sortable headers.
    sort_map = {
        "status": RexProduct.status,
        "effective": RexProduct.estimated_effective_date,
        "estimated_effective_date": RexProduct.estimated_effective_date,
        "name": RexProduct.name,
        "ticker": RexProduct.ticker,
        "suite": RexProduct.product_suite,
        "filed": RexProduct.initial_filing_date,
        "initial_filing_date": RexProduct.initial_filing_date,
        "listed": RexProduct.official_listed_date,
        "official_listed_date": RexProduct.official_listed_date,
        "latest_form": RexProduct.latest_form,
        # O1 layout rewrite — new columns surfaced in the products table.
        # ``latest_filing_date`` is a derived value (max of known SEC dates
        # on the row); for SQL ordering we proxy with initial_filing_date,
        # then re-sort in Python within the page when this key is active.
        "trust": RexProduct.trust,
        "underlier": RexProduct.underlier,
        "target_listing_date": RexProduct.target_listing_date,
        "latest_filing_date": RexProduct.initial_filing_date,
    }
    col = sort_map.get(sort_col)
    if col is not None:
        query = query.order_by(col.asc().nulls_last() if _asc else col.desc().nulls_last())
    else:
        # ``days_in_stage`` is a derived/computed value — we can't push it
        # into SQL cleanly, so we order by updated_at as the closest proxy
        # (the same field that drives the day-count fallback). Real sort
        # happens in Python after the slice is materialized.
        if sort_col == "days_in_stage":
            query = query.order_by(
                RexProduct.updated_at.asc().nulls_last() if _asc
                else RexProduct.updated_at.desc().nulls_last()
            )
        else:
            query = query.order_by(
                RexProduct.status.asc(),
                RexProduct.estimated_effective_date.asc().nulls_last(),
                RexProduct.name.asc(),
            )

    # ---- Pagination ----
    total_count = query.count()
    if show_all:
        # No LIMIT — single page that holds everything.
        total_pages = 1
        page = 1
        offset = 0
        products_page = query.all()
        effective_per_page = total_count or 1
    else:
        total_pages = max(1, (total_count + per_page_value - 1) // per_page_value)
        if page > total_pages:
            page = total_pages
        offset = (page - 1) * per_page_value
        products_page = query.offset(offset).limit(per_page_value).all()
        effective_per_page = per_page_value

    # ---- Days in current stage (rough proxy) ----
    # Use the most-recent meaningful stage timestamp on the row, fall back
    # to updated_at. Capped at None when no signal at all.
    products_view = []
    for p in products_page:
        stage_anchors = [
            p.initial_filing_date,
            p.official_listed_date,
            p.target_listing_date,
        ]
        if p.updated_at:
            stage_anchors.append(p.updated_at.date() if hasattr(p.updated_at, "date") else p.updated_at)
        stage_anchors = [d for d in stage_anchors if d is not None]
        if stage_anchors:
            anchor = max(stage_anchors)
            days_in_stage = max(0, (today - anchor).days)
        else:
            days_in_stage = None
        # Latest known SEC date on the row — max of initial_filing /
        # official_listed / target_listing. Surfaced as the "Latest
        # Filing Date" column. NOT a DB query — pure row-local derivation.
        filing_anchors = [
            p.initial_filing_date,
            p.official_listed_date,
            p.target_listing_date,
        ]
        filing_anchors = [d for d in filing_anchors if d is not None]
        latest_filing_date = max(filing_anchors) if filing_anchors else None
        products_view.append({
            "p": p,
            "days_in_stage": days_in_stage,
            "latest_filing_date": latest_filing_date,
        })

    # Post-slice sort for the derived ``days_in_stage`` column. Done in
    # Python because the value isn't a real SQL column. Within-page only
    # so pages are still stable across navigation.
    if sort_col == "days_in_stage":
        products_view.sort(
            key=lambda r: (r["days_in_stage"] is None, r["days_in_stage"] or 0),
            reverse=(sort_dir == "desc"),
        )
    elif sort_col == "latest_filing_date":
        # Derived column — re-sort within the page so users get a true
        # chronological order (rather than the initial_filing_date proxy
        # applied at the SQL layer).
        products_view.sort(
            key=lambda r: (r["latest_filing_date"] is None, r["latest_filing_date"] or date.min),
            reverse=(sort_dir == "desc"),
        )

    # Preserve every active query param (other than page) so pagination
    # links don't drop the user's filters. ``per_page`` is preserved as
    # the raw token so "all" round-trips correctly.
    base_qs_parts = []
    if status:           base_qs_parts.append(("status", status))
    if suite:            base_qs_parts.append(("suite", suite))
    if q:                base_qs_parts.append(("q", q))
    if urgency:          base_qs_parts.append(("urgency", urgency))
    if sort:             base_qs_parts.append(("sort", sort))
    if dir:              base_qs_parts.append(("dir", dir))
    if show_all:         base_qs_parts.append(("per_page", "all"))
    elif per_page_value != 50: base_qs_parts.append(("per_page", str(per_page_value)))
    if recent_days_value != 14: base_qs_parts.append(("recent_days", str(recent_days_value)))
    if hide_terminal_flag: base_qs_parts.append(("hide_terminal", "1"))
    from urllib.parse import urlencode
    base_qs = urlencode(base_qs_parts)
    base_qs_no_hide_terminal = urlencode([(k, v) for (k, v) in base_qs_parts if k != "hide_terminal"])
    # For per_page / recent_days / sort toggles we want the URL minus
    # the param being toggled, so the new value can be appended cleanly.
    base_qs_no_per_page = urlencode([(k, v) for (k, v) in base_qs_parts if k != "per_page"])
    base_qs_no_recent_days = urlencode([(k, v) for (k, v) in base_qs_parts if k != "recent_days"])
    base_qs_no_sort = urlencode([(k, v) for (k, v) in base_qs_parts if k not in ("sort", "dir")])

    is_admin = request.session.get("is_admin", False)

    return templates.TemplateResponse("pipeline_products.html", {
        "request": request,
        "today": today,
        "is_admin": is_admin,
        # KPIs — ``total`` is still used by the All suite-kpi pill above
        # the products table. The Quick Stats / Recent Activity sections
        # were removed in the O1 layout rewrite, so listed/filed/awaiting/
        # research/filings_last_7d/launches_last_30d/effectives_next_30d/
        # effectives_next_90d/avg_cycle/min_cycle/max_cycle/cycle_sample/
        # next_launches/recent_activity/last_updated_overall no longer
        # ship to the template. (The underlying DB queries are still run
        # because O3 owns the status-enum / DB query layer — only the
        # context dict was trimmed.)
        "total": total,
        # Counts
        "urgency_counts": urgency_counts,
        "status_counts": status_counts,
        "suite_counts": suite_counts,
        # Funnel — top-of-page chart (moved above By Suite + table)
        "funnel": funnel,
        "funnel_max": funnel_max,
        # Suite breakdown
        "suite_breakdown": suite_breakdown,
        "suite_colors": SUITE_COLORS,
        # Products (paginated)
        "products_view": products_view,
        "products": products_page,  # legacy alias
        "filtered_count": total_count,
        "page": page,
        "per_page": per_page_value,
        "per_page_token": "all" if show_all else str(per_page_value),
        "show_all_rows": show_all,
        "effective_per_page": effective_per_page,
        "per_page_warning": show_all and total_count > 500,
        "total_pages": total_pages,
        "page_offset": offset,
        "base_qs": base_qs,
        "base_qs_no_hide_terminal": base_qs_no_hide_terminal,
        "base_qs_no_per_page": base_qs_no_per_page,
        "base_qs_no_recent_days": base_qs_no_recent_days,
        "base_qs_no_sort": base_qs_no_sort,
        # Filter state
        "valid_statuses": VALID_STATUSES,
        "valid_suites": VALID_SUITES,
        "status_colors": STATUS_COLORS,
        "filter_status": status or "",
        "filter_suite": suite or "",
        "filter_q": q or "",
        "filter_urgency": urgency or "",
        "hide_terminal": hide_terminal_flag,
        "sort_col": sort_col,
        "sort_dir": sort_dir,
        # NOTE: ``recent_days`` + ``last_updated_overall`` removed in the
        # O1 layout rewrite (Recent Activity section deleted). They are
        # still computed above in case sibling routes import shared
        # helpers; only the context dict was trimmed.
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

    # ---- Status-change markers (TASK 4) ----
    # We can't reconstruct full status-transition history without an audit
    # log, but RexProduct.updated_at is populated whenever any field
    # (including status) is mutated through the admin update endpoint.
    # Surface a count of products touched per day in this month so the
    # calendar shows lifecycle activity. Future work: dedicated
    # rex_product_status_history table for true transition events.
    try:
        status_changes = (
            db.query(RexProduct.id, RexProduct.ticker, RexProduct.name,
                     RexProduct.status, RexProduct.updated_at)
            .filter(RexProduct.updated_at.isnot(None))
            .filter(func.date(RexProduct.updated_at).between(
                first_day.isoformat(), last_day.isoformat()))
            .all()
        )
        for sc in status_changes:
            if sc.updated_at is None:
                continue
            d = sc.updated_at.date() if hasattr(sc.updated_at, "date") else sc.updated_at
            if not (first_day <= d <= last_day):
                continue
            events_by_day[d].append({
                "type": "status_change",
                "ticker": sc.ticker or "",
                "name": sc.name,
                "status": sc.status,
                "color": "#8b5cf6",  # violet — distinct from other event types
            })
    except Exception:
        # Defensive: never break the calendar if updated_at is missing or
        # the column doesn't exist on legacy schemas.
        pass

    # ---- Build KPIs (always, regardless of type filter) ----
    total = db.query(RexProduct).count()
    listed = db.query(RexProduct).filter(RexProduct.status == "Listed").count()
    filed = db.query(RexProduct).filter(RexProduct.status.in_(["Filed", "Effective"])).count()
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
