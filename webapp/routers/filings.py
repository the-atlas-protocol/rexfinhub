"""
Filings router - Filing Explorer, Dashboard, Landscape, Candidates, Evaluator, Report.

Central hub for the Filings pillar: SEC filing data, competitive landscape,
Bloomberg-powered analysis, and candidate evaluation.
"""
from __future__ import annotations

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

PROSPECTUS_FORMS = ["485APOS", "485BPOS", "485BXT", "497", "497K"]
_DATE_RANGE_MAP = {"7": 7, "30": 30, "90": 90, "365": 365}


# ===================================================================
# Hub (root landing page)
# ===================================================================

@router.get("/")
def filings_hub(request: Request, db: Session = Depends(get_db)):
    """Filings Hub - landing page for the Filings pillar."""
    # Count KPIs
    fund_count = db.execute(select(func.count()).select_from(FundStatus)).scalar() or 0
    filing_count = db.execute(select(func.count()).select_from(Filing)).scalar() or 0
    trust_count = db.execute(
        select(func.count()).select_from(Trust).where(Trust.is_active == True)
    ).scalar() or 0

    return templates.TemplateResponse("filings_hub.html", {
        "request": request,
        "fund_count": fund_count,
        "filing_count": filing_count,
        "trust_count": trust_count,
    })


# ===================================================================
# Explorer (was GET /, now GET /explorer)
# ===================================================================

@router.get("/explorer")
def filing_explorer(
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
    """Filing Explorer - dual-mode page for searching funds and filings."""

    # Validate mode
    if mode not in ("funds", "filings"):
        mode = "funds"

    # Common data: trusts for dropdowns, global counts for KPIs
    trusts = db.execute(
        select(Trust).where(Trust.is_active == True).order_by(Trust.name)
    ).scalars().all()

    fund_count = db.execute(select(func.count()).select_from(FundStatus)).scalar() or 0
    filing_count = db.execute(select(func.count()).select_from(Filing)).scalar() or 0
    trust_count = len(trusts)

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
# Hub redirect (old /hub URL)
# ===================================================================

@router.get("/hub")
def filings_hub_redirect():
    """Redirect old /filings/hub to /filings/."""
    return RedirectResponse(url="/filings/", status_code=301)


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


@router.get("/dashboard")
def filings_dashboard(
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
    all_trusts = _trust_stats(db)

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

    # Recent filings with configurable filters (prospectus-related only)
    _PROSPECTUS_PREFIXES = ("485A", "485B", "497", "S-1", "S-3", "EFFECT", "POS AM")
    filing_query = (
        select(
            Filing,
            Trust.name.label("trust_name"),
            Trust.slug.label("trust_slug"),
            func.group_concat(FundExtraction.series_name.distinct()).label("fund_names"),
        )
        .join(Trust, Trust.id == Filing.trust_id)
        .outerjoin(FundExtraction, FundExtraction.filing_id == Filing.id)
    )

    if days > 0:
        cutoff = date.today() - timedelta(days=days)
        filing_query = filing_query.where(Filing.filing_date >= cutoff)

    if form_type:
        filing_query = filing_query.where(Filing.form.ilike(f"{form_type}%"))
    else:
        # Default: only prospectus-related forms on the dashboard
        filing_query = filing_query.where(
            or_(*(Filing.form.ilike(f"{p}%") for p in _PROSPECTUS_PREFIXES))
        )

    if filing_trust_id:
        filing_query = filing_query.where(Filing.trust_id == filing_trust_id)

    filing_query = (
        filing_query
        .group_by(Filing.id)
        .order_by(Filing.filing_date.desc())
    )

    # Count total filings before applying pagination
    total_filings = db.execute(
        select(func.count()).select_from(filing_query.subquery())
    ).scalar() or 0
    total_pages = max(1, math.ceil(total_filings / per_page))
    page = min(page, total_pages)

    # Apply pagination
    recent_filings = db.execute(
        filing_query.offset((page - 1) * per_page).limit(per_page)
    ).all()

    # Trust list for filing filter dropdown
    filing_trusts = db.execute(
        select(Trust).where(Trust.is_active == True).order_by(Trust.name)
    ).scalars().all()

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

    # Competitor filings this week (non-REX trusts, grouped by trust)
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


# ===================================================================
# Filing Landscape (moved from screener.py)
# ===================================================================

@router.get("/landscape")
def filing_landscape(
    request: Request,
    db: Session = Depends(get_db),
    leverage: str = Query("all"),
    view: str = Query("all"),
    q: str = Query(""),
):
    """Competitive Filing Landscape - the main screener landing page."""
    from webapp.services.filing_landscape import build_filing_landscape

    data = build_filing_landscape(db)
    matrices = data["matrices"]

    # Determine which leverage levels to show
    if leverage in ("2x", "3x", "4x", "5x"):
        display_leverages = [leverage]
    else:
        display_leverages = ["2x", "3x", "4x", "5x"]

    # Apply view filter (rex-only / missing)
    filtered_matrices = {}
    for lev in display_leverages:
        matrix = dict(matrices.get(lev, {}))
        if view == "rex-only":
            matrix = {
                u: iss_map for u, iss_map in matrix.items()
                if len(iss_map) == 1 and any(i in REX_ISSUERS for i in iss_map)
            }
        elif view == "missing":
            matrix = {
                u: iss_map for u, iss_map in matrix.items()
                if not any(i in REX_ISSUERS for i in iss_map)
            }

        # Apply search filter
        if q:
            q_upper = q.upper()
            matrix = {
                u: iss_map for u, iss_map in matrix.items()
                if q_upper in u.upper()
            }

        filtered_matrices[lev] = matrix

    any_results = any(bool(m) for m in filtered_matrices.values())

    return templates.TemplateResponse("screener_landscape.html", {
        "request": request,
        "kpis": data["kpis"],
        "filtered_matrices": filtered_matrices,
        "active_issuers": data["active_issuers"],
        "issuer_scorecard": data["issuer_scorecard"],
        "generated_at": data["generated_at"],
        "bloomberg_available": data_available(),
        "leverage": leverage,
        "view": view,
        "q": q,
        "display_leverages": display_leverages,
        "any_results": any_results,
    })


# ===================================================================
# LI Filing Candidates (moved from screener.py /3x-analysis)
# ===================================================================

@router.get("/candidates")
def filing_candidates(request: Request):
    """LI Filing Candidates - unified 2x/3x/4x leverage analysis."""
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
        "data_date": analysis.get("data_date"),
        "computed_at": analysis.get("computed_at"),
    })


# ===================================================================
# Candidate Evaluator (moved from screener.py /evaluate)
# ===================================================================

@router.get("/evaluator")
def filing_evaluator_page(request: Request):
    """Interactive candidate evaluator page."""
    return templates.TemplateResponse("screener_evaluate.html", {
        "request": request,
        "tab": "evaluate",
        "data_available": data_available(),
        "cache_warming": cache_warming(),
    })


@router.post("/evaluator")
def filing_evaluator_api(
    request: Request,
    tickers: list[str] = Body(..., embed=True),
):
    """API endpoint: evaluate candidate tickers and return JSON results."""
    if not tickers:
        return JSONResponse({"error": "No tickers provided"}, status_code=400)

    tickers = tickers[:20]

    if _ON_RENDER:
        return JSONResponse(
            {"error": "Evaluation requires Bloomberg data (run locally)."},
            status_code=404,
        )

    try:
        from screener.candidate_evaluator import evaluate_candidates
        results = evaluate_candidates(tickers)
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
        clean_results.append(serialize_eval(r))

    return JSONResponse({"results": clean_results})


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

    # Count total
    count_q = select(func.count()).select_from(query.subquery())
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

    query = (
        select(
            Filing,
            Trust.name.label("trust_name"),
            Trust.slug.label("trust_slug"),
            Trust.is_rex.label("is_rex"),
            func.group_concat(FundExtraction.series_name.distinct()).label("fund_names"),
        )
        .join(Trust, Trust.id == Filing.trust_id)
        .outerjoin(FundExtraction, FundExtraction.filing_id == Filing.id)
        .group_by(Filing.id)
    )

    # Text search
    if q:
        fund_match = (
            select(FundExtraction.filing_id)
            .where(FundExtraction.series_name.ilike(f"%{q}%"))
        )
        query = query.where(or_(
            Filing.accession_number.ilike(f"%{q}%"),
            Trust.name.ilike(f"%{q}%"),
            Filing.form.ilike(f"%{q}%"),
            Filing.id.in_(fund_match),
        ))

    # Form type filter
    if form_type:
        query = query.where(Filing.form.ilike(f"{form_type}%"))

    # Date range filter
    days = _DATE_RANGE_MAP.get(date_range)
    cutoff = None
    if days:
        cutoff = date.today() - timedelta(days=days)
        query = query.where(Filing.filing_date >= cutoff)

    # Trust filter
    if trust_id:
        query = query.where(Filing.trust_id == trust_id)

    query = query.order_by(Filing.filing_date.desc())

    # Count total before pagination
    total_filings = db.execute(
        select(func.count()).select_from(query.subquery())
    ).scalar() or 0
    total_pages = max(1, math.ceil(total_filings / per_page))
    page = min(page, total_pages)

    # Paginated results
    results = db.execute(
        query.offset((page - 1) * per_page).limit(per_page)
    ).all()

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
