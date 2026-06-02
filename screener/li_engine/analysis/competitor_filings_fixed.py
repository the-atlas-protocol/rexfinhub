"""Competitor filing timestamps per underlier — fixed version.

Join path: filings → fund_extractions (via filing_id) → mkt_master_data
(via class_symbol + ' US' == ticker) → map_li_underlier.

The agent's previous attempt produced empty frames; re-joining with an
explicit suffix concatenation fixes it.
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from webapp.services.ticker_normalize import normalize_underlier as _clean

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
OUT_LONG = _ROOT / "data" / "analysis" / "filings_by_underlier.parquet"
OUT_CS = _ROOT / "data" / "analysis" / "competitor_filing_cross_section.parquet"


def build_filings_long() -> pd.DataFrame:
    conn = sqlite3.connect(str(DB))
    try:
        df = pd.read_sql_query(
            """
            SELECT f.filing_date,
                   f.form,
                   f.registrant,
                   f.accession_number,
                   fe.class_symbol,
                   mmd.ticker AS product_ticker,
                   mmd.is_rex,
                   mmd.map_li_underlier,
                   mmd.map_li_leverage_amount,
                   mmd.map_li_direction,
                   mmd.issuer
            FROM filings f
            JOIN fund_extractions fe ON fe.filing_id = f.id
            JOIN mkt_master_data mmd ON mmd.ticker = fe.class_symbol || ' US'
            WHERE f.form IN ('485APOS', '485BPOS', '485BXT', 'N-1A', 'S-1')
              AND mmd.map_li_underlier IS NOT NULL
              AND mmd.map_li_underlier != ''
              AND mmd.primary_category = 'LI'
            """,
            conn,
        )
    finally:
        conn.close()

    df["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce")
    df = df.dropna(subset=["filing_date"])
    df["underlier"] = df["map_li_underlier"].astype(str).map(_clean)
    df = df[df["underlier"] != ""]
    df = df.sort_values(["underlier", "filing_date"]).reset_index(drop=True)

    df["days_since_prior_filing_same_underlier"] = (
        df.groupby("underlier")["filing_date"].diff().dt.days
    )
    return df


def build_cross_section(long: pd.DataFrame) -> pd.DataFrame:
    today = pd.Timestamp.today()
    cutoff_180 = today - pd.Timedelta(days=180)
    cutoff_ytd = pd.Timestamp(year=today.year, month=1, day=1)

    cs_rows = []
    for underlier, grp in long.groupby("underlier"):
        non_rex = grp[grp["is_rex"] == 0]
        rex = grp[grp["is_rex"] == 1]

        comp_485apos_180d = non_rex[(non_rex["form"] == "485APOS") & (non_rex["filing_date"] >= cutoff_180)]
        comp_485apos_ytd = non_rex[(non_rex["form"] == "485APOS") & (non_rex["filing_date"] >= cutoff_ytd)]

        days_since_last_comp = None
        if len(non_rex) > 0:
            days_since_last_comp = int((today - non_rex["filing_date"].max()).days)

        days_since_last_rex = None
        if len(rex) > 0:
            days_since_last_rex = int((today - rex["filing_date"].max()).days)

        cs_rows.append({
            "underlier": underlier,
            "n_rex_filings_ever": int(len(rex)),
            "n_competitor_filings_ever": int(len(non_rex)),
            "n_competitor_485apos_180d": int(len(comp_485apos_180d)),
            "n_competitor_485apos_ytd": int(len(comp_485apos_ytd)),
            "days_since_last_competitor_filing": days_since_last_comp,
            "days_since_last_rex_filing": days_since_last_rex,
            "n_unique_competitors_ever": int(non_rex["registrant"].nunique()),
            "n_unique_issuers_ever": int(grp["issuer"].nunique()),
            "latest_filing_date": grp["filing_date"].max(),
        })
    return pd.DataFrame(cs_rows).set_index("underlier").sort_values("n_competitor_485apos_180d", ascending=False)


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    long = build_filings_long()
    log.info("Long: %d rows", len(long))
    long.to_parquet(OUT_LONG, compression="snappy", index=False)

    cs = build_cross_section(long)
    log.info("Cross-section: %d underliers", len(cs))
    cs.to_parquet(OUT_CS, compression="snappy")

    print(f"\nTotal filings: {len(long):,}")
    print(f"REX filings: {(long['is_rex'] == 1).sum():,}")
    print(f"Competitor filings: {(long['is_rex'] == 0).sum():,}")
    print(f"Distinct underliers: {long['underlier'].nunique()}")
    print()
    print("Top 10 underliers by recent competitor 485APOS activity (last 180d):")
    print(cs.head(10)[["n_competitor_485apos_180d", "n_rex_filings_ever",
                      "n_competitor_filings_ever", "days_since_last_competitor_filing"]].to_string())
    print()
    print("Top 10 underliers by REX filings:")
    rex_top = cs.sort_values("n_rex_filings_ever", ascending=False).head(10)
    print(rex_top[["n_rex_filings_ever", "n_competitor_filings_ever",
                   "n_unique_competitors_ever"]].to_string())


if __name__ == "__main__":
    main()
