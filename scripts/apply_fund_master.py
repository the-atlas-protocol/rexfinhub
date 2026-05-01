"""Phase 3.3 — Apply fund_master.csv to mkt_master_data new columns.

Reads config/rules/fund_master.csv and writes the new taxonomy columns
(asset_class, primary_strategy, sub_strategy + 20 attributes) to
mkt_master_data. Idempotent — safe to re-run after editing the CSV.

Manual edits to fund_master.csv take precedence over auto-classifier
output (per the Layer 1 priority in CLASSIFICATION_SYSTEM_PLAN.md).
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
INPUT_CSV = PROJECT_ROOT / "config" / "rules" / "fund_master.csv"

# Columns in fund_master.csv that map directly to mkt_master_data columns
TARGET_COLUMNS = [
    "asset_class", "primary_strategy", "sub_strategy",
    "concentration", "underlier_name", "underlier_is_wrapper",
    "root_underlier_name", "wrapper_type", "mechanism",
    "leverage_ratio", "direction", "reset_period",
    "distribution_freq", "outcome_period_months",
    "cap_pct", "buffer_pct", "accelerator_multiplier", "barrier_pct",
    "region", "duration_bucket", "credit_quality",
    "tax_structure", "qualified_dividends",
]


def _coerce_value(col: str, raw: str):
    """Type-coerce CSV string values for SQLite columns."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if col in ("leverage_ratio", "cap_pct", "buffer_pct",
               "accelerator_multiplier", "barrier_pct"):
        try:
            return float(raw)
        except ValueError:
            return None
    if col == "outcome_period_months":
        try:
            return int(raw)
        except ValueError:
            return None
    if col in ("underlier_is_wrapper", "qualified_dividends"):
        return 1 if raw.lower() in ("true", "yes", "1", "t") else 0
    return raw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview changes without writing.")
    args = ap.parse_args()

    if not INPUT_CSV.exists():
        print(f"ERROR: {INPUT_CSV} not found. Run build_fund_master_seed.py first.")
        return 1
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        return 1

    rows = list(csv.DictReader(INPUT_CSV.open(encoding="utf-8")))
    print(f"Loaded {len(rows):,} rows from {INPUT_CSV}")

    if args.dry_run:
        print("[DRY-RUN] Would update mkt_master_data with these classifications:")
        for r in rows[:5]:
            print(f"  {r['ticker']:10s} → asset={r['asset_class']:12s} primary={r['primary_strategy']:18s} sub={r['sub_strategy'][:35]}")
        print(f"  ... and {len(rows)-5:,} more")
        return 0

    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()

    set_clauses = ", ".join(f"{c} = ?" for c in TARGET_COLUMNS)
    sql = f"UPDATE mkt_master_data SET {set_clauses} WHERE ticker = ?"

    updated = 0
    not_found = 0
    for r in rows:
        ticker = (r.get("ticker") or "").strip()
        if not ticker:
            continue
        values = [_coerce_value(c, r.get(c, "")) for c in TARGET_COLUMNS]
        values.append(ticker)
        cur.execute(sql, values)
        if cur.rowcount > 0:
            updated += 1
        else:
            not_found += 1

    con.commit()
    con.close()

    print(f"Applied: {updated:,} rows updated in mkt_master_data")
    print(f"Not found: {not_found:,} tickers (in CSV but not in mkt_master_data — possibly liquidated)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
