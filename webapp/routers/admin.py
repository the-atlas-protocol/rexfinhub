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

    # Load email recipients list
    recipients = []
    if RECIPIENTS_FILE.exists():
        recipients = [
            line.strip()
            for line in RECIPIENTS_FILE.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")
        ]

    # Pipeline status (last 5 runs)
    from webapp.models import MktPipelineRun
    pipeline_runs = db.query(MktPipelineRun).order_by(
        MktPipelineRun.id.desc()
    ).limit(5).all()

    # Bloomberg file freshness
    bbg_status = {"source": "unknown", "age_hours": 999, "modified": "N/A"}
    try:
        from webapp.services.bbg_file import get_bloomberg_file, _LOCAL_CACHE, _file_age_hours
        if _LOCAL_CACHE.exists():
            from datetime import datetime as _dt
            bbg_status["age_hours"] = _file_age_hours(_LOCAL_CACHE)
            bbg_status["modified"] = _dt.fromtimestamp(_LOCAL_CACHE.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
            bbg_status["source"] = "Graph API cache"
            bbg_status["size_mb"] = _LOCAL_CACHE.stat().st_size / (1024 * 1024)
    except Exception:
        pass

    # Send gate status
    from pathlib import Path as _P
    gate_file = _P(__file__).resolve().parent.parent.parent / "config" / ".send_enabled"
    send_gate_open = gate_file.exists() and gate_file.read_text().strip().lower() == "true"

    # Classification proposals
    from webapp.models import ClassificationProposal
    pending_proposals = db.query(ClassificationProposal).filter(
        ClassificationProposal.status == "pending"
    ).order_by(ClassificationProposal.created_at.desc()).all()
    approved_proposals = db.query(ClassificationProposal).filter(
        ClassificationProposal.status == "approved"
    ).order_by(ClassificationProposal.reviewed_at.desc()).limit(10).all()

    # Report send history
    import json as _json
    send_log_path = _P(__file__).resolve().parent.parent.parent / "data" / ".send_log.json"
    send_history = {}
    if send_log_path.exists():
        try:
            send_history = _json.loads(send_log_path.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Find last sent time for each report
    report_status = {}
    for key in ["daily_filing", "weekly_report", "li_report", "income_report", "flow_report", "autocall_report"]:
        last_sent = None
        for date_str in sorted(send_history.keys(), reverse=True):
            if key in send_history[date_str]:
                val = send_history[date_str][key]
                if isinstance(val, str) and val != "PAUSED":
                    last_sent = f"{date_str} {val}"
                    break
                elif isinstance(val, dict) and val.get("sent_at"):
                    last_sent = f"{date_str} {val['sent_at']}"
                    break
        report_status[key] = last_sent

    # Classification validation
    cls_validation = {"issues": [], "summary": {"total_funds": 0, "categories": {}, "issue_count": 0}}
    try:
        from webapp.services.classification_validator import validate_classifications
        cls_validation = validate_classifications()
    except Exception as e:
        log.warning("Classification validation failed: %s", e)

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "pending_requests": pending_requests,
        "all_requests": all_requests,
        "pending_subscribers": pending_subscribers,
        "tracked_ciks": tracked_ciks,
        "ai_configured": ai_configured(),
        "ai_usage_today": ai_usage_today,
        "recipients": recipients,
        "pipeline_runs": pipeline_runs,
        "bbg_status": bbg_status,
        "send_gate_open": send_gate_open,
        "pending_proposals": pending_proposals,
        "approved_proposals": approved_proposals,
        "cls_validation": cls_validation,
        "report_status": report_status,
    })


# ---------------------------------------------------------------------------
# Classification Review Queue
# ---------------------------------------------------------------------------

@router.post("/classification/scan")
def classification_scan(request: Request, db: Session = Depends(get_db)):
    """Scan for unmapped funds and populate the review queue."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    try:
        import json as _json
        from tools.rules_editor.classify_engine import scan_unmapped
        from webapp.models import ClassificationProposal

        results = scan_unmapped(since_days=90)
        candidates = results.get("candidates", [])
        inserted = 0

        # Get existing proposal tickers to avoid duplicates
        existing = {
            p.ticker for p in db.query(ClassificationProposal.ticker).filter(
                ClassificationProposal.status.in_(["pending", "approved"])
            ).all()
        }

        for c in candidates:
            if c["ticker"] in existing:
                continue
            db.add(ClassificationProposal(
                ticker=c["ticker"],
                fund_name=c.get("fund_name"),
                issuer=c.get("issuer"),
                aum=c.get("aum"),
                proposed_category=c.get("etp_category"),
                proposed_strategy=c.get("strategy"),
                confidence=c.get("confidence"),
                reason=c.get("reason"),
                attributes_json=_json.dumps(c.get("attributes", {})),
                status="pending",
            ))
            inserted += 1

        db.commit()
        return RedirectResponse(f"/admin/?cls_scan=1&cls_count={inserted}", status_code=303)
    except Exception as e:
        log.error("Classification scan failed: %s", e)
        return RedirectResponse(f"/admin/?cls_error=1", status_code=303)


@router.post("/classification/{proposal_id}/approve")
def classification_approve(
    request: Request, proposal_id: int, db: Session = Depends(get_db)
):
    """Approve a classification proposal — writes to fund_mapping.csv + attributes CSV."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    try:
        import json as _json
        from webapp.models import ClassificationProposal
        from tools.rules_editor.classify_engine import apply_classifications

        proposal = db.query(ClassificationProposal).filter(
            ClassificationProposal.id == proposal_id
        ).first()
        if not proposal:
            return RedirectResponse("/admin/?cls_error=missing", status_code=303)

        # Build candidate dict matching apply_classifications() input format
        candidate = {
            "ticker": proposal.ticker,
            "etp_category": proposal.proposed_category,
            "attributes": _json.loads(proposal.attributes_json or "{}"),
        }

        apply_classifications([candidate])

        proposal.status = "approved"
        proposal.reviewed_at = datetime.utcnow()
        db.commit()

        return RedirectResponse(f"/admin/?cls_approved={proposal.ticker}", status_code=303)
    except Exception as e:
        log.error("Classification approve failed: %s", e)
        return RedirectResponse(f"/admin/?cls_error=approve", status_code=303)


@router.post("/classification/{proposal_id}/reject")
def classification_reject(
    request: Request, proposal_id: int, db: Session = Depends(get_db)
):
    """Reject a classification proposal."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    from webapp.models import ClassificationProposal

    proposal = db.query(ClassificationProposal).filter(
        ClassificationProposal.id == proposal_id
    ).first()
    if proposal:
        proposal.status = "rejected"
        proposal.reviewed_at = datetime.utcnow()
        db.commit()

    return RedirectResponse("/admin/?cls_rejected=1", status_code=303)


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
            f"REX Weekly ETP Report: {datetime.now().strftime('%m/%d/%Y')}",
            html, ["relasmar@rexfin.com"]
        )

        if ok:
            return RedirectResponse("/admin/?digest=test_weekly_sent", status_code=303)
        return RedirectResponse("/admin/?digest=test_weekly_fail", status_code=303)
    except Exception as e:
        from urllib.parse import quote
        log.error("Test weekly send failed: %s", e)
        return RedirectResponse(f"/admin/?digest=error&msg={quote(str(e)[:100])}", status_code=303)


# --- Morning Brief ---

@router.post("/morning-brief")
def admin_send_morning_brief(request: Request, db: Session = Depends(get_db)):
    """Send morning brief email (admin only)."""
    if not _is_admin(request):
        return RedirectResponse("/admin/login", status_code=302)

    try:
        from etp_tracker.email_alerts import send_morning_brief
        dashboard_url = str(request.base_url).rstrip("/")
        ok = send_morning_brief(db, dashboard_url=dashboard_url)

        if ok:
            return RedirectResponse("/admin/?digest=morning_sent", status_code=303)
        return RedirectResponse("/admin/?digest=morning_fail", status_code=303)
    except Exception as e:
        from urllib.parse import quote
        log.error("Morning brief send failed: %s", e)
        return RedirectResponse(f"/admin/?digest=error&msg={quote(str(e)[:100])}", status_code=303)


@router.get("/morning-brief/preview")
def admin_preview_morning_brief(request: Request, db: Session = Depends(get_db)):
    """Preview morning brief in browser (admin only)."""
    if not _is_admin(request):
        return RedirectResponse("/admin/login", status_code=302)

    from etp_tracker.email_alerts import build_morning_brief_html
    dashboard_url = str(request.base_url).rstrip("/")
    html = build_morning_brief_html(db, dashboard_url=dashboard_url)
    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)


@router.post("/morning-brief/send-test")
def send_test_morning_brief(request: Request, db: Session = Depends(get_db)):
    """Send morning brief to relasmar@rexfin.com ONLY (test send)."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    try:
        from etp_tracker.email_alerts import build_morning_brief_html, _send_html_digest
        dashboard_url = str(request.base_url).rstrip("/")
        html = build_morning_brief_html(db, dashboard_url=dashboard_url)
        ok = _send_html_digest(html, ["relasmar@rexfin.com"], edition="morning")

        if ok:
            return RedirectResponse("/admin/?digest=test_morning_sent", status_code=303)
        return RedirectResponse("/admin/?digest=test_morning_fail", status_code=303)
    except Exception as e:
        from urllib.parse import quote
        log.error("Test morning brief send failed: %s", e)
        return RedirectResponse(f"/admin/?digest=error&msg={quote(str(e)[:100])}", status_code=303)


# --- Bloomberg Report Emails (L&I, Income) ---

@router.get("/reports/preview-li")
def preview_li_report(request: Request, db: Session = Depends(get_db)):
    """Preview L&I report email in browser."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    from fastapi.responses import HTMLResponse
    try:
        from webapp.services.report_emails import build_li_email
        dashboard_url = str(request.base_url).rstrip("/")
        html, _images = build_li_email(dashboard_url=dashboard_url, db=db)
        return HTMLResponse(content=html)
    except Exception as e:
        log.error("L&I email preview failed: %s", e, exc_info=True)
        return HTMLResponse(content=f"<h2>Error building L&I email</h2><pre>{e}</pre>", status_code=200)


@router.get("/reports/preview-cc")
def preview_cc_report(request: Request, db: Session = Depends(get_db)):
    """Preview Income report email in browser."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    from fastapi.responses import HTMLResponse
    try:
        from webapp.services.report_emails import build_cc_email
        dashboard_url = str(request.base_url).rstrip("/")
        html, _images = build_cc_email(dashboard_url=dashboard_url, db=db)
        return HTMLResponse(content=html)
    except Exception as e:
        log.error("CC email preview failed: %s", e, exc_info=True)
        return HTMLResponse(content=f"<h2>Error building CC email</h2><pre>{e}</pre>", status_code=200)


@router.post("/reports/send-test-li")
def send_test_li_report(request: Request, db: Session = Depends(get_db)):
    """Send L&I report email to relasmar@rexfin.com only."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    try:
        from webapp.services.report_emails import build_li_email
        from etp_tracker.email_alerts import _send_html_digest
        dashboard_url = str(request.base_url).rstrip("/")
        html, images = build_li_email(dashboard_url=dashboard_url, db=db)
        ok = _send_html_digest(html, ["relasmar@rexfin.com"], edition="daily",
                               subject_override=f"REX ETP Leverage & Inverse Report: {datetime.now().strftime('%m/%d/%Y')}",
                               images=images)
        if ok:
            return RedirectResponse("/admin/?digest=test_li_sent", status_code=303)
        return RedirectResponse("/admin/?digest=test_fail", status_code=303)
    except Exception as e:
        from urllib.parse import quote
        log.error("Test L&I report send failed: %s", e)
        return RedirectResponse(f"/admin/?digest=error&msg={quote(str(e)[:100])}", status_code=303)


@router.post("/reports/send-test-cc")
def send_test_cc_report(request: Request, db: Session = Depends(get_db)):
    """Send Income report email to relasmar@rexfin.com only."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    try:
        from webapp.services.report_emails import build_cc_email
        from etp_tracker.email_alerts import _send_html_digest
        dashboard_url = str(request.base_url).rstrip("/")
        html, images = build_cc_email(dashboard_url=dashboard_url, db=db)
        ok = _send_html_digest(html, ["relasmar@rexfin.com"], edition="daily",
                               subject_override=f"REX ETP Income Report: {datetime.now().strftime('%m/%d/%Y')}",
                               images=images)
        if ok:
            return RedirectResponse("/admin/?digest=test_cc_sent", status_code=303)
        return RedirectResponse("/admin/?digest=test_fail", status_code=303)
    except Exception as e:
        from urllib.parse import quote
        log.error("Test CC report send failed: %s", e)
        return RedirectResponse(f"/admin/?digest=error&msg={quote(str(e)[:100])}", status_code=303)


@router.post("/reports/send-li")
def send_li_report(request: Request, db: Session = Depends(get_db)):
    """Send L&I report email to all recipients."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    try:
        from webapp.services.report_emails import build_li_email
        from etp_tracker.email_alerts import _send_html_digest, _load_recipients
        dashboard_url = str(request.base_url).rstrip("/")
        html, images = build_li_email(dashboard_url=dashboard_url, db=db)
        recipients = _load_recipients()
        if not recipients:
            return RedirectResponse("/admin/?digest=no_recipients", status_code=303)
        ok = _send_html_digest(html, recipients, edition="daily",
                               subject_override=f"REX ETP Leverage & Inverse Report: {datetime.now().strftime('%m/%d/%Y')}",
                               images=images)
        if ok:
            return RedirectResponse("/admin/?digest=li_sent", status_code=303)
        return RedirectResponse("/admin/?digest=li_fail", status_code=303)
    except Exception as e:
        from urllib.parse import quote
        log.error("L&I report send-all failed: %s", e)
        return RedirectResponse(f"/admin/?digest=error&msg={quote(str(e)[:100])}", status_code=303)


@router.post("/reports/send-cc")
def send_cc_report(request: Request, db: Session = Depends(get_db)):
    """Send Income report email to all recipients."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    try:
        from webapp.services.report_emails import build_cc_email
        from etp_tracker.email_alerts import _send_html_digest, _load_recipients
        dashboard_url = str(request.base_url).rstrip("/")
        html, images = build_cc_email(dashboard_url=dashboard_url, db=db)
        recipients = _load_recipients()
        if not recipients:
            return RedirectResponse("/admin/?digest=no_recipients", status_code=303)
        ok = _send_html_digest(html, recipients, edition="daily",
                               subject_override=f"REX ETP Income Report: {datetime.now().strftime('%m/%d/%Y')}",
                               images=images)
        if ok:
            return RedirectResponse("/admin/?digest=cc_sent", status_code=303)
        return RedirectResponse("/admin/?digest=cc_fail", status_code=303)
    except Exception as e:
        from urllib.parse import quote
        log.error("CC report send-all failed: %s", e)
        return RedirectResponse(f"/admin/?digest=error&msg={quote(str(e)[:100])}", status_code=303)


# --- Flow Report ---

@router.get("/reports/preview-flow")
def preview_flow_report(request: Request, db: Session = Depends(get_db)):
    """Preview Flow Report email in browser. ?refresh=1 clears stale cache."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    from fastapi.responses import HTMLResponse
    try:
        if request.query_params.get("refresh"):
            from webapp.models import MktReportCache
            db.execute(MktReportCache.__table__.delete().where(
                MktReportCache.report_key == "flow_report"))
            db.commit()
            from webapp.services import report_data, market_data
            from webapp.services.screener_3x_cache import invalidate_cache as inv_screener
            report_data.invalidate_cache()
            market_data.invalidate_cache()
            inv_screener()
        from webapp.services.report_emails import build_flow_email
        dashboard_url = str(request.base_url).rstrip("/")
        html, _images = build_flow_email(dashboard_url=dashboard_url, db=db)
        return HTMLResponse(content=html)
    except Exception as e:
        log.error("Flow email preview failed: %s", e, exc_info=True)
        return HTMLResponse(content=f"<h2>Error building Flow email</h2><pre>{e}</pre>", status_code=200)


@router.post("/reports/send-test-flow")
def send_test_flow_report(request: Request, db: Session = Depends(get_db)):
    """Send Flow Report email to relasmar@rexfin.com only."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    try:
        from webapp.services.report_emails import build_flow_email
        from etp_tracker.email_alerts import _send_html_digest
        dashboard_url = str(request.base_url).rstrip("/")
        html, images = build_flow_email(dashboard_url=dashboard_url, db=db)
        ok = _send_html_digest(html, ["relasmar@rexfin.com"], edition="daily",
                               subject_override=f"REX ETP Flow Report: {datetime.now().strftime('%m/%d/%Y')}",
                               images=images)
        if ok:
            return RedirectResponse("/admin/?digest=test_flow_sent", status_code=303)
        return RedirectResponse("/admin/?digest=test_fail", status_code=303)
    except Exception as e:
        from urllib.parse import quote
        log.error("Test Flow report send failed: %s", e)
        return RedirectResponse(f"/admin/?digest=error&msg={quote(str(e)[:100])}", status_code=303)


@router.post("/reports/send-flow")
def send_flow_report(request: Request, db: Session = Depends(get_db)):
    """Send Flow Report email to all recipients."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    try:
        from webapp.services.report_emails import build_flow_email
        from etp_tracker.email_alerts import _send_html_digest, _load_recipients
        dashboard_url = str(request.base_url).rstrip("/")
        html, images = build_flow_email(dashboard_url=dashboard_url, db=db)
        recipients = _load_recipients()
        if not recipients:
            return RedirectResponse("/admin/?digest=no_recipients", status_code=303)
        ok = _send_html_digest(html, recipients, edition="daily",
                               subject_override=f"REX ETP Flow Report: {datetime.now().strftime('%m/%d/%Y')}",
                               images=images)
        if ok:
            return RedirectResponse("/admin/?digest=flow_sent", status_code=303)
        return RedirectResponse("/admin/?digest=flow_fail", status_code=303)
    except Exception as e:
        from urllib.parse import quote
        log.error("Flow report send-all failed: %s", e)
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
# NOTE: Bloomberg upload removed (2026-03-03).  Market data is synced to
# SQLite locally via run_daily.py -> market_sync.sync_market_data(), then the
# DB is uploaded to Render.  Uploading the Excel file to Render does nothing
# since all routes read from SQLite.

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
