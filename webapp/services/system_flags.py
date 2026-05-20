"""Phase 7 Part B Stage 2 (ADR 0010): system_flags helper with dual-read.

Wraps the legacy flag-file pattern with a DB-backed helper. Reads prefer
the `system_flags` table (Phase 7 Part B Stage 1 schema) but fall back to
the existing file in `config/` or `data/` if the DB row is missing. This
lets us migrate flag-read sites incrementally without flipping any
breakage risk.

Writes always update BOTH the DB row AND the file (until Stage 3 deletes
the files). Idempotent.

Usage:
    from webapp.services.system_flags import get_flag, set_flag

    if get_flag("send_paused"):
        log.info("send_paused is set — standing down")

    set_flag("auto_demote_vanished", True, set_by="admin:relasmar",
             notes="enabled for BUG-04 LIQU/DLST cleanup")
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"


# flag_name -> path to legacy file (the file's existence/contents drives the
# fallback). Keep in sync with scripts/migrate_state_tables.py FLAG_FILES.
FLAG_FILES = {
    "send_enabled":          (PROJECT_ROOT / "config" / ".send_enabled",
                              lambda p: p.exists() and p.read_text(encoding="utf-8").strip().lower() == "true"),
    "send_paused":           (PROJECT_ROOT / "data" / ".send_paused", lambda p: p.exists()),
    "preflight_maintenance": (PROJECT_ROOT / "data" / ".preflight_maintenance", lambda p: p.exists()),
    "autogo_on_warn":        (PROJECT_ROOT / "data" / ".autogo_on_warn", lambda p: p.exists()),
    "auto_demote_vanished":  (PROJECT_ROOT / "data" / ".auto_demote_vanished", lambda p: p.exists()),
}


def _read_db_flag(flag_name: str) -> Optional[bool]:
    """Return True/False from system_flags table, or None if row absent."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        try:
            row = conn.execute(
                "SELECT is_set FROM system_flags WHERE flag_name = ?",
                (flag_name,),
            ).fetchone()
            if row is None:
                return None
            return bool(row[0])
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        log.debug("system_flags table not present: %s", e)
        return None


def _read_file_flag(flag_name: str) -> bool:
    """Fallback: evaluate the legacy file."""
    spec = FLAG_FILES.get(flag_name)
    if not spec:
        return False
    path, evaluator = spec
    try:
        return bool(evaluator(path))
    except Exception as e:
        log.warning("flag %s file evaluator failed: %s", flag_name, e)
        return False


def get_flag(flag_name: str) -> bool:
    """Read a system flag. DB row authoritative; file is fallback.

    The two should agree (writes update both). When they diverge,
    DB wins — that's the cutover preference for Stage 2+.
    """
    db_val = _read_db_flag(flag_name)
    if db_val is not None:
        return db_val
    return _read_file_flag(flag_name)


def set_flag(
    flag_name: str,
    value: bool,
    *,
    set_by: str = "code",
    notes: str = "",
) -> None:
    """Update both the DB row AND the legacy file (dual-write during cutover).

    Raises if the flag_name isn't in FLAG_FILES (unknown flag).
    """
    if flag_name not in FLAG_FILES:
        raise ValueError(f"Unknown flag_name: {flag_name!r}. "
                         f"Allowed: {sorted(FLAG_FILES.keys())}")

    now = datetime.utcnow().isoformat()

    # DB upsert
    try:
        conn = sqlite3.connect(str(DB_PATH))
        try:
            conn.execute("""
                INSERT INTO system_flags (flag_name, is_set, set_at, set_by, notes)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT (flag_name)
                DO UPDATE SET is_set = excluded.is_set,
                              set_at = excluded.set_at,
                              set_by = excluded.set_by,
                              notes  = excluded.notes
            """, (flag_name, 1 if value else 0, now, set_by, notes))
            conn.commit()
        finally:
            conn.close()
    except sqlite3.OperationalError as e:
        log.warning("set_flag DB upsert failed for %s: %s", flag_name, e)

    # File mirror (legacy compatibility)
    path, _ = FLAG_FILES[flag_name]
    path.parent.mkdir(parents=True, exist_ok=True)
    if flag_name == "send_enabled":
        # Single non-boolean flag — file contents 'true' / 'false'
        path.write_text("true" if value else "false", encoding="utf-8")
    else:
        # Existence-based flag (touch to set, unlink to clear)
        if value:
            path.touch()
        elif path.exists():
            try:
                path.unlink()
            except OSError as e:
                log.warning("set_flag unlink failed for %s: %s", flag_name, e)


def list_flags() -> dict[str, dict]:
    """Return a snapshot of every known flag's current state (both sources)."""
    out = {}
    for flag in FLAG_FILES:
        out[flag] = {
            "db": _read_db_flag(flag),
            "file": _read_file_flag(flag),
            "effective": get_flag(flag),
        }
    return out
