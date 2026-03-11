"""
REST API router for N8N and external integrations.

All endpoints require X-API-Key header for authentication.
Prefix: /api/v1
"""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, BackgroundTasks, UploadFile, File
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Trust, Filing, FundStatus, PipelineRun

router = APIRouter(prefix="/api/v1", tags=["api"])


def _load_api_key() -> str:
    """Load API key from .env or environment."""
    env_file = Path(__file__).resolve().parent.parent.parent / "config" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("API_KEY", "")


def verify_api_key(x_api_key: str = Header(default="")):
    """Verify API key from X-API-Key header."""
    import hmac
    expected = _load_api_key()
    if not expected:
        raise HTTPException(status_code=503, detail="API not configured")
    if not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.get("/health")
def api_health():
    """Health check for the API."""
    return {"status": "ok", "version": "2.0.0"}


@router.get("/trusts")
def list_trusts(
    active_only: bool = True,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    """List all monitored trusts with fund counts."""
    query = select(
        Trust.id, Trust.cik, Trust.name, Trust.slug, Trust.is_rex, Trust.is_active,
        func.count(FundStatus.id).label("fund_count"),
    ).join(FundStatus, FundStatus.trust_id == Trust.id, isouter=True).group_by(Trust.id)

    if active_only:
        query = query.where(Trust.is_active == True)

    rows = db.execute(query).all()
    return [
        {
            "id": r.id, "cik": r.cik, "name": r.name, "slug": r.slug,
            "is_rex": r.is_rex, "is_active": r.is_active, "fund_count": r.fund_count,
        }
        for r in rows
    ]


@router.get("/funds")
def list_funds(
    status: str | None = None,
    trust: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    """Query funds with optional filters."""
    query = (
        select(FundStatus, Trust.name.label("trust_name"))
        .join(Trust, Trust.id == FundStatus.trust_id)
    )

    if status:
        query = query.where(FundStatus.status == status.upper())
    if trust:
        query = query.where(Trust.name.ilike(f"%{trust}%"))

    query = query.order_by(FundStatus.latest_filing_date.desc().nullslast()).limit(limit)
    rows = db.execute(query).all()

    return [
        {
            "id": r.FundStatus.id,
            "trust_name": r.trust_name,
            "fund_name": r.FundStatus.fund_name,
            "ticker": r.FundStatus.ticker,
            "status": r.FundStatus.status,
            "status_reason": r.FundStatus.status_reason,
            "effective_date": str(r.FundStatus.effective_date) if r.FundStatus.effective_date else None,
            "latest_form": r.FundStatus.latest_form,
            "latest_filing_date": str(r.FundStatus.latest_filing_date) if r.FundStatus.latest_filing_date else None,
            "series_id": r.FundStatus.series_id,
            "class_contract_id": r.FundStatus.class_contract_id,
        }
        for r in rows
    ]


@router.get("/filings/recent")
def recent_filings(
    days: int = 1,
    form: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    """Get recent filings within N days."""
    since = date.today() - timedelta(days=days)
    query = (
        select(Filing, Trust.name.label("trust_name"))
        .join(Trust, Trust.id == Filing.trust_id)
        .where(Filing.filing_date >= since)
        .order_by(Filing.filing_date.desc())
        .limit(limit)
    )

    if form:
        query = query.where(Filing.form == form.upper())

    rows = db.execute(query).all()
    return [
        {
            "id": r.Filing.id,
            "trust_name": r.trust_name,
            "accession_number": r.Filing.accession_number,
            "form": r.Filing.form,
            "filing_date": str(r.Filing.filing_date) if r.Filing.filing_date else None,
            "primary_link": r.Filing.primary_link,
        }
        for r in rows
    ]


@router.get("/pipeline/status")
def pipeline_status(db: Session = Depends(get_db), _: None = Depends(verify_api_key)):
    """Get the status of the most recent pipeline run."""
    run = db.execute(
        select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()

    if not run:
        return {"status": "never_run", "last_run": None}

    return {
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "trusts_processed": run.trusts_processed,
        "filings_found": run.filings_found,
        "error_message": run.error_message,
        "triggered_by": run.triggered_by,
    }


@router.post("/pipeline/run")
def trigger_pipeline(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    """Trigger a pipeline run in the background."""
    from webapp.services.pipeline_service import run_pipeline_background, is_pipeline_running

    if is_pipeline_running():
        return {"status": "already_running", "message": "Pipeline is already running"}

    background_tasks.add_task(run_pipeline_background)
    return {"status": "started", "message": "Pipeline run started in background"}


@router.post("/digest/send")
def send_digest(
    _: None = Depends(verify_api_key),
):
    """Send the daily digest email."""
    from etp_tracker.email_alerts import send_digest_email
    sent = send_digest_email(Path("outputs"))
    return {"sent": sent}


@router.post("/db/upload")
async def upload_db(
    file: UploadFile = File(...),
    _: None = Depends(verify_api_key),
):
    """Replace the database file with an uploaded copy.

    Accepts raw or gzipped (.gz) SQLite DB files.
    Streams to disk in 64KB chunks to stay under Render's 512MB RAM.
    Gzipped uploads decompress in streaming chunks (never buffers full file).
    """
    import gzip as _gzip
    from webapp.database import DB_PATH, engine

    is_gzipped = (file.filename or "").endswith(".gz") or file.content_type == "application/gzip"
    tmp_path = str(DB_PATH) + ".uploading"
    try:
        total_in = 0
        total_out = 0
        if is_gzipped:
            # Render disk is 1GB. Old DB (~455MB) + gz (~63MB) + decompressed (~455MB) = 973MB.
            # To stay under limit: stream gz to disk, delete old DB, then decompress.
            gz_tmp = tmp_path + ".gz"
            # Step 1: Stream compressed data to disk (455MB existing + 63MB gz = 518MB)
            with open(gz_tmp, "wb") as f:
                while True:
                    chunk = await file.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    total_in += len(chunk)
            # Step 2: Dispose engine and remove old DB to free ~455MB
            engine.dispose()
            try:
                os.unlink(str(DB_PATH))
            except OSError:
                pass
            # Step 3: Decompress gz -> new DB (63MB gz + 455MB out = 518MB peak)
            with _gzip.open(gz_tmp, "rb") as gz_in:
                with open(tmp_path, "wb") as f_out:
                    while True:
                        chunk = gz_in.read(65536)
                        if not chunk:
                            break
                        f_out.write(chunk)
                        total_out += len(chunk)
            try:
                os.unlink(gz_tmp)
            except OSError:
                pass
        else:
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = await file.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    total_out += len(chunk)
            total_in = total_out
            # Dispose existing connections so the file isn't locked
            engine.dispose()

        # Move new DB into place
        shutil.move(tmp_path, str(DB_PATH))

        in_mb = total_in / 1_000_000
        out_mb = total_out / 1_000_000
        msg = f"Database replaced ({out_mb:.1f} MB)"
        if is_gzipped:
            msg = f"Database replaced ({in_mb:.1f} MB gzipped -> {out_mb:.1f} MB)"
        return {"status": "ok", "message": msg}
    except Exception as e:
        for p in [tmp_path, tmp_path + ".gz"]:
            try:
                os.unlink(p)
            except OSError:
                pass
        import logging
        logging.getLogger(__name__).error("DB upload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
