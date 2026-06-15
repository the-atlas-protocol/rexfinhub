"""Daily history snapshot — append-only, every tracked field for every fund, every day.

Plan WS-C (2026-06-15): mkt_master_data is FULL-SNAPSHOT-REPLACED on every Bloomberg
sync (market/db_writer.py), so daily AUM / flow / price / status / classification
history is otherwise LOST between syncs. This captures one row per fund per day into
an append-only `mkt_daily_snapshot` table so "how did this number change over time"
is answerable for every field, forever.

Idempotent per (snapshot_date, ticker): the last run of a given day wins, so wiring
this as a post-step after each sync keeps end-of-day truth. Runs standalone today as
the no-regret first capture; the Phase-1 wiring adds it to the Bloomberg chain + a
nightly archive copy to D:.

Usage:
    python scripts/snapshot_daily.py            # capture today (ET)
    python scripts/snapshot_daily.py --date 2026-06-15
    python scripts/snapshot_daily.py --db data/etp_tracker.db --dry-run
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_TABLE = "mkt_master_data"
SNAPSHOT_TABLE = "mkt_daily_snapshot"


def _et_today() -> str:
    """Market-local (ET) calendar date as YYYY-MM-DD, matching the backup stamp."""
    return (datetime.now(timezone.utc) - timedelta(hours=4)).strftime("%Y-%m-%d")


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]


def ensure_table(conn: sqlite3.Connection) -> None:
    """Create mkt_daily_snapshot mirroring mkt_master_data + a snapshot_date key."""
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if SNAPSHOT_TABLE in existing:
        return
    src_cols = _columns(conn, SOURCE_TABLE)
    if "id" in src_cols:
        src_cols.remove("id")  # source PK is meaningless across days
    col_defs = ",\n  ".join(f'"{c}"' for c in src_cols)
    conn.execute(
        f'CREATE TABLE {SNAPSHOT_TABLE} (\n'
        f'  snapshot_date TEXT NOT NULL,\n  {col_defs},\n'
        f'  PRIMARY KEY (snapshot_date, ticker)\n)'
    )
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{SNAPSHOT_TABLE}_ticker "
        f"ON {SNAPSHOT_TABLE} (ticker, snapshot_date)")


def capture(conn: sqlite3.Connection, snap_date: str, dry_run: bool) -> tuple[int, int]:
    """Capture today's mkt_master_data into the snapshot. Returns (rows, replaced)."""
    ensure_table(conn)
    snap_cols = [c for c in _columns(conn, SNAPSHOT_TABLE) if c != "snapshot_date"]
    src_cols = set(_columns(conn, SOURCE_TABLE))
    # only copy columns present in BOTH (schema-drift tolerant)
    cols = [c for c in snap_cols if c in src_cols]
    src_total = conn.execute(f"SELECT COUNT(*) FROM {SOURCE_TABLE}").fetchone()[0]
    already = conn.execute(
        f"SELECT COUNT(*) FROM {SNAPSHOT_TABLE} WHERE snapshot_date=?",
        (snap_date,)).fetchone()[0]
    if dry_run:
        return src_total, already

    # idempotent: the last sync of a day wins
    conn.execute(f"DELETE FROM {SNAPSHOT_TABLE} WHERE snapshot_date=?", (snap_date,))
    col_list = ", ".join(f'"{c}"' for c in cols)
    conn.execute(
        f'INSERT INTO {SNAPSHOT_TABLE} (snapshot_date, {col_list}) '
        f'SELECT ?, {col_list} FROM {SOURCE_TABLE} '
        f'WHERE ticker IS NOT NULL AND TRIM(ticker) <> ""',
        (snap_date,))
    conn.commit()
    got = conn.execute(
        f"SELECT COUNT(*) FROM {SNAPSHOT_TABLE} WHERE snapshot_date=?",
        (snap_date,)).fetchone()[0]
    return got, already


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(PROJECT_ROOT / "data" / "etp_tracker.db"))
    ap.add_argument("--date", default=None, help="snapshot date YYYY-MM-DD (default: today ET)")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    snap_date = args.date or _et_today()
    conn = sqlite3.connect(args.db)
    try:
        rows, already = capture(conn, snap_date, args.dry_run)
    finally:
        conn.close()

    mode = "DRY-RUN" if args.dry_run else "captured"
    note = f" (replaced {already} existing)" if already and not args.dry_run else ""
    print(f"mkt_daily_snapshot {mode}: {rows} rows for {snap_date}{note}")
    # distinct days held, for visibility
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    try:
        days = conn.execute(
            f"SELECT COUNT(DISTINCT snapshot_date) FROM {SNAPSHOT_TABLE}").fetchone()[0]
        span = conn.execute(
            f"SELECT MIN(snapshot_date), MAX(snapshot_date) FROM {SNAPSHOT_TABLE}").fetchone()
        print(f"  history held: {days} day(s), {span[0]} .. {span[1]}")
    except sqlite3.OperationalError:
        pass
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
