from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
import json
import time
import logging

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from sqlalchemy import select

from webapp.models import Trust, FilingAlert, TrustCandidate

log = logging.getLogger(__name__)

EFTS_URL = "https://efts.sec.gov/LATEST/search-index"
FORM_TYPES_40ACT = "485BPOS,485APOS,485BXT"
FORM_TYPES_33ACT = "S-1,S-1/A,S-3,S-3/A"
PAUSE = 0.35
USER_AGENT = "REX-ETP-Tracker/2.0 (relasmar@rexfin.com)"
AUTO_APPROVE_THRESHOLD = 0.70


@dataclass
class EdgarHit:
    cik: str
    company_name: str
    accession_number: str
    form_type: str
    filed_date: str


@dataclass
class WatcherResult:
    alerts_created: int = 0
    alerts_skipped: int = 0
    candidates_new: int = 0
    candidates_updated: int = 0
    errors: list = field(default_factory=list)


def _get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    retry = Retry(total=3, backoff_factor=0.5, status_forcelist=(429, 500, 502, 503))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


def poll_recent_filings(db, lookback_days: int = 1, form_types: str | None = None,
                        poll_33act: bool = False) -> WatcherResult:
    known_rows = db.execute(select(Trust.cik, Trust.id)).fetchall()
    cik_to_trust = {str(int(row[0])): row[1] for row in known_rows}
    known_ciks = set(cik_to_trust.keys())

    today = date.today()
    start = today - timedelta(days=lookback_days)

    # Poll 40-Act forms (485 series)
    ft = form_types or FORM_TYPES_40ACT
    hits = _query_edgar(ft, start.isoformat(), today.isoformat())

    # Optionally also poll 33-Act forms (S-1/S-3 for crypto/ETN filers)
    if poll_33act:
        hits_33 = _query_edgar(FORM_TYPES_33ACT, start.isoformat(), today.isoformat())
        hits.extend(hits_33)
        log.info("33-Act poll: found %d additional hits", len(hits_33))

    result = WatcherResult()
    for hit in hits:
        try:
            if hit.cik in known_ciks:
                created = _upsert_filing_alert(db, cik_to_trust[hit.cik], hit)
                if created:
                    result.alerts_created += 1
                else:
                    result.alerts_skipped += 1
            else:
                is_new = _upsert_trust_candidate(db, hit)
                if is_new:
                    result.candidates_new += 1
                else:
                    result.candidates_updated += 1
        except Exception as e:
            result.errors.append(f"CIK {hit.cik}: {e}")
            log.warning("Error processing hit for CIK %s: %s", hit.cik, e)

    db.commit()
    return result


def _query_edgar(form_types: str, start_date: str, end_date: str) -> list[EdgarHit]:
    session = _get_session()
    hits: list[EdgarHit] = []
    offset = 0

    while True:
        params = {
            "forms": form_types,
            "dateRange": "custom",
            "startdt": start_date,
            "enddt": end_date,
            "from": offset,
        }
        time.sleep(PAUSE)
        try:
            resp = session.get(EFTS_URL, params=params, timeout=15)
        except requests.RequestException as e:
            log.error("EFTS request failed: %s", e)
            break

        if resp.status_code != 200:
            log.error("EFTS returned %d", resp.status_code)
            break

        data = resp.json()
        page_hits = data.get("hits", {}).get("hits", [])
        if not page_hits:
            break

        for h in page_hits:
            src = h.get("_source", {})
            ciks = src.get("ciks", [])
            if not ciks:
                continue
            hits.append(EdgarHit(
                cik=str(int(ciks[0])),
                company_name=src.get("entity_name", "Unknown"),
                accession_number=src.get("adsh", ""),
                form_type=src.get("form_type", ""),
                filed_date=src.get("file_date", ""),
            ))

        total = data.get("hits", {}).get("total", {}).get("value", 0)
        offset += len(page_hits)
        if offset >= total:
            break

    return hits


def _upsert_filing_alert(db, trust_id: int, hit: EdgarHit) -> bool:
    existing = db.query(FilingAlert).filter_by(accession_number=hit.accession_number).first()
    if existing:
        return False
    filed = None
    if hit.filed_date:
        try:
            filed = date.fromisoformat(hit.filed_date)
        except ValueError:
            pass
    alert = FilingAlert(
        trust_id=trust_id,
        accession_number=hit.accession_number,
        form_type=hit.form_type,
        filed_date=filed,
    )
    db.add(alert)
    return True


def _upsert_trust_candidate(db, hit: EdgarHit) -> bool:
    existing = db.query(TrustCandidate).filter_by(cik=hit.cik).first()
    if existing:
        existing.last_seen = datetime.utcnow()
        existing.filing_count += 1
        seen = json.loads(existing.form_types_seen or "[]")
        if hit.form_type not in seen:
            seen.append(hit.form_type)
            existing.form_types_seen = json.dumps(sorted(seen))
        return False
    candidate = TrustCandidate(
        cik=hit.cik,
        company_name=hit.company_name,
        form_types_seen=json.dumps([hit.form_type]),
    )
    db.add(candidate)
    return True


def auto_approve_candidates(db, threshold: float = AUTO_APPROVE_THRESHOLD) -> int:
    """Enrich new candidates via discovery and auto-approve high-scoring ones.

    Auto-approved trusts get a Trust record with source='watcher' and
    is_active=True so the next daily pipeline run picks them up.

    Returns:
        Number of candidates auto-approved.
    """
    from etp_tracker.discovery import enrich_candidate
    from etp_tracker.sec_client import SECClient

    client = SECClient(user_agent=USER_AGENT, pause=PAUSE)
    candidates = db.query(TrustCandidate).filter_by(status="new").all()
    approved = 0

    for c in candidates:
        result = enrich_candidate(client, c)
        if not result:
            continue

        score = result.get("etf_trust_score", 0)
        c.etf_trust_score = score

        if score >= threshold:
            # Check not already tracked
            existing_trust = db.query(Trust).filter_by(cik=c.cik).first()
            if existing_trust:
                c.status = "duplicate"
                log.info("Candidate CIK %s already tracked as '%s'", c.cik, existing_trust.name)
                continue

            # Create new Trust record (prefer enriched name from SEC)
            trust_name = result.get("name") or c.company_name
            trust = Trust(
                cik=c.cik,
                name=trust_name,
                slug=trust_name.lower().replace(" ", "-").replace("/", "-")[:200],
                is_active=True,
                source="watcher",
                entity_type=result.get("entity_type", "unknown"),
                sic_code=result.get("sic_code"),
            )
            db.add(trust)
            c.status = "auto_approved"
            c.reviewed_at = datetime.utcnow()
            c.reviewed_by = "watcher_auto"
            approved += 1
            log.info("Auto-approved CIK %s '%s' (score=%.2f)", c.cik, c.company_name, score)
        else:
            c.status = "low_score"
            log.debug("CIK %s scored %.2f (below threshold %.2f)", c.cik, score, threshold)

    db.commit()
    log.info("Auto-approve: %d/%d candidates approved", approved, len(candidates))
    return approved
