"""
Intel competitors router - competitor analysis and product universe pages.

Page routes:
  /intel/competitors             - Competitor holdings overview
  /intel/competitors/new-filers  - New filers for competitor products
  /intel/products                - Full product universe browser
  /intel/head-to-head            - Product comparison by underlying
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from webapp.dependencies import get_holdings_db as get_db
from webapp.services.holdings_intel import (
    fmt_value,
    get_available_quarters,
    get_competitor_new_filers,
    get_distinct_underlyings,
    get_distinct_verticals,
    get_head_to_head,
    get_holdings_by_issuer,
    get_holdings_by_product,
    get_holdings_by_vertical,
    get_hub_kpis,
    get_latest_quarter,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/intel", tags=["intel"])
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

@router.get("/competitors")
def competitors_page(
    request: Request,
    quarter: str | None = Query(default=None),
    tab: str = Query(default="product"),
    vertical: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Competitor holdings overview."""
    q, quarters, latest_quarter, is_latest = _resolve_quarter(db, quarter)

    if not q:
        return templates.TemplateResponse("intel/competitors.html", {
            "request": request,
            "quarter": "",
            "quarters": [],
            "tab": tab,
            "vertical": vertical,
            "products": [],
            "verticals": [],
            "issuers": [],
            "kpis": {},
            "is_latest": True,
            "latest_quarter": "",
            "fmt_value": fmt_value,
        })

    verticals = get_distinct_verticals(db, q)

    # Non-REX products only
    products = get_holdings_by_product(db, q, rex_only=False, vertical=vertical)
    products = [p for p in products if not p.get("is_rex")]

    issuers = get_holdings_by_issuer(db, q, rex_only=False)
    # Filter out REX issuers from issuer list
    issuers = [i for i in issuers if i.get("issuer") not in (
        "REX Financial", "REX Financial: MicroSectors",
    )]

    # Competitor-specific KPIs
    comp_products = len(set(p["ticker"] for p in products))
    comp_aum = sum(p.get("total_aum", 0) for p in products)
    comp_issuers = len(set(p.get("issuer") for p in products if p.get("issuer")))

    kpis = {
        "comp_products": comp_products,
        "comp_aum": comp_aum,
        "comp_aum_fmt": fmt_value(comp_aum),
        "comp_issuers": comp_issuers,
    }

    return templates.TemplateResponse("intel/competitors.html", {
        "request": request,
        "quarter": q,
        "quarters": quarters,
        "tab": tab,
        "vertical": vertical,
        "products": products,
        "verticals": verticals,
        "issuers": issuers,
        "kpis": kpis,
        "is_latest": is_latest,
        "latest_quarter": latest_quarter,
        "fmt_value": fmt_value,
    })


@router.get("/competitors/new-filers")
def competitors_new_filers_page(
    request: Request,
    quarter: str | None = Query(default=None),
    tab: str = Query(default="all"),
    vertical: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """New filers for competitor products."""
    q, quarters, latest_quarter, is_latest = _resolve_quarter(db, quarter)

    if not q:
        return templates.TemplateResponse("intel/competitors_new_filers.html", {
            "request": request,
            "quarter": "",
            "quarters": [],
            "tab": tab,
            "vertical": vertical,
            "new_filers": [],
            "kpis": {},
            "issuer_breakdown": [],
            "vertical_breakdown": [],
            "is_latest": True,
            "latest_quarter": "",
            "fmt_value": fmt_value,
        })

    new_filers = get_competitor_new_filers(db, q)
    if vertical:
        new_filers = [f for f in new_filers if f.get("vertical") == vertical]

    # Competitor new filer KPIs
    new_filer_count = len(set(f["institution_id"] for f in new_filers))
    new_filer_aum = sum(f.get("total_value", 0) for f in new_filers)

    # Products and issuers across all competitor new filer activity
    comp_products = len(set(f.get("ticker") for f in new_filers if f.get("ticker")))
    comp_issuers = len(set(f.get("issuer") for f in new_filers if f.get("issuer")))

    kpis = {
        "new_filer_count": new_filer_count,
        "new_filer_aum": new_filer_aum,
        "new_filer_aum_fmt": fmt_value(new_filer_aum),
        "comp_products": comp_products,
        "comp_issuers": comp_issuers,
    }

    issuer_breakdown = get_holdings_by_issuer(db, q, rex_only=False)
    issuer_breakdown = [i for i in issuer_breakdown if i.get("issuer") not in (
        "REX Financial", "REX Financial: MicroSectors",
    )]
    vertical_breakdown = get_holdings_by_vertical(db, q, rex_only=False)

    return templates.TemplateResponse("intel/competitors_new_filers.html", {
        "request": request,
        "quarter": q,
        "quarters": quarters,
        "tab": tab,
        "vertical": vertical,
        "new_filers": new_filers,
        "kpis": kpis,
        "issuer_breakdown": issuer_breakdown,
        "vertical_breakdown": vertical_breakdown,
        "is_latest": is_latest,
        "latest_quarter": latest_quarter,
        "fmt_value": fmt_value,
    })


@router.get("/products")
def products_page(
    request: Request,
    quarter: str | None = Query(default=None),
    search: str | None = Query(default=None),
    vertical: str | None = Query(default=None),
    page: int | None = Query(default=1),
    db: Session = Depends(get_db),
):
    """Full product universe browser."""
    q, quarters, latest_quarter, is_latest = _resolve_quarter(db, quarter)

    if not q:
        return templates.TemplateResponse("intel/products.html", {
            "request": request,
            "quarter": "",
            "quarters": [],
            "search": search,
            "vertical": vertical,
            "products": [],
            "verticals": [],
            "kpis": {},
            "page": 1,
            "total_pages": 1,
            "total_filtered": 0,
            "is_latest": True,
            "latest_quarter": "",
            "fmt_value": fmt_value,
        })

    all_products = get_holdings_by_product(db, q, rex_only=False, vertical=vertical)
    verticals = get_distinct_verticals(db, q)

    # Apply text search filter if provided
    if search:
        search_lower = search.lower()
        all_products = [
            p for p in all_products
            if search_lower in (p.get("ticker") or "").lower()
            or search_lower in (p.get("security_name") or "").lower()
            or search_lower in (p.get("issuer") or "").lower()
        ]

    # Product-level KPIs (computed on full filtered set)
    total_products = len(set(p["ticker"] for p in all_products))
    total_issuers = len(set(p.get("issuer") for p in all_products if p.get("issuer")))
    etf_count = len([p for p in all_products if p.get("product_type") == "ETF"])
    etn_count = len([p for p in all_products if p.get("product_type") == "ETN"])
    vertical_count = len(set(p.get("vertical") for p in all_products if p.get("vertical")))

    kpis = {
        "total_products": total_products,
        "total_issuers": total_issuers,
        "etf_count": etf_count,
        "etn_count": etn_count,
        "vertical_count": vertical_count,
    }

    # Pagination — 100 per page
    per_page = 100
    page_num = max(1, page or 1)
    total_filtered = len(all_products)
    total_pages = max(1, (total_filtered + per_page - 1) // per_page)
    page_num = min(page_num, total_pages)
    products = all_products[(page_num - 1) * per_page : page_num * per_page]

    return templates.TemplateResponse("intel/products.html", {
        "request": request,
        "quarter": q,
        "quarters": quarters,
        "search": search,
        "vertical": vertical,
        "products": products,
        "verticals": verticals,
        "kpis": kpis,
        "page": page_num,
        "total_pages": total_pages,
        "total_filtered": total_filtered,
        "is_latest": is_latest,
        "latest_quarter": latest_quarter,
        "fmt_value": fmt_value,
    })


@router.get("/head-to-head")
def head_to_head_page(
    request: Request,
    quarter: str | None = Query(default=None),
    underlying: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Head-to-head product comparison by underlying."""
    q, quarters, latest_quarter, is_latest = _resolve_quarter(db, quarter)

    if not q:
        return templates.TemplateResponse("intel/head_to_head.html", {
            "request": request,
            "quarter": "",
            "quarters": [],
            "underlying": underlying,
            "products": [],
            "underlyings_list": [],
            "is_latest": True,
            "latest_quarter": "",
            "fmt_value": fmt_value,
        })

    underlyings_list = get_distinct_underlyings(db, q)
    products = get_head_to_head(db, q, underlying) if underlying else []

    return templates.TemplateResponse("intel/head_to_head.html", {
        "request": request,
        "quarter": q,
        "quarters": quarters,
        "underlying": underlying,
        "products": products,
        "underlyings_list": underlyings_list,
        "is_latest": is_latest,
        "latest_quarter": latest_quarter,
        "fmt_value": fmt_value,
    })
