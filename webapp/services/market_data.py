"""
Market Intelligence data loader and cache.

Loads data/DASHBOARD/The Dashboard.xlsx and provides:
- get_master_data()          -> Full fund universe (q_master_data)
- get_rex_summary()          -> REX totals + per-suite breakdown
- get_category_summary()     -> Category totals, REX share, top products
- get_time_series()          -> AUM time series for charts
- get_kpis()                 -> Calculate standard KPIs from a dataframe
- get_slicer_options()       -> Available filter values for a category
- invalidate_cache()         -> Clear cache (e.g. after data file update)
"""
from __future__ import annotations

import json
import logging
import math
import re
import threading
import time
from pathlib import Path
from typing import Any

from datetime import datetime as _dt

import pandas as pd

log = logging.getLogger(__name__)

_REX_SUITE_NAMES = {
    "Leverage & Inverse - Single Stock": "T-REX",
    "Leverage & Inverse - Index/Basket/ETF Based": "MicroSector",
    "Income - Single Stock": "Growth & Income",
    "Income - Index/Basket/ETF Based": "Premium Income",
    "Crypto": "Crypto",
    "Defined Outcome": "Defined Outcome",
    "Thematic": "Thematic",
    "Leverage & Inverse - Unknown/Miscellaneous": "L&I Other",
}

_LOCAL_DATA = Path(r"C:\Users\RyuEl-Asmar\REX Financial LLC\REX Financial LLC - Rex Financial LLC\Product Development\MasterFiles\MASTER Data\The Dashboard.xlsx")
_FALLBACK_DATA = Path("data/DASHBOARD/The Dashboard.xlsx")
DATA_FILE = _LOCAL_DATA if _LOCAL_DATA.exists() else _FALLBACK_DATA

#  Cache 
_lock = threading.Lock()
_cache: dict[str, Any] = {}
_cache_time: float = 0.0
_CACHE_TTL = 3600  # 1 hour


def _is_fresh() -> bool:
    return bool(_cache) and (time.time() - _cache_time) < _CACHE_TTL


def invalidate_cache() -> None:
    global _cache, _cache_time
    with _lock:
        _cache = {}
        _cache_time = 0.0
    log.info("Market data cache invalidated")


def get_data_as_of() -> str:
    """Return file modification date as 'Feb 20, 2026' or empty string."""
    try:
        return _dt.fromtimestamp(DATA_FILE.stat().st_mtime).strftime("%b %d, %Y")
    except Exception:
        return ""


def _load_fresh() -> dict[str, Any]:
    """Load all required sheets from Excel."""
    log.info("Loading The Dashboard.xlsx ")
    master = pd.read_excel(DATA_FILE, sheet_name="q_master_data", engine="openpyxl")
    ts = pd.read_excel(DATA_FILE, sheet_name="q_aum_time_series_labeled", engine="openpyxl")

    # Normalise booleans that may come in as object columns
    for col in ("is_rex",):
        if col in master.columns:
            master[col] = master[col].fillna(False).astype(bool)
    if "is_rex" in ts.columns:
        ts["is_rex"] = ts["is_rex"].fillna(False).astype(bool)

    # Numeric coercions for metric columns
    _NUMERIC = [
        "t_w4.aum",
        "t_w4.fund_flow_1day", "t_w4.fund_flow_1week",
        "t_w4.fund_flow_1month", "t_w4.fund_flow_3month",
        "t_w4.fund_flow_6month", "t_w4.fund_flow_ytd",
        "t_w3.total_return_1week", "t_w3.total_return_1month",
        "t_w3.annualized_yield",
        "t_w2.expense_ratio",
    ] + [f"t_w4.aum_{i}" for i in range(1, 37)]

    for col in _NUMERIC:
        if col in master.columns:
            master[col] = pd.to_numeric(master[col], errors="coerce").fillna(0.0)

    # Strip " US" suffix from tickers for clean matching
    ticker_col = next((c for c in master.columns if c.lower().strip() == "ticker"), None)
    if ticker_col:
        master["ticker_clean"] = master[ticker_col].str.replace(r"\s+US$", "", regex=True)
    else:
        master["ticker_clean"] = ""

    if "aum_value" in ts.columns:
        ts["aum_value"] = pd.to_numeric(ts["aum_value"], errors="coerce").fillna(0.0)

    # Date normalisation for time series
    if "date" in ts.columns:
        ts["date"] = pd.to_datetime(ts["date"], errors="coerce")

    log.info("Loaded %d funds, %d time-series rows", len(master), len(ts))
    return {"master": master, "ts": ts}


def _get_cache() -> dict[str, Any]:
    global _cache, _cache_time
    with _lock:
        if _is_fresh():
            return _cache
    data = _load_fresh()
    with _lock:
        _cache = data
        _cache_time = time.time()
    return data


#  Public helpers 

def data_available() -> bool:
    return DATA_FILE.exists()


def get_master_data() -> pd.DataFrame:
    return _get_cache()["master"].copy()


def get_time_series_df() -> pd.DataFrame:
    return _get_cache()["ts"].copy()


def _fmt_currency(val: float) -> str:
    """Format a value in millions: returns '$X.XB' or '$X.XM'."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "$0"
    if abs(val) >= 1_000:
        return f"${val/1_000:.1f}B"
    if abs(val) >= 1:
        return f"${val:.1f}M"
    return f"${val:.2f}M"


def _fmt_flow(val: float) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}{_fmt_currency(val)}"


def get_kpis(df: pd.DataFrame) -> dict:
    """Calculate standard KPIs from a filtered dataframe (values in $M)."""
    total_aum = float(df["t_w4.aum"].sum()) if "t_w4.aum" in df.columns else 0.0
    flow_1w = float(df["t_w4.fund_flow_1week"].sum()) if "t_w4.fund_flow_1week" in df.columns else 0.0
    flow_1m = float(df["t_w4.fund_flow_1month"].sum()) if "t_w4.fund_flow_1month" in df.columns else 0.0
    flow_3m = float(df["t_w4.fund_flow_3month"].sum()) if "t_w4.fund_flow_3month" in df.columns else 0.0
    count = int(len(df))
    aum_fmt = _fmt_currency(total_aum)
    return {
        "total_aum": total_aum,
        "total_aum_fmt": aum_fmt,
        "aum_fmt": aum_fmt,          # alias for templates/JS
        "flow_1w": flow_1w,
        "flow_1w_fmt": _fmt_flow(flow_1w),
        "flow_1m": flow_1m,
        "flow_1m_fmt": _fmt_flow(flow_1m),
        "flow_3m": flow_3m,
        "flow_3m_fmt": _fmt_flow(flow_3m),
        "count": count,
        "num_products": count,       # alias for templates/JS
        "flow_1w_positive": flow_1w >= 0,
        "flow_1m_positive": flow_1m >= 0,
        "flow_3m_positive": flow_3m >= 0,
    }


#  REX Summary 

_SUITE_ORDER = [
    "Leverage & Inverse - Single Stock",
    "Leverage & Inverse - Index/Basket/ETF Based",
    "Crypto",
    "Income - Single Stock",
    "Income - Index/Basket/ETF Based",
    "Thematic",
]


def get_rex_summary(fund_structure: str | None = None) -> dict:
    """Return REX overall KPIs + per-suite breakdown."""
    df = get_master_data()

    # ETF/ETN filter
    if fund_structure and fund_structure != "all":
        fund_type_col = next((c for c in df.columns if c.lower().strip() == "fund_type"), None)
        if fund_type_col:
            df = df[df[fund_type_col] == fund_structure].copy()

    all_cats = df[df["category_display"].notna()].copy()
    rex = df[df["is_rex"] == True].copy()

    overall = get_kpis(rex)

    suites = []
    for suite_name in _SUITE_ORDER:
        rex_suite = rex[rex["category_display"] == suite_name]
        if rex_suite.empty:
            continue
        cat_suite = all_cats[all_cats["category_display"] == suite_name]
        cat_aum = float(cat_suite["t_w4.aum"].sum())
        rex_aum = float(rex_suite["t_w4.aum"].sum())
        market_share = (rex_aum / cat_aum * 100) if cat_aum > 0 else 0.0

        # Top movers by 1-week flow
        sorted_suite = rex_suite.sort_values("t_w4.fund_flow_1week", ascending=False)
        top_movers = []
        for _, row in sorted_suite.head(5).iterrows():
            flow = float(row.get("t_w4.fund_flow_1week", 0))
            if flow == 0:
                continue
            fmt = _fmt_flow(flow)
            top_movers.append({
                "ticker": str(row.get("ticker", "")),
                "fund_name": str(row.get("fund_name", "")),
                "flow_1w": flow,
                "flow_1w_fmt": fmt,
                "flow": fmt,       # alias for _suite_card.html
                "flow_raw": flow,  # alias for _suite_card.html
                "positive": flow >= 0,
            })
        # Add bottom movers if not already there
        for _, row in sorted_suite.tail(3).iterrows():
            flow = float(row.get("t_w4.fund_flow_1week", 0))
            ticker = str(row.get("ticker", ""))
            if flow == 0 or any(m["ticker"] == ticker for m in top_movers):
                continue
            fmt = _fmt_flow(flow)
            top_movers.append({
                "ticker": ticker,
                "fund_name": str(row.get("fund_name", "")),
                "flow_1w": flow,
                "flow_1w_fmt": fmt,
                "flow": fmt,       # alias for _suite_card.html
                "flow_raw": flow,  # alias for _suite_card.html
                "positive": flow >= 0,
            })

        kpis = get_kpis(rex_suite)

        # Sparkline: last 4 months of REX AUM in this suite (oldest to newest)
        sparkline = []
        for col in ["t_w4.aum_4", "t_w4.aum_3", "t_w4.aum_2", "t_w4.aum_1"]:
            if col in rex_suite.columns:
                sparkline.append(round(float(rex_suite[col].sum()), 2))
            else:
                sparkline.append(0.0)

        # Top 50 products in this suite by AUM
        suite_products = []
        top_suite = rex_suite.nlargest(min(50, len(rex_suite)), "t_w4.aum")
        for _, row in top_suite.iterrows():
            p_ticker = str(row.get("ticker_clean", row.get("ticker", "")))
            p_aum = float(row.get("t_w4.aum", 0) or 0)
            p_flow_1w = float(row.get("t_w4.fund_flow_1week", 0) or 0)
            p_er = row.get("t_w2.expense_ratio")
            suite_products.append({
                "ticker": p_ticker,
                "fund_name": str(row.get("fund_name", "")),
                "aum_fmt": _fmt_currency(p_aum),
                "flow_1w_fmt": _fmt_flow(p_flow_1w),
                "expense_ratio_fmt": f"{p_er:.2f}%" if p_er is not None and not (isinstance(p_er, float) and math.isnan(p_er)) else "",
                "is_rex": bool(row.get("is_rex", False)),
            })

        suites.append({
            "name": suite_name,
            "rex_name": _REX_SUITE_NAMES.get(suite_name, suite_name),
            "short_name": _suite_short(suite_name),
            "kpis": kpis,
            "market_share": round(market_share, 1),
            "market_share_fmt": f"{market_share:.1f}%",
            "total_aum_fmt": _fmt_currency(rex_aum),
            "top_movers": top_movers,
            "products": suite_products,
            "category_param": suite_name,  # for link to category view
            "sparkline_data": sparkline,  # oldest to newest
        })

    # Chart data: AUM by suite for pie chart
    pie_labels = [s["short_name"] for s in suites]
    pie_values = [round(s["kpis"]["total_aum"], 2) for s in suites]

    return {
        "kpis": overall,     # renamed: templates use summary.kpis
        "overall": overall,  # kept for backwards compat
        "suites": suites,
        "pie_labels": json.dumps(pie_labels),
        "pie_values": json.dumps(pie_values),
        "pie_data": {"labels": pie_labels, "values": pie_values},  # for templates
    }


def _suite_short(name: str) -> str:
    mapping = {
        "Leverage & Inverse - Single Stock": "L&I Single Stock",
        "Leverage & Inverse - Index/Basket/ETF Based": "L&I Index/ETF",
        "Crypto": "Crypto",
        "Income - Single Stock": "Income Single Stock",
        "Income - Index/Basket/ETF Based": "Income Index/ETF",
        "Thematic": "Thematic",
        "Defined Outcome": "Defined Outcome",
    }
    return mapping.get(name, name)


#  Category Summary 

_CATEGORY_SLICERS: dict[str, list[dict]] = {
    "Crypto": [
        {"field": "q_category_attributes.map_crypto_is_spot", "label": "Type"},
        {"field": "q_category_attributes.map_crypto_underlier", "label": "Underlier"},
    ],
    "Income - Single Stock": [
        {"field": "q_category_attributes.map_cc_underlier", "label": "Underlier"},
    ],
    "Income - Index/Basket/ETF Based": [
        {"field": "q_category_attributes.map_cc_index", "label": "Index"},
    ],
    "Leverage & Inverse - Single Stock": [
        {"field": "q_category_attributes.map_li_direction", "label": "Direction"},
        {"field": "q_category_attributes.map_li_leverage_amount", "label": "Leverage"},
        {"field": "q_category_attributes.map_li_underlier", "label": "Underlier"},
    ],
    "Leverage & Inverse - Index/Basket/ETF Based": [
        {"field": "q_category_attributes.map_li_direction", "label": "Direction"},
        {"field": "q_category_attributes.map_li_leverage_amount", "label": "Leverage"},
        {"field": "q_category_attributes.map_li_category", "label": "Asset Class"},
    ],
    "Defined Outcome": [
        {"field": "q_category_attributes.map_defined_category", "label": "Outcome Type"},
    ],
    "Thematic": [
        {"field": "q_category_attributes.map_thematic_category", "label": "Theme"},
    ],
}

ALL_CATEGORIES = [
    "Leverage & Inverse - Single Stock",
    "Leverage & Inverse - Index/Basket/ETF Based",
    "Crypto",
    "Income - Single Stock",
    "Income - Index/Basket/ETF Based",
    "Defined Outcome",
    "Thematic",
]


def get_slicer_options(category: str) -> list[dict]:
    """Return slicer definitions + current values for a category."""
    df = get_master_data()
    slicers = _CATEGORY_SLICERS.get(category, [])
    result = []
    for s in slicers:
        field = s["field"]
        if field not in df.columns:
            continue
        cat_df = df[df["category_display"] == category] if category else df
        options = sorted(
            [str(v) for v in cat_df[field].dropna().unique() if str(v).strip()]
        )
        result.append({
            "field": field,
            "label": s["label"],
            "options": options,
        })
    return result


def get_category_summary(category: str | None, filters: dict | None = None, fund_structure: str | None = None) -> dict:
    """Return category totals, REX share, top products, issuer breakdown."""
    df = get_master_data()

    # ETF/ETN filter
    if fund_structure and fund_structure != "all":
        fund_type_col = next((c for c in df.columns if c.lower().strip() == "fund_type"), None)
        if fund_type_col:
            df = df[df[fund_type_col] == fund_structure].copy()

    # Filter by category
    if category and category != "All":
        df = df[df["category_display"] == category].copy()
    else:
        df = df[df["category_display"].notna()].copy()

    # Apply dynamic slicers
    if filters:
        for field, value in filters.items():
            if field in df.columns and value:
                if isinstance(value, list):
                    df = df[df[field].isin(value)]
                else:
                    df = df[df[field] == value]

    rex_df = df[df["is_rex"] == True]
    non_rex_df = df[df["is_rex"] == False]

    cat_kpis = get_kpis(df)
    rex_kpis = get_kpis(rex_df)

    cat_aum = cat_kpis["total_aum"]
    rex_aum = rex_kpis["total_aum"]
    market_share = (rex_aum / cat_aum * 100) if cat_aum > 0 else 0.0

    # Top products table (sorted by AUM)
    top_df = df.sort_values("t_w4.aum", ascending=False).head(50)
    top_products = []
    for rank, (_, row) in enumerate(top_df.iterrows(), 1):
        aum = float(row.get("t_w4.aum", 0))
        flow_1w = float(row.get("t_w4.fund_flow_1week", 0))
        flow_1m = float(row.get("t_w4.fund_flow_1month", 0))
        raw_yield = row.get("t_w3.annualized_yield")
        try:
            yield_val = float(raw_yield) if raw_yield is not None and not (isinstance(raw_yield, float) and math.isnan(raw_yield)) else None
        except (TypeError, ValueError):
            yield_val = None
        top_products.append({
            "rank": rank,
            "ticker": str(row.get("ticker", "")),
            "fund_name": str(row.get("fund_name", "")),
            "issuer": str(row.get("issuer_display", "")),
            "aum": aum,
            "aum_fmt": _fmt_currency(aum),
            "flow_1w": flow_1w,
            "flow_1w_fmt": _fmt_flow(flow_1w),
            "flow_1w_positive": flow_1w >= 0,
            "flow_1m": flow_1m,
            "flow_1m_fmt": _fmt_flow(flow_1m),
            "flow_1m_positive": flow_1m >= 0,
            "yield_val": yield_val,  # raw float for template formatting
            "yield_fmt": f"{yield_val:.2f}%" if yield_val is not None else "",
            "is_rex": bool(row.get("is_rex", False)),
            "category": str(row.get("category_display", "")),
        })

    # REX products in this category (full list)
    rex_products = []
    rex_sorted = rex_df.sort_values("t_w4.aum", ascending=False)
    for _, row in rex_sorted.iterrows():
        aum = float(row.get("t_w4.aum", 0))
        flow_1w = float(row.get("t_w4.fund_flow_1week", 0))
        flow_1m = float(row.get("t_w4.fund_flow_1month", 0))
        flow_3m = float(row.get("t_w4.fund_flow_3month", 0))
        # Rank in full category
        rank_in_cat = int((df["t_w4.aum"] > aum).sum()) + 1
        raw_yield_r = row.get("t_w3.annualized_yield")
        try:
            yield_val_r = float(raw_yield_r) if raw_yield_r is not None and not (isinstance(raw_yield_r, float) and math.isnan(raw_yield_r)) else None
        except (TypeError, ValueError):
            yield_val_r = None
        rex_products.append({
            "ticker": str(row.get("ticker", "")),
            "fund_name": str(row.get("fund_name", "")),
            "aum": aum,
            "aum_fmt": _fmt_currency(aum),
            "flow_1w": flow_1w,
            "flow_1w_fmt": _fmt_flow(flow_1w),
            "flow_1w_positive": flow_1w >= 0,
            "flow_1m": flow_1m,
            "flow_1m_fmt": _fmt_flow(flow_1m),
            "flow_1m_positive": flow_1m >= 0,
            "flow_3m": flow_3m,
            "flow_3m_fmt": _fmt_flow(flow_3m),
            "flow_3m_positive": flow_3m >= 0,
            "rank_in_cat": rank_in_cat,
            "rank": rank_in_cat,  # alias for templates
            "yield_val": yield_val_r,
            "yield_fmt": f"{yield_val_r:.2f}%" if yield_val_r is not None else "",
        })

    # Issuer breakdown (for bar chart)
    issuer_aum = (
        df.groupby("issuer_display")["t_w4.aum"]
        .sum()
        .sort_values(ascending=False)
        .head(15)
    )
    issuer_labels = [str(i) for i in issuer_aum.index.tolist()]
    issuer_values = [round(float(v), 2) for v in issuer_aum.values.tolist()]
    # Mark REX in issuer list
    rex_issuers = set(rex_df["issuer_display"].dropna().unique())
    issuer_is_rex = [lbl in rex_issuers for lbl in issuer_labels]

    return {
        "category": category or "All",
        "cat_kpis": cat_kpis,
        "rex_kpis": rex_kpis,
        "market_share": round(market_share, 1),
        "market_share_fmt": f"{market_share:.1f}%",
        "rex_share": round(market_share, 1),  # alias for templates
        "top_products": top_products,
        "rex_products": rex_products,
        "issuer_labels": json.dumps(issuer_labels),
        "issuer_values": json.dumps(issuer_values),
        "issuer_is_rex": json.dumps(issuer_is_rex),
        "issuer_data": {  # structured dict for templates/JS
            "labels": issuer_labels,
            "values": issuer_values,
            "is_rex": issuer_is_rex,
        },
        "total_funds": len(df),
    }


#  Treemap

def get_treemap_data(category: str | None = None) -> dict:
    """Return product list for treemap rendering (top 200 by AUM)."""
    df = get_master_data()
    if category and category != "All":
        df = df[df["category_display"] == category].copy()
    else:
        df = df[df["category_display"].notna()].copy()

    # Deduplicate on ticker within filtered df
    ticker_col = next((c for c in df.columns if c.lower().strip() == "ticker"), None)
    if ticker_col:
        df = df.drop_duplicates(subset=[ticker_col], keep="first")

    df = df.sort_values("t_w4.aum", ascending=False).head(200)

    try:
        products = []
        for _, row in df.iterrows():
            aum = float(row.get("t_w4.aum", 0))
            products.append({
                "label": str(row.get("ticker_clean", row.get("ticker", ""))),
                "value": round(aum, 2),
                "group": str(row.get("category_display", "Other")),
                "is_rex": bool(row.get("is_rex", False)),
                "ticker": str(row.get("ticker_clean", row.get("ticker", ""))),
                "fund_name": str(row.get("fund_name", "")),
                "issuer": str(row.get("issuer_display", "")),
                "aum_fmt": _fmt_currency(aum),
            })
    except Exception as e:
        log.error("Treemap product build error: %s", e)
        products = []

    total = float(df["t_w4.aum"].sum()) if not df.empty else 0.0
    return {
        "products": products,
        "total_aum": round(total, 2),
        "total_aum_fmt": _fmt_currency(total) if products else "N/A",
        "categories": ALL_CATEGORIES,
    }


#  Issuer Summary

def get_issuer_summary(category: str | None = None, fund_structure: str | None = None) -> dict:
    """Return per-issuer AUM, flows, product count, market share."""
    df = get_master_data()
    if category and category != "All":
        df = df[df["category_display"] == category].copy()
    else:
        df = df[df["category_display"].notna()].copy()

    # ETF/ETN filter
    if fund_structure and fund_structure != "all":
        fund_type_col = next((c for c in df.columns if c.lower().strip() == "fund_type"), None)
        if fund_type_col:
            df = df[df[fund_type_col] == fund_structure].copy()

    # Replace null issuer_display with "Unknown"
    df["issuer_display"] = df["issuer_display"].fillna("Unknown")

    total_aum = float(df["t_w4.aum"].sum()) if not df.empty else 0.0

    # Identify REX issuers
    rex_issuers = set(df[df["is_rex"] == True]["issuer_display"].dropna().unique())

    try:
        grouped = df.groupby("issuer_display")
        issuers = []
        for issuer_name, grp in grouped:
            aum = float(grp["t_w4.aum"].sum())
            flow_1w = float(grp["t_w4.fund_flow_1week"].sum()) if "t_w4.fund_flow_1week" in grp.columns else 0.0
            flow_1m = float(grp["t_w4.fund_flow_1month"].sum()) if "t_w4.fund_flow_1month" in grp.columns else 0.0
            share = (aum / total_aum * 100) if total_aum > 0 else 0.0
            num_products = int(len(grp))
            if num_products == 0:
                continue
            issuers.append({
                "issuer_name": str(issuer_name),
                "total_aum": aum,
                "aum_fmt": _fmt_currency(aum),
                "flow_1w": flow_1w,
                "flow_1w_fmt": _fmt_flow(flow_1w),
                "flow_1m": flow_1m,
                "flow_1m_fmt": _fmt_flow(flow_1m),
                "num_products": num_products,
                "market_share_pct": round(share, 1),
                "is_rex": str(issuer_name) in rex_issuers,
            })
    except Exception as e:
        log.error("Issuer groupby error: %s", e)
        issuers = []

    issuers.sort(key=lambda x: x["total_aum"], reverse=True)
    return {
        "issuers": issuers,
        "total_aum": round(total_aum, 2),
        "total_aum_fmt": _fmt_currency(total_aum),
        "categories": ALL_CATEGORIES,
    }


#  Market Share Timeline

def get_market_share_timeline() -> dict:
    """Return monthly category market share % over last 24 months."""
    ts = get_time_series_df()

    if ts.empty or "date" not in ts.columns or "category_display" not in ts.columns:
        return {"labels": [], "series": []}

    ts = ts.dropna(subset=["date", "category_display"]).copy()

    # Aggregate: for each (date, category), sum aum_value
    agg = (
        ts.groupby(["date", "category_display"])["aum_value"]
        .sum()
        .reset_index()
        .sort_values("date")
    )

    # Keep last 24 months
    dates = sorted(agg["date"].unique())
    if len(dates) > 24:
        dates = dates[-24:]
    agg = agg[agg["date"].isin(dates)]

    labels = [d.strftime("%b %Y") for d in dates]

    # For each date, compute total AUM across all categories
    date_totals = agg.groupby("date")["aum_value"].sum()

    series = []
    for cat in ALL_CATEGORIES:
        cat_data = agg[agg["category_display"] == cat].set_index("date")
        values = []
        for d in dates:
            cat_aum = float(cat_data.loc[d, "aum_value"]) if d in cat_data.index else 0.0
            total = float(date_totals.get(d, 1.0))
            pct = round(cat_aum / total * 100, 1) if total > 0 else 0.0
            values.append(pct)
        series.append({
            "name": cat,
            "short_name": _suite_short(cat),
            "values": values,
        })

    return {"labels": labels, "series": series}


#  Issuer Market Share (per category)

def get_issuer_share(cat: str) -> dict:
    """Issuer market share within a specific category."""
    master = get_master_data()
    if master.empty:
        return {}

    df = master[master["category_display"] == cat].copy() if cat else master.copy()
    if df.empty:
        return {}

    total_aum = float(df["t_w4.aum"].sum())

    # Identify REX issuers
    rex_issuers = set(df[df["is_rex"] == True]["issuer_display"].dropna().unique())

    # Replace null issuer_display
    df["issuer_display"] = df["issuer_display"].fillna("Unknown")

    grouped = df.groupby("issuer_display")["t_w4.aum"].sum().sort_values(ascending=False)
    issuers = []
    for issuer_name, aum in grouped.items():
        aum_val = float(aum)
        pct = (aum_val / total_aum * 100) if total_aum > 0 else 0.0
        num_prods = int(len(df[df["issuer_display"] == issuer_name]))
        issuers.append({
            "name": str(issuer_name),
            "aum": aum_val,
            "aum_fmt": _fmt_currency(aum_val),
            "pct": round(pct, 1),
            "is_rex": str(issuer_name) in rex_issuers,
            "num_products": num_prods,
        })

    # Trend: top 5 issuers, last 12 months from time series
    top_5_issuers = [i["name"] for i in issuers[:5]]
    ts = get_time_series_df()
    trend = {"months": [], "series": []}
    if not ts.empty and "date" in ts.columns and "issuer_display" in ts.columns:
        ts_cat = ts[ts["category_display"] == cat].copy() if cat else ts.copy()
        ts_cat = ts_cat.dropna(subset=["date", "issuer_display"])
        if not ts_cat.empty:
            dates = sorted(ts_cat["date"].unique())
            if len(dates) > 12:
                dates = dates[-12:]
            ts_cat = ts_cat[ts_cat["date"].isin(dates)]
            trend["months"] = [d.strftime("%b %Y") for d in dates]
            for issuer_name in top_5_issuers:
                issuer_ts = ts_cat[ts_cat["issuer_display"] == issuer_name]
                values = []
                for d in dates:
                    val = float(issuer_ts[issuer_ts["date"] == d]["aum_value"].sum())
                    values.append(round(val, 2))
                trend["series"].append({
                    "issuer": issuer_name,
                    "values": values,
                    "is_rex": issuer_name in rex_issuers,
                })

    return {
        "category": cat,
        "total_aum": total_aum,
        "total_aum_fmt": _fmt_currency(total_aum),
        "issuers": issuers,
        "trend": trend,
    }


#  Underlier Deep-Dive

def get_underlier_summary(underlier_type: str = "income", underlier: str | None = None) -> dict:
    """Return underlier-level stats for covered call (income) or L&I single stock."""
    df = get_master_data()

    if underlier_type == "income":
        cat_filter = "Income - Single Stock"
        field = "q_category_attributes.map_cc_underlier"
    else:
        cat_filter = "Leverage & Inverse - Single Stock"
        field = "q_category_attributes.map_li_underlier"

    df = df[df["category_display"] == cat_filter].copy()

    if field not in df.columns:
        # Field missing - return empty
        return {"underliers": [], "products": [], "underlier_type": underlier_type, "selected": underlier}

    if underlier is None:
        # Return list of underliers with aggregated stats
        grouped = df.groupby(field)
        underliers = []
        for ul_name, grp in grouped:
            if not str(ul_name).strip():
                continue
            aum = float(grp["t_w4.aum"].sum())
            rex_count = int(grp["is_rex"].sum())
            underliers.append({
                "name": str(ul_name),
                "aum": aum,
                "aum_fmt": _fmt_currency(aum),
                "num_products": int(len(grp)),
                "num_rex": rex_count,
            })
        underliers.sort(key=lambda x: x["aum"], reverse=True)
        return {"underliers": underliers, "products": [], "underlier_type": underlier_type, "selected": None}
    else:
        # Return products for this underlier
        sub = df[df[field] == underlier].copy()
        total_underlier_aum = float(sub["t_w4.aum"].sum()) if not sub.empty else 0.0
        products = []
        for _, row in sub.sort_values("t_w4.aum", ascending=False).iterrows():
            aum = float(row.get("t_w4.aum", 0))
            flow_1w = float(row.get("t_w4.fund_flow_1week", 0))
            flow_1m = float(row.get("t_w4.fund_flow_1month", 0))
            flow_3m = float(row.get("t_w4.fund_flow_3month", 0))
            raw_yield = row.get("t_w3.annualized_yield")
            raw_er = row.get("t_w2.expense_ratio")
            raw_ret_1m = row.get("t_w3.total_return_1month")
            try:
                yield_val = float(raw_yield) if raw_yield is not None and not (isinstance(raw_yield, float) and math.isnan(raw_yield)) else None
            except (TypeError, ValueError):
                yield_val = None
            try:
                er_val = float(raw_er) if raw_er is not None and not (isinstance(raw_er, float) and math.isnan(raw_er)) else None
            except (TypeError, ValueError):
                er_val = None
            try:
                ret_1m_val = float(raw_ret_1m) if raw_ret_1m is not None and not (isinstance(raw_ret_1m, float) and math.isnan(raw_ret_1m)) else None
            except (TypeError, ValueError):
                ret_1m_val = None
            mkt_share = (aum / total_underlier_aum * 100) if total_underlier_aum > 0 else 0.0
            products.append({
                "ticker": str(row.get("ticker", "")),
                "fund_name": str(row.get("fund_name", "")),
                "direction": str(row.get("q_category_attributes.map_li_direction", "")) if underlier_type == "li" else "",
                "leverage": str(row.get("q_category_attributes.map_li_leverage_amount", "")) if underlier_type == "li" else "",
                "aum": aum,
                "aum_fmt": _fmt_currency(aum),
                "flow_1w": flow_1w,
                "flow_1w_fmt": _fmt_flow(flow_1w),
                "flow_1m_fmt": _fmt_flow(flow_1m),
                "flow_3m_fmt": _fmt_flow(flow_3m),
                "expense_ratio_fmt": f"{er_val:.2f}%" if er_val is not None else "",
                "total_return_1m_fmt": f"{ret_1m_val:.2f}%" if ret_1m_val is not None else "",
                "market_share_pct": round(mkt_share, 1),
                "yield_val": yield_val,
                "yield_fmt": f"{yield_val:.1f}%" if yield_val is not None else "-",
                "is_rex": bool(row.get("is_rex", False)),
            })
        underliers_list = []  # Also return the full list so UI can show selector
        for ul_name, grp in df.groupby(field):
            if not str(ul_name).strip():
                continue
            underliers_list.append({
                "name": str(ul_name),
                "aum_fmt": _fmt_currency(float(grp["t_w4.aum"].sum())),
                "num_products": int(len(grp)),
                "num_rex": int(grp["is_rex"].sum()),
            })
        underliers_list.sort(key=lambda x: x["num_products"], reverse=True)
        return {"underliers": underliers_list, "products": products, "underlier_type": underlier_type, "selected": underlier}


#  Time Series

def get_time_series(category: str | None = None, is_rex: bool | None = None) -> dict:
    """Return aggregated monthly AUM time series for charts."""
    ts = get_time_series_df()

    if category and category != "All":
        ts = ts[ts["category_display"] == category]
    elif category == "All" or category is None:
        pass  # all categories

    if is_rex is not None:
        ts = ts[ts["is_rex"] == is_rex]

    if ts.empty or "date" not in ts.columns:
        return {"labels": "[]", "values": "[]"}

    agg = (
        ts.dropna(subset=["date"])
        .groupby("date")["aum_value"]
        .sum()
        .reset_index()
        .sort_values("date")
    )
    # Keep last 24 months
    agg = agg.tail(25)
    labels = [d.strftime("%b %Y") for d in agg["date"]]
    values = [round(float(v), 2) for v in agg["aum_value"]]
    return {
        "labels": json.dumps(labels),
        "values": json.dumps(values),
    }
