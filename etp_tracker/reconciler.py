"""Tier 3: Daily SEC form index reconciler.

Safety net for the atom watcher (Tier 1). Once a day, pull the SEC's
authoritative daily filing index and diff it against the filing_alerts
table. Any filings the atom feed missed (rate-limit spikes, parse errors,
outages) are inserted as new alerts with source='reconciler', which Tier 2
(single_filing_worker) then enriches.

Index URL format:
    https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{N}/form.{YYYYMMDD}.idx

Note: SEC does not publish indexes on weekends/holidays. reconcile_day()
returns zero counts for those days.

Run as systemd oneshot:
    /home/jarvis/venv/bin/python -m etp_tracker.reconciler
"""
from __future__ import annotations

import logging
import os
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "REX-ETP-Tracker/2.0 (relasmar@rexfin.com)"
)

# Same set as atom_watcher.py FORM_QUERIES — keep in sync.
ACCEPTED_FORMS = {
    "485APOS", "485BPOS", "485BXT", "485B", "485A",
    "497", "497K", "497J",
    "N-1A", "N-1A/A",
    "N-2", "N-2/A",
    "S-1", "S-1/A",
    "S-3", "S-3/A", "S-3ASR",
}

INDEX_URL = (
    "https://www.sec.gov/Archives/edgar/daily-index/"
    "{year}/QTR{qtr}/form.{ymd}.idx"
)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class IndexRow:
    """One row parsed from a daily form.idx file."""
    form_type: str
    company_name: str
    cik: str
    date_filed: str  # YYYY-MM-DD as it appears in the index
    filename: str    # e.g. edgar/data/822977/0001193125-26-101234-index.htm
    accession_number: str  # extracted from filename


@dataclass
class ReconcileResult:
    """Counts from one reconcile_day() run."""
    target_date: date
    fetched: int = 0        # 1 if the index file was pulled, 0 if skipped
    parsed: int = 0         # total rows parsed from the index
    matched: int = 0        # accepted rows already present in filing_alerts
    new_inserted: int = 0   # accepted rows newly inserted
    skipped_weekend: bool = False


# ---------------------------------------------------------------------------
# Index URL + parsing
# ---------------------------------------------------------------------------

def _quarter(d: date) -> int:
    return (d.month - 1) // 3 + 1


def index_url_for(d: date) -> str:
    """Build the daily form index URL for a given date."""
    return INDEX_URL.format(
        year=d.year,
        qtr=_quarter(d),
        ymd=d.strftime("%Y%m%d"),
    )


def _extract_accession(filename: str) -> str:
    """Pull the 18-char accession number from an index filename.

    Examples:
        edgar/data/822977/0001193125-26-101234-index.htm  -> 0001193125-26-101234
        edgar/data/822977/0001193125-26-101234.txt        -> 0001193125-26-101234
    """
    if not filename:
        return ""
    base = filename.rsplit("/", 1)[-1]
    # Strip common suffixes
    for suffix in ("-index.htm", "-index.html", ".txt"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
            break
    return base.strip()


def parse_form_index(text: str) -> list[IndexRow]:
    """Parse a form.YYYYMMDD.idx body into IndexRow objects.

    The index is a fixed-column ASCII report with a dashes separator line
    followed by rows shaped like:

        485BPOS          Goldman Sachs Trust                       822977      20260414    edgar/data/822977/0001193125-26-101234.txt

    In practice the dashes line is one contiguous dash run (no gaps), so
    fixed-offset slicing is unreliable. The data columns are separated by
    runs of whitespace and only company_name contains internal spaces, so
    we rsplit from the right (filename, date_filed, cik are atomic) and
    then pull the form off the front of the remainder.

    Also dedupes rows by accession_number — the SEC index legitimately
    emits the same filing multiple times (once per filer/issuer role).
    """
    if not text:
        return []

    lines = text.splitlines()

    # Find the dashes separator line — the header lines vary but the row
    # immediately before the data is always a run of '-' characters.
    header_idx = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and set(stripped) == {"-"} and len(stripped) >= 20:
            header_idx = i
            break

    if header_idx is None:
        log.warning("parse_form_index: no dashes separator found")
        return []

    rows: list[IndexRow] = []
    seen_accessions: set[str] = set()

    for line in lines[header_idx + 1:]:
        if not line.strip():
            continue

        # Right-split: [head, cik, date_filed, filename]
        parts = line.rsplit(None, 3)
        if len(parts) != 4:
            continue
        head, cik, date_filed, filename = parts

        # head = "FORMTYPE   Company Name With Spaces"
        head_parts = head.split(None, 1)
        if len(head_parts) != 2:
            continue
        form, company = head_parts
        form = form.strip()
        company = company.strip()

        if not form or not filename:
            continue

        accession = _extract_accession(filename)
        if not accession or accession in seen_accessions:
            continue
        seen_accessions.add(accession)

        rows.append(IndexRow(
            form_type=form,
            company_name=company,
            cik=str(int(cik)) if cik.isdigit() else cik,
            date_filed=date_filed,
            filename=filename,
            accession_number=accession,
        ))

    return rows


# ---------------------------------------------------------------------------
# Core reconcile
# ---------------------------------------------------------------------------

def reconcile_day(db, target_date: date) -> ReconcileResult:
    """Pull form.YYYYMMDD.idx, diff against filing_alerts, insert missing.

    Args:
        db: a webapp.database.SessionLocal() session (caller owns lifecycle)
        target_date: date to reconcile. Weekends short-circuit to zero counts.

    Returns:
        ReconcileResult with fetched/parsed/matched/new_inserted counts.
    """
    from sqlalchemy import select
    from webapp.models import FilingAlert
    from datetime import date as _date
    from .sec_client import SECClient

    result = ReconcileResult(target_date=target_date)

    # SEC does not publish an index on weekends. Holidays will 404 below;
    # we treat that as zero gracefully.
    if target_date.weekday() >= 5:
        log.info("reconcile_day: %s is a weekend, skipping", target_date)
        result.skipped_weekend = True
        return result

    url = index_url_for(target_date)
    log.info("reconcile_day: fetching %s", url)

    client = SECClient(user_agent=USER_AGENT)
    try:
        text = client.fetch_text(url)
    except Exception as e:
        log.warning("reconcile_day: fetch failed for %s: %s", target_date, e)
        return result

    if not text or "form.idx" in text.lower() and len(text) < 100:
        log.info("reconcile_day: empty or missing index for %s", target_date)
        return result

    result.fetched = 1

    rows = parse_form_index(text)
    result.parsed = len(rows)

    # Filter to forms we care about
    accepted_rows = [r for r in rows if r.form_type in ACCEPTED_FORMS]
    if not accepted_rows:
        log.info(
            "reconcile_day: %s parsed=%d accepted=0 new=0",
            target_date, result.parsed,
        )
        return result

    # Pre-load existing accessions in one query
    accessions = [r.accession_number for r in accepted_rows]
    existing: set[str] = set()
    # Chunk the IN clause to be safe on very long days
    CHUNK = 500
    for i in range(0, len(accessions), CHUNK):
        chunk = accessions[i: i + CHUNK]
        rows_existing = db.execute(
            select(FilingAlert.accession_number).where(
                FilingAlert.accession_number.in_(chunk)
            )
        ).all()
        existing.update(row[0] for row in rows_existing)

    result.matched = len(existing)

    # Rows in a single index all share target_date; try to parse the
    # row's date_filed (format is typically 'YYYYMMDD' or 'YYYY-MM-DD')
    # and fall back to target_date on any mismatch.
    def _parse_dt(s: str):
        if not s:
            return target_date
        s = s.strip()
        try:
            if len(s) == 8 and s.isdigit():
                return _date(int(s[0:4]), int(s[4:6]), int(s[6:8]))
            return _date.fromisoformat(s[:10])
        except (ValueError, TypeError):
            return target_date

    batch_count = 0
    for row in accepted_rows:
        if row.accession_number in existing:
            continue

        alert = FilingAlert(
            trust_id=None,  # Tier 2 will resolve
            cik=row.cik or None,
            accession_number=row.accession_number,
            form_type=row.form_type,
            filed_date=_parse_dt(row.date_filed),
            source="reconciler",
            enrichment_status=0,
            company_name=row.company_name or None,
        )
        db.add(alert)
        result.new_inserted += 1
        batch_count += 1

        if batch_count >= 100:
            db.commit()
            batch_count = 0

    if batch_count:
        db.commit()

    log.info(
        "reconcile_day: %s fetched=%d parsed=%d matched=%d new=%d",
        target_date, result.fetched, result.parsed,
        result.matched, result.new_inserted,
    )
    return result


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def run_recent(days_back: int = 3) -> None:
    """Reconcile the last N calendar days (default 3).

    Idempotent: running twice does not duplicate rows because
    filing_alerts.accession_number is UNIQUE.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    from webapp.database import init_db, SessionLocal
    init_db()

    today = date.today()
    totals = {"fetched": 0, "parsed": 0, "matched": 0, "new": 0}

    db = SessionLocal()
    try:
        for offset in range(days_back, 0, -1):
            target = today - timedelta(days=offset)
            try:
                r = reconcile_day(db, target)
            except Exception as e:
                log.exception("reconcile_day failed for %s: %s", target, e)
                continue
            totals["fetched"] += r.fetched
            totals["parsed"] += r.parsed
            totals["matched"] += r.matched
            totals["new"] += r.new_inserted

        # Also reconcile today (useful if run late in the day)
        try:
            r = reconcile_day(db, today)
            totals["fetched"] += r.fetched
            totals["parsed"] += r.parsed
            totals["matched"] += r.matched
            totals["new"] += r.new_inserted
        except Exception as e:
            log.exception("reconcile_day failed for %s: %s", today, e)
    finally:
        db.close()

    log.info(
        "run_recent done: days_back=%d fetched=%d parsed=%d matched=%d new=%d",
        days_back, totals["fetched"], totals["parsed"],
        totals["matched"], totals["new"],
    )


if __name__ == "__main__":
    days = 3
    if len(sys.argv) > 1:
        try:
            days = int(sys.argv[1])
        except ValueError:
            pass
    run_recent(days_back=days)
