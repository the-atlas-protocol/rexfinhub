"""Audit-log coverage scaffold + T-REX 2X re-home (2026-05-12).

Background
----------
``webapp/database.py`` ``init_db()`` historically only imported a subset of
ORM models before calling ``Base.metadata.create_all()``. SQLAlchemy only
registers tables for imported classes, so four audit-adjacent tables were
silently missing from a fresh DB on first boot:

    - classification_audit_log
    - classification_proposals
    - reserved_symbols
    - api_audit_log

They appeared lazily when a request handler imported the model — but any
write that happened before that first lazy import lost its audit trail.

Two new audit tables were introduced in the same fix:

    - reserved_symbols_audit_log    (one row per ADD / UPDATE / DELETE on
                                     reserved_symbols, with a ``changes``
                                     JSON blob)
    - rex_product_status_history    (per-row history of rex_products.status
                                     transitions)

This script does two things, idempotently:

  1. Ensures all SIX of those tables exist in ``data/etp_tracker.db``
     (a no-op once the model registration fix has shipped and the app has
     booted at least once, but safe to run anyway as a backstop).
  2. Re-homes the 21 stranded rows previously written to
     ``classification_audit_log`` under
     ``sweep_run_id='manual_2026-05-09_trex2x'``. Those rows record
     T-REX 2X status transitions, which belong in
     ``rex_product_status_history`` — they were mis-routed because that
     table did not exist yet.

Usage
-----
    # Dry run (default) — reports what WOULD change, no writes.
    python scripts/migrate_audit_coverage_2026-05-12.py
    python scripts/migrate_audit_coverage_2026-05-12.py --dry-run

    # Apply — requires typing "I AGREE" at the interactive confirm.
    python scripts/migrate_audit_coverage_2026-05-12.py --apply

    # Point at a non-default DB (e.g. a local copy).
    python scripts/migrate_audit_coverage_2026-05-12.py --db data/test.db
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"

# The full set of tables that init_db() is expected to materialize for
# audit coverage. Names must match the ORM ``__tablename__`` exactly.
TARGET_TABLES = [
    "classification_audit_log",     # registered lazily — pre-existing
    "classification_proposals",     # registered lazily — pre-existing
    "reserved_symbols",             # registered lazily — pre-existing
    "api_audit_log",                # registered lazily — pre-existing
    "reserved_symbols_audit_log",   # NEW (2026-05-12)
    "rex_product_status_history",   # NEW (2026-05-12)
]

# The "stranded" T-REX 2X audit rows to re-home from
# classification_audit_log into rex_product_status_history.
STRANDED_SWEEP_RUN_ID = "manual_2026-05-09_trex2x"


def _confirm_apply() -> bool:
    """Demand the operator type the literal string 'I AGREE'."""
    print()
    print("=" * 72)
    print("APPLY mode requested. This will write to the database.")
    print("Type 'I AGREE' (exactly, case-sensitive) to proceed, or anything")
    print("else to abort.")
    print("=" * 72)
    try:
        answer = input("> ").strip()
    except (EOFError, KeyboardInterrupt):
        print("Aborted.")
        return False
    if answer != "I AGREE":
        print(f"Aborted. Received: {answer!r}")
        return False
    return True


def _existing_tables(conn: sqlite3.Connection, names: list[str]) -> set[str]:
    """Return the subset of ``names`` that exist in the DB."""
    placeholders = ",".join("?" for _ in names)
    rows = conn.execute(
        f"SELECT name FROM sqlite_master WHERE type='table' AND name IN ({placeholders})",
        names,
    ).fetchall()
    return {r[0] for r in rows}


def _create_missing_tables(missing: list[str], *, dry_run: bool) -> int:
    """Create the listed tables via SQLAlchemy metadata. Returns count created."""
    if not missing:
        return 0
    # Importing webapp.* triggers SQLAlchemy model registration. Do it lazily
    # so the script can be inspected without dragging the full app onto sys.path.
    sys.path.insert(0, str(PROJECT_ROOT))
    from webapp.database import Base, engine
    # noqa: F401 — importing for side-effects (registers tables on Base.metadata)
    import webapp.models  # noqa: F401

    created = 0
    for tname in missing:
        if tname not in Base.metadata.tables:
            print(f"  [WARN] {tname} not registered on Base.metadata "
                  f"— skipping. Did the model definition land?")
            continue
        if dry_run:
            print(f"  [DRY] would create table: {tname}")
        else:
            Base.metadata.tables[tname].create(bind=engine, checkfirst=True)
            print(f"  [APPLY] created table: {tname}")
        created += 1
    return created


def _build_ticker_to_rex_product_id(conn: sqlite3.Connection) -> dict[str, int]:
    """Map UPPER(ticker) -> rex_products.id for ticker resolution."""
    cur = conn.execute(
        "SELECT id, ticker FROM rex_products "
        "WHERE ticker IS NOT NULL AND ticker != ''"
    )
    mapping: dict[str, int] = {}
    for rid, ticker in cur.fetchall():
        key = (ticker or "").strip().upper()
        if not key:
            continue
        # Latest id wins on collision; rex_products has duplicates for
        # placeholder tickers during pre-launch (see capm.py:248 comment).
        mapping[key] = rid
    return mapping


def _rehome_stranded(
    conn: sqlite3.Connection, *, dry_run: bool
) -> tuple[int, int]:
    """Move T-REX 2X status rows out of classification_audit_log.

    Returns (rehomed_count, skipped_count). Idempotent: rows already
    present in rex_product_status_history with a matching note tag are
    skipped.
    """
    # Stranded rows must exist in classification_audit_log to be considered.
    try:
        rows = conn.execute(
            "SELECT id, ticker, column_name, old_value, new_value, "
            "       sweep_run_id, reason, created_at "
            "FROM classification_audit_log "
            "WHERE sweep_run_id = ?",
            (STRANDED_SWEEP_RUN_ID,),
        ).fetchall()
    except sqlite3.OperationalError as e:
        # classification_audit_log doesn't exist yet — nothing to re-home.
        print(f"  [SKIP] classification_audit_log not present: {e}")
        return 0, 0

    if not rows:
        print(f"  [INFO] no stranded rows found for sweep_run_id="
              f"{STRANDED_SWEEP_RUN_ID!r}")
        return 0, 0

    ticker_map = _build_ticker_to_rex_product_id(conn)
    note_tag = f"rehomed_from=classification_audit_log "\
               f"sweep_run_id={STRANDED_SWEEP_RUN_ID}"

    # Idempotency check: count how many we've already migrated.
    try:
        already = conn.execute(
            "SELECT COUNT(*) FROM rex_product_status_history "
            "WHERE notes LIKE ?",
            (f"%{note_tag}%",),
        ).fetchone()[0]
    except sqlite3.OperationalError:
        already = 0
    if already:
        print(f"  [INFO] {already} rows already re-homed previously "
              f"(matched on notes tag) — will only add the remainder.")

    rehomed = 0
    skipped = 0
    for (cal_id, ticker, column_name, old_val, new_val, _run, reason,
         created_at) in rows:
        # We only re-home rows that actually represent a status mutation.
        if (column_name or "").lower() != "status":
            skipped += 1
            continue
        key = (ticker or "").strip().upper()
        rex_product_id = ticker_map.get(key)
        if rex_product_id is None:
            print(f"  [WARN] no rex_products row for ticker={ticker!r} "
                  f"(cal_id={cal_id}) — inserting with NULL FK")
        notes = f"{note_tag} cal_id={cal_id}"
        if reason:
            notes = f"{notes} reason={reason}"

        # Skip if a row with this exact tag is already present.
        # In dry-run the target table may not exist yet — fail-soft.
        try:
            existing = conn.execute(
                "SELECT 1 FROM rex_product_status_history "
                "WHERE notes LIKE ? LIMIT 1",
                (f"%cal_id={cal_id}%",),
            ).fetchone()
        except sqlite3.OperationalError:
            existing = None
        if existing:
            skipped += 1
            continue

        if dry_run:
            print(f"  [DRY] would re-home cal_id={cal_id} ticker={ticker!r} "
                  f"{old_val!r} -> {new_val!r}")
        else:
            conn.execute(
                "INSERT INTO rex_product_status_history "
                "(rex_product_id, old_status, new_status, changed_at, "
                " changed_by, notes) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    rex_product_id,
                    old_val,
                    new_val,
                    created_at or datetime.utcnow().isoformat(sep=" "),
                    "migrate_audit_coverage_2026-05-12",
                    notes,
                ),
            )
        rehomed += 1
    return rehomed, skipped


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH,
                        help=f"SQLite DB path (default: {DEFAULT_DB_PATH})")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="Report only, no writes (default).")
    mode.add_argument("--apply", action="store_true",
                      help="Apply changes; requires interactive 'I AGREE'.")
    args = parser.parse_args()

    dry_run = not args.apply  # default = dry-run

    print("=" * 72)
    print(f"  Mode: {'APPLY' if not dry_run else 'DRY RUN'}")
    print(f"  DB:   {args.db}")
    print("=" * 72)

    if not args.db.exists():
        print(f"ERROR: DB not found at {args.db}", file=sys.stderr)
        return 2

    if not dry_run and not _confirm_apply():
        return 1

    conn = sqlite3.connect(str(args.db))
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        # ---- Step 1: table coverage ----
        print()
        print("Step 1: ensuring all audit-related tables exist")
        existing = _existing_tables(conn, TARGET_TABLES)
        missing = [t for t in TARGET_TABLES if t not in existing]
        for t in TARGET_TABLES:
            mark = "OK" if t in existing else "MISSING"
            print(f"  [{mark}] {t}")
        created = _create_missing_tables(missing, dry_run=dry_run)

        # Re-open so the newly-created tables are visible to the
        # sqlite3 connection (SQLAlchemy created them on a separate
        # connection in the same DB file).
        conn.commit()
        conn.close()
        conn = sqlite3.connect(str(args.db))
        conn.execute("PRAGMA foreign_keys=ON")

        # ---- Step 2: re-home stranded T-REX 2X rows ----
        print()
        print("Step 2: re-homing stranded T-REX 2X audit rows")
        rehomed, skipped = _rehome_stranded(conn, dry_run=dry_run)

        if not dry_run:
            conn.commit()

        # ---- Summary ----
        print()
        print("=" * 72)
        print(f"  Tables created : {created} (of {len(missing)} missing)")
        print(f"  Rows re-homed  : {rehomed}")
        print(f"  Rows skipped   : {skipped} (non-status / already migrated)")
        if dry_run:
            print(f"  [DRY RUN] No changes were written. Re-run with --apply.")
        print("=" * 72)
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
