"""
Admin router - Password-protected admin panel for trust management,
pipeline control, digest testing, and system status.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Trust, AnalysisResult, PipelineRun, ScreenerUpload

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="webapp/templates")
log = logging.getLogger(__name__)

ADMIN_PASSWORD = "123"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REQUESTS_FILE = PROJECT_ROOT / "trust_requests.txt"
OUTPUT_DIR = PROJECT_ROOT / "outputs"


def _is_admin(request: Request) -> bool:
    """Check if current session is admin-authenticated."""
    return request.session.get("is_admin", False)


def _read_requests() -> list[dict]:
    """Read trust_requests.txt and parse into list of dicts."""
    if not REQUESTS_FILE.exists():
        return []
    requests = []
    for line in REQUESTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) >= 4:
            requests.append({
                "status": parts[0],
                "cik": parts[1],
                "name": parts[2],
                "timestamp": parts[3],
            })
    return requests


def _update_request_status(cik: str, new_status: str):
    """Update a request's status in trust_requests.txt."""
    if not REQUESTS_FILE.exists():
        return
    lines = REQUESTS_FILE.read_text(encoding="utf-8").splitlines()
    updated = []
    for line in lines:
        if f"|{cik}|" in line and line.startswith("PENDING"):
            parts = line.split("|")
            parts[0] = new_status
            updated.append("|".join(parts))
        else:
            updated.append(line)
    REQUESTS_FILE.write_text("\n".join(updated) + "\n", encoding="utf-8")


# --- Login / Logout ---

@router.get("/")
def admin_page(request: Request, db: Session = Depends(get_db)):
    """Admin dashboard (requires login)."""
    if not _is_admin(request):
        return templates.TemplateResponse("admin_login.html", {
            "request": request,
            "error": None,
        })

    # Trust requests
    all_requests = _read_requests()
    pending_requests = [r for r in all_requests if r["status"] == "PENDING"]

    # Pipeline status - fix stale "running" records
    from webapp.services.pipeline_service import is_pipeline_running
    pipeline_running = is_pipeline_running()

    last_run = db.execute(
        select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()

    if last_run and last_run.status == "running" and not pipeline_running:
        last_run.status = "interrupted"
        last_run.finished_at = datetime.utcnow()
        last_run.error_message = "Server restarted while pipeline was running"
        db.commit()

    # AI analysis status
    from webapp.services.claude_service import is_configured as ai_configured
    today_start = datetime.combine(date.today(), datetime.min.time())
    ai_usage_today = db.execute(
        select(func.count(AnalysisResult.id))
        .where(AnalysisResult.created_at >= today_start)
    ).scalar() or 0

    # Screener status
    screener_upload = db.execute(
        select(ScreenerUpload).order_by(ScreenerUpload.uploaded_at.desc()).limit(1)
    ).scalar_one_or_none()

    # Detect screener data file on disk
    screener_data_available = SCREENER_DATA_FILE.exists()

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "pending_requests": pending_requests,
        "all_requests": all_requests,
        "last_run": last_run,
        "pipeline_running": pipeline_running,
        "ai_configured": ai_configured(),
        "ai_usage_today": ai_usage_today,
        "screener_upload": screener_upload,
        "screener_data_available": screener_data_available,
    })


@router.post("/login")
def admin_login(request: Request, password: str = Form("")):
    """Verify admin password."""
    if password == ADMIN_PASSWORD:
        request.session["is_admin"] = True
        return RedirectResponse("/admin/", status_code=303)

    return templates.TemplateResponse("admin_login.html", {
        "request": request,
        "error": "Wrong password.",
    })


@router.get("/logout")
def admin_logout(request: Request):
    """Clear admin session."""
    request.session.pop("is_admin", None)
    return RedirectResponse("/", status_code=302)


# --- Trust Request Management ---

@router.post("/requests/approve")
def approve_request(
    request: Request,
    cik: str = Form(""),
    name: str = Form(""),
    db: Session = Depends(get_db),
):
    """Approve a trust monitoring request - adds to DB for next pipeline run."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    if not cik or not name:
        return RedirectResponse("/admin/?error=missing_data", status_code=303)

    # Check if already exists
    existing = db.execute(
        select(Trust).where(Trust.cik == cik)
    ).scalar_one_or_none()

    if not existing:
        slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")
        db.add(Trust(
            cik=cik,
            name=name,
            slug=slug,
            is_rex=False,
            is_active=True,
            added_by="ADMIN",
        ))
        db.commit()

    _update_request_status(cik, "APPROVED")
    return RedirectResponse("/admin/?approved=1", status_code=303)


@router.post("/requests/reject")
def reject_request(request: Request, cik: str = Form("")):
    """Reject a trust monitoring request."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    _update_request_status(cik, "REJECTED")
    return RedirectResponse("/admin/?rejected=1", status_code=303)


# --- Pipeline Control ---

@router.post("/pipeline/run")
def trigger_pipeline(request: Request, background_tasks: BackgroundTasks):
    """Trigger a pipeline run in the background."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    from webapp.services.pipeline_service import run_pipeline_background, is_pipeline_running

    if is_pipeline_running():
        return RedirectResponse("/admin/?pipeline=running", status_code=303)

    # Check if outputs dir is writable (won't work on read-only deployments)
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return RedirectResponse("/admin/?pipeline=error&msg=Cannot+write+to+outputs+dir.+Run+pipeline+locally.", status_code=303)

    background_tasks.add_task(run_pipeline_background, "admin")
    return RedirectResponse("/admin/?pipeline=started", status_code=303)


# --- Digest Send ---

@router.post("/digest/send")
def send_digest(request: Request):
    """Send the digest email now using current pipeline data."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    try:
        # Verify output files exist
        if not OUTPUT_DIR.exists():
            return RedirectResponse("/admin/?digest=no_files", status_code=303)

        csv_count = sum(1 for _ in OUTPUT_DIR.rglob("*_4_Fund_Status.csv"))
        if csv_count == 0:
            return RedirectResponse("/admin/?digest=no_files", status_code=303)

        from etp_tracker.email_alerts import send_digest_email
        dashboard_url = str(request.base_url).rstrip("/")
        sent = send_digest_email(OUTPUT_DIR, dashboard_url=dashboard_url)

        if sent:
            return RedirectResponse("/admin/?digest=sent", status_code=303)
        return RedirectResponse("/admin/?digest=fail", status_code=303)

    except Exception as e:
        from urllib.parse import quote
        log.error("Digest send failed: %s", e)
        return RedirectResponse(f"/admin/?digest=error&msg={quote(str(e)[:100])}", status_code=303)


# --- Screener Upload & Scoring ---

SCREENER_DATA_DIR = PROJECT_ROOT / "data" / "SCREENER"

# Use the same data file path as the screener config
from screener.config import DATA_FILE as SCREENER_DATA_FILE


@router.post("/screener/upload")
async def screener_upload(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Upload Bloomberg Excel file and trigger scoring pipeline."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    try:
        # Validate file type
        if not file.filename.endswith(".xlsx"):
            return RedirectResponse("/admin/?screener=error&msg=Must+be+.xlsx+file", status_code=303)

        # Save file to the canonical path the data_loader expects
        SCREENER_DATA_DIR.mkdir(parents=True, exist_ok=True)
        content = await file.read()
        SCREENER_DATA_FILE.write_bytes(content)

        # Validate sheets
        import openpyxl
        wb = openpyxl.load_workbook(SCREENER_DATA_FILE, read_only=True)
        sheets = wb.sheetnames
        wb.close()
        if "stock_data" not in sheets or "etp_data" not in sheets:
            return RedirectResponse("/admin/?screener=error&msg=Missing+stock_data+or+etp_data+sheets", status_code=303)

        # Create upload record
        upload = ScreenerUpload(
            file_name=file.filename,
            uploaded_by="admin",
        )
        db.add(upload)
        db.commit()
        db.refresh(upload)

        # Trigger scoring in background
        from webapp.services.screener_service import run_screener_pipeline
        background_tasks.add_task(run_screener_pipeline, upload.id)

        return RedirectResponse("/admin/?screener=uploaded", status_code=303)

    except Exception as e:
        from urllib.parse import quote
        return RedirectResponse(f"/admin/?screener=error&msg={quote(str(e)[:100])}", status_code=303)


@router.post("/screener/rescore")
def screener_rescore(
    request: Request,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
):
    """Re-run scoring on existing data."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    from webapp.services.screener_service import is_screener_running

    if is_screener_running():
        return RedirectResponse("/admin/?screener=running", status_code=303)

    # Check the data file the loader expects
    if not SCREENER_DATA_FILE.exists():
        return RedirectResponse("/admin/?screener=error&msg=No+data+file.+Upload+Bloomberg+.xlsx+first.", status_code=303)

    # Create new upload record
    upload = ScreenerUpload(
        file_name=SCREENER_DATA_FILE.name,
        uploaded_by="admin-rescore",
    )
    db.add(upload)
    db.commit()
    db.refresh(upload)

    from webapp.services.screener_service import run_screener_pipeline
    background_tasks.add_task(run_screener_pipeline, upload.id)

    return RedirectResponse("/admin/?screener=scoring", status_code=303)


@router.post("/screener/email")
def screener_email_report(
    request: Request,
    db: Session = Depends(get_db),
):
    """Generate PDF and email the screener report."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    try:
        from webapp.services.graph_email import is_configured
        if not is_configured():
            return RedirectResponse("/admin/?screener=error&msg=Azure+Graph+API+not+configured", status_code=303)

        # Get latest results
        latest_upload = db.execute(
            select(ScreenerUpload)
            .where(ScreenerUpload.status == "completed")
            .order_by(ScreenerUpload.uploaded_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if not latest_upload:
            return RedirectResponse("/admin/?screener=error&msg=No+screener+results", status_code=303)

        from webapp.models import ScreenerResult
        results = db.execute(
            select(ScreenerResult)
            .where(ScreenerResult.upload_id == latest_upload.id)
            .order_by(ScreenerResult.composite_score.desc())
        ).scalars().all()

        result_dicts = [{
            "ticker": r.ticker, "sector": r.sector, "composite_score": r.composite_score,
            "predicted_aum": r.predicted_aum, "mkt_cap": r.mkt_cap,
            "call_oi_pctl": r.call_oi_pctl, "passes_filters": r.passes_filters,
            "filing_status": r.filing_status, "competitive_density": r.competitive_density,
            "competitor_count": r.competitor_count, "total_competitor_aum": r.total_competitor_aum,
        } for r in results]

        # Generate PDF
        from screener.report_generator import generate_executive_report
        pdf_bytes = generate_executive_report(
            results=result_dicts,
            model_info={"model_type": latest_upload.model_type, "r_squared": latest_upload.model_r_squared},
            data_date=latest_upload.uploaded_at.strftime("%B %d, %Y"),
        )

        # Send email
        from screener.email_report import send_screener_report
        # Use same recipient list as digest
        recipients_file = PROJECT_ROOT / "email_recipients.txt"
        recipients = []
        if recipients_file.exists():
            for line in recipients_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "@" in line:
                    recipients.append(line)

        if not recipients:
            recipients = ["relasmar@rexfin.com"]

        sent = send_screener_report(pdf_bytes, recipients)
        if sent:
            return RedirectResponse("/admin/?screener=emailed", status_code=303)
        return RedirectResponse("/admin/?screener=error&msg=Email+send+failed", status_code=303)

    except Exception as e:
        from urllib.parse import quote
        return RedirectResponse(f"/admin/?screener=error&msg={quote(str(e)[:100])}", status_code=303)
