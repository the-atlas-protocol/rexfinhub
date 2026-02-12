"""
Filings router - Filing list page with full search and filtering.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Trust, Filing, FundExtraction

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")

PROSPECTUS_FORMS = ["485APOS", "485BPOS", "485BXT", "497", "497K"]


@router.get("/")
def filing_list(
    request: Request,
    q: str = "",
    form: str = "",
    trust_id: int = 0,
    show_all: bool = False,
    db: Session = Depends(get_db),
):
    """Filing list with search, form filter, and trust filter."""
    query = (
        select(
            Filing,
            Trust.name.label("trust_name"),
            Trust.slug.label("trust_slug"),
            func.group_concat(FundExtraction.series_name.distinct()).label("fund_names"),
        )
        .join(Trust, Trust.id == Filing.trust_id)
        .outerjoin(FundExtraction, FundExtraction.filing_id == Filing.id)
        .group_by(Filing.id)
    )

    # Text search across accession number, trust name, and fund names
    if q:
        query = query.where(or_(
            Filing.accession_number.ilike(f"%{q}%"),
            Trust.name.ilike(f"%{q}%"),
            Filing.form.ilike(f"%{q}%"),
        ))

    if form:
        query = query.where(Filing.form.ilike(f"%{form}%"))
    elif not show_all:
        query = query.where(Filing.form.in_(PROSPECTUS_FORMS))

    if trust_id:
        query = query.where(Filing.trust_id == trust_id)

    query = query.order_by(Filing.filing_date.desc())
    results = db.execute(query).all()

    # Total filings count (unfiltered, prospectus only)
    total_all = db.execute(
        select(func.count()).select_from(Filing)
    ).scalar() or 0

    trusts = db.execute(
        select(Trust).where(Trust.is_active == True).order_by(Trust.name)
    ).scalars().all()

    return templates.TemplateResponse("filing_list.html", {
        "request": request,
        "filings": results,
        "trusts": trusts,
        "q": q,
        "form": form,
        "trust_id": trust_id,
        "show_all": show_all,
        "total": len(results),
        "total_all": total_all,
    })
