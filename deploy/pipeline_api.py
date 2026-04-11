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
    """Current pipeline state."""
    return {
        "running": _state["running"],
        "last_result": _state["last_result"],
        "last_time": _state["last_time"],
        "server_time": datetime.now().isoformat(),
    }


@app.post("/pipeline/pull-sync")
def pull_sync(_: None = Depends(verify_key)):
    """Pull Bloomberg from SharePoint + sync to DB."""
    def _do():
        os.environ["SEC_CACHE_DIR"] = str(PROJECT_ROOT / "cache" / "sec")
        from webapp.services.graph_files import download_bloomberg_from_sharepoint
        from webapp.database import init_db, SessionLocal
        from webapp.services.market_sync import sync_market_data

        path = download_bloomberg_from_sharepoint()
        if not path:
            return "Bloomberg pull failed"

        init_db()
        db = SessionLocal()
        r = sync_market_data(db)
        db.close()
        return f"Synced {r.get('master_rows', 0)} rows"

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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
