"""Bloomberg stable-file auto-ingest — trigger the structural chain the moment
the SharePoint file is *completely* saved, without racing a mid-edit save.

The owner edits the Bloomberg .xlsm on SharePoint at a variable time each day.
Instead of guessing with a fixed clock (the old 17:15/21:00 timers, which raced
the saver and once ingested a torn file), this polls the file's watermark
(lastModifiedDateTime + size) and ingests only when it has been STABLE across a
debounce window — i.e. the owner has stopped editing. Idempotent per day, and
``/refreshdata`` remains an unconditional manual override.

Modes:
    python scripts/bloomberg_autoingest.py            # poll + maybe ingest
    python scripts/bloomberg_autoingest.py --cutoff   # alert if nothing ingested by cutoff
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ET = ZoneInfo("America/New_York")
STATE = PROJECT_ROOT / "data" / ".bbg_poll_state.json"
MARKER = PROJECT_ROOT / "data" / ".bloomberg_autoingest_marker.json"

STABILITY_WINDOW = timedelta(minutes=15)  # unchanged this long -> owner is done
MIN_SIZE = 1_000_000                       # torn/empty-file guard (matches download validator)


def _now_et() -> datetime:
    return datetime.now(ET)


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_json(p: Path, data: dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data), encoding="utf-8")


def _parse_dt(s: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _alert(subject: str, message: str) -> None:
    try:
        from etp_tracker.email_alerts import send_critical_alert
        send_critical_alert(subject, message)
    except Exception as e:  # noqa: BLE001
        print(f"  (alert failed: {e})")


def _ingested_today() -> bool:
    m = _read_json(MARKER)
    return m.get("date") == _now_et().date().isoformat() and bool(m.get("ingested"))


def poll_and_maybe_ingest() -> int:
    today = _now_et().date().isoformat()

    # GUARD-2: already ingested today (auto or via /refreshdata) -> nothing to do.
    if _ingested_today():
        print("autoingest: already ingested today — no-op")
        return 0

    try:
        from webapp.services.graph_files import get_sharepoint_file_metadata
        meta = get_sharepoint_file_metadata()
    except Exception as e:  # noqa: BLE001
        print(f"autoingest: metadata fetch failed: {e}")
        return 0
    if not meta or not meta.get("lastModifiedDateTime"):
        print("autoingest: no metadata — skipping")
        return 0

    sp_mtime = _parse_dt(meta["lastModifiedDateTime"])
    sp_size = int(meta.get("size") or 0)
    if sp_mtime is None:
        print("autoingest: unparseable mtime — skipping")
        return 0

    # GUARD-1: never ingest yesterday's file.
    if sp_mtime.astimezone(ET).date().isoformat() != today:
        print(f"autoingest: file is not today's (mtime {sp_mtime.astimezone(ET).date()}) — waiting")
        return 0
    # GUARD-3: torn / empty file.
    if sp_size <= MIN_SIZE:
        print(f"autoingest: file too small ({sp_size} bytes) — waiting")
        return 0

    watermark = f"{sp_mtime.isoformat()}|{sp_size}"
    state = _read_json(STATE)
    now = _now_et()
    if state.get("watermark") == watermark:
        first_seen = _parse_dt(state.get("first_seen_at", "")) or now
        stable_for = now - first_seen
        if stable_for >= STABILITY_WINDOW:
            print(f"autoingest: file stable for {stable_for} — INGESTING")
            rc = subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "run_chain.py")],
                                cwd=str(PROJECT_ROOT)).returncode
            if rc == 0:
                _write_json(MARKER, {"date": today, "ingested": True, "sp_mtime": sp_mtime.isoformat()})
                print("autoingest: structural chain OK — marker set")
            else:
                print(f"autoingest: run_chain rc={rc} — NOT marking (will retry next poll)")
            return rc
        print(f"autoingest: stable {stable_for} of {STABILITY_WINDOW} — waiting one more poll")
        return 0

    # Watermark changed (or first sighting) -> (re)start the debounce clock.
    _write_json(STATE, {"watermark": watermark, "first_seen_at": now.isoformat()})
    print("autoingest: new/changed file watermark — debounce clock started")
    return 0


def cutoff_check() -> int:
    """Alert if a business day passed its cutoff with no ingest."""
    now = _now_et()
    if now.weekday() >= 5:  # Sat/Sun
        return 0
    if _ingested_today():
        return 0
    _alert(
        "Bloomberg file not fresh by cutoff",
        f"No Bloomberg ingest for {now.date().isoformat()} by {now:%H:%M} ET. "
        "The SharePoint file may not have been saved today, or the auto-ingest "
        "never saw a stable file. Run /refreshdata manually if the file is ready.",
    )
    print("autoingest: cutoff alert sent")
    return 0


def main(argv: list[str]) -> int:
    if "--cutoff" in argv:
        return cutoff_check()
    return poll_and_maybe_ingest()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
