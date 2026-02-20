"""
Market Intelligence data loader and cache.

Loads The_Dashboard.xlsx and provides aggregated views for:
- REX View: suite-by-suite performance
- Category View: competitive landscape with dynamic filters
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data" / "DASHBOARD"
DATA_FILE = DATA_DIR / "The Dashboard.xlsx"

# ---------------------------------------------------------------------------
# Cache (thread-safe, 1-hour TTL)
# ---------------------------------------------------------------------------
_cache: dict[str, pd.DataFrame] = {}
_cache_lock = threading.Lock()
_cache_time: float = 0.0
CACHE_TTL = 3600  # seconds

# ---------------------------------------------------------------------------
# Category -> dynamic slicer definitions
# ---------------------------------------------------------------------------
CATEGORY_SLICERS: dict[str, list[dict]] = {
    "Crypto": [
        {"field": "q_category_attributes.map_crypto_is_spot", "label": "Type", "type": "dropdown"},
        {"field": "q_category_attributes.map_crypto_underlier", "label": "Underlier", "type": "multi-select"},
    ],
    "Income - Single Stock": [
        {"field": "q_category_attributes.map_cc_underlier", "label": "Underlier", "type": "multi-select"},
    ],
    "Income - Index/Basket/ETF Based": [
        {"field": "q_category_attributes.map_cc_index", "label": "Index", "type": "multi-select"},
    ],
    "Leverage & Inverse - Single Stock": [
        {"field": "q_category_attributes.map_li_direction", "label": "Direction", "type": "dropdown"},
        {"field": "q_category_attributes.map_li_leverage_amount", "label": "Leverage", "type": "dropdown"},
        {"field": "q_category_attributes.map_li_underlier", "label": "Underlier", "type": "multi-select"},
    ],
    "Leverage & Inverse - Index/Basket/ETF Based": [
        {"field": "q_category_attributes.map_li_direction", "label": "Direction", "type": "dropdown"},
        {"field": "q_category_attributes.map_li_leverage_amount", "label": "Leverage", "type": "dropdown"},
        {"field": "q_category_attributes.map_li_category", "label": "Asset Class", "type": "dropdown"},
    ],
    "Defined Outcome": [
        {"field": "q_category_attributes.map_defined_category", "label": "Outcome Type", "type": "dropdown"},
    ],
    "Thematic": [
        {"field": "q_category_attributes.map_thematic_category", "label": "Theme", "type": "multi-select"},
    ],
}

# REX suites in display order
REX_SUITES = [
    "Leverage & Inverse - Single Stock",
    "Leverage & Inverse - Index/Basket/ETF Based",
    "Crypto",
    "Income - Single Stock",
    "Income - Index/Basket/ETF Based",
    "Thematic",
]

# All selectable categories (includes non-REX ones)
ALL_CATEGORIES = [
    "Crypto",
    "Income - Single Stock",
    "Income - Index/Basket/ETF Based",
    "Leverage & Inverse - Single Stock",
    "Leverage & Inverse - Index/Basket/ETF Based",
    "Defined Outcome",
    "Thematic",
]


def _load_fresh() -> dict[str, pd.DataFrame]:
    """Load required sheets from Excel."""
    if not DATA_FILE.exists():
        log.warning("Dashboard data file not found: %s", DATA_FILE)
        return {}

    log.info("Loading dashboard data from %s", DATA_FILE)
    sheets = {}
    xls = pd.ExcelFile(DATA_FILE)

    sheets["master"] = pd.read_excel(xls, "q_master_data")
    sheets["time_series"] = pd.read_excel(xls, "q_aum_time_series_labeled")

    # Ensure is_rex is boolean
    sheets["master"]["is_rex"] = sheets["master"]["is_rex"].fillna(False).astype(bool)
    sheets["time_series"]["is_rex"] = sheets["time_series"]["is_rex"].fillna(False).astype(bool)

    log.info(
        "Loaded %d funds (%d REX), %d time series rows",
        len(sheets["master"]),
        sheets["master"]["is_rex"].sum(),
        len(sheets["time_series"]),
    )
    return sheets


def _get_cache() -> dict[str, pd.DataFrame]:
    """Return cached data, refreshing if stale."""
    global _cache, _cache_time
    now = time.time()
    if _cache and (now - _cache_time) < CACHE_TTL:
        return _cache
    with _cache_lock:
        # Double-check after acquiring lock
        if _cache and (now - _cache_time) < CACHE_TTL:
            return _cache
        _cache = _load_fresh()
        _cache_time = time.time()
        return _cache


def invalidate_cache():
    """Clear cache (call from admin if needed)."""
    global _cache, _cache_time
    with _cache_lock:
        _cache = {}
        _cache_time = 0.0


def data_available() -> bool:
    """Check if the dashboard data file exists."""
    return DATA_FILE.exists()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_money(val: float) -> str:
    """Format a dollar value (in millions) to human-readable string."""
    if pd.isna(val):
        return "$0"
    abs_val = abs(val)
    sign = "-" if val < 0 else ""
    if abs_val >= 1000:
        return f"{sign}${abs_val / 1000:.1f}B"
    if abs_val >= 1:
        return f"{sign}${abs_val:.1f}M"
    if abs_val >= 0.001:
        return f"{sign}${abs_val * 1000:.0f}K"
    return "$0"


def _fmt_flow(val: float) -> str:
    """Format a flow value with +/- prefix."""
    if pd.isna(val):
        return "$0"
    abs_val = abs(val)
    sign = "+" if val > 0 else ("-" if val < 0 else "")
    if abs_val >= 1000:
        return f"{sign}${abs_val / 1000:.1f}B"
    if abs_val >= 1:
        return f"{sign}${abs_val:.1f}M"
    if abs_val >= 0.001:
        return f"{sign}${abs_val * 1000:.0f}K"
    return "$0"


def _compute_kpis(df: pd.DataFrame) -> dict:
    """Compute standard KPIs from a subset of master data."""
    aum = df["t_w4.aum"].sum()
    return {
        "aum": aum,
        "aum_fmt": _fmt_money(aum),
        "flow_1w": df["t_w4.fund_flow_1week"].sum(),
        "flow_1w_fmt": _fmt_flow(df["t_w4.fund_flow_1week"].sum()),
        "flow_1m": df["t_w4.fund_flow_1month"].sum(),
        "flow_1m_fmt": _fmt_flow(df["t_w4.fund_flow_1month"].sum()),
        "flow_3m": df["t_w4.fund_flow_3month"].sum(),
        "flow_3m_fmt": _fmt_flow(df["t_w4.fund_flow_3month"].sum()),
        "num_products": len(df),
    }


# ---------------------------------------------------------------------------
# REX View data
# ---------------------------------------------------------------------------

def get_rex_summary() -> dict | None:
    """
    Return REX totals and by-suite breakdown.

    Returns dict with keys:
        kpis: overall REX KPIs
        suites: list of suite dicts (name, kpis, market_share, top_movers)
        pie_data: {labels, values} for AUM pie chart
    """
    cache = _get_cache()
    if not cache:
        return None

    master = cache["master"]
    rex = master[master["is_rex"] == True]  # noqa: E712
    if rex.empty:
        return None

    overall_kpis = _compute_kpis(rex)

    suites = []
    pie_labels = []
    pie_values = []

    for suite_name in REX_SUITES:
        cat_all = master[master["category_display"] == suite_name]
        cat_rex = cat_all[cat_all["is_rex"] == True]  # noqa: E712

        if cat_rex.empty:
            continue

        kpis = _compute_kpis(cat_rex)
        cat_aum = cat_all["t_w4.aum"].sum()
        share = (kpis["aum"] / cat_aum * 100) if cat_aum > 0 else 0.0

        # Top 3 movers by 1-week flow (best + worst)
        sorted_funds = cat_rex.dropna(subset=["t_w4.fund_flow_1week"]).sort_values(
            "t_w4.fund_flow_1week", ascending=False
        )
        top_movers = []
        for _, row in sorted_funds.head(3).iterrows():
            ticker = str(row.get("ticker", "")).replace(" US", "")
            top_movers.append({
                "ticker": ticker,
                "flow": _fmt_flow(row["t_w4.fund_flow_1week"]),
                "flow_raw": row["t_w4.fund_flow_1week"],
            })

        suites.append({
            "name": suite_name,
            "kpis": kpis,
            "market_share": round(share, 1),
            "top_movers": top_movers,
        })

        pie_labels.append(suite_name)
        pie_values.append(round(kpis["aum"], 1))

    return {
        "kpis": overall_kpis,
        "suites": suites,
        "pie_data": {"labels": pie_labels, "values": pie_values},
    }


# ---------------------------------------------------------------------------
# REX AUM trend (time series)
# ---------------------------------------------------------------------------

def get_rex_aum_trend() -> dict | None:
    """
    Return monthly total REX AUM for the line chart.
    Returns {labels: [date strings], values: [aum floats]}.
    """
    cache = _get_cache()
    if not cache:
        return None

    ts = cache["time_series"]
    rex_ts = ts[ts["is_rex"] == True]  # noqa: E712
    if rex_ts.empty:
        return None

    grouped = rex_ts.groupby("date")["aum_value"].sum().sort_index()

    labels = [d.strftime("%b %Y") for d in grouped.index]
    values = [round(float(v), 1) for v in grouped.values]

    return {"labels": labels, "values": values}


# ---------------------------------------------------------------------------
# Category View data
# ---------------------------------------------------------------------------

def get_category_summary(category: str | None = None, filters: dict | None = None) -> dict | None:
    """
    Return category analysis: totals, REX share, top products, issuer breakdown.
    """
    cache = _get_cache()
    if not cache:
        return None

    master = cache["master"]

    # Apply category filter
    if category and category != "All":
        df = master[master["category_display"] == category].copy()
    else:
        # All categories that have category_display set
        df = master[master["category_display"].notna()].copy()

    # Apply dynamic slicer filters
    if filters:
        for field, value in filters.items():
            if field in df.columns and value:
                if isinstance(value, list):
                    df = df[df[field].isin(value)]
                else:
                    df = df[df[field] == value]

    if df.empty:
        return _empty_category_summary(category)

    rex_df = df[df["is_rex"] == True]  # noqa: E712

    cat_kpis = _compute_kpis(df)
    rex_kpis = _compute_kpis(rex_df) if not rex_df.empty else _compute_kpis(pd.DataFrame(columns=df.columns))

    cat_aum = cat_kpis["aum"]
    rex_share = (rex_kpis["aum"] / cat_aum * 100) if cat_aum > 0 else 0.0

    # Top products by AUM
    top_df = df.dropna(subset=["t_w4.aum"]).sort_values("t_w4.aum", ascending=False)
    top_products = []
    for rank, (_, row) in enumerate(top_df.head(50).iterrows(), 1):
        top_products.append({
            "rank": rank,
            "ticker": str(row.get("ticker", "")),
            "fund_name": str(row.get("fund_name", "")),
            "issuer": str(row.get("issuer_display", "")),
            "aum": row.get("t_w4.aum", 0),
            "aum_fmt": _fmt_money(row.get("t_w4.aum", 0)),
            "flow_1w": row.get("t_w4.fund_flow_1week", 0),
            "flow_1w_fmt": _fmt_flow(row.get("t_w4.fund_flow_1week", 0)),
            "flow_1m": row.get("t_w4.fund_flow_1month", 0),
            "flow_1m_fmt": _fmt_flow(row.get("t_w4.fund_flow_1month", 0)),
            "yield_val": row.get("t_w3.annualized_yield"),
            "is_rex": bool(row.get("is_rex", False)),
        })

    # REX products in this category
    rex_products = []
    if not rex_df.empty:
        rex_sorted = rex_df.dropna(subset=["t_w4.aum"]).sort_values("t_w4.aum", ascending=False)
        # Find rank in overall category
        all_tickers_ranked = top_df["ticker"].tolist()
        for _, row in rex_sorted.iterrows():
            ticker = str(row.get("ticker", ""))
            try:
                rank = all_tickers_ranked.index(ticker) + 1
            except ValueError:
                rank = None
            rex_products.append({
                "rank": rank,
                "ticker": ticker,
                "fund_name": str(row.get("fund_name", "")),
                "aum": row.get("t_w4.aum", 0),
                "aum_fmt": _fmt_money(row.get("t_w4.aum", 0)),
                "flow_1w_fmt": _fmt_flow(row.get("t_w4.fund_flow_1week", 0)),
                "flow_1m_fmt": _fmt_flow(row.get("t_w4.fund_flow_1month", 0)),
                "yield_val": row.get("t_w3.annualized_yield"),
            })

    # Issuer breakdown (for bar chart)
    issuer_aum = (
        df.groupby("issuer_display")["t_w4.aum"]
        .sum()
        .sort_values(ascending=False)
        .head(15)
    )
    issuer_data = {
        "labels": issuer_aum.index.tolist(),
        "values": [round(float(v), 1) for v in issuer_aum.values],
        "is_rex": [label == "REX" for label in issuer_aum.index],
    }

    return {
        "category": category or "All",
        "cat_kpis": cat_kpis,
        "rex_kpis": rex_kpis,
        "rex_share": round(rex_share, 1),
        "top_products": top_products,
        "rex_products": rex_products,
        "issuer_data": issuer_data,
    }


def _empty_category_summary(category: str | None) -> dict:
    """Return an empty summary when no data matches."""
    empty_kpis = {
        "aum": 0, "aum_fmt": "$0",
        "flow_1w": 0, "flow_1w_fmt": "$0",
        "flow_1m": 0, "flow_1m_fmt": "$0",
        "flow_3m": 0, "flow_3m_fmt": "$0",
        "num_products": 0,
    }
    return {
        "category": category or "All",
        "cat_kpis": empty_kpis,
        "rex_kpis": empty_kpis,
        "rex_share": 0.0,
        "top_products": [],
        "rex_products": [],
        "issuer_data": {"labels": [], "values": [], "is_rex": []},
    }


# ---------------------------------------------------------------------------
# Category AUM trend (time series)
# ---------------------------------------------------------------------------

def get_category_aum_trend(category: str | None = None) -> dict | None:
    """
    Return monthly AUM trend for a category, with REX overlay.
    Returns {labels, total_values, rex_values}.
    """
    cache = _get_cache()
    if not cache:
        return None

    ts = cache["time_series"]

    if category and category != "All":
        ts = ts[ts["category_display"] == category]

    if ts.empty:
        return None

    total = ts.groupby("date")["aum_value"].sum().sort_index()
    rex_ts = ts[ts["is_rex"] == True]  # noqa: E712
    rex = rex_ts.groupby("date")["aum_value"].sum().sort_index() if not rex_ts.empty else pd.Series(dtype=float)

    labels = [d.strftime("%b %Y") for d in total.index]
    total_values = [round(float(v), 1) for v in total.values]
    rex_values = [round(float(rex.get(d, 0)), 1) for d in total.index]

    return {"labels": labels, "total_values": total_values, "rex_values": rex_values}


# ---------------------------------------------------------------------------
# Slicer options
# ---------------------------------------------------------------------------

def get_slicer_options(category: str) -> list[dict]:
    """
    Return available filter values for a category's dynamic slicers.
    Each item: {field, label, type, options: [value, ...]}.
    """
    slicers = CATEGORY_SLICERS.get(category, [])
    if not slicers:
        return []

    cache = _get_cache()
    if not cache:
        return []

    master = cache["master"]
    cat_df = master[master["category_display"] == category]

    result = []
    for slicer in slicers:
        field = slicer["field"]
        if field not in cat_df.columns:
            continue
        options = sorted(
            [str(v) for v in cat_df[field].dropna().unique() if str(v).strip()],
            key=str.lower,
        )
        if options:
            result.append({
                "field": field,
                "label": slicer["label"],
                "type": slicer["type"],
                "options": options,
            })

    return result
