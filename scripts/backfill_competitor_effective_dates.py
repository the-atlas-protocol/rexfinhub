"""Backfill fund_status.effective_date for COMPETITOR rows from parsed 485 filings.

Why this exists: ``scripts/refresh_effective_dates.py`` propagates parsed
effective dates onto ``rex_products`` (REX products) only. Competitor products
live in ``fund_status`` and their ``effective_date`` is never refreshed from the
485-series parse, so ~hundreds of competitor rows sit NULL even though a real
prospectus-effective / election date was parsed into ``fund_extractions`` from
their 485APOS / 485BXT / 485BPOS filing. That NULL leaves the "Earliest Eff"
column empty in the six COMPETITOR sections of the T-REX system report
(load_underlier_competition() in trex_combined_v9.py reads
fund_status.effective_date).

This step is the forward mirror of refresh_effective_dates.py: join
fund_status.series_id -> fund_extractions.series_id and take the most recent
485-series filing's effective_date. The series_id link is exact, so multi-series
filings are handled correctly.

IMPORTANT: this never invents a date. It only writes where a NULL fund_status
row has a REAL parsed effective_date in fund_extractions for the same series_id
from a 485APOS / 485BXT / 485BPOS filing. Rows that are genuinely date-less
(DELAYED / recently filed / no election parsed) stay NULL, which is correct.
Idempotent and re-runnable.

Usage:
    python scripts/backfill_competitor_effective_dates.py            # dry-run
    python scripts/backfill_competitor_effective_dates.py --apply    # writes; backs up first
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

# Most recent 485-series filing's effective date for a given series_id.
# Identical form policy to refresh_effective_dates.py: 485APOS sets the date,
# 485BXT extends it, 485BPOS is the immediately-effective amendment. 497* forms
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true",
                    help="Write the backfilled dates (default: dry-run).")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    if args.apply:
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUPS_DIR / f"etp_tracker_{datetime.now():%Y%m%dT%H%M%S}_pre_comp_eff_backfill.db"
        shutil.copy2(db_path, backup)
        print(f"Backup: {backup}")

    conn = sqlite3.connect(str(db_path))
    try:
        # Only NULL rows with a usable series_id are candidates.
        rows = conn.execute(
            "SELECT id, ticker, fund_name, series_id FROM fund_status "
            "WHERE (effective_date IS NULL OR TRIM(effective_date) = '') "
            "  AND series_id IS NOT NULL AND TRIM(series_id) <> ''"
        ).fetchall()

        filled = 0
        for rid, ticker, fund_name, series_id in rows:
            hit = conn.execute(LATEST_485_EFF, (series_id,)).fetchone()
            if not hit or not hit[0]:
                continue  # genuinely date-less — leave NULL
            new_eff = hit[0]
            filled += 1
            if filled <= 30:
                label = ticker or (fund_name or "")[:34]
                print(f"  {label:36s} NULL  ->  {new_eff}")
            if args.apply:
                conn.execute(
                    "UPDATE fund_status SET effective_date = ?, "
                    "effective_date_confidence = COALESCE(effective_date_confidence, 'BACKFILL'), "
                    "updated_at = ? WHERE id = ?",
                    (new_eff, datetime.utcnow().isoformat(), rid),
                )

        if args.apply:
            conn.commit()

        remaining_null = conn.execute(
            "SELECT COUNT(*) FROM fund_status "
            "WHERE effective_date IS NULL OR TRIM(effective_date) = ''"
        ).fetchone()[0]
        # In dry-run the 'filled' rows are still NULL in the DB, so subtract them
        # to report the would-be remaining count.
        projected_remaining = remaining_null - (0 if args.apply else filled)

        verb = "backfilled" if args.apply else "would backfill"
        print(f"\n{'APPLIED' if args.apply else 'DRY-RUN'}: {filled} competitor "
              f"effective date(s) {verb} from parsed 485 filings.")
        print(f"Remaining legitimately-NULL fund_status rows "
              f"(DELAYED / no parsed date): {projected_remaining}.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
