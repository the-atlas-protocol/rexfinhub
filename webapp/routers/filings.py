"""
Filings router - Filing Explorer with dual-mode search (Funds + Filings).

Combines fund search and filing search into a single page with tab-based mode switching.
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
from webapp.models import Trust, Filing, FundExtraction, FundStatus

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")

PROSPECTUS_FORMS = ["485APOS", "485BPOS", "485BXT", "497", "497K"]
_DATE_RANGE_MAP = {"7": 7, "30": 30, "90": 90, "365": 365}


@router.get("/")
def filing_explorer(
    request: Request,
    mode: str = "funds",
    q: str = "",
    status: str = "",
    form_type: str = "",
    trust_id: int = 0,
    date_range: str = "all",
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=10, le=250),
    db: Session = Depends(get_db),
):
    """Filing Explorer — dual-mode page for searching funds and filings."""

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
            q=q, status=status, trust_id=trust_id, page=page, per_page=per_page,
            fund_count=fund_count, filing_count=filing_count, trust_count=trust_count,
        )
    else:
        return _handle_filings_mode(
            request, db, trusts,
            q=q, form_type=form_type, trust_id=trust_id, date_range=date_range,
            page=page, per_page=per_page,
            fund_count=fund_count, filing_count=filing_count, trust_count=trust_count,
        )


def _handle_funds_mode(
    request: Request,
    db: Session,
    trusts: list,
    *,
    q: str,
    status: str,
    trust_id: int,
    page: int,
    per_page: int,
    fund_count: int,
    filing_count: int,
    trust_count: int,
):
    """Funds tab — query FundStatus, adapted from funds.py logic."""
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

    # Exclude blank fund names
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

    # Count total
    count_q = select(func.count()).select_from(query.subquery())
    total_results = db.execute(count_q).scalar() or 0
    total_pages = max(1, math.ceil(total_results / per_page))

    if page > total_pages:
        page = total_pages

    query = query.order_by(FundStatus.fund_name)
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
    """Filings tab — query Filing + FundExtraction, adapted from original filings.py logic."""
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

    # Form type counts (for summary bar) — respect current filters except form_type
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
