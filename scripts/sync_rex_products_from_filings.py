"""Sync rex_products from new SEC filings.

Closes the lag between the ``filings`` table (fresh — populated nightly by the
SEC pipeline) and the ``rex_products`` table (the source for /operations/pipeline).
Before this script existed, newly-filed funds did not show up on the pipeline
page until a human manually ran ``scripts/insert_*.py`` for that batch.

What it does (three phases, all idempotent):

1. **INSERT new rex_products rows from new filings.**
   For each ``Filing`` with ``form in {485APOS, 485BPOS, 485BXT}`` and
   ``filing_date >= watermark``, if the filing's CIK is in the curated trust
   list (``etp_tracker.trusts.TRUST_CIKS``) and there's no existing rex_products
   row that matches it, insert one. Match priority (skip-or-create):

       a) (cik, series_id)             — strongest, when the filing has a series
       b) (cik, fund_name normalized)  — within the same trust
       c) (trust_id, fund_name normalized)

   Fund name is taken from ``FundExtraction.series_name`` if present (richer),
   else parsed from ``Filing.primary_document``. The trust string on the new
   row is the registrant. Suite is inferred from the fund name using the same
   ``_infer_suite`` rules as ``webapp.services.rex_product_sync`` (kept in
   sync — see the import).

2. **UPDATE existing rex_products on form transitions.**
   For each existing row that matched a new filing, advance ``latest_form`` /
   ``latest_prospectus_link`` if the new filing is a later-stage form. A
   485BPOS arriving flips status ``Filed -> Effective``; a 485BXT just bumps
   the prospectus link. Fields listed in ``manually_edited_fields`` are
   skipped (admin overrides win). Every change writes a row to
   ``capm_audit_log``.

3. **ACTIVATION from mkt_master_data.**
   For each ``status='Effective'`` row with a non-NULL ticker, look up the
   matching ``mkt_master_data`` row by ticker. If Bloomberg says
   ``market_status='ACTV'`` and ``inception_date`` is set, promote to
   ``status='Listed'`` and stamp ``official_listed_date`` from inception.

Watermark: a single ISO date in ``data/.sync_rex_products_watermark``. On
``--apply``, the script reads the watermark, syncs filings >= that date, and
writes today's date on success. Re-runs are safe — the in-script match check
suppresses duplicates regardless of the watermark.

Usage::

    python scripts/sync_rex_products_from_filings.py            # dry-run (default)
    python scripts/sync_rex_products_from_filings.py --dry-run
    python scripts/sync_rex_products_from_filings.py --apply    # writes; prompts "I AGREE"

Safeguards:
    * Default is dry-run. ``--apply`` requires a "I AGREE" stdin prompt.
    * ``--apply`` backs up ``data/etp_tracker.db`` to ``data/backups/`` first.
    * Every UPDATE writes to ``capm_audit_log`` for traceability.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import shutil
import sys
from dataclasses import dataclass, field
from datetime import date, datetime  # ADR 0014: timedelta dropped with the +75d guess
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

log = logging.getLogger("sync_rex_products_from_filings")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

ACCEPTED_FORMS = ("485APOS", "485BPOS", "485BXT")

# Form precedence by lifecycle stage, oldest-first:
#   485APOS (filed/in-review) -> 485BXT (DELAYS the pending amendment, states a
#   new effective date) -> 485BPOS (registration effective).
# 485BXT sits BETWEEN APOS and BPOS: it pushes a pending filing, it does NOT
# amend an already-effective prospectus. A 485BPOS arriving after a 485BXT must
# still register as later-stage (the prior code ranked BXT above BPOS, so a BPOS
# landing after a delay was silently ignored and the row never went Effective).
_FORM_RANK = {
    "485APOS": 1,
    "485BXT":  2,
    "485BPOS": 3,
}

# Default Rule 485(a) review window for 485APOS filings — used to seed
# ADR 0014: the +75d estimation (RULE_485A_DAYS) was DELETED. A missing effective
# date stays NULL truthfully; it is never fabricated as filing_date + N days.

# REX-name prefixes -- the PRIMARY filter for "is this a REX product?".
# We used to also accept "filing is from any curated trust" but that pulled in
# Direxion / ProShares / Defiance / Innovator / Roundhill / etc. because those
# competitor trusts live in TRUST_CIKS for filing-race monitoring. Curated
# trust now plays no role; only the fund/registrant name decides.
REX_NAME_PATTERNS = (
    re.compile(r"^T-?REX\b", re.IGNORECASE),
    # REX-Osprey variants: hyphen+space, optional TM, trademark glyph
    re.compile(r"^REX[\s\-]+\s*OSPREY", re.IGNORECASE),
    re.compile(r"^REX\s", re.IGNORECASE),
    re.compile(r"^MICROSECTORS\b", re.IGNORECASE),
    re.compile(r"^OSPREY\b", re.IGNORECASE),  # Osprey Bitcoin Trust (REX subsidiary)
)

# Hard denylist of competitor brand prefixes -- belt+suspenders against any
# future scenario where a competitor name happens to lead with "REX".
COMPETITOR_NAME_PATTERNS = (
    re.compile(r"^DIREXION\b", re.IGNORECASE),
    re.compile(r"^PROSHARES\b", re.IGNORECASE),
    re.compile(r"^DEFIANCE\b", re.IGNORECASE),
    re.compile(r"^INNOVATOR\b", re.IGNORECASE),
    re.compile(r"^ROUNDHILL\b", re.IGNORECASE),
    re.compile(r"^TRADR\b", re.IGNORECASE),
    re.compile(r"^GRANITESHARES\b", re.IGNORECASE),
    re.compile(r"^AMPLIFY\b", re.IGNORECASE),
    re.compile(r"^KODEX\b", re.IGNORECASE),
    re.compile(r"^CORGI\b", re.IGNORECASE),
    re.compile(r"^TUTTLE\b", re.IGNORECASE),
    re.compile(r"^VOLATILITY\s+SHARES\b", re.IGNORECASE),
    re.compile(r"^GLOBAL\s+X\b", re.IGNORECASE),
    re.compile(r"^FIRST\s+TRUST\b", re.IGNORECASE),
    re.compile(r"^THEMES\b", re.IGNORECASE),
    re.compile(r"^GSR\b", re.IGNORECASE),
    re.compile(r"^TIDAL\b", re.IGNORECASE),
    re.compile(r"^HEDGEYE\b", re.IGNORECASE),
)

WATERMARK_FILE = PROJECT_ROOT / "data" / ".sync_rex_products_watermark"
BACKUPS_DIR = PROJECT_ROOT / "data" / "backups"
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"

# Source tag written into ClassificationAuditLog / capm_audit_log so an
# operator can trace exactly which script touched a value.
CHANGED_BY = "sync_rex_products_from_filings_2026-05-13"


# ---------------------------------------------------------------------------
# Helpers — fund-name extraction + suite inference (kept local on purpose).
# We reuse the existing _infer_suite from rex_product_sync so suite logic
# stays in one place.
# ---------------------------------------------------------------------------

from webapp.services.rex_product_sync import _infer_suite  # noqa: E402


def _normalize_name(raw: str | None) -> str:
    """Uppercase + collapse whitespace for fund-name matching."""
    if not raw:
        return ""
    return " ".join(str(raw).upper().split())


def _fund_name_from_filing(filing, extraction) -> str:
    """Best-available fund name for a filing.

    Priority:
        1. FundExtraction.series_name (rich, parsed from filing body)
        2. Filing.registrant (trust-level — last resort)
    """
    if extraction is not None and extraction.series_name:
        return extraction.series_name.strip()
    # primary_document is typically a filename like "trex2xlongnvda.htm" —
    # not a usable display name. registrant is the only readable fallback.
    return (filing.registrant or "Unknown Fund").strip()


def _is_rex_name(name: str | None) -> bool:
    if not name:
        return False
    s = name.strip()
    if any(p.match(s) for p in COMPETITOR_NAME_PATTERNS):
        return False
    return any(p.match(s) for p in REX_NAME_PATTERNS)


def _later_form(old: str | None, new: str | None) -> bool:
    """Return True if ``new`` is a later-stage form than ``old``."""
    return _FORM_RANK.get(new or "", 0) > _FORM_RANK.get(old or "", 0)


# Lifecycle rank for rex_products.status. Mirrors status_reconciler's
# _LIFECYCLE_ORDER but in the Title-case vocabulary rex_products uses. Sync only
# ever PROMOTES along this order; demotions/corrections are the reconciler's job
# (it has the Bloomberg/8-A12B evidence sync lacks). This keeps the reconciler's
# Phase-1 corrections from being reverted on the next sync.
_STATUS_RANK = {
    "Under Consideration": 0,
    "Target List": 1,
    "Filed": 2,
    "Delayed": 3,
    "Effective": 4,
    "Listed": 5,
    "Delisted": 6,
}


def _form_status(form: str | None, eff_date: date | None, today: date) -> str:
    """Status implied by a freshly-seen filing, per the corrected effectiveness
    model (two separate legal events — registration-effective vs listed).

    - 485BPOS = registration effective, but only once its stated effective date
      has arrived. A future-dated BPOS is still 'Filed' (registered, not yet
      effective), NOT 'Effective'.
    - 485BXT = delays a pending amendment to a stated future date. It is still on
      file and not yet effective, so it is 'Filed' (Ryu: no 'Delayed' status).
    - 485APOS (or anything else) = on file / in review -> 'Filed'.

    Listing (-> 'Listed') requires an 8-A12B + ticker + first trade and is never
    inferred from a 485 form here.
    """
    if form == "485BPOS":
        if eff_date is not None and eff_date <= today:
            return "Effective"
        return "Filed"
    if form == "485BXT":
        return "Filed"
    return "Filed"


def _parse_inception(raw) -> date | None:
    """Parse mkt_master_data.inception_date — Strings in many formats."""
    if not raw:
        return None
    s = str(raw).strip()
    if not s or s.lower() in ("none", "null", "nan"):
        return None
    try:
        return date.fromisoformat(s[:10])
    except (ValueError, TypeError):
        pass
    for fmt in ("%Y/%m/%d", "%m/%d/%Y", "%d-%b-%y", "%d-%b-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return None


def _parse_overrides(raw: str | None) -> set[str]:
    """Parse rex_products.manually_edited_fields (JSON list) -> set."""
    if not raw:
        return set()
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return set()
    return {str(x) for x in parsed if isinstance(x, str)} if isinstance(parsed, list) else set()


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------

@dataclass
class SyncStats:
    filings_scanned: int = 0
    new_products_inserted: int = 0
    new_products_planned: int = 0  # dry-run only
    form_transitions: int = 0
    effective_date_updates: int = 0
    status_promotions: int = 0
    listed_promotions: int = 0
    vanished_count: int = 0   # Phase 4: tickers absent from mkt_master_data
    skipped_admin_override: int = 0
    skipped_already_matched: int = 0
    by_date: dict[str, int] = field(default_factory=dict)
    by_trust: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Watermark
# ---------------------------------------------------------------------------

def read_watermark() -> date:
    """Read last-synced filing_date watermark.

    ORCH-03/08 fix (2026-06-08): if the watermark file is missing, write
    today's date and use it — fresh installs no longer default to
    2026-04-01 and re-process ~2 months of filings on first run. Use
    ``--init-watermark`` to set the watermark explicitly without scanning,
    and ``--since YYYY-MM-DD`` to override on a single run.
    """
    if not WATERMARK_FILE.exists():
        today = date.today()
        log.warning(
            "watermark missing; initialising to today (%s) — use --since to "
            "backfill explicitly", today.isoformat(),
        )
        write_watermark(today)
        return today
    try:
        raw = WATERMARK_FILE.read_text(encoding="utf-8").strip()
        return date.fromisoformat(raw[:10])
    except (OSError, ValueError) as e:
        today = date.today()
        log.warning(
            "watermark unreadable (%s); resetting to today (%s)",
            e, today.isoformat(),
        )
        write_watermark(today)
        return today


def write_watermark(d: date) -> None:
    WATERMARK_FILE.parent.mkdir(parents=True, exist_ok=True)
    WATERMARK_FILE.write_text(d.isoformat() + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Audit log helper
# ---------------------------------------------------------------------------

def _audit(db, action: str, row_id: int, field_name: str,
           old_value, new_value, row_label: str) -> None:
    """Append a row to capm_audit_log. Silently no-ops on any DB error."""
    from webapp.models import CapMAuditLog

    def _strify(v):
        if v is None:
            return None
        if isinstance(v, (date, datetime)):
            return v.isoformat()
        return str(v)

    try:
        db.add(CapMAuditLog(
            action=action,
            table_name="rex_products",
            row_id=row_id,
            field_name=field_name,
            old_value=_strify(old_value),
            new_value=_strify(new_value),
            row_label=row_label,
            changed_by=CHANGED_BY,
        ))
    except Exception as e:
        log.debug("audit skipped (%s/%s): %s", row_id, field_name, e)


# ---------------------------------------------------------------------------
# Core phases
# ---------------------------------------------------------------------------

def _build_indexes(db):
    """Pre-load rex_products into match indexes.

    Returns three dicts:
        by_cik_series : (cik, series_id) -> RexProduct
        by_cik_name   : (cik, normalized_name) -> RexProduct
        by_trust_name : (trust_string_lowered, normalized_name) -> RexProduct
    """
    from webapp.models import RexProduct

    products = db.query(RexProduct).all()
    by_cik_series: dict[tuple, object] = {}
    by_cik_name: dict[tuple, object] = {}
    by_trust_name: dict[tuple, object] = {}

    for p in products:
        cik = (p.cik or "").lstrip("0") or None
        name_n = _normalize_name(p.name)
        trust_n = (p.trust or "").strip().lower()

        if cik and p.series_id:
            by_cik_series[(cik, p.series_id)] = p
        if cik and name_n:
            by_cik_name[(cik, name_n)] = p
        if trust_n and name_n:
            by_trust_name[(trust_n, name_n)] = p

    return by_cik_series, by_cik_name, by_trust_name


def _find_existing(filing, fund_name: str, extraction,
                   by_cik_series, by_cik_name, by_trust_name):
    """Apply the three-tier match priority. Returns the RexProduct or None."""
    cik = (filing.cik or "").lstrip("0") or None
    name_n = _normalize_name(fund_name)
    trust_n = (filing.registrant or "").strip().lower()

    series_id = extraction.series_id if extraction is not None else None

    if cik and series_id:
        hit = by_cik_series.get((cik, series_id))
        if hit:
            return hit
    if cik and name_n:
        hit = by_cik_name.get((cik, name_n))
        if hit:
            return hit
    if trust_n and name_n:
        hit = by_trust_name.get((trust_n, name_n))
        if hit:
            return hit
    return None


def phase1_2_sync_filings(db, since: date, dry_run: bool,
                           trust_ciks: set[str]) -> SyncStats:
    """Phase 1 + 2: insert new rex_products, advance existing on form change."""
    from sqlalchemy import select
    from webapp.models import Filing, FundExtraction, RexProduct

    stats = SyncStats()

    # Pull all candidate filings in one query.
    filings = db.execute(
        select(Filing).where(
            Filing.form.in_(ACCEPTED_FORMS),
            Filing.filing_date >= since,
        ).order_by(Filing.filing_date.asc(), Filing.id.asc())
    ).scalars().all()
    stats.filings_scanned = len(filings)

    if not filings:
        return stats

    # Pre-load FundExtraction rows for these filings keyed by filing_id.
    # Bug A fix (2026-06-02): a single 485APOS can carry multiple distinct
    # series (e.g. 3 sister funds in one prospectus). The old code kept ONE
    # extraction per filing, which collapsed those into a single rex_products
    # row. Now we collect ALL extractions per filing and create one
    # rex_products row per DISTINCT series_id. Extractions with no series_id
    # (~7,588 mostly old 497s) fall back to name-based matching.
    filing_ids = [f.id for f in filings]
    extractions_by_filing: dict[int, list[object]] = {}
    if filing_ids:
        CHUNK = 500
        for i in range(0, len(filing_ids), CHUNK):
            chunk = filing_ids[i: i + CHUNK]
            rows = db.execute(
                select(FundExtraction).where(FundExtraction.filing_id.in_(chunk))
            ).scalars().all()
            for ext in rows:
                extractions_by_filing.setdefault(ext.filing_id, []).append(ext)

    by_cik_series, by_cik_name, by_trust_name = _build_indexes(db)

    today = date.today()

    for f in filings:
        cik_norm = (f.cik or "").lstrip("0") or None
        in_curated_trust = bool(cik_norm and cik_norm in trust_ciks)
        _ = in_curated_trust  # retained for future audit/log use

        # Group this filing's extractions by distinct series_id. Each distinct
        # series becomes one (filing, representative-extraction) work unit.
        # Multiple classes (C-IDs) under the same series collapse to the first;
        # extractions with no series_id are handled separately via name-match.
        exts_all = extractions_by_filing.get(f.id, [])
        ext_by_series: dict[str, object] = {}
        ext_no_series: list[object] = []
        class_ids_by_series: dict[str, list[str]] = {}
        for ext in exts_all:
            if ext.series_id:
                if ext.series_id not in ext_by_series:
                    ext_by_series[ext.series_id] = ext
                if ext.class_contract_id:
                    class_ids_by_series.setdefault(ext.series_id, []).append(
                        ext.class_contract_id)
            else:
                ext_no_series.append(ext)

        # Build the ordered work-units: distinct series first (sorted for
        # deterministic order — series #1 keeps prospectus_name, series>=2
        # get it nulled), then no-series extractions, then (if nothing at
        # all) a single None placeholder so registrant-only filings still
        # get processed.
        work_units: list[tuple[object | None, int]] = []
        for idx, sid in enumerate(sorted(ext_by_series.keys())):
            work_units.append((ext_by_series[sid], idx))
        for ext in ext_no_series:
            work_units.append((ext, -1))  # -1 = no-series, name-match path
        if not work_units:
            work_units.append((None, 0))

        for ext, series_index in work_units:
            fund_name = _fund_name_from_filing(f, ext)

            rex_name = _is_rex_name(fund_name) or _is_rex_name(f.registrant)
            # Name-based filter ONLY. Curated-trust is for filing-race monitoring,
            # NOT for "this is a REX product." Without this check we leaked
            # Direxion / ProShares / Defiance funds into rex_products.
            if not rex_name:
                continue

            # Null prospectus_name on the 2nd+ distinct series within this
            # filing — the prospectus is filing-level, attributing it to
            # every series duplicates the same document across rows.
            if ext is not None and series_index >= 1 and ext.prospectus_name:
                ext.prospectus_name = None

            existing = _find_existing(f, fund_name, ext,
                                       by_cik_series, by_cik_name, by_trust_name)

            if existing is None:
                # ----- Phase 1: INSERT new row -----
                # ADR 0014: the effective date comes ONLY from the parsed extraction
                # (fund_extractions.effective_date). No filing_date+N estimation — a
                # missing date stays NULL truthfully and is propagated/repaired at the
                # source by propagate_effective_dates.py (latest 485 per series wins).
                est_eff: date | None = None
                if ext is not None and ext.effective_date:
                    est_eff = ext.effective_date
                # Status from the corrected model (date-gated effective; BXT=delayed).
                new_status = _form_status(f.form, est_eff, today)

                # When a series has multiple classes (C-IDs), join with "|" so
                # the row records every class under this series. Single-class
                # series just store the lone C-ID.
                cc_ids = None
                if ext is not None and ext.series_id:
                    cc_list = class_ids_by_series.get(ext.series_id) or []
                    if cc_list:
                        cc_ids = "|".join(cc_list) if len(cc_list) > 1 else cc_list[0]
                if cc_ids is None and ext is not None:
                    cc_ids = ext.class_contract_id

                payload = dict(
                    name=(fund_name or "Unknown Fund")[:200],
                    trust=(f.registrant or "")[:200] or None,
                    product_suite=_infer_suite(fund_name or ""),
                    status=new_status,
                    cik=cik_norm,
                    series_id=(ext.series_id if ext is not None else None),
                    class_contract_id=cc_ids,
                    latest_form=f.form,
                    latest_prospectus_link=f.primary_link,
                    initial_filing_date=f.filing_date,
                    estimated_effective_date=est_eff,
                    notes=f"auto-created by sync_rex_products_from_filings on {today.isoformat()}",
                )

                if dry_run:
                    stats.new_products_planned += 1
                    d_key = (f.filing_date.isoformat() if f.filing_date else "unknown")
                    stats.by_date[d_key] = stats.by_date.get(d_key, 0) + 1
                    tk = (f.registrant or "Unknown")[:60]
                    stats.by_trust[tk] = stats.by_trust.get(tk, 0) + 1
                    continue

                new_product = RexProduct(**payload)
                db.add(new_product)
                db.flush()  # populate id for audit logging
                stats.new_products_inserted += 1
                d_key = (f.filing_date.isoformat() if f.filing_date else "unknown")
                stats.by_date[d_key] = stats.by_date.get(d_key, 0) + 1
                tk = (f.registrant or "Unknown")[:60]
                stats.by_trust[tk] = stats.by_trust.get(tk, 0) + 1

                _audit(db, action="INSERT", row_id=new_product.id,
                       field_name="(row)", old_value=None,
                       new_value=fund_name,
                       row_label=fund_name[:60])

                # Register the new row in indexes so subsequent filings in the
                # same run don't double-insert it.
                name_n = _normalize_name(fund_name)
                trust_n = (f.registrant or "").strip().lower()
                if cik_norm and ext is not None and ext.series_id:
                    by_cik_series[(cik_norm, ext.series_id)] = new_product
                if cik_norm and name_n:
                    by_cik_name[(cik_norm, name_n)] = new_product
                if trust_n and name_n:
                    by_trust_name[(trust_n, name_n)] = new_product
                continue

            # ----- Phase 2: UPDATE existing -----
            stats.skipped_already_matched += 1
            overrides = _parse_overrides(existing.manually_edited_fields)
            row_label = (existing.ticker or existing.name or f"#{existing.id}")[:60]

            # Refresh estimated_effective_date from THIS filing's effective date.
            # Runs for EVERY matched 485-series filing, not only on form
            # transitions: a fund that files 485BXT after 485BXT (e.g. T-REX 2X
            # Long BITF) keeps extending its effective date, and the old code —
            # which updated the date only when a 485BPOS arrived — left those
            # rows stale. Filings are processed oldest-first, so the most recent
            # one wins. Admin overrides (manually_edited_fields) are respected.
            # ADR 0014: refresh only from the parsed extraction date; never guess
            # filing_date+N. A series with no parsed date stays NULL until a real
            # election/effective date is parsed.
            _new_eff = None
            if ext is not None and ext.effective_date:
                _new_eff = ext.effective_date
            if (_new_eff and "estimated_effective_date" not in overrides
                    and str(existing.estimated_effective_date) != str(_new_eff)):
                stats.effective_date_updates += 1
                if not dry_run:
                    _audit(db, "UPDATE", existing.id, "estimated_effective_date",
                           existing.estimated_effective_date, _new_eff, row_label)
                    existing.estimated_effective_date = _new_eff

            if _later_form(existing.latest_form, f.form):
                stats.form_transitions += 1

                if "latest_form" not in overrides:
                    if not dry_run:
                        _audit(db, "UPDATE", existing.id, "latest_form",
                               existing.latest_form, f.form, row_label)
                        existing.latest_form = f.form
                else:
                    stats.skipped_admin_override += 1

                if "latest_prospectus_link" not in overrides and f.primary_link:
                    if not dry_run:
                        _audit(db, "UPDATE", existing.id, "latest_prospectus_link",
                               existing.latest_prospectus_link, f.primary_link, row_label)
                        existing.latest_prospectus_link = f.primary_link

                # Promote status along the lifecycle per the corrected model:
                # 485BXT -> Delayed, 485BPOS (eff date arrived) -> Effective.
                # PROMOTE ONLY — never demote. Demotions and evidence-based
                # corrections (ghost-Listed, Bloomberg ACTV/LIQU) belong to the
                # status_reconciler, which is the single authority for downgrades.
                # This is what keeps the reconciler's corrections from being
                # reverted the next time this filing's series is re-synced.
                if "status" not in overrides:
                    proposed = _form_status(f.form, existing.estimated_effective_date, today)
                    cur_rank = _STATUS_RANK.get(existing.status or "", 0)
                    new_rank = _STATUS_RANK.get(proposed, 0)
                    if new_rank > cur_rank:
                        if not dry_run:
                            _audit(db, "UPDATE", existing.id, "status",
                                   existing.status, proposed, row_label)
                            existing.status = proposed
                        stats.status_promotions += 1

    if not dry_run:
        db.commit()
        # Reflect planned -> inserted for symmetry in --apply output
        stats.new_products_planned = stats.new_products_inserted

    return stats


_NAME_BOILERPLATE = frozenset({
    "REX", "T-REX", "TREX", "REX-OSPREY", "OSPREY", "2X", "3X", "DAILY",
    "TARGET", "ETF", "ETN", "STRATEGY", "INCOMEMAX", "LONG", "INVERSE",
    "SHORT", "PREMIUM", "INCOME", "GROWTH", "&", "AND", "THE", "OF",
    "MICROSECTORS", "FUND", "TRUST", "FANG", "LEVERAGED",
})


def _name_tokens(name: str | None) -> set[str]:
    """Tokenise a fund name, drop boilerplate, return uppercase set."""
    if not name:
        return set()
    return {t.upper().strip(",.()-") for t in name.split() if t.strip()} - _NAME_BOILERPLATE


def _names_overlap(a: str | None, b: str | None) -> bool:
    """True if two fund names share at least one meaningful (non-boilerplate)
    token. Used in Phase 3 to validate a ticker match before promoting status.

    Recycled-ticker case (Bug 2): SEC reassigns TSII from "TSM Growth & Income"
    to "TSLA Growth & Income". Ticker matches but fund names share no
    meaningful tokens — return False so we don't promote the wrong row.
    """
    ta, tb = _name_tokens(a), _name_tokens(b)
    if not ta or not tb:
        # Fail-open when either side is missing — older rex_products rows
        # may lack a name; ticker match alone is acceptable there.
        return True
    return bool(ta & tb)


def phase3_activate_from_market(db, dry_run: bool) -> SyncStats:
    """Promote ``status='Effective'`` rex_products to 'Listed' when Bloomberg
    says the ticker is ACTV AND inception date is sane AND fund name overlaps.

    Sanity gates added 2026-05-19 (Phase 0b triage patches for Bugs 2 + 3):

    1. Fund-name overlap (Bug 2 — TSII recycling case): rex_products.name
       must share at least one non-boilerplate token with mkt_master_data
       fund_name. Prevents promoting the wrong rex row when SEC has recycled
       a ticker across two unrelated funds.

    2. Inception-date sanity (Bug 3 — placeholder dates): the parsed
       inception_date must be ON OR AFTER the rex_products.initial_filing_date
       AND within the last 60 calendar days. Catches bulk-seeded placeholder
       dates (e.g., a dozen products all stamped 2026-02-18 before they
       actually traded).

    Returns a SyncStats with only listed_promotions filled in.
    """
    from sqlalchemy import select
    from webapp.models import RexProduct, MktMasterData

    stats = SyncStats()

    eff_rows = db.execute(
        select(RexProduct).where(
            RexProduct.status == "Effective",
            RexProduct.ticker.is_not(None),
        )
    ).scalars().all()

    if not eff_rows:
        return stats

    # Build ticker -> (market_status, inception, fund_name) index from mkt_master_data
    mkt_rows = db.execute(select(MktMasterData)).scalars().all()
    mkt_index: dict[str, tuple[str | None, str | None, str | None]] = {}
    for m in mkt_rows:
        if not m.ticker:
            continue
        key = m.ticker.strip().upper().replace(" US", "")
        mkt_index[key] = (m.market_status, m.inception_date, m.fund_name)

    today = date.today()
    skipped_name_mismatch = 0
    skipped_stale_inception = 0
    skipped_pre_filing = 0

    for p in eff_rows:
        ticker_n = (p.ticker or "").strip().upper().replace(" US", "")
        info = mkt_index.get(ticker_n)
        if not info:
            continue
        mkt_status, inception_raw, mkt_name = info
        if (mkt_status or "").upper() != "ACTV":
            continue
        inc = _parse_inception(inception_raw)
        if inc is None:
            continue

        # Bug 2 patch: fund-name cross-validation before ticker-based promotion
        if not _names_overlap(p.name, mkt_name):
            skipped_name_mismatch += 1
            log.info(
                "phase3 skip name-mismatch: rex='%s' (%s) vs mkt='%s' (%s)",
                (p.name or "")[:50], p.ticker, (mkt_name or "")[:50], ticker_n,
            )
            continue

        # Bug 3 patch: inception-date sanity (must be > filing and within 60d)
        if p.initial_filing_date and inc < p.initial_filing_date:
            skipped_pre_filing += 1
            log.info(
                "phase3 skip inception-before-filing: %s inc=%s filed=%s",
                p.ticker, inc, p.initial_filing_date,
            )
            continue
        if (today - inc).days > 60:
            skipped_stale_inception += 1
            log.info(
                "phase3 skip stale-inception: %s inc=%s (%d days old)",
                p.ticker, inc, (today - inc).days,
            )
            continue

        overrides = _parse_overrides(p.manually_edited_fields)
        row_label = (p.ticker or p.name or f"#{p.id}")[:60]

        if "status" not in overrides:
            if not dry_run:
                _audit(db, "UPDATE", p.id, "status", p.status, "Listed", row_label)
                p.status = "Listed"
            stats.listed_promotions += 1

        if "official_listed_date" not in overrides and not p.official_listed_date:
            if not dry_run:
                _audit(db, "UPDATE", p.id, "official_listed_date",
                       p.official_listed_date, inc, row_label)
                p.official_listed_date = inc

    if not dry_run:
        db.commit()

    if skipped_name_mismatch or skipped_stale_inception or skipped_pre_filing:
        log.info(
            "phase3 sanity skips: name-mismatch=%d stale-inception=%d pre-filing=%d",
            skipped_name_mismatch, skipped_stale_inception, skipped_pre_filing,
        )

    return stats


def phase4_demote_vanished_from_market(db, dry_run: bool) -> SyncStats:
    """Demote ``status='Listed'`` rex_products whose ticker has completely
    disappeared from mkt_master_data (BMAX US case — Bloomberg reclaimed
    the ticker after delisting, so we get no LIQU signal — the row is just
    gone).

    Doesn't auto-flip to Delisted (rarely there are transient drop-outs).
    Instead it logs a warning so the morning summary surfaces the candidate.
    Auto-demotion behind an explicit opt-in flag .auto_demote_vanished — see
    DECISIONS/0005-vanished-from-bloomberg.md (proposed).

    Returns a SyncStats with vanished_count populated.
    """
    from sqlalchemy import select
    from webapp.models import RexProduct, MktMasterData

    stats = SyncStats()

    listed_rows = db.execute(
        select(RexProduct).where(
            RexProduct.status == "Listed",
            RexProduct.ticker.is_not(None),
        )
    ).scalars().all()

    if not listed_rows:
        return stats

    mkt_tickers = {
        m.ticker.strip().upper().replace(" US", "")
        for m in db.execute(select(MktMasterData.ticker)).all()
        if m[0]
    }

    # Phase 7 Part B Stage 2 (ADR 0010): DB-first flag read; legacy file fallback.
    try:
        from webapp.services.system_flags import get_flag
        auto_demote = get_flag("auto_demote_vanished")
    except ImportError:
        auto_demote = (PROJECT_ROOT / "data" / ".auto_demote_vanished").exists()

    vanished = []
    for p in listed_rows:
        ticker_n = (p.ticker or "").strip().upper().replace(" US", "")
        if ticker_n in mkt_tickers:
            continue
        vanished.append(p)
        log.warning(
            "phase4 ticker vanished from Bloomberg: %s (%s) — rex still Listed",
            p.ticker, (p.name or "")[:60],
        )
        if auto_demote:
            overrides = _parse_overrides(p.manually_edited_fields)
            if "status" not in overrides:
                if not dry_run:
                    _audit(db, "UPDATE", p.id, "status", p.status, "Delisted",
                           (p.ticker or p.name or f"#{p.id}")[:60])
                    p.status = "Delisted"

    if not dry_run and auto_demote and vanished:
        db.commit()

    stats.vanished_count = len(vanished)
    return stats


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------

def _backup_db() -> Path | None:
    if not DB_PATH.exists():
        return None
    BACKUPS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    dst = BACKUPS_DIR / f"etp_tracker_{stamp}_pre_sync_rex_products.db"
    shutil.copy2(DB_PATH, dst)
    # Rotation: keep the 3 most-recent pre_sync snapshots only. Without this
    # the fresh-poller (15-min cadence) accumulates 50+ snapshots/day at
    # 633MB each and fills the VPS disk in 12-18 hours. 3 keepers = ~2GB cap.
    try:
        snapshots = sorted(
            BACKUPS_DIR.glob("etp_tracker_*_pre_sync_rex_products.db"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for old in snapshots[3:]:
            try:
                old.unlink()
            except OSError:
                pass
    except Exception:
        pass
    return dst


def _confirm_apply() -> bool:
    print("\n--apply will modify data/etp_tracker.db.")
    print("Type 'I AGREE' to proceed (anything else aborts):")
    try:
        line = input().strip()
    except EOFError:
        return False
    return line == "I AGREE"


def _print_report(stats1: SyncStats, stats3: SyncStats, since: date,
                   dry_run: bool) -> None:
    print("\n=== sync_rex_products_from_filings ===")
    print(f"Mode             : {'DRY-RUN' if dry_run else 'APPLY'}")
    print(f"Watermark (since): {since.isoformat()}")
    print(f"Filings scanned  : {stats1.filings_scanned}")
    print(f"New rex_products : {stats1.new_products_planned}")
    print(f"Form transitions : {stats1.form_transitions}")
    print(f"Eff-date updates : {stats1.effective_date_updates}")
    print(f"Status promotions: {stats1.status_promotions}")
    print(f"Skipped (already): {stats1.skipped_already_matched}")
    print(f"Skipped (admin)  : {stats1.skipped_admin_override}")
    print(f"Listed (Phase 3) : {stats3.listed_promotions}")

    if stats1.by_date:
        print("\nProposals by filing_date (top 20):")
        for d, n in sorted(stats1.by_date.items(), key=lambda kv: kv[0], reverse=True)[:20]:
            print(f"  {d}  {n}")

    if stats1.by_trust:
        print("\nProposals by trust (top 10):")
        for t, n in sorted(stats1.by_trust.items(), key=lambda kv: -kv[1])[:10]:
            print(f"  {n:>4}  {t}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true", default=False,
                        help="Show proposed changes without writing (default).")
    parser.add_argument("--apply", action="store_true", default=False,
                        help="Write changes after 'I AGREE' confirmation.")
    parser.add_argument("--since", default=None,
                        help="Override watermark (YYYY-MM-DD).")
    parser.add_argument("--no-prompt", action="store_true", default=False,
                        help="With --apply, skip the 'I AGREE' prompt "
                             "(for daily-cron use after preflight checks).")
    parser.add_argument("--init-watermark", action="store_true", default=False,
                        help="Write today's date to the watermark file and "
                             "exit without scanning. Use on fresh installs "
                             "to avoid re-processing historical filings.")
    args = parser.parse_args(argv)

    # --init-watermark: short-circuit before any DB work.
    if args.init_watermark:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        )
        today = date.today()
        write_watermark(today)
        print(f"Watermark initialised -> {today.isoformat()}")
        print(f"File: {WATERMARK_FILE}")
        return 0

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Default to dry-run unless --apply
    dry_run = not args.apply
    if args.dry_run:
        dry_run = True

    if not dry_run and not args.no_prompt:
        if not _confirm_apply():
            print("Aborted (no 'I AGREE').")
            return 2

    if not dry_run:
        backup = _backup_db()
        if backup is not None:
            print(f"Backup: {backup}")

    since: date
    if args.since:
        since = date.fromisoformat(args.since)
    else:
        since = read_watermark()

    from webapp.database import init_db, SessionLocal
    from etp_tracker.trusts import TRUST_CIKS
    init_db()

    # Normalize TRUST_CIKS keys to lstripped form (the curated dict uses
    # un-padded numeric strings already, but be defensive).
    trust_ciks_norm = {str(k).lstrip("0") for k in TRUST_CIKS.keys()}

    db = SessionLocal()
    try:
        stats1 = phase1_2_sync_filings(db, since=since, dry_run=dry_run,
                                         trust_ciks=trust_ciks_norm)
        stats3 = phase3_activate_from_market(db, dry_run=dry_run)
        stats4 = phase4_demote_vanished_from_market(db, dry_run=dry_run)
    finally:
        db.close()

    _print_report(stats1, stats3, since, dry_run)
    if stats4.vanished_count:
        print(f"\nPhase 4 (vanished from Bloomberg): {stats4.vanished_count} "
              f"Listed ticker(s) absent from mkt_master_data. "
              f"See logs; auto-demote behind .auto_demote_vanished flag.")

    if not dry_run:
        write_watermark(date.today())
        print(f"\nWatermark updated -> {date.today().isoformat()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
