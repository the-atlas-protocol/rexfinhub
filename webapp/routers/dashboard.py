"""
Dashboard router - Main page with KPIs, trust list, recent activity.
"""
from __future__ import annotations

import math
import urllib.parse
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.fund_filters import MUTUAL_FUND_EXCLUSIONS
from webapp.models import Trust, FundStatus, Filing, FundExtraction

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")

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
def dashboard(
    request: Request,
    added: str = "",
    days: int = 7,
    form_type: str = "",
    filing_trust_id: int = 0,
    entity_type: str = "",
    trust_filter: str = "active",
    trust_page: int = Query(default=1, ge=1),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=10, le=200),
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
    if per_page != 50:
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
    if per_page != 50:
        trust_qs_params["per_page"] = per_page
    trust_base_qs = urllib.parse.urlencode(trust_qs_params)

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "trusts": trust_list,
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
        "entity_type": entity_type,
        "type_counts": type_counts,
        "type_labels": _TYPE_LABELS,
        "page": page,
        "per_page": per_page,
        "total_filings": total_filings,
        "total_pages": total_pages,
        "base_qs": base_qs,
        "trust_filter": trust_filter,
        "trust_page": trust_page,
        "total_trust_count": total_trust_count,
        "total_trust_pages": total_trust_pages,
        "trust_base_qs": trust_base_qs,
        "status_counts": status_counts,
        "status_labels": _STATUS_LABELS,
    })
