"""Screener router - ETF Launch Screener pages."""
from __future__ import annotations

import io
import json
import logging
from typing import Optional

from fastapi import APIRouter, Body, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import ScreenerResult, ScreenerUpload

router = APIRouter(prefix="/screener", tags=["screener"])
templates = Jinja2Templates(directory="webapp/templates")
log = logging.getLogger(__name__)

# Tickers to exclude from rankings (known bad data)
EXCLUDE_TICKERS = {"WBHC US"}


def _get_launched_underliers() -> set[str]:
    """Get launched underliers to exclude from rankings. Graceful on failure."""
    try:
        from screener.data_loader import load_etp_data
        from screener.filing_match import get_launched_underliers
        etp_df = load_etp_data()
        return get_launched_underliers(etp_df)
    except Exception as e:
        log.warning("Could not load launched underliers: %s", e)
        return set()


@router.get("/")
def screener_opportunities(
    request: Request,
    q: str = "",
    sector: str = "",
    db: Session = Depends(get_db),
):
    """Main screener page - two tables: all opportunities + filed only."""
    # Get latest upload
    latest_upload = db.execute(
        select(ScreenerUpload)
        .where(ScreenerUpload.status == "completed")
        .order_by(ScreenerUpload.uploaded_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if not latest_upload:
        # Check if data exists on disk but hasn't been scored yet
        try:
            from screener.config import DATA_FILE
            data_available = DATA_FILE.exists()
        except Exception:
            data_available = False

        return templates.TemplateResponse("screener_rankings.html", {
            "request": request,
            "all_opportunities": [],
            "filed_only": [],
            "total_screened": 0,
            "launched_count": 0,
            "filed_count": 0,
            "q": q,
            "sector": sector,
            "sectors": [],
            "upload": None,
            "tab": "opportunities",
            "data_available": data_available,
        })

    # Fetch ALL results ordered by score
    all_results = db.execute(
        select(ScreenerResult)
        .where(ScreenerResult.upload_id == latest_upload.id)
        .order_by(ScreenerResult.composite_score.desc())
    ).scalars().all()

    total_screened = len(all_results)

    # Get launched underliers to exclude
    launched = _get_launched_underliers()
    launched_count = len(launched)

    # Filter out launched underliers + bad data
    clean_results = []
    for r in all_results:
        ticker_clean = r.ticker.replace(" US", "").upper()
        if r.ticker in EXCLUDE_TICKERS:
            continue
        if ticker_clean in launched:
            continue
        clean_results.append(r)

    # Apply search/sector filters
    if q:
        q_upper = q.strip().upper()
        clean_results = [r for r in clean_results if q_upper in r.ticker.upper()]
    if sector:
        clean_results = [r for r in clean_results if r.sector == sector]

    # Split: all opportunities (top 50) + filed only (top 50)
    all_opportunities = clean_results[:50]
    filed_only = [
        r for r in clean_results
        if r.filing_status and r.filing_status.startswith("REX Filed")
    ][:50]
    filed_count = sum(
        1 for r in clean_results
        if r.filing_status and r.filing_status.startswith("REX Filed")
    )

    # Get distinct sectors for filter dropdown
    sectors = db.execute(
        select(ScreenerResult.sector)
        .where(ScreenerResult.upload_id == latest_upload.id)
        .where(ScreenerResult.sector.isnot(None))
        .where(ScreenerResult.sector != "")
        .distinct()
        .order_by(ScreenerResult.sector)
    ).scalars().all()

    return templates.TemplateResponse("screener_rankings.html", {
        "request": request,
        "all_opportunities": all_opportunities,
        "filed_only": filed_only,
        "total_screened": total_screened,
        "launched_count": launched_count,
        "filed_count": filed_count,
        "q": q,
        "sector": sector,
        "sectors": sectors,
        "upload": latest_upload,
        "tab": "opportunities",
    })


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

    # Get this stock's screener result
    result = None
    if latest_upload:
        result = db.execute(
            select(ScreenerResult)
            .where(ScreenerResult.upload_id == latest_upload.id)
            .where(ScreenerResult.ticker == ticker)
        ).scalar_one_or_none()

    # Load competitive data from etp_data
    products = []
    aum_series_data = []
    market_share_data = []

    try:
        from screener.data_loader import load_etp_data
        from screener.competitive import get_products_for_underlier, compute_aum_trajectories

        etp_df = load_etp_data()
        # Try with " US" suffix (Bloomberg format)
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

            # AUM trajectory data for Chart.js
            trajectories = compute_aum_trajectories(etp_df)
            underlier_trajs = trajectories[trajectories["underlier"] == underlier_bb]
            for _, t in underlier_trajs.iterrows():
                if t["aum_series"]:
                    aum_series_data.append({
                        "label": t["ticker"],
                        "data": t["aum_series"],
                    })

            # Market share for pie chart
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


@router.get("/rex-funds")
def screener_rex_funds(
    request: Request,
    db: Session = Depends(get_db),
):
    """REX fund portfolio health check - grouped by T-REX, Microsectors, Other."""
    latest_upload = db.execute(
        select(ScreenerUpload)
        .where(ScreenerUpload.status == "completed")
        .order_by(ScreenerUpload.uploaded_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    product_groups = {"T-REX": [], "Microsectors": [], "Other REX": []}
    kpis = {"total_aum": 0, "net_flow_1m": 0, "best": None, "worst": None}

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

            # Deduplicate by ticker (some funds appear twice in etp_data)
            seen_tickers = set()
            for _, row in rex_all.iterrows():
                ticker = row.get("ticker", "")
                if ticker in seen_tickers:
                    continue
                seen_tickers.add(ticker)

                fund_name = str(row.get("fund_name", "")).upper()

                # Classify into product line
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

            # Sort each group by AUM descending
            for key in product_groups:
                product_groups[key].sort(key=lambda x: x["aum"], reverse=True)

    except Exception as e:
        log.warning("Error loading REX fund data: %s", e)

    return templates.TemplateResponse("screener_rex.html", {
        "request": request,
        "product_groups": product_groups,
        "kpis": kpis,
        "upload": latest_upload,
        "tab": "rex",
    })


@router.get("/report")
def screener_report_download(
    request: Request,
    db: Session = Depends(get_db),
):
    """Generate and download the executive PDF report."""
    latest_upload = db.execute(
        select(ScreenerUpload)
        .where(ScreenerUpload.status == "completed")
        .order_by(ScreenerUpload.uploaded_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if not latest_upload:
        from fastapi.responses import HTMLResponse
        return HTMLResponse("<h2>No screener data. Upload from Admin panel first.</h2>", status_code=404)

    # Get all results
    results = db.execute(
        select(ScreenerResult)
        .where(ScreenerResult.upload_id == latest_upload.id)
        .order_by(ScreenerResult.composite_score.desc())
    ).scalars().all()

    result_dicts = [
        {
            "ticker": r.ticker,
            "sector": r.sector,
            "composite_score": r.composite_score,
            "mkt_cap": r.mkt_cap,
            "total_oi_pctl": r.total_oi_pctl,
            "passes_filters": r.passes_filters,
            "filing_status": r.filing_status,
            "competitive_density": r.competitive_density,
            "competitor_count": r.competitor_count,
            "total_competitor_aum": r.total_competitor_aum,
        }
        for r in results
    ]

    from screener.report_generator import generate_rankings_report
    pdf_bytes = generate_rankings_report(
        results=result_dicts,
        data_date=latest_upload.uploaded_at.strftime("%B %d, %Y"),
    )

    filename = f"ETF_Launch_Screener_{latest_upload.uploaded_at.strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ==================== Candidate Evaluator ====================

@router.get("/evaluate")
def screener_evaluate_page(request: Request):
    """Interactive candidate evaluator page."""
    # Check if Bloomberg data is available
    data_available = False
    try:
        from screener.config import DATA_FILE
        data_available = DATA_FILE.exists()
    except Exception:
        pass

    return templates.TemplateResponse("screener_evaluate.html", {
        "request": request,
        "tab": "evaluate",
        "data_available": data_available,
    })


@router.post("/evaluate")
def screener_evaluate_api(
    request: Request,
    tickers: list[str] = Body(..., embed=True),
):
    """API endpoint: evaluate candidate tickers and return JSON results."""
    if not tickers:
        return JSONResponse({"error": "No tickers provided"}, status_code=400)

    # Cap at 20 tickers per request
    tickers = tickers[:20]

    try:
        from screener.candidate_evaluator import evaluate_candidates
        results = evaluate_candidates(tickers)
    except FileNotFoundError:
        return JSONResponse(
            {"error": "Bloomberg data file not found. Upload from Admin panel first."},
            status_code=404,
        )
    except Exception as e:
        log.error("Candidate evaluation failed: %s", e)
        return JSONResponse({"error": str(e)[:200]}, status_code=500)

    # Serialize results for JSON (convert numpy/pandas types)
    clean_results = []
    for r in results:
        clean_results.append(_serialize_eval(r))

    return JSONResponse({"results": clean_results})


def _serialize_eval(r: dict) -> dict:
    """Convert evaluation result to JSON-safe dict."""
    import math

    def _clean(v):
        if v is None:
            return None
        if isinstance(v, float):
            if math.isnan(v) or math.isinf(v):
                return None
            return round(v, 2)
        if isinstance(v, dict):
            return {k: _clean(vv) for k, vv in v.items()}
        if isinstance(v, list):
            return [_clean(vv) for vv in v]
        if hasattr(v, 'item'):  # numpy scalar
            return _clean(v.item())
        return v

    return _clean(r)
