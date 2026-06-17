"""Pull the CURRENT Bloomberg daily file from production into the local data dir.

Production (the VPS) fetches the authoritative workbook from SharePoint via the
Graph API on every run, so its local copy is always the freshest complete file
(all sheets: BlueOcean, microsector_flow, etc.). Locally the Graph API is not
configured, so a local copy can silently go stale — that was the root of the
40-vs-41 / missing-sheet chase (2026-06-16, working out of a worktree with a
stale partial copy).

Run this before any LOCAL report build so the local workflow is never stale:

    python scripts/refresh_bloomberg.py

It is also called automatically by scripts/build_previews.py.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
LOCAL = PROJECT_ROOT / "data" / "DASHBOARD" / "bloomberg_daily_file.xlsm"
VPS_HOST = "jarvis@46.224.126.196"
VPS_PATH = "/home/jarvis/rexfinhub/data/DASHBOARD/bloomberg_daily_file.xlsm"


def refresh() -> int:
    LOCAL.parent.mkdir(parents=True, exist_ok=True)
    before = None
    if LOCAL.exists():
        before = (time.time() - LOCAL.stat().st_mtime) / 3600.0
        print(f"local before: {before:.1f}h old, {LOCAL.stat().st_size/1e6:.1f}MB")
    print(f"pulling current file from {VPS_HOST} ...")
    rc = subprocess.run(["scp", "-q", f"{VPS_HOST}:{VPS_PATH}", str(LOCAL)]).returncode
    if rc != 0:
        print("ERROR: scp failed (is SSH to the VPS available?)", file=sys.stderr)
        return rc
    age_h = (time.time() - LOCAL.stat().st_mtime) / 3600.0
    print(f"OK: local file now {age_h:.1f}h old ({LOCAL.stat().st_size/1e6:.1f}MB)")
    # Sheet sanity — surface the sheets the reports depend on so a partial file
    # is caught immediately, not three reports later.
    try:
        import pandas as pd
        xl = pd.ExcelFile(LOCAL, engine="openpyxl")
        need = ["BlueOcean", "microsector_flow", "microsector_aum"]
        have = {s: (s in xl.sheet_names) for s in need}
        print(f"sheets: {len(xl.sheet_names)} | " + " | ".join(f"{k}:{v}" for k, v in have.items()))
        if not all(have.values()):
            print("WARNING: missing sheets ^ — MicroSectors / Blue Ocean reports will not build.",
                  file=sys.stderr)
    except Exception as e:
        print(f"sheet check skipped: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(refresh())
