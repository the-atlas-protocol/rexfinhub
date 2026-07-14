"""Fetch month-end FX rates via yfinance for Asia-reporting currencies.

Output: fx_rates.json — {month_end: {pair: rate}}
Pairs: USDKRW, USDJPY, USDHKD, USDSGD, USDTHB, USDMYR, USDTWD
Values expressed as LOCAL per USD (so 1 USD = KRW 1427.5 etc.).
"""
import json
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

PAIRS = ["KRW", "JPY", "HKD", "SGD", "THB", "MYR", "TWD"]
# yfinance tickers: e.g. KRW=X means USDKRW
TICKERS = {c: f"{c}=X" for c in PAIRS}


def month_end_fx(year: int, month: int, data: pd.DataFrame) -> tuple[date | None, float | None]:
    """Return the last trading-day close of the month, or None if no data."""
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)
    mask = (data.index.date >= start) & (data.index.date < end)
    month_rows = data.loc[mask]
    if month_rows.empty:
        return None, None
    last = month_rows.iloc[-1]
    return month_rows.index[-1].date(), float(last["Close"].iloc[0] if hasattr(last["Close"], "iloc") else last["Close"])


def main():
    # 14 months: Feb 25 through Mar 26 inclusive
    months = []
    y, m = 2025, 2
    while (y, m) <= (2026, 3):
        months.append((y, m))
        m += 1
        if m > 12:
            y += 1; m = 1

    result = {}  # {yyyy-mm: {ccy: {rate, as_of_date}}}
    for ccy, tk in TICKERS.items():
        print(f"  Pulling {tk}...")
        hist = yf.Ticker(tk).history(start="2025-01-01", end="2026-04-15", auto_adjust=False)
        if hist.empty:
            print(f"    no data")
            continue
        for (y, m) in months:
            label = f"{y:04d}-{m:02d}"
            as_of, rate = month_end_fx(y, m, hist)
            if rate is None:
                continue
            result.setdefault(label, {})[ccy] = {"rate": rate, "as_of": as_of.isoformat()}

    out = Path("fx_rates.json")
    out.write_text(json.dumps(result, indent=2, sort_keys=True))
    print(f"\nSaved {out}  ({len(result)} months x {len(PAIRS)} currencies)")

    # Quick sanity print for Feb26 and Mar26
    for lbl in ["2026-02", "2026-03"]:
        print(f"\n{lbl}:")
        for ccy, rec in result.get(lbl, {}).items():
            print(f"  1 USD = {rec['rate']:>10,.4f} {ccy}   (as of {rec['as_of']})")


if __name__ == "__main__":
    main()
