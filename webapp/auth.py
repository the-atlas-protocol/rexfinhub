"""
Azure AD SSO Authentication via MSAL.

Uses the authorization code flow:
1. User visits /auth/login -> redirect to Microsoft login
2. Microsoft redirects back to /auth/callback with auth code
3. We exchange code for tokens, store user info in session
4. get_current_user dependency checks session on protected routes
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _load_auth_config() -> dict[str, str]:
    """Load Azure AD config from .env or environment."""
    env_vars: dict[str, str] = {}
    env_file = Path(__file__).resolve().parent.parent / "config" / ".env"
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
        "session_secret": env_vars.get("SESSION_SECRET", os.environ.get("SESSION_SECRET", "dev-secret-change-me")),
    }


_config = _load_auth_config()

TENANT_ID = _config["tenant_id"]
CLIENT_ID = _config["client_id"]
CLIENT_SECRET = _config["client_secret"]
SESSION_SECRET = _config["session_secret"]

AUTHORITY = f"https://login.microsoftonline.com/{TENANT_ID}" if TENANT_ID else ""
SCOPES = ["User.Read"]
REDIRECT_PATH = "/auth/callback"


def is_auth_configured() -> bool:
    """Check if Azure AD credentials are set."""
    return all([TENANT_ID, CLIENT_ID, CLIENT_SECRET])


def get_msal_app(redirect_uri: str | None = None):
    """Create an MSAL ConfidentialClientApplication."""
    try:
        import msal
    except ImportError:
        log.error("msal package not installed. Run: pip install msal")
        return None

    return msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=AUTHORITY,
        client_credential=CLIENT_SECRET,
    )


def build_auth_url(redirect_uri: str) -> str:
    """Generate the Azure AD authorization URL."""
    app = get_msal_app()
    if not app:
        return ""
    flow = app.initiate_auth_code_flow(scopes=SCOPES, redirect_uri=redirect_uri)
    # Store flow in a module-level cache (simple approach for single-process)
    _auth_flows[flow["state"]] = flow
    return flow.get("auth_uri", "")


def complete_auth(code_response: dict[str, Any], redirect_uri: str) -> dict[str, Any] | None:
    """Exchange the auth code for tokens. Returns user claims dict or None."""
    state = code_response.get("state", "")
    flow = _auth_flows.pop(state, None)
    if not flow:
        log.error("No matching auth flow for state=%s", state)
        return None

    app = get_msal_app()
    if not app:
        return None

    result = app.acquire_token_by_auth_code_flow(flow, code_response)
    if "access_token" not in result:
        log.error("Token exchange failed: %s", result.get("error_description", result))
        return None

    # Extract user info from id_token claims
    claims = result.get("id_token_claims", {})
    return {
        "name": claims.get("name", "Unknown"),
        "email": claims.get("preferred_username", claims.get("email", "")),
        "oid": claims.get("oid", ""),
    }


# In-memory auth flow cache (fine for single-process deployment)
_auth_flows: dict[str, dict] = {}
