"""Sync REX products from SEC filings to the rex_products table.

The rex_products table was seeded from the Excel tracker (470 products).
After that, new REX filings flow through the SEC pipeline into FundStatus,
but they don't automatically appear in rex_products. This service bridges
that gap — after every SEC scrape, any REX fund in FundStatus that's not
in rex_products gets inserted.

Also updates existing rex_products with the latest SEC status when a series_id match is found.

Designed to be called from run_daily.py after step4 completes, and from
the admin panel on demand.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from sqlalchemy import select
from sqlalchemy.orm import Session

log = logging.getLogger(__name__)

REX_TRUST_NAMES = [
    "REX ETF Trust",
    "ETF Opportunities Trust",
    "HANetf II ICAV",
]


def _infer_suite(fund_name: str) -> str:
    """Best-effort suite inference from a fund name."""
    if not fund_name:
        return "T-REX"
    name = fund_name.upper()

    # T-REX leveraged/inverse series
    if re.search(r"T-?REX\s+(?:2X|3X|4X|-2X|-3X|-4X)", name):
        return "T-REX"
    # Growth & Income series (ends in -II like NVII, TSII)
    if "GROWTH" in name and "INCOME" in name:
        return "Growth & Income"
    # Premium Income series
    if "PREMIUM INCOME" in name:
        return "Premium Income"
    # IncomeMax series
    if "INCOMEMAX" in name:
        return "IncomeMax"
    # Autocallable
    if "AUTOCALLABLE" in name:
        return "Autocallable"
    # T-Bill
    if "T-BILL" in name or "LADDERED T-BILL" in name:
        return "T-Bill"
    # Crypto
    if any(kw in name for kw in ["BITCOIN", "BTC", "ETHER", "ETH", "SOLANA", "SOL", "DOGE", "XRP", "CRYPTO"]):
        return "Crypto"
    # Thematic
    if any(kw in name for kw in ["DRONE", "ROBOTIC", "AI ", "QUANTUM", "DEFENSE", "SPACE"]):
        return "Thematic"

    return "T-REX"  # default


def _status_from_sec(sec_status: str) -> str:
    """Map SEC FundStatus to an INITIAL rex_products status (for new inserts only).

    SEC EFFECTIVE means the registration is effective — it does NOT mean
    the fund is actually trading on an exchange. Only Bloomberg tickers
    promote a product to 'Listed'. So SEC EFFECTIVE maps to 'Awaiting Effective'.
    Bloomberg sync handles the Listed transition.
    """
    mapping = {
        "EFFECTIVE": "Awaiting Effective",
        "PENDING": "Filed",
        "DELAYED": "Filed",
        "UNKNOWN": "Filed",
    }
    return mapping.get(sec_status or "", "Filed")


# Rank statuses so we only move FORWARD through the lifecycle.
# Lower number = earlier in lifecycle. Never downgrade.
_STATUS_RANK = {
    "Research": 0,
    "Target List": 1,
    "Filed": 2,
    "Awaiting Effective": 3,
    "Listed": 4,
    "Delisted": 5,
}


def _can_advance(old_status: str, new_status: str) -> bool:
    """Return True if moving old → new is a valid forward transition."""
    old_rank = _STATUS_RANK.get(old_status, -1)
    new_rank = _STATUS_RANK.get(new_status, -1)
    return new_rank > old_rank


def sync_rex_products_from_sec(db: Session, *, update_existing: bool = True) -> dict:
    """Upsert REX products from FundStatus into rex_products.

    Args:
        db: Database session
        update_existing: If True, update status/dates on existing products
                         when a matching series_id is found.

    Returns:
        dict with counts: added, updated, total_rex_funds
    """
    from webapp.models import RexProduct, FundStatus, Trust

    # Find all REX funds in SEC pipeline
    rex_trusts = db.execute(
        select(Trust.id, Trust.name).where(Trust.is_rex == True)
    ).all()

    if not rex_trusts:
        log.info("No REX trusts found in DB")
        return {"added": 0, "updated": 0, "total_rex_funds": 0}

    trust_ids = [t.id for t in rex_trusts]
    trust_name_by_id = {t.id: t.name for t in rex_trusts}

    # Pull all REX funds from FundStatus
    rex_funds = db.execute(
        select(
            FundStatus.series_id,
            FundStatus.class_contract_id,
            FundStatus.fund_name,
            FundStatus.ticker,
            FundStatus.status,
            FundStatus.effective_date,
            FundStatus.latest_form,
            FundStatus.latest_filing_date,
            FundStatus.prospectus_link,
            FundStatus.trust_id,
        )
        .where(FundStatus.trust_id.in_(trust_ids))
    ).all()

    if not rex_funds:
        return {"added": 0, "updated": 0, "total_rex_funds": 0}

    # Existing rex_products keyed by series_id for fast lookup.
    # For name-fallback matching we use a normalized full name (collapse whitespace).
    def _norm_name(n: str | None) -> str:
        return " ".join((n or "").upper().split())

    existing_by_series = {}
    existing_by_name = {}
    for p in db.query(RexProduct).all():
        if p.series_id:
            existing_by_series[p.series_id] = p
        if p.name:
            existing_by_name[_norm_name(p.name)] = p

    added = 0
    updated = 0

    for f in rex_funds:
        if not f.series_id:
            continue

        existing = existing_by_series.get(f.series_id)
        if not existing and f.fund_name:
            # Fallback: match by normalized full name (catches Excel-imported
            # rows that don't have series_id yet but are the same fund)
            existing = existing_by_name.get(_norm_name(f.fund_name))

        new_status = _status_from_sec(f.status)
        trust_name = trust_name_by_id.get(f.trust_id, "")

        if existing:
            if not update_existing:
                continue
            changed = False
            # Never downgrade status — only advance forward through lifecycle.
            # Listed status is controlled by Bloomberg sync, not SEC.
            if _can_advance(existing.status or "", new_status) and new_status != "Listed":
                existing.status = new_status
                changed = True
            if not existing.series_id:
                existing.series_id = f.series_id
                changed = True
            if not existing.class_contract_id and f.class_contract_id:
                existing.class_contract_id = f.class_contract_id
                changed = True
            if not existing.ticker and f.ticker:
                existing.ticker = f.ticker
                changed = True
            if not existing.trust:
                existing.trust = trust_name
                changed = True
            if f.latest_form and existing.latest_form != f.latest_form:
                existing.latest_form = f.latest_form
                changed = True
            if f.prospectus_link and existing.latest_prospectus_link != f.prospectus_link:
                existing.latest_prospectus_link = f.prospectus_link
                changed = True
            if f.latest_filing_date and not existing.initial_filing_date:
                existing.initial_filing_date = f.latest_filing_date
                changed = True
            if f.effective_date and not existing.estimated_effective_date:
                existing.estimated_effective_date = f.effective_date
                changed = True
            if changed:
                existing.updated_at = datetime.utcnow()
                updated += 1
        else:
            # New product — insert
            new_product = RexProduct(
                name=(f.fund_name or "Unknown REX Product")[:200],
                trust=trust_name,
                product_suite=_infer_suite(f.fund_name or ""),
                status=new_status,
                ticker=f.ticker,
                series_id=f.series_id,
                class_contract_id=f.class_contract_id,
                latest_form=f.latest_form,
                latest_prospectus_link=f.prospectus_link,
                initial_filing_date=f.latest_filing_date,
                estimated_effective_date=f.effective_date,
            )
            db.add(new_product)
            # Add to lookup so later rows don't duplicate within the same run
            existing_by_series[f.series_id] = new_product
            added += 1

    db.commit()

    # Deduplicate: if the same name appears multiple times, keep the one
    # with series_id set (the canonical SEC-linked row).
    removed = _dedupe_by_name(db)

    log.info("rex_products sync: added=%d updated=%d dedupe_removed=%d total_rex=%d",
             added, updated, removed, len(rex_funds))
    return {
        "added": added,
        "updated": updated,
        "dedupe_removed": removed,
        "total_rex_funds": len(rex_funds),
    }


def _dedupe_by_name(db: Session) -> int:
    """Remove duplicate rex_products rows with the same name.
    Prefers the row with a series_id (SEC-linked), then the one with a ticker,
    then the earliest-created.
    """
    from webapp.models import RexProduct
    from sqlalchemy import func

    # Find names that appear more than once
    dup_names = [
        r[0] for r in db.query(RexProduct.name)
        .group_by(RexProduct.name)
        .having(func.count(RexProduct.id) > 1)
        .all()
    ]
    if not dup_names:
        return 0

    removed = 0
    for name in dup_names:
        rows = db.query(RexProduct).filter(RexProduct.name == name).all()
        # Sort: series_id present > ticker present > earliest created
        rows.sort(key=lambda p: (
            -int(bool(p.series_id)),
            -int(bool(p.ticker)),
            p.created_at or datetime.min,
        ))
        keeper = rows[0]
        # Merge missing fields from the duplicates into the keeper
        for dup in rows[1:]:
            if not keeper.series_id and dup.series_id:
                keeper.series_id = dup.series_id
            if not keeper.ticker and dup.ticker:
                keeper.ticker = dup.ticker
            if not keeper.class_contract_id and dup.class_contract_id:
                keeper.class_contract_id = dup.class_contract_id
            if not keeper.latest_prospectus_link and dup.latest_prospectus_link:
                keeper.latest_prospectus_link = dup.latest_prospectus_link
            db.delete(dup)
            removed += 1

    db.commit()
    return removed


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Sync REX products from SEC")
    parser.add_argument("--no-update", action="store_true", help="Only insert new, don't update existing")
    args = parser.parse_args()

    from webapp.database import init_db, SessionLocal
    init_db()
    db = SessionLocal()
    try:
        result = sync_rex_products_from_sec(db, update_existing=not args.no_update)
        print(f"Sync result: {result}")
    finally:
        db.close()
