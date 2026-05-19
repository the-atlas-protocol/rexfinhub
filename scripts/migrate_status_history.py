"""Phase 5 (ADR 0008) — Stages 1+2: status_history schema + backfill.

Creates the bi-temporal status_history table and synthesizes one history
row per active rex_products row using the current status.

  valid_from = COALESCE(initial_filing_date, created_at)
  valid_to   = NULL (current)
  tx_from    = NOW
  tx_to      = NULL
  source     = 'backfill_2026-05-19'

For Listed rows, an extra synthetic 'effective' row is inserted dated
75 days before inception_date (SEC Rule 485(a) default) — gives us a
minimal history that the future reconciler can build on.

Idempotent. Pre-requisite: backfill_product_master.py.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"

DDL = """
CREATE TABLE IF NOT EXISTS status_history (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id    TEXT NOT NULL,
    status          TEXT NOT NULL,
    valid_from      TIMESTAMP NOT NULL,
    valid_to        TIMESTAMP,
    tx_from         TIMESTAMP NOT NULL,
    tx_to           TIMESTAMPTZ,
    source          TEXT NOT NULL,
    evidence        TEXT,
    set_by          TEXT NOT NULL,
    created_at      TIMESTAMP NOT NULL,
    FOREIGN KEY (canonical_id) REFERENCES product_master(canonical_id)
)
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_status_history_canonical ON status_history (canonical_id, valid_from)",
    "CREATE INDEX IF NOT EXISTS idx_status_history_current ON status_history (status, valid_to)",
]

# Map rex_products.status (6-state enum) -> status_history.status (lowercase canonical)
STATUS_MAP = {
    "Under Consideration": "under_consideration",
    "Target List":         "target_list",
    "Filed":               "filed",
    "Effective":           "effective",
    "Listed":              "listed",
    "Delisted":            "delisted",
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
        # Schema
        conn.execute(DDL)
        for stmt in INDEXES:
            conn.execute(stmt)

        rex_rows = conn.execute("""
            SELECT id, canonical_id, status, initial_filing_date, inception_date,
                   official_listed_date, created_at
            FROM rex_products
            WHERE canonical_id IS NOT NULL
        """).fetchall()

        # Existing history rows (idempotency — skip canonical_ids that already have any history)
        existing = {row[0] for row in conn.execute(
            "SELECT DISTINCT canonical_id FROM status_history"
        ).fetchall()}

        print(f"DB: {db_path}")
        print(f"rex_products rows with canonical_id: {len(rex_rows)}")
        print(f"canonical_ids with status_history already: {len(existing)}")

        now = datetime.utcnow().isoformat()
        to_insert = []
        stats = {"current": 0, "synthesized_effective": 0, "skipped": 0,
                 "unknown_status": 0}

        for r in rex_rows:
            if r["canonical_id"] in existing:
                stats["skipped"] += 1
                continue

            raw_status = r["status"] or "Under Consideration"
            canonical_status = STATUS_MAP.get(raw_status)
            if not canonical_status:
                stats["unknown_status"] += 1
                continue

            valid_from = r["initial_filing_date"] or r["created_at"] or now

            # Current state row — valid_to=NULL
            to_insert.append((
                r["canonical_id"], canonical_status, valid_from, None,
                now, None, "backfill_2026-05-19", None, "backfill", now,
            ))
            stats["current"] += 1

            # For Listed rows with inception_date, synthesize an earlier 'effective'
            # transition dated 75 days before inception (SEC Rule 485(a) default).
            if canonical_status == "listed" and r["inception_date"]:
                try:
                    inception = datetime.fromisoformat(str(r["inception_date"])[:10])
                    eff_from = (inception - timedelta(days=75)).isoformat()
                    eff_to = inception.isoformat()
                    to_insert.append((
                        r["canonical_id"], "effective", eff_from, eff_to,
                        now, None, "backfill_2026-05-19", None, "backfill", now,
                    ))
                    stats["synthesized_effective"] += 1
                except (ValueError, TypeError):
                    pass

        print(f"\nBackfill plan:")
        for k, v in stats.items():
            print(f"  {k:25s} {v}")
        print(f"  rows to insert total:     {len(to_insert)}")

        if args.dry_run:
            print("\nDRY-RUN — no changes executed")
            return 0

        if to_insert:
            conn.executemany(
                "INSERT INTO status_history "
                "(canonical_id, status, valid_from, valid_to, "
                " tx_from, tx_to, source, evidence, set_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                to_insert,
            )
            conn.commit()
            print(f"\nCommitted {len(to_insert)} status_history rows.")

        total = conn.execute("SELECT COUNT(*) FROM status_history").fetchone()[0]
        current = conn.execute(
            "SELECT COUNT(*) FROM status_history WHERE valid_to IS NULL"
        ).fetchone()[0]
        print(f"status_history total: {total} (current: {current})")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
