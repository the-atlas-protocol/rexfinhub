"""Admin health dashboard — single page showing what's working and what isn't.

Answers the question "is the system healthy?" without SSH-ing into the VPS
or clicking through five admin pages. Shows:

  - Disk free (VPS + Render if reachable)
  - Latest market pipeline run (status, started_at, duration)
  - Last email send (subject, recipients, allowed)
  - Watcher state (atom + worker last-seen, pending alert count)
  - MicroSectors freshness (Bloomberg file staleness warnings)
  - Send gate state (.send_enabled)
  - Prebaked reports (count + latest bake time)

No external calls beyond the local DB. Read-only. Cheap.
"""
from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from webapp.database import get_db, get_live_feed_db
from webapp.models import (
    FilingAlert, MktPipelineRun, LiveFeedItem, Trust, Filing, FundStatus,
)
from webapp.services.admin_auth import load_admin_password

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin-health"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SEND_GATE = PROJECT_ROOT / "config" / ".send_enabled"
SEND_AUDIT = PROJECT_ROOT / "data" / ".send_audit.json"
SEND_LOG = PROJECT_ROOT / "data" / ".send_log.json"
PREBAKED_DIR = PROJECT_ROOT / "data" / "prebaked_reports"
GATE_LOG = PROJECT_ROOT / "data" / ".gate_state_log.jsonl"
PREFLIGHT_TOKEN = PROJECT_ROOT / "data" / ".preflight_token"
PREFLIGHT_DECISION = PROJECT_ROOT / "data" / ".preflight_decision.json"
PREFLIGHT_RESULT = PROJECT_ROOT / "data" / ".preflight_result.json"
VPS_COMMIT = PROJECT_ROOT / ".vps_commit"
BBG_FILE = PROJECT_ROOT / "data" / "DASHBOARD" / "bloomberg_daily_file.xlsm"

_ADMIN_PASSWORD = load_admin_password()


def _is_admin(request: Request) -> bool:
    return bool(
        request.session.get("is_admin")
        or (request.cookies.get("admin_auth") == _ADMIN_PASSWORD and _ADMIN_PASSWORD)
    )


def _fmt_age(dt: datetime | None) -> str:
    if not dt:
        return "—"
    now = datetime.now()
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    delta = now - dt
    sec = int(delta.total_seconds())
    if sec < 0:
        return "future?"
    if sec < 60:
        return f"{sec}s ago"
    if sec < 3600:
        return f"{sec // 60}m ago"
    if sec < 86400:
        return f"{sec // 3600}h ago"
    return f"{sec // 86400}d ago"


def _disk_info() -> dict:
    try:
        total, used, free = shutil.disk_usage(str(PROJECT_ROOT))
        return {
            "free_gb": round(free / (1024**3), 1),
            "used_pct": round(100.0 * used / total, 1),
            "total_gb": round(total / (1024**3), 1),
        }
    except Exception as e:
        return {"error": str(e)}


def _latest_pipeline_run(db: Session) -> dict:
    row = db.execute(
        select(MktPipelineRun)
        .order_by(MktPipelineRun.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if not row:
        return {"id": None}
    duration = None
    if row.started_at and row.finished_at:
        duration = int((row.finished_at - row.started_at).total_seconds())
    return {
        "id": row.id,
        "started_at": row.started_at.isoformat() if row.started_at else None,
        "age": _fmt_age(row.started_at),
        "status": row.status,
        "duration_sec": duration,
        "source_file": row.source_file,
        "error": row.error_message,
    }


def _last_send() -> dict:
    if not SEND_AUDIT.exists():
        return {"found": False}
    try:
        entries = json.loads(SEND_AUDIT.read_text(encoding="utf-8"))
        if not entries:
            return {"found": False}
        last = entries[-1]
        ts = last.get("timestamp")
        last_dt = None
        if ts:
            try:
                last_dt = datetime.fromisoformat(ts)
            except Exception:
                pass
        return {
            "found": True,
            "timestamp": ts,
            "age": _fmt_age(last_dt),
            "subject": last.get("subject"),
            "recipient_count": last.get("recipient_count"),
            "allowed": last.get("allowed"),
        }
    except Exception as e:
        return {"found": False, "error": str(e)}


def _watcher_state(db: Session) -> dict:
    pending = db.execute(
        select(func.count()).select_from(FilingAlert)
        .where(FilingAlert.enrichment_status == 0)
    ).scalar() or 0
    done = db.execute(
        select(func.count()).select_from(FilingAlert)
        .where(FilingAlert.enrichment_status == 1)
    ).scalar() or 0
    failed = db.execute(
        select(func.count()).select_from(FilingAlert)
        .where(FilingAlert.enrichment_status == 2)
    ).scalar() or 0
    newest = db.execute(
        select(FilingAlert.detected_at)
        .order_by(FilingAlert.detected_at.desc())
        .limit(1)
    ).scalar()
    return {
        "pending": pending,
        "done": done,
        "failed": failed,
        "newest_detected": newest.isoformat() if newest else None,
        "newest_age": _fmt_age(newest),
    }


def _live_feed_state(live_db: Session) -> dict:
    total = live_db.execute(select(func.count()).select_from(LiveFeedItem)).scalar() or 0
    newest = live_db.execute(
        select(LiveFeedItem.detected_at)
        .order_by(LiveFeedItem.detected_at.desc())
        .limit(1)
    ).scalar()
    return {
        "rows": total,
        "newest_age": _fmt_age(newest),
    }


def _send_gate_state() -> dict:
    if not SEND_GATE.exists():
        return {"exists": False, "enabled": None}
    try:
        content = SEND_GATE.read_text(encoding="utf-8").strip().lower()
        return {"exists": True, "enabled": content == "true", "raw": content}
    except Exception as e:
        return {"exists": True, "error": str(e)}


def _prebake_state() -> dict:
    if not PREBAKED_DIR.exists():
        return {"dir_exists": False, "files": 0}
    files = sorted(PREBAKED_DIR.glob("*.html"))
    newest_mtime = None
    for f in files:
        try:
            m = datetime.fromtimestamp(f.stat().st_mtime)
            if newest_mtime is None or m > newest_mtime:
                newest_mtime = m
        except OSError:
            continue
    return {
        "dir_exists": True,
        "files": len(files),
        "newest_age": _fmt_age(newest_mtime),
        "names": [f.stem for f in files],
    }


def _bbg_freshness() -> dict:
    """Bloomberg xlsm file mtime + age."""
    if not BBG_FILE.exists():
        return {"exists": False}
    try:
        mtime = datetime.fromtimestamp(BBG_FILE.stat().st_mtime)
        age_h = (datetime.now() - mtime).total_seconds() / 3600
        return {
            "exists": True,
            "mtime": mtime.isoformat(timespec="seconds"),
            "age": _fmt_age(mtime),
            "age_hours": round(age_h, 1),
            "stale": age_h > 12,
        }
    except Exception as e:
        return {"exists": True, "error": str(e)}


def _preflight_token_state() -> dict:
    """Age of data/.preflight_token (idempotency token from last preflight run)."""
    if not PREFLIGHT_TOKEN.exists():
        return {"exists": False}
    try:
        payload = json.loads(PREFLIGHT_TOKEN.read_text(encoding="utf-8"))
        created = payload.get("created_et")
        created_dt = None
        if created:
            try:
                created_dt = datetime.fromisoformat(created)
                if created_dt.tzinfo is not None:
                    created_dt = created_dt.replace(tzinfo=None)
            except Exception:
                pass
        age_h = None
        if created_dt:
            age_h = round((datetime.now() - created_dt).total_seconds() / 3600, 1)
        return {
            "exists": True,
            "token": payload.get("token", "?")[:8] + "...",
            "created_et": created,
            "age": _fmt_age(created_dt),
            "age_hours": age_h,
            "valid_for_hours": payload.get("valid_for_hours", 4),
            "expired": age_h is not None and age_h > payload.get("valid_for_hours", 4),
        }
    except Exception as e:
        return {"exists": True, "error": str(e)}


def _preflight_decision_state() -> dict:
    """State of data/.preflight_decision.json — GO / HOLD / missing."""
    if not PREFLIGHT_DECISION.exists():
        # Also check .preflight_result.json for overall_status
        if PREFLIGHT_RESULT.exists():
            try:
                result = json.loads(PREFLIGHT_RESULT.read_text(encoding="utf-8"))
                return {
                    "exists": False,
                    "action": None,
                    "preflight_status": result.get("overall_status"),
                    "preflight_ts": result.get("timestamp"),
                }
            except Exception:
                pass
        return {"exists": False, "action": None}
    try:
        payload = json.loads(PREFLIGHT_DECISION.read_text(encoding="utf-8"))
        recorded = payload.get("recorded_et")
        recorded_dt = None
        if recorded:
            try:
                recorded_dt = datetime.fromisoformat(recorded)
                if recorded_dt.tzinfo is not None:
                    recorded_dt = recorded_dt.replace(tzinfo=None)
            except Exception:
                pass
        preflight_status = None
        if PREFLIGHT_RESULT.exists():
            try:
                result = json.loads(PREFLIGHT_RESULT.read_text(encoding="utf-8"))
                preflight_status = result.get("overall_status")
            except Exception:
                pass
        return {
            "exists": True,
            "action": payload.get("action", "?").upper(),
            "token": payload.get("token", "?")[:8] + "...",
            "recorded_et": recorded,
            "age": _fmt_age(recorded_dt),
            "reason": payload.get("reason", ""),
            "preflight_status": preflight_status,
        }
    except Exception as e:
        return {"exists": True, "error": str(e)}


def _gate_transitions() -> dict:
    """Last 3 gate state transitions from data/.gate_state_log.jsonl."""
    if not GATE_LOG.exists():
        return {"exists": False, "transitions": []}
    try:
        lines = GATE_LOG.read_text(encoding="utf-8").strip().splitlines()
        recent = []
        for raw in reversed(lines):
            if len(recent) >= 3:
                break
            try:
                entry = json.loads(raw)
                ts = entry.get("timestamp")
                ts_dt = None
                if ts:
                    try:
                        ts_dt = datetime.fromisoformat(ts)
                        if ts_dt.tzinfo is not None:
                            ts_dt = ts_dt.replace(tzinfo=None)
                    except Exception:
                        pass
                recent.append({
                    "timestamp": ts,
                    "age": _fmt_age(ts_dt),
                    "action": entry.get("action"),
                    "state": entry.get("state"),
                    "actor": entry.get("actor"),
                    "note": entry.get("note", ""),
                })
            except Exception:
                continue
        return {"exists": True, "transitions": recent}
    except Exception as e:
        return {"exists": True, "transitions": [], "error": str(e)}


def _today_send_status() -> dict:
    """Did the daily send fire today? Read from data/.send_log.json."""
    result: dict = {"fired": False, "recipients": [], "allowed": None}
    # Primary: .send_log.json (structured per-send log written by graph_email)
    if SEND_LOG.exists():
        try:
            entries = json.loads(SEND_LOG.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                entries = [entries]
            today = datetime.now().date().isoformat()
            for entry in reversed(entries):
                ts = entry.get("timestamp", "")
                if ts.startswith(today):
                    result["fired"] = True
                    result["timestamp"] = ts
                    result["recipients"] = entry.get("recipients", [])
                    result["allowed"] = entry.get("allowed")
                    result["subject"] = entry.get("subject")
                    result["recipient_count"] = len(result["recipients"])
                    break
            return result
        except Exception:
            pass
    # Fallback: .send_audit.json
    if SEND_AUDIT.exists():
        try:
            entries = json.loads(SEND_AUDIT.read_text(encoding="utf-8"))
            if not isinstance(entries, list):
                entries = [entries]
            today = datetime.now().date().isoformat()
            for entry in reversed(entries):
                ts = entry.get("timestamp", "")
                if ts.startswith(today):
                    result["fired"] = True
                    result["timestamp"] = ts
                    result["allowed"] = entry.get("allowed")
                    result["subject"] = entry.get("subject")
                    result["recipient_count"] = entry.get("recipient_count")
                    break
        except Exception:
            pass
    return result


def _vps_freshness() -> dict:
    """VPS code freshness — compare .vps_commit to local HEAD."""
    local_head = None
    try:
        import subprocess
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True,
            cwd=str(PROJECT_ROOT), timeout=5,
        )
        if r.returncode == 0:
            local_head = r.stdout.strip()[:12]
    except Exception:
        pass

    if not VPS_COMMIT.exists():
        return {"vps_commit": None, "local_head": local_head, "in_sync": None, "file_exists": False}
    try:
        vps_commit = VPS_COMMIT.read_text(encoding="utf-8").strip()[:12]
        in_sync = vps_commit == local_head if (vps_commit and local_head) else None
        return {
            "vps_commit": vps_commit,
            "local_head": local_head,
            "in_sync": in_sync,
            "file_exists": True,
        }
    except Exception as e:
        return {"vps_commit": None, "local_head": local_head, "in_sync": None,
                "file_exists": True, "error": str(e)}


def _db_counts(db: Session) -> dict:
    return {
        "trusts": db.execute(select(func.count()).select_from(Trust)).scalar() or 0,
        "trusts_active": db.execute(
            select(func.count()).select_from(Trust).where(Trust.is_active == True)
        ).scalar() or 0,
        "filings": db.execute(select(func.count()).select_from(Filing)).scalar() or 0,
        "fund_statuses": db.execute(select(func.count()).select_from(FundStatus)).scalar() or 0,
    }


@router.get("/health", response_class=HTMLResponse)
def health_page(
    request: Request,
    db: Session = Depends(get_db),
    live_db: Session = Depends(get_live_feed_db),
):
    """Admin health dashboard. Requires session auth."""
    if not _is_admin(request):
        return RedirectResponse("/login?next=/admin/health", status_code=302)

    ctx = {
        "request": request,
        "disk": _disk_info(),
        "pipeline": _latest_pipeline_run(db),
        "last_send": _last_send(),
        "watchers": _watcher_state(db),
        "live_feed": _live_feed_state(live_db),
        "send_gate": _send_gate_state(),
        "prebake": _prebake_state(),
        "db_counts": _db_counts(db),
        # Observability additions (Sys-E Fix 2)
        "bbg": _bbg_freshness(),
        "preflight_token": _preflight_token_state(),
        "preflight_decision": _preflight_decision_state(),
        "gate_transitions": _gate_transitions(),
        "today_send": _today_send_status(),
        "vps": _vps_freshness(),
        "n_expected_reports": len(report_registry.REGISTRY),
        "now": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return templates.TemplateResponse("admin_health.html", ctx)


@router.get("/health.json")
def health_json(
    request: Request,
    db: Session = Depends(get_db),
    live_db: Session = Depends(get_live_feed_db),
):
    """Same payload as /admin/health but JSON (for monitoring/automation)."""
    if not _is_admin(request):
        return {"error": "unauthorized"}, 401
    return {
        "disk": _disk_info(),
        "pipeline": _latest_pipeline_run(db),
        "last_send": _last_send(),
        "watchers": _watcher_state(db),
        "live_feed": _live_feed_state(live_db),
        "send_gate": _send_gate_state(),
        "prebake": _prebake_state(),
        "db_counts": _db_counts(db),
        # Observability additions (Sys-E Fix 2)
        "bbg": _bbg_freshness(),
        "preflight_token": _preflight_token_state(),
        "preflight_decision": _preflight_decision_state(),
        "gate_transitions": _gate_transitions(),
        "today_send": _today_send_status(),
        "vps": _vps_freshness(),
        "now": datetime.now().isoformat(),
    }
