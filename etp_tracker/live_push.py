"""Live feed push: VPS -> Render /api/v1/live/push.

The atom watcher detects filings in ~60 seconds. The single_filing_worker
enriches them in another ~30-60 seconds. Instead of waiting for the next
daily DB upload (which triggers a ~4-minute Render restart), this module
pushes each new alert straight to Render's lightweight /api/v1/live/push
endpoint as a single-row insert.

One function, one request, non-blocking. Failures are logged and swallowed
so the main watcher + worker loops never stall on push errors.

Environment:
    RENDER_API_URL   default https://rex-etp-tracker.onrender.com
    API_KEY          loaded from config/.env, same key used by run_daily
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger(__name__)

_CONFIG_CACHE: dict[str, str] | None = None


def _config() -> dict[str, str]:
    global _CONFIG_CACHE
    if _CONFIG_CACHE is not None:
        return _CONFIG_CACHE

    url = os.environ.get("RENDER_API_URL", "https://rex-etp-tracker.onrender.com")
    api_key = os.environ.get("API_KEY", "")

    if not api_key:
        env_file = Path(__file__).resolve().parent.parent / "config" / ".env"
        if env_file.exists():
            for line in env_file.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("API_KEY="):
                    api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break

    _CONFIG_CACHE = {"url": url.rstrip("/"), "api_key": api_key}
    return _CONFIG_CACHE


def push_alert(
    accession_number: str,
    form: str,
    *,
    cik: str | None = None,
    company_name: str | None = None,
    trust_id: int | None = None,
    trust_slug: str | None = None,
    trust_name: str | None = None,
    filed_date: Any = None,  # date | str | None
    primary_doc_url: str | None = None,
    source: str | None = None,
    timeout: float = 5.0,
) -> bool:
    """Push one filing to the Render live feed.

    Returns True on success, False on any failure (never raises). Caller
    should not gate its main flow on the return value — this is fire-and-
    forget by design.
    """
    cfg = _config()
    if not cfg["api_key"]:
        log.debug("live push: no API key, skipping")
        return False

    payload: dict[str, Any] = {
        "accession_number": accession_number,
        "form": form,
    }
    if cik:
        payload["cik"] = cik
    if company_name:
        payload["company_name"] = company_name
    if trust_id is not None:
        payload["trust_id"] = int(trust_id)
    if trust_slug:
        payload["trust_slug"] = trust_slug
    if trust_name:
        payload["trust_name"] = trust_name
    if filed_date:
        payload["filed_date"] = (
            filed_date.isoformat() if hasattr(filed_date, "isoformat") else str(filed_date)
        )
    if primary_doc_url:
        payload["primary_doc_url"] = primary_doc_url
    if source:
        payload["source"] = source

    try:
        resp = requests.post(
            f"{cfg['url']}/api/v1/live/push",
            json=payload,
            headers={"X-API-Key": cfg["api_key"]},
            timeout=timeout,
        )
        if resp.status_code == 200:
            return True
        log.warning(
            "live push %s -> %d: %s",
            accession_number, resp.status_code, resp.text[:200],
        )
        return False
    except requests.RequestException as exc:
        log.warning("live push %s failed: %s", accession_number, exc)
        return False


def push_alerts_batch(alerts: list[dict]) -> tuple[int, int]:
    """Push multiple alerts sequentially. Returns (ok_count, fail_count).

    Each dict should have the same keys as push_alert kwargs.
    """
    ok = 0
    fail = 0
    for a in alerts:
        if push_alert(**a):
            ok += 1
        else:
            fail += 1
    return ok, fail
