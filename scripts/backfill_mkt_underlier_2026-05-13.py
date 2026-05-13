"""Backfill mkt_master_data.map_li_underlier / map_cc_underlier from fund_name.

Symptom (Ryu 2026-05-13): /operations/pipeline/underlier/XOVR shows no
competitors because XOVL (Defiance Daily Target 2X Long XOVR ETF, ACTV,
$4M AUM) has map_li_underlier=NULL. Same gap on every Covered-Call fund —
JEPQ/QQQI/QYLD all have map_cc_underlier=NULL.

The classifier sweep populates these columns via attributes_LI.csv /
attributes_CC.csv rules, but the rules don't cover ETP-as-underlier
patterns or new patterns from recent filings. This script regex-extracts
the underlier from fund_name, then VALIDATES the candidate against the
known-ticker universe (must already exist in mkt_master_data) or a small
index/company map. Rows that don't pass validation are skipped — bias is
toward false negatives over false positives.

Safety: --dry-run / --apply gating, "I AGREE" prompt, DB backup before
write, audit row per change in capm_audit_log.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "etp_tracker.db"
BACKUP_DIR = ROOT / "data" / "backups"

# Company name → ticker (case-insensitive match on fund_name substring).
COMPANY_TO_TICKER = {
    "ALPHABET": "GOOGL", "GOOGLE": "GOOGL",
    "TESLA": "TSLA",
    "AMAZON": "AMZN",
    "MICROSOFT": "MSFT",
    "META": "META", "FACEBOOK": "META",
    "NVIDIA": "NVDA",
    "APPLE": "AAPL",
    "BERKSHIRE": "BRK.B",
    "BROADCOM": "AVGO",
    "MICROSTRATEGY": "MSTR",
    "PALANTIR": "PLTR",
    "COINBASE": "COIN",
    "ROBINHOOD": "HOOD",
    "ROBLOX": "RBLX",
    "RIVIAN": "RIVN",
    "LUCID": "LCID",
    "ETHEREUM": "XETUSD", "ETHER": "XETUSD",
    "BITCOIN": "XBTUSD",
    "SOLANA": "XSOUSD",
    "DOGECOIN": "XDGUSD",
    "DISCORD": "DCRD",
    "MAGNIFICENT SEVEN": "MAGS",
    "MAGNIFICENT 7": "MAGS",
}

# Index-name → canonical ticker (longest first to avoid prefix collisions).
INDEX_TO_TICKER = {
    "NASDAQ-100": "QQQ", "NASDAQ 100": "QQQ", "NASDAQ100": "QQQ",
    "NASDAQ COMPOSITE": "QQQ", "NASDAQ": "QQQ", "QQQ": "QQQ",
    "S&P 500": "SPY", "S AND P 500": "SPY", "SP500": "SPY",
    "SPX": "SPY", "SPY": "SPY",
    "DOW JONES": "DIA", "DOW 30": "DIA", "DJIA": "DIA", "DIA": "DIA",
    "RUSSELL 2000": "IWM", "RUSSELL2000": "IWM", "IWM": "IWM",
    "FANG+": "FANG+", "FANG PLUS": "FANG+",
    "MSCI EMERGING": "EEM", "EMERGING MARKETS": "EEM",
    "MSCI EAFE": "EFA",
    "MSCI JAPAN": "EWJ", "JAPAN": "EWJ",
    "MSCI INDIA": "INDA", "INDIA": "INDA",
    "MSCI CHINA": "FXI", "CHINA": "FXI",
    "MSCI KOREA": "EWY", "KOREA": "EWY",
    "MSCI TAIWAN": "EWT", "TAIWAN": "EWT",
    "MSCI MEXICO": "EWW", "MEXICO": "EWW",
    "MSCI BRAZIL": "EWZ", "BRAZIL": "EWZ",
    "GOLD MINERS": "GDX",
    "SILVER MINERS": "SIL",
    "BIOTECHNOLOGY": "XBI", "BIOTECH": "XBI",
    "SEMICONDUCTOR": "SOXX", "SEMI": "SOXX",
    "DRONE": "UAV",
    "CYBERSECURITY": "HACK",
    "CRYPTO": "BITQ",
}

# Tokens to ignore from the leverage-regex capture (not real tickers).
_STOPWORDS = {
    "AND", "THE", "FOR", "DAILY", "TARGET", "DAY", "ETF", "FUND", "TRUST",
    "STRATEGY", "REX", "MICROSECTORS", "INCOMEMAX", "SUITE", "PORTFOLIO",
    "INCOME", "PREMIUM", "GROWTH", "YIELD", "SECTOR", "EQUITY", "BOND",
    "INDEX", "BUFFER", "DEFINED", "OUTCOME", "MONTHLY", "QUARTERLY",
    "WEEKLY", "BULL", "BEAR", "LONG", "SHORT", "INVERSE", "DIRECT",
    "ULTRA", "ULTRASHORT", "ULTRAPRO", "DIREXION", "PROSHARES",
    "GRANITESHARES", "GLOBAL", "NEOS", "YIELDMAX", "JPMORGAN", "OPTION",
    "CALL", "PUT", "COVERED", "TRADR", "DEFIANCE", "TIDAL", "ETN",
    "MAGNIFICENT", "INNOVATION", "PURE", "ENERGY", "FINANCIAL", "ACTIVE",
    "MSCI", "LEVERAGED", "ALL", "BASE", "FREE", "RETURN", "CAP", "CORE",
    "PLUS", "MAX", "BLAST", "EDGE",
}

_LEVERAGE_RE = re.compile(
    r"\b(?:LONG|SHORT|INVERSE|BULL|BEAR|ULTRA|ULTRASHORT|ULTRAPRO)\s+([A-Z]{1,12})\b",
    re.IGNORECASE,
)
_OPTION_INCOME_RE = re.compile(
    r"\b([A-Z]{1,5})\s+(?:OPTION\s+INCOME|COVERED\s+CALL|PREMIUM\s+INCOME)",
    re.IGNORECASE,
)


def _is_known_ticker(cand: str, known_tickers: set[str]) -> bool:
    """Strict validation: must already be a ticker in mkt_master_data
    OR appear as a value in our index/company maps OR be a known crypto."""
    if cand in known_tickers:
        return True
    if cand in INDEX_TO_TICKER.values():
        return True
    if cand in COMPANY_TO_TICKER.values():
        return True
    if cand in ("XBTUSD", "XETUSD", "XSOUSD", "XDGUSD"):
        return True
    return False


def _try_index_match(name_upper: str) -> str | None:
    """Word-boundary match against the index name map (longest first).
    Word boundary prevents 'DIA' matching inside 'INDIA' etc."""
    for idx_name in sorted(INDEX_TO_TICKER.keys(), key=len, reverse=True):
        # \b for word-boundary, but `&`/`+` aren't word chars — fall back to
        # plain substring for those few keys.
        pat = re.compile(r"\b" + re.escape(idx_name) + r"\b") if idx_name.isalnum() or " " in idx_name else None
        if pat:
            if pat.search(name_upper):
                return INDEX_TO_TICKER[idx_name]
        else:
            if idx_name in name_upper:
                return INDEX_TO_TICKER[idx_name]
    return None


def derive_li_underlier(name: str, known_tickers: set[str]) -> str | None:
    if not name:
        return None
    n = name.upper()
    # Leveraged on an index? Most reliable signal.
    m = _LEVERAGE_RE.search(n)
    if m:
        cand = m.group(1).upper()
        if cand not in _STOPWORDS:
            cand = COMPANY_TO_TICKER.get(cand, cand)
            if _is_known_ticker(cand, known_tickers):
                return cand
    # Index-name match in fund_name.
    idx = _try_index_match(n)
    if idx:
        return idx
    # Company-name fallback.
    for company, ticker in COMPANY_TO_TICKER.items():
        if company in n:
            return ticker
    return None


def derive_cc_underlier(name: str, known_tickers: set[str]) -> str | None:
    if not name:
        return None
    n = name.upper()
    # Index-name match wins on CC (most are NASDAQ/S&P/DOW based).
    idx = _try_index_match(n)
    if idx:
        return idx
    # "<TICKER> OPTION INCOME / COVERED CALL / PREMIUM INCOME"
    m = _OPTION_INCOME_RE.search(n)
    if m:
        cand = m.group(1).upper()
        if cand not in _STOPWORDS:
            cand = COMPANY_TO_TICKER.get(cand, cand)
            if _is_known_ticker(cand, known_tickers):
                return cand
    # Company-name fallback.
    for company, ticker in COMPANY_TO_TICKER.items():
        if company in n:
            return ticker
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if args.apply:
        print("Type 'I AGREE' (exactly) to proceed, anything else aborts.")
        print("=" * 72)
        if sys.stdin.readline().strip() != "I AGREE":
            print("Aborted.")
            return 2

    c = sqlite3.connect(str(DB_PATH))

    # Build the known-ticker set once — strip ` US` suffix.
    known: set[str] = set()
    for r in c.execute("SELECT DISTINCT ticker FROM mkt_master_data WHERE ticker IS NOT NULL"):
        if r[0]:
            known.add(str(r[0]).strip().upper().replace(" US", "").replace(" CURNCY", ""))

    # L&I candidates
    li_rows = c.execute("""
        SELECT ticker, fund_name FROM mkt_master_data
        WHERE (primary_strategy = 'Leveraged & Inverse'
               OR fund_name LIKE '%2X %' OR fund_name LIKE '%3X %' OR fund_name LIKE '%ULTRA %')
          AND market_status IN ('ACTV', 'PEND')
          AND (map_li_underlier IS NULL OR map_li_underlier = '')
    """).fetchall()
    li_proposals = []
    for ticker, name in li_rows:
        derived = derive_li_underlier(name, known)
        if derived:
            li_proposals.append((ticker, name, derived))

    # CC / Income candidates
    cc_rows = c.execute("""
        SELECT ticker, fund_name FROM mkt_master_data
        WHERE (primary_strategy = 'Income'
               OR fund_name LIKE '%COVERED CALL%'
               OR fund_name LIKE '%PREMIUM INCOME%'
               OR fund_name LIKE '%OPTION INCOME%')
          AND market_status IN ('ACTV', 'PEND')
          AND (map_cc_underlier IS NULL OR map_cc_underlier = '')
    """).fetchall()
    cc_proposals = []
    for ticker, name in cc_rows:
        derived = derive_cc_underlier(name, known)
        if derived:
            cc_proposals.append((ticker, name, derived))

    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"DB: {DB_PATH}")
    print(f"Known tickers: {len(known)}")
    print("=" * 72)
    print(f"L&I candidates with NULL map_li_underlier: {len(li_rows)}")
    print(f"  proposals: {len(li_proposals)} ({100*len(li_proposals)/max(len(li_rows),1):.1f}% coverage)")
    print(f"CC candidates with NULL map_cc_underlier: {len(cc_rows)}")
    print(f"  proposals: {len(cc_proposals)} ({100*len(cc_proposals)/max(len(cc_rows),1):.1f}% coverage)")
    print()
    print("L&I sample (first 12):")
    for p in li_proposals[:12]:
        print(f"  {str(p[0]):10s} -> {p[2]:8s}  {p[1][:55]}")
    print()
    print("CC sample (first 12):")
    for p in cc_proposals[:12]:
        print(f"  {str(p[0]):10s} -> {p[2]:8s}  {p[1][:55]}")

    if args.apply and (li_proposals or cc_proposals):
        backup = BACKUP_DIR / f"etp_tracker.db.pre-mkt-underlier-{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}.bak"
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        print()
        print(f"Backing up DB to {backup} ...")
        b = sqlite3.connect(str(backup))
        with b:
            c.backup(b)
        b.close()
        print("  Backup OK.")
        ts = datetime.utcnow()
        for ticker, name, derived in li_proposals:
            c.execute("UPDATE mkt_master_data SET map_li_underlier = ?, updated_at = ? WHERE ticker = ?",
                      (derived, ts, ticker))
            c.execute(
                "INSERT INTO capm_audit_log (action, table_name, row_id, field_name, old_value, new_value, row_label, changed_by, changed_at) "
                "VALUES ('UPDATE','mkt_master_data',0,'map_li_underlier',NULL,?,?,?,?)",
                (derived, str(ticker), "mkt_underlier_backfill_2026-05-13", ts),
            )
        for ticker, name, derived in cc_proposals:
            c.execute("UPDATE mkt_master_data SET map_cc_underlier = ?, updated_at = ? WHERE ticker = ?",
                      (derived, ts, ticker))
            c.execute(
                "INSERT INTO capm_audit_log (action, table_name, row_id, field_name, old_value, new_value, row_label, changed_by, changed_at) "
                "VALUES ('UPDATE','mkt_master_data',0,'map_cc_underlier',NULL,?,?,?,?)",
                (derived, str(ticker), "mkt_underlier_backfill_2026-05-13", ts),
            )
        c.commit()
        print(f"Applied {len(li_proposals)} L&I + {len(cc_proposals)} CC updates.")

    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
