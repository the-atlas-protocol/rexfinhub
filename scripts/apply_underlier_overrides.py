"""Apply underlier/category mapping overrides to mkt_master_data.

Reads config/rules/underlier_overrides.csv and writes the corrected mapping
value for each ticker listed.  Supports multiple target columns via the
optional 'column_name' CSV field.  The script is idempotent -- re-running it
is always safe because each UPDATE is only counted as a change when the stored
value actually differs from the override.

Design intent
-------------
Bloomberg's sync pipeline (sync_market_data / bloomberg.timer) overwrites
underlier/category fields on every run.  This script is intended to run
*after* sync_market_data so our curated fixes are the last word written to
the DB.  In the daily automation chain, add it immediately after the Bloomberg
sync step in run_daily.py or the systemd timer:

    sync_market_data  ->  apply_underlier_overrides  ->  prebake_reports

The CSV is the source of truth for corrections.  To add a new fix, append a
row to config/rules/underlier_overrides.csv and re-run this script.

CSV columns
-----------
    ticker             Bloomberg ticker (must match mkt_master_data.ticker)
    column_name        DB column to update (optional; defaults to map_li_underlier
                       for backward compatibility with Stream A rows).
                       Allowed values:
                           map_li_underlier
                           map_cc_underlier
                           map_crypto_underlier
                           map_defined_category
                           map_thematic_category
    corrected_value    Corrected value to write into column_name.
                       (Original Stream A rows use 'map_li_underlier' as both
                       the column name AND the value column -- the loader
                       handles both formats transparently.)
    source             Free-text audit trail label
    notes              Human-readable rationale
    fixed_at           ISO-8601 timestamp when the fix was authored

Usage
-----
    python scripts/apply_underlier_overrides.py
    python scripts/apply_underlier_overrides.py --dry-run
"""
from __future__ import annotations

import argparse
import csv
import random
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
OVERRIDES_CSV = PROJECT_ROOT / "config" / "rules" / "underlier_overrides.csv"

# Columns we are allowed to update -- prevents SQL injection via column_name field.
_ALLOWED_COLUMNS: frozenset[str] = frozenset({
    "map_li_underlier",
    "map_cc_underlier",
    "map_crypto_underlier",
    "map_defined_category",
    "map_thematic_category",
})

# Default column for legacy Stream A rows (no column_name field)
_DEFAULT_COLUMN = "map_li_underlier"

# Required CSV columns for schema contract
REQUIRED_CSV_COLUMNS = {"ticker"}


def load_overrides(csv_path: Path = OVERRIDES_CSV) -> tuple[list[dict], list[str]]:
    """Return (parsed rows, fieldnames) from the overrides CSV, skipping blanks.

    Handles both the original Stream A format (value in 'map_li_underlier'
    column) and the extended multi-column format (value in 'corrected_value'
    column with 'column_name' identifying the target DB column).
    """
    rows: list[dict] = []
    fieldnames: list[str] = []
    with csv_path.open(encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        for row in reader:
            ticker = (row.get("ticker") or "").strip()
            if not ticker:
                continue

            # Determine target column
            col_name = (row.get("column_name") or "").strip() or _DEFAULT_COLUMN

            # Determine corrected value: prefer 'corrected_value' field; fall back
            # to reading from the column whose name matches col_name (Stream A format).
            new_val = (row.get("corrected_value") or "").strip()
            if not new_val:
                new_val = (row.get(col_name) or "").strip()

            if not new_val:
                continue  # nothing to apply

            if col_name not in _ALLOWED_COLUMNS:
                print(f"  WARNING : {ticker:14s}  unknown column_name={col_name!r} -- skipping")
                continue

            rows.append({
                "ticker":     ticker,
                "column":     col_name,
                "new_val":    new_val,
                "source":     (row.get("source") or "").strip(),
                "notes":      (row.get("notes") or "").strip(),
            })
    return rows, fieldnames


# ---------------------------------------------------------------------------
# Precondition checks
# ---------------------------------------------------------------------------

def check_preconditions(
    overrides: list[dict],
    fieldnames: list[str],
    csv_path: Path = OVERRIDES_CSV,
    db_path: Path = DB_PATH,
) -> list[str]:
    """Return a list of error strings. Empty list = all preconditions pass."""
    errors: list[str] = []

    # 1. CSV file exists
    if not csv_path.exists():
        errors.append(f"CSV not found: {csv_path}")

    # 2. CSV has expected schema (ticker is mandatory)
    missing_cols = REQUIRED_CSV_COLUMNS - set(fieldnames)
    if missing_cols:
        errors.append(f"CSV missing required columns: {sorted(missing_cols)}")

    # 3. CSV has > 0 data rows
    if not overrides:
        errors.append("CSV has 0 usable override rows (empty or all skipped).")

    # 4 & 5. DB connection + table + columns
    if not db_path.exists():
        errors.append(f"DB not found: {db_path}")
    else:
        try:
            con = sqlite3.connect(str(db_path))
            cur = con.cursor()
            cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='mkt_master_data'"
            )
            if not cur.fetchone():
                errors.append("DB is missing table: mkt_master_data")
            else:
                cur.execute("PRAGMA table_info(mkt_master_data)")
                db_cols = {row[1] for row in cur.fetchall()}
                missing_db_cols = _ALLOWED_COLUMNS - db_cols
                if missing_db_cols:
                    errors.append(
                        f"mkt_master_data missing expected columns: {sorted(missing_db_cols)}"
                    )
            con.close()
        except sqlite3.Error as exc:
            errors.append(f"DB connection failed: {exc}")

    return errors


# ---------------------------------------------------------------------------
# Postcondition checks
# ---------------------------------------------------------------------------

def check_postconditions(
    overrides: list[dict],
    updated: int,
    not_found: int,
    db_path: Path = DB_PATH,
) -> list[str]:
    """Return a list of warning strings. Called AFTER writes."""
    warnings: list[str] = []

    total = len(overrides)
    expected_max = total - not_found
    if updated > expected_max:
        warnings.append(
            f"Postcondition: updated={updated} exceeds expected max={expected_max} "
            f"(total={total}, not_found={not_found}). Investigate."
        )

    # Spot-check: pick 3 random overrides and verify DB values match
    checkable = [o for o in overrides if o.get("ticker") and o.get("new_val")]
    sample = random.sample(checkable, min(3, len(checkable)))
    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        mismatches: list[str] = []
        for o in sample:
            ticker = o["ticker"]
            col = o["column"]
            expected_val = o["new_val"]
            cur.execute(
                f"SELECT {col} FROM mkt_master_data WHERE ticker = ?",  # noqa: S608
                (ticker,),
            )
            result = cur.fetchone()
            if result is None:
                continue  # delisted — acceptable
            db_val = result[0] or ""
            if db_val != expected_val:
                mismatches.append(
                    f"  {ticker} [{col}]: DB={db_val!r}, expected={expected_val!r}"
                )
        if mismatches:
            warnings.append(
                "Postcondition spot-check FAILED:\n" + "\n".join(mismatches)
            )
        con.close()
    except sqlite3.Error as exc:
        warnings.append(f"Postcondition DB check failed: {exc}")

    return warnings


def apply_overrides(
    con: sqlite3.Connection,
    overrides: list[dict],
    *,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    """Apply each override row.

    Returns (updated, noop, not_found).
    """
    cur = con.cursor()
    updated = 0
    noop = 0
    not_found = 0

    for row in overrides:
        ticker    = row["ticker"]
        col       = row["column"]
        new_val   = row["new_val"]

        # Read current value so we can report a true no-op.
        cur.execute(
            f"SELECT {col} FROM mkt_master_data WHERE ticker = ?",  # noqa: S608
            (ticker,),
        )
        existing = cur.fetchone()
        if existing is None:
            print(f"  NOT FOUND : {ticker:14s}  (not in mkt_master_data -- skipping)")
            not_found += 1
            continue

        current_val = existing[0] or ""
        if current_val == new_val:
            noop += 1
            continue

        print(
            f"  {'[DRY-RUN] ' if dry_run else ''}UPDATE : {ticker:14s}  "
            f"[{col}]  {current_val!r:28s}  ->  {new_val!r}"
        )
        if not dry_run:
            cur.execute(
                f"UPDATE mkt_master_data SET {col} = ? WHERE ticker = ?",  # noqa: S608
                (new_val, ticker),
            )
        updated += 1

    if not dry_run:
        con.commit()

    return updated, noop, not_found


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Apply underlier/category mapping overrides to mkt_master_data."
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to the database.",
    )
    ap.add_argument("--db-path", default=None,
                    help="Override DB path (used by test harness).")
    ap.add_argument("--csv-path", default=None,
                    help="Override CSV path (used by test harness).")
    args = ap.parse_args()

    db_path = Path(args.db_path) if args.db_path else DB_PATH
    csv_path = Path(args.csv_path) if args.csv_path else OVERRIDES_CSV

    if not csv_path.exists():
        print(f"ERROR: overrides CSV not found at {csv_path}")
        return 1
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}")
        return 1

    overrides, fieldnames = load_overrides(csv_path)
    print(f"Loaded {len(overrides)} override(s) from {csv_path}")

    # ---- Preconditions ----
    errors = check_preconditions(overrides, fieldnames, csv_path=csv_path, db_path=db_path)
    if errors:
        for e in errors:
            print(f"PRECONDITION FAILED: {e}", file=sys.stderr)
        return 1

    print("Preconditions OK.")

    if args.dry_run:
        print("[DRY-RUN] No changes will be written.\n")

    con = sqlite3.connect(str(db_path))
    try:
        updated, noop, not_found = apply_overrides(con, overrides, dry_run=args.dry_run)
    finally:
        con.close()

    print()
    if args.dry_run:
        print(f"[DRY-RUN] Would update : {updated}")
        print(f"[DRY-RUN] Already OK   : {noop}")
        print("[DRY-RUN] All preconditions passed. No writes performed.")
        return 0

    print(f"Updated  : {updated}")
    print(f"No-op    : {noop}  (already matched -- idempotent)")
    print(f"Not found: {not_found}")

    # ---- Postconditions ----
    warnings = check_postconditions(overrides, updated, not_found, db_path=db_path)
    if warnings:
        for w in warnings:
            print(f"POSTCONDITION WARNING: {w}", file=sys.stderr)
    else:
        print("Postconditions OK.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
