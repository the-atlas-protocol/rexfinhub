"""Intraday refresh — 4x/day artifact rebuild + Render upload.

Replaces the old ``rexfinhub-sec-scrape`` unit (which was misnamed: it was
already doing far more than SEC scraping). Per ADR 0005, the SEC scrape
half is redundant with ``rexfinhub-fresh-poller.timer`` running every 15
min during business hours. This wrapper:

  1. Checks whether ``fresh-poller`` ran in the last 30 minutes (via
     ``data/.poll_fresh_filings.log`` mtime).
  2. If yes -> calls ``scripts/run_daily.py --skip-sec``. Skips the SEC
     scrape entirely (fresh-poller has it covered) and runs the rest:
     market sync, classification, screener snapshot, total returns,
     parquet rebuild, DB compact, Render upload.
  3. If no  -> calls ``scripts/run_daily.py`` (full pipeline). Defensive
     fallback for when fresh-poller is stuck or failing.

Expected runtime:
  - skip-sec path:  ~5-8 min (was ~15-20 min for the old full sweep)
  - fallback path:  ~15-20 min (same as the old behavior)

Logs every decision to ``data/.intraday_refresh.log`` so the morning
``[PIPELINE]`` summary can verify the rhythm is healthy.

Schedule via ``rexfinhub-intraday-refresh.timer`` 4x/day Mon-Fri at
08:00 / 12:00 / 16:00 / 20:00 ET.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

FRESH_POLLER_LOG = PROJECT_ROOT / "data" / ".poll_fresh_filings.log"
INTRADAY_LOG = PROJECT_ROOT / "data" / ".intraday_refresh.log"

# Window in which a fresh-poller success counts as "recent enough" to skip
# the SEC scrape here. Fresh-poller is scheduled every 15 min during 08:00-20:45
# ET so a 30-min window is generous — gives us one missed slot of grace.
FRESH_POLLER_WINDOW_MIN = 30


def _fresh_poller_recent() -> tuple[bool, float | None]:
    """True if fresh-poller's log was updated within the window.

    Returns (recent, age_minutes_or_None).
    """
    if not FRESH_POLLER_LOG.exists():
        return False, None
    try:
        mtime = FRESH_POLLER_LOG.stat().st_mtime
        age_min = (time.time() - mtime) / 60.0
        return age_min <= FRESH_POLLER_WINDOW_MIN, age_min
    except OSError:
        return False, None


def _log(msg: str) -> None:
    INTRADAY_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line, flush=True)
    with INTRADAY_LOG.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force-full", action="store_true",
                    help="Run the full pipeline (do not skip SEC scrape) even if fresh-poller is healthy")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print which mode would run and exit 0 without executing")
    args = ap.parse_args()

    recent, age_min = _fresh_poller_recent()
    if args.force_full or not recent:
        mode = "FULL"
        reason = ("--force-full set" if args.force_full
                  else f"fresh-poller stale (age={age_min:.1f}min)" if age_min is not None
                  else "fresh-poller log missing")
        cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / "run_daily.py")]
    else:
        mode = "SKIP-SEC"
        reason = f"fresh-poller fresh (age={age_min:.1f}min)"
        cmd = [sys.executable, str(PROJECT_ROOT / "scripts" / "run_daily.py"), "--skip-sec"]

    _log(f"START mode={mode} reason='{reason}' cmd={' '.join(cmd)}")
    if args.dry_run:
        _log("DRY-RUN — not executing")
        return 0

    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), check=False)
        rc = proc.returncode
    except Exception as e:
        _log(f"CRASHED: {e}")
        return 1

    elapsed = time.time() - t0
    _log(f"END   mode={mode} rc={rc} elapsed={elapsed:.1f}s")
    return rc


if __name__ == "__main__":
    sys.exit(main())
