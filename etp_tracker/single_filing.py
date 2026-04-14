"""Tier 2: Single-filing enricher.

Polls the filing_alerts table for rows with enrichment_status=0 and runs the
existing step3 extraction logic against a single filing (rather than a whole
trust's historical batch). Auto-creates a Trust row when the CIK is unknown.

This module is the narrow-scope sibling of etp_tracker.step3 — it reuses the
same strategy routing (_extract_full / _extract_header_only / _extract_s1_metadata)
so that a filing seen via the atom watcher produces the same output shape as
one found through the curated-trust pipeline.

Public API:
    enrich_alert(db, alert) -> EnrichResult

Does not commit — the caller (worker) owns transaction boundaries so that a
mid-batch crash loses at most one filing's progress.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import EXTRACTION_STRATEGIES, DEFAULT_EXTRACTION_STRATEGY
from .paths import build_primary_link, build_submission_txt_link
from .sec_client import SECClient
from .step3 import (
    _extract_full,
    _extract_header_only,
    _extract_s1_metadata,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result object
# ---------------------------------------------------------------------------

@dataclass
class EnrichResult:
    """Outcome of enriching a single FilingAlert."""
    ok: bool
    accession: str
    trust_id: int | None = None
    trust_created: bool = False
    filing_id: int | None = None
    extractions_count: int = 0
    skipped_reason: str | None = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Trust auto-creation
# ---------------------------------------------------------------------------

_SLUG_STRIP_RE = re.compile(r"[^a-z0-9\s-]")
_SLUG_SPACE_RE = re.compile(r"[\s_]+")


def _make_slug(name: str) -> str:
    """lowercase -> strip special chars -> spaces to dashes -> collapse."""
    s = (name or "").lower()
    s = _SLUG_STRIP_RE.sub("", s)
    s = _SLUG_SPACE_RE.sub("-", s).strip("-")
    return s or "unknown"


def _unique_slug(db: Session, base_slug: str) -> str:
    """Return a slug that doesn't collide with existing Trust.slug."""
    from webapp.models import Trust

    slug = base_slug
    n = 1
    while db.execute(select(Trust.id).where(Trust.slug == slug)).first():
        n += 1
        slug = f"{base_slug}-{n}"
        if n > 50:
            # Pathological — fall back to a cik-qualified slug
            return f"{base_slug}-x"
    return slug


def resolve_or_create_trust(db: Session, client: SECClient, cik: str,
                             fallback_name: str | None = None) -> tuple[int, bool, str]:
    """Find the Trust for this CIK or create one.

    Returns (trust_id, created, trust_name). Does not commit.
    """
    from webapp.models import Trust

    cik_norm = str(int(str(cik)))
    existing = db.execute(
        select(Trust).where(Trust.cik == cik_norm)
    ).scalar_one_or_none()
    if existing:
        return existing.id, False, existing.name

    # Pull the authoritative name from SEC submissions JSON
    name = fallback_name or f"CIK {cik_norm}"
    try:
        data = client.load_submissions_json(cik_norm)
        if data.get("name"):
            name = data["name"]
    except Exception as e:
        log.warning("Could not load submissions JSON for CIK %s: %s", cik_norm, e)

    slug = _unique_slug(db, _make_slug(name))

    trust = Trust(
        cik=cik_norm,
        name=name,
        slug=slug,
        is_rex=False,
        is_active=True,
        source="watcher_atom",
    )
    db.add(trust)
    db.flush()  # populate trust.id without committing
    log.info("Created Trust(cik=%s, name=%r, slug=%s, id=%d)",
             cik_norm, name, slug, trust.id)
    return trust.id, True, name


# ---------------------------------------------------------------------------
# Filing lookup via submissions JSON
# ---------------------------------------------------------------------------

def _lookup_filing_metadata(client: SECClient, cik: str, accession: str) -> dict | None:
    """Find the filing row in the submissions JSON for this accession.

    Returns a dict with form/filing_date/primary_document/is_ixbrl, or None if
    the accession is not present in the cached submissions data.
    """
    try:
        data = client.load_submissions_json(cik)
    except Exception as e:
        log.warning("submissions JSON fetch failed for CIK %s: %s", cik, e)
        return None

    def _scan(rec: dict) -> dict | None:
        forms = rec.get("form", []) or []
        accns = rec.get("accessionNumber", []) or []
        files = rec.get("primaryDocument", []) or []
        dates = rec.get("filingDate", []) or []
        ixbrls = rec.get("isInlineXBRL", []) or []
        for i, a in enumerate(accns):
            if a == accession:
                return {
                    "form": forms[i] if i < len(forms) else "",
                    "filing_date": dates[i] if i < len(dates) else "",
                    "primary_document": files[i] if i < len(files) else "",
                    "is_ixbrl": str(ixbrls[i]) == "1" if i < len(ixbrls) else False,
                }
        return None

    rec = data.get("filings", {}).get("recent", {})
    found = _scan(rec)
    if found:
        return found

    # Paginated older filings
    for file_entry in data.get("filings", {}).get("files", []) or []:
        fname = file_entry.get("name", "")
        if not fname:
            continue
        url = f"https://data.sec.gov/submissions/{fname}"
        try:
            extra = client.fetch_json(url)
        except Exception:
            continue
        found = _scan(extra)
        if found:
            return found
    return None


# ---------------------------------------------------------------------------
# Extraction orchestration
# ---------------------------------------------------------------------------

def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        dt = pd.to_datetime(s, errors="coerce")
        if pd.isna(dt):
            return None
        return dt.date()
    except Exception:
        return None


def _run_extraction(client: SECClient, form: str, filing_dt: str, cik: str,
                    registrant: str, accession: str, prim_url: str,
                    txt_url: str, is_ixbrl: bool) -> list[dict]:
    """Route to the right step3 strategy and return row dicts."""
    form_upper = (form or "").strip().upper()
    strategy = EXTRACTION_STRATEGIES.get(form_upper, DEFAULT_EXTRACTION_STRATEGY)

    if strategy == "s1_metadata":
        return _extract_s1_metadata(
            client, txt_url, form, filing_dt, cik, registrant, accession, prim_url,
        )
    if strategy == "header_only":
        return _extract_header_only(
            client, txt_url, form, filing_dt, cik, registrant, accession, prim_url,
        )
    return _extract_full(
        client, txt_url, form, filing_dt, cik, registrant, accession, prim_url, is_ixbrl,
    )


def _write_extraction_to_db(db: Session, trust_id: int, cik: str,
                             accession: str, form: str, filing_dt: str,
                             prim_url: str, txt_url: str, registrant: str,
                             rows: list[dict]) -> tuple[int, int]:
    """Create Filing + FundExtraction rows and lightweight FundStatus updates.

    Returns (filing_id, extractions_count).
    """
    from webapp.models import Filing, FundExtraction, FundStatus

    filing_date = _parse_date(filing_dt)

    # Upsert Filing
    existing_filing = db.execute(
        select(Filing).where(Filing.accession_number == accession)
    ).scalar_one_or_none()

    if existing_filing:
        filing = existing_filing
        # Ensure trust_id is set (might have been inserted unlinked earlier)
        if filing.trust_id != trust_id:
            filing.trust_id = trust_id
    else:
        # Pick a primary_document filename out of the primary link
        primary_document = ""
        if prim_url:
            primary_document = prim_url.rsplit("/", 1)[-1]

        filing = Filing(
            trust_id=trust_id,
            accession_number=accession,
            form=form,
            filing_date=filing_date,
            primary_document=primary_document or None,
            primary_link=prim_url or None,
            submission_txt_link=txt_url or None,
            cik=str(int(str(cik))),
            registrant=registrant,
            processed=True,
        )
        db.add(filing)
        db.flush()

    extractions_count = 0
    touched_series: dict[tuple[str, str], dict] = {}

    for row in rows:
        series_id = (row.get("Series ID") or "").strip()
        class_id = (row.get("Class-Contract ID") or "").strip()
        series_name = (row.get("Series Name") or "").strip()
        class_name = (row.get("Class Contract Name") or "").strip()
        class_symbol = (row.get("Class Symbol") or "").strip()
        effective_date = _parse_date(row.get("Effective Date") or "")
        eff_conf = (row.get("Effective Date Confidence") or "").strip() or None
        delaying = (row.get("Delaying Amendment") or "").strip().upper() == "Y"
        prospectus_name = (row.get("Prospectus Name") or "").strip() or None
        extracted_from = (row.get("Extracted From") or "").strip() or None

        fe = FundExtraction(
            filing_id=filing.id,
            series_id=series_id or None,
            series_name=series_name or None,
            class_contract_id=class_id or None,
            class_contract_name=class_name or None,
            class_symbol=class_symbol or None,
            extracted_from=extracted_from,
            effective_date=effective_date,
            effective_date_confidence=eff_conf,
            delaying_amendment=delaying,
            prospectus_name=prospectus_name,
        )
        db.add(fe)
        extractions_count += 1

        # Lightweight rollup key — step4 is still the full-rollup authority,
        # but we can at least record the most recent filing touching this
        # series so the dashboard doesn't lag a day behind the watcher.
        touched_series[(series_id, class_id)] = {
            "series_name": series_name,
            "class_name": class_name,
            "class_symbol": class_symbol,
            "prospectus_name": prospectus_name,
            "effective_date": effective_date,
            "eff_conf": eff_conf,
        }

    # Lightweight FundStatus update — only latest_form / latest_filing_date /
    # prospectus_link. The authoritative status computation stays in step4.
    for (sid, cid), info in touched_series.items():
        fund_name = info["class_name"] or info["series_name"]
        if not fund_name:
            continue
        status_row = db.execute(
            select(FundStatus).where(
                FundStatus.trust_id == trust_id,
                FundStatus.series_id == (sid or None),
                FundStatus.class_contract_id == (cid or None),
            )
        ).scalar_one_or_none()

        if status_row:
            # Only overwrite if this filing is more recent
            if not status_row.latest_filing_date or (
                filing_date and filing_date >= status_row.latest_filing_date
            ):
                status_row.latest_form = form
                status_row.latest_filing_date = filing_date
                if prim_url:
                    status_row.prospectus_link = prim_url
                if info["prospectus_name"]:
                    status_row.prospectus_name = info["prospectus_name"]
                if info["effective_date"]:
                    status_row.effective_date = info["effective_date"]
                    status_row.effective_date_confidence = info["eff_conf"]
        else:
            db.add(FundStatus(
                trust_id=trust_id,
                series_id=sid or None,
                class_contract_id=cid or None,
                fund_name=fund_name,
                sgml_name=info["series_name"] or None,
                prospectus_name=info["prospectus_name"],
                ticker=info["class_symbol"] or None,
                status="PENDING",  # step4 will refine
                status_reason="watcher_atom: lightweight rollup pending step4",
                effective_date=info["effective_date"],
                effective_date_confidence=info["eff_conf"],
                latest_form=form,
                latest_filing_date=filing_date,
                prospectus_link=prim_url or None,
            ))

    return filing.id, extractions_count


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def enrich_alert(db: Session, alert, client: SECClient | None = None) -> EnrichResult:
    """Enrich one FilingAlert by fetching the filing and running step3 extraction.

    Mutates alert in-place (enrichment_status, enrichment_error, trust_id).
    Does NOT commit — the caller owns the transaction.

    Args:
        db: SQLAlchemy session
        alert: FilingAlert ORM row
        client: optional SECClient (created with default UA if None)

    Returns an EnrichResult.
    """
    if client is None:
        client = SECClient()

    accession = alert.accession_number
    cik = alert.cik

    if not cik:
        alert.enrichment_status = 2
        alert.enrichment_error = "alert has no CIK"
        return EnrichResult(ok=False, accession=accession,
                            error="alert has no CIK")

    try:
        # Step 1: resolve or create Trust
        trust_id, created, trust_name = resolve_or_create_trust(
            db, client, cik, fallback_name=alert.company_name,
        )
        alert.trust_id = trust_id

        # Step 2: look up the filing in the submissions JSON to get form type
        # (should match alert.form_type), filing date, and iXBRL flag.
        meta = _lookup_filing_metadata(client, cik, accession)
        if not meta:
            # Fallback: use what's on the alert directly. We can still run
            # header-only and SGML-based extraction without submissions JSON.
            form = alert.form_type
            filing_dt = alert.filed_date.isoformat() if alert.filed_date else ""
            primary_document = ""
            if alert.primary_doc_url:
                primary_document = alert.primary_doc_url.rsplit("/", 1)[-1]
            is_ixbrl = False
        else:
            form = meta["form"] or alert.form_type
            filing_dt = meta["filing_date"] or (
                alert.filed_date.isoformat() if alert.filed_date else ""
            )
            primary_document = meta["primary_document"]
            is_ixbrl = meta["is_ixbrl"]

        prim_url = build_primary_link(cik, accession, primary_document)
        if not prim_url and alert.primary_doc_url:
            prim_url = alert.primary_doc_url
        txt_url = build_submission_txt_link(cik, accession)

        # Step 3: run extraction using the existing step3 strategy dispatch
        rows = _run_extraction(
            client=client,
            form=form,
            filing_dt=filing_dt,
            cik=str(int(str(cik))),
            registrant=trust_name,
            accession=accession,
            prim_url=prim_url,
            txt_url=txt_url,
            is_ixbrl=is_ixbrl,
        )

        # Step 4: persist Filing + FundExtractions + lightweight FundStatus rollup
        filing_id, extractions_count = _write_extraction_to_db(
            db=db,
            trust_id=trust_id,
            cik=cik,
            accession=accession,
            form=form,
            filing_dt=filing_dt,
            prim_url=prim_url,
            txt_url=txt_url,
            registrant=trust_name,
            rows=rows,
        )

        alert.enrichment_status = 1
        alert.enrichment_error = None
        alert.processed = True

        return EnrichResult(
            ok=True,
            accession=accession,
            trust_id=trust_id,
            trust_created=created,
            filing_id=filing_id,
            extractions_count=extractions_count,
        )

    except Exception as exc:
        log.exception("enrich_alert failed for %s: %s", accession, exc)
        alert.enrichment_status = 2
        alert.enrichment_error = f"{type(exc).__name__}: {exc}"[:500]
        return EnrichResult(
            ok=False,
            accession=accession,
            error=str(exc),
        )
