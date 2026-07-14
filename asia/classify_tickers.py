"""Classify all 96 DB tickers — find Bloomberg column + lifecycle status.

Lifecycle rules:
- active:           has non-null Bloomberg AUM in the latest month available
- delisted:         had non-null AUM historically but is NaN in recent months
- pending_launch:   no historical Bloomberg data at all (per Ryu: BBUP, BNBR, FIGO, SPOU)
- never_launched:   truly no data anywhere (distinction from pending is judgment call)

Output: tickers_classification.json — {ticker: {bbg_ticker, bbg_suffix, lifecycle, first_seen, last_seen, latest_aum}}
"""
import json
from datetime import date
from pathlib import Path

import pandas as pd
import psycopg2
import psycopg2.extras

BB = Path(r"C:/Users/RyuEl-Asmar/REX Financial LLC/REX Financial LLC - MasterFiles/MASTER Data/bloomberg_daily_file.xlsm")


def main():
    # DB tickers
    conn = psycopg2.connect(host="localhost", port=5433, user="postgres", dbname="rex_asia",
                            cursor_factory=psycopg2.extras.RealDictCursor)
    cur = conn.cursor()
    cur.execute("SELECT ticker, name FROM etp ORDER BY ticker")
    db_rows = cur.fetchall()
    conn.close()

    # Bloomberg AUM sheet
    aum = pd.read_excel(BB, sheet_name="data_aum", header=0)
    aum = aum.rename(columns={aum.columns[0]: "Date"})
    aum["Date"] = pd.to_datetime(aum["Date"], errors="coerce")
    bbg_cols = [c for c in aum.columns if c != "Date"]

    # Microsector sheet — ETNs. Authoritative for ETN AUM.
    ms_raw = pd.read_excel(BB, sheet_name="microsector", header=None)
    ms_tickers = ms_raw.iloc[3, 1:].tolist()
    ms = ms_raw.iloc[4:].copy()
    ms.columns = ["Date"] + ms_tickers
    ms["Date"] = pd.to_datetime(ms["Date"], errors="coerce")
    ms = ms.dropna(subset=["Date"])
    etn_set = set(ms_tickers)

    def bbg_col_for(db_ticker: str) -> tuple[str | None, str | None]:
        """Return (bbg_column, suffix) — try US then LN. For DB names ending in _LN, drop the suffix."""
        base = db_ticker
        if base.endswith("_LN"):
            base = base[:-3]
            preferred = "LN"
        else:
            preferred = "US"
        for suffix in ([preferred, "LN" if preferred == "US" else "US"]):
            col = f"{base} {suffix} Equity"
            if col in bbg_cols:
                return col, suffix
        return None, None

    # Classify
    KNOWN_PENDING = {"BBUP", "BNBR", "FIGO", "SPOU"}

    result = {}
    for r in db_rows:
        t = r["ticker"]
        col, suffix = bbg_col_for(t)
        rec = {"name": r["name"], "bbg_ticker": col, "bbg_suffix": suffix}

        if t in KNOWN_PENDING:
            rec["lifecycle"] = "pending_launch"
            rec["first_seen"] = rec["last_seen"] = None
            rec["latest_aum_musd"] = None
            rec["is_etn"] = False
            result[t] = rec
            continue

        rec["is_etn"] = t in etn_set

        # Pick the right source: microsector for ETNs, data_aum for ETFs
        if rec["is_etn"]:
            series = ms[t] if t in ms.columns else None
            dates = ms["Date"]
            scale = 1e-6  # microsector values are raw $; convert to $M for consistency
        elif col is not None:
            series = aum[col]
            dates = aum["Date"]
            scale = 1.0  # data_aum already in $M
        else:
            series = None

        if series is None or series.notna().sum() == 0:
            rec["lifecycle"] = "never_launched"
            rec["first_seen"] = rec["last_seen"] = None
            rec["latest_aum_musd"] = None
        else:
            # Consider a value "meaningful" if > 0 (delisted funds are 0 or NaN)
            nonzero_mask = series.notna() & (series > 0)
            if nonzero_mask.sum() == 0:
                rec["lifecycle"] = "never_launched"
                rec["first_seen"] = rec["last_seen"] = None
                rec["latest_aum_musd"] = None
            else:
                first_seen = dates[nonzero_mask].iloc[0].date().isoformat()
                last_seen = dates[nonzero_mask].iloc[-1].date().isoformat()
                latest_aum = float(series[nonzero_mask].iloc[-1]) * scale
                rec["first_seen"] = first_seen
                rec["last_seen"] = last_seen
                rec["latest_aum_musd"] = latest_aum
                file_max = aum["Date"].max().date()
                days_stale = (file_max - dates[nonzero_mask].iloc[-1].date()).days
                rec["lifecycle"] = "active" if days_stale <= 30 else "delisted"
                rec["days_stale"] = days_stale
        result[t] = rec

    Path("tickers_classification.json").write_text(json.dumps(result, indent=2, sort_keys=True))

    # Summary
    from collections import Counter
    cnt = Counter(r["lifecycle"] for r in result.values())
    print("Lifecycle breakdown:")
    for k, v in cnt.most_common():
        print(f"  {k:18s}: {v}")
    print()

    # Show non-active details
    for t, r in sorted(result.items()):
        if r["lifecycle"] != "active":
            print(f"  {t:8s} [{r['lifecycle']:16s}] bbg={r['bbg_ticker']}  first={r['first_seen']}  last={r['last_seen']}")


if __name__ == "__main__":
    main()
