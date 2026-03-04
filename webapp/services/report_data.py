"""
Bloomberg report data service.

Provides data for three weekly reports:
- Leveraged & Inverse (L&I)
- Covered Call (CC)
- Single-Stock (SS)

On Render: reads pre-computed JSON from mkt_report_cache (zero memory).
Locally: computes from files via _get_cache() (used during sync).
"""
from __future__ import annotations

import json
import logging
import math
import os
import threading
import time
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from webapp.models import MktReportCache

# market.config imports are lazy (inside _load_all) so Render never loads
# openpyxl or resolves the Excel file path at import time.

_ON_RENDER = bool(os.environ.get("RENDER"))

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


def _optimise_dtypes(df: pd.DataFrame) -> None:
    """Downcast numeric columns to float32 and low-cardinality strings to category."""
    _CATEGORY_COLS = {"etp_category", "issuer_display", "issuer"}
    _SENTINEL_VALUES = ("Unknown", "")
    for col in df.columns:
        if col in _CATEGORY_COLS:
            cat = df[col].astype("category")
            missing = [v for v in _SENTINEL_VALUES if v not in cat.cat.categories]
            if missing:
                cat = cat.cat.add_categories(missing)
            df[col] = cat
        elif df[col].dtype == "float64":
            df[col] = df[col].astype("float32")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------
# Pre-exported CSV directory (created by scripts/render_build.sh)
_CSV_SHEETS_DIR = Path("data/DASHBOARD/sheets")


def _read_sheet(name: str, index_col: int | None = None) -> pd.DataFrame:
    """Read a sheet from pre-exported CSV (fast, low memory) or fall back to Excel."""
    csv_path = _CSV_SHEETS_DIR / f"{name}.csv"
    if csv_path.exists():
        log.info("Reading %s from CSV", name)
        return pd.read_csv(csv_path, index_col=index_col, engine="python", on_bad_lines="skip")

    # Fall back to Excel (local only — never reached on Render)
    from market.config import DATA_FILE
    path = DATA_FILE
    if not path.exists():
        return pd.DataFrame()
    log.info("Reading %s from Excel (slow path)", name)
    return pd.read_excel(path, sheet_name=name, engine="openpyxl", index_col=index_col)


def _load_all() -> dict[str, Any]:
    """Load Bloomberg sheets + rules CSVs, build master dataframe.

    Only called locally (never on Render — _ON_RENDER guards prevent it).
    """
    from market.config import DATA_FILE, RULES_DIR, W1_COL_MAP, W2_COL_MAP, W3_COL_MAP, W4_FLOW_COL_MAP

    # Check if we have CSVs or the Excel file
    has_csvs = (_CSV_SHEETS_DIR / "w1.csv").exists()
    has_excel = DATA_FILE.exists()

    if not has_csvs and not has_excel:
        log.warning("No data source found (no CSVs at %s, no Excel at %s)", _CSV_SHEETS_DIR, DATA_FILE)
        return {"available": False}

    source = "CSV" if has_csvs else "Excel"
    log.info("Loading report data from %s", source)

    if has_excel and not has_csvs:
        # Verify required sheets exist in the Excel file
        try:
            import openpyxl
            wb = openpyxl.load_workbook(DATA_FILE, read_only=True, data_only=True)
            available_sheets = set(wb.sheetnames)
            wb.close()
        except Exception as e:
            log.error("Cannot open data file %s: %s", DATA_FILE, e)
            return {"available": False}

        required = {"w1", "w2", "w3", "w4"}
        if not required.issubset(available_sheets):
            missing = required - available_sheets
            log.warning("Data file missing required sheets %s", missing)
            return {"available": False}

    # Read w1-w4
    try:
        w1 = _read_sheet("w1")
        w2 = _read_sheet("w2")
        w3 = _read_sheet("w3")
        w4_raw = _read_sheet("w4")
    except Exception as e:
        log.error("Error reading sheets: %s", e)
        return {"available": False}

    # Drop unnamed index column that to_csv(index=True) produces
    for sheet in [w1, w2, w3, w4_raw]:
        if sheet.columns[0].startswith("Unnamed"):
            sheet.drop(columns=[sheet.columns[0]], inplace=True)

    # Rename w1 columns
    w1 = w1.rename(columns=W1_COL_MAP)
    w1 = w1.dropna(subset=["ticker"])
    w1 = w1.drop_duplicates(subset=["ticker"], keep="first")

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
    _W4_SKIP = set(W4_FLOW_COL_MAP.values()) | {"ticker", "Ticker", "Fund Name"}
    aum_col = None
    for col in w4.columns:
        if col not in _W4_SKIP:
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
        data_aum = _read_sheet("data_aum", index_col=0)
        data_flow = _read_sheet("data_flow", index_col=0)
        data_notional = _read_sheet("data_notional", index_col=0)

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
    issuer_map = _read_csv(RULES_DIR / "issuer_mapping.csv")
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

    # Merge issuer_mapping -> issuer_display (friendly name)
    if not issuer_map.empty and {"etp_category", "issuer", "issuer_nickname"}.issubset(issuer_map.columns):
        im = issuer_map[["etp_category", "issuer", "issuer_nickname"]].drop_duplicates()
        issuer_col = "issuer" if "issuer" in master.columns else None
        if issuer_col:
            master = master.merge(im, on=["etp_category", "issuer"], how="left")
            master["issuer_display"] = master["issuer_nickname"].fillna(master["issuer"])
            master.drop(columns=["issuer_nickname"], inplace=True)
        else:
            master["issuer_display"] = master.get("issuer", "")
    else:
        master["issuer_display"] = master.get("issuer", "")

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
    data_as_of_short = ""
    if has_timeseries and len(data_aum) > 0:
        last_date = data_aum.index.max()
        if pd.notna(last_date):
            data_as_of = last_date.strftime("%B %d, %Y")
            data_as_of_short = last_date.strftime("%m/%d/%Y")

    log.info("Report data loaded: %d funds, timeseries=%s", len(master), has_timeseries)

    _optimise_dtypes(master)

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
        "issuer_map_df": issuer_map,
        "data_as_of": data_as_of,
        "data_as_of_short": data_as_of_short,
    }


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        log.warning("Rules file not found: %s", path)
        return pd.DataFrame()
    return pd.read_csv(path, engine="python", on_bad_lines="skip")


def _read_report_cache(db: Session | None, key: str) -> dict | None:
    """Read a pre-computed report from mkt_report_cache.

    Returns the deserialized dict if found, None otherwise.
    """
    if db is None:
        return None
    try:
        row = db.execute(
            select(MktReportCache).where(MktReportCache.report_key == key)
        ).scalar_one_or_none()
        if row and row.data_json:
            return json.loads(row.data_json)
    except Exception as e:
        log.debug("Report cache read failed for %s: %s", key, e)
    return None


def data_available(db: Session | None = None) -> bool:
    if db is not None:
        try:
            row = db.execute(
                select(MktReportCache.id).limit(1)
            ).scalar_one_or_none()
            if row is not None:
                return True
        except Exception:
            pass
    if _ON_RENDER:
        return False
    return _get_cache().get("available", False)


def get_data_as_of(db: Session | None = None) -> str:
    if db is not None:
        try:
            row = db.execute(
                select(MktReportCache.data_as_of).limit(1)
            ).scalar_one_or_none()
            if row:
                return row or ""
        except Exception:
            pass
    if _ON_RENDER:
        return ""
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
# KPI helper: WoW AUM change from time-series
# ---------------------------------------------------------------------------
def _compute_aum_wow(data_aum: pd.DataFrame, tickers: list[str]) -> tuple[str, bool]:
    """Compute week-over-week AUM change from the last 2 weekly observations.

    Returns (formatted_string, is_positive) e.g. ("+2.3% WoW", True).
    """
    if data_aum.empty or not tickers:
        return ("", True)

    available = [t for t in tickers if t in data_aum.columns]
    if not available:
        return ("", True)

    df = data_aum[available].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    # Resample to weekly (Friday) and take last observation
    weekly = df.resample("W-FRI").last()
    if len(weekly) < 2:
        return ("", True)

    latest_total = float(weekly.iloc[-1].sum())
    prev_total = float(weekly.iloc[-2].sum())
    if prev_total <= 0:
        return ("", True)

    pct_change = (latest_total / prev_total - 1) * 100
    sign = "+" if pct_change >= 0 else ""
    return (f"{sign}{pct_change:.1f}% WoW", pct_change >= 0)


# ---------------------------------------------------------------------------
# Email segment helpers (shared by L&I + CC for v3 emails)
# ---------------------------------------------------------------------------
def _compute_breakdown(df: pd.DataFrame, groupby_col: str, total_aum: float,
                       include_yield: bool = False,
                       include_direction: bool = False,
                       include_type: bool = False,
                       clean_suffix: str = "") -> list[dict]:
    """Compute grouped breakdown rows for an attribute column.

    Returns list sorted by AUM descending.  Optional enrichments:
    - include_direction: adds num_long / num_short (L&I)
    - include_type: adds num_traditional / num_synthetic (CC)
    - include_yield: adds avg_yield_fmt (CC)
    - clean_suffix: string to strip from group names (e.g. " US")
    """
    if df.empty or groupby_col not in df.columns:
        return []
    rows = []
    for name, grp in df.groupby(groupby_col, observed=True):
        if not name or (isinstance(name, float) and math.isnan(name)):
            continue
        label = str(name)
        if clean_suffix:
            label = label.replace(clean_suffix, "")
        aum = float(grp["aum"].sum())
        flow_1w = float(grp["fund_flow_1week"].sum())
        row = {
            "name": label,
            "count": len(grp),
            "aum": aum,
            "aum_fmt": _fmt_currency(aum),
            "flow_1w": flow_1w,
            "flow_1w_fmt": _fmt_flow(flow_1w),
            "market_share": (aum / total_aum * 100) if total_aum > 0 else 0.0,
        }
        if include_yield and "annualized_yield" in grp.columns:
            avg_y = float(grp["annualized_yield"].replace(0, float("nan")).mean())
            if math.isnan(avg_y):
                avg_y = 0.0
            row["avg_yield"] = avg_y
            row["avg_yield_fmt"] = _fmt_pct(avg_y)
        if include_direction and "map_li_direction" in grp.columns:
            direction = grp["map_li_direction"].str.lower()
            row["num_long"] = int(direction.isin(["long", "leveraged"]).sum())
            row["num_short"] = int(direction.isin(["short", "inverse"]).sum())
        if include_type and "cc_type" in grp.columns:
            row["num_traditional"] = int((grp["cc_type"] == "Traditional").sum())
            row["num_synthetic"] = int((grp["cc_type"] == "Synthetic").sum())
        rows.append(row)
    rows.sort(key=lambda x: x["aum"], reverse=True)
    return rows


def _compute_email_segment(df: pd.DataFrame, data_aum: pd.DataFrame,
                           include_yield: bool = False) -> dict:
    """Compute KPIs, issuers, top10/bottom10 for one segment of funds.

    Used by both get_li_report() and get_cc_report() to split
    Index/ETF/Basket vs Single Stock data for v3 report emails.
    """
    if df.empty:
        return {
            "kpis": {"count": 0, "total_aum": "$0",
                     "flow_1w": "$0", "flow_1w_positive": True,
                     "flow_ytd": "$0", "flow_ytd_positive": True},
            "issuers": [],
            "top10": [],
            "bottom10": [],
        }

    total_aum = float(df["aum"].sum())
    flow_1w = float(df["fund_flow_1week"].sum())
    flow_ytd = float(df["fund_flow_ytd"].sum())
    aum_change_1w, aum_change_positive = _compute_aum_wow(
        data_aum, df["ticker_clean"].tolist()
    )

    kpis = {
        "count": len(df),
        "total_aum": _fmt_currency(total_aum),
        "aum_change_1w": aum_change_1w,
        "aum_change_positive": aum_change_positive,
        "flow_1w": _fmt_flow(flow_1w),
        "flow_1w_positive": flow_1w >= 0,
        "flow_ytd": _fmt_flow(flow_ytd),
        "flow_ytd_positive": flow_ytd >= 0,
    }

    if include_yield and "annualized_yield" in df.columns:
        avg_yield = float(
            df["annualized_yield"].replace(0, float("nan")).mean()
        )
        if math.isnan(avg_yield):
            avg_yield = 0.0
        kpis["avg_yield"] = _fmt_pct(avg_yield)

    # Issuer breakdown
    issuer_col = (
        "issuer_display" if "issuer_display" in df.columns
        else ("issuer" if "issuer" in df.columns else "fund_name")
    )
    issuers = []
    for issuer, grp in df.groupby(issuer_col, observed=True):
        if not issuer or (isinstance(issuer, float) and math.isnan(issuer)):
            continue
        aum = float(grp["aum"].sum())
        iss = {
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
        }
        if include_yield and "annualized_yield" in grp.columns:
            avg_y = float(grp["annualized_yield"].replace(0, float("nan")).mean())
            if math.isnan(avg_y):
                avg_y = 0.0
            iss["avg_yield"] = avg_y
            iss["avg_yield_fmt"] = _fmt_pct(avg_y)
        issuers.append(iss)
    issuers.sort(key=lambda x: x["aum"], reverse=True)

    # Top 10 / Bottom 10 by 1W flow
    sorted_df = df.sort_values("fund_flow_1week", ascending=False)
    top10 = _segment_fund_rows(sorted_df.head(10), total_aum)
    bottom10 = _segment_fund_rows(
        sorted_df.tail(10).sort_values("fund_flow_1week", ascending=True),
        total_aum,
    )

    return {
        "kpis": kpis,
        "issuers": issuers,
        "top10": top10,
        "bottom10": bottom10,
    }


def _segment_fund_rows(df: pd.DataFrame, total_aum: float) -> list[dict]:
    """Build fund row dicts for segment display (email tables)."""
    rows = []
    for _, r in df.iterrows():
        ticker = str(r.get("ticker_clean", r.get("ticker", "")))
        aum = _safe_float(r.get("aum", 0))
        issuer = str(r.get("issuer_display", r.get("issuer", "")))
        rows.append({
            "ticker": ticker,
            "fund_name": str(r.get("fund_name", "")),
            "issuer": issuer,
            "aum": aum,
            "aum_fmt": _fmt_currency(aum),
            "flow_1w": _safe_float(r.get("fund_flow_1week", 0)),
            "flow_1w_fmt": _fmt_flow(_safe_float(r.get("fund_flow_1week", 0))),
            "return_1w": _safe_float(r.get("total_return_1week", 0)),
            "return_1w_fmt": _fmt_pct(_safe_float(r.get("total_return_1week", 0))),
            "yield_val": _safe_float(r.get("annualized_yield", 0)),
            "yield_fmt": _fmt_pct(_safe_float(r.get("annualized_yield", 0))),
            "is_rex": bool(r.get("is_rex", False)),
        })
    return rows


# ---------------------------------------------------------------------------
# L&I Report
# ---------------------------------------------------------------------------
def get_li_report(db: Session | None = None) -> dict:
    """Data for Leveraged & Inverse report.

    If db is provided, reads from mkt_report_cache (zero memory).
    Otherwise computes from files (used during local sync).
    """
    cached = _read_report_cache(db, "li_report")
    if cached is not None:
        return cached

    # On Render, never fall through to file-based cache (prevents OOM)
    if _ON_RENDER:
        log.warning("LI report: DB cache miss on Render, returning empty")
        return {"available": False, "data_as_of": "", "data_as_of_short": ""}

    cache = _get_cache()
    if not cache.get("available"):
        return {"available": False, "data_as_of": "", "data_as_of_short": ""}

    master = cache["master"].copy()
    data_aum = cache["data_aum"]
    data_flow = cache["data_flow"]
    data_notional = cache["data_notional"]
    rex_tickers = cache["rex_tickers"]

    # Filter to LI tickers (ETF only -- exclude ETNs)
    li = master[(master["etp_category"] == "LI") & (master["fund_type"] == "ETF")].copy()
    if li.empty:
        return {"available": True, "data_as_of": cache["data_as_of"], "data_as_of_short": cache.get("data_as_of_short", ""),
                "kpis": {}, "providers": [], "top10": [], "bottom10": [],
                "chart": {"labels": [], "datasets": [], "total": []},
                "flow_chart": {"labels": [], "datasets": []}}

    # KPIs
    total_aum = float(li["aum"].sum())
    flow_1w = float(li["fund_flow_1week"].sum())
    flow_ytd = float(li["fund_flow_ytd"].sum())

    # WoW AUM change from time-series
    aum_change_1w, aum_change_positive = _compute_aum_wow(data_aum, li["ticker_clean"].tolist())

    kpis = {
        "count": len(li),
        "total_aum": _fmt_currency(total_aum),
        "aum_change_1w": aum_change_1w,
        "aum_change_positive": aum_change_positive,
        "flow_1w": _fmt_flow(flow_1w),
        "flow_1w_positive": flow_1w >= 0,
        "flow_ytd": _fmt_flow(flow_ytd),
        "flow_ytd_positive": flow_ytd >= 0,
    }

    # Provider summary: group by issuer display name
    # Include leveraged/inverse split if map_li_direction is available
    issuer_col = "issuer_display" if "issuer_display" in li.columns else ("issuer" if "issuer" in li.columns else "fund_name")
    has_direction = "map_li_direction" in li.columns
    providers = []
    for issuer, grp in li.groupby(issuer_col, observed=True):
        if not issuer or (isinstance(issuer, float) and math.isnan(issuer)):
            continue
        aum = float(grp["aum"].sum())
        p = {
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
        }
        if has_direction:
            lev = grp[grp["map_li_direction"].str.lower().isin(["long", "leveraged"])]
            inv = grp[~grp["map_li_direction"].str.lower().isin(["long", "leveraged"])]
            p["num_leveraged"] = len(lev)
            p["num_inverse"] = len(inv)
            p["aum_leveraged"] = float(lev["aum"].sum())
            p["aum_leveraged_fmt"] = _fmt_currency(float(lev["aum"].sum()))
            p["aum_inverse"] = float(inv["aum"].sum())
            p["aum_inverse_fmt"] = _fmt_currency(float(inv["aum"].sum()))
            p["flow_1w_leveraged"] = float(lev["fund_flow_1week"].sum())
            p["flow_1w_leveraged_fmt"] = _fmt_flow(float(lev["fund_flow_1week"].sum()))
            p["flow_1w_inverse"] = float(inv["fund_flow_1week"].sum())
            p["flow_1w_inverse_fmt"] = _fmt_flow(float(inv["fund_flow_1week"].sum()))
        providers.append(p)
    providers.sort(key=lambda x: x["aum"], reverse=True)

    # Total row
    flow_1m_total = float(li["fund_flow_1month"].sum())
    total_row = {
        "issuer": "Total",
        "count": len(li),
        "aum": total_aum,
        "aum_fmt": _fmt_currency(total_aum),
        "flow_1w": flow_1w,
        "flow_1w_fmt": _fmt_flow(flow_1w),
        "flow_1m": flow_1m_total,
        "flow_1m_fmt": _fmt_flow(flow_1m_total),
        "flow_ytd": flow_ytd,
        "flow_ytd_fmt": _fmt_flow(flow_ytd),
        "market_share": 100.0,
        "is_rex": False,
    }
    if has_direction:
        lev_all = li[li["map_li_direction"].str.lower().isin(["long", "leveraged"])]
        inv_all = li[~li["map_li_direction"].str.lower().isin(["long", "leveraged"])]
        total_row["num_leveraged"] = len(lev_all)
        total_row["num_inverse"] = len(inv_all)
        total_row["aum_leveraged"] = float(lev_all["aum"].sum())
        total_row["aum_leveraged_fmt"] = _fmt_currency(float(lev_all["aum"].sum()))
        total_row["aum_inverse"] = float(inv_all["aum"].sum())
        total_row["aum_inverse_fmt"] = _fmt_currency(float(inv_all["aum"].sum()))
        total_row["flow_1w_leveraged"] = float(lev_all["fund_flow_1week"].sum())
        total_row["flow_1w_leveraged_fmt"] = _fmt_flow(float(lev_all["fund_flow_1week"].sum()))
        total_row["flow_1w_inverse"] = float(inv_all["fund_flow_1week"].sum())
        total_row["flow_1w_inverse_fmt"] = _fmt_flow(float(inv_all["fund_flow_1week"].sum()))

    # REX fund detail (for spotlight section)
    rex_li = li[li["is_rex"]].sort_values("aum", ascending=False)
    rex_funds_list = []
    for _, r in rex_li.iterrows():
        aum_val = _safe_float(r.get("aum", 0))
        rex_funds_list.append({
            "ticker": str(r.get("ticker_clean", "")),
            "fund_name": str(r.get("fund_name", "")),
            "aum": aum_val,
            "aum_fmt": _fmt_currency(aum_val),
            "flow_1w": _safe_float(r.get("fund_flow_1week", 0)),
            "flow_1w_fmt": _fmt_flow(_safe_float(r.get("fund_flow_1week", 0))),
            "flow_1m": _safe_float(r.get("fund_flow_1month", 0)),
            "flow_1m_fmt": _fmt_flow(_safe_float(r.get("fund_flow_1month", 0))),
            "yield_val": _safe_float(r.get("annualized_yield", 0)),
            "yield_fmt": _fmt_pct(_safe_float(r.get("annualized_yield", 0))),
        })

    # Top 10 / Bottom 10 by 1W flow
    li_sorted = li.sort_values("fund_flow_1week", ascending=False)
    top10 = _fund_rows(li_sorted.head(10), total_aum)
    bottom10 = _fund_rows(li_sorted.tail(10).sort_values("fund_flow_1week", ascending=True), total_aum)

    # Historical AUM chart (monthly, by issuer)
    li_tickers = li["ticker_clean"].tolist()
    issuer_grp_map = dict(zip(li["ticker_clean"], li[issuer_col].fillna("Unknown").astype(str)))
    chart = _monthly_aum_last(data_aum, li_tickers, issuer_grp_map, top_n=8)

    # Cumulative flow chart
    flow_chart = _cumulative_flow(data_flow, li_tickers, issuer_grp_map, top_n=5)

    # Daily charts (7D rolling flow, 10D rolling volume)
    daily_flow_chart = _daily_series(data_flow, li_tickers, issuer_grp_map, top_n=5, rolling_window=7)
    daily_volume_chart = _daily_series(data_notional, li_tickers, issuer_grp_map, top_n=5, rolling_window=10)

    # Segment split: Index/ETF/Basket vs Single Stock (for v3 emails)
    if "map_li_subcategory" in li.columns:
        ss_mask = li["map_li_subcategory"].str.lower() == "single stock"
        li_index_df = li[~ss_mask]
        li_ss_df = li[ss_mask]
    else:
        li_index_df = li
        li_ss_df = pd.DataFrame()
    index_seg = _compute_email_segment(li_index_df, data_aum)
    ss_seg = _compute_email_segment(li_ss_df, data_aum)

    # Attribute breakdowns for v3 emails
    idx_total_aum = float(li_index_df["aum"].sum()) if not li_index_df.empty else 0
    ss_total_aum = float(li_ss_df["aum"].sum()) if not li_ss_df.empty else 0

    # Index: category breakdown (Equity, Crypto, FI, etc.) with Long/Short
    index_by_category = _compute_breakdown(
        li_index_df, "map_li_category", idx_total_aum, include_direction=True,
    )
    # SS: top underliers (TSLA, NVDA, etc.)
    ss_by_underlier = _compute_breakdown(
        li_ss_df, "map_li_underlier", ss_total_aum,
        include_direction=True, clean_suffix=" US",
    )

    return {
        "available": True,
        "data_as_of": cache["data_as_of"], "data_as_of_short": cache.get("data_as_of_short", ""),
        "kpis": kpis,
        "providers": providers,
        "total_row": total_row,
        "rex_funds": rex_funds_list,
        "top10": top10,
        "bottom10": bottom10,
        "chart": chart,
        "flow_chart": flow_chart,
        "daily_flow_chart": daily_flow_chart,
        "daily_volume_chart": daily_volume_chart,
        # V3 email segments
        "index_kpis": index_seg["kpis"],
        "ss_kpis": ss_seg["kpis"],
        "index_issuers": index_seg["issuers"],
        "ss_issuers": ss_seg["issuers"],
        "index_top10": index_seg["top10"],
        "index_bottom10": index_seg["bottom10"],
        "ss_top10": ss_seg["top10"],
        "ss_bottom10": ss_seg["bottom10"],
        "index_by_category": index_by_category,
        "ss_by_underlier": ss_by_underlier,
    }


def _fund_rows(df: pd.DataFrame, total_aum: float) -> list[dict]:
    rows = []
    for _, r in df.iterrows():
        ticker = str(r.get("ticker_clean", r.get("ticker", "")))
        aum = _safe_float(r.get("aum", 0))
        issuer_display = str(r.get("issuer_display", r.get("issuer", "")))
        # Product type: prefer explicit ss_product_type (for SS report), else derive
        ss_type = str(r.get("ss_product_type", ""))
        direction = str(r.get("map_li_direction", "")).lower()
        leverage = _safe_float(r.get("map_li_leverage_amount", 0))
        if ss_type == "Covered Call":
            ptype = "Covered Call"
            lev_factor = ""
        elif direction in ("short", "inverse"):
            ptype = "Inverse"
            lev_factor = f"-{leverage * 100:.0f}%" if leverage else ""
        else:
            ptype = "Leveraged"
            lev_factor = f"{leverage * 100:.0f}%" if leverage else ""
        rows.append({
            "ticker": ticker,
            "fund_name": str(r.get("fund_name", "")),
            "issuer": issuer_display,
            "product_type": ptype,
            "leverage_factor": lev_factor,
            "aum": aum,
            "aum_fmt": _fmt_currency(aum),
            "flow_1d": _safe_float(r.get("fund_flow_1day", 0)),
            "flow_1d_fmt": _fmt_flow(_safe_float(r.get("fund_flow_1day", 0))),
            "flow_1w": _safe_float(r.get("fund_flow_1week", 0)),
            "flow_1w_fmt": _fmt_flow(_safe_float(r.get("fund_flow_1week", 0))),
            "flow_1m": _safe_float(r.get("fund_flow_1month", 0)),
            "flow_1m_fmt": _fmt_flow(_safe_float(r.get("fund_flow_1month", 0))),
            "return_1w": _safe_float(r.get("total_return_1week", 0)),
            "return_1w_fmt": _fmt_pct(_safe_float(r.get("total_return_1week", 0))),
            "return_1m": _safe_float(r.get("total_return_1month", 0)),
            "return_1m_fmt": _fmt_pct(_safe_float(r.get("total_return_1month", 0))),
            "market_share": (aum / total_aum * 100) if total_aum > 0 else 0.0,
            "is_rex": bool(r.get("is_rex", False)),
        })
    return rows


# ---------------------------------------------------------------------------
# Covered Call Report
# ---------------------------------------------------------------------------
def get_cc_report(db: Session | None = None) -> dict:
    """Data for Covered Call report.

    If db is provided, reads from mkt_report_cache (zero memory).
    Otherwise computes from files (used during local sync).
    """
    cached = _read_report_cache(db, "cc_report")
    if cached is not None:
        return cached

    if _ON_RENDER:
        log.warning("CC report: DB cache miss on Render, returning empty")
        return {"available": False, "data_as_of": "", "data_as_of_short": ""}

    cache = _get_cache()
    if not cache.get("available"):
        return {"available": False, "data_as_of": "", "data_as_of_short": ""}

    master = cache["master"].copy()
    data_aum = cache["data_aum"]
    data_flow = cache["data_flow"]
    data_notional = cache["data_notional"]
    rex_tickers = cache["rex_tickers"]

    # Filter to CC tickers
    cc = master[master["etp_category"] == "CC"].copy()
    if cc.empty:
        return {"available": True, "data_as_of": cache["data_as_of"], "data_as_of_short": cache.get("data_as_of_short", ""),
                "kpis": {}, "rex_funds": [], "top_flow_segments": {},
                "top_yield_segments": {}, "aum_by_category": [],
                "issuers": [], "all_products": [],
                "issuer_aum_chart": {}, "rex_ts_chart": {},
                "flow_chart": {"labels": [], "datasets": []}}

    total_aum = float(cc["aum"].sum())
    flow_1w = float(cc["fund_flow_1week"].sum())
    avg_yield = float(cc["annualized_yield"].replace(0, float("nan")).mean()) if "annualized_yield" in cc.columns else 0.0

    # WoW AUM change from time-series
    aum_change_1w, aum_change_positive = _compute_aum_wow(data_aum, cc["ticker_clean"].tolist())

    kpis = {
        "count": len(cc),
        "total_aum": _fmt_currency(total_aum),
        "aum_change_1w": aum_change_1w,
        "aum_change_positive": aum_change_positive,
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
        for cat, grp in cc.groupby("cc_category", observed=True):
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
    issuer_col = "issuer_display" if "issuer_display" in cc.columns else "issuer"
    for issuer, grp in cc.groupby(issuer_col, observed=True):
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

    # Chart 3: Cumulative flow chart
    cc_issuer_map = dict(zip(cc["ticker_clean"], cc[issuer_col].fillna("Unknown").astype(str)))
    flow_chart = _cumulative_flow(data_flow, cc_tickers, cc_issuer_map, top_n=5)

    # Daily charts (7D rolling flow, 10D rolling volume)
    daily_flow_chart = _daily_series(data_flow, cc_tickers, cc_issuer_map, top_n=5, rolling_window=7)
    daily_volume_chart = _daily_series(data_notional, cc_tickers, cc_issuer_map, top_n=5, rolling_window=10)

    # Segment split: Index/ETF/Basket vs Single Stock (for v3 emails)
    if "cc_category" in cc.columns:
        ss_mask = cc["cc_category"] == "Single Stock"
        cc_index_df = cc[~ss_mask]
        cc_ss_df = cc[ss_mask]
    else:
        cc_index_df = cc
        cc_ss_df = pd.DataFrame()
    cc_index_seg = _compute_email_segment(cc_index_df, data_aum, include_yield=True)
    cc_ss_seg = _compute_email_segment(cc_ss_df, data_aum, include_yield=True)

    # Attribute breakdowns for v3 emails
    idx_total_aum = float(cc_index_df["aum"].sum()) if not cc_index_df.empty else 0
    ss_total_aum = float(cc_ss_df["aum"].sum()) if not cc_ss_df.empty else 0

    # Index: category breakdown (Broad Beta, Tech, Crypto, etc.) with Trad/Synth + yield
    index_by_category = _compute_breakdown(
        cc_index_df, "cc_category", idx_total_aum,
        include_yield=True, include_type=True,
    )
    # SS: top underliers with yield
    ss_by_underlier = _compute_breakdown(
        cc_ss_df, "map_cc_underlier", ss_total_aum,
        include_yield=True, include_type=True, clean_suffix=" US",
    )

    return {
        "available": True,
        "data_as_of": cache["data_as_of"], "data_as_of_short": cache.get("data_as_of_short", ""),
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
        "flow_chart": flow_chart,
        "daily_flow_chart": daily_flow_chart,
        "daily_volume_chart": daily_volume_chart,
        # V3 email segments
        "index_kpis": cc_index_seg["kpis"],
        "ss_kpis": cc_ss_seg["kpis"],
        "index_issuers": cc_index_seg["issuers"],
        "ss_issuers": cc_ss_seg["issuers"],
        "index_top10": cc_index_seg["top10"],
        "index_bottom10": cc_index_seg["bottom10"],
        "ss_top10": cc_ss_seg["top10"],
        "ss_bottom10": cc_ss_seg["bottom10"],
        "index_by_category": index_by_category,
        "ss_by_underlier": ss_by_underlier,
    }


def _cc_fund_rows(df: pd.DataFrame) -> list[dict]:
    rows = []
    for _, r in df.iterrows():
        aum = _safe_float(r.get("aum", 0))
        issuer_display = str(r.get("issuer_display", r.get("issuer", "")))
        rows.append({
            "ticker": str(r.get("ticker_clean", "")),
            "fund_name": str(r.get("fund_name", "")),
            "issuer": issuer_display,
            "is_rex": bool(r.get("is_rex", False)),
            "cc_type": str(r.get("cc_type", "")),
            "cc_category": str(r.get("cc_category", "")),
            "aum": aum,
            "aum_fmt": _fmt_currency(aum),
            "flow_1w": _safe_float(r.get("fund_flow_1week", 0)),
            "flow_1w_fmt": _fmt_flow(_safe_float(r.get("fund_flow_1week", 0))),
            "flow_1m": _safe_float(r.get("fund_flow_1month", 0)),
            "flow_1m_fmt": _fmt_flow(_safe_float(r.get("fund_flow_1month", 0))),
            "return_1w": _safe_float(r.get("total_return_1week", 0)),
            "return_1w_fmt": _fmt_pct(_safe_float(r.get("total_return_1week", 0))),
            "return_1m": _safe_float(r.get("total_return_1month", 0)),
            "return_1m_fmt": _fmt_pct(_safe_float(r.get("total_return_1month", 0))),
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
def get_ss_report(db: Session | None = None) -> dict:
    """Data for Single-Stock Leveraged ETFs report.

    If db is provided, reads from mkt_report_cache (zero memory).
    Otherwise computes from files (used during local sync).
    """
    cached = _read_report_cache(db, "ss_report")
    if cached is not None:
        return cached

    if _ON_RENDER:
        log.warning("SS report: DB cache miss on Render, returning empty")
        return {"available": False, "data_as_of": "", "data_as_of_short": ""}

    cache = _get_cache()
    if not cache.get("available"):
        return {"available": False, "data_as_of": "", "data_as_of_short": ""}

    master = cache["master"].copy()
    data_aum = cache["data_aum"]
    data_flow = cache["data_flow"]
    data_notional = cache["data_notional"]

    # Filter to single-stock ETFs: leveraged (LI subcategory) + covered call (CC category)
    # ETF only -- exclude ETNs
    ss_li = master[
        (master["etp_category"] == "LI") &
        (master["fund_type"] == "ETF") &
        (master.get("map_li_subcategory", pd.Series(dtype=str)).str.lower() == "single stock")
    ].copy() if "map_li_subcategory" in master.columns else pd.DataFrame()

    ss_cc = master[
        (master["etp_category"] == "CC") &
        (master["fund_type"] == "ETF") &
        (master.get("cc_category", pd.Series(dtype=str)) == "Single Stock")
    ].copy() if "cc_category" in master.columns else pd.DataFrame()

    # Tag product type for display
    if not ss_li.empty:
        ss_li["ss_product_type"] = "Leveraged"
    if not ss_cc.empty:
        ss_cc["ss_product_type"] = "Covered Call"

    ss = pd.concat([ss_li, ss_cc], ignore_index=True)

    if ss.empty:
        return {"available": True, "data_as_of": cache["data_as_of"], "data_as_of_short": cache.get("data_as_of_short", ""),
                "kpis": {}, "aum_pie": {}, "flow_charts": {},
                "aum_charts": {}, "volume_charts": {}}

    total_aum = float(ss["aum"].sum())
    issuer_col = "issuer_display" if "issuer_display" in ss.columns else "issuer"

    # Unique issuers and underliers
    issuers_unique = ss[issuer_col].dropna().unique().tolist()
    underlier_col = "map_li_underlier" if "map_li_underlier" in ss.columns else None
    # For CC, underlier is in map_cc_underlier
    cc_underlier_col = "map_cc_underlier" if "map_cc_underlier" in ss.columns else None
    all_underliers = set()
    if underlier_col:
        all_underliers.update(ss[underlier_col].dropna().unique())
    if cc_underlier_col:
        all_underliers.update(ss[cc_underlier_col].dropna().unique())
    underliers_unique = sorted(all_underliers)
    top_underlier = underliers_unique[0] if underliers_unique else "N/A"

    # Segment counts for KPIs
    num_leveraged = len(ss_li)
    num_cc = len(ss_cc)

    # WoW AUM change
    aum_change_1w, aum_change_positive = _compute_aum_wow(data_aum, ss["ticker_clean"].tolist())

    kpis = {
        "count": len(ss),
        "num_leveraged": num_leveraged,
        "num_cc": num_cc,
        "total_aum": _fmt_currency(total_aum),
        "aum_leveraged": _fmt_currency(float(ss_li["aum"].sum())) if not ss_li.empty else "$0",
        "aum_cc": _fmt_currency(float(ss_cc["aum"].sum())) if not ss_cc.empty else "$0",
        "aum_change_1w": aum_change_1w,
        "aum_change_positive": aum_change_positive,
        "issuers": len(issuers_unique),
        "top_underlier": str(top_underlier).replace(" US", ""),
    }

    # AUM pie by issuer
    aum_by_issuer = {}
    for issuer, grp in ss.groupby(issuer_col, observed=True):
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

    # Provider summary (for email)
    ss_providers = []
    for issuer, grp in ss.groupby(issuer_col, observed=True):
        if not issuer or (isinstance(issuer, float) and math.isnan(issuer)):
            continue
        aum = float(grp["aum"].sum())
        ss_providers.append({
            "issuer": str(issuer),
            "count": len(grp),
            "aum": aum,
            "aum_fmt": _fmt_currency(aum),
            "flow_1w": float(grp["fund_flow_1week"].sum()),
            "flow_1w_fmt": _fmt_flow(float(grp["fund_flow_1week"].sum())),
            "flow_1m": float(grp["fund_flow_1month"].sum()),
            "flow_1m_fmt": _fmt_flow(float(grp["fund_flow_1month"].sum())),
            "market_share": (aum / total_aum * 100) if total_aum > 0 else 0.0,
            "is_rex": bool(grp["is_rex"].any()),
        })
    ss_providers.sort(key=lambda x: x["aum"], reverse=True)

    # Underlier summary (top 15 by AUM)
    underlier_summary = []
    ss_copy = ss.copy()
    # Merge LI and CC underlier columns
    if underlier_col:
        ss_copy["_underlier"] = ss_copy[underlier_col].fillna("")
        if cc_underlier_col:
            mask = ss_copy["_underlier"] == ""
            ss_copy.loc[mask, "_underlier"] = ss_copy.loc[mask, cc_underlier_col].fillna("")
    elif cc_underlier_col:
        ss_copy["_underlier"] = ss_copy[cc_underlier_col].fillna("")
    else:
        ss_copy["_underlier"] = ""
    ss_copy["_underlier"] = ss_copy["_underlier"].astype(str).str.replace(" US", "", regex=False)
    for und, grp in ss_copy[ss_copy["_underlier"] != ""].groupby("_underlier", observed=True):
        if not und:
            continue
        u_aum = float(grp["aum"].sum())
        underlier_summary.append({
            "underlier": str(und),
            "count": int(grp["ticker_clean"].nunique()),
            "aum": u_aum,
            "aum_fmt": _fmt_currency(u_aum),
            "flow_1w": float(grp["fund_flow_1week"].sum()),
            "flow_1w_fmt": _fmt_flow(float(grp["fund_flow_1week"].sum())),
            "market_share": (u_aum / total_aum * 100) if total_aum > 0 else 0.0,
        })
    underlier_summary.sort(key=lambda x: x["aum"], reverse=True)
    underlier_summary = underlier_summary[:15]

    # REX SS products (for spotlight section)
    rex_ss = ss[ss["is_rex"]].sort_values("aum", ascending=False)
    rex_ss_funds = []
    for _, r in rex_ss.iterrows():
        aum_val = _safe_float(r.get("aum", 0))
        rex_ss_funds.append({
            "ticker": str(r.get("ticker_clean", "")),
            "fund_name": str(r.get("fund_name", "")),
            "aum": aum_val,
            "aum_fmt": _fmt_currency(aum_val),
            "flow_1w": _safe_float(r.get("fund_flow_1week", 0)),
            "flow_1w_fmt": _fmt_flow(_safe_float(r.get("fund_flow_1week", 0))),
            "flow_1m": _safe_float(r.get("fund_flow_1month", 0)),
            "flow_1m_fmt": _fmt_flow(_safe_float(r.get("fund_flow_1month", 0))),
            "yield_val": _safe_float(r.get("annualized_yield", 0)),
            "yield_fmt": _fmt_pct(_safe_float(r.get("annualized_yield", 0))),
        })

    # Top 10 / Bottom 10 by 1W flow
    ss_sorted = ss.sort_values("fund_flow_1week", ascending=False)
    ss_top10 = _fund_rows(ss_sorted.head(10), total_aum)
    ss_bottom10 = _fund_rows(ss_sorted.tail(10).sort_values("fund_flow_1week", ascending=True), total_aum)

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
        "data_as_of": cache["data_as_of"], "data_as_of_short": cache.get("data_as_of_short", ""),
        "kpis": kpis,
        "aum_pie": aum_pie,
        "providers": ss_providers,
        "underlier_summary": underlier_summary,
        "rex_funds": rex_ss_funds,
        "top10": ss_top10,
        "bottom10": ss_bottom10,
        "flow_charts": flow_charts,
        "aum_charts": aum_charts,
        "volume_charts": volume_charts,
    }
