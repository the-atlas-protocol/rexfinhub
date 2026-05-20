"""Track 4b — drop the retired rex_products.underlier column.

`underlier` is now a hybrid_property on the RexProduct model that resolves
from underlier_master via fund_underlier (Phase 4 / ADR 0006). underlier_master
is the single typed source of truth. This script drops the now-dead physical
column.

GATE C — equivalence proof of death. Every rex_products row with a non-empty
`underlier` value must resolve to a non-empty underlier_master.display_symbol
via its fund_underlier link; otherwise dropping the column would lose data.
The script aborts if any row fails.

Idempotent: a no-op when the column is already gone.

Usage:
    python scripts/drop_rex_underlier_column.py --dry-run
    python scripts/drop_rex_underlier_column.py
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"


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
        cols = {r[1] for r in conn.execute("PRAGMA table_info(rex_products)").fetchall()}
        if "underlier" not in cols:
            print("rex_products.underlier already dropped — nothing to do.")
            return 0

        total_nonempty = conn.execute(
            "SELECT COUNT(*) FROM rex_products "
            "WHERE underlier IS NOT NULL AND TRIM(underlier) <> ''"
        ).fetchone()[0]

        # GATE C — every non-empty underlier must resolve via underlier_master.
        lost = conn.execute("""
            SELECT rp.ticker, rp.underlier
            FROM rex_products rp
            WHERE rp.underlier IS NOT NULL AND TRIM(rp.underlier) <> ''
              AND NOT EXISTS (
                  SELECT 1 FROM fund_underlier fu
                  JOIN underlier_master um ON um.underlier_id = fu.underlier_id
                  WHERE fu.canonical_id = rp.canonical_id
                    AND fu.effective_to IS NULL
                    AND um.display_symbol IS NOT NULL
                    AND TRIM(um.display_symbol) <> ''
              )
        """).fetchall()

        print(f"DB: {db_path}")
        print(f"rex_products rows with a non-empty underlier: {total_nonempty}")
        print(f"\nGATE C — rows that would LOSE underlier data: {len(lost)}")
        for t, u in lost[:20]:
            print(f"    {t}: underlier={u!r} has no underlier_master resolution")

        if lost:
            print("\nGATE C FAILED — some underlier values are not recoverable from "
                  "underlier_master. Aborting; column NOT dropped.")
            return 1

        print("  GATE C PASSED — every underlier value resolves via underlier_master.")

        if args.dry_run:
            print("\nDRY-RUN — rex_products.underlier would be DROPPED. No change made.")
            return 0

        conn.execute("ALTER TABLE rex_products DROP COLUMN underlier")
        conn.commit()
        print("\nrex_products.underlier DROPPED. Retirement complete — the column is "
              "now the RexProduct.underlier hybrid resolving from underlier_master.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
