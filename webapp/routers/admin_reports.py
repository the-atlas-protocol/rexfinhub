"""Admin reports preview page — serves pre-baked static HTML.

The heavy lifting (SQL queries, Bloomberg data loading, template rendering)
happens on the VPS via scripts/prebake_reports.py. Files are uploaded to
Render via POST /api/v1/reports/upload/{report_key} and stored at
data/prebaked_reports/{key}.html. This page just reads the static file.

Result: instant page load on Render, no per-view compute cost.

2026-04-28 refactor: REPORT_CATALOG is now a thin compatibility shim over
`webapp/services/report_registry`. Adding a new report = one entry in
the registry; preview, dashboard, send_all, and admin endpoints all
pick it up automatically.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.services import report_registry
from webapp.services.admin_auth import load_admin_password

log = logging.getLogger(__name__)
router = APIRouter(prefix="/admin/reports", tags=["admin-reports"])
templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

ADMIN_PASSWORD = load_admin_password()
PREBAKED_DIR = Path("data/prebaked_reports")
DECISION_FILE = Path("data/.preflight_decision.json")
TOKEN_FILE = Path("data/.preflight_token")


def _check_auth(request: Request) -> bool:
    return (
        request.cookies.get("admin_auth") == ADMIN_PASSWORD
        or request.session.get("is_admin") is True
    )


# Backwards-compat shim — derived from report_registry.REGISTRY at import time.
# Existing call sites (preview_landing, preview_raw, anything that imports
# REPORT_CATALOG) keep working unchanged. New code should import from
# `webapp.services.report_registry` directly.
REPORT_CATALOG = report_registry.as_legacy_dict()


def _load_metadata(report_key: str) -> dict:
    """Load the .meta.json sidecar for a pre-baked report."""
    meta_path = PREBAKED_DIR / f"{report_key}.meta.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text())
    except Exception:
        return {}


def _report_status(report_key: str) -> dict:
    """Return status for a report: exists, baked_at, size, etc."""
    html_path = PREBAKED_DIR / f"{report_key}.html"
    if not html_path.exists():
        return {"exists": False, "baked_at": None, "size_bytes": 0}

    meta = _load_metadata(report_key)
    return {
        "exists": True,
        "baked_at": meta.get("baked_at"),
        "size_bytes": html_path.stat().st_size,
    }


@router.get("/preview", response_class=HTMLResponse)
def preview_landing(request: Request, db: Session = Depends(get_db)):
    """Admin landing page listing all pre-baked reports."""
    if not _check_auth(request):
        return RedirectResponse("/admin/", status_code=302)

    # Enrich each report with its current file status
    enriched = {}
    for key, meta in REPORT_CATALOG.items():
        enriched[key] = {**meta, **_report_status(key)}

    return templates.TemplateResponse("admin_reports_preview.html", {
        "request": request,
        "reports": enriched,
        "now": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "prebaked_dir": str(PREBAKED_DIR),
    })


@router.get("/preview/{report_key}/raw", response_class=HTMLResponse)
def preview_raw(report_key: str, request: Request):
    """Serve the pre-baked HTML for a report. Instant — no rendering."""
    if not _check_auth(request):
        return HTMLResponse("<h2>Unauthorized</h2>", status_code=401)

    if report_key not in REPORT_CATALOG:
        return HTMLResponse("<h2>Unknown report</h2>", status_code=404)

    html_path = PREBAKED_DIR / f"{report_key}.html"
    if not html_path.exists():
        return HTMLResponse(
            f"""
            <html><body style='font-family:sans-serif; padding:40px; background:#f8fafc;'>
            <div style='max-width:600px; margin:0 auto; background:white; padding:24px; border-radius:6px; border-left:3px solid #d97706;'>
            <h2 style='margin:0 0 8px; color:#0f172a;'>Not baked yet</h2>
            <p style='color:#374151;'>This report hasn't been baked yet. Run <code>python scripts/prebake_reports.py</code> on the VPS.</p>
            </div></body></html>
            """,
            status_code=404,
        )

    return HTMLResponse(html_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Send-day dashboard + GO/HOLD decision endpoint
# Added 2026-04-28 (plan task #6).
# ---------------------------------------------------------------------------

def _read_token() -> dict | None:
    """Load the current preflight token (written by preflight_check.py).

    Returns None if no token file or invalid JSON. The token expires
    `valid_for_hours` after `created_et`; expiration is enforced at
    decision time, not here.
    """
    if not TOKEN_FILE.exists():
        return None
    try:
        return json.loads(TOKEN_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_decision() -> dict | None:
    if not DECISION_FILE.exists():
        return None
    try:
        return json.loads(DECISION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return None


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    """Send-day dashboard — preview URLs for every active report + GO/HOLD buttons.

    Replaces the per-report manual click flow with a single page Ryu can
    open from the preflight summary email.
    """
    if not _check_auth(request):
        return RedirectResponse("/admin/", status_code=302)

    active = report_registry.get_active()
    token_info = _read_token()
    decision = _read_decision()

    # Build report rows
    rows = []
    for r in active:
        status = _report_status(r.key)
        size_str = f"{status['size_bytes']:,} B" if status["exists"] else "(not baked)"
        rows.append(f"""
        <tr>
          <td style="padding:10px 12px;border-bottom:1px solid #ecf0f1;">
            <strong>{r.name}</strong><br>
            <span style="font-size:11px;color:#7f8c8d;">{r.description}</span>
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #ecf0f1;font-size:11px;color:#566573;">
            {r.bundle}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #ecf0f1;font-size:11px;color:#566573;">
            {size_str}
          </td>
          <td style="padding:10px 12px;border-bottom:1px solid #ecf0f1;">
            <a href="/admin/reports/preview/{r.key}/raw" target="_blank"
               style="color:#0984e3;text-decoration:none;font-size:12px;">Preview &rarr;</a>
          </td>
        </tr>
        """)
    rows_html = "".join(rows)

    # Token + decision panel
    if token_info:
        token = token_info.get("token", "")
        created = token_info.get("created_et", "")
        token_panel = f"""
        <div style="margin:16px 0;padding:14px;background:#f4f5f6;border-radius:6px;">
          <strong>Preflight token:</strong> <code>{token[:8]}…</code>
          (created {created}, valid 4h)
        </div>
        """
    else:
        token_panel = """
        <div style="margin:16px 0;padding:14px;background:#fef2f2;border-radius:6px;border-left:4px solid #e74c3c;">
          <strong>No preflight token.</strong> Run <code>preflight_check.py</code> on the VPS first.
        </div>
        """

    if decision:
        decision_panel = f"""
        <div style="margin:16px 0;padding:14px;background:#ecfdf5;border-radius:6px;border-left:4px solid #27ae60;">
          <strong>Latest decision:</strong> {decision.get("action", "?").upper()}
          at {decision.get("recorded_et", "?")} ({decision.get("note", "")})
        </div>
        """
    else:
        decision_panel = ""

    # Action buttons
    if token_info:
        token = token_info.get("token", "")
        n = len(active)
        actions = f"""
        <form method="POST" action="/admin/reports/decision" style="display:inline-block;margin-right:12px;">
          <input type="hidden" name="token" value="{token}">
          <input type="hidden" name="action" value="GO">
          <button type="submit" style="background:#27ae60;color:white;border:none;padding:12px 24px;
                  border-radius:6px;font-size:14px;font-weight:700;cursor:pointer;">GO &mdash; Send all {n}</button>
        </form>
        <form method="POST" action="/admin/reports/decision" style="display:inline-block;">
          <input type="hidden" name="token" value="{token}">
          <input type="hidden" name="action" value="HOLD">
          <button type="submit" style="background:#e74c3c;color:white;border:none;padding:12px 24px;
                  border-radius:6px;font-size:14px;font-weight:700;cursor:pointer;">HOLD &mdash; Investigate</button>
        </form>
        """
    else:
        actions = '<p style="color:#7f8c8d;">Decision buttons appear once a preflight token is present.</p>'

    return HTMLResponse(f"""<!DOCTYPE html><html><head><meta charset="UTF-8">
<title>REX Send-Day Dashboard</title></head>
<body style="font-family:-apple-system,sans-serif;color:#1a1a2e;padding:20px;max-width:960px;margin:0 auto;">
<h2 style="margin:0 0 8px;">Send-Day Dashboard</h2>
<p style="font-size:13px;color:#7f8c8d;margin:0 0 16px;">As of {datetime.now().strftime('%Y-%m-%d %H:%M %Z')}</p>
{token_panel}
{decision_panel}
<table style="width:100%;border-collapse:collapse;border:1px solid #dee2e6;border-radius:6px;overflow:hidden;">
  <thead><tr style="background:#1a1a2e;color:white;">
    <th style="padding:10px 12px;text-align:left;">Report</th>
    <th style="padding:10px 12px;text-align:left;">Bundle</th>
    <th style="padding:10px 12px;text-align:left;">Baked</th>
    <th style="padding:10px 12px;text-align:left;">Preview</th>
  </tr></thead>
  <tbody>{rows_html}</tbody>
</table>
<div style="margin:24px 0;padding:16px;background:#f8f9fa;border-radius:6px;">
  {actions}
</div>
<p style="font-size:11px;color:#7f8c8d;">
  GO records the decision to <code>data/.preflight_decision.json</code>. The next
  scheduled <code>send_all.py</code> run consumes the decision + token. HOLD records
  the same to block the send. Decisions expire when the token expires (4h).
</p>
</body></html>""")


@router.post("/decision")
def record_decision(request: Request,
                    token: str = Form(...),
                    action: str = Form(...)):
    """Record a GO/HOLD decision against the current preflight token.

    `send_all.py` (when invoked from the scheduled timer) reads this file,
    verifies the token matches the current preflight, and only fires when
    action == "GO".
    """
    if not _check_auth(request):
        return JSONResponse({"error": "unauthorized"}, status_code=401)

    if action.upper() not in ("GO", "HOLD"):
        return JSONResponse({"error": "invalid action"}, status_code=400)

    token_info = _read_token()
    if not token_info:
        return JSONResponse({"error": "no active preflight token"}, status_code=409)
    if token_info.get("token") != token:
        return JSONResponse({"error": "token mismatch"}, status_code=409)

    try:
        from zoneinfo import ZoneInfo
        recorded_et = datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")
    except Exception:
        recorded_et = datetime.now().isoformat(timespec="seconds")

    payload = {
        "token": token,
        "action": action.upper(),
        "recorded_et": recorded_et,
        "note": f"Recorded via /admin/reports/decision",
    }
    DECISION_FILE.parent.mkdir(parents=True, exist_ok=True)
    DECISION_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    log.info("Send decision recorded: %s for token %s", action.upper(), token[:8])

    return RedirectResponse("/admin/reports/dashboard", status_code=303)
