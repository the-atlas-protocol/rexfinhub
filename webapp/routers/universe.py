"""
Universe router - Browse the full trust universe with filters.
"""
from __future__ import annotations

import math
import urllib.parse

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, or_
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Trust, FundStatus

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")

_TYPE_LABELS = {
    "etf_trust": "ETF Trust",
    "mutual_fund": "Mutual Fund",
    "grantor_trust": "Grantor Trust",
    "unknown": "Unknown",
}

_ACT_LABELS = {
    "40_act": "40 Act",
    "33_act": "33 Act",
    "unknown": "Unknown",
}

@router.get("/universe/")
def universe(
    request: Request,
    q: str = "",
    entity_type: str = "",
    regulatory_act: str = "",
    is_active: str = "",
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=100, ge=10, le=500),
    db: Session = Depends(get_db),
):
    """Browse the full trust universe with filters."""
    # Base query: trust + fund count
    fund_count_sq = (
        select(
            FundStatus.trust_id,
            func.count(FundStatus.id).label("fund_count"),
        )
        .group_by(FundStatus.trust_id)
        .subquery()
    )

    query = (
        select(
            Trust,
            func.coalesce(fund_count_sq.c.fund_count, 0).label("fund_count"),
        )
        .outerjoin(fund_count_sq, fund_count_sq.c.trust_id == Trust.id)
    )

    # Apply filters
    if q.strip():
        query = query.where(or_(
            Trust.name.ilike(f"%{q}%"),
            Trust.cik.ilike(f"%{q}%"),
        ))
    if entity_type:
        query = query.where(Trust.entity_type == entity_type)
    if regulatory_act:
        query = query.where(Trust.regulatory_act == regulatory_act)
    if is_active == "true":
        query = query.where(Trust.is_active == True)
    elif is_active == "false":
        query = query.where(Trust.is_active == False)
    # Default: show all (no is_active filter)

    # Count totals
    total_results = db.execute(
        select(func.count()).select_from(query.subquery())
    ).scalar() or 0
    total_pages = max(1, math.ceil(total_results / per_page))
    page = min(page, total_pages)

    # Paginate
    query = query.order_by(Trust.name)
    query = query.offset((page - 1) * per_page).limit(per_page)
    results = db.execute(query).all()

    # KPI counts by entity_type (across all trusts, not just filtered)
    type_counts_raw = db.execute(
        select(Trust.entity_type, func.count(Trust.id))
        .group_by(Trust.entity_type)
    ).all()
    type_counts = {row[0] or "unknown": row[1] for row in type_counts_raw}

    total_trusts = db.execute(select(func.count(Trust.id))).scalar() or 0

    # Build query string for pagination
    qs_params = {}
    if q:
        qs_params["q"] = q
    if entity_type:
        qs_params["entity_type"] = entity_type
    if regulatory_act:
        qs_params["regulatory_act"] = regulatory_act
    if is_active:
        qs_params["is_active"] = is_active
    if per_page != 100:
        qs_params["per_page"] = per_page
    base_qs = urllib.parse.urlencode(qs_params)

    return templates.TemplateResponse("universe.html", {
        "request": request,
        "trusts": results,
        "q": q,
        "entity_type": entity_type,
        "regulatory_act": regulatory_act,
        "is_active": is_active,
        "page": page,
        "per_page": per_page,
        "total_results": total_results,
        "total_pages": total_pages,
        "total_trusts": total_trusts,
        "type_counts": type_counts,
        "base_qs": base_qs,
        "type_labels": _TYPE_LABELS,
        "act_labels": _ACT_LABELS,
    })
