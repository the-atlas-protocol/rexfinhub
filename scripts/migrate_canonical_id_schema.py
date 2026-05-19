"""Phase 4 (ADR 0006) — Stage 1: create canonical-id tables + add canonical_id
column to rex_products.

Idempotent: inspects schema and only creates what's missing. Safe to re-run.

Pure additive change — adds 4 new tables + 1 new nullable column. No existing
data touched, no existing reads/writes affected. Phase 4 Stages 2-5 (backfill,
OpenFIGI resolution, fund_underlier population, drop freeform columns) follow
in separate scripts.

Usage:
    python scripts/migrate_canonical_id_schema.py
    python scripts/migrate_canonical_id_schema.py --dry-run
    python scripts/migrate_canonical_id_schema.py --db /path/to/etp_tracker.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"


# CREATE statements — idempotent via IF NOT EXISTS.
# UUID is stored as TEXT in SQLite (no native UUID type). All canonical_id
# values are 36-char strings ("xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx").
DDL_STATEMENTS = [
    # product_master: one row per logical product. UUID PK never changes.
    """CREATE TABLE IF NOT EXISTS product_master (
        canonical_id    TEXT PRIMARY KEY,
        created_at      TIMESTAMP NOT NULL,
        fund_name       TEXT,
        status_current  TEXT,
        is_rex          INTEGER NOT NULL DEFAULT 0
    )""",
    # identifier_xref: every external identifier maps to a canonical_id.
    # Composite primary key allows multiple ticker assignments over time.
    """CREATE TABLE IF NOT EXISTS identifier_xref (
        canonical_id    TEXT NOT NULL,
        id_type         TEXT NOT NULL,
        id_value        TEXT NOT NULL,
        valid_from      TIMESTAMP NOT NULL,
        valid_to        TIMESTAMP,
        source          TEXT NOT NULL,
        PRIMARY KEY (canonical_id, id_type, id_value, valid_from),
        FOREIGN KEY (canonical_id) REFERENCES product_master(canonical_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_identifier_xref_lookup
        ON identifier_xref (id_type, id_value)""",
    """CREATE INDEX IF NOT EXISTS idx_identifier_xref_current
        ON identifier_xref (id_type, id_value, valid_to)""",
    # underlier_master: typed polymorphic. Different fields populated per type.
    """CREATE TABLE IF NOT EXISTS underlier_master (
        underlier_id    TEXT PRIMARY KEY,
        underlier_type  TEXT NOT NULL,
        primary_figi    TEXT,
        ticker          TEXT,
        index_provider  TEXT,
        index_code      TEXT,
        crypto_base     TEXT,
        crypto_quote    TEXT,
        display_symbol  TEXT NOT NULL,
        created_at      TIMESTAMP NOT NULL,
        resolved_via    TEXT
    )""",
    """CREATE INDEX IF NOT EXISTS idx_underlier_master_display
        ON underlier_master (display_symbol)""",
    """CREATE INDEX IF NOT EXISTS idx_underlier_master_type
        ON underlier_master (underlier_type)""",
    # fund_underlier: many-to-many with weights + effective periods.
    """CREATE TABLE IF NOT EXISTS fund_underlier (
        canonical_id    TEXT NOT NULL,
        underlier_id    TEXT NOT NULL,
        weight          REAL,
        effective_from  TIMESTAMP NOT NULL,
        effective_to    TIMESTAMP,
        PRIMARY KEY (canonical_id, underlier_id, effective_from),
        FOREIGN KEY (canonical_id) REFERENCES product_master(canonical_id),
        FOREIGN KEY (underlier_id) REFERENCES underlier_master(underlier_id)
    )""",
    """CREATE INDEX IF NOT EXISTS idx_fund_underlier_current
        ON fund_underlier (canonical_id, effective_to)""",
]


def table_exists(conn: sqlite3.Connection, name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r[1] == column for r in rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        # Verify rex_products exists (Phase 3 must have already run).
        if not table_exists(conn, "rex_products"):
            print("ERROR: rex_products table not found. Run Phase 1-3 migrations first.",
                  file=sys.stderr)
            return 1

        existing_tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        new_tables = {"product_master", "identifier_xref", "underlier_master", "fund_underlier"}

        print(f"DB: {db_path}")
        print("Phase 4 Stage 1 — canonical-id schema migration")
        print(f"  product_master:    {'EXISTS' if 'product_master' in existing_tables else 'will create'}")
        print(f"  identifier_xref:   {'EXISTS' if 'identifier_xref' in existing_tables else 'will create'}")
        print(f"  underlier_master:  {'EXISTS' if 'underlier_master' in existing_tables else 'will create'}")
        print(f"  fund_underlier:    {'EXISTS' if 'fund_underlier' in existing_tables else 'will create'}")

        has_canonical_col = column_exists(conn, "rex_products", "canonical_id")
        print(f"  rex_products.canonical_id: {'EXISTS' if has_canonical_col else 'will add'}")

        if args.dry_run:
            print("\nDRY-RUN — no changes executed")
            return 0

        # 1. Run all DDL (idempotent via IF NOT EXISTS).
        for stmt in DDL_STATEMENTS:
            conn.execute(stmt)

        # 2. Add rex_products.canonical_id if missing. SQLite ALTER TABLE only
        # supports ADD COLUMN with NULLable + no default — exactly what we need.
        if not has_canonical_col:
            conn.execute("ALTER TABLE rex_products ADD COLUMN canonical_id TEXT")

        # 3. Indexes on the new column (separate from CREATE TABLE since SQLite
        # doesn't support indexed columns in ALTER TABLE).
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rex_products_canonical_id "
                     "ON rex_products (canonical_id)")

        conn.commit()

        # 4. Verification.
        after_tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        missing = new_tables - after_tables
        if missing:
            print(f"ERROR: tables not created: {missing}", file=sys.stderr)
            return 1
        if not column_exists(conn, "rex_products", "canonical_id"):
            print("ERROR: rex_products.canonical_id not added", file=sys.stderr)
            return 1

        print("\nCommitted. Schema ready for Phase 4 Stage 2 (data backfill).")
        print("Next: scripts/backfill_product_master.py (not yet written)")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
