"""
Automation Health Review — comprehensive check of all automated systems.

Checks:
  1. Scheduled tasks (Watcher, RapidSync, DailySync)
  2. Data freshness (filings, notes, market, screener)
  3. Drive health (C: cache, D: cold storage)
  4. Render site status
  5. Archive integrity (daily snapshots)
  6. Pipeline components (trust universe, classification, ETN overrides)
  7. Email send log

Usage:
    python scripts/automation_review.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime, date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)


def _check(label: str, ok: bool, detail: str = "") -> dict:
    status = "OK" if ok else "ISSUE"
    icon = "+" if ok else "!"
    line = f"  [{icon}] {label}: {detail}" if detail else f"  [{icon}] {label}"
    print(line)
    return {"label": label, "status": status, "detail": detail}


def run_review() -> dict:
    issues = []
    results = {}
    now = datetime.now()
    today = date.today()

    print(f"=== Automation Health Review ({now.strftime('%Y-%m-%d %H:%M')}) ===")
    print()

    # 1. Scheduled Tasks
    print("[1] SCHEDULED TASKS")
    for task_name in ["ETP_Watcher", "ETP_RapidSync", "ETP_DailySync"]:
        result = subprocess.run(
            ["schtasks", "/query", "/tn", task_name, "/fo", "LIST"],
            capture_output=True, text=True,
        )
        if result.returncode == 0:
            status = next_run = last_run = ""
            for line in result.stdout.strip().split("\n"):
                line = line.strip()
                if line.startswith("Status:"):
                    status = line.split(":", 1)[1].strip()
                elif line.startswith("Next Run Time:"):
                    next_run = line.split(":", 1)[1].strip()
                elif line.startswith("Last Run Time:"):
                    last_run = line.split(":", 1)[1].strip()
            ok = status in ("Ready", "Running")
            r = _check(task_name, ok, f"status={status}, next={next_run}")
            if not ok:
                issues.append(f"{task_name} is {status}")
        else:
            _check(task_name, False, "NOT FOUND in Task Scheduler")
            issues.append(f"{task_name} not scheduled")
    print()

    # 2. Data Freshness
    print("[2] DATA FRESHNESS")
    try:
        from webapp.database import init_db, SessionLocal
        from webapp.models import Trust, Filing, FilingAlert, MktPipelineRun
        from sqlalchemy import select, func

        init_db()
        db = SessionLocal()

        # Fund filings
        latest_filing = db.execute(select(func.max(Filing.filing_date))).scalar()
        filing_age = (today - latest_filing).days if latest_filing else 999
        _check("Fund filings", filing_age <= 1, f"latest={latest_filing}, {filing_age}d ago")
        if filing_age > 1:
            issues.append(f"Fund filings {filing_age} days stale (latest={latest_filing})")

        # Trusts
        trust_count = db.execute(select(func.count(Trust.id)).where(Trust.is_active == True)).scalar()
        _check("Trusts monitored", trust_count > 2000, f"{trust_count:,} active")

        # Watcher alerts
        alerts = db.execute(select(func.count(FilingAlert.id))).scalar()
        _check("Watcher alerts", alerts > 0, f"{alerts} total")

        # Market data
        latest_mkt = db.execute(
            select(MktPipelineRun).order_by(MktPipelineRun.id.desc()).limit(1)
        ).scalar_one_or_none()
        if latest_mkt and latest_mkt.finished_at:
            mkt_age_hours = (now - latest_mkt.finished_at).total_seconds() / 3600
            _check("Market data", mkt_age_hours < 26, f"synced {latest_mkt.finished_at.strftime('%m/%d %H:%M')}, {mkt_age_hours:.0f}h ago")
            if mkt_age_hours > 26:
                issues.append(f"Market data {mkt_age_hours:.0f}h stale")
        else:
            _check("Market data", False, "no pipeline run found")
            issues.append("No market pipeline run found")

        # Structured notes
        notes_db = Path("data/structured_notes.db")
        if notes_db.exists():
            conn = sqlite3.connect(str(notes_db))
            notes_latest = conn.execute("SELECT MAX(filing_date) FROM filings").fetchone()[0]
            notes_total = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
            conn.close()
            notes_age = (today - date.fromisoformat(notes_latest)).days if notes_latest else 999
            _check("Structured notes", notes_age <= 2, f"{notes_total:,} filings, latest={notes_latest}")
            if notes_age > 2:
                issues.append(f"Notes {notes_age} days stale")
        else:
            _check("Structured notes", False, "DB not found")
            issues.append("structured_notes.db missing")

        db.close()
    except Exception as e:
        _check("Database", False, str(e))
        issues.append(f"Database error: {e}")
    print()

    # 3. Drives
    print("[3] DRIVES")
    d_drive = Path("D:/sec-data")
    d_connected = d_drive.exists()
    _check("D: drive", d_connected, "CONNECTED" if d_connected else "NOT CONNECTED")

    c_cache = Path("cache")
    if c_cache.exists():
        c_files = sum(1 for _ in c_cache.rglob("*") if _.is_file())
        _check("C: cache", True, f"{c_files} files (will archive to D: next run)")
    else:
        _check("C: cache", True, "CLEAN (archived)")

    if d_connected:
        d_cache = d_drive / "cache" / "rexfinhub"
        if d_cache.exists():
            d_files = sum(1 for _ in d_cache.rglob("*") if _.is_file())
            d_size = sum(f.stat().st_size for f in d_cache.rglob("*") if f.is_file()) / 1e9
            _check("D: SEC cache", True, f"{d_files:,} files ({d_size:.1f} GB)")
        d_notes = d_drive / "databases" / "structured_notes.db"
        _check("D: Notes DB", d_notes.exists(),
               f"{d_notes.stat().st_size / 1e6:.0f} MB" if d_notes.exists() else "MISSING")
    print()

    # 4. Render
    print("[4] RENDER")
    try:
        import requests
        health = requests.get("https://rex-etp-tracker.onrender.com/health", timeout=10).json()
        _check("Render site", health.get("status") == "ok",
               f"status={health.get('status')}, commit={health.get('commit', '?')[:8]}")
    except Exception as e:
        _check("Render site", False, str(e)[:60])
        issues.append("Render unreachable")
    print()

    # 5. Archives
    print("[5] DAILY ARCHIVES")
    local_snaps = Path("data/DASHBOARD/exports/screener_snapshots")
    cold_snaps = Path("D:/sec-data/archives/screener")
    local_count = len([d for d in local_snaps.iterdir() if d.is_dir()]) if local_snaps.exists() else 0
    cold_count = len([d for d in cold_snaps.iterdir() if d.is_dir()]) if cold_snaps.exists() else 0

    _check("Local snapshots", local_count > 0, f"{local_count} snapshots")
    _check("D: cold snapshots", cold_count > 0 or not d_connected,
           f"{cold_count} snapshots" if d_connected else "D: not connected")

    # Check today's snapshot exists
    today_snap = local_snaps / today.isoformat()
    _check("Today's snapshot", today_snap.exists(),
           f"{len(list(today_snap.iterdir()))} files" if today_snap.exists() else "NOT YET (runs at 6PM)")

    # Autocall ranks
    rank_dir = Path("data/DASHBOARD/exports/autocall_ranks")
    rank_count = len(list(rank_dir.glob("*.json"))) if rank_dir.exists() else 0
    _check("Autocall rank history", rank_count > 0, f"{rank_count} days")
    print()

    # 6. Bloomberg Data
    print("[6] BLOOMBERG FILE")
    from screener.config import DATA_FILE
    if DATA_FILE.exists():
        mtime = datetime.fromtimestamp(DATA_FILE.stat().st_mtime)
        age_hours = (now - mtime).total_seconds() / 3600
        _check("Bloomberg file", age_hours < 26,
               f"{DATA_FILE.name} modified {mtime.strftime('%m/%d %H:%M')} ({age_hours:.0f}h ago)")
        if age_hours > 26:
            issues.append(f"Bloomberg file {age_hours:.0f}h stale")
    else:
        _check("Bloomberg file", False, "NOT FOUND")
        issues.append("Bloomberg file missing")
    print()

    # 7. Email Send Log
    print("[7] EMAIL LOG")
    send_log_path = Path("data/.send_log.json")
    if send_log_path.exists():
        send_log = json.loads(send_log_path.read_text())
        last_dates = sorted(send_log.keys(), reverse=True)[:3]
        for d in last_dates:
            reports = ", ".join(f"{k}@{v}" for k, v in send_log[d].items())
            _check(f"Sent {d}", True, reports)
    else:
        _check("Send log", False, "not found")
    print()

    # 8. Watcher + Rapid Sync Logs
    print("[8] RUN LOGS")
    for log_prefix, label in [("watcher", "Watcher"), ("rapid", "Rapid Sync")]:
        log_file = Path(f"logs/{log_prefix}_{today.isoformat()}.log")
        if log_file.exists():
            lines = log_file.read_text(encoding="utf-8").strip().split("\n")
            errors = [l for l in lines if "error" in l.lower() or "fail" in l.lower()]
            _check(f"{label} log (today)", len(errors) == 0,
                   f"{len(lines)} entries, {len(errors)} errors")
            if errors:
                issues.append(f"{label} had {len(errors)} errors today")
                for e in errors[:3]:
                    print(f"      {e.strip()[:100]}")
        else:
            _check(f"{label} log (today)", True, "no log yet")
    print()

    # Summary
    print("=" * 50)
    if issues:
        print(f"ISSUES FOUND: {len(issues)}")
        for i in issues:
            print(f"  ! {i}")
    else:
        print("ALL SYSTEMS HEALTHY")
    print("=" * 50)

    return {"timestamp": now.isoformat(), "issues": issues, "issue_count": len(issues)}


if __name__ == "__main__":
    run_review()
