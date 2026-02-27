"""
Unified Pipeline Orchestrator

Runs all pipelines in sequence, uploads DB to Render, and sends email digest.
Designed for Windows Task Scheduler with wake timers.

Execution order (default):
  1. SEC pipeline (run_daily.main -- pipeline + Excel + DB sync + screener rescore)
  2. Market pipeline (subprocess -- has its own change detection)
  3. Upload DB to Render
  4. Send email digest

Modes:
  --skip-email    Scrape-only mode (8 AM / 12 PM / 9 PM tasks)
  --email-only    Email dispatch only (5 PM task): sends daily brief,
                  plus weekly report on Mondays

Usage:
    python scripts/run_all_pipelines.py                   # full run
    python scripts/run_all_pipelines.py --skip-email      # scrape only
    python scripts/run_all_pipelines.py --email-only      # 5 PM email dispatch
    python scripts/run_all_pipelines.py --skip-sec        # skip SEC pipeline
    python scripts/run_all_pipelines.py --skip-market     # skip market pipeline
    python scripts/run_all_pipelines.py --force-market    # force market even if unchanged
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

LOG_DIR = PROJECT_ROOT / "logs"


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


def run_sec_pipeline() -> bool:
    """Run the SEC filing pipeline via run_daily.main()."""
    print("\n--- SEC Pipeline ---")
    try:
        from scripts.run_daily import main as run_daily_main
        run_daily_main()
        return True
    except Exception as e:
        print(f"  SEC pipeline FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


def run_market_pipeline(force: bool = False) -> bool:
    """Run the market pipeline via subprocess (has its own arg parsing)."""
    print("\n--- Market Pipeline ---")
    cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / "run_market_pipeline.py")]
    if force:
        cmd.append("--force")

    try:
        result = subprocess.run(
            cmd,
            cwd=str(PROJECT_ROOT),
            capture_output=True,
            text=True,
            timeout=1800,  # 30 min max
        )
        # Print output (market pipeline prints its own progress)
        if result.stdout.strip():
            for line in result.stdout.strip().splitlines():
                print(f"  {line}")
        if result.returncode != 0 and result.stderr.strip():
            print(f"  stderr: {result.stderr.strip()}")

        if result.returncode == 0:
            return True
        else:
            print(f"  Market pipeline exited with code {result.returncode}")
            return False
    except subprocess.TimeoutExpired:
        print("  Market pipeline TIMED OUT (30 min limit)")
        return False
    except Exception as e:
        print(f"  Market pipeline FAILED: {e}")
        return False


def upload_db() -> bool:
    """Upload the local SQLite DB to Render."""
    print("\n--- Upload DB to Render ---")
    try:
        from scripts.run_daily import upload_db_to_render
        upload_db_to_render()
        return True
    except Exception as e:
        print(f"  DB upload FAILED: {e}")
        return False


def send_email(edition: str = "daily") -> bool:
    """Send the daily email digest (DB-based)."""
    _labels = {"daily": "Daily Brief", "morning": "Morning Brief", "evening": "Evening Update"}
    _label = _labels.get(edition, "Daily Brief")
    print(f"\n--- Email Digest ({_label}) ---")
    try:
        from webapp.database import init_db, SessionLocal
        from etp_tracker.email_alerts import send_digest_from_db

        init_db()
        db = SessionLocal()
        try:
            sent = send_digest_from_db(db, edition=edition)
            if sent:
                print(f"  {_label} sent.")
            else:
                print("  Email skipped (SMTP not configured or no recipients).")
            return True
        finally:
            db.close()
    except Exception as e:
        print(f"  Email digest FAILED: {e}")
        return False


def dispatch_emails() -> dict[str, str]:
    """5 PM email dispatch: daily brief always, weekly report on Mondays."""
    results = {}

    # Always send the daily brief
    ok = send_email(edition="daily")
    results["daily"] = "ok" if ok else "FAILED"

    # On Monday (weekday 0), also send the weekly report
    if datetime.now().weekday() == 0:
        print("\n--- Weekly Report (Monday) ---")
        try:
            from webapp.database import init_db, SessionLocal
            from etp_tracker.weekly_digest import send_weekly_digest

            init_db()
            db = SessionLocal()
            try:
                sent = send_weekly_digest(db)
                if sent:
                    print("  Weekly report sent.")
                else:
                    print("  Weekly report skipped (SMTP not configured or no recipients).")
                results["weekly"] = "ok"
            finally:
                db.close()
        except Exception as e:
            print(f"  Weekly report FAILED: {e}")
            import traceback
            traceback.print_exc()
            results["weekly"] = "FAILED"
    else:
        day_name = datetime.now().strftime("%A")
        print(f"\n--- Weekly Report (skipped -- {day_name}, not Monday) ---")
        results["weekly"] = "skipped"

    return results


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Run all pipelines")
    parser.add_argument("--skip-sec", action="store_true", help="Skip SEC pipeline")
    parser.add_argument("--skip-market", action="store_true", help="Skip market pipeline")
    parser.add_argument("--skip-email", action="store_true", help="Skip email digest")
    parser.add_argument("--email-only", action="store_true",
                        help="Email dispatch only (skip all pipelines + upload)")
    parser.add_argument("--force-market", action="store_true", help="Force market pipeline even if data unchanged")
    parser.add_argument("--edition", choices=["morning", "evening", "daily"], default=None,
                        help="Digest edition (default: daily)")
    args = parser.parse_args()

    # Setup logging
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    log_file = LOG_DIR / f"pipeline_{timestamp}.log"
    sys.stdout = Logger(log_file)

    start = time.time()
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"{'=' * 50}")
    print(f"=== All Pipelines Run ({now}) ===")
    print(f"{'=' * 50}")
    print(f"Log: {log_file}")

    results = {}

    if args.email_only:
        # --- Email-only mode (5 PM task) ---
        print("\n--- EMAIL-ONLY MODE ---")
        email_results = dispatch_emails()
        results.update(email_results)
    else:
        # --- Full pipeline mode ---

        # 1. SEC pipeline
        if args.skip_sec:
            print("\n--- SEC Pipeline (SKIPPED) ---")
            results["sec"] = "skipped"
        else:
            ok = run_sec_pipeline()
            results["sec"] = "ok" if ok else "FAILED"

        # 2. Market pipeline
        if args.skip_market:
            print("\n--- Market Pipeline (SKIPPED) ---")
            results["market"] = "skipped"
        else:
            ok = run_market_pipeline(force=args.force_market)
            results["market"] = "ok" if ok else "FAILED"

        # 3. Upload DB to Render
        ok = upload_db()
        results["upload"] = "ok" if ok else "FAILED"

        # 4. Email digest
        if args.skip_email:
            print("\n--- Email Digest (SKIPPED) ---")
            results["email"] = "skipped"
        else:
            edition = args.edition or "daily"
            ok = send_email(edition=edition)
            results["email"] = "ok" if ok else "FAILED"

    # Summary
    elapsed = time.time() - start
    print(f"\n{'=' * 50}")
    print(f"=== Summary ===")
    for step, status in results.items():
        marker = "[OK]" if status == "ok" else "[SKIP]" if status == "skipped" else "[FAIL]"
        print(f"  {marker} {step}")
    print(f"\nCompleted in {elapsed:.0f}s ({elapsed / 60:.1f}m)")
    print(f"Log: {log_file}")

    # Always exit 0 -- Task Scheduler shouldn't retry on partial failure
    sys.exit(0)


if __name__ == "__main__":
    main()
