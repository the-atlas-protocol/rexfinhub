"""Fix 27 date inversions on rex_products (audit follow-up, 2026-05-12).

Background
----------
A query against `rex_products` returns 27 rows where date ordering is violated:

  Group A (20 rows, REX 2X / REX Short single-stock series on Tidal Trust II):
    initial_filing_date > estimated_effective_date

  Group B (7 rows, OBTC / NVII / COII / MSII / TSII / XRPK / SOLX):
    estimated_effective_date > target_listing_date

Group A is a back-fill artifact: `initial_filing_date` was overwritten with
the date of the most-recent 485BXT (delaying amendment) — see
`fund_status.latest_filing_date` for these series. The SEC-recognized series
registration date (`fund_status.effective_date = 2022-12-05`) is the true
initial.

Group B is real: REX listed on amendment before the original 485APOS
effectiveness rolled in. We flag and leave alone.

What this script does
---------------------
1. For each Group A row:
   a. Find the earliest 485APOS filing for the row's trust (joined via
      `rex_products.trust` -> `trusts.name` -> `filings.trust_id`).
   b. If MIN(485APOS).filing_date <= estimated_effective_date, use it as the
      new initial_filing_date.
   c. Otherwise (the case in our DB today — our filings table only goes back
      to 2024 for Tidal Trust II, but the series registered with SEC in
      2022-12-05 via an earlier N-1A we never indexed), fall back to
      `estimated_effective_date` so the inversion resolves cleanly. The
      fallback is logged with reason="no_485apos_predates_effective".
   d. If no 485APOS at all is found for the trust, leave the row alone and
      print a warning.

2. For each Group B row: do not modify. Print as informational.

3. Every change (and every "would change") is logged to
   `classification_audit_log` with `dry_run` flagged appropriately.

4. Before any --apply run, take a SQLite backup via the Python sqlite3
   `.backup` API (works on Windows where the `sqlite3` CLI may be absent).

Usage
-----
    # safe default — prints what would change, no writes
    python scripts/fix_rex_filing_dates_2026-05-12.py

    # idempotent on subsequent runs (already-fixed rows are skipped)
    python scripts/fix_rex_filing_dates_2026-05-12.py --apply

    # override DB path (e.g. when running from a worktree)
    python scripts/fix_rex_filing_dates_2026-05-12.py --db-path /home/jarvis/rexfinhub/data/etp_tracker.db --apply

VPS invocation
--------------
    ssh jarvis@46.224.126.196 "cd /home/jarvis/rexfinhub && /home/jarvis/venv/bin/python scripts/fix_rex_filing_dates_2026-05-12.py --apply"
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# ----- constants -------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"
SWEEP_RUN_ID = f"fix_date_inversions_{datetime.now().strftime('%Y%m%dT%H%M%S')}"
CONFIRMATION_PHRASE = "I AGREE"

log = logging.getLogger("fix_date_inversions")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ----- helpers ---------------------------------------------------------------

def fetch_group_a(cur: sqlite3.Cursor) -> list[sqlite3.Row]:
    """Group A: initial_filing_date > estimated_effective_date."""
    cur.execute(
        """
        SELECT id, ticker, trust, series_id,
               initial_filing_date, estimated_effective_date, target_listing_date
        FROM rex_products
        WHERE initial_filing_date IS NOT NULL
          AND estimated_effective_date IS NOT NULL
          AND initial_filing_date > estimated_effective_date
        ORDER BY id
        """
    )
    return cur.fetchall()


def fetch_group_b(cur: sqlite3.Cursor) -> list[sqlite3.Row]:
    """Group B: estimated_effective_date > target_listing_date."""
    cur.execute(
        """
        SELECT id, ticker, trust, series_id,
               initial_filing_date, estimated_effective_date, target_listing_date
        FROM rex_products
        WHERE estimated_effective_date IS NOT NULL
          AND target_listing_date IS NOT NULL
          AND estimated_effective_date > target_listing_date
        ORDER BY id
        """
    )
    return cur.fetchall()


def find_trust_id_for_product(cur: sqlite3.Cursor, trust_name: str) -> int | None:
    """Resolve `rex_products.trust` (free text) to `trusts.id` via exact name match."""
    if not trust_name:
        return None
    cur.execute("SELECT id FROM trusts WHERE name = ? LIMIT 1", (trust_name,))
    row = cur.fetchone()
    return row[0] if row else None


def earliest_485apos(cur: sqlite3.Cursor, trust_id: int) -> tuple[str, str] | None:
    """Return (accession_number, filing_date) of the earliest 485APOS for the trust."""
    cur.execute(
        """
        SELECT accession_number, filing_date
        FROM filings
        WHERE trust_id = ? AND form = '485APOS'
        ORDER BY filing_date ASC, accession_number ASC
        LIMIT 1
        """,
        (trust_id,),
    )
    row = cur.fetchone()
    return (row[0], row[1]) if row else None


def write_audit(
    cur: sqlite3.Cursor,
    *,
    ticker: str | None,
    old_value: str | None,
    new_value: str | None,
    reason: str,
    source: str,
    confidence: str,
    dry_run: bool,
    extra: dict | None = None,
) -> None:
    """Write a single row to classification_audit_log.

    We piggyback on the existing audit table (which is keyed by ticker and
    column_name). For Group A rows that lack a ticker we use the series_id
    string instead so the row is still identifiable.
    """
    full_reason = reason
    if extra:
        full_reason = f"{reason} | {json.dumps(extra, default=str)}"
    cur.execute(
        """
        INSERT INTO classification_audit_log
            (sweep_run_id, ticker, column_name, old_value, new_value,
             source, confidence, reason, dry_run, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            SWEEP_RUN_ID,
            ticker or "",
            "initial_filing_date",
            old_value,
            new_value,
            source,
            confidence,
            full_reason,
            1 if dry_run else 0,
            datetime.now().isoformat(timespec="seconds"),
        ),
    )


def backup_db(db_path: Path) -> Path:
    """Take a SQLite backup using the Python .backup API (works without sqlite3 CLI).

    Backup is written to `<db_dir>/backups/` so it lives next to the source DB
    regardless of where the script is invoked from (project root, worktree, VPS).
    """
    backup_dir = db_path.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%dT%H%M%S")
    dest = backup_dir / f"etp_tracker.db.pre-date-fix-{ts}.bak"
    src = sqlite3.connect(str(db_path))
    try:
        dst = sqlite3.connect(str(dest))
        with dst:
            src.backup(dst)
        dst.close()
    finally:
        src.close()
    return dest


# ----- main ------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without this flag, the script is dry-run only.",
    )
    parser.add_argument(
        "--db-path",
        default=str(DEFAULT_DB),
        help=f"SQLite DB path (default: {DEFAULT_DB}).",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the pre-apply backup (NOT RECOMMENDED).",
    )
    args = parser.parse_args()

    db_path = Path(args.db_path)
    if not db_path.exists():
        log.error("DB not found at %s", db_path)
        return 1

    log.info("DB: %s", db_path)
    log.info("Mode: %s", "APPLY" if args.apply else "DRY-RUN")
    log.info("Sweep run id: %s", SWEEP_RUN_ID)

    if args.apply:
        print()
        print("=" * 70)
        print("  APPLY MODE: this will modify rex_products and write audit rows.")
        print(f"  DB: {db_path}")
        print(f'  Type "{CONFIRMATION_PHRASE}" to proceed:')
        print("=" * 70)
        try:
            response = input("> ").strip()
        except EOFError:
            log.error("No stdin available; refusing to apply without confirmation.")
            return 1
        if response != CONFIRMATION_PHRASE:
            log.error("Confirmation did not match. Aborting.")
            return 1

        if not args.no_backup:
            backup_path = backup_db(db_path)
            log.info("Backup written: %s", backup_path)

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    group_a = fetch_group_a(cur)
    group_b_all = fetch_group_b(cur)
    group_a_ids = {r["id"] for r in group_a}
    # Group B = inversions that are NOT already in Group A
    group_b = [r for r in group_b_all if r["id"] not in group_a_ids]

    # --- Group A ------------------------------------------------------------
    print()
    print("=" * 70)
    print(f"GROUP A — {len(group_a)} rows with initial_filing_date > estimated_effective_date")
    print("=" * 70)

    fixes_applied = 0
    fixes_skipped = 0
    for r in group_a:
        rid = r["id"]
        ticker = r["ticker"]
        trust = r["trust"]
        series_id = r["series_id"]
        old_initial = r["initial_filing_date"]
        eff = r["estimated_effective_date"]

        trust_id = find_trust_id_for_product(cur, trust)
        if trust_id is None:
            print(
                f"  id={rid:<4} series={series_id} trust={trust!r} -> SKIP "
                f"(no matching trusts.name)"
            )
            write_audit(
                cur,
                ticker=ticker or series_id,
                old_value=old_initial,
                new_value=None,
                reason="skip_no_trust_match",
                source="fix_date_inversions",
                confidence="HIGH",
                dry_run=not args.apply,
                extra={"trust_name": trust, "series_id": series_id},
            )
            fixes_skipped += 1
            continue

        earliest = earliest_485apos(cur, trust_id)
        if earliest is None:
            print(
                f"  id={rid:<4} series={series_id} -> SKIP "
                f"(no 485APOS in filings table for trust_id={trust_id})"
            )
            write_audit(
                cur,
                ticker=ticker or series_id,
                old_value=old_initial,
                new_value=None,
                reason="skip_no_485apos_found",
                source="fix_date_inversions",
                confidence="HIGH",
                dry_run=not args.apply,
                extra={"trust_id": trust_id, "series_id": series_id},
            )
            fixes_skipped += 1
            continue

        acc, earliest_date = earliest
        if earliest_date <= eff:
            new_initial = earliest_date
            source_label = f"filings.485APOS({acc})"
            reason = "earliest_485apos_predates_effective"
        else:
            # MIN(485APOS) is itself later than estimated_effective_date.
            # This happens when the series was registered before our filings
            # table coverage starts (Tidal Trust II is indexed from 2024,
            # but these series registered 2022-12-05 via earlier filings).
            # The honest fix is to use estimated_effective_date itself —
            # that IS the SEC-recognized series registration date.
            new_initial = eff
            source_label = f"fallback_to_effective_date(min_485apos={earliest_date})"
            reason = "no_485apos_predates_effective"

        if new_initial == old_initial:
            # Already fixed (idempotent)
            print(f"  id={rid:<4} series={series_id} already {old_initial} (no-op)")
            fixes_skipped += 1
            continue

        print(
            f"  id={rid:<4} series={series_id} ticker={ticker or '-':<6} "
            f"{old_initial} -> {new_initial}  [{reason}]"
        )

        write_audit(
            cur,
            ticker=ticker or series_id,
            old_value=old_initial,
            new_value=new_initial,
            reason=reason,
            source=source_label,
            confidence="HIGH",
            dry_run=not args.apply,
            extra={
                "rex_products_id": rid,
                "trust_id": trust_id,
                "estimated_effective_date": eff,
                "earliest_485apos_date": earliest_date,
                "earliest_485apos_accession": acc,
            },
        )

        if args.apply:
            cur.execute(
                """
                UPDATE rex_products
                SET initial_filing_date = ?, updated_at = ?
                WHERE id = ? AND initial_filing_date = ?
                """,
                (new_initial, datetime.now().isoformat(timespec="seconds"),
                 rid, old_initial),
            )
            if cur.rowcount != 1:
                log.error(
                    "Expected to update 1 row for id=%s, got %s. Rolling back.",
                    rid, cur.rowcount,
                )
                conn.rollback()
                conn.close()
                return 2
        fixes_applied += 1

    # --- Group B ------------------------------------------------------------
    print()
    print("=" * 70)
    print(f"GROUP B — {len(group_b)} rows with estimated_effective_date > target_listing_date")
    print("=" * 70)
    print("  (informational only — these are real cases of listing on amendment)")
    print()
    for r in group_b:
        print(
            f"  id={r['id']:<4} ticker={r['ticker']:<6} trust={r['trust']:<28} "
            f"eff={r['estimated_effective_date']} listing={r['target_listing_date']}"
        )
        write_audit(
            cur,
            ticker=r["ticker"],
            old_value=r["estimated_effective_date"],
            new_value=r["estimated_effective_date"],
            reason="group_b_informational_no_change",
            source="fix_date_inversions",
            confidence="HIGH",
            dry_run=not args.apply,
            extra={
                "rex_products_id": r["id"],
                "target_listing_date": r["target_listing_date"],
                "note": "Real inversion — listing on amendment before original effectiveness. No fix.",
            },
        )

    if args.apply:
        conn.commit()
    conn.close()

    print()
    print("=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  Group A rows considered: {len(group_a)}")
    print(f"  Group A changes:         {fixes_applied}  ({'APPLIED' if args.apply else 'would apply'})")
    print(f"  Group A skipped:         {fixes_skipped}")
    print(f"  Group B informational:   {len(group_b)}  (no change)")
    print(f"  Sweep run id:            {SWEEP_RUN_ID}")
    if not args.apply:
        print()
        print("  This was a DRY RUN. Re-run with --apply to write changes.")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
