"""
Market Intelligence routes.

Routes:
- GET /market/         -> Redirect to /market/rex
- GET /market/rex      -> REX View (suite-by-suite performance)
- GET /market/category -> Category View (competitive landscape)
- GET /market/api/rex-summary       -> JSON for REX View
- GET /market/api/category-summary  -> JSON for Category View
- GET /market/api/rex-trend         -> JSON for REX AUM trend chart
- GET /market/api/category-trend    -> JSON for category AUM trend chart
- GET /market/api/slicers/{category} -> JSON filter options
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from webapp.services import market_data

router = APIRouter(prefix="/market", tags=["market"])
templates = Jinja2Templates(directory="webapp/templates")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------

@router.get("/")
async def market_index():
    """Redirect to REX View."""
    return RedirectResponse("/market/rex", status_code=302)


@router.get("/rex")
async def rex_view(request: Request):
    """REX View - suite-by-suite performance."""
    available = market_data.data_available()
    summary = market_data.get_rex_summary() if available else None
    trend = market_data.get_rex_aum_trend() if available else None

    return templates.TemplateResponse("market/rex.html", {
        "request": request,
        "available": available,
        "summary": summary,
        "trend": trend,
        "categories": market_data.ALL_CATEGORIES,
    })


@router.get("/category")
async def category_view(request: Request, cat: str = Query(None)):
    """Category View - competitive landscape."""
    available = market_data.data_available()
    category = cat or "All"
    summary = market_data.get_category_summary(category) if available else None
    trend = market_data.get_category_aum_trend(category) if available else None
    slicers = market_data.get_slicer_options(category) if available and category != "All" else []

    return templates.TemplateResponse("market/category.html", {
        "request": request,
        "available": available,
        "category": category,
        "summary": summary,
        "trend": trend,
        "slicers": slicers,
        "categories": market_data.ALL_CATEGORIES,
    })


# ---------------------------------------------------------------------------
# API endpoints for AJAX updates
# ---------------------------------------------------------------------------

@router.get("/api/rex-summary")
async def api_rex_summary():
    """JSON data for REX View."""
    summary = market_data.get_rex_summary()
    if not summary:
        return JSONResponse({"error": "No data available"}, status_code=404)
    return summary


@router.get("/api/category-summary")
async def api_category_summary(
    category: str = Query("All"),
    filters: str = Query(None),
):
    """JSON data for Category View with optional slicer filters."""
    filter_dict = {}
    if filters:
        try:
            filter_dict = json.loads(filters)
        except json.JSONDecodeError:
            return JSONResponse({"error": "Invalid filters JSON"}, status_code=400)

    summary = market_data.get_category_summary(category, filter_dict or None)
    if not summary:
        return JSONResponse({"error": "No data available"}, status_code=404)
    return summary


@router.get("/api/rex-trend")
async def api_rex_trend():
    """JSON time series for REX AUM trend line chart."""
    trend = market_data.get_rex_aum_trend()
    if not trend:
        return JSONResponse({"error": "No data available"}, status_code=404)
    return trend


@router.get("/api/category-trend")
async def api_category_trend(category: str = Query("All")):
    """JSON time series for category AUM trend chart."""
    trend = market_data.get_category_aum_trend(category)
    if not trend:
        return JSONResponse({"error": "No data available"}, status_code=404)
    return trend


@router.get("/api/slicers/{category}")
async def api_slicers(category: str):
    """JSON slicer options for a category."""
    slicers = market_data.get_slicer_options(category)
    return slicers
