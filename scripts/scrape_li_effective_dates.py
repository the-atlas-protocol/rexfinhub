#!/usr/bin/env python
"""L&I effective-date TRUTH PASS - scrape EVERY L&I 485-family filing, parse its
own true date, write it to the ONE source (fund_extractions), then derive the
authoritative effective_date + Effective status per ADR 0014.

Ryu 2026-06-22. Per /goal: capture each effective date TRUTHFULLY from its own
filing, NO estimation, reflect exactly what each filing says. This is the one-time
full L&I truth pass that ADR 0014 known-gaps calls for.

SCOPE - every distinct 485APOS / 485BXT / 485BPOS filing whose series_name is L&I
(2X/3X/LEVERAGED/INVERSE/ULTRA/ULTRAPRO/BULL/BEAR).

THE ACCURATE MODEL (parsed per form, written to fund_extractions = THE source):
  * 485APOS  -> cover-page ELECTION via robust_election (60/75d/immediate/explicit
               date). The 60/75-day rules ARE the filing's own elected designation
               (filing_date + N), not an outside estimate. conf = ELECTION[-DATE].
  * 485BXT   -> the NEW designated date from the body ("designating <date> as the
               new effective date"); delays/replaces the prior. conf = BXT-NEW.
  * 485BPOS  -> the actual effective date: SGML EFFECTIVENESS DATE header first,
               else a HIGH-confidence body designation. conf = BPOS.
  Validate every date (filing_date <= date; 2015 <= year <= 2031). NEVER estimate.
  A filing that genuinely elects nothing parseable is left NULL truthfully + logged.

DERIVE (ADR 0014 latest-485-wins) and write to the report-facing stores:
  * series authoritative effective_date = the LATEST 485-family filing's parsed date.
  * 497-confirmed-effective = a 497/497K exists for the series (Rule 497(c): filed
    only post-effectiveness) -> the series is Effective by that 497's filing date.
  * fund_status.effective_date (+ rex_products.estimated_effective_date for REX):
    OVERWRITE header/backfill approximations when the robust parse yields a genuine
    checked election/designation (accuracy > non-destructive - this is the L&I truth
    pass). NEVER overwrite a real date with NULL or a guess; never move a date that
    was admin-edited (manually_edited_fields). Status -> Effective when the
    authoritative date <= today OR a 497 confirms it.

Idempotent + resumable: per-filing parse results are checkpointed in the
li_eff_scrape_progress table; a re-run skips filings already parsed (use --refetch
to force). Safe to run detached.

Run (on the VPS):
    nohup /home/jarvis/venv/bin/python scripts/scrape_li_effective_dates.py --apply >/dev/null 2>&1 &
    tail -f logs/scrape_li_effective_dates.log
Flags: --limit N  --dry-run  --refetch  --pause SECS  --derive-only
"""
from __future__ import annotations
import argparse
import logging
import sqlite3
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from etp_tracker.sec_client import SECClient
from etp_tracker.config import USER_AGENT_DEFAULT
from etp_tracker.step3 import (
    _extract_effectiveness_from_hdr,
    _find_effective_date_in_text,
)
from robust_election import parse_election_robust

DB = ROOT / "data" / "etp_tracker.db"
TODAY = date.today().isoformat()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(ROOT / "logs" / "scrape_li_effective_dates.log"),
    ],
)
log = logging.getLogger("scrape_li_eff")

_LI_SERIES = """(
    upper(fe.series_name) GLOB '*[0-9]X*'
    OR upper(fe.series_name) LIKE '%LEVERAGED%'
    OR upper(fe.series_name) LIKE '%INVERSE%'
    OR upper(fe.series_name) LIKE '%ULTRA%'
    OR upper(fe.series_name) LIKE '%ULTRAPRO%'
    OR upper(fe.series_name) LIKE '%BULL%'
    OR upper(fe.series_name) LIKE '%BEAR%'
)"""


def ensure_progress_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS li_eff_scrape_progress (
            filing_id     INTEGER PRIMARY KEY,
            accession     TEXT,
            form          TEXT,
            effective_date TEXT,
            confidence    TEXT,
            reason        TEXT,
            parsed_at     TEXT
        )"""
    )
    conn.commit()


def _valid(date_str: str, filing_date) -> bool:
    if not date_str:
        return False
    try:
        y = int(date_str[:4])
    except Exception:
        return False
    if not (2015 <= y <= 2031):
        return False
    if filing_date and date_str < str(filing_date)[:10]:
        return False
    return True


def parse_filing(client: SECClient, form: str, filing_date,
                 txt_url: str) -> tuple[str, str, str]:
    if not txt_url:
        return "", "", "no-url"
    try:
        txt = client.fetch_text(txt_url)
    except Exception as exc:  # noqa: BLE001
        log.warning("fetch failed %s: %s", txt_url, exc)
        return "", "", "fetch-error"
    if not txt:
        return "", "", "empty"

    form = (form or "").upper()

    if form.startswith("485A"):
        ed, conf = parse_election_robust(txt, filing_date)
        if ed and _valid(ed, filing_date):
            return ed, conf, "ok"
        ed_b, conf_b, _ = _find_effective_date_in_text(txt)
        if ed_b and conf_b == "HIGH" and _valid(ed_b, filing_date):
            return ed_b, "ELECTION-DATE", "ok-body"
        return "", "", (conf or "no-election")

    if form == "485BXT":
        # A 485BXT designates its NEW effective date on the SAME cover-page checkbox
        # election a 485APOS uses (typically "[X] on <date> pursuant to paragraph (b)").
        # That checked election IS the new designated date the BXT delays/replaces to.
        ed_el, conf_el = parse_election_robust(txt, filing_date)
        if ed_el and _valid(ed_el, filing_date):
            return ed_el, "BXT-NEW", "ok-election"
        # fallback: an explicit body "designating <date> as the new effective date".
        ed_b, conf_b, _ = _find_effective_date_in_text(txt)
        if ed_b and conf_b == "HIGH" and _valid(ed_b, filing_date):
            return ed_b, "BXT-NEW", "ok"
        hdr = _extract_effectiveness_from_hdr(txt)
        if hdr and _valid(hdr, filing_date):
            return hdr, "BXT-HEADER", "ok-hdr"
        if ed_b and conf_b == "MEDIUM" and _valid(ed_b, filing_date):
            return ed_b, "BXT-MEDIUM", "ok-medium"
        return "", "", "no-new-date"

    hdr = _extract_effectiveness_from_hdr(txt)
    if hdr and _valid(hdr, filing_date):
        return hdr, "BPOS-HEADER", "ok"
    ed_b, conf_b, _ = _find_effective_date_in_text(txt)
    if ed_b and conf_b == "HIGH" and _valid(ed_b, filing_date):
        return ed_b, "BPOS-BODY", "ok-body"
    # last resort: a checked cover-page election (some 485BPOS elect "on <date>
    # pursuant to (b)" rather than carry an SGML EFFECTIVENESS DATE header).
    ed_el, _conf_el = parse_election_robust(txt, filing_date)
    if ed_el and _valid(ed_el, filing_date):
        return ed_el, "BPOS-BODY", "ok-election"
    return "", "", "no-effective-date"


_AUTHORITATIVE = {
    "ELECTION", "ELECTION-DATE", "BXT-NEW", "BXT-HEADER",
    "BPOS-HEADER", "BPOS-BODY",
}


def li_filings(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    sql = f"""
        SELECT DISTINCT f.id AS filing_id, f.form, f.accession_number,
               f.filing_date, f.submission_txt_link
        FROM fund_extractions fe
        JOIN filings f ON f.id = fe.filing_id
        WHERE f.form IN ('485APOS', '485BXT', '485BPOS')
          AND {_LI_SERIES}
        ORDER BY f.filing_date DESC, f.id DESC
    """
    return conn.execute(sql).fetchall()


def scrape(conn: sqlite3.Connection, args) -> dict:
    rows = li_filings(conn)
    if args.limit:
        rows = rows[: args.limit]

    done: set[int] = set()
    if not args.refetch:
        done = {r[0] for r in conn.execute(
            "SELECT filing_id FROM li_eff_scrape_progress")}
    todo = [r for r in rows if r["filing_id"] not in done]
    log.info("L&I 485-family filings: %d total | %d already parsed | %d to scrape",
             len(rows), len(rows) - len(todo), len(todo))

    client = SECClient(user_agent=USER_AGENT_DEFAULT, pause=args.pause)
    stats = {"parsed": 0, "dated": 0, "dateless": 0, "errors": 0,
             "fe_rows_written": 0}
    t0 = time.time()

    for i, r in enumerate(todo, 1):
        ed, conf, reason = parse_filing(
            client, r["form"], r["filing_date"], r["submission_txt_link"])
        if ed:
            stats["dated"] += 1
            if not args.dry_run:
                cur = conn.execute(
                    "UPDATE fund_extractions "
                    "SET effective_date=?, effective_date_confidence=? "
                    "WHERE filing_id=?",
                    (ed, conf, r["filing_id"]),
                )
                stats["fe_rows_written"] += cur.rowcount
        else:
            if reason in ("fetch-error", "no-url", "empty"):
                stats["errors"] += 1
            else:
                stats["dateless"] += 1

        if not args.dry_run:
            conn.execute(
                "INSERT OR REPLACE INTO li_eff_scrape_progress "
                "(filing_id, accession, form, effective_date, confidence, reason, parsed_at) "
                "VALUES (?,?,?,?,?,?,datetime('now'))",
                (r["filing_id"], r["accession_number"], r["form"], ed or None,
                 conf or None, reason),
            )
            conn.commit()
        stats["parsed"] += 1

        if ed and (i <= 20 or i % 200 == 0):
            log.info("[%d/%d] %s %s -> %s (%s)", i, len(todo),
                     r["accession_number"], r["form"], ed, conf)
        if i % 100 == 0:
            el = time.time() - t0
            rate = i / el if el else 0
            eta = (len(todo) - i) / rate / 60 if rate else 0
            log.info("progress %d/%d | dated=%d dateless=%d errors=%d | "
                     "%.1f/s ETA %.0f min", i, len(todo), stats["dated"],
                     stats["dateless"], stats["errors"], rate, eta)

    log.info("SCRAPE DONE: parsed=%d dated=%d dateless=%d errors=%d "
             "fe_rows_written=%d | %.0fs", stats["parsed"], stats["dated"],
             stats["dateless"], stats["errors"], stats["fe_rows_written"],
             time.time() - t0)
    return stats


def _is_admin_overridden(mef) -> bool:
    return bool(mef) and "estimated_effective_date" in mef


def derive(conn: sqlite3.Connection, apply: bool) -> dict:
    stats = {"series": 0, "fs_set": 0, "fs_overwrote": 0, "fs_status_eff": 0,
             "rp_set": 0, "rp_skipped_override": 0, "series_dateless": 0,
             "series_497_confirmed": 0}

    series_ids = [r[0] for r in conn.execute(
        f"SELECT DISTINCT fe.series_id FROM fund_extractions fe "
        f"WHERE {_LI_SERIES} AND fe.series_id IS NOT NULL "
        f"AND TRIM(fe.series_id) <> ''")]

    LATEST_485 = """
        SELECT fe.effective_date, fe.effective_date_confidence, f.filing_date
        FROM fund_extractions fe JOIN filings f ON f.id = fe.filing_id
        WHERE fe.series_id = ?
          AND fe.effective_date IS NOT NULL AND TRIM(fe.effective_date) <> ''
          AND f.form IN ('485APOS','485BXT','485BPOS')
        ORDER BY f.filing_date DESC, f.id DESC LIMIT 1
    """
    HAS_497 = """
        SELECT MIN(f.filing_date) FROM fund_extractions fe
        JOIN filings f ON f.id = fe.filing_id
        WHERE fe.series_id = ? AND f.form IN ('497','497K')
    """

    for sid in series_ids:
        stats["series"] += 1
        hit = conn.execute(LATEST_485, (sid,)).fetchone()
        eff_date = hit[0] if hit else None
        eff_conf = hit[1] if hit else None
        confirm_497 = conn.execute(HAS_497, (sid,)).fetchone()[0]
        if confirm_497:
            stats["series_497_confirmed"] += 1
        if not eff_date:
            stats["series_dateless"] += 1

        authoritative = eff_conf in _AUTHORITATIVE if eff_conf else False
        is_effective = bool((eff_date and eff_date <= TODAY) or confirm_497)

        for fsr in conn.execute(
            "SELECT id, ticker, fund_name, effective_date, "
            "effective_date_confidence, status FROM fund_status WHERE series_id=?",
            (sid,),
        ).fetchall():
            fid, tkr, fname, cur_date, cur_conf, cur_status = fsr
            cur_date_s = str(cur_date or "").strip()
            new_status = "Effective" if is_effective else cur_status

            set_date = False
            if eff_date:
                if not cur_date_s:
                    set_date = True
                elif authoritative and eff_date != cur_date_s and \
                        (cur_conf or "") not in _AUTHORITATIVE:
                    set_date = True
                    stats["fs_overwrote"] += 1

            if set_date or (new_status != cur_status):
                if set_date:
                    stats["fs_set"] += 1
                if new_status == "Effective" and cur_status != "Effective":
                    stats["fs_status_eff"] += 1
                if apply:
                    if set_date:
                        conn.execute(
                            "UPDATE fund_status SET effective_date=?, "
                            "effective_date_confidence=?, status=?, "
                            "updated_at=datetime('now') WHERE id=?",
                            (eff_date, eff_conf, new_status, fid))
                    else:
                        conn.execute(
                            "UPDATE fund_status SET status=?, "
                            "updated_at=datetime('now') WHERE id=?",
                            (new_status, fid))

        for rpr in conn.execute(
            "SELECT id, ticker, name, estimated_effective_date, "
            "manually_edited_fields FROM rex_products WHERE series_id=?",
            (sid,),
        ).fetchall():
            rid, tkr, name, cur_date, mef = rpr
            if _is_admin_overridden(mef):
                stats["rp_skipped_override"] += 1
                continue
            cur_date_s = str(cur_date or "").strip()
            if not eff_date:
                continue
            if (not cur_date_s) or (authoritative and eff_date != cur_date_s):
                stats["rp_set"] += 1
                if apply:
                    conn.execute(
                        "UPDATE rex_products SET estimated_effective_date=?, "
                        "updated_at=datetime('now') WHERE id=?",
                        (eff_date, rid))

    if apply:
        conn.commit()
    log.info("DERIVE %s: series=%d | fs_set=%d (overwrote=%d) fs->Effective=%d | "
             "rp_set=%d (override skip=%d) | series 497-confirmed=%d dateless=%d",
             "APPLIED" if apply else "DRY", stats["series"], stats["fs_set"],
             stats["fs_overwrote"], stats["fs_status_eff"], stats["rp_set"],
             stats["rp_skipped_override"], stats["series_497_confirmed"],
             stats["series_dateless"])
    return stats


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--refetch", action="store_true")
    ap.add_argument("--derive-only", action="store_true")
    ap.add_argument("--pause", type=float, default=0.15)
    args = ap.parse_args()

    (ROOT / "logs").mkdir(exist_ok=True)
    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row
    ensure_progress_table(conn)

    if not args.derive_only:
        scrape(conn, args)

    apply = args.apply and not args.dry_run
    derive(conn, apply)

    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
