"""
scripts/fetch_13f.py -- MVP 13F ingestion for top 10 institutions.

Fetches the most recent 13F-HR filing per institution directly from the SEC
EDGAR submissions JSON, parses the infotable XML, and populates the
Institution + Holding tables in data/13f_holdings.db.

Usage:
    # Dry-run (prints what it would do, no DB writes):
    python scripts/fetch_13f.py --dry-run

    # Live run (most recent quarter, all top-10):
    python scripts/fetch_13f.py

    # Single institution:
    python scripts/fetch_13f.py --institution blackrock

Known limitations (MVP):
    - Top 10 institutions only. Extend TOP_10 list to add more.
    - One quarter only (most recent 13F-HR per institution).
    - No CUSIP enrichment -- is_tracked stays False.
    - Backfill deferred.

Set ENABLE_13F=1 in Render environment variables to expose /holdings/* routes.
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("fetch_13f")

USER_AGENT = "REX-ETP-Tracker/2.0 relasmar@rexfin.com"

# ---------------------------------------------------------------------------
# Top 10 institutions -- CIKs verified against SEC submissions JSON
# Format: (cik_string, canonical_name, short_key)
# ---------------------------------------------------------------------------
TOP_10: list[tuple[str, str, str]] = [
    ("0001364742", "BlackRock Inc.",              "blackrock"),
    ("0000102909", "Vanguard Group Inc",           "vanguard"),
    ("0000093751", "State Street Corp",            "state street"),
    ("0000315066", "Fidelity Management & Research", "fidelity"),
    ("0000019617", "JPMorgan Chase & Co",          "jpmorgan"),
    ("0000886982", "Goldman Sachs Group Inc",      "goldman sachs"),
    ("0000895421", "Morgan Stanley",               "morgan stanley"),
    ("0000072971", "Wells Fargo & Company",        "wells fargo"),
    ("0000070858", "Bank of America Corp",         "bank of america"),
    ("0001390777", "BNY Mellon",                   "bny mellon"),
]

SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SEC_ARCHIVE_BASE = "https://www.sec.gov/Archives/edgar/data"

# ---------------------------------------------------------------------------
# HTTP helpers (reuse etp_tracker.sec_client where possible)
# ---------------------------------------------------------------------------

def _get_client():
    """Return a configured SECClient instance."""
    from etp_tracker.sec_client import SECClient
    return SECClient(user_agent=USER_AGENT, pause=0.15)


def _fetch_submissions(client, cik: str) -> dict:
    """Fetch the submissions JSON for a CIK (padded to 10 digits)."""
    padded = cik.lstrip("0").zfill(10)
    url = SEC_SUBMISSIONS_URL.format(cik=padded)
    text = client.fetch_text(url, use_cache=False)
    import json
    return json.loads(text)


def _find_latest_13f(submissions: dict) -> dict | None:
    """Return metadata for the most recent 13F-HR filing.

    Submissions JSON filings array is ordered newest-first.
    Returns a dict with: accession, date_filed, report_date, primary_doc
    or None if no 13F-HR found.
    """
    recent = submissions.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accessions = recent.get("accessionNumber", [])
    dates_filed = recent.get("filingDate", [])
    primary_docs = recent.get("primaryDocument", [])
    report_dates = recent.get("reportDate", [])

    for i, form in enumerate(forms):
        if form in ("13F-HR", "13F-HR/A"):
            return {
                "accession": accessions[i],
                "date_filed": dates_filed[i] if i < len(dates_filed) else None,
                "report_date": report_dates[i] if i < len(report_dates) else None,
                "primary_doc": primary_docs[i] if i < len(primary_docs) else None,
                "form": form,
            }
    return None


def _fetch_infotable_xml(client, cik_raw: str, accession: str) -> str:
    """Fetch the infotable XML for a given filing.

    First tries the primary document path, then falls back to the
    standard infotable.xml naming convention.
    """
    # CIK without leading zeros for archive path
    cik_int = str(int(cik_raw))
    acc_nodash = accession.replace("-", "")

    # Build candidate URLs
    candidates = [
        f"{SEC_ARCHIVE_BASE}/{cik_int}/{acc_nodash}/infotable.xml",
        f"{SEC_ARCHIVE_BASE}/{cik_int}/{acc_nodash}/form13fInfoTable.xml",
        f"{SEC_ARCHIVE_BASE}/{cik_int}/{acc_nodash}/INFORMATION_TABLE.xml",
    ]

    for url in candidates:
        try:
            text = client.fetch_text(url, use_cache=False)
            if text and "<infoTable>" in text.lower().replace("infotable", "infotable"):
                return text
            # Some files use ns-qualified tags
            if text and len(text) > 200:
                return text
        except Exception:
            continue

    # Last resort: fetch the filing index to find the infotable document
    index_url = f"{SEC_ARCHIVE_BASE}/{cik_int}/{acc_nodash}/{accession}-index.htm"
    try:
        index_text = client.fetch_text(index_url, use_cache=False)
        # Look for infotable filename in index
        import re
        m = re.search(r'href="([^"]*(?:infotable|INFOTABLE|form13f|FORM13F)[^"]*\.xml)"', index_text, re.I)
        if m:
            doc_path = m.group(1).lstrip("/")
            doc_url = f"https://www.sec.gov/{doc_path}"
            text = client.fetch_text(doc_url, use_cache=False)
            if text:
                return text
    except Exception:
        pass

    return ""


def _parse_infotable(xml_text: str) -> list[dict]:
    """Parse an infotable XML and return list of holding dicts.

    Handles both namespaced and non-namespaced variants from SEC EDGAR.
    Returns list of dicts with: issuer_name, cusip, value_usd, shares,
    share_type, investment_discretion.
    """
    if not xml_text:
        return []

    holdings = []
    try:
        root = ET.fromstring(xml_text.encode("utf-8"))
        # Strip namespace prefix for simpler matching
        ns_map = {}
        for event, elem in ET.iterparse(__import__("io").StringIO(xml_text), events=["start-ns"]):
            prefix, uri = elem
            ns_map[prefix or ""] = uri

        # Build namespace prefix for search
        default_ns = ns_map.get("", "")
        ns_prefix = f"{{{default_ns}}}" if default_ns else ""

        def _find_text(parent, tag: str) -> str:
            # Try with namespace, then without
            el = parent.find(f"{ns_prefix}{tag}")
            if el is None:
                el = parent.find(tag)
            return (el.text or "").strip() if el is not None else ""

        # Look for infoTable elements (case varies)
        info_tables = (
            root.findall(f".//{ns_prefix}infoTable")
            or root.findall(".//infoTable")
        )

        for row in info_tables:
            issuer = _find_text(row, "nameOfIssuer")
            cusip = _find_text(row, "cusip")
            val_str = _find_text(row, "value")
            shr_str = _find_text(row, "sshPrnamt")
            shr_type = _find_text(row, "sshPrnamtType")
            disc = _find_text(row, "investmentDiscretion")

            try:
                value_usd = float(val_str) * 1000 if val_str else None  # SEC reports in thousands
            except ValueError:
                value_usd = None

            try:
                shares = float(shr_str) if shr_str else None
            except ValueError:
                shares = None

            holdings.append({
                "issuer_name": issuer or None,
                "cusip": cusip or None,
                "value_usd": value_usd,
                "shares": shares,
                "share_type": shr_type or None,
                "investment_discretion": disc or None,
            })

    except Exception as exc:
        log.warning("XML parse error: %s", exc)

    return holdings


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------

def _dry_run(institutions: list[tuple[str, str, str]]) -> None:
    """Print what the script WOULD do without any DB writes."""
    client = _get_client()

    print()
    print("DRY-RUN MODE -- no data will be written")
    print("=" * 65)
    print(f"Institutions targeted: {len(institutions)}")
    print()

    for cik, name, _ in institutions:
        padded = cik.lstrip("0").zfill(10)
        print(f"  CIK {padded}  {name}")
        try:
            subs = _fetch_submissions(client, cik)
            filing = _find_latest_13f(subs)
            if filing:
                print(f"    Latest 13F-HR: {filing['form']} filed {filing['date_filed']}"
                      f"  report_date={filing['report_date']}")
                print(f"    Accession:     {filing['accession']}")
                cik_int = str(int(cik))
                acc_nodash = filing['accession'].replace("-", "")
                print(f"    Archive URL:   {SEC_ARCHIVE_BASE}/{cik_int}/{acc_nodash}/")
            else:
                print("    No 13F-HR found in recent filings")
        except Exception as exc:
            print(f"    ERROR: {exc}")
        print()

    print("To run for real: remove --dry-run")
    print()


# ---------------------------------------------------------------------------
# Live ingestion
# ---------------------------------------------------------------------------

def _ingest(institutions: list[tuple[str, str, str]]) -> dict:
    """Fetch and insert Institution + Holding rows for each institution."""
    from webapp.database import init_holdings_db, HoldingsSessionLocal
    from webapp.models import Institution, Holding
    from sqlalchemy import select

    init_holdings_db()
    client = _get_client()

    stats = {
        "institutions_upserted": 0,
        "holdings_inserted": 0,
        "errors": [],
    }

    db = HoldingsSessionLocal()
    try:
        for cik, name, _ in institutions:
            padded = cik.lstrip("0").zfill(10)
            log.info("Processing: %s (CIK %s)", name, padded)

            try:
                subs = _fetch_submissions(client, cik)
                filing = _find_latest_13f(subs)
                if not filing:
                    log.warning("No 13F-HR found for %s", name)
                    stats["errors"].append(f"{name}: no 13F-HR found")
                    continue

                report_date_str = filing.get("report_date") or ""
                try:
                    report_date = datetime.strptime(report_date_str, "%Y-%m-%d").date()
                except ValueError:
                    report_date = date.today()

                date_filed_str = filing.get("date_filed") or ""
                try:
                    last_filed = datetime.strptime(date_filed_str, "%Y-%m-%d").date()
                except ValueError:
                    last_filed = None

                # Upsert institution
                existing = db.execute(
                    select(Institution).where(Institution.cik == padded)
                ).scalar_one_or_none()

                if existing:
                    inst = existing
                    inst.name = subs.get("name", name)
                    inst.last_filed = last_filed
                    inst.updated_at = datetime.utcnow()
                else:
                    inst = Institution(
                        cik=padded,
                        name=subs.get("name", name),
                        city=subs.get("addresses", {}).get("business", {}).get("city"),
                        state_or_country=subs.get("addresses", {}).get("business", {}).get("stateOrCountry"),
                        last_filed=last_filed,
                    )
                    db.add(inst)
                    db.flush()  # get inst.id

                stats["institutions_upserted"] += 1

                # Fetch and parse infotable
                xml_text = _fetch_infotable_xml(client, cik, filing["accession"])
                rows = _parse_infotable(xml_text)
                log.info("  Parsed %d holdings from infotable", len(rows))

                accession_clean = filing["accession"].replace("-", "")[:30]

                for row in rows:
                    h = Holding(
                        institution_id=inst.id,
                        report_date=report_date,
                        filing_accession=accession_clean,
                        issuer_name=row["issuer_name"],
                        cusip=row["cusip"],
                        value_usd=row["value_usd"],
                        shares=row["shares"],
                        share_type=row["share_type"],
                        investment_discretion=row["investment_discretion"],
                        is_tracked=False,
                    )
                    db.add(h)

                stats["holdings_inserted"] += len(rows)

                # Update institution AUM total
                if rows:
                    total_val = sum(r["value_usd"] for r in rows if r["value_usd"] is not None)
                    inst.aum_total = total_val
                    inst.filing_count = (inst.filing_count or 0) + 1

                db.commit()
                log.info("  Committed: %d holdings for %s", len(rows), name)

            except Exception as exc:
                db.rollback()
                log.error("Failed for %s: %s", name, exc, exc_info=True)
                stats["errors"].append(f"{name}: {exc}")

    finally:
        db.close()

    return stats


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="13F MVP -- fetch most recent 13F-HR for top 10 institutions",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be fetched without writing to the DB",
    )
    parser.add_argument(
        "--institution",
        metavar="NAME",
        help="Filter to a single institution (case-insensitive substring match)",
    )
    args = parser.parse_args()

    # Resolve institution filter
    institutions = TOP_10
    if args.institution:
        needle = args.institution.lower()
        institutions = [
            e for e in TOP_10
            if needle in e[2] or needle in e[1].lower()
        ]
        if not institutions:
            log.error("No institution matched: %r. Available: %s",
                      args.institution, ", ".join(e[2] for e in TOP_10))
            sys.exit(1)

    if args.dry_run:
        _dry_run(institutions)
        return

    stats = _ingest(institutions)

    print()
    print("INGESTION COMPLETE")
    print("=" * 65)
    print(f"Institutions upserted: {stats['institutions_upserted']}")
    print(f"Holdings inserted:     {stats['holdings_inserted']:,}")
    if stats["errors"]:
        print(f"Errors ({len(stats['errors'])}):")
        for e in stats["errors"][:10]:
            print(f"  - {e}")
    print()


if __name__ == "__main__":
    main()
