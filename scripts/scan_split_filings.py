#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Extract share splits from the SEC 497 supplements we already scrape.

    python scripts/scan_split_filings.py            # scan recent, upsert
    python scripts/scan_split_filings.py --since 2026-01-01
    python scripts/scan_split_filings.py --list     # show what's on record

WHY THIS, AND NOT SOMETHING ELSE
--------------------------------
A split makes a fund's price jump with no economic content. Anything that compares a price
to last month's — Asia's shares-invariant repricing, market-move/flow attribution — is
wrong across a split unless it knows. In Jul 2026 BMNU and DJTU each did 1-for-10 reverses
and nothing noticed: both funds' Asia AUM inflated enormously (BMNU to 97% of its own
global AUM, DJTU above 100%) and a fabricated "+$112.2M BMNU inflows" line reached the
report.

The issuer tells the SEC before it happens. MSTU's 1-for-10 was filed 2026-07-31 as a 497
supplement for an August 24 ex-date — three weeks of notice, in a document we already had
in `filings` and were not reading. That makes EDGAR the primary source:

  * authoritative — it is the issuer's own legal notice, not a vendor's derivation
  * early — filed before the event, so a month can be built knowing what is coming
  * already ingested — the SEC scrape runs 4x/day; this only reads what it stored
  * free

Rejected alternatives: Databento's corporate-actions dataset is $299/mo and not on our
entitlement, and its price feeds are unadjusted, so they would reproduce the bug rather
than catch it. rexshares.com returns 403 to automated fetch. yfinance's calendar is useful
for cross-checking but is a derived vendor feed — it lists a split only after the fact, and
was found to be incomplete.

This does not replace the adjusted-price series used for returns; it is the primary record,
with the price series as independent confirmation.
"""
from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, ".."))
DB = os.path.join(ROOT, "data", "etp_tracker.db")
UA = "REX-ETP-Tracker/2.0 (relasmar@rexfin.com)"

# Filenames the issuer uses for these supplements. Cheap prefilter so we fetch a handful of
# documents rather than every 497.
DOC_HINT = re.compile(r"(rev)?stock ?split|sharesplit|reversesplit|revsplit", re.I)
BODY_HINT = re.compile(r"reverse (stock|share) split|forward (stock|share) split|split ratio", re.I)

RATIO_RE = re.compile(r"split ratio of\s*(\d+)\s*[:\-]\s*(\d+)", re.I)
RATIO_WORDS_RE = re.compile(
    r"receive\s+\w+\s*\((\d+)\)\s*post-split shares? for every\s+\w+\s*\((\d+)\)\s*pre-split", re.I)
TICKER_RE = re.compile(r"\(([A-Z]{2,6})\)")
EFF_RE = re.compile(r"effectuated after the close of trading on\s+([A-Z][a-z]+ \d{1,2}, \d{4})", re.I)
EX_RE = re.compile(r"begin trading on a split-adjusted basis on\s+([A-Z][a-z]+ \d{1,2}, \d{4})", re.I)


def iso(s):
    if not s:
        return None
    import datetime as dt
    for f in ("%B %d, %Y", "%b %d, %Y"):
        try:
            return dt.datetime.strptime(s.strip(), f).date().isoformat()
        except ValueError:
            pass
    return None


def ensure_table(con):
    con.execute("""
        CREATE TABLE IF NOT EXISTS corporate_actions (
            ticker        TEXT NOT NULL,
            action        TEXT NOT NULL,          -- 'reverse_split' | 'forward_split'
            ratio_from    REAL NOT NULL,          -- pre-split shares
            ratio_to      REAL NOT NULL,          -- post-split shares
            effective_date TEXT,                  -- after close of this day
            ex_date       TEXT,                   -- first split-adjusted trading day
            filing_date   TEXT,
            accession     TEXT,
            source_url    TEXT,
            PRIMARY KEY (ticker, ex_date, action)
        )""")
    con.commit()


def fetch(url, tries=3):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": UA})
            with urllib.request.urlopen(req, timeout=45) as r:
                return r.read().decode("utf-8", "replace")
        except Exception:
            if i == tries - 1:
                return None
            time.sleep(1.5 * (i + 1))
    return None



def resolve_docs(url, cik, accession):
    """Return the real document URLs for a filing.

    61 of 79 REX 497 rows store the accession's -index.htm rather than the supplement
    itself, so fetching primary_link straight gets a listing page and parses to nothing.
    That is why the first pass found only 3 of the 7 funds in the Aug 2026 split. Ask EDGAR
    for the filing's file list and take the real documents.
    """
    if url and not url.endswith("-index.htm"):
        return [url]
    accn = (accession or "").replace("-", "")
    if not accn or cik is None:
        return [url] if url else []
    base = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn}"
    import json
    raw = fetch(f"{base}/index.json")
    if not raw:
        return [url] if url else []
    try:
        items = json.loads(raw)["directory"]["item"]
    except Exception:
        return [url] if url else []
    out = []
    for it in items:
        n = it.get("name", "")
        if n.endswith((".htm", ".html")) and "-index" not in n and not n.startswith("0001"):
            out.append(f"{base}/{n}")
    return out or ([url] if url else [])


def parse(html, filing_date, accession, url):
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&#8217;|&#8216;", "'", text)
    text = re.sub(r"&#8220;|&#8221;", '"', text)
    text = re.sub(r"&nbsp;|&#160;", " ", text)
    text = re.sub(r"\s+", " ", text)
    if not BODY_HINT.search(text):
        return []

    m = RATIO_RE.search(text) or RATIO_WORDS_RE.search(text)
    if not m:
        return []
    a, b = float(m.group(1)), float(m.group(2))
    if RATIO_RE.search(text):
        # "1:10" means one post-split share for every ten pre-split.
        post, pre = a, b
    else:
        post, pre = a, b
    if pre <= 0 or post <= 0:
        return []
    action = "reverse_split" if pre > post else "forward_split"

    eff = iso(EFF_RE.search(text).group(1)) if EFF_RE.search(text) else None
    ex = iso(EX_RE.search(text).group(1)) if EX_RE.search(text) else None

    # Tickers appear parenthesised after each fund name. Filter obvious non-tickers.
    STOP = {"SAI", "ETF", "SEC", "USA", "NAV", "THE", "AND"}
    tickers = [t for t in TICKER_RE.findall(text) if t not in STOP]
    seen, out = set(), []
    for t in tickers:
        if t in seen:
            continue
        seen.add(t)
        out.append((t, action, pre, post, eff, ex, filing_date, accession, url))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="filing_date floor, YYYY-MM-DD")
    ap.add_argument("--list", action="store_true", help="print stored actions and exit")
    # Filenames are not a reliable filter — only 3 of the 7 funds in the Aug 2026 action
    # had "split" in the document name. Scanning every 497 body for the issuing trusts is
    # bounded (~150/yr per trust) and cannot miss one for being named unhelpfully.
    ap.add_argument("--ciks", default="1771146,2043954",
                    help="comma-separated CIKs whose 497 bodies are scanned in full")
    args = ap.parse_args()

    con = sqlite3.connect(DB)
    ensure_table(con)

    if args.list:
        rows = con.execute("""SELECT ticker, action, ratio_from, ratio_to, effective_date,
                                     ex_date, filing_date FROM corporate_actions
                              ORDER BY ex_date DESC, ticker""").fetchall()
        print(f"{len(rows)} corporate action(s) on record")
        for t, act, fr, to, eff, ex, fd in rows:
            label = f"1-for-{int(fr/to)}" if act == "reverse_split" else f"{int(to/fr)}-for-1"
            print(f"  {t:<7} {label:<10} ex {ex}  (effective after close {eff}, filed {fd})")
        return

    since = args.since or "2026-01-01"
    cand = con.execute("""
        SELECT filing_date, accession_number, primary_document, primary_link, cik
        FROM filings
        WHERE form LIKE '497%' AND filing_date >= ?
        ORDER BY filing_date DESC
    """, (since,)).fetchall()
    ciks = {c.strip().lstrip("0") for c in args.ciks.split(",") if c.strip()}

    def _own(cik):
        return str(cik or "").lstrip("0") in ciks

    hits = [r for r in cand if _own(r[4]) or (r[2] and DOC_HINT.search(r[2]))]
    print(f"{len(cand)} 497 filings since {since}; {len(hits)} to scan "
          f"(every body for CIKs {sorted(ciks)}, plus split-named docs elsewhere)")

    found = 0
    for filing_date, acc, doc, link, cik in hits:
        url = link
        if not url:
            accn = (acc or "").replace("-", "")
            url = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accn}/{doc}"
        recs = []
        for durl in resolve_docs(url, cik, acc):
            html = fetch(durl)
            time.sleep(0.15)      # SEC asks for <10 req/s; we are far under
            if not html:
                continue
            recs.extend(parse(html, filing_date, acc, durl))
        if not recs:
            continue
        for rec in recs:
            con.execute("""INSERT OR REPLACE INTO corporate_actions
                (ticker, action, ratio_from, ratio_to, effective_date, ex_date,
                 filing_date, accession, source_url) VALUES (?,?,?,?,?,?,?,?,?)""", rec)
            found += 1
            t, action, pre, post, eff, ex = rec[0], rec[1], rec[2], rec[3], rec[4], rec[5]
            label = f"1-for-{int(pre/post)}" if action == "reverse_split" else f"{int(post/pre)}-for-1"
            print(f"  {t:<7} {label:<10} ex {ex}  filed {filing_date}")
    con.commit()
    print(f"\n{found} action row(s) upserted into corporate_actions")


if __name__ == "__main__":
    main()
