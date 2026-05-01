"""Phase 2 — Add new classification columns to mkt_master_data.

Additive migration only — no existing columns are modified or dropped.
Idempotent: safe to re-run (uses ADD COLUMN IF NOT EXISTS pattern via
information_schema check).

After this runs, the new columns exist as NULL on all rows. Phase 3
(`apply_fund_master.py`) will populate them from `config/rules/fund_master.csv`.

Columns added match the locked taxonomy in
docs/CLASSIFICATION_SYSTEM_PLAN.md.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"

# (column_name, sql_type) pairs to add to mkt_master_data
NEW_COLUMNS = [
    # Three-axis taxonomy
    ("asset_class",            "VARCHAR(30)"),
    ("primary_strategy",       "VARCHAR(40)"),
    ("sub_strategy",           "VARCHAR(80)"),

    # Underlier identification
    ("concentration",          "VARCHAR(10)"),     # single | basket
    ("underlier_name",         "VARCHAR(60)"),     # NVDA | SPX | BTC | gold | basket-id
    ("underlier_is_wrapper",   "BOOLEAN"),
    ("root_underlier_name",    "VARCHAR(60)"),

    # Wrapper / packaging
    ("wrapper_type",           "VARCHAR(20)"),     # standalone | fund_of_funds | laddered | synthetic | feeder

    # Mechanism
    ("mechanism",              "VARCHAR(20)"),     # physical | swap | futures | options | structured_note | synthetic

    # Quantitative
    ("leverage_ratio",         "FLOAT"),           # 1.0, 1.25, 2.0, 3.0
    ("direction",              "VARCHAR(10)"),     # long | short | neutral
    ("reset_period",           "VARCHAR(15)"),     # daily | weekly | monthly | quarterly | none
    ("distribution_freq",      "VARCHAR(15)"),     # daily | weekly | monthly | quarterly | annual | none
    ("outcome_period_months",  "INTEGER"),         # for Defined Outcome
    ("cap_pct",                "FLOAT"),
    ("buffer_pct",             "FLOAT"),
    ("accelerator_multiplier", "FLOAT"),
    ("barrier_pct",            "FLOAT"),

    # Asset characteristics
    ("region",                 "VARCHAR(30)"),
    ("duration_bucket",        "VARCHAR(20)"),     # ultra_short | short | intermediate | long | ultra_long
    ("credit_quality",         "VARCHAR(20)"),

    # Tax & regulatory
    ("tax_structure",          "VARCHAR(20)"),     # 40_act | mlp_k1 | grantor_trust | partnership | uit
    ("qualified_dividends",    "BOOLEAN"),
]


def existing_columns(con: sqlite3.Connection, table: str) -> set[str]:
    cur = con.cursor()
    cur.execute(f"PRAGMA table_info({table})")
    return {r[1] for r in cur.fetchall()}


def main():
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}")
        return 1

    con = sqlite3.connect(str(DB_PATH))
    print(f"Migrating mkt_master_data in {DB_PATH}")
    print()

    existing = existing_columns(con, "mkt_master_data")
    print(f"Existing columns: {len(existing)}")

    added = 0
    skipped = 0
    cur = con.cursor()
    for col_name, col_type in NEW_COLUMNS:
        if col_name in existing:
            skipped += 1
            print(f"  skip   {col_name:30s} (already exists)")
            continue
        try:
            cur.execute(f"ALTER TABLE mkt_master_data ADD COLUMN {col_name} {col_type}")
            added += 1
            print(f"  added  {col_name:30s} {col_type}")
        except sqlite3.Error as e:
            print(f"  ERROR  {col_name}: {e}")

    con.commit()
    con.close()

    print()
    print(f"Migration complete: {added} added, {skipped} already present")
    return 0


if __name__ == "__main__":
    sys.exit(main())
