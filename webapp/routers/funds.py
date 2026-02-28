"""
Funds router - Fund list and detail pages.
"""
from __future__ import annotations

import math

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, or_, desc, distinct
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.fund_filters import MUTUAL_FUND_EXCLUSIONS
from webapp.models import Trust, FundStatus, NameHistory, FundExtraction, Filing, CusipMapping, Holding, Institution

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")


@router.get("/")
def fund_list(
    request: Request,
    q: str = "",
    status: str = "",
    trust_id: int = 0,
    show_mutual: str = "",
    page: int = 1,
    per_page: int = 100,
    db: Session = Depends(get_db),
):
    """Paginated fund list with search and filters."""
    # Clamp per_page to allowed values
    if per_page not in (25, 50, 100, 250):
        per_page = 100
    if page < 1:
        page = 1

    query = select(FundStatus, Trust.name.label("trust_name"), Trust.slug.label("trust_slug")).join(
        Trust, Trust.id == FundStatus.trust_id
    )

    # Exclude blank fund names (crypto S-1 filers with no 485 filings)
    query = query.where(FundStatus.fund_name != "")

    # Exclude mutual fund share classes unless toggled on
    if show_mutual != "true":
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

    # Count total matching results before pagination
    count_query = select(func.count()).select_from(query.subquery())
    total_results = db.execute(count_query).scalar() or 0
    total_pages = max(1, math.ceil(total_results / per_page))

    if page > total_pages:
        page = total_pages

    query = query.order_by(FundStatus.fund_name)
    query = query.offset((page - 1) * per_page).limit(per_page)
    results = db.execute(query).all()

    total_all = db.execute(select(func.count()).select_from(FundStatus)).scalar() or 0

    trusts = db.execute(
        select(Trust).where(Trust.is_active == True).order_by(Trust.name)
    ).scalars().all()

    # Diagnostic: trusts with zero FundStatus records
    trusts_with_no_funds = db.execute(
        select(Trust.name).where(Trust.is_active == True)
        .where(~Trust.id.in_(select(FundStatus.trust_id).distinct()))
        .order_by(Trust.name)
    ).scalars().all()

    return templates.TemplateResponse("fund_list.html", {
        "request": request,
        "funds": results,
        "trusts": trusts,
        "q": q,
        "status": status,
        "trust_id": trust_id,
        "show_mutual": show_mutual,
        "total": total_results,
        "total_all": total_all,
        "page": page,
        "per_page": per_page,
        "total_results": total_results,
        "total_pages": total_pages,
        "trusts_with_no_funds": trusts_with_no_funds,
    })


@router.get("/{series_id}")
def fund_detail(series_id: str, request: Request, db: Session = Depends(get_db)):
    """Fund detail page with history."""
    fund = db.execute(
        select(FundStatus).where(FundStatus.series_id == series_id)
    ).scalar_one_or_none()

    if not fund:
        raise HTTPException(status_code=404, detail="Fund not found")

    trust = db.execute(select(Trust).where(Trust.id == fund.trust_id)).scalar_one_or_none()

    # Name history
    names = db.execute(
        select(NameHistory)
        .where(NameHistory.series_id == series_id)
        .order_by(NameHistory.first_seen_date.desc())
    ).scalars().all()

    # All extractions for this series (filing history)
    extractions = db.execute(
        select(FundExtraction, Filing.id.label("filing_id"), Filing.form, Filing.filing_date, Filing.primary_link)
        .join(Filing, Filing.id == FundExtraction.filing_id)
        .where(FundExtraction.series_id == series_id)
        .order_by(Filing.filing_date.desc())
    ).all()

    # 13F institutional holders for this fund's ticker
    holders_13f = []
    holders_count = 0
    holders_total_value = 0.0
    holders_ticker = fund.ticker
    if fund.ticker:
        mapping = db.execute(
            select(CusipMapping).where(func.upper(CusipMapping.ticker) == fund.ticker.upper())
        ).scalar_one_or_none()
        if mapping and mapping.cusip:
            latest_q = db.execute(
                select(func.max(Holding.report_date)).where(Holding.cusip == mapping.cusip)
            ).scalar()
            if latest_q:
                holders_count = db.execute(
                    select(func.count(distinct(Holding.institution_id)))
                    .where(Holding.cusip == mapping.cusip, Holding.report_date == latest_q)
                ).scalar() or 0
                holders_total_value = db.execute(
                    select(func.sum(Holding.value_usd))
                    .where(Holding.cusip == mapping.cusip, Holding.report_date == latest_q)
                ).scalar() or 0
                top_holders = db.execute(
                    select(Holding, Institution.name.label("inst_name"))
                    .join(Institution, Institution.id == Holding.institution_id)
                    .where(Holding.cusip == mapping.cusip, Holding.report_date == latest_q)
                    .order_by(desc(Holding.value_usd))
                    .limit(10)
                ).all()
                holders_13f = [
                    {"name": r.inst_name, "value_usd": r.Holding.value_usd, "shares": r.Holding.shares}
                    for r in top_holders
                ]

    return templates.TemplateResponse("fund_detail.html", {
        "request": request,
        "fund": fund,
        "trust": trust,
        "names": names,
        "extractions": extractions,
        "holders_13f": holders_13f,
        "holders_count": holders_count,
        "holders_total_value": holders_total_value,
        "holders_ticker": holders_ticker,
    })
