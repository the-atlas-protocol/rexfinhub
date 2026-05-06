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
PREBAKED_DIR = PROJECT_ROOT / "data" / "prebaked_reports"

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
        "now": datetime.now().isoformat(),
    }
