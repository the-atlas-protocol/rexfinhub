"""Pre-bake all REX reports as static HTML and upload to Render.

The VPS has all the data (Bloomberg, SEC pipeline, rex_products, etc.) and
can build every report HTML quickly. Render is a read-only website — any
SQL query / template render adds latency and DB load. So we build the HTML
once on the VPS and upload it as a static file. Render serves the file
directly via /admin/reports/preview, with no compute.

Reports built:
    daily_filing       — REX Daily ETP Report
    weekly_report      — REX Weekly ETP Report
    li_report          — REX ETP Leverage & Inverse Report
    income_report      — REX ETP Income Report
    flow_report        — REX ETP Flow Report
    autocall_report    — Autocallable ETF Weekly Update
    intelligence_brief — Filing Intelligence Brief
    filing_screener    — T-REX Filing Candidates
    product_status     — REX Product Pipeline

Usage:
    # Build + upload all reports
    python scripts/prebake_reports.py

    # Build only — don't upload (test locally)
    python scripts/prebake_reports.py --no-upload

    # Build a specific report
    python scripts/prebake_reports.py --only intelligence_brief

    # Override the Render URL
    python scripts/prebake_reports.py --render-url https://rexfinhub.com
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

log = logging.getLogger("prebake")

DEFAULT_RENDER_URL = "https://rex-etp-tracker.onrender.com"
LOCAL_BAKE_DIR = PROJECT_ROOT / "data" / "prebaked_reports"
WEEKLY_THESES_DIR = PROJECT_ROOT / "data" / "weekly_theses"


def _load_latest_weekly_theses() -> dict:
    """Return the most recent <YYYY-MM-DD>.json under data/weekly_theses/.

    Returns an empty dict if the directory or any file is missing/invalid.
    Manual override files (suffix `_manual.json`) are NOT used here — the
    generator script already merges them into the cache before save.

    Builders that want theses can pull this dict via the `WEEKLY_THESES`
    module attribute (set in `main()`).
    """
    if not WEEKLY_THESES_DIR.exists():
        return {}
    candidates = sorted(
        p for p in WEEKLY_THESES_DIR.glob("*.json")
        if not p.stem.endswith("_manual")
    )
    if not candidates:
        return {}
    latest = candidates[-1]
    try:
        payload = json.loads(latest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("weekly_theses cache at %s is invalid: %s", latest, exc)
        return {}
    if not isinstance(payload, dict) or "theses" not in payload:
        return {}
    log.info(
        "Loaded weekly theses: %s (%d ticker(s), week_of=%s)",
        latest.name, len(payload.get("theses") or {}), payload.get("week_of"),
    )
    return payload


# Populated in main() so builders can `from scripts.prebake_reports import WEEKLY_THESES`.
WEEKLY_THESES: dict = {}


def _load_api_key() -> str:
    """Read API_KEY from config/.env or environment.

    Matches the convention used by scripts/run_daily.py for Render uploads.
    """
    env_val = os.environ.get("API_KEY", "")
    if env_val:
        return env_val
    env_file = PROJECT_ROOT / "config" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def _build_daily_filing(db) -> str:
    from etp_tracker.email_alerts import build_digest_html_from_db
    return build_digest_html_from_db(db, DEFAULT_RENDER_URL, edition="daily")


def _build_weekly(db) -> str:
    from etp_tracker.weekly_digest import build_weekly_digest_html
    return build_weekly_digest_html(db, DEFAULT_RENDER_URL)


def _build_li(db) -> str:
    from webapp.services.report_emails import build_li_email
    html, _ = build_li_email(DEFAULT_RENDER_URL, db)
    return html


def _build_income(db) -> str:
    from webapp.services.report_emails import build_cc_email
    html, _ = build_cc_email(DEFAULT_RENDER_URL, db)
    return html


def _build_flow(db) -> str:
    from webapp.services.report_emails import build_flow_email
    html, _ = build_flow_email(DEFAULT_RENDER_URL, db)
    return html


def _build_autocall(db) -> str:
    from webapp.services.report_emails import build_autocall_email
    html, _ = build_autocall_email(DEFAULT_RENDER_URL, db)
    return html


def _build_intelligence(db) -> str:
    from etp_tracker.intelligence_brief import build_intelligence_brief
    return build_intelligence_brief(db, lookback_days=1)


def _build_screener(db) -> str:
    from screener.filing_screener_report import build_filing_screener_report
    return build_filing_screener_report(max_picks=5)


def _build_product_status(db) -> str:
    from etp_tracker.product_status_report import build_product_status_report
    return build_product_status_report(db)


# Report key -> builder function. ORDER MATTERS for logging/progress.
BUILDERS = {
    "daily_filing":       _build_daily_filing,
    "weekly_report":      _build_weekly,
    "li_report":          _build_li,
    "income_report":      _build_income,
    "flow_report":        _build_flow,
    "autocall_report":    _build_autocall,
    "intelligence_brief": _build_intelligence,
    "filing_screener":    _build_screener,
    "product_status":     _build_product_status,
}


def _bake_one(report_key: str, builder, db) -> tuple[bytes | None, str | None]:
    """Run a single builder. Returns (html_bytes, error_message)."""
    t0 = time.time()
    try:
        html = builder(db)
        if not html or len(html) < 100:
            return None, f"builder returned suspiciously small output ({len(html or '')} chars)"
        elapsed = time.time() - t0
        log.info("  %s: built (%d chars, %.1fs)", report_key, len(html), elapsed)
        return html.encode("utf-8"), None
    except Exception as e:
        log.error("  %s: FAILED — %s", report_key, e)
        log.debug(traceback.format_exc())
        return None, str(e)


def _save_local(report_key: str, html_bytes: bytes) -> Path:
    """Write the baked HTML and meta.json sidecar to the local prebaked_reports/ dir."""
    import json as _json
    LOCAL_BAKE_DIR.mkdir(parents=True, exist_ok=True)
    out = LOCAL_BAKE_DIR / f"{report_key}.html"
    out.write_bytes(html_bytes)
    meta = {
        "baked_at": datetime.now().isoformat(),
        "size_bytes": len(html_bytes),
        "report_key": report_key,
    }
    meta_path = LOCAL_BAKE_DIR / f"{report_key}.meta.json"
    meta_path.write_text(_json.dumps(meta, indent=2), encoding="utf-8")
    return out


def _upload(report_key: str, html_bytes: bytes, render_url: str, api_key: str) -> tuple[bool, str]:
    """POST the HTML to Render's /api/v1/reports/upload/{key}."""
    import requests

    if not api_key:
        return False, "no API key set"

    url = f"{render_url}/api/v1/reports/upload/{report_key}"
    headers = {"X-API-Key": api_key}
    files = {"file": (f"{report_key}.html", io.BytesIO(html_bytes), "text/html")}

    try:
        resp = requests.post(url, files=files, headers=headers, timeout=120)
    except Exception as e:
        return False, f"request error: {e}"

    if resp.status_code != 200:
        return False, f"HTTP {resp.status_code}: {resp.text[:200]}"

    try:
        data = resp.json()
        return True, data.get("status", "ok")
    except Exception:
        return True, "ok"


def main():
    parser = argparse.ArgumentParser(description="Pre-bake REX reports and upload to Render")
    parser.add_argument("--no-upload", action="store_true", help="Skip the upload step")
    parser.add_argument("--only", help="Bake only this report key")
    parser.add_argument("--render-url", default=DEFAULT_RENDER_URL, help="Render base URL")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    from webapp.database import init_db, SessionLocal

    # Load weekly LLM theses (B3) once, expose to builders that opt in
    # via `from scripts.prebake_reports import WEEKLY_THESES`.
    global WEEKLY_THESES
    WEEKLY_THESES = _load_latest_weekly_theses()

    init_db()
    db = SessionLocal()

    # Filter to single report if --only
    targets = BUILDERS
    if args.only:
        if args.only not in BUILDERS:
            log.error("Unknown report key: %s. Valid: %s", args.only, list(BUILDERS.keys()))
            sys.exit(1)
        targets = {args.only: BUILDERS[args.only]}

    api_key = _load_api_key() if not args.no_upload else ""

    log.info("Pre-baking %d report(s)", len(targets))
    log.info("Local bake dir: %s", LOCAL_BAKE_DIR)
    if not args.no_upload:
        log.info("Render URL: %s", args.render_url)
        log.info("API key: %s", "set" if api_key else "MISSING — uploads will be skipped")
    else:
        log.info("Upload disabled (--no-upload)")
    log.info("")

    results = {"baked": 0, "uploaded": 0, "failed": [], "skipped_upload": []}

    try:
        for key, builder in targets.items():
            html_bytes, err = _bake_one(key, builder, db)
            if err or not html_bytes:
                results["failed"].append({"key": key, "error": err})
                continue

            _save_local(key, html_bytes)
            results["baked"] += 1

            if args.no_upload:
                continue

            if not api_key:
                results["skipped_upload"].append(key)
                continue

            ok, msg = _upload(key, html_bytes, args.render_url, api_key)
            if ok:
                results["uploaded"] += 1
                log.info("  %s: uploaded", key)
            else:
                log.error("  %s: upload failed — %s", key, msg)
                results["failed"].append({"key": key, "error": f"upload: {msg}"})
    finally:
        db.close()

    log.info("")
    log.info("Summary:")
    log.info("  Baked: %d", results["baked"])
    log.info("  Uploaded: %d", results["uploaded"])
    if results["skipped_upload"]:
        log.info("  Skipped upload (no key): %s", ", ".join(results["skipped_upload"]))
    if results["failed"]:
        log.info("  Failed: %d", len(results["failed"]))
        for f in results["failed"]:
            log.info("    - %s: %s", f["key"], f["error"])

    # Exit code: 0 if anything baked successfully, 1 if everything failed
    if results["baked"] == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
