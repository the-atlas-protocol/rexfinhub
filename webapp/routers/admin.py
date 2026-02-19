"""
Admin router - Password-protected admin panel for trust management,
digest testing, screener scoring, and system status.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import date, datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Trust, FundStatus, AnalysisResult, ScreenerUpload

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="webapp/templates")
log = logging.getLogger(__name__)

ADMIN_PASSWORD = "123"
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REQUESTS_FILE = PROJECT_ROOT / "config" / "trust_requests.txt"
SUBSCRIBERS_FILE = PROJECT_ROOT / "config" / "digest_subscribers.txt"
RECIPIENTS_FILE = PROJECT_ROOT / "config" / "email_recipients.txt"
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


def _read_subscribers() -> list[dict]:
    """Read digest_subscribers.txt and return pending entries."""
    if not SUBSCRIBERS_FILE.exists():
        return []
    subs = []
    for line in SUBSCRIBERS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split("|")
        if len(parts) >= 3:
            subs.append({"status": parts[0], "email": parts[1], "timestamp": parts[2]})
    return [s for s in subs if s["status"] == "PENDING"]


def _update_subscriber_status(email: str, new_status: str):
    """Update a subscriber's status in digest_subscribers.txt."""
    if not SUBSCRIBERS_FILE.exists():
        return
    lines = SUBSCRIBERS_FILE.read_text(encoding="utf-8").splitlines()
    updated = []
    for line in lines:
        if f"|{email}|" in line and line.startswith("PENDING"):
            parts = line.split("|")
            parts[0] = new_status
            updated.append("|".join(parts))
        else:
            updated.append(line)
    SUBSCRIBERS_FILE.write_text("\n".join(updated) + "\n", encoding="utf-8")


def _add_to_recipients(email: str):
    """Append email to email_recipients.txt if not already present."""
    existing = set()
    if RECIPIENTS_FILE.exists():
        existing = {
            line.strip().lower()
            for line in RECIPIENTS_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        }
    if email.lower() not in existing:
        with RECIPIENTS_FILE.open("a", encoding="utf-8") as f:
            f.write(email.strip() + "\n")


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

    # Digest subscribers
    pending_subscribers = _read_subscribers()

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
        "pending_subscribers": pending_subscribers,
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

    # Also add to trusts.py registry so pipeline always picks it up
    try:
        from etp_tracker.trusts import add_trust
        add_trust(cik, name)
    except Exception as e:
        log.warning("Could not write to trusts.py (read-only on Render): %s", e)

    _update_request_status(cik, "APPROVED")
    return RedirectResponse("/admin/?approved=1", status_code=303)


@router.post("/requests/reject")
def reject_request(request: Request, cik: str = Form("")):
    """Reject a trust monitoring request."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    _update_request_status(cik, "REJECTED")
    return RedirectResponse("/admin/?rejected=1", status_code=303)


# --- Digest Send ---

@router.post("/digest/send")
def send_digest(request: Request, db: Session = Depends(get_db)):
    """Send the digest email now using database data."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    try:
        from etp_tracker.email_alerts import send_digest_from_db
        dashboard_url = str(request.base_url).rstrip("/")
        sent = send_digest_from_db(db, dashboard_url=dashboard_url)

        if sent:
            return RedirectResponse("/admin/?digest=sent", status_code=303)
        return RedirectResponse("/admin/?digest=fail", status_code=303)

    except Exception as e:
        from urllib.parse import quote
        log.error("Digest send failed: %s", e)
        return RedirectResponse(f"/admin/?digest=error&msg={quote(str(e)[:100])}", status_code=303)


# --- Subscriber Management ---

@router.post("/subscribers/approve")
def approve_subscriber(request: Request, email: str = Form("")):
    """Approve a digest subscriber - adds to email_recipients.txt."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    if email:
        _add_to_recipients(email)
        _update_subscriber_status(email, "APPROVED")

    return RedirectResponse("/admin/?approved_sub=1", status_code=303)


@router.post("/subscribers/reject")
def reject_subscriber(request: Request, email: str = Form("")):
    """Reject a digest subscriber request."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    if email:
        _update_subscriber_status(email, "REJECTED")

    return RedirectResponse("/admin/?rejected_sub=1", status_code=303)


# --- Screener Upload & Scoring ---

# Use the same data file path as the screener config
from screener.config import DATA_FILE as SCREENER_DATA_FILE


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

    # Invalidate 3x analysis cache (re-scoring)
    from webapp.services.screener_3x_cache import invalidate_cache
    invalidate_cache()

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
        recipients_file = PROJECT_ROOT / "config" / "email_recipients.txt"
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


# --- Ticker Quality Check ---

@router.get("/ticker-qc")
def ticker_qc(request: Request, db: Session = Depends(get_db)):
    """Ticker quality check - find duplicate and missing tickers across all funds."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    # Get all fund statuses with their trust names
    rows = db.execute(
        select(
            FundStatus.ticker,
            FundStatus.fund_name,
            FundStatus.series_id,
            FundStatus.class_contract_id,
            FundStatus.status,
            FundStatus.latest_form,
            Trust.name.label("trust_name"),
        )
        .join(Trust, Trust.id == FundStatus.trust_id)
        .order_by(FundStatus.ticker)
    ).all()

    # Build ticker -> funds mapping
    from collections import defaultdict
    ticker_map = defaultdict(list)
    missing_ticker = []
    total_funds = 0

    for r in rows:
        total_funds += 1
        ticker = (r.ticker or "").strip().upper()
        if not ticker or ticker in ("NAN", "N/A", "NONE", "TBD", "SYMBOL"):
            missing_ticker.append({
                "fund_name": r.fund_name,
                "series_id": r.series_id or "",
                "trust": r.trust_name,
                "status": r.status,
                "form": r.latest_form or "",
            })
        else:
            ticker_map[ticker].append({
                "fund_name": r.fund_name,
                "series_id": r.series_id or "",
                "class_id": r.class_contract_id or "",
                "trust": r.trust_name,
                "status": r.status,
                "form": r.latest_form or "",
            })

    # Find duplicates (same ticker, different series IDs)
    duplicates = {}
    for ticker, funds in ticker_map.items():
        series_ids = set(f["series_id"] for f in funds)
        if len(series_ids) > 1:
            cross_trust = len(set(f["trust"] for f in funds)) > 1
            duplicates[ticker] = {"funds": funds, "cross_trust": cross_trust}

    # Sort duplicates by severity (cross-trust first, then by count)
    sorted_dupes = sorted(
        duplicates.items(),
        key=lambda x: (-x[1]["cross_trust"], -len(x[1]["funds"]), x[0]),
    )

    return templates.TemplateResponse("admin_ticker_qc.html", {
        "request": request,
        "total_funds": total_funds,
        "total_tickers": len(ticker_map),
        "duplicates": sorted_dupes,
        "duplicate_count": len(duplicates),
        "missing_ticker": missing_ticker,
        "missing_count": len(missing_ticker),
    })
