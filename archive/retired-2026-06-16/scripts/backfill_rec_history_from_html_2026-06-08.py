raise SystemExit("RETIRED 2026-06-16 - quarantined to archive/retired-2026-06-16/, do not run. 3-gate proven 0 live refs; pending final sweep.")
"""Backfill recommendation_history from archived weekly HTML reports.

Wave E1 wire-up (commit 5a91fa5) only catches NEW sends. This script
reconstructs the historical record from archived li_weekly_v2_*.html and
trex_combined_*.html reports under /home/jarvis/rexfinhub/reports/.

DRY-RUN by default. The --apply path is intentionally gated — Ryu must
approve the dry-run output before any DB writes happen.

Assumptions documented inline:
  - week_of for a report = monday_of(report_date) where report_date is from
    the filename (e.g. li_weekly_v2_2026-06-05.html -> 2026-06-05).
  - Three distinct HTML layouts exist across the archive window:
      A) Pre-v2 (Apr 27 - May 7-8): "Top Launch Recommendations" +
         "Top Filing Recommendations" cards. No tier badges, no scores.
         Tier assigned by rank (top 3 = HIGH, next 4 = MEDIUM, rest = WATCH)
         per recommendation_history._tier_for().
      B) v3 intermediate (May 12 only): Defensive + Offensive + Watch tables.
         Per-row tier inferable from which table the ticker is in.
      C) v3 current (May 19+): Defensive + Offensive + Foreign tables (NO
         Watch -- watch_html is computed but never inserted into the template,
         see weekly_v2_report.py line 2587 vs 2762+). The Highlights box
         declares "N HIGH | M MEDIUM | P WATCH" counts but does NOT list
         WATCH tickers. For these reports we can only recover Defensive +
         Offensive rows.
  - Section taxonomy (per RecRow.section in recommendation_history.py):
      * Defensive table -> section='filing' (whitespace where competitors
        already filed; per build_rows_from_renderer pattern). Tier='WATCH'
        because we have no score for these (they come from competitor filings,
        not the candidate-score universe) and we should not over-promote.
      * Offensive table -> section='filing' (true whitespace shots). Tier
        assigned by rank order.
      * Top Launch (pre-v2) -> section='launch'.
      * Top Filing (pre-v2) -> section='filing'.
  - Tier assignment in Offensive (v3) and pre-v2 Top X sections:
      use _tier_for(rank): top 3 = HIGH, next 4 = MEDIUM, rest = WATCH.
      Matches the default rule in build_rows_from_renderer.
  - composite_score is NOT present in the rendered HTML -- the v3 table
    columns omit it. We set composite_score=None for all backfilled rows.
    This is correct: we cannot fabricate a score we don't have.
  - thesis_snippet: where the card layout has company name + sector +
    metrics inline, we capture "company (sector)" as a snippet. Where
    nothing is available we leave None.
  - trex_combined_*.html: section 2 ("Our Pipeline") -> launch,
    section 3 ("Whitespace Ranking" / "Strict Whitespace") -> filing.

Output:
  - Dry-run MD at C:/Projects/temp/preview-decisions/rec_history_backfill_dryrun.md
  - JSON staging at C:/Projects/temp/preview-decisions/rec_history_backfill_staged.json

Cross-validation:
  - For reports with run_date in li_engine_daily (2026-05-29, 06-01..06-05),
    confirm that HIGH+MEDIUM tier tickers from the report appear in the
    li_engine_daily snapshot within +/- 3 days. Pure presence-check -- we
    cannot compare scores since they're not in HTML.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from bs4 import BeautifulSoup

# Repo path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from screener.li_engine.analysis.recommendation_history import (  # noqa: E402
    RecRow, monday_of, _tier_for,
)

REPORTS_DIR = Path("C:/Projects/temp/li_reports_archive")
READONLY_DB = Path("C:/Projects/temp/etp_tracker_readonly.db")
OUT_DIR = Path("C:/Projects/temp/preview-decisions")
OUT_DIR.mkdir(parents=True, exist_ok=True)

DRYRUN_MD = OUT_DIR / "rec_history_backfill_dryrun.md"
STAGED_JSON = OUT_DIR / "rec_history_backfill_staged.json"

# Layouts
LAYOUT_PREV2 = "prev2"          # Apr 27 - May 7-8
LAYOUT_V3_WATCH = "v3_watch"    # May 12 (intermediate)
LAYOUT_V3 = "v3"                # May 19+
LAYOUT_TREX = "trex"            # trex_combined_*

_FILE_DATE_RE = re.compile(r"_(\d{4}-\d{2}-\d{2})\.html$")


def _file_date(path: Path) -> date | None:
    m = _FILE_DATE_RE.search(path.name)
    if not m:
        return None
    return datetime.strptime(m.group(1), "%Y-%m-%d").date()


# ---------------------------------------------------------------------------
# Layout detection
# ---------------------------------------------------------------------------
def _detect_layout(soup: BeautifulSoup, path: Path) -> str:
    if path.name.startswith("trex_combined"):
        return LAYOUT_TREX
    headers = [
        d.get_text(" ", strip=True)
        for d in soup.find_all("div", style=re.compile("border-bottom:2px solid"))
    ]
    joined = " | ".join(headers)
    if "Top Launch Recommendations" in joined:
        return LAYOUT_PREV2
    if "Watch" in joined and "Early Signals" in joined:
        return LAYOUT_V3_WATCH
    if "Defensive" in joined and "Offensive" in joined:
        return LAYOUT_V3
    return "unknown"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
# Ticker shape: 1-10 chars, A-Z 0-9 . -, must start with a letter.
_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def _extract_section_table(soup: BeautifulSoup, header_text: str):
    """Find a section header div whose text contains header_text, return
    the first <table> that follows it."""
    for div in soup.find_all("div", style=re.compile("border-bottom:2px solid")):
        if header_text in div.get_text(" ", strip=True):
            return div.find_next("table")
    return None


def _table_tickers_ordered(table) -> list[tuple[str, str | None, str | None]]:
    """From a recs table (Defensive/Offensive/Top X), return ordered list of
    (ticker, company, sector) tuples.

    The renderer marks ticker cells with Courier monospace font + blue
    color (#0984e3). This appears as either:
      - inline style on the <td> directly (v3 layout), or
      - a nested <span> with the same style (pre-v2 card layout).
    We scan both.
    """
    if table is None:
        return []
    out: list[tuple[str, str | None, str | None]] = []
    for tr in table.find_all("tr"):
        tds = tr.find_all("td")
        if not tds:
            continue
        # find the index of the ticker cell
        ticker_idx = None
        ticker = None
        for i, td in enumerate(tds):
            style = td.get("style", "") or ""
            if "Courier" in style:
                txt = td.get_text(" ", strip=True)
                # Strip away nested chip/badge text -- ticker is the first token
                cand = txt.split()[0] if txt else ""
                if _TICKER_RE.match(cand):
                    ticker_idx = i
                    ticker = cand
                    break
            # nested span fallback
            sp = td.find("span", style=re.compile("Courier"))
            if sp:
                txt = sp.get_text(" ", strip=True)
                cand = txt.split()[0] if txt else ""
                if _TICKER_RE.match(cand):
                    ticker_idx = i
                    ticker = cand
                    break
        if ticker is None:
            continue
        company = None
        sector = None
        if ticker_idx + 1 < len(tds):
            company = tds[ticker_idx + 1].get_text(" ", strip=True)[:200] or None
        if ticker_idx + 2 < len(tds):
            sector = tds[ticker_idx + 2].get_text(" ", strip=True)[:80] or None
        out.append((ticker, company, sector))
    return out


def _prev2_section_tickers(soup: BeautifulSoup, header_text: str) -> list[tuple[str, str | None, str | None]]:
    """Pre-v2 layout uses cards, not tabular rows. Walk every monospace
    ticker span inside the table after the given section header, dedupe
    preserving order."""
    table = _extract_section_table(soup, header_text)
    if table is None:
        return []
    seen: set[str] = set()
    out: list[tuple[str, str | None, str | None]] = []
    for s in table.find_all("span", style=re.compile("Courier")):
        ticker = s.get_text(strip=True).upper().split()[0] if s.get_text(strip=True) else ""
        if not _TICKER_RE.match(ticker):
            continue
        if ticker in seen:
            continue
        seen.add(ticker)
        out.append((ticker, None, None))
    return out


# ---------------------------------------------------------------------------
# Per-layout parsers
# ---------------------------------------------------------------------------
def parse_v3(soup: BeautifulSoup, week_of: date) -> list[RecRow]:
    rows: list[RecRow] = []

    # Defensive -- section='filing', tier='WATCH' (no score available)
    def_table = _extract_section_table(soup, "Defensive")
    for ticker, company, sector in _table_tickers_ordered(def_table):
        rows.append(RecRow(
            week_of=week_of,
            ticker=ticker,
            confidence_tier="WATCH",
            composite_score=None,
            fund_name=company,
            thesis_snippet=(f"{company} ({sector})" if company and sector else (company or None)),
            suggested_rex_ticker=None,
            section="filing",
        ))

    # Offensive -- section='filing', tier via _tier_for(rank)
    off_table = _extract_section_table(soup, "Offensive")
    off_rows = _table_tickers_ordered(off_table)
    for rank, (ticker, company, sector) in enumerate(off_rows):
        rows.append(RecRow(
            week_of=week_of,
            ticker=ticker,
            confidence_tier=_tier_for(None, rank),
            composite_score=None,
            fund_name=company,
            thesis_snippet=(f"{company} ({sector})" if company and sector else (company or None)),
            suggested_rex_ticker=None,
            section="filing",
        ))
    return rows


def parse_v3_watch(soup: BeautifulSoup, week_of: date) -> list[RecRow]:
    """May 12 layout: Defensive + Offensive + Watch."""
    rows = parse_v3(soup, week_of)
    watch_table = _extract_section_table(soup, "Watch")
    for ticker, company, sector in _table_tickers_ordered(watch_table):
        rows.append(RecRow(
            week_of=week_of,
            ticker=ticker,
            confidence_tier="WATCH",
            composite_score=None,
            fund_name=company,
            thesis_snippet=(f"{company} ({sector})" if company and sector else (company or None)),
            suggested_rex_ticker=None,
            section="filing",
        ))
    return rows


def parse_prev2(soup: BeautifulSoup, week_of: date) -> list[RecRow]:
    rows: list[RecRow] = []
    launch = _prev2_section_tickers(soup, "Top Launch Recommendations")
    for rank, (ticker, _, _) in enumerate(launch):
        rows.append(RecRow(
            week_of=week_of,
            ticker=ticker,
            confidence_tier=_tier_for(None, rank),
            composite_score=None,
            fund_name=None,
            thesis_snippet=None,
            suggested_rex_ticker=None,
            section="launch",
        ))
    filing = _prev2_section_tickers(soup, "Top Filing Recommendations")
    for rank, (ticker, _, _) in enumerate(filing):
        rows.append(RecRow(
            week_of=week_of,
            ticker=ticker,
            confidence_tier=_tier_for(None, rank),
            composite_score=None,
            fund_name=None,
            thesis_snippet=None,
            suggested_rex_ticker=None,
            section="filing",
        ))
    return rows


def parse_trex(soup: BeautifulSoup, week_of: date) -> list[RecRow]:
    """trex_combined: section 2 (Our Pipeline) -> launch,
    section 3 (Whitespace) -> filing."""
    rows: list[RecRow] = []

    def _find_section(needles: list[str]):
        for div in soup.find_all("div", style=re.compile("border-bottom:2px solid")):
            txt = div.get_text(" ", strip=True)
            for needle in needles:
                if needle in txt:
                    return div.find_next("table")
        return None

    pipe = _find_section(["Our Pipeline"])
    for rank, (ticker, company, _) in enumerate(_table_tickers_ordered(pipe)):
        rows.append(RecRow(
            week_of=week_of,
            ticker=ticker,
            confidence_tier=_tier_for(None, rank),
            composite_score=None,
            fund_name=company,
            thesis_snippet=company,
            suggested_rex_ticker=None,
            section="launch",
        ))

    ws = _find_section(["Whitespace Ranking", "Strict Whitespace"])
    for rank, (ticker, company, _) in enumerate(_table_tickers_ordered(ws)):
        rows.append(RecRow(
            week_of=week_of,
            ticker=ticker,
            confidence_tier=_tier_for(None, rank),
            composite_score=None,
            fund_name=company,
            thesis_snippet=company,
            suggested_rex_ticker=None,
            section="filing",
        ))
    return rows


PARSERS = {
    LAYOUT_PREV2: parse_prev2,
    LAYOUT_V3_WATCH: parse_v3_watch,
    LAYOUT_V3: parse_v3,
    LAYOUT_TREX: parse_trex,
}


# ---------------------------------------------------------------------------
# Cross-validation against li_engine_daily
# ---------------------------------------------------------------------------
def _li_engine_tickers_for_date(conn, run_date: date, window_days: int = 3) -> set[str]:
    cur = conn.cursor()
    start = (run_date - timedelta(days=window_days)).isoformat()
    end = (run_date + timedelta(days=window_days)).isoformat()
    cur.execute(
        "SELECT DISTINCT ticker FROM li_engine_daily WHERE run_date BETWEEN ? AND ?",
        (start, end),
    )
    return {r[0].upper() for r in cur.fetchall() if r[0]}


def cross_validate(rows: list[RecRow], report_date: date, conn) -> dict:
    """Check whether HIGH+MEDIUM tier rows (the Offensive whitespace rank top
    of the candidate universe) appear in li_engine_daily near the report
    date."""
    universe = _li_engine_tickers_for_date(conn, report_date, window_days=3)
    if not universe:
        return {"applicable": False, "reason": f"no li_engine_daily within +/-3d of {report_date}"}
    checked = [r for r in rows if r.confidence_tier in ("HIGH", "MEDIUM")]
    if not checked:
        return {"applicable": False, "reason": "no HIGH/MEDIUM rows in report"}
    hits = sum(1 for r in checked if r.ticker.upper() in universe)
    return {
        "applicable": True,
        "report_date": report_date.isoformat(),
        "universe_size": len(universe),
        "checked_rows": len(checked),
        "hits": hits,
        "match_rate": round(hits / len(checked), 3),
        "misses": [r.ticker for r in checked if r.ticker.upper() not in universe],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def process_all() -> dict:
    report_files = sorted(REPORTS_DIR.glob("*.html"))
    per_report: list[dict] = []
    all_rows: list[RecRow] = []
    parse_failures: list[dict] = []

    conn = sqlite3.connect(f"file:{READONLY_DB}?mode=ro", uri=True)

    for path in report_files:
        rd = _file_date(path)
        if rd is None:
            parse_failures.append({"file": path.name, "reason": "no date in filename"})
            continue
        with open(path, encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
        layout = _detect_layout(soup, path)
        if layout not in PARSERS:
            parse_failures.append({"file": path.name, "reason": f"unknown layout: {layout}"})
            continue
        rows = PARSERS[layout](soup, monday_of(rd))
        if not rows:
            # Fail-loud per accuracy rule -- record but keep going.
            parse_failures.append({"file": path.name, "reason": f"layout={layout} but zero rows extracted"})
            continue

        # Dedupe within the report by (ticker, tier, section) -- keep first
        seen = set()
        deduped = []
        for r in rows:
            key = (r.ticker.upper(), r.confidence_tier, r.section)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)

        xv = cross_validate(deduped, rd, conn)
        per_report.append({
            "file": path.name,
            "report_date": rd.isoformat(),
            "week_of": monday_of(rd).isoformat(),
            "layout": layout,
            "rows": len(deduped),
            "tiers": {
                "HIGH": sum(1 for r in deduped if r.confidence_tier == "HIGH"),
                "MEDIUM": sum(1 for r in deduped if r.confidence_tier == "MEDIUM"),
                "WATCH": sum(1 for r in deduped if r.confidence_tier == "WATCH"),
            },
            "sections": {
                "launch": sum(1 for r in deduped if r.section == "launch"),
                "filing": sum(1 for r in deduped if r.section == "filing"),
            },
            "cross_validation": xv,
        })
        all_rows.extend(deduped)

    conn.close()

    # Collapse across reports for the same week_of (Monday AND Thursday/Friday
    # reports share a week_of). Same dedupe key as the prod UNIQUE constraint.
    final_seen: set[tuple] = set()
    final_rows: list[RecRow] = []
    week_collisions = 0
    for r in all_rows:
        key = (r.week_of.isoformat(), r.ticker.upper(), r.confidence_tier.upper())
        if key in final_seen:
            week_collisions += 1
            continue
        final_seen.add(key)
        final_rows.append(r)

    week_dist: dict[str, int] = {}
    for r in final_rows:
        w = r.week_of.isoformat()
        week_dist[w] = week_dist.get(w, 0) + 1

    tier_dist = {
        "HIGH": sum(1 for r in final_rows if r.confidence_tier == "HIGH"),
        "MEDIUM": sum(1 for r in final_rows if r.confidence_tier == "MEDIUM"),
        "WATCH": sum(1 for r in final_rows if r.confidence_tier == "WATCH"),
    }
    section_dist = {
        "launch": sum(1 for r in final_rows if r.section == "launch"),
        "filing": sum(1 for r in final_rows if r.section == "filing"),
    }

    STAGED_JSON.write_text(json.dumps(
        [{
            "week_of": r.week_of.isoformat(),
            "ticker": r.ticker,
            "confidence_tier": r.confidence_tier,
            "composite_score": r.composite_score,
            "fund_name": r.fund_name,
            "thesis_snippet": r.thesis_snippet,
            "suggested_rex_ticker": r.suggested_rex_ticker,
            "section": r.section,
        } for r in final_rows],
        indent=2,
        default=str,
    ), encoding="utf-8")

    md = [
        "# Recommendation History Backfill -- DRY-RUN",
        "",
        f"Generated: {datetime.utcnow().isoformat(timespec='seconds')}Z",
        f"Source: {REPORTS_DIR}",
        f"Reports scanned: {len(report_files)}",
        f"Reports parsed: {len(per_report)}",
        f"Reports failed: {len(parse_failures)}",
        f"Within-week collisions deduped: {week_collisions}",
        "",
        "## Summary",
        "",
        f"- **Total rows that WOULD be inserted: {len(final_rows)}**",
        f"- Weeks covered: {len(week_dist)}",
        f"- Tier distribution: HIGH={tier_dist['HIGH']}, MEDIUM={tier_dist['MEDIUM']}, WATCH={tier_dist['WATCH']}",
        f"- Section distribution: launch={section_dist['launch']}, filing={section_dist['filing']}",
        "",
        "## Rows per week_of (Monday-normalized)",
        "",
        "| week_of | row count |",
        "|---|---|",
    ]
    for w in sorted(week_dist):
        md.append(f"| {w} | {week_dist[w]} |")

    md += [
        "",
        "## Cross-validation against li_engine_daily",
        "",
        "For reports with run_date in li_engine_daily (2026-05-29, 2026-06-01..06-05),",
        "we check whether HIGH+MEDIUM tier tickers from the report appear in the",
        "li_engine_daily universe within +/- 3 days. (Score comparison is not possible",
        "-- the HTML reports do not include composite_score per row in any layout.)",
        "",
        "| file | applicable | checked | hits | match_rate | misses |",
        "|---|---|---|---|---|---|",
    ]
    for pr in per_report:
        xv = pr["cross_validation"]
        if xv.get("applicable"):
            misses = ", ".join(xv.get("misses", [])[:8])
            if len(xv.get("misses", [])) > 8:
                misses += f" (+{len(xv['misses'])-8} more)"
            md.append(
                f"| {pr['file']} | yes | {xv['checked_rows']} | {xv['hits']} | "
                f"{xv['match_rate']:.0%} | {misses} |"
            )
        else:
            md.append(f"| {pr['file']} | no | - | - | - | {xv.get('reason','')} |")

    md += [
        "",
        "## Per-report breakdown",
        "",
        "| file | layout | rows | HIGH | MEDIUM | WATCH | launch | filing |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for pr in per_report:
        md.append(
            f"| {pr['file']} | {pr['layout']} | {pr['rows']} | "
            f"{pr['tiers']['HIGH']} | {pr['tiers']['MEDIUM']} | {pr['tiers']['WATCH']} | "
            f"{pr['sections']['launch']} | {pr['sections']['filing']} |"
        )

    if parse_failures:
        md += ["", "## Reports that failed to parse", ""]
        for f in parse_failures:
            md.append(f"- `{f['file']}` -- {f['reason']}")

    # Spot-checks: pick 3 from the most recent week
    md += ["", "## Spot-check candidates from most recent week", ""]
    if week_dist:
        latest_week = max(week_dist.keys())
        spot_candidates = [r for r in final_rows if r.week_of.isoformat() == latest_week]
        for r in spot_candidates[:3]:
            md.append(
                f"- **{r.ticker}** week_of={r.week_of} tier={r.confidence_tier} "
                f"section={r.section} fund_name={r.fund_name!r}"
            )

    md += ["", "## Sample of 15 rows (RecRow shape)", "", "```json"]
    md.append(json.dumps([
        {
            "week_of": r.week_of.isoformat(),
            "ticker": r.ticker,
            "confidence_tier": r.confidence_tier,
            "composite_score": r.composite_score,
            "fund_name": r.fund_name,
            "thesis_snippet": r.thesis_snippet,
            "section": r.section,
        } for r in final_rows[:15]
    ], indent=2, default=str))
    md.append("```")

    DRYRUN_MD.write_text("\n".join(md), encoding="utf-8")

    return {
        "reports_scanned": len(report_files),
        "reports_parsed": len(per_report),
        "reports_failed": len(parse_failures),
        "total_rows_staged": len(final_rows),
        "weeks_covered": len(week_dist),
        "tier_distribution": tier_dist,
        "section_distribution": section_dist,
        "within_week_collisions_deduped": week_collisions,
        "dryrun_md": str(DRYRUN_MD),
        "staged_json": str(STAGED_JSON),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true",
                        help="Disabled in this build. Run dry-run, await Ryu's go.")
    args = parser.parse_args()
    if args.apply:
        print("ERROR: --apply path is gated. Run the dry-run, surface to Ryu, "
              "and only enable --apply after explicit approval.")
        sys.exit(2)
    summary = process_all()
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
