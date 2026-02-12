"""Screener router - ETF Launch Screener pages."""
from __future__ import annotations

import io
import json
import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import ScreenerResult, ScreenerUpload

router = APIRouter(prefix="/screener", tags=["screener"])
templates = Jinja2Templates(directory="webapp/templates")
log = logging.getLogger(__name__)

PAGE_SIZE = 50


@router.get("/")
def screener_rankings(
    request: Request,
    q: str = "",
    sector: str = "",
    qualified: str = "",
    page: int = Query(1, ge=1),
    db: Session = Depends(get_db),
):
    """Main screener rankings page."""
    # Get latest upload
    latest_upload = db.execute(
        select(ScreenerUpload)
        .where(ScreenerUpload.status == "completed")
        .order_by(ScreenerUpload.uploaded_at.desc())
        .limit(1)
    ).scalar_one_or_none()

    if not latest_upload:
        return templates.TemplateResponse("screener_rankings.html", {
            "request": request,
            "results": [],
            "total": 0,
            "page": 1,
            "pages": 1,
            "q": q,
            "sector": sector,
            "qualified": qualified,
            "sectors": [],
            "upload": None,
            "tab": "rankings",
        })

    # Build query
    query = select(ScreenerResult).where(ScreenerResult.upload_id == latest_upload.id)

    if q:
        query = query.where(ScreenerResult.ticker.ilike(f"%{q}%"))
    if sector:
        query = query.where(ScreenerResult.sector == sector)
    if qualified == "1":
        query = query.where(ScreenerResult.passes_filters == True)

    # Count total
    count_q = select(func.count()).select_from(query.subquery())
    total = db.execute(count_q).scalar() or 0
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    # Fetch page
    results = db.execute(
        query.order_by(ScreenerResult.composite_score.desc())
        .offset((page - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    ).scalars().all()

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
        "results": results,
        "total": total,
        "page": page,
        "pages": pages,
        "q": q,
        "sector": sector,
        "qualified": qualified,
        "sectors": sectors,
        "upload": latest_upload,
        "tab": "rankings",
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
            "predicted_aum": r.predicted_aum,
            "mkt_cap": r.mkt_cap,
            "call_oi_pctl": r.call_oi_pctl,
            "passes_filters": r.passes_filters,
            "filing_status": r.filing_status,
            "competitive_density": r.competitive_density,
            "competitor_count": r.competitor_count,
            "total_competitor_aum": r.total_competitor_aum,
        }
        for r in results
    ]

    # Get REX fund data
    rex_funds = []
    try:
        from screener.data_loader import load_etp_data
        import pandas as pd
        etp_df = load_etp_data()
        rex_lev = etp_df[
            (etp_df.get("is_rex") == True) & (etp_df.get("uses_leverage") == True)
        ]
        underlier_col = "q_category_attributes.map_li_underlier"
        for _, row in rex_lev.iterrows():
            rex_funds.append({
                "ticker": row.get("ticker", ""),
                "underlier": str(row.get(underlier_col, ""))[:12],
                "aum": float(pd.to_numeric(row.get("t_w4.aum", 0), errors="coerce") or 0),
                "flow_1m": float(pd.to_numeric(row.get("t_w4.fund_flow_1month", 0), errors="coerce") or 0),
                "flow_3m": float(pd.to_numeric(row.get("t_w4.fund_flow_3month", 0), errors="coerce") or 0),
                "flow_ytd": float(pd.to_numeric(row.get("t_w4.fund_flow_ytd", 0), errors="coerce") or 0),
                "return_ytd": float(pd.to_numeric(row.get("t_w3.total_return_ytd", 0), errors="coerce") or 0),
            })
    except Exception as e:
        log.warning("Could not load REX fund data for report: %s", e)

    model_info = {
        "model_type": latest_upload.model_type,
        "r_squared": latest_upload.model_r_squared,
    }

    from screener.report_generator import generate_executive_report
    pdf_bytes = generate_executive_report(
        results=result_dicts,
        rex_funds=rex_funds if rex_funds else None,
        model_info=model_info,
        data_date=latest_upload.uploaded_at.strftime("%B %d, %Y"),
    )

    filename = f"ETF_Launch_Screener_{latest_upload.uploaded_at.strftime('%Y%m%d')}.pdf"
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
