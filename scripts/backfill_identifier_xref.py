"""Phase 4 (ADR 0006) — Stage 3: backfill identifier_xref from rex_products.

For each rex_products row that has a canonical_id, inserts identifier_xref rows
for every populated identifier column. All current — valid_to=NULL.

Identifier sources mapped:
    ticker           -> id_type='ticker'
    cik              -> id_type='cik'
    series_id        -> id_type='series_id'
    class_contract_id -> id_type='class_contract_id'
    bb_ticker        -> id_type='bloomberg'

valid_from heuristic: COALESCE(initial_filing_date, created_at)
source: 'backfill_2026-05-19'

Idempotent: checks (canonical_id, id_type, id_value, valid_from) before insert.

Pre-requisite: backfill_product_master.py must have run.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"

# (rex_products column, id_type)
ID_MAPPINGS = [
    ("ticker", "ticker"),
    ("cik", "cik"),
    ("series_id", "series_id"),
    ("class_contract_id", "class_contract_id"),
    ("bb_ticker", "bloomberg"),
]

BACKFILL_SOURCE = "backfill_2026-05-19"


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
        # Pre-flight checks
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "identifier_xref" not in tables:
            print("ERROR: identifier_xref table missing. Run migrate_canonical_id_schema.py first.",
                  file=sys.stderr)
            return 1

        rex_rows = conn.execute("""
            SELECT id, canonical_id, ticker, cik, series_id, class_contract_id,
                   bb_ticker, initial_filing_date, created_at
            FROM rex_products
            WHERE canonical_id IS NOT NULL
        """).fetchall()

        no_canonical = conn.execute(
            "SELECT COUNT(*) FROM rex_products WHERE canonical_id IS NULL"
        ).fetchone()[0]
        if no_canonical:
            print(f"WARNING: {no_canonical} rex_products rows missing canonical_id. "
                  "Run backfill_product_master.py first.")

        print(f"DB: {db_path}")
        print(f"rex_products rows with canonical_id: {len(rex_rows)}")

        # Build candidate rows
        candidates: list[tuple] = []
        stats = {f"{ty}_proposed": 0 for _, ty in ID_MAPPINGS}

        for r in rex_rows:
            valid_from = r["initial_filing_date"] or r["created_at"] or datetime.utcnow().isoformat()
            for col, id_type in ID_MAPPINGS:
                val = r[col]
                if val is None:
                    continue
                val_s = str(val).strip()
                if not val_s:
                    continue
                candidates.append((
                    r["canonical_id"], id_type, val_s, valid_from, BACKFILL_SOURCE,
                ))
                stats[f"{id_type}_proposed"] += 1

        # Existing rows to skip (idempotency)
        existing = {
            (row[0], row[1], row[2], row[3])
            for row in conn.execute(
                "SELECT canonical_id, id_type, id_value, valid_from FROM identifier_xref"
            ).fetchall()
        }
        to_insert = [c for c in candidates if (c[0], c[1], c[2], c[3]) not in existing]

        print(f"  candidate rows total: {len(candidates)}")
        print(f"  already present:      {len(candidates) - len(to_insert)}")
        print(f"  to insert:            {len(to_insert)}")
        for col, ty in ID_MAPPINGS:
            print(f"    {ty:20s} proposed: {stats[f'{ty}_proposed']}")

        if args.dry_run:
            print("\nDRY-RUN — no changes executed")
            return 0

        if to_insert:
            conn.executemany(
                "INSERT INTO identifier_xref (canonical_id, id_type, id_value, valid_from, source) "
                "VALUES (?, ?, ?, ?, ?)",
                to_insert,
            )
            conn.commit()
            print(f"\nCommitted {len(to_insert)} new identifier_xref rows.")
        else:
            print("\nNothing to insert.")

        # Validation
        total = conn.execute("SELECT COUNT(*) FROM identifier_xref").fetchone()[0]
        print(f"identifier_xref total rows: {total}")
        for col, ty in ID_MAPPINGS:
            n = conn.execute(
                "SELECT COUNT(*) FROM identifier_xref WHERE id_type = ?", (ty,)
            ).fetchone()[0]
            print(f"  {ty:20s} count: {n}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
