"""NASDAQ + NYSE universe loader.

Pulls authoritative symbol lists from NASDAQ Trader (includes all US-listed
common stocks, ETFs, ADRs). Saves a point-in-time parquet snapshot per day.

Replaces bbg daily file as the source-of-truth for "what tickers exist today."
bbg keeps its role for time-series flow/AUM data only.

Sources:
    https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt
    https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt   (NYSE/AMEX)

Both are free, no auth, updated daily by NASDAQ Trader.
"""
from __future__ import annotations

import io
import logging
from datetime import date
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
HISTORY_DIR = _ROOT / "data" / "historical" / "universe"

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


def fetch_nasdaq() -> pd.DataFrame:
    r = requests.get(NASDAQ_URL, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), sep="|", skipfooter=1, engine="python")
    df["exchange"] = "NASDAQ"
    df = df.rename(columns={
        "Symbol": "ticker",
        "Security Name": "name",
        "ETF": "is_etf",
        "Test Issue": "is_test",
        "Financial Status": "financial_status",
    })
    return df


def fetch_other() -> pd.DataFrame:
    """NYSE + NYSE American + NYSE Arca + BATS."""
    r = requests.get(OTHER_URL, timeout=30)
    r.raise_for_status()
    df = pd.read_csv(io.StringIO(r.text), sep="|", skipfooter=1, engine="python")
    df = df.rename(columns={
        "ACT Symbol": "ticker",
        "Security Name": "name",
        "Exchange": "exchange_code",
        "ETF": "is_etf",
        "Test Issue": "is_test",
        "CQS Symbol": "cqs_symbol",
    })
    exchange_map = {"N": "NYSE", "A": "NYSE AMERICAN", "P": "NYSE ARCA",
                    "Z": "BATS", "V": "IEX"}
    df["exchange"] = df["exchange_code"].map(exchange_map).fillna(df["exchange_code"])
    return df


def build_universe() -> pd.DataFrame:
    nas = fetch_nasdaq()
    other = fetch_other()

    common = ["ticker", "name", "exchange", "is_etf", "is_test"]
    all_listings = pd.concat([
        nas[common],
        other[common],
    ], ignore_index=True)

    all_listings["is_etf"] = all_listings["is_etf"].astype(str).str.upper() == "Y"
    all_listings["is_test"] = all_listings["is_test"].astype(str).str.upper() == "Y"

    # Filter: real tradeable common stocks + ADRs, not ETFs, not test issues
    equities = all_listings[(~all_listings["is_etf"]) & (~all_listings["is_test"])].copy()
    equities = equities.dropna(subset=["ticker"])
    equities["ticker"] = equities["ticker"].astype(str).str.upper().str.strip()
    equities = equities[equities["ticker"].str.match(r"^[A-Z][A-Z0-9.\-]{0,5}$", na=False)]
    equities = equities.drop_duplicates(subset="ticker")

    log.info("Universe: %d total listings, %d equities (non-ETF, non-test)",
             len(all_listings), len(equities))
    return equities.set_index("ticker")


def save_snapshot(df: pd.DataFrame, for_date: date | None = None) -> Path:
    d = for_date or date.today()
    out_dir = HISTORY_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{d.isoformat()}.parquet"
    df.to_parquet(path, compression="snappy")
    log.info("Saved universe snapshot: %s (%d rows, %.1f KB)",
             path, len(df), path.stat().st_size / 1024)
    return path


def latest_snapshot() -> pd.DataFrame | None:
    if not HISTORY_DIR.exists():
        return None
    files = sorted(HISTORY_DIR.glob("*.parquet"))
    if not files:
        return None
    return pd.read_parquet(files[-1])


def diff_new_listings(today_df: pd.DataFrame,
                      yesterday_df: pd.DataFrame | None) -> pd.DataFrame:
    """Return tickers that appear today but not yesterday (new listings)."""
    if yesterday_df is None or yesterday_df.empty:
        return pd.DataFrame()
    new = today_df.index.difference(yesterday_df.index)
    return today_df.loc[new]


def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--diff", action="store_true", help="Show new listings vs. yesterday")
    args = p.parse_args()

    yesterday = latest_snapshot()
    today = build_universe()
    save_snapshot(today)

    print(f"\nUniverse size: {len(today):,} equities")
    print(f"By exchange:")
    print(today["exchange"].value_counts().to_string())

    if args.diff and yesterday is not None:
        new = diff_new_listings(today, yesterday)
        print(f"\nNew listings since last snapshot: {len(new)}")
        if len(new) > 0:
            print(new[["name", "exchange"]].to_string())


if __name__ == "__main__":
    main()
