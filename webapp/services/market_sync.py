"""
Sync market data from DataFrames into SQLite.

Reads data_engine output (master + time_series DataFrames) and writes
them into mkt_master_data, mkt_time_series, and mkt_report_cache tables.
This runs locally as part of run_daily.py; Render never builds DataFrames.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
from sqlalchemy import delete, text
from sqlalchemy.orm import Session

from webapp.models import (
    MktGlobalEtp,
    MktMasterData,
    MktPipelineRun,
    MktReportCache,
    MktTimeSeries,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column mapping: DataFrame prefix -> flat DB column name
# ---------------------------------------------------------------------------
# data_engine produces columns with t_w2.*, t_w3.*, t_w4.*, q_category_attributes.* prefixes.
# MktMasterData stores them flat (no prefix).

_PREFIX_STRIP = {
    "t_w2.": "",
    "t_w3.": "",
    "t_w4.": "",
    "q_category_attributes.": "",
}

# Columns from data_engine that map directly to MktMasterData fields
_BASE_COLS = [
    "ticker", "fund_name", "issuer", "listed_exchange", "inception_date",
    "fund_type", "asset_class_focus", "regulatory_structure",
    "index_weighting_methodology", "underlying_index", "is_singlestock",
    "is_active", "uses_derivatives", "uses_swaps", "is_40act",
    "uses_leverage", "leverage_amount", "outcome_type", "is_crypto",
    "cusip", "market_status", "fund_description",
]

_W2_COLS = [
    "expense_ratio", "management_fee", "average_bidask_spread",
    "nav_tracking_error", "percentage_premium",
    "average_percent_premium_52week", "average_vol_30day",
    "percent_short_interest", "open_interest",
]

_W3_COLS = [
    "total_return_1day", "total_return_1week", "total_return_1month",
    "total_return_3month", "total_return_6month", "total_return_ytd",
    "total_return_1year", "total_return_3year", "annualized_yield",
]

_W4_FLOW_COLS = [
    "fund_flow_1day", "fund_flow_1week", "fund_flow_1month",
    "fund_flow_3month", "fund_flow_6month", "fund_flow_ytd",
    "fund_flow_1year", "fund_flow_3year",
    "aum",
]

_ENRICHMENT_COLS = [
    "etp_category", "issuer_nickname", "category_display", "issuer_display",
    "is_rex", "fund_category_key", "primary_category", "rex_suite",
]

_ATTR_COLS = [
    "map_li_category", "map_li_subcategory", "map_li_direction",
    "map_li_leverage_amount", "map_li_underlier",
    "map_cc_underlier", "map_cc_index",
    "map_crypto_is_spot", "map_crypto_underlier",
    "map_defined_category", "map_thematic_category",
    "cc_type", "cc_category",
]

_CLASSIFICATION_COLS = [
    "strategy", "strategy_confidence", "underlier_type",
]

# All AUM history columns: aum_1..aum_36
_AUM_HISTORY = [f"aum_{i}" for i in range(1, 37)]


def _strip_prefix(col: str) -> str:
    """Strip known prefixes from a DataFrame column name."""
    for prefix in _PREFIX_STRIP:
        if col.startswith(prefix):
            return col[len(prefix):]
    return col


def _safe_float(val: Any) -> float | None:
    """Convert to float, return None for NaN/NA."""
    try:
        v = float(val)
        if pd.isna(v):
            return None
        return v
    except (TypeError, ValueError):
        return None


def _safe_str(val: Any) -> str | None:
    """Convert to str, return None for NaN/NA."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip()
    return s if s and s.lower() != "nan" else None


def _get_col(row: pd.Series, field: str) -> Any:
    """Get a column value, trying both prefixed and unprefixed names."""
    # Try direct
    if field in row.index:
        return row[field]
    # Try with prefixes
    for prefix in ["t_w2.", "t_w3.", "t_w4.", "q_category_attributes."]:
        prefixed = f"{prefix}{field}"
        if prefixed in row.index:
            return row[prefixed]
    return None


def _pack_aum_history(row: pd.Series) -> str | None:
    """Pack aum_1..aum_36 into a JSON dict."""
    history = {}
    for i in range(1, 37):
        val = _get_col(row, f"aum_{i}")
        fv = _safe_float(val)
        if fv is not None:
            history[f"aum_{i}"] = fv
    return json.dumps(history) if history else None


# ---------------------------------------------------------------------------
# Core sync function
# ---------------------------------------------------------------------------
def sync_market_data(
    db: Session,
    data_file: Path | None = None,
    csv_dir: Path | None = None,
) -> dict:
    """Write data_engine output into SQLite tables.

    Args:
        db: SQLAlchemy session
        data_file: Optional Excel path (passed to data_engine.build_all)
        csv_dir: Optional CSV directory (uses build_all_from_csvs instead)

    Returns:
        {"master_rows": int, "ts_rows": int, "report_keys": list}
    """
    from webapp.services.data_engine import build_all, build_all_from_csvs

    # Step 1: Build DataFrames
    log.info("Building DataFrames via data_engine...")
    if csv_dir and Path(csv_dir).exists():
        result = build_all_from_csvs(csv_dir)
    else:
        result = build_all(data_file)

    master_df = result.get("master", pd.DataFrame())
    ts_df = result.get("ts", pd.DataFrame())

    if master_df.empty:
        log.warning("No master data produced, skipping sync")
        return {"master_rows": 0, "ts_rows": 0, "report_keys": []}

    # Step 2: Create pipeline run record
    run = MktPipelineRun(
        started_at=datetime.utcnow(),
        status="running",
        source_file=str(data_file or csv_dir or "auto"),
        etp_rows_read=len(master_df),
    )
    db.add(run)
    db.flush()  # get run.id
    run_id = run.id

    # Step 3: Clear existing data (full snapshot replace)
    log.info("Clearing existing market data...")
    db.execute(delete(MktReportCache))
    db.execute(delete(MktTimeSeries).where(True))
    db.execute(delete(MktMasterData).where(True))
    db.flush()

    # Step 4: Bulk insert master data
    log.info("Inserting %d master rows...", len(master_df))
    master_rows = _insert_master_data(db, master_df, run_id)

    # Step 5: Bulk insert time series
    ts_rows = 0
    if not ts_df.empty:
        log.info("Inserting %d time series rows...", len(ts_df))
        ts_rows = _insert_time_series(db, ts_df, run_id, master_df)

    # Step 6: Pre-compute and cache reports
    log.info("Computing report caches...")
    report_keys = _compute_and_cache_reports(db, master_df, run_id)

    # Step 6a: Pre-compute and cache screener results (non-fatal)
    try:
        _compute_and_cache_screener(db)
    except Exception as e:
        log.warning("Screener cache skipped: %s", e)

    # Step 6b: Sync global ETP supplement (non-fatal)
    global_rows = 0
    try:
        sheets_dir = Path(csv_dir) if csv_dir else Path("data/DASHBOARD/sheets")
        global_rows = _sync_global_etp(db, sheets_dir, run_id)
        log.info("Synced %d global ETP rows", global_rows)
    except Exception as e:
        log.warning("Global ETP sync skipped: %s", e)

    # Step 6c: Export CSVs (local only)
    if not os.environ.get("RENDER"):
        try:
            _export_csvs(master_df, ts_df)
        except Exception as e:
            log.warning("CSV export skipped: %s", e)

    # Step 7: Finalize pipeline run
    run.finished_at = datetime.utcnow()
    run.status = "completed"
    run.master_rows_written = master_rows
    run.ts_rows_written = ts_rows
    db.commit()

    log.info("Market sync complete: %d master, %d TS, %d reports",
             master_rows, ts_rows, len(report_keys))

    return {
        "master_rows": master_rows,
        "ts_rows": ts_rows,
        "report_keys": report_keys,
    }


# ---------------------------------------------------------------------------
# Insert helpers
# ---------------------------------------------------------------------------
def _insert_master_data(db: Session, df: pd.DataFrame, run_id: int) -> int:
    """Insert master data rows from DataFrame into mkt_master_data."""
    rows = []
    for _, row in df.iterrows():
        ticker = _safe_str(row.get("ticker"))
        if not ticker:
            continue

        obj = MktMasterData(
            pipeline_run_id=run_id,
            # Base fields
            ticker=ticker,
            fund_name=_safe_str(row.get("fund_name")),
            issuer=_safe_str(row.get("issuer")),
            listed_exchange=_safe_str(row.get("listed_exchange")),
            inception_date=_safe_str(row.get("inception_date")),
            fund_type=_safe_str(row.get("fund_type")),
            asset_class_focus=_safe_str(row.get("asset_class_focus")),
            regulatory_structure=_safe_str(row.get("regulatory_structure")),
            index_weighting_methodology=_safe_str(row.get("index_weighting_methodology")),
            underlying_index=_safe_str(row.get("underlying_index")),
            is_singlestock=_safe_str(row.get("is_singlestock")),
            is_active=_safe_str(row.get("is_active")),
            uses_derivatives=_safe_str(row.get("uses_derivatives")),
            uses_swaps=_safe_str(row.get("uses_swaps")),
            is_40act=_safe_str(row.get("is_40act")),
            uses_leverage=_safe_str(row.get("uses_leverage")),
            leverage_amount=_safe_str(row.get("leverage_amount")),
            outcome_type=_safe_str(row.get("outcome_type")),
            is_crypto=_safe_str(row.get("is_crypto")),
            cusip=_safe_str(row.get("cusip")),
            market_status=_safe_str(row.get("market_status")),
            fund_description=_safe_str(row.get("fund_description")),
            # W2 metrics
            expense_ratio=_safe_float(_get_col(row, "expense_ratio")),
            management_fee=_safe_float(_get_col(row, "management_fee")),
            average_bidask_spread=_safe_float(_get_col(row, "average_bidask_spread")),
            nav_tracking_error=_safe_float(_get_col(row, "nav_tracking_error")),
            percentage_premium=_safe_float(_get_col(row, "percentage_premium")),
            average_percent_premium_52week=_safe_float(_get_col(row, "average_percent_premium_52week")),
            average_vol_30day=_safe_float(_get_col(row, "average_vol_30day")),
            percent_short_interest=_safe_float(_get_col(row, "percent_short_interest")),
            open_interest=_safe_float(_get_col(row, "open_interest")),
            # W3 returns
            total_return_1day=_safe_float(_get_col(row, "total_return_1day")),
            total_return_1week=_safe_float(_get_col(row, "total_return_1week")),
            total_return_1month=_safe_float(_get_col(row, "total_return_1month")),
            total_return_3month=_safe_float(_get_col(row, "total_return_3month")),
            total_return_6month=_safe_float(_get_col(row, "total_return_6month")),
            total_return_ytd=_safe_float(_get_col(row, "total_return_ytd")),
            total_return_1year=_safe_float(_get_col(row, "total_return_1year")),
            total_return_3year=_safe_float(_get_col(row, "total_return_3year")),
            annualized_yield=_safe_float(_get_col(row, "annualized_yield")),
            # W4 flows + AUM
            fund_flow_1day=_safe_float(_get_col(row, "fund_flow_1day")),
            fund_flow_1week=_safe_float(_get_col(row, "fund_flow_1week")),
            fund_flow_1month=_safe_float(_get_col(row, "fund_flow_1month")),
            fund_flow_3month=_safe_float(_get_col(row, "fund_flow_3month")),
            fund_flow_6month=_safe_float(_get_col(row, "fund_flow_6month")),
            fund_flow_ytd=_safe_float(_get_col(row, "fund_flow_ytd")),
            fund_flow_1year=_safe_float(_get_col(row, "fund_flow_1year")),
            fund_flow_3year=_safe_float(_get_col(row, "fund_flow_3year")),
            aum=_safe_float(_get_col(row, "aum")),
            aum_history_json=_pack_aum_history(row),
            # Enrichment
            etp_category=_safe_str(row.get("etp_category")),
            issuer_nickname=_safe_str(row.get("issuer_nickname")),
            category_display=_safe_str(row.get("category_display")),
            issuer_display=_safe_str(row.get("issuer_display")),
            is_rex=bool(row.get("is_rex", False)) if pd.notna(row.get("is_rex")) else False,
            fund_category_key=_safe_str(row.get("fund_category_key")),
            primary_category=_safe_str(row.get("primary_category")),
            rex_suite=_safe_str(row.get("rex_suite")),
            # Category attributes
            map_li_category=_safe_str(_get_col(row, "map_li_category")),
            map_li_subcategory=_safe_str(_get_col(row, "map_li_subcategory")),
            map_li_direction=_safe_str(_get_col(row, "map_li_direction")),
            map_li_leverage_amount=_safe_str(_get_col(row, "map_li_leverage_amount")),
            map_li_underlier=_safe_str(_get_col(row, "map_li_underlier")),
            map_cc_underlier=_safe_str(_get_col(row, "map_cc_underlier")),
            map_cc_index=_safe_str(_get_col(row, "map_cc_index")),
            map_crypto_is_spot=_safe_str(_get_col(row, "map_crypto_is_spot")),
            map_crypto_underlier=_safe_str(_get_col(row, "map_crypto_underlier")),
            map_defined_category=_safe_str(_get_col(row, "map_defined_category")),
            map_thematic_category=_safe_str(_get_col(row, "map_thematic_category")),
            # CC-specific
            cc_type=_safe_str(_get_col(row, "cc_type")),
            cc_category=_safe_str(_get_col(row, "cc_category")),
            ticker_clean=ticker.replace(" US", "").strip() if ticker else None,
            # Classification
            strategy=_safe_str(row.get("strategy")),
            strategy_confidence=_safe_str(row.get("strategy_confidence")),
            underlier_type=_safe_str(row.get("underlier_type")),
        )
        rows.append(obj)

    # Batch insert
    BATCH = 5000
    for i in range(0, len(rows), BATCH):
        db.add_all(rows[i:i + BATCH])
        db.flush()

    return len(rows)


def _insert_time_series(
    db: Session, ts_df: pd.DataFrame, run_id: int, master_df: pd.DataFrame
) -> int:
    """Insert time series rows from DataFrame into mkt_time_series."""
    # Build enrichment lookup from master (for category_display, issuer_display, is_rex, etc.)
    enrichment: dict[str, dict] = {}
    for _, row in master_df.drop_duplicates(subset=["ticker"], keep="first").iterrows():
        ticker = str(row.get("ticker", ""))
        if ticker:
            enrichment[ticker] = {
                "category_display": _safe_str(row.get("category_display")),
                "issuer_display": _safe_str(row.get("issuer_display")),
                "is_rex": bool(row.get("is_rex", False)) if pd.notna(row.get("is_rex")) else False,
                "issuer_group": _safe_str(row.get("issuer_group")),
                "fund_category_key": _safe_str(row.get("fund_category_key")),
            }

    rows = []
    for _, row in ts_df.iterrows():
        ticker = _safe_str(row.get("ticker"))
        if not ticker:
            continue

        months_ago = int(row.get("months_ago", 0))
        aum_value = _safe_float(row.get("aum_value"))

        enrich = enrichment.get(ticker, {})
        # Time series from data_engine already has category/issuer joined
        cat_display = _safe_str(row.get("category_display")) or enrich.get("category_display")
        iss_display = _safe_str(row.get("issuer_display")) or enrich.get("issuer_display")
        is_rex = bool(row.get("is_rex", enrich.get("is_rex", False)))
        issuer_group = _safe_str(row.get("issuer_group")) or enrich.get("issuer_group")
        fck = _safe_str(row.get("fund_category_key")) or enrich.get("fund_category_key")

        obj = MktTimeSeries(
            pipeline_run_id=run_id,
            ticker=ticker,
            months_ago=months_ago,
            aum_value=aum_value,
            category_display=cat_display,
            issuer_display=iss_display,
            is_rex=is_rex,
            issuer_group=issuer_group,
            fund_category_key=fck,
        )
        rows.append(obj)

    BATCH = 10000
    for i in range(0, len(rows), BATCH):
        db.add_all(rows[i:i + BATCH])
        db.flush()

    return len(rows)


# ---------------------------------------------------------------------------
# CSV exports (local only)
# ---------------------------------------------------------------------------
_EXPORT_DIR = Path("data/exports")


def _export_csvs(master_df: pd.DataFrame, ts_df: pd.DataFrame) -> None:
    """Write CSV exports to data/exports/ for ad-hoc analysis.

    Called during sync_market_data, local only (never on Render).
    Each file is overwritten on every run.
    """
    _EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    # Flatten column names (strip prefixes)
    df = master_df.copy()
    df.columns = [_strip_prefix(c) for c in df.columns]

    # master_all.csv -- full universe
    df.to_csv(_EXPORT_DIR / "master_all.csv", index=False)

    # rex_only.csv
    if "is_rex" in df.columns:
        rex = df[df["is_rex"] == True]
        rex.to_csv(_EXPORT_DIR / "rex_only.csv", index=False)

    # li_funds.csv
    if "etp_category" in df.columns:
        li = df[df["etp_category"] == "LI"]
        li.to_csv(_EXPORT_DIR / "li_funds.csv", index=False)

    # cc_funds.csv
    if "etp_category" in df.columns:
        cc = df[df["etp_category"] == "CC"]
        cc.to_csv(_EXPORT_DIR / "cc_funds.csv", index=False)

    # issuer_rollup.csv
    if "issuer_display" in df.columns and "aum" in df.columns:
        issuer = df.groupby("issuer_display", observed=True).agg(
            fund_count=("ticker", "count"),
            total_aum=("aum", "sum"),
            flow_1w=("fund_flow_1week", "sum") if "fund_flow_1week" in df.columns else ("aum", lambda x: 0),
            flow_1m=("fund_flow_1month", "sum") if "fund_flow_1month" in df.columns else ("aum", lambda x: 0),
            flow_ytd=("fund_flow_ytd", "sum") if "fund_flow_ytd" in df.columns else ("aum", lambda x: 0),
        ).sort_values("total_aum", ascending=False)
        total_aum = issuer["total_aum"].sum()
        if total_aum > 0:
            issuer["market_share_pct"] = (issuer["total_aum"] / total_aum * 100).round(2)
        issuer.to_csv(_EXPORT_DIR / "issuer_rollup.csv")

    # underlier_rollup.csv (single stock only)
    if "map_li_underlier" in df.columns:
        ss = df[df.get("is_singlestock", "").astype(str).str.lower().isin(["true", "1", "yes"])] if "is_singlestock" in df.columns else pd.DataFrame()
        if not ss.empty:
            underlier = ss.groupby("map_li_underlier", observed=True).agg(
                fund_count=("ticker", "count"),
                total_aum=("aum", "sum"),
                flow_1w=("fund_flow_1week", "sum") if "fund_flow_1week" in ss.columns else ("aum", lambda x: 0),
                flow_ytd=("fund_flow_ytd", "sum") if "fund_flow_ytd" in ss.columns else ("aum", lambda x: 0),
            ).sort_values("total_aum", ascending=False)
            underlier.to_csv(_EXPORT_DIR / "underlier_rollup.csv")

    count = len(list(_EXPORT_DIR.glob("*.csv")))
    log.info("Exported %d CSVs to %s", count, _EXPORT_DIR)


# ---------------------------------------------------------------------------
# Report pre-computation
# ---------------------------------------------------------------------------
def _compute_and_cache_reports(
    db: Session, master_df: pd.DataFrame, run_id: int
) -> list[str]:
    """Pre-compute report dicts and store as JSON in mkt_report_cache.

    Reports are computed here (locally, with full DataFrames) so that
    Render can serve them as simple JSON reads without holding data in memory.
    """
    from webapp.services import report_data as rd

    cached_keys = []

    # Invalidate all in-memory caches so they pick up fresh DB data
    rd.invalidate_cache()
    from webapp.services import market_data as md
    from webapp.services.screener_3x_cache import invalidate_cache as inv_screener
    md.invalidate_cache()
    inv_screener()

    for report_key, get_fn in [
        ("li_report", rd.get_li_report),
        ("cc_report", rd.get_cc_report),
        ("flow_report", rd.get_flow_report),
    ]:
        try:
            data = get_fn(db=db)
            if data.get("available"):
                cache_row = MktReportCache(
                    pipeline_run_id=run_id,
                    report_key=report_key,
                    data_json=json.dumps(data, default=_json_default),
                    data_as_of=data.get("data_as_of", ""),
                    updated_at=datetime.utcnow(),
                )
                db.add(cache_row)
                cached_keys.append(report_key)
                log.info("Cached report: %s", report_key)
        except Exception as e:
            log.error("Failed to compute %s: %s", report_key, e)

    db.flush()
    return cached_keys


def _compute_and_cache_screener(db: Session) -> None:
    """Pre-compute screener 3x analysis and store in DB.

    Runs the full screener pipeline (loads Bloomberg data, scores stocks)
    and saves the result to mkt_report_cache so Render can serve it
    without touching Excel.
    """
    from webapp.services.screener_3x_cache import compute_and_cache, save_to_db

    log.info("Computing screener cache...")
    result = compute_and_cache()
    save_to_db(db, result)
    log.info("Screener cache saved to DB")


def _json_default(obj: Any) -> Any:
    """JSON serializer for objects not serializable by default."""
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, pd.Timedelta):
        return str(obj)
    if hasattr(obj, "item"):  # numpy scalar
        return obj.item()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


# ---------------------------------------------------------------------------
# Global ETP supplement sync
# ---------------------------------------------------------------------------
_GLOBAL_SHEETS = ["assets", "cost", "performance", "flows", "liquidity", "gics", "geographic", "structure"]

# Column mappings: CSV column name -> model field name
# Each sheet uses column index 0 as Name and index 1 as Ticker
_GLOBAL_FIELD_MAP = {
    "assets": {
        "Fund Asset Class AUM": "class_aum",
        "Fund AUM": "fund_aum",
        "NAV": "nav",
        "Num Holdings": "holdings_count",
    },
    "cost": {
        "Expense Ratio": "expense_ratio",
        "Management Fee": "mgmt_fee",
        "Bid-Ask Spread": "bid_ask_spread",
        "NAV Tracking Error": "nav_tracking_error",
        "Premium": "premium",
    },
    "performance": {
        "MTD Return": "return_mtd",
        "5Y Return": "return_5y",
        "10Y Return": "return_10y",
        "52W High": "high_52w",
        "52W Low": "low_52w",
        "12M Yield": "yield_12m",
    },
    "liquidity": {
        "Volume": "volume_1d",
        "Avg Volume 30D": "avg_volume_30d",
        "Implied Liquidity": "implied_liquidity",
        "Agg Traded Value": "agg_traded_val",
    },
    "structure": {
        "Fund Type": "fund_type",
        "Structure": "structure",
        "UCITS": "is_ucits",
        "Leverage": "leverage",
        "Inception Date": "inception_date",
    },
}


def _normalize_ticker(raw: str) -> str:
    """Strip ' Equity' suffix and whitespace from Bloomberg ticker."""
    if not raw or not isinstance(raw, str):
        return ""
    t = raw.strip()
    if t.endswith(" Equity"):
        t = t[:-7].strip()
    return t


def _sync_global_etp(db: Session, sheets_dir: Path, run_id: int) -> int:
    """Read 7 global supplement CSVs, join by ticker, and bulk-insert into mkt_global_etp.

    Non-fatal: raises on missing CSVs so caller can catch and skip.
    """
    if not sheets_dir or not sheets_dir.exists():
        raise FileNotFoundError(f"Sheets dir not found: {sheets_dir}")

    # Check that at least the assets sheet exists
    assets_path = sheets_dir / "assets.csv"
    if not assets_path.exists():
        raise FileNotFoundError(f"assets.csv not found in {sheets_dir}")

    # Read all available sheets into a dict of DataFrames
    sheet_dfs: dict[str, pd.DataFrame] = {}
    for sheet_name in _GLOBAL_SHEETS:
        csv_path = sheets_dir / f"{sheet_name}.csv"
        if csv_path.exists():
            try:
                df = pd.read_csv(csv_path, engine="python", on_bad_lines="skip")
                sheet_dfs[sheet_name] = df
            except Exception as e:
                log.warning("Failed to read %s: %s", csv_path, e)

    if "assets" not in sheet_dfs:
        raise FileNotFoundError("Could not read assets.csv")

    # Build base from assets sheet (Name + Ticker as identity)
    assets_df = sheet_dfs["assets"]
    cols = assets_df.columns.tolist()
    # First two columns are typically Name and Ticker
    name_col = cols[0] if len(cols) > 0 else None
    ticker_col = cols[1] if len(cols) > 1 else None

    if not name_col or not ticker_col:
        raise ValueError("assets.csv must have at least 2 columns (Name, Ticker)")

    # Build lookup: normalized_ticker -> {field: value}
    records: dict[str, dict] = {}

    for _, row in assets_df.iterrows():
        raw_ticker = str(row.get(ticker_col, ""))
        ticker = _normalize_ticker(raw_ticker)
        if not ticker:
            continue
        records[ticker] = {
            "name": _safe_str(row.get(name_col)),
            "ticker": ticker,
        }

    # Merge fields from each sheet
    for sheet_name, field_map in _GLOBAL_FIELD_MAP.items():
        if sheet_name not in sheet_dfs:
            continue
        df = sheet_dfs[sheet_name]
        s_cols = df.columns.tolist()
        s_ticker_col = s_cols[1] if len(s_cols) > 1 else None
        if not s_ticker_col:
            continue

        for _, row in df.iterrows():
            ticker = _normalize_ticker(str(row.get(s_ticker_col, "")))
            if not ticker or ticker not in records:
                continue

            for csv_col, model_field in field_map.items():
                # Try exact match first, then fuzzy column match
                val = None
                if csv_col in row.index:
                    val = row[csv_col]
                else:
                    # Try partial match (Bloomberg column names can vary)
                    for c in s_cols:
                        if csv_col.lower() in c.lower():
                            val = row[c]
                            break
                if val is not None:
                    records[ticker][model_field] = val

    # Pack GICS and geographic JSON from dedicated sheets
    for sheet_name, json_field in [("gics", "gics_json"), ("geographic", "geo_json")]:
        if sheet_name not in sheet_dfs:
            continue
        df = sheet_dfs[sheet_name]
        s_cols = df.columns.tolist()
        s_ticker_col = s_cols[1] if len(s_cols) > 1 else None
        if not s_ticker_col:
            continue

        # Percentage columns start from index 2 onwards
        pct_cols = s_cols[2:]
        for _, row in df.iterrows():
            ticker = _normalize_ticker(str(row.get(s_ticker_col, "")))
            if not ticker or ticker not in records:
                continue
            pct_data = {}
            for c in pct_cols:
                v = _safe_float(row.get(c))
                if v is not None and v != 0.0:
                    pct_data[c] = v
            if pct_data:
                records[ticker][json_field] = json.dumps(pct_data)

    # Delete existing rows and bulk insert
    db.execute(delete(MktGlobalEtp).where(True))
    db.flush()

    rows = []
    for ticker, data in records.items():
        obj = MktGlobalEtp(
            pipeline_run_id=run_id,
            ticker=data.get("ticker", ticker),
            name=_safe_str(data.get("name")),
            class_aum=_safe_float(data.get("class_aum")),
            fund_aum=_safe_float(data.get("fund_aum")),
            nav=_safe_float(data.get("nav")),
            holdings_count=int(data["holdings_count"]) if _safe_float(data.get("holdings_count")) is not None else None,
            expense_ratio=_safe_float(data.get("expense_ratio")),
            mgmt_fee=_safe_float(data.get("mgmt_fee")),
            bid_ask_spread=_safe_float(data.get("bid_ask_spread")),
            nav_tracking_error=_safe_float(data.get("nav_tracking_error")),
            premium=_safe_float(data.get("premium")),
            return_mtd=_safe_float(data.get("return_mtd")),
            return_5y=_safe_float(data.get("return_5y")),
            return_10y=_safe_float(data.get("return_10y")),
            high_52w=_safe_float(data.get("high_52w")),
            low_52w=_safe_float(data.get("low_52w")),
            yield_12m=_safe_float(data.get("yield_12m")),
            volume_1d=_safe_float(data.get("volume_1d")),
            avg_volume_30d=_safe_float(data.get("avg_volume_30d")),
            implied_liquidity=_safe_float(data.get("implied_liquidity")),
            agg_traded_val=_safe_float(data.get("agg_traded_val")),
            fund_type=_safe_str(data.get("fund_type")),
            structure=_safe_str(data.get("structure")),
            is_ucits=_safe_str(data.get("is_ucits")),
            leverage=_safe_str(data.get("leverage")),
            inception_date=_safe_str(data.get("inception_date")),
            gics_json=data.get("gics_json"),
            geo_json=data.get("geo_json"),
        )
        rows.append(obj)

    BATCH = 5000
    for i in range(0, len(rows), BATCH):
        db.add_all(rows[i:i + BATCH])
        db.flush()

    return len(rows)
