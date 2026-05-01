"""Phase 1.3 — Apply ticker cleanup decisions from the review queue.

Reads docs/ticker_review_queue.csv (output of audit_ticker_duplicates.py,
optionally edited by Ryu) and applies the suggested_action column:

  KEEP    — leave the row untouched
  NULL    — set fund_extractions.class_symbol = NULL for that row
  MANUAL  — leave for Ryu's manual review (NO change applied)

Records every change to data/.ticker_cleanup_log.jsonl with full context
so the operation is auditable + reversible if needed.

This script DOES write to the DB. It's idempotent (re-running is safe —
already-NULLed rows stay NULL).

Usage:
    python scripts/apply_ticker_cleanup.py            # apply
    python scripts/apply_ticker_cleanup.py --dry-run  # preview, no writes
"""
from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
QUEUE_CSV = PROJECT_ROOT / "docs" / "ticker_review_queue.csv"
AUDIT_LOG = PROJECT_ROOT / "data" / ".ticker_cleanup_log.jsonl"


def _now_et() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")
    except Exception:
        return datetime.now().isoformat(timespec="seconds")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would change without writing to DB.")
    args = ap.parse_args()

    if not QUEUE_CSV.exists():
        print(f"ERROR: queue CSV not found at {QUEUE_CSV}")
        print("Run scripts/audit_ticker_duplicates.py first.")
        return 1
    if not DB_PATH.exists():
        print(f"ERROR: DB not found at {DB_PATH}")
        return 1

    rows = list(csv.DictReader(QUEUE_CSV.open(encoding="utf-8")))
    print(f"Loaded {len(rows):,} rows from {QUEUE_CSV}")

    # Tally
    from collections import Counter
    actions = Counter(r["suggested_action"] for r in rows)
    print(f"Actions: {dict(actions)}")
    print()

    null_rows = [r for r in rows if r["suggested_action"] == "NULL"]
    keep_rows = [r for r in rows if r["suggested_action"] == "KEEP"]
    manual_rows = [r for r in rows if r["suggested_action"] == "MANUAL"]

    if args.dry_run:
        print(f"[DRY-RUN] Would NULL  {len(null_rows):,} fund_extractions.class_symbol values")
        print(f"[DRY-RUN] Would KEEP  {len(keep_rows):,} rows untouched")
        print(f"[DRY-RUN] Would SKIP  {len(manual_rows):,} rows (MANUAL — needs your review)")
        # Sample
        if null_rows:
            print()
            print("Sample of rows that would be NULL'd (first 5):")
            for r in null_rows[:5]:
                print(f"  id={r['extraction_id']:>6s} acc={r['accession_number'][-12:]} | "
                      f"{r['series_name'][:50]:50s} | tk={r['class_symbol']} → NULL")
        return 0

    # Apply
    con = sqlite3.connect(str(DB_PATH))
    cur = con.cursor()
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)

    updated = 0
    with AUDIT_LOG.open("a", encoding="utf-8") as logf:
        ts = _now_et()
        for r in null_rows:
            try:
                eid = int(r["extraction_id"])
            except (ValueError, KeyError):
                continue
            cur.execute(
                "UPDATE fund_extractions SET class_symbol = NULL WHERE id = ?",
                (eid,),
            )
            if cur.rowcount > 0:
                updated += 1
                logf.write(json.dumps({
                    "timestamp": ts,
                    "action": "NULL",
                    "extraction_id": eid,
                    "old_class_symbol": r["class_symbol"],
                    "series_name": r["series_name"],
                    "accession_number": r["accession_number"],
                    "registrant": r["registrant"],
                    "reason": r["reason"],
                }) + "\n")

    con.commit()
    con.close()

    print(f"Applied {updated:,} NULL updates to fund_extractions.class_symbol")
    print(f"Audit log: {AUDIT_LOG}")
    print(f"KEEP:     {len(keep_rows):,} rows left untouched")
    print(f"MANUAL:   {len(manual_rows):,} rows skipped (need your review)")
    print()
    print("Next:")
    print("  - Verify your dashboard / reports show clean ticker assignments")
    print("  - Optionally re-run audit_ticker_duplicates.py to confirm no remaining dupes")
    print("  - Review the BRACKET (MANUAL) rows separately for pre-IPO placeholders")
    return 0


if __name__ == "__main__":
    sys.exit(main())
