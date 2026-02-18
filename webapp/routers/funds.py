"""
Funds router - Fund list and detail pages.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, or_
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.fund_filters import MUTUAL_FUND_EXCLUSIONS
from webapp.models import Trust, FundStatus, NameHistory, FundExtraction, Filing

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")


@router.get("/")
def fund_list(
    request: Request,
    q: str = "",
    status: str = "",
    trust_id: int = 0,
    db: Session = Depends(get_db),
):
    """Paginated fund list with search and filters."""
    query = select(FundStatus, Trust.name.label("trust_name"), Trust.slug.label("trust_slug")).join(
        Trust, Trust.id == FundStatus.trust_id
    )

    # Exclude blank fund names (crypto S-1 filers with no 485 filings)
    query = query.where(FundStatus.fund_name != "")

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

    query = query.order_by(FundStatus.fund_name)
    results = db.execute(query).all()

    total_all = db.execute(select(func.count()).select_from(FundStatus)).scalar() or 0

    trusts = db.execute(
        select(Trust).where(Trust.is_active == True).order_by(Trust.name)
    ).scalars().all()

    return templates.TemplateResponse("fund_list.html", {
        "request": request,
        "funds": results,
        "trusts": trusts,
        "q": q,
        "status": status,
        "trust_id": trust_id,
        "total": len(results),
        "total_all": total_all,
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

    return templates.TemplateResponse("fund_detail.html", {
        "request": request,
        "fund": fund,
        "trust": trust,
        "names": names,
        "extractions": extractions,
    })
