"""
Overnight Batch Runner - 2026-02-25

Runs the full pipeline for ALL 236 trusts (194 existing + 42 new),
syncs to DB, uploads to Render, commits and pushes to GitHub.

Usage:
    python scripts/run_overnight.py
"""
from __future__ import annotations
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from etp_tracker.run_pipeline import run_pipeline
from etp_tracker.trusts import get_all_ciks, get_overrides

OUTPUT_DIR = PROJECT_ROOT / "outputs"
USER_AGENT = "REX-ETP-Tracker/2.0 (relasmar@rexfin.com)"
LOG_FILE = PROJECT_ROOT / "logs" / f"overnight_{datetime.now():%Y%m%d_%H%M}.log"


class Logger:
    """Tee stdout to both console and log file."""
    def __init__(self, logpath: Path):
        logpath.parent.mkdir(parents=True, exist_ok=True)
        self.terminal = sys.stdout
        self.log = open(logpath, "w", encoding="utf-8")

    def write(self, msg):
        self.terminal.write(msg)
        self.log.write(msg)
        self.log.flush()

    def flush(self):
        self.terminal.flush()
        self.log.flush()


def run_cmd(args: list[str], label: str) -> bool:
    """Run a shell command, print output, return True on success."""
    print(f"  $ {' '.join(args)}")
    result = subprocess.run(args, capture_output=True, text=True, cwd=str(PROJECT_ROOT))
    if result.stdout.strip():
        print(f"  {result.stdout.strip()}")
    if result.returncode != 0:
        print(f"  FAILED ({label}): {result.stderr.strip()}")
        return False
    return True


def main():
    sys.stdout = Logger(LOG_FILE)
    start = time.time()
    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    ciks = get_all_ciks()

    print(f"=== Overnight Batch Run ({today}) ===")
    print(f"Trusts: {len(ciks)}")
    print(f"Log: {LOG_FILE}")

    # Ensure git config is set for this repo
    subprocess.run(["git", "config", "user.name", "Ryu El-Asmar"],
                    cwd=str(PROJECT_ROOT), capture_output=True)
    subprocess.run(["git", "config", "user.email", "relasmar@rexfin.com"],
                    cwd=str(PROJECT_ROOT), capture_output=True)

    # Step 1: Run full pipeline (incremental - manifests skip already-processed filings)
    print(f"\n[1/5] Running pipeline for {len(ciks)} trusts...")
    try:
        n = run_pipeline(
            ciks=ciks,
            overrides=get_overrides(),
            refresh_submissions=True,
            refresh_force_now=True,
            user_agent=USER_AGENT,
        )
        print(f"  Processed {n} trusts")
    except Exception as e:
        print(f"  Pipeline error: {e}")
        import traceback
        traceback.print_exc()

    # Step 2: Export Excel
    print("\n[2/5] Exporting Excel...")
    try:
        from scripts.run_daily import export_excel
        export_excel(OUTPUT_DIR)
    except Exception as e:
        print(f"  Excel export failed (non-fatal): {e}")

    # Step 3: Sync to database
    print("\n[3/5] Syncing to database...")
    try:
        from webapp.database import init_db, SessionLocal
        from webapp.services.sync_service import seed_trusts, sync_all
        init_db()
        db = SessionLocal()
        try:
            seed_trusts(db)
            sync_all(db, OUTPUT_DIR)
        finally:
            db.close()
        print("  Database synced.")
    except Exception as e:
        print(f"  DB sync failed (non-fatal): {e}")

    # Step 4: Upload DB to Render
    print("\n[4/5] Uploading database to Render...")
    try:
        from scripts.run_daily import upload_db_to_render
        upload_db_to_render()
    except Exception as e:
        print(f"  Upload failed (non-fatal): {e}")

    # Step 5: Git commit and push
    print("\n[5/5] Committing and pushing to GitHub...")
    # Stage the important files
    files_to_stage = [
        "etp_tracker/trusts.py",
        "data/etp_tracker.db",
    ]
    for f in files_to_stage:
        if (PROJECT_ROOT / f).exists():
            run_cmd(["git", "add", f], f"stage {f}")

    # Check if there are changes to commit
    result = subprocess.run(["git", "diff", "--cached", "--quiet"],
                            cwd=str(PROJECT_ROOT), capture_output=True)
    if result.returncode != 0:
        elapsed = time.time() - start
        msg = f"batch: overnight pipeline run - {len(ciks)} trusts, {elapsed/60:.0f}m"
        run_cmd(["git", "commit", "-m", msg], "commit")
        run_cmd(["git", "push", "origin", "main"], "push")
    else:
        print("  No changes to commit.")

    elapsed = time.time() - start
    print(f"\n=== Overnight run complete in {elapsed:.0f}s ({elapsed/60:.1f}m) ===")


if __name__ == "__main__":
    main()
