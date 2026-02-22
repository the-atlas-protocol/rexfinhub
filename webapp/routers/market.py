"""
Market Intelligence router.

Routes:
  GET /market/            -> redirect to /market/rex
  GET /market/rex         -> REX View (suite-by-suite performance)
  GET /market/category    -> Category View (competitive landscape)
  GET /market/api/rex-summary       -> JSON for REX View charts
  GET /market/api/category-summary  -> JSON for Category View (with filters)
  GET /market/api/time-series       -> JSON for line charts
  GET /market/api/slicers/{cat}     -> JSON slicer options for a category
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

log = logging.getLogger(__name__)
router = APIRouter(prefix="/market", tags=["market"])
templates = Jinja2Templates(directory="webapp/templates")


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
def rex_view(request: Request, product_type: str = Query(default="All")):
    """REX View - executive dashboard by suite."""
    svc = _svc()
    available = svc.data_available()
    if not available:
        return templates.TemplateResponse("market/rex.html", {
            "request": request,
            "available": False,
            "active_tab": "rex",
            "data_as_of": svc.get_data_as_of(),
        })
    try:
        summary = svc.get_rex_summary()
        trend = _parse_ts(svc.get_time_series(is_rex=True))
        return templates.TemplateResponse("market/rex.html", {
            "request": request,
            "available": True,
            "active_tab": "rex",
            "summary": summary,
            "trend": trend,
            "product_type": product_type,
            "data_as_of": svc.get_data_as_of(),
        })
    except Exception as e:
        log.error("REX view error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/rex.html", {
            "request": request,
            "available": False,
            "active_tab": "rex",
            "error": str(e),
            "data_as_of": svc.get_data_as_of(),
        })


@router.get("/category")
def category_view(
    request: Request,
    cat: str = Query(default="All"),
    filters: str = Query(default=None),
):
    """Category View - competitive landscape with dynamic filters."""
    svc = _svc()
    available = svc.data_available()
    if not available:
        return templates.TemplateResponse("market/category.html", {
            "request": request,
            "available": False,
            "active_tab": "category",
            "categories": svc.ALL_CATEGORIES,
            "category": cat,
            "data_as_of": svc.get_data_as_of(),
        })
    try:
        filter_dict = json.loads(filters) if filters else {}
        cat_arg = cat if cat != "All" else None
        summary = svc.get_category_summary(cat_arg, filter_dict)
        slicers = svc.get_slicer_options(cat) if cat and cat != "All" else []
        ts_cat = _parse_ts(svc.get_time_series(category=cat_arg))
        ts_rex = _parse_ts(svc.get_time_series(category=cat_arg, is_rex=True))
        trend = {
            "labels": ts_cat["labels"],
            "total_values": ts_cat["values"],
            "rex_values": ts_rex["values"],
        }
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
            "data_as_of": svc.get_data_as_of(),
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
            "data_as_of": svc.get_data_as_of(),
        })


@router.get("/treemap")
def treemap_view(request: Request, cat: str = Query(default="All")):
    svc = _svc()
    available = svc.data_available()
    if not available:
        return templates.TemplateResponse("market/treemap.html", {"request": request, "available": False, "active_tab": "treemap", "categories": svc.ALL_CATEGORIES, "data_as_of": svc.get_data_as_of()})
    try:
        cat_arg = cat if cat != "All" else None
        summary = svc.get_treemap_data(cat_arg)
        return templates.TemplateResponse("market/treemap.html", {
            "request": request, "available": True, "active_tab": "treemap",
            "summary": summary, "categories": svc.ALL_CATEGORIES, "category": cat,
            "data_as_of": svc.get_data_as_of(),
        })
    except Exception as e:
        log.error("Treemap error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/treemap.html", {"request": request, "available": False, "active_tab": "treemap", "categories": svc.ALL_CATEGORIES, "error": str(e), "data_as_of": svc.get_data_as_of()})


@router.get("/issuer")
def issuer_view(request: Request, cat: str = Query(default="All")):
    svc = _svc()
    available = svc.data_available()
    if not available:
        return templates.TemplateResponse("market/issuer.html", {"request": request, "available": False, "active_tab": "issuer", "categories": svc.ALL_CATEGORIES, "data_as_of": svc.get_data_as_of()})
    try:
        cat_arg = cat if cat != "All" else None
        summary = svc.get_issuer_summary(cat_arg)
        return templates.TemplateResponse("market/issuer.html", {
            "request": request, "available": True, "active_tab": "issuer",
            "summary": summary, "categories": svc.ALL_CATEGORIES, "category": cat,
            "data_as_of": svc.get_data_as_of(),
        })
    except Exception as e:
        log.error("Issuer view error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/issuer.html", {"request": request, "available": False, "active_tab": "issuer", "categories": svc.ALL_CATEGORIES, "error": str(e), "data_as_of": svc.get_data_as_of()})


@router.get("/issuer/detail")
def issuer_detail_view(request: Request, issuer: str = Query(default="")):
    """Issuer deep-dive: all products, AUM trend, category breakdown."""
    import math
    import pandas as pd
    from datetime import datetime
    svc = _svc()
    available = svc.data_available()
    issuer_data = {}
    products = []
    categories = []
    aum_trend = {}

    fmt = svc._fmt_currency if hasattr(svc, '_fmt_currency') else None
    if fmt is None:
        from webapp.services.market_data import _fmt_currency
        fmt = _fmt_currency

    if available and issuer:
        try:
            master = svc.get_master_data()
            if not master.empty:
                ticker_col = next((c for c in master.columns if c.lower().strip() == "ticker"), "ticker")
                issuer_col = next((c for c in master.columns if c.lower().strip() == "issuer_display"), None)
                aum_col = next((c for c in master.columns if "t_w4.aum" == c.lower().strip()), None) or \
                           next((c for c in master.columns if c.endswith(".aum") and not any(c.endswith(f".aum_{i}") for i in range(1, 37))), None)
                cat_col = next((c for c in master.columns if c.lower().strip() == "category_display"), None)
                name_col = next((c for c in master.columns if c.lower().strip() == "fund_name"), None)

                if issuer_col:
                    df = master[master[issuer_col].fillna("").str.strip() == issuer.strip()].copy()
                    if not df.empty:
                        total_aum = float(df[aum_col].fillna(0).sum()) if aum_col else 0

                        # Category breakdown
                        if cat_col and aum_col:
                            cat_grp = df.groupby(cat_col)[aum_col].sum().reset_index()
                            categories = [{"name": r[cat_col], "aum_fmt": fmt(float(r[aum_col]))}
                                         for _, r in cat_grp.sort_values(aum_col, ascending=False).iterrows()]

                        # Product list
                        products_df = df.sort_values(aum_col, ascending=False) if aum_col else df
                        for _, row in products_df.iterrows():
                            aum_val = float(row.get(aum_col, 0) or 0) if aum_col else 0
                            products.append({
                                "ticker": str(row.get("ticker_clean", row.get(ticker_col, ""))),
                                "fund_name": str(row.get(name_col, "")) if name_col else "",
                                "category": str(row.get(cat_col, "")) if cat_col else "",
                                "aum_fmt": fmt(aum_val),
                                "is_rex": bool(row.get("is_rex", False)),
                            })

                        is_rex = any(df.get("is_rex", pd.Series(dtype=bool)))

                        issuer_data = {
                            "name": issuer,
                            "total_aum": total_aum,
                            "total_aum_fmt": fmt(total_aum),
                            "num_products": len(df),
                            "num_categories": len(categories),
                            "is_rex": is_rex,
                        }

                        # 12-month AUM trend
                        months_labels = []
                        months_values = []
                        now = datetime.now()
                        for i in range(12, -1, -1):
                            col_name = f"t_w4.aum_{i}" if i > 0 else aum_col
                            if col_name and col_name in df.columns:
                                val = float(df[col_name].fillna(0).sum())
                                try:
                                    from dateutil.relativedelta import relativedelta
                                    dt = now - relativedelta(months=i)
                                except ImportError:
                                    from datetime import timedelta
                                    dt = now - timedelta(days=30 * i)
                                months_labels.append(dt.strftime("%b %Y"))
                                months_values.append(round(val, 2))
                        aum_trend = {"labels": months_labels, "values": months_values}
        except Exception as e:
            log.error("Issuer detail error: %s", e)

    return templates.TemplateResponse("market/issuer_detail.html", {
        "request": request,
        "active_tab": "issuer",
        "available": available,
        "issuer": issuer,
        "issuer_data": issuer_data,
        "products": products,
        "categories": categories,
        "aum_trend": aum_trend,
        "data_as_of": "",
    })


@router.get("/share")
def share_timeline_view(request: Request):
    svc = _svc()
    available = svc.data_available()
    if not available:
        return templates.TemplateResponse("market/share_timeline.html", {"request": request, "available": False, "active_tab": "share", "data_as_of": svc.get_data_as_of()})
    try:
        timeline = svc.get_market_share_timeline()
        return templates.TemplateResponse("market/share_timeline.html", {
            "request": request, "available": True, "active_tab": "share", "timeline": timeline,
            "data_as_of": svc.get_data_as_of(),
        })
    except Exception as e:
        log.error("Share timeline error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/share_timeline.html", {"request": request, "available": False, "active_tab": "share", "error": str(e), "data_as_of": svc.get_data_as_of()})


@router.get("/underlier")
def underlier_view(request: Request, type: str = Query(default="income"), underlier: str = Query(default=None)):
    svc = _svc()
    available = svc.data_available()
    if not available:
        return templates.TemplateResponse("market/underlier.html", {"request": request, "available": False, "active_tab": "underlier", "data_as_of": svc.get_data_as_of()})
    try:
        summary = svc.get_underlier_summary(type, underlier)
        return templates.TemplateResponse("market/underlier.html", {
            "request": request, "available": True, "active_tab": "underlier",
            "summary": summary, "underlier_type": type, "selected_underlier": underlier,
            "data_as_of": svc.get_data_as_of(),
        })
    except Exception as e:
        log.error("Underlier view error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/underlier.html", {"request": request, "available": False, "active_tab": "underlier", "error": str(e), "data_as_of": svc.get_data_as_of()})


#  API endpoints (AJAX)

@router.get("/api/rex-summary")
def api_rex_summary():
    try:
        svc = _svc()
        return JSONResponse(svc.get_rex_summary())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/category-summary")
def api_category_summary(
    category: str = Query(default="All"),
    filters: str = Query(default=None),
):
    try:
        svc = _svc()
        filter_dict = json.loads(filters) if filters else {}
        cat = category if category != "All" else None
        data = svc.get_category_summary(cat, filter_dict)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/time-series")
def api_time_series(
    category: str = Query(default="All"),
    is_rex: str = Query(default="both"),
):
    try:
        svc = _svc()
        cat = category if category != "All" else None
        if is_rex == "true":
            data = svc.get_time_series(category=cat, is_rex=True)
        elif is_rex == "false":
            data = svc.get_time_series(category=cat, is_rex=False)
        else:
            # Return both
            all_ts = svc.get_time_series(category=cat)
            rex_ts = svc.get_time_series(category=cat, is_rex=True)
            data = {
                "labels": all_ts["labels"],
                "values_all": all_ts["values"],
                "values_rex": rex_ts["values"],
            }
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/slicers/{category:path}")
def api_slicers(category: str):
    try:
        svc = _svc()
        return JSONResponse(svc.get_slicer_options(category))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/treemap")
def api_treemap(category: str = Query(default="All")):
    try:
        svc = _svc()
        cat = category if category != "All" else None
        return JSONResponse(svc.get_treemap_data(cat))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/issuer")
def api_issuer(category: str = Query(default="All")):
    try:
        svc = _svc()
        cat = category if category != "All" else None
        return JSONResponse(svc.get_issuer_summary(cat))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/share")
def api_share():
    try:
        return JSONResponse(_svc().get_market_share_timeline())
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/api/underlier")
def api_underlier(type: str = Query(default="income"), underlier: str = Query(default=None)):
    try:
        return JSONResponse(_svc().get_underlier_summary(type, underlier))
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/api/invalidate-cache")
def api_invalidate_cache():
    """Clear the market data cache (admin utility)."""
    try:
        _svc().invalidate_cache()
        return JSONResponse({"status": "ok"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
