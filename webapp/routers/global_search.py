"""
Global search API - searches trusts, funds, and filings via ILIKE.
Ctrl+K palette in the frontend hits this endpoint.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Trust, FundStatus, Filing

router = APIRouter(prefix="/api/v1", tags=["search"])


@router.get("/search")
def global_search(
    q: str = Query("", min_length=0, max_length=200),
    limit: int = Query(20, ge=1, le=50),
    db: Session = Depends(get_db),
):
    q = q.strip()
    if not q:
        return {"trusts": [], "funds": [], "filings": []}

    pattern = f"%{q}%"

    # 1. Trust search: name + CIK
    trust_rows = db.execute(
        select(Trust.id, Trust.name, Trust.slug, Trust.cik, Trust.entity_type)
        .where(Trust.is_active == True)
        .where((Trust.name.ilike(pattern)) | (Trust.cik.ilike(pattern)))
        .order_by(Trust.name)
        .limit(limit)
    ).all()
    trusts = [
        {"name": r.name, "slug": r.slug, "cik": r.cik, "entity_type": r.entity_type}
        for r in trust_rows
    ]

    # 2. Fund search: fund_name + ticker + series_id
    fund_rows = db.execute(
        select(
            FundStatus.fund_name,
            FundStatus.ticker,
            FundStatus.status,
            FundStatus.series_id,
            Trust.name.label("trust_name"),
            Trust.slug.label("trust_slug"),
        )
        .join(Trust, Trust.id == FundStatus.trust_id)
        .where(
            (FundStatus.fund_name.ilike(pattern))
            | (FundStatus.ticker.ilike(pattern))
            | (FundStatus.series_id.ilike(pattern))
        )
        .order_by(FundStatus.fund_name)
        .limit(limit)
    ).all()
    funds = [
        {
            "fund_name": r.fund_name,
            "ticker": r.ticker,
            "status": r.status,
            "trust_name": r.trust_name,
            "trust_slug": r.trust_slug,
        }
        for r in fund_rows
    ]

    # 3. Filing search: accession_number + registrant
    filing_rows = db.execute(
        select(
            Filing.id,
            Filing.accession_number,
            Filing.form,
            Filing.filing_date,
            Filing.registrant,
            Trust.name.label("trust_name"),
            Trust.slug.label("trust_slug"),
        )
        .join(Trust, Trust.id == Filing.trust_id)
        .where(
            (Filing.accession_number.ilike(pattern))
            | (Filing.registrant.ilike(pattern))
        )
        .order_by(Filing.filing_date.desc())
        .limit(limit)
    ).all()
    filings = [
        {
            "id": r.id,
            "accession": r.accession_number,
            "form": r.form,
            "filing_date": str(r.filing_date) if r.filing_date else None,
            "trust_name": r.trust_name,
            "trust_slug": r.trust_slug,
        }
        for r in filing_rows
    ]

    return {"trusts": trusts, "funds": funds, "filings": filings}
