"""Tier 4: Weekly bulk trust discovery from SEC company.idx.

Tiers 1-3 detect NEW filings from already-known trusts. Tier 4 discovers
NEW FILERS by pulling the SEC's weekly full-index company.idx, filtering
for recent prospectus filings, and stubbing unknown CIKs as inactive
Trust rows with source='bulk_discovery'. Admins then review them in the
existing Trust Requests admin page.

ETag-based caching avoids redundant downloads: company.idx updates daily
but we only want weekly discovery. Persistent ETag stored in
cache/sec/bulk_sync_etag.txt; on HTTP 304 we skip parsing entirely.

Run as systemd oneshot via rexfinhub-bulk-sync.timer (Sundays 03:00 ET).

CLI:
    /home/jarvis/venv/bin/python -m etp_tracker.bulk_sync
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

import requests
from sqlalchemy.orm import Session

from .config import USER_AGENT_DEFAULT

log = logging.getLogger(__name__)

# Prospectus forms where genuinely new filers first appear.
# Excludes 497 variants (supplements) and 485B (short forms) — new CIKs
# always show up via 485APOS/485BPOS/N-1A/S-1 first.
ACCEPTED_FORMS = {
    "485APOS", "485BPOS", "485BXT",
    "N-1A", "N-1A/A",
    "N-2", "N-2/A",
    "S-1", "S-1/A",
}

_CACHE_DIR = Path(
    os.environ.get(
        "SEC_CACHE_DIR",
        str(Path(__file__).resolve().parent.parent / "cache" / "sec"),
    )
)
_ETAG_FILE = _CACHE_DIR / "bulk_sync_etag.txt"
_BODY_CACHE_FILE = _CACHE_DIR / "bulk_sync_company.idx"


@dataclass
class BulkSyncResult:
    rows_parsed: int = 0
    rows_after_filter: int = 0
    new_ciks: int = 0
    stubbed_trusts: int = 0
    not_modified: bool = False

    def as_dict(self) -> dict:
        return {
            "rows_parsed": self.rows_parsed,
            "rows_after_filter": self.rows_after_filter,
            "new_ciks": self.new_ciks,
            "stubbed_trusts": self.stubbed_trusts,
            "not_modified": self.not_modified,
        }


def _slugify(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", (name or "").lower())
    s = re.sub(r"\s+", "-", s).strip("-")
    return s[:100] or "trust"


def _current_quarter_url(today: date | None = None) -> str:
    """Return the SEC company.idx URL for the quarter containing `today`.

    Handles quarter rollover: on Jan 1, we ask for YYYY/QTR1 of the new year.
    """
    d = today or date.today()
    quarter = (d.month - 1) // 3 + 1
    return (
        f"https://www.sec.gov/Archives/edgar/full-index/{d.year}/QTR{quarter}/company.idx"
    )


def _load_etag() -> str | None:
    try:
        if _ETAG_FILE.exists():
            v = _ETAG_FILE.read_text(encoding="utf-8").strip()
            return v or None
    except Exception:
        pass
    return None


def _save_etag(etag: str) -> None:
    try:
        _ETAG_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = _ETAG_FILE.with_suffix(".tmp")
        tmp.write_text(etag, encoding="utf-8")
        tmp.replace(_ETAG_FILE)
    except Exception as e:
        log.warning("Could not save ETag: %s", e)


def _fetch_company_idx(url: str, user_agent: str) -> tuple[str | None, str | None]:
    """Fetch company.idx using If-None-Match. Returns (body, etag).

    Returns (None, None) on 304 Not Modified.
    """
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
    prior_etag = _load_etag()
    if prior_etag:
        headers["If-None-Match"] = prior_etag

    # Respect SEC rate limit.
    time.sleep(0.35)
    r = requests.get(url, headers=headers, timeout=(30.0, 300.0))
    if r.status_code == 304:
        log.info("company.idx unchanged since last sync (304)")
        return None, None
    r.raise_for_status()
    new_etag = r.headers.get("ETag")
    return r.text, new_etag


# Anchored tail pattern: CIK (digits) + YYYY-MM-DD + filename path. This is
# robust against form type widths exceeding the header column width (SEC
# data rows are not strict fixed-width — long forms like "SCHEDULE 13G/A"
# overflow the header's "Form Type" column).
_ROW_TAIL_RE = re.compile(
    r"^(?P<name>.+?)\s{2,}"
    r"(?P<form>\S[\S ]*?)\s{2,}"
    r"(?P<cik>\d{1,10})\s{2,}"
    r"(?P<date>\d{4}-\d{2}-\d{2})\s{2,}"
    r"(?P<file>\S.*)$"
)


def _parse_company_idx(body: str) -> list[dict]:
    """Parse SEC company.idx into list of {name, form, cik, date_filed}.

    company.idx has a header with column labels, a dashed separator, then
    data rows. Data rows are whitespace-separated with at least 2 spaces
    between fields but field widths don't strictly match header columns
    (long forms like "SCHEDULE 13G/A" overflow). We anchor on the CIK+date
    pair at the row tail via regex, which is unambiguous.
    """
    lines = body.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("Company Name") and "Form Type" in line and "CIK" in line:
            header_idx = i
            break
    if header_idx is None:
        log.error("company.idx header row not found")
        return []

    # Data starts after the dashed separator line(s).
    data_start = header_idx + 1
    while data_start < len(lines) and (
        not lines[data_start].strip() or lines[data_start].startswith("-")
    ):
        data_start += 1

    rows: list[dict] = []
    for line in lines[data_start:]:
        if not line.strip():
            continue
        m = _ROW_TAIL_RE.match(line.rstrip())
        if not m:
            continue
        try:
            filed = datetime.strptime(m.group("date"), "%Y-%m-%d").date()
        except ValueError:
            continue
        rows.append({
            "name": m.group("name").strip(),
            "form": m.group("form").strip(),
            "cik": str(int(m.group("cik"))),  # normalize, drop leading zeros
            "date_filed": filed,
        })
    return rows


def _latest_per_cik(rows: list[dict]) -> dict[str, dict]:
    """Group filtered rows by CIK, keep the most recent filing per CIK."""
    latest: dict[str, dict] = {}
    for r in rows:
        cik = r["cik"]
        prev = latest.get(cik)
        if prev is None or r["date_filed"] > prev["date_filed"]:
            latest[cik] = r
    return latest


def _unique_slug(db: Session, base: str) -> str:
    """Return a slug unique within the trusts table."""
    # Local import to avoid circular (webapp imports etp_tracker in some paths).
    from webapp.models import Trust

    slug = base
    counter = 2
    while db.query(Trust.id).filter(Trust.slug == slug).first() is not None:
        suffix = f"-{counter}"
        slug = (base[: 100 - len(suffix)] + suffix)
        counter += 1
        if counter > 999:
            break
    return slug


def sync_once(db: Session, lookback_days: int = 90) -> BulkSyncResult:
    """One-shot bulk sync. Parse SEC company.idx, stub unknown CIKs as
    inactive Trust rows. Does NOT commit if caller passes a session they
    intend to roll back — the caller is responsible for db.commit().
    This function flushes per batch and commits at the end.

    Returns counts for logging/metrics.
    """
    from webapp.models import Trust

    result = BulkSyncResult()
    url = _current_quarter_url()
    log.info("Fetching %s", url)
    try:
        body, new_etag = _fetch_company_idx(url, USER_AGENT_DEFAULT)
    except requests.HTTPError as e:
        # Quarter rollover: if current quarter file doesn't exist yet (first
        # few days of a new quarter), fall back to previous quarter.
        if e.response is not None and e.response.status_code == 404:
            log.warning("Current quarter not available (404); falling back to prior quarter")
            today = date.today()
            first_of_month = today.replace(day=1)
            prior = first_of_month - timedelta(days=1)
            url = _current_quarter_url(prior)
            log.info("Fetching %s", url)
            body, new_etag = _fetch_company_idx(url, USER_AGENT_DEFAULT)
        else:
            raise

    if body is None:
        result.not_modified = True
        return result

    # Cache the body for debugging/inspection (best-effort).
    try:
        _BODY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _BODY_CACHE_FILE.write_text(body, encoding="utf-8", errors="ignore")
    except Exception:
        pass

    rows = _parse_company_idx(body)
    result.rows_parsed = len(rows)
    log.info("Parsed %d rows from company.idx", len(rows))

    cutoff = date.today() - timedelta(days=lookback_days)
    filtered = [
        r for r in rows
        if r["form"] in ACCEPTED_FORMS and r["date_filed"] >= cutoff
    ]
    result.rows_after_filter = len(filtered)
    log.info(
        "Filtered to %d rows (forms=%d, lookback=%d days)",
        len(filtered), len(ACCEPTED_FORMS), lookback_days,
    )

    latest = _latest_per_cik(filtered)
    candidate_ciks = set(latest.keys())

    # Find which CIKs we don't already know about.
    existing = {
        str(int(c[0]))
        for c in db.query(Trust.cik).filter(Trust.cik.in_(candidate_ciks)).all()
        if c[0]
    }
    new_ciks = candidate_ciks - existing
    result.new_ciks = len(new_ciks)
    log.info(
        "Candidates=%d, already known=%d, new=%d",
        len(candidate_ciks), len(existing), len(new_ciks),
    )

    # Stub-create new trusts in batches of 50.
    batch_size = 50
    pending = 0
    stubbed = 0
    for cik in sorted(new_ciks):
        row = latest[cik]
        base_slug = _slugify(row["name"])
        slug = _unique_slug(db, base_slug)
        trust = Trust(
            cik=cik,
            name=row["name"],
            slug=slug,
            is_rex=False,
            is_active=False,
            source="bulk_discovery",
            last_filed=row["date_filed"],
        )
        db.add(trust)
        log.info("new trust candidate: %s %s", cik, row["name"])
        stubbed += 1
        pending += 1
        if pending >= batch_size:
            db.flush()
            pending = 0

    if pending:
        db.flush()

    db.commit()
    result.stubbed_trusts = stubbed

    # Save new ETag only after successful commit.
    if new_etag:
        _save_etag(new_etag)

    log.info("Bulk sync complete: %s", result.as_dict())
    return result


def run() -> None:
    """CLI entrypoint for systemd oneshot."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("rexfinhub bulk sync starting")
    from webapp.database import SessionLocal

    db = SessionLocal()
    try:
        result = sync_once(db)
        log.info("Result: %s", result.as_dict())
    except Exception as e:
        log.exception("Bulk sync failed: %s", e)
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    run()
