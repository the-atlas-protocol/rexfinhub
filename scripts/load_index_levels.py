"""Load index_levels.xlsx (or .csv) into the autocall_* tables.

Wipes & replaces all four tables in one transaction. Also clears the
distribution-sweep cache, since stale cache after a fresh data drop would
return outdated results.

Usage:
    python scripts/load_index_levels.py path/to/index_levels.xlsx
    python scripts/load_index_levels.py path/to/index_levels.csv

The xlsx/csv is wide format: row 1 = ticker headers, col 1 = dates,
cells contain levels (or "#N/A" before each ticker's inception).
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy.orm import Session

# Add project root to path so we can import webapp.*
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from webapp.database import SessionLocal, init_db  # noqa: E402
from webapp.models import (  # noqa: E402
    AutocallCrisisPreset,
    AutocallIndexLevel,
    AutocallIndexMetadata,
    AutocallSweepCache,
)


# ---------------------------------------------------------------------------
# Static reference metadata (curated from the autocall_project.txt spec).
# Categories control dropdown visibility on the page:
#   underlying          — broad-market indices (SPX, NDX, bond TR, etc.)
#   strategy_underlying — vol-targeted indices used as autocall underlyings
#   autocall_product    — the autocall strategy indices THEMSELVES (hidden
#                         from reference pickers — they ARE products, not refs)
# sort_order controls dropdown ordering within a group.
# ---------------------------------------------------------------------------

INDEX_METADATA: list[tuple[str, str, str, str, int]] = [
    # ticker, full_name, short_name, category, sort_order
    # --- Pure underlyings (default-visible) ---
    ("SPX Index", "S&P 500 Index", "S&P 500", "underlying", 10),
    ("NDX Index", "Nasdaq-100 Index", "Nasdaq-100", "underlying", 20),
    ("SPXT Index", "S&P 500 Total Return Index", "S&P 500 TR", "underlying", 30),
    ("B500T Index", "Bloomberg 500 Total Return Index", "BBG 500 TR", "underlying", 40),
    ("SPDAUDT Index", "S&P 500 Dividend Aristocrats Total Return Index", "S&P 500 Div Aristocrats TR", "underlying", 50),
    ("VIX Index", "Cboe Volatility Index", "VIX", "underlying", 60),
    ("LBUSTRUU Index", "Bloomberg US Agg Total Return Value Unhedged USD", "BBG Agg TR", "underlying", 70),
    ("LUACTRUU Index", "Bloomberg US Corporate Total Return Value Unhedged USD", "BBG Corp TR", "underlying", 80),
    ("LF98TRUU Index", "Bloomberg US Corporate High Yield Total Return Index Value Unhedged USD", "BBG HY TR", "underlying", 90),
    # --- Strategy underlyings (default-visible) ---
    ("BMAXUS Index", "Bloomberg US Large Cap VolMax", "BBG Large Cap VolMax", "strategy_underlying", 110),
    ("MQUSLVA Index", "MerQube US Large-Cap Vol Advantage Index", "MerQube Large-Cap Vol Advantage", "strategy_underlying", 120),
    ("MQVTUSLE Index", "MerQube US Large Cap Vol Target 40% Index", "MerQube Large-Cap Vol Target 40", "strategy_underlying", 130),
    ("MQUSQVA Index", "MerQube Nasdaq 100 Vol Advantage Index", "MerQube Nasdaq-100 Vol Advantage", "strategy_underlying", 140),
    ("MQVTUSTE Index", "MerQube US Tech Vol Target 40% Index", "MerQube Tech Vol Target 40", "strategy_underlying", 150),
    ("MQUSTVA Index", "MerQube US Tech+ Vol Advantage Index", "MerQube Tech+ Vol Advantage", "strategy_underlying", 160),
    ("MQUSHIQL Index", "MerQube US Vol Advantage Tech+ HiQ Leverage Index", "MerQube Tech+ HiQ Leverage", "strategy_underlying", 170),
    # --- Autocall product indices (hidden from reference dropdowns) ---
    ("BMAXATCL Index", "Bloomberg US Large Cap VolMax Autocallable Total Return Index", "BBG VolMax Autocall TR", "autocall_product", 210),
    ("BMAXACER Index", "Bloomberg US Large Cap VolMax Autocallable Excess Return Index", "BBG VolMax Autocall ER", "autocall_product", 220),
    ("BMAXACFR Index", "Bloomberg US Large Cap VolMax Autocallable Funded Return Index", "BBG VolMax Autocall FR", "autocall_product", 230),
    ("BMAXACPN Index", "Bloomberg US Large Cap VolMax Autocallable Coupon Index", "BBG VolMax Autocall Coupon", "autocall_product", 240),
    ("MQAUTOCL Index", "MerQube US Large-Cap Vol Advantage Autocallable Index", "MerQube LC Autocall", "autocall_product", 250),
    ("MQAUTOCP Index", "MerQube US Large-Cap Vol Advantage Autocallable Index - Price Return", "MerQube LC Autocall PR", "autocall_product", 260),
    ("MQAUCOUP Index", "MerQube US Large-Cap Vol Advantage Autocallable Index - Cumulative Coupon", "MerQube LC Autocall Cum. Coupon", "autocall_product", 270),
    ("MQAUTOQL Index", "MerQube Nasdaq-100 Vol Advantage Autocallable Index", "MerQube NDX Autocall", "autocall_product", 280),
    ("MQAUTOQP Index", "MerQube Nasdaq-100 Vol Advantage Autocallable Index - Price Return", "MerQube NDX Autocall PR", "autocall_product", 290),
    ("MQAQCOUP Index", "MerQube US Technology Vol Advantage Autocallable Index - Cumulative Coupon", "MerQube Tech Autocall Cum. Coupon", "autocall_product", 300),
]


CRISIS_PRESETS: list[tuple[str, date, int]] = [
    ("GFC", date(2008, 5, 19), 10),
    ("EU Debt", date(2011, 5, 2), 20),
    ("China/Oil", date(2015, 5, 21), 30),
    ("Volmageddon", date(2018, 1, 26), 40),
    ("US-China Trade", date(2018, 10, 3), 50),
    ("COVID Crash", date(2020, 2, 19), 60),
    ("Inflation/Hikes", date(2021, 12, 31), 70),
    ("Tariff Trade War", date(2025, 2, 19), 80),
]


def _read_wide(path: Path) -> pd.DataFrame:
    """Read the wide-format file. Date in col 1, tickers across row 1."""
    suffix = path.suffix.lower()
    if suffix in (".xlsx", ".xlsm"):
        # Source has a two-row header: row 1 = tickers, row 2 = full names.
        df = pd.read_excel(path, header=0, skiprows=[1])
    elif suffix == ".csv":
        # CSV deliveries are expected to be single-header (date,ticker1,ticker2,...).
        df = pd.read_csv(path, engine="python", on_bad_lines="skip")
    else:
        raise ValueError(f"Unsupported extension: {suffix}")

    # First column is dates, rest are tickers.
    df = df.rename(columns={df.columns[0]: "date"})
    df["date"] = pd.to_datetime(df["date"]).dt.date
    return df


def _wide_to_long(df: pd.DataFrame) -> list[tuple[date, str, float]]:
    """Reshape wide → list of (date, ticker, level) rows. Drops #N/A and NaN."""
    out: list[tuple[date, str, float]] = []
    tickers = [c for c in df.columns if c != "date"]
    for ticker in tickers:
        col = df[["date", ticker]].copy()
        col = col[col[ticker].notna()]
        # Some cells contain the literal string "#N/A".
        col = col[col[ticker].astype(str).str.strip() != "#N/A"]
        # Coerce remaining values to float.
        col[ticker] = pd.to_numeric(col[ticker], errors="coerce")
        col = col[col[ticker].notna()]
        for d, lvl in zip(col["date"], col[ticker]):
            out.append((d, ticker, float(lvl)))
    return out


def load(path: Path, db: Session) -> dict:
    """Wipe & reload all four autocall_* tables. Returns a summary dict."""
    df = _read_wide(path)
    long_rows = _wide_to_long(df)

    # Validate: every ticker in the file must have metadata.
    file_tickers = {t for _, t, _ in long_rows}
    meta_tickers = {t for t, *_ in INDEX_METADATA}
    unknown = file_tickers - meta_tickers
    missing = meta_tickers - file_tickers
    if unknown:
        raise ValueError(
            f"Tickers in file but not in INDEX_METADATA: {sorted(unknown)}. "
            f"Add to scripts/load_index_levels.py:INDEX_METADATA before reload."
        )

    # Wipe.
    db.query(AutocallSweepCache).delete()
    db.query(AutocallIndexLevel).delete()
    db.query(AutocallIndexMetadata).delete()
    db.query(AutocallCrisisPreset).delete()
    db.flush()

    # Insert metadata (only entries whose ticker actually appears in the file
    # — keeps the dropdowns honest if a future drop excludes a ticker).
    for ticker, full_name, short_name, category, sort_order in INDEX_METADATA:
        if ticker not in file_tickers:
            continue
        db.add(AutocallIndexMetadata(
            ticker=ticker,
            full_name=full_name,
            short_name=short_name,
            category=category,
            sort_order=sort_order,
        ))

    # Insert presets.
    for name, start_date, sort_order in CRISIS_PRESETS:
        db.add(AutocallCrisisPreset(
            name=name, start_date=start_date, sort_order=sort_order,
        ))

    # Insert levels — bulk-insert via raw mappings is ~100× faster than ORM.
    db.bulk_insert_mappings(
        AutocallIndexLevel,
        [{"date": d, "ticker": t, "level": l} for d, t, l in long_rows],
    )

    db.commit()

    return {
        "rows": len(long_rows),
        "tickers": len(file_tickers),
        "missing_from_file": sorted(missing),
        "date_min": min(d for d, _, _ in long_rows).isoformat(),
        "date_max": max(d for d, _, _ in long_rows).isoformat(),
    }


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(2)

    src = Path(sys.argv[1]).expanduser().resolve()
    if not src.exists():
        print(f"File not found: {src}", file=sys.stderr)
        sys.exit(1)

    print(f"Loading {src}...")
    init_db()
    db = SessionLocal()
    try:
        summary = load(src, db)
    finally:
        db.close()

    print(f"  rows inserted: {summary['rows']:,}")
    print(f"  tickers:       {summary['tickers']}")
    print(f"  date range:    {summary['date_min']} -> {summary['date_max']}")
    if summary["missing_from_file"]:
        print(f"  WARNING — metadata tickers not in file: {summary['missing_from_file']}")
    print("Done.")


if __name__ == "__main__":
    main()
