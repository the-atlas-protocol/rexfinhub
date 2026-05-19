"""Phase 3 (ADR 0007) — Stage 1: add CapM-derived columns to rex_products.

Idempotent. Inspects the live schema and only issues ALTER TABLE for columns
that don't already exist. Safe to run on local + VPS + a future Render replica.

Usage:
    python scripts/migrate_capm_to_rex_schema.py
    python scripts/migrate_capm_to_rex_schema.py --dry-run
    python scripts/migrate_capm_to_rex_schema.py --db /path/to/etp_tracker.db

Stage 2 (data backfill) is in scripts/migrate_capm_data_to_rex.py.
Stage 5 (drop capm_products) is in scripts/drop_capm_products.py — DO NOT RUN
until at least 7 days after stage 3 dual-write is in production.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"

# Per ADR 0007. Order matters only for grep-ability — SQLite has no column-position
# semantics that affect anything observable.
NEW_COLUMNS: list[tuple[str, str]] = [
    ("bb_ticker",          "VARCHAR(30)"),
    ("inception_date",     "DATE"),
    ("issuer",             "VARCHAR(200)"),
    ("fixed_fee",          "VARCHAR(20)"),
    ("variable_fee",       "VARCHAR(50)"),
    ("cut_off",            "VARCHAR(20)"),
    ("custodian",          "VARCHAR(100)"),
    ("our_category",       "VARCHAR(50)"),
    ("product_type",       "VARCHAR(50)"),
    ("category",           "VARCHAR(50)"),
    ("sub_category",       "VARCHAR(50)"),
    ("leverage",           "VARCHAR(10)"),
    ("underlying_ticker",  "VARCHAR(50)"),
    ("underlying_name",    "VARCHAR(300)"),
    ("expense_ratio",      "FLOAT"),
    ("bmo_suite",          "VARCHAR(50)"),
]


def existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB),
                    help=f"Path to SQLite DB (default: {DEFAULT_DB})")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the ALTER TABLE statements without executing")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        present = existing_columns(conn, "rex_products")
        if not present:
            print(f"ERROR: rex_products table not found in {db_path}", file=sys.stderr)
            return 1

        to_add = [(col, ty) for col, ty in NEW_COLUMNS if col not in present]
        already = [col for col, _ in NEW_COLUMNS if col in present]

        print(f"DB: {db_path}")
        print(f"rex_products existing column count: {len(present)}")
        print(f"  already-present CapM columns: {len(already)} {already if already else ''}")
        print(f"  to add:                       {len(to_add)}")

        if not to_add:
            print("Nothing to do — schema already migrated.")
            return 0

        for col, ty in to_add:
            stmt = f"ALTER TABLE rex_products ADD COLUMN {col} {ty}"
            if args.dry_run:
                print(f"  DRY-RUN  {stmt}")
            else:
                print(f"  EXEC     {stmt}")
                conn.execute(stmt)

        if not args.dry_run:
            conn.commit()
            after = existing_columns(conn, "rex_products")
            print(f"Committed. rex_products now has {len(after)} columns.")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
