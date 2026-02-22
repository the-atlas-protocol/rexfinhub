"""
Advanced Market Intelligence routes.

Routes:
  GET /market/timeline   -> Fund Lifecycle Timeline (per-trust filing history)
  GET /market/calendar   -> Compliance Calendar (upcoming extensions, recent effectivities)
  GET /market/compare    -> Fund Comparison (side-by-side ticker comparison)
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Query, Request, Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from webapp.dependencies import get_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/market", tags=["market-advanced"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))


@router.get("/timeline")
def timeline_view(
    request: Request,
    trust_id: int = Query(default=None),
    db: Session = Depends(get_db),
):
    """Fund Lifecycle Timeline - shows filing history for a selected trust."""
    from webapp.models import Trust, Filing, FundExtraction

    trusts = db.execute(select(Trust).order_by(Trust.name)).scalars().all()

    timeline_items = []
    selected_trust = None

    if trust_id:
        selected_trust = db.get(Trust, trust_id)
        if selected_trust:
            filings = db.execute(
                select(Filing)
                .where(Filing.trust_id == trust_id)
                .where(Filing.form.in_(["485BPOS", "485BXT", "485APOS", "N-14"]))
                .order_by(desc(Filing.filing_date))
                .limit(200)
            ).scalars().all()

            for filing in filings:
                extractions = db.execute(
                    select(FundExtraction)
                    .where(FundExtraction.filing_id == filing.id)
                    .limit(10)
                ).scalars().all()

                # Get effective date from first extraction if available
                eff_date = None
                for ext in extractions:
                    if ext.effective_date:
                        eff_date = ext.effective_date
                        break

                timeline_items.append({
                    "filing": filing,
                    "extractions": extractions,
                    "fund_count": len(extractions),
                    "effective_date": eff_date,
                })

    return templates.TemplateResponse("market/timeline.html", {
        "request": request,
        "active_tab": "timeline",
        "available": True,
        "trusts": trusts,
        "selected_trust": selected_trust,
        "trust_id": trust_id,
        "timeline_items": timeline_items,
    })


@router.get("/calendar")
def calendar_view(
    request: Request,
    db: Session = Depends(get_db),
):
    """Compliance Calendar - recent fund launches and upcoming effective events."""
    from webapp.models import Trust, Filing, FundExtraction

    today = date.today()
    cutoff_90 = today - timedelta(days=90)
    cutoff_60_ahead = today + timedelta(days=60)

    # Recent fund launches: 485BPOS with effective date in last 90 days
    recent_rows = db.execute(
        select(FundExtraction, Filing, Trust)
        .join(Filing, FundExtraction.filing_id == Filing.id)
        .join(Trust, Filing.trust_id == Trust.id)
        .where(Filing.form == "485BPOS")
        .where(FundExtraction.effective_date >= cutoff_90)
        .where(FundExtraction.effective_date <= today)
        .order_by(FundExtraction.effective_date.desc())
        .limit(100)
    ).all()

    # Upcoming effective events: any form with future effective date
    upcoming_rows = db.execute(
        select(FundExtraction, Filing, Trust)
        .join(Filing, FundExtraction.filing_id == Filing.id)
        .join(Trust, Filing.trust_id == Trust.id)
        .where(FundExtraction.effective_date > today)
        .where(FundExtraction.effective_date <= cutoff_60_ahead)
        .order_by(FundExtraction.effective_date.asc())
        .limit(100)
    ).all()

    # Deduplicate by accession_number
    seen_recent = set()
    recent_launches = []
    for extraction, filing, trust in recent_rows:
        if filing.accession_number in seen_recent:
            continue
        seen_recent.add(filing.accession_number)
        days_since = (today - extraction.effective_date).days if extraction.effective_date else 0
        recent_launches.append({
            "filing": filing,
            "trust": trust,
            "days_since": days_since,
        })

    seen_upcoming = set()
    upcoming_classified = []
    for extraction, filing, trust in upcoming_rows:
        if filing.accession_number in seen_upcoming:
            continue
        seen_upcoming.add(filing.accession_number)
        days_until = (extraction.effective_date - today).days if extraction.effective_date else 0
        urgency = "green" if days_until > 30 else "amber" if days_until > 7 else "red"
        upcoming_classified.append({
            "filing": filing,
            "trust": trust,
            "days_until": days_until,
            "urgency": urgency,
        })

    return templates.TemplateResponse("market/calendar.html", {
        "request": request,
        "active_tab": "calendar",
        "available": True,
        "today": today,
        "recent_launches": recent_launches,
        "upcoming": upcoming_classified,
    })


@router.get("/compare")
def compare_view(
    request: Request,
    tickers: str = Query(default=""),
):
    """Fund Comparison - side-by-side comparison of up to 4 tickers."""
    from webapp.services.market_data import get_master_data, data_available, _fmt_currency, _fmt_flow

    available = data_available()
    # Strip " US" from user-submitted tickers
    ticker_list = [t.upper().replace(" US", "").strip() for t in tickers.split(",") if t.strip()][:4]

    fund_data = []
    totalrealreturns_url = ""
    if available and ticker_list:
        try:
            master = get_master_data()
            # Use ticker_clean column for matching
            match_col = "ticker_clean" if "ticker_clean" in master.columns else None
            if not match_col:
                match_col = next((c for c in master.columns if c.lower() == "ticker"), None)

            aum_col = "t_w4.aum"
            if match_col:
                for ticker in ticker_list:
                    row = master[master[match_col].str.upper() == ticker.upper()]
                    if not row.empty:
                        r = row.iloc[0]

                        # AUM history: last 12 months (aum_1 = most recent prior month, aum = current)
                        aum_history = []
                        for i in range(12, 0, -1):
                            col = f"t_w4.aum_{i}"
                            if col in master.columns:
                                val = float(r.get(col, 0) or 0)
                                aum_history.append(val)
                        # Current month
                        aum_history.append(float(r.get(aum_col, 0) or 0))

                        # Flows by period
                        flows = {
                            "1w": float(r.get("t_w4.fund_flow_1week", 0) or 0),
                            "1m": float(r.get("t_w4.fund_flow_1month", 0) or 0),
                            "3m": float(r.get("t_w4.fund_flow_3month", 0) or 0),
                            "6m": float(r.get("t_w4.fund_flow_6month", 0) or 0),
                            "ytd": float(r.get("t_w4.fund_flow_ytd", 0) or 0),
                        }

                        fund_data.append({
                            "ticker": ticker,
                            "row": r.to_dict(),
                            "aum_history": aum_history,
                            "flows": flows,
                            "flows_fmt": {k: _fmt_flow(v) for k, v in flows.items()},
                            "aum_fmt": _fmt_currency(float(r.get(aum_col, 0) or 0)),
                            "inception_date": str(r.get("inception_date", "")) if r.get("inception_date") else "",
                        })

            # Build totalrealreturns URL
            clean_tickers = [t.upper() for t in ticker_list]
            totalrealreturns_url = f"https://totalrealreturns.com/n/{','.join(clean_tickers)}"
        except Exception:
            log.exception("Error loading compare data")

    return templates.TemplateResponse("market/compare.html", {
        "request": request,
        "active_tab": "compare",
        "available": available,
        "tickers": tickers,
        "ticker_list": ticker_list,
        "fund_data": fund_data,
        "totalrealreturns_url": totalrealreturns_url,
    })
