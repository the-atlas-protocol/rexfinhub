"""
FastAPI dependencies for the ETP Filing Tracker.
"""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import RedirectResponse

from webapp.auth import is_auth_configured
from webapp.database import SessionLocal, HoldingsSessionLocal


def get_db():
    """Yields a DB session, auto-closes on completion."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_holdings_db():
    """Yields a 13F holdings DB session, auto-closes."""
    db = HoldingsSessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(request: Request) -> dict | None:
    """Return user dict from session, or None if not authenticated.
    When Azure AD is not configured, returns a default user (no auth required)."""
    if not is_auth_configured():
        return {"name": "Local User", "email": "", "oid": ""}
    return request.session.get("user")


def require_auth(request: Request):
    """Dependency that redirects to login if not authenticated.
    Use on routes that require authentication."""
    user = get_current_user(request)
    if user is None:
        return RedirectResponse("/auth/login", status_code=302)
    return user


def freshness_context(request: Request, db=None):
    """Build data freshness context for a template response.

    Usage in route handler:
        ctx = {"request": request, ...other context...}
        ctx.update(freshness_context(request, db))
        return templates.TemplateResponse("page.html", ctx)
    """
    try:
        from webapp.services.data_freshness import get_freshness, sources_for_path
        if db is None:
            _db = SessionLocal()
            _close = True
        else:
            _db = db
            _close = False
        try:
            return {
                "data_freshness": get_freshness(_db),
                "data_sources": sources_for_path(request.url.path),
            }
        finally:
            if _close:
                _db.close()
    except Exception:
        return {"data_freshness": {}, "data_sources": []}
