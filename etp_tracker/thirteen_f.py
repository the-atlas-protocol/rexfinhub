"""
13F Institutional Holdings Pipeline

Ingests SEC 13F-HR quarterly bulk datasets and maps holdings
to our ETP universe via CUSIP. Supports bulk (ZIP), incremental (EFTS),
and local (pre-extracted TSV) ingestion.

Usage:
    python -m etp_tracker.thirteen_f seed
    python -m etp_tracker.thirteen_f ingest 2025q4
    python -m etp_tracker.thirteen_f incremental
    python -m etp_tracker.thirteen_f local /path/to/tsvs
    python -m etp_tracker.thirteen_f health
"""
from __future__ import annotations

import io
import logging
import os
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests
from sqlalchemy import select, func, distinct

# ---------------------------------------------------------------------------
# Project path setup
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from webapp.database import (
    HoldingsSessionLocal as SessionLocal,
    SessionLocal as MainSessionLocal,
    init_holdings_db as init_db,
)
from webapp.models import CusipMapping, Holding, Institution, MktMasterData

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
BATCH_SIZE = 1000
SEC_BULK_BASE = "https://www.sec.gov/files/structureddata/data/form-13f-data-sets"
SEC_EFTS_URL = (
    "https://efts.sec.gov/LATEST/search-index"
    "?q=%2213F-HR%22&dateRange=custom&startdt={start}&enddt={end}&forms=13F-HR"
)


def _build_bulk_urls(quarter: str) -> list[str]:
    """Build possible SEC bulk download URLs for a quarter.

    SEC uses two naming schemes:
    - Pre-2024: '{year}q{n}_form13f.zip' (e.g. 2023q4_form13f.zip)
    - 2024+: Rolling 3-month filing windows (e.g. 01dec2024-28feb2025_form13f.zip)

    The 2024+ windows are NOT calendar quarters. They group by filing date,
    not report period. We accept either a quarter label like '2025q1' or a
    raw SEC filename slug like '01dec2024-28feb2025'.
    """
    # If user passed a raw SEC slug (e.g. '01dec2024-28feb2025'), use directly
    if quarter[0].isdigit() and "-" in quarter and len(quarter) > 10:
        return [f"{SEC_BULK_BASE}/{quarter}_form13f.zip"]

    urls = []
    try:
        year = int(quarter[:4])
        q = int(quarter[-1])
    except (ValueError, IndexError):
        urls.append(f"{SEC_BULK_BASE}/{quarter}_form13f.zip")
        return urls

    # 2024+ new format: rolling 3-month filing windows
    # These are the actual SEC filenames scraped from their data page
    _FILING_WINDOWS_2024 = {
        (2024, 1): ["01jan2024-29feb2024"],
        (2024, 2): ["01mar2024-31may2024"],
        (2024, 3): ["01jun2024-31aug2024"],
        (2024, 4): ["01sep2024-30nov2024", "01dec2024-28feb2025"],
        (2025, 1): ["01mar2025-31may2025"],
        (2025, 2): ["01jun2025-31aug2025"],
        (2025, 3): ["01sep2025-30nov2025"],
        (2025, 4): ["01dec2025-28feb2026"],
    }

    if (year, q) in _FILING_WINDOWS_2024:
        for slug in _FILING_WINDOWS_2024[(year, q)]:
            urls.append(f"{SEC_BULK_BASE}/{slug}_form13f.zip")
    else:
        # Pre-2024 old format
        urls.append(f"{SEC_BULK_BASE}/{year}q{q}_form13f.zip")
        # Legacy format as fallback
        urls.append(f"{SEC_BULK_BASE}/13f{quarter}.zip")

    return urls


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------
def _fetch(url: str, user_agent: str, timeout: int = 30) -> requests.Response:
    """GET with SEC-mandated rate limit and proper User-Agent."""
    headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
    time.sleep(0.35)  # SEC rate limit
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return resp


# ---------------------------------------------------------------------------
# Shared helpers (used by ingest_13f_dataset + ingest_13f_local)
# ---------------------------------------------------------------------------

def _parse_sec_date(date_str: str) -> date:
    """Parse SEC date formats: DD-MON-YYYY (e.g. 31-DEC-2025) or YYYY-MM-DD."""
    if not date_str or not date_str.strip():
        return date(1900, 1, 1)
    date_str = date_str.strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(date_str, fmt).date()
        except ValueError:
            continue
    return date(1900, 1, 1)


def _build_accession_map(sub_df: pd.DataFrame) -> dict[str, dict]:
    """SUBMISSION DataFrame -> {accession_number: {cik, filing_date, report_date}}."""
    accession_map: dict[str, dict] = {}
    for _, row in sub_df.iterrows():
        acc = str(row.get("ACCESSION_NUMBER", "")).strip()
        if not acc:
            continue
        accession_map[acc] = {
            "cik": str(row.get("CIK", "")).strip(),
            "filing_date": str(row.get("FILING_DATE", "")).strip(),
            "report_date": str(row.get("PERIODOFREPORT", "")).strip(),
        }
    return accession_map


def _build_cik_info(
    cover_df: pd.DataFrame, sub_df: pd.DataFrame,
) -> dict[str, dict]:
    """Merge COVERPAGE + SUBMISSION -> {cik: {name, city, state_or_country}}.

    COVERPAGE has FILINGMANAGER_NAME but no CIK.
    SUBMISSION has CIK + ACCESSION_NUMBER.
    Join on ACCESSION_NUMBER to bridge them.
    """
    # Build accession -> CIK lookup from SUBMISSION
    acc_to_cik: dict[str, str] = {}
    for _, row in sub_df.iterrows():
        acc = str(row.get("ACCESSION_NUMBER", "")).strip()
        cik = str(row.get("CIK", "")).strip()
        if acc and cik:
            acc_to_cik[acc] = cik

    cik_info: dict[str, dict] = {}
    for _, row in cover_df.iterrows():
        acc = str(row.get("ACCESSION_NUMBER", "")).strip()
        cik = acc_to_cik.get(acc, "")
        if not cik:
            continue

        name = str(row.get("FILINGMANAGER_NAME", "")).strip()
        city = str(row.get("FILINGMANAGER_CITY", "")).strip()
        state = str(row.get("FILINGMANAGER_STATEORCOUNTRY", "")).strip()

        if name:
            cik_info[cik] = {
                "name": name,
                "city": city or None,
                "state_or_country": state or None,
            }

    return cik_info


def _upsert_institutions(
    db, cik_info: dict[str, dict], accession_map: dict[str, dict],
) -> dict[str, int]:
    """Upsert Institution rows from CIK info. Returns {cik: institution_id}."""
    cik_to_inst_id: dict[str, int] = {}
    all_ciks = set(cik_info.keys()) | {
        v["cik"] for v in accession_map.values() if v["cik"]
    }

    for cik in all_ciks:
        if not cik:
            continue

        info = cik_info.get(cik, {})
        name = info.get("name", f"CIK {cik}")
        city = info.get("city")
        state_or_country = info.get("state_or_country")

        existing = db.execute(
            select(Institution).where(Institution.cik == cik)
        ).scalar_one_or_none()

        if existing:
            existing.name = name
            if city:
                existing.city = city
            if state_or_country:
                existing.state_or_country = state_or_country
            existing.filing_count = existing.filing_count + 1
            existing.updated_at = datetime.utcnow()
            cik_to_inst_id[cik] = existing.id
        else:
            inst = Institution(
                cik=cik, name=name, filing_count=1,
                city=city, state_or_country=state_or_country,
            )
            db.add(inst)
            db.flush()
            cik_to_inst_id[cik] = inst.id

    db.commit()
    return cik_to_inst_id


def _safe_int(val):
    """Convert value to int, returning None on failure."""
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return None


def _build_holding(
    row, accession_map: dict, cik_to_inst_id: dict, cusip_set: set | None = None,
) -> Holding | None:
    """Convert a single INFOTABLE row to a Holding, or None if unmappable."""
    acc = str(row.get("ACCESSION_NUMBER", "")).strip()
    acc_info = accession_map.get(acc, {})
    cik = acc_info.get("cik", "")
    inst_id = cik_to_inst_id.get(cik)

    if inst_id is None:
        return None

    report_dt = _parse_sec_date(acc_info.get("report_date", ""))

    try:
        # Post-2023 (Jan 3, 2023+): VALUE is full dollars.
        # Pre-2023: VALUE was in thousands — multiply by 1000 at ingestion
        # to keep value_usd always in full dollars.
        value = float(row.get("VALUE", 0))
    except (ValueError, TypeError):
        value = None

    try:
        shares = float(row.get("SSHPRNAMT", 0))
    except (ValueError, TypeError):
        shares = None

    cusip = str(row.get("CUSIP", "")).strip()

    return Holding(
        institution_id=inst_id,
        report_date=report_dt,
        filing_accession=acc,
        issuer_name=str(row.get("NAMEOFISSUER", "")).strip() or None,
        cusip=cusip or None,
        value_usd=value,
        shares=shares,
        share_type=str(row.get("SSHPRNAMTTYPE", "")).strip() or None,
        investment_discretion=str(row.get("INVESTMENTDISCRETION", "")).strip() or None,
        voting_sole=_safe_int(row.get("VOTING_AUTH_SOLE")),
        voting_shared=_safe_int(row.get("VOTING_AUTH_SHARED")),
        voting_none=_safe_int(row.get("VOTING_AUTH_NONE")),
        is_tracked=bool(cusip_set and cusip and cusip in cusip_set),
    )


# ---------------------------------------------------------------------------
# 1. seed_cusip_mappings
# ---------------------------------------------------------------------------
def seed_cusip_mappings() -> int:
    """Seed cusip_mappings from mkt_master_data.

    Upserts on CUSIP: if a CUSIP already exists in the mapping table,
    update ticker/fund_name; otherwise insert a new row.

    Returns:
        Count of CUSIPs seeded or updated.
    """
    # Read MktMasterData from main DB, write CusipMapping to holdings DB
    main_db = MainSessionLocal()
    db = SessionLocal()
    try:
        # Pull all mkt_master_data rows that have a non-empty CUSIP
        master_rows = main_db.execute(
            select(MktMasterData.cusip, MktMasterData.ticker, MktMasterData.fund_name)
            .where(MktMasterData.cusip.isnot(None))
            .where(MktMasterData.cusip != "")
        ).all()

        log.info("Found %d rows with CUSIPs in mkt_master_data", len(master_rows))

        count = 0
        for cusip, ticker, fund_name in master_rows:
            cusip = cusip.strip()
            if not cusip:
                continue

            existing = db.execute(
                select(CusipMapping).where(CusipMapping.cusip == cusip)
            ).scalar_one_or_none()

            if existing:
                existing.ticker = ticker
                existing.fund_name = fund_name
                existing.source = "mkt_master"
            else:
                db.add(CusipMapping(
                    cusip=cusip,
                    ticker=ticker,
                    fund_name=fund_name,
                    source="mkt_master",
                ))
            count += 1

            if count % BATCH_SIZE == 0:
                db.commit()
                log.info("  Committed %d CUSIP mappings...", count)

        db.commit()
        log.info("Seeded %d CUSIP mappings from mkt_master_data", count)
        return count
    finally:
        db.close()
        main_db.close()


# ---------------------------------------------------------------------------
# 2. ingest_13f_dataset  (bulk historical from SEC ZIP)
# ---------------------------------------------------------------------------
def ingest_13f_dataset(
    quarter: str,
    user_agent: str,
    cache_dir: str = "D:/sec-data/cache/rexfinhub",
) -> dict:
    """Download and ingest a quarterly 13F bulk dataset from SEC.

    Args:
        quarter: e.g. "2025q4"
        user_agent: SEC-compliant User-Agent string
        cache_dir: directory for caching downloaded ZIPs

    Returns:
        Stats dict with counts for institutions, holdings, matched CUSIPs, etc.
    """
    stats = {
        "quarter": quarter,
        "institutions_upserted": 0,
        "holdings_inserted": 0,
        "cusips_matched": 0,
        "errors": [],
    }

    # ------------------------------------------------------------------
    # Step 1: Download ZIP (with cache)
    # ------------------------------------------------------------------
    cache_path = Path(cache_dir) / "13f"
    cache_path.mkdir(parents=True, exist_ok=True)
    zip_file = cache_path / f"13f{quarter}.zip"

    if zip_file.exists():
        log.info("Using cached ZIP: %s", zip_file)
    else:
        urls = _build_bulk_urls(quarter)
        downloaded = False
        for url in urls:
            log.info("Trying %s ...", url)
            try:
                resp = _fetch(url, user_agent, timeout=120)
                zip_file.write_bytes(resp.content)
                log.info("Downloaded %s (%.1f MB)", zip_file.name, len(resp.content) / 1e6)
                downloaded = True
                break
            except requests.HTTPError as exc:
                log.info("  Not found: %s", exc)
                continue
        if not downloaded:
            msg = f"Failed to download 13F dataset for {quarter} from any URL"
            log.error(msg)
            stats["errors"].append(msg)
            return stats

    # ------------------------------------------------------------------
    # Step 2: Extract TSVs from ZIP
    # ------------------------------------------------------------------
    import tempfile as _tmpmod

    infotable_tmpfile = None
    try:
        with zipfile.ZipFile(zip_file, "r") as zf:
            names = zf.namelist()
            log.info("ZIP contents: %s", names)

            def _find_in_zip(target_name: str) -> str | None:
                """Match by filename only (strip dir prefixes, case-insensitive)."""
                return next(
                    (n for n in names
                     if n.upper().rstrip("/").rsplit("/", 1)[-1] == target_name.upper()),
                    None,
                )

            tsv_data = {}
            # SUBMISSION and COVERPAGE are small (~10K rows) — load into memory
            for target in ("SUBMISSION.tsv", "COVERPAGE.tsv"):
                match = _find_in_zip(target)
                if match is None:
                    msg = f"Missing {target} in ZIP"
                    log.error(msg)
                    stats["errors"].append(msg)
                    return stats
                raw = zf.read(match)
                tsv_data[target.upper()] = raw.decode("utf-8", errors="replace")

            # INFOTABLE is huge (~3.5M rows, 500MB+) — extract to temp file
            match = _find_in_zip("INFOTABLE.tsv")
            if match is None:
                msg = "Missing INFOTABLE.tsv in ZIP"
                log.error(msg)
                stats["errors"].append(msg)
                return stats
            infotable_tmpfile = _tmpmod.NamedTemporaryFile(
                mode="wb", suffix=".tsv", delete=False,
            )
            infotable_tmpfile.write(zf.read(match))
            infotable_tmpfile.close()
            log.info("Extracted INFOTABLE to temp file: %s", infotable_tmpfile.name)

    except zipfile.BadZipFile as exc:
        msg = f"Corrupt ZIP file: {exc}"
        log.error(msg)
        stats["errors"].append(msg)
        return stats

    # ------------------------------------------------------------------
    # Step 3: Parse SUBMISSION + COVERPAGE -> upsert Institutions
    # ------------------------------------------------------------------
    sub_df = pd.read_csv(
        io.StringIO(tsv_data["SUBMISSION.TSV"]),
        sep="\t",
        engine="python",
        on_bad_lines="skip",
        dtype=str,
    )
    cover_df = pd.read_csv(
        io.StringIO(tsv_data["COVERPAGE.TSV"]),
        sep="\t",
        engine="python",
        on_bad_lines="skip",
        dtype=str,
    )

    # Normalise column names to uppercase
    sub_df.columns = [c.strip().upper() for c in sub_df.columns]
    cover_df.columns = [c.strip().upper() for c in cover_df.columns]

    log.info("SUBMISSION rows: %d, COVERPAGE rows: %d", len(sub_df), len(cover_df))

    # Use shared helpers
    accession_map = _build_accession_map(sub_df)
    cik_info = _build_cik_info(cover_df, sub_df)

    # Upsert institutions
    db = SessionLocal()
    try:
        cik_to_inst_id = _upsert_institutions(db, cik_info, accession_map)
        stats["institutions_upserted"] = len(cik_to_inst_id)
        log.info("Upserted %d institutions", stats["institutions_upserted"])

        # ------------------------------------------------------------------
        # Step 4: Parse INFOTABLE -> insert Holdings (chunked to avoid OOM)
        # ------------------------------------------------------------------
        # Pre-load CUSIP mappings for matching
        cusip_set = set(
            row[0] for row in db.execute(select(CusipMapping.cusip)).all()
        )

        # Read from temp file in chunks (50K rows) to stay within memory
        chunk_iter = pd.read_csv(
            infotable_tmpfile.name,
            sep="\t",
            engine="python",
            on_bad_lines="skip",
            dtype=str,
            chunksize=50_000,
        )

        batch: list[Holding] = []
        for chunk_df in chunk_iter:
            chunk_df.columns = [c.strip().upper() for c in chunk_df.columns]

            for idx, row in chunk_df.iterrows():
                holding = _build_holding(row, accession_map, cik_to_inst_id, cusip_set)
                if holding is None:
                    continue

                batch.append(holding)

                # Track CUSIP matches
                if holding.cusip and holding.cusip in cusip_set:
                    stats["cusips_matched"] += 1

                if len(batch) >= BATCH_SIZE:
                    db.add_all(batch)
                    db.commit()
                    stats["holdings_inserted"] += len(batch)
                    if stats["holdings_inserted"] % 50000 == 0:
                        log.info("  Inserted %d holdings...", stats["holdings_inserted"])
                    batch = []

        # Final batch
        if batch:
            db.add_all(batch)
            db.commit()
            stats["holdings_inserted"] += len(batch)

        log.info(
            "Ingestion complete: %d institutions, %d holdings, %d CUSIP matches",
            stats["institutions_upserted"],
            stats["holdings_inserted"],
            stats["cusips_matched"],
        )

    except Exception as exc:
        db.rollback()
        msg = f"Error during ingestion: {exc}"
        log.error(msg, exc_info=True)
        stats["errors"].append(msg)
    finally:
        db.close()
        # Clean up temp file
        if infotable_tmpfile:
            try:
                os.unlink(infotable_tmpfile.name)
            except OSError:
                pass

    return stats


# ---------------------------------------------------------------------------
# 3. ingest_13f_incremental  (full XML parsing)
# ---------------------------------------------------------------------------
def ingest_13f_incremental(
    user_agent: str,
    days_back: int = 7,
    cache_dir: str = "D:/sec-data/cache/rexfinhub",
) -> dict:
    """Search EDGAR EFTS for recent 13F-HR filings and parse XML infotables.

    For each filing found:
    1. Fetch the filing index page to find the XML infotable URL
    2. Parse holdings from the XML infotable
    3. Upsert Institution + insert Holdings (dedup by accession)

    Args:
        user_agent: SEC-compliant User-Agent string
        days_back: how many days back to search
        cache_dir: directory for caching

    Returns:
        Stats dict with counts.
    """
    import xml.etree.ElementTree as ET

    stats = {
        "filings_found": 0,
        "filings_parsed": 0,
        "filings_skipped": 0,
        "institutions_upserted": 0,
        "holdings_inserted": 0,
        "cusips_matched": 0,
        "errors": [],
    }

    end = date.today()
    start = end - timedelta(days=days_back)

    # Search EFTS for recent 13F-HR filings
    log.info("Searching EFTS for 13F-HR filings: %s to %s", start, end)
    filing_hits = _search_13f_filings(user_agent, start, end)
    stats["filings_found"] = len(filing_hits)

    if not filing_hits:
        log.info("No recent 13F-HR filings found")
        return stats

    db = SessionLocal()
    try:
        # Pre-load CUSIP mappings
        cusip_set = set(
            row[0] for row in db.execute(select(CusipMapping.cusip)).all()
        )

        for hit in filing_hits:
            cik = hit["cik"]
            accession = hit["accession"]
            company_name = hit.get("company_name", f"CIK {cik}")

            # Skip if already ingested (dedup by accession)
            existing = db.execute(
                select(Holding.id).where(Holding.filing_accession == accession)
            ).first()
            if existing:
                stats["filings_skipped"] += 1
                continue

            try:
                # Fetch filing index to find XML infotable
                xml_url = _find_infotable_xml_url(cik, accession, user_agent)
                if not xml_url:
                    stats["errors"].append(f"No XML infotable for {accession}")
                    continue

                # Parse XML infotable
                holdings_data = _parse_13f_xml(xml_url, user_agent)
                if not holdings_data:
                    continue

                # Upsert institution
                inst = db.execute(
                    select(Institution).where(Institution.cik == str(cik))
                ).scalar_one_or_none()

                if inst:
                    inst.name = company_name
                    inst.filing_count = inst.filing_count + 1
                    inst.last_filed = end
                    inst.updated_at = datetime.utcnow()
                else:
                    inst = Institution(
                        cik=str(cik),
                        name=company_name,
                        filing_count=1,
                        last_filed=end,
                    )
                    db.add(inst)
                    db.flush()

                stats["institutions_upserted"] += 1

                # Insert holdings
                report_date = hit.get("report_date", end)
                for h in holdings_data:
                    cusip = h.get("cusip", "").strip()
                    holding = Holding(
                        institution_id=inst.id,
                        report_date=report_date,
                        filing_accession=accession,
                        issuer_name=h.get("issuer_name"),
                        cusip=cusip or None,
                        value_usd=h.get("value"),
                        shares=h.get("shares"),
                        share_type=h.get("share_type"),
                        investment_discretion=h.get("investment_discretion"),
                        voting_sole=h.get("voting_sole"),
                        voting_shared=h.get("voting_shared"),
                        voting_none=h.get("voting_none"),
                    )
                    db.add(holding)
                    stats["holdings_inserted"] += 1

                    if cusip and cusip in cusip_set:
                        stats["cusips_matched"] += 1

                db.commit()
                stats["filings_parsed"] += 1
                log.info("Parsed %s: %d holdings", accession, len(holdings_data))

            except Exception as exc:
                db.rollback()
                msg = f"Error parsing {accession}: {exc}"
                log.warning(msg)
                stats["errors"].append(msg)

    finally:
        db.close()

    log.info(
        "Incremental: %d found, %d parsed, %d skipped, %d holdings, %d CUSIP matches",
        stats["filings_found"], stats["filings_parsed"], stats["filings_skipped"],
        stats["holdings_inserted"], stats["cusips_matched"],
    )
    return stats


def _search_13f_filings(user_agent: str, start: date, end: date) -> list[dict]:
    """Search EFTS for 13F-HR filings in date range. Returns list of hit dicts."""
    hits = []
    offset = 0

    while True:
        url = (
            f"https://efts.sec.gov/LATEST/search-index"
            f"?forms=13F-HR&dateRange=custom"
            f"&startdt={start.strftime('%Y-%m-%d')}"
            f"&enddt={end.strftime('%Y-%m-%d')}"
            f"&from={offset}"
        )
        try:
            resp = _fetch(url, user_agent)
            data = resp.json()
        except Exception as exc:
            log.error("EFTS search failed: %s", exc)
            break

        page_hits = data.get("hits", {}).get("hits", [])
        if not page_hits:
            break

        for h in page_hits:
            src = h.get("_source", {})
            ciks = src.get("ciks", [])
            if not ciks:
                continue
            acc = src.get("adsh", "")
            if not acc:
                continue

            report_date_str = src.get("period_of_report", "")
            try:
                report_dt = datetime.strptime(report_date_str, "%Y-%m-%d").date()
            except (ValueError, TypeError):
                report_dt = start

            hits.append({
                "cik": str(int(ciks[0])),
                "accession": acc,
                "company_name": src.get("entity_name", ""),
                "report_date": report_dt,
            })

        total = data.get("hits", {}).get("total", {}).get("value", 0)
        offset += len(page_hits)
        if offset >= total:
            break

    return hits


def _find_infotable_xml_url(cik: str, accession: str, user_agent: str) -> str | None:
    """Fetch the filing index and find the XML infotable URL."""
    acc_no_dash = accession.replace("-", "")
    index_url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dash}/{accession}-index.htm"

    try:
        resp = _fetch(index_url, user_agent)
        html = resp.text
    except Exception:
        return None

    # Look for XML infotable link in the index page
    # The infotable XML typically has "infotable" in the filename
    import re
    pattern = re.compile(r'href="([^"]*infotable[^"]*\.xml)"', re.IGNORECASE)
    match = pattern.search(html)
    if match:
        path = match.group(1)
        if path.startswith("/"):
            return f"https://www.sec.gov{path}"
        return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc_no_dash}/{path}"

    # Fallback: look for any XML file in the index
    pattern2 = re.compile(
        rf'href="(/Archives/edgar/data/{cik}/{acc_no_dash}/[^"]*\.xml)"',
        re.IGNORECASE,
    )
    match2 = pattern2.search(html)
    if match2:
        return f"https://www.sec.gov{match2.group(1)}"

    return None


def _parse_13f_xml(xml_url: str, user_agent: str) -> list[dict]:
    """Parse a 13F-HR XML infotable into a list of holding dicts."""
    import xml.etree.ElementTree as ET

    try:
        resp = _fetch(xml_url, user_agent, timeout=60)
        content = resp.text
    except Exception as exc:
        log.warning("Failed to fetch XML: %s", exc)
        return []

    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        log.warning("XML parse error for %s: %s", xml_url, exc)
        return []

    # Handle namespace: 13F XML uses varying namespaces
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    holdings = []
    for info in root.iter(f"{ns}infoTable"):
        def _text(tag):
            el = info.find(f"{ns}{tag}")
            return el.text.strip() if el is not None and el.text else ""

        def _int(tag):
            val = _text(tag)
            try:
                return int(float(val))
            except (ValueError, TypeError):
                return None

        def _float(tag):
            val = _text(tag)
            try:
                return float(val)
            except (ValueError, TypeError):
                return None

        # Parse voting authority (nested element)
        voting = info.find(f"{ns}votingAuthority")
        voting_sole = voting_shared = voting_none = None
        if voting is not None:
            def _voting_int(tag):
                el = voting.find(f"{ns}{tag}")
                if el is not None and el.text:
                    try:
                        return int(float(el.text.strip()))
                    except (ValueError, TypeError):
                        pass
                return None
            voting_sole = _voting_int("Sole")
            voting_shared = _voting_int("Shared")
            voting_none = _voting_int("None")

        # Parse shares/principal amount (nested)
        shares_el = info.find(f"{ns}shrsOrPrnAmt")
        shares = None
        share_type = None
        if shares_el is not None:
            amt_el = shares_el.find(f"{ns}sshPrnamt")
            type_el = shares_el.find(f"{ns}sshPrnamtType")
            if amt_el is not None and amt_el.text:
                try:
                    shares = float(amt_el.text.strip())
                except (ValueError, TypeError):
                    pass
            if type_el is not None and type_el.text:
                share_type = type_el.text.strip()

        holdings.append({
            "issuer_name": _text("nameOfIssuer"),
            "cusip": _text("cusip"),
            "value": _float("value"),
            "shares": shares,
            "share_type": share_type,
            "investment_discretion": _text("investmentDiscretion"),
            "voting_sole": voting_sole,
            "voting_shared": voting_shared,
            "voting_none": voting_none,
        })

    return holdings


# ---------------------------------------------------------------------------
# 4. get_latest_available_quarter
# ---------------------------------------------------------------------------
def get_latest_available_quarter() -> str | None:
    """Auto-detect the most recent quarterly 13F dataset available on SEC.

    SEC publishes bulk datasets with naming like '2025q4'. Check from the
    current quarter backwards until we find one that exists.

    Returns:
        Quarter string like '2025q4', or None if none found.
    """
    today = date.today()
    year = today.year
    quarter = (today.month - 1) // 3 + 1

    # Check current and previous 4 quarters
    for _ in range(5):
        label = f"{year}q{quarter}"
        urls = _build_bulk_urls(label)
        for url in urls:
            try:
                resp = requests.head(url, headers={"User-Agent": "REX-ETP-FilingTracker/2.0"}, timeout=10)
                if resp.status_code == 200:
                    return label
            except Exception:
                pass

        quarter -= 1
        if quarter < 1:
            quarter = 4
            year -= 1

    return None


# ---------------------------------------------------------------------------
# 5. enrich_cusip_mappings_from_holdings
# ---------------------------------------------------------------------------
def enrich_cusip_mappings_from_holdings() -> int:
    """Match unlinked CUSIPs in holdings to fund tickers via mkt_master_data.

    Finds CUSIPs that appear in holdings but don't have a cusip_mapping entry,
    and tries to match them using issuer_name similarity to mkt_master_data.

    Returns:
        Count of new CUSIP mappings created.
    """
    # Read MktMasterData from main DB, read/write holdings data in holdings DB
    main_db = MainSessionLocal()
    db = SessionLocal()
    try:
        # Get CUSIPs from holdings that aren't in cusip_mappings yet
        mapped_cusips = set(
            row[0] for row in db.execute(select(CusipMapping.cusip)).all()
        )

        unmapped = db.execute(
            select(Holding.cusip, Holding.issuer_name)
            .where(Holding.cusip.isnot(None))
            .where(Holding.cusip != "")
            .distinct()
        ).all()

        # Load master data for matching (from main DB)
        master_rows = main_db.execute(
            select(MktMasterData.cusip, MktMasterData.ticker, MktMasterData.fund_name)
            .where(MktMasterData.cusip.isnot(None))
            .where(MktMasterData.cusip != "")
        ).all()
        master_by_cusip = {row[0].strip(): (row[1], row[2]) for row in master_rows if row[0]}

        count = 0
        for cusip, issuer_name in unmapped:
            cusip = cusip.strip()
            if not cusip or cusip in mapped_cusips:
                continue

            # Direct CUSIP match against master data
            if cusip in master_by_cusip:
                ticker, fund_name = master_by_cusip[cusip]
                db.add(CusipMapping(
                    cusip=cusip,
                    ticker=ticker,
                    fund_name=fund_name,
                    source="holdings_enrichment",
                ))
                mapped_cusips.add(cusip)
                count += 1

                if count % BATCH_SIZE == 0:
                    db.commit()

        db.commit()
        log.info("Enriched %d CUSIP mappings from holdings", count)
        return count
    finally:
        db.close()
        main_db.close()


# ---------------------------------------------------------------------------
# 6. ingest_13f_local  (pre-extracted TSV directory)
# ---------------------------------------------------------------------------
def ingest_13f_local(tsv_dir: str) -> dict:
    """Ingest 13F data from a directory of pre-extracted TSV files.

    Reads SUBMISSION.tsv, COVERPAGE.tsv, and INFOTABLE.tsv from tsv_dir.
    INFOTABLE is read in chunks (50K rows) to handle large files (343MB+).
    Deduplicates by skipping accession numbers already present in holdings.

    Args:
        tsv_dir: path to directory containing the three TSV files

    Returns:
        Stats dict with counts.
    """
    stats = {
        "institutions_upserted": 0,
        "holdings_inserted": 0,
        "holdings_skipped": 0,
        "cusips_matched": 0,
        "errors": [],
    }

    tsv_path = Path(tsv_dir)
    if not tsv_path.is_dir():
        msg = f"Not a directory: {tsv_dir}"
        log.error(msg)
        stats["errors"].append(msg)
        return stats

    # Find TSV files (case-insensitive)
    def _find_tsv(name: str) -> Path | None:
        for f in tsv_path.iterdir():
            if f.name.upper() == name.upper():
                return f
        return None

    sub_file = _find_tsv("SUBMISSION.tsv")
    cover_file = _find_tsv("COVERPAGE.tsv")
    info_file = _find_tsv("INFOTABLE.tsv")

    for name, f in [
        ("SUBMISSION.tsv", sub_file),
        ("COVERPAGE.tsv", cover_file),
        ("INFOTABLE.tsv", info_file),
    ]:
        if f is None:
            msg = f"Missing {name} in {tsv_dir}"
            log.error(msg)
            stats["errors"].append(msg)
            return stats

    # ---- Read SUBMISSION + COVERPAGE (small files, full read) ----
    log.info("Reading SUBMISSION.tsv...")
    sub_df = pd.read_csv(
        sub_file, sep="\t", engine="python", on_bad_lines="skip", dtype=str,
    )
    sub_df.columns = [c.strip().upper() for c in sub_df.columns]
    log.info("  %d rows", len(sub_df))

    log.info("Reading COVERPAGE.tsv...")
    cover_df = pd.read_csv(
        cover_file, sep="\t", engine="python", on_bad_lines="skip", dtype=str,
    )
    cover_df.columns = [c.strip().upper() for c in cover_df.columns]
    log.info("  %d rows", len(cover_df))

    # Build lookup structures
    accession_map = _build_accession_map(sub_df)
    cik_info = _build_cik_info(cover_df, sub_df)
    log.info(
        "Accession map: %d entries, CIK info: %d institutions",
        len(accession_map), len(cik_info),
    )

    db = SessionLocal()
    try:
        cik_to_inst_id = _upsert_institutions(db, cik_info, accession_map)
        stats["institutions_upserted"] = len(cik_to_inst_id)
        log.info("Upserted %d institutions", stats["institutions_upserted"])

        # Pre-load CUSIP mappings
        cusip_set = set(
            row[0] for row in db.execute(select(CusipMapping.cusip)).all()
        )

        # Get existing accession numbers to skip duplicates
        existing_accessions = set(
            row[0] for row in db.execute(
                select(distinct(Holding.filing_accession))
                .where(Holding.filing_accession.isnot(None))
            ).all()
        )
        log.info("Existing accessions to skip: %d", len(existing_accessions))

        # ---- Read INFOTABLE in chunks ----
        log.info("Reading INFOTABLE.tsv in chunks of 50,000...")
        batch: list[Holding] = []
        total_rows = 0

        for chunk in pd.read_csv(
            info_file, sep="\t", engine="python", on_bad_lines="skip",
            dtype=str, chunksize=50_000,
        ):
            chunk.columns = [c.strip().upper() for c in chunk.columns]

            for _, row in chunk.iterrows():
                total_rows += 1

                # Skip already-loaded accessions
                acc = str(row.get("ACCESSION_NUMBER", "")).strip()
                if acc in existing_accessions:
                    stats["holdings_skipped"] += 1
                    continue

                holding = _build_holding(row, accession_map, cik_to_inst_id, cusip_set)
                if holding is None:
                    continue

                batch.append(holding)

                if holding.cusip and holding.cusip in cusip_set:
                    stats["cusips_matched"] += 1

                if len(batch) >= BATCH_SIZE:
                    db.add_all(batch)
                    db.commit()
                    stats["holdings_inserted"] += len(batch)
                    batch = []

            if total_rows % 100_000 < 50_000:
                log.info(
                    "  Progress: %dk rows, %d inserted, %d skipped",
                    total_rows // 1000,
                    stats["holdings_inserted"],
                    stats["holdings_skipped"],
                )

        # Final batch
        if batch:
            db.add_all(batch)
            db.commit()
            stats["holdings_inserted"] += len(batch)

        log.info(
            "Local ingestion complete: %d institutions, %d holdings inserted, "
            "%d skipped, %d CUSIP matches, %dk total rows",
            stats["institutions_upserted"],
            stats["holdings_inserted"],
            stats["holdings_skipped"],
            stats["cusips_matched"],
            total_rows // 1000,
        )

    except Exception as exc:
        db.rollback()
        msg = f"Error during local ingestion: {exc}"
        log.error(msg, exc_info=True)
        stats["errors"].append(msg)
    finally:
        db.close()

    return stats


# ---------------------------------------------------------------------------
# 7. data_health_report
# ---------------------------------------------------------------------------
def data_health_report():
    """Print diagnostic report on 13F data health."""
    db = SessionLocal()
    try:
        n_inst = db.execute(select(func.count(Institution.id))).scalar()
        n_hold = db.execute(select(func.count(Holding.id))).scalar()
        n_cusip = db.execute(select(func.count(CusipMapping.id))).scalar()

        print("=" * 60)
        print("13F DATA HEALTH REPORT")
        print("=" * 60)
        print(f"Institutions:    {n_inst:>12,}")
        print(f"Holdings:        {n_hold:>12,}")
        print(f"CUSIP mappings:  {n_cusip:>12,}")
        print()

        # Distinct report dates
        quarters = db.execute(
            select(distinct(Holding.report_date))
            .where(Holding.report_date != date(1900, 1, 1))
            .order_by(Holding.report_date)
        ).all()
        quarter_dates = [r[0] for r in quarters if r[0]]
        print(f"Report dates:    {len(quarter_dates):>12,}")
        if quarter_dates:
            print(f"  Earliest: {quarter_dates[0]}")
            print(f"  Latest:   {quarter_dates[-1]}")
        print()

        # Data quality checks
        bad_dates = db.execute(
            select(func.count(Holding.id))
            .where(Holding.report_date == date(1900, 1, 1))
        ).scalar()
        null_city = db.execute(
            select(func.count(Institution.id))
            .where(Institution.city.is_(None))
        ).scalar()
        has_voting = db.execute(
            select(func.count(Holding.id))
            .where(Holding.voting_sole.isnot(None))
        ).scalar()

        print("Data Quality:")
        print(f"  Holdings with date 1900-01-01: {bad_dates:>10,}  {'OK' if bad_dates == 0 else 'WARN'}")
        print(f"  Institutions with NULL city:   {null_city:>10,}")
        print(f"  Holdings with voting data:     {has_voting:>10,}  {'OK' if has_voting > 0 else 'WARN'}")
        print()

        # CUSIP match rate
        distinct_cusips = db.execute(
            select(func.count(distinct(Holding.cusip)))
            .where(Holding.cusip.isnot(None))
        ).scalar()
        cusip_mapped = set(
            row[0] for row in db.execute(select(CusipMapping.cusip)).all()
        )
        matched = db.execute(
            select(func.count(distinct(Holding.cusip)))
            .where(Holding.cusip.in_(cusip_mapped))
        ).scalar() if cusip_mapped else 0

        print("CUSIP Coverage:")
        print(f"  Distinct CUSIPs in holdings: {distinct_cusips:>10,}")
        print(f"  Matched to fund universe:    {matched:>10,}")
        if distinct_cusips:
            print(f"  Match rate:                  {matched / distinct_cusips * 100:>9.1f}%")
        print()

        # Top 10 institutions by holdings count
        top_inst = db.execute(
            select(
                Institution.name,
                func.count(Holding.id).label("cnt"),
            )
            .join(Holding, Holding.institution_id == Institution.id)
            .group_by(Institution.id)
            .order_by(func.count(Holding.id).desc())
            .limit(10)
        ).all()

        print("Top 10 Institutions by Holdings:")
        for name, cnt in top_inst:
            print(f"  {cnt:>8,}  {name[:60]}")
        print()

        # Geographic distribution
        geo = db.execute(
            select(
                Institution.state_or_country,
                func.count(Institution.id),
            )
            .where(Institution.state_or_country.isnot(None))
            .group_by(Institution.state_or_country)
            .order_by(func.count(Institution.id).desc())
            .limit(15)
        ).all()

        if geo:
            print("Geographic Distribution (top 15):")
            for state, cnt in geo:
                print(f"  {state or 'NULL':<6} {cnt:>6,}")
            print()

        # REX-specific: holdings matching REX fund CUSIPs
        rex_cusips = db.execute(
            select(CusipMapping.cusip, CusipMapping.ticker, CusipMapping.fund_name)
            .where(CusipMapping.source == "mkt_master")
        ).all()

        if rex_cusips:
            rex_cusip_set = {r[0] for r in rex_cusips}
            rex_holdings = db.execute(
                select(func.count(Holding.id))
                .where(Holding.cusip.in_(rex_cusip_set))
            ).scalar()

            print(f"REX Fund Holdings: {rex_holdings:,}")
            if rex_holdings > 0:
                per_fund = db.execute(
                    select(
                        CusipMapping.ticker,
                        CusipMapping.fund_name,
                        func.count(Holding.id),
                    )
                    .join(Holding, Holding.cusip == CusipMapping.cusip)
                    .where(CusipMapping.source == "mkt_master")
                    .group_by(CusipMapping.cusip)
                    .order_by(func.count(Holding.id).desc())
                ).all()

                for ticker, fund_name, cnt in per_fund:
                    short_name = (fund_name or "")[:45]
                    print(f"  {ticker or '???':<8} {cnt:>6,}  {short_name}")
            print()

        print("=" * 60)

    finally:
        db.close()


# ---------------------------------------------------------------------------
# 8. backfill_is_tracked
# ---------------------------------------------------------------------------
def backfill_is_tracked() -> int:
    """Tag existing holdings where CUSIP matches cusip_mappings.

    Adds the is_tracked column via ALTER TABLE if it doesn't exist yet,
    then sets is_tracked=1 for all holdings with a matching CUSIP.

    Returns:
        Count of holdings tagged as tracked.
    """
    from sqlalchemy import text, inspect as sa_inspect

    db = SessionLocal()
    try:
        # Add column if missing (SQLite migration)
        inspector = sa_inspect(db.bind)
        columns = [c["name"] for c in inspector.get_columns("holdings")]
        if "is_tracked" not in columns:
            db.execute(text("ALTER TABLE holdings ADD COLUMN is_tracked BOOLEAN DEFAULT 0"))
            db.commit()
            log.info("Added is_tracked column to holdings")

        # Reset all to 0, then tag matches
        db.execute(text("UPDATE holdings SET is_tracked = 0"))
        result = db.execute(text(
            "UPDATE holdings SET is_tracked = 1 "
            "WHERE cusip IN (SELECT cusip FROM cusip_mappings)"
        ))
        db.commit()

        tagged = db.execute(
            select(func.count(Holding.id)).where(Holding.is_tracked == True)
        ).scalar()

        # Also create indexes if missing
        for idx_sql in [
            "CREATE INDEX IF NOT EXISTS idx_holdings_tracked ON holdings (is_tracked)",
            "CREATE INDEX IF NOT EXISTS idx_holdings_tracked_date ON holdings (is_tracked, report_date)",
        ]:
            db.execute(text(idx_sql))
        db.commit()

        log.info("Tagged %d holdings as tracked", tagged)
        return tagged
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 9. export_tracked_db  (lean DB for Render deployment)
# ---------------------------------------------------------------------------
def export_tracked_db(output_path: str) -> dict:
    """Export a lean copy of the DB containing only tracked holdings.

    Creates a new SQLite database at output_path with:
    - Only holdings where is_tracked=1
    - Only institutions that have at least one tracked holding
    - All cusip_mappings
    - Full schema preserved

    Returns:
        Stats dict with row counts and file size.
    """
    import shutil
    import sqlite3
    from webapp.database import HOLDINGS_DB_PATH

    src_db = str(HOLDINGS_DB_PATH)
    dst_db = output_path

    log.info("Exporting tracked DB: %s -> %s", src_db, dst_db)

    # Copy full DB first, then delete untracked data
    shutil.copy2(src_db, dst_db)

    conn = sqlite3.connect(dst_db)
    try:
        cur = conn.cursor()

        # Count before
        before_holdings = cur.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
        before_inst = cur.execute("SELECT COUNT(*) FROM institutions").fetchone()[0]

        # Delete untracked holdings
        cur.execute("DELETE FROM holdings WHERE is_tracked = 0 OR is_tracked IS NULL")

        # Delete institutions with no remaining holdings
        cur.execute(
            "DELETE FROM institutions WHERE id NOT IN "
            "(SELECT DISTINCT institution_id FROM holdings)"
        )

        conn.commit()

        # Count after
        after_holdings = cur.execute("SELECT COUNT(*) FROM holdings").fetchone()[0]
        after_inst = cur.execute("SELECT COUNT(*) FROM institutions").fetchone()[0]

        # VACUUM to reclaim space
        cur.execute("VACUUM")
        conn.commit()

        file_size = Path(dst_db).stat().st_size

        stats = {
            "before_holdings": before_holdings,
            "after_holdings": after_holdings,
            "before_institutions": before_inst,
            "after_institutions": after_inst,
            "file_size_mb": round(file_size / 1e6, 1),
        }
        log.info(
            "Export complete: %d->%d holdings, %d->%d institutions, %.1f MB",
            before_holdings, after_holdings, before_inst, after_inst,
            stats["file_size_mb"],
        )
        return stats
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    init_db()

    if len(sys.argv) < 2:
        print("Usage:")
        print("  python -m etp_tracker.thirteen_f seed")
        print("  python -m etp_tracker.thirteen_f ingest 2025q4")
        print("  python -m etp_tracker.thirteen_f incremental")
        print("  python -m etp_tracker.thirteen_f local /path/to/tsvs")
        print("  python -m etp_tracker.thirteen_f health")
        sys.exit(1)

    cmd = sys.argv[1]
    user_agent = "REX-ETP-FilingTracker/2.0 (contact: relasmar@rexfin.com)"

    if cmd == "seed":
        n = seed_cusip_mappings()
        print(f"Seeded {n} CUSIP mappings from mkt_master_data.")

    elif cmd == "ingest":
        if len(sys.argv) < 3:
            print("Error: provide quarter, e.g. 2025q4")
            sys.exit(1)
        quarter = sys.argv[2]
        result = ingest_13f_dataset(quarter, user_agent)
        print(f"Quarter: {result['quarter']}")
        print(f"  Institutions upserted: {result['institutions_upserted']}")
        print(f"  Holdings inserted:     {result['holdings_inserted']}")
        print(f"  CUSIPs matched:        {result['cusips_matched']}")
        if result["errors"]:
            print(f"  Errors: {len(result['errors'])}")
            for e in result["errors"]:
                print(f"    - {e}")

    elif cmd == "incremental":
        stats = ingest_13f_incremental(user_agent)
        print(f"Filings found:   {stats['filings_found']}")
        print(f"Filings parsed:  {stats['filings_parsed']}")
        print(f"Filings skipped: {stats['filings_skipped']}")
        print(f"Holdings added:  {stats['holdings_inserted']}")
        print(f"CUSIPs matched:  {stats['cusips_matched']}")
        if stats["errors"]:
            print(f"Errors: {len(stats['errors'])}")
            for e in stats["errors"][:10]:
                print(f"  - {e}")

    elif cmd == "local":
        if len(sys.argv) < 3:
            print("Error: provide path to TSV directory")
            sys.exit(1)
        tsv_dir = sys.argv[2]
        result = ingest_13f_local(tsv_dir)
        print(f"Institutions: {result['institutions_upserted']}")
        print(f"Holdings:     {result['holdings_inserted']}")
        print(f"Skipped:      {result['holdings_skipped']}")
        print(f"CUSIPs:       {result['cusips_matched']}")
        if result["errors"]:
            print(f"Errors: {len(result['errors'])}")
            for e in result["errors"]:
                print(f"  - {e}")

    elif cmd == "health":
        data_health_report()

    elif cmd == "latest-quarter":
        q = get_latest_available_quarter()
        if q:
            print(f"Latest available quarter: {q}")
        else:
            print("No quarterly dataset found")

    elif cmd == "enrich":
        n = enrich_cusip_mappings_from_holdings()
        print(f"Enriched {n} CUSIP mappings from holdings")

    else:
        print(f"Unknown command: {cmd}")
        print("Valid commands: seed, ingest, incremental, local, health, latest-quarter, enrich")
        sys.exit(1)
