"""
Market Intelligence router.

Routes:
  GET /market/                       -> redirect to /market/rex
  GET /market/rex                    -> REX View (suite-by-suite performance)
  GET /market/category               -> Category View (competitive landscape)
  GET /market/underlier              -> Underlier drill-down
  GET /market/api/rex-summary        -> JSON for REX View charts
  GET /market/api/category-summary   -> JSON for Category View (with filters)
  GET /market/api/time-series        -> JSON for line charts
  GET /market/api/slicers/{cat}      -> JSON slicer options for a category
"""
from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from webapp.dependencies import get_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/market", tags=["market"])
templates = Jinja2Templates(directory="webapp/templates")

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


def _svc():
    from webapp.services import market_data
    return market_data


#  Pages 

@router.get("/")
def market_index():
    return RedirectResponse("/market/rex", status_code=302)


def _parse_ts(ts: dict) -> dict[str, Any]:
    """Convert get_time_series() JSON strings to raw lists for templates."""
    return {
        "labels": json.loads(ts["labels"]),
        "values": json.loads(ts["values"]),
    }


@router.get("/rex")
def rex_view(request: Request, db: Session = Depends(get_db), product_type: str = Query(default="All"), fund_structure: str = Query(default="ETF,ETN"), category: str = Query(default="All")):
    """REX View - executive dashboard by suite."""
    svc = _svc()
    available = svc.data_available(db)
    rex_categories = svc.REX_SUITES
    if not available:
        return templates.TemplateResponse("market/rex.html", {
            "request": request,
            "available": False,
            "active_tab": "rex",
            "categories": rex_categories,
            "data_as_of": svc.get_data_as_of(db),
        })
    try:
        cat_arg = category if category != "All" else None
        summary = svc.get_rex_summary(db, fund_structure=fund_structure, category=cat_arg, etn_overrides=True)

        trend = _parse_ts(svc.get_time_series(db, is_rex=True, category=cat_arg, fund_type=fund_structure))
        # If a specific category is selected, also provide "all REX" trend for overlay
        trend_all = None
        if cat_arg:
            trend_all = _parse_ts(svc.get_time_series(db, is_rex=True, fund_type=fund_structure))
        return templates.TemplateResponse("market/rex.html", {
            "request": request,
            "available": True,
            "active_tab": "rex",
            "summary": summary,
            "trend": trend,
            "trend_all": trend_all,
            "product_type": product_type,
            "fund_structure": fund_structure,
            "category": category,
            "categories": rex_categories,
            "data_as_of": svc.get_data_as_of(db),
        })
    except Exception as e:
        log.error("REX view error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/rex.html", {
            "request": request,
            "available": False,
            "active_tab": "rex",
            "error": str(e),
            "categories": rex_categories,
            "data_as_of": svc.get_data_as_of(db),
        })


@router.get("/category")
def category_view(
    request: Request,
    db: Session = Depends(get_db),
    cat: str = Query(default="All"),
    filters: str = Query(default=None),
    fund_structure: str = Query(default="ETF,ETN"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=10, le=200),
):
    """Category View - competitive landscape with dynamic filters."""
    svc = _svc()
    available = svc.data_available(db)

    # Default to first category instead of "All" (aggregating all categories is misleading)
    if cat == "All" and available:
        first_cat = svc.ALL_CATEGORIES[0] if svc.ALL_CATEGORIES else "Crypto"
        frag = "&fragment=1" if request.query_params.get("fragment") == "1" else ""
        return RedirectResponse(
            f"/market/category?cat={urllib.parse.quote(first_cat)}&fund_structure={fund_structure}{frag}",
            status_code=302,
        )

    if not available:
        return templates.TemplateResponse("market/category.html", {
            "request": request,
            "available": False,
            "active_tab": "category",
            "categories": svc.ALL_CATEGORIES,
            "category": cat,
            "data_as_of": svc.get_data_as_of(db),
        })
    try:
        # Build filter dict from either JSON or individual query params
        filter_dict = {}
        if filters:
            try:
                filter_dict = json.loads(filters)
            except (json.JSONDecodeError, TypeError):
                pass
        # Also read slicer params directly from query string
        for key, val in request.query_params.items():
            if key.startswith("q_category_attributes.") and val:
                filter_dict[key] = val

        cat_arg = cat if cat != "All" else None
        summary = svc.get_category_summary(db, cat_arg, filter_dict, fund_structure=fund_structure, page=page, per_page=per_page)

        # Treemap data for this category
        treemap_data = svc.get_treemap_data(db, cat_arg, fund_type=fund_structure, filters=filter_dict)

        slicers = svc.get_slicer_options(db, cat) if cat and cat != "All" else []
        ts_cat = _parse_ts(svc.get_time_series(db, category=cat_arg, filters=filter_dict))
        ts_rex = _parse_ts(svc.get_time_series(db, category=cat_arg, is_rex=True, filters=filter_dict))
        trend = {
            "labels": ts_cat["labels"],
            "total_values": ts_cat["values"],
            "rex_values": ts_rex["values"],
        }
        # Build base query string for pagination (preserves all filters except page)
        qs_params = {"cat": cat, "fund_structure": fund_structure}
        if per_page != 50:
            qs_params["per_page"] = per_page
        for key, val in filter_dict.items():
            qs_params[key] = val
        base_qs = urllib.parse.urlencode(qs_params)

        return templates.TemplateResponse("market/category.html", {
            "request": request,
            "available": True,
            "active_tab": "category",
            "categories": svc.ALL_CATEGORIES,
            "category": cat,
            "summary": summary,
            "slicers": slicers,
            "active_filters": filter_dict,
            "trend": trend,
            "fund_structure": fund_structure,
            "treemap": treemap_data,
            "page": page,
            "per_page": per_page,
            "base_qs": base_qs,
            "data_as_of": svc.get_data_as_of(db),
        })
    except Exception as e:
        log.error("Category view error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/category.html", {
            "request": request,
            "available": False,
            "active_tab": "category",
            "categories": svc.ALL_CATEGORIES,
            "category": cat,
            "error": str(e),
            "data_as_of": svc.get_data_as_of(db),
        })


@router.get("/treemap")
def treemap_view(request: Request, cat: str = Query(default="")):
    """Treemap merged into Category View - redirect there."""
    redirect_url = "/market/category"
    if cat:
        redirect_url += f"?cat={cat}"
    return RedirectResponse(redirect_url, status_code=302)


@router.get("/issuer")
def issuer_view(request: Request, db: Session = Depends(get_db), cat: str = Query(default="All"), fund_structure: str = Query(default="ETF,ETN")):
    svc = _svc()
    available = svc.data_available(db)
    if not available:
        return templates.TemplateResponse("market/issuer.html", {
            "request": request, "available": False, "active_tab": "issuer",
            "categories": svc.ALL_CATEGORIES, "data_as_of": svc.get_data_as_of(db),
        })
    try:
        cat_arg = cat if cat != "All" else None
        summary = svc.get_issuer_summary(db, cat_arg, fund_structure=fund_structure)
        # Trend/share data for charts (donut + 12-month trend)
        share_data = {}
        if cat_arg:
            try:
                share_data = svc.get_issuer_share(db, cat_arg)
            except Exception:
                pass
        return templates.TemplateResponse("market/issuer.html", {
            "request": request, "available": True, "active_tab": "issuer",
            "summary": summary, "categories": svc.ALL_CATEGORIES, "category": cat,
            "fund_structure": fund_structure,
            "share_data": share_data,
            "data_as_of": svc.get_data_as_of(db),
        })
    except Exception as e:
        log.error("Issuer view error: %s", e, exc_info=True)
        empty_summary = {"issuers": [], "total_aum": 0, "total_aum_fmt": "$0", "categories": svc.ALL_CATEGORIES}
        return templates.TemplateResponse("market/issuer.html", {
            "request": request, "available": True, "active_tab": "issuer",
            "summary": empty_summary, "categories": svc.ALL_CATEGORIES, "category": cat,
            "error": str(e), "data_as_of": svc.get_data_as_of(db),
        })


@router.get("/issuer/detail")
def _issuer_detail_redirect(issuer: str = Query(default="")):
    """Legacy /market/issuer/detail?issuer=X -> /issuers/{canonical_name} (301).

    Replaced by the canonical /issuers/{name} surface in PR 2b. Canonicalize
    the variant before redirecting so e.g. ?issuer=BlackRock+Inc lands on
    /issuers/BlackRock instead of bouncing twice.
    """
    from webapp.services.market_data import _get_issuer_canon_map
    if not issuer:
        return RedirectResponse("/issuers/", status_code=301)
    canon_map = _get_issuer_canon_map()
    target = canon_map.get(issuer.strip(), issuer.strip())
    return RedirectResponse(f"/issuers/{target}", status_code=301)


@router.get("/share")
def share_timeline_view(request: Request, cat: str = Query(default="")):
    """Redirect to merged Issuer Analysis page."""
    redirect_url = "/market/issuer"
    if cat:
        redirect_url += f"?cat={urllib.parse.quote(cat, safe='')}"
    return RedirectResponse(redirect_url, status_code=302)


@router.get("/rex-performance")
def rex_performance_view(
    request: Request,
    db: Session = Depends(get_db),
):
    """REX Performance -- interactive screener with column picker and filters."""
    svc = _svc()
    available = svc.data_available(db)
    return templates.TemplateResponse("market/rex_performance.html", {
        "request": request,
        "available": available,
        "active_tab": "rex-performance",
        "data_as_of": svc.get_data_as_of(db),
    })


@router.get("/api/screener-data")
def screener_data_api(
    db: Session = Depends(get_db),
    scope: str = Query(default="all"),  # all, rex, competitors
):
    """Return all active fund data as JSON for the interactive screener."""
    from sqlalchemy import text as sa_text

    cols = (
        "ticker, fund_name, issuer_display, aum, market_status, "
        "etp_category, category_display, is_rex, rex_suite, "
        "total_return_1day, total_return_1week, total_return_1month, "
        "total_return_3month, total_return_6month, total_return_ytd, total_return_1year, "
        "total_return_3year, annualized_yield, "
        "expense_ratio, management_fee, average_vol_30day, open_interest, "
        "percent_short_interest, average_bidask_spread, nav_tracking_error, percentage_premium, "
        "average_percent_premium_52week, "
        "fund_flow_1day, fund_flow_1week, fund_flow_1month, fund_flow_3month, "
        "fund_flow_6month, fund_flow_ytd, fund_flow_1year, "
        "inception_date, fund_type, asset_class_focus, underlying_index, "
        "is_singlestock, uses_leverage, leverage_amount, outcome_type, is_crypto, "
        "strategy, underlier_type, cusip, listed_exchange, regulatory_structure, "
        "map_li_direction, map_li_leverage_amount, map_li_underlier, "
        "map_cc_underlier, map_crypto_underlier, map_defined_category, "
        "map_thematic_category, cc_type, cc_category, strategy_confidence, "
        "uses_derivatives, uses_swaps, is_40act, index_weighting_methodology, "
        "primary_strategy, asset_class, sub_strategy"
    )
    # Only ETFs and ETNs (exclude Open-End Funds, SICAVs, etc.)
    query = f"SELECT {cols} FROM mkt_master_data WHERE market_status = 'ACTV' AND (fund_type = 'ETF' OR fund_type = 'ETN')"
    if scope == "rex":
        query += " AND is_rex = 1"
    elif scope == "competitors":
        query += " AND is_rex = 0"

    rows = db.execute(sa_text(query)).fetchall()
    col_names = [c.strip() for c in cols.split(",")]

    import math
    funds = []
    for row in rows:
        d = {}
        for i, col in enumerate(col_names):
            val = row[i]
            if val is None:
                d[col] = None
            elif isinstance(val, float):
                # JSON can't serialize NaN or Infinity
                d[col] = None if (math.isnan(val) or math.isinf(val)) else round(val, 6)
            elif isinstance(val, int):
                d[col] = val
            else:
                d[col] = str(val)
        funds.append(d)

    return {"funds": funds, "count": len(funds)}


@router.get("/stock-coverage")
def stock_coverage_redirect(request: Request, type: str = Query(default="income"), underlier: str = Query(default=None)):
    """Redirect /market/stock-coverage to /market/underlier (canonical route)."""
    qs = f"?type={type}"
    if underlier:
        qs += f"&underlier={underlier}"
    return RedirectResponse(f"/market/underlier{qs}", status_code=302)


@router.get("/underlier")
def underlier_view(request: Request, db: Session = Depends(get_db), type: str = Query(default="income"), underlier: str = Query(default=None)):
    svc = _svc()
    available = svc.data_available(db)
    # Normalize the underlier param so ?underlier=SNDK and ?underlier=SNDK+US
    # both resolve to the same canonical key. Crypto shorthand (BTC, ETH)
    # also maps to BBG canonical (XBTUSD, XETUSD).
    from webapp.services.ticker_normalize import normalize_underlier
    underlier_norm = normalize_underlier(underlier) if underlier else None
    if not available:
        return templates.TemplateResponse("market/underlier.html", {"request": request, "available": False, "active_tab": "underlier", "data_as_of": svc.get_data_as_of(db)})
    try:
        summary = svc.get_underlier_summary(db, type, underlier_norm)
        return templates.TemplateResponse("market/underlier.html", {
            "request": request, "available": True, "active_tab": "underlier",
            "summary": summary, "underlier_type": type, "selected_underlier": underlier_norm,
            "data_as_of": svc.get_data_as_of(db),
        })
    except Exception as e:
        log.error("Underlier view error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/underlier.html", {"request": request, "available": False, "active_tab": "underlier", "error": str(e), "data_as_of": svc.get_data_as_of(db)})


#  API endpoints (AJAX)

@router.get("/api/rex-summary")
def api_rex_summary(db: Session = Depends(get_db)):
    try:
        svc = _svc()
        return JSONResponse(svc.get_rex_summary(db, fund_structure="ETF,ETN", etn_overrides=True))
    except Exception as e:
        log.error("Request failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/api/category-summary")
def api_category_summary(
    db: Session = Depends(get_db),
    category: str = Query(default="All"),
    filters: str = Query(default=None),
    fund_structure: str = Query(default="ETF,ETN"),
):
    try:
        svc = _svc()
        filter_dict = json.loads(filters) if filters else {}
        cat = category if category != "All" else None
        data = svc.get_category_summary(db, cat, filter_dict, fund_structure=fund_structure)
        return JSONResponse(data)
    except Exception as e:
        log.error("Request failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/api/time-series")
def api_time_series(
    db: Session = Depends(get_db),
    category: str = Query(default="All"),
    is_rex: str = Query(default="both"),
):
    try:
        svc = _svc()
        cat = category if category != "All" else None
        if is_rex == "true":
            data = svc.get_time_series(db, category=cat, is_rex=True)
        elif is_rex == "false":
            data = svc.get_time_series(db, category=cat, is_rex=False)
        else:
            # Return both
            all_ts = svc.get_time_series(db, category=cat)
            rex_ts = svc.get_time_series(db, category=cat, is_rex=True)
            data = {
                "labels": all_ts["labels"],
                "values_all": all_ts["values"],
                "values_rex": rex_ts["values"],
            }
        return JSONResponse(data)
    except Exception as e:
        log.error("Request failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/api/slicers/{category:path}")
def api_slicers(category: str, db: Session = Depends(get_db)):
    try:
        svc = _svc()
        return JSONResponse(svc.get_slicer_options(db, category))
    except Exception as e:
        log.error("Request failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/api/treemap")
def api_treemap(db: Session = Depends(get_db), category: str = Query(default="All"), fund_structure: str = Query(default="ETF,ETN")):
    try:
        svc = _svc()
        cat = category if category != "All" else None
        return JSONResponse(svc.get_treemap_data(db, cat, fund_type=fund_structure))
    except Exception as e:
        log.error("Request failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/api/issuer")
def api_issuer(db: Session = Depends(get_db), category: str = Query(default="All"), fund_structure: str = Query(default="ETF,ETN")):
    try:
        svc = _svc()
        cat = category if category != "All" else None
        return JSONResponse(svc.get_issuer_summary(db, cat, fund_structure=fund_structure))
    except Exception as e:
        log.error("Request failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/api/share")
def api_share(db: Session = Depends(get_db)):
    try:
        return JSONResponse(_svc().get_market_share_timeline(db))
    except Exception as e:
        log.error("Request failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/api/underlier")
def api_underlier(db: Session = Depends(get_db), type: str = Query(default="income"), underlier: str = Query(default=None)):
    try:
        return JSONResponse(_svc().get_underlier_summary(db, type, underlier))
    except Exception as e:
        log.error("Request failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.post("/api/invalidate-cache")
def api_invalidate_cache(request: Request):
    """Clear the market data cache (admin utility)."""
    if not request.session.get("is_admin"):
        return JSONResponse({"error": "Unauthorized"}, status_code=403)
    try:
        _svc().invalidate_cache()
        from webapp.services.report_data import invalidate_cache as inv_report
        from webapp.services.screener_3x_cache import invalidate_cache as inv_screener
        inv_report()
        inv_screener()
        return JSONResponse({"status": "ok"})
    except Exception as e:
        log.error("Cache invalidation failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Cache invalidation failed"}, status_code=500)
