"""Duplicate ticker cleanup tool for the ETP Filing Tracker DB.

Problem
-------
The user has observed hundreds of filings that share the same ticker. Same
ticker across DIFFERENT trusts is legitimate (tickers get reused after a
fund delists). Same ticker WITHIN the same trust/filing is a scraping bug
and needs to be cleaned up.

What this script does
---------------------
1. Analyzes duplicate tickers WITHIN each trust (across FundStatus rows
   and within individual FundExtraction rows for multi-class filings).
2. Optionally NULLs out the ticker on the older / less-current rows,
   keeping the EFFECTIVE + most-recent one.
3. Logs every change it would make (dry-run) or did make (--commit) to a
   JSON file under temp/ for reversibility and audit.
4. Backs up etp_tracker.db before any real write.

Never deletes rows. Only NULLs tickers. All writes are transactional.

Usage
-----
Analyze all trusts (dry run):
    python scripts/dedupe_ticker_cleanup.py --analyze

Analyze one trust:
    python scripts/dedupe_ticker_cleanup.py --analyze --trust "ETF Opportunities Trust"

Fix extractions (dry run first):
    python scripts/dedupe_ticker_cleanup.py --fix-extractions --trust "ETF Opportunities Trust"

Actually commit extractions:
    python scripts/dedupe_ticker_cleanup.py --fix-extractions --trust "ETF Opportunities Trust" --commit

Fix fund_status rows (dry run / commit):
    python scripts/dedupe_ticker_cleanup.py --fix-status
    python scripts/dedupe_ticker_cleanup.py --fix-status --commit
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import warnings
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

# datetime.utcnow() triggers a DeprecationWarning on Python 3.13, but
# SQLAlchemy still uses it internally for default timestamps. Suppress
# just that one warning so the analysis output stays readable.
warnings.filterwarnings(
    "ignore",
    message=r"datetime\.datetime\.utcnow\(\) is deprecated.*",
    category=DeprecationWarning,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

from sqlalchemy.orm import Session  # noqa: E402

from webapp.database import DB_PATH, SessionLocal, init_db  # noqa: E402
from webapp.models import Filing, FundExtraction, FundStatus, Trust  # noqa: E402


STATUS_PRIORITY = {
    "EFFECTIVE": 3,
    "PENDING": 2,
    "DELAYED": 1,
}


def _norm_ticker(t: str | None) -> str | None:
    if t is None:
        return None
    s = str(t).strip().upper()
    return s or None


def _fmt_date(d: date | datetime | None) -> str:
    if d is None:
        return ""
    if isinstance(d, datetime):
        return d.strftime("%Y-%m-%d")
    return d.isoformat()


def _ensure_log_path() -> Path:
    stamp = datetime.now().strftime("%Y-%m-%d")
    log_dir = PROJECT_ROOT / "temp"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"dedupe_log_{stamp}.json"


def _append_log(log_path: Path, entries: list[dict]) -> None:
    existing: list = []
    if log_path.exists():
        try:
            existing = json.loads(log_path.read_text(encoding="utf-8"))
            if not isinstance(existing, list):
                existing = []
        except Exception:
            existing = []
    existing.extend(entries)
    log_path.write_text(json.dumps(existing, indent=2, default=str), encoding="utf-8")


def _backup_db() -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak_path = DB_PATH.with_name(f"etp_tracker.db.pre-dedupe-{stamp}.bak")
    shutil.copy2(DB_PATH, bak_path)
    return bak_path


# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
def _iter_trusts(db: Session, trust_filter: str | None):
    q = db.query(Trust)
    if trust_filter:
        q = q.filter(Trust.name == trust_filter)
    return q.order_by(Trust.name).all()


def _status_rank(status: FundStatus) -> tuple:
    """Higher tuple = better row to KEEP.

    Preference order:
      1. EFFECTIVE beats PENDING beats DELAYED beats other.
      2. Most recent latest_filing_date wins.
      3. Most recent updated_at wins as final tiebreaker.
    """
    status_score = STATUS_PRIORITY.get((status.status or "").upper(), 0)
    lfd = status.latest_filing_date or date.min
    upd = status.updated_at or datetime.min
    return (status_score, lfd, upd, status.id)


def analyze_fund_status_duplicates(db: Session, trust_filter: str | None) -> list[dict]:
    """Return one dict per duplicate-ticker group inside a single trust.

    Each group contains the full list of FundStatus rows that share a
    ticker, plus which row is the proposed KEEP and which are proposed NULL.
    """
    groups: list[dict] = []
    trusts = _iter_trusts(db, trust_filter)

    for trust in trusts:
        rows = (
            db.query(FundStatus)
            .filter(FundStatus.trust_id == trust.id)
            .all()
        )
        by_ticker: dict[str, list[FundStatus]] = defaultdict(list)
        for r in rows:
            tk = _norm_ticker(r.ticker)
            if not tk:
                continue
            by_ticker[tk].append(r)

        for ticker, dupes in by_ticker.items():
            if len(dupes) < 2:
                continue
            ranked = sorted(dupes, key=_status_rank, reverse=True)
            keep = ranked[0]
            null_rows = ranked[1:]
            groups.append({
                "trust_id": trust.id,
                "trust_name": trust.name,
                "ticker": ticker,
                "keep": keep,
                "null_rows": null_rows,
                "all_rows": dupes,
            })
    return groups


def _extraction_rank(ext: FundExtraction, filing: Filing) -> tuple:
    """Higher tuple = better extraction row to KEEP within a trust+ticker group.

    Preference:
      1. Strongest effective-date confidence (IXBRL > HEADER > HIGH > MEDIUM > ...)
      2. Most recent filing_date
      3. Most recent FundExtraction.created_at
    """
    conf_score = {
        "IXBRL": 4, "HEADER": 3, "HIGH": 2, "MEDIUM": 1,
    }.get((ext.effective_date_confidence or "").upper(), 0)
    fdt = filing.filing_date or date.min
    created = ext.created_at or datetime.min
    return (conf_score, fdt, created, ext.id)


def analyze_extraction_duplicates(db: Session, trust_filter: str | None) -> dict[str, list[dict]]:
    """Find duplicate tickers inside FundExtraction rows, grouped two ways.

    Returns:
        {
            "within_filing": [...],   # same ticker > once in the SAME filing
            "within_trust": [...],    # same ticker across different filings in same trust
        }
    """
    trusts = _iter_trusts(db, trust_filter)

    within_filing: list[dict] = []
    within_trust: list[dict] = []

    for trust in trusts:
        # Fetch all extractions joined to filings for this trust in one query
        ext_rows = (
            db.query(FundExtraction, Filing)
            .join(Filing, FundExtraction.filing_id == Filing.id)
            .filter(Filing.trust_id == trust.id)
            .all()
        )
        if not ext_rows:
            continue

        # 1) Within-filing duplicates: same ticker appears more than once in one filing.
        per_filing: dict[int, list[tuple[FundExtraction, Filing]]] = defaultdict(list)
        for ext, filing in ext_rows:
            per_filing[filing.id].append((ext, filing))

        for filing_id, pairs in per_filing.items():
            by_ticker: dict[str, list[tuple[FundExtraction, Filing]]] = defaultdict(list)
            for ext, filing in pairs:
                tk = _norm_ticker(ext.class_symbol)
                if not tk:
                    continue
                by_ticker[tk].append((ext, filing))
            for ticker, group in by_ticker.items():
                if len(group) < 2:
                    continue
                # Keep the first occurrence (lowest id — earliest inserted).
                ranked = sorted(group, key=lambda pair: pair[0].id)
                keep = ranked[0][0]
                null_rows = [g[0] for g in ranked[1:]]
                within_filing.append({
                    "trust_id": trust.id,
                    "trust_name": trust.name,
                    "filing_id": filing_id,
                    "accession_number": ranked[0][1].accession_number,
                    "form": ranked[0][1].form,
                    "filing_date": ranked[0][1].filing_date,
                    "ticker": ticker,
                    "keep": keep,
                    "null_rows": null_rows,
                    "all_rows": [g[0] for g in ranked],
                })

        # 2) Within-trust duplicates: same ticker across DIFFERENT filings
        #    but same series_id. If series_id is blank, fall back to the
        #    (trust, ticker) grouping so we still catch it.
        by_trust_key: dict[tuple, list[tuple[FundExtraction, Filing]]] = defaultdict(list)
        for ext, filing in ext_rows:
            tk = _norm_ticker(ext.class_symbol)
            if not tk:
                continue
            series_key = ext.series_id or ""
            by_trust_key[(series_key, tk)].append((ext, filing))

        for (series_key, ticker), group in by_trust_key.items():
            # Collapse to distinct filings; a within-filing dup is already
            # handled above and should not double-count here.
            filings_seen = {pair[1].id for pair in group}
            if len(filings_seen) < 2:
                continue
            ranked = sorted(group, key=lambda pair: _extraction_rank(pair[0], pair[1]), reverse=True)
            keep_ext, keep_filing = ranked[0]
            null_pairs = ranked[1:]
            within_trust.append({
                "trust_id": trust.id,
                "trust_name": trust.name,
                "series_id": series_key or None,
                "ticker": ticker,
                "keep_ext": keep_ext,
                "keep_filing": keep_filing,
                "null_pairs": null_pairs,
                "all_pairs": ranked,
            })

    return {"within_filing": within_filing, "within_trust": within_trust}


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def _print_status_groups(groups: list[dict]) -> None:
    if not groups:
        print("No duplicate tickers found in FundStatus.")
        return

    # Group by trust for a readable tree layout
    by_trust: dict[str, list[dict]] = defaultdict(list)
    for g in groups:
        by_trust[g["trust_name"]].append(g)

    print("=" * 72)
    print("DUPLICATE TICKERS IN fund_status (per trust)")
    print("=" * 72)
    for trust_name in sorted(by_trust):
        tgroups = by_trust[trust_name]
        print(f"\nTrust: {trust_name} ({len(tgroups)} duplicate tickers)")
        for g in sorted(tgroups, key=lambda x: x["ticker"]):
            print(f"  {g['ticker']}: {len(g['all_rows'])} occurrences")
            for r in g["all_rows"]:
                tag = "KEEP" if r.id == g["keep"].id else "NULL"
                print(
                    f"    [{(r.status or '').ljust(9)}] "
                    f"series={r.series_id or '-'} "
                    f"class={r.class_contract_id or '-'} "
                    f"latest={r.latest_form or '-'} {_fmt_date(r.latest_filing_date)} "
                    f"({tag})"
                )


def _print_extraction_groups(result: dict[str, list[dict]]) -> None:
    within_filing = result["within_filing"]
    within_trust = result["within_trust"]

    print("\n" + "=" * 72)
    print("DUPLICATE TICKERS IN fund_extractions — SAME FILING (likely scraping bug)")
    print("=" * 72)
    if not within_filing:
        print("None found.")
    else:
        by_trust: dict[str, list[dict]] = defaultdict(list)
        for g in within_filing:
            by_trust[g["trust_name"]].append(g)
        for trust_name in sorted(by_trust):
            tgroups = by_trust[trust_name]
            print(f"\nTrust: {trust_name} ({len(tgroups)} dup groups)")
            for g in sorted(tgroups, key=lambda x: (x["accession_number"], x["ticker"])):
                print(
                    f"  {g['ticker']} in filing {g['form']} "
                    f"{g['accession_number']} ({_fmt_date(g['filing_date'])}): "
                    f"{len(g['all_rows'])} occurrences"
                )
                for ext in g["all_rows"]:
                    tag = "KEEP" if ext.id == g["keep"].id else "NULL"
                    print(
                        f"    id={ext.id} series={ext.series_id or '-'} "
                        f"class={ext.class_contract_id or '-'} "
                        f"name={(ext.class_contract_name or ext.series_name or '-')[:60]} "
                        f"({tag})"
                    )

    print("\n" + "=" * 72)
    print("DUPLICATE TICKERS IN fund_extractions — SAME TRUST+SERIES, MULTIPLE FILINGS")
    print("=" * 72)
    if not within_trust:
        print("None found.")
    else:
        by_trust: dict[str, list[dict]] = defaultdict(list)
        for g in within_trust:
            by_trust[g["trust_name"]].append(g)
        for trust_name in sorted(by_trust):
            tgroups = by_trust[trust_name]
            print(f"\nTrust: {trust_name} ({len(tgroups)} dup groups)")
            for g in sorted(tgroups, key=lambda x: x["ticker"]):
                print(
                    f"  {g['ticker']} (series {g['series_id'] or '-'}): "
                    f"{len(g['all_pairs'])} occurrences across "
                    f"{len({p[1].id for p in g['all_pairs']})} filings"
                )
                for ext, filing in g["all_pairs"]:
                    tag = "KEEP" if ext.id == g["keep_ext"].id else "NULL"
                    conf = ext.effective_date_confidence or "-"
                    print(
                        f"    id={ext.id} {filing.form} {filing.accession_number} "
                        f"{_fmt_date(filing.filing_date)} conf={conf} ({tag})"
                    )


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------
def fix_extractions(
    db: Session,
    trust_filter: str | None,
    commit: bool,
    log_path: Path,
) -> dict[str, int]:
    result = analyze_extraction_duplicates(db, trust_filter)
    _print_extraction_groups(result)

    total_null = 0
    log_entries: list[dict] = []

    before_tickers = (
        db.query(FundExtraction)
        .filter(FundExtraction.class_symbol.isnot(None))
        .count()
    )

    for g in result["within_filing"]:
        for ext in g["null_rows"]:
            log_entries.append({
                "kind": "fund_extraction_within_filing",
                "trust_name": g["trust_name"],
                "filing_id": g["filing_id"],
                "accession_number": g["accession_number"],
                "ticker_removed": ext.class_symbol,
                "fund_extraction_id": ext.id,
                "series_id": ext.series_id,
                "class_contract_id": ext.class_contract_id,
                "class_contract_name": ext.class_contract_name,
                "kept_extraction_id": g["keep"].id,
                "timestamp": datetime.utcnow().isoformat(),
            })
            if commit:
                ext.class_symbol = None
            total_null += 1

    for g in result["within_trust"]:
        for ext, filing in g["null_pairs"]:
            log_entries.append({
                "kind": "fund_extraction_within_trust",
                "trust_name": g["trust_name"],
                "series_id": g["series_id"],
                "ticker_removed": ext.class_symbol,
                "fund_extraction_id": ext.id,
                "filing_id": filing.id,
                "filing_form": filing.form,
                "filing_accession": filing.accession_number,
                "filing_date": _fmt_date(filing.filing_date),
                "kept_extraction_id": g["keep_ext"].id,
                "kept_filing_accession": g["keep_filing"].accession_number,
                "timestamp": datetime.utcnow().isoformat(),
            })
            if commit:
                ext.class_symbol = None
            total_null += 1

    if log_entries:
        _append_log(log_path, log_entries)

    if commit:
        db.commit()
        after_tickers = (
            db.query(FundExtraction)
            .filter(FundExtraction.class_symbol.isnot(None))
            .count()
        )
        print(
            f"\nCommitted. fund_extractions with a ticker: "
            f"before={before_tickers} after={after_tickers} "
            f"(delta={before_tickers - after_tickers}, expected={total_null})"
        )
        if before_tickers - after_tickers != total_null:
            print("WARNING: delta does not match expected NULL count.")
    else:
        db.rollback()
        print(
            f"\nDRY RUN. Would NULL {total_null} ticker(s) in fund_extractions. "
            "Re-run with --commit to apply."
        )

    return {
        "within_filing_groups": len(result["within_filing"]),
        "within_trust_groups": len(result["within_trust"]),
        "tickers_nulled": total_null,
    }


def fix_fund_status(
    db: Session,
    trust_filter: str | None,
    commit: bool,
    log_path: Path,
) -> dict[str, int]:
    groups = analyze_fund_status_duplicates(db, trust_filter)
    _print_status_groups(groups)

    total_null = 0
    log_entries: list[dict] = []

    before_tickers = (
        db.query(FundStatus)
        .filter(FundStatus.ticker.isnot(None))
        .count()
    )

    for g in groups:
        for row in g["null_rows"]:
            log_entries.append({
                "kind": "fund_status",
                "trust_name": g["trust_name"],
                "ticker_removed": row.ticker,
                "fund_status_id": row.id,
                "series_id": row.series_id,
                "class_contract_id": row.class_contract_id,
                "fund_name": row.fund_name,
                "status": row.status,
                "latest_form": row.latest_form,
                "latest_filing_date": _fmt_date(row.latest_filing_date),
                "kept_fund_status_id": g["keep"].id,
                "kept_status": g["keep"].status,
                "kept_latest_filing_date": _fmt_date(g["keep"].latest_filing_date),
                "timestamp": datetime.utcnow().isoformat(),
            })
            if commit:
                row.ticker = None
            total_null += 1

    if log_entries:
        _append_log(log_path, log_entries)

    if commit:
        db.commit()
        after_tickers = (
            db.query(FundStatus)
            .filter(FundStatus.ticker.isnot(None))
            .count()
        )
        print(
            f"\nCommitted. fund_status with a ticker: "
            f"before={before_tickers} after={after_tickers} "
            f"(delta={before_tickers - after_tickers}, expected={total_null})"
        )
        if before_tickers - after_tickers != total_null:
            print("WARNING: delta does not match expected NULL count.")
    else:
        db.rollback()
        print(
            f"\nDRY RUN. Would NULL {total_null} ticker(s) in fund_status. "
            "Re-run with --commit to apply."
        )

    return {
        "status_dup_groups": len(groups),
        "tickers_nulled": total_null,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Find and optionally clean up duplicate tickers within the same trust.",
    )
    parser.add_argument("--analyze", action="store_true",
                        help="Run analysis only (default if no other mode given).")
    parser.add_argument("--fix-extractions", action="store_true",
                        help="Clean duplicate tickers in fund_extractions.")
    parser.add_argument("--fix-status", action="store_true",
                        help="Clean duplicate tickers in fund_status.")
    parser.add_argument("--commit", action="store_true",
                        help="Actually write changes. Without this, runs in dry-run mode.")
    parser.add_argument("--trust", type=str, default=None,
                        help='Limit scope to one trust (exact name match).')
    args = parser.parse_args()

    if not (args.analyze or args.fix_extractions or args.fix_status):
        args.analyze = True

    init_db()

    # Backup before any real write
    if args.commit and (args.fix_extractions or args.fix_status):
        bak = _backup_db()
        print(f"DB backed up to: {bak}")

    log_path = _ensure_log_path()

    db = SessionLocal()
    try:
        summary: dict = {}
        if args.analyze and not (args.fix_extractions or args.fix_status):
            status_groups = analyze_fund_status_duplicates(db, args.trust)
            _print_status_groups(status_groups)
            ext_result = analyze_extraction_duplicates(db, args.trust)
            _print_extraction_groups(ext_result)
            total_status_nulls = sum(len(g["null_rows"]) for g in status_groups)
            total_ext_within_filing_nulls = sum(
                len(g["null_rows"]) for g in ext_result["within_filing"]
            )
            total_ext_within_trust_nulls = sum(
                len(g["null_pairs"]) for g in ext_result["within_trust"]
            )
            summary = {
                "fund_status_dup_groups": len(status_groups),
                "fund_status_potential_nulls": total_status_nulls,
                "fund_extractions_within_filing_groups": len(ext_result["within_filing"]),
                "fund_extractions_within_filing_potential_nulls": total_ext_within_filing_nulls,
                "fund_extractions_within_trust_groups": len(ext_result["within_trust"]),
                "fund_extractions_within_trust_potential_nulls": total_ext_within_trust_nulls,
            }
        else:
            if args.fix_extractions:
                summary["fix_extractions"] = fix_extractions(
                    db, args.trust, args.commit, log_path,
                )
            if args.fix_status:
                summary["fix_status"] = fix_fund_status(
                    db, args.trust, args.commit, log_path,
                )

        print("\n" + "=" * 72)
        print("SUMMARY")
        print("=" * 72)
        for k, v in summary.items():
            print(f"  {k}: {v}")
        if log_path.exists() and (args.fix_extractions or args.fix_status):
            print(f"\nChange log: {log_path}")
        print()
        return 0
    except Exception as e:
        db.rollback()
        print(f"ERROR: {e}")
        raise
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
