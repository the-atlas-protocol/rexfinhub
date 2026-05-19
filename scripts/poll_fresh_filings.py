"""Lightweight high-frequency poller for new SEC 485 filings.

Sits between the 4x/day full sweep (rexfinhub-sec-scrape) and an event-driven
RSS daemon. Fires every 15 min during US market hours. Uses SEC's daily-index
pre-flight to skip ~95% of trusts that have no new filings today, so each pass
typically finishes in 30-90 sec instead of 15-20 min for a full sweep.

Pipeline freshness contract:
    Worst-case lag between SEC filing acceptance and pipeline-page visibility
    drops from ~4h (between 4x/day sweeps) to ~15-20 min during business hours.

What it does:
  1. Acquire a flock on data/.poll_fresh_filings.lock -- refuses to run if the
     heavy 4x/day sec-scrape is already mid-flight (no concurrent uploads).
  2. Snapshot row counts (filings, rex_products) BEFORE.
  3. Run `etp_tracker.run_pipeline.run_pipeline` with use_daily_index=True and
     since=<today minus 2 days>. The daily-index pre-flight skips any trust
     that has not filed today.
  4. Invoke scripts/sync_rex_products_from_filings.py --apply --no-prompt to
     promote new filings into rex_products rows.
  5. Snapshot row counts AFTER. If anything new arrived -> push DB to Render
     via scripts.run_daily.upload_db_to_render. Otherwise skip the upload.
  6. Append a one-line summary to data/.poll_fresh_filings.log.

Safety:
  - Holds an exclusive lockfile; refuses if already locked (no concurrent runs)
  - Skips Render upload entirely if no rows changed (saves bandwidth)
  - Surfaces every error with traceback; exits 1 so systemd marks the unit failed
  - Idempotent: running twice in a row with no new SEC activity is a no-op

Usage::

    python scripts/poll_fresh_filings.py
    python scripts/poll_fresh_filings.py --dry-run     # scrape + sync, NO Render upload
    python scripts/poll_fresh_filings.py --skip-upload # alias for --dry-run

Designed to be invoked from a systemd timer like::

    OnCalendar=Mon..Fri *-*-* 08,09,10,11,12,13,14,15,16,17,18,19,20:00/15:00 America/New_York
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
LOCK_PATH = PROJECT_ROOT / "data" / ".poll_fresh_filings.lock"
LOG_PATH = PROJECT_ROOT / "data" / ".poll_fresh_filings.log"
HEAVY_UNIT = "rexfinhub-intraday-refresh.service"  # ADR 0005: renamed from rexfinhub-sec-scrape


def _counts(db_path: Path) -> dict:
    """Snapshot the relevant table sizes + newest filing date."""
    if not db_path.exists():
        return {"filings": -1, "rex_products": -1, "latest_filing": "N/A"}
    c = sqlite3.connect(str(db_path))
    try:
        return {
            "filings": c.execute("SELECT COUNT(*) FROM filings").fetchone()[0],
            "rex_products": c.execute("SELECT COUNT(*) FROM rex_products").fetchone()[0],
            "latest_filing": (
                c.execute("SELECT MAX(filing_date) FROM filings").fetchone()[0] or "N/A"
            ),
        }
    finally:
        c.close()


def _heavy_scrape_active() -> bool:
    """Refuse to overlap with the 4x/day full-universe sweep."""
    try:
        out = subprocess.run(
            ["systemctl", "is-active", HEAVY_UNIT],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() == "active" or out.stdout.strip() == "activating"
    except FileNotFoundError:
        # Not on systemd (e.g. running this script on Windows for dry-run)
        return False
    except Exception:
        return False


def _log(line: str) -> None:
    """Append a one-line audit record to .poll_fresh_filings.log."""
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def run_scrape() -> int:
    """Run the lightweight scrape. Returns number of CIKs touched (best-effort)."""
    from etp_tracker.run_pipeline import load_ciks_from_db, run_pipeline

    ciks, overrides = load_ciks_from_db()  # default: curated universe (~290 trusts)
    since = (datetime.now().date() - timedelta(days=2)).isoformat()

    print(f"  Scraping with use_daily_index=True (curated universe, since={since})")
    print(f"  Universe size: {len(ciks)} CIKs (daily index will skip those without new filings)")

    run_pipeline(
        ciks=ciks,
        overrides=overrides,
        since=since,
        refresh_submissions=True,
        refresh_max_age_hours=0,  # always re-pull submissions index for freshness
        user_agent="REX-FreshPoller/1.0 (relasmar@rexfin.com)",
        etf_only=True,
        use_daily_index=True,     # the magic flag -- skip trusts with no new filings today
        use_async=True,
        max_workers=8,
        triggered_by="fresh-poller",
    )
    return len(ciks)


def run_sync() -> None:
    """Promote any new filings into rex_products."""
    print("  Syncing rex_products from new filings...")
    res = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "scripts" / "sync_rex_products_from_filings.py"),
         "--apply", "--no-prompt"],
        capture_output=True, text=True, timeout=300,
    )
    if res.returncode != 0:
        raise RuntimeError(
            f"sync_rex_products_from_filings exit={res.returncode}: {res.stderr[-400:]}"
        )
    # Last few lines of stdout for visibility
    tail = "\n".join(res.stdout.splitlines()[-5:])
    print(f"  sync_rex_products tail:\n{tail}")


def run_upload() -> None:
    """Push the DB to Render. Uses the same lean-upload path as run_daily."""
    from scripts.run_daily import upload_db_to_render
    upload_db_to_render()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true", help="Scrape + sync but skip Render upload")
    ap.add_argument("--skip-upload", action="store_true", help="Alias for --dry-run")
    ap.add_argument("--force", action="store_true",
                    help="Run even if rexfinhub-sec-scrape.service is active (use with care)")
    args = ap.parse_args()
    skip_upload = args.dry_run or args.skip_upload

    started = datetime.now()
    print(f"=== Fresh-filings poller @ {started.isoformat(timespec='seconds')} ===")

    # Refuse to overlap with the heavy 4x/day sweep -- it already does scrape+sync+upload
    if not args.force and _heavy_scrape_active():
        msg = f"  SKIP: {HEAVY_UNIT} is active. Heavy sweep already running."
        print(msg)
        _log(f"{started.isoformat(timespec='seconds')}  SKIP (heavy active)")
        return 0

    # Exclusive lockfile (Linux flock)
    lock_fh = None
    try:
        try:
            import fcntl
            LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            lock_fh = open(LOCK_PATH, "w")
            try:
                fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                print("  SKIP: another poll_fresh_filings is already running.")
                _log(f"{started.isoformat(timespec='seconds')}  SKIP (lock held)")
                return 0
        except ImportError:
            # Windows dry-run path: skip flock
            print("  (no fcntl, skipping lockfile -- Windows dry-run)")

        before = _counts(DB_PATH)
        print(f"  Before: filings={before['filings']:,}  rex_products={before['rex_products']:,}  "
              f"latest_filing={before['latest_filing']}")

        # 1. Scrape (daily-index narrowed)
        t0 = time.time()
        try:
            run_scrape()
        except Exception as e:
            traceback.print_exc()
            _log(f"{started.isoformat(timespec='seconds')}  FAIL scrape: {e}")
            return 1
        scrape_secs = time.time() - t0
        print(f"  Scrape finished in {scrape_secs:.1f}s")

        # 2. rex_products sync
        try:
            run_sync()
        except Exception as e:
            traceback.print_exc()
            _log(f"{started.isoformat(timespec='seconds')}  FAIL sync: {e}")
            return 1

        after = _counts(DB_PATH)
        delta_filings = after["filings"] - before["filings"]
        delta_rex = after["rex_products"] - before["rex_products"]
        latest_changed = after["latest_filing"] != before["latest_filing"]
        print(f"  After:  filings={after['filings']:,}  rex_products={after['rex_products']:,}  "
              f"latest_filing={after['latest_filing']}")
        print(f"  Delta:  filings=+{delta_filings}  rex_products=+{delta_rex}  "
              f"latest_changed={latest_changed}")

        # 3. Upload only if something actually changed
        uploaded = False
        if delta_filings == 0 and delta_rex == 0 and not latest_changed:
            print("  No new data -- skipping Render upload (saves bandwidth).")
        elif skip_upload:
            print("  Changes detected but --dry-run / --skip-upload set -- not uploading.")
        else:
            try:
                run_upload()
                uploaded = True
            except Exception as e:
                traceback.print_exc()
                _log(f"{started.isoformat(timespec='seconds')}  "
                     f"FAIL upload (data committed locally): {e}")
                return 1

        elapsed = (datetime.now() - started).total_seconds()
        summary = (
            f"{started.isoformat(timespec='seconds')}  OK  "
            f"+{delta_filings}f +{delta_rex}r  "
            f"latest={after['latest_filing']}  "
            f"upload={'yes' if uploaded else 'no'}  "
            f"elapsed={elapsed:.0f}s"
        )
        _log(summary)
        print(f"=== Done. {summary} ===")
        return 0

    finally:
        if lock_fh is not None:
            try:
                lock_fh.close()
            except Exception:
                pass


if __name__ == "__main__":
    sys.exit(main())
