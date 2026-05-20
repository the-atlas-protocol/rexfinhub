"""BUG-04 extension: demote rex_products.status=Listed rows whose ticker shows
LIQU/DLST/INAC/EXPD in mkt_master_data.

phase4_demote_vanished_from_market handles the case where the ticker is
COMPLETELY GONE from mkt_master_data. This script handles the parallel case:
the ticker is still in mkt_master_data but with an explicit non-active
status that the rex_products row hasn't picked up yet.

Found by the assertion runner's listed_has_mkt_data check on 2026-05-19:
  BMAX (LIQU), XRPK (DLST), SOLX (DLST), +2 more.

Per-ticker audit logged. Idempotent.

Usage:
    python scripts/demote_liqu_dlst_rex_products.py --dry-run
    python scripts/demote_liqu_dlst_rex_products.py --apply
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"


NON_ACTIVE_STATUSES = {"LIQU", "DLST", "INAC", "EXPD"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true",
                    help="Write changes. Default = dry-run.")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute("""
            SELECT rp.id, rp.ticker, rp.name, rp.manually_edited_fields,
                   m.market_status, m.fund_name AS mkt_name
            FROM rex_products rp
            JOIN mkt_master_data m
              ON UPPER(m.ticker_clean) = UPPER(rp.ticker)
                 OR UPPER(m.ticker) = UPPER(rp.ticker)
            WHERE rp.status = 'Listed'
              AND m.market_status IN ('LIQU','DLST','INAC','EXPD')
        """).fetchall()

        print(f"DB: {db_path}")
        print(f"Candidates (Listed REX with mkt LIQU/DLST/INAC/EXPD): {len(rows)}")
        for r in rows:
            print(f"  {r['ticker']:8s} {r['market_status']:5s} | {(r['name'] or '')[:55]}")

        if not args.apply:
            print("\nDRY-RUN — no changes")
            return 0

        now = datetime.utcnow().isoformat()
        applied = 0
        skipped_admin = 0
        for r in rows:
            # Skip if admin has overridden status
            overrides = []
            if r["manually_edited_fields"]:
                try:
                    v = json.loads(r["manually_edited_fields"])
                    if isinstance(v, list):
                        overrides = [str(x) for x in v]
                except (json.JSONDecodeError, TypeError):
                    pass
            if "status" in overrides:
                skipped_admin += 1
                continue

            conn.execute("UPDATE rex_products SET status = 'Delisted', updated_at = ? "
                         "WHERE id = ?", (now, r["id"]))
            # Audit log
            conn.execute("""
                INSERT INTO capm_audit_log
                (action, table_name, row_id, field_name, old_value, new_value,
                 row_label, changed_by, changed_at)
                VALUES ('UPDATE', 'rex_products', ?, 'status', 'Listed', 'Delisted',
                        ?, 'demote_liqu_dlst:auto', ?)
            """, (r["id"], r["ticker"] or r["name"] or f"#{r['id']}", now))
            applied += 1

        conn.commit()
        print(f"\nApplied: {applied}")
        print(f"Skipped (admin override): {skipped_admin}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
