"""
Advanced Market Intelligence routes.

Routes:
  GET /market/calendar   -> Compliance Calendar (upcoming extensions, recent effectivities)
  GET /market/compare    -> Fund Comparison (side-by-side ticker comparison)
"""
from __future__ import annotations

import calendar as cal_mod
import json
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

# Auto-inject base_template for fragment tab navigation (?fragment=1)
_orig_template_response = templates.TemplateResponse


def _fragment_template_response(name, context, *args, **kwargs):
    request = context.get("request")
    if request and hasattr(request, "query_params") and request.query_params.get("fragment") == "1":
        context.setdefault("base_template", "market/_fragment_base.html")
    else:
        context.setdefault("base_template", "market/base.html")
    return _orig_template_response(name, context, *args, **kwargs)


templates.TemplateResponse = _fragment_template_response


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
            "fund_name": extraction.series_name or "",
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
            "fund_name": extraction.series_name or "",
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
            fund_name = item.get("fund_name", "")
            display_name = fund_name or trust_name
            events_by_date[eff.day].append({
                "type": "launch",
                "label": display_name[:20],
                "trust": trust_name,
                "fund_name": fund_name,
                "form": item["filing"].form if item.get("filing") else "",
            })
    for item in upcoming_classified:
        eff = item.get("effective_date")
        if eff and eff.year == cal_year and eff.month == cal_month:
            trust_name = item["trust"].name if item["trust"] else ""
            fund_name = item.get("fund_name", "")
            display_name = fund_name or trust_name
            events_by_date[eff.day].append({
                "type": "upcoming",
                "label": display_name[:20],
                "trust": trust_name,
                "fund_name": fund_name,
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


@router.get("/api/ticker-search")
def ticker_search_api(
    q: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """Autocomplete ticker search for compare page. Returns top 15 matches."""
    if not q or len(q) < 1:
        return {"results": []}

    from sqlalchemy import text as sa_text
    q_upper = q.upper().replace(" US", "").strip()

    rows = db.execute(sa_text(
        "SELECT ticker, fund_name, issuer_display, aum, is_rex "
        "FROM mkt_master_data "
        "WHERE market_status = 'ACTV' AND (fund_type = 'ETF' OR fund_type = 'ETN') "
        "AND (UPPER(ticker) LIKE :q1 OR UPPER(fund_name) LIKE :q2) "
        "ORDER BY CAST(aum AS REAL) DESC LIMIT 15"
    ), {"q1": f"%{q_upper}%", "q2": f"%{q_upper}%"}).fetchall()

    results = []
    for r in rows:
        ticker_clean = str(r[0]).replace(" US", "").strip()
        results.append({
            "ticker": ticker_clean,
            "name": str(r[1] or "")[:60],
            "issuer": str(r[2] or ""),
            "aum": round(float(r[3] or 0), 1),
            "is_rex": bool(r[4]),
        })
    return {"results": results}


@router.get("/compare")
def compare_view(
    request: Request,
    tickers: str = Query(default=""),
    db: Session = Depends(get_db),
):
    """Fund Comparison - side-by-side comparison of up to 10 tickers."""
    import pandas as pd
    from webapp.services.market_data import get_master_data, data_available, _fmt_currency, _fmt_flow

    available = data_available(db)
    # Strip " US" from user-submitted tickers
    ticker_list = [t.upper().replace(" US", "").strip() for t in tickers.split(",") if t.strip()][:10]

    fund_data = []
    totalrealreturns_url = ""
    if available and ticker_list:
        try:
            master = get_master_data(db)
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

    # Fetch total return data from TotalRealReturns
    total_returns = None
    if ticker_list:
        try:
            from scripts.scrape_total_returns import scrape as scrape_returns
            tr_result = scrape_returns(ticker_list)
            if "error" not in tr_result and tr_result.get("dates"):
                total_returns = {
                    "dates_json": json.dumps(tr_result["dates"]),
                    "series": {},
                    "stats": tr_result.get("stats", {}),
                    "date_range": tr_result.get("date_range", []),
                }
                for sym in ticker_list:
                    if sym in tr_result.get("growth_series", {}):
                        total_returns["series"][sym] = tr_result["growth_series"][sym]
                total_returns["series_json"] = json.dumps(total_returns["series"])
        except Exception:
            log.exception("Error fetching total returns")

    return templates.TemplateResponse("market/compare.html", {
        "request": request,
        "active_tab": "compare",
        "available": available,
        "tickers": tickers,
        "ticker_list": ticker_list,
        "fund_data": fund_data,
        "totalrealreturns_url": totalrealreturns_url,
        "total_returns": total_returns,
    })


@router.get("/fund/{ticker}")
def market_fund_detail(
    ticker: str,
    request: Request,
    db: Session = Depends(get_db),
):
    """Standalone market fund page — Bloomberg DES-style overview."""
    import pandas as pd
    from webapp.services.market_data import get_master_data, data_available, _fmt_currency, _fmt_flow, get_data_as_of

    available = data_available(db)
    ticker = ticker.upper().replace(" US", "").strip()
    fund = None
    aum_history = []
    flows = {}
    flows_fmt = {}
    competitors = []
    total_returns = None
    series_id = None

    if available:
        try:
            master = get_master_data(db)
            match_col = "ticker_clean" if "ticker_clean" in master.columns else "ticker"

            row = master[master[match_col].str.upper() == ticker]
            if not row.empty:
                r = row.iloc[0]
                d = r.to_dict()

                # AUM history (12 months + current)
                aum_col = "t_w4.aum"
                for i in range(12, 0, -1):
                    col = f"t_w4.aum_{i}"
                    aum_history.append(float(r.get(col, 0) or 0) if col in master.columns else 0)
                aum_history.append(float(r.get(aum_col, 0) or 0))

                # Flows
                flows = {
                    "1d": float(r.get("t_w4.fund_flow_1day", 0) or 0),
                    "1w": float(r.get("t_w4.fund_flow_1week", 0) or 0),
                    "1m": float(r.get("t_w4.fund_flow_1month", 0) or 0),
                    "3m": float(r.get("t_w4.fund_flow_3month", 0) or 0),
                    "6m": float(r.get("t_w4.fund_flow_6month", 0) or 0),
                    "ytd": float(r.get("t_w4.fund_flow_ytd", 0) or 0),
                    "1y": float(r.get("t_w4.fund_flow_1year", 0) or 0),
                }
                flows_fmt = {k: _fmt_flow(v) for k, v in flows.items()}

                # Returns
                returns = {}
                for period, col in [
                    ("1D", "t_w3.total_return_1day"), ("1W", "t_w3.total_return_1week"),
                    ("1M", "t_w3.total_return_1month"), ("3M", "t_w3.total_return_3month"),
                    ("6M", "t_w3.total_return_6month"), ("YTD", "t_w3.total_return_ytd"),
                    ("1Y", "t_w3.total_return_1year"), ("3Y", "t_w3.total_return_3year"),
                ]:
                    val = r.get(col)
                    returns[period] = float(val) if val is not None and val == val else None

                # Metrics
                def _safe(key):
                    v = r.get(key)
                    return float(v) if v is not None and v == v else None

                inception = None
                inc_raw = r.get("inception_date")
                if inc_raw is not None and not (isinstance(inc_raw, float) and pd.isna(inc_raw)):
                    try:
                        inception = pd.Timestamp(inc_raw).strftime("%b %d, %Y")
                    except Exception:
                        inception = str(inc_raw)

                fund = {
                    "ticker": ticker,
                    "name": d.get("fund_name", ticker),
                    "issuer": d.get("issuer_display", d.get("issuer", "")),
                    "category": d.get("category_display", ""),
                    "fund_type": d.get("fund_type", d.get("t_w1.fund_type", "")),
                    "inception": inception,
                    "is_rex": bool(d.get("is_rex")),
                    "aum": _safe("t_w4.aum"),
                    "aum_fmt": _fmt_currency(float(r.get("t_w4.aum", 0) or 0)),
                    "expense_ratio": _safe("t_w2.expense_ratio"),
                    "spread": _safe("t_w2.average_bidask_spread"),
                    "volume_30d": _safe("t_w2.average_vol_30day"),
                    "short_interest": _safe("t_w2.percent_short_interest"),
                    "premium_discount": _safe("t_w2.percentage_premium"),
                    "nav_tracking_error": _safe("t_w2.nav_tracking_error"),
                    "open_interest": _safe("t_w2.open_interest"),
                    "annualized_yield": _safe("t_w3.annualized_yield"),
                    "returns": returns,
                    "leverage": d.get("map_li_leverage_amount", ""),
                    "direction": d.get("map_li_direction", ""),
                    "underlier": d.get("map_li_underlier", ""),
                    # New 3-axis taxonomy (additive — legacy category fields preserved above)
                    "primary_strategy": d.get("primary_strategy") or "",
                    "asset_class": d.get("asset_class") or "",
                    "sub_strategy": d.get("sub_strategy") or "",
                    "row": d,
                }

                # Competitors: same category or same underlier
                cat = d.get("category_display", "")
                underlier = d.get("map_li_underlier", "")
                if cat and match_col:
                    comp_filter = master[match_col].str.upper() != ticker
                    comp_filter &= master["market_status"] == "ACTV"
                    if underlier:
                        comp_filter &= (master.get("map_li_underlier", pd.Series()) == underlier)
                    elif cat:
                        comp_filter &= (master.get("category_display", pd.Series()) == cat)
                    comp_rows = master[comp_filter].sort_values("t_w4.aum", ascending=False).head(10)
                    for _, cr in comp_rows.iterrows():
                        competitors.append({
                            "ticker": cr.get(match_col, ""),
                            "name": cr.get("fund_name", ""),
                            "issuer": cr.get("issuer_display", ""),
                            "aum": float(cr.get("t_w4.aum", 0) or 0),
                            "aum_fmt": _fmt_currency(float(cr.get("t_w4.aum", 0) or 0)),
                            "expense_ratio": float(cr.get("t_w2.expense_ratio", 0) or 0),
                            "flow_1m": float(cr.get("t_w4.fund_flow_1month", 0) or 0),
                            "return_ytd": float(cr.get("t_w3.total_return_ytd", 0) or 0),
                            "is_rex": bool(cr.get("is_rex")),
                        })

            # Cross-link: find SEC filing series_id by ticker
            from webapp.models import FundStatus
            fund_record = db.execute(
                select(FundStatus.series_id).where(
                    FundStatus.ticker.ilike(ticker)
                )
            ).scalar_one_or_none()
            if fund_record:
                series_id = fund_record

        except Exception:
            log.exception("Error loading market fund detail for %s", ticker)

    # Total return data
    if fund:
        try:
            from scripts.scrape_total_returns import scrape as scrape_returns
            tr_result = scrape_returns([ticker])
            if "error" not in tr_result and tr_result.get("dates"):
                total_returns = {
                    "dates_json": json.dumps(tr_result["dates"]),
                    "series_json": json.dumps({ticker: tr_result["growth_series"].get(ticker, [])}),
                    "stats": tr_result.get("stats", {}).get(ticker, {}),
                }
        except Exception:
            log.exception("Error fetching total returns for %s", ticker)

    return templates.TemplateResponse("market/fund.html", {
        "request": request,
        "active_tab": "fund",
        "available": available,
        "data_as_of": get_data_as_of(db) if available else None,
        "ticker": ticker,
        "fund": fund,
        "aum_history": aum_history,
        "flows": flows,
        "flows_fmt": flows_fmt,
        "competitors": competitors,
        "total_returns": total_returns,
        "series_id": series_id,
    })
