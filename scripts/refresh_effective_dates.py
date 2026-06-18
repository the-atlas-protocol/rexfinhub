"""Refresh rex_products.estimated_effective_date from the latest 485-series filing.

Why this exists: ``sync_rex_products_from_filings`` collapses a multi-series
filing to a single extraction, so one 485BXT / 485BPOS covering 15+ series
updates only ONE product's effective date. Funds that file repeated 485BXT
effective-date extensions (e.g. T-REX 2X Long BITF — a 15-series 485BXT) drift
stale because their series is not the one the sync happened to pick.

This step refreshes EVERY product directly: join rex_products.series_id to
fund_extractions.series_id and take the most recent 485-series filing's
effective_date. The series_id link is exact, so multi-series filings are
handled correctly. Idempotent; respects manually_edited_fields overrides.

Usage:
    python scripts/refresh_effective_dates.py            # dry-run (default)
    python scripts/refresh_effective_dates.py --apply    # writes; backs up first
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"
BACKUPS_DIR = PROJECT_ROOT / "data" / "backups"

# Most recent 485-series filing's effective date for a given series_id.
# 485APOS sets it, 485BXT extends it, 485BPOS is the immediately-effective
# amendment — all three carry the authoritative effective date. 497* forms
# are post-effective prospectus updates and are deliberately excluded.
LATEST_485_EFF = """
    SELECT fe.effective_date
    FROM fund_extractions fe
    JOIN filings f ON f.id = fe.filing_id
    WHERE fe.series_id = ?
      AND fe.effective_date IS NOT NULL AND TRIM(fe.effective_date) <> ''
      AND f.form IN ('485APOS', '485BXT', '485BPOS')
    ORDER BY f.filing_date DESC, f.id DESC
    LIMIT 1
"""


def _is_overridden(manually_edited_fields: str | None) -> bool:
    """True if an admin has manually pinned estimated_effective_date."""
    if not manually_edited_fields:
        return False
    try:
        parsed = json.loads(manually_edited_fields)
        if isinstance(parsed, list):
            return "estimated_effective_date" in parsed
    except (ValueError, TypeError):
        pass
    return "estimated_effective_date" in manually_edited_fields


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true",
                    help="Write the refreshed dates (default: dry-run).")
    args = ap.parse_args()
    dry_run = not args.apply

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    if args.apply:
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUPS_DIR / f"etp_tracker_{datetime.now():%Y%m%dT%H%M%S}_pre_eff_refresh.db"
        shutil.copy2(db_path, backup)
        print(f"Backup: {backup}")

    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT id, ticker, name, series_id, estimated_effective_date, "
            "manually_edited_fields FROM rex_products "
            "WHERE series_id IS NOT NULL AND TRIM(series_id) <> ''"
        ).fetchall()

        changed = 0
        skipped_override = 0
        for rid, ticker, name, series_id, cur_eff, mef in rows:
            hit = conn.execute(LATEST_485_EFF, (series_id,)).fetchone()
            if not hit or not hit[0]:
                continue
            new_eff = hit[0]
            if str(cur_eff) == str(new_eff):
                continue
            if _is_overridden(mef):
                skipped_override += 1
                continue
            changed += 1
            if changed <= 30:
                label = ticker or (name or "")[:34]
                print(f"  {label:36s} {cur_eff}  ->  {new_eff}")
            if args.apply:
                conn.execute(
                    "UPDATE rex_products SET estimated_effective_date = ?, "
                    "updated_at = ? WHERE id = ?",
                    (new_eff, datetime.utcnow().isoformat(), rid),
                )

        # Durable fund_status fallback: the 485APOS election backfill writes
        # fund_status (not fund_extractions), so propagate its election dates onto
        # rex_products rows still NULL, matched by series_id — closes the gap that
        # left the filed T-REX 2X products showing "Pending". (Ryu 2026-06-17.)
        if args.apply:
            _fs = conn.execute(
                "UPDATE rex_products SET estimated_effective_date = ("
                "  SELECT fs.effective_date FROM fund_status fs"
                "  WHERE fs.series_id = rex_products.series_id"
                "    AND fs.effective_date IS NOT NULL AND TRIM(fs.effective_date) <> ''"
                "  ORDER BY fs.effective_date LIMIT 1) "
                "WHERE (estimated_effective_date IS NULL OR TRIM(estimated_effective_date) = '') "
                "  AND series_id IS NOT NULL AND TRIM(series_id) <> '' "
                "  AND EXISTS (SELECT 1 FROM fund_status fs WHERE fs.series_id = rex_products.series_id"
                "              AND fs.effective_date IS NOT NULL AND TRIM(fs.effective_date) <> '')"
            )
            print(f"  fund_status fallback: filled {_fs.rowcount or 0} NULL date(s) by series_id")
            conn.commit()

        verb = "refreshed" if args.apply else "would refresh"
        print(f"\n{'APPLIED' if args.apply else 'DRY-RUN'}: {changed} "
              f"effective date(s) {verb}; {skipped_override} skipped "
              f"(admin override).")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
