"""Phase 3.4 — Apply issuer_brand_overrides.csv to mkt_master_data.

Reads config/rules/issuer_brand_overrides.csv and writes issuer_display for
each ticker row. Idempotent — safe to re-run; skips rows already matching.

Design intent:
    sync_market_data (BBG re-import) resets issuer_display to NULL.
    This script must be run AFTER every sync_market_data call to restore
    brand attributions. Suitable for inclusion in run_daily.py or as a
    post-sync hook.

Usage:
    python scripts/apply_issuer_brands.py [--dry-run]

Reference style: scripts/apply_fund_master.py
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
OVERRIDES_CSV = PROJECT_ROOT / "config" / "rules" / "issuer_brand_overrides.csv"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Apply issuer_brand_overrides.csv to mkt_master_data."
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview changes without writing to the DB.")
    args = ap.parse_args()

    if not OVERRIDES_CSV.exists():
        print(f"ERROR: {OVERRIDES_CSV} not found. "
              "Run scripts/derive_issuer_brands.py first.")
        return 1
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        return 1

    rows = list(csv.DictReader(OVERRIDES_CSV.open(encoding="utf-8")))
    print(f"Loaded {len(rows):,} rows from {OVERRIDES_CSV}")

    if args.dry_run:
        print("[DRY-RUN] Would update mkt_master_data with these brands:")
        for r in rows[:5]:
            print(f"  {r['ticker']:15s} -> {r['issuer_display']}")
        print(f"  ... and {len(rows)-5:,} more")
        return 0

    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()

    updated = 0
    noop = 0
    not_found = 0

    for r in rows:
        ticker = (r.get("ticker") or "").strip()
        brand = (r.get("issuer_display") or "").strip()
        if not ticker or not brand:
            continue

        # Check current value first to distinguish update vs. no-op
        cur.execute(
            "SELECT issuer_display FROM mkt_master_data WHERE ticker = ?",
            (ticker,),
        )
        existing = cur.fetchone()

        if existing is None:
            not_found += 1
            continue

        current_val = existing[0]
        if current_val == brand:
            noop += 1
            continue

        cur.execute(
            "UPDATE mkt_master_data SET issuer_display = ? WHERE ticker = ?",
            (brand, ticker),
        )
        if cur.rowcount > 0:
            updated += 1
        else:
            not_found += 1

    con.commit()
    con.close()

    print(f"Applied:   {updated:,} rows updated in mkt_master_data")
    print(f"No-ops:    {noop:,} rows already had correct issuer_display")
    print(f"Not found: {not_found:,} tickers in CSV but not in DB "
          "(possibly liquidated or delisted)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
