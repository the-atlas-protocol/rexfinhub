raise SystemExit("RETIRED 2026-06-16 - quarantined to archive/retired-2026-06-16/, do not run. 3-gate proven 0 live refs; pending final sweep.")
"""Phase 7 Part B (ADR 0010): consolidate 14 state files into 3 tables.

Creates schema + initial backfill from existing files:
    system_flags    <- .send_enabled, .send_paused, .preflight_maintenance,
                       .autogo_on_warn, .auto_demote_vanished (5 boolean flag files)
    preflight_run   <- .preflight_token, .preflight_result.json,
                       .preflight_decision.json (3 preflight files;
                       most recent merged into one row, history reconstructed
                       from git history if needed)
    system_event    <- .pipeline_stages.jsonl, .gate_state_log.jsonl,
                       .intraday_refresh.log, .poll_fresh_filings.log,
                       .cboe_rotated_at, .duplicate_tickers_audit.json
                       (parsed line-by-line where possible)

Dual-read window: files remain authoritative until Phase 7 Part B Stage 2+
when read paths flip to read from these tables. This is just the schema +
initial backfill.

Idempotent. Pre-existing rows are preserved.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"
DATA_DIR = PROJECT_ROOT / "data"
CONFIG_DIR = PROJECT_ROOT / "config"


DDL_STATEMENTS = [
    """CREATE TABLE IF NOT EXISTS system_flags (
        flag_name   TEXT PRIMARY KEY,
        is_set      INTEGER NOT NULL,
        set_at      TIMESTAMP NOT NULL,
        set_by      TEXT,
        notes       TEXT
    )""",
    """CREATE TABLE IF NOT EXISTS preflight_run (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        ran_at          TIMESTAMP NOT NULL,
        result_status   TEXT NOT NULL,
        decision        TEXT,
        decision_token  TEXT,
        result_json     TEXT,
        decision_json   TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_preflight_run_ran_at ON preflight_run (ran_at DESC)",
    """CREATE TABLE IF NOT EXISTS system_event (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        event_type  TEXT NOT NULL,
        event_at    TIMESTAMP NOT NULL,
        details     TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS idx_system_event_at ON system_event (event_type, event_at DESC)",
]


# Source files to ingest into system_flags
FLAG_FILES = [
    ("send_enabled",          CONFIG_DIR / ".send_enabled",
     lambda p: p.read_text(encoding="utf-8").strip().lower() == "true" if p.exists() else False),
    ("send_paused",           DATA_DIR / ".send_paused",      lambda p: p.exists()),
    ("preflight_maintenance", DATA_DIR / ".preflight_maintenance", lambda p: p.exists()),
    ("autogo_on_warn",        DATA_DIR / ".autogo_on_warn",   lambda p: p.exists()),
    ("auto_demote_vanished",  DATA_DIR / ".auto_demote_vanished", lambda p: p.exists()),
]


# Source files for system_event
EVENT_SOURCES = [
    ("cboe_rotated", DATA_DIR / ".cboe_rotated_at",
     lambda p: [{"event_at": p.read_text(encoding="utf-8").strip(),
                 "details": json.dumps({})}] if p.exists() else []),
    ("intraday_refresh", DATA_DIR / ".intraday_refresh.log",
     lambda p: [_parse_log_line(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
               if p.exists() else []),
    ("fresh_poller", DATA_DIR / ".poll_fresh_filings.log",
     lambda p: [_parse_log_line(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]
               if p.exists() else []),
]


def _parse_log_line(line: str) -> dict:
    """Parse '2026-05-19T18:20:39  STATUS  details...' into event dict."""
    parts = line.split(None, 2)
    if len(parts) >= 1:
        return {"event_at": parts[0], "details": json.dumps({"line": line[19:].strip()})}
    return {"event_at": datetime.utcnow().isoformat(), "details": json.dumps({"line": line})}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        # Schema
        for stmt in DDL_STATEMENTS:
            conn.execute(stmt)

        now = datetime.utcnow().isoformat()
        stats = {"flags_inserted": 0, "flags_skipped": 0,
                 "events_inserted": 0, "events_skipped": 0,
                 "preflight_runs_inserted": 0}

        # --- system_flags backfill ---
        for flag_name, path, evaluator in FLAG_FILES:
            existing = conn.execute(
                "SELECT 1 FROM system_flags WHERE flag_name = ?", (flag_name,)
            ).fetchone()
            if existing:
                stats["flags_skipped"] += 1
                continue
            try:
                is_set = bool(evaluator(path))
            except Exception:
                is_set = False
            if not args.dry_run:
                conn.execute(
                    "INSERT INTO system_flags (flag_name, is_set, set_at, set_by, notes) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (flag_name, 1 if is_set else 0, now, "backfill_2026-05-19",
                     f"backfill from {path.name}"),
                )
            stats["flags_inserted"] += 1

        # --- preflight_run backfill (latest only) ---
        result_path = DATA_DIR / ".preflight_result.json"
        decision_path = DATA_DIR / ".preflight_decision.json"
        token_path = DATA_DIR / ".preflight_token"
        if result_path.exists():
            existing = conn.execute(
                "SELECT 1 FROM preflight_run LIMIT 1"
            ).fetchone()
            if not existing:
                try:
                    result_json = json.loads(result_path.read_text(encoding="utf-8"))
                    decision_json = (json.loads(decision_path.read_text(encoding="utf-8"))
                                     if decision_path.exists() else None)
                    token = None
                    if token_path.exists():
                        try:
                            token_data = json.loads(token_path.read_text(encoding="utf-8"))
                            token = token_data.get("token")
                        except (json.JSONDecodeError, AttributeError):
                            token = token_path.read_text(encoding="utf-8").strip()
                    if not args.dry_run:
                        conn.execute(
                            "INSERT INTO preflight_run "
                            "(ran_at, result_status, decision, decision_token, "
                            " result_json, decision_json) "
                            "VALUES (?, ?, ?, ?, ?, ?)",
                            (
                                result_json.get("timestamp", now),
                                result_json.get("overall_status", "unknown"),
                                (decision_json or {}).get("action"),
                                token,
                                json.dumps(result_json),
                                json.dumps(decision_json) if decision_json else None,
                            ),
                        )
                    stats["preflight_runs_inserted"] += 1
                except (json.JSONDecodeError, AttributeError) as e:
                    print(f"WARN: failed to parse preflight files: {e}")

        # --- system_event backfill (sampling: max 100 per source to keep size sane) ---
        for event_type, path, parser in EVENT_SOURCES:
            existing = conn.execute(
                "SELECT 1 FROM system_event WHERE event_type = ? LIMIT 1",
                (event_type,),
            ).fetchone()
            if existing:
                stats["events_skipped"] += 1
                continue
            try:
                events = parser(path)[-100:]  # last 100 only
            except Exception:
                events = []
            if not args.dry_run:
                for ev in events:
                    conn.execute(
                        "INSERT INTO system_event (event_type, event_at, details) "
                        "VALUES (?, ?, ?)",
                        (event_type, ev["event_at"], ev.get("details")),
                    )
            stats["events_inserted"] += len(events)

        if not args.dry_run:
            conn.commit()

        print(f"DB: {db_path}")
        print(f"Phase 7 Part B — state-table migration")
        for k, v in stats.items():
            print(f"  {k:25s} {v}")

        # Verification
        flags = conn.execute("SELECT COUNT(*) FROM system_flags").fetchone()[0]
        runs = conn.execute("SELECT COUNT(*) FROM preflight_run").fetchone()[0]
        events = conn.execute("SELECT COUNT(*) FROM system_event").fetchone()[0]
        print(f"\nTotals after:")
        print(f"  system_flags:  {flags}")
        print(f"  preflight_run: {runs}")
        print(f"  system_event:  {events}")

        if args.dry_run:
            print("\nDRY-RUN — no writes")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
