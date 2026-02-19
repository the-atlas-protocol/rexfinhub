"""
Azure Graph API Email Service

Sends email via Microsoft Graph API using client credentials flow (MSAL).
Falls back gracefully if msal is not installed or credentials are missing.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

import requests

log = logging.getLogger(__name__)

GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/users/{sender}/sendMail"
GRAPH_USER_URL = "https://graph.microsoft.com/v1.0/users/{sender}"
SCOPE = ["https://graph.microsoft.com/.default"]


def _load_env() -> dict[str, str]:
    """Load Azure config from .env file or environment."""
    env_vars: dict[str, str] = {}
    env_file = Path(__file__).resolve().parent.parent.parent / "config" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                env_vars[key.strip()] = val.strip().strip('"').strip("'")

    return {
        "tenant_id": env_vars.get("AZURE_TENANT_ID", os.environ.get("AZURE_TENANT_ID", "")),
        "client_id": env_vars.get("AZURE_CLIENT_ID", os.environ.get("AZURE_CLIENT_ID", "")),
        "client_secret": env_vars.get("AZURE_CLIENT_SECRET", os.environ.get("AZURE_CLIENT_SECRET", "")),
        "sender": env_vars.get("AZURE_SENDER", os.environ.get("AZURE_SENDER", "")),
    }


def _get_access_token(tenant_id: str, client_id: str, client_secret: str) -> str | None:
    """Acquire token via MSAL client credentials flow."""
    try:
        import msal
    except ImportError:
        log.error("msal package not installed. Run: pip install msal")
        return None

    authority = f"https://login.microsoftonline.com/{tenant_id}"
    app = msal.ConfidentialClientApplication(
        client_id,
        authority=authority,
        client_credential=client_secret,
    )
    result = app.acquire_token_for_client(scopes=SCOPE)

    if "access_token" in result:
        log.info("Azure AD token acquired successfully")
        return result["access_token"]
    else:
        log.error("Token acquisition failed: %s", result.get("error_description", result))
        return None


def is_configured() -> bool:
    """Check if Azure Graph API credentials are configured."""
    cfg = _load_env()
    return all([cfg["tenant_id"], cfg["client_id"], cfg["client_secret"], cfg["sender"]])


def test_connection() -> bool:
    """Test Azure connection by acquiring token and verifying sender mailbox."""
    cfg = _load_env()
    if not all([cfg["tenant_id"], cfg["client_id"], cfg["client_secret"], cfg["sender"]]):
        log.error("Azure credentials not configured in .env")
        return False

    token = _get_access_token(cfg["tenant_id"], cfg["client_id"], cfg["client_secret"])
    if not token:
        return False

    url = GRAPH_USER_URL.format(sender=cfg["sender"])
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, timeout=15)

    if resp.status_code == 200:
        data = resp.json()
        log.info("Azure connection OK. Mailbox: %s (%s)", data.get("mail"), data.get("displayName"))
        return True
    else:
        log.error("Cannot access sender mailbox [%d]: %s", resp.status_code, resp.text)
        return False


def send_email(
    subject: str,
    html_body: str,
    recipients: list[str],
) -> bool:
    """Send email via Microsoft Graph API.
    Returns True on success, False on failure."""
    cfg = _load_env()
    if not all([cfg["tenant_id"], cfg["client_id"], cfg["client_secret"], cfg["sender"]]):
        log.warning("Azure Graph API not configured")
        return False

    token = _get_access_token(cfg["tenant_id"], cfg["client_id"], cfg["client_secret"])
    if not token:
        return False

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
        },
        "saveToSentItems": "true",
    }
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    resp = requests.post(url, json=payload, headers=headers, timeout=30)

    if resp.status_code == 202:
        log.info("Email sent via Graph API to %s", ", ".join(recipients))
        return True
    else:
        log.error("Graph API send failed [%d]: %s", resp.status_code, resp.text)
        return False
