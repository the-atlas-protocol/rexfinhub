"""
Trust router - Trust detail page with all funds.
"""
from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, distinct
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Trust, FundStatus, Filing

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")

_FORM_TOOLTIPS = {
    "485BPOS": "Post-effective amendment - fund is actively trading",
    "485BXT": "Extension filing - extends time for fund to go effective",
    "485APOS": "Initial filing - fund has 75 days to become effective",
    "497": "Supplement to existing prospectus",
    "497K": "Summary prospectus supplement",
}


def _days_since(d: date | None) -> int | None:
    if not d:
        return None
    return (date.today() - d).days


def _expected_effective(form: str | None, filing_date: date | None, eff_date: date | None) -> dict | None:
    """Compute expected effective date and days remaining."""
    if eff_date:
        days_left = (eff_date - date.today()).days
        return {"date": eff_date, "days_left": days_left}
    if form and form.upper().startswith("485A") and filing_date:
        expected = filing_date + timedelta(days=75)
        days_left = (expected - date.today()).days
        return {"date": expected, "days_left": days_left}
    return None


@router.get("/")
def trusts_index(request: Request, db: Session = Depends(get_db)):
    """Browse-all trusts index — closes the discoverability gap (PR 2d)."""
    # Per-trust fund counts via subquery
    fund_counts = db.execute(
        select(
            FundStatus.trust_id,
            func.count(FundStatus.id).label("total"),
            func.sum(func.iif(FundStatus.status == "EFFECTIVE", 1, 0)).label("effective"),
            func.sum(func.iif(FundStatus.status == "PENDING", 1, 0)).label("pending"),
        )
        .group_by(FundStatus.trust_id)
    ).all()
    counts_by_trust: dict[int, dict] = {
        r.trust_id: {"total": r.total or 0, "effective": r.effective or 0, "pending": r.pending or 0}
        for r in fund_counts
    }

    # Recent-filing counts (last 30 days) per trust
    cutoff = date.today() - timedelta(days=30)
    recent_filings_q = db.execute(
        select(Filing.trust_id, func.count(Filing.id).label("n"))
        .where(Filing.filing_date >= cutoff)
        .group_by(Filing.trust_id)
    ).all()
    recent_by_trust: dict[int, int] = {r.trust_id: r.n for r in recent_filings_q}

    trusts = db.execute(
        select(Trust).where(Trust.is_active == True).order_by(Trust.name)
    ).scalars().all()

    rows = []
    for t in trusts:
        c = counts_by_trust.get(t.id, {})
        rows.append({
            "slug": t.slug,
            "name": t.name,
            "cik": t.cik,
            "regulatory_act": t.regulatory_act or "",
            "n_funds": c.get("total", 0),
            "n_effective": c.get("effective", 0),
            "n_pending": c.get("pending", 0),
            "n_recent_filings_30d": recent_by_trust.get(t.id, 0),
        })

    # Sort: most active first (recent filings desc, then total funds desc)
    rows.sort(key=lambda r: (-r["n_recent_filings_30d"], -r["n_funds"], r["name"]))

    return templates.TemplateResponse("trusts_index.html", {
        "request": request,
        "trusts": rows,
        "total_count": len(rows),
    })


@router.get("/{slug}")
def trust_detail(slug: str, request: Request, db: Session = Depends(get_db)):
    trust = db.execute(select(Trust).where(Trust.slug == slug)).scalar_one_or_none()
    if not trust:
        raise HTTPException(status_code=404, detail="Trust not found")

    funds = db.execute(
        select(FundStatus).where(FundStatus.trust_id == trust.id)
    ).scalars().all()

    # Sort: PENDING first, then DELAYED, then EFFECTIVE
    status_order = {"PENDING": 0, "DELAYED": 1, "EFFECTIVE": 2, "UNKNOWN": 3}
    funds = sorted(funds, key=lambda f: (
        status_order.get(f.status, 3),
        -(f.latest_filing_date.toordinal() if f.latest_filing_date else 0),
    ))

    # Enrich with computed fields
    enriched = []
    for f in funds:
        enriched.append({
            "fund": f,
            "days_since": _days_since(f.latest_filing_date),
            "expected": _expected_effective(f.latest_form, f.latest_filing_date, f.effective_date),
            "form_tooltip": _FORM_TOOLTIPS.get((f.latest_form or "").upper(), ""),
        })

    # Status counts
    eff_count = sum(1 for f in funds if f.status == "EFFECTIVE")
    pend_count = sum(1 for f in funds if f.status == "PENDING")
    delay_count = sum(1 for f in funds if f.status == "DELAYED")

    # Recent filings for this trust
    recent_filings = db.execute(
        select(Filing)
        .where(Filing.trust_id == trust.id)
        .order_by(Filing.filing_date.desc())
        .limit(20)
    ).scalars().all()

    # 13F disabled on production — always zero
    inst_13f_count = 0
    inst_13f_value = 0.0
    inst_13f_quarter = None

    return templates.TemplateResponse("trust_detail.html", {
        "request": request,
        "trust": trust,
        "funds": enriched,
        "total": len(funds),
        "effective": eff_count,
        "pending": pend_count,
        "delayed": delay_count,
        "recent_filings": recent_filings,
        "today": date.today(),
        "inst_13f_count": inst_13f_count,
        "inst_13f_value": inst_13f_value,
        "inst_13f_quarter": inst_13f_quarter,
    })
