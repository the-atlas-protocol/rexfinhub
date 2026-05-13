"""
Intel insights router - geographic and trend analysis pages.

Page routes:
  /intel/country  - International holders by country
  /intel/asia     - Asian holders of REX products
  /intel/trends   - Historical trend analysis
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from webapp.dependencies import get_holdings_db as get_db, require_admin
from webapp.services.holdings_intel import (
    ASIA_CODES,
    COUNTRY_NAMES,
    fmt_value,
    get_asia_data,
    get_available_quarters,
    get_country_breakdown,
    get_country_data,
    get_latest_quarter,
    get_trend_data,
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
    """Resolve quarter param into (quarter, quarters_list, latest_quarter, is_latest)."""
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

@router.get("/country")
def country_page(
    request: Request,
    quarter: str | None = Query(default=None),
    vertical: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """International holders by country."""
    q, quarters, latest_quarter, is_latest = _resolve_quarter(db, quarter)

    if not q:
        return templates.TemplateResponse("intel/country.html", {
            "request": request,
            "quarter": "",
            "quarters": [],
            "vertical": vertical,
            "country_data": [],
            "country_breakdown": [],
            "is_latest": True,
            "latest_quarter": "",
            "fmt_value": fmt_value,
        })

    country_data = get_country_data(db, q)
    if vertical:
        country_data = [d for d in country_data if d.get("vertical") == vertical]

    country_breakdown = get_country_breakdown(country_data)

    return templates.TemplateResponse("intel/country.html", {
        "request": request,
        "quarter": q,
        "quarters": quarters,
        "vertical": vertical,
        "country_data": country_data,
        "country_breakdown": country_breakdown,
        "is_latest": is_latest,
        "latest_quarter": latest_quarter,
        "fmt_value": fmt_value,
    })


@router.get("/asia")
def asia_page(
    request: Request,
    quarter: str | None = Query(default=None),
    tab: str = Query(default="institution"),
    vertical: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Asian holders of REX products."""
    q, quarters, latest_quarter, is_latest = _resolve_quarter(db, quarter)

    if not q:
        return templates.TemplateResponse("intel/asia.html", {
            "request": request,
            "quarter": "",
            "quarters": [],
            "tab": tab,
            "vertical": vertical,
            "asia_data": [],
            "kpis": {},
            "is_latest": True,
            "latest_quarter": "",
            "fmt_value": fmt_value,
        })

    asia_data = get_asia_data(db, q)
    if vertical:
        asia_data = [d for d in asia_data if d.get("vertical") == vertical]

    # Asia KPIs
    asia_aum = sum(d.get("total_value", 0) for d in asia_data)
    asia_institutions = len(set(d["institution_id"] for d in asia_data))
    asia_countries = len(set(d.get("country_code") for d in asia_data if d.get("country_code")))
    asia_products = len(set(d.get("ticker") for d in asia_data if d.get("ticker")))

    kpis = {
        "asia_aum": asia_aum,
        "asia_aum_fmt": fmt_value(asia_aum),
        "asia_institutions": asia_institutions,
        "asia_countries": asia_countries,
        "asia_products": asia_products,
    }

    return templates.TemplateResponse("intel/asia.html", {
        "request": request,
        "quarter": q,
        "quarters": quarters,
        "tab": tab,
        "vertical": vertical,
        "asia_data": asia_data,
        "kpis": kpis,
        "is_latest": is_latest,
        "latest_quarter": latest_quarter,
        "fmt_value": fmt_value,
    })


@router.get("/trends")
def trends_page(
    request: Request,
    quarter: str | None = Query(default=None),
    tab: str = Query(default="aum_trend"),
    db: Session = Depends(get_db),
):
    """Historical trend analysis - all products and REX."""
    q, quarters, latest_quarter, is_latest = _resolve_quarter(db, quarter)

    if not q:
        return templates.TemplateResponse("intel/trends.html", {
            "request": request,
            "quarter": "",
            "quarters": [],
            "tab": tab,
            "trend_data": [],
            "trend_data_rex": [],
            "is_latest": True,
            "latest_quarter": "",
            "fmt_value": fmt_value,
        })

    trend_data = get_trend_data(db, rex_only=False)
    trend_data_rex = get_trend_data(db, rex_only=True)

    return templates.TemplateResponse("intel/trends.html", {
        "request": request,
        "quarter": q,
        "quarters": quarters,
        "tab": tab,
        "trend_data": trend_data,
        "trend_data_rex": trend_data_rex,
        "is_latest": is_latest,
        "latest_quarter": latest_quarter,
        "fmt_value": fmt_value,
    })
