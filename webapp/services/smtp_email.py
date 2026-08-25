# -*- coding: utf-8 -*-
"""SMTP transport for REX report email — mirrors webapp.services.graph_email.send_email.

Why this exists: the Graph app registration (REX_Automation) was disabled in Entra on
2026-08-25, so every send fails with AADSTS7000112. _send_html_digest had its SMTP
fallback deliberately removed at some point ("SMTP fallback disabled"), leaving no way
out. This restores SMTP as an EXPLICIT choice, never a silent fallback — a quiet
fallback could ship reports from the wrong address without anyone noticing, and three
of these go to RBC, CAIS and BMO.

Select it with REXFIN_SEND_TRANSPORT=smtp. Anything else keeps the Graph path.

Matches the Graph signature exactly, including inline CID images and file attachments,
so the rendered email is identical to what recipients normally receive.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from email.utils import formataddr, make_msgid

log = logging.getLogger(__name__)


def is_configured() -> bool:
    """True when every setting needed for an SMTP send is present."""
    need = ("SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_FROM")
    missing = [k for k in need if not os.environ.get(k)]
    if not (os.environ.get("SMTP_PASS") or os.environ.get("SMTP_PASSWORD")):
        missing.append("SMTP_PASS")
    if missing:
        log.error("SMTP not configured — missing: %s", ", ".join(missing))
        return False
    return True


def send_email(
    subject: str,
    html_body: str,
    recipients: list[str],
    images: list[tuple[str, bytes, str]] | None = None,
    attachments: list[tuple[str, bytes, str]] | None = None,
    bypass_gate: bool = False,
) -> bool:
    """Send one HTML email over SMTP. Returns True only on a clean handoff.

    Signature deliberately identical to graph_email.send_email so the caller does not
    care which transport is in use.
    """
    if not is_configured():
        return False
    if not recipients:
        log.error("SMTP send called with no recipients: %s", subject)
        return False

    host = os.environ["SMTP_HOST"]
    port = int(os.environ.get("SMTP_PORT", "587"))
    user = os.environ["SMTP_USER"]
    pwd = os.environ.get("SMTP_PASS") or os.environ.get("SMTP_PASSWORD")
    sender = os.environ["SMTP_FROM"]
    display = os.environ.get("SMTP_FROM_NAME", "REX Financial")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = formataddr((display, sender))
    msg["To"] = ", ".join(recipients)
    msg.set_content(
        "This report is formatted in HTML. Please view it in an HTML-capable client."
    )

    # Inline images are referenced as cid:<content_id> in the HTML. The Graph payload
    # uses a bare contentId, so rewrite to a real Message-ID form and patch the HTML to
    # match, otherwise the images render as broken boxes.
    cid_map = {}
    if images:
        for cid, _png, _fname in images:
            cid_map[cid] = make_msgid(domain="rexfin.com")
        for old, new in cid_map.items():
            html_body = html_body.replace(f"cid:{old}", f"cid:{new[1:-1]}")

    msg.add_alternative(html_body, subtype="html")
    html_part = msg.get_payload()[-1]

    if images:
        for cid, png_bytes, filename in images:
            html_part.add_related(
                png_bytes, maintype="image", subtype="png",
                cid=cid_map[cid], filename=filename,
            )

    if attachments:
        for fname, fbytes, mime in attachments:
            main, _, sub = (mime or "application/octet-stream").partition("/")
            msg.add_attachment(fbytes, maintype=main or "application",
                               subtype=sub or "octet-stream", filename=fname)

    try:
        with smtplib.SMTP(host, port, timeout=60) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(user, pwd)
            s.send_message(msg)
        log.info("SMTP send OK via %s as %s — %d recipient(s): %s",
                 host, sender, len(recipients), subject)
        return True
    except smtplib.SMTPAuthenticationError as e:
        log.error("SMTP AUTH REJECTED for %s on %s — %s. If this is Microsoft 365, SMTP "
                  "AUTH is disabled per-mailbox by default and a tenant admin must enable "
                  "it (Exchange admin > mailbox > Manage email apps > Authenticated SMTP).",
                  user, host, str(e)[:200])
        return False
    except Exception as e:
        log.error("SMTP send failed via %s: %s: %s", host, type(e).__name__, str(e)[:200])
        return False
