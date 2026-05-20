"""Send screener executive PDF report via Microsoft Graph API."""
from __future__ import annotations

import base64
import logging
from datetime import datetime

import requests as http_requests

log = logging.getLogger(__name__)

GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"


def send_screener_report(
    pdf_bytes: bytes,
    recipients: list[str],
    subject: str | None = None,
) -> bool:
    """Send the screener PDF report as email attachment via Graph API.

    Uses the same Azure AD credentials as the existing graph_email module.
    Returns True on success.
    """
    # --- SEND GATE (matches email_alerts._send_html_digest pattern) ---
    try:
        from webapp.services.system_flags import get_flag as _flag
        _gate_open = _flag("send_enabled")
    except ImportError:
        from pathlib import Path as _P
        _gate = _P(__file__).resolve().parent.parent / "config" / ".send_enabled"
        _gate_open = _gate.exists() and _gate.read_text().strip().lower() == "true"
    if not _gate_open:
        log.warning("SEND BLOCKED (Screener): send_enabled flag is not set.")
        return False
    # --- END SEND GATE ---

    from webapp.services.graph_email import _load_env, _get_access_token

    cfg = _load_env()
    if not all([cfg["tenant_id"], cfg["client_id"], cfg["client_secret"], cfg["sender"]]):
        log.warning("Azure Graph API not configured")
        return False

    token = _get_access_token(cfg["tenant_id"], cfg["client_id"], cfg["client_secret"])
    if not token:
        return False

    if not subject:
        subject = f"ETF Launch Screener Report - {datetime.now().strftime('%B %d, %Y')}"

    filename = f"ETF_Launch_Screener_{datetime.now().strftime('%Y%m%d')}.pdf"

    html_body = f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 600px;">
        <h2 style="color: #1a1a2e;">ETF Launch Screener Report</h2>
        <p>Please find attached the latest ETF Launch Screener executive report.</p>
        <p>The report includes:</p>
        <ul>
            <li>Top launch candidates ranked by composite score</li>
            <li>Competitive landscape analysis</li>
            <li>REX fund portfolio performance</li>
            <li>Scoring methodology and model diagnostics</li>
        </ul>
        <p style="color: #636e72; font-size: 12px;">
            Generated {datetime.now().strftime('%B %d, %Y at %H:%M')} |
            <a href="https://rex-etp-tracker.onrender.com/screener/">View Online Dashboard</a>
        </p>
    </div>
    """

    url = GRAPH_SEND_URL.format(sender=cfg["sender"])
    payload = {
        "message": {
            "subject": subject,
            "body": {
                "contentType": "HTML",
                "content": html_body,
            },
            "toRecipients": [
                {"emailAddress": {"address": addr}} for addr in recipients
            ],
            "attachments": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "name": filename,
                    "contentType": "application/pdf",
                    "contentBytes": base64.b64encode(pdf_bytes).decode("utf-8"),
                }
            ],
        },
        "saveToSentItems": "true",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    resp = http_requests.post(url, json=payload, headers=headers, timeout=30)

    if resp.status_code == 202:
        log.info("Screener report sent to %s", ", ".join(recipients))
        return True
    else:
        log.error("Failed to send screener report [%d]: %s", resp.status_code, resp.text)
        return False
