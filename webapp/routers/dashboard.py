"""
Dashboard router - Main page with KPIs, trust list, recent activity.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.fund_filters import MUTUAL_FUND_EXCLUSIONS
from webapp.models import Trust, FundStatus, Filing, FundExtraction
from etp_tracker.trusts import get_act_type

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")

# REX trusts appear first
_PRIORITY_TRUSTS = ["REX ETF Trust", "ETF Opportunities Trust"]

_ALLOWED_DAYS = {7, 14, 30, 90, 0}


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
        trusts.append({
            "id": r.id, "name": r.name, "slug": r.slug, "is_rex": r.is_rex,
            "total": r.total or 0, "effective": r.effective or 0,
            "pending": r.pending or 0, "delayed": r.delayed or 0,
            "filing_count": r.filing_count or 0,
            "act_type": get_act_type(r.cik),
        })

    # Sort: priority trusts first, then alphabetical
    def sort_key(t):
        if t["name"] in _PRIORITY_TRUSTS:
            return (0, _PRIORITY_TRUSTS.index(t["name"]))
        return (1, t["name"])

    trusts.sort(key=sort_key)
    return trusts


@router.get("/")
def dashboard(
    request: Request,
    added: str = "",
    days: int = 7,
    form_type: str = "",
    filing_trust_id: int = 0,
    db: Session = Depends(get_db),
):
    trust_list = _trust_stats(db)

    total_funds = sum(t["total"] for t in trust_list)
    total_effective = sum(t["effective"] for t in trust_list)
    total_pending = sum(t["pending"] for t in trust_list)
    total_delayed = sum(t["delayed"] for t in trust_list)

    # Validate days parameter
    if days not in _ALLOWED_DAYS:
        days = 7

    # Recent filings with configurable filters
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

    if filing_trust_id:
        filing_query = filing_query.where(Filing.trust_id == filing_trust_id)

    filing_query = (
        filing_query
        .group_by(Filing.id)
        .order_by(Filing.filing_date.desc())
        .limit(50)
    )
    recent_filings = db.execute(filing_query).all()

    # Trust list for filing filter dropdown
    filing_trusts = db.execute(
        select(Trust).where(Trust.is_active == True).order_by(Trust.name)
    ).scalars().all()

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
    })
