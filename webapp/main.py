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

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from webapp.auth import SESSION_SECRET
from webapp.database import init_db

log = logging.getLogger(__name__)
WEBAPP_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: initialize database. Shutdown: cleanup."""
    init_db()
    log.info("Database initialized.")
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="ETP Filing Tracker",
        version="2.0.0",
        lifespan=lifespan,
    )

    # Session middleware (required for Azure AD SSO)
    app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET)

    # Static files (CSS, JS)
    static_dir = WEBAPP_DIR / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Routers
    from webapp.routers import auth_routes, dashboard, trusts, funds, filings, search, analysis, digest, downloads, api, admin
    app.include_router(auth_routes.router)
    app.include_router(dashboard.router)
    app.include_router(trusts.router, prefix="/trusts")
    app.include_router(funds.router, prefix="/funds")
    app.include_router(filings.router, prefix="/filings")
    app.include_router(search.router)
    app.include_router(analysis.router)
    app.include_router(digest.router)
    app.include_router(downloads.router)
    app.include_router(api.router)
    app.include_router(admin.router)

    # Health check
    @app.get("/health")
    def health():
        resp = {"status": "ok", "version": "2.0.0"}
        commit = os.environ.get("RENDER_GIT_COMMIT", "")
        if commit:
            resp["commit"] = commit[:8]
        return resp

    return app


app = create_app()
