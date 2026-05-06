"""Filing race clock — who filed what, when it's likely live, our react window.

Uses filings table + FundStatus + mkt_master_data.

Outputs:
    1. Per-issuer cadence: median days from 485APOS → active
    2. Recent competitor 485APOS filings with projected launch dates
    3. Per-underlier race status: did REX file too? how many days until we need to?
"""
from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
OUT_RACE = _ROOT / "data" / "analysis" / "filing_race.parquet"
OUT_CADENCE = _ROOT / "data" / "analysis" / "issuer_cadence.parquet"


def _clean(t):
    return t.split()[0].upper().strip() if isinstance(t, str) else ""


def load_filings_joined() -> pd.DataFrame:
    """filings + fund_extractions + mkt_master_data → one frame per (filing, product)."""
    conn = sqlite3.connect(str(DB))
    try:
        df = pd.read_sql_query(
            """
            SELECT f.filing_date,
                   f.form,
                   f.accession_number,
                   f.registrant,
                   mmd.ticker AS product_ticker,
                   mmd.fund_name,
                   mmd.issuer,
                   mmd.is_rex,
                   mmd.map_li_underlier,
                   mmd.map_li_leverage_amount,
                   mmd.map_li_direction,
                   mmd.market_status,
                   mmd.aum,
                   mmd.inception_date
            FROM filings f
            JOIN fund_extractions fe ON fe.filing_id = f.id
            JOIN mkt_master_data mmd ON mmd.ticker = fe.class_symbol || ' US'
            WHERE f.form IN ('485APOS', '485BPOS', '485BXT', 'N-1A', 'S-1')
              AND mmd.primary_category = 'LI'
              AND mmd.map_li_underlier IS NOT NULL
              AND mmd.map_li_underlier != ''
            """,
            conn,
        )
    finally:
        conn.close()

    df["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce")
    df["inception_date"] = pd.to_datetime(df["inception_date"], errors="coerce")
    df["underlier"] = df["map_li_underlier"].astype(str).map(_clean)
    df = df.dropna(subset=["filing_date", "underlier"])
    df = df[df["underlier"] != ""]
    return df


def issuer_cadence(filings: pd.DataFrame) -> pd.DataFrame:
    """For each issuer, median days from earliest 485APOS → active product."""
    active = filings[filings["market_status"].isin(["ACTV", "ACTIVE"])].copy()
    apos = filings[filings["form"] == "485APOS"].copy()

    # Per product: first 485APOS date
    first_apos = apos.groupby("product_ticker")["filing_date"].min().rename("first_apos_date")

    # Merge with inception_date on active products
    merged = active.drop_duplicates("product_ticker").join(
        first_apos, on="product_ticker", how="left"
    )
    merged = merged.dropna(subset=["inception_date", "first_apos_date"])
    merged["days_file_to_launch"] = (
        merged["inception_date"] - merged["first_apos_date"]
    ).dt.days
    # Filter obvious data errors
    merged = merged[(merged["days_file_to_launch"] > 0) &
                    (merged["days_file_to_launch"] < 730)]

    cadence = merged.groupby("issuer").agg(
        n_launches=("product_ticker", "count"),
        median_days=("days_file_to_launch", "median"),
        mean_days=("days_file_to_launch", "mean"),
        min_days=("days_file_to_launch", "min"),
        max_days=("days_file_to_launch", "max"),
        is_rex=("is_rex", "max"),
    ).sort_values("n_launches", ascending=False)
    cadence["median_days"] = cadence["median_days"].astype(int)
    cadence["mean_days"] = cadence["mean_days"].astype(int)
    return cadence


def recent_485apos_with_projection(filings: pd.DataFrame,
                                    cadence: pd.DataFrame,
                                    days: int = 120) -> pd.DataFrame:
    """Recent competitor 485APOS filings. For each, project launch date using
    issuer's historical cadence. Show REX's reaction status on the same
    underlier."""
    today = pd.Timestamp.today()
    cutoff = today - pd.Timedelta(days=days)

    recent = filings[
        (filings["form"] == "485APOS") &
        (filings["is_rex"] == 0) &
        (filings["filing_date"] >= cutoff)
    ].copy()

    # Deduplicate by (registrant, underlier, filing_date within 1 day)
    recent = recent.drop_duplicates(
        subset=["registrant", "underlier", "filing_date"]
    )

    # Project launch date using issuer cadence (fall back to 75-day rule 485(a))
    DEFAULT_CADENCE = 75

    def project(row):
        iss = row["issuer"]
        if iss in cadence.index:
            med = cadence.loc[iss, "median_days"]
            return row["filing_date"] + pd.Timedelta(days=int(med))
        return row["filing_date"] + pd.Timedelta(days=DEFAULT_CADENCE)

    recent["projected_launch"] = pd.to_datetime(recent.apply(project, axis=1))
    recent["days_until_launch"] = (recent["projected_launch"] - today).dt.days

    # REX reaction status per underlier
    rex_filings = filings[filings["is_rex"] == 1].groupby("underlier").agg(
        rex_latest_filing=("filing_date", "max"),
        rex_any_active=("market_status",
                        lambda x: int((x.isin(["ACTV", "ACTIVE"])).any())),
    )
    recent = recent.merge(rex_filings, on="underlier", how="left")
    recent["rex_has_reacted"] = recent["rex_latest_filing"].notna() & (
        recent["rex_latest_filing"] >= (recent["filing_date"] - pd.Timedelta(days=30))
    )

    # Sort by projected launch (most urgent first)
    return recent.sort_values("projected_launch").reset_index(drop=True)


def main():
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--days", type=int, default=120, help="Lookback for recent filings")
    args = p.parse_args()

    filings = load_filings_joined()
    log.info("Loaded %d L&I filings", len(filings))

    cadence = issuer_cadence(filings)
    cadence.to_parquet(OUT_CADENCE)
    log.info("Issuer cadence: %d issuers", len(cadence))

    race = recent_485apos_with_projection(filings, cadence, days=args.days)
    race.to_parquet(OUT_RACE, index=False)
    log.info("Recent competitor 485APOS: %d filings", len(race))

    print("=" * 100)
    print("ISSUER CADENCE — median days from 485APOS to active product")
    print("=" * 100)
    print(f"{'Issuer':<40} {'Launches':>10} {'Median days':>12} {'Range':>20}")
    for iss, row in cadence.head(15).iterrows():
        rng = f"[{row['min_days']}-{row['max_days']}]"
        rex = " (REX)" if row["is_rex"] else ""
        print(f"  {iss[:40]:<40}{rex:<6} {int(row['n_launches']):>10} "
              f"{int(row['median_days']):>12}  {rng:>18}")

    print()
    print("=" * 100)
    print(f"RECENT COMPETITOR 485APOS FILINGS (last {args.days} days)")
    print("=" * 100)
    upcoming = race[race["days_until_launch"] > 0].head(20)
    past = race[race["days_until_launch"] <= 0].tail(10)

    print(f"\nProjected-launch in FUTURE (launch window still open): {len(upcoming)} filings")
    for _, row in upcoming.iterrows():
        days_go = row["days_until_launch"]
        rex_stat = "REX filed" if row["rex_has_reacted"] else "REX NOT filed"
        urgency = "URGENT" if days_go < 30 else ""
        print(f"  {row['filing_date'].date()} + {int(days_go):>3}d → "
              f"{row['underlier']:<10} [{row['registrant'][:30]}] "
              f"[{rex_stat}] {urgency}")

    print(f"\nAlready past projected launch (missed window or launched): {len(past)} recent")
    for _, row in past.tail(10).iterrows():
        print(f"  {row['filing_date'].date()} → {row['projected_launch'].date()} "
              f"{row['underlier']:<10} [{row['registrant'][:30]}]")


if __name__ == "__main__":
    main()
