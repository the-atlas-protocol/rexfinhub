"""Restore drill — prove a backup is actually restorable, never touching the live DB.

The nightly backup is sound (atomic .backup() + integrity check), but a backup you have
never restored is not a backup. This script restores a snapshot to a SCRATCH path, runs
SQLite's integrity checks, and asserts the key tables have plausible row counts — so
recovery is a rehearsed 30-second command, not two hours of improvisation under pressure.

It is READ-ONLY with respect to the live DB: it copies the chosen backup to a temp file
and inspects that copy. Exit 0 = restorable + sane; 1 = a check failed (do NOT trust it).

Run on the VPS against the real backups:
    python scripts/restore_drill.py                       # newest in data/backups/
    python scripts/restore_drill.py --backup <file.db>    # a specific snapshot
Locally you can smoke-test the mechanism by pointing --backup at any sqlite DB.
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"

# Tables that must exist with at least this many rows for a restore to be "sane".
SANITY = {"mkt_master_data": 5000}


def newest_backup(backup_dir: Path = BACKUP_DIR) -> Path | None:
    cands = sorted(backup_dir.glob("*.db"), key=lambda p: p.stat().st_mtime, reverse=True) \
        if backup_dir.exists() else []
    return cands[0] if cands else None


def drill(backup: Path, scratch_dir: Path | None = None) -> dict:
    """Restore `backup` to a scratch copy and validate it. Returns a result dict."""
    res = {"backup": str(backup), "ok": False, "checks": {}, "detail": ""}
    if not backup.exists():
        res["detail"] = f"backup not found: {backup}"
        return res

    tmpdir = Path(scratch_dir or tempfile.mkdtemp(prefix="restore_drill_"))
    scratch = tmpdir / "restored.db"
    try:
        shutil.copy2(backup, scratch)  # the "restore" — into scratch, never the live DB
        con = sqlite3.connect(str(scratch))
        try:
            cur = con.cursor()
            integ = cur.execute("PRAGMA integrity_check").fetchone()[0]
            res["checks"]["integrity_check"] = integ
            quick = cur.execute("PRAGMA quick_check").fetchone()[0]
            res["checks"]["quick_check"] = quick

            counts = {}
            for tbl, floor in SANITY.items():
                try:
                    n = cur.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                except sqlite3.Error:
                    n = -1
                counts[tbl] = n
            res["checks"]["row_counts"] = counts

            integ_ok = integ == "ok"
            quick_ok = quick == "ok"
            counts_ok = all(counts.get(t, -1) >= floor for t, floor in SANITY.items())
            res["ok"] = integ_ok and quick_ok and counts_ok
            if not res["ok"]:
                bad = []
                if not integ_ok:
                    bad.append(f"integrity_check={integ}")
                if not quick_ok:
                    bad.append(f"quick_check={quick}")
                for t, floor in SANITY.items():
                    if counts.get(t, -1) < floor:
                        bad.append(f"{t}={counts.get(t)} (<{floor})")
                res["detail"] = "; ".join(bad)
            else:
                res["detail"] = (f"restorable; integrity ok; "
                                 + ", ".join(f"{t}={n:,}" for t, n in counts.items()))
        finally:
            con.close()
    finally:
        if scratch_dir is None:  # we created the temp dir -> clean it up
            shutil.rmtree(tmpdir, ignore_errors=True)
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description="Prove a DB backup is restorable (read-only)")
    ap.add_argument("--backup", help="backup file (default: newest in data/backups/)")
    ap.add_argument("--scratch-dir", help="keep the restored copy here (default: temp, removed)")
    args = ap.parse_args()

    backup = Path(args.backup) if args.backup else newest_backup()
    if backup is None:
        print(f"FAIL: no backup found in {BACKUP_DIR} (and none given via --backup)")
        return 1

    res = drill(backup, Path(args.scratch_dir) if args.scratch_dir else None)
    print(f"restore drill: {Path(res['backup']).name}")
    print(f"  result: {'PASS' if res['ok'] else 'FAIL'} — {res['detail']}")
    return 0 if res["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
