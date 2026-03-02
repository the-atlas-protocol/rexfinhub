"""
CSV-to-DB Sync Service

Imports existing pipeline CSV outputs into the SQLite database.
Designed to be run:
  - Once for initial migration (full_migration)
  - After each pipeline run (sync_all)
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from webapp.models import (
    Trust, Filing, FundExtraction, FundStatus, NameHistory,
)

# REX-owned trusts get special ordering/flagging
_REX_CIKS = {"2043954", "1771146"}


def _slugify(name: str) -> str:
    """Convert trust name to URL-safe slug."""
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")


def _parse_date(val) -> date | None:
    """Safely parse a date string from CSV."""
    if pd.isna(val) or not val:
        return None
    try:
        return datetime.strptime(str(val).strip()[:10], "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _str_or_none(val) -> str | None:
    """Convert CSV value to string or None."""
    if pd.isna(val):
        return None
    s = str(val).strip()
    return s if s else None


def _bool_val(val) -> bool:
    """Convert CSV value to bool."""
    if pd.isna(val):
        return False
    s = str(val).strip().upper()
    return s in ("TRUE", "1", "YES", "Y")


def seed_trusts(db: Session) -> int:
    """Import trust registry from etp_tracker/trusts.py into DB.
    Returns count of trusts seeded."""
    from etp_tracker.trusts import TRUST_CIKS

    count = 0
    for cik, name in TRUST_CIKS.items():
        existing = db.execute(select(Trust).where(Trust.cik == cik)).scalar_one_or_none()
        if existing:
            existing.name = name
            existing.slug = _slugify(name)
            existing.is_rex = cik in _REX_CIKS
        else:
            db.add(Trust(
                cik=cik,
                name=name,
                slug=_slugify(name),
                is_rex=cik in _REX_CIKS,
                is_active=True,
                added_by="SEED",
            ))
            count += 1
    db.commit()
    return count


def _get_trust_map(db: Session) -> dict[str, Trust]:
    """Returns CIK -> Trust mapping."""
    trusts = db.execute(select(Trust)).scalars().all()
    return {t.cik: t for t in trusts}


def sync_filings(db: Session, trust: Trust, output_dir: Path) -> int:
    """Sync step 1 CSV (All Trust Filings) into filings table.
    Returns count of new filings inserted."""
    csv_path = output_dir / f"{trust.name}_1_All_Trust_Filings.csv"
    if not csv_path.exists():
        return 0

    df = pd.read_csv(csv_path, dtype=str)
    if df.empty:
        return 0

    # Check ALL existing accessions (globally unique, not per-trust)
    existing_accessions = set(
        row[0] for row in db.execute(
            select(Filing.accession_number)
        ).all()
    )

    count = 0
    seen = set()
    for _, row in df.iterrows():
        acc = _str_or_none(row.get("Accession Number"))
        if not acc or acc in existing_accessions or acc in seen:
            continue
        seen.add(acc)

        db.add(Filing(
            trust_id=trust.id,
            accession_number=acc,
            form=_str_or_none(row.get("Form")) or "",
            filing_date=_parse_date(row.get("Filing Date")),
            primary_document=_str_or_none(row.get("Primary Document")),
            primary_link=_str_or_none(row.get("Primary Link")),
            submission_txt_link=_str_or_none(row.get("Full Submission TXT")),
            cik=trust.cik,
            registrant=_str_or_none(row.get("Registrant")),
            processed=False,
        ))
        count += 1

    db.commit()
    return count


def sync_extractions(db: Session, trust: Trust, output_dir: Path) -> int:
    """Sync step 3 CSV (Fund Extraction) into fund_extractions table.
    Returns count of new extractions inserted."""
    csv_path = output_dir / f"{trust.name}_3_Prospectus_Fund_Extraction.csv"
    if not csv_path.exists():
        return 0

    df = pd.read_csv(csv_path, dtype=str, on_bad_lines="skip", engine="python")
    if df.empty:
        return 0

    # Build accession -> filing_id map
    filing_map: dict[str, int] = {}
    for f_id, f_acc in db.execute(
        select(Filing.id, Filing.accession_number).where(Filing.trust_id == trust.id)
    ).all():
        filing_map[f_acc] = f_id

    # Get existing extraction count by filing to avoid re-importing
    existing_filing_ids = set(
        row[0] for row in db.execute(
            select(FundExtraction.filing_id).where(
                FundExtraction.filing_id.in_(list(filing_map.values()))
            ).distinct()
        ).all()
    ) if filing_map else set()

    count = 0
    for _, row in df.iterrows():
        acc = _str_or_none(row.get("Accession Number"))
        filing_id = filing_map.get(acc)
        if not filing_id or filing_id in existing_filing_ids:
            continue

        db.add(FundExtraction(
            filing_id=filing_id,
            series_id=_str_or_none(row.get("Series ID")),
            series_name=_str_or_none(row.get("Series Name")),
            class_contract_id=_str_or_none(row.get("Class-Contract ID")),
            class_contract_name=_str_or_none(row.get("Class Contract Name")),
            class_symbol=_str_or_none(row.get("Class Symbol")),
            extracted_from=_str_or_none(row.get("Extracted From")),
            effective_date=_parse_date(row.get("Effective Date")),
            effective_date_confidence=_str_or_none(row.get("Effective Date Confidence")),
            delaying_amendment=_bool_val(row.get("Delaying Amendment")),
            prospectus_name=_str_or_none(row.get("Prospectus Name")),
        ))
        count += 1

    db.commit()
    return count


def sync_fund_status(db: Session, trust: Trust, output_dir: Path) -> int:
    """Sync step 4 CSV (Fund Status) into fund_status table.
    Upserts by (trust_id, series_id, class_contract_id).
    Returns count of funds upserted."""
    csv_path = output_dir / f"{trust.name}_4_Fund_Status.csv"
    if not csv_path.exists():
        return 0

    df = pd.read_csv(csv_path, dtype=str, on_bad_lines="skip", engine="python")
    if df.empty:
        return 0

    count = 0
    for _, row in df.iterrows():
        series_id = _str_or_none(row.get("Series ID"))
        class_contract_id = _str_or_none(row.get("Class-Contract ID"))
        ticker = _str_or_none(row.get("Ticker"))

        # Build lookup conditions — use IS NULL for None values (SQL = NULL is always false)
        conditions = [FundStatus.trust_id == trust.id]
        if series_id is None:
            conditions.append(FundStatus.series_id.is_(None))
        else:
            conditions.append(FundStatus.series_id == series_id)
        if class_contract_id is None:
            conditions.append(FundStatus.class_contract_id.is_(None))
        else:
            conditions.append(FundStatus.class_contract_id == class_contract_id)

        # 33 Act trusts have no Series/Class IDs — use ticker as discriminator
        if series_id is None and class_contract_id is None:
            if ticker is not None:
                conditions.append(FundStatus.ticker == ticker)
            else:
                conditions.append(FundStatus.ticker.is_(None))

        existing = db.execute(
            select(FundStatus).where(*conditions)
        ).scalar_one_or_none()

        fund_name = _str_or_none(row.get("Fund Name")) or ""
        sgml_name = _str_or_none(row.get("SGML Name"))
        prospectus_name = _str_or_none(row.get("Prospectus Name"))
        ticker = _str_or_none(row.get("Ticker"))
        status = _str_or_none(row.get("Status")) or "UNKNOWN"
        status_reason = _str_or_none(row.get("Status Reason"))
        effective_date = _parse_date(row.get("Effective Date"))
        eff_conf = _str_or_none(row.get("Effective Date Confidence"))
        latest_form = _str_or_none(row.get("Latest Form"))
        latest_filing_date = _parse_date(row.get("Latest Filing Date"))
        prospectus_link = _str_or_none(row.get("Prospectus Link"))

        if existing:
            existing.fund_name = fund_name
            existing.sgml_name = sgml_name
            existing.prospectus_name = prospectus_name
            existing.ticker = ticker
            existing.status = status
            existing.status_reason = status_reason
            existing.effective_date = effective_date
            existing.effective_date_confidence = eff_conf
            existing.latest_form = latest_form
            existing.latest_filing_date = latest_filing_date
            existing.prospectus_link = prospectus_link
        else:
            db.add(FundStatus(
                trust_id=trust.id,
                series_id=series_id,
                class_contract_id=class_contract_id,
                fund_name=fund_name,
                sgml_name=sgml_name,
                prospectus_name=prospectus_name,
                ticker=ticker,
                status=status,
                status_reason=status_reason,
                effective_date=effective_date,
                effective_date_confidence=eff_conf,
                latest_form=latest_form,
                latest_filing_date=latest_filing_date,
                prospectus_link=prospectus_link,
            ))
        count += 1

    db.commit()
    return count


def sync_name_history(db: Session, trust: Trust, output_dir: Path) -> int:
    """Sync step 5 CSV (Name History) into name_history table.
    Returns count of entries synced."""
    csv_path = output_dir / f"{trust.name}_5_Name_History.csv"
    if not csv_path.exists():
        return 0

    df = pd.read_csv(csv_path, dtype=str)
    if df.empty:
        return 0

    # Get series IDs that belong to this trust (from fund_status)
    trust_series = set(
        row[0] for row in db.execute(
            select(FundStatus.series_id).where(FundStatus.trust_id == trust.id)
        ).all()
        if row[0]
    )

    count = 0
    for _, row in df.iterrows():
        series_id = _str_or_none(row.get("Series ID"))
        name = _str_or_none(row.get("Name"))
        if not series_id or not name:
            continue

        # Check if this exact entry already exists
        existing = db.execute(
            select(NameHistory).where(
                NameHistory.series_id == series_id,
                NameHistory.name == name,
            )
        ).scalar_one_or_none()

        if existing:
            existing.last_seen_date = _parse_date(row.get("Last Seen Date")) or existing.last_seen_date
            existing.is_current = _bool_val(row.get("Is Current"))
        else:
            db.add(NameHistory(
                series_id=series_id,
                name=name,
                name_clean=_str_or_none(row.get("Name Clean")),
                first_seen_date=_parse_date(row.get("First Seen Date")),
                last_seen_date=_parse_date(row.get("Last Seen Date")),
                is_current=_bool_val(row.get("Is Current")),
                source_form=_str_or_none(row.get("Source Form")),
                source_accession=_str_or_none(row.get("Source Accession")),
            ))
        count += 1

    db.commit()
    return count


def sync_trust(db: Session, trust: Trust, output_root: Path) -> dict:
    """Sync all CSV data for one trust into the database.
    Returns dict with counts."""
    output_dir = output_root / trust.name
    if not output_dir.exists():
        return {"trust": trust.name, "filings": 0, "extractions": 0, "funds": 0, "names": 0}

    return {
        "trust": trust.name,
        "filings": sync_filings(db, trust, output_dir),
        "extractions": sync_extractions(db, trust, output_dir),
        "funds": sync_fund_status(db, trust, output_dir),
        "names": sync_name_history(db, trust, output_dir),
    }


def sync_all(db: Session, output_root: Path | None = None) -> list[dict]:
    """Sync all trusts from CSV outputs into the database.
    Returns list of per-trust result dicts."""
    if output_root is None:
        output_root = Path(__file__).resolve().parent.parent.parent / "outputs"

    trust_map = _get_trust_map(db)
    results = []
    for trust in trust_map.values():
        r = sync_trust(db, trust, output_root)
        results.append(r)
        print(f"  {r['trust']}: {r['filings']} filings, {r['extractions']} extractions, "
              f"{r['funds']} funds, {r['names']} names")
    return results


def full_migration(db: Session, output_root: Path | None = None) -> dict:
    """One-time migration: seed trusts + sync all CSV data.
    Returns summary dict."""
    print("Seeding trusts from registry...")
    seeded = seed_trusts(db)
    print(f"  {seeded} new trusts seeded")

    print("\nSyncing CSV data to database...")
    results = sync_all(db, output_root)

    total_filings = sum(r["filings"] for r in results)
    total_funds = sum(r["funds"] for r in results)
    total_names = sum(r["names"] for r in results)

    summary = {
        "trusts_seeded": seeded,
        "trusts_synced": len(results),
        "total_filings": total_filings,
        "total_funds": total_funds,
        "total_names": total_names,
    }
    print(f"\nMigration complete: {summary}")
    return summary
