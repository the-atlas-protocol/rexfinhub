"""SEC EDGAR Atom Feed Watcher — near-realtime filing detection.

Polls the public 'getcurrent' atom feed every 60 seconds for new prospectus
filings across the entire SEC universe. No curated list — every filer is
in scope. Detection latency is ~1-3 minutes from filing acceptance.

Architecture:
  Tier 1 (this module): Atom feed polling
    - 8 polls per cycle, one per form prefix
    - Strict client-side form filter (S-1 query also returns S-11/A — reject)
    - Dedupe by accession number against filing_alerts table
    - Insert new accessions with source='atom', enrichment_status=0
    - Triggers Tier 2 enrichment in-process (no IPC)

  Tier 2 (etp_tracker/single_filing.py — separate module):
    - For each new accession: fetch submissions JSON, run step3 extraction
    - If CIK is unknown: insert into trusts (source='watcher_atom', is_active=1)
    - Mark filing_alerts.enrichment_status=2 when done

Schema additions to filing_alerts (run via init_db migrate):
    cik TEXT
    form TEXT  (mirror of form_type with consistent naming)
    source TEXT DEFAULT 'efts'  ('atom' for new entries)
    enrichment_status INTEGER DEFAULT 0  (0=raw, 1=metadata fetched, 2=step3 done)
    primary_doc_url TEXT
    size_bytes INTEGER

Run as systemd daemon:
    /home/jarvis/venv/bin/python -m etp_tracker.atom_watcher

Configuration via environment variables:
    SEC_USER_AGENT: required, must include contact email
    POLL_INTERVAL: seconds between cycles (default 60)
    SEC_CACHE_DIR: cache root (existing convention)
"""
from __future__ import annotations

import logging
import os
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ATOM_BASE = "https://www.sec.gov/cgi-bin/browse-edgar"
USER_AGENT = os.environ.get("SEC_USER_AGENT", "REX-ETP-Tracker/2.0 (relasmar@rexfin.com)")
POLL_INTERVAL = int(os.environ.get("POLL_INTERVAL", "60"))

# Form prefixes to poll, with the EXACT subforms we accept from each.
# Atom prefix matching is greedy: type=S-1 also returns S-11/A which we
# DON'T want (S-11 is real estate, not an ETP). Strict client-side filter.
#
# IMPORTANT: only fund-specific forms. S-1 and S-3 are used by every
# non-fund operating company (IPOs, secondaries, shelf registrations),
# which polluted the trusts table with Devon Energy, Lennar Homes, etc.
# Rare commodity ETPs that DO use S-1 (e.g. Grayscale BTC) are added
# manually via the admin trust-CRUD panel.
FORM_QUERIES = [
    # (atom query type, accepted exact forms)
    ("485", {"485APOS", "485BPOS", "485BXT", "485B", "485A"}),
    ("497", {"497", "497K", "497J"}),
    ("N-1A", {"N-1A", "N-1A/A"}),
    ("N-2", {"N-2", "N-2/A"}),
]

# Atom XML namespace
ATOM_NS = {"a": "http://www.w3.org/2005/Atom"}

# State file persists last-modified timestamps + recent-accession dedup set
# across watcher restarts. Lives next to the cache.
STATE_DIR = Path(os.environ.get("SEC_CACHE_DIR", "cache/sec")) / "atom_state"


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class AtomEntry:
    """One filing parsed from the atom feed."""
    accession_number: str
    form: str
    cik: str
    company_name: str
    filed_date: str   # YYYY-MM-DD from <updated>
    primary_doc_url: str
    size_str: str  # e.g. "1 MB", "39 MB"

    def filed_iso(self) -> datetime | None:
        """Return the entry's <updated> as a UTC datetime, or None."""
        try:
            # Format: 2026-04-14T10:50:42-04:00
            return datetime.fromisoformat(self.filed_date)
        except (ValueError, TypeError):
            return None


@dataclass
class PollResult:
    """Outcome of one polling cycle."""
    queried: int = 0       # number of feeds polled
    fetched: int = 0       # number of feeds that returned 200 (not 304)
    parsed: int = 0        # entries parsed across all feeds
    accepted: int = 0      # entries that passed client-side form filter
    new: int = 0           # entries not already in filing_alerts (would be inserted)
    skipped: int = 0       # entries already in filing_alerts
    errors: int = 0


# ---------------------------------------------------------------------------
# HTTP session
# ---------------------------------------------------------------------------

def _build_session() -> requests.Session:
    """Build a session with SEC-friendly retries + UA."""
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "application/atom+xml,application/xml",
        "Accept-Encoding": "gzip, deflate",
    })
    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset(["GET"]),
    )
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


# ---------------------------------------------------------------------------
# State persistence
# ---------------------------------------------------------------------------

def _state_path() -> Path:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    return STATE_DIR / "watcher_state.json"


def _load_state() -> dict:
    """Load { 'last_modified': {form_query: 'rfc822 string'}, 'recent_accessions': [...]  }.

    last_modified powers If-Modified-Since headers per feed.
    recent_accessions is a rolling set of recently-seen accession IDs that
    serves as an in-memory dedup before we hit the DB. Persisted across
    restarts so a watcher reboot doesn't re-emit alerts for filings still
    visible in the feed.
    """
    import json
    p = _state_path()
    if not p.exists():
        return {"last_modified": {}, "recent_accessions": []}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("Failed to load watcher state, starting fresh: %s", e)
        return {"last_modified": {}, "recent_accessions": []}


def _save_state(state: dict) -> None:
    import json
    p = _state_path()
    # Cap recent_accessions list to most recent 5000 (atom feed shows 100 per
    # form, 8 forms = 800 max in flight; 5000 gives plenty of headroom)
    if "recent_accessions" in state:
        state["recent_accessions"] = state["recent_accessions"][-5000:]
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(state), encoding="utf-8")
    tmp.replace(p)


# ---------------------------------------------------------------------------
# Atom feed fetch + parse
# ---------------------------------------------------------------------------

def fetch_atom_feed(session: requests.Session, query_type: str,
                    if_modified_since: str | None = None,
                    count: int = 100) -> tuple[str, str | None] | None:
    """Fetch one atom feed. Returns (xml_text, new_last_modified) or None on 304.

    Sends If-Modified-Since header for cheap polling. SEC does honor it on
    this endpoint (verified live 2026-04-14).
    """
    params = {
        "action": "getcurrent",
        "type": query_type,
        "output": "atom",
        "count": count,
    }
    headers = {}
    if if_modified_since:
        headers["If-Modified-Since"] = if_modified_since

    try:
        resp = session.get(
            ATOM_BASE,
            params=params,
            headers=headers,
            timeout=(10, 30),
        )
    except requests.RequestException as e:
        log.warning("Atom fetch failed for type=%s: %s", query_type, e)
        return None

    if resp.status_code == 304:
        log.debug("Atom feed type=%s: 304 Not Modified", query_type)
        return ("", if_modified_since)
    if resp.status_code != 200:
        log.warning("Atom feed type=%s: HTTP %d", query_type, resp.status_code)
        return None

    new_lm = resp.headers.get("Last-Modified")
    return (resp.text, new_lm)


def parse_atom_feed(xml_text: str, accepted_forms: set[str]) -> list[AtomEntry]:
    """Parse atom XML into AtomEntry objects, filtered by exact form match.

    Args:
        xml_text: full XML body from getcurrent feed
        accepted_forms: set of EXACT form strings to keep (e.g. {"485APOS",
            "485BPOS"}). Atom prefix matching is too greedy — type=S-1 returns
            S-11/A — so we filter client-side here.

    Returns: list of AtomEntry, deduplicated by accession_number within feed
        (atom feeds emit each filing 1-3 times per role: Filer/Issuer/Reporting).
    """
    if not xml_text or not xml_text.strip():
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        log.warning("Atom parse error: %s", e)
        return []

    entries = []
    seen_accessions = set()

    for entry in root.findall("a:entry", ATOM_NS):
        # <category term="485APOS"/>
        category = entry.find("a:category", ATOM_NS)
        form = category.get("term", "") if category is not None else ""
        if form not in accepted_forms:
            continue

        # <id>urn:tag:sec.gov,2008:accession-number=0001445546-26-002716</id>
        id_el = entry.find("a:id", ATOM_NS)
        if id_el is None or not id_el.text:
            continue
        # Parse out accession number
        id_text = id_el.text
        if "accession-number=" not in id_text:
            continue
        accession = id_text.split("accession-number=", 1)[1].strip()
        if not accession or accession in seen_accessions:
            continue
        seen_accessions.add(accession)

        # <title>485APOS - Goldman Sachs ETF Trust (0001479026) (Filer)</title>
        title_el = entry.find("a:title", ATOM_NS)
        title = title_el.text if title_el is not None else ""
        # Extract company name + CIK
        company_name = ""
        cik = ""
        if " - " in title and "(" in title:
            after_dash = title.split(" - ", 1)[1]
            # "Goldman Sachs ETF Trust (0001479026) (Filer)"
            paren_idx = after_dash.rfind("(")  # last "(Filer)" or "(Issuer)"
            if paren_idx > 0:
                # find the second-to-last paren which holds the CIK
                rest = after_dash[:paren_idx].strip()
                inner_paren = rest.rfind("(")
                if inner_paren > 0:
                    company_name = rest[:inner_paren].strip()
                    cik_str = rest[inner_paren + 1:].rstrip(") ").strip()
                    if cik_str.isdigit():
                        cik = str(int(cik_str))  # strip leading zeros
        if not cik:
            log.debug("Could not parse CIK from title: %s", title)
            continue

        # <link href="..."/>
        link_el = entry.find("a:link", ATOM_NS)
        primary_doc = link_el.get("href", "") if link_el is not None else ""

        # <updated>2026-04-14T10:50:42-04:00</updated>
        updated_el = entry.find("a:updated", ATOM_NS)
        filed_date = updated_el.text if updated_el is not None else ""

        # <summary> contains "Size: 1 MB"
        size_str = ""
        summary_el = entry.find("a:summary", ATOM_NS)
        if summary_el is not None and summary_el.text:
            txt = summary_el.text
            if "Size:" in txt:
                size_str = txt.split("Size:", 1)[1].strip().split("<")[0].strip()

        entries.append(AtomEntry(
            accession_number=accession,
            form=form,
            cik=cik,
            company_name=company_name,
            filed_date=filed_date,
            primary_doc_url=primary_doc,
            size_str=size_str,
        ))

    return entries


# ---------------------------------------------------------------------------
# DB write — insert new alerts
# ---------------------------------------------------------------------------

def insert_new_alerts(entries: Iterable[AtomEntry]) -> tuple[int, int]:
    """Insert atom entries into filing_alerts, dedupe by accession.

    Returns (new_count, skipped_count).

    NOTE: this writes raw alerts only. Enrichment (resolving CIK to Trust,
    running step3 extraction) is the responsibility of single_filing.py
    Tier 2. Watcher's job is to record the existence of the filing fast.
    """
    from webapp.database import init_db, SessionLocal
    from webapp.models import FilingAlert, Trust
    from sqlalchemy import select
    from datetime import date as _date

    init_db()
    db = SessionLocal()
    new_count = 0
    skipped_count = 0
    try:
        # Pre-load existing accessions in batch (much faster than per-row check)
        entries_list = list(entries)
        if not entries_list:
            return (0, 0)
        accessions = [e.accession_number for e in entries_list]
        existing_set = {
            row[0] for row in db.execute(
                select(FilingAlert.accession_number).where(
                    FilingAlert.accession_number.in_(accessions)
                )
            ).all()
        }

        # Resolve CIK -> trust_id for known trusts (so the alert links to a real trust row)
        ciks = list({e.cik for e in entries_list})
        cik_to_trust = {
            row[0]: row[1] for row in db.execute(
                select(Trust.cik, Trust.id).where(Trust.cik.in_(ciks))
            ).all()
        }

        # Parse size "39 MB" / "1 MB" -> bytes
        def _size_bytes(s: str) -> int | None:
            if not s:
                return None
            parts = s.strip().split()
            if len(parts) != 2:
                return None
            try:
                n = float(parts[0])
            except ValueError:
                return None
            unit = parts[1].upper()
            mult = {"KB": 1024, "MB": 1024**2, "GB": 1024**3}.get(unit, 1)
            return int(n * mult)

        for e in entries_list:
            if e.accession_number in existing_set:
                skipped_count += 1
                continue

            filed_dt = None
            if e.filed_date:
                try:
                    filed_dt = _date.fromisoformat(e.filed_date[:10])
                except (ValueError, TypeError):
                    pass

            # Tier 1 writes the alert regardless of CIK-known status. Tier 2
            # (single_filing_worker) resolves trust_id and runs step3
            # extraction asynchronously by polling enrichment_status=0 rows.
            trust_id = cik_to_trust.get(e.cik)

            alert = FilingAlert(
                trust_id=trust_id,  # may be None for unknown CIK
                cik=e.cik,
                accession_number=e.accession_number,
                form_type=e.form,
                filed_date=filed_dt,
                source="atom",
                enrichment_status=0,  # pending Tier 2 enrichment
                primary_doc_url=e.primary_doc_url or None,
                size_bytes=_size_bytes(e.size_str),
                company_name=e.company_name or None,
            )
            db.add(alert)
            new_count += 1
        db.commit()

        # Push raw detections to Render live feed. This is the user-visible
        # "a new filing just happened" moment — even before Tier 2 enrichment,
        # so browsers see it within seconds of SEC acceptance. Tier 2 will
        # UPSERT the same row later with trust_slug/trust_name once known.
        try:
            from .live_push import push_alert
            for e in entries_list:
                if e.accession_number in existing_set:
                    continue
                trust_id_val = cik_to_trust.get(e.cik)
                push_alert(
                    accession_number=e.accession_number,
                    form=e.form,
                    cik=e.cik,
                    company_name=e.company_name,
                    trust_id=trust_id_val,
                    trust_name=e.company_name,  # best guess until Tier 2 resolves
                    filed_date=e.filed_date[:10] if e.filed_date else None,
                    primary_doc_url=e.primary_doc_url,
                    source="atom",
                )
        except Exception as push_exc:
            log.warning("live push batch failed: %s", push_exc)
    finally:
        db.close()
    return (new_count, skipped_count)


# ---------------------------------------------------------------------------
# Main poll loop
# ---------------------------------------------------------------------------

def poll_once(session: requests.Session, state: dict) -> PollResult:
    """Run one polling cycle across all 8 form queries."""
    result = PollResult()
    last_modified_map = state.setdefault("last_modified", {})
    recent_set = set(state.setdefault("recent_accessions", []))

    all_new_entries: list[AtomEntry] = []

    for query_type, accepted_forms in FORM_QUERIES:
        result.queried += 1
        try:
            ims = last_modified_map.get(query_type)
            fetch_result = fetch_atom_feed(session, query_type, if_modified_since=ims)
            if fetch_result is None:
                result.errors += 1
                continue
            xml_text, new_lm = fetch_result

            if not xml_text:
                # 304 Not Modified — nothing new
                continue
            result.fetched += 1

            entries = parse_atom_feed(xml_text, accepted_forms)
            result.parsed += len(entries)
            result.accepted += len(entries)

            # Dedupe against in-memory recent set BEFORE hitting DB
            new_entries = [e for e in entries if e.accession_number not in recent_set]
            all_new_entries.extend(new_entries)
            for e in new_entries:
                recent_set.add(e.accession_number)
                state["recent_accessions"].append(e.accession_number)

            if new_lm:
                last_modified_map[query_type] = new_lm

            # SEC rate limit: 10 req/sec. We're at 8 req/min, plenty of headroom,
            # but be a good citizen with a small pause between feeds.
            time.sleep(0.2)
        except Exception as e:
            log.exception("Error polling type=%s: %s", query_type, e)
            result.errors += 1

    # Batch insert all new entries to DB
    if all_new_entries:
        try:
            new_count, skipped_count = insert_new_alerts(all_new_entries)
            result.new = new_count
            result.skipped = skipped_count
        except Exception as e:
            log.exception("Failed to insert alerts: %s", e)
            result.errors += 1

    return result


def run_forever():
    """Long-running poll loop. Designed for systemd Type=simple."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("Atom watcher starting")
    log.info("Polling %d form queries every %ds", len(FORM_QUERIES), POLL_INTERVAL)
    log.info("User-Agent: %s", USER_AGENT)

    session = _build_session()
    state = _load_state()
    log.info("Loaded state: %d cached last-modified, %d recent accessions",
             len(state.get("last_modified", {})), len(state.get("recent_accessions", [])))

    cycle = 0
    while True:
        cycle += 1
        start = time.time()
        try:
            result = poll_once(session, state)
            elapsed = time.time() - start
            log.info(
                "Cycle %d: %ds  queried=%d fetched=%d parsed=%d new=%d skipped=%d errors=%d",
                cycle, int(elapsed),
                result.queried, result.fetched, result.parsed,
                result.new, result.skipped, result.errors,
            )
            _save_state(state)
        except Exception as e:
            log.exception("Cycle %d failed: %s", cycle, e)

        # Sleep until next cycle
        sleep_for = max(0, POLL_INTERVAL - (time.time() - start))
        time.sleep(sleep_for)


if __name__ == "__main__":
    run_forever()
