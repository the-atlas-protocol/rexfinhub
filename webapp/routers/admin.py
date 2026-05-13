"""
Admin router - Password-protected admin panel for trust management,
digest testing, and system status.
"""
from __future__ import annotations

import logging
import os
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
from webapp.services.admin_auth import load_admin_password

router = APIRouter(prefix="/admin", tags=["admin"])
templates = Jinja2Templates(directory="webapp/templates")
log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

ADMIN_PASSWORD = load_admin_password()
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

    # All recipient lists from DB
    from webapp.services.recipients import get_all_recipients_by_list
    all_recipients = get_all_recipients_by_list(db)

    # SEC scrape metrics
    sec_metrics = {}
    try:
        import json as _j2
        summary_path = _P(__file__).resolve().parent.parent.parent / "outputs" / "_run_summary.json"
        if summary_path.exists():
            sec_metrics = _j2.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        pass

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

    # Structured notes status
    notes_stats = {"available": False, "total_products": 0, "total_filings": 0, "date_max": "--"}
    try:
        from webapp.routers.notes import _load_stats
        notes_stats = _load_stats()
    except Exception as e:
        log.warning("Structured notes stats failed: %s", e)

    # Product pipeline (from Workstream C — rex_products table)
    product_stats = {"available": False, "total": 0}
    try:
        from webapp.models import RexProduct
        from sqlalchemy import func as _func
        total = db.query(RexProduct).count()
        if total > 0:
            status_rows = db.query(RexProduct.status, _func.count(RexProduct.id)).group_by(RexProduct.status).all()
            product_stats = {
                "available": True,
                "total": total,
                "by_status": {s: c for s, c in status_rows},
            }
    except Exception as e:
        log.debug("Product pipeline stats not available: %s", e)

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
        "all_recipients": all_recipients,
        "sec_metrics": sec_metrics,
        "notes_stats": notes_stats,
        "product_stats": product_stats,
    })


# ---------------------------------------------------------------------------
# Email Gate + Recipient Management
# ---------------------------------------------------------------------------

@router.post("/gate/toggle")
def toggle_gate(request: Request):
    """Toggle email send gate on/off."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    from pathlib import Path as _P
    gate = _P(__file__).resolve().parent.parent.parent / "config" / ".send_enabled"
    if gate.exists() and gate.read_text().strip().lower() == "true":
        gate.unlink()
        log.info("Send gate LOCKED by admin (IP: %s)", request.client.host if request.client else "unknown")
        return RedirectResponse("/admin/?gate=locked", status_code=303)
    else:
        gate.parent.mkdir(parents=True, exist_ok=True)
        gate.write_text("true")
        log.info("Send gate UNLOCKED by admin (IP: %s)", request.client.host if request.client else "unknown")
        return RedirectResponse("/admin/?gate=unlocked", status_code=303)


@router.post("/recipients/add")
def add_recipient_route(request: Request, list_name: str = Form(""), email: str = Form(""), db: Session = Depends(get_db)):
    """Add an email to a recipient list (DB + sync to VPS)."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    from webapp.services.recipients import add_recipient, VALID_LIST_TYPES
    if list_name not in VALID_LIST_TYPES or not email.strip():
        return RedirectResponse("/admin/?recip_error=1", status_code=303)
    # Write to local DB
    add_recipient(db, email.strip(), list_name, added_by="admin")
    # Sync to VPS (so emails actually send from there)
    if _ON_RENDER:
        _call_vps(f"/pipeline/recipients/add?email={email.strip()}&list_type={list_name}")
    return RedirectResponse(f"/admin/?recip_added={email.strip()}", status_code=303)


@router.post("/recipients/remove")
def remove_recipient_route(request: Request, list_name: str = Form(""), email: str = Form(""), db: Session = Depends(get_db)):
    """Remove an email from a recipient list (DB + sync to VPS)."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    from webapp.services.recipients import remove_recipient
    remove_recipient(db, email.strip(), list_name)
    if _ON_RENDER:
        _call_vps(f"/pipeline/recipients/remove?email={email.strip()}&list_type={list_name}")
    return RedirectResponse(f"/admin/?recip_removed={email.strip()}", status_code=303)


# ---------------------------------------------------------------------------
# Operational Actions (Run Now)
# ---------------------------------------------------------------------------

_VPS_API = "https://46.224.126.196"
_ON_RENDER = os.environ.get("RENDER", "") != ""


@router.get("/vps-status")
def vps_status(request: Request):
    """Proxy VPS pipeline status for admin JS polling."""
    if not _is_admin(request):
        return {"running": None, "last_result": None}
    result = _call_vps("/pipeline/status", method="GET")
    return result or {"running": None, "last_result": {"name": "vps", "status": "error", "detail": "unreachable"}}


def _call_vps(endpoint: str, method: str = "POST") -> dict | None:
    """Call the pipeline API on the VPS. Returns response dict or None on failure.

    method: "POST" for action endpoints (pull-sync, sec-scrape, upload-render,
            recipients/*) or "GET" for read-only endpoints (status).
    """
    import requests as _req
    api_key = os.environ.get("API_KEY", "")
    if not api_key:
        try:
            from pathlib import Path as _P2
            env = _P2(__file__).resolve().parent.parent.parent / "config" / ".env"
            for line in env.read_text().splitlines():
                if line.startswith("API_KEY="):
                    api_key = line.split("=", 1)[1].strip()
        except Exception:
            pass
    try:
        fn = _req.get if method.upper() == "GET" else _req.post
        resp = fn(f"{_VPS_API}{endpoint}",
                  headers={"X-API-Key": api_key}, timeout=30,
                  verify=False)  # Self-signed cert on VPS
        return resp.json() if resp.status_code == 200 else None
    except Exception as e:
        log.error("VPS API call failed (%s): %s", endpoint, e)
        return None


@router.post("/pull-bloomberg")
def pull_bloomberg(request: Request, db: Session = Depends(get_db)):
    """Pull fresh Bloomberg from SharePoint + sync to DB."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    if _ON_RENDER:
        # On Render: call VPS to do the work
        result = _call_vps("/pipeline/pull-sync")
        if result and result.get("status") in ("started", "ok"):
            return RedirectResponse("/admin/?bbg_pulled=1&mkt_synced=1&mkt_count=VPS", status_code=303)
        return RedirectResponse("/admin/?bbg_error=1", status_code=303)

    # Local: run directly
    try:
        from webapp.services.graph_files import download_bloomberg_from_sharepoint
        from webapp.services.market_sync import sync_market_data
        path = download_bloomberg_from_sharepoint()
        if not path:
            return RedirectResponse("/admin/?bbg_error=download_failed", status_code=303)
        result = sync_market_data(db)
        count = result.get("master_rows", 0)

        # Auto-scan classifications for new unmapped funds
        cls_inserted = 0
        try:
            scan = _run_classification_scan(db, since_days=365)
            cls_inserted = scan.get("inserted", 0)
        except Exception as scan_err:
            log.error("Auto-scan after pull failed (non-fatal): %s", scan_err, exc_info=True)

        return RedirectResponse(
            f"/admin/?bbg_pulled=1&mkt_synced=1&mkt_count={count}&cls_auto={cls_inserted}",
            status_code=303,
        )
    except Exception as e:
        log.error("Pull & Sync failed: %s", e, exc_info=True)
        from urllib.parse import quote
        return RedirectResponse(f"/admin/?bbg_error=sync_failed&msg={quote(str(e)[:120])}", status_code=303)


@router.post("/upload-render")
def upload_render(request: Request):
    """Upload lean DB to Render."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    if _ON_RENDER:
        result = _call_vps("/pipeline/upload-render")
        if result and result.get("status") in ("started", "ok"):
            return RedirectResponse("/admin/?render_uploaded=1", status_code=303)
        return RedirectResponse("/admin/?render_error=1", status_code=303)

    try:
        from scripts.run_daily import upload_db_to_render
        upload_db_to_render()
        return RedirectResponse("/admin/?render_uploaded=1", status_code=303)
    except Exception as e:
        log.error("Render upload failed: %s", e)
        return RedirectResponse("/admin/?render_error=1", status_code=303)


@router.post("/sec-scrape")
def trigger_sec_scrape(request: Request):
    """Trigger SEC filing scrape on the VPS (incremental, all trusts)."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    if _ON_RENDER:
        result = _call_vps("/pipeline/sec-scrape")
        if result and result.get("status") in ("started", "ok"):
            return RedirectResponse("/admin/?sec_scrape_started=1", status_code=303)
        return RedirectResponse("/admin/?sec_scrape_error=1", status_code=303)

    # Local: run directly (blocking — not recommended for long scrapes)
    try:
        import os
        from etp_tracker.run_pipeline import run_pipeline, load_ciks_from_db
        os.environ.setdefault("SEC_CACHE_DIR", "cache/sec")
        ciks, overrides = load_ciks_from_db()
        result = run_pipeline(
            ciks=ciks, overrides=overrides, since="2024-01-01",
            refresh_submissions=True,
            user_agent="REX-ETP-Tracker/2.0 (relasmar@rexfin.com)",
            etf_only=True,
        )
        return RedirectResponse(f"/admin/?sec_scrape_done=1&count={result}", status_code=303)
    except Exception as e:
        log.error("SEC scrape failed: %s", e)
        return RedirectResponse("/admin/?sec_scrape_error=1", status_code=303)


@router.post("/sync-market")
def sync_market(request: Request, db: Session = Depends(get_db)):
    """Trigger market data sync from Bloomberg file."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    try:
        from webapp.services.market_sync import sync_market_data
        result = sync_market_data(db)
        count = result.get("master_rows", 0)
        return RedirectResponse(f"/admin/?mkt_synced=1&mkt_count={count}", status_code=303)
    except Exception as e:
        log.error("Market sync failed: %s", e)
        return RedirectResponse("/admin/?mkt_error=1", status_code=303)


# ---------------------------------------------------------------------------
# Classification Review Queue
# ---------------------------------------------------------------------------

@router.get("/classification/lookup")
def classification_lookup(request: Request):
    """Look up a fund's current classification by ticker."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    import json as _json
    import pandas as pd
    from market.config import RULES_DIR

    ticker = request.query_params.get("q", "").strip().upper()
    if not ticker:
        return {"ticker": None, "found": False}

    # Append " US" if not present
    if not ticker.endswith(" US"):
        ticker = ticker + " US"

    fm = pd.read_csv(RULES_DIR / "fund_mapping.csv", engine="python", on_bad_lines="skip")
    fm["ticker"] = fm["ticker"].astype(str).str.strip()
    rows = fm[fm["ticker"] == ticker]

    if rows.empty:
        return {"ticker": ticker, "found": False, "category": None, "attributes": {}}

    cat = rows.iloc[0]["etp_category"]
    source = rows.iloc[0].get("source", "unknown")

    # Load attributes for this category
    attrs = {}
    attr_file = RULES_DIR / f"attributes_{cat}.csv"
    if attr_file.exists():
        attr_df = pd.read_csv(attr_file, engine="python", on_bad_lines="skip")
        attr_df["ticker"] = attr_df["ticker"].astype(str).str.strip()
        match = attr_df[attr_df["ticker"] == ticker]
        if not match.empty:
            row = match.iloc[0]
            for col in attr_df.columns:
                if col != "ticker":
                    val = row[col]
                    attrs[col] = str(val) if pd.notna(val) else ""

    return {
        "ticker": ticker,
        "found": True,
        "category": cat,
        "source": source,
        "attributes": attrs,
        "attribute_columns": list(attrs.keys()),
    }


@router.post("/classification/update")
def classification_update(request: Request):
    """Update a fund's classification (category + attributes)."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    import pandas as pd
    from market.config import RULES_DIR
    from starlette.datastructures import FormData

    # Read form data synchronously via Starlette's sync interface
    import asyncio
    loop = asyncio.new_event_loop()
    form = loop.run_until_complete(request.form())
    loop.close()

    ticker = form.get("ticker", "").strip()
    new_cat = form.get("category", "").strip()

    # Whitelist categories to prevent path injection
    ALLOWED_CATEGORIES = {"LI", "CC", "Crypto", "Defined", "Thematic"}
    if not ticker or not new_cat or new_cat not in ALLOWED_CATEGORIES:
        return RedirectResponse("/admin/?cls_error=missing", status_code=303)

    # Update fund_mapping.csv
    fm_path = RULES_DIR / "fund_mapping.csv"
    fm = pd.read_csv(fm_path, engine="python", on_bad_lines="skip")
    fm["ticker"] = fm["ticker"].astype(str).str.strip()

    # Remove old entries for this ticker
    fm = fm[fm["ticker"] != ticker]
    # Add new entry
    new_row = pd.DataFrame([{"ticker": ticker, "etp_category": new_cat, "is_primary": 1, "source": "manual"}])
    fm = pd.concat([fm, new_row], ignore_index=True)
    # Atomic write: temp file then rename
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', dir=str(fm_path.parent), delete=False, suffix='.csv') as tmp:
        fm.to_csv(tmp.name, index=False)
        Path(tmp.name).replace(fm_path)

    # Update attributes CSV
    attr_file = RULES_DIR / f"attributes_{new_cat}.csv"
    if attr_file.exists():
        attr_df = pd.read_csv(attr_file, engine="python", on_bad_lines="skip")
        attr_df["ticker"] = attr_df["ticker"].astype(str).str.strip()
        # Remove old entry
        attr_df = attr_df[attr_df["ticker"] != ticker]
        # Build new attrs from form
        new_attrs = {"ticker": ticker}
        for key in form.keys():
            if key.startswith("attr_"):
                col_name = key[5:]  # strip "attr_" prefix
                new_attrs[col_name] = form.get(key, "")
        attr_df = pd.concat([attr_df, pd.DataFrame([new_attrs])], ignore_index=True)
        # Atomic write
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', dir=str(attr_file.parent), delete=False, suffix='.csv') as tmp:
            attr_df.to_csv(tmp.name, index=False)
            Path(tmp.name).replace(attr_file)

    return RedirectResponse(f"/admin/?cls_updated={ticker}", status_code=303)


def _run_classification_scan(db: Session, since_days: int = 365) -> dict:
    """Shared helper: run scan_unmapped and upsert new proposals.

    Returns dict with counts: inserted, total_candidates, outside, stale.
    Safe to call from the pull-sync flow (no HTTP context).
    """
    import json as _json
    from tools.rules_editor.classify_engine import scan_unmapped
    from webapp.models import ClassificationProposal

    results = scan_unmapped(since_days=since_days)
    candidates = results.get("candidates", [])
    summary = results.get("summary", {})

    existing = {
        p.ticker for p in db.query(ClassificationProposal.ticker).filter(
            ClassificationProposal.status.in_(["pending", "approved"])
        ).all()
    }

    inserted = 0
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
    return {
        "inserted": inserted,
        "total_candidates": summary.get("candidates", 0),
        "outside": summary.get("outside", 0),
        "stale": summary.get("stale", 0),
    }


@router.post("/classification/scan")
def classification_scan(request: Request, db: Session = Depends(get_db)):
    """Scan for unmapped funds and populate the review queue."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    try:
        result = _run_classification_scan(db, since_days=365)
        return RedirectResponse(
            f"/admin/?cls_scan=1&cls_count={result['inserted']}",
            status_code=303,
        )
    except Exception as e:
        log.error("Classification scan failed: %s", e, exc_info=True)
        from urllib.parse import quote
        return RedirectResponse(f"/admin/?cls_error=scan&msg={quote(str(e)[:120])}", status_code=303)


@router.post("/classification/{proposal_id}/approve")
def classification_approve(
    request: Request, proposal_id: int, db: Session = Depends(get_db)
):
    """Approve a classification proposal.

    On Render, this delegates to the VPS API so the writes hit the real rules
    CSVs + the authoritative DB. Render's local writes would be wiped by the
    nightly DB upload from VPS. Locally, still writes directly.
    """
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    if _ON_RENDER:
        result = _call_vps(f"/pipeline/classification/approve/{proposal_id}")
        if not result:
            return RedirectResponse("/admin/?cls_error=vps_unreachable", status_code=303)
        status = result.get("status", "error")
        if status == "approved":
            # Reflect on Render DB too so the UI updates immediately; the next
            # DB upload from VPS will make this canonical.
            try:
                from webapp.models import ClassificationProposal
                p = db.query(ClassificationProposal).filter(
                    ClassificationProposal.id == proposal_id
                ).first()
                if p:
                    p.status = "approved"
                    p.reviewed_at = datetime.utcnow()
                    db.commit()
            except Exception:
                pass
            return RedirectResponse(
                f"/admin/?cls_approved={result.get('ticker', proposal_id)}",
                status_code=303,
            )
        return RedirectResponse(f"/admin/?cls_error={status}", status_code=303)

    # Local dev path (not Render) — write directly.
    try:
        import json as _json
        from webapp.models import ClassificationProposal
        from tools.rules_editor.classify_engine import apply_classifications

        proposal = db.query(ClassificationProposal).filter(
            ClassificationProposal.id == proposal_id
        ).first()
        if not proposal:
            return RedirectResponse("/admin/?cls_error=missing", status_code=303)
        if proposal.status != "pending":
            return RedirectResponse("/admin/?cls_error=already_resolved", status_code=303)

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
        log.error("Classification approve failed: %s", e, exc_info=True)
        from urllib.parse import quote
        return RedirectResponse(f"/admin/?cls_error=approve&msg={quote(str(e)[:120])}", status_code=303)


@router.post("/classification/{proposal_id}/reject")
def classification_reject(
    request: Request, proposal_id: int, db: Session = Depends(get_db)
):
    """Reject a classification proposal.

    On Render, delegates to the VPS API (authoritative). Locally, writes direct.
    """
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    if _ON_RENDER:
        result = _call_vps(f"/pipeline/classification/reject/{proposal_id}")
        if not result:
            return RedirectResponse("/admin/?cls_error=vps_unreachable", status_code=303)
        status = result.get("status", "error")
        if status == "rejected":
            try:
                from webapp.models import ClassificationProposal
                p = db.query(ClassificationProposal).filter(
                    ClassificationProposal.id == proposal_id
                ).first()
                if p:
                    p.status = "rejected"
                    p.reviewed_at = datetime.utcnow()
                    db.commit()
            except Exception:
                pass
            return RedirectResponse(
                f"/admin/?cls_rejected={result.get('ticker', proposal_id)}",
                status_code=303,
            )
        return RedirectResponse(f"/admin/?cls_error={status}", status_code=303)

    try:
        from webapp.models import ClassificationProposal

        proposal = db.query(ClassificationProposal).filter(
            ClassificationProposal.id == proposal_id
        ).first()
        if proposal:
            proposal.status = "rejected"
            proposal.reviewed_at = datetime.utcnow()
            db.commit()
            return RedirectResponse(f"/admin/?cls_rejected={proposal.ticker}", status_code=303)
        return RedirectResponse("/admin/?cls_error=missing_reject", status_code=303)
    except Exception as e:
        log.error("Classification reject failed: %s", e, exc_info=True)
        from urllib.parse import quote
        return RedirectResponse(f"/admin/?cls_error=reject&msg={quote(str(e)[:120])}", status_code=303)


@router.post("/prepare-daily")
def prepare_daily(request: Request):
    """One-click daily prep: Bloomberg pull + market sync + prebake + upload
    + test-send every report to relasmar@rexfin.com.

    Runs in background on VPS. Returns immediately with a status hint.
    """
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)
    result = _call_vps("/pipeline/prepare-daily")
    if result and result.get("status") in ("started", "ok"):
        return RedirectResponse("/admin/?prep=started", status_code=303)
    return RedirectResponse("/admin/?prep=error", status_code=303)


@router.post("/classification/batch")
def classification_batch(
    request: Request,
    action: str = Form(""),
    proposal_ids: str = Form(""),
    override_category: str = Form(""),
    db: Session = Depends(get_db),
):
    """Batch operation on multiple classification proposals.

    action: approve | reject | recategorize
    proposal_ids: comma-separated list of proposal IDs
    override_category: optional category override for 'recategorize' action
    """
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    try:
        import json as _json
        from webapp.models import ClassificationProposal
        from tools.rules_editor.classify_engine import apply_classifications

        ids = [int(x.strip()) for x in proposal_ids.split(",") if x.strip().isdigit()]
        if not ids:
            return RedirectResponse("/admin/?cls_error=no_selection", status_code=303)

        valid_cats = {"LI", "CC", "Crypto", "Defined", "Thematic"}
        if action == "recategorize" and override_category not in valid_cats:
            return RedirectResponse("/admin/?cls_error=invalid_category", status_code=303)

        proposals = db.query(ClassificationProposal).filter(
            ClassificationProposal.id.in_(ids),
            ClassificationProposal.status == "pending",
        ).all()

        if not proposals:
            return RedirectResponse("/admin/?cls_error=none_pending", status_code=303)

        count = 0
        if action == "reject":
            for p in proposals:
                p.status = "rejected"
                p.reviewed_at = datetime.utcnow()
                count += 1
            db.commit()
            return RedirectResponse(f"/admin/?cls_batch_rejected={count}", status_code=303)

        # For approve / recategorize — build candidates and apply
        candidates = []
        for p in proposals:
            category = override_category if action == "recategorize" else p.proposed_category
            if category not in valid_cats:
                continue
            candidates.append({
                "ticker": p.ticker,
                "etp_category": category,
                "attributes": _json.loads(p.attributes_json or "{}"),
            })

        if candidates:
            apply_classifications(candidates)

        for p in proposals:
            p.status = "approved"
            p.reviewed_at = datetime.utcnow()
            if action == "recategorize":
                p.review_notes = f"Recategorized to {override_category}"
            count += 1
        db.commit()

        return RedirectResponse(f"/admin/?cls_batch_approved={count}", status_code=303)

    except Exception as e:
        log.error("Batch classification failed: %s", e)
        from urllib.parse import quote
        return RedirectResponse(f"/admin/?cls_error=batch&msg={quote(str(e)[:80])}", status_code=303)


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


@router.get("/classification-stats")
def classification_stats(request: Request, db: Session = Depends(get_db)):
    """Classification stats — per-bucket counts for the new 3-axis taxonomy."""
    if not _is_admin(request):
        return templates.TemplateResponse("admin_login.html", {"request": request, "error": None})

    from sqlalchemy import text as sa_text

    def _query(group_col: str, limit: int = 50) -> list[dict]:
        rows = db.execute(sa_text(f"""
            SELECT {group_col},
                   COUNT(*) AS total,
                   SUM(CASE WHEN is_rex = 1 THEN 1 ELSE 0 END) AS rex_count,
                   SUM(CASE WHEN is_rex = 0 THEN 1 ELSE 0 END) AS comp_count,
                   ROUND(SUM(COALESCE(aum, 0)), 2) AS total_aum,
                   ROUND(SUM(CASE WHEN is_rex = 1 THEN COALESCE(aum, 0) ELSE 0 END), 2) AS rex_aum
            FROM mkt_master_data
            WHERE {group_col} IS NOT NULL AND market_status = 'ACTV'
            GROUP BY {group_col}
            ORDER BY total DESC
            LIMIT {limit}
        """)).fetchall()
        return [
            {
                "bucket": r[0],
                "total": r[1],
                "rex_count": r[2],
                "comp_count": r[3],
                "total_aum": round(float(r[4] or 0), 1),
                "rex_aum": round(float(r[5] or 0), 1),
            }
            for r in rows
        ]

    by_primary_strategy = _query("primary_strategy")
    by_asset_class = _query("asset_class")
    by_sub_strategy = _query("sub_strategy", limit=20)

    # Coverage stats
    total_row = db.execute(sa_text("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN primary_strategy IS NOT NULL THEN 1 ELSE 0 END) as classified,
               SUM(CASE WHEN etp_category IS NOT NULL THEN 1 ELSE 0 END) as legacy_classified
        FROM mkt_master_data WHERE market_status = 'ACTV'
    """)).fetchone()
    coverage = {
        "total": total_row[0] if total_row else 0,
        "classified": total_row[1] if total_row else 0,
        "legacy_classified": total_row[2] if total_row else 0,
    }
    if coverage["total"] > 0:
        coverage["pct"] = round(100 * coverage["classified"] / coverage["total"], 1)
        coverage["legacy_pct"] = round(100 * coverage["legacy_classified"] / coverage["total"], 1)
    else:
        coverage["pct"] = 0
        coverage["legacy_pct"] = 0

    # Per-column coverage % across the full 3-axis taxonomy + attribute set
    # (introduced for the categorization application sweep — task #98).
    per_column_coverage: list[dict] = []
    target_cols = [
        "asset_class", "primary_strategy", "sub_strategy",
        "concentration", "underlier_name", "mechanism",
        "leverage_ratio", "direction", "reset_period", "distribution_freq",
        "cap_pct", "buffer_pct", "barrier_pct",
        "region", "duration_bucket", "credit_quality",
    ]
    for col in target_cols:
        try:
            row = db.execute(sa_text(f"""
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN {col} IS NOT NULL AND {col} != '' THEN 1 ELSE 0 END) AS pop
                FROM mkt_master_data
                WHERE market_status IN ('ACTV','PEND')
            """)).fetchone()
            t = row[0] or 0
            p = row[1] or 0
            pct = round(100 * p / t, 1) if t else 0
            per_column_coverage.append({
                "column": col, "populated": int(p), "total": int(t), "pct": pct,
            })
        except Exception as e:
            log.warning("per-column coverage query failed for %s: %s", col, e)

    # Last sweep summary (from classification_audit_log)
    last_sweep = None
    try:
        last_row = db.execute(sa_text("""
            SELECT sweep_run_id,
                   MIN(created_at) AS started_at,
                   COUNT(*) AS rows_logged,
                   SUM(CASE WHEN source = 'sweep_high' THEN 1 ELSE 0 END) AS high_fills,
                   SUM(CASE WHEN source = 'sweep_medium' THEN 1 ELSE 0 END) AS med_fills,
                   SUM(CASE WHEN source = 'conflict' THEN 1 ELSE 0 END) AS conflicts,
                   MAX(CASE WHEN dry_run = 1 THEN 1 ELSE 0 END) AS any_dry_run
            FROM classification_audit_log
            WHERE sweep_run_id IS NOT NULL
            GROUP BY sweep_run_id
            ORDER BY started_at DESC
            LIMIT 1
        """)).fetchone()
        if last_row:
            last_sweep = {
                "sweep_run_id": last_row[0],
                "started_at": str(last_row[1]) if last_row[1] else "",
                "rows_logged": int(last_row[2] or 0),
                "high_fills": int(last_row[3] or 0),
                "med_fills": int(last_row[4] or 0),
                "conflicts": int(last_row[5] or 0),
                "dry_run": bool(last_row[6]),
            }
    except Exception as e:
        log.warning("last sweep query failed (table may not exist yet): %s", e)

    # Pending proposals queue size
    pending_proposals = 0
    try:
        pending_proposals = db.execute(sa_text(
            "SELECT COUNT(*) FROM classification_proposals WHERE status='pending'"
        )).scalar() or 0
    except Exception:
        pending_proposals = 0

    # Latest conflicts CSV link (file generated under docs/classification_conflicts_YYYY-MM-DD.csv)
    from pathlib import Path
    docs_dir = Path(__file__).resolve().parent.parent.parent / "docs"
    latest_conflicts_csv = None
    if docs_dir.exists():
        candidates = sorted(docs_dir.glob("classification_conflicts_*.csv"), reverse=True)
        if candidates:
            latest_conflicts_csv = candidates[0].name  # filename only — link as docs path

    return templates.TemplateResponse("admin_classification_stats.html", {
        "request": request,
        "by_primary_strategy": by_primary_strategy,
        "by_asset_class": by_asset_class,
        "by_sub_strategy": by_sub_strategy,
        "coverage": coverage,
        "per_column_coverage": per_column_coverage,
        "last_sweep": last_sweep,
        "pending_proposals": int(pending_proposals),
        "latest_conflicts_csv": latest_conflicts_csv,
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


# --- Trust CRUD (direct add/deactivate, no approval queue) ---

@router.post("/trusts/add")
def add_trust_direct(
    request: Request,
    cik: str = Form(""),
    name: str = Form(""),
    is_rex: str = Form(""),
    db: Session = Depends(get_db),
):
    """Directly add a trust to the monitored list (admin bypass for approval queue)."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    from urllib.parse import quote
    cik = normalize_cik(cik.strip())
    name = name.strip()

    if not cik or not name:
        return RedirectResponse("/admin/?trust_error=missing_fields", status_code=303)

    # Verify CIK against SEC submissions JSON before adding
    try:
        import requests as _req
        padded = cik.zfill(10)
        url = f"https://data.sec.gov/submissions/CIK{padded}.json"
        resp = _req.get(url, headers={"User-Agent": "REX-ETP-Tracker/2.0 (relasmar@rexfin.com)"}, timeout=15)
        if resp.status_code != 200:
            return RedirectResponse(f"/admin/?trust_error=cik_not_found&cik={cik}", status_code=303)
        sec_data = resp.json()
        sec_name = sec_data.get("name", "")
        # Sanity check the name matches (loosely)
        if name.lower() not in sec_name.lower() and sec_name.lower() not in name.lower():
            log.warning("Trust name mismatch: user='%s' SEC='%s'", name, sec_name)
    except Exception as e:
        log.warning("SEC verification failed for CIK %s: %s", cik, e)
        return RedirectResponse(f"/admin/?trust_error=verify_failed&msg={quote(str(e)[:50])}", status_code=303)

    existing = db.execute(select(Trust).where(Trust.cik == cik)).scalar_one_or_none()
    if existing:
        if not existing.is_active:
            existing.is_active = True
            db.commit()
            return RedirectResponse("/admin/?trust_reactivated=1", status_code=303)
        return RedirectResponse("/admin/?trust_error=already_exists", status_code=303)

    slug = re.sub(r"[^a-z0-9]+", "-", name.lower().strip()).strip("-")
    db.add(Trust(
        cik=cik,
        name=name,
        slug=slug,
        is_rex=bool(is_rex),
        is_active=True,
        added_by="ADMIN_DIRECT",
    ))
    db.commit()

    # Also add to trusts.py registry
    try:
        from etp_tracker.trusts import add_trust
        add_trust(cik, name)
    except Exception as e:
        log.warning("Could not write to trusts.py: %s", e)

    return RedirectResponse("/admin/?trust_added=1", status_code=303)


@router.post("/trusts/deactivate")
def deactivate_trust(
    request: Request,
    trust_id: int = Form(0),
    db: Session = Depends(get_db),
):
    """Deactivate a trust (stops monitoring without deleting)."""
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    trust = db.query(Trust).filter(Trust.id == trust_id).first()
    if not trust:
        return RedirectResponse("/admin/?trust_error=not_found", status_code=303)

    trust.is_active = False
    db.commit()
    return RedirectResponse("/admin/?trust_deactivated=1", status_code=303)


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


@router.get("/digest/debug-daily")
def debug_daily(request: Request, db: Session = Depends(get_db)):
    """Debug endpoint — returns the dict gathered for the daily digest as JSON.

    Lets the operator compare what _gather_daily_data sees on this DB vs VPS
    when the rendered HTML differs (e.g., Render missing sections that VPS has).
    Reports row counts on each section's source data so empty sections can be
    traced to either (a) missing source rows or (b) renderer logic.
    """
    if not _is_admin(request):
        return RedirectResponse("/admin/", status_code=302)

    from etp_tracker.email_alerts import _gather_daily_data, _gather_pipeline_funds
    from datetime import date
    from sqlalchemy import text

    # Gather what the daily build sees
    try:
        data = _gather_daily_data(db, edition="daily")
    except Exception as e:
        import traceback as _tb
        return {"error": str(e), "trace": _tb.format_exc()}

    # Section-by-section row counts
    summary = {
        "section_counts": {},
        "section_keys_top_level": list(data.keys()) if isinstance(data, dict) else None,
        "filings_today_db": None,
        "filings_yesterday_db": None,
        "rex_products_count": None,
        "fund_status_pending_count": None,
        "mkt_master_data_count": None,
        "mkt_pend_30d_count": None,
        "filing_analyses_count": None,
        "today": date.today().isoformat(),
    }

    # Key section sizes from the gathered dict
    for k in ("filing_groups", "updated_groups", "top_filings",
              "pending", "launches", "pipeline_funds"):
        v = data.get(k) if isinstance(data, dict) else None
        if isinstance(v, list):
            summary["section_counts"][k] = len(v)
        elif v is None:
            summary["section_counts"][k] = "MISSING_KEY"
        else:
            summary["section_counts"][k] = type(v).__name__

    # Direct DB probes
    today = date.today().isoformat()
    try:
        summary["filings_today_db"] = db.execute(
            text("SELECT COUNT(*) FROM filings WHERE filing_date = :d AND form LIKE '485%'"),
            {"d": today},
        ).scalar()
    except Exception as e:
        summary["filings_today_db"] = f"ERR: {e}"
    try:
        summary["rex_products_count"] = db.execute(text("SELECT COUNT(*) FROM rex_products")).scalar()
    except Exception as e:
        summary["rex_products_count"] = f"ERR: {e}"
    try:
        summary["fund_status_pending_count"] = db.execute(
            text("SELECT COUNT(*) FROM fund_status WHERE status='PENDING'")
        ).scalar()
    except Exception as e:
        summary["fund_status_pending_count"] = f"ERR: {e}"
    try:
        summary["mkt_master_data_count"] = db.execute(text("SELECT COUNT(*) FROM mkt_master_data")).scalar()
    except Exception as e:
        summary["mkt_master_data_count"] = f"ERR: {e}"
    try:
        summary["mkt_pend_30d_count"] = len(_gather_pipeline_funds())
    except Exception as e:
        summary["mkt_pend_30d_count"] = f"ERR: {e}"
    try:
        summary["filing_analyses_count"] = db.execute(text("SELECT COUNT(*) FROM filing_analyses")).scalar()
    except Exception as e:
        summary["filing_analyses_count"] = f"ERR: {e}"

    return summary


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
        ok = _send_html_digest(html, ["relasmar@rexfin.com"], bypass_gate=True, edition="daily")

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
        ok = _send_html_digest(html, ["relasmar@rexfin.com"], bypass_gate=True, edition="morning")

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
        ok = _send_html_digest(html, ["relasmar@rexfin.com"], bypass_gate=True, edition="daily",
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
        ok = _send_html_digest(html, ["relasmar@rexfin.com"], bypass_gate=True, edition="daily",
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
        ok = _send_html_digest(html, ["relasmar@rexfin.com"], bypass_gate=True, edition="daily",
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
