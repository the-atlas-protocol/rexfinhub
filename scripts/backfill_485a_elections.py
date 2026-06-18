"""Backfill scheduled effective dates from 485APOS cover-page elections.

Hub_Trex A1 (handoff 2026-06-12): pending filings carry NULL effective dates
because nothing parsed the cover-page election. step3 now parses it for new
filings; this script repairs the EXISTING rows:

  - fund_status rows in PENDING/DELAYED with NULL effective_date and a
    485APOS latest form -> fetch the prospectus doc (cached SEC client),
    parse the election, set effective_date + confidence='ELECTION'.
  - rex_products rows in Filed with latest_form 485APOS whose
    estimated_effective_date is NULL or exactly the filing+75 blanket guess
    -> same parse; correct the date when the election says otherwise.

485BXT rows are untouched (their scheduled date comes from the BXT header
and supersedes any APOS election). Respects manually_edited_fields overrides.

Usage:
    python scripts/backfill_485a_elections.py --dry-run
    python scripts/backfill_485a_elections.py --apply [--limit 200]
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from etp_tracker.sec_client import SECClient  # noqa: E402
from etp_tracker.step3 import _parse_485a_election  # noqa: E402

USER_AGENT = "REX-ETP-Tracker/2.0 relasmar@rexfin.com"


def _doc_text(client: SECClient, url: str) -> str:
    if not url:
        return ""
    # Many prospectus_link values point at the EDGAR filing INDEX page
    # (...-index.htm), which has no cover-page election — the election lives in the
    # full submission text. Resolve -index.htm -> .txt so the parser sees it. This
    # recovers rows that previously logged as "parse failures". (Ryu 2026-06-17.)
    if url.endswith("-index.htm"):
        url = url[: -len("-index.htm")] + ".txt"
    try:
        raw = client.fetch_text(url)
    except Exception:
        return ""
    return raw or ""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=300)
    args = ap.parse_args()
    dry = not args.apply

    import sqlite3
    db = sqlite3.connect(str(PROJECT_ROOT / "data" / "etp_tracker.db"))
    db.row_factory = sqlite3.Row
    client = SECClient(user_agent=USER_AGENT)

    fixed_fs = fixed_rp = parsed_fail = 0

    # ---- fund_status: the competitor/pending universe -----------------------
    rows = db.execute("""
        SELECT id, fund_name, prospectus_link, latest_filing_date
        FROM fund_status
        WHERE status IN ('PENDING', 'DELAYED')
          AND (effective_date IS NULL OR effective_date = '')
          AND latest_form LIKE '485A%'
          AND prospectus_link IS NOT NULL AND prospectus_link != ''
          -- Only chase recent filings: 485APOS older than 2yr that are still PENDING
          -- are abandoned shelf filings that never went effective — dating them with a
          -- 2007-era election is meaningless noise. (Ryu 2026-06-17.)
          AND latest_filing_date >= date('now', '-2 years')
        ORDER BY latest_filing_date DESC
        LIMIT ?
    """, (args.limit,)).fetchall()
    print(f"fund_status candidates: {len(rows)}")
    cache: dict[str, tuple[str, str]] = {}
    for r in rows:
        url = r["prospectus_link"]
        if url in cache:
            d, conf = cache[url]
        else:
            d, conf = _parse_485a_election(_doc_text(client, url), r["latest_filing_date"])
            cache[url] = (d, conf)
        if not d:
            parsed_fail += 1
            continue
        print(f"  fund_status #{r['id']:6d} {str(r['fund_name'])[:48]:48s} -> {d}")
        if not dry:
            db.execute("UPDATE fund_status SET effective_date=?, "
                       "effective_date_confidence='ELECTION' WHERE id=?", (d, r["id"]))
        fixed_fs += 1

    # ---- rex_products: NULL or blanket +75d guesses -------------------------
    rows = db.execute("""
        SELECT id, name, latest_prospectus_link, initial_filing_date,
               estimated_effective_date, manually_edited_fields
        FROM rex_products
        WHERE status IN ('Filed', 'Delayed')
          AND latest_form = '485APOS'
          AND latest_prospectus_link IS NOT NULL AND latest_prospectus_link != ''
    """).fetchall()
    print(f"rex_products candidates: {len(rows)}")
    for r in rows:
        if "estimated_effective_date" in (r["manually_edited_fields"] or ""):
            continue
        guess75 = None
        if r["initial_filing_date"]:
            try:
                y, m_, d_ = map(int, str(r["initial_filing_date"])[:10].split("-"))
                guess75 = (date(y, m_, d_) + timedelta(days=75)).isoformat()
            except ValueError:
                pass
        cur = str(r["estimated_effective_date"] or "")[:10]
        # only touch NULL or exact blanket-guess rows; real dates stay
        if cur and cur != (guess75 or ""):
            continue
        url = r["latest_prospectus_link"]
        if url in cache:
            d, conf = cache[url]
        else:
            d, conf = _parse_485a_election(_doc_text(client, url), r["initial_filing_date"])
            cache[url] = (d, conf)
        if not d:
            parsed_fail += 1
            continue
        if d == cur:
            continue
        print(f"  rex_products #{r['id']:5d} {str(r['name'])[:48]:48s} {cur or 'NULL'} -> {d}")
        if not dry:
            db.execute("UPDATE rex_products SET estimated_effective_date=? WHERE id=?",
                       (d, r["id"]))
        fixed_rp += 1

    if not dry:
        db.commit()
    db.close()
    print(f"\n=== backfill ({'DRY-RUN' if dry else 'APPLIED'}) ===")
    print(f"  fund_status fixed : {fixed_fs}")
    print(f"  rex_products fixed: {fixed_rp}")
    print(f"  parse failures    : {parsed_fail} (left NULL — genuinely unparseable covers)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
