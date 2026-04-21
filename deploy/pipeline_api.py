"""Lightweight pipeline API for the VPS.

Runs on the Hetzner VPS alongside the systemd timers.
The Render admin panel calls these endpoints to trigger operations.
Authenticated via API key (same as Render upload key).

Usage:
    /home/jarvis/venv/bin/python deploy/pipeline_api.py

Endpoints:
    POST /pipeline/pull-sync     Pull Bloomberg + sync market data
    POST /pipeline/sec-scrape    Run SEC filing scrape (background)
    POST /pipeline/upload-render Upload lean DB to Render
    GET  /pipeline/status        Current pipeline state
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s")
log = logging.getLogger("pipeline_api")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
os.chdir(PROJECT_ROOT)
sys.path.insert(0, str(PROJECT_ROOT))

app = FastAPI(title="REX Pipeline API", docs_url=None, redoc_url=None)
app.add_middleware(CORSMiddleware, allow_origins=["https://rex-etp-tracker.onrender.com"], allow_methods=["POST", "GET"], allow_headers=["*"])

# Auth
API_KEY = os.environ.get("API_KEY", "")
if not API_KEY:
    try:
        env_file = PROJECT_ROOT / "config" / ".env"
        for line in env_file.read_text().splitlines():
            if line.startswith("API_KEY="):
                API_KEY = line.split("=", 1)[1].strip()
    except Exception:
        pass


def verify_key(x_api_key: str = Header("")):
    if not API_KEY or x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


# Background task state
_state = {
    "running": None,  # "pull-sync" | "sec-scrape" | "upload" | None
    "last_result": None,
    "last_time": None,
}
_state_lock = threading.Lock()

# Validate API key at startup
if not API_KEY:
    log.critical("API_KEY not set — pipeline API will reject all requests")


def _run_in_background(name: str, fn):
    """Run a function in a background thread, tracking state."""
    with _state_lock:
        if _state["running"]:
            return {"status": "busy", "running": _state["running"]}
        _state["running"] = name

    def _worker():
        try:
            result = fn()
            _state["last_result"] = {"name": name, "status": "ok", "detail": str(result)}
        except Exception as e:
            _state["last_result"] = {"name": name, "status": "error", "detail": str(e)}
            log.error("%s failed: %s", name, e)
        finally:
            _state["running"] = None
            _state["last_time"] = datetime.now().isoformat()

    threading.Thread(target=_worker, daemon=True).start()
    return {"status": "started", "task": name}


@app.get("/pipeline/status")
def status(_: None = Depends(verify_key)):
    """Current pipeline state + server health."""
    import shutil
    disk = shutil.disk_usage("/home/jarvis")
    return {
        "running": _state["running"],
        "last_result": _state["last_result"],
        "last_time": _state["last_time"],
        "server_time": datetime.now().isoformat(),
        "disk_free_gb": round(disk.free / (1024**3), 1),
        "disk_total_gb": round(disk.total / (1024**3), 1),
    }


@app.post("/pipeline/pull-sync")
def pull_sync(_: None = Depends(verify_key)):
    """Pull Bloomberg + market sync + prebake reports + upload to Render.

    This endpoint runs the full publish chain so a manual trigger from admin
    updates both VPS state AND what Render serves. Before this fix, pull-sync
    only updated the VPS DB, leaving Render with stale prebaked HTML and a
    stale DB snapshot until the next daily timer (~24h later).
    """
    def _do():
        import subprocess
        os.environ["SEC_CACHE_DIR"] = str(PROJECT_ROOT / "cache" / "sec")
        from webapp.services.graph_files import download_bloomberg_from_sharepoint
        from webapp.database import init_db, SessionLocal
        from webapp.services.market_sync import sync_market_data

        # 1. Pull latest Bloomberg from SharePoint via Graph API
        path = download_bloomberg_from_sharepoint()
        if not path:
            return "Bloomberg pull failed"

        # 2. Sync master/time_series/report_cache tables
        init_db()
        db = SessionLocal()
        try:
            r = sync_market_data(db)
            master_rows = r.get("master_rows", 0)
        finally:
            db.close()

        # 3. Prebake all 9 reports and push static HTML to Render.
        # Use the same Python that's running the API — it's already the venv.
        import sys as _sys
        prebake_log = ""
        try:
            proc = subprocess.run(
                [_sys.executable, str(PROJECT_ROOT / "scripts" / "prebake_reports.py")],
                cwd=str(PROJECT_ROOT),
                capture_output=True, text=True, timeout=1200,
            )
            last_line = ""
            combined = (proc.stdout or "") + (proc.stderr or "")
            lines = [l for l in combined.splitlines() if l.strip()]
            if lines:
                last_line = lines[-1][:120]
            prebake_log = f"prebake rc={proc.returncode}; {last_line}"
        except Exception as e:
            prebake_log = f"prebake ERROR: {e}"

        # 4. Upload the freshly-synced DB to Render (lean, gzipped, atomic swap)
        upload_log = ""
        try:
            from scripts.run_daily import upload_db_to_render
            upload_db_to_render()
            upload_log = "db uploaded"
        except Exception as e:
            upload_log = f"db upload ERROR: {e}"

        return f"Synced {master_rows} rows | {prebake_log} | {upload_log}"

    return _run_in_background("pull-sync", _do)


@app.post("/pipeline/sec-scrape")
def sec_scrape(_: None = Depends(verify_key)):
    """Run SEC filing scrape (all trusts, incremental)."""
    def _do():
        os.environ["SEC_CACHE_DIR"] = str(PROJECT_ROOT / "cache" / "sec")
        from etp_tracker.run_pipeline import run_pipeline, load_ciks_from_db

        ciks, overrides = load_ciks_from_db()
        result = run_pipeline(
            ciks=ciks, overrides=overrides, since="2024-01-01",
            refresh_submissions=True,
            user_agent="REX-ETP-Tracker/2.0 (relasmar@rexfin.com)",
            etf_only=True,
        )
        return f"Processed {result}"

    return _run_in_background("sec-scrape", _do)


@app.post("/pipeline/upload-render")
def upload_render(_: None = Depends(verify_key)):
    """Upload lean DB to Render."""
    def _do():
        from scripts.run_daily import upload_db_to_render
        upload_db_to_render()
        return "Uploaded"

    return _run_in_background("upload-render", _do)


# ---------------------------------------------------------------------------
# Classification passthrough — Render admin calls these so the writes land on
# the VPS (source of truth for rules + pipeline), not on Render's ephemeral
# filesystem where they get clobbered by the nightly DB upload.
# ---------------------------------------------------------------------------

@app.post("/pipeline/classification/approve/{proposal_id}")
def classification_approve(proposal_id: int, _: None = Depends(verify_key)):
    """Approve a ClassificationProposal on the VPS: write to data/rules/*.csv
    and mark the proposal status=approved. Writes here propagate back to
    Render on the next DB upload.
    """
    import json as _json
    from webapp.database import init_db, SessionLocal
    from webapp.models import ClassificationProposal
    from tools.rules_editor.classify_engine import apply_classifications

    init_db()
    db = SessionLocal()
    try:
        p = db.query(ClassificationProposal).filter(
            ClassificationProposal.id == proposal_id
        ).first()
        if not p:
            raise HTTPException(404, f"Proposal {proposal_id} not found")
        if p.status != "pending":
            return {"status": "already_resolved", "prior_status": p.status,
                    "ticker": p.ticker}

        candidate = {
            "ticker": p.ticker,
            "etp_category": p.proposed_category,
            "attributes": _json.loads(p.attributes_json or "{}"),
        }
        result = apply_classifications([candidate])

        p.status = "approved"
        p.reviewed_at = datetime.utcnow()
        db.commit()
        return {"status": "approved", "ticker": p.ticker,
                "category": p.proposed_category, "applied": result}
    finally:
        db.close()


@app.post("/pipeline/classification/reject/{proposal_id}")
def classification_reject(proposal_id: int, _: None = Depends(verify_key)):
    """Mark a ClassificationProposal rejected on the VPS."""
    from webapp.database import init_db, SessionLocal
    from webapp.models import ClassificationProposal

    init_db()
    db = SessionLocal()
    try:
        p = db.query(ClassificationProposal).filter(
            ClassificationProposal.id == proposal_id
        ).first()
        if not p:
            raise HTTPException(404, f"Proposal {proposal_id} not found")
        if p.status != "pending":
            return {"status": "already_resolved", "prior_status": p.status,
                    "ticker": p.ticker}
        p.status = "rejected"
        p.reviewed_at = datetime.utcnow()
        db.commit()
        return {"status": "rejected", "ticker": p.ticker}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# One-click "prepare daily reports" — runs the full chain and test-sends every
# report to a single recipient so the operator can review without touching
# production distribution lists.
# ---------------------------------------------------------------------------

@app.post("/pipeline/prepare-daily")
def prepare_daily(_: None = Depends(verify_key)):
    """Pull Bloomberg → market sync → prebake → Render upload → test-send to
    relasmar@rexfin.com. One button, background task, single status report.
    """
    def _do():
        os.environ["SEC_CACHE_DIR"] = str(PROJECT_ROOT / "cache" / "sec")
        logs: list[str] = []

        # 1. Bloomberg pull from SharePoint
        try:
            from webapp.services.graph_files import download_bloomberg_from_sharepoint
            path = download_bloomberg_from_sharepoint()
            if not path:
                logs.append("bloomberg pull FAILED (download returned None)")
                return " | ".join(logs)
            logs.append("bloomberg pulled")
        except Exception as e:
            logs.append(f"bloomberg pull ERROR: {e}")
            return " | ".join(logs)

        # 2. Market sync into DB
        from webapp.database import init_db, SessionLocal
        from webapp.services.market_sync import sync_market_data
        init_db()
        db = SessionLocal()
        try:
            r = sync_market_data(db)
            logs.append(f"market_sync {r.get('master_rows', 0)} rows")
        except Exception as e:
            logs.append(f"market_sync ERROR: {e}")
        finally:
            db.close()

        # 3. Prebake all 9 reports + upload each to Render
        try:
            proc = subprocess.run(
                [sys.executable, str(PROJECT_ROOT / "scripts" / "prebake_reports.py")],
                cwd=str(PROJECT_ROOT), capture_output=True, text=True, timeout=1500,
            )
            logs.append(f"prebake rc={proc.returncode}")
        except Exception as e:
            logs.append(f"prebake ERROR: {e}")

        # 4. DB upload to Render (so the admin site sees new classifications etc.)
        try:
            from scripts.run_daily import upload_db_to_render
            upload_db_to_render()
            logs.append("db uploaded")
        except Exception as e:
            logs.append(f"db upload ERROR: {e}")

        # 5. Test-send every report to relasmar only (bypass gate + production list)
        try:
            from etp_tracker.email_alerts import (
                _send_html_digest, build_digest_html_from_db,
            )
            from etp_tracker.weekly_digest import build_weekly_digest_html
            from webapp.services.report_emails import (
                build_li_email, build_cc_email, build_flow_email, build_autocall_email,
            )
            date_str = datetime.now().strftime("%m/%d/%Y")
            dash = "https://rexfinhub.com"

            # Open gate briefly so graph_email's internal gate passes.
            gate = PROJECT_ROOT / "config" / ".send_enabled"
            original = gate.read_text() if gate.exists() else "false"
            gate.write_text("true")

            try:
                init_db()
                _db = SessionLocal()
                try:
                    # Test-send only the reports actually scheduled for today.
                    # Daily ships Mon-Fri. Weekly bundle + Autocall ship Monday.
                    # Weekday: Mon=0, Tue=1, ..., Sun=6.
                    _weekday = datetime.now().weekday()
                    reports = [
                        ("REX Daily ETP Report", "daily",
                         lambda: (build_digest_html_from_db(_db, dashboard_url=dash, edition="daily"), [])),
                    ]
                    if _weekday == 0:  # Monday — also ship weekly bundle + autocall
                        reports += [
                            ("REX Weekly ETP Report",            "weekly",
                             lambda: (build_weekly_digest_html(_db, dashboard_url=dash), [])),
                            ("REX ETP Leverage & Inverse Report","li",
                             lambda: build_li_email(dashboard_url=dash, db=_db)),
                            ("REX ETP Income Report",            "income",
                             lambda: build_cc_email(dashboard_url=dash, db=_db)),
                            ("REX ETP Flow Report",              "flow",
                             lambda: build_flow_email(dashboard_url=dash, db=_db)),
                            ("Autocallable ETF Weekly Update",   "autocall",
                             lambda: build_autocall_email(dashboard_url=dash, db=_db)),
                        ]
                    sent = 0
                    failed: list[str] = []
                    for title, edition, builder in reports:
                        try:
                            html, images = builder()
                            ok = _send_html_digest(
                                html_body=html, recipients=["relasmar@rexfin.com"],
                                subject_override=f"[TEST] {title}: {date_str}",
                                images=images, edition=edition,
                            )
                            if ok:
                                sent += 1
                            else:
                                failed.append(title)
                        except Exception as e:
                            failed.append(f"{title}({e})")
                    logs.append(f"test-sent {sent}/{len(reports)} to relasmar")
                    if failed:
                        logs.append(f"send fails: {', '.join(failed)}")
                finally:
                    _db.close()
            finally:
                gate.write_text(original)
        except Exception as e:
            logs.append(f"test-send ERROR: {e}")

        return " | ".join(logs)

    return _run_in_background("prepare-daily", _do)


@app.post("/pipeline/recipients/add")
def add_recipient_api(
    email: str, list_type: str, _: None = Depends(verify_key)
):
    """Add a recipient to the VPS database."""
    from webapp.database import init_db, SessionLocal
    from webapp.services.recipients import add_recipient, VALID_LIST_TYPES

    if list_type not in VALID_LIST_TYPES:
        raise HTTPException(400, f"Invalid list_type: {list_type}")

    init_db()
    db = SessionLocal()
    try:
        ok = add_recipient(db, email, list_type, added_by="render-admin")
        return {"status": "ok", "added": ok, "email": email, "list_type": list_type}
    finally:
        db.close()


@app.post("/pipeline/recipients/remove")
def remove_recipient_api(
    email: str, list_type: str, _: None = Depends(verify_key)
):
    """Remove a recipient from the VPS database."""
    from webapp.database import init_db, SessionLocal
    from webapp.services.recipients import remove_recipient

    init_db()
    db = SessionLocal()
    try:
        ok = remove_recipient(db, email, list_type)
        return {"status": "ok", "removed": ok, "email": email, "list_type": list_type}
    finally:
        db.close()


@app.get("/pipeline/recipients")
def list_recipients_api(_: None = Depends(verify_key)):
    """List all active recipients from VPS database."""
    from webapp.database import init_db, SessionLocal
    from webapp.services.recipients import get_all_recipients_by_list

    init_db()
    db = SessionLocal()
    try:
        return get_all_recipients_by_list(db)
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
