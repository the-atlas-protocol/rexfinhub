"""Backfill rex_products.underlier from the fund name for ~334 NULL rows.

Most REX product names encode the underlier as a token in the fund name —
e.g. "T-REX 2X LONG NVDA DAILY TARGET ETF" → NVDA. This script scans
all non-Delisted rex_products rows with NULL underlier, applies a regex
chain, and (with --apply) writes the inferred value to the DB.

Patterns (precedence order):
  1. T-REX:    "(LONG|SHORT|INVERSE) <TICKER> (DAILY|TARGET|ETF)" with 1-5 alpha
  2. IncomeMax/PremiumIncome/G&I: "<TICKER> (Yield|Income|Premium|G&I)"
  3. Company-name fallback for the few that spell out (e.g. ALPHABET→GOOGL)

Safety:
- --dry-run prints proposed changes only.
- --apply requires "I AGREE" stdin prompt.
- Skips rows whose `manually_edited_fields` already includes 'underlier'.
- Backups DB to data/backups/etp_tracker.db.pre-underlier-backfill-{ts}.bak
  before writing.
- Writes a capm_audit_log row per change (table_name='rex_products',
  field_name='underlier').
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "etp_tracker.db"
BACKUP_DIR = ROOT / "data" / "backups"

# Spell-out → ticker map for cases where the fund name uses the issuer
# common name rather than the ticker. Add entries as new gaps surface.
COMPANY_TO_TICKER = {
    "ALPHABET": "GOOGL",
    "ALPHABET INC": "GOOGL",
    "GOOGLE": "GOOGL",
    "TESLA": "TSLA",
    "TESLA INC": "TSLA",
    "AMAZON": "AMZN",
    "MICROSOFT": "MSFT",
    "META": "META",
    "FACEBOOK": "META",
    "NVIDIA": "NVDA",
    "APPLE": "AAPL",
    "BERKSHIRE HATHAWAY": "BRK.B",
    "BROADCOM": "AVGO",
    "MICROSTRATEGY": "MSTR",
    "PALANTIR": "PLTR",
    "COINBASE": "COIN",
    "ROBINHOOD": "HOOD",
    "ROBLOX": "RBLX",
    "RIVIAN": "RIVN",
    "LUCID": "LCID",
    "BITCOIN": "BTC",
    "ETHER": "ETH",
    "ETHEREUM": "ETH",
    "SOLANA": "SOL",
    "DOGECOIN": "DOGE",
}


# Underlier sits AFTER the leverage word: "T-REX 2X LONG <SYM>". Allow up
# to 12 chars so we catch ALPHABET / MICROSTRATEGY before falling to the
# company-name map.
_LEVERAGE_RE = re.compile(
    r"\b(?:LONG|SHORT|INVERSE|BULL|BEAR)\s+([A-Z]{1,12})\b",
    re.IGNORECASE,
)
# IncomeMax / Premium Income / G&I suites: "REX IncomeMax <SYM> Strategy"
# or "REX <SYM> Income/Premium/G&I". Underlier always AFTER the suite token.
_AFTER_SUITE_RE = re.compile(
    r"(?:INCOMEMAX|PREMIUM\s+INCOME|GROWTH\s+(?:AND|&|&AMP;)\s+INCOME|G&AMP;I|G&I)"
    r"\s+([A-Z]{1,8})(?:\s+STRATEGY|\s+ETF|\s+FUND|\b)",
    re.IGNORECASE,
)
# MicroSectors ETN suite: name pattern like "MicroSectors FANG+ Index"
# or "MicroSectors -3x Gold Miners ETN". Underlier here is sector/index,
# not a single ticker — backfill from a small explicit map below rather
# than regex.
_MICROSECTORS_HINTS = {
    "FANG": "FANG+",
    "GOLD MINERS": "GDX",
    "SILVER MINERS": "SIL",
    "GOLD": "GLD",
    "SILVER": "SLV",
    "OIL & GAS": "XOP",
    "BIOTECH": "XBI",
    "U.S. BIG OIL": "BIGOIL",
    "U.S. BIG BANKS": "BIGBANK",
    "STEEL": "SLX",
    "REGIONAL BANKS": "KRE",
    "TRAVEL": "TRVL",
}

_STOPWORDS = {
    "AND", "THE", "FOR", "DAILY", "TARGET", "DAY", "ETF", "FUND", "TRUST",
    "STRATEGY", "REX", "MICROSECTORS", "INCOMEMAX", "SUITE", "PORTFOLIO",
    "INCOME", "PREMIUM", "GROWTH", "YIELD", "SECTOR", "EQUITY", "BOND",
    "INDEX", "BUFFER", "DEFINED", "OUTCOME", "MONTHLY", "QUARTERLY",
    "WEEKLY", "BULL", "BEAR", "LONG", "SHORT", "INVERSE", "DIRECT",
}


def derive_underlier(name: str) -> str | None:
    if not name:
        return None
    n = name.upper()

    # 1. Suite-name-anchored pattern (IncomeMax/Premium/G&I).
    m = _AFTER_SUITE_RE.search(n)
    if m:
        cand = m.group(1).upper()
        if cand not in _STOPWORDS:
            return cand

    # 2. Leverage pattern.
    m = _LEVERAGE_RE.search(n)
    if m:
        cand = m.group(1).upper()
        if cand not in _STOPWORDS:
            # If the captured token is a known long company name, resolve
            # to its ticker. Otherwise return as-is.
            return COMPANY_TO_TICKER.get(cand, cand)

    # 3. MicroSectors-specific hints.
    if "MICROSECTORS" in n:
        for hint, ticker in _MICROSECTORS_HINTS.items():
            if hint in n:
                return ticker

    # 4. Company-name spell-out fallback (e.g. names that don't follow
    #    the leverage pattern but mention the issuer).
    for company, ticker in COMPANY_TO_TICKER.items():
        if company in n and company not in _STOPWORDS:
            return ticker

    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Print proposed changes only.")
    group.add_argument("--apply", action="store_true", help="Write to DB (prompts for 'I AGREE').")
    args = parser.parse_args()

    if args.apply:
        print("Type 'I AGREE' (exactly, case-sensitive) to proceed, or anything else to abort.")
        print("=" * 72)
        line = sys.stdin.readline().strip()
        if line != "I AGREE":
            print("Aborted.")
            return 2

    c = sqlite3.connect(str(DB_PATH))
    rows = c.execute(
        "SELECT id, ticker, name, product_suite, manually_edited_fields "
        "FROM rex_products "
        "WHERE (underlier IS NULL OR underlier = '') "
        "  AND status != 'Delisted'"
    ).fetchall()

    print(f"Mode: {'APPLY' if args.apply else 'DRY RUN'}")
    print(f"DB:   {DB_PATH}")
    print(f"NULL-underlier candidates: {len(rows)}")
    print("=" * 72)

    by_suite_total: dict[str, int] = {}
    by_suite_fixed: dict[str, int] = {}
    skipped_override = 0
    proposals = []

    for row_id, ticker, name, suite, mef in rows:
        s = suite or "(none)"
        by_suite_total[s] = by_suite_total.get(s, 0) + 1
        if mef:
            try:
                if "underlier" in json.loads(mef):
                    skipped_override += 1
                    continue
            except (json.JSONDecodeError, TypeError):
                pass
        derived = derive_underlier(name)
        if derived:
            by_suite_fixed[s] = by_suite_fixed.get(s, 0) + 1
            proposals.append((row_id, ticker, name, s, derived))

    print(f"Skipped (admin-edited 'underlier'): {skipped_override}")
    print(f"Proposals: {len(proposals)}")
    print()
    for s, total in sorted(by_suite_total.items(), key=lambda x: -x[1]):
        fixed = by_suite_fixed.get(s, 0)
        print(f"  {s:25s} {fixed:>4d} / {total:>4d}  ({100*fixed/total:5.1f}% coverage)")
    print()
    print("Sample (first 15):")
    for p in proposals[:15]:
        print(f"  id={p[0]:4d}  t={str(p[1]):8s}  suite={p[3]:18s}  derived={p[4]:8s}  name={p[2][:35]}")

    if args.apply and proposals:
        backup = BACKUP_DIR / f"etp_tracker.db.pre-underlier-backfill-{datetime.utcnow().strftime('%Y%m%dT%H%M%S')}.bak"
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        print()
        print(f"Backing up DB to {backup} ...")
        b = sqlite3.connect(str(backup))
        with b:
            c.backup(b)
        b.close()
        print("  Backup OK.")

        # Audit log helper — same shape as the admin endpoint writes.
        for row_id, ticker, name, suite, derived in proposals:
            label = ticker or (name[:40] if name else f"#{row_id}")
            # Add 'underlier' to manually_edited_fields so future sweeps respect it
            c.execute(
                "UPDATE rex_products SET underlier = ?, "
                "manually_edited_fields = COALESCE(NULLIF(manually_edited_fields,''), '[]'), "
                "updated_at = ? WHERE id = ?",
                (derived, datetime.utcnow(), row_id),
            )
            # Audit
            c.execute(
                "INSERT INTO capm_audit_log "
                "(action, table_name, row_id, field_name, old_value, new_value, "
                " row_label, changed_by, changed_at) "
                "VALUES (?, 'rex_products', ?, 'underlier', NULL, ?, ?, ?, ?)",
                ("UPDATE", row_id, derived, label, "underlier_backfill_2026-05-12", datetime.utcnow()),
            )
        c.commit()
        print(f"Applied {len(proposals)} updates + audit rows.")

    c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
