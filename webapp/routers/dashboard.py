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
from webapp.models import Trust, FundStatus, Filing, FundExtraction

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")

# REX trusts appear first
_PRIORITY_TRUSTS = ["REX ETF Trust", "ETF Opportunities Trust"]


def _trust_stats(db: Session) -> list[dict]:
    """Get per-trust fund counts, ordered: priority trusts first, then alpha."""
    rows = db.execute(
        select(
            Trust.id,
            Trust.name,
            Trust.slug,
            Trust.is_rex,
            func.count(FundStatus.id).label("total"),
            func.sum(func.iif(FundStatus.status == "EFFECTIVE", 1, 0)).label("effective"),
            func.sum(func.iif(FundStatus.status == "PENDING", 1, 0)).label("pending"),
            func.sum(func.iif(FundStatus.status == "DELAYED", 1, 0)).label("delayed"),
        )
        .join(FundStatus, FundStatus.trust_id == Trust.id, isouter=True)
        .where(Trust.is_active == True)
        .group_by(Trust.id)
    ).all()

    trusts = []
    for r in rows:
        trusts.append({
            "id": r.id, "name": r.name, "slug": r.slug, "is_rex": r.is_rex,
            "total": r.total or 0, "effective": r.effective or 0,
            "pending": r.pending or 0, "delayed": r.delayed or 0,
        })

    # Sort: priority trusts first, then alphabetical
    def sort_key(t):
        if t["name"] in _PRIORITY_TRUSTS:
            return (0, _PRIORITY_TRUSTS.index(t["name"]))
        return (1, t["name"])

    trusts.sort(key=sort_key)
    return trusts


@router.get("/")
def dashboard(request: Request, added: str = "", db: Session = Depends(get_db)):
    trust_list = _trust_stats(db)

    total_funds = sum(t["total"] for t in trust_list)
    total_effective = sum(t["effective"] for t in trust_list)
    total_pending = sum(t["pending"] for t in trust_list)
    total_delayed = sum(t["delayed"] for t in trust_list)

    # Recent filings (last 7 days)
    week_ago = date.today() - timedelta(days=7)
    recent_filings = db.execute(
        select(
            Filing,
            Trust.name.label("trust_name"),
            Trust.slug.label("trust_slug"),
            func.group_concat(FundExtraction.series_name.distinct()).label("fund_names"),
        )
        .join(Trust, Trust.id == Filing.trust_id)
        .outerjoin(FundExtraction, FundExtraction.filing_id == Filing.id)
        .where(Filing.filing_date >= week_ago)
        .group_by(Filing.id)
        .order_by(Filing.filing_date.desc())
        .limit(20)
    ).all()

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
    })
