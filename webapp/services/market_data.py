"""
Market Intelligence data service.

Loads fund universe and time series from SQLite (mkt_master_data, mkt_time_series).
Data is written to SQLite by market_sync.py during the local daily pipeline.
All functions accept a SQLAlchemy Session and return dicts/DataFrames.
Uses a thread-safe in-memory cache (event-driven invalidation) so tab navigation is instant.
"""
from __future__ import annotations

import json
import logging
import math
import threading
from typing import Any

from datetime import datetime as _dt

import pandas as pd
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from webapp.models import MktMasterData, MktPipelineRun, MktTimeSeries

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# In-memory cache (loaded once, reused across requests)
# ---------------------------------------------------------------------------
_master_lock = threading.Lock()
_master_df: pd.DataFrame | None = None

_ts_lock = threading.Lock()
_ts_df: pd.DataFrame | None = None




# ---------------------------------------------------------------------------
# Column mapping: flat DB names -> legacy prefixed names (backward compat)
# ---------------------------------------------------------------------------
_FLAT_TO_PREFIXED = {
    # W2 metrics
    "expense_ratio": "t_w2.expense_ratio",
    "management_fee": "t_w2.management_fee",
    "average_bidask_spread": "t_w2.average_bidask_spread",
    "nav_tracking_error": "t_w2.nav_tracking_error",
    "percentage_premium": "t_w2.percentage_premium",
    "average_percent_premium_52week": "t_w2.average_percent_premium_52week",
    "average_vol_30day": "t_w2.average_vol_30day",
    "percent_short_interest": "t_w2.percent_short_interest",
    "open_interest": "t_w2.open_interest",
    # W3 returns
    "total_return_1day": "t_w3.total_return_1day",
    "total_return_1week": "t_w3.total_return_1week",
    "total_return_1month": "t_w3.total_return_1month",
    "total_return_3month": "t_w3.total_return_3month",
    "total_return_6month": "t_w3.total_return_6month",
    "total_return_ytd": "t_w3.total_return_ytd",
    "total_return_1year": "t_w3.total_return_1year",
    "total_return_3year": "t_w3.total_return_3year",
    "annualized_yield": "t_w3.annualized_yield",
    # W4 flows + AUM
    "fund_flow_1day": "t_w4.fund_flow_1day",
    "fund_flow_1week": "t_w4.fund_flow_1week",
    "fund_flow_1month": "t_w4.fund_flow_1month",
    "fund_flow_3month": "t_w4.fund_flow_3month",
    "fund_flow_6month": "t_w4.fund_flow_6month",
    "fund_flow_ytd": "t_w4.fund_flow_ytd",
    "fund_flow_1year": "t_w4.fund_flow_1year",
    "fund_flow_3year": "t_w4.fund_flow_3year",
    "aum": "t_w4.aum",
    # Category attributes
    "map_li_category": "q_category_attributes.map_li_category",
    "map_li_subcategory": "q_category_attributes.map_li_subcategory",
    "map_li_direction": "q_category_attributes.map_li_direction",
    "map_li_leverage_amount": "q_category_attributes.map_li_leverage_amount",
    "map_li_underlier": "q_category_attributes.map_li_underlier",
    "map_cc_underlier": "q_category_attributes.map_cc_underlier",
    "map_cc_index": "q_category_attributes.map_cc_index",
    "map_crypto_type": "q_category_attributes.map_crypto_type",
    "map_crypto_underlier": "q_category_attributes.map_crypto_underlier",
    "map_defined_category": "q_category_attributes.map_defined_category",
    "map_thematic_category": "q_category_attributes.map_thematic_category",
    "cc_type": "q_category_attributes.cc_type",
    "cc_category": "q_category_attributes.cc_category",
}


# ---------------------------------------------------------------------------
# DB -> DataFrame loaders (transient per-request, freed after)
# ---------------------------------------------------------------------------
_EMPTY_MASTER_COLS = [
    "ticker", "fund_name", "issuer", "etp_category", "category_display",
    "issuer_display", "is_rex", "ticker_clean", "fund_type",
    "t_w4.aum", "t_w4.fund_flow_1week", "t_w4.fund_flow_1month",
    "t_w4.fund_flow_ytd", "primary_category", "rex_suite",
]


def _apply_etn_overrides(df: pd.DataFrame) -> pd.DataFrame:
    """Apply MicroSectors ETN proprietary overrides (for internal reports only).

    Bloomberg reports total issuance (not true AUM) and zero flows for ETNs.
    This reads the 'microsector' + 'data_ms'/'data_price' sheets to compute true values.
    Returns the same DataFrame (modified in-place).
    """
    try:
        from market.microsectors import read_overrides, apply_overrides
        from market.config import DATA_FILE
        if DATA_FILE.exists():
            xl = pd.ExcelFile(DATA_FILE, engine="openpyxl")
            if "microsector" in xl.sheet_names:
                ov = read_overrides(xl)
                if ov:
                    apply_overrides(df, ov)
    except Exception as e:
        log.warning("ETN override failed (non-fatal): %s", e)
    return df


def _load_master(db: Session) -> pd.DataFrame:
    """Load mkt_master_data into a DataFrame with legacy prefixed column names.

    Double-checked locking: only one thread loads from DB.
    Cache lives until explicitly invalidated (no TTL).
    """
    global _master_df
    if _master_df is not None:
        return _master_df
    with _master_lock:
        if _master_df is not None:
            return _master_df
        _master_df = _load_master_from_db(db)
        log.info("Master data cached: %d rows", len(_master_df))
        return _master_df


def _load_master_from_db(db: Session) -> pd.DataFrame:
    """Actual DB load (called once, then cached).

    Uses pd.read_sql to avoid loading 43K ORM objects into memory.
    Peak memory is ~3x lower than the ORM .all() approach.
    """
    try:
        conn = db.get_bind()
        df = pd.read_sql("SELECT * FROM mkt_master_data", conn)
    except Exception as e:
        log.error("Failed to query mkt_master_data: %s", e)
        return pd.DataFrame(columns=_EMPTY_MASTER_COLS)
    if df.empty:
        return pd.DataFrame(columns=_EMPTY_MASTER_COLS)

    # Drop the auto-increment id column
    if "id" in df.columns:
        df = df.drop(columns=["id"])

    # Unpack AUM history JSON into individual columns (vectorized)
    if "aum_history_json" in df.columns:
        mask = df["aum_history_json"].notna() & (df["aum_history_json"] != "")
        if mask.any():
            parsed = df.loc[mask, "aum_history_json"].apply(
                lambda s: json.loads(s) if s else {}
            )
            history_df = pd.DataFrame(parsed.tolist(), index=parsed.index)
            for col in history_df.columns:
                df[col] = history_df[col]

    # Rename flat columns to prefixed names for backward compat
    rename = {}
    for flat, prefixed in _FLAT_TO_PREFIXED.items():
        if flat in df.columns:
            rename[flat] = prefixed
    # AUM history columns
    for i in range(1, 37):
        key = f"aum_{i}"
        if key in df.columns:
            rename[key] = f"t_w4.{key}"
    df = df.rename(columns=rename)

    # Normalize types
    if "is_rex" in df.columns:
        df["is_rex"] = df["is_rex"].fillna(False).astype(bool)

    # Numeric coercions for metric columns
    _NUMERIC = [
        "t_w4.aum",
        "t_w4.fund_flow_1day", "t_w4.fund_flow_1week",
        "t_w4.fund_flow_1month", "t_w4.fund_flow_3month",
        "t_w4.fund_flow_6month", "t_w4.fund_flow_ytd",
        "t_w4.fund_flow_1year", "t_w4.fund_flow_3year",
        "t_w3.total_return_1week", "t_w3.total_return_1month",
        "t_w3.annualized_yield",
        "t_w2.expense_ratio",
        "t_w2.average_vol_30day",
        "t_w2.average_bidask_spread",
    ] + [f"t_w4.aum_{i}" for i in range(1, 37)]
    for col in _NUMERIC:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    return df


def _load_ts(db: Session) -> pd.DataFrame:
    """Load mkt_time_series into a DataFrame.

    Double-checked locking: only one thread loads from DB.
    Cache lives until explicitly invalidated (no TTL).
    """
    global _ts_df
    if _ts_df is not None:
        return _ts_df
    with _ts_lock:
        if _ts_df is not None:
            return _ts_df
        _ts_df = _load_ts_from_db(db)
        log.info("Time series cached: %d rows", len(_ts_df))
        return _ts_df


def _load_ts_from_db(db: Session) -> pd.DataFrame:
    """Actual DB load for time series (called once, then cached).

    Uses pd.read_sql to avoid loading 285K ORM objects into memory.
    Peak memory is ~3x lower than the ORM .all() approach.
    Zeros out AUM for months before a product's inception date.
    """
    try:
        conn = db.get_bind()
        df = pd.read_sql("SELECT * FROM mkt_time_series", conn)
    except Exception as e:
        log.error("Failed to query mkt_time_series: %s", e)
        return pd.DataFrame()
    if df.empty:
        return pd.DataFrame()

    # Drop the auto-increment id column
    if "id" in df.columns:
        df = df.drop(columns=["id"])

    if "is_rex" in df.columns:
        df["is_rex"] = df["is_rex"].fillna(False).astype(bool)
    if "aum_value" in df.columns:
        df["aum_value"] = pd.to_numeric(df["aum_value"], errors="coerce").fillna(0.0)

    # Synthesize a date column from months_ago for backward compat
    if "months_ago" in df.columns:
        now = pd.Timestamp(_dt.now().date())
        df["date"] = df["months_ago"].apply(lambda m: now - pd.DateOffset(months=int(m)))

    # Zero out AUM for months before inception (Bloomberg backfills stale data)
    try:
        incep_df = pd.read_sql(
            "SELECT ticker, inception_date FROM mkt_master_data", conn
        )
        if not incep_df.empty:
            incep_df["inception_date"] = pd.to_datetime(
                incep_df["inception_date"], errors="coerce"
            )
            incep_df = incep_df.dropna(subset=["inception_date"])
            df = df.merge(incep_df, on="ticker", how="left")
            pre = df["date"] < df["inception_date"]
            zeroed = (pre & (df["aum_value"] > 0)).sum()
            if zeroed:
                df.loc[pre, "aum_value"] = 0.0
                log.info("Zeroed %d pre-inception AUM values in time series", zeroed)
            df = df.drop(columns=["inception_date"])
    except Exception as e:
        log.warning("Pre-inception zeroing failed (non-fatal): %s", e)

    return df


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------
def invalidate_cache() -> None:
    """Clear cached DataFrames so next request reloads from DB."""
    global _master_df, _ts_df
    with _master_lock:
        _master_df = None
    with _ts_lock:
        _ts_df = None


def data_available(db: Session) -> bool:
    """Return True if market data exists in the database."""
    result = db.execute(text("SELECT 1 FROM mkt_master_data LIMIT 1")).first()
    return result is not None


def get_data_as_of(db: Session) -> str:
    """Return data date as 'March 10, 2026' -- prefers time series date over pipeline timestamp."""
    # Prefer actual data date from time series (last Bloomberg data point)
    try:
        ts_row = db.execute(
            select(MktTimeSeries.as_of_date)
            .where(MktTimeSeries.as_of_date.isnot(None))
            .order_by(MktTimeSeries.as_of_date.desc())
            .limit(1)
        ).scalar()
        if ts_row and hasattr(ts_row, "strftime"):
            return ts_row.strftime("%B %d, %Y")
    except Exception:
        pass
    # Fallback: pipeline run timestamp
    row = db.execute(
        select(MktPipelineRun.finished_at)
        .where(MktPipelineRun.status == "completed")
        .order_by(MktPipelineRun.finished_at.desc())
        .limit(1)
    ).scalar()
    if row:
        return row.strftime("%B %d, %Y")
    return ""


def get_master_data(db: Session, etn_overrides: bool = False) -> pd.DataFrame:
    """Return full fund universe as DataFrame (cached).

    Args:
        etn_overrides: If True, return a copy with MicroSectors ETN proprietary
            data (true AUM + flows).  Only for internal reports/emails.
    """
    df = _load_master(db)
    if etn_overrides:
        df = df.copy()
        _apply_etn_overrides(df)
    return df


def get_time_series_df(db: Session) -> pd.DataFrame:
    """Return full time series as DataFrame (cached, 1h TTL)."""
    return _load_ts(db)


def _fmt_currency(val: float) -> str:
    """Format a value in millions: returns '$X,XXX.XB' or '$X.XM' with commas."""
    if val is None or (isinstance(val, float) and math.isnan(val)):
        return "$0"
    if abs(val) >= 1_000:
        b = val / 1_000
        return f"${b:,.1f}B"
    if abs(val) >= 1:
        return f"${val:,.1f}M"
    return f"${val:.2f}M"


def _fmt_flow(val: float) -> str:
    sign = "+" if val >= 0 else ""
    return f"{sign}{_fmt_currency(val)}"


def _is_actv(df: pd.DataFrame) -> pd.Series:
    """Return boolean mask: True for rows where market_status is 'ACTV' or missing."""
    if "market_status" not in df.columns:
        return pd.Series(True, index=df.index)
    return df["market_status"].fillna("ACTV").str.strip().str.upper() == "ACTV"


def get_kpis(df: pd.DataFrame) -> dict:
    """Calculate standard KPIs from a filtered dataframe (values in $M).

    Product count uses ACTV filter (excludes liquidated/delisted).
    AUM/flow totals include all funds (liquidated = $0 anyway).
    """
    total_aum = float(df["t_w4.aum"].sum()) if "t_w4.aum" in df.columns else 0.0
    flow_1w = float(df["t_w4.fund_flow_1week"].sum()) if "t_w4.fund_flow_1week" in df.columns else 0.0
    flow_1m = float(df["t_w4.fund_flow_1month"].sum()) if "t_w4.fund_flow_1month" in df.columns else 0.0
    flow_3m = float(df["t_w4.fund_flow_3month"].sum()) if "t_w4.fund_flow_3month" in df.columns else 0.0
    actv_count = int(_is_actv(df).sum())
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
        "count": actv_count,
        "num_products": actv_count,  # alias for templates/JS
        "flow_1w_positive": flow_1w >= 0,
        "flow_1m_positive": flow_1m >= 0,
        "flow_3m_positive": flow_3m >= 0,
    }


#  REX Summary 

_SUITE_ORDER = [
    "T-REX",
    "MicroSectors",
    "Equity Premium Income",
    "Growth & Income",
    "IncomeMax",
    "Autocallable",
    "Crypto",
    "T-Bill",
    "Thematic",
]


def get_rex_summary(db: Session, fund_structure: str | None = None, category: str | None = None, etn_overrides: bool = False) -> dict:
    """Return REX overall KPIs + per-suite breakdown.

    Args:
        db: SQLAlchemy session.
        fund_structure: "ETF", "ETN", "ETF,ETN", or "all" to filter by fund type.
        category: If set (and not "All"), filter to only REX products in that category.
        etn_overrides: If True, use MicroSectors ETN proprietary data (internal reports only).
    """
    df = _load_master(db)
    if etn_overrides:
        df = df.copy()
        _apply_etn_overrides(df)

    # ETF/ETN filter (supports comma-separated multi-select)
    if fund_structure and fund_structure != "all":
        fund_type_col = next((c for c in df.columns if c.lower().strip() == "fund_type"), None)
        if fund_type_col:
            types = [t.strip() for t in fund_structure.split(",") if t.strip()]
            df = df[df[fund_type_col].isin(types)].copy()

    all_cats = df[df["category_display"].notna()]
    rex = df[df["is_rex"] == True].copy()

    # Deduplicate products by ticker (products can appear in multiple rows)
    if "ticker_clean" in rex.columns:
        rex = rex.drop_duplicates(subset=["ticker_clean"], keep="first")

    # Category filter (narrows to one suite for KPIs)
    if category and category != "All":
        # Support filtering by rex_suite name
        if "rex_suite" in rex.columns and category.strip() in _SUITE_ORDER:
            rex = rex[rex["rex_suite"].fillna("").str.strip() == category.strip()].copy()
        else:
            rex = rex[rex["category_display"].str.strip() == category.strip()].copy()
            all_cats = all_cats[all_cats["category_display"].str.strip() == category.strip()].copy()

    overall = get_kpis(rex)

    # --- MoM % change for overall AUM ---
    aum_prev = float(rex["t_w4.aum_1"].sum()) if "t_w4.aum_1" in rex.columns else 0.0
    aum_curr = overall["total_aum"]
    overall["aum_mom_pct"] = round((aum_curr - aum_prev) / aum_prev * 100, 1) if aum_prev > 0 else 0.0

    # --- Overall market share ---
    total_market_aum = float(all_cats["t_w4.aum"].sum()) if not all_cats.empty else 0.0
    overall_mkt_share = (aum_curr / total_market_aum * 100) if total_market_aum > 0 else 0.0
    prev_market_aum = float(all_cats["t_w4.aum_1"].sum()) if "t_w4.aum_1" in all_cats.columns else 0.0
    prev_share = (aum_prev / prev_market_aum * 100) if prev_market_aum > 0 else 0.0
    overall["market_share_pct"] = round(overall_mkt_share, 2)
    overall["market_share_mom_pct"] = round(overall_mkt_share - prev_share, 2)

    # --- Avg yield ---
    yield_col = "t_w3.annualized_yield"
    if yield_col in rex.columns:
        yields = rex[yield_col].dropna()
        yields = yields[yields.apply(lambda v: not (isinstance(v, float) and math.isnan(v)))]
        overall["avg_yield"] = round(float(yields.mean()), 2) if len(yields) > 0 else 0.0
        overall["avg_yield_fmt"] = f"{overall['avg_yield']:.2f}%"
    else:
        overall["avg_yield"] = 0.0
        overall["avg_yield_fmt"] = "N/A"

    # --- Market Appreciation (overall) ---
    total_aum_1 = float(rex["t_w4.aum_1"].sum()) if "t_w4.aum_1" in rex.columns else 0.0
    total_flow_1m = float(rex["t_w4.fund_flow_1month"].sum()) if "t_w4.fund_flow_1month" in rex.columns else 0.0
    mkt_appreciation = aum_curr - total_aum_1 - total_flow_1m
    overall["mkt_appreciation"] = round(mkt_appreciation, 2)
    overall["mkt_appreciation_fmt"] = _fmt_flow(mkt_appreciation)
    overall["mkt_appreciation_positive"] = mkt_appreciation >= 0

    # --- Overall Volume & Spread ---
    if "t_w2.average_vol_30day" in rex.columns:
        overall["total_volume"] = round(float(rex["t_w2.average_vol_30day"].sum()), 0)
        vol = overall["total_volume"]
        if vol >= 1_000_000:
            overall["total_volume_fmt"] = f"{vol/1_000_000:,.1f}M"
        elif vol >= 1_000:
            overall["total_volume_fmt"] = f"{vol/1_000:,.0f}K"
        else:
            overall["total_volume_fmt"] = f"{vol:,.0f}"
    else:
        overall["total_volume"] = 0
        overall["total_volume_fmt"] = "N/A"

    if "t_w2.average_bidask_spread" in rex.columns:
        aum_vals = rex["t_w4.aum"]
        spread_vals = rex["t_w2.average_bidask_spread"]
        valid_spread = (aum_vals > 0) & (spread_vals > 0)
        if valid_spread.any():
            weighted = float((spread_vals[valid_spread] * aum_vals[valid_spread]).sum() / aum_vals[valid_spread].sum())
            overall["avg_spread"] = round(weighted, 4)
            overall["avg_spread_fmt"] = f"${weighted:.4f}"
        else:
            overall["avg_spread"] = 0.0
            overall["avg_spread_fmt"] = "N/A"
    else:
        overall["avg_spread"] = 0.0
        overall["avg_spread_fmt"] = "N/A"

    # --- Best performer (1M total return) ---
    ret_col = "t_w3.total_return_1month"
    if ret_col in rex.columns and not rex.empty:
        best_row = rex.loc[rex[ret_col].idxmax()] if rex[ret_col].notna().any() else None
        if best_row is not None:
            overall["best_performer"] = {
                "ticker": str(best_row.get("ticker_clean", best_row.get("ticker", ""))),
                "return_1m": round(float(best_row.get(ret_col, 0)), 2),
                "return_1m_fmt": f"{float(best_row.get(ret_col, 0)):+.2f}%",
            }
        else:
            overall["best_performer"] = None
    else:
        overall["best_performer"] = None

    # --- AUM sparkline (overall, 12 months, oldest to newest) ---
    overall_sparkline = []
    for i in range(12, 0, -1):
        col = f"t_w4.aum_{i}"
        if col in rex.columns:
            overall_sparkline.append(round(float(rex[col].sum()), 2))
        else:
            overall_sparkline.append(0.0)
    overall_sparkline.append(round(aum_curr, 2))
    overall["sparkline"] = overall_sparkline

    # --- Market share sparkline (12 months) ---
    share_sparkline = []
    for i in range(12, 0, -1):
        col = f"t_w4.aum_{i}"
        if col in rex.columns and col in all_cats.columns:
            r_aum = float(rex[col].sum())
            m_aum = float(all_cats[col].sum())
            share_sparkline.append(round((r_aum / m_aum * 100) if m_aum > 0 else 0.0, 2))
        else:
            share_sparkline.append(0.0)
    share_sparkline.append(round(overall_mkt_share, 2))
    overall["share_sparkline"] = share_sparkline

    # --- Best 5 / Worst 5 by 1M return ---
    best5 = []
    worst5 = []
    if ret_col in rex.columns and not rex.empty:
        valid_ret = rex[rex[ret_col].notna()].copy()
        for _, row in valid_ret.nlargest(5, ret_col).iterrows():
            best5.append({
                "ticker": str(row.get("ticker_clean", row.get("ticker", ""))),
                "fund_name": str(row.get("fund_name", "")),
                "return_1m": round(float(row.get(ret_col, 0)), 2),
                "return_1m_fmt": f"{float(row.get(ret_col, 0)):+.2f}%",
            })
        for _, row in valid_ret.nsmallest(5, ret_col).iterrows():
            worst5.append({
                "ticker": str(row.get("ticker_clean", row.get("ticker", ""))),
                "fund_name": str(row.get("fund_name", "")),
                "return_1m": round(float(row.get(ret_col, 0)), 2),
                "return_1m_fmt": f"{float(row.get(ret_col, 0)):+.2f}%",
            })

    # --- Multi-metric best/worst (for JS metric switching) ---
    perf_metrics = {}
    _metric_defs = {
        "return_1m": ("t_w3.total_return_1month", "{:+.2f}%", True),
        "return_1w": ("t_w3.total_return_1week", "{:+.2f}%", True),
        "flow_1w": ("t_w4.fund_flow_1week", None, False),
        "flow_1m": ("t_w4.fund_flow_1month", None, False),
        "flow_3m": ("t_w4.fund_flow_3month", None, False),
        "flow_6m": ("t_w4.fund_flow_6month", None, False),
        "flow_ytd": ("t_w4.fund_flow_ytd", None, False),
        "flow_1y": ("t_w4.fund_flow_1year", None, False),
        "yield": ("t_w3.annualized_yield", "{:.2f}%", True),
    }
    for mkey, (mcol, mfmt, is_pct) in _metric_defs.items():
        if mcol not in rex.columns or rex.empty:
            continue
        valid = rex[rex[mcol].notna()].copy()
        if valid.empty:
            continue
        b5 = []
        w5 = []
        for _, row in valid.nlargest(5, mcol).iterrows():
            val = float(row.get(mcol, 0))
            if mfmt:
                vfmt = mfmt.format(val)
            else:
                vfmt = _fmt_flow(val)
            b5.append({"ticker": str(row.get("ticker_clean", row.get("ticker", ""))),
                        "fund_name": str(row.get("fund_name", "")), "value_fmt": vfmt})
        for _, row in valid.nsmallest(5, mcol).iterrows():
            val = float(row.get(mcol, 0))
            if mfmt:
                vfmt = mfmt.format(val)
            else:
                vfmt = _fmt_flow(val)
            w5.append({"ticker": str(row.get("ticker_clean", row.get("ticker", ""))),
                        "fund_name": str(row.get("fund_name", "")), "value_fmt": vfmt})
        perf_metrics[mkey] = {"best5": b5, "worst5": w5}

    # --- Flow data arrays for bar chart (per suite) ---
    flow_chart_data = {"suites": [], "flow_1w": [], "flow_1m": [], "flow_3m": [], "flow_6m": [], "flow_ytd": [], "flow_1y": []}
    # --- Chart data arrays for unified chart ---
    volume_chart_data = {"suites": [], "values": []}
    spread_chart_data = {"suites": [], "values": []}
    appreciation_chart_data = {"suites": [], "values": []}

    suites = []
    has_rex_suite = "rex_suite" in rex.columns and rex["rex_suite"].notna().any()
    for suite_name in _SUITE_ORDER:
        if has_rex_suite:
            rex_suite_df = rex[rex["rex_suite"].fillna("").str.strip() == suite_name.strip()] if not rex.empty else rex
        else:
            rex_suite_df = rex[rex["category_display"].str.strip() == suite_name.strip()] if not rex.empty else rex
        if rex_suite_df.empty:
            continue
        # Market share: REX AUM in this suite's primary_category vs total category AUM
        suite_categories = set(rex_suite_df["category_display"].dropna().unique()) if "category_display" in rex_suite_df.columns else set()
        cat_suite = all_cats[all_cats["category_display"].isin(suite_categories)] if suite_categories and not all_cats.empty else all_cats
        cat_aum = float(cat_suite["t_w4.aum"].sum())
        rex_aum = float(rex_suite_df["t_w4.aum"].sum())
        market_share = (rex_aum / cat_aum * 100) if cat_aum > 0 else 0.0

        # MoM for suite
        prev_rex_aum = float(rex_suite_df["t_w4.aum_1"].sum()) if "t_w4.aum_1" in rex_suite_df.columns else 0.0
        aum_mom = round((rex_aum - prev_rex_aum) / prev_rex_aum * 100, 1) if prev_rex_aum > 0 else 0.0

        # Top movers by 1-week flow
        sorted_suite = rex_suite_df.sort_values("t_w4.fund_flow_1week", ascending=False)
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
                "flow": fmt,
                "flow_raw": flow,
                "positive": flow >= 0,
            })
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
                "flow": fmt,
                "flow_raw": flow,
                "positive": flow >= 0,
            })

        kpis = get_kpis(rex_suite_df)

        # Suite market appreciation
        s_aum_curr = float(rex_suite_df["t_w4.aum"].sum())
        s_aum_prev = float(rex_suite_df["t_w4.aum_1"].sum()) if "t_w4.aum_1" in rex_suite_df.columns else 0.0
        s_flow_1m = float(rex_suite_df["t_w4.fund_flow_1month"].sum()) if "t_w4.fund_flow_1month" in rex_suite_df.columns else 0.0
        s_mkt_appr = s_aum_curr - s_aum_prev - s_flow_1m
        kpis["mkt_appreciation"] = round(s_mkt_appr, 2)
        kpis["mkt_appreciation_fmt"] = _fmt_flow(s_mkt_appr)
        kpis["mkt_appreciation_positive"] = s_mkt_appr >= 0

        # Suite volume (30-day avg sum)
        if "t_w2.average_vol_30day" in rex_suite_df.columns:
            s_vol = float(rex_suite_df["t_w2.average_vol_30day"].sum())
            kpis["volume"] = round(s_vol, 0)
            if s_vol >= 1_000_000:
                kpis["volume_fmt"] = f"{s_vol/1_000_000:,.1f}M"
            elif s_vol >= 1_000:
                kpis["volume_fmt"] = f"{s_vol/1_000:,.0f}K"
            else:
                kpis["volume_fmt"] = f"{s_vol:,.0f}"
        else:
            kpis["volume"] = 0
            kpis["volume_fmt"] = "N/A"

        # Suite bid-ask spread (AUM-weighted avg)
        if "t_w2.average_bidask_spread" in rex_suite_df.columns:
            s_aum_col = rex_suite_df["t_w4.aum"]
            s_spread_col = rex_suite_df["t_w2.average_bidask_spread"]
            s_valid = (s_aum_col > 0) & (s_spread_col > 0)
            if s_valid.any():
                s_weighted = float((s_spread_col[s_valid] * s_aum_col[s_valid]).sum() / s_aum_col[s_valid].sum())
                kpis["avg_spread"] = round(s_weighted, 4)
                kpis["avg_spread_fmt"] = f"${s_weighted:.4f}"
            else:
                kpis["avg_spread"] = 0.0
                kpis["avg_spread_fmt"] = "N/A"
        else:
            kpis["avg_spread"] = 0.0
            kpis["avg_spread_fmt"] = "N/A"

        # Suite avg yield
        if yield_col in rex_suite_df.columns:
            s_yields = rex_suite_df[yield_col].dropna()
            s_yields = s_yields[s_yields.apply(lambda v: not (isinstance(v, float) and math.isnan(v)))]
            kpis["avg_yield"] = round(float(s_yields.mean()), 2) if len(s_yields) > 0 else 0.0
            kpis["avg_yield_fmt"] = f"{kpis['avg_yield']:.2f}%"
        else:
            kpis["avg_yield"] = 0.0
            kpis["avg_yield_fmt"] = "N/A"

        # Sparkline: last 4 months of REX AUM in this suite (oldest to newest)
        sparkline = []
        for col in ["t_w4.aum_4", "t_w4.aum_3", "t_w4.aum_2", "t_w4.aum_1"]:
            if col in rex_suite_df.columns:
                sparkline.append(round(float(rex_suite_df[col].sum()), 2))
            else:
                sparkline.append(0.0)

        # Top 50 ACTIVE products in this suite by AUM (exclude liquidated/delisted)
        suite_products = []
        actv_suite = rex_suite_df[_is_actv(rex_suite_df)] if not rex_suite_df.empty else rex_suite_df
        top_suite = actv_suite.nlargest(min(50, len(actv_suite)), "t_w4.aum")
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

        display_name = suite_name  # rex_suite values are already display names

        # Flow data for bar chart
        flow_1w = float(rex_suite_df["t_w4.fund_flow_1week"].sum()) if "t_w4.fund_flow_1week" in rex_suite_df.columns else 0.0
        flow_1m = float(rex_suite_df["t_w4.fund_flow_1month"].sum()) if "t_w4.fund_flow_1month" in rex_suite_df.columns else 0.0
        flow_3m = float(rex_suite_df["t_w4.fund_flow_3month"].sum()) if "t_w4.fund_flow_3month" in rex_suite_df.columns else 0.0
        flow_6m = float(rex_suite_df["t_w4.fund_flow_6month"].sum()) if "t_w4.fund_flow_6month" in rex_suite_df.columns else 0.0
        flow_ytd = float(rex_suite_df["t_w4.fund_flow_ytd"].sum()) if "t_w4.fund_flow_ytd" in rex_suite_df.columns else 0.0
        flow_1y = float(rex_suite_df["t_w4.fund_flow_1year"].sum()) if "t_w4.fund_flow_1year" in rex_suite_df.columns else 0.0
        flow_chart_data["suites"].append(display_name)
        flow_chart_data["flow_1w"].append(round(flow_1w, 2))
        flow_chart_data["flow_1m"].append(round(flow_1m, 2))
        flow_chart_data["flow_3m"].append(round(flow_3m, 2))
        flow_chart_data["flow_6m"].append(round(flow_6m, 2))
        flow_chart_data["flow_ytd"].append(round(flow_ytd, 2))
        flow_chart_data["flow_1y"].append(round(flow_1y, 2))

        # Unified chart data arrays
        volume_chart_data["suites"].append(display_name)
        volume_chart_data["values"].append(round(kpis["volume"], 0))
        spread_chart_data["suites"].append(display_name)
        spread_chart_data["values"].append(round(kpis["avg_spread"], 4))
        appreciation_chart_data["suites"].append(display_name)
        appreciation_chart_data["values"].append(round(kpis["mkt_appreciation"], 2))

        suites.append({
            "name": suite_name,
            "rex_name": display_name,
            "short_name": display_name,
            "kpis": kpis,
            "market_share": round(market_share, 1),
            "market_share_fmt": f"{market_share:.1f}%",
            "total_aum_fmt": _fmt_currency(rex_aum),
            "aum_mom_pct": aum_mom,
            "top_movers": top_movers,
            "products": suite_products,
            "category_param": suite_name,
            "sparkline_data": sparkline,
        })

    # Chart data: AUM by suite for pie chart
    pie_labels = [s["rex_name"] for s in suites]
    pie_values = [round(s["kpis"]["total_aum"], 2) for s in suites]

    # --- Suite-level time series (for AUM breakdown toggle) ---
    suite_ts = _build_suite_time_series(rex, fund_structure=None)

    # --- Unified chart_data dict ---
    chart_data = {
        "flow": flow_chart_data,
        "volume": volume_chart_data,
        "spread": spread_chart_data,
        "appreciation": appreciation_chart_data,
        "suite_ts": suite_ts,
    }

    return {
        "kpis": overall,
        "overall": overall,
        "suites": suites,
        "pie_labels": json.dumps(pie_labels),
        "pie_values": json.dumps(pie_values),
        "pie_data": {"labels": pie_labels, "values": pie_values},
        "best5": best5,
        "worst5": worst5,
        "perf_metrics": perf_metrics,
        "flow_chart": flow_chart_data,
        "chart_data": chart_data,
    }


def _build_suite_time_series(rex_df: pd.DataFrame, fund_structure: str | None = None) -> dict:
    """Build per-suite monthly AUM from snapshot columns (aum_1..aum_36).

    Returns: {"labels": ["Jan 2024", ...], "total": [...], "suites": {"T-REX": [...], ...}}
    """
    now = _dt.now()
    labels = []
    total_vals = []
    suite_vals: dict[str, list[float]] = {}

    has_rex_suite = "rex_suite" in rex_df.columns and rex_df["rex_suite"].notna().any()

    # Build 12-month + current = 13 data points
    aum_cols = [(i, f"t_w4.aum_{i}") for i in range(12, 0, -1)] + [(0, "t_w4.aum")]
    for i, col in aum_cols:
        if col not in rex_df.columns:
            continue
        try:
            from dateutil.relativedelta import relativedelta
            dt = now - relativedelta(months=i)
        except ImportError:
            from datetime import timedelta
            dt = now - timedelta(days=30 * i)
        labels.append(dt.strftime("%b %Y"))
        total_vals.append(round(float(rex_df[col].sum()), 2))

        for suite_name in _SUITE_ORDER:
            if suite_name not in suite_vals:
                suite_vals[suite_name] = []
            if has_rex_suite:
                suite_df = rex_df[rex_df["rex_suite"].fillna("").str.strip() == suite_name.strip()] if not rex_df.empty else rex_df
            else:
                suite_df = rex_df[rex_df["category_display"].str.strip() == suite_name.strip()] if not rex_df.empty else rex_df
            suite_vals[suite_name].append(round(float(suite_df[col].sum()), 2))

    return {"labels": labels, "total": total_vals, "suites": suite_vals}


def _suite_short(name: str) -> str:
    """Short display name for a suite (used in charts/labels)."""
    return name  # rex_suite names are already short enough


def _apply_slicer_filter(df: pd.DataFrame, field: str, value) -> pd.DataFrame:
    """Apply a slicer filter with type-safe comparison.

    URL params are always strings, but DataFrame columns may be numeric.
    Coerce the filter value to the column's dtype before comparing.
    """
    col = df[field]
    if isinstance(value, list):
        if pd.api.types.is_numeric_dtype(col.dtype):
            try:
                cast = [float(v) for v in value]
                return df[col.isin(cast)]
            except (ValueError, TypeError):
                pass
        return df[col.isin(value)]
    else:
        if pd.api.types.is_numeric_dtype(col.dtype):
            try:
                return df[col == float(value)]
            except (ValueError, TypeError):
                pass
        return df[col == value]


#  Category Summary

_CATEGORY_SLICERS: dict[str, list[dict]] = {
    "Crypto": [
        {"field": "q_category_attributes.map_crypto_type", "label": "Type"},
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

REX_SUITES = list(_SUITE_ORDER)


def get_slicer_options(db: Session, category: str) -> list[dict]:
    """Return slicer definitions + current values for a category."""
    df = _load_master(db)
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


def get_category_summary(db: Session, category: str | None, filters: dict | None = None, fund_structure: str | None = None, page: int = 1, per_page: int = 50, etn_overrides: bool = False) -> dict:
    """Return category totals, REX share, top products, issuer breakdown."""
    df = _load_master(db)
    if etn_overrides:
        df = df.copy()
        _apply_etn_overrides(df)

    # ETF/ETN filter (supports comma-separated multi-select)
    if fund_structure and fund_structure != "all":
        fund_type_col = next((c for c in df.columns if c.lower().strip() == "fund_type"), None)
        if fund_type_col:
            types = [t.strip() for t in fund_structure.split(",") if t.strip()]
            df = df[df[fund_type_col].isin(types)].copy()

    # Filter by category
    if category and category != "All":
        df = df[df["category_display"] == category].copy()
    else:
        df = df[df["category_display"].notna()].copy()

    # Apply dynamic slicers
    if filters:
        for field, value in filters.items():
            if field in df.columns and value:
                df = _apply_slicer_filter(df, field, value)

    rex_df = df[df["is_rex"] == True]
    non_rex_df = df[df["is_rex"] == False]

    cat_kpis = get_kpis(df)
    rex_kpis = get_kpis(rex_df)

    cat_aum = cat_kpis["total_aum"]
    rex_aum = rex_kpis["total_aum"]
    market_share = (rex_aum / cat_aum * 100) if cat_aum > 0 else 0.0

    # Top products table (sorted by AUM, with pagination)
    all_sorted = df.sort_values("t_w4.aum", ascending=False)
    total_products = len(all_sorted)
    total_pages = max(1, (total_products + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page
    top_df = all_sorted.iloc[offset:offset + per_page]
    top_products = []
    for rank, (_, row) in enumerate(top_df.iterrows(), offset + 1):
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
        df.groupby("issuer_display", observed=False)["t_w4.aum"]
        .sum()
        .sort_values(ascending=False)
        .head(15)
    )
    issuer_labels = [str(i) for i in issuer_aum.index.tolist()]
    issuer_values = [round(float(v), 2) for v in issuer_aum.values.tolist()]
    # Mark REX in issuer list
    rex_issuers = set(rex_df["issuer_display"].dropna().unique())
    issuer_is_rex = [lbl in rex_issuers for lbl in issuer_labels]

    # --- Per-issuer chart_data for unified chart (top 12 by AUM) ---
    top_issuers = issuer_aum.head(12)
    chart_issuers = [str(i) for i in top_issuers.index.tolist()]
    flow_chart = {"issuers": chart_issuers, "flow_1w": [], "flow_1m": [], "flow_3m": [], "flow_6m": [], "flow_ytd": [], "flow_1y": []}
    vol_chart = {"issuers": chart_issuers, "values": []}
    spread_chart = {"issuers": chart_issuers, "values": []}
    appr_chart = {"issuers": chart_issuers, "values": []}
    for iss_name in chart_issuers:
        iss_df = df[df["issuer_display"] == iss_name]
        # Flows
        for col_suffix, period in [("fund_flow_1week", "1w"), ("fund_flow_1month", "1m"), ("fund_flow_3month", "3m"),
                                   ("fund_flow_6month", "6m"), ("fund_flow_ytd", "ytd"), ("fund_flow_1year", "1y")]:
            col = f"t_w4.{col_suffix}"
            val = round(float(iss_df[col].sum()), 2) if col in iss_df.columns else 0.0
            flow_chart[f"flow_{period}"].append(val)
        # Volume (30-day avg)
        v_col = "t_w2.average_vol_30day"
        vol_chart["values"].append(round(float(iss_df[v_col].sum()), 0) if v_col in iss_df.columns else 0.0)
        # Spread (AUM-weighted avg)
        s_col = "t_w2.average_bidask_spread"
        a_col = "t_w4.aum"
        if s_col in iss_df.columns and a_col in iss_df.columns:
            valid = (iss_df[a_col] > 0) & (iss_df[s_col] > 0)
            if valid.any():
                spread_chart["values"].append(round(float((iss_df[s_col][valid] * iss_df[a_col][valid]).sum() / iss_df[a_col][valid].sum()), 4))
            else:
                spread_chart["values"].append(0.0)
        else:
            spread_chart["values"].append(0.0)
        # Market appreciation: aum - aum_prev - flow_1m
        iss_aum = float(iss_df["t_w4.aum"].sum()) if "t_w4.aum" in iss_df.columns else 0.0
        iss_aum_prev = float(iss_df["t_w4.aum_1"].sum()) if "t_w4.aum_1" in iss_df.columns else 0.0
        iss_flow_1m = float(iss_df["t_w4.fund_flow_1month"].sum()) if "t_w4.fund_flow_1month" in iss_df.columns else 0.0
        appr_chart["values"].append(round(iss_aum - iss_aum_prev - iss_flow_1m, 2))

    # Market share over time (from time series, top issuers)
    mkt_share_ts = {"labels": [], "datasets": []}
    try:
        ts_df = _load_ts(db)
        if category and category != "All":
            ts_df = ts_df[ts_df["category_display"].str.strip() == category.strip()]
        months = sorted(ts_df["months_ago"].dropna().unique())
        months_display = [f"{int(m)}M ago" if m > 0 else "Current" for m in months[:13]]
        mkt_share_ts["labels"] = months_display

        palette = ['#1E40AF','#DC2626','#059669','#D97706','#7C3AED','#DB2777','#0891B2','#65A30D','#6366F1','#F43F5E']
        for idx, iss_name in enumerate(chart_issuers[:8]):
            iss_ts = ts_df[ts_df["issuer_display"] == iss_name]
            pcts = []
            for m in months[:13]:
                month_total = float(ts_df[ts_df["months_ago"] == m]["aum_value"].sum()) or 1.0
                iss_month = float(iss_ts[iss_ts["months_ago"] == m]["aum_value"].sum())
                pcts.append(round(iss_month / month_total * 100, 2))
            mkt_share_ts["datasets"].append({
                "label": iss_name,
                "data": pcts,
                "borderColor": palette[idx % len(palette)],
                "backgroundColor": palette[idx % len(palette)] + "20",
                "fill": False,
                "tension": 0.3,
                "pointRadius": 2,
                "borderWidth": 2,
            })
    except Exception as e:
        log.warning("Market share TS failed: %s", e)

    chart_data = {
        "flow": flow_chart,
        "volume": vol_chart,
        "spread": spread_chart,
        "appreciation": appr_chart,
        "mkt_share_ts": mkt_share_ts,
    }

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
        "chart_data": chart_data,
        "total_funds": total_products,
        "page": page,
        "per_page": per_page,
        "total_pages": total_pages,
    }


#  Treemap

def get_treemap_data(db: Session, category: str | None = None, fund_type: str | None = None, issuer_filter: str | None = None, filters: dict | None = None) -> dict:
    """Return hierarchical issuer-grouped treemap data (top 200 by AUM).

    Returns both flat `products` list (for backward compat) and `issuers` hierarchy.
    """
    df = _load_master(db)
    if category and category != "All":
        df = df[df["category_display"].str.strip() == category.strip()].copy()
    else:
        df = df[df["category_display"].notna()].copy()

    # ETF/ETN filter
    if fund_type and fund_type != "all":
        fund_type_col = next((c for c in df.columns if c.lower().strip() == "fund_type"), None)
        if fund_type_col:
            df = df[df[fund_type_col] == fund_type].copy()

    # Dynamic slicer filters
    if filters:
        for field, value in filters.items():
            if field in df.columns and value:
                df = _apply_slicer_filter(df, field, value)

    # Issuer filter
    if issuer_filter and issuer_filter != "All":
        df = df[df["issuer_display"].fillna("").str.strip() == issuer_filter.strip()].copy()

    # Deduplicate on ticker within filtered df
    ticker_col = next((c for c in df.columns if c.lower().strip() == "ticker"), None)
    if ticker_col:
        df = df.drop_duplicates(subset=[ticker_col], keep="first")

    df = df.sort_values("t_w4.aum", ascending=False).head(200)

    total = float(df["t_w4.aum"].sum()) if not df.empty else 0.0

    # Build flat product list (backward compat) AND hierarchical issuer grouping
    products = []
    issuer_map: dict[str, dict] = {}

    try:
        for _, row in df.iterrows():
            aum = float(row.get("t_w4.aum", 0))
            issuer_name = str(row.get("issuer_display", "Other")).strip() or "Other"
            pct = round((aum / total * 100) if total > 0 else 0.0, 2)
            p = {
                "label": str(row.get("ticker_clean", row.get("ticker", ""))),
                "value": round(aum, 2),
                "group": issuer_name,
                "is_rex": bool(row.get("is_rex", False)),
                "ticker": str(row.get("ticker_clean", row.get("ticker", ""))),
                "fund_name": str(row.get("fund_name", "")),
                "issuer": issuer_name,
                "aum_fmt": _fmt_currency(aum),
                "pct": pct,
            }
            products.append(p)

            if issuer_name not in issuer_map:
                issuer_map[issuer_name] = {"issuer": issuer_name, "aum": 0.0, "children": [], "is_rex": False}
            issuer_map[issuer_name]["aum"] += aum
            issuer_map[issuer_name]["children"].append({
                "ticker": p["ticker"],
                "fund_name": p["fund_name"],
                "aum": round(aum, 2),
                "aum_fmt": p["aum_fmt"],
                "is_rex": p["is_rex"],
            })
            if p["is_rex"]:
                issuer_map[issuer_name]["is_rex"] = True

    except Exception as e:
        log.error("Treemap product build error: %s", e)
        products = []

    # Build hierarchical list, group small issuers (<0.5% share) into "Others"
    threshold = total * 0.005  # 0.5%
    issuers_list = []
    others = {"issuer": "Others", "aum": 0.0, "pct": 0.0, "children": [], "is_rex": False}
    for name, data in sorted(issuer_map.items(), key=lambda x: x[1]["aum"], reverse=True):
        pct = round((data["aum"] / total * 100) if total > 0 else 0.0, 1)
        entry = {
            "issuer": name,
            "aum": round(data["aum"], 2),
            "aum_fmt": _fmt_currency(data["aum"]),
            "pct": pct,
            "children": data["children"],
            "is_rex": data["is_rex"],
        }
        if data["aum"] < threshold and not data["is_rex"]:
            others["aum"] += data["aum"]
            others["children"].extend(data["children"])
        else:
            issuers_list.append(entry)
    if others["children"]:
        others["aum"] = round(others["aum"], 2)
        others["aum_fmt"] = _fmt_currency(others["aum"])
        others["pct"] = round((others["aum"] / total * 100) if total > 0 else 0.0, 1)
        issuers_list.append(others)

    # Available issuers for filter dropdown
    all_issuers = sorted(issuer_map.keys())

    return {
        "products": products,
        "issuers": issuers_list,
        "all_issuers": all_issuers,
        "total_aum": round(total, 2),
        "total_aum_fmt": _fmt_currency(total) if products else "N/A",
        "categories": ALL_CATEGORIES,
    }


#  Issuer Summary

def get_issuer_summary(db: Session, category: str | None = None, fund_structure: str | None = None) -> dict:
    """Return per-issuer AUM, flows, product count, market share."""
    df = _load_master(db)
    if category and category != "All":
        df = df[df["category_display"].str.strip() == category.strip()].copy()
    else:
        df = df[df["category_display"].notna()].copy()

    # ETF/ETN filter (supports comma-separated multi-select)
    if fund_structure and fund_structure != "all":
        fund_type_col = next((c for c in df.columns if c.lower().strip() == "fund_type"), None)
        if fund_type_col:
            types = [t.strip() for t in fund_structure.split(",") if t.strip()]
            df = df[df[fund_type_col].isin(types)].copy()

    # Replace null issuer_display with "Unknown"
    df["issuer_display"] = df["issuer_display"].fillna("Unknown")

    total_aum = float(df["t_w4.aum"].sum()) if not df.empty else 0.0

    # Identify REX issuers
    rex_issuers = set(df[df["is_rex"] == True]["issuer_display"].dropna().unique())

    try:
        grouped = df.groupby("issuer_display", observed=False)
        issuers = []
        for issuer_name, grp in grouped:
            aum = float(grp["t_w4.aum"].sum())
            flow_1w = float(grp["t_w4.fund_flow_1week"].sum()) if "t_w4.fund_flow_1week" in grp.columns else 0.0
            flow_1m = float(grp["t_w4.fund_flow_1month"].sum()) if "t_w4.fund_flow_1month" in grp.columns else 0.0
            share = (aum / total_aum * 100) if total_aum > 0 else 0.0
            num_products = int(_is_actv(grp).sum())
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

def get_market_share_timeline(db: Session) -> dict:
    """Return monthly category market share % over last 24 months."""
    ts = _load_ts(db)

    if ts.empty or "date" not in ts.columns or "category_display" not in ts.columns:
        return {"labels": [], "series": []}

    ts = ts.dropna(subset=["date", "category_display"])

    # Aggregate: for each (date, category), sum aum_value
    agg = (
        ts.groupby(["date", "category_display"], observed=False)["aum_value"]
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
    date_totals = agg.groupby("date", observed=False)["aum_value"].sum()

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

def get_issuer_share(db: Session, cat: str) -> dict:
    """Issuer market share within a specific category."""
    master = _load_master(db)
    if master.empty:
        return {}

    df = master[master["category_display"].str.strip() == cat.strip()].copy() if cat else master.copy()
    if df.empty:
        return {}

    total_aum = float(df["t_w4.aum"].sum())

    # Identify REX issuers
    rex_issuers = set(df[df["is_rex"] == True]["issuer_display"].dropna().unique())

    # Replace null issuer_display
    df["issuer_display"] = df["issuer_display"].fillna("Unknown")

    grouped = df.groupby("issuer_display", observed=False)["t_w4.aum"].sum().sort_values(ascending=False)
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
    ts = _load_ts(db)
    trend = {"months": [], "series": []}
    pct_trend = {"months": [], "series": []}
    if not ts.empty and "date" in ts.columns and "issuer_display" in ts.columns:
        ts_cat = ts[ts["category_display"].str.strip() == cat.strip()].copy() if cat else ts.copy()
        ts_cat = ts_cat.dropna(subset=["date", "issuer_display"])
        if not ts_cat.empty:
            dates = sorted(ts_cat["date"].unique())
            if len(dates) > 12:
                dates = dates[-12:]
            ts_cat = ts_cat[ts_cat["date"].isin(dates)]
            trend["months"] = [d.strftime("%b %Y") for d in dates]
            pct_trend["months"] = trend["months"]
            # Compute date totals for percentage calculation
            date_totals = ts_cat.groupby("date", observed=False)["aum_value"].sum()
            for issuer_name in top_5_issuers:
                issuer_ts = ts_cat[ts_cat["issuer_display"] == issuer_name]
                values = []
                pct_values = []
                for d in dates:
                    val = float(issuer_ts[issuer_ts["date"] == d]["aum_value"].sum())
                    values.append(round(val, 2))
                    dt_total = float(date_totals.get(d, 1.0))
                    pct_values.append(round(val / dt_total * 100, 1) if dt_total > 0 else 0.0)
                trend["series"].append({
                    "issuer": issuer_name,
                    "values": values,
                    "is_rex": issuer_name in rex_issuers,
                })
                pct_trend["series"].append({
                    "issuer": issuer_name,
                    "values": pct_values,
                    "is_rex": issuer_name in rex_issuers,
                })

    return {
        "category": cat,
        "total_aum": total_aum,
        "total_aum_fmt": _fmt_currency(total_aum),
        "issuers": issuers,
        "trend": trend,
        "pct_trend": pct_trend,
    }


#  Underlier Deep-Dive

def get_underlier_summary(db: Session, underlier_type: str = "income", underlier: str | None = None) -> dict:
    """Return underlier-level stats for covered call (income) or L&I single stock."""
    df = _load_master(db)

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
        grouped = df.groupby(field, observed=False)
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
        for ul_name, grp in df.groupby(field, observed=False):
            if not str(ul_name).strip():
                continue
            underliers_list.append({
                "name": str(ul_name),
                "aum_fmt": _fmt_currency(float(grp["t_w4.aum"].sum())),
                "num_products": int(len(grp)),
                "num_rex": int(grp["is_rex"].sum()),
            })
        underliers_list.sort(key=lambda x: x["num_products"], reverse=True)

        # 12-month AUM trend for this underlier
        aum_trend_labels = []
        aum_trend_values = []
        now = _dt.now()
        for i in range(12, 0, -1):
            col_name = f"t_w4.aum_{i}"
            if col_name in sub.columns:
                val = float(sub[col_name].fillna(0).sum())
                try:
                    from dateutil.relativedelta import relativedelta
                    dt = now - relativedelta(months=i)
                except ImportError:
                    from datetime import timedelta
                    dt = now - timedelta(days=30 * i)
                aum_trend_labels.append(dt.strftime("%b %Y"))
                aum_trend_values.append(round(val, 2))
        # Current month
        if "t_w4.aum" in sub.columns:
            aum_trend_labels.append(now.strftime("%b %Y"))
            aum_trend_values.append(round(total_underlier_aum, 2))

        return {
            "underliers": underliers_list,
            "products": products,
            "underlier_type": underlier_type,
            "selected": underlier,
            "aum_trend": {"labels": aum_trend_labels, "values": aum_trend_values},
            "total_underlier_aum_fmt": _fmt_currency(total_underlier_aum),
        }


#  Time Series

def get_time_series(db: Session, category: str | None = None, is_rex: bool | None = None, fund_type: str | None = None, filters: dict | None = None) -> dict:
    """Return aggregated monthly AUM time series for charts.

    Args:
        category: Filter to a specific category_display value.
        is_rex: Filter to REX-only (True) or non-REX (False).
        fund_type: "ETF", "ETN", or "ETF,ETN" to filter by fund structure.
                   Requires joining with master data by ticker.
        filters: Dynamic slicer filters dict (field -> value).
    """
    ts = _load_ts(db)

    if category and category != "All":
        ts = ts[ts["category_display"] == category]
    elif category == "All" or category is None:
        pass  # all categories

    if is_rex is not None:
        ts = ts[ts["is_rex"] == is_rex]

    # Fund type filter: join with master data to get fund_type per ticker
    if fund_type and fund_type != "all":
        master = _load_master(db)
        fund_type_col = next((c for c in master.columns if c.lower().strip() == "fund_type"), None)
        ticker_col = "ticker" if "ticker" in ts.columns else None
        if fund_type_col and ticker_col:
            types = [t.strip() for t in fund_type.split(",") if t.strip()]
            valid_tickers = set(master[master[fund_type_col].isin(types)]["ticker"].dropna().unique())
            ts = ts[ts[ticker_col].isin(valid_tickers)]

    # Dynamic slicer filters: filter by matching tickers from master data
    if filters:
        master = _load_master(db)
        filt = master.copy()
        for field, value in filters.items():
            if field in filt.columns and value:
                filt = _apply_slicer_filter(filt, field, value)
        valid_tickers = set(filt["ticker"].dropna().unique())
        if "ticker" in ts.columns:
            ts = ts[ts["ticker"].isin(valid_tickers)]

    if ts.empty or "date" not in ts.columns:
        return {"labels": "[]", "values": "[]"}

    agg = (
        ts.dropna(subset=["date"])
        .groupby("date", observed=False)["aum_value"]
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
