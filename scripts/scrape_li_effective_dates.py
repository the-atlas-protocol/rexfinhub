#!/usr/bin/env python
"""Targeted SEC scrape: fill TRUE effective dates for L&I fund_status rows with NULL.

Ryu 2026-06-22. Every L&I filing must show its TRUE effective date scraped from the
actual SEC document — never an estimate. ~444 L&I-name fund_status series carry a NULL
effective_date; the T-REX competitor sections (load_underlier_competition) read
fund_status.effective_date, so those NULLs render as blanks in the report.

What this does, per L&I-name fund_status row with effective_date IS NULL:
  1. Find the most relevant 485-family filing for the row (via fund_extractions->filings,
     keyed on series_id; prefer 485APOS > 485BPOS > 485BXT, newest first).
  2. Fetch the ACTUAL SEC submission document with the existing polite SECClient
     (proper User-Agent + rate limit), reusing the on-disk cache.
  3. Parse the REAL elected/designated effective date:
       - 485APOS: step3._parse_485a_election (cover-page effectiveness ELECTION).
       - any 485: SGML EFFECTIVENESS DATE header + step3 HIGH-confidence date phrases.
  4. WRITE the scraped date to fund_status.effective_date with
     effective_date_confidence='SCRAPED'. Idempotent — only touches NULL rows.

NEVER estimates. If the document carries no elected/designated date (a true DELAYED /
no-election filing), the row is LEFT NULL — that is the truth.

Run:  /home/jarvis/venv/bin/python scripts/scrape_li_effective_dates.py [--limit N] [--dry-run]
"""
from __future__ import annotations
import argparse
import logging
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from etp_tracker.sec_client import SECClient
from etp_tracker.config import USER_AGENT_DEFAULT
from etp_tracker.step3 import (
    _parse_485a_election,
    _extract_effectiveness_from_hdr,
    _find_effective_date_in_text,
)
from robust_election import parse_election_robust

DB = ROOT / "data" / "etp_tracker.db"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "logs" / "scrape_li_effective_dates.log"),
    ],
)
log = logging.getLogger("scrape_li_eff")

_LI_SQL = """(
    lower(fs.fund_name) GLOB '*[0-9]x*'
    OR lower(fs.fund_name) LIKE '%leverag%'
    OR lower(fs.fund_name) LIKE '%inverse%'
    OR lower(fs.fund_name) LIKE '%bull%'
    OR lower(fs.fund_name) LIKE '%bear%'
    OR lower(fs.fund_name) LIKE '%ultra%'
    OR lower(fs.fund_name) LIKE '%daily target%'
)"""


def candidates(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    sql = f"""
    SELECT fs.id AS fs_id, fs.fund_name, fs.status, fs.series_id, fs.class_contract_id,
           f.id AS filing_id, f.form, f.accession_number, f.filing_date,
           f.cik, f.registrant, f.submission_txt_link, f.primary_link
    FROM fund_status fs
    JOIN fund_extractions fe ON fe.series_id = fs.series_id
    JOIN filings f ON f.id = fe.filing_id
    WHERE fs.effective_date IS NULL
      AND fs.series_id IS NOT NULL
      AND f.form LIKE '485%'
      AND {_LI_SQL}
    """
    rows = conn.execute(sql).fetchall()
    rank = {"485APOS": 1, "485BPOS": 2, "485BXT": 3}
    best: dict[int, sqlite3.Row] = {}
    for r in rows:
        fid = r["fs_id"]
        cur = best.get(fid)
        if cur is None:
            best[fid] = r
            continue
        rk_new = rank.get(r["form"], 9)
        rk_cur = rank.get(cur["form"], 9)
        if rk_new < rk_cur or (rk_new == rk_cur and (r["filing_date"] or "") > (cur["filing_date"] or "")):
            best[fid] = r
    out = list(best.values())
    out.sort(key=lambda r: r["filing_date"] or "", reverse=True)
    return out


def scrape_one(client: SECClient, row: sqlite3.Row) -> tuple[str, str]:
    txt_url = row["submission_txt_link"]
    if not txt_url:
        return "", "no-url"
    try:
        txt = client.fetch_text(txt_url)
    except Exception as exc:  # noqa: BLE001
        log.warning("fetch failed %s: %s", row["accession_number"], exc)
        return "", "fetch-error"
    if not txt:
        return "", "empty"

    form = (row["form"] or "").upper()
    filing_date = row["filing_date"]

    ed_hdr = _extract_effectiveness_from_hdr(txt)
    if ed_hdr:
        return ed_hdr, "HEADER"

    if form.startswith("485A"):
        ed, conf = parse_election_robust(txt, filing_date)
        if ed:
            return ed, conf

    ed_txt, conf_txt, _delaying = _find_effective_date_in_text(txt)
    if ed_txt and conf_txt == "HIGH":
        return ed_txt, "TEXT-HIGH"

    return "", "no-election"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--pause", type=float, default=0.15)
    args = ap.parse_args()

    (ROOT / "logs").mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    rows = candidates(conn)
    if args.limit:
        rows = rows[: args.limit]
    log.info("candidates: %d L&I-name NULL-eff rows linked to a 485 filing", len(rows))

    client = SECClient(user_agent=USER_AGENT_DEFAULT, pause=args.pause)

    scraped = 0
    dateless = 0
    errors = 0
    t0 = time.time()
    for i, row in enumerate(rows, 1):
        ed, tag = scrape_one(client, row)
        if ed:
            if not args.dry_run:
                cur = conn.execute(
                    "UPDATE fund_status SET effective_date=?, "
                    "effective_date_confidence='SCRAPED', updated_at=datetime('now') "
                    "WHERE id=? AND effective_date IS NULL",
                    (ed, row["fs_id"]),
                )
                conn.commit()
                wrote = cur.rowcount
            else:
                wrote = 0
            scraped += 1
            log.info("[%d/%d] %s %s -> %s (%s)%s",
                     i, len(rows), row["accession_number"], (row["fund_name"] or "")[:50],
                     ed, tag, "" if not args.dry_run and wrote else " [dry/skip-write]")
        else:
            if tag in ("fetch-error", "no-url", "empty"):
                errors += 1
            else:
                dateless += 1
            if i % 25 == 0:
                log.info("[%d/%d] ... no date: %s (%s)", i, len(rows),
                         (row["fund_name"] or "")[:50], tag)

        if i % 50 == 0:
            rate = i / (time.time() - t0)
            log.info("progress %d/%d | scraped=%d dateless=%d errors=%d | %.1f rows/s",
                     i, len(rows), scraped, dateless, errors, rate)

    conn.close()
    log.info("DONE: %d candidates | scraped(TRUE dates)=%d | genuinely dateless=%d | errors=%d | %.0fs",
             len(rows), scraped, dateless, errors, time.time() - t0)
    print(f"RESULT scraped={scraped} dateless={dateless} errors={errors} total={len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
