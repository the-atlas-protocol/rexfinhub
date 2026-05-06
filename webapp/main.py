"""
FastAPI application factory for the ETP Filing Tracker web platform.

Run locally:
    uvicorn webapp.main:app --reload --port 8000
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Header, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse as StarletteRedirect

from webapp.auth import SESSION_SECRET
from webapp.database import init_db

log = logging.getLogger(__name__)
WEBAPP_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = WEBAPP_DIR.parent

# ---------------------------------------------------------------------------
# Site-wide password
# ---------------------------------------------------------------------------

def _load_site_password() -> str:
    """Load SITE_PASSWORD from config/.env or environment.

    In production (RENDER env var set) the password MUST be provided — the
    trivial fallback "123" is rejected to prevent accidental open access.
    """
    env_file = PROJECT_ROOT / "config" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                if key.strip() == "SITE_PASSWORD":
                    return val.strip().strip('"').strip("'")
    value = os.environ.get("SITE_PASSWORD", "")
    if not value:
        if os.environ.get("RENDER"):
            raise RuntimeError(
                "SITE_PASSWORD environment variable is required in production. "
                "Set it in the Render dashboard."
            )
        # Local dev: use a non-trivial placeholder that still makes the site
        # accessible without setting up .env.
        return "dev-site-password"
    return value


SITE_PASSWORD = _load_site_password()


def _load_admin_password() -> str:
    """Load ADMIN_PASSWORD from config/.env or environment."""
    env_file = PROJECT_ROOT / "config" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                if key.strip() == "ADMIN_PASSWORD":
                    return val.strip().strip('"').strip("'")
    return os.environ.get("ADMIN_PASSWORD", "")


_ADMIN_PASSWORD = _load_admin_password()


def _safe_redirect(url: str) -> str:
    """Ensure redirect target is a safe relative path."""
    if not url or not url.startswith("/") or url.startswith("//"):
        return "/"
    return url


# Paths that don't require site auth
_PUBLIC_PREFIXES = ("/login", "/static/", "/health", "/api/v1/", "/favicon")


class SiteAuthMiddleware(BaseHTTPMiddleware):
    """Redirect unauthenticated requests to /login."""

    async def dispatch(self, request, call_next):
        path = request.url.path
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)
        if not request.session.get("site_auth"):
            next_url = quote(path, safe="/")
            return StarletteRedirect(f"/login?next={next_url}", status_code=302)
        return await call_next(request)


class DataFreshnessMiddleware(BaseHTTPMiddleware):
    """Populate request.state with data freshness info for templates."""

    async def dispatch(self, request, call_next):
        path = request.url.path
        # Always init (even for static) so template never gets AttributeError
        request.state.data_freshness = {}
        request.state.data_sources = []
        if not any(path.startswith(p) for p in ("/static/", "/api/", "/login", "/health", "/favicon")):
            try:
                from webapp.services.data_freshness import get_freshness, sources_for_path
                from webapp.database import SessionLocal as _SL
                db = _SL()
                try:
                    request.state.data_freshness = get_freshness(db)
                    request.state.data_sources = sources_for_path(path)
                finally:
                    db.close()
            except Exception as e:
                log.debug("Freshness middleware: %s", e)
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to all responses."""

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if os.environ.get("RENDER"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

templates = Jinja2Templates(directory=str(WEBAPP_DIR / "templates"))

# Expose feature flags to all templates (used by base.html for conditional nav)
templates.env.globals["enable_13f"] = bool(os.environ.get("ENABLE_13F"))



_caches_ready = False  # flipped True when all caches are loaded


def _prewarm_caches() -> None:
    """Pre-warm all data caches synchronously at startup.

    Runs BEFORE uvicorn accepts requests so no user ever hits a cold cache.
    Keeps startup fast (~5-10s for DB reads) and avoids background-thread
    race conditions.
    """
    global _caches_ready
    import time
    t0 = time.time()

    from webapp.database import SessionLocal
    db = SessionLocal()
    try:
        # Market data: load master + time series DataFrames into memory
        try:
            from webapp.services import market_data
            if market_data.data_available(db):
                market_data._load_master(db)
                market_data._load_ts(db)
                log.info("Market data cache warmed.")
        except Exception as e:
            log.warning("Market cache warm failed (non-fatal): %s", e)

        # Screener cache
        try:
            from webapp.services.screener_3x_cache import warm_cache
            warm_cache(db=db)
            log.info("Screener cache warmed.")
        except Exception as e:
            log.warning("Screener cache warm failed (non-fatal): %s", e)
    finally:
        db.close()

    _caches_ready = True
    log.info("All caches ready in %.1fs.", time.time() - t0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize database, pre-warm all caches. Shutdown: cleanup."""
    init_db()
    log.info("Database initialized.")
    # Live feed DB — separate file so daily DB uploads don't wipe it
    from webapp.database import init_live_feed_db
    init_live_feed_db()
    log.info("Live feed database initialized (data/live_feed.db).")
    if os.environ.get("ENABLE_13F"):
        from webapp.database import init_holdings_db
        init_holdings_db()
        log.info("13F holdings database initialized (data/13f_holdings.db).")
    _prewarm_caches()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="REX FinHub",
        version="2.0.0",
        description="ETP market intelligence platform.",
        lifespan=lifespan,
        docs_url=None,   # Disable public Swagger
        redoc_url=None,   # Disable public ReDoc
        openapi_url=None, # Disable OpenAPI spec
    )

    # Middleware order matters: last added = outermost (runs first).
    # SiteAuthMiddleware needs session -> add it first (inner),
    # then SessionMiddleware (outer, decodes session before auth check).
    app.add_middleware(SiteAuthMiddleware)
    app.add_middleware(DataFreshnessMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, max_age=28800, same_site="lax", https_only=bool(os.environ.get("RENDER")))

    # Static files (CSS, JS)
    static_dir = WEBAPP_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # --- Login / Logout routes (before routers) ---

    @app.get("/login")
    def login_page(request: Request, next: str = "/"):
        if request.session.get("site_auth"):
            return RedirectResponse("/", status_code=302)
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": None, "next_url": next}
        )

    @app.post("/login")
    def login_submit(
        request: Request,
        password: str = Form(...),
        next: str = Form("/"),
    ):
        if password == SITE_PASSWORD or password == _ADMIN_PASSWORD:
            request.session["site_auth"] = True
            if password == _ADMIN_PASSWORD:
                request.session["is_admin"] = True
            return RedirectResponse(_safe_redirect(next), status_code=303)
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Invalid password", "next_url": next},
        )

    @app.get("/logout")
    def logout(request: Request):
        request.session.clear()
        return RedirectResponse("/login", status_code=302)

    # --- Home page moved to dashboard.py router (redesign 2026-03-18) ---

    # --- Routers ---
    from webapp.routers import auth_routes, dashboard, trusts, funds, search, analysis, digest, downloads, api, admin, screener, market, filings, universe, global_search, analytics, reports
    from webapp.routers.market_advanced import router as market_advanced_router
    app.include_router(auth_routes.router)
    app.include_router(dashboard.router)
    app.include_router(trusts.router, prefix="/trusts")
    app.include_router(funds.router, prefix="/funds")
    app.include_router(search.router)
    app.include_router(analysis.router)
    app.include_router(digest.router)
    app.include_router(downloads.router)
    app.include_router(api.router)
    app.include_router(admin.router)
    from webapp.routers import admin_products, pipeline_calendar, admin_reports, admin_health
    app.include_router(admin_products.router)
    app.include_router(pipeline_calendar.router)
    app.include_router(admin_reports.router)
    app.include_router(admin_health.router)
    app.include_router(screener.router)
    app.include_router(market.router)
    app.include_router(market_advanced_router)
    from webapp.routers import monitor, notes, notes_autocall
    app.include_router(monitor.router)
    app.include_router(notes.router)
    app.include_router(notes_autocall.router)
    app.include_router(filings.router, prefix="/filings")
    app.include_router(universe.router)

    # 13F Holdings: only enabled with ENABLE_13F=1 (local dev, separate DB)
    if os.environ.get("ENABLE_13F"):
        from webapp.routers import holdings
        app.include_router(holdings.router)
        # 13F Intelligence Hub (requires holdings data)
        from webapp.routers import intel, intel_competitors, intel_insights
        app.include_router(intel.router)
        app.include_router(intel_competitors.router)
        app.include_router(intel_insights.router)
    app.include_router(global_search.router)
    app.include_router(analytics.router)
    app.include_router(reports.router)
    from webapp.routers import capm
    app.include_router(capm.router)

    # L&I Strategy Engine — whitespace candidates, filing race, ticker deep-dive
    from webapp.routers import strategy
    app.include_router(strategy.router)

    # Health check -- Render uses this for zero-downtime deploys.
    # Returns 503 until caches are warm so Render keeps the old instance
    # serving traffic until the new one is fully ready.
    @app.get("/health")
    def health():
        resp = {"status": "ok" if _caches_ready else "warming", "version": "2.0.0"}
        commit = os.environ.get("RENDER_GIT_COMMIT", "")
        if commit:
            resp["commit"] = commit[:8]
        if not _caches_ready:
            return JSONResponse(resp, status_code=503)
        return resp

# --- Maintenance banner (in-memory, resets on deploy) ---
    @app.get("/api/v1/maintenance")
    def get_maintenance():
        if _maintenance_msg is None:
            return {"active": False}
        return {
            "active": True,
            "message": _maintenance_msg.get("message", ""),
            "shutdown_at": _maintenance_msg.get("shutdown_at"),
        }

    def _maint_authorized(request: Request, token: str | None = None) -> bool:
        """Check admin session or token param (admin password)."""
        if request.session.get("is_admin"):
            return True
        return token is not None and _ADMIN_PASSWORD and token == _ADMIN_PASSWORD

    @app.post("/api/v1/maintenance")
    def set_maintenance(request: Request, minutes: int = Form(5), token: str = Form(None),
                        message: str = Form(None)):
        if not _maint_authorized(request, token):
            return JSONResponse({"error": "Unauthorized"}, status_code=403)
        from datetime import datetime, timezone, timedelta
        global _maintenance_msg
        shutdown_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        _maintenance_msg = {
            "message": message or f"Scheduled maintenance in {minutes} minutes.",
            "shutdown_at": shutdown_at.isoformat(),
        }
        return {"ok": True, "shutdown_at": shutdown_at.isoformat()}

    @app.delete("/api/v1/maintenance")
    def clear_maintenance(request: Request,
                          x_admin_token: str | None = Header(None, alias="X-Admin-Token")):
        if not _maint_authorized(request, x_admin_token):
            return JSONResponse({"error": "Unauthorized"}, status_code=403)
        global _maintenance_msg
        _maintenance_msg = None
        return {"ok": True}

    return app


_maintenance_msg: dict | None = None

app = create_app()
