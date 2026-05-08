"""Compare tools — Phase 1 of the v3 URL migration.

Compare ETPs is the live one (imported from market_advanced).
The other 4 (filings, notes, 13f-inst, 13f-products) are 'Coming Soon'
stubs for now — stable URLs from day one means future builds don't
break inbound links.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from webapp.routers.market_advanced import _fund_compare_impl

router = APIRouter(prefix="/tools/compare", tags=["tools-compare"])
templates = Jinja2Templates(directory="webapp/templates")

router.add_api_route("/etps", _fund_compare_impl, methods=["GET"])


def _coming_soon(title: str):
    def handler(request: Request):
        # Use existing _fragment_base or base.html with a simple coming-soon panel
        return HTMLResponse(f"""
        <!DOCTYPE html>
        <html><head><title>{title} — Coming Soon</title>
        <link rel="stylesheet" href="/static/css/style.css"></head>
        <body><div class="container" style="padding: 4rem; text-align: center;">
        <h1>{title}</h1>
        <p style="opacity: 0.7;">Coming Soon. This URL is reserved for the future {title} tool.</p>
        <p><a href="/">← Back to home</a></p>
        </div></body></html>
        """)
    return handler


router.add_api_route("/filings", _coming_soon("Compare Filings"), methods=["GET"])
router.add_api_route("/notes", _coming_soon("Compare Notes"), methods=["GET"])
router.add_api_route("/13f-inst", _coming_soon("Compare 13F Institutions"), methods=["GET"])
router.add_api_route("/13f-products", _coming_soon("Compare 13F Products"), methods=["GET"])
