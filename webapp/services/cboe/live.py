"""Live single-ticker check — for on-demand refresh from the UI.

When a user searches an exact 1-4 letter ticker on /filings/symbols, this
fires one HTTP request to CBOE's symbol_status endpoint and upserts the
result before the page query runs. The displayed `last_checked` then
shows the actual fresh-as-of time, not whatever last bulk scan caught it.

Synchronous (uses requests) so it can be called directly from sync
FastAPI handlers without entangling with the running event loop.
"""
from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

import requests

from webapp.database import SessionLocal
from webapp.models import CboeStateChange, CboeSymbol
from webapp.services.cboe.scanner import (
    CBOE_ENDPOINT, USER_AGENT, _is_cloudflare_block, _state_name,
)

log = logging.getLogger(__name__)

LIVE_TIMEOUT = 8


_TICKER_RE = re.compile(r"^[A-Z]{1,4}$")


def is_ticker_query(q: str | None) -> bool:
    """True if `q` is exactly a 1-4 uppercase-letter ticker."""
    if not q:
        return False
    return bool(_TICKER_RE.fullmatch(q.strip().upper()))


def _load_cookie() -> str | None:
    val = os.environ.get("CBOE_SESSION_COOKIE", "")
    if val:
        return val
    project_root = Path(__file__).resolve().parents[3]
    env_file = project_root / "config" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("CBOE_SESSION_COOKIE="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def live_check(ticker: str, *, db_factory=SessionLocal) -> dict:
    """Fetch one ticker live from CBOE, upsert into cboe_symbols.

    Returns {ok, available, error, checked_at}. Never raises — auth/network
    failures return ok=False with an `error` string so the caller can
    silently fall back to the cached row.
    """
    ticker = (ticker or "").strip().upper()
    if not _TICKER_RE.fullmatch(ticker):
        return {"ok": False, "error": "ticker must be 1-4 uppercase letters"}

    cookie = _load_cookie()
    if not cookie:
        return {"ok": False, "error": "no CBOE_SESSION_COOKIE configured"}

    headers = {
        "User-Agent": USER_AGENT,
        "Cookie": cookie,
        "Accept": "application/json",
    }
    try:
        r = requests.get(
            CBOE_ENDPOINT,
            params={"symbol": ticker},
            headers=headers,
            timeout=LIVE_TIMEOUT,
            allow_redirects=False,
        )
    except requests.RequestException as e:
        log.warning("live_check(%s) network error: %s", ticker, e)
        return {"ok": False, "error": str(e)}

    if r.status_code in (302, 401, 403):
        if r.status_code == 403 and _is_cloudflare_block(r.text):
            return {
                "ok": False,
                "blocked": True,
                "error": "CBOE blocked at the Cloudflare edge — the VPS IP is "
                         "WAF-blocked; rotating CBOE_SESSION_COOKIE will not help.",
            }
        return {"ok": False, "error": f"auth (status {r.status_code}); refresh CBOE_SESSION_COOKIE"}
    if r.status_code != 200:
        return {"ok": False, "error": f"unexpected status {r.status_code}"}

    try:
        data = r.json()
    except ValueError:
        return {"ok": False, "error": "non-JSON response"}

    avail = data.get("available")
    if not isinstance(avail, bool):
        return {"ok": False, "error": "no boolean `available` in response"}

    now = datetime.utcnow()
    with db_factory() as db:
        row = db.get(CboeSymbol, ticker)
        if row is None:
            db.add(CboeSymbol(
                ticker=ticker,
                length=len(ticker),
                available=avail,
                last_checked_at=now,
                first_seen_available_at=now if avail else None,
                first_seen_taken_at=None if avail else now,
                state_change_count=0,
            ))
        else:
            old_state = _state_name(row.available)
            new_state = _state_name(avail)
            row.available = avail
            row.last_checked_at = now
            if avail and row.first_seen_available_at is None:
                row.first_seen_available_at = now
            if (not avail) and row.first_seen_taken_at is None:
                row.first_seen_taken_at = now
            if old_state != new_state and old_state != "unknown":
                row.state_change_count = (row.state_change_count or 0) + 1
                db.add(CboeStateChange(
                    ticker=ticker, old_state=old_state, new_state=new_state,
                    detected_at=now,
                ))
        db.commit()

    return {"ok": True, "available": avail, "checked_at": now}
