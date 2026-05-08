"""SEC 13F Intelligence — placeholder routes per IA spec.

All 4 sub-pages serve a 'Coming Soon' placeholder. Nav items
are non-clickable per Ryu's spec; these URLs exist so future
builds don't break inbound links.
"""
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/sec/13f", tags=["sec-13f"])


def _coming_soon(title: str):
    def handler(request: Request):
        return HTMLResponse(f"""
        <!DOCTYPE html>
        <html><head><title>{title} — Coming Soon</title>
        <link rel="stylesheet" href="/static/css/style.css"></head>
        <body><div class="container" style="padding: 4rem; text-align: center;">
        <h1>{title}</h1>
        <p style="opacity: 0.7;">Coming Soon. The 13F pillar is being rebuilt.</p>
        <p><a href="/">← Back to home</a></p>
        </div></body></html>
        """)
    return handler


router.add_api_route("/rex-report", _coming_soon("REX Quarter Report"), methods=["GET"])
router.add_api_route("/market-report", _coming_soon("13F Market Report"), methods=["GET"])
router.add_api_route("/institutions", _coming_soon("Institution Explorer"), methods=["GET"])
router.add_api_route("/country", _coming_soon("Country Intel"), methods=["GET"])
