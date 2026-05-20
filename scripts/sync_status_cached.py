"""Phase 5 Stage 4 (ADR 0008): wire rex_products.status_cached denormalized column.

Adds a `status_cached` column to rex_products (lowercase canonical form, e.g.
'listed' vs the existing rex_products.status 'Listed'). Backfills from the
latest status_history.valid_to=NULL row per canonical_id.

After this lands, the status_reconciler.py should call refresh_cache(canonical_id)
after every append_transition() so the cache stays in sync. Read paths that
want the bi-temporal current-state can either read status_cached (fast) or
join status_history (history-aware).

The original rex_products.status column (CapM-case strings like 'Under
Consideration', 'Listed', 'Delisted') is RETAINED for backward compat with
templates + flow report. Phase 5 Stage 5 will deprecate direct writes to it.

Idempotent.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"


def ensure_column(conn: sqlite3.Connection) -> bool:
    """Add status_cached column if missing. Returns True if added."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(rex_products)").fetchall()}
    if "status_cached" in cols:
        return False
    conn.execute("ALTER TABLE rex_products ADD COLUMN status_cached TEXT")
    return True


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
        added = ensure_column(conn)
        print(f"DB: {db_path}")
        print(f"rex_products.status_cached: {'ADDED' if added else 'already present'}")

        # Backfill from status_history (most recent valid_to=NULL per canonical_id)
        rows = conn.execute("""
            SELECT rp.id, rp.canonical_id, sh.status
            FROM rex_products rp
            LEFT JOIN status_history sh
              ON sh.canonical_id = rp.canonical_id
             AND sh.valid_to IS NULL
            WHERE rp.canonical_id IS NOT NULL
        """).fetchall()

        updates = []
        no_history = 0
        unchanged = 0
        for rid, cid, cached_status in rows:
            if cached_status is None:
                no_history += 1
                continue
            updates.append((cached_status, rid))

        print(f"rex_products with canonical_id: {len(rows)}")
        print(f"  to set status_cached:        {len(updates)}")
        print(f"  no history row (skipped):    {no_history}")

        if args.dry_run:
            print("\nDRY-RUN — no writes")
            return 0

        # Idempotent: only update if value differs
        actual_writes = 0
        for status_cached, rid in updates:
            cur = conn.execute(
                "SELECT status_cached FROM rex_products WHERE id = ?", (rid,)
            ).fetchone()
            if cur and cur[0] == status_cached:
                continue
            conn.execute(
                "UPDATE rex_products SET status_cached = ? WHERE id = ?",
                (status_cached, rid),
            )
            actual_writes += 1

        conn.commit()
        print(f"\nCommitted {actual_writes} status_cached updates.")

        # Verification
        nulls = conn.execute(
            "SELECT COUNT(*) FROM rex_products WHERE canonical_id IS NOT NULL "
            "AND status_cached IS NULL"
        ).fetchone()[0]
        print(f"rows with canonical_id but NULL status_cached: {nulls}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
