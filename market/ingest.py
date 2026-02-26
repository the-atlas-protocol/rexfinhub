"""Excel ingest: read Bloomberg data (bbg_data.xlsx or legacy) and join ETP sheets on ticker."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from market.config import (
    DATA_FILE, BASE_FIELDS, W2_FIELDS, W3_FIELDS, W4_FIELDS,
    W2_PREFIX, W3_PREFIX, W4_PREFIX,
    SHEET_W1, SHEET_W2, SHEET_W3, SHEET_W4, SHEET_S1, SHEET_MKT_STATUS,
    W1_COL_MAP, W2_COL_MAP, W3_COL_MAP, W4_FLOW_COL_MAP,
    SHEET_ETP_BASE, SHEET_ETP_METRICS, SHEET_ETP_RETURNS,
    SHEET_ETP_FLOWS, SHEET_STOCK_DATA,
)

log = logging.getLogger(__name__)


def read_input(data_file: Path | str | None = None) -> dict:
    """Read the input Excel file.

    Auto-detects format:
    - New BBG format: w1/w2/w3/w4/s1/mkt_status sheets
    - Legacy 5-sheet: etp_base/etp_metrics/etp_returns/etp_flows/stock_data
    - Legacy single-sheet: data_import

    Returns dict with keys:
    - etp_combined: all ETP sheets joined on ticker with prefixed columns
    - stock_data: raw stock_data sheet
    - mkt_status: market status reference (new format only)
    - source_path: str path to input file
    """
    path = Path(data_file) if data_file else DATA_FILE
    if not path.exists():
        raise FileNotFoundError(f"Input data file not found: {path}")

    log.info("Reading input: %s", path)
    xl = pd.ExcelFile(path, engine="openpyxl")
    sheets = xl.sheet_names

    # Detect format
    if SHEET_W1 in sheets:
        etp, stock, mkt_status = _read_bbg_format(xl)
    elif SHEET_ETP_BASE in sheets:
        etp = _read_5sheet(xl)
        stock = _read_stock(xl, SHEET_STOCK_DATA)
        mkt_status = pd.DataFrame()
    elif "data_import" in sheets:
        etp = _read_legacy(xl)
        stock = _read_stock(xl, "stock_data")
        mkt_status = pd.DataFrame()
    else:
        raise ValueError(
            f"Unrecognized input format. Expected sheets: "
            f"{SHEET_W1} or {SHEET_ETP_BASE} or data_import. Found: {sheets}"
        )

    return {
        "etp_combined": etp,
        "stock_data": stock,
        "mkt_status": mkt_status,
        "source_path": str(path),
    }


# ---------------------------------------------------------------------------
# New BBG format (w1/w2/w3/w4/s1/mkt_status)
# ---------------------------------------------------------------------------

def _read_bbg_format(xl: pd.ExcelFile) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read new BBG format with abbreviated column names.

    Returns (etp_combined, stock_data, mkt_status).
    """
    # --- w1: base data ---
    w1 = _read_sheet(xl, SHEET_W1)
    w1 = w1.rename(columns=W1_COL_MAP)
    w1 = w1.dropna(subset=["ticker"])
    log.info("w1 (base): %d rows x %d cols", *w1.shape)

    # --- w2: metrics ---
    w2 = _read_sheet(xl, SHEET_W2)
    w2 = w2.rename(columns=W2_COL_MAP)
    # Drop Fund Name column if present (duplicate of w1)
    w2 = w2.drop(columns=["Fund Name", "fund_name"], errors="ignore")
    w2 = w2.dropna(subset=["ticker"])
    log.info("w2 (metrics): %d rows x %d cols", *w2.shape)

    # --- w3: returns ---
    w3 = _read_sheet(xl, SHEET_W3)
    w3 = w3.rename(columns=W3_COL_MAP)
    w3 = w3.drop(columns=["Fund Name", "fund_name"], errors="ignore")
    w3 = w3.dropna(subset=["ticker"])
    log.info("w3 (returns): %d rows x %d cols", *w3.shape)

    # --- w4: flows + AUM history ---
    w4 = _read_sheet(xl, SHEET_W4)
    w4 = _process_w4(w4)
    log.info("w4 (flows+AUM): %d rows x %d cols", *w4.shape)

    # Apply t_w2./t_w3./t_w4. prefixes on non-ticker columns
    w2_rename = {c: f"{W2_PREFIX}{c}" for c in w2.columns if c != "ticker"}
    w3_rename = {c: f"{W3_PREFIX}{c}" for c in w3.columns if c != "ticker"}
    w4_rename = {c: f"{W4_PREFIX}{c}" for c in w4.columns if c != "ticker"}

    w2 = w2.rename(columns=w2_rename)
    w3 = w3.rename(columns=w3_rename)
    w4 = w4.rename(columns=w4_rename)

    # Join all 4 sheets on ticker (left from w1)
    combined = w1
    combined = combined.merge(w2, on="ticker", how="left")
    combined = combined.merge(w3, on="ticker", how="left")
    combined = combined.merge(w4, on="ticker", how="left")

    log.info("ETP combined (BBG): %d rows x %d cols", *combined.shape)

    # --- s1: stock data ---
    stock = pd.DataFrame()
    if SHEET_S1 in xl.sheet_names:
        stock = _read_sheet(xl, SHEET_S1)
        log.info("s1 (stock): %d rows x %d cols", *stock.shape)

    # --- mkt_status: reference ---
    mkt_status = pd.DataFrame()
    if SHEET_MKT_STATUS in xl.sheet_names:
        mkt_status = _read_sheet(xl, SHEET_MKT_STATUS)
        log.info("mkt_status: %d rows", len(mkt_status))

    return combined, stock, mkt_status


def _process_w4(w4: pd.DataFrame) -> pd.DataFrame:
    """Process w4 sheet: rename flow columns and positionally rename AUM columns.

    W4 layout:
    - Col 0: Ticker
    - Col 1: Fund Name (drop)
    - Cols 2-9: 8 flow columns
    - Col 10: AUM current (Formula Col. 1 or similar)
    - Cols 11-46: AUM history (aum_1 through aum_36)
    """
    # First rename known flow columns
    w4 = w4.rename(columns=W4_FLOW_COL_MAP)

    # Drop Fund Name if present
    w4 = w4.drop(columns=["Fund Name", "fund_name"], errors="ignore")

    # Drop null tickers
    w4 = w4.dropna(subset=["ticker"])

    # Positionally rename AUM columns (indices 9+ after ticker + 8 flows)
    # Find the position after the known columns
    known_cols = {"ticker"} | {v for v in W4_FLOW_COL_MAP.values() if v != "ticker"}
    remaining_cols = [c for c in w4.columns if c not in known_cols]

    # These remaining columns are the AUM columns (positional: current, then 1-36 months back)
    aum_names = ["aum"] + [f"aum_{i}" for i in range(1, 37)]

    if remaining_cols:
        rename_map = {}
        for i, col in enumerate(remaining_cols):
            if i < len(aum_names):
                rename_map[col] = aum_names[i]
        w4 = w4.rename(columns=rename_map)
        log.info("  w4 AUM columns renamed: %d cols (of %d remaining)",
                 len(rename_map), len(remaining_cols))

    return w4


# ---------------------------------------------------------------------------
# Legacy 5-sheet format (etp_base/etp_metrics/etp_returns/etp_flows)
# ---------------------------------------------------------------------------

def _read_5sheet(xl: pd.ExcelFile) -> pd.DataFrame:
    """Read legacy 5-sheet format and join ETP sheets on ticker."""
    base = _read_sheet(xl, SHEET_ETP_BASE)
    metrics = _read_sheet(xl, SHEET_ETP_METRICS)
    returns = _read_sheet(xl, SHEET_ETP_RETURNS)
    flows = _read_sheet(xl, SHEET_ETP_FLOWS)

    # Validate ticker column exists
    for name, df in [("etp_base", base), ("etp_metrics", metrics),
                     ("etp_returns", returns), ("etp_flows", flows)]:
        if "ticker" not in df.columns:
            raise ValueError(f"Sheet '{name}' missing 'ticker' column")

    # Drop rows with null ticker
    base = base.dropna(subset=["ticker"])
    metrics = metrics.dropna(subset=["ticker"])
    returns = returns.dropna(subset=["ticker"])
    flows = flows.dropna(subset=["ticker"])

    log.info("etp_base: %d rows, etp_metrics: %d, etp_returns: %d, etp_flows: %d",
             len(base), len(metrics), len(returns), len(flows))

    # Rename metrics/returns/flows columns with prefixes (except ticker)
    metrics_rename = {c: f"{W2_PREFIX}{c}" for c in metrics.columns if c != "ticker"}
    returns_rename = {c: f"{W3_PREFIX}{c}" for c in returns.columns if c != "ticker"}
    flows_rename = {c: f"{W4_PREFIX}{c}" for c in flows.columns if c != "ticker"}

    metrics = metrics.rename(columns=metrics_rename)
    returns = returns.rename(columns=returns_rename)
    flows = flows.rename(columns=flows_rename)

    # Join on ticker (left join from base)
    combined = base
    combined = combined.merge(metrics, on="ticker", how="left")
    combined = combined.merge(returns, on="ticker", how="left")
    combined = combined.merge(flows, on="ticker", how="left")

    log.info("ETP combined (5-sheet): %d rows x %d cols", *combined.shape)
    return combined


# ---------------------------------------------------------------------------
# Legacy single-sheet format (data_import)
# ---------------------------------------------------------------------------

def _read_legacy(xl: pd.ExcelFile) -> pd.DataFrame:
    """Read legacy single-sheet data_import format."""
    raw = _read_sheet(xl, "data_import")
    raw = raw.dropna(subset=["ticker"])

    # Build rename map (case-insensitive)
    w2_lower = {f.lower() for f in W2_FIELDS}
    w3_lower = {f.lower() for f in W3_FIELDS}
    w4_lower = {f.lower() for f in W4_FIELDS}
    base_lower = {f.lower() for f in BASE_FIELDS}

    # Keep only first occurrence of each field name
    seen_lower = set()
    keep_cols = []
    for col in raw.columns:
        col_lower = col.lower().strip()
        if col_lower in seen_lower:
            continue
        if col_lower in base_lower or col_lower in w2_lower or \
           col_lower in w3_lower or col_lower in w4_lower:
            keep_cols.append(col)
            seen_lower.add(col_lower)

    raw = raw[keep_cols].copy()

    # Apply prefixes
    rename = {}
    for col in raw.columns:
        cl = col.lower().strip()
        if cl in w2_lower:
            rename[col] = f"{W2_PREFIX}{cl}"
        elif cl in w3_lower:
            rename[col] = f"{W3_PREFIX}{cl}"
        elif cl in w4_lower:
            rename[col] = f"{W4_PREFIX}{cl}"

    raw = raw.rename(columns=rename)
    log.info("Legacy data_import: %d rows x %d cols", *raw.shape)
    return raw


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_sheet(xl: pd.ExcelFile, sheet: str) -> pd.DataFrame:
    """Read a sheet, stripping whitespace from column names."""
    df = xl.parse(sheet)
    df.columns = [str(c).strip() for c in df.columns]
    return df


def _read_stock(xl: pd.ExcelFile, sheet_name: str) -> pd.DataFrame:
    """Read stock data sheet if it exists."""
    if sheet_name in xl.sheet_names:
        stock = _read_sheet(xl, sheet_name)
        log.info("stock_data: %d rows", len(stock))
        return stock
    return pd.DataFrame()
