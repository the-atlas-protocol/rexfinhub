"""Session-tied CSRF token helpers.

Used by `CsrfMiddleware` (in webapp/main.py) to protect state-mutating admin
endpoints. The token is generated lazily per session and stored in
``request.session["csrf_token"]``.

Verification accepts the token from any of:

  * form field ``_csrf_token`` (multipart or urlencoded POST)
  * header ``X-CSRF-Token`` (used by JS fetch calls)
  * query parameter ``_csrf_token`` (last-resort fallback)

Endpoints excluded from CSRF (and the rationale):

  * ``/admin/login`` — there is no session yet; the password itself is the
    bearer credential.
  * ``/api/v1/db/*`` and other ``/api/v1/*`` routes — gated by ``X-API-Key``
    and called by machines, not browsers, so CSRF would be friction without
    benefit. Browser-callable admin endpoints under ``/api/v1/maintenance``
    *are* covered.
"""
from __future__ import annotations

import hmac
import secrets

CSRF_SESSION_KEY = "csrf_token"
CSRF_FORM_FIELD = "_csrf_token"
CSRF_HEADER = "X-CSRF-Token"


def get_or_create_token(session: dict) -> str:
    """Return the session's CSRF token, creating one if absent."""
    token = session.get(CSRF_SESSION_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        session[CSRF_SESSION_KEY] = token
    return token


def is_valid(session: dict, presented: str | None) -> bool:
    """Constant-time compare presented token against the session token."""
    if not presented:
        return False
    expected = session.get(CSRF_SESSION_KEY) or ""
    if not expected:
        return False
    return hmac.compare_digest(expected, presented)
