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
import urllib.parse
from typing import Any, Optional

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

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


def _compute_health_scores(master_df) -> dict[str, dict]:
    """Compute product health scores (0-100) for REX products.

    5 dimensions, each scored 0-20:
      1. AUM Level: percentile rank vs category peers
      2. Flow Momentum: 1M flow as % of AUM, ranked
      3. Tracking Quality: inverse of tracking error vs category (lower = better)
      4. Liquidity: inverse of bid-ask spread (tighter = better)
      5. AUM Trend: 3-month AUM growth rate
    """
    import math

    rex = master_df[master_df["is_rex"] == True].copy()
    if rex.empty or "ticker_clean" not in rex.columns:
        return {}
    rex = rex.drop_duplicates(subset=["ticker_clean"], keep="first")

    scores = {}
    aum_col = "t_w4.aum"
    flow_col = "t_w4.fund_flow_1month"
    spread_col = "t_w2.average_bidask_spread"
    aum_3m_col = "t_w4.aum_3"

    for _, row in rex.iterrows():
        ticker = str(row.get("ticker_clean", ""))
        if not ticker:
            continue

        aum = float(row.get(aum_col, 0) or 0)
        flow_1m = float(row.get(flow_col, 0) or 0)
        spread = float(row.get(spread_col, 0) or 0)
        aum_3m = float(row.get(aum_3m_col, 0) or 0)
        cat = str(row.get("category_display", ""))

        # 1. AUM Level (0-20): log-scale, $500M+ = 20, <$1M = 0
        if aum > 0:
            aum_score = min(20, max(0, round(math.log10(aum + 1) / math.log10(500) * 20, 1)))
        else:
            aum_score = 0

        # 2. Flow Momentum (0-20): flow as % of AUM
        if aum > 0:
            flow_pct = flow_1m / aum * 100
            # +5% or more = 20, 0 = 10, -5% = 0
            flow_score = min(20, max(0, round(10 + flow_pct * 2, 1)))
        else:
            flow_score = 0

        # 3. Spread/Liquidity (0-20): tighter spread = better
        if spread > 0:
            # $0.01 = 20, $0.05 = 12, $0.20 = 0
            spread_score = min(20, max(0, round(20 - (spread / 0.01), 1)))
        else:
            spread_score = 10  # no data = neutral

        # 4. AUM Trend (0-20): 3-month growth
        if aum_3m > 0 and aum > 0:
            growth = (aum - aum_3m) / aum_3m * 100
            # +20% = 20, 0 = 10, -20% = 0
            trend_score = min(20, max(0, round(10 + growth * 0.5, 1)))
        else:
            trend_score = 10  # no data = neutral

        # 5. Size-adjusted bonus (0-20): larger funds get a stability bonus
        if aum >= 100:
            size_score = 20
        elif aum >= 10:
            size_score = round(10 + (aum - 10) / 9, 1)
        else:
            size_score = round(aum, 1)

        total = round(aum_score + flow_score + spread_score + trend_score + size_score)
        total = min(100, max(0, total))

        if total >= 70:
            grade = "GREEN"
        elif total >= 40:
            grade = "AMBER"
        else:
            grade = "RED"

        scores[ticker] = {
            "score": total,
            "grade": grade,
            "aum_score": aum_score,
            "flow_score": flow_score,
            "spread_score": spread_score,
            "trend_score": trend_score,
            "size_score": size_score,
        }

    return scores


def _enrich_summary_with_health(summary: dict, health_scores: dict):
    """Inject health scores into suite product lists."""
    for suite in summary.get("suites", []):
        for product in suite.get("products", []):
            ticker = product.get("ticker", "")
            hs = health_scores.get(ticker)
            if hs:
                product["health_score"] = hs["score"]
                product["health_grade"] = hs["grade"]
            else:
                product["health_score"] = None
                product["health_grade"] = None


@router.get("/rex")
def rex_view(request: Request, product_type: str = Query(default="All"), fund_structure: str = Query(default="ETF"), category: str = Query(default="All")):
    """REX View - executive dashboard by suite."""
    svc = _svc()
    available = svc.data_available()
    # Filter out "Defined Outcome" from categories (no REX products)
    rex_categories = [c for c in svc.ALL_CATEGORIES if c != "Defined Outcome"]
    if not available:
        return templates.TemplateResponse("market/rex.html", {
            "request": request,
            "available": False,
            "active_tab": "rex",
            "categories": rex_categories,
            "data_as_of": svc.get_data_as_of(),
        })
    try:
        cat_arg = category if category != "All" else None
        summary = svc.get_rex_summary(fund_structure=fund_structure, category=cat_arg)

        # Compute and inject health scores
        master = svc.get_master_data()
        health_scores = _compute_health_scores(master)
        _enrich_summary_with_health(summary, health_scores)

        trend = _parse_ts(svc.get_time_series(is_rex=True, category=cat_arg, fund_type=fund_structure))
        # If a specific category is selected, also provide "all REX" trend for overlay
        trend_all = None
        if cat_arg:
            trend_all = _parse_ts(svc.get_time_series(is_rex=True, fund_type=fund_structure))
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
            "data_as_of": svc.get_data_as_of(),
        })
    except Exception as e:
        log.error("REX view error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/rex.html", {
            "request": request,
            "available": False,
            "active_tab": "rex",
            "error": str(e),
            "categories": rex_categories,
            "data_as_of": svc.get_data_as_of(),
        })


@router.get("/category")
def category_view(
    request: Request,
    cat: str = Query(default="All"),
    filters: str = Query(default=None),
    fund_structure: str = Query(default="ETF"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=50, ge=10, le=200),
):
    """Category View - competitive landscape with dynamic filters."""
    svc = _svc()
    available = svc.data_available()

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
            "data_as_of": svc.get_data_as_of(),
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
        summary = svc.get_category_summary(cat_arg, filter_dict, fund_structure=fund_structure, page=page, per_page=per_page)

        # Treemap data for this category
        treemap_data = svc.get_treemap_data(cat_arg, fund_type=fund_structure, filters=filter_dict)

        slicers = svc.get_slicer_options(cat) if cat and cat != "All" else []
        ts_cat = _parse_ts(svc.get_time_series(category=cat_arg, filters=filter_dict))
        ts_rex = _parse_ts(svc.get_time_series(category=cat_arg, is_rex=True, filters=filter_dict))
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
def treemap_view(request: Request, cat: str = Query(default="")):
    """Treemap merged into Category View - redirect there."""
    redirect_url = "/market/category"
    if cat:
        redirect_url += f"?cat={cat}"
    return RedirectResponse(redirect_url, status_code=302)


@router.get("/issuer")
def issuer_view(request: Request, cat: str = Query(default="All"), fund_structure: str = Query(default="ETF")):
    svc = _svc()
    available = svc.data_available()
    if not available:
        return templates.TemplateResponse("market/issuer.html", {
            "request": request, "available": False, "active_tab": "issuer",
            "categories": svc.ALL_CATEGORIES, "data_as_of": svc.get_data_as_of(),
        })
    try:
        cat_arg = cat if cat != "All" else None
        summary = svc.get_issuer_summary(cat_arg, fund_structure=fund_structure)
        # Trend/share data for charts (donut + 12-month trend)
        share_data = {}
        if cat_arg:
            try:
                share_data = svc.get_issuer_share(cat_arg)
            except Exception:
                pass
        return templates.TemplateResponse("market/issuer.html", {
            "request": request, "available": True, "active_tab": "issuer",
            "summary": summary, "categories": svc.ALL_CATEGORIES, "category": cat,
            "fund_structure": fund_structure,
            "share_data": share_data,
            "data_as_of": svc.get_data_as_of(),
        })
    except Exception as e:
        log.error("Issuer view error: %s", e, exc_info=True)
        empty_summary = {"issuers": [], "total_aum": 0, "total_aum_fmt": "$0", "categories": svc.ALL_CATEGORIES}
        return templates.TemplateResponse("market/issuer.html", {
            "request": request, "available": True, "active_tab": "issuer",
            "summary": empty_summary, "categories": svc.ALL_CATEGORIES, "category": cat,
            "error": str(e), "data_as_of": svc.get_data_as_of(),
        })


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
                        total_aum = float(df[aum_col].fillna(0).sum()) if aum_col and aum_col in df.columns else 0

                        # Category breakdown
                        if cat_col and aum_col and aum_col in df.columns:
                            cat_grp = df.groupby(cat_col)[aum_col].sum().reset_index()
                            categories = [{"name": r[cat_col], "aum_fmt": fmt(float(r[aum_col]))}
                                         for _, r in cat_grp.sort_values(aum_col, ascending=False).iterrows()]

                        # Product list
                        products_df = df.sort_values(aum_col, ascending=False) if aum_col and aum_col in df.columns else df
                        for _, row in products_df.iterrows():
                            aum_val = float(row.get(aum_col, 0) or 0) if aum_col and aum_col in df.columns else 0
                            products.append({
                                "ticker": str(row.get("ticker_clean", row.get(ticker_col, ""))),
                                "fund_name": str(row.get(name_col, "")) if name_col else "",
                                "category": str(row.get(cat_col, "")) if cat_col else "",
                                "aum_fmt": fmt(aum_val),
                                "is_rex": bool(row.get("is_rex", False)),
                            })

                        is_rex = bool(df["is_rex"].any()) if "is_rex" in df.columns else False

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
                            if not col_name or col_name not in df.columns:
                                continue
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
            log.error("Issuer detail error: %s", e, exc_info=True)

    error_msg = ""
    if available and issuer and not issuer_data:
        error_msg = f"No data found for issuer '{issuer}'. The issuer name may not match Bloomberg data."

    return templates.TemplateResponse("market/issuer_detail.html", {
        "request": request,
        "active_tab": "issuer",
        "available": available,
        "issuer": issuer,
        "issuer_data": issuer_data,
        "products": products,
        "categories": categories,
        "aum_trend": aum_trend,
        "data_as_of": svc.get_data_as_of() if available else "",
        "error": error_msg,
    })


@router.get("/share")
def share_timeline_view(request: Request, cat: str = Query(default="")):
    """Redirect to merged Issuer Analysis page."""
    redirect_url = "/market/issuer"
    if cat:
        redirect_url += f"?cat={urllib.parse.quote(cat, safe='')}"
    return RedirectResponse(redirect_url, status_code=302)


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


# ---------- Flow Decomposition ----------

_DECOMP_PERIODS = [
    {"key": "1m", "label": "1 Month", "aum_col": "t_w4.aum_1", "flow_col": "t_w4.fund_flow_1month"},
    {"key": "3m", "label": "3 Month", "aum_col": "t_w4.aum_3", "flow_col": "t_w4.fund_flow_3month"},
    {"key": "6m", "label": "6 Month", "aum_col": "t_w4.aum_6", "flow_col": "t_w4.fund_flow_6month"},
    {"key": "1y", "label": "1 Year", "aum_col": "t_w4.aum_12", "flow_col": "t_w4.fund_flow_1year"},
]


def _compute_flow_decomp(df, period: dict) -> list[dict]:
    """Compute per-product flow decomposition for a period."""
    import math
    aum_col = period["aum_col"]
    flow_col = period["flow_col"]

    if aum_col not in df.columns or flow_col not in df.columns:
        return []

    results = []
    for _, row in df.iterrows():
        aum_now = float(row.get("t_w4.aum", 0) or 0)
        aum_prior = float(row.get(aum_col, 0) or 0)
        organic_flow = float(row.get(flow_col, 0) or 0)
        total_change = aum_now - aum_prior
        market_effect = total_change - organic_flow

        if aum_prior == 0 and aum_now == 0:
            continue

        ticker = str(row.get("ticker_clean", row.get("ticker", "")))
        results.append({
            "ticker": ticker,
            "fund_name": str(row.get("fund_name", ""))[:40],
            "category": str(row.get("category_display", "")),
            "issuer": str(row.get("issuer_display", "")),
            "is_rex": bool(row.get("is_rex", False)),
            "aum_prior": round(aum_prior, 2),
            "aum_now": round(aum_now, 2),
            "total_change": round(total_change, 2),
            "organic_flow": round(organic_flow, 2),
            "market_effect": round(market_effect, 2),
            "flow_pct": round(organic_flow / aum_prior * 100, 2) if aum_prior > 0 else 0.0,
            "market_pct": round(market_effect / aum_prior * 100, 2) if aum_prior > 0 else 0.0,
        })
    return results


def _aggregate_decomp(products: list[dict], group_key: str) -> list[dict]:
    """Aggregate decomposition by a group key (category or issuer)."""
    from collections import defaultdict
    groups: dict[str, dict] = {}
    for p in products:
        gk = p.get(group_key, "Unknown") or "Unknown"
        if gk not in groups:
            groups[gk] = {"name": gk, "aum_prior": 0, "aum_now": 0, "organic_flow": 0,
                          "market_effect": 0, "total_change": 0, "count": 0, "has_rex": False}
        g = groups[gk]
        g["aum_prior"] += p["aum_prior"]
        g["aum_now"] += p["aum_now"]
        g["organic_flow"] += p["organic_flow"]
        g["market_effect"] += p["market_effect"]
        g["total_change"] += p["total_change"]
        g["count"] += 1
        if p.get("is_rex"):
            g["has_rex"] = True

    result = []
    for g in groups.values():
        g["aum_prior"] = round(g["aum_prior"], 2)
        g["aum_now"] = round(g["aum_now"], 2)
        g["organic_flow"] = round(g["organic_flow"], 2)
        g["market_effect"] = round(g["market_effect"], 2)
        g["total_change"] = round(g["total_change"], 2)
        g["flow_pct"] = round(g["organic_flow"] / g["aum_prior"] * 100, 2) if g["aum_prior"] > 0 else 0.0
        g["market_pct"] = round(g["market_effect"] / g["aum_prior"] * 100, 2) if g["aum_prior"] > 0 else 0.0
        result.append(g)
    return sorted(result, key=lambda x: abs(x["total_change"]), reverse=True)


@router.get("/flow-decomposition")
def flow_decomposition_view(
    request: Request,
    period: str = Query(default="1m"),
    view: str = Query(default="category"),
    fund_structure: str = Query(default="ETF"),
):
    """Flow Decomposition - separate AUM changes into organic flow vs market performance."""
    svc = _svc()
    available = svc.data_available()
    if not available:
        return templates.TemplateResponse("market/flow_decomposition.html", {
            "request": request, "available": False, "active_tab": "flow",
            "data_as_of": svc.get_data_as_of(),
        })
    try:
        df = svc.get_master_data()
        # Filter by fund structure
        ft_col = next((c for c in df.columns if c.lower().strip() == "fund_type"), None)
        if ft_col and fund_structure != "all":
            df = df[df[ft_col].isin([t.strip() for t in fund_structure.split(",")])]
        # Deduplicate by ticker
        if "ticker_clean" in df.columns:
            df = df.drop_duplicates(subset=["ticker_clean"], keep="first")

        # Find matching period
        period_cfg = next((p for p in _DECOMP_PERIODS if p["key"] == period), _DECOMP_PERIODS[0])

        # Compute per-product decomposition
        products = _compute_flow_decomp(df, period_cfg)

        # Category and issuer aggregates
        by_category = _aggregate_decomp(products, "category")
        by_issuer = _aggregate_decomp(products, "issuer")

        # Overall totals
        total_aum_prior = sum(p["aum_prior"] for p in products)
        total_aum_now = sum(p["aum_now"] for p in products)
        total_organic = sum(p["organic_flow"] for p in products)
        total_market = sum(p["market_effect"] for p in products)
        total_change = total_aum_now - total_aum_prior

        # REX-only totals
        rex_products = [p for p in products if p.get("is_rex")]
        rex_organic = sum(p["organic_flow"] for p in rex_products)
        rex_market = sum(p["market_effect"] for p in rex_products)
        rex_change = sum(p["total_change"] for p in rex_products)

        # Top movers by organic flow (top 10 inflows + top 10 outflows)
        sorted_by_flow = sorted(products, key=lambda x: x["organic_flow"], reverse=True)
        top_inflows = [p for p in sorted_by_flow[:10] if p["organic_flow"] > 0]
        top_outflows = [p for p in sorted_by_flow[-10:] if p["organic_flow"] < 0]
        top_outflows.reverse()

        # Waterfall chart data (category level)
        waterfall_labels = [c["name"][:25] for c in by_category[:8]]
        waterfall_flows = [c["organic_flow"] for c in by_category[:8]]
        waterfall_market = [c["market_effect"] for c in by_category[:8]]

        from webapp.services.market_data import _fmt_currency, _fmt_flow

        return templates.TemplateResponse("market/flow_decomposition.html", {
            "request": request,
            "available": True,
            "active_tab": "flow",
            "period": period,
            "period_label": period_cfg["label"],
            "periods": _DECOMP_PERIODS,
            "view": view,
            "fund_structure": fund_structure,
            "products": sorted(products, key=lambda x: abs(x["total_change"]), reverse=True)[:100],
            "by_category": by_category,
            "by_issuer": by_issuer,
            "total": {
                "aum_prior": round(total_aum_prior, 2),
                "aum_prior_fmt": _fmt_currency(total_aum_prior),
                "aum_now": round(total_aum_now, 2),
                "aum_now_fmt": _fmt_currency(total_aum_now),
                "organic_flow": round(total_organic, 2),
                "organic_flow_fmt": _fmt_flow(total_organic),
                "market_effect": round(total_market, 2),
                "market_effect_fmt": _fmt_flow(total_market),
                "total_change": round(total_change, 2),
                "total_change_fmt": _fmt_flow(total_change),
            },
            "rex_total": {
                "organic_flow": round(rex_organic, 2),
                "organic_flow_fmt": _fmt_flow(rex_organic),
                "market_effect": round(rex_market, 2),
                "market_effect_fmt": _fmt_flow(rex_market),
                "total_change": round(rex_change, 2),
                "total_change_fmt": _fmt_flow(rex_change),
            },
            "top_inflows": top_inflows,
            "top_outflows": top_outflows,
            "waterfall": {
                "labels": waterfall_labels,
                "flows": waterfall_flows,
                "market": waterfall_market,
            },
            "data_as_of": svc.get_data_as_of(),
            "fmt_currency": _fmt_currency,
            "fmt_flow": _fmt_flow,
        })
    except Exception as e:
        log.error("Flow decomposition error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/flow_decomposition.html", {
            "request": request, "available": False, "active_tab": "flow",
            "error": str(e), "data_as_of": svc.get_data_as_of(),
        })


# ---------- White Space Analyzer ----------

def _build_white_space(df) -> dict:
    """Analyze product slots (underlier x direction x leverage) for gaps."""
    import math
    from collections import defaultdict

    li_underlier = "q_category_attributes.map_li_underlier"
    li_direction = "q_category_attributes.map_li_direction"
    li_leverage = "q_category_attributes.map_li_leverage_amount"
    cc_underlier = "q_category_attributes.map_cc_underlier"
    aum_col = "t_w4.aum"

    # --- L&I white space ---
    li_df = df[df[li_underlier].notna() & (df[li_underlier].astype(str).str.strip() != "")].copy() if li_underlier in df.columns else None

    slots = {}  # (underlier, direction, leverage) -> {products, aum, rex_count, ...}
    underlier_totals = defaultdict(lambda: {"aum": 0, "products": 0})

    if li_df is not None and not li_df.empty:
        for _, row in li_df.iterrows():
            underlier = str(row.get(li_underlier, "")).strip()
            direction = str(row.get(li_direction, "Long")).strip()
            try:
                lev_raw = row.get(li_leverage, 2) or 2
                leverage = int(float(str(lev_raw).replace("x", "").replace("X", "").strip() or "2"))
            except (ValueError, TypeError):
                leverage = 2
            aum = float(row.get(aum_col, 0) or 0)
            is_rex = bool(row.get("is_rex", False))
            ticker = str(row.get("ticker_clean", row.get("ticker", "")))

            if not underlier or underlier.upper() in ("NAN", "N/A"):
                continue

            key = (underlier, direction, leverage)
            if key not in slots:
                slots[key] = {"underlier": underlier, "direction": direction, "leverage": leverage,
                              "products": [], "total_aum": 0, "rex_products": [], "rex_aum": 0}
            slots[key]["products"].append(ticker)
            slots[key]["total_aum"] += aum
            if is_rex:
                slots[key]["rex_products"].append(ticker)
                slots[key]["rex_aum"] += aum

            underlier_totals[underlier]["aum"] += aum
            underlier_totals[underlier]["products"] += 1

    # --- Generate gap opportunities ---
    # For each underlier with existing products, check what slots are missing
    all_underliers = set()
    for key in slots:
        all_underliers.add(key[0])

    # Standard leverage/direction combinations to check
    _COMBOS = [
        ("Long", 2), ("Short", 2), ("Long", 3), ("Short", 3), ("Long", 4), ("Short", 4),
    ]

    gaps = []
    filled = []
    for underlier in sorted(all_underliers):
        ut = underlier_totals[underlier]
        for direction, leverage in _COMBOS:
            key = (underlier, direction, leverage)
            slot = slots.get(key)
            has_rex = bool(slot and slot["rex_products"])
            comp_count = len(slot["products"]) if slot else 0
            comp_aum = slot["total_aum"] if slot else 0

            # Opportunity score: higher = better opportunity
            # Factors: underlier total AUM (market size), no REX presence, low competition
            market_size = ut["aum"]
            # Score: market_size * (1 if no REX else 0.2) * (1 / (1 + comp_count))
            rex_factor = 0.2 if has_rex else 1.0
            comp_factor = 1.0 / (1 + comp_count) if not has_rex else 0.3
            score = market_size * rex_factor * comp_factor

            entry = {
                "underlier": underlier,
                "direction": direction,
                "leverage": leverage,
                "slot_label": f"{underlier} {direction} {leverage}x",
                "comp_count": comp_count,
                "comp_aum": round(comp_aum, 2),
                "has_rex": has_rex,
                "rex_tickers": slot["rex_products"] if slot else [],
                "comp_tickers": [t for t in (slot["products"] if slot else []) if t not in (slot["rex_products"] if slot else [])],
                "market_size": round(market_size, 2),
                "score": round(score, 2),
            }

            if has_rex:
                filled.append(entry)
            else:
                gaps.append(entry)

    gaps.sort(key=lambda x: x["score"], reverse=True)
    filled.sort(key=lambda x: x["market_size"], reverse=True)

    # --- Income white space ---
    cc_gaps = []
    if cc_underlier in df.columns:
        cc_df = df[df[cc_underlier].notna() & (df[cc_underlier].astype(str).str.strip() != "")].copy()
        if not cc_df.empty:
            cc_slots = defaultdict(lambda: {"products": [], "aum": 0, "rex_products": [], "rex_aum": 0})
            for _, row in cc_df.iterrows():
                underlier = str(row.get(cc_underlier, "")).strip()
                aum = float(row.get(aum_col, 0) or 0)
                is_rex = bool(row.get("is_rex", False))
                ticker = str(row.get("ticker_clean", ""))
                if not underlier or underlier.upper() in ("NAN", "N/A"):
                    continue
                cc_slots[underlier]["products"].append(ticker)
                cc_slots[underlier]["aum"] += aum
                if is_rex:
                    cc_slots[underlier]["rex_products"].append(ticker)
                    cc_slots[underlier]["rex_aum"] += aum

            for underlier, slot in cc_slots.items():
                has_rex = bool(slot["rex_products"])
                comp_count = len(slot["products"]) - len(slot["rex_products"])
                cc_gaps.append({
                    "underlier": underlier,
                    "comp_count": comp_count,
                    "total_aum": round(slot["aum"], 2),
                    "has_rex": has_rex,
                    "rex_tickers": slot["rex_products"],
                    "score": round(slot["aum"] * (0.2 if has_rex else 1.0), 2),
                })
            cc_gaps.sort(key=lambda x: x["score"], reverse=True)

    # Summary stats
    total_gaps = len([g for g in gaps if g["comp_count"] > 0])
    empty_slots = len([g for g in gaps if g["comp_count"] == 0 and g["market_size"] > 100])
    rex_covered = len(filled)

    return {
        "gaps": gaps[:100],
        "filled": filled,
        "cc_gaps": cc_gaps[:50],
        "total_gaps": total_gaps,
        "empty_slots": empty_slots,
        "rex_covered": rex_covered,
        "total_slots": len(gaps) + len(filled),
        "unique_underliers": len(all_underliers),
    }


@router.get("/whitespace")
def whitespace_view(
    request: Request,
    view: str = Query(default="gaps"),
    fund_structure: str = Query(default="ETF"),
    min_aum: float = Query(default=0),
):
    """White Space Analyzer - find product gaps in the market."""
    svc = _svc()
    available = svc.data_available()
    if not available:
        return templates.TemplateResponse("market/whitespace.html", {
            "request": request, "available": False, "active_tab": "whitespace",
            "data_as_of": svc.get_data_as_of(),
        })
    try:
        df = svc.get_master_data()
        ft_col = next((c for c in df.columns if c.lower().strip() == "fund_type"), None)
        if ft_col and fund_structure != "all":
            df = df[df[ft_col].isin([t.strip() for t in fund_structure.split(",")])]
        if "ticker_clean" in df.columns:
            df = df.drop_duplicates(subset=["ticker_clean"], keep="first")

        ws = _build_white_space(df)

        # Apply min AUM filter
        if min_aum > 0:
            ws["gaps"] = [g for g in ws["gaps"] if g["market_size"] >= min_aum]
            ws["cc_gaps"] = [g for g in ws["cc_gaps"] if g["total_aum"] >= min_aum]

        from webapp.services.market_data import _fmt_currency

        return templates.TemplateResponse("market/whitespace.html", {
            "request": request,
            "available": True,
            "active_tab": "whitespace",
            "view": view,
            "fund_structure": fund_structure,
            "min_aum": min_aum,
            "ws": ws,
            "data_as_of": svc.get_data_as_of(),
            "fmt_currency": _fmt_currency,
        })
    except Exception as e:
        log.error("White space error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/whitespace.html", {
            "request": request, "available": False, "active_tab": "whitespace",
            "error": str(e), "data_as_of": svc.get_data_as_of(),
        })


# ---------- Revenue Model ----------

@router.get("/revenue")
def revenue_view(
    request: Request,
    view: str = Query(default="category"),
    fund_structure: str = Query(default="ETF"),
):
    """Revenue Model - AUM x Expense Ratio = annual revenue."""
    import math
    svc = _svc()
    available = svc.data_available()
    if not available:
        return templates.TemplateResponse("market/revenue.html", {
            "request": request, "available": False, "active_tab": "revenue",
            "data_as_of": svc.get_data_as_of(),
        })
    try:
        df = svc.get_master_data()
        ft_col = next((c for c in df.columns if c.lower().strip() == "fund_type"), None)
        if ft_col and fund_structure != "all":
            df = df[df[ft_col].isin([t.strip() for t in fund_structure.split(",")])]
        if "ticker_clean" in df.columns:
            df = df.drop_duplicates(subset=["ticker_clean"], keep="first")

        from webapp.services.market_data import _fmt_currency

        # Per-product revenue
        products = []
        for _, row in df.iterrows():
            aum = float(row.get("t_w4.aum", 0) or 0)
            er = float(row.get("t_w2.expense_ratio", 0) or 0)
            mgmt_fee = float(row.get("t_w2.management_fee", 0) or 0) if "t_w2.management_fee" in df.columns else 0.0
            if aum <= 0 or er <= 0:
                continue
            revenue = aum * er / 100  # AUM in $M, ER in %, result in $M
            # Flow-adjusted forward projection: annualized 1M flow rate
            flow_1m = float(row.get("t_w4.fund_flow_1month", 0) or 0)
            projected_aum_12m = aum + (flow_1m * 12)
            projected_revenue = max(0, projected_aum_12m) * er / 100

            products.append({
                "ticker": str(row.get("ticker_clean", row.get("ticker", ""))),
                "fund_name": str(row.get("fund_name", ""))[:40],
                "category": str(row.get("category_display", "")),
                "issuer": str(row.get("issuer_display", "")),
                "is_rex": bool(row.get("is_rex", False)),
                "aum": round(aum, 2),
                "expense_ratio": round(er, 2),
                "mgmt_fee": round(mgmt_fee, 2),
                "revenue": round(revenue, 4),
                "revenue_fmt": _fmt_currency(revenue),
                "projected_revenue": round(projected_revenue, 4),
                "projected_fmt": _fmt_currency(projected_revenue),
                # Sensitivity scenarios
                "rev_minus20": round(aum * 0.80 * er / 100, 4),
                "rev_minus10": round(aum * 0.90 * er / 100, 4),
                "rev_plus10": round(aum * 1.10 * er / 100, 4),
                "rev_plus20": round(aum * 1.20 * er / 100, 4),
            })

        # Sort by revenue descending
        products.sort(key=lambda x: x["revenue"], reverse=True)

        # Aggregate by category
        from collections import defaultdict
        cat_agg: dict[str, dict] = {}
        for p in products:
            cat = p["category"] or "Unknown"
            if cat not in cat_agg:
                cat_agg[cat] = {"name": cat, "aum": 0, "revenue": 0, "projected": 0, "count": 0,
                                "has_rex": False, "rex_revenue": 0}
            cat_agg[cat]["aum"] += p["aum"]
            cat_agg[cat]["revenue"] += p["revenue"]
            cat_agg[cat]["projected"] += p["projected_revenue"]
            cat_agg[cat]["count"] += 1
            if p.get("is_rex"):
                cat_agg[cat]["has_rex"] = True
                cat_agg[cat]["rex_revenue"] += p["revenue"]

        by_category = []
        for c in cat_agg.values():
            c["revenue"] = round(c["revenue"], 4)
            c["projected"] = round(c["projected"], 4)
            c["rex_revenue"] = round(c["rex_revenue"], 4)
            c["aum"] = round(c["aum"], 2)
            c["revenue_fmt"] = _fmt_currency(c["revenue"])
            c["projected_fmt"] = _fmt_currency(c["projected"])
            c["rex_revenue_fmt"] = _fmt_currency(c["rex_revenue"])
            c["aum_fmt"] = _fmt_currency(c["aum"])
            c["avg_er"] = round(c["revenue"] / c["aum"] * 100, 2) if c["aum"] > 0 else 0
            c["rex_share_pct"] = round(c["rex_revenue"] / c["revenue"] * 100, 1) if c["revenue"] > 0 else 0
            by_category.append(c)
        by_category.sort(key=lambda x: x["revenue"], reverse=True)

        # Aggregate by issuer
        iss_agg: dict[str, dict] = {}
        for p in products:
            iss = p["issuer"] or "Unknown"
            if iss not in iss_agg:
                iss_agg[iss] = {"name": iss, "aum": 0, "revenue": 0, "projected": 0, "count": 0,
                                "has_rex": False}
            iss_agg[iss]["aum"] += p["aum"]
            iss_agg[iss]["revenue"] += p["revenue"]
            iss_agg[iss]["projected"] += p["projected_revenue"]
            iss_agg[iss]["count"] += 1
            if p.get("is_rex"):
                iss_agg[iss]["has_rex"] = True

        by_issuer = []
        for c in iss_agg.values():
            c["revenue"] = round(c["revenue"], 4)
            c["projected"] = round(c["projected"], 4)
            c["aum"] = round(c["aum"], 2)
            c["revenue_fmt"] = _fmt_currency(c["revenue"])
            c["projected_fmt"] = _fmt_currency(c["projected"])
            c["aum_fmt"] = _fmt_currency(c["aum"])
            c["avg_er"] = round(c["revenue"] / c["aum"] * 100, 2) if c["aum"] > 0 else 0
            by_issuer.append(c)
        by_issuer.sort(key=lambda x: x["revenue"], reverse=True)

        # Totals
        total_revenue = sum(p["revenue"] for p in products)
        total_projected = sum(p["projected_revenue"] for p in products)
        total_aum = sum(p["aum"] for p in products)
        rex_products = [p for p in products if p.get("is_rex")]
        rex_revenue = sum(p["revenue"] for p in rex_products)
        rex_projected = sum(p["projected_revenue"] for p in rex_products)
        rex_aum = sum(p["aum"] for p in rex_products)

        # Chart data: top 10 categories by revenue
        chart_labels = [c["name"][:25] for c in by_category[:10]]
        chart_revenue = [c["revenue"] for c in by_category[:10]]
        chart_rex = [c["rex_revenue"] for c in by_category[:10]]

        return templates.TemplateResponse("market/revenue.html", {
            "request": request,
            "available": True,
            "active_tab": "revenue",
            "view": view,
            "fund_structure": fund_structure,
            "products": products[:100],
            "by_category": by_category,
            "by_issuer": by_issuer[:20],
            "total": {
                "aum": round(total_aum, 2),
                "aum_fmt": _fmt_currency(total_aum),
                "revenue": round(total_revenue, 4),
                "revenue_fmt": _fmt_currency(total_revenue),
                "projected": round(total_projected, 4),
                "projected_fmt": _fmt_currency(total_projected),
                "avg_er": round(total_revenue / total_aum * 100, 2) if total_aum > 0 else 0,
                "count": len(products),
            },
            "rex": {
                "aum": round(rex_aum, 2),
                "aum_fmt": _fmt_currency(rex_aum),
                "revenue": round(rex_revenue, 4),
                "revenue_fmt": _fmt_currency(rex_revenue),
                "projected": round(rex_projected, 4),
                "projected_fmt": _fmt_currency(rex_projected),
                "avg_er": round(rex_revenue / rex_aum * 100, 2) if rex_aum > 0 else 0,
                "count": len(rex_products),
                "share_pct": round(rex_revenue / total_revenue * 100, 1) if total_revenue > 0 else 0,
            },
            "chart": {
                "labels": chart_labels,
                "revenue": chart_revenue,
                "rex": chart_rex,
            },
            "data_as_of": svc.get_data_as_of(),
            "fmt_currency": _fmt_currency,
        })
    except Exception as e:
        log.error("Revenue view error: %s", e, exc_info=True)
        return templates.TemplateResponse("market/revenue.html", {
            "request": request, "available": False, "active_tab": "revenue",
            "error": str(e), "data_as_of": svc.get_data_as_of(),
        })


#  API endpoints (AJAX)

@router.get("/api/rex-summary")
def api_rex_summary():
    try:
        svc = _svc()
        return JSONResponse(svc.get_rex_summary())
    except Exception as e:
        log.error("Request failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


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
        log.error("Request failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


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
        log.error("Request failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/api/slicers/{category:path}")
def api_slicers(category: str):
    try:
        svc = _svc()
        return JSONResponse(svc.get_slicer_options(category))
    except Exception as e:
        log.error("Request failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/api/treemap")
def api_treemap(category: str = Query(default="All")):
    try:
        svc = _svc()
        cat = category if category != "All" else None
        return JSONResponse(svc.get_treemap_data(cat))
    except Exception as e:
        log.error("Request failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/api/issuer")
def api_issuer(category: str = Query(default="All")):
    try:
        svc = _svc()
        cat = category if category != "All" else None
        return JSONResponse(svc.get_issuer_summary(cat))
    except Exception as e:
        log.error("Request failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/api/share")
def api_share():
    try:
        return JSONResponse(_svc().get_market_share_timeline())
    except Exception as e:
        log.error("Request failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Internal server error"}, status_code=500)


@router.get("/api/underlier")
def api_underlier(type: str = Query(default="income"), underlier: str = Query(default=None)):
    try:
        return JSONResponse(_svc().get_underlier_summary(type, underlier))
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
        return JSONResponse({"status": "ok"})
    except Exception as e:
        log.error("Cache invalidation failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Cache invalidation failed"}, status_code=500)
