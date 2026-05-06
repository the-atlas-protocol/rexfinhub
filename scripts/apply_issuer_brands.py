"""Phase 3.4 — Apply issuer_brand_overrides.csv to mkt_master_data.

Reads config/rules/issuer_brand_overrides.csv and writes issuer_display for
each ticker row. Idempotent — safe to re-run; skips rows already matching.

Design intent:
    sync_market_data (BBG re-import) resets issuer_display to NULL.
    This script must be run AFTER every sync_market_data call to restore
    brand attributions. Suitable for inclusion in run_daily.py or as a
    post-sync hook.

Usage:
    python scripts/apply_issuer_brands.py [--dry-run]

Reference style: scripts/apply_fund_master.py
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
OVERRIDES_CSV = PROJECT_ROOT / "config" / "rules" / "issuer_brand_overrides.csv"

# Columns the CSV must contain
REQUIRED_CSV_COLUMNS = {"ticker", "issuer_display"}

# DB column we write — must exist in mkt_master_data
TARGET_DB_COLUMN = "issuer_display"


# ---------------------------------------------------------------------------
# Precondition checks
# ---------------------------------------------------------------------------

def check_preconditions(
    rows: list[dict],
    fieldnames: list[str],
    csv_path: Path = OVERRIDES_CSV,
    db_path: Path = DB_PATH,
) -> list[str]:
    """Return a list of error strings. Empty list = all preconditions pass."""
    errors: list[str] = []

    # 1. CSV file exists
    if not csv_path.exists():
        errors.append(f"CSV not found: {csv_path}")

    # 2. CSV has expected schema
    missing_cols = REQUIRED_CSV_COLUMNS - set(fieldnames)
    if missing_cols:
        errors.append(f"CSV missing required columns: {sorted(missing_cols)}")

    # 3. CSV has > 0 data rows
    if not rows:
        errors.append("CSV has 0 data rows (empty after header).")

    # 4 & 5. DB connection + table + column
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
                missing_db_cols = {"ticker", TARGET_DB_COLUMN} - db_cols
                if missing_db_cols:
                    errors.append(
                        f"mkt_master_data missing columns: {sorted(missing_db_cols)}"
                    )
            con.close()
        except sqlite3.Error as exc:
            errors.append(f"DB connection failed: {exc}")

    return errors


# ---------------------------------------------------------------------------
# Postcondition checks
# ---------------------------------------------------------------------------

def check_postconditions(
    rows: list[dict],
    updated: int,
    not_found: int,
    db_path: Path = DB_PATH,
) -> list[str]:
    """Return a list of warning strings. Called AFTER writes."""
    warnings: list[str] = []

    # Row-count sanity: updated should not exceed (rows - not_found)
    expected_max = len(rows) - not_found
    if updated > expected_max:
        warnings.append(
            f"Postcondition: updated={updated} exceeds expected max={expected_max} "
            f"(csv_rows={len(rows)}, not_found={not_found}). Investigate."
        )

    # NULL check: rows we wrote should now have a non-NULL issuer_display
    written_tickers = [
        (r.get("ticker") or "").strip()
        for r in rows
        if (r.get("ticker") or "").strip() and (r.get("issuer_display") or "").strip()
    ]
    if written_tickers:
        try:
            con = sqlite3.connect(str(db_path))
            cur = con.cursor()
            sample = written_tickers[:200]
            placeholders = ",".join("?" * len(sample))
            cur.execute(
                f"SELECT COUNT(*) FROM mkt_master_data "
                f"WHERE ticker IN ({placeholders}) AND issuer_display IS NULL",
                sample,
            )
            null_count = cur.fetchone()[0]
            if null_count > 0:
                warnings.append(
                    f"Postcondition: {null_count} rows in mkt_master_data still have "
                    f"NULL issuer_display after update (sampled {len(sample)} tickers)."
                )

            # Spot-check: 3 random rows — verify DB value matches CSV value
            checkable = [
                r for r in rows
                if (r.get("ticker") or "").strip() and (r.get("issuer_display") or "").strip()
            ]
            sample_rows = random.sample(checkable, min(3, len(checkable)))
            mismatches: list[str] = []
            for r in sample_rows:
                ticker = r["ticker"].strip()
                expected_brand = r["issuer_display"].strip()
                cur.execute(
                    "SELECT issuer_display FROM mkt_master_data WHERE ticker = ?",
                    (ticker,),
                )
                result = cur.fetchone()
                if result is None:
                    continue  # delisted — acceptable
                db_val = result[0] or ""
                if db_val != expected_brand:
                    mismatches.append(
                        f"  {ticker}: DB issuer_display={db_val!r}, CSV={expected_brand!r}"
                    )
            if mismatches:
                warnings.append(
                    "Postcondition spot-check FAILED:\n" + "\n".join(mismatches)
                )

            con.close()
        except sqlite3.Error as exc:
            warnings.append(f"Postcondition DB check failed: {exc}")

    return warnings


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Apply issuer_brand_overrides.csv to mkt_master_data."
    )
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview changes without writing to the DB.")
    ap.add_argument("--db-path", default=None,
                    help="Override DB path (used by test harness).")
    ap.add_argument("--csv-path", default=None,
                    help="Override CSV path (used by test harness).")
    args = ap.parse_args()

    db_path = Path(args.db_path) if args.db_path else DB_PATH
    csv_path = Path(args.csv_path) if args.csv_path else OVERRIDES_CSV

    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found. "
              "Run scripts/derive_issuer_brands.py first.")
        return 1
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}")
        return 1

    with csv_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = list(reader.fieldnames or [])

    print(f"Loaded {len(rows):,} rows from {csv_path}")

    # ---- Preconditions ----
    errors = check_preconditions(rows, fieldnames, csv_path=csv_path, db_path=db_path)
    if errors:
        for e in errors:
            print(f"PRECONDITION FAILED: {e}", file=sys.stderr)
        return 1

    print("Preconditions OK.")

    # ---- Dry-run ----
    if args.dry_run:
        print("[DRY-RUN] Would update mkt_master_data with these brands:")
        for r in rows[:5]:
            print(f"  {r['ticker']:15s} -> {r['issuer_display']}")
        if len(rows) > 5:
            print(f"  ... and {len(rows) - 5:,} more")
        print("[DRY-RUN] All preconditions passed. No writes performed.")
        return 0

    # ---- Execute writes ----
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()

    updated = 0
    noop = 0
    not_found = 0

    for r in rows:
        ticker = (r.get("ticker") or "").strip()
        brand = (r.get("issuer_display") or "").strip()
        if not ticker or not brand:
            continue

        cur.execute(
            "SELECT issuer_display FROM mkt_master_data WHERE ticker = ?",
            (ticker,),
        )
        existing = cur.fetchone()

        if existing is None:
            not_found += 1
            continue

        current_val = existing[0]
        if current_val == brand:
            noop += 1
            continue

        cur.execute(
            "UPDATE mkt_master_data SET issuer_display = ? WHERE ticker = ?",
            (brand, ticker),
        )
        if cur.rowcount > 0:
            updated += 1
        else:
            not_found += 1

    con.commit()
    con.close()

    print(f"Applied:   {updated:,} rows updated in mkt_master_data")
    print(f"No-ops:    {noop:,} rows already had correct issuer_display")
    print(f"Not found: {not_found:,} tickers in CSV but not in DB "
          "(possibly liquidated or delisted)")

    # ---- Postconditions ----
    warnings = check_postconditions(rows, updated, not_found, db_path=db_path)
    if warnings:
        for w in warnings:
            print(f"POSTCONDITION WARNING: {w}", file=sys.stderr)
    else:
        print("Postconditions OK.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
