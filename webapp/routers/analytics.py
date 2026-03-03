"""
Analytics / Trends page - filing volume, trust growth, status distribution.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select, cast, String
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Trust, FundStatus, Filing

router = APIRouter(tags=["analytics"])
templates = Jinja2Templates(directory="webapp/templates")


@router.get("/analytics")
def analytics_page(
    request: Request,
    db: Session = Depends(get_db),
):
    # KPIs
    total_trusts = db.execute(
        select(func.count(Trust.id)).where(Trust.is_active == True)
    ).scalar() or 0
    total_filings = db.execute(select(func.count(Filing.id))).scalar() or 0
    total_funds = db.execute(select(func.count(FundStatus.id))).scalar() or 0
    cutoff_7d = date.today() - timedelta(days=7)
    filings_7d = db.execute(
        select(func.count(Filing.id)).where(Filing.filing_date >= cutoff_7d)
    ).scalar() or 0

    # 1. Filing volume by month (last 24 months)
    cutoff_24m = date.today() - timedelta(days=730)
    volume_rows = db.execute(
        select(
            func.strftime("%Y-%m", Filing.filing_date).label("month"),
            func.count(Filing.id).label("cnt"),
        )
        .where(Filing.filing_date >= cutoff_24m)
        .where(Filing.filing_date != None)
        .group_by("month")
        .order_by("month")
    ).all()
    volume_labels = [r.month for r in volume_rows]
    volume_data = [r.cnt for r in volume_rows]

    # 2. Trust growth cumulative by month
    growth_rows = db.execute(
        select(
            func.strftime("%Y-%m", Trust.created_at).label("month"),
            func.count(Trust.id).label("cnt"),
        )
        .group_by("month")
        .order_by("month")
    ).all()
    growth_labels = []
    growth_data = []
    running = 0
    for r in growth_rows:
        running += r.cnt
        growth_labels.append(r.month)
        growth_data.append(running)

    # 3. Fund status distribution
    status_rows = db.execute(
        select(FundStatus.status, func.count(FundStatus.id).label("cnt"))
        .group_by(FundStatus.status)
        .order_by(func.count(FundStatus.id).desc())
    ).all()
    status_labels = [r.status for r in status_rows]
    status_data = [r.cnt for r in status_rows]

    # 4. Top 10 form types
    form_rows = db.execute(
        select(Filing.form, func.count(Filing.id).label("cnt"))
        .group_by(Filing.form)
        .order_by(func.count(Filing.id).desc())
        .limit(10)
    ).all()
    form_labels = [r.form for r in form_rows]
    form_data = [r.cnt for r in form_rows]

    # 5. Entity type breakdown
    entity_rows = db.execute(
        select(
            func.coalesce(Trust.entity_type, "unknown").label("etype"),
            func.count(Trust.id).label("cnt"),
        )
        .where(Trust.is_active == True)
        .group_by("etype")
        .order_by(func.count(Trust.id).desc())
    ).all()
    entity_labels = [r.etype for r in entity_rows]
    entity_data = [r.cnt for r in entity_rows]

    return templates.TemplateResponse("analytics.html", {
        "request": request,
        "total_trusts": total_trusts,
        "total_filings": total_filings,
        "total_funds": total_funds,
        "filings_7d": filings_7d,
        "volume_labels": volume_labels,
        "volume_data": volume_data,
        "growth_labels": growth_labels,
        "growth_data": growth_data,
        "status_labels": status_labels,
        "status_data": status_data,
        "form_labels": form_labels,
        "form_data": form_data,
        "entity_labels": entity_labels,
        "entity_data": entity_data,
    })
