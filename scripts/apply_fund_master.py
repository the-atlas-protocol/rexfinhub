"""Phase 3.3 — Apply fund_master.csv to mkt_master_data new columns.

Reads config/rules/fund_master.csv and writes the new taxonomy columns
(asset_class, primary_strategy, sub_strategy + 20 attributes) to
mkt_master_data. Idempotent — safe to re-run after editing the CSV.

Manual edits to fund_master.csv take precedence over auto-classifier
output (per the Layer 1 priority in CLASSIFICATION_SYSTEM_PLAN.md).
"""
from __future__ import annotations

import argparse
import csv
import random
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
INPUT_CSV = PROJECT_ROOT / "config" / "rules" / "fund_master.csv"

# Columns in fund_master.csv that map directly to mkt_master_data columns
TARGET_COLUMNS = [
    "asset_class", "primary_strategy", "sub_strategy",
    "concentration", "underlier_name", "underlier_is_wrapper",
    "root_underlier_name", "wrapper_type", "mechanism",
    "leverage_ratio", "direction", "reset_period",
    "distribution_freq", "outcome_period_months",
    "cap_pct", "buffer_pct", "accelerator_multiplier", "barrier_pct",
    "region", "duration_bucket", "credit_quality",
    "tax_structure", "qualified_dividends",
]

# Columns in fund_master.csv that must be present (schema contract)
REQUIRED_CSV_COLUMNS = {"ticker"} | set(TARGET_COLUMNS)

# Columns that, once written, must NOT be NULL for a properly classified fund
# (asset_class and primary_strategy are the minimum meaningful signal)
NON_NULL_POSTCONDITION_COLS = ["asset_class", "primary_strategy"]


def _coerce_value(col: str, raw: str):
    """Type-coerce CSV string values for SQLite columns."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if col in ("leverage_ratio", "cap_pct", "buffer_pct",
               "accelerator_multiplier", "barrier_pct"):
        try:
            return float(raw)
        except ValueError:
            return None
    if col == "outcome_period_months":
        try:
            return int(raw)
        except ValueError:
            return None
    if col in ("underlier_is_wrapper", "qualified_dividends"):
        return 1 if raw.lower() in ("true", "yes", "1", "t") else 0
    return raw


# ---------------------------------------------------------------------------
# Precondition checks
# ---------------------------------------------------------------------------

def check_preconditions(
    rows: list[dict],
    fieldnames: list[str],
    csv_path: Path = INPUT_CSV,
    db_path: Path = DB_PATH,
) -> list[str]:
    """Return a list of error strings. Empty list = all preconditions pass."""
    errors: list[str] = []

    # 1. CSV file exists — already verified before loading; checked again here
    #    for callers that pass pre-loaded data (e.g. test harness).
    if not csv_path.exists():
        errors.append(f"CSV not found: {csv_path}")

    # 2. CSV has expected schema
    missing_cols = REQUIRED_CSV_COLUMNS - set(fieldnames or [])
    if missing_cols:
        errors.append(f"CSV missing required columns: {sorted(missing_cols)}")

    # 3. CSV has > 0 data rows
    if not rows:
        errors.append("CSV has 0 data rows (empty after header).")

    # 4 & 5. DB connection + table + columns
    if not db_path.exists():
        errors.append(f"DB not found: {db_path}")
    else:
        try:
            con = sqlite3.connect(str(db_path))
            cur = con.cursor()
            cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='mkt_master_data'")
            if not cur.fetchone():
                errors.append("DB is missing table: mkt_master_data")
            else:
                cur.execute("PRAGMA table_info(mkt_master_data)")
                db_cols = {row[1] for row in cur.fetchall()}
                missing_db_cols = (set(TARGET_COLUMNS) | {"ticker"}) - db_cols
                if missing_db_cols:
                    errors.append(f"mkt_master_data missing columns: {sorted(missing_db_cols)}")
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

    expected_updated = len(rows) - not_found
    # Allow for idempotent re-runs (rows already matching count as 0 rowcount)
    # so we only warn when updated exceeds expected — not when it is lower.
    if updated > expected_updated:
        warnings.append(
            f"Postcondition: updated={updated} exceeds expected={expected_updated} "
            f"(csv_rows={len(rows)}, not_found={not_found}). Investigate."
        )

    # NULL check on key columns for tickers we should have written
    written_tickers = [
        (r.get("ticker") or "").strip()
        for r in rows
        if (r.get("ticker") or "").strip()
        and (r.get("asset_class") or "").strip()  # only check rows that had a value
    ]
    if written_tickers:
        try:
            con = sqlite3.connect(str(db_path))
            cur = con.cursor()
            for col in NON_NULL_POSTCONDITION_COLS:
                # Sample up to 200 tickers for the NULL check
                sample = written_tickers[:200]
                placeholders = ",".join("?" * len(sample))
                cur.execute(
                    f"SELECT COUNT(*) FROM mkt_master_data "
                    f"WHERE ticker IN ({placeholders}) AND {col} IS NULL",
                    sample,
                )
                null_count = cur.fetchone()[0]
                if null_count > 0:
                    warnings.append(
                        f"Postcondition: {null_count} rows in mkt_master_data have NULL {col} "
                        f"after update (sampled {len(sample)} written tickers)."
                    )

            # Spot-check: 3 random tickers from CSV vs DB
            checkable = [
                r for r in rows
                if (r.get("ticker") or "").strip() and (r.get("asset_class") or "").strip()
            ]
            sample_rows = random.sample(checkable, min(3, len(checkable)))
            mismatches: list[str] = []
            for r in sample_rows:
                ticker = r["ticker"].strip()
                expected_ac = r.get("asset_class", "").strip() or None
                cur.execute(
                    "SELECT asset_class FROM mkt_master_data WHERE ticker = ?",
                    (ticker,),
                )
                result = cur.fetchone()
                if result is None:
                    continue  # delisted — acceptable
                db_val = result[0]
                if db_val != expected_ac:
                    mismatches.append(
                        f"  {ticker}: DB asset_class={db_val!r}, CSV={expected_ac!r}"
                    )
            if mismatches:
                warnings.append(
                    "Postcondition spot-check FAILED for:\n" + "\n".join(mismatches)
                )

            con.close()
        except sqlite3.Error as exc:
            warnings.append(f"Postcondition DB check failed: {exc}")

    return warnings


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Preview changes without writing.")
    ap.add_argument("--db-path", default=None,
                    help="Override DB path (used by test harness).")
    ap.add_argument("--csv-path", default=None,
                    help="Override CSV path (used by test harness).")
    args = ap.parse_args()

    # Allow test harness to inject paths without monkey-patching globals
    db_path = Path(args.db_path) if args.db_path else DB_PATH
    csv_path = Path(args.csv_path) if args.csv_path else INPUT_CSV

    # ---- Load CSV ----
    if not csv_path.exists():
        print(f"ERROR: {csv_path} not found. Run build_fund_master_seed.py first.")
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
        print("[DRY-RUN] Would update mkt_master_data with these classifications:")
        for r in rows[:5]:
            print(f"  {r['ticker']:10s} -> asset={r['asset_class']:12s} "
                  f"primary={r['primary_strategy']:18s} sub={r['sub_strategy'][:35]}")
        if len(rows) > 5:
            print(f"  ... and {len(rows) - 5:,} more")
        print("[DRY-RUN] All preconditions passed. No writes performed.")
        return 0

    # ---- Execute writes ----
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()

    set_clauses = ", ".join(f"{c} = ?" for c in TARGET_COLUMNS)
    sql = f"UPDATE mkt_master_data SET {set_clauses} WHERE ticker = ?"

    updated = 0
    not_found = 0
    for r in rows:
        ticker = (r.get("ticker") or "").strip()
        if not ticker:
            continue
        values = [_coerce_value(c, r.get(c, "")) for c in TARGET_COLUMNS]
        values.append(ticker)
        cur.execute(sql, values)
        if cur.rowcount > 0:
            updated += 1
        else:
            not_found += 1

    con.commit()
    con.close()

    print(f"Applied: {updated:,} rows updated in mkt_master_data")
    print(f"Not found: {not_found:,} tickers (in CSV but not in mkt_master_data — possibly liquidated)")

    # ---- Postconditions ----
    warnings = check_postconditions(rows, updated, not_found, db_path=db_path)
    if warnings:
        for w in warnings:
            print(f"POSTCONDITION WARNING: {w}", file=sys.stderr)
        # Don't auto-rollback — writes are committed. Warn and let operator decide.
    else:
        print("Postconditions OK.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
