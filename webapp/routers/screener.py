"""Screener router - Bloomberg Analysis pages + 301 redirects to /filings/."""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import ScreenerResult, ScreenerUpload
from webapp.services.screener_helpers import (
    get_3x_data,
    data_available,
    cache_warming,
    _ON_RENDER,
)

router = APIRouter(prefix="/screener", tags=["screener"])
templates = Jinja2Templates(directory="webapp/templates")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 301 redirects — old screener URLs → new /filings/ URLs
# ---------------------------------------------------------------------------

@router.get("/")
def screener_landing_redirect(request: Request):
    qs = str(request.url.query)
    url = "/filings/landscape" + ("?" + qs if qs else "")
    return RedirectResponse(url=url, status_code=301)


@router.get("/3x-analysis")
def screener_3x_redirect():
    return RedirectResponse(url="/filings/candidates", status_code=301)


@router.get("/4x")
def screener_4x_redirect():
    return RedirectResponse(url="/filings/candidates", status_code=301)


@router.get("/evaluate")
def screener_evaluate_redirect():
    return RedirectResponse(url="/filings/evaluator", status_code=301)


@router.post("/evaluate")
def screener_evaluate_post_redirect():
    return RedirectResponse(url="/filings/evaluator", status_code=307)


@router.get("/report")
def screener_report_redirect():
    return RedirectResponse(url="/filings/report", status_code=301)


# ---------------------------------------------------------------------------
# Market Landscape (stays in screener)
# ---------------------------------------------------------------------------

@router.get("/market")
def screener_market_landscape(request: Request):
    """Market Landscape - underlier popularity + top 2x ETFs."""
    analysis = get_3x_data()

    if analysis is None:
        return templates.TemplateResponse("screener_market.html", {
            "request": request,
            "tab": "market",
            "data_available": data_available(),
            "cache_warming": cache_warming(),
        })

    return templates.TemplateResponse("screener_market.html", {
        "request": request,
        "tab": "market",
        "data_available": True,
        "snapshot": analysis["snapshot"],
        "underlier_pop": analysis.get("underlier_pop", []),
        "top_2x": analysis.get("top_2x", []),
        "data_date": analysis.get("data_date"),
    })


# ---------------------------------------------------------------------------
# REX Track Record (enhanced)
# ---------------------------------------------------------------------------

@router.get("/rex-funds")
def screener_rex_funds(
    request: Request,
    db: Session = Depends(get_db),
):
    """REX fund portfolio health + T-REX track record."""
    latest_upload = db.execute(
        select(ScreenerUpload)
        .where(ScreenerUpload.status == "completed")
        .order_by(ScreenerUpload.uploaded_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    product_groups = {"T-REX": [], "Microsectors": [], "Other REX": []}
    kpis = {"total_aum": 0, "net_flow_1m": 0, "best": None, "worst": None}

    if not _ON_RENDER:
        try:
            from screener.data_loader import load_etp_data
            etp_df = load_etp_data()

            rex_all = etp_df[etp_df.get("is_rex") == True].copy()

            if not rex_all.empty:
                import pandas as pd
                rex_all["_aum"] = pd.to_numeric(rex_all.get("t_w4.aum", 0), errors="coerce").fillna(0)
                rex_all["_flow_1m"] = pd.to_numeric(rex_all.get("t_w4.fund_flow_1month", 0), errors="coerce").fillna(0)

                kpis["total_aum"] = round(rex_all["_aum"].sum(), 1)
                kpis["net_flow_1m"] = round(rex_all["_flow_1m"].sum(), 1)

                best_idx = rex_all["_flow_1m"].idxmax()
                worst_idx = rex_all["_flow_1m"].idxmin()
                kpis["best"] = rex_all.loc[best_idx, "ticker"] if pd.notna(best_idx) else None
                kpis["best_flow"] = round(rex_all.loc[best_idx, "_flow_1m"], 1) if pd.notna(best_idx) else 0
                kpis["worst"] = rex_all.loc[worst_idx, "ticker"] if pd.notna(worst_idx) else None
                kpis["worst_flow"] = round(rex_all.loc[worst_idx, "_flow_1m"], 1) if pd.notna(worst_idx) else 0

                seen_tickers = set()
                for _, row in rex_all.iterrows():
                    ticker = row.get("ticker", "")
                    if ticker in seen_tickers:
                        continue
                    seen_tickers.add(ticker)

                    fund_name = str(row.get("fund_name", "")).upper()
                    if fund_name.startswith("T-REX"):
                        group_key = "T-REX"
                    elif fund_name.startswith("MICROSECTORS"):
                        group_key = "Microsectors"
                    else:
                        group_key = "Other REX"

                    product_groups[group_key].append({
                        "ticker": ticker,
                        "fund_name": row.get("fund_name", ""),
                        "underlier": row.get("q_category_attributes.map_li_underlier", ""),
                        "direction": row.get("q_category_attributes.map_li_direction", ""),
                        "leverage": row.get("q_category_attributes.map_li_leverage_amount", ""),
                        "aum": round(float(row.get("_aum", 0)), 1),
                        "flow_1m": round(float(row.get("_flow_1m", 0)), 1),
                        "flow_3m": round(float(pd.to_numeric(row.get("t_w4.fund_flow_3month", 0), errors="coerce") or 0), 1),
                        "flow_ytd": round(float(pd.to_numeric(row.get("t_w4.fund_flow_ytd", 0), errors="coerce") or 0), 1),
                        "return_ytd": round(float(pd.to_numeric(row.get("t_w3.total_return_ytd", 0), errors="coerce") or 0), 2),
                        "spread": row.get("t_w2.average_bidask_spread"),
                        "tracking_error": row.get("t_w2.nav_tracking_error"),
                    })

                for key in product_groups:
                    product_groups[key].sort(key=lambda x: x["aum"], reverse=True)

        except Exception as e:
            log.warning("Error loading REX fund data: %s", e)

    # Get track record from 3x analysis cache
    rex_track = []
    analysis = get_3x_data()
    if analysis:
        rex_track = analysis.get("rex_track", [])

    return templates.TemplateResponse("screener_rex.html", {
        "request": request,
        "product_groups": product_groups,
        "kpis": kpis,
        "rex_track": rex_track,
        "upload": latest_upload,
        "tab": "rex",
    })


# ---------------------------------------------------------------------------
# Risk Watchlist
# ---------------------------------------------------------------------------

@router.get("/risk")
def screener_risk_watchlist(request: Request):
    """Risk Watchlist - scoped volatility risk table."""
    analysis = get_3x_data()

    if analysis is None:
        return templates.TemplateResponse("screener_risk.html", {
            "request": request,
            "tab": "risk",
            "data_available": data_available(),
            "cache_warming": cache_warming(),
        })

    risk_watchlist = analysis.get("risk_watchlist", [])
    high_plus_count = sum(1 for r in risk_watchlist if r.get("risk_level") in ("HIGH", "EXTREME"))
    extreme_count = sum(1 for r in risk_watchlist if r.get("risk_level") == "EXTREME")

    return templates.TemplateResponse("screener_risk.html", {
        "request": request,
        "tab": "risk",
        "data_available": True,
        "risk_watchlist": risk_watchlist,
        "high_plus_count": high_plus_count,
        "extreme_count": extreme_count,
        "data_date": analysis.get("data_date"),
    })


# ---------------------------------------------------------------------------
# Stock Detail
# ---------------------------------------------------------------------------

@router.get("/stock/{ticker}")
def screener_stock_detail(
    request: Request,
    ticker: str,
    db: Session = Depends(get_db),
):
    """Per-stock competitive deep dive."""
    latest_upload = db.execute(
        select(ScreenerUpload)
        .where(ScreenerUpload.status == "completed")
        .order_by(ScreenerUpload.uploaded_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    result = None
    if latest_upload:
        result = db.execute(
            select(ScreenerResult)
            .where(ScreenerResult.upload_id == latest_upload.id)
            .where(ScreenerResult.ticker == ticker)
        ).scalar_one_or_none()

    products = []
    aum_series_data = []
    market_share_data = []

    if not _ON_RENDER:
        try:
            from screener.data_loader import load_etp_data
            from screener.competitive import get_products_for_underlier, compute_aum_trajectories

            etp_df = load_etp_data()
            ticker_clean = ticker.replace(" US", "")
            underlier_bb = f"{ticker_clean} US"

            prods = get_products_for_underlier(etp_df, underlier_bb)
            if not prods.empty:
                for _, p in prods.iterrows():
                    aum_val = p.get("t_w4.aum", 0)
                    if not isinstance(aum_val, (int, float)):
                        try:
                            aum_val = float(aum_val) if aum_val else 0
                        except (ValueError, TypeError):
                            aum_val = 0

                    products.append({
                        "ticker": p.get("ticker", ""),
                        "fund_name": p.get("fund_name", ""),
                        "issuer": p.get("issuer_display", p.get("issuer", "")),
                        "leverage": p.get("q_category_attributes.map_li_leverage_amount", ""),
                        "direction": p.get("q_category_attributes.map_li_direction", ""),
                        "aum": round(float(aum_val), 1),
                        "expense_ratio": p.get("t_w2.expense_ratio"),
                        "flow_1m": p.get("t_w4.fund_flow_1month"),
                        "flow_3m": p.get("t_w4.fund_flow_3month"),
                        "flow_ytd": p.get("t_w4.fund_flow_ytd"),
                        "spread": p.get("t_w2.average_bidask_spread"),
                        "tracking_error": p.get("t_w2.nav_tracking_error"),
                        "is_rex": p.get("is_rex", False),
                    })

                trajectories = compute_aum_trajectories(etp_df)
                underlier_trajs = trajectories[trajectories["underlier"] == underlier_bb]
                for _, t in underlier_trajs.iterrows():
                    if t["aum_series"]:
                        aum_series_data.append({
                            "label": t["ticker"],
                            "data": t["aum_series"],
                        })

                for prod in products:
                    if prod["aum"] > 0:
                        market_share_data.append({
                            "label": prod["ticker"],
                            "value": prod["aum"],
                            "is_rex": prod.get("is_rex", False),
                        })

        except Exception as e:
            log.warning("Error loading competitive data for %s: %s", ticker, e)

    return templates.TemplateResponse("screener_stock.html", {
        "request": request,
        "ticker": ticker,
        "ticker_clean": ticker.replace(" US", ""),
        "result": result,
        "products": products,
        "aum_series_json": json.dumps(aum_series_data),
        "market_share_json": json.dumps(market_share_data),
        "upload": latest_upload,
        "tab": "competitive",
    })
