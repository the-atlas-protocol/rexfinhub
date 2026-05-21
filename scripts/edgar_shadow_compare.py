"""Track 6 (ADR 0010) — SHADOW comparison: edgartools vs the in-house extractor.

Measures the gap between `edgartools` and the in-house SEC extraction stack
(step3 / single_filing / bulk_loader) WITHOUT touching the live pipeline. For
the 485-series filings the in-house pipeline recorded in the last N days, it
fetches the same form+date window via edgartools and reports the agreement:

  - in both          — the in-house pipeline and edgartools agree (by accession)
  - in-house only    — filings edgartools missed (the gap that blocks cutover)
  - edgartools only  — filings edgartools sees that we don't curate (expected;
                       edgartools returns the whole SEC universe for a form)

The key metric is **edgartools coverage of the in-house set** — edgartools
must see everything the legacy pipeline sees before a cutover is safe.

This is the dual-run evidence ADR 0010's cutover decision rests on. The
legacy stack stays authoritative (D1: build everything; remove nothing until
proven). Run it on a cadence; review data/.edgar_shadow.log; cut over only
when coverage is consistently 100%.

Read-only — safe to run anytime. A no-op if edgartools is not installed.

Usage:
    python scripts/edgar_shadow_compare.py [--db PATH] [--days N]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"
LOG_PATH = PROJECT_ROOT / "data" / ".edgar_shadow.log"

FORMS = ["485BPOS", "485APOS", "485BXT"]


def _norm_acc(a: str | None) -> str:
    """Normalize an accession number for set comparison (strip dashes, lower)."""
    return (a or "").replace("-", "").strip().lower()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--content-sample", type=int, default=40,
                    help="Compare series/class content for N filings present in "
                         "both sets (0 to skip the content pass)")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    today = date.today()
    since = (today - timedelta(days=args.days)).isoformat()
    until = today.isoformat()

    # --- In-house view: filings recorded by the legacy pipeline ---
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT accession_number, form, filing_date, cik FROM filings "
            "WHERE form IN (?, ?, ?) AND filing_date >= ?",
            (*FORMS, since),
        ).fetchall()
    finally:
        conn.close()
    inhouse = {_norm_acc(r[0]): r for r in rows if r[0]}
    print(f"In-house 485-series filings since {since}: {len(inhouse)}")

    # --- edgartools view ---
    try:
        from etp_tracker.edgar_client import fetch_recent_filings, edgartools_available
    except Exception as e:
        print(f"ERROR importing edgar_client: {e}", file=sys.stderr)
        return 1

    if not edgartools_available():
        print("edgartools is NOT installed in this environment.")
        print("Install it (pip install edgartools) to run the shadow comparison.")
        _log(0, len(inhouse), 0, 0, 0, 0.0, since, note="edgartools-not-installed")
        return 0

    edgar_views = fetch_recent_filings(FORMS, since, until)
    edgar = {_norm_acc(v.accession): v for v in edgar_views if v.ok and v.accession}
    print(f"edgartools 485-series filings since {since}: {len(edgar)}")

    both = set(inhouse) & set(edgar)
    inhouse_only = set(inhouse) - set(edgar)
    edgar_only = set(edgar) - set(inhouse)
    coverage = (len(both) / len(inhouse) * 100.0) if inhouse else 100.0

    print("\n=== SHADOW COMPARISON (edgartools vs in-house) ===")
    print(f"  in both:                 {len(both)}")
    print(f"  in-house only (gap):     {len(inhouse_only)}  <- edgartools missed these")
    print(f"  edgartools only:         {len(edgar_only)}  (whole-SEC universe; expected)")
    print(f"  edgartools coverage of the in-house set: {coverage:.1f}%")
    if inhouse_only:
        print("\n  Sample in-house filings edgartools missed:")
        for acc in list(inhouse_only)[:10]:
            r = inhouse[acc]
            print(f"    {r[0]}  {r[1]}  {r[2]}  cik={r[3]}")
    if coverage >= 100.0 and inhouse:
        print("\n  edgartools fully covers the in-house set for this window.")
    elif inhouse:
        print(f"\n  GAP: edgartools is missing {len(inhouse_only)} filings — NOT ready "
              f"for cutover. Investigate before ADR 0010 Stage 4.")

    _log(len(edgar), len(inhouse), len(both), len(inhouse_only), len(edgar_only),
         coverage, since)

    # --- Content-extraction parity (ADR 0010 Stage 3/4) ---
    # For a sample of filings BOTH sources agree exist, check that edgartools'
    # series/class extraction matches the in-house fund_extractions rows. This
    # is the evidence the cutover (Stage 4) needs beyond mere filing discovery.
    if args.content_sample > 0 and both:
        import random
        try:
            from etp_tracker.edgar_client import fetch_filing_header_content
        except Exception as e:
            print(f"\nContent pass skipped — edgar_client import failed: {e}")
            fetch_filing_header_content = None
        if fetch_filing_header_content is not None:
            sample = sorted(both)
            random.Random(42).shuffle(sample)
            sample = sample[: args.content_sample]
            conn = sqlite3.connect(str(db_path))
            match_n = 0
            mismatches: list[tuple[str, str]] = []
            for acc_norm in sample:
                acc_raw = inhouse[acc_norm][0]
                fid = conn.execute(
                    "SELECT id FROM filings WHERE accession_number = ?", (acc_raw,)
                ).fetchone()
                if not fid:
                    continue
                inh_rows = conn.execute(
                    "SELECT series_id, class_contract_id FROM fund_extractions "
                    "WHERE filing_id = ?", (fid[0],)
                ).fetchall()
                inh_series = {(s or "").strip() for s, _ in inh_rows if s and s.strip()}
                inh_class = {(c or "").strip() for _, c in inh_rows if c and c.strip()}
                ec = fetch_filing_header_content(acc_raw)
                if not ec.ok:
                    mismatches.append((acc_raw, f"edgar fetch failed: {ec.error}"))
                    continue
                ed_series = {r.series_id.strip() for r in ec.rows if r.series_id.strip()}
                ed_class = {r.class_contract_id.strip() for r in ec.rows
                            if r.class_contract_id.strip()}
                if inh_series == ed_series and inh_class == ed_class:
                    match_n += 1
                else:
                    mismatches.append((acc_raw,
                        f"series in-house={sorted(inh_series)} edgar={sorted(ed_series)}; "
                        f"class in-house={sorted(inh_class)} edgar={sorted(ed_class)}"))
            conn.close()
            total = match_n + len(mismatches)
            cparity = (match_n / total * 100.0) if total else 100.0
            print(f"\n=== CONTENT PARITY (series + class IDs, sample of {total}) ===")
            print(f"  exact match:  {match_n}/{total}  ({cparity:.1f}%)")
            if mismatches:
                print(f"  mismatches:   {len(mismatches)}")
                for acc, why in mismatches[:12]:
                    print(f"    {acc}: {why}")
            if cparity >= 100.0 and total:
                print("  edgartools content extraction matches the in-house pipeline "
                      "for this sample.")

    print(f"\nLogged to {LOG_PATH}")
    return 0


def _log(edgar_n, inhouse_n, both, inhouse_only, edgar_only, coverage, since,
         note: str = "") -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(f"{datetime.utcnow().isoformat()}  since={since}  "
                f"inhouse={inhouse_n}  edgar={edgar_n}  both={both}  "
                f"inhouse_only={inhouse_only}  edgar_only={edgar_only}  "
                f"coverage={coverage:.1f}%  {note}\n")


if __name__ == "__main__":
    sys.exit(main())
