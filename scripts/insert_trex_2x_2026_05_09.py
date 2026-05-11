"""
One-shot manual insert: 14 US + 7 non-US T-REX 2X Long ETFs filed 2026-05-09.

Context: Greg's email Friday 5/8 announced these tickers; Practus paralegal
Sharon confirmed two 485A filings landed at SEC (one 16-fund batch, one
7-fund batch). The SEC scrape pipeline didn't run after 2026-05-04, so these
products never got picked up automatically. This script seeds them into
rex_products so they're visible on /operations/pipeline before the REX Ops
meeting.

Idempotent: skips any product whose name already exists in rex_products.

Usage:
    python scripts/insert_trex_2x_2026_05_09.py
"""
from __future__ import annotations

import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from webapp.database import SessionLocal, init_db  # noqa: E402
from webapp.models import RexProduct  # noqa: E402
from sqlalchemy import text as sa_text  # noqa: E402

# ---------------------------------------------------------------------------
# Configuration — per Sharon's links + Greg's email
# ---------------------------------------------------------------------------
LINK_16 = (
    "https://www.sec.gov/Archives/edgar/data/1771146/"
    "000177114626000937/t-rex2xlong16newetfs485afi.htm"
)
LINK_7 = (
    "https://www.sec.gov/Archives/edgar/data/1771146/"
    "000177114626000938/t-rex2xlong7newetfs485afil.htm"
)

INITIAL_FILING = date(2026, 5, 9)
EST_EFFECTIVE = date(2026, 7, 23)  # +75 days from 485APOS
TRUST = "ETF Opportunities Trust"
CIK = "1771146"
SUITE = "T-REX"
STATUS = "Filed (485A)"
FORM = "485APOS"
DIRECTION = "Long"

# 14 US tickers from Greg's email — these go in the 16-fund batch (LINK_16)
US_UNDERLIERS = [
    "OSS", "TEAM", "VECO", "AMSC", "LWLG", "MXL", "TTMI",
    "NOK", "PURR", "PENG", "AEHR", "WOLF", "GRAB", "SHMD",
]

# 7 non-US underliers — these go in the 7-fund batch (LINK_7).
# Format: (display_name_for_underlier_field, link_to_use)
NON_US_UNDERLIERS = [
    ("SIVE (Sweden)",                 "Sweden — SIVE/SEK"),
    ("Samsung Electronics (005930.KS)", "Korea — Samsung 005930"),
    ("Hyundai Motor (005380.KS)",     "Korea — Hyundai 005380"),
    ("Hanwha (012450.KS)",            "Korea — Hanwha 012450"),
    ("Metaplanet (3350.T)",           "Tokyo — Metaplanet 3350"),
    ("SoftBank Group (9984.T)",       "Tokyo — SoftBank 9984"),
    ("Nintendo (7974.T)",             "Tokyo — Nintendo 7974"),
]


def _name_for(underlier: str) -> str:
    return f"T-REX 2X Long {underlier} Daily Target ETF"


def _audit(db, ticker: str, column: str, new_value: str, reason: str) -> None:
    """Best-effort audit log entry — silently no-ops if table is unhappy."""
    try:
        db.execute(
            sa_text(
                "INSERT INTO classification_audit_log "
                "(sweep_run_id, ticker, column_name, old_value, new_value, "
                " source, confidence, reason, dry_run, created_at) "
                "VALUES (:run_id, :ticker, :col, NULL, :new, :src, :conf, "
                "        :reason, 0, :ts)"
            ),
            {
                "run_id": "manual_2026-05-09_trex2x",
                "ticker": ticker,
                "col": column,
                "new": new_value,
                "src": "manual_insert",
                "conf": "HIGH",
                "reason": reason,
                "ts": datetime.utcnow().isoformat(timespec="seconds"),
            },
        )
    except Exception as e:
        print(f"  audit log skipped for {ticker}/{column}: {e}")


def _insert_one(
    db, *, name: str, underlier: str, link: str, audit_ticker: str
) -> str:
    """Returns 'INS', 'SKIP', or 'ERR'."""
    existing = (
        db.query(RexProduct).filter(RexProduct.name == name).first()
    )
    if existing:
        return "SKIP"

    product = RexProduct(
        name=name,
        product_suite=SUITE,
        status=STATUS,
        underlier=underlier,
        direction=DIRECTION,
        initial_filing_date=INITIAL_FILING,
        estimated_effective_date=EST_EFFECTIVE,
        latest_form=FORM,
        latest_prospectus_link=link,
        trust=TRUST,
        cik=CIK,
        notes=(
            "Manual insert 2026-05-11 (Greg email 2026-05-08, "
            "Sharon confirmation, batch 485APOS filed 2026-05-09)."
        ),
    )
    db.add(product)
    db.flush()  # populate product.id without committing the txn yet

    _audit(
        db,
        ticker=audit_ticker,
        column="status",
        new_value=STATUS,
        reason=(
            "Manual insert — REX Ops meeting prep; SEC pipeline last ran "
            "2026-05-04, missed Friday 5/9 filings."
        ),
    )
    return "INS"


def main() -> int:
    init_db()
    db = SessionLocal()
    inserted = 0
    skipped = 0
    errors = 0
    try:
        print("=== US batch (16-fund 485APOS) ===")
        print(f"  Link: {LINK_16}")
        for ticker in US_UNDERLIERS:
            name = _name_for(ticker)
            try:
                result = _insert_one(
                    db,
                    name=name,
                    underlier=ticker,
                    link=LINK_16,
                    audit_ticker=ticker,
                )
            except Exception as e:
                result = "ERR"
                print(f"  ERR  {ticker}: {e}")
                errors += 1
                continue
            if result == "INS":
                inserted += 1
                print(f"  INS  {ticker:<8} -> {name}")
            else:
                skipped += 1
                print(f"  SKIP {ticker:<8} (already exists)")

        print()
        print("=== Non-US batch (7-fund 485APOS) ===")
        print(f"  Link: {LINK_7}")
        for underlier, audit_label in NON_US_UNDERLIERS:
            name = _name_for(underlier)
            audit_ticker = audit_label.split(" — ")[-1].split()[0]
            try:
                result = _insert_one(
                    db,
                    name=name,
                    underlier=underlier,
                    link=LINK_7,
                    audit_ticker=audit_ticker,
                )
            except Exception as e:
                result = "ERR"
                print(f"  ERR  {underlier}: {e}")
                errors += 1
                continue
            if result == "INS":
                inserted += 1
                print(f"  INS  {underlier}")
            else:
                skipped += 1
                print(f"  SKIP {underlier} (already exists)")

        if errors == 0:
            db.commit()
            print(f"\n=== Committed: {inserted} inserted, {skipped} skipped ===")
        else:
            db.rollback()
            print(
                f"\n=== ROLLED BACK: {errors} error(s) — "
                f"would have inserted {inserted}, skipped {skipped} ==="
            )
            return 1

        # Verification readout
        print()
        print("=== Verification ===")
        rows = db.execute(
            sa_text(
                "SELECT id, name, status, latest_form, initial_filing_date "
                "FROM rex_products "
                "WHERE initial_filing_date = '2026-05-09' "
                "  AND status = 'Filed (485A)' "
                "ORDER BY id DESC"
            )
        ).fetchall()
        print(f"Filed (485A) rows dated 2026-05-09: {len(rows)}")
        for r in rows[:25]:
            print(f"  {r}")
        if len(rows) > 25:
            print(f"  ... ({len(rows) - 25} more)")

        funnel_total = db.execute(
            sa_text(
                "SELECT COUNT(*) FROM rex_products "
                "WHERE status IN ('Filed','Filed (485A)','Filed (485B)','Awaiting Effective')"
            )
        ).scalar()
        print(f"Funnel 'Filed/Awaiting Effective' total: {funnel_total}")

        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
