"""Phase 6 Stage 7 prerequisite (ADR 0009): apply classification_override
rows to mkt_master_data after the data_engine builds it.

This is the operational hook that makes the 486 classification_override
rows from Stage 2 actually take effect. Until this script ran, admins
could write overrides via /admin/classify-override/{canonical_id} but
the live read paths still saw the CSV-derived classification.

Flow:
  1. For each classification_override row, join canonical_id ->
     identifier_xref (id_type='ticker', valid_to=NULL) -> ticker.
  2. For each (ticker, field_name, value), UPDATE the matching
     mkt_master_data row's column with the override value.
  3. NULL values in classification_override mean explicit blacklist —
     UPDATE the column to NULL.

Idempotent. Re-runs are no-ops for rows that already match.

Designed to run as an ExecStartPost step in rexfinhub-bloomberg-chain.service
after apply_bloomberg_post_steps.py finishes (Phase 1 wrapper). For now
this is invoked manually.

Usage:
    python scripts/apply_classification_overrides.py
    python scripts/apply_classification_overrides.py --dry-run
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"


# Map classification_override.field_name -> mkt_master_data column.
# Only fields that have a corresponding mkt_master_data column can be
# applied. Other fields (e.g. is_rex, expense_ratio_override) need
# different application paths and are skipped here.
_FIELD_TO_MKT_COL = {
    "etp_category":     "etp_category",
    "issuer_display":   "issuer_display",
    "primary_strategy": "primary_strategy",
    "asset_class":      "asset_class",
    "sub_strategy":     "sub_strategy",
    "direction":        "direction",
    "underlier_name":   "underlier_name",
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
    try:
        # Verify required tables
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        for tbl in ("classification_override", "identifier_xref",
                    "mkt_master_data"):
            if tbl not in tables:
                print(f"ERROR: {tbl} table missing.", file=sys.stderr)
                return 1

        # Pull all overrides
        rows = conn.execute("""
            SELECT co.canonical_id, co.field_name, co.value, co.set_by, co.reason,
                   ix.id_value AS ticker
            FROM classification_override co
            JOIN identifier_xref ix
              ON ix.canonical_id = co.canonical_id
             AND ix.id_type = 'ticker'
             AND ix.valid_to IS NULL
            WHERE co.field_name IN ({})
        """.format(",".join(f"'{f}'" for f in _FIELD_TO_MKT_COL.keys()))).fetchall()

        print(f"DB: {db_path}")
        print(f"applicable overrides: {len(rows)}")

        stats = {"updated": 0, "unchanged": 0, "no_mkt_row": 0,
                 "blacklisted": 0}
        per_field = {}

        for canonical_id, field_name, value, set_by, reason, ticker in rows:
            col = _FIELD_TO_MKT_COL.get(field_name)
            if not col:
                continue

            # Find matching mkt_master_data row (by ticker)
            mkt = conn.execute(
                f"SELECT ticker, {col} FROM mkt_master_data "
                "WHERE UPPER(ticker_clean) = UPPER(?) OR UPPER(ticker) = UPPER(?) "
                "LIMIT 1",
                (ticker, ticker),
            ).fetchone()
            if not mkt:
                stats["no_mkt_row"] += 1
                continue

            current = mkt[1]
            if current == value:
                stats["unchanged"] += 1
                continue

            if value is None:
                stats["blacklisted"] += 1
            else:
                stats["updated"] += 1
            per_field[field_name] = per_field.get(field_name, 0) + 1

            if not args.dry_run:
                conn.execute(
                    f"UPDATE mkt_master_data SET {col} = ? "
                    "WHERE UPPER(ticker_clean) = UPPER(?) OR UPPER(ticker) = UPPER(?)",
                    (value, ticker, ticker),
                )

        if not args.dry_run:
            conn.commit()

        print(f"\nApplication summary:")
        for k, v in stats.items():
            print(f"  {k:14s} {v}")
        print(f"\nPer field:")
        for f, n in sorted(per_field.items()):
            print(f"  {f:18s} {n}")

        if args.dry_run:
            print("\nDRY-RUN — no writes")

        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
