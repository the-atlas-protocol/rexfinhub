"""
Sync The Dashboard.xlsx from OneDrive to local fallback.

- Archives current fallback to data/DASHBOARD/history/ (date-stamped)
- Copies fresh file from OneDrive
- Skips if OneDrive file hasn't changed (same mtime)
- Can be run manually or scheduled (Task Scheduler / cron)

Usage:
    python scripts/sync_dashboard_data.py
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

ONEDRIVE_SRC = Path(
    r"C:\Users\RyuEl-Asmar\REX Financial LLC"
    r"\REX Financial LLC - Rex Financial LLC"
    r"\Product Development\MasterFiles\MASTER Data\The Dashboard.xlsx"
)
FALLBACK_DST = PROJECT_ROOT / "data" / "DASHBOARD" / "The Dashboard.xlsx"
HISTORY_DIR = PROJECT_ROOT / "data" / "DASHBOARD" / "history"


def sync(force: bool = False) -> bool:
    """Sync OneDrive -> fallback. Returns True if file was updated."""
    if not ONEDRIVE_SRC.exists():
        print(f"OneDrive source not found: {ONEDRIVE_SRC}")
        return False

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)

    # Skip if unchanged (compare file size + mtime)
    if not force and FALLBACK_DST.exists():
        src_stat = ONEDRIVE_SRC.stat()
        dst_stat = FALLBACK_DST.stat()
        if src_stat.st_size == dst_stat.st_size and src_stat.st_mtime <= dst_stat.st_mtime:
            print("Fallback is up to date, skipping.")
            return False

    # Archive current fallback before overwriting
    if FALLBACK_DST.exists():
        dst_mtime = datetime.fromtimestamp(FALLBACK_DST.stat().st_mtime)
        archive_name = f"The Dashboard_{dst_mtime.strftime('%Y-%m-%d')}.xlsx"
        archive_path = HISTORY_DIR / archive_name

        # Don't overwrite an existing archive for the same date
        if not archive_path.exists():
            shutil.copy2(str(FALLBACK_DST), str(archive_path))
            print(f"Archived: {archive_name}")
        else:
            print(f"Archive already exists: {archive_name}")

    # Copy fresh file
    shutil.copy2(str(ONEDRIVE_SRC), str(FALLBACK_DST))
    src_mtime = datetime.fromtimestamp(ONEDRIVE_SRC.stat().st_mtime)
    size_mb = ONEDRIVE_SRC.stat().st_size / 1_048_576
    print(f"Synced: {size_mb:.1f}MB (modified {src_mtime.strftime('%Y-%m-%d %H:%M')})")

    # Prune history older than 90 days
    cutoff = datetime.now().timestamp() - (90 * 86400)
    pruned = 0
    for old_file in HISTORY_DIR.glob("The Dashboard_*.xlsx"):
        if old_file.stat().st_mtime < cutoff:
            old_file.unlink()
            pruned += 1
    if pruned:
        print(f"Pruned {pruned} archive(s) older than 90 days")

    return True


if __name__ == "__main__":
    force = "--force" in sys.argv
    updated = sync(force=force)
    sys.exit(0 if updated else 0)
