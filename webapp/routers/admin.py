"""
Admin router - Password-protected admin panel for trust management,
digest testing, and system status.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path

import shutil

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Trust, FundStatus, AnalysisResult, TrustRequest, DigestSubscriber

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="webapp/templates")
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _load_admin_password() -> str:
    """Load ADMIN_PASSWORD from config/.env or environment."""
    import os
    env_file = PROJECT_ROOT / "config" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                if key.strip() == "ADMIN_PASSWORD":
                    return val.strip().strip('"').strip("'")
    return os.environ.get("ADMIN_PASSWORD", "")


ADMIN_PASSWORD = _load_admin_password()
RECIPIENTS_FILE = PROJECT_ROOT / "config" / "email_recipients.txt"
OUTPUT_DIR = PROJECT_ROOT / "outputs"


def normalize_cik(cik: str) -> str:
    """Strip leading zeros for consistent CIK comparison."""
    return str(int(cik)) if cik and cik.strip().isdigit() else cik.strip()


def _is_admin(request: Request) -> bool:
    """Check if current session is admin-authenticated."""
    return request.session.get("is_admin", False)


# --- Login / Logout ---

@router.get("/")
def admin_page(request: Request, db: Session = Depends(get_db)):
    """Admin dashboard (requires login)."""
    if not _is_admin(request):
        return templates.TemplateResponse("admin_login.html", {
            "request": request,
            "error": None,
        })

    # Trust requests from DB
    all_requests = db.query(TrustRequest).order_by(TrustRequest.requested_at.desc()).all()
    pending_requests = [r for r in all_requests if r.status == "PENDING"]

    # Check which pending CIKs are already tracked
    tracked_ciks = set()
    if pending_requests:
        tracked_ciks = {
            normalize_cik(row[0])
            for row in db.execute(select(Trust.cik)).all()
        }

    # Digest subscribers from DB
    pending_subscribers = db.query(DigestSubscriber).filter(
        DigestSubscriber.status == "PENDING"
    ).order_by(DigestSubscriber.requested_at.desc()).all()

    # AI analysis status
    from webapp.services.claude_service import is_configured as ai_configured
    today_start = datetime.combine(date.today(), datetime.min.time())
    ai_usage_today = db.execute(
        select(func.count(AnalysisResult.id))
        .where(AnalysisResult.created_at >= today_start)
    ).scalar() or 0

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "pending_requests": pending_requests,
        "all_requests": all_requests,
        "pending_subscribers": pending_subscribers,
        "tracked_ciks": tracked_ciks,
        "ai_configured": ai_configured(),
        "ai_usage_today": ai_usage_today,
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
    request_id: int = Form(0),
    db: Session = Depends(get_db),
):
    """Approve a trust monitoring request - adds to DB for next pipeline run."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    trust_req = db.query(TrustRequest).filter(TrustRequest.id == request_id).first()
    if not trust_req:
        return RedirectResponse("/admin/?error=missing_data", status_code=303)

    cik = normalize_cik(trust_req.cik)
    name = trust_req.name

    # Check if trust already exists (normalized CIK comparison)
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

    # Mark request as approved
    trust_req.status = "APPROVED"
    trust_req.resolved_at = datetime.utcnow()
    db.commit()

    # Also add to trusts.py registry so pipeline always picks it up
    from urllib.parse import quote
    detail = "Trust added to database and registered in trusts.py"
    try:
        from etp_tracker.trusts import add_trust
        add_trust(cik, name)
    except Exception as e:
        log.warning("Could not write to trusts.py (read-only on Render): %s", e)
        detail = "Trust added to database (trusts.py update skipped -- read-only filesystem on Render)"

    return RedirectResponse(f"/admin/?approved=1&detail={quote(detail)}", status_code=303)


@router.post("/requests/reject")
def reject_request(
    request: Request,
    request_id: int = Form(0),
    db: Session = Depends(get_db),
):
    """Reject a trust monitoring request."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    trust_req = db.query(TrustRequest).filter(TrustRequest.id == request_id).first()
    if trust_req:
        trust_req.status = "REJECTED"
        trust_req.resolved_at = datetime.utcnow()
        db.commit()

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
        sent = send_digest_from_db(db, dashboard_url=dashboard_url, edition="daily")

        if sent:
            return RedirectResponse("/admin/?digest=sent", status_code=303)
        return RedirectResponse("/admin/?digest=fail", status_code=303)

    except Exception as e:
        from urllib.parse import quote
        log.error("Digest send failed: %s", e)
        return RedirectResponse(f"/admin/?digest=error&msg={quote(str(e)[:100])}", status_code=303)


@router.post("/digest/send-weekly")
def send_weekly(request: Request, db: Session = Depends(get_db)):
    """Send the weekly intelligence brief to all recipients."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    try:
        from etp_tracker.weekly_digest import send_weekly_digest
        dashboard_url = str(request.base_url).rstrip("/")
        sent = send_weekly_digest(db, dashboard_url=dashboard_url)

        if sent:
            return RedirectResponse("/admin/?digest=weekly_sent", status_code=303)
        return RedirectResponse("/admin/?digest=weekly_fail", status_code=303)

    except Exception as e:
        from urllib.parse import quote
        log.error("Weekly digest send failed: %s", e)
        return RedirectResponse(f"/admin/?digest=error&msg={quote(str(e)[:100])}", status_code=303)


@router.get("/digest/preview-daily")
def preview_daily(request: Request, db: Session = Depends(get_db)):
    """Preview daily brief HTML without sending."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    from etp_tracker.email_alerts import build_digest_html_from_db
    dashboard_url = str(request.base_url).rstrip("/")
    html = build_digest_html_from_db(db, dashboard_url=dashboard_url, edition="daily")
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)


@router.get("/digest/preview-weekly")
def preview_weekly(request: Request, db: Session = Depends(get_db)):
    """Preview weekly digest HTML without sending."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    from etp_tracker.weekly_digest import build_weekly_digest_html
    dashboard_url = str(request.base_url).rstrip("/")
    html = build_weekly_digest_html(db, dashboard_url=dashboard_url)
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)


@router.post("/digest/send-test")
def send_test_digest(request: Request, db: Session = Depends(get_db)):
    """Send daily brief to relasmar@rexfin.com ONLY (test send)."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    try:
        from etp_tracker.email_alerts import build_digest_html_from_db, _send_html_digest
        dashboard_url = str(request.base_url).rstrip("/")
        html = build_digest_html_from_db(db, dashboard_url=dashboard_url, edition="daily")
        ok = _send_html_digest(html, ["relasmar@rexfin.com"], edition="daily")

        if ok:
            return RedirectResponse("/admin/?digest=test_sent", status_code=303)
        return RedirectResponse("/admin/?digest=test_fail", status_code=303)
    except Exception as e:
        from urllib.parse import quote
        log.error("Test daily send failed: %s", e)
        return RedirectResponse(f"/admin/?digest=error&msg={quote(str(e)[:100])}", status_code=303)


@router.post("/digest/send-test-weekly")
def send_test_weekly(request: Request, db: Session = Depends(get_db)):
    """Send weekly report to relasmar@rexfin.com ONLY (test send)."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    try:
        from etp_tracker.weekly_digest import build_weekly_digest_html, _send_weekly_html
        dashboard_url = str(request.base_url).rstrip("/")
        html = build_weekly_digest_html(db, dashboard_url=dashboard_url)
        ok = _send_weekly_html(
            f"REX ETF Weekly Report - {datetime.now().strftime('%B %d, %Y')}",
            html, ["relasmar@rexfin.com"]
        )

        if ok:
            return RedirectResponse("/admin/?digest=test_weekly_sent", status_code=303)
        return RedirectResponse("/admin/?digest=test_weekly_fail", status_code=303)
    except Exception as e:
        from urllib.parse import quote
        log.error("Test weekly send failed: %s", e)
        return RedirectResponse(f"/admin/?digest=error&msg={quote(str(e)[:100])}", status_code=303)


# --- Subscriber Management ---

@router.post("/subscribers/approve")
def approve_subscriber(
    request: Request,
    subscriber_id: int = Form(0),
    db: Session = Depends(get_db),
):
    """Approve a digest subscriber - adds to email_recipients.txt."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    sub = db.query(DigestSubscriber).filter(DigestSubscriber.id == subscriber_id).first()
    if sub:
        # Add to email_recipients.txt
        existing = set()
        if RECIPIENTS_FILE.exists():
            existing = {
                line.strip().lower()
                for line in RECIPIENTS_FILE.read_text(encoding="utf-8").splitlines()
                if line.strip() and not line.startswith("#")
            }
        if sub.email.lower() not in existing:
            with RECIPIENTS_FILE.open("a", encoding="utf-8") as f:
                f.write(sub.email.strip() + "\n")

        sub.status = "APPROVED"
        sub.resolved_at = datetime.utcnow()
        db.commit()

    return RedirectResponse("/admin/?approved_sub=1", status_code=303)


@router.post("/subscribers/reject")
def reject_subscriber(
    request: Request,
    subscriber_id: int = Form(0),
    db: Session = Depends(get_db),
):
    """Reject a digest subscriber request."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    sub = db.query(DigestSubscriber).filter(DigestSubscriber.id == subscriber_id).first()
    if sub:
        sub.status = "REJECTED"
        sub.resolved_at = datetime.utcnow()
        db.commit()

    return RedirectResponse("/admin/?rejected_sub=1", status_code=303)


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


# --- Data File Upload ---

@router.post("/upload/dashboard")
async def upload_dashboard(request: Request, file: UploadFile = File(...)):
    """Upload The Dashboard.xlsx to data/DASHBOARD/ on the persistent disk."""
    if not _is_admin(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=403)

    dest = PROJECT_ROOT / "data" / "DASHBOARD" / "The Dashboard.xlsx"
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        with dest.open("wb") as f:
            shutil.copyfileobj(file.file, f)
        size_mb = dest.stat().st_size / 1_048_576

        # Invalidate market data cache so next request re-reads the new file
        try:
            from webapp.services.market_data import invalidate_cache
            invalidate_cache()
        except Exception as e:
            log.warning("Could not invalidate market_data cache: %s", e)

        # Trigger screener cache rebuild in background
        try:
            import threading
            from webapp.services.screener_3x_cache import compute_and_cache
            t = threading.Thread(target=compute_and_cache, name="screener-rebuild", daemon=True)
            t.start()
            log.info("Screener cache rebuild started in background")
        except Exception as e:
            log.warning("Could not start screener rebuild: %s", e)

        log.info("Dashboard uploaded: %.1f MB -> %s", size_mb, dest)
        return JSONResponse({"ok": True, "path": str(dest), "size_mb": round(size_mb, 2)})
    except Exception as e:
        log.error("Dashboard upload failed: %s", e, exc_info=True)
        return JSONResponse({"error": "Upload failed. Check server logs."}, status_code=500)


@router.post("/upload/screener-cache")
async def upload_screener_cache(request: Request, file: UploadFile = File(...)):
    """Upload a pre-computed screener cache.json directly into memory + disk."""
    if not _is_admin(request):
        return JSONResponse({"error": "Unauthorized"}, status_code=403)

    dest = PROJECT_ROOT / "data" / "SCREENER" / "cache.json"
    dest.parent.mkdir(parents=True, exist_ok=True)

    try:
        data = await file.read()
        dest.write_bytes(data)

        # Load into memory immediately (don't wait for restart)
        import json
        from webapp.services.screener_3x_cache import set_3x_analysis
        cache = json.loads(data)
        set_3x_analysis(cache)

        log.info("Screener cache uploaded: %d bytes, %d keys", len(data), len(cache))
        return JSONResponse({"ok": True, "keys": list(cache.keys()), "size_bytes": len(data)})
    except Exception as e:
        log.error("Screener cache upload failed: %s", e)
        return JSONResponse({"error": "Cache upload failed. Check server logs."}, status_code=500)
