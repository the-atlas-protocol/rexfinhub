"""
Filings router - Filing Explorer, Dashboard, Landscape, Candidates, Evaluator, Report.

Central hub for the Filings pillar: SEC filing data, competitive landscape,
Bloomberg-powered analysis, and candidate evaluation.
"""
from __future__ import annotations

import csv
import io
import logging
import math
import urllib.parse
from datetime import date, timedelta

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.fund_filters import MUTUAL_FUND_EXCLUSIONS
from webapp.models import Trust, Filing, FundExtraction, FundStatus
from webapp.services.screener_helpers import (
    get_3x_data,
    data_available,
    cache_warming,
    serialize_eval,
    _ON_RENDER,
    REX_ISSUERS,
)

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# In-memory TTL cache for heavy aggregations.
# build_filing_landscape() takes ~2.5 s and _trust_stats() takes ~1 s — both
# scan the full Filing/FundStatus tables and return data that only changes
# when the daily pipeline runs. A 5-minute TTL is well below the pipeline
# cadence and keeps repeat page loads under the 1 s budget.
# ---------------------------------------------------------------------------
import time as _time

_AGG_CACHE: dict[str, tuple[float, object]] = {}
_AGG_TTL_SECONDS = 300.0


def _cached(key: str, builder, ttl: float = _AGG_TTL_SECONDS):
    """Return cached value or rebuild it. Single-process, single-thread safe."""
    now = _time.monotonic()
    hit = _AGG_CACHE.get(key)
    if hit is not None and (now - hit[0]) < ttl:
        return hit[1]
    val = builder()
    _AGG_CACHE[key] = (now, val)
    return val



def _et_time(dt) -> str:
    """Format a naive UTC datetime as ET ('YYYY-MM-DD HH:MM ET'). Empty string for None."""
    if dt is None:
        return ""
    from datetime import timezone
    from zoneinfo import ZoneInfo
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M ET")


templates.env.filters["et_time"] = _et_time

PROSPECTUS_FORMS = ["485APOS", "485BPOS", "485BXT", "497", "497K"]
_DATE_RANGE_MAP = {"7": 7, "30": 30, "90": 90, "365": 365}


# ===================================================================
# Hub root — KILLED; both /filings/ and /filings/hub now 301 → /sec/etp/
# ===================================================================

@router.get("/")
def _filings_root_redirect():
    """301 the legacy filings hub root to the new SEC ETP dashboard."""
    return RedirectResponse(url="/sec/etp/", status_code=301)


# ===================================================================
# Explorer (was GET /, now GET /explorer)
# ===================================================================

def _filing_explorer_impl(
    request: Request,
    mode: str = "funds",
    q: str = "",
    status: str = "",
    form_type: str = "",
    trust_id: int = 0,
    date_range: str = "all",
    sort: str = "",
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=10, le=250),
    db: Session = Depends(get_db),
):
    """Filing Explorer — registered at /sec/etp/filings via sec_etp.py.
    Old /filings/explorer 301s to the new URL."""

    # Validate mode
    if mode not in ("funds", "filings"):
        mode = "funds"

    # Common data: trusts for dropdowns, global counts for KPIs.
    # Restrict dropdown to trusts with filings or fund records — otherwise
    # the <select> renders 15k+ <option>s (1.4 MB of dead HTML).
    # Cache: dropdown + KPI counts only change when the pipeline runs.
    trusts = _cached("explorer_trusts_dropdown", lambda: db.execute(
        select(Trust.id, Trust.name)
        .where(Trust.is_active == True)
        .where(or_(
            Trust.id.in_(select(Filing.trust_id).distinct()),
            Trust.id.in_(select(FundStatus.trust_id).distinct()),
        ))
        .order_by(Trust.name)
    ).all())

    counts = _cached("explorer_global_counts", lambda: {
        "fund_count": db.execute(select(func.count()).select_from(FundStatus)).scalar() or 0,
        "filing_count": db.execute(select(func.count()).select_from(Filing)).scalar() or 0,
        "trust_count": db.execute(
            select(func.count()).select_from(Trust).where(Trust.is_active == True)
        ).scalar() or 0,
    })
    fund_count = counts["fund_count"]
    filing_count = counts["filing_count"]
    trust_count = counts["trust_count"]

    if mode == "funds":
        return _handle_funds_mode(
            request, db, trusts,
            q=q, status=status, trust_id=trust_id, sort=sort, page=page, per_page=per_page,
            fund_count=fund_count, filing_count=filing_count, trust_count=trust_count,
        )
    else:
        return _handle_filings_mode(
            request, db, trusts,
            q=q, form_type=form_type, trust_id=trust_id, date_range=date_range,
            page=page, per_page=per_page,
            fund_count=fund_count, filing_count=filing_count, trust_count=trust_count,
        )


# ===================================================================
# Old-URL 301 redirects (legacy /filings/* → new pillar URLs)
# ===================================================================

@router.get("/explorer")
def _filing_explorer_redirect():
    """301 the legacy explorer URL to /sec/etp/filings."""
    return RedirectResponse(url="/sec/etp/filings", status_code=301)


@router.get("/hub")
def _filings_hub_redirect():
    """Redirect old /filings/hub to /sec/etp/."""
    return RedirectResponse(url="/sec/etp/", status_code=301)


# ===================================================================
# Dashboard (moved from dashboard.py)
# ===================================================================

# REX trusts appear first
_PRIORITY_TRUSTS = ["REX ETF Trust", "ETF Opportunities Trust"]

_ALLOWED_DAYS = {7, 14, 30, 90, 0}

_TYPE_LABELS = {
    "etf_trust": "ETF Trust",
    "mutual_fund": "Mutual Fund",
    "grantor_trust": "Grantor Trust",
    "unknown": "Unknown",
}


def _get_act_type(trust) -> str:
    """Get act type from trust model (DB-driven, replaces trusts.py lookup)."""
    if trust.regulatory_act:
        return "33" if trust.regulatory_act == "33_act" else "40"
    # Fallback for trusts without regulatory_act set
    try:
        from etp_tracker.trusts import get_act_type
        return get_act_type(trust.cik)
    except Exception:
        return "40"


def _trust_stats(db: Session) -> list[dict]:
    """Get per-trust fund counts, ordered: priority trusts first, then alpha."""
    # Subquery: ETF-only fund stats (excludes mutual fund share classes + blank names)
    fund_q = (
        select(
            FundStatus.trust_id,
            func.count(FundStatus.id).label("total"),
            func.sum(func.iif(FundStatus.status == "EFFECTIVE", 1, 0)).label("effective"),
            func.sum(func.iif(FundStatus.status == "PENDING", 1, 0)).label("pending"),
            func.sum(func.iif(FundStatus.status == "DELAYED", 1, 0)).label("delayed"),
        )
        .where(FundStatus.fund_name != "")
    )
    for pattern in MUTUAL_FUND_EXCLUSIONS:
        fund_q = fund_q.where(~FundStatus.fund_name.ilike(pattern))
    fund_sq = fund_q.group_by(FundStatus.trust_id).subquery()

    # Subquery: filing counts per trust
    filing_count_sq = (
        select(
            Filing.trust_id,
            func.count(Filing.id).label("fc"),
        )
        .group_by(Filing.trust_id)
        .subquery()
    )

    # Main query: all active trusts with left-joined stats
    query = (
        select(
            Trust.id,
            Trust.name,
            Trust.slug,
            Trust.cik,
            Trust.is_rex,
            Trust.entity_type,
            Trust.regulatory_act,
            Trust.last_filed,
            Trust.source,
            func.coalesce(fund_sq.c.total, 0).label("total"),
            func.coalesce(fund_sq.c.effective, 0).label("effective"),
            func.coalesce(fund_sq.c.pending, 0).label("pending"),
            func.coalesce(fund_sq.c.delayed, 0).label("delayed"),
            func.coalesce(filing_count_sq.c.fc, 0).label("filing_count"),
        )
        .outerjoin(fund_sq, fund_sq.c.trust_id == Trust.id)
        .outerjoin(filing_count_sq, filing_count_sq.c.trust_id == Trust.id)
        .where(Trust.is_active == True)
    )

    rows = db.execute(query).all()

    trusts = []
    for r in rows:
        # Determine act type from DB column or fallback
        if hasattr(r, 'regulatory_act') and r.regulatory_act:
            act_type = "33" if r.regulatory_act == "33_act" else "40"
        else:
            try:
                from etp_tracker.trusts import get_act_type as _legacy_act_type
                act_type = _legacy_act_type(r.cik)
            except Exception:
                act_type = "40"
        t = {
            "id": r.id, "name": r.name, "slug": r.slug, "is_rex": r.is_rex,
            "total": r.total or 0, "effective": r.effective or 0,
            "pending": r.pending or 0, "delayed": r.delayed or 0,
            "filing_count": r.filing_count or 0,
            "act_type": act_type,
            "entity_type": getattr(r, 'entity_type', None),
            "last_filed": getattr(r, 'last_filed', None),
            "source": getattr(r, 'source', None),
        }
        # Classify scrape status for display
        if t["filing_count"] == 0:
            t["scrape_status"] = "pending"
        elif t["total"] == 0 and t["act_type"] == "33":
            t["scrape_status"] = "s1_filer"
        elif t["total"] == 0:
            t["scrape_status"] = "no_etf_funds"
        else:
            t["scrape_status"] = "active"
        trusts.append(t)

    # Sort: priority trusts first, then alphabetical
    def sort_key(t):
        if t["name"] in _PRIORITY_TRUSTS:
            return (0, _PRIORITY_TRUSTS.index(t["name"]))
        return (1, t["name"])

    trusts.sort(key=sort_key)
    return trusts


_ALLOWED_TRUST_FILTERS = {"all", "active", "pending", "no_etf_funds", "s1_filer"}

_STATUS_LABELS = {
    "all": "All",
    "active": "With ETF Funds",
    "pending": "Awaiting Scrape",
    "no_etf_funds": "No ETF Funds",
    "s1_filer": "33 Act Filers",
}

TRUST_PAGE_SIZE = 60


def _dashboard_impl(
    request: Request,
    added: str = "",
    days: int = 7,
    form_type: str = "",
    filing_trust_id: int = 0,
    entity_type: str = "",
    trust_filter: str = "active",
    trust_page: int = Query(default=1, ge=1),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=10, ge=10, le=200),
    db: Session = Depends(get_db),
):
    """Filings Dashboard — registered at /sec/etp/ via sec_etp.py.
    Old /filings/dashboard 301s to the new URL."""
    # Cached for 5 minutes — _trust_stats scans 15k+ trusts and joins on
    # FundStatus + Filing aggregates (~1 s uncached). The pipeline only
    # mutates these tables a few times a day, so a 5 min TTL is safe.
    all_trusts = _cached("trust_stats", lambda: _trust_stats(db))

    # Filter by entity_type if specified
    if entity_type:
        all_trusts = [t for t in all_trusts if t.get("entity_type") == entity_type]

    # KPIs always reflect full (entity-filtered) set
    total_funds = sum(t["total"] for t in all_trusts)
    total_effective = sum(t["effective"] for t in all_trusts)
    total_pending = sum(t["pending"] for t in all_trusts)
    total_delayed = sum(t["delayed"] for t in all_trusts)

    # Weekly filing delta for KPI trend
    cutoff_7d = date.today() - timedelta(days=7)
    new_filings_7d = db.execute(
        select(func.count(Filing.id)).where(Filing.filing_date >= cutoff_7d)
    ).scalar() or 0

    # Status counts for filter buttons (before applying trust_filter)
    status_counts = {}
    for t in all_trusts:
        s = t["scrape_status"]
        status_counts[s] = status_counts.get(s, 0) + 1

    # Apply trust_filter to the card grid
    if trust_filter not in _ALLOWED_TRUST_FILTERS:
        trust_filter = "active"
    if trust_filter != "all":
        trust_list = [t for t in all_trusts if t["scrape_status"] == trust_filter]
    else:
        trust_list = all_trusts

    # Server-side pagination for trust grid
    total_trust_count = len(trust_list)
    total_trust_pages = max(1, math.ceil(total_trust_count / TRUST_PAGE_SIZE))
    trust_page = min(trust_page, total_trust_pages)
    trust_list = trust_list[(trust_page - 1) * TRUST_PAGE_SIZE : trust_page * TRUST_PAGE_SIZE]

    # Validate days parameter
    if days not in _ALLOWED_DAYS:
        days = 7

    # Recent filings with configurable filters (prospectus-related only).
    # Two-step query: paginate Filing+Trust first, then backfill fund_names
    # for the visible page. The previous single-query GROUP_CONCAT pattern
    # forced SQLite to materialize the full grouped result before COUNT,
    # adding seconds for no benefit.
    _PROSPECTUS_PREFIXES = ("485A", "485B", "497", "S-1", "S-3", "EFFECT", "POS AM")
    base_filing_q = (
        select(
            Filing,
            Trust.name.label("trust_name"),
            Trust.slug.label("trust_slug"),
        )
        .join(Trust, Trust.id == Filing.trust_id)
    )

    if days > 0:
        cutoff = date.today() - timedelta(days=days)
        base_filing_q = base_filing_q.where(Filing.filing_date >= cutoff)

    if form_type:
        base_filing_q = base_filing_q.where(Filing.form.ilike(f"{form_type}%"))
    else:
        base_filing_q = base_filing_q.where(
            or_(*(Filing.form.ilike(f"{p}%") for p in _PROSPECTUS_PREFIXES))
        )

    if filing_trust_id:
        base_filing_q = base_filing_q.where(Filing.trust_id == filing_trust_id)

    base_filing_q = base_filing_q.order_by(Filing.filing_date.desc())

    # Cheap count on Filing (no GROUP BY)
    count_filing_q = select(func.count(Filing.id)).select_from(Filing).join(
        Trust, Trust.id == Filing.trust_id
    )
    if days > 0:
        count_filing_q = count_filing_q.where(Filing.filing_date >= cutoff)
    if form_type:
        count_filing_q = count_filing_q.where(Filing.form.ilike(f"{form_type}%"))
    else:
        count_filing_q = count_filing_q.where(
            or_(*(Filing.form.ilike(f"{p}%") for p in _PROSPECTUS_PREFIXES))
        )
    if filing_trust_id:
        count_filing_q = count_filing_q.where(Filing.trust_id == filing_trust_id)

    total_filings = db.execute(count_filing_q).scalar() or 0
    total_pages = max(1, math.ceil(total_filings / per_page))
    page = min(page, total_pages)

    page_filings = db.execute(
        base_filing_q.offset((page - 1) * per_page).limit(per_page)
    ).all()

    # Backfill fund_names per visible page.
    page_fids = [r.Filing.id for r in page_filings]
    fund_names_map: dict[int, str] = {}
    if page_fids:
        fund_rows_dash = db.execute(
            select(
                FundExtraction.filing_id,
                func.group_concat(FundExtraction.series_name.distinct()).label("names"),
            )
            .where(FundExtraction.filing_id.in_(page_fids))
            .group_by(FundExtraction.filing_id)
        ).all()
        fund_names_map = {fid: names for fid, names in fund_rows_dash}

    class _DashFilingRow:
        __slots__ = ("Filing", "trust_name", "trust_slug", "fund_names")

        def __init__(self, src, fund_names):
            self.Filing = src.Filing
            self.trust_name = src.trust_name
            self.trust_slug = src.trust_slug
            self.fund_names = fund_names

    recent_filings = [
        _DashFilingRow(r, fund_names_map.get(r.Filing.id, ""))
        for r in page_filings
    ]

    # Trust list for filing filter dropdown.
    # Only trusts that filed in the last 365 days can meaningfully appear in
    # the dashboard's recent-filings table (max window is 90d, default 7d) —
    # the unfiltered set is 15k+ rows and dominates page weight (~250 KB).
    one_year_ago = date.today() - timedelta(days=365)
    filing_trusts = db.execute(
        select(Trust.id, Trust.name)
        .where(Trust.is_active == True)
        .where(Trust.id.in_(
            select(Filing.trust_id)
            .where(Filing.filing_date >= one_year_ago)
            .distinct()
        ))
        .order_by(Trust.name)
    ).all()

    # Build query string preserving current filters (without page)
    qs_params = {}
    if days != 7:
        qs_params["days"] = days
    if form_type:
        qs_params["form_type"] = form_type
    if filing_trust_id:
        qs_params["filing_trust_id"] = filing_trust_id
    if entity_type:
        qs_params["entity_type"] = entity_type
    if per_page != 10:
        qs_params["per_page"] = per_page
    if trust_filter != "active":
        qs_params["trust_filter"] = trust_filter
    base_qs = urllib.parse.urlencode(qs_params)

    # Entity type counts (reuse all_trusts, no second query)
    type_counts = {}
    for t in all_trusts:
        et = t.get("entity_type") or "unknown"
        type_counts[et] = type_counts.get(et, 0) + 1

    # Trust grid query string (preserves trust_filter + entity_type, resets trust_page)
    trust_qs_params = {}
    if trust_filter != "active":
        trust_qs_params["trust_filter"] = trust_filter
    if entity_type:
        trust_qs_params["entity_type"] = entity_type
    if days != 7:
        trust_qs_params["days"] = days
    if form_type:
        trust_qs_params["form_type"] = form_type
    if filing_trust_id:
        trust_qs_params["filing_trust_id"] = filing_trust_id
    if per_page != 10:
        trust_qs_params["per_page"] = per_page
    trust_base_qs = urllib.parse.urlencode(trust_qs_params)

    # Today's filing count (for KPI strip)
    todays_filings = db.execute(
        select(func.count(Filing.id)).where(Filing.filing_date == date.today())
    ).scalar() or 0

    # Count distinct trusts that filed today (for KPI context line)
    todays_trust_count = db.execute(
        select(func.count(func.distinct(Filing.trust_id)))
        .where(Filing.filing_date == date.today())
    ).scalar() or 0

    # Competitor new fund filings this week (485BPOS/485APOS only — new funds, not supplements)
    week_ago = date.today() - timedelta(days=7)
    competitor_filings = db.execute(text("""
        SELECT t.name as trust_name, t.id as trust_id,
               COUNT(*) as filing_count,
               GROUP_CONCAT(DISTINCT f.form) as form_types,
               GROUP_CONCAT(DISTINCT fe.series_name) as fund_names
        FROM filings f
        JOIN trusts t ON f.trust_id = t.id
        LEFT JOIN fund_extractions fe ON fe.filing_id = f.id
        WHERE f.filing_date >= :week_ago
        AND (f.form LIKE '485BPOS%' OR f.form LIKE '485APOS%')
        AND t.name NOT LIKE '%REX%'
        AND t.name NOT LIKE '%T-REX%'
        AND t.name NOT LIKE '%MicroSectors%'
        AND t.is_active = 1
        GROUP BY t.id
        ORDER BY COUNT(*) DESC
        LIMIT 10
    """), {"week_ago": str(week_ago)}).fetchall()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "trusts": all_trusts,
        "total_funds": total_funds,
        "total_effective": total_effective,
        "total_pending": total_pending,
        "total_delayed": total_delayed,
        "recent_filings": recent_filings,
        "today": date.today(),
        "added": added,
        "days": days,
        "form_type": form_type,
        "filing_trust_id": filing_trust_id,
        "filing_trusts": filing_trusts,
        "page": page,
        "per_page": per_page,
        "total_filings": total_filings,
        "total_pages": total_pages,
        "base_qs": base_qs,
        "status_counts": status_counts,
        "new_filings_7d": new_filings_7d,
        "todays_filings": todays_filings,
        "todays_trust_count": todays_trust_count,
        "competitor_filings": competitor_filings,
    })


@router.get("/dashboard")
def _dashboard_redirect():
    """301 the legacy dashboard URL to /sec/etp/."""
    return RedirectResponse(url="/sec/etp/", status_code=301)


# ===================================================================
# Filing Landscape (moved from screener.py)
# ===================================================================

def _filter_fund_rows(
    fund_rows: list[dict],
    leverage: str,
    view: str,
    q: str,
) -> list[dict]:
    """Apply leverage / view / search filters to fund_rows."""
    filtered = fund_rows

    # Leverage filter
    if leverage and leverage != "all":
        filtered = [r for r in filtered if r["leverage"] == leverage]

    # View filter
    if view == "rex-only":
        filtered = [r for r in filtered if r["issuer"] in REX_ISSUERS]
    elif view == "missing":
        filtered = [r for r in filtered if r["issuer"] not in REX_ISSUERS]

    # Search filter (case-insensitive across multiple fields)
    if q:
        ql = q.lower()
        filtered = [
            r for r in filtered
            if ql in (r["underlier"] or "").lower()
            or ql in (r["fund_name"] or "").lower()
            or ql in (r["ticker"] or "").lower()
            or ql in (r["issuer"] or "").lower()
        ]

    return filtered


def _landscape_impl(
    request: Request,
    db: Session = Depends(get_db),
    mode: str = Query("filings"),
    leverage: str = Query("all"),
    view: str = Query("all"),
    q: str = Query(""),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=10, le=200),
):
    """L&I Landscape — registered at /sec/etp/leverageandinverse via sec_etp.py.
    Old /filings/landscape 301s to the new URL."""
    if mode not in ("filings", "products"):
        mode = "filings"

    # Clamp per_page to allowed values
    if per_page not in (25, 50, 100, 200):
        per_page = 50

    # ------------------------------------------------------------------
    # Filings mode: SEC filing matrix + fund-level rows
    # Server-side filter + paginate the fund table; cap the matrix view.
    # Previously dumped all 3k+ fund rows + 1k+ matrix rows × 10 issuer cols
    # in a single response (~5.7 MB). Now: paginated fund table + capped
    # matrix = under 500 KB and under 1s.
    # ------------------------------------------------------------------
    if mode == "filings":
        from webapp.services.filing_landscape import build_filing_landscape

        # Cached for 5 minutes — build_filing_landscape aggregates the entire
        # L&I universe (~2.5 s uncached) and only changes when the pipeline runs.
        data = _cached("filing_landscape", lambda: build_filing_landscape(db))
        matrices = data["matrices"]

        # Apply server-side filters to fund rows
        all_fund_rows = data["fund_rows"]
        filtered_rows = _filter_fund_rows(all_fund_rows, leverage, view, q)

        # Pagination
        total_rows = len(filtered_rows)
        total_pages = max(1, math.ceil(total_rows / per_page))
        page = min(page, total_pages)
        page_start = (page - 1) * per_page
        page_rows = filtered_rows[page_start : page_start + per_page]

        # Build flat rows for the matrix view, capped to top 100 underliers
        # by fund count (matrix is behind a collapsed <details>, rarely opened —
        # full set explodes the page; users filter via Top Underliers chips).
        MATRIX_CAP = 100
        all_issuers_ordered = data["all_active_issuers"]
        flat_rows_unranked = []
        for lev in ("2x", "3x", "4x", "5x"):
            for underlier in sorted(matrices.get(lev, {}).keys()):
                issuers_map = matrices[lev][underlier]
                has_rex = any(i in REX_ISSUERS for i in issuers_map)
                flat_rows_unranked.append({
                    "underlier": underlier,
                    "leverage": lev,
                    "issuers": issuers_map,
                    "has_rex": has_rex,
                    "_score": len(issuers_map),
                })
        flat_rows_unranked.sort(key=lambda r: (-r["_score"], r["underlier"]))
        flat_rows_total = len(flat_rows_unranked)
        flat_rows = flat_rows_unranked[:MATRIX_CAP]
        for r in flat_rows:
            r.pop("_score", None)

        # Build base_qs preserving filters (excludes page so pager can append)
        qs_params = {"mode": "filings"}
        if leverage and leverage != "all":
            qs_params["leverage"] = leverage
        if view and view != "all":
            qs_params["view"] = view
        if q:
            qs_params["q"] = q
        if per_page != 50:
            qs_params["per_page"] = per_page
        base_qs = urllib.parse.urlencode(qs_params)

        return templates.TemplateResponse("screener_landscape.html", {
            "request": request,
            "mode": "filings",
            "kpis": data["kpis"],
            "flat_rows": flat_rows,
            "flat_rows_total": flat_rows_total,
            "matrix_cap": MATRIX_CAP,
            "all_issuers": all_issuers_ordered,
            "issuer_scorecard": data["issuer_scorecard"],
            "generated_at": data["generated_at"],
            "leverage": leverage,
            "view": view,
            "q": q,
            # Server-side paginated rows
            "fund_rows": page_rows,
            "page": page,
            "per_page": per_page,
            "total_rows": total_rows,
            "total_pages": total_pages,
            "base_qs": base_qs,
            "top_underliers": data["top_underliers"],
            "leverage_counts": data["leverage_counts"],
        })

    # ------------------------------------------------------------------
    # Products mode: load from screener cache (works on Render + local)
    # ------------------------------------------------------------------
    products = []
    products_error = ""

    analysis = get_3x_data()
    if analysis and analysis.get("li_products"):
        products = analysis["li_products"]
    else:
        products_error = "Product data not yet cached. Score Bloomberg data from the Admin panel first."

    return templates.TemplateResponse("screener_landscape.html", {
        "request": request,
        "mode": "products",
        "products": products,
        "products_error": products_error,
        "leverage": leverage,
        "view": view,
        "q": q,
    })


# ===================================================================
# Landscape CSV Export
# ===================================================================

def _stream_landscape_csv(header: list[str], row_generator):
    """Yield CSV content row-by-row (avoids buffering entire file)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(header)
    yield buf.getvalue()
    for row_data in row_generator:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(row_data)
        yield buf.getvalue()


def _landscape_export_impl(
    db: Session = Depends(get_db),
    leverage: str = Query("all"),
    view: str = Query("all"),
    q: str = Query(""),
):
    """Export filtered landscape fund rows as CSV.
    Registered at /sec/etp/leverageandinverse/export via sec_etp.py.
    Old /filings/landscape/export 301s to the new URL."""
    from webapp.services.filing_landscape import build_filing_landscape

    data = _cached("filing_landscape", lambda: build_filing_landscape(db))
    filtered = _filter_fund_rows(data["fund_rows"], leverage, view, q)

    header = [
        "Leverage", "Fund Name", "Ticker", "Issuer", "Trust",
        "Underlier", "Status", "Effective Date", "Latest Filing",
        "Form", "Prospectus Link",
    ]

    def rows():
        for r in filtered:
            yield [
                r["leverage"],
                r["fund_name"],
                r["ticker"] or "",
                r["issuer"],
                r["trust"],
                r["underlier"],
                r["status"],
                str(r["effective_date"]) if r["effective_date"] else "",
                str(r["latest_filing_date"]) if r["latest_filing_date"] else "",
                r["latest_form"] or "",
                r["prospectus_link"] or "",
            ]

    return StreamingResponse(
        _stream_landscape_csv(header, rows()),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=li_landscape_export.csv"},
    )


@router.get("/landscape")
def _landscape_redirect(request: Request):
    """301 the legacy landscape URL to /sec/etp/leverageandinverse (preserve querystring)."""
    qs = str(request.url.query)
    target = "/sec/etp/leverageandinverse" + (("?" + qs) if qs else "")
    return RedirectResponse(url=target, status_code=301)


@router.get("/landscape/export")
def _landscape_export_redirect(request: Request):
    """301 the legacy landscape CSV export to /sec/etp/leverageandinverse/export."""
    qs = str(request.url.query)
    target = "/sec/etp/leverageandinverse/export" + (("?" + qs) if qs else "")
    return RedirectResponse(url=target, status_code=301)


# ===================================================================
# LI Filing Candidates (moved from screener.py /3x-analysis)
# ===================================================================

def _candidates_impl(request: Request):
    """LI Filing Candidates — registered at /tools/li/candidates via tools_li.py.
    Old /filings/candidates 301s to the new URL."""
    analysis = get_3x_data()

    if analysis is None:
        return templates.TemplateResponse("screener_3x.html", {
            "request": request,
            "tab": "recommendations",
            "data_available": data_available(),
            "cache_warming": cache_warming(),
        })

    four_x = analysis.get("four_x", [])
    avg_daily_vol = 0
    if four_x:
        avg_daily_vol = sum(c.get("daily_vol", 0) for c in four_x) / len(four_x)

    two_x_candidates = analysis.get("two_x_candidates", [])

    return templates.TemplateResponse("screener_3x.html", {
        "request": request,
        "tab": "recommendations",
        "data_available": True,
        "snapshot": analysis["snapshot"],
        "tiers": analysis["tiers"],
        "four_x": four_x,
        "four_x_count": len(four_x),
        "avg_daily_vol": avg_daily_vol,
        "top_2x": analysis.get("top_2x", []),
        "two_x_candidates": two_x_candidates,
        "data_date": analysis.get("data_date"),
        "computed_at": analysis.get("computed_at"),
    })


# ===================================================================
# Candidate Evaluator (merged into /tools/li/candidates in PR 3)
# ===================================================================

def _evaluator_get_impl(request: Request):
    """Interactive candidate evaluator page.
    Registered at /tools/li/candidates via tools_li.py — for PR 1, candidates
    impl renders the page; this handler stays available for the merged UI in PR 3.
    Old /filings/evaluator 301s to the new URL."""
    return templates.TemplateResponse("screener_evaluate.html", {
        "request": request,
        "tab": "evaluate",
        "data_available": data_available(),
        "cache_warming": cache_warming(),
    })


def _evaluator_post_impl(
    request: Request,
    tickers: list[str] = Body(..., embed=True),
):
    """API endpoint: evaluate candidate tickers and return JSON results.
    Registered at POST /tools/li/candidates via tools_li.py.
    Old POST /filings/evaluator 301s (308) to the new URL."""
    if not tickers:
        return JSONResponse({"error": "No tickers provided"}, status_code=400)

    tickers = tickers[:20]

    if _ON_RENDER:
        # Try cached evaluations
        analysis = get_3x_data()
        eval_cache = analysis.get("eval_cache", {}) if analysis else {}

        cached_results = []
        missing = []
        for t in tickers:
            tc = t.upper().replace(" US", "")
            if tc in eval_cache:
                cached_results.append(eval_cache[tc])
            else:
                missing.append(tc)

        if cached_results:
            return JSONResponse({"results": cached_results, "cached": True, "missing": missing})

        return JSONResponse(
            {"error": "Ticker not in cache. Available: " + ", ".join(sorted(eval_cache.keys())[:20])},
            status_code=404,
        )

    try:
        from dataclasses import asdict
        from screener.foundation_scorer import score_candidates
        results = score_candidates(tickers)
    except FileNotFoundError:
        return JSONResponse(
            {"error": "Bloomberg data file not found. Upload from Admin panel first."},
            status_code=404,
        )
    except Exception as e:
        log.error("Candidate evaluation failed: %s", e)
        return JSONResponse({"error": str(e)[:200]}, status_code=500)

    clean_results = []
    for r in results:
        clean_results.append(serialize_eval(asdict(r)))

    return JSONResponse({"results": clean_results})


@router.get("/candidates")
def _candidates_redirect():
    """301 the legacy candidates URL to /tools/li/candidates."""
    return RedirectResponse(url="/tools/li/candidates", status_code=301)


@router.get("/evaluator")
def _evaluator_get_redirect():
    """301 the legacy evaluator GET to /tools/li/candidates."""
    return RedirectResponse(url="/tools/li/candidates", status_code=301)


@router.post("/evaluator")
def _evaluator_post_redirect():
    """308 the legacy evaluator POST to /tools/li/candidates (preserve method+body)."""
    return RedirectResponse(url="/tools/li/candidates", status_code=308)


# ===================================================================
# Report Download (moved from screener.py /report)
# ===================================================================

@router.get("/report")
def filing_report(request: Request):
    """Generate and download the 3x/4x PDF report."""
    if _ON_RENDER:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(
            "<h2>Report generation is not available on Render. Run locally.</h2>",
            status_code=404,
        )
    try:
        from screener.generate_report import run_3x_report
        out_path = run_3x_report()
        pdf_bytes = out_path.read_bytes()

        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={out_path.name}"},
        )
    except FileNotFoundError:
        from fastapi.responses import HTMLResponse
        return HTMLResponse(
            "<h2>No Bloomberg data. Upload from Admin panel first.</h2>",
            status_code=404,
        )
    except Exception as e:
        log.error("Report generation failed: %s", e)
        from fastapi.responses import HTMLResponse
        return HTMLResponse(f"<h2>Report generation failed</h2><p>{e}</p>", status_code=500)


# ===================================================================
# Explorer helpers (private)
# ===================================================================

def _handle_funds_mode(
    request: Request,
    db: Session,
    trusts: list,
    *,
    q: str,
    status: str,
    trust_id: int,
    sort: str = "",
    page: int = 1,
    per_page: int = 25,
    fund_count: int = 0,
    filing_count: int = 0,
    trust_count: int = 0,
):
    """Funds tab - query FundStatus, adapted from funds.py logic."""
    # Clamp per_page
    if per_page not in (25, 50, 100, 250):
        per_page = 25
    if page < 1:
        page = 1

    query = select(
        FundStatus,
        Trust.name.label("trust_name"),
        Trust.slug.label("trust_slug"),
    ).join(Trust, Trust.id == FundStatus.trust_id)

    # Exclude blank/placeholder fund names
    query = query.where(FundStatus.fund_name.notin_(["", "-", " "]))

    # Exclude mutual fund share classes
    for pattern in MUTUAL_FUND_EXCLUSIONS:
        query = query.where(~FundStatus.fund_name.ilike(pattern))

    if q:
        query = query.where(or_(
            FundStatus.fund_name.ilike(f"%{q}%"),
            FundStatus.ticker.ilike(f"%{q}%"),
        ))
    if status:
        query = query.where(FundStatus.status == status.upper())
    if trust_id:
        query = query.where(FundStatus.trust_id == trust_id)

    # Direct COUNT (no subquery wrapper). When the user has applied no extra
    # filters, the count of "ETF funds with non-blank names" is stable per
    # session — cache it for 5 min to skip the 23-NOT-ILIKE table scan.
    has_filters = bool(q or status or trust_id)
    if not has_filters:
        cache_key = "explorer_funds_count"
        total_results = _cached(
            cache_key,
            lambda: db.execute(
                select(func.count(FundStatus.id))
                .where(FundStatus.fund_name.notin_(["", "-", " "]))
                .where(*[~FundStatus.fund_name.ilike(p) for p in MUTUAL_FUND_EXCLUSIONS])
            ).scalar() or 0,
        )
    else:
        # Count directly on FundStatus — Trust join unnecessary for COUNT
        # since we only filter on FundStatus.trust_id (no Trust columns).
        count_q = select(func.count(FundStatus.id)).where(
            FundStatus.fund_name.notin_(["", "-", " "])
        )
        for pattern in MUTUAL_FUND_EXCLUSIONS:
            count_q = count_q.where(~FundStatus.fund_name.ilike(pattern))
        if q:
            count_q = count_q.where(or_(
                FundStatus.fund_name.ilike(f"%{q}%"),
                FundStatus.ticker.ilike(f"%{q}%"),
            ))
        if status:
            count_q = count_q.where(FundStatus.status == status.upper())
        if trust_id:
            count_q = count_q.where(FundStatus.trust_id == trust_id)
        total_results = db.execute(count_q).scalar() or 0
    total_pages = max(1, math.ceil(total_results / per_page))

    if page > total_pages:
        page = total_pages

    # Server-side sorting
    sort_map = {
        "name": FundStatus.fund_name.asc(),
        "name_desc": FundStatus.fund_name.desc(),
        "ticker": FundStatus.ticker.asc().nullslast(),
        "ticker_desc": FundStatus.ticker.desc().nullsfirst(),
        "status": FundStatus.status.asc(),
        "status_desc": FundStatus.status.desc(),
        "date": FundStatus.latest_filing_date.asc().nullslast(),
        "date_desc": FundStatus.latest_filing_date.desc().nullslast(),
    }
    order = sort_map.get(sort, FundStatus.latest_filing_date.desc().nullslast())
    query = query.order_by(order)
    query = query.offset((page - 1) * per_page).limit(per_page)
    results = db.execute(query).all()

    return templates.TemplateResponse("filing_explorer.html", {
        "request": request,
        "mode": "funds",
        "funds": results,
        "trusts": trusts,
        "q": q,
        "status": status,
        "trust_id": trust_id,
        "sort": sort,
        "page": page,
        "per_page": per_page,
        "total_results": total_results,
        "total_pages": total_pages,
        "fund_count": fund_count,
        "filing_count": filing_count,
        "trust_count": trust_count,
    })


def _handle_filings_mode(
    request: Request,
    db: Session,
    trusts: list,
    *,
    q: str,
    form_type: str,
    trust_id: int,
    date_range: str,
    page: int,
    per_page: int,
    fund_count: int,
    filing_count: int,
    trust_count: int,
):
    """Filings tab - query Filing + FundExtraction, adapted from original filings.py logic."""
    # Clamp per_page
    if per_page not in (25, 50, 100, 200):
        per_page = 50

    # Step 1: paginate Filing+Trust only (fast index scan).
    # Step 2: fetch fund_names for the visible page in one extra query.
    # The previous single-query GROUP_CONCAT-with-COUNT pattern took ~3 s for
    # the count alone because SQLite materialized the entire grouped result.
    base_q = (
        select(
            Filing,
            Trust.name.label("trust_name"),
            Trust.slug.label("trust_slug"),
            Trust.is_rex.label("is_rex"),
        )
        .join(Trust, Trust.id == Filing.trust_id)
    )

    # Text search
    if q:
        fund_match = (
            select(FundExtraction.filing_id)
            .where(FundExtraction.series_name.ilike(f"%{q}%"))
        )
        base_q = base_q.where(or_(
            Filing.accession_number.ilike(f"%{q}%"),
            Trust.name.ilike(f"%{q}%"),
            Filing.form.ilike(f"%{q}%"),
            Filing.id.in_(fund_match),
        ))

    # Form type filter
    if form_type:
        base_q = base_q.where(Filing.form.ilike(f"{form_type}%"))

    # Date range filter
    days = _DATE_RANGE_MAP.get(date_range)
    cutoff = None
    if days:
        cutoff = date.today() - timedelta(days=days)
        base_q = base_q.where(Filing.filing_date >= cutoff)

    # Trust filter
    if trust_id:
        base_q = base_q.where(Filing.trust_id == trust_id)

    base_q = base_q.order_by(Filing.filing_date.desc())

    # Cheap COUNT on Filing (no GROUP BY).
    count_q = select(func.count(Filing.id)).select_from(Filing).join(
        Trust, Trust.id == Filing.trust_id
    )
    if q:
        count_q = count_q.where(or_(
            Filing.accession_number.ilike(f"%{q}%"),
            Trust.name.ilike(f"%{q}%"),
            Filing.form.ilike(f"%{q}%"),
            Filing.id.in_(
                select(FundExtraction.filing_id)
                .where(FundExtraction.series_name.ilike(f"%{q}%"))
            ),
        ))
    if form_type:
        count_q = count_q.where(Filing.form.ilike(f"{form_type}%"))
    if cutoff is not None:
        count_q = count_q.where(Filing.filing_date >= cutoff)
    if trust_id:
        count_q = count_q.where(Filing.trust_id == trust_id)

    total_filings = db.execute(count_q).scalar() or 0
    total_pages = max(1, math.ceil(total_filings / per_page))
    page = min(page, total_pages)

    # Paginated rows (no fund_names yet)
    page_rows = db.execute(
        base_q.offset((page - 1) * per_page).limit(per_page)
    ).all()

    # Backfill fund_names per page in a single query.
    page_filing_ids = [r.Filing.id for r in page_rows]
    fund_names_map: dict[int, str] = {}
    if page_filing_ids:
        fund_rows = db.execute(
            select(
                FundExtraction.filing_id,
                func.group_concat(FundExtraction.series_name.distinct()).label("names"),
            )
            .where(FundExtraction.filing_id.in_(page_filing_ids))
            .group_by(FundExtraction.filing_id)
        ).all()
        fund_names_map = {fid: names for fid, names in fund_rows}

    # Wrap each Row with a fund_names attr for template compatibility.
    class _FilingRow:
        __slots__ = ("Filing", "trust_name", "trust_slug", "is_rex", "fund_names")

        def __init__(self, src, fund_names):
            self.Filing = src.Filing
            self.trust_name = src.trust_name
            self.trust_slug = src.trust_slug
            self.is_rex = src.is_rex
            self.fund_names = fund_names

    results = [
        _FilingRow(r, fund_names_map.get(r.Filing.id, ""))
        for r in page_rows
    ]

    # Form type counts (for summary bar) - respect current filters except form_type
    count_query = select(Filing.form, func.count(Filing.id).label("cnt"))
    if q:
        fund_match_count = (
            select(FundExtraction.filing_id)
            .where(FundExtraction.series_name.ilike(f"%{q}%"))
        )
        count_query = (
            count_query
            .join(Trust, Trust.id == Filing.trust_id)
            .where(or_(
                Filing.accession_number.ilike(f"%{q}%"),
                Trust.name.ilike(f"%{q}%"),
                Filing.form.ilike(f"%{q}%"),
                Filing.id.in_(fund_match_count),
            ))
        )
    if cutoff is not None:
        count_query = count_query.where(Filing.filing_date >= cutoff)
    if trust_id:
        count_query = count_query.where(Filing.trust_id == trust_id)
    count_query = count_query.group_by(Filing.form)
    raw_counts = db.execute(count_query).all()

    form_counts = {}
    for form_name, cnt in raw_counts:
        key = form_name.upper().strip() if form_name else "OTHER"
        if key.startswith("485B") and "BXT" not in key:
            form_counts["485BPOS"] = form_counts.get("485BPOS", 0) + cnt
        elif "BXT" in key:
            form_counts["485BXT"] = form_counts.get("485BXT", 0) + cnt
        elif key.startswith("485A"):
            form_counts["485APOS"] = form_counts.get("485APOS", 0) + cnt
        elif key.startswith("497"):
            form_counts["497"] = form_counts.get("497", 0) + cnt
        else:
            form_counts["OTHER"] = form_counts.get("OTHER", 0) + cnt

    # Build query string for pagination links (preserve all filters, exclude page)
    qs_params = {"mode": "filings"}
    if q:
        qs_params["q"] = q
    if form_type:
        qs_params["form_type"] = form_type
    if trust_id:
        qs_params["trust_id"] = trust_id
    if date_range != "all":
        qs_params["date_range"] = date_range
    if per_page != 50:
        qs_params["per_page"] = per_page
    base_qs = urllib.parse.urlencode(qs_params)

    return templates.TemplateResponse("filing_explorer.html", {
        "request": request,
        "mode": "filings",
        "filings": results,
        "trusts": trusts,
        "q": q,
        "form_type": form_type,
        "trust_id": trust_id,
        "date_range": date_range,
        "page": page,
        "per_page": per_page,
        "total_filings": total_filings,
        "total_pages": total_pages,
        "form_counts": form_counts,
        "base_qs": base_qs,
        "fund_count": fund_count,
        "filing_count": filing_count,
        "trust_count": trust_count,
    })


# ===================================================================
# Symbols — CBOE symbol-reservation availability + competitor pipeline intel
# ===================================================================

def _symbols_impl(
    request: Request,
    db: Session = Depends(get_db),
    length: str = Query("all"),
    state: str = Query("all"),
    q: str = Query(""),
    sort: str = Query("last_checked_desc"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=100, ge=10, le=500),
):
    """CBOE symbol-reservation table — registered at /tools/tickers via tools_tickers.py.
    Available / reserved / active across the 1-4 letter ticker space. Joined to
    mkt_master_data so taken symbols show fund name + issuer when known;
    reserved-without-known-fund = competitor pipeline intel.
    Old /filings/symbols 301s to the new URL."""
    from webapp.services.cboe.cross_reference import (
        auth_health, enriched_rows, last_scan, summary_counts,
    )
    from webapp.services.cboe.live import is_ticker_query, live_check

    # If the search box holds a clean 1-4 letter ticker, hit CBOE live and
    # upsert before querying — guarantees the row reflects right-now state
    # rather than whatever the last bulk scan caught.
    live_refresh: dict | None = None
    if is_ticker_query(q):
        live_refresh = live_check(q.strip().upper())

    length_int: int | None = None
    if length in ("1", "2", "3", "4"):
        length_int = int(length)

    state_filter: str | None = state if state in ("available", "reserved", "active", "unknown") else None

    if sort not in ("last_checked_desc", "ticker", "length", "state"):
        sort = "last_checked_desc"

    offset = (page - 1) * per_page
    rows, total = enriched_rows(
        db,
        length=length_int,
        state=state_filter,
        search=q.strip() or None,
        sort=sort,
        limit=per_page,
        offset=offset,
    )

    counts = summary_counts(db)
    last = last_scan(db)
    health = auth_health(db)
    total_pages = max(1, math.ceil(total / per_page)) if total else 1

    base_qs = urllib.parse.urlencode({
        k: v for k, v in {
            "length": length, "state": state, "q": q, "sort": sort, "per_page": per_page,
        }.items() if v not in ("", None, "all")
    })

    return templates.TemplateResponse("filings_symbols.html", {
        "request": request,
        "rows": rows,
        "total": total,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
        "length": length,
        "state": state,
        "q": q,
        "sort": sort,
        "counts": counts,
        "last_scan": last,
        "auth_health": health,
        "live_refresh": live_refresh,
        "base_qs": base_qs,
    })


@router.get("/symbols")
def _symbols_redirect(request: Request):
    """301 the legacy CBOE symbols URL to /tools/tickers (preserve querystring)."""
    qs = str(request.url.query)
    target = "/tools/tickers" + (("?" + qs) if qs else "")
    return RedirectResponse(url=target, status_code=301)
