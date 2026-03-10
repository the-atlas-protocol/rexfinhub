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

from fastapi import FastAPI, Form, Request
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
    """Load SITE_PASSWORD from config/.env or environment."""
    env_file = PROJECT_ROOT / "config" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, val = line.split("=", 1)
                if key.strip() == "SITE_PASSWORD":
                    return val.strip().strip('"').strip("'")
    return os.environ.get("SITE_PASSWORD", "123")


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


def _prewarm_caches() -> None:
    """Pre-warm all data caches in a background thread at startup.

    Loads market DataFrames + screener results so the first visitor
    never hits a cold cache.
    """
    import threading

    def _warm_all():
        from webapp.database import SessionLocal
        db = SessionLocal()
        try:
            # Market data: load master + time series DataFrames into memory
            try:
                from webapp.services import market_data
                if market_data.data_available(db):
                    market_data._load_master(db)
                    market_data._load_ts(db)
                    log.info("Market data cache warmed at startup.")
            except Exception as e:
                log.warning("Market cache warm failed (non-fatal): %s", e)

            # Screener cache
            try:
                if os.environ.get("RENDER"):
                    from webapp.services.screener_3x_cache import warm_cache
                    warm_cache(db=db)
                else:
                    from webapp.services.screener_3x_cache import warm_cache
                    warm_cache()
                log.info("Screener cache warmed at startup.")
            except Exception as e:
                log.warning("Screener cache warm failed (non-fatal): %s", e)
        finally:
            db.close()

    t = threading.Thread(target=_warm_all, name="cache-warm", daemon=True)
    t.start()
    log.info("Background cache warm-up started.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize database, pre-warm all caches. Shutdown: cleanup."""
    init_db()
    log.info("Database initialized.")
    _prewarm_caches()
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="REX FinHub",
        version="2.0.0",
        lifespan=lifespan,
    )

    # Middleware order matters: last added = outermost (runs first).
    # SiteAuthMiddleware needs session -> add it first (inner),
    # then SessionMiddleware (outer, decodes session before auth check).
    app.add_middleware(SiteAuthMiddleware)
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

    # --- Home page ---
    @app.get("/")
    def home_page(request: Request):
        return templates.TemplateResponse("home.html", {"request": request})

    # --- Routers ---
    from webapp.routers import auth_routes, dashboard, trusts, funds, search, analysis, digest, downloads, api, admin, screener, market, filings, universe, holdings, global_search, analytics, reports
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
    app.include_router(screener.router)
    app.include_router(market.router)
    app.include_router(market_advanced_router)
    app.include_router(filings.router, prefix="/filings")
    app.include_router(universe.router)
    app.include_router(holdings.router)
    app.include_router(global_search.router)
    app.include_router(analytics.router)
    app.include_router(reports.router)

    # Health check
    @app.get("/health")
    def health():
        resp = {"status": "ok", "version": "2.0.0"}
        commit = os.environ.get("RENDER_GIT_COMMIT", "")
        if commit:
            resp["commit"] = commit[:8]
        return resp

    # --- Maintenance banner (in-memory, resets on deploy) ---
    @app.get("/api/v1/maintenance")
    def get_maintenance():
        return {"active": _maintenance_msg is not None,
                "message": _maintenance_msg["message"] if _maintenance_msg else ""}

    @app.post("/api/v1/maintenance")
    def set_maintenance(request: Request, message: str = Form(...)):
        if not request.session.get("is_admin"):
            return JSONResponse({"error": "Unauthorized"}, status_code=403)
        global _maintenance_msg
        _maintenance_msg = {"message": message}
        return {"ok": True}

    @app.delete("/api/v1/maintenance")
    def clear_maintenance(request: Request):
        if not request.session.get("is_admin"):
            return JSONResponse({"error": "Unauthorized"}, status_code=403)
        global _maintenance_msg
        _maintenance_msg = None
        return {"ok": True}

    return app


_maintenance_msg: dict | None = None

app = create_app()
