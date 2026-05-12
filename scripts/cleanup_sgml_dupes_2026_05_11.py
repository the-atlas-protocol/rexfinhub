"""Wave 4 T2 cleanup — remove SGML LABEL-WINDOW poison ticker rows.

Context
-------
Stage 1 SEC ingestion audit (preflight `audit_ticker_dupes_recent`) flags
``(registrant, ticker)`` pairs in ``fund_extractions`` that span more than
one ``series_name`` in the last 24h of filings. R3 fixed forward by
tightening the body extractor regex; this script cleans the EXISTING
contamination one time.

Investigation 2026-05-11
------------------------
With the latest ingestion at filing_date=2026-05-04, the 24h preflight
window contains exactly ONE bleed pair::

    (registrant='AQR Funds', ticker='CLASS')  -> 3 series, 7 rows

All 7 rows have:
  * ``class_symbol == 'CLASS'`` — the SGML column header, not a real ticker
  * ``extracted_from == 'SGML-TXT|LABEL-WINDOW'`` — the regex source R3 fixed
  * a sibling row in the same filing with the same ``class_contract_id`` and
    a real ticker pulled by ``SGML-TXT`` alone (e.g. QCERX, QICLX) — only
    for the R6 class. Class I / Class N rows have no sibling, so deletion
    leaves them with no extracted ticker, which is correct (SGML genuinely
    did not carry one for those classes in these 497K filings).

The earlier 2026-05-01 batch (filings 626675..626690, 39 rows) shows the
same pattern but is OUTSIDE the 24h preflight window, so it does not block
preflight today. We still clean it (same poison, same root cause) so the
DB is internally consistent for whichever ingestion window is queried next.

Scope
-----
DELETE FROM fund_extractions WHERE id IN (...) — explicit ID list only.
Restricted to ``registrant='AQR Funds' AND class_symbol='CLASS'``. No other
table is touched. Audit log of every deleted row (full content + timestamp)
is written to ``temp/cleanup_sgml_dupes_2026_05_11_<ts>.json`` so the
operation can be reversed by re-INSERTing from the log.

Usage
-----
Dry run (default — prints the to-delete list, makes no changes)::

    python scripts/cleanup_sgml_dupes_2026_05_11.py
    python scripts/cleanup_sgml_dupes_2026_05_11.py --dry-run

Apply (writes to ``data/etp_tracker.db`` after backing it up)::

    python scripts/cleanup_sgml_dupes_2026_05_11.py --apply

Limit to the strict 24h preflight window (skip the 2026-05-01 batch)::

    python scripts/cleanup_sgml_dupes_2026_05_11.py --apply --preflight-window-only

Rollback
--------
The audit JSON contains every deleted row's full column tuple. To restore::

    python scripts/cleanup_sgml_dupes_2026_05_11.py --rollback temp/<log>.json
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import warnings
from datetime import datetime
from pathlib import Path

warnings.filterwarnings(
    "ignore",
    message=r"datetime\.datetime\.utcnow\(\) is deprecated.*",
    category=DeprecationWarning,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"

# Selection criteria — narrow, explicit, audit-driven.
TARGET_REGISTRANT = "AQR Funds"
TARGET_CLASS_SYMBOL = "CLASS"


def _utc_now() -> str:
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")


def _ts_for_filename() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _select_targets(con: sqlite3.Connection, preflight_window_only: bool) -> list[dict]:
    """Return every fund_extractions row that matches the poison signature.

    With ``preflight_window_only=True`` restrict to filings in the last 24h
    relative to the most recent filing_date in the DB (mirrors the
    preflight ``audit_ticker_dupes_recent`` window).
    """
    cur = con.cursor()
    cur.execute("SELECT MAX(filing_date) FROM filings")
    max_filing_date = cur.fetchone()[0]
    if max_filing_date is None:
        return []

    where_window = ""
    params: tuple = (TARGET_REGISTRANT, TARGET_CLASS_SYMBOL)
    if preflight_window_only:
        where_window = " AND f.filing_date >= date(?, '-1 day')"
        params = (TARGET_REGISTRANT, TARGET_CLASS_SYMBOL, max_filing_date)

    sql = f"""
        SELECT fe.id, fe.filing_id, fe.series_id, fe.series_name,
               fe.class_contract_id, fe.class_contract_name, fe.class_symbol,
               fe.extracted_from, fe.effective_date, fe.effective_date_confidence,
               fe.delaying_amendment, fe.prospectus_name, fe.created_at,
               f.accession_number, f.form, f.filing_date, f.registrant
        FROM fund_extractions fe
        JOIN filings f ON f.id = fe.filing_id
        WHERE f.registrant = ?
          AND fe.class_symbol = ?
          {where_window}
        ORDER BY fe.id
    """
    cur.execute(sql, params)
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


def _audit_count_after(con: sqlite3.Connection) -> tuple[int, list[dict]]:
    """Re-run the preflight `audit_ticker_dupes_recent` query and return
    (bleed pair count, detail rows).
    """
    from collections import defaultdict

    cur = con.cursor()
    cur.execute("SELECT MAX(filing_date) FROM filings")
    max_filing_date = cur.fetchone()[0]
    if max_filing_date is None:
        return 0, []

    cur.execute(
        """
        SELECT f.registrant, fe.class_symbol, fe.series_name
        FROM fund_extractions fe
        JOIN filings f ON f.id = fe.filing_id
        WHERE fe.class_symbol IS NOT NULL
          AND fe.class_symbol != ''
          AND f.filing_date >= date(?, '-1 day')
          AND f.registrant IS NOT NULL
        """,
        (max_filing_date,),
    )
    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    for reg, sym, ser in cur.fetchall():
        groups[(reg, sym)].add(ser)
    bleed = [(k, v) for k, v in groups.items() if len(v) > 1]
    detail = [
        {"registrant": k[0], "ticker": k[1], "series_count": len(v),
         "series_sample": sorted(v)[:5]}
        for k, v in bleed
    ]
    return len(bleed), detail


def _backup_db() -> Path:
    bak_path = DB_PATH.with_name(f"etp_tracker.db.pre-T2-{_ts_for_filename()}.bak")
    shutil.copy2(DB_PATH, bak_path)
    return bak_path


def _write_audit_log(entries: list[dict], action: str, extra: dict) -> Path:
    log_dir = PROJECT_ROOT / "temp"
    log_dir.mkdir(parents=True, exist_ok=True)
    path = log_dir / f"cleanup_sgml_dupes_2026_05_11_{_ts_for_filename()}.json"
    payload = {
        "script": "cleanup_sgml_dupes_2026_05_11.py",
        "action": action,
        "executed_at_utc": _utc_now(),
        "target_registrant": TARGET_REGISTRANT,
        "target_class_symbol": TARGET_CLASS_SYMBOL,
        "deleted_row_count": len(entries),
        "deleted_rows": entries,
        **extra,
    }
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return path


def _do_delete(con: sqlite3.Connection, ids: list[int]) -> int:
    if not ids:
        return 0
    placeholders = ",".join("?" for _ in ids)
    cur = con.cursor()
    cur.execute(f"DELETE FROM fund_extractions WHERE id IN ({placeholders})", ids)
    return cur.rowcount


def cmd_run(args: argparse.Namespace) -> int:
    if not DB_PATH.exists():
        print(f"FATAL: DB not found at {DB_PATH}")
        return 2

    con = sqlite3.connect(str(DB_PATH))
    try:
        targets = _select_targets(con, preflight_window_only=args.preflight_window_only)
        before_count, before_detail = _audit_count_after(con)

        print(f"DB: {DB_PATH}")
        print(f"Audit BEFORE — bleed pairs in 24h preflight window: {before_count}")
        for d in before_detail:
            print(f"  {d!r}")
        print()
        print(f"Selection criteria: registrant={TARGET_REGISTRANT!r} AND class_symbol={TARGET_CLASS_SYMBOL!r}"
              + (" (preflight 24h window only)" if args.preflight_window_only else " (all time)"))
        print(f"Rows matched: {len(targets)}")
        for r in targets:
            print(f"  fe.id={r['id']} filing_id={r['filing_id']} series_id={r['series_id']!r} "
                  f"series_name={r['series_name']!r} class={r['class_contract_name']!r} "
                  f"extracted_from={r['extracted_from']!r} accession={r['accession_number']} "
                  f"filing_date={r['filing_date']}")

        if not targets:
            print("\nNo targets to delete. Exiting.")
            return 0

        if args.dry_run or not args.apply:
            print(f"\n[DRY RUN] Would DELETE {len(targets)} rows. Re-run with --apply to commit.")
            return 0

        # APPLY
        bak_path = _backup_db()
        print(f"\nBacked up DB to: {bak_path}")

        ids = [int(r["id"]) for r in targets]
        try:
            con.execute("BEGIN")
            deleted = _do_delete(con, ids)
            con.commit()
        except Exception as e:
            con.rollback()
            print(f"FATAL: delete failed and rolled back: {e}")
            return 3

        log_path = _write_audit_log(
            entries=targets,
            action="DELETE",
            extra={
                "db_backup_path": str(bak_path),
                "audit_before_bleed_count": before_count,
                "audit_before_detail": before_detail,
                "preflight_window_only": bool(args.preflight_window_only),
            },
        )
        print(f"DELETE OK — removed {deleted} rows.")
        print(f"Audit log: {log_path}")

        after_count, after_detail = _audit_count_after(con)
        print(f"\nAudit AFTER — bleed pairs in 24h preflight window: {after_count}")
        for d in after_detail:
            print(f"  {d!r}")

        # Append the after-state to the log for completeness.
        log_payload = json.loads(log_path.read_text(encoding="utf-8"))
        log_payload["audit_after_bleed_count"] = after_count
        log_payload["audit_after_detail"] = after_detail
        log_path.write_text(json.dumps(log_payload, indent=2, default=str), encoding="utf-8")

        return 0 if after_count == 0 else 1
    finally:
        con.close()


def cmd_rollback(args: argparse.Namespace) -> int:
    log_path = Path(args.rollback)
    if not log_path.exists():
        print(f"FATAL: log file not found: {log_path}")
        return 2
    payload = json.loads(log_path.read_text(encoding="utf-8"))
    rows = payload.get("deleted_rows") or []
    if not rows:
        print("No deleted_rows in log; nothing to restore.")
        return 0

    con = sqlite3.connect(str(DB_PATH))
    try:
        cur = con.cursor()
        cur.execute("PRAGMA table_info(fund_extractions)")
        valid_cols = [c[1] for c in cur.fetchall()]

        # Only restore columns that still exist on the current table.
        restore_cols = [c for c in [
            "id", "filing_id", "series_id", "series_name",
            "class_contract_id", "class_contract_name", "class_symbol",
            "extracted_from", "effective_date", "effective_date_confidence",
            "delaying_amendment", "prospectus_name", "created_at",
        ] if c in valid_cols]

        placeholders = ",".join("?" for _ in restore_cols)
        col_list = ",".join(restore_cols)

        bak_path = DB_PATH.with_name(f"etp_tracker.db.pre-T2-rollback-{_ts_for_filename()}.bak")
        shutil.copy2(DB_PATH, bak_path)
        print(f"Backed up DB to: {bak_path}")

        inserted = 0
        try:
            con.execute("BEGIN")
            for row in rows:
                values = [row.get(c) for c in restore_cols]
                cur.execute(
                    f"INSERT OR IGNORE INTO fund_extractions ({col_list}) VALUES ({placeholders})",
                    values,
                )
                inserted += cur.rowcount
            con.commit()
        except Exception as e:
            con.rollback()
            print(f"FATAL: rollback failed and was reverted: {e}")
            return 3

        print(f"Restored {inserted} rows from {log_path}")
        return 0
    finally:
        con.close()


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--apply", action="store_true",
                   help="Actually delete the rows (default is dry-run)")
    p.add_argument("--dry-run", action="store_true", default=False,
                   help="Print the to-delete list and exit (default behavior)")
    p.add_argument("--preflight-window-only", action="store_true", default=False,
                   help="Restrict to filings in the last 24h of preflight window")
    p.add_argument("--rollback", metavar="LOG_PATH",
                   help="Re-insert deleted rows from a previous audit log")
    args = p.parse_args()

    if args.rollback:
        return cmd_rollback(args)
    return cmd_run(args)


if __name__ == "__main__":
    raise SystemExit(main())
