"""Apply L&I underlier mapping overrides to mkt_master_data.

Reads config/rules/underlier_overrides.csv and writes the corrected
map_li_underlier value for each ticker listed.  The script is idempotent —
re-running it is always safe because each UPDATE is only counted as a change
when the stored value actually differs from the override.

Design intent
-------------
Bloomberg's sync pipeline (sync_market_data / bloomberg.timer) overwrites
map_li_underlier on every run.  This script is intended to run *after*
sync_market_data so our curated fixes are the last word written to the DB.
In the daily automation chain, add it immediately after the Bloomberg sync
step in run_daily.py or the systemd timer:

    sync_market_data  →  apply_underlier_overrides  →  prebake_reports

The CSV is the source of truth for corrections.  To add a new fix, append a
row to config/rules/underlier_overrides.csv and re-run this script.

CSV columns
-----------
    ticker             Bloomberg ticker (must match mkt_master_data.ticker)
    map_li_underlier   Corrected underlier value to write
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
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
OVERRIDES_CSV = PROJECT_ROOT / "config" / "rules" / "underlier_overrides.csv"


def load_overrides() -> list[dict]:
    """Return parsed rows from the overrides CSV, skipping blanks."""
    rows: list[dict] = []
    with OVERRIDES_CSV.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            ticker = (row.get("ticker") or "").strip()
            underlier = (row.get("map_li_underlier") or "").strip()
            if ticker and underlier:
                rows.append({
                    "ticker":           ticker,
                    "map_li_underlier": underlier,
                    "source":           (row.get("source") or "").strip(),
                    "notes":            (row.get("notes") or "").strip(),
                })
    return rows


def apply_overrides(
    con: sqlite3.Connection,
    overrides: list[dict],
    *,
    dry_run: bool = False,
) -> tuple[int, int]:
    """Apply each override row.

    Returns (updated, noop) — number of rows actually changed vs already
    matching (idempotent re-runs produce 0 updated, N noop).
    """
    cur = con.cursor()
    updated = 0
    noop = 0
    not_found = 0

    for row in overrides:
        ticker = row["ticker"]
        new_val = row["map_li_underlier"]

        # Read current value so we can report a true no-op
        cur.execute(
            "SELECT map_li_underlier FROM mkt_master_data WHERE ticker = ?",
            (ticker,),
        )
        existing = cur.fetchone()
        if existing is None:
            print(f"  NOT FOUND : {ticker:14s}  (not in mkt_master_data — skipping)")
            not_found += 1
            continue

        current_val = existing[0] or ""
        if current_val == new_val:
            noop += 1
            continue

        print(
            f"  {'[DRY-RUN] ' if dry_run else ''}UPDATE : {ticker:14s}  "
            f"{current_val!r:25s}  ->  {new_val!r}"
        )
        if not dry_run:
            cur.execute(
                "UPDATE mkt_master_data SET map_li_underlier = ? WHERE ticker = ?",
                (new_val, ticker),
            )
        updated += 1

    if not dry_run:
        con.commit()

    return updated, noop


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Apply L&I underlier mapping overrides to mkt_master_data."
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing to the database.",
    )
    args = ap.parse_args()

    if not OVERRIDES_CSV.exists():
        print(f"ERROR: overrides CSV not found at {OVERRIDES_CSV}")
        return 1
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        return 1

    overrides = load_overrides()
    print(f"Loaded {len(overrides)} override(s) from {OVERRIDES_CSV}")

    if args.dry_run:
        print("[DRY-RUN] No changes will be written.\n")

    con = sqlite3.connect(str(DB_PATH))
    try:
        updated, noop = apply_overrides(con, overrides, dry_run=args.dry_run)
    finally:
        con.close()

    print()
    if args.dry_run:
        print(f"[DRY-RUN] Would update : {updated}")
        print(f"[DRY-RUN] Already OK   : {noop}")
    else:
        print(f"Updated  : {updated}")
        print(f"No-op    : {noop}  (already matched — idempotent)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
