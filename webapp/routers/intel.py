"""
Intel router - 13F Intelligence Hub main pages and REX report views.

Page routes:
  /intel/                 - Hub home (market overview)
  /intel/rex              - REX Report (rex-specific KPIs + new filers)
  /intel/rex/filers       - REX Filers (by ticker, vertical, issuer)
  /intel/rex/performance  - REX Performance (QoQ, trends)
  /intel/rex/sales        - REX Sales (state-level, QoQ)

API routes:
  /intel/api/kpis         - Hub KPIs (JSON)
  /intel/api/trend        - Trend data (JSON)
  /intel/api/holdings     - Holdings by product (JSON)
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from webapp.dependencies import get_holdings_db as get_db, require_admin
from webapp.services.holdings_intel import (
    fmt_value,
    get_available_quarters,
    get_country_data,
    get_distinct_verticals,
    get_holdings_by_issuer,
    get_holdings_by_product,
    get_holdings_by_vertical,
    get_hub_kpis,
    get_latest_quarter,
    get_new_filers,
    get_qoq_changes,
    get_top_filers,
    get_trend_data,
    get_us_state_data,
)

log = logging.getLogger(__name__)

router = APIRouter(
    prefix="/intel",
    tags=["intel"],
    dependencies=[Depends(require_admin)],
)
templates = Jinja2Templates(directory="webapp/templates")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _resolve_quarter(db: Session, quarter_param: str | None) -> tuple[str, list[str], str, bool]:
    """Resolve quarter param into (quarter, quarters_list, latest_quarter, is_latest).

    Returns defaults when data is missing.
    """
    quarters = get_available_quarters(db)
    latest_quarter = get_latest_quarter(db)

    if not latest_quarter:
        return ("", [], "", True)

    quarter = quarter_param if quarter_param and quarter_param in quarters else latest_quarter
    is_latest = (quarter == latest_quarter)

    return (quarter, quarters, latest_quarter, is_latest)


# =========================================================================
# PAGE ROUTES
# =========================================================================

@router.get("/")
def intel_home(
    request: Request,
    quarter: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Hub home page - market overview."""
    q, quarters, latest_quarter, is_latest = _resolve_quarter(db, quarter)

    if not q:
        return templates.TemplateResponse("intel/home.html", {
            "request": request,
            "quarter": "",
            "quarters": [],
            "kpis": {},
            "products": [],
            "vertical_breakdown": [],
            "issuer_breakdown": [],
            "international": [],
            "is_latest": True,
            "latest_quarter": "",
            "fmt_value": fmt_value,
        })

    kpis = get_hub_kpis(db, q)
    products = get_holdings_by_product(db, q)[:15]
    vertical_breakdown = get_holdings_by_vertical(db, q)
    issuer_breakdown = get_holdings_by_issuer(db, q)
    international = get_country_data(db, q)[:6]

    return templates.TemplateResponse("intel/home.html", {
        "request": request,
        "quarter": q,
        "quarters": quarters,
        "kpis": kpis,
        "products": products,
        "vertical_breakdown": vertical_breakdown,
        "issuer_breakdown": issuer_breakdown,
        "international": international,
        "is_latest": is_latest,
        "latest_quarter": latest_quarter,
        "fmt_value": fmt_value,
    })


@router.get("/rex")
def rex_report(
    request: Request,
    quarter: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """REX Report - rex-specific KPIs and new filers."""
    q, quarters, latest_quarter, is_latest = _resolve_quarter(db, quarter)

    if not q:
        return templates.TemplateResponse("intel/rex_report.html", {
            "request": request,
            "quarter": "",
            "quarters": [],
            "kpis": {},
            "new_filers": [],
            "vertical_breakdown": [],
            "is_latest": True,
            "latest_quarter": "",
            "fmt_value": fmt_value,
        })

    kpis = get_hub_kpis(db, q)
    new_filers = get_new_filers(db, q, rex_only=True)[:10]
    vertical_breakdown = get_holdings_by_vertical(db, q, rex_only=True)

    return templates.TemplateResponse("intel/rex_report.html", {
        "request": request,
        "quarter": q,
        "quarters": quarters,
        "kpis": kpis,
        "new_filers": new_filers,
        "vertical_breakdown": vertical_breakdown,
        "is_latest": is_latest,
        "latest_quarter": latest_quarter,
        "fmt_value": fmt_value,
    })


@router.get("/rex/filers")
def rex_filers(
    request: Request,
    quarter: str | None = Query(default=None),
    tab: str = Query(default="ticker"),
    vertical: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """REX Filers - breakdown by ticker, vertical, issuer."""
    q, quarters, latest_quarter, is_latest = _resolve_quarter(db, quarter)

    if not q:
        return templates.TemplateResponse("intel/rex_filers.html", {
            "request": request,
            "quarter": "",
            "quarters": [],
            "tab": tab,
            "vertical": vertical,
            "products": [],
            "verticals": [],
            "issuers": [],
            "top_filers": [],
            "new_filers": [],
            "is_latest": True,
            "latest_quarter": "",
            "fmt_value": fmt_value,
        })

    verticals = get_distinct_verticals(db, q)
    products = get_holdings_by_product(db, q, rex_only=True, vertical=vertical)
    issuers = get_holdings_by_issuer(db, q, rex_only=True)
    top_filers = get_top_filers(db, q, rex_only=True)
    new_filers = get_new_filers(db, q, rex_only=True)

    return templates.TemplateResponse("intel/rex_filers.html", {
        "request": request,
        "quarter": q,
        "quarters": quarters,
        "tab": tab,
        "vertical": vertical,
        "products": products,
        "verticals": verticals,
        "issuers": issuers,
        "top_filers": top_filers,
        "new_filers": new_filers,
        "is_latest": is_latest,
        "latest_quarter": latest_quarter,
        "fmt_value": fmt_value,
    })


@router.get("/rex/performance")
def rex_performance(
    request: Request,
    quarter: str | None = Query(default=None),
    tab: str = Query(default="aum_trend"),
    db: Session = Depends(get_db),
):
    """REX Performance - QoQ changes, trends."""
    q, quarters, latest_quarter, is_latest = _resolve_quarter(db, quarter)

    if not q:
        return templates.TemplateResponse("intel/rex_performance.html", {
            "request": request,
            "quarter": "",
            "quarters": [],
            "tab": tab,
            "products": [],
            "vertical_breakdown": [],
            "trend_data": [],
            "qoq_data": [],
            "kpis": {},
            "is_latest": True,
            "latest_quarter": "",
            "fmt_value": fmt_value,
        })

    products = get_holdings_by_product(db, q, rex_only=True)
    vertical_breakdown = get_holdings_by_vertical(db, q, rex_only=True)
    trend_data = get_trend_data(db, rex_only=True)
    qoq_data = get_qoq_changes(db, q, rex_only=True)
    kpis = get_hub_kpis(db, q)
    # Add computed market share
    if kpis.get("total_aum") and kpis["total_aum"] > 0:
        kpis["market_share_pct"] = f"{kpis['rex_aum'] / kpis['total_aum'] * 100:.4f}%"
    else:
        kpis["market_share_pct"] = "--"

    return templates.TemplateResponse("intel/rex_performance.html", {
        "request": request,
        "quarter": q,
        "quarters": quarters,
        "tab": tab,
        "products": products,
        "vertical_breakdown": vertical_breakdown,
        "trend_data": trend_data,
        "qoq_data": qoq_data,
        "kpis": kpis,
        "is_latest": is_latest,
        "latest_quarter": latest_quarter,
        "fmt_value": fmt_value,
    })


@router.get("/rex/sales")
def rex_sales(
    request: Request,
    quarter: str | None = Query(default=None),
    tab: str = Query(default="momentum"),
    db: Session = Depends(get_db),
):
    """REX Sales - state-level analysis, QoQ."""
    q, quarters, latest_quarter, is_latest = _resolve_quarter(db, quarter)
    # Default to concentration tab when <2 quarters (momentum needs QoQ)
    has_qoq = len(quarters) >= 2
    if tab == "momentum" and not has_qoq:
        tab = "concentration"

    if not q:
        return templates.TemplateResponse("intel/rex_sales.html", {
            "request": request,
            "quarter": "",
            "quarters": [],
            "tab": tab,
            "qoq_data": [],
            "state_data": [],
            "kpis": {},
            "is_latest": True,
            "latest_quarter": "",
            "fmt_value": fmt_value,
        })

    qoq_data = get_qoq_changes(db, q, rex_only=True)
    state_data = get_us_state_data(db, q, rex_only=True)
    kpis = get_hub_kpis(db, q)

    # Sales-specific KPIs from QoQ data
    aum_up = len([d for d in qoq_data if (d.get("qoq_dollar") or 0) > 0])
    aum_down = len([d for d in qoq_data if (d.get("qoq_dollar") or 0) < 0])
    aum_flat = len([d for d in qoq_data if (d.get("qoq_dollar") or 0) == 0 or d.get("qoq_dollar") is None])
    kpis["aum_up"] = aum_up
    kpis["aum_down"] = aum_down
    kpis["aum_flat"] = aum_flat

    # State-level KPIs
    kpis["states_with_holders"] = len(state_data)
    kpis["us_filers"] = sum(s.get("filers", 0) for s in state_data)
    us_rex_aum = sum(s.get("rex_aum", 0) for s in state_data)
    kpis["us_rex_aum_fmt"] = fmt_value(us_rex_aum)

    return templates.TemplateResponse("intel/rex_sales.html", {
        "request": request,
        "quarter": q,
        "quarters": quarters,
        "tab": tab,
        "qoq_data": qoq_data,
        "state_data": state_data,
        "kpis": kpis,
        "is_latest": is_latest,
        "latest_quarter": latest_quarter,
        "fmt_value": fmt_value,
    })


# =========================================================================
# API ROUTES
# =========================================================================

@router.get("/api/kpis")
def api_kpis(
    quarter: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Hub KPIs as JSON."""
    q, _, _, _ = _resolve_quarter(db, quarter)
    if not q:
        return JSONResponse({"error": "No holdings data available"}, status_code=404)
    return get_hub_kpis(db, q)


@router.get("/api/trend")
def api_trend(
    rex_only: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    """Trend data as JSON."""
    return get_trend_data(db, rex_only=rex_only)


@router.get("/api/holdings")
def api_holdings(
    quarter: str | None = Query(default=None),
    rex_only: bool = Query(default=False),
    vertical: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Holdings by product as JSON."""
    q, _, _, _ = _resolve_quarter(db, quarter)
    if not q:
        return JSONResponse({"error": "No holdings data available"}, status_code=404)
    return get_holdings_by_product(db, q, rex_only=rex_only, vertical=vertical)
