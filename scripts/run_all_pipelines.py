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
        saved_argv = sys.argv
        sys.argv = [sys.argv[0]]  # Clear so run_daily's argparser doesn't see our flags
        from scripts.run_daily import main as run_daily_main
        run_daily_main()
        return True
    except Exception as e:
        print(f"  SEC pipeline FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        sys.argv = saved_argv


# run_market_pipeline() was REMOVED in Stage F (approved plan). It shelled out to
# scripts/run_market_pipeline.py — a second market write path that diverged from
# sync_market_data and was the origin of the issuer_mapping 'Tidal' brand drift.
# Market refresh now flows only through run_chain -> sync_market_data.


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

        # 2. Market pipeline — RETIRED (Stage F, approved plan). The legacy
        # scripts/run_market_pipeline.py was a SECOND market write path that diverged
        # from sync_market_data (no MicroSectors override, no post-steps, and the
        # issuer_mapping brand drift that produced 'Tidal'). Market refresh now goes
        # exclusively through run_chain -> sync_market_data. This orchestrator is
        # Windows-legacy; the VPS runs the chain on its own trigger.
        print("\n--- Market Pipeline (RETIRED — handled by run_chain) ---")
        results["market"] = "via run_chain"

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
        # Only the literal "FAILED" is a failure. Retired/delegated steps carry an
        # informational status (e.g. market -> "via run_chain") and were being printed
        # as [FAIL], so every nightly summary looked broken — which trains you to ignore
        # it and would mask a REAL failure. The notification logic below already keys on
        # "FAILED" correctly; this makes the printed summary agree with it.
        marker = ("[OK]" if status == "ok"
                  else "[SKIP]" if status == "skipped"
                  else "[FAIL]" if status == "FAILED"
                  else "[INFO]")
        status_note = "" if status in ("ok", "skipped", "FAILED") else f" ({status})"
        print(f"  {marker} {step}{status_note}")
    print(f"\nCompleted in {elapsed:.0f}s ({elapsed / 60:.1f}m)")
    print(f"Log: {log_file}")

    # Desktop notification (Windows only — skipped on Linux/macOS)
    import platform
    if platform.system() == "Windows":
        try:
            import ctypes
            failed = [s for s, v in results.items() if v == "FAILED"]
            if failed:
                title = "REX Pipeline - Errors"
                msg = f"Finished in {elapsed:.0f}s\nFailed: {', '.join(failed)}"
                icon = 0x30  # warning
            else:
                title = "REX Pipeline - Complete"
                steps = [s for s, v in results.items() if v == "ok"]
                msg = f"Finished in {elapsed:.0f}s\n{', '.join(steps)}"
                icon = 0x40  # info
            ctypes.windll.user32.MessageBoxW(0, msg, title, icon | 0x40000)
        except Exception:
            pass

    # Always exit 0 -- Task Scheduler shouldn't retry on partial failure
    sys.exit(0)


if __name__ == "__main__":
    main()
