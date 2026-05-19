"""Phase 4 (ADR 0006) — Stage 2: backfill product_master + canonical_id.

For every existing rex_products row, generates a UUID, inserts a matching
row into product_master, and sets rex_products.canonical_id to the same UUID.

Idempotent: skips rows that already have a canonical_id set. Safe to re-run.

Pre-requisite: scripts/migrate_canonical_id_schema.py must have already run.

Usage:
    python scripts/backfill_product_master.py
    python scripts/backfill_product_master.py --dry-run
    python scripts/backfill_product_master.py --db /path/to/etp_tracker.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import uuid
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"

# REX-branded suite names (drives is_rex flag on product_master).
# Mirrors webapp/routers/capm.py::_classify_rex_suite logic but explicit here
# so the script is standalone-runnable.
REX_SUITES = {
    "T-REX", "REX", "REX-OSPREY", "REX Premium Income",
    "Premium Income", "Growth & Income", "IncomeMax", "Crypto",
    "Thematic", "Autocallable", "T-Bill", "MicroSectors ETN",
}


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
    conn.row_factory = sqlite3.Row
    try:
        # Verify Stage 1 ran (canonical_id column + product_master table both present).
        rex_cols = {row[1] for row in conn.execute("PRAGMA table_info(rex_products)").fetchall()}
        if "canonical_id" not in rex_cols:
            print("ERROR: rex_products.canonical_id missing. Run migrate_canonical_id_schema.py first.",
                  file=sys.stderr)
            return 1
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "product_master" not in tables:
            print("ERROR: product_master table missing. Run migrate_canonical_id_schema.py first.",
                  file=sys.stderr)
            return 1

        rex_rows = conn.execute(
            "SELECT id, name, product_suite, status, canonical_id FROM rex_products"
        ).fetchall()

        existing_count = conn.execute("SELECT COUNT(*) FROM product_master").fetchone()[0]

        print(f"DB: {db_path}")
        print(f"rex_products rows: {len(rex_rows)}")
        print(f"product_master existing rows: {existing_count}")

        stats = {"already_has_id": 0, "would_create": 0, "would_update_rex": 0}
        new_pm_rows = []
        rex_updates = []

        for r in rex_rows:
            if r["canonical_id"]:
                stats["already_has_id"] += 1
                continue
            new_uuid = str(uuid.uuid4())
            is_rex = 1 if (r["product_suite"] in REX_SUITES) else 0
            new_pm_rows.append((
                new_uuid,
                datetime.utcnow().isoformat(),
                r["name"],
                r["status"],
                is_rex,
            ))
            rex_updates.append((new_uuid, r["id"]))
            stats["would_create"] += 1
            stats["would_update_rex"] += 1

        print(f"  rex_products with canonical_id already set: {stats['already_has_id']}")
        print(f"  new product_master rows to insert:          {stats['would_create']}")
        print(f"  rex_products rows to update canonical_id:   {stats['would_update_rex']}")

        if args.dry_run:
            print("\nDRY-RUN — no changes executed")
            return 0

        if new_pm_rows:
            conn.executemany(
                "INSERT INTO product_master "
                "(canonical_id, created_at, fund_name, status_current, is_rex) "
                "VALUES (?, ?, ?, ?, ?)",
                new_pm_rows,
            )
            conn.executemany(
                "UPDATE rex_products SET canonical_id = ? WHERE id = ?",
                rex_updates,
            )
            conn.commit()
            print(f"\nCommitted. Inserted {len(new_pm_rows)} product_master rows; "
                  f"updated {len(rex_updates)} rex_products rows.")
        else:
            print("\nNothing to do.")

        # Validation.
        unset = conn.execute(
            "SELECT COUNT(*) FROM rex_products WHERE canonical_id IS NULL"
        ).fetchone()[0]
        pm_total = conn.execute("SELECT COUNT(*) FROM product_master").fetchone()[0]
        rex_total = conn.execute("SELECT COUNT(*) FROM rex_products").fetchone()[0]
        print(f"\nValidation:")
        print(f"  rex_products with canonical_id=NULL:  {unset}")
        print(f"  product_master total rows:             {pm_total}")
        print(f"  rex_products total rows:               {rex_total}")
        if unset > 0:
            print("  WARNING: some rex_products rows have no canonical_id.")
            return 1
        if pm_total != rex_total:
            print(f"  WARNING: count mismatch ({pm_total} vs {rex_total}).")
            return 1
        print("  All rex_products rows have canonical_id; counts match.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
