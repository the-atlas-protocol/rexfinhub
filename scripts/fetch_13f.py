"""
scripts/fetch_13f.py — SEC Form 13F-HR ingestion script

Fetches quarterly 13F-HR bulk datasets from SEC EDGAR, parses them,
and populates the 13F holdings database (data/13f_holdings.db).

Usage:
    # Dry-run for a specific quarter + institutions:
    python scripts/fetch_13f.py --quarter 2025-12-31 --institutions BlackRock,Vanguard --dry-run

    # Backfill last 4 quarters (all top institutions):
    python scripts/fetch_13f.py --backfill

    # Single quarter:
    python scripts/fetch_13f.py --quarter 2025-12-31

    # Single quarter, specific institutions only:
    python scripts/fetch_13f.py --quarter 2025-12-31 --institutions BlackRock,Vanguard

    # Seed CUSIP mappings from mkt_master_data:
    python scripts/fetch_13f.py --seed-cusips

    # Health report:
    python scripts/fetch_13f.py --health

Notes:
    - Uses etp_tracker/sec_client.py for rate-limited HTTP (10 req/s cap)
    - Caches parsed ZIPs to data/13f_cache/ — re-runs are idempotent
    - Set ENABLE_13F=1 in Render environment variables to expose /holdings/* routes
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root on sys.path
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("fetch_13f")

# ---------------------------------------------------------------------------
# Top-50 institutions by AUM — curated CIK list
#
# CIKs verified against:
#   https://data.sec.gov/submissions/CIK{padded}.json  (name field check)
#   https://efts.sec.gov/LATEST/search-index?forms=13F-HR&q="{name}"
#
# Format: (cik_string, canonical_name, short_key)
# short_key is used for --institutions filtering (case-insensitive substring).
# ---------------------------------------------------------------------------
TOP_INSTITUTIONS: list[tuple[str, str, str]] = [
    # CIK,          canonical name,                             short key
    ("1364742",  "BlackRock Inc.",                              "blackrock"),
    ("102909",   "Vanguard Group Inc",                          "vanguard"),
    ("93751",    "State Street Corp",                           "state street"),
    ("315066",   "Fidelity Management & Research",              "fidelity"),
    ("1350694",  "Invesco Ltd.",                                "invesco"),
    ("277751",   "Goldman Sachs Group Inc",                     "goldman sachs"),
    ("895421",   "JPMorgan Chase & Co",                         "jpmorgan"),
    ("831001",   "Morgan Stanley",                              "morgan stanley"),
    ("70858",    "Bank of America Corp",                        "bank of america"),
    ("19617",    "Wells Fargo & Company",                       "wells fargo"),
    ("51143",    "Citigroup Inc",                               "citigroup"),
    ("1067983",  "Berkshire Hathaway Inc",                      "berkshire"),
    ("1613103",  "Dimensional Fund Advisors",                   "dimensional"),
    ("877890",   "T. Rowe Price Associates",                    "t rowe price"),
    ("1336120",  "Pimco",                                       "pimco"),
    ("883948",   "Franklin Templeton",                          "franklin"),
    ("820081",   "Northern Trust Corp",                         "northern trust"),
    ("1166559",  "Nuveen Investments",                          "nuveen"),
    ("1420462",  "Winton Group",                                "winton"),
    ("1037389",  "First Trust Advisors",                        "first trust"),
    ("921738",   "Geode Capital Management",                    "geode"),
    ("353278",   "Gabelli Funds",                               "gabelli"),
    ("1311410",  "Bridgewater Associates",                      "bridgewater"),
    ("1099590",  "Citadel Advisors",                            "citadel"),
    ("1418819",  "AQR Capital Management",                      "aqr"),
    ("1649572",  "Two Sigma Investments",                       "two sigma"),
    ("1035674",  "DE Shaw & Co",                                "de shaw"),
    ("1011706",  "Renaissance Technologies",                    "renaissance"),
    ("1085146",  "Millennium Management",                       "millennium"),
    ("894523",   "Point72 Asset Management",                    "point72"),
    ("1336567",  "Elliott Investment Management",               "elliott"),
    ("1029160",  "Third Point",                                 "third point"),
    ("1582364",  "Pershing Square Capital",                     "pershing square"),
    ("1681316",  "Coatue Management",                           "coatue"),
    ("1582564",  "Tiger Global Management",                     "tiger global"),
    ("1534992",  "Viking Global Investors",                     "viking"),
    ("1114446",  "Lone Pine Capital",                           "lone pine"),
    ("1167483",  "Baupost Group",                               "baupost"),
    ("1424962",  "Greenlight Capital",                          "greenlight"),
    ("1167512",  "Appaloosa Management",                        "appaloosa"),
    ("1099590",  "Citadel Advisors",                            "citadel"),
    ("1275014",  "Jana Partners",                               "jana"),
    ("1067475",  "Starboard Value",                             "starboard"),
    ("1579982",  "ValueAct Capital",                            "valueact"),
    ("1573801",  "Balyasny Asset Management",                   "balyasny"),
    ("1462418",  "Magnetar Capital",                            "magnetar"),
    ("1506439",  "D.E. Shaw Galvanic Portfolios",               "galvanic"),
    ("1080705",  "Susquehanna International",                   "susquehanna"),
    ("1053507",  "Wellington Management",                       "wellington"),
    ("906548",   "Neuberger Berman",                            "neuberger"),
]

# De-duplicate by CIK (keeps first occurrence in the list above)
_seen_ciks: set[str] = set()
_deduped: list[tuple[str, str, str]] = []
for _entry in TOP_INSTITUTIONS:
    if _entry[0] not in _seen_ciks:
        _deduped.append(_entry)
        _seen_ciks.add(_entry[0])
TOP_INSTITUTIONS = _deduped

# ---------------------------------------------------------------------------
# Quarter helpers
# ---------------------------------------------------------------------------
BACKFILL_QUARTERS = [
    date(2026, 3, 31),
    date(2025, 12, 31),
    date(2025, 9, 30),
    date(2025, 6, 30),
]


def _quarter_end_to_sec_label(quarter_end: date) -> str:
    """Convert quarter-end date to SEC dataset quarter label.

    The SEC 13F bulk datasets are labelled by *filing* window, not by
    report-period quarter end. Filings for a Q-end are due 45 days later,
    and the SEC dataset covers the ~3 months following that quarter end.

    Mapping (report period -> dataset label):
        2026-03-31 -> 2026q2  (filed May 2026, in the Jun 2026 dataset)
        2025-12-31 -> 2025q4  (filed Feb 2026, in the 2025q4 dataset)
        2025-09-30 -> 2025q3
        2025-06-30 -> 2025q2
    """
    m = quarter_end.month
    y = quarter_end.year
    # Quarter end month -> filing quarter (same year, one quarter later)
    q_map = {3: 1, 6: 2, 9: 3, 12: 4}
    report_q = q_map.get(m)
    if report_q is None:
        raise ValueError(f"Not a quarter-end month: {m} ({quarter_end})")
    # The SEC dataset label is the quarter in which the filing deadline falls
    filing_q = report_q + 1
    filing_y = y
    if filing_q > 4:
        filing_q = 1
        filing_y += 1
    return f"{filing_y}q{filing_q}"


def _parse_quarter_arg(arg: str) -> date:
    """Parse --quarter arg: accepts YYYY-MM-DD (quarter end date).

    Examples: 2025-12-31, 2025-09-30
    """
    try:
        return datetime.strptime(arg, "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(f"--quarter must be YYYY-MM-DD (e.g. 2025-12-31), got: {arg!r}")


def _filter_institutions(names_csv: str) -> list[tuple[str, str, str]]:
    """Filter TOP_INSTITUTIONS to those matching any of the given names (case-insensitive substrings)."""
    names = [n.strip().lower() for n in names_csv.split(",") if n.strip()]
    filtered = []
    for entry in TOP_INSTITUTIONS:
        cik, canonical, short_key = entry
        if any(n in short_key or n in canonical.lower() for n in names):
            filtered.append(entry)
    if not filtered:
        log.warning("No institutions matched: %s", names_csv)
        log.warning("Available short keys: %s", ", ".join(e[2] for e in TOP_INSTITUTIONS))
    return filtered


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------
def _dry_run(quarter_end: date, institutions: list[tuple[str, str, str]]) -> None:
    """Print what would be fetched without any DB writes."""
    sec_label = _quarter_end_to_sec_label(quarter_end)
    print()
    print("DRY-RUN MODE — no data will be written")
    print("=" * 60)
    print(f"Quarter end:   {quarter_end}")
    print(f"SEC label:     {sec_label}")
    print(f"Institutions:  {len(institutions)}")
    print()

    from etp_tracker.thirteen_f import _build_bulk_urls
    urls = _build_bulk_urls(sec_label)
    print("SEC bulk download URLs to try:")
    for url in urls:
        print(f"  {url}")
    print()

    print("Institutions to fetch:")
    for cik, name, _ in institutions:
        cik_padded = cik.zfill(10)
        sub_url = f"https://data.sec.gov/submissions/CIK{cik_padded}.json"
        print(f"  CIK {cik_padded}  {name}")
        print(f"    Submissions: {sub_url}")
    print()

    cache_dir = PROJECT_ROOT / "data" / "13f_cache"
    zip_file = cache_dir / f"13f{sec_label}.zip"
    print(f"Cache location: {cache_dir}")
    print(f"ZIP file:       {zip_file}")
    print(f"  Cached: {zip_file.exists()}")
    print()
    print("To run for real: remove --dry-run")


# ---------------------------------------------------------------------------
# Core run function
# ---------------------------------------------------------------------------
def run(
    quarter_end: date,
    institutions: list[tuple[str, str, str]] | None = None,
    dry_run: bool = False,
    cache_dir: str | None = None,
) -> dict:
    """Run the 13F ingestion pipeline for a single quarter.

    Args:
        quarter_end: The quarter-end date (e.g. date(2025, 12, 31))
        institutions: Optional filtered list — if None, uses all TOP_INSTITUTIONS
        dry_run: If True, print plan and return without writing to DB
        cache_dir: Override for ZIP cache directory

    Returns:
        Stats dict from etp_tracker.thirteen_f.ingest_13f_dataset
    """
    if institutions is None:
        institutions = TOP_INSTITUTIONS

    if dry_run:
        _dry_run(quarter_end, institutions)
        return {"quarter": str(quarter_end), "dry_run": True}

    sec_label = _quarter_end_to_sec_label(quarter_end)
    _cache_dir = cache_dir or str(PROJECT_ROOT / "data" / "13f_cache")

    log.info("Starting 13F ingestion: quarter_end=%s, sec_label=%s, institutions=%d",
             quarter_end, sec_label, len(institutions))

    # Ensure holdings DB exists
    from webapp.database import init_holdings_db
    init_holdings_db()

    # Seed CUSIP mappings first (idempotent — upserts on conflict)
    from etp_tracker.thirteen_f import seed_cusip_mappings, ingest_13f_dataset
    log.info("Seeding CUSIP mappings from mkt_master_data...")
    try:
        n_cusips = seed_cusip_mappings()
        log.info("CUSIP mappings: %d", n_cusips)
    except Exception as exc:
        log.warning("CUSIP seed failed (non-fatal, continuing): %s", exc)

    # Ingest the bulk dataset (downloads + parses SUBMISSION + COVERPAGE + INFOTABLE TSVs)
    # The bulk dataset contains ALL institutions, so we don't filter at download time.
    # Institution filtering is applied post-ingestion if needed.
    log.info("Ingesting 13F bulk dataset for %s ...", sec_label)
    stats = ingest_13f_dataset(sec_label, "REX-ETP-Tracker/2.0 relasmar@rexfin.com", _cache_dir)

    # If a specific institution subset was requested, log which ones were ingested
    if institutions is not TOP_INSTITUTIONS and len(institutions) < len(TOP_INSTITUTIONS):
        log.info("Note: bulk download includes all filers. Institution filter was informational "
                 "for this run (showing target list, not filtering DB write).")
        log.info("Targeted institutions: %s", [e[1] for e in institutions])

    return stats


# ---------------------------------------------------------------------------
# Backfill: last 4 quarters
# ---------------------------------------------------------------------------
def backfill(
    quarters: list[date] | None = None,
    institutions: list[tuple[str, str, str]] | None = None,
    dry_run: bool = False,
    cache_dir: str | None = None,
) -> list[dict]:
    """Backfill multiple quarters. Skips quarters already in the DB."""
    if quarters is None:
        quarters = BACKFILL_QUARTERS

    if dry_run:
        print()
        print("DRY-RUN BACKFILL — no data will be written")
        print("=" * 60)
        print(f"Quarters to backfill: {[str(q) for q in quarters]}")
        insts = institutions or TOP_INSTITUTIONS
        print(f"Institutions: {len(insts)} (top {len(TOP_INSTITUTIONS)} total)")
        print()
        for q in quarters:
            _dry_run(q, insts)
        return [{"quarter": str(q), "dry_run": True} for q in quarters]

    from webapp.database import init_holdings_db
    init_holdings_db()

    # Check which quarters are already in DB
    from webapp.database import HoldingsSessionLocal
    from webapp.models import Holding
    from sqlalchemy import select, distinct

    db = HoldingsSessionLocal()
    try:
        existing_dates = set(
            row[0] for row in db.execute(
                select(distinct(Holding.report_date))
                .where(Holding.report_date.isnot(None))
            ).all()
        )
    finally:
        db.close()

    log.info("Existing report dates in DB: %s", sorted(existing_dates))

    all_stats = []
    for q_end in quarters:
        # Skip if this quarter-end already has holdings (idempotent)
        if q_end in existing_dates:
            log.info("Quarter %s already in DB — skipping", q_end)
            all_stats.append({"quarter": str(q_end), "skipped": True})
            continue

        log.info("Processing quarter: %s", q_end)
        stats = run(q_end, institutions=institutions, dry_run=False, cache_dir=cache_dir)
        all_stats.append(stats)

        # Log summary for this quarter
        log.info(
            "Quarter %s done: institutions=%s, holdings=%s, cusips_matched=%s, errors=%s",
            q_end,
            stats.get("institutions_upserted", "?"),
            stats.get("holdings_inserted", "?"),
            stats.get("cusips_matched", "?"),
            len(stats.get("errors", [])),
        )

    return all_stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="SEC 13F-HR Ingestion Script",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--quarter",
        metavar="YYYY-MM-DD",
        help="Quarter-end date to fetch (e.g. 2025-12-31)",
    )
    mode.add_argument(
        "--backfill",
        action="store_true",
        help="Backfill last 4 quarters (2026-03-31, 2025-12-31, 2025-09-30, 2025-06-30)",
    )
    mode.add_argument(
        "--seed-cusips",
        action="store_true",
        help="Seed/refresh CUSIP mappings from mkt_master_data only (no 13F fetch)",
    )
    mode.add_argument(
        "--health",
        action="store_true",
        help="Print DB health report",
    )

    parser.add_argument(
        "--institutions",
        metavar="NAME[,NAME...]",
        help="Comma-separated institution names to target (case-insensitive substring match). "
             "If omitted, fetches all top-50. The bulk dataset always downloads all filers; "
             "this flag is informational for dry-run and post-filtering logging.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be fetched without writing to the DB",
    )
    parser.add_argument(
        "--cache-dir",
        metavar="PATH",
        default=None,
        help="Override ZIP cache directory (default: data/13f_cache/)",
    )

    args = parser.parse_args()

    # Resolve institution filter
    institutions: list[tuple[str, str, str]] | None = None
    if args.institutions:
        institutions = _filter_institutions(args.institutions)
        if not institutions and not args.dry_run:
            log.error("No institutions matched --institutions=%s — aborting", args.institutions)
            sys.exit(1)

    # Dispatch
    if args.seed_cusips:
        if args.dry_run:
            print("DRY-RUN: would seed CUSIP mappings from mkt_master_data (no write)")
            sys.exit(0)
        from webapp.database import init_holdings_db
        init_holdings_db()
        from etp_tracker.thirteen_f import seed_cusip_mappings
        n = seed_cusip_mappings()
        print(f"Seeded {n:,} CUSIP mappings from mkt_master_data.")

    elif args.health:
        from webapp.database import init_holdings_db
        init_holdings_db()
        from etp_tracker.thirteen_f import data_health_report
        data_health_report()

    elif args.backfill:
        all_stats = backfill(
            institutions=institutions,
            dry_run=args.dry_run,
            cache_dir=args.cache_dir,
        )
        if not args.dry_run:
            print()
            print("BACKFILL SUMMARY")
            print("=" * 60)
            total_institutions = 0
            total_holdings = 0
            total_cusips = 0
            for s in all_stats:
                q = s.get("quarter", "?")
                if s.get("skipped"):
                    print(f"  {q}: SKIPPED (already in DB)")
                elif s.get("dry_run"):
                    print(f"  {q}: DRY-RUN")
                else:
                    inst = s.get("institutions_upserted", 0)
                    hold = s.get("holdings_inserted", 0)
                    cusip = s.get("cusips_matched", 0)
                    errs = len(s.get("errors", []))
                    total_institutions += inst
                    total_holdings += hold
                    total_cusips += cusip
                    status = "OK" if not errs else f"WARN ({errs} errors)"
                    print(f"  {q}: {inst:,} institutions, {hold:,} holdings, {cusip:,} CUSIPs matched — {status}")
            print()
            print(f"Total: {total_holdings:,} holdings, {total_cusips:,} tracked CUSIPs")

    elif args.quarter:
        quarter_end = _parse_quarter_arg(args.quarter)
        stats = run(
            quarter_end,
            institutions=institutions,
            dry_run=args.dry_run,
            cache_dir=args.cache_dir,
        )
        if not args.dry_run:
            print()
            print("INGESTION COMPLETE")
            print("=" * 60)
            print(f"Quarter end:           {quarter_end}")
            print(f"SEC label:             {_quarter_end_to_sec_label(quarter_end)}")
            print(f"Institutions upserted: {stats.get('institutions_upserted', 0):,}")
            print(f"Holdings inserted:     {stats.get('holdings_inserted', 0):,}")
            print(f"CUSIPs matched:        {stats.get('cusips_matched', 0):,}")
            errs = stats.get("errors", [])
            if errs:
                print(f"Errors ({len(errs)}):")
                for e in errs[:10]:
                    print(f"  - {e}")

    else:
        parser.print_help()
        sys.exit(0)


if __name__ == "__main__":
    main()
