"""One-shot backfill: add capm_audit_log entries for the 15 rows rescued by
scripts/repair_missing_canonical.py on 2026-06-04 (rex_products.id 1213-1227).

Context
-------
NEW-01 (handoff 2026-06-08, §4): the repair script did not write to
capm_audit_log when it rescued the 15 missing-canonical orphan series on
2026-06-04. Trace 2 (single_filing 0001771146-26-000937) surfaced the gap.
The runtime fix (repair_missing_canonical._audit_repair) is shipped in the
same PR; this script retroactively backfills the 15 rows so the activity log
is contiguous.

Safety
------
- Dry-run by default. ``--apply`` requires ``I AGREE`` via stdin.
- Idempotent — won't re-insert if a capm_audit_log row already exists for
  the same row_id + changed_by='repair_missing_canonical'.
- Backs up the DB before --apply, same rotation policy as the sibling
  scripts (3 most-recent kept).

This script is a one-time tool. Once it has been run successfully on the
VPS its job is done; DO NOT run it again routinely.

Usage
-----
    python scripts/backfill_repair_audit_log_2026-06-08.py
    python scripts/backfill_repair_audit_log_2026-06-08.py --apply
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"
BACKUPS_DIR = PROJECT_ROOT / "data" / "backups"

# Range of rex_products.id rescued on 2026-06-04. Inclusive on both ends.
TARGET_ID_LO = 1213
TARGET_ID_HI = 1227
CHANGED_BY = "repair_missing_canonical"
# Date we are backdating the audit entry to (the day the repair ran).
RESCUE_DATE_ISO = "2026-06-04T00:00:00"


def _confirm_apply() -> bool:
    print("\n--apply will modify data/etp_tracker.db.")
    print("Type 'I AGREE' to proceed (anything else aborts):")
    try:
        return input().strip() == "I AGREE"
    except EOFError:
        return False


def _backup_db(db_path: Path) -> Path | None:
    if not db_path.exists():
        return None
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    dst = BACKUPS_DIR / f"etp_tracker_{stamp}_pre_backfill_repair_audit.db"
    shutil.copy2(db_path, dst)
    try:
        snaps = sorted(
            BACKUPS_DIR.glob("etp_tracker_*_pre_backfill_repair_audit.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in snaps[3:]:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:
        pass
    return dst


def find_target_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, name, canonical_id FROM rex_products "
        "WHERE id BETWEEN ? AND ? ORDER BY id",
        (TARGET_ID_LO, TARGET_ID_HI),
    ).fetchall()


def already_audited(conn: sqlite3.Connection, row_id: int) -> bool:
    return conn.execute(
        "SELECT 1 FROM capm_audit_log "
        "WHERE row_id = ? AND changed_by = ? LIMIT 1",
        (row_id, CHANGED_BY),
    ).fetchone() is not None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--apply", action="store_true", default=False)
    ap.add_argument("--no-prompt", action="store_true", default=False)
    args = ap.parse_args(argv)

    dry_run = not args.apply
    if args.dry_run:
        dry_run = True

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    if not dry_run and not args.no_prompt:
        if not _confirm_apply():
            print("Aborted (no 'I AGREE').")
            return 2

    if not dry_run:
        backup = _backup_db(db_path)
        if backup:
            print(f"Backup: {backup}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        # Pre-flight.
        for tbl in ("rex_products", "capm_audit_log"):
            present = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (tbl,),
            ).fetchone()
            if not present:
                print(f"ERROR: required table {tbl!r} missing", file=sys.stderr)
                return 1

        rows = find_target_rows(conn)
        print(f"=== backfill_repair_audit_log ({TARGET_ID_LO}-{TARGET_ID_HI}) ===")
        print(f"Mode             : {'DRY-RUN' if dry_run else 'APPLY'}")
        print(f"Target rows found: {len(rows)}")
        if len(rows) < (TARGET_ID_HI - TARGET_ID_LO + 1):
            print(f"WARN: expected {TARGET_ID_HI - TARGET_ID_LO + 1} rows in range, "
                  f"found {len(rows)} — some IDs may have been deleted")

        to_insert = []
        for r in rows:
            if r["canonical_id"] is None:
                print(f"  skip id={r['id']} — no canonical_id (was never rescued?)")
                continue
            if already_audited(conn, r["id"]):
                print(f"  skip id={r['id']} — audit row already present")
                continue
            to_insert.append(r)

        print(f"Will insert      : {len(to_insert)}")

        if dry_run:
            for r in to_insert:
                print(f"  PLAN id={r['id']:>4d}  canonical={r['canonical_id'][:8]}...  "
                      f"name={(r['name'] or '')[:40]}")
            print("\nDRY-RUN — pass --apply to write.")
            return 0

        for r in to_insert:
            conn.execute(
                """
                INSERT INTO capm_audit_log
                (action, table_name, row_id, field_name, old_value, new_value,
                 row_label, changed_by, changed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "RESCUE",
                    "rex_products",
                    r["id"],
                    "canonical_id",
                    None,
                    r["canonical_id"],
                    (r["name"] or "")[:200],
                    CHANGED_BY,
                    RESCUE_DATE_ISO,
                ),
            )
        conn.commit()
        print(f"Inserted {len(to_insert)} backfill rows.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
