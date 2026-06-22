"""ADR 0014 — propagate the ONE authoritative effective date to both derived stores.

`fund_extractions.effective_date` is THE source of truth (parsed at ingestion by
etp_tracker/step3.py via the entity-aware robust election parser). The two stores
that REPORTS read are derived from it here, at every refresh:

  * fund_status.effective_date          — the table the T-REX report + the six
                                          competitor sections (load_underlier_competition)
                                          read for "Earliest Eff." This was the
                                          dead-letter route: the parse never reached it.
  * rex_products.estimated_effective_date — the REX product cache.

For each series_id we take the effective_date of the LATEST 485-family filing by
filing_date (485APOS sets it, 485BXT extends/replaces it, 485BPOS makes it effective).

NON-DESTRUCTIVE write rule (ADR 0014, 'never invents'): we FILL a NULL/blank target,
and for an existing non-NULL date we only move it FORWARD (new >= current). We never
move a date backward — an annual 485BPOS prospectus-refresh for a long-listed fund
carries a date earlier than the fund's recorded effective date, and clobbering 53k+
already-correct rows with that refresh artifact would be a regression, not a fix. A
genuine new election/extension for a pipeline fund is always >= the current value, so
it lands. Admin overrides (manually_edited_fields) are respected. Idempotent.

This REPLACES the manual two-script dance (refresh_effective_dates.py +
backfill_competitor_effective_dates.py), which become audit-only. It is wired into
apply_bloomberg_post_steps.py so it runs every refresh automatically.

Usage:
    python scripts/propagate_effective_dates.py            # dry-run (default)
    python scripts/propagate_effective_dates.py --apply    # writes; backs up first
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

# Latest 485-family filing's effective date for a series_id. 497* are post-effective
# prospectus updates and are deliberately excluded (they don't set the effective date).
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
    if not manually_edited_fields:
        return False
    try:
        parsed = json.loads(manually_edited_fields)
        if isinstance(parsed, list):
            return "estimated_effective_date" in parsed
    except (ValueError, TypeError):
        pass
    return "estimated_effective_date" in manually_edited_fields


def _latest(conn: sqlite3.Connection, series_id: str) -> str | None:
    hit = conn.execute(LATEST_485_EFF, (series_id,)).fetchone()
    return hit[0] if hit and hit[0] else None


def propagate(conn: sqlite3.Connection, apply: bool) -> dict:
    stats = {"fs_changed": 0, "rp_changed": 0, "rp_skipped_override": 0}

    # --- fund_status (competitor + REX rows the report reads) ---
    fs_rows = conn.execute(
        "SELECT id, ticker, fund_name, series_id, effective_date FROM fund_status "
        "WHERE series_id IS NOT NULL AND TRIM(series_id) <> ''"
    ).fetchall()
    for rid, ticker, fund_name, series_id, cur in fs_rows:
        new = _latest(conn, series_id)
        # ADR 0014 dead-letter route: FILL a NULL/blank target only. fund_status
        # spans the entire 40-Act universe (215k mutual-fund share classes) whose
        # existing date is the SEC-feed / header effective date; the latest 485 is
        # often just the annual prospectus-refresh, so overwriting a populated row
        # would mass-mutate dates that are already correct. A populated row is left
        # to the header feed / reconciler; only the genuine blanks are filled here.
        if not new:
            continue
        cur_s = str(cur or "").strip()
        if cur_s:
            continue
        stats["fs_changed"] += 1
        if stats["fs_changed"] <= 15:
            print(f"  [fund_status]  {(ticker or (fund_name or '')[:34]):36s} {cur or 'NULL'} -> {new}")
        if apply:
            conn.execute(
                "UPDATE fund_status SET effective_date = ?, "
                "effective_date_confidence = COALESCE(effective_date_confidence, 'PROPAGATED'), "
                "updated_at = ? WHERE id = ?",
                (new, datetime.utcnow().isoformat(), rid),
            )

    # --- rex_products (REX product cache) ---
    rp_rows = conn.execute(
        "SELECT id, ticker, name, series_id, estimated_effective_date, manually_edited_fields "
        "FROM rex_products WHERE series_id IS NOT NULL AND TRIM(series_id) <> ''"
    ).fetchall()
    for rid, ticker, name, series_id, cur, mef in rp_rows:
        new = _latest(conn, series_id)
        if not new:
            continue
        cur_s = str(cur or "").strip()
        if cur_s:
            continue
        if _is_overridden(mef):
            stats["rp_skipped_override"] += 1
            continue
        stats["rp_changed"] += 1
        if stats["rp_changed"] <= 15:
            print(f"  [rex_products] {(ticker or (name or '')[:34]):36s} {cur or 'NULL'} -> {new}")
        if apply:
            conn.execute(
                "UPDATE rex_products SET estimated_effective_date = ?, updated_at = ? WHERE id = ?",
                (new, datetime.utcnow().isoformat(), rid),
            )

    if apply:
        conn.commit()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--apply", action="store_true",
                    help="Write the propagated dates (default: dry-run).")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    if args.apply:
        BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
        backup = BACKUPS_DIR / f"etp_tracker_{datetime.now():%Y%m%dT%H%M%S}_pre_eff_propagate.db"
        shutil.copy2(db_path, backup)
        print(f"Backup: {backup}")

    conn = sqlite3.connect(str(db_path))
    try:
        stats = propagate(conn, args.apply)
    finally:
        conn.close()

    verb = "propagated" if args.apply else "would propagate"
    print(f"\n{'APPLIED' if args.apply else 'DRY-RUN'}: "
          f"fund_status {verb} {stats['fs_changed']}; "
          f"rex_products {verb} {stats['rp_changed']} "
          f"({stats['rp_skipped_override']} skipped, admin override).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
