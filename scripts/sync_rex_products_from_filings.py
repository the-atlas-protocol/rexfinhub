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
from datetime import date, datetime, timedelta
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

# Form precedence: a 485BPOS arriving is "later-stage" than a 485APOS that
# preceded it. 485BXT (sticker) is the latest — it amends an already-effective
# prospectus, so its arrival should never downgrade the status.
_FORM_RANK = {
    "485APOS": 1,
    "485BPOS": 2,
    "485BXT":  3,
}

# Default Rule 485(a) review window for 485APOS filings — used to seed
# estimated_effective_date when we can't see one in the filing yet.
RULE_485A_DAYS = 75

# REX-name prefixes used when the filing's CIK isn't in TRUST_CIKS but the
# registrant name looks like one of REX's funds (e.g. cross-trust REX-Osprey
# products filed via World Funds Trust or HANetf II ICAV).
REX_NAME_PATTERNS = (
    re.compile(r"^T-?REX\b", re.IGNORECASE),
    re.compile(r"^REX-OSPREY\b", re.IGNORECASE),
    re.compile(r"^REX\s", re.IGNORECASE),
    re.compile(r"^MICROSECTORS\b", re.IGNORECASE),
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
    return any(p.match(name.strip()) for p in REX_NAME_PATTERNS)


def _later_form(old: str | None, new: str | None) -> bool:
    """Return True if ``new`` is a later-stage form than ``old``."""
    return _FORM_RANK.get(new or "", 0) > _FORM_RANK.get(old or "", 0)


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
    status_promotions: int = 0
    listed_promotions: int = 0
    skipped_admin_override: int = 0
    skipped_already_matched: int = 0
    by_date: dict[str, int] = field(default_factory=dict)
    by_trust: dict[str, int] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Watermark
# ---------------------------------------------------------------------------

def read_watermark() -> date:
    """Read last-synced filing_date watermark; default to 2026-04-01 if absent.

    The default is intentionally a few weeks back — we want the first
    --apply run to surface ALL the missed May filings, not just today's.
    """
    if not WATERMARK_FILE.exists():
        return date(2026, 4, 1)
    try:
        raw = WATERMARK_FILE.read_text(encoding="utf-8").strip()
        return date.fromisoformat(raw[:10])
    except (OSError, ValueError) as e:
        log.warning("watermark unreadable (%s); defaulting to 2026-04-01", e)
        return date(2026, 4, 1)


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
    filing_ids = [f.id for f in filings]
    extractions_by_filing: dict[int, object] = {}
    if filing_ids:
        CHUNK = 500
        for i in range(0, len(filing_ids), CHUNK):
            chunk = filing_ids[i: i + CHUNK]
            rows = db.execute(
                select(FundExtraction).where(FundExtraction.filing_id.in_(chunk))
            ).scalars().all()
            for ext in rows:
                # If multiple extractions per filing, keep the first non-null series.
                cur = extractions_by_filing.get(ext.filing_id)
                if cur is None or (ext.series_id and not cur.series_id):
                    extractions_by_filing[ext.filing_id] = ext

    by_cik_series, by_cik_name, by_trust_name = _build_indexes(db)

    today = date.today()

    for f in filings:
        cik_norm = (f.cik or "").lstrip("0") or None
        in_curated_trust = bool(cik_norm and cik_norm in trust_ciks)
        ext = extractions_by_filing.get(f.id)
        fund_name = _fund_name_from_filing(f, ext)

        rex_name = _is_rex_name(fund_name) or _is_rex_name(f.registrant)
        # Per the brief: we accept curated-trust filings + REX-name filings
        # from non-curated trusts.  Non-curated, non-REX filings are skipped.
        if not (in_curated_trust or rex_name):
            continue

        existing = _find_existing(f, fund_name, ext,
                                   by_cik_series, by_cik_name, by_trust_name)

        if existing is None:
            # ----- Phase 1: INSERT new row -----
            new_status = "Effective" if f.form == "485BPOS" else "Filed"
            est_eff: date | None = None
            if ext is not None and ext.effective_date:
                est_eff = ext.effective_date
            elif f.form == "485APOS" and f.filing_date:
                est_eff = f.filing_date + timedelta(days=RULE_485A_DAYS)
            elif f.form == "485BPOS" and f.filing_date:
                est_eff = f.filing_date

            payload = dict(
                name=(fund_name or "Unknown Fund")[:200],
                trust=(f.registrant or "")[:200] or None,
                product_suite=_infer_suite(fund_name or ""),
                status=new_status,
                cik=cik_norm,
                series_id=(ext.series_id if ext is not None else None),
                class_contract_id=(ext.class_contract_id if ext is not None else None),
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

            # 485BPOS arriving on a 'Filed' row -> Effective
            if f.form == "485BPOS":
                if existing.status == "Filed" and "status" not in overrides:
                    if not dry_run:
                        _audit(db, "UPDATE", existing.id, "status",
                               existing.status, "Effective", row_label)
                        existing.status = "Effective"
                    stats.status_promotions += 1
                # When 485BPOS arrives, est_effective should reflect actual filing
                if "estimated_effective_date" not in overrides and f.filing_date:
                    if not dry_run:
                        _audit(db, "UPDATE", existing.id, "estimated_effective_date",
                               existing.estimated_effective_date, f.filing_date, row_label)
                        existing.estimated_effective_date = f.filing_date

    if not dry_run:
        db.commit()
        # Reflect planned -> inserted for symmetry in --apply output
        stats.new_products_planned = stats.new_products_inserted

    return stats


def phase3_activate_from_market(db, dry_run: bool) -> SyncStats:
    """Promote ``status='Effective'`` rex_products to 'Listed' when Bloomberg
    says the ticker is ACTV and has an inception date.

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

    # Build ticker -> (market_status, inception) index from mkt_master_data
    mkt_rows = db.execute(select(MktMasterData)).scalars().all()
    mkt_index: dict[str, tuple[str | None, str | None]] = {}
    for m in mkt_rows:
        if not m.ticker:
            continue
        key = m.ticker.strip().upper().replace(" US", "")
        mkt_index[key] = (m.market_status, m.inception_date)

    for p in eff_rows:
        ticker_n = (p.ticker or "").strip().upper().replace(" US", "")
        info = mkt_index.get(ticker_n)
        if not info:
            continue
        mkt_status, inception_raw = info
        if (mkt_status or "").upper() != "ACTV":
            continue
        inc = _parse_inception(inception_raw)
        if inc is None:
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
    args = parser.parse_args(argv)

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
    finally:
        db.close()

    _print_report(stats1, stats3, since, dry_run)

    if not dry_run:
        write_watermark(date.today())
        print(f"\nWatermark updated -> {date.today().isoformat()}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
