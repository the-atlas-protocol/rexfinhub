"""Fast intraday candidate refresh — keeps the T-REX System page current WITHOUT
a Bloomberg pull, a 10-report bake, or a send.

The only inputs that change intraday are retail attention (ApeWisdom, live) and
new SEC filings (DB, refreshed every 15 min by the fresh-poller). Bloomberg
structural signals are EOD. So this loop just rebuilds the six analysis parquets
the page reads, refreshes the score-history table (run_v1 — otherwise orphaned),
bakes the downloadable PDFs, and pushes the parquets to Render. Cheap (~2-4 min),
safe to run every 30 min.

A lockfile prevents overlap with itself and is skipped while the heavy structural
chain (run_chain / apply_bloomberg_post_steps) holds it, so the two never rewrite
the parquets at once.

Usage:  python scripts/refresh_candidates.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable
LOCK = PROJECT_ROOT / "data" / ".candidates_refresh.lock"
LOCK_STALE_SECONDS = 1800  # a lock older than this is assumed dead (crashed run)

# Same six builders as parquet-rebuild.service / apply_bloomberg_post_steps Stage E,
# in the same order, so the fast loop and the structural chain converge on identical
# parquet schemas. run_v1 then refreshes the daily score-history table.
_STEPS: list[tuple[str, list[str]]] = [
    ("universe_loader",   [PY, "-m", "screener.li_engine.analysis.universe_loader"]),
    ("bbg_timeseries",    [PY, "-m", "screener.li_engine.analysis.bbg_timeseries"]),
    ("filed_underliers",  [PY, "-m", "screener.li_engine.analysis.filed_underliers"]),
    ("competitor_counts", [PY, "-m", "screener.li_engine.analysis.competitor_counts"]),
    ("launch_candidates", [PY, "-m", "screener.li_engine.analysis.launch_candidates"]),
    ("whitespace_v4",     [PY, "-m", "screener.li_engine.analysis.whitespace_v4"]),
    ("run_v1_scores",     [PY, "-m", "screener.li_engine.run_v1"]),
]


def _lock_held() -> bool:
    if not LOCK.exists():
        return False
    try:
        age = time.time() - LOCK.stat().st_mtime
    except OSError:
        return False
    if age > LOCK_STALE_SECONDS:
        print(f"  lock is stale ({age:.0f}s) — overriding")
        return False
    return True


def _run(label: str, cmd: list[str]) -> int:
    t0 = time.time()
    rc = subprocess.run(cmd, cwd=str(PROJECT_ROOT)).returncode
    dt = time.time() - t0
    print(f"  {'OK ' if rc == 0 else 'ERR'} {label:18s} rc={rc} {dt:5.1f}s")
    return rc


def main() -> int:
    if _lock_held():
        print("candidates-refresh: another run (fast or structural) holds the lock — skipping")
        return 0
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    LOCK.write_text(str(os.getpid()), encoding="utf-8")
    failed = []
    try:
        for label, cmd in _STEPS:
            if _run(label, cmd) != 0:
                failed.append(label)  # log + continue; one bad builder shouldn't skip the rest

        # Re-bake the downloadable PDFs from the fresh parquets (Chromium is here).
        _run("bake_pdfs", [PY, str(PROJECT_ROOT / "scripts" / "bake_trex_pdfs.py")])

        # Push only the parquets to Render so the public page serves fresh data.
        try:
            from scripts.run_daily import upload_parquets_to_render
            upload_parquets_to_render()
            print("  OK  render_upload      parquets pushed")
        except Exception as exc:  # noqa: BLE001 — upload failure must not fail the refresh
            print(f"  ERR render_upload      {exc}")
    finally:
        try:
            LOCK.unlink()
        except OSError:
            pass
    print(f"candidates-refresh done; builder failures: {failed or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
