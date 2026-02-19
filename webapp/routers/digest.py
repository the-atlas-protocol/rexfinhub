"""
Digest router - Subscribe page and send endpoint.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

router = APIRouter(prefix="/digest", tags=["digest"])
templates = Jinja2Templates(directory="webapp/templates")

OUTPUT_DIR = Path("outputs")
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
SUBSCRIBERS_FILE = PROJECT_ROOT / "config" / "digest_subscribers.txt"

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")


def _already_subscribed(email: str) -> bool:
    """Check if email is already in subscribers file."""
    if not SUBSCRIBERS_FILE.exists():
        return False
    content = SUBSCRIBERS_FILE.read_text(encoding="utf-8")
    return email.lower() in content.lower()


@router.get("/subscribe")
def subscribe_page(request: Request):
    """Show the digest subscription form."""
    return templates.TemplateResponse("digest_subscribe.html", {
        "request": request,
        "submitted": False,
        "error": None,
    })


@router.post("/subscribe")
def subscribe_submit(request: Request, email: str = Form(...)):
    """Handle subscription request."""
    email = email.strip().lower()

    if not _EMAIL_RE.match(email):
        return templates.TemplateResponse("digest_subscribe.html", {
            "request": request,
            "submitted": False,
            "error": "Please enter a valid email address.",
        })

    if _already_subscribed(email):
        return templates.TemplateResponse("digest_subscribe.html", {
            "request": request,
            "submitted": False,
            "error": "This email has already been submitted.",
        })

    # Append to subscribers file
    timestamp = datetime.now().isoformat(timespec="seconds")
    line = f"PENDING|{email}|{timestamp}\n"
    with open(SUBSCRIBERS_FILE, "a", encoding="utf-8") as f:
        f.write(line)

    return templates.TemplateResponse("digest_subscribe.html", {
        "request": request,
        "submitted": True,
        "error": None,
    })


@router.post("/send")
def send_digest(request: Request):
    """Send the digest email now (admin action)."""
    try:
        from etp_tracker.email_alerts import send_digest_email

        dashboard_url = str(request.base_url).rstrip("/")
        sent = send_digest_email(OUTPUT_DIR, dashboard_url=dashboard_url)

        if sent:
            return RedirectResponse("/digest/subscribe?sent=ok", status_code=303)
        return RedirectResponse("/digest/subscribe?sent=fail", status_code=303)
    except Exception:
        return RedirectResponse("/digest/subscribe?sent=fail", status_code=303)
