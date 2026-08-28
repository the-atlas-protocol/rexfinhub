"""market data daily time-series parser.

Parses the time-series sheets embedded in bloomberg_daily_file.xlsm:
    data_flow     — daily fund flows per product (948 products, 2021-11-10 → today)
    data_aum      — daily AUM per product (same dimensions)
    data_notional — daily gross notional per product
    data_price    — daily price per product (118 products, back to 2016-12-19)
    data_nav      — daily NAV per product (97 products, back to 2016-12-19)
    w5            — cross-sectional 1/2/3/5-day, 1m/3m/6m/ytd/1y returns

Also parses `mkt_master_data.aum_history_json` — 12-36 months monthly AUM per fund.

Produces a single long-format parquet panel for downstream analysis.
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
BBG_FILE = _ROOT / "data" / "DASHBOARD" / "bloomberg_daily_file.xlsm"
DB = _ROOT / "data" / "etp_tracker.db"
OUT_DIR = _ROOT / "data" / "analysis"
PARQUET = OUT_DIR / "bbg_timeseries_panel.parquet"


def _clean_ticker(t: str) -> str:
    if not isinstance(t, str):
        return ""
    return t.split()[0].upper().strip()


def _melt_time_sheet(sheet: str, metric: str) -> pd.DataFrame:
    df = pd.read_excel(BBG_FILE, sheet_name=sheet)
    date_col = df.columns[0]
    long = df.melt(id_vars=[date_col], var_name="ticker_raw", value_name="value")
    long = long.rename(columns={date_col: "date"})
    long["value"] = pd.to_numeric(long["value"], errors="coerce")
    long = long[long["value"].notna()].copy()
    long["ticker"] = long["ticker_raw"].astype(str).map(_clean_ticker)
    long = long[long["ticker"] != ""]
    long["metric"] = metric
    long["date"] = pd.to_datetime(long["date"], errors="coerce")
    long = long.dropna(subset=["date"])
    return long[["ticker", "date", "metric", "value"]].sort_values(["ticker", "date"]).reset_index(drop=True)


def load_data_flow() -> pd.DataFrame:
    return _melt_time_sheet("data_flow", "daily_flow")


def load_data_aum() -> pd.DataFrame:
    return _melt_time_sheet("data_aum", "aum")


def load_data_notional() -> pd.DataFrame:
    return _melt_time_sheet("data_notional", "notional")


def load_data_price() -> pd.DataFrame:
    return _melt_time_sheet("data_price", "price")


def load_data_nav() -> pd.DataFrame:
    return _melt_time_sheet("data_nav", "nav")


def load_w5() -> pd.DataFrame:
    df = pd.read_excel(BBG_FILE, sheet_name="w5")
    if "Ticker" in df.columns:
        df["ticker"] = df["Ticker"].astype(str).map(_clean_ticker)
    return df


def compute_forward_flow(flow: pd.DataFrame, window: int = 30) -> pd.DataFrame:
    """For each (ticker, date), sum the next `window` observations of flow.
    Uses reversed rolling sum for vectorized speed."""
    parts = []
    for ticker, group in flow.groupby("ticker", sort=False):
        g = group.sort_values("date").reset_index(drop=True)
        # reverse the series, rolling-sum of `window`, then reverse back and shift -1
        rev = g["value"].iloc[::-1].rolling(window, min_periods=1).sum().iloc[::-1]
        fwd = rev.shift(-1).fillna(0.0)
        out = pd.DataFrame({
            "ticker": ticker,
            "date": g["date"].values,
            "metric": f"forward_{window}d_flow",
            "value": fwd.values,
        })
        parts.append(out)
    return pd.concat(parts, ignore_index=True)


def load_aum_history(db_path: Path = DB) -> pd.DataFrame:
    """Parse mkt_master_data.aum_history_json (12-36 monthly AUM per fund)."""
    try:
        conn = sqlite3.connect(str(db_path))
    except Exception as e:
        log.warning("aum_history: could not open DB: %s", e)
        return pd.DataFrame()
    try:
        rows = conn.execute(
            "SELECT ticker, aum_history_json, updated_at FROM mkt_master_data "
            "WHERE aum_history_json IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    records = []
    for ticker, blob, updated_at in rows:
        if not blob:
            continue
        try:
            history = json.loads(blob)
        except json.JSONDecodeError:
            continue
        anchor = pd.to_datetime(updated_at, errors="coerce") if updated_at else pd.Timestamp.today()
        if pd.isna(anchor):
            anchor = pd.Timestamp.today()
        for key, val in history.items():
            if not key.startswith("aum_"):
                continue
            try:
                months_ago = int(key.split("_", 1)[1])
                value = float(val)
            except (ValueError, TypeError):
                continue
            dt = (anchor - pd.DateOffset(months=months_ago)).normalize()
            records.append({
                "ticker": _clean_ticker(ticker),
                "date": dt,
                "metric": "monthly_aum",
                "value": value,
            })
    return pd.DataFrame(records)


def build_panel(include_forward_flow: bool = True, forward_window: int = 30) -> pd.DataFrame:
    parts = []
    log.info("Loading data_flow...")
    flow = load_data_flow()
    parts.append(flow)
    if include_forward_flow:
        log.info("Computing forward %d-day flows...", forward_window)
        parts.append(compute_forward_flow(flow, window=forward_window))
    log.info("Loading data_aum...")
    parts.append(load_data_aum())
    log.info("Loading data_notional...")
    parts.append(load_data_notional())
    log.info("Loading data_price...")
    parts.append(load_data_price())
    log.info("Loading data_nav...")
    parts.append(load_data_nav())
    log.info("Loading aum_history_json...")
    ah = load_aum_history()
    if not ah.empty:
        parts.append(ah)

    panel = pd.concat(parts, ignore_index=True)
    panel = panel.sort_values(["ticker", "metric", "date"]).reset_index(drop=True)
    return panel


def save_panel(panel: pd.DataFrame, path: Path = PARQUET) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    panel.to_parquet(path, compression="snappy", index=False)
    log.info("Saved %d rows to %s (%.1f MB)", len(panel), path, path.stat().st_size / 1024 / 1024)
    return path


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    panel = build_panel()
    save_panel(panel)
    print(f"Panel shape: {panel.shape}")
    print(f"Date range: {panel['date'].min()} to {panel['date'].max()}")
    print(f"Metrics: {sorted(panel['metric'].unique())}")
    print(f"Tickers: {panel['ticker'].nunique():,}")


if __name__ == "__main__":
    main()
