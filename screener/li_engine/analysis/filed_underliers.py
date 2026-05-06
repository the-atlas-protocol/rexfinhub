"""Extract every underlier ticker that appears in any L&I fund filing,
regardless of whether the product is yet in mkt_master_data.

The join in whitespace_v4 missed AXTI, FIG, KLAR, etc. because those products
were filed (showed up in fund_extractions.series_name) but the products
weren't yet in mkt_master_data. We need to be exhaustive.

Strategy: regex scan the names for leverage/inverse patterns + extract underlier.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
OUT = _ROOT / "data" / "analysis" / "filed_underliers.parquet"


# Regex patterns that match L&I product naming conventions
# Each captures the UNDERLIER ticker (typically 2-5 uppercase letters)
PATTERNS = [
    # T-REX 2X LONG AXTI DAILY TARGET ETF
    # T-REX 2X INVERSE TSLA DAILY TARGET ETF
    re.compile(r"\bT-?REX\s+(?:2X|3X)\s+(?:LONG|SHORT|INVERSE)\s+([A-Z]{2,6})\s+DAILY", re.IGNORECASE),
    # Direxion Daily 2X XXX Bull
    re.compile(r"\bDirexion\s+Daily\s+(?:2X|3X)\s+([A-Z]{2,6})\s+(?:Bull|Bear)", re.IGNORECASE),
    # GraniteShares 2x Long XXX Daily ETF
    re.compile(r"\b(?:GraniteShares|GS)\s+(?:2x|3x|2X|3X)\s+(?:Long|Short)\s+([A-Z]{2,6})\s+Daily", re.IGNORECASE),
    # Tradr 2X Long AXTI Daily ETF
    re.compile(r"\bTradr\s+(?:2X|3X)\s+(?:Long|Short)\s+([A-Z]{2,6})\s+Daily", re.IGNORECASE),
    # AXTI 2x Daily ETF / NVDA 2x Daily Long ETF
    re.compile(r"\b([A-Z]{2,6})\s+(?:2x|3x|2X|3X)\s+Daily\s+(?:Long|Short|ETF)", re.IGNORECASE),
    # Defiance Daily Target 2x Long XXX ETF
    re.compile(r"\bDefiance\s+(?:Daily\s+Target\s+)?(?:2x|3x|2X|3X)\s+(?:Long|Short)\s+([A-Z]{2,6})\b", re.IGNORECASE),
    # Leverage Shares 2X XXX ETF
    re.compile(r"\bLeverage\s+Shares\s+(?:2x|3x|2X|3X)\s+([A-Z]{2,6})\s+(?:Long|Short|ETF)?", re.IGNORECASE),
    # ProShares Ultra XXX
    re.compile(r"\bProShares\s+Ultra(?:Short|Pro)?\s+([A-Z]{2,6})\b", re.IGNORECASE),
    # Generic: "2X XXX ETF" or "3X XXX ETF" anywhere in the name
    re.compile(r"(?:^|\s)(?:2X|3X)\s+(?:Long|Short|Inverse|Bull|Bear)\s+([A-Z]{2,6})\b", re.IGNORECASE),
    # Generic: "XXX 2X" at start
    re.compile(r"^([A-Z]{2,6})\s+(?:2X|3X)\s", re.IGNORECASE),
]

# Stop words that can match as fake tickers
STOP_TICKERS = {
    "ETF", "ETN", "FUND", "DAILY", "TARGET", "LONG", "SHORT", "INVERSE",
    "BULL", "BEAR", "ULTRA", "PRO", "TREX", "REX", "X", "XX", "XXX",
    "OF", "THE", "AND", "OR", "FOR", "AS", "BY", "IN", "ON", "AT",
    "INC", "CORP", "LTD", "LLC", "ETP", "DAY", "WEEK", "USD",
    "AAA", "BBB", "CCC", "DDD",
}


def extract_underlier(fund_name: str) -> str | None:
    if not fund_name or not isinstance(fund_name, str):
        return None
    for pat in PATTERNS:
        m = pat.search(fund_name)
        if m:
            cand = m.group(1).upper().strip()
            if cand and cand not in STOP_TICKERS and len(cand) >= 2:
                return cand
    return None


def build() -> pd.DataFrame:
    """Scan fund_extractions for L&I patterns. Return one row per
    (underlier, issuer/registrant, filing_date, status) — enough to make
    the whitespace exclusion robust."""
    conn = sqlite3.connect(str(DB))
    try:
        df = pd.read_sql_query(
            """
            SELECT fe.series_name, fe.class_contract_name, fe.class_symbol,
                   f.filing_date, f.form, f.registrant
            FROM fund_extractions fe
            LEFT JOIN filings f ON f.id = fe.filing_id
            """,
            conn,
        )
    finally:
        conn.close()

    log.info("Scanning %d fund_extractions for L&I patterns", len(df))

    df["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce")

    # Try extraction from series_name first, then class_contract_name
    df["underlier"] = df["series_name"].apply(extract_underlier)
    fallback = df["underlier"].isna()
    df.loc[fallback, "underlier"] = df.loc[fallback, "class_contract_name"].apply(extract_underlier)

    matched = df.dropna(subset=["underlier"]).copy()
    matched["underlier"] = matched["underlier"].str.upper()

    # Aggregate per underlier — last filing date, count, distinct issuers
    today = pd.Timestamp.today()
    summary = matched.groupby("underlier").agg(
        n_filings_total=("filing_date", "count"),
        last_filing_date=("filing_date", "max"),
        first_filing_date=("filing_date", "min"),
        n_distinct_registrants=("registrant", "nunique"),
        sample_fund_name=("series_name", "first"),
        sample_registrant=("registrant", "first"),
    )
    summary["days_since_last_filing"] = (today - summary["last_filing_date"]).dt.days

    log.info("Distinct underliers extracted from fund-name regex: %d", len(summary))
    return summary


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    df = build()
    df.to_parquet(OUT, compression="snappy")
    log.info("Wrote %s (%d rows)", OUT, len(df))

    print(f"\nDistinct underliers found in L&I filings: {len(df)}")
    print("\nSample (most recent filings):")
    recent = df.sort_values("last_filing_date", ascending=False).head(20)
    for u, r in recent.iterrows():
        print(f"  {u:<8} last_filing={r['last_filing_date'].date() if pd.notna(r['last_filing_date']) else '—':<12} "
              f"n={int(r['n_filings_total']):>3} issuers={int(r['n_distinct_registrants']):>2} "
              f"sample: {(r['sample_fund_name'] or '')[:50]}")

    # Specific check
    print("\nAXTI in extracted set?", "AXTI" in df.index)
    print("FIG in extracted set?", "FIG" in df.index)
    print("KLAR in extracted set?", "KLAR" in df.index)


if __name__ == "__main__":
    main()
