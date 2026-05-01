"""Phase 1.1 — Audit historical ticker duplicates in fund_extractions.

Per Ryu's instruction (2026-04-30):
  - Don't rescrape — too intensive
  - Identify rows where the same ticker was assigned to different series
    within the same accession + same trust (the "ticker bleed" bug fixed
    going-forward in step3.py 2026-04-30)
  - Same ticker across DIFFERENT issuers is OK (means one issuer gave it up)
  - Bracket tickers ([OpenAI], [Anthropic], etc.) flagged separately for
    manual review

Output: docs/ticker_review_queue.csv with columns
  accession, registrant, series_name, class_symbol, filing_date, form,
  bucket (DUPLICATE | BRACKET | KEEP), suggested_action (NULL | KEEP | MANUAL),
  reason

Ryu reviews the CSV, edits suggested_action where needed, then runs
scripts/apply_ticker_cleanup.py to apply the fixes.

This script is READ-ONLY — it never writes to the DB.
"""
from __future__ import annotations

import csv
import re
import sqlite3
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"
OUTPUT_CSV = PROJECT_ROOT / "docs" / "ticker_review_queue.csv"

# Pattern for placeholder tickers — funds tracking pre-IPO names where the
# real ticker isn't assigned yet. Brackets contain the company name.
BRACKET_TICKER_RX = re.compile(r"\[([A-Za-z\s]+)\]")


def find_duplicates(con: sqlite3.Connection) -> list[dict]:
    """Find ticker duplicates using majority-vote heuristic.

    Strategy: for each (registrant, ticker), aggregate across ALL filings
    to find the CANONICAL series name (the one most often paired with that
    ticker — correct assignments persist across filings while bleed errors
    are accession-specific noise). Mark rows where the series matches the
    canonical KEEP; mark all others NULL.

    This collapses thousands of redundant rows into a clean decision per
    (registrant, ticker) pair while preserving the audit trail.
    """
    cur = con.cursor()
    cur.execute("""
        SELECT
          f.accession_number,
          f.registrant,
          f.filing_date,
          f.form,
          fe.series_name,
          fe.class_symbol,
          fe.id AS extraction_id
        FROM fund_extractions fe
        JOIN filings f ON f.id = fe.filing_id
        WHERE fe.class_symbol IS NOT NULL
          AND fe.class_symbol != ''
        ORDER BY f.accession_number, fe.class_symbol, fe.id
    """)
    rows = [dict(zip([d[0] for d in cur.description], r)) for r in cur.fetchall()]

    from collections import defaultdict, Counter

    # Pass 1: For each (registrant, ticker), count how often each series_name
    # appears. The most common is the canonical owner.
    pair_to_series_counts: dict[tuple, Counter] = defaultdict(Counter)
    for r in rows:
        pair = (r["registrant"], r["class_symbol"])
        pair_to_series_counts[pair][r["series_name"]] += 1

    # Determine canonical series per (registrant, ticker)
    canonical: dict[tuple, str] = {}
    contested: set[tuple] = set()
    for pair, counts in pair_to_series_counts.items():
        if len(counts) == 1:
            # Only ever paired with one series → not a dupe at all
            canonical[pair] = next(iter(counts))
        else:
            top, top_count = counts.most_common(1)[0]
            total = sum(counts.values())
            # Canonical only if dominant (>50% of all extractions for this pair)
            if top_count / total > 0.50:
                canonical[pair] = top
            else:
                # Genuinely contested — neither series clearly owns the ticker
                contested.add(pair)

    # Pass 2: Detect duplicates within each accession. Only flag rows where
    # the (registrant, ticker) is mapped to >1 series total.
    accession_groups = defaultdict(list)
    for r in rows:
        key = (r["accession_number"], r["registrant"], r["class_symbol"])
        accession_groups[key].append(r)

    duplicates: list[dict] = []
    for key, items in accession_groups.items():
        series_in_group = {i["series_name"] for i in items}
        pair = (items[0]["registrant"], items[0]["class_symbol"])
        # Skip groups where ticker is uniquely owned (no cross-filing conflict)
        if pair in canonical and len(series_in_group) == 1:
            continue
        # Only flag if there's >1 series for this pair somewhere in history
        if len(pair_to_series_counts[pair]) <= 1:
            continue
        canonical_series = canonical.get(pair)
        for item in items:
            is_canonical = (item["series_name"] == canonical_series)
            is_contested = pair in contested
            if is_contested:
                # No clear winner — safer to NULL all (better empty than wrong).
                # Manual MANUAL bucket is reserved for brackets where Ryu must
                # make a judgment call; contested ticker dupes have no good
                # signal so we drop them and let the going-forward parser fix
                # populate them on next filing.
                action = "NULL"
                reason = f"Contested: ticker {item['class_symbol']} maps to {len(pair_to_series_counts[pair])} series with no clear majority — NULLing"
            elif is_canonical:
                action = "KEEP"
                reason = f"Canonical owner of {item['class_symbol']} ({pair_to_series_counts[pair][item['series_name']]} of {sum(pair_to_series_counts[pair].values())} extractions)"
            else:
                action = "NULL"
                top = canonical_series or "(none)"
                reason = f"Bleed: ticker {item['class_symbol']} canonically belongs to '{top[:40]}', not this series"
            duplicates.append({
                **item,
                "bucket": "DUPLICATE",
                "suggested_action": action,
                "reason": reason,
            })
    return duplicates


def find_bracket_tickers(con: sqlite3.Connection) -> list[dict]:
    """Find rows whose series_name has bracketed placeholders like
    [OpenAI], [SpaceX] — these need Ryu's eyes regardless of dedup state."""
    cur = con.cursor()
    cur.execute("""
        SELECT
          f.accession_number,
          f.registrant,
          f.filing_date,
          f.form,
          fe.series_name,
          fe.class_symbol,
          fe.id AS extraction_id
        FROM fund_extractions fe
        JOIN filings f ON f.id = fe.filing_id
        WHERE fe.series_name LIKE '%[%]%'
        ORDER BY fe.series_name, f.filing_date DESC
    """)
    rows = []
    for r in cur.fetchall():
        d = dict(zip([col[0] for col in cur.description], r))
        m = BRACKET_TICKER_RX.search(d["series_name"] or "")
        company = m.group(1).strip() if m else ""
        rows.append({
            **d,
            "bucket": "BRACKET",
            "suggested_action": "MANUAL",
            "reason": f"Bracketed placeholder for '{company}' — needs Ryu's review",
        })
    return rows


def main():
    if not DB_PATH.exists():
        print(f"DB not found at {DB_PATH}")
        return 1

    con = sqlite3.connect(str(DB_PATH))
    print(f"Auditing fund_extractions in {DB_PATH}")
    print()

    dupes = find_duplicates(con)
    brackets = find_bracket_tickers(con)
    con.close()

    print(f"Duplicates found:        {len(dupes)} rows in {len({(d['accession_number'], d['registrant']) for d in dupes})} unique (accession, registrant) groups")
    print(f"Bracket-ticker rows:     {len(brackets)}")
    print()

    # Combine + write
    all_rows = dupes + brackets
    if not all_rows:
        print("No issues found. Nothing to write.")
        return 0

    OUTPUT_CSV.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "extraction_id", "filing_date", "form", "accession_number",
        "registrant", "series_name", "class_symbol",
        "bucket", "suggested_action", "reason",
    ]
    with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for row in all_rows:
            w.writerow({k: row.get(k, "") for k in fieldnames})

    print(f"Wrote review queue to: {OUTPUT_CSV}")
    print(f"  Total rows: {len(all_rows)}")
    print()
    print("Next:")
    print("  1. Open the CSV, review each row's suggested_action")
    print("  2. Edit suggested_action to KEEP, NULL, or MANUAL as appropriate")
    print("  3. Run: python scripts/apply_ticker_cleanup.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
