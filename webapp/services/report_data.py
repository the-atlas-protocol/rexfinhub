"""
Bloomberg report data service.

Provides data for three weekly reports:
- Leveraged & Inverse (L&I)
- Covered Call (CC)
- Single-Stock (SS)

Thread-safe cache with 1-hour TTL, same pattern as market_data.py.
"""
from __future__ import annotations

import logging
import math
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd

from market.config import (
    DATA_FILE,
    RULES_DIR,
    W1_COL_MAP,
    W2_COL_MAP,
    W3_COL_MAP,
    W4_FLOW_COL_MAP,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
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


def _get_cache() -> dict[str, Any]:
    global _cache, _cache_time
    with _lock:
        if _is_fresh():
            return _cache
    data = _load_all()
    with _lock:
        _cache = data
        _cache_time = time.time()
    return data


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def _fmt_currency(val: float) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "$0"
    if abs(val) >= 1_000:
        return f"${val / 1_000:,.1f}B"
    if abs(val) >= 1:
        return f"${val:,.1f}M"
    return f"${val:.2f}M"


def _fmt_flow(val: float) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "$0"
    sign = "+" if val >= 0 else ""
    return f"{sign}{_fmt_currency(val)}"


def _fmt_pct(val: float) -> str:
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "0.0%"
    return f"{val:.1f}%"


def _safe_float(val: Any) -> float:
    try:
        v = float(val)
        return 0.0 if math.isnan(v) else v
    except (TypeError, ValueError):
        return 0.0


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
def _load_all() -> dict[str, Any]:
    """Load Bloomberg sheets + rules CSVs, build master dataframe."""
    path = DATA_FILE
    if not path.exists():
        log.warning("Bloomberg data file not found: %s", path)
        return {"available": False}

    log.info("Loading report data from %s", path)

    # Check which sheets are available
    try:
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        available_sheets = set(wb.sheetnames)
        wb.close()
    except Exception as e:
        log.error("Cannot open data file %s: %s", path, e)
        return {"available": False}

    required = {"w1", "w2", "w3", "w4"}
    if not required.issubset(available_sheets):
        missing = required - available_sheets
        log.warning("Data file missing required sheets %s: %s", missing, path)
        return {"available": False}

    # Read w1-w4
    try:
        w1 = pd.read_excel(path, sheet_name="w1", engine="openpyxl")
        w2 = pd.read_excel(path, sheet_name="w2", engine="openpyxl")
        w3 = pd.read_excel(path, sheet_name="w3", engine="openpyxl")
        w4_raw = pd.read_excel(path, sheet_name="w4", engine="openpyxl")
    except Exception as e:
        log.error("Error reading sheets from %s: %s", path, e)
        return {"available": False}

    # Rename w1 columns
    w1 = w1.rename(columns=W1_COL_MAP)

    # Rename w2 (drop Fund Name if present)
    w2 = w2.rename(columns=W2_COL_MAP)
    if "Fund Name" in w2.columns:
        w2 = w2.drop(columns=["Fund Name"])

    # Rename w3 (drop Fund Name if present)
    w3 = w3.rename(columns=W3_COL_MAP)
    if "Fund Name" in w3.columns:
        w3 = w3.drop(columns=["Fund Name"])

    # Rename w4 flows + extract AUM
    w4_cols = list(w4_raw.columns)
    w4_rename = {}
    for orig, canon in W4_FLOW_COL_MAP.items():
        if orig in w4_cols:
            w4_rename[orig] = canon
    w4 = w4_raw.rename(columns=w4_rename)
    if "Fund Name" in w4.columns:
        w4 = w4.drop(columns=["Fund Name"])
    # AUM is typically column index 10 (after ticker + name + 8 flows)
    # Find it by position: first column after the flow columns that isn't already named
    aum_col = None
    for col in w4.columns:
        if col not in W4_FLOW_COL_MAP.values() and col not in ("ticker", "Fund Name"):
            if aum_col is None:
                w4 = w4.rename(columns={col: "aum"})
                aum_col = "aum"
                break
    # If AUM column is at a known position
    if "aum" not in w4.columns and len(w4.columns) > 10:
        w4 = w4.rename(columns={w4.columns[10]: "aum"})

    # Join on ticker
    master = w1.copy()
    for df in [w2, w3, w4]:
        if "ticker" in df.columns:
            merge_cols = [c for c in df.columns if c != "ticker" and c not in master.columns]
            if merge_cols:
                master = master.merge(df[["ticker"] + merge_cols], on="ticker", how="left")

    # Numeric coercions
    _NUMERIC = [
        "aum", "fund_flow_1day", "fund_flow_1week", "fund_flow_1month",
        "fund_flow_3month", "fund_flow_6month", "fund_flow_ytd",
        "fund_flow_1year", "fund_flow_3year",
        "expense_ratio", "annualized_yield",
        "total_return_1day", "total_return_1week", "total_return_1month",
        "total_return_3month", "total_return_6month", "total_return_ytd",
        "total_return_1year", "total_return_3year",
    ]
    for col in _NUMERIC:
        if col in master.columns:
            master[col] = pd.to_numeric(master[col], errors="coerce").fillna(0.0)

    # Clean ticker: strip " US" suffix for matching
    if "ticker" in master.columns:
        master["ticker_clean"] = master["ticker"].astype(str).str.strip()
    else:
        master["ticker_clean"] = ""

    # Read time-series sheets (wide format: dates as index, tickers as columns)
    try:
        data_aum = pd.read_excel(path, sheet_name="data_aum", engine="openpyxl", index_col=0)
        data_flow = pd.read_excel(path, sheet_name="data_flow", engine="openpyxl", index_col=0)
        data_notional = pd.read_excel(path, sheet_name="data_notional", engine="openpyxl", index_col=0)

        # Strip " Equity" suffix from column names
        for df in [data_aum, data_flow, data_notional]:
            df.columns = [
                c.replace(" Equity", "").strip() if isinstance(c, str) else c
                for c in df.columns
            ]
            # Ensure index is datetime
            df.index = pd.to_datetime(df.index, errors="coerce")
            df.dropna(how="all", axis=0, inplace=True)

        has_timeseries = True
    except Exception as e:
        log.warning("Could not load time-series sheets: %s", e)
        data_aum = pd.DataFrame()
        data_flow = pd.DataFrame()
        data_notional = pd.DataFrame()
        has_timeseries = False

    # Load rules CSVs
    fund_map = _read_csv(RULES_DIR / "fund_mapping.csv")
    li_attrs = _read_csv(RULES_DIR / "attributes_LI.csv")
    cc_attrs = _read_csv(RULES_DIR / "attributes_CC.csv")
    rex_funds = _read_csv(RULES_DIR / "rex_funds.csv")

    rex_tickers = set(rex_funds["ticker"].tolist()) if "ticker" in rex_funds.columns else set()

    # Mark REX
    master["is_rex"] = master["ticker_clean"].isin(rex_tickers)

    # Merge fund_mapping (etp_category)
    if "ticker" in fund_map.columns and "etp_category" in fund_map.columns:
        fm = fund_map[["ticker", "etp_category"]].copy()
        fm = fm.rename(columns={"ticker": "ticker_clean"})
        master = master.merge(fm, on="ticker_clean", how="left")
    else:
        master["etp_category"] = ""

    # Merge LI attributes
    if "ticker" in li_attrs.columns:
        la = li_attrs.rename(columns={"ticker": "ticker_clean"})
        master = master.merge(la, on="ticker_clean", how="left", suffixes=("", "_li"))

    # Merge CC attributes
    if "ticker" in cc_attrs.columns:
        ca = cc_attrs.rename(columns={"ticker": "ticker_clean"})
        master = master.merge(ca, on="ticker_clean", how="left", suffixes=("", "_cc"))

    # Data as-of date
    data_as_of = ""
    if has_timeseries and len(data_aum) > 0:
        last_date = data_aum.index.max()
        if pd.notna(last_date):
            data_as_of = last_date.strftime("%B %d, %Y")

    log.info("Report data loaded: %d funds, timeseries=%s", len(master), has_timeseries)

    return {
        "available": True,
        "master": master,
        "data_aum": data_aum,
        "data_flow": data_flow,
        "data_notional": data_notional,
        "has_timeseries": has_timeseries,
        "rex_tickers": rex_tickers,
        "li_attrs": li_attrs,
        "cc_attrs": cc_attrs,
        "data_as_of": data_as_of,
    }


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        log.warning("Rules file not found: %s", path)
        return pd.DataFrame()
    return pd.read_csv(path, engine="python", on_bad_lines="skip")


def data_available() -> bool:
    return _get_cache().get("available", False)


def get_data_as_of() -> str:
    return _get_cache().get("data_as_of", "")


# ---------------------------------------------------------------------------
# Time-series aggregation helpers
# ---------------------------------------------------------------------------
def _monthly_agg(wide_df: pd.DataFrame, tickers: list[str],
                 group_map: dict[str, str], top_n: int = 8) -> dict:
    """Aggregate wide-format time series by month and group.

    Returns: {labels: [months], datasets: [{label, data}], total: [values]}
    """
    if wide_df.empty or not tickers:
        return {"labels": [], "datasets": [], "total": []}

    # Filter to tickers that exist in columns
    available = [t for t in tickers if t in wide_df.columns]
    if not available:
        return {"labels": [], "datasets": [], "total": []}

    df = wide_df[available].copy()
    df = df.apply(pd.to_numeric, errors="coerce").fillna(0.0)

    # Resample to month-end, sum
    monthly = df.resample("ME").sum()

    # Total across all tickers
    total = monthly.sum(axis=1).tolist()
    labels = [d.strftime("%b %Y") for d in monthly.index]

    # Group tickers
    groups: dict[str, list[str]] = {}
    for t in available:
        g = group_map.get(t, "Other")
        groups.setdefault(g, []).append(t)

    # Sum AUM by group, pick top N by total
    group_totals = {}
    for g, g_tickers in groups.items():
        group_totals[g] = float(monthly[g_tickers].sum().sum())

    sorted_groups = sorted(group_totals, key=group_totals.get, reverse=True)
    top_groups = sorted_groups[:top_n]
    other_groups = sorted_groups[top_n:]

    datasets = []
    for g in top_groups:
        g_tickers = groups[g]
        vals = monthly[g_tickers].sum(axis=1).tolist()
        datasets.append({"label": g, "data": vals})

    # "Other" bucket
    if other_groups:
        other_tickers = []
        for g in other_groups:
            other_tickers.extend(groups[g])
        vals = monthly[other_tickers].sum(axis=1).tolist()
        datasets.append({"label": "Other", "data": vals})

    return {"labels": labels, "datasets": datasets, "total": total}


def _monthly_aum_last(wide_df: pd.DataFrame, tickers: list[str],
                      group_map: dict[str, str], top_n: int = 8) -> dict:
    """Like _monthly_agg but takes last value per month (for AUM snapshots)."""
    if wide_df.empty or not tickers:
        return {"labels": [], "datasets": [], "total": []}

    available = [t for t in tickers if t in wide_df.columns]
    if not available:
        return {"labels": [], "datasets": [], "total": []}

    df = wide_df[available].copy()
    df = df.apply(pd.to_numeric, errors="coerce").fillna(0.0)
    monthly = df.resample("ME").last()

    total = monthly.sum(axis=1).tolist()
    labels = [d.strftime("%b %Y") for d in monthly.index]

    groups: dict[str, list[str]] = {}
    for t in available:
        g = group_map.get(t, "Other")
        groups.setdefault(g, []).append(t)

    group_totals = {}
    for g, g_tickers in groups.items():
        group_totals[g] = float(monthly[g_tickers].iloc[-1].sum()) if len(monthly) > 0 else 0.0

    sorted_groups = sorted(group_totals, key=group_totals.get, reverse=True)
    top_groups = sorted_groups[:top_n]
    other_groups = sorted_groups[top_n:]

    datasets = []
    for g in top_groups:
        vals = monthly[groups[g]].sum(axis=1).tolist()
        datasets.append({"label": g, "data": vals})

    if other_groups:
        other_tickers = []
        for g in other_groups:
            other_tickers.extend(groups[g])
        vals = monthly[other_tickers].sum(axis=1).tolist()
        datasets.append({"label": "Other", "data": vals})

    return {"labels": labels, "datasets": datasets, "total": total}


def _cumulative_flow(wide_df: pd.DataFrame, tickers: list[str],
                     group_map: dict[str, str], top_n: int = 5) -> dict:
    """Cumulative sum of daily flows, grouped."""
    if wide_df.empty or not tickers:
        return {"labels": [], "datasets": []}

    available = [t for t in tickers if t in wide_df.columns]
    if not available:
        return {"labels": [], "datasets": []}

    df = wide_df[available].copy()
    df = df.apply(pd.to_numeric, errors="coerce").fillna(0.0)
    cum = df.cumsum()

    # Thin labels (show every ~30th)
    labels = [d.strftime("%Y-%m-%d") for d in cum.index]

    # Total
    total_cum = cum.sum(axis=1).tolist()
    datasets = [{"label": "Total", "data": total_cum}]

    # Group
    groups: dict[str, list[str]] = {}
    for t in available:
        g = group_map.get(t, "Other")
        groups.setdefault(g, []).append(t)

    # Top N by final cumulative value
    group_finals = {}
    for g, g_tickers in groups.items():
        group_finals[g] = float(cum[g_tickers].iloc[-1].sum()) if len(cum) > 0 else 0.0

    sorted_groups = sorted(group_finals, key=lambda x: abs(group_finals[x]), reverse=True)
    for g in sorted_groups[:top_n]:
        vals = cum[groups[g]].sum(axis=1).tolist()
        datasets.append({"label": g, "data": vals})

    return {"labels": labels, "datasets": datasets}


def _daily_series(wide_df: pd.DataFrame, tickers: list[str],
                  group_map: dict[str, str], top_n: int = 5,
                  rolling_window: int = 0) -> dict:
    """Daily time series, optionally with rolling average, grouped."""
    if wide_df.empty or not tickers:
        return {"labels": [], "datasets": []}

    available = [t for t in tickers if t in wide_df.columns]
    if not available:
        return {"labels": [], "datasets": []}

    df = wide_df[available].copy()
    df = df.apply(pd.to_numeric, errors="coerce").fillna(0.0)

    if rolling_window > 0:
        df = df.rolling(window=rolling_window, min_periods=1).mean()

    labels = [d.strftime("%Y-%m-%d") for d in df.index]

    # Total
    total = df.sum(axis=1).tolist()
    datasets = [{"label": "Total", "data": total}]

    # Group
    groups: dict[str, list[str]] = {}
    for t in available:
        g = group_map.get(t, "Other")
        groups.setdefault(g, []).append(t)

    # Top N by latest value
    group_latest = {}
    for g, g_tickers in groups.items():
        group_latest[g] = float(df[g_tickers].iloc[-1].sum()) if len(df) > 0 else 0.0

    sorted_groups = sorted(group_latest, key=lambda x: abs(group_latest[x]), reverse=True)
    for g in sorted_groups[:top_n]:
        vals = df[groups[g]].sum(axis=1).tolist()
        datasets.append({"label": g, "data": vals})

    return {"labels": labels, "datasets": datasets}


# ---------------------------------------------------------------------------
# L&I Report
# ---------------------------------------------------------------------------
def get_li_report() -> dict:
    """Data for Leveraged & Inverse report."""
    cache = _get_cache()
    if not cache.get("available"):
        return {"available": False, "data_as_of": ""}

    master = cache["master"].copy()
    data_aum = cache["data_aum"]
    rex_tickers = cache["rex_tickers"]

    # Filter to LI tickers
    li = master[master["etp_category"] == "LI"].copy()
    if li.empty:
        return {"available": True, "data_as_of": cache["data_as_of"],
                "kpis": {}, "providers": [], "top10": [], "bottom10": [],
                "chart": {"labels": [], "datasets": [], "total": []}}

    # KPIs
    total_aum = float(li["aum"].sum())
    flow_1w = float(li["fund_flow_1week"].sum())
    flow_ytd = float(li["fund_flow_ytd"].sum())
    kpis = {
        "count": len(li),
        "total_aum": _fmt_currency(total_aum),
        "flow_1w": _fmt_flow(flow_1w),
        "flow_1w_positive": flow_1w >= 0,
        "flow_ytd": _fmt_flow(flow_ytd),
        "flow_ytd_positive": flow_ytd >= 0,
    }

    # Provider summary: group by issuer
    issuer_col = "issuer" if "issuer" in li.columns else "fund_name"
    providers = []
    for issuer, grp in li.groupby(issuer_col):
        if not issuer or (isinstance(issuer, float) and math.isnan(issuer)):
            continue
        aum = float(grp["aum"].sum())
        providers.append({
            "issuer": str(issuer),
            "count": len(grp),
            "aum": aum,
            "aum_fmt": _fmt_currency(aum),
            "flow_1w": float(grp["fund_flow_1week"].sum()),
            "flow_1w_fmt": _fmt_flow(float(grp["fund_flow_1week"].sum())),
            "flow_1m": float(grp["fund_flow_1month"].sum()),
            "flow_1m_fmt": _fmt_flow(float(grp["fund_flow_1month"].sum())),
            "flow_ytd": float(grp["fund_flow_ytd"].sum()),
            "flow_ytd_fmt": _fmt_flow(float(grp["fund_flow_ytd"].sum())),
            "market_share": (aum / total_aum * 100) if total_aum > 0 else 0.0,
            "is_rex": bool(grp["is_rex"].any()),
        })
    providers.sort(key=lambda x: x["aum"], reverse=True)

    # Total row
    total_row = {
        "issuer": "Total",
        "count": len(li),
        "aum": total_aum,
        "aum_fmt": _fmt_currency(total_aum),
        "flow_1w": flow_1w,
        "flow_1w_fmt": _fmt_flow(flow_1w),
        "flow_1m": float(li["fund_flow_1month"].sum()),
        "flow_1m_fmt": _fmt_flow(float(li["fund_flow_1month"].sum())),
        "flow_ytd": flow_ytd,
        "flow_ytd_fmt": _fmt_flow(flow_ytd),
        "market_share": 100.0,
        "is_rex": False,
    }

    # Top 10 / Bottom 10 by 1W flow
    li_sorted = li.sort_values("fund_flow_1week", ascending=False)
    top10 = _fund_rows(li_sorted.head(10), total_aum)
    bottom10 = _fund_rows(li_sorted.tail(10).sort_values("fund_flow_1week", ascending=True), total_aum)

    # Historical AUM chart (monthly, by issuer)
    li_tickers = li["ticker_clean"].tolist()
    issuer_map = dict(zip(li["ticker_clean"], li[issuer_col].fillna("Unknown").astype(str)))
    chart = _monthly_aum_last(data_aum, li_tickers, issuer_map, top_n=8)

    return {
        "available": True,
        "data_as_of": cache["data_as_of"],
        "kpis": kpis,
        "providers": providers,
        "total_row": total_row,
        "top10": top10,
        "bottom10": bottom10,
        "chart": chart,
    }


def _fund_rows(df: pd.DataFrame, total_aum: float) -> list[dict]:
    rows = []
    for _, r in df.iterrows():
        ticker = str(r.get("ticker_clean", r.get("ticker", "")))
        aum = _safe_float(r.get("aum", 0))
        rows.append({
            "ticker": ticker,
            "fund_name": str(r.get("fund_name", "")),
            "issuer": str(r.get("issuer", "")),
            "aum": aum,
            "aum_fmt": _fmt_currency(aum),
            "flow_1d": _safe_float(r.get("fund_flow_1day", 0)),
            "flow_1d_fmt": _fmt_flow(_safe_float(r.get("fund_flow_1day", 0))),
            "flow_1w": _safe_float(r.get("fund_flow_1week", 0)),
            "flow_1w_fmt": _fmt_flow(_safe_float(r.get("fund_flow_1week", 0))),
            "market_share": (aum / total_aum * 100) if total_aum > 0 else 0.0,
            "is_rex": bool(r.get("is_rex", False)),
        })
    return rows


# ---------------------------------------------------------------------------
# Covered Call Report
# ---------------------------------------------------------------------------
def get_cc_report() -> dict:
    """Data for Covered Call report."""
    cache = _get_cache()
    if not cache.get("available"):
        return {"available": False, "data_as_of": ""}

    master = cache["master"].copy()
    data_aum = cache["data_aum"]
    rex_tickers = cache["rex_tickers"]

    # Filter to CC tickers
    cc = master[master["etp_category"] == "CC"].copy()
    if cc.empty:
        return {"available": True, "data_as_of": cache["data_as_of"],
                "kpis": {}, "rex_funds": [], "top_flow_segments": {},
                "top_yield_segments": {}, "aum_by_category": [],
                "issuers": [], "all_products": [],
                "issuer_aum_chart": {}, "rex_ts_chart": {}}

    total_aum = float(cc["aum"].sum())
    flow_1w = float(cc["fund_flow_1week"].sum())
    avg_yield = float(cc["annualized_yield"].replace(0, float("nan")).mean()) if "annualized_yield" in cc.columns else 0.0

    kpis = {
        "count": len(cc),
        "total_aum": _fmt_currency(total_aum),
        "flow_1w": _fmt_flow(flow_1w),
        "flow_1w_positive": flow_1w >= 0,
        "avg_yield": _fmt_pct(avg_yield),
    }

    # Table 1: REX CC funds
    rex_cc = cc[cc["is_rex"]].sort_values("aum", ascending=False)
    rex_funds_list = []
    rex_total_aum = 0.0
    for _, r in rex_cc.iterrows():
        aum = _safe_float(r.get("aum", 0))
        rex_total_aum += aum
        rex_funds_list.append({
            "ticker": str(r.get("ticker_clean", "")),
            "fund_name": str(r.get("fund_name", "")),
            "aum": aum,
            "aum_fmt": _fmt_currency(aum),
            "return_1w": _safe_float(r.get("total_return_1week", 0)),
            "return_1w_fmt": _fmt_pct(_safe_float(r.get("total_return_1week", 0))),
            "flow_1w": _safe_float(r.get("fund_flow_1week", 0)),
            "flow_1w_fmt": _fmt_flow(_safe_float(r.get("fund_flow_1week", 0))),
            "flow_1m": _safe_float(r.get("fund_flow_1month", 0)),
            "flow_1m_fmt": _fmt_flow(_safe_float(r.get("fund_flow_1month", 0))),
            "flow_ytd": _safe_float(r.get("fund_flow_ytd", 0)),
            "flow_ytd_fmt": _fmt_flow(_safe_float(r.get("fund_flow_ytd", 0))),
            "yield_val": _safe_float(r.get("annualized_yield", 0)),
            "yield_fmt": _fmt_pct(_safe_float(r.get("annualized_yield", 0))),
        })

    # Segment tabs: All, Traditional, Synthetic, Single Stock
    segments = {
        "All": cc,
        "Traditional": cc[cc.get("cc_type", pd.Series(dtype=str)) == "Traditional"] if "cc_type" in cc.columns else pd.DataFrame(),
        "Synthetic": cc[cc.get("cc_type", pd.Series(dtype=str)) == "Synthetic"] if "cc_type" in cc.columns else pd.DataFrame(),
        "Single Stock": cc[cc.get("cc_type", pd.Series(dtype=str)) == "Single Stock"] if "cc_type" in cc.columns else pd.DataFrame(),
    }

    # Table 2: Top 10 by 1M flow per segment
    top_flow_segments = {}
    for seg_name, seg_df in segments.items():
        if seg_df.empty:
            top_flow_segments[seg_name] = []
            continue
        top = seg_df.sort_values("fund_flow_1month", ascending=False).head(10)
        top_flow_segments[seg_name] = _cc_fund_rows(top)

    # Table 3: Top 10 by yield per segment
    top_yield_segments = {}
    for seg_name, seg_df in segments.items():
        if seg_df.empty:
            top_yield_segments[seg_name] = []
            continue
        top = seg_df.sort_values("annualized_yield", ascending=False).head(10)
        top_yield_segments[seg_name] = _cc_fund_rows(top)

    # Table 4: AUM by cc_category
    aum_by_category = []
    if "cc_category" in cc.columns:
        for cat, grp in cc.groupby("cc_category"):
            if not cat or (isinstance(cat, float) and math.isnan(cat)):
                continue
            aum = float(grp["aum"].sum())
            aum_by_category.append({
                "category": str(cat),
                "count": len(grp),
                "aum": aum,
                "aum_fmt": _fmt_currency(aum),
                "flow_1w": float(grp["fund_flow_1week"].sum()),
                "flow_1w_fmt": _fmt_flow(float(grp["fund_flow_1week"].sum())),
                "flow_1m": float(grp["fund_flow_1month"].sum()),
                "flow_1m_fmt": _fmt_flow(float(grp["fund_flow_1month"].sum())),
                "market_share": (aum / total_aum * 100) if total_aum > 0 else 0.0,
            })
        aum_by_category.sort(key=lambda x: x["aum"], reverse=True)

    # Table 5: Issuer ranking
    issuers = []
    issuer_col = "issuer"
    for issuer, grp in cc.groupby(issuer_col):
        if not issuer or (isinstance(issuer, float) and math.isnan(issuer)):
            continue
        aum = float(grp["aum"].sum())
        issuers.append({
            "issuer": str(issuer),
            "count": len(grp),
            "aum": aum,
            "aum_fmt": _fmt_currency(aum),
            "flow_1w": float(grp["fund_flow_1week"].sum()),
            "flow_1w_fmt": _fmt_flow(float(grp["fund_flow_1week"].sum())),
            "flow_1m": float(grp["fund_flow_1month"].sum()),
            "flow_1m_fmt": _fmt_flow(float(grp["fund_flow_1month"].sum())),
            "flow_ytd": float(grp["fund_flow_ytd"].sum()),
            "flow_ytd_fmt": _fmt_flow(float(grp["fund_flow_ytd"].sum())),
            "market_share": (aum / total_aum * 100) if total_aum > 0 else 0.0,
        })
    issuers.sort(key=lambda x: x["aum"], reverse=True)

    # Table 6: All CC products
    all_products = _cc_fund_rows(cc.sort_values("aum", ascending=False))

    # Chart 1: AUM by issuer (doughnut) - top 10 + Other
    issuer_aum_chart = {"labels": [], "values": []}
    for i, iss in enumerate(issuers[:10]):
        issuer_aum_chart["labels"].append(iss["issuer"])
        issuer_aum_chart["values"].append(round(iss["aum"], 1))
    if len(issuers) > 10:
        other_aum = sum(x["aum"] for x in issuers[10:])
        issuer_aum_chart["labels"].append("Other")
        issuer_aum_chart["values"].append(round(other_aum, 1))

    # Chart 2: REX CC AUM & market share over time
    cc_tickers = cc["ticker_clean"].tolist()
    rex_cc_tickers = rex_cc["ticker_clean"].tolist()
    rex_ts_chart = _rex_market_share_ts(data_aum, cc_tickers, rex_cc_tickers)

    return {
        "available": True,
        "data_as_of": cache["data_as_of"],
        "kpis": kpis,
        "rex_funds": rex_funds_list,
        "rex_total_aum": _fmt_currency(rex_total_aum),
        "top_flow_segments": top_flow_segments,
        "top_yield_segments": top_yield_segments,
        "aum_by_category": aum_by_category,
        "issuers": issuers,
        "all_products": all_products,
        "issuer_aum_chart": issuer_aum_chart,
        "rex_ts_chart": rex_ts_chart,
    }


def _cc_fund_rows(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, r in df.iterrows():
        aum = _safe_float(r.get("aum", 0))
        rows.append({
            "ticker": str(r.get("ticker_clean", "")),
            "fund_name": str(r.get("fund_name", "")),
            "issuer": str(r.get("issuer", "")),
            "cc_type": str(r.get("cc_type", "")),
            "cc_category": str(r.get("cc_category", "")),
            "aum": aum,
            "aum_fmt": _fmt_currency(aum),
            "flow_1m": _safe_float(r.get("fund_flow_1month", 0)),
            "flow_1m_fmt": _fmt_flow(_safe_float(r.get("fund_flow_1month", 0))),
            "yield_val": _safe_float(r.get("annualized_yield", 0)),
            "yield_fmt": _fmt_pct(_safe_float(r.get("annualized_yield", 0))),
        })
    return rows


def _rex_market_share_ts(data_aum: pd.DataFrame, all_cc_tickers: list[str],
                         rex_cc_tickers: list[str]) -> dict:
    """Monthly REX CC AUM + market share %."""
    if data_aum.empty:
        return {"labels": [], "rex_aum": [], "share_pct": []}

    all_avail = [t for t in all_cc_tickers if t in data_aum.columns]
    rex_avail = [t for t in rex_cc_tickers if t in data_aum.columns]
    if not all_avail or not rex_avail:
        return {"labels": [], "rex_aum": [], "share_pct": []}

    all_monthly = data_aum[all_avail].apply(pd.to_numeric, errors="coerce").fillna(0).resample("ME").last().sum(axis=1)
    rex_monthly = data_aum[rex_avail].apply(pd.to_numeric, errors="coerce").fillna(0).resample("ME").last().sum(axis=1)

    labels = [d.strftime("%b %Y") for d in all_monthly.index]
    rex_aum = rex_monthly.tolist()
    share_pct = []
    for total, rex in zip(all_monthly, rex_monthly):
        share_pct.append(round(rex / total * 100, 2) if total > 0 else 0.0)

    return {"labels": labels, "rex_aum": rex_aum, "share_pct": share_pct}


# ---------------------------------------------------------------------------
# Single-Stock Report
# ---------------------------------------------------------------------------
def get_ss_report() -> dict:
    """Data for Single-Stock Leveraged ETFs report."""
    cache = _get_cache()
    if not cache.get("available"):
        return {"available": False, "data_as_of": ""}

    master = cache["master"].copy()
    data_aum = cache["data_aum"]
    data_flow = cache["data_flow"]
    data_notional = cache["data_notional"]

    # Filter to single-stock L&I: attributes_LI where subcategory = "Single Stock"
    ss = master[
        (master["etp_category"] == "LI") &
        (master.get("map_li_subcategory", pd.Series(dtype=str)).str.lower() == "single stock")
    ].copy() if "map_li_subcategory" in master.columns else pd.DataFrame()

    if ss.empty:
        return {"available": True, "data_as_of": cache["data_as_of"],
                "kpis": {}, "aum_pie": {}, "flow_charts": {},
                "aum_charts": {}, "volume_charts": {}}

    total_aum = float(ss["aum"].sum())
    issuer_col = "issuer"

    # Unique issuers and underliers
    issuers_unique = ss[issuer_col].dropna().unique().tolist()
    underlier_col = "map_li_underlier" if "map_li_underlier" in ss.columns else None
    underliers_unique = ss[underlier_col].dropna().unique().tolist() if underlier_col else []
    top_underlier = underliers_unique[0] if underliers_unique else "N/A"

    kpis = {
        "count": len(ss),
        "total_aum": _fmt_currency(total_aum),
        "issuers": len(issuers_unique),
        "top_underlier": str(top_underlier).replace(" US", ""),
    }

    # AUM pie by issuer
    aum_by_issuer = {}
    for issuer, grp in ss.groupby(issuer_col):
        if not issuer or (isinstance(issuer, float) and math.isnan(issuer)):
            continue
        aum_by_issuer[str(issuer)] = float(grp["aum"].sum())
    sorted_issuers = sorted(aum_by_issuer, key=aum_by_issuer.get, reverse=True)
    aum_pie = {
        "labels": sorted_issuers[:8] + (["Other"] if len(sorted_issuers) > 8 else []),
        "values": [round(aum_by_issuer[i], 1) for i in sorted_issuers[:8]],
    }
    if len(sorted_issuers) > 8:
        aum_pie["values"].append(round(sum(aum_by_issuer[i] for i in sorted_issuers[8:]), 1))

    # Build group maps
    ss_tickers = ss["ticker_clean"].tolist()
    issuer_map = dict(zip(ss["ticker_clean"], ss[issuer_col].fillna("Unknown").astype(str)))
    underlier_map = {}
    if underlier_col:
        underlier_map = dict(zip(
            ss["ticker_clean"],
            ss[underlier_col].fillna("Unknown").astype(str).str.replace(" US", "", regex=False)
        ))

    # Flow charts (cumulative)
    flow_total = _cumulative_flow(data_flow, ss_tickers, issuer_map, top_n=0)
    flow_by_issuer = _cumulative_flow(data_flow, ss_tickers, issuer_map, top_n=5)
    flow_by_underlier = _cumulative_flow(data_flow, ss_tickers, underlier_map, top_n=5)

    flow_charts = {
        "total": {"labels": flow_total["labels"], "datasets": flow_total["datasets"][:1]},
        "by_issuer": {"labels": flow_by_issuer["labels"], "datasets": flow_by_issuer["datasets"][1:]},
        "by_underlier": {"labels": flow_by_underlier["labels"], "datasets": flow_by_underlier["datasets"][1:]},
    }

    # AUM charts (daily last, grouped)
    aum_total = _daily_series(data_aum, ss_tickers, issuer_map, top_n=0)
    aum_by_iss = _daily_series(data_aum, ss_tickers, issuer_map, top_n=5)
    aum_by_und = _daily_series(data_aum, ss_tickers, underlier_map, top_n=5)

    aum_charts = {
        "total": {"labels": aum_total["labels"], "datasets": aum_total["datasets"][:1]},
        "by_issuer": {"labels": aum_by_iss["labels"], "datasets": aum_by_iss["datasets"][1:]},
        "by_underlier": {"labels": aum_by_und["labels"], "datasets": aum_by_und["datasets"][1:]},
    }

    # Volume charts (10D rolling avg)
    vol_total = _daily_series(data_notional, ss_tickers, issuer_map, top_n=0, rolling_window=10)
    vol_by_iss = _daily_series(data_notional, ss_tickers, issuer_map, top_n=5, rolling_window=10)
    vol_by_und = _daily_series(data_notional, ss_tickers, underlier_map, top_n=5, rolling_window=10)

    volume_charts = {
        "total": {"labels": vol_total["labels"], "datasets": vol_total["datasets"][:1]},
        "by_issuer": {"labels": vol_by_iss["labels"], "datasets": vol_by_iss["datasets"][1:]},
        "by_underlier": {"labels": vol_by_und["labels"], "datasets": vol_by_und["datasets"][1:]},
    }

    return {
        "available": True,
        "data_as_of": cache["data_as_of"],
        "kpis": kpis,
        "aum_pie": aum_pie,
        "flow_charts": flow_charts,
        "aum_charts": aum_charts,
        "volume_charts": volume_charts,
    }
