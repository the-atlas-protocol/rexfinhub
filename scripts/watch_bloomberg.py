"""
Bloomberg file watcher -- detects fresh Bloomberg data and triggers market sync.

Runs as scheduled task every 5 min (4:00-5:00 PM weekdays).
If Bloomberg file is from today and newer than last sync, triggers:
  1. Market data sync
  2. Full classification
  3. Screener cache rebuild
  4. Upload to Render

Usage:
    python scripts/watch_bloomberg.py
"""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

MARKER = PROJECT_ROOT / "data" / "DASHBOARD" / ".bloomberg_watch_marker.json"


def _file_is_fresh() -> tuple[bool, Path | None]:
    """Check if Bloomberg file was modified today and is newer than last watch trigger."""
    from screener.config import DATA_FILE as bbg

    if not bbg or not bbg.exists():
        return False, None

    file_date = datetime.fromtimestamp(bbg.stat().st_mtime).date()
    if file_date != date.today():
        return False, bbg

    # Check marker -- did we already trigger today for this file version?
    if MARKER.exists():
        try:
            with open(MARKER) as f:
                marker = json.load(f)
            if marker.get("date") == date.today().isoformat():
                last_mtime = marker.get("file_mtime", 0)
                if bbg.stat().st_mtime <= last_mtime:
                    return False, bbg  # Already triggered for this version
        except (json.JSONDecodeError, KeyError):
            pass

    return True, bbg


def _trigger_sync(bbg_path: Path):
    """Stage E (approved plan): a fresh Bloomberg file drives the ONE ordered chain.

    The watcher used to run its own bespoke sequence (sync -> classify -> restamp ->
    screener -> upload), a parallel copy of the chain that could drift from it. It now
    invokes scripts/run_chain.py — the single source — so every refresh goes through
    the same path: git self-heal -> sync -> classify + self-healing cascade -> brands
    -> parquets -> reconcile -> build 10 reports -> preflight GATE (promote only on
    green) -> the single 'reports ready / needs your call' notification. The screener
    cache rebuild + Render upload (not part of run_chain) are preserved afterward.
    """
    import subprocess
    print(f"  Fresh Bloomberg detected: {bbg_path.name} "
          f"(mtime: {datetime.fromtimestamp(bbg_path.stat().st_mtime):%H:%M})")

    # The one chain — builds + gates reports and notifies. Non-zero is logged, not
    # fatal: we still refresh the screener cache + Render replica from whatever
    # landed (the send gate stays the hard stop on bad data).
    print("  Running the chain (scripts/run_chain.py)...")
    try:
        rc = subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "run_chain.py")],
            cwd=str(PROJECT_ROOT), timeout=3600,
        ).returncode
        print(f"  run_chain exit={rc}")
    except Exception as e:
        print(f"  run_chain failed: {e}")

    # Screener cache (not part of run_chain — keep refreshing it here).
    print("  Rebuilding screener cache...")
    try:
        from webapp.services.screener_3x_cache import compute_and_cache
        cache_data = compute_and_cache()
        cache_path = PROJECT_ROOT / "temp" / "screener_cache.json"
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with open(cache_path, "w") as f:
            json.dump(cache_data, f, default=str)
        print(f"  Cache: {cache_path.stat().st_size / 1024:.0f} KB")
    except Exception as e:
        print(f"  Screener cache failed (non-fatal): {e}")

    # Upload to Render (public read-only replica).
    print("  Uploading to Render...")
    try:
        subprocess.run(
            [sys.executable, str(PROJECT_ROOT / "scripts" / "run_daily.py"), "--upload"],
            cwd=str(PROJECT_ROOT), timeout=600,
        )
    except Exception as e:
        print(f"  Upload failed (non-fatal): {e}")

    # Write marker
    MARKER.parent.mkdir(parents=True, exist_ok=True)
    with open(MARKER, "w") as f:
        json.dump({
            "date": date.today().isoformat(),
            "file_mtime": bbg_path.stat().st_mtime,
            "triggered_at": datetime.now().isoformat(),
        }, f)

    print("  Bloomberg watch: sync complete")


if __name__ == "__main__":
    print(f"Bloomberg watch: {datetime.now():%Y-%m-%d %H:%M}")
    is_fresh, bbg_path = _file_is_fresh()
    if is_fresh and bbg_path:
        _trigger_sync(bbg_path)
    else:
        reason = "no file found" if not bbg_path else "not modified today or already triggered"
        print(f"  No fresh Bloomberg data detected ({reason})")
