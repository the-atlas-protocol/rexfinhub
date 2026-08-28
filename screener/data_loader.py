"""Load and validate market data from bloomberg_daily_file.xlsm.

Single data source: bloomberg_daily_file.xlsm
  - s1 sheet: US equity universe (stock data)
  - w1-w4 sheets: ETP universe (built via data_engine)

REX funds are derived from ETP data where is_rex == True.
Filing status comes from the pipeline database (not a sheet).
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from screener.config import DATA_FILE

log = logging.getLogger(__name__)


def _resolve_path(path: Path | str | None = None) -> Path:
    """Return the data file path, defaulting to config."""
    p = Path(path) if path else DATA_FILE
    if not p.exists():
        raise FileNotFoundError(f"Data file not found: {p}")
    return p


def load_stock_data(path: Path | str | None = None) -> pd.DataFrame:
    """Load s1 sheet (US equity universe) from bloomberg_daily_file."""
    p = _resolve_path(path)
    df = pd.read_excel(p, sheet_name="s1", engine="openpyxl")
    log.info("s1 (stock) loaded: %d rows x %d cols", len(df), len(df.columns))

    # Drop rows with missing tickers (trailing empty rows in Excel)
    if "Ticker" in df.columns:
        df = df.dropna(subset=["Ticker"]).reset_index(drop=True)

    # Deduplicate by ticker (source Excel sometimes has duplicate rows)
    if "Ticker" in df.columns:
        before = len(df)
        df = df.drop_duplicates(subset=["Ticker"], keep="first").reset_index(drop=True)
        dupes = before - len(df)
        if dupes:
            log.warning("Dropped %d duplicate ticker rows from stock_data", dupes)

    # Normalize ticker: keep original as ticker_raw, strip " US" for matching
    if "Ticker" in df.columns:
        df["ticker_raw"] = df["Ticker"]
        df["ticker_clean"] = df["Ticker"].str.replace(r"\s+US$", "", regex=True)

    # Optimize float dtypes
    float_cols = [
        "Mkt Cap", "Volatility 10D", "Volatility 30D", "Volatility 90D",
        "Short Interest Ratio", "Institutional Owner % Shares Outstanding",
        "% Insider Shares Outstanding", "News Sentiment Daily Avg",
        "Last Price", "52W High", "52W Low", "Turnover / Traded Value",
    ]
    for col in float_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").astype("float32")

    return df


def load_etp_data(path: Path | str | None = None) -> pd.DataFrame:
    """Build ETP universe from w1-w4 sheets via data_engine."""
    p = _resolve_path(path)
    from webapp.services.data_engine import build_all
    result = build_all(p)
    df = result.get("master", pd.DataFrame())
    log.info("ETP data built from w1-w4: %d rows x %d cols", len(df), len(df.columns))

    # Normalize underlier ticker (column name varies by data source)
    underlier_col = None
    for candidate in ["q_category_attributes.map_li_underlier", "map_li_underlier"]:
        if candidate in df.columns:
            underlier_col = candidate
            break
    if underlier_col:
        # Alias to canonical name for downstream consumers
        if underlier_col != "q_category_attributes.map_li_underlier":
            df["q_category_attributes.map_li_underlier"] = df[underlier_col]
        df["underlier_clean"] = df[underlier_col].fillna("").str.replace(r"\s+US$", "", regex=True)

    return df


def load_all(path: Path | str | None = None) -> dict[str, pd.DataFrame]:
    """Load both datasets, return as dict."""
    p = _resolve_path(path)
    return {
        "stock_data": load_stock_data(p),
        "etp_data": load_etp_data(p),
    }
