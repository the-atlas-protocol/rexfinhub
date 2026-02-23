"""
Advanced Market Intelligence routes.

Routes:
  GET /market/calendar   -> Compliance Calendar (upcoming extensions, recent effectivities)
  GET /market/compare    -> Fund Comparison (side-by-side ticker comparison)
"""
from __future__ import annotations

import calendar as cal_mod
import logging
from collections import defaultdict
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


@router.get("/calendar")
def calendar_view(
    request: Request,
    db: Session = Depends(get_db),
    month: int = Query(default=None),
    year: int = Query(default=None),
):
    """Compliance Calendar - recent fund launches and upcoming effective events."""
    from webapp.models import Trust, Filing, FundExtraction

    today = date.today()
    cal_month = month if month and 1 <= month <= 12 else today.month
    cal_year = year if year else today.year
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
            "effective_date": extraction.effective_date,
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
            "effective_date": extraction.effective_date,
        })

    # Build calendar grid: events_by_date for the selected month
    events_by_date = defaultdict(list)
    for item in recent_launches:
        eff = item.get("effective_date")
        if eff and eff.year == cal_year and eff.month == cal_month:
            trust_name = item["trust"].name if item["trust"] else ""
            events_by_date[eff.day].append({
                "type": "launch",
                "label": trust_name[:20],
                "trust": trust_name,
                "form": item["filing"].form if item.get("filing") else "",
            })
    for item in upcoming_classified:
        eff = item.get("effective_date")
        if eff and eff.year == cal_year and eff.month == cal_month:
            trust_name = item["trust"].name if item["trust"] else ""
            events_by_date[eff.day].append({
                "type": "upcoming",
                "label": trust_name[:20],
                "trust": trust_name,
                "form": item["filing"].form if item.get("filing") else "",
                "urgency": item["urgency"],
            })

    # Build weeks grid (list of 7-element arrays, None for empty cells)
    first_weekday, num_days = cal_mod.monthrange(cal_year, cal_month)
    weeks = []
    current_week = [None] * first_weekday
    for day in range(1, num_days + 1):
        current_week.append({
            "day": day,
            "events": events_by_date.get(day, []),
            "is_today": (day == today.day and cal_month == today.month and cal_year == today.year),
        })
        if len(current_week) == 7:
            weeks.append(current_week)
            current_week = []
    if current_week:
        current_week.extend([None] * (7 - len(current_week)))
        weeks.append(current_week)

    # Prev/next month navigation
    if cal_month == 1:
        prev_month, prev_year = 12, cal_year - 1
    else:
        prev_month, prev_year = cal_month - 1, cal_year
    if cal_month == 12:
        next_month, next_year = 1, cal_year + 1
    else:
        next_month, next_year = cal_month + 1, cal_year

    month_name = cal_mod.month_name[cal_month]

    return templates.TemplateResponse("market/calendar.html", {
        "request": request,
        "active_tab": "calendar",
        "available": True,
        "today": today,
        "recent_launches": recent_launches,
        "upcoming": upcoming_classified,
        "weeks": weeks,
        "month_name": month_name,
        "cal_month": cal_month,
        "cal_year": cal_year,
        "prev_month": prev_month,
        "prev_year": prev_year,
        "next_month": next_month,
        "next_year": next_year,
    })


@router.get("/compare")
def compare_view(
    request: Request,
    tickers: str = Query(default=""),
):
    """Fund Comparison - side-by-side comparison of up to 4 tickers."""
    import pandas as pd
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
                            "inception_date_fmt": pd.Timestamp(r.get("inception_date")).strftime("%b %d, %Y") if r.get("inception_date") is not None and not (isinstance(r.get("inception_date"), float) and pd.isna(r.get("inception_date"))) else "",
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
