"""Tier 3: Daily SEC form index reconciler + REX lifecycle reconciler.

Two responsibilities:

1. **SEC daily-index safety net** for the atom watcher (Tier 1). Once a day,
   pull the SEC's authoritative daily filing index and diff it against the
   filing_alerts table. Any filings the atom feed missed (rate-limit spikes,
   parse errors, outages) are inserted as new alerts with source='reconciler',
   which Tier 2 (single_filing_worker) then enriches.

2. **REX lifecycle reconciliation** — tie rex_products rows back to the SEC
   filings that produced them (multi-key match), promote PEND market-status
   rows whose inception has passed, and backfill missing CIKs on rex_products
   from the trusts table. Logs a structured stats line.

Index URL format:
    https://www.sec.gov/Archives/edgar/daily-index/{YYYY}/QTR{N}/form.{YYYYMMDD}.idx

Note: SEC does not publish indexes on weekends/holidays. reconcile_day()
returns zero counts for those days.

Run as systemd oneshot:
    /home/jarvis/venv/bin/python -m etp_tracker.reconciler

CLI flags:
    --dry-run        Compute stats but do not write to DB.
    --days N         How many days of SEC index to fetch (default 3).

Issue refs: closes #138 (4.4% match rate), #104 (PEND/ACTV promotion).
"""
from __future__ import annotations

import logging
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

USER_AGENT = os.environ.get(
    "SEC_USER_AGENT", "REX-ETP-Tracker/2.0 (relasmar@rexfin.com)"
)

# Same set as atom_watcher.py FORM_QUERIES — keep in sync.
# S-1/S-3 excluded: they include every non-fund operating company's
# prospectus (Devon Energy, Lennar Homes, etc.). Rare commodity ETPs
# that use S-1 (e.g. Grayscale BTC) get added manually via admin.
ACCEPTED_FORMS = {
    "485APOS", "485BPOS", "485BXT", "485B", "485A",
    "497", "497K", "497J",
    "N-1A", "N-1A/A",
    "N-2", "N-2/A",
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
# REX lifecycle reconciliation
# ---------------------------------------------------------------------------

# Match window for tying rex_products rows back to filings. The previous
# 3-day window left ~95% of rex_products unmatched because most filings
# were made weeks before the row was inspected.
REX_FILING_MATCH_WINDOW_DAYS = 90


def _normalize_ticker(raw) -> str:
    """Uppercase + strip exchange suffix (' US', ':US', etc.) and whitespace.

    Examples:
        'NVDX'        -> 'NVDX'
        'NVDX US'     -> 'NVDX'
        'nvdx us'     -> 'NVDX'
        ' BRKB '      -> 'BRKB'
    """
    if not raw:
        return ""
    s = str(raw).strip().upper()
    # Strip trailing exchange suffixes
    for suffix in (" US", ":US", " EQUITY", " UN", " UQ"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].strip()
    return s


def _normalize_issuer(raw) -> str:
    """Lowercase + collapse whitespace + strip common suffixes for fuzzy issuer match."""
    if not raw:
        return ""
    s = str(raw).lower().strip()
    s = re.sub(r"\s+", " ", s)
    # Drop common corporate suffixes that vary across sources
    for suffix in (" trust", " etf trust", " series trust", " etfs", " etf", ",", "."):
        s = s.replace(suffix, " ")
    return re.sub(r"\s+", " ", s).strip()


def _parse_inception(raw) -> date | None:
    """Parse mkt_master_data.inception_date — stored as String, often ISO or 'YYYY-MM-DD'."""
    if not raw:
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("none", "null", "nan"):
        return None
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        pass
    # Try common alternates
    for fmt in ("%Y/%m/%d", "%m/%d/%Y", "%d-%b-%y", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class RexMatchStats:
    """Counts from match_rex_products()."""
    total_rex_products: int = 0
    matched: int = 0
    matched_by_cik: int = 0
    matched_by_trust: int = 0
    matched_by_issuer: int = 0
    unmatched: int = 0


def match_rex_products(db, window_days: int = REX_FILING_MATCH_WINDOW_DAYS) -> RexMatchStats:
    """Match every rex_products row against the filings table using multi-key fallback.

    Match priority:
        1. (cik, ticker_normalized)               — strongest signal
        2. (trust name -> trust_id -> filings)    — when CIK is NULL but trust is set
        3. (issuer/trust name fuzzy)              — last-resort registrant LIKE match

    Date window: only filings within `window_days` (calendar days) of today
    are considered. The previous implementation used 3 days which is why the
    match rate collapsed to 4.4%.

    Read-only — does not mutate rex_products. Returns counts for logging.
    """
    from sqlalchemy import select, func
    from webapp.models import RexProduct, Trust, Filing

    stats = RexMatchStats()
    today = date.today()
    cutoff = today - timedelta(days=window_days)

    products = db.execute(select(RexProduct)).scalars().all()
    stats.total_rex_products = len(products)
    if not products:
        return stats

    # Pre-load filings within window into a dict-of-dicts for fast lookup.
    # filings_by_cik: cik -> list[Filing] (within window)
    filings_in_window = db.execute(
        select(Filing).where(
            Filing.filing_date >= cutoff,
        )
    ).scalars().all()

    filings_by_cik: dict[str, list] = {}
    filings_by_trust: dict[int, list] = {}
    filings_by_registrant: dict[str, list] = {}
    for f in filings_in_window:
        if f.cik:
            filings_by_cik.setdefault(str(int(f.cik)) if str(f.cik).isdigit() else str(f.cik), []).append(f)
        if f.trust_id is not None:
            filings_by_trust.setdefault(f.trust_id, []).append(f)
        if f.registrant:
            filings_by_registrant.setdefault(_normalize_issuer(f.registrant), []).append(f)

    # Build trust-name -> trust_id index for fallback 1
    trusts = db.execute(select(Trust)).scalars().all()
    trust_name_to_id: dict[str, int] = {}
    for t in trusts:
        trust_name_to_id[_normalize_issuer(t.name)] = t.id

    for p in products:
        cik_n = str(int(p.cik)) if (p.cik and str(p.cik).isdigit()) else (p.cik or "")
        ticker_n = _normalize_ticker(p.ticker)

        # Primary: CIK match (ticker not always present on filings table — use
        # CIK alone as the primary key, falling back through the chain only
        # when CIK is empty).
        if cik_n and cik_n in filings_by_cik:
            stats.matched += 1
            stats.matched_by_cik += 1
            continue

        # Fallback 1: trust_id via rex_products.trust (string) -> trusts table
        trust_norm = _normalize_issuer(p.trust)
        if trust_norm:
            trust_id = trust_name_to_id.get(trust_norm)
            if trust_id and trust_id in filings_by_trust:
                stats.matched += 1
                stats.matched_by_trust += 1
                continue

        # Fallback 2: fuzzy registrant match
        if trust_norm and trust_norm in filings_by_registrant:
            stats.matched += 1
            stats.matched_by_issuer += 1
            continue

        stats.unmatched += 1

    log.info(
        "match_rex_products: total=%d matched=%d (cik=%d trust=%d issuer=%d) unmatched=%d window=%dd",
        stats.total_rex_products, stats.matched,
        stats.matched_by_cik, stats.matched_by_trust, stats.matched_by_issuer,
        stats.unmatched, window_days,
    )
    return stats


@dataclass
class PromoteStats:
    """Counts from promote_pend_to_actv()."""
    candidates: int = 0
    promoted: int = 0


def promote_pend_to_actv(db, dry_run: bool = False) -> PromoteStats:
    """Flip mkt_master_data.market_status from 'PEND' to 'ACTV' where
    inception_date is in the past.

    Issue #104: 50 PEND rows have past inception dates not promoted to ACTV.

    Writes an audit row to classification_audit_log for each promotion so
    the change is traceable + rollback-able.

    Args:
        db: SQLAlchemy session
        dry_run: if True, identify candidates but do not write.

    Returns PromoteStats.
    """
    from sqlalchemy import select
    from webapp.models import MktMasterData, ClassificationAuditLog

    stats = PromoteStats()
    today = date.today()
    run_id = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")

    rows = db.execute(
        select(MktMasterData).where(MktMasterData.market_status == "PEND")
    ).scalars().all()

    for row in rows:
        inc = _parse_inception(row.inception_date)
        if inc is None or inc >= today:
            continue
        stats.candidates += 1
        if dry_run:
            continue

        old_value = row.market_status
        row.market_status = "ACTV"
        db.add(ClassificationAuditLog(
            sweep_run_id=run_id,
            ticker=row.ticker,
            column_name="market_status",
            old_value=old_value,
            new_value="ACTV",
            source="reconciler",
            confidence="HIGH",
            reason=f"inception_date {inc.isoformat()} < today {today.isoformat()}",
            dry_run=False,
        ))
        stats.promoted += 1

    if not dry_run and stats.promoted:
        db.commit()

    log.info(
        "promote_pend_to_actv: candidates=%d promoted=%d dry_run=%s",
        stats.candidates, stats.promoted, dry_run,
    )
    return stats


@dataclass
class BackfillCikStats:
    """Counts from backfill_missing_cik()."""
    candidates: int = 0
    backfilled: int = 0
    no_trust_match: int = 0


def backfill_missing_cik(db, dry_run: bool = False) -> BackfillCikStats:
    """Populate rex_products.cik from trusts table when NULL.

    Match key: rex_products.trust (string) -> trusts.name (case-insensitive,
    normalized). 118 rex_products rows currently have NULL cik AND non-NULL
    trust, which blocks downstream filing-match.

    Returns BackfillCikStats.
    """
    from sqlalchemy import select
    from webapp.models import RexProduct, Trust

    stats = BackfillCikStats()

    trusts = db.execute(select(Trust)).scalars().all()
    trust_name_to_cik: dict[str, str] = {
        _normalize_issuer(t.name): t.cik for t in trusts if t.cik
    }

    rows = db.execute(
        select(RexProduct).where(
            RexProduct.cik.is_(None),
            RexProduct.trust.is_not(None),
        )
    ).scalars().all()
    stats.candidates = len(rows)

    for row in rows:
        match_cik = trust_name_to_cik.get(_normalize_issuer(row.trust))
        if not match_cik:
            stats.no_trust_match += 1
            continue
        if dry_run:
            stats.backfilled += 1
            continue
        row.cik = match_cik
        stats.backfilled += 1

    if not dry_run and stats.backfilled:
        db.commit()

    log.info(
        "backfill_missing_cik: candidates=%d backfilled=%d no_trust_match=%d dry_run=%s",
        stats.candidates, stats.backfilled, stats.no_trust_match, dry_run,
    )
    return stats


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def run_recent(days_back: int = 3, dry_run: bool = False) -> None:
    """Reconcile the last N calendar days (default 3) + run REX lifecycle steps.

    Pipeline:
        1. SEC daily-index reconcile (fills filing_alerts gaps)
        2. backfill_missing_cik() on rex_products
        3. match_rex_products() — read-only match-rate audit
        4. promote_pend_to_actv() on mkt_master_data

    Idempotent: running twice does not duplicate filing_alerts rows (UNIQUE
    constraint on accession_number) and PEND->ACTV is one-way.
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
        # --- Step 1: SEC daily-index reconcile -------------------------------
        if not dry_run:
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

            try:
                r = reconcile_day(db, today)
                totals["fetched"] += r.fetched
                totals["parsed"] += r.parsed
                totals["matched"] += r.matched
                totals["new"] += r.new_inserted
            except Exception as e:
                log.exception("reconcile_day failed for %s: %s", today, e)
        else:
            log.info("run_recent: --dry-run, skipping SEC index fetch")

        # --- Step 2: backfill missing CIK on rex_products --------------------
        try:
            backfill_stats = backfill_missing_cik(db, dry_run=dry_run)
        except Exception as e:
            log.exception("backfill_missing_cik failed: %s", e)
            backfill_stats = BackfillCikStats()

        # --- Step 3: match rex_products against filings ----------------------
        try:
            match_stats = match_rex_products(db)
        except Exception as e:
            log.exception("match_rex_products failed: %s", e)
            match_stats = RexMatchStats()

        # --- Step 4: PEND -> ACTV promotion ----------------------------------
        try:
            promote_stats = promote_pend_to_actv(db, dry_run=dry_run)
        except Exception as e:
            log.exception("promote_pend_to_actv failed: %s", e)
            promote_stats = PromoteStats()

    finally:
        db.close()

    # Structured summary line — one line, key=value pairs, grep-able from
    # journalctl.
    match_rate = (
        f"{(match_stats.matched / match_stats.total_rex_products * 100):.1f}"
        if match_stats.total_rex_products else "0.0"
    )
    log.info(
        "reconciler_summary "
        "days_back=%d dry_run=%s "
        "sec_fetched=%d sec_parsed=%d sec_matched=%d sec_new=%d "
        "total_rex_products=%d matched=%d match_rate=%s%% "
        "matched_by_cik=%d matched_by_trust=%d matched_by_issuer=%d "
        "promoted_pend_to_actv=%d backfilled_cik=%d",
        days_back, dry_run,
        totals["fetched"], totals["parsed"], totals["matched"], totals["new"],
        match_stats.total_rex_products, match_stats.matched, match_rate,
        match_stats.matched_by_cik, match_stats.matched_by_trust,
        match_stats.matched_by_issuer,
        promote_stats.promoted, backfill_stats.backfilled,
    )


def _parse_argv(argv: list[str]) -> tuple[int, bool]:
    """Parse legacy positional + new flag form. Returns (days, dry_run)."""
    days = 3
    dry_run = False
    for arg in argv[1:]:
        if arg == "--dry-run":
            dry_run = True
        elif arg.startswith("--days="):
            try:
                days = int(arg.split("=", 1)[1])
            except (ValueError, IndexError):
                pass
        elif arg.isdigit():
            # Legacy positional: `python -m etp_tracker.reconciler 7`
            days = int(arg)
    return days, dry_run


if __name__ == "__main__":
    days, dry_run = _parse_argv(sys.argv)
    run_recent(days_back=days, dry_run=dry_run)
