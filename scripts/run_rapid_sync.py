"""
Rapid Sync — lightweight pipeline for intraday filing detection.

Runs every 2 hours via Task Scheduler. Catches new filings and trusts
between full daily runs, syncs to DB, and uploads to Render so the
live site stays current within hours.

Workflow:
    1. Watcher poll (new trusts + filing alerts via EFTS)
    2. SEC pipeline (incremental — only new filings, seconds if quiet)
    3. DB sync
    4. Compact + upload to Render

Skips: universe ZIP sync, structured notes, market data, screener cache,
       archival (those stay in the full daily run).

Usage:
    python scripts/run_rapid_sync.py

Task Scheduler (every 2 hours, 6am-10pm):
    Created by scripts/setup_scheduler.py
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

OUTPUT_DIR = PROJECT_ROOT / "outputs"
SINCE_DATE = "2024-01-01"
USER_AGENT = "REX-ETP-Tracker/2.0 (relasmar@rexfin.com)"
DASHBOARD_URL = "https://rex-etp-tracker.onrender.com"
RENDER_API_URL = "https://rex-etp-tracker.onrender.com/api/v1"


def _load_env(key: str) -> str:
    env_val = os.environ.get(key, "")
    if env_val:
        return env_val
    env_file = PROJECT_ROOT / "config" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def main():
    start = time.time()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"=== Rapid Sync ({now}) ===")

    new_trusts = 0
    new_filings = 0
    changed_trusts = None

    # Step 1: Watcher poll — catch new trusts + filing alerts
    print("\n[1/5] Polling EDGAR for new filings...")
    try:
        from webapp.database import init_db, SessionLocal
        from etp_tracker.watcher import poll_recent_filings, auto_approve_candidates

        init_db()
        db = SessionLocal()
        try:
            result = poll_recent_filings(db, lookback_days=1, poll_33act=True)
            print(f"  Alerts: {result.alerts_created} new, {result.alerts_skipped} known")
            print(f"  Candidates: {result.candidates_new} new CIKs detected")

            if result.candidates_new > 0:
                approved = auto_approve_candidates(db)
                new_trusts = approved
                print(f"  Auto-approved: {approved} new trust(s)")
        finally:
            db.close()
    except Exception as e:
        print(f"  Watcher failed: {e}")

    # Step 1b: Structured notes discovery
    print("\n[1b/6] Structured Notes discovery...")
    notes_project = Path("C:/Projects/structured-notes")
    notes_src = Path("D:/sec-data/databases/structured_notes.db")
    notes_dst = PROJECT_ROOT / "data" / "structured_notes.db"
    try:
        if notes_project.exists():
            import subprocess
            result = subprocess.run(
                [sys.executable, "cli.py", "discover"],
                cwd=str(notes_project), capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                new_lines = [l for l in result.stdout.split('\n') if 'new filings' in l]
                total_new = sum(int(l.split()[1]) for l in new_lines if l.strip())
                print(f"  Discovered {total_new} new note filings")
            else:
                print(f"  Discovery failed: {result.stderr[-100:]}")
            # Sync DB from D: to local
            if notes_src.exists():
                import shutil
                shutil.copy2(str(notes_src), str(notes_dst))
                print(f"  Notes DB synced from D:")
    except Exception as e:
        print(f"  Notes failed: {e}")

    # Step 2: SEC pipeline (incremental — only new filings)
    print("\n[2/6] SEC Filing Pipeline (incremental)...")
    try:
        from etp_tracker.run_pipeline import run_pipeline, load_ciks_from_db

        cache_dir = PROJECT_ROOT / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)

        ciks, overrides = load_ciks_from_db()
        print(f"  Scanning {len(ciks)} trusts...")
        n, changed_trusts = run_pipeline(
            ciks=ciks,
            overrides=overrides,
            since=SINCE_DATE,
            refresh_submissions=True,
            user_agent=USER_AGENT,
            etf_only=True,
            cache_dir=cache_dir,
        )
        new_filings = len(changed_trusts) if changed_trusts else 0
        print(f"  {n} trusts scanned, {new_filings} with new filings")
    except Exception as e:
        print(f"  Pipeline failed: {e}")

    # Step 3: DB sync
    print("\n[3/6] Syncing to database...")
    try:
        from webapp.services.sync_service import seed_trusts, sync_all
        from webapp.database import init_db, SessionLocal

        init_db()
        db = SessionLocal()
        try:
            seed_trusts(db)
            results = sync_all(db, OUTPUT_DIR, only_trusts=changed_trusts)
            print(f"  Synced: {len(results)} trusts")
        finally:
            db.close()
    except Exception as e:
        print(f"  DB sync failed: {e}")

    # Step 4: Archive cache to D: (if available)
    print("\n[4/6] Archiving cache...")
    try:
        cache_archive = Path("D:/sec-data/cache/rexfinhub")
        cache_local = PROJECT_ROOT / "cache"
        if cache_archive.parent.exists() and cache_local.exists():
            import shutil
            copied = 0
            for src_dir_name in ("web", "submissions"):
                src_dir = cache_local / src_dir_name
                dst_dir = cache_archive / src_dir_name
                if not src_dir.exists():
                    continue
                dst_dir.mkdir(parents=True, exist_ok=True)
                for src_file in src_dir.rglob("*"):
                    if not src_file.is_file():
                        continue
                    dst_file = dst_dir / src_file.relative_to(src_dir)
                    if not dst_file.exists() or src_file.stat().st_size != dst_file.stat().st_size:
                        dst_file.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copy2(src_file, dst_file)
                        copied += 1
            if copied:
                print(f"  Archived {copied} files C: -> D:")
                shutil.rmtree(cache_local, ignore_errors=True)
                print(f"  C: cache cleaned")
            else:
                print(f"  No new cache files")
        else:
            print(f"  D: not available or no cache to archive")
    except Exception as e:
        print(f"  Archive failed: {e}")

    # Step 5: Upload to Render (only if something changed)
    if new_trusts > 0 or new_filings > 0:
        print("\n[5/6] Uploading to Render...")
        try:
            # Compact DB
            import sqlite3
            db_path = str(PROJECT_ROOT / "data" / "etp_tracker.db")
            if Path(db_path).exists():
                conn = sqlite3.connect(db_path)
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.close()

            # Upload DB
            import gzip
            import shutil
            import requests

            api_key = _load_env("API_KEY")
            headers = {"X-API-Key": api_key} if api_key else {}
            render_db = PROJECT_ROOT / "data" / "etp_tracker_render.db"
            gz_path = str(render_db) + ".upload.gz"

            shutil.copy2(db_path, render_db)
            conn = sqlite3.connect(str(render_db))
            for table in ("holdings", "institutions", "cusip_mappings"):
                conn.execute(f"DROP TABLE IF EXISTS [{table}]")
            conn.execute("VACUUM")
            conn.close()

            with open(render_db, "rb") as f_in:
                with gzip.open(gz_path, "wb", compresslevel=6) as f_out:
                    while True:
                        chunk = f_in.read(1024 * 1024)
                        if not chunk:
                            break
                        f_out.write(chunk)

            gz_mb = Path(gz_path).stat().st_size / 1e6
            with open(gz_path, "rb") as f:
                resp = requests.post(
                    f"{RENDER_API_URL}/db/upload",
                    files={"file": ("etp_tracker.db.gz", f, "application/gzip")},
                    headers=headers,
                    timeout=600,
                )
            if resp.status_code == 200:
                print(f"  Uploaded to Render ({gz_mb:.0f} MB)")
            else:
                print(f"  Upload failed: {resp.status_code}")

            # Cleanup
            for p in (gz_path, str(render_db)):
                Path(p).unlink(missing_ok=True)

        except Exception as e:
            print(f"  Upload failed: {e}")
    else:
        print("\n[5/6] No changes — skipping Render upload")

    elapsed = time.time() - start
    print(f"\n=== Rapid Sync done in {elapsed:.0f}s ===")
    print(f"  New trusts: {new_trusts}, New filings: {new_filings}")


if __name__ == "__main__":
    main()
