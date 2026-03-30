"""
Rapid Sync — lightweight pipeline for intraday filing detection.

Runs every 2 hours via Task Scheduler. Catches new filings and trusts
between full daily runs, syncs to DB, and uploads to Render so the
live site stays current within hours.

Safe for: no internet, no D: drive, computer wake from sleep.
Every step is independent — failures don't cascade.

Usage:
    python scripts/run_rapid_sync.py

Task Scheduler (every 2 hours, 8am-10pm):
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

NOTES_PROJECT = Path("C:/Projects/structured-notes")
NOTES_DB_SRC = Path("D:/sec-data/databases/structured_notes.db")
NOTES_DB_DST = PROJECT_ROOT / "data" / "structured_notes.db"

CACHE_LOCAL = PROJECT_ROOT / "cache"
CACHE_ARCHIVE = Path("D:/sec-data/cache/rexfinhub")

LOG_DIR = PROJECT_ROOT / "logs"


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


def _internet_available() -> bool:
    """Quick check — can we reach SEC?"""
    import requests
    try:
        requests.head("https://www.sec.gov", timeout=5,
                      headers={"User-Agent": USER_AGENT})
        return True
    except Exception:
        return False


def _d_available() -> bool:
    return CACHE_ARCHIVE.parent.exists()


def main():
    start = time.time()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    # Log to file so we can audit runs
    LOG_DIR.mkdir(exist_ok=True)
    log_file = LOG_DIR / f"rapid_{datetime.now().strftime('%Y-%m-%d')}.log"

    def log(msg: str):
        line = f"{datetime.now().strftime('%H:%M:%S')} {msg}"
        print(line)
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    log(f"=== Rapid Sync ({now}) ===")

    online = _internet_available()
    d_drive = _d_available()
    log(f"  Internet: {'ONLINE' if online else 'OFFLINE'}")
    log(f"  D: drive: {'CONNECTED' if d_drive else 'NOT CONNECTED'}")

    if not online:
        log("  OFFLINE — skipping all SEC/Render operations")
        # Still sync notes DB from D: if available
        if d_drive and NOTES_DB_SRC.exists() and NOTES_DB_DST.exists():
            if NOTES_DB_SRC.stat().st_mtime > NOTES_DB_DST.stat().st_mtime:
                import shutil
                shutil.copy2(str(NOTES_DB_SRC), str(NOTES_DB_DST))
                log("  Notes DB synced from D: (offline catch-up)")
        log(f"\n=== Rapid Sync done (offline) in {time.time() - start:.0f}s ===")
        return

    new_trusts = 0
    new_filings = 0
    new_notes = 0
    changed_trusts = None

    # Step 1: Watcher poll — catch new trusts + filing alerts
    log("\n[1/6] Polling EDGAR for new filings...")
    try:
        from webapp.database import init_db, SessionLocal
        from etp_tracker.watcher import poll_recent_filings, auto_approve_candidates

        init_db()
        db = SessionLocal()
        try:
            result = poll_recent_filings(db, lookback_days=1, poll_33act=True)
            log(f"  Alerts: {result.alerts_created} new, {result.alerts_skipped} known")
            log(f"  Candidates: {result.candidates_new} new CIKs detected")

            if result.candidates_new > 0:
                approved = auto_approve_candidates(db)
                new_trusts = approved
                log(f"  Auto-approved: {approved} new trust(s)")
        finally:
            db.close()
    except Exception as e:
        log(f"  Watcher failed: {e}")

    # Step 2: Structured notes discovery
    log("\n[2/6] Structured Notes discovery...")
    try:
        if NOTES_PROJECT.exists():
            import subprocess
            result = subprocess.run(
                [sys.executable, "cli.py", "discover"],
                cwd=str(NOTES_PROJECT), capture_output=True, text=True, timeout=300
            )
            if result.returncode == 0:
                new_lines = [l for l in result.stdout.split('\n') if 'new filings' in l]
                total_new = 0
                for l in new_lines:
                    parts = l.strip().split()
                    if len(parts) >= 2:
                        try:
                            total_new += int(parts[1])
                        except ValueError:
                            pass
                new_notes = total_new
                log(f"  Discovered {total_new} new note filings")
            else:
                log(f"  Discovery issue: {result.stderr[-100:] if result.stderr else 'unknown'}")

        # Sync notes DB from D: to local (if D: available and newer)
        if d_drive and NOTES_DB_SRC.exists():
            if not NOTES_DB_DST.exists() or NOTES_DB_SRC.stat().st_mtime > NOTES_DB_DST.stat().st_mtime:
                import shutil
                shutil.copy2(str(NOTES_DB_SRC), str(NOTES_DB_DST))
                log(f"  Notes DB synced from D:")
            else:
                log(f"  Notes DB already current")
        elif not d_drive:
            log(f"  D: not available — notes DB not synced")
    except Exception as e:
        log(f"  Notes failed: {e}")

    # Step 3: SEC pipeline (incremental — only new filings)
    log("\n[3/6] SEC Filing Pipeline (incremental)...")
    try:
        from etp_tracker.run_pipeline import run_pipeline, load_ciks_from_db

        CACHE_LOCAL.mkdir(parents=True, exist_ok=True)

        ciks, overrides = load_ciks_from_db()
        log(f"  Scanning {len(ciks)} trusts...")
        n, changed_trusts = run_pipeline(
            ciks=ciks,
            overrides=overrides,
            since=SINCE_DATE,
            refresh_submissions=True,
            user_agent=USER_AGENT,
            etf_only=True,
            cache_dir=CACHE_LOCAL,
        )
        new_filings = len(changed_trusts) if changed_trusts else 0
        log(f"  {n} trusts scanned, {new_filings} with new filings")
    except Exception as e:
        log(f"  Pipeline failed: {e}")

    # Step 4: DB sync
    log("\n[4/6] Syncing to database...")
    try:
        from webapp.services.sync_service import seed_trusts, sync_all
        from webapp.database import init_db, SessionLocal

        init_db()
        db = SessionLocal()
        try:
            seed_trusts(db)
            results = sync_all(db, OUTPUT_DIR, only_trusts=changed_trusts)
            log(f"  Synced: {len(results)} trusts")
        finally:
            db.close()
    except Exception as e:
        log(f"  DB sync failed: {e}")

    # Step 5: Archive cache C: -> D: (only if D: available)
    log("\n[5/6] Archiving cache...")
    try:
        if d_drive and CACHE_LOCAL.exists():
            import shutil
            copied = 0
            for src_dir_name in ("web", "submissions"):
                src_dir = CACHE_LOCAL / src_dir_name
                dst_dir = CACHE_ARCHIVE / src_dir_name
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
                log(f"  Archived {copied} files C: -> D:")
                # Only clean C: if D: archive succeeded
                shutil.rmtree(CACHE_LOCAL, ignore_errors=True)
                log(f"  C: cache cleaned")
            else:
                log(f"  No new cache files to archive")
        elif not d_drive:
            log(f"  D: not available — cache stays on C:")
            # DO NOT clean C: cache when D: is unavailable
        else:
            log(f"  No cache to archive")
    except Exception as e:
        log(f"  Archive failed (cache preserved on C:): {e}")

    # Step 6: Upload to Render (only if something changed)
    has_changes = new_trusts > 0 or new_filings > 0 or new_notes > 0
    if has_changes:
        log(f"\n[6/6] Uploading to Render...")
        try:
            import sqlite3
            import gzip
            import shutil
            import requests

            db_path = str(PROJECT_ROOT / "data" / "etp_tracker.db")
            if not Path(db_path).exists():
                log(f"  No database found, skipping upload")
            else:
                # WAL checkpoint
                conn = sqlite3.connect(db_path)
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                conn.close()

                # Strip 13F tables and compress
                api_key = _load_env("API_KEY")
                headers = {"X-API-Key": api_key} if api_key else {}
                render_db = PROJECT_ROOT / "data" / "etp_tracker_render.db"
                gz_path = str(render_db) + ".upload.gz"

                try:
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
                    resp = requests.post(
                        f"{RENDER_API_URL}/db/upload",
                        files={"file": ("etp_tracker.db.gz", open(gz_path, "rb"), "application/gzip")},
                        headers=headers,
                        timeout=600,
                    )
                    if resp.status_code == 200:
                        log(f"  Uploaded to Render ({gz_mb:.0f} MB)")
                    else:
                        log(f"  Upload failed: HTTP {resp.status_code}")
                finally:
                    # Always clean up temp files
                    for p in (gz_path, str(render_db)):
                        try:
                            Path(p).unlink(missing_ok=True)
                        except Exception:
                            pass

        except requests.exceptions.ConnectionError:
            log(f"  Upload failed: can't reach Render (network issue)")
        except Exception as e:
            log(f"  Upload failed: {e}")
    else:
        log(f"\n[6/6] No changes — skipping Render upload")

    elapsed = time.time() - start
    log(f"\n=== Rapid Sync done in {elapsed:.0f}s ===")
    log(f"  Trusts: +{new_trusts}  Filings: +{new_filings}  Notes: +{new_notes}")


if __name__ == "__main__":
    main()
