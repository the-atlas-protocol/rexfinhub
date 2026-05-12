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

from datetime import datetime

from fastapi import FastAPI, Form, Header, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse as StarletteRedirect

from webapp.auth import SESSION_SECRET
from webapp.database import init_db
from webapp.templates_init import build_templates
from webapp.services.csrf import (
    CSRF_FORM_FIELD,
    CSRF_HEADER,
    get_or_create_token,
    is_valid as _csrf_is_valid,
)

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
            # Don't crash the entire site if env var missing — log loudly
            # and use a random unguessable secret so the site is locked
            # but reachable. Operator must set SITE_PASSWORD in Render dashboard.
            import logging, secrets
            logging.getLogger(__name__).error(
                "SITE_PASSWORD missing on Render. Site is locked with random secret. "
                "Set SITE_PASSWORD in Render dashboard to restore access."
            )
            return secrets.token_urlsafe(32)
        # Local dev fallback
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
_PUBLIC_PREFIXES = ("/login", "/static/", "/health", "/api/v1/", "/favicon", "/robots.txt", "/sitemap.xml")


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


# Paths that require CSRF protection on state-mutating verbs.
# Kept tight on purpose: only browser-driven admin surfaces. The X-API-Key
# routes under /api/v1/* are machine-to-machine and use bearer auth.
_CSRF_PROTECTED_PREFIXES = ("/admin/", "/api/v1/maintenance")
# /admin/login posts the password itself before any session exists, so the
# token cannot have been issued yet — exempt to avoid a chicken/egg loop.
#
# /api/v1/uploads/screener-cache is a machine-to-machine bearer-token
# endpoint (see webapp/routers/api.py) used by the daily VPS pipeline.
# It is not under any current _CSRF_PROTECTED_PREFIXES entry, but is listed
# here defensively so widening the prefix list later doesn't silently
# break the daily upload.
_CSRF_EXEMPT_PATHS = {"/admin/login", "/api/v1/uploads/screener-cache"}
_CSRF_PROTECTED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class CsrfMiddleware(BaseHTTPMiddleware):
    """Validate a session-tied CSRF token on admin state-mutating requests.

    Token is generated lazily by ``get_or_create_token`` (called via the
    Jinja global ``csrf_token``) and stored in the session. Submissions
    must echo it back via the ``_csrf_token`` form field or
    ``X-CSRF-Token`` header.

    Failure mode is a 403 JSON response so the missing-token cause is
    visible in the browser network tab; the legitimate path is to refresh
    the page and resubmit, which re-issues the token.
    """

    async def dispatch(self, request, call_next):
        method = request.method.upper()
        path = request.url.path
        # Always populate request.state.csrf_token so base.html can render
        # the meta tag without each router needing to register a Jinja
        # global. (Many routers spin up their own Jinja2Templates instance
        # with separate environment, so a global registered in main.py is
        # not visible to all of them.)
        try:
            request.state.csrf_token = get_or_create_token(request.session)
        except Exception:
            request.state.csrf_token = ""

        if (
            method in _CSRF_PROTECTED_METHODS
            and path not in _CSRF_EXEMPT_PATHS
            and any(path.startswith(p) for p in _CSRF_PROTECTED_PREFIXES)
        ):
            # Pull the presented token from header, form, or query.
            presented = request.headers.get(CSRF_HEADER)
            ctype = (request.headers.get("content-type") or "").lower()

            # Hotfix H1 (2026-05-11): for multipart bodies, REQUIRE the
            # X-CSRF-Token header. Calling ``await request.form()`` would
            # spool the entire upload to disk before we can reject the
            # request — a free DoS vector against /admin/upload/*. Browsers
            # that submit our admin forms always set the header via
            # base.html's fetch wrapper, so legitimate flows still work;
            # this only kills naive curl-an-empty-token POSTs and the DoS.
            if not presented and "multipart/" in ctype:
                from fastapi.responses import JSONResponse as _JR
                return _JR(
                    {"error": "CSRF validation failed", "detail":
                     "Multipart admin requests must send X-CSRF-Token header."},
                    status_code=403,
                )

            if not presented:
                # urlencoded forms are small and bounded by Starlette's
                # default body limits, so consuming + caching them is safe.
                if "application/x-www-form-urlencoded" in ctype:
                    try:
                        form = await request.form()
                        presented = form.get(CSRF_FORM_FIELD)
                    except Exception:
                        presented = None
            if not presented:
                presented = request.query_params.get(CSRF_FORM_FIELD)

            if not _csrf_is_valid(request.session, presented):
                from fastapi.responses import JSONResponse as _JR
                return _JR(
                    {"error": "CSRF validation failed", "detail":
                     "Missing or invalid _csrf_token. Refresh the page and retry."},
                    status_code=403,
                )

        return await call_next(request)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

templates = build_templates(WEBAPP_DIR / "templates")

# Expose feature flags to all templates (used by base.html for conditional nav)
templates.env.globals["enable_13f"] = bool(os.environ.get("ENABLE_13F"))

# Canonical URL registry — templates can call {{ url('funds.detail', ticker='NVDX') }}
from webapp.routes import url as _route_url
templates.env.globals["url"] = _route_url


def _csrf_token_for(request: Request) -> str:
    """Jinja global: ``{{ csrf_token(request) }}`` -> session-tied token.

    Lazily issued on first call so unauthenticated/anonymous pages don't
    pay the cost. base.html embeds it in a <meta> tag and a small JS
    helper auto-attaches it to admin form submits + fetch calls.
    """
    try:
        return get_or_create_token(request.session)
    except Exception:
        return ""


templates.env.globals["csrf_token"] = _csrf_token_for



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
    # SiteAuthMiddleware + CsrfMiddleware both need session -> add them first
    # (inner), then SessionMiddleware (outer, decodes session before either
    # check). CsrfMiddleware is registered before SiteAuthMiddleware so that
    # CSRF rejection happens before any auth redirect noise — a
    # missing-token POST should fail with 403, not bounce through /login.
    app.add_middleware(SiteAuthMiddleware)
    app.add_middleware(CsrfMiddleware)
    app.add_middleware(DataFreshnessMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, max_age=28800, same_site="lax", https_only=bool(os.environ.get("RENDER")))

    # Static files (CSS, JS)
    static_dir = WEBAPP_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # --- robots.txt + sitemap.xml (public, registered before SiteAuth blocks) ---

    @app.get("/robots.txt", include_in_schema=False)
    def robots_txt():
        return FileResponse(WEBAPP_DIR / "static" / "robots.txt", media_type="text/plain")

    @app.get("/sitemap.xml", include_in_schema=False)
    def sitemap_xml():
        """Generate sitemap from public, indexable routes."""
        from webapp.routes import ROUTES
        base = "https://rexfinhub.com"
        today = datetime.utcnow().strftime("%Y-%m-%d")
        # Only public list/dashboard pages — no detail surfaces (too many) and no admin/api
        PUBLIC = [
            "home", "data",
            "operations.products", "operations.pipeline", "operations.calendar",
            "market.rex", "market.category", "market.issuer", "market.underlier", "market.stocks",
            "sec.etp.dashboard", "sec.etp.filings", "sec.etp.leverageandinverse",
            "sec.notes.dashboard", "sec.notes.filings",
            "tools.compare.etps", "tools.li.candidates", "tools.simulators.autocall",
            "tools.tickers", "tools.calendar",
            "funds.index", "issuers.index", "trusts.index",
        ]
        urls_xml = "\n".join(
            f'  <url><loc>{base}{ROUTES[name]}</loc><lastmod>{today}</lastmod></url>'
            for name in PUBLIC if name in ROUTES
        )
        body = f'''<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
{urls_xml}
</urlset>'''
        return Response(content=body, media_type="application/xml")

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

    # Fix 3: /calendar/ top-level pillar — delegates to the market_advanced calendar_view
    # /pipeline/ (old) retains its own calendar unchanged; /market/calendar also kept
    from webapp.routers.calendar_router import router as calendar_router
    app.include_router(calendar_router)
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

    # REX Reserved Symbols (admin-editable curated list of REX's own ticker reservations).
    # Distinct from /tools/tickers (full CBOE universe scan).
    from webapp.routers import operations_reserved
    app.include_router(operations_reserved.router)

    # L&I Strategy Engine — whitespace candidates, filing race, ticker deep-dive
    from webapp.routers import strategy
    app.include_router(strategy.router)

    # ====================================================================
    # v3 URL migration (Phase 1, dual-route): new section-prefix routes.
    # Old URLs (e.g. /filings/dashboard) 301-redirect to the new canonical
    # destinations registered below. See docs/website_FINAL_PLAN_2026-05-08.md
    # ====================================================================
    from webapp.routers import (
        operations,
        sec_etp,
        sec_notes,
        sec_13f,
        tools_compare,
        tools_li,
        tools_simulators,
        tools_tickers,
        tools_calendar,
        filings_detail,
    )
    app.include_router(operations.router)
    app.include_router(sec_etp.router)
    app.include_router(sec_notes.router)
    app.include_router(sec_13f.router)
    app.include_router(tools_compare.router)
    app.include_router(tools_li.router)
    app.include_router(tools_simulators.router)
    app.include_router(tools_tickers.router)
    app.include_router(tools_calendar.router)
    app.include_router(filings_detail.router)
    from webapp.routers import issuers
    app.include_router(issuers.router)
    from webapp.routers import stocks
    app.include_router(stocks.router)
    # rexops-O6: pipeline -> underlier race view (REX vs competitor by
    # lifecycle stage). Separate router so it ships independently of the
    # main pipeline_calendar module.
    from webapp.routers import underlier_view
    app.include_router(underlier_view.router)

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
