"""Load and validate Bloomberg data from the daily datatest Excel file.

Data source: datatest.xlsx with 2 sheets:
  - stock_data: US equity universe (~2,468 rows x 29 cols)
  - etp_data: Full US ETP universe (~5,073 rows x 102 cols)

REX funds are derived from etp_data where is_rex == True.
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
    """Load stock_data sheet (US equity universe)."""
    p = _resolve_path(path)
    df = pd.read_excel(p, sheet_name="stock_data", engine="openpyxl")
    log.info("stock_data loaded: %d rows x %d cols", len(df), len(df.columns))

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
    """Load etp_data sheet (full US ETP universe, all columns)."""
    p = _resolve_path(path)
    df = pd.read_excel(p, sheet_name="q_master_data", engine="openpyxl")
    log.info("q_master_data loaded: %d rows x %d cols", len(df), len(df.columns))

    # Normalize underlier ticker
    underlier_col = "q_category_attributes.map_li_underlier"
    if underlier_col in df.columns:
        df["underlier_clean"] = df[underlier_col].fillna("").str.replace(r"\s+US$", "", regex=True)

    return df


def load_all(path: Path | str | None = None) -> dict[str, pd.DataFrame]:
    """Load both datasets, return as dict."""
    p = _resolve_path(path)
    return {
        "stock_data": load_stock_data(p),
        "etp_data": load_etp_data(p),
    }
