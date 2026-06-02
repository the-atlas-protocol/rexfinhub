"""Bug A regression test — multi-series 485APOS handling.

A single 485APOS prospectus can register N distinct series_ids (sister funds
filed together). The pre-fix sync collapsed all FundExtractions for one
filing into a single rex_products row, leaking 2 of 3 sister funds.

Asserts:
  1. A 3-series 485APOS produces 3 rex_products rows (one per distinct series).
  2. Each row carries the correct series_id and class_contract_id.
  3. prospectus_name is preserved on the first series, nulled on series #2+.
  4. An extraction with no series_id falls back to name-based matching against
     an existing row (no duplicate insert).
"""
from __future__ import annotations

from datetime import date

import pytest

from scripts.sync_rex_products_from_filings import phase1_2_sync_filings
from webapp.models import Filing, FundExtraction, RexProduct, Trust


CURATED_CIK = "1771146"
CURATED_TRUST_NAME = "ETF Opportunities Trust"


def _trust_ciks() -> set[str]:
    from etp_tracker.trusts import TRUST_CIKS
    return {str(k).lstrip("0") for k in TRUST_CIKS.keys()}


@pytest.fixture()
def curated_trust(db_session):
    trust = Trust(
        cik=CURATED_CIK,
        name=CURATED_TRUST_NAME,
        slug="etf-opportunities-trust",
        is_rex=False,
        is_active=True,
    )
    db_session.add(trust)
    db_session.flush()
    return trust


def test_3_series_485APOS_creates_3_rex_products(db_session, curated_trust):
    """One 485APOS with 3 distinct series_ids should produce 3 rex_products rows."""
    filing = Filing(
        trust_id=curated_trust.id,
        accession_number="0001771146-26-000777",
        form="485APOS",
        filing_date=date(2026, 5, 20),
        primary_link="https://sec.gov/Archives/multi.htm",
        primary_document="multi.htm",
        cik=CURATED_CIK,
        registrant=CURATED_TRUST_NAME,
        processed=True,
    )
    db_session.add(filing)
    db_session.flush()

    # Three sister funds in the same prospectus, each with its own series and class.
    db_session.add(FundExtraction(
        filing_id=filing.id,
        series_id="S000111001",
        series_name="T-REX 2X Long AAA Daily Target ETF",
        class_contract_id="C000222001",
        prospectus_name="REX Multi-Series Prospectus 2026",
    ))
    db_session.add(FundExtraction(
        filing_id=filing.id,
        series_id="S000111002",
        series_name="T-REX 2X Long BBB Daily Target ETF",
        class_contract_id="C000222002",
        prospectus_name="REX Multi-Series Prospectus 2026",
    ))
    db_session.add(FundExtraction(
        filing_id=filing.id,
        series_id="S000111003",
        series_name="T-REX 2X Long CCC Daily Target ETF",
        class_contract_id="C000222003",
        prospectus_name="REX Multi-Series Prospectus 2026",
    ))
    db_session.commit()

    stats = phase1_2_sync_filings(
        db_session, since=date(2026, 5, 1),
        dry_run=False, trust_ciks=_trust_ciks(),
    )

    # One filing scanned, three rex_products created
    assert stats.filings_scanned == 1
    assert stats.new_products_inserted == 3

    products = db_session.query(RexProduct).order_by(RexProduct.series_id).all()
    assert len(products) == 3

    series_ids = {p.series_id for p in products}
    assert series_ids == {"S000111001", "S000111002", "S000111003"}

    # Each row carries its own class_contract_id
    by_series = {p.series_id: p for p in products}
    assert by_series["S000111001"].class_contract_id == "C000222001"
    assert by_series["S000111002"].class_contract_id == "C000222002"
    assert by_series["S000111003"].class_contract_id == "C000222003"

    # Each row got the correct name
    assert by_series["S000111001"].name == "T-REX 2X Long AAA Daily Target ETF"
    assert by_series["S000111002"].name == "T-REX 2X Long BBB Daily Target ETF"
    assert by_series["S000111003"].name == "T-REX 2X Long CCC Daily Target ETF"


def test_prospectus_name_nulled_on_series_2_plus(db_session, curated_trust):
    """First series keeps prospectus_name; series #2 and #3 get it nulled."""
    filing = Filing(
        trust_id=curated_trust.id,
        accession_number="0001771146-26-000888",
        form="485APOS",
        filing_date=date(2026, 5, 21),
        primary_link="https://sec.gov/Archives/prosp.htm",
        cik=CURATED_CIK,
        registrant=CURATED_TRUST_NAME,
    )
    db_session.add(filing)
    db_session.flush()

    db_session.add(FundExtraction(
        filing_id=filing.id,
        series_id="S000333001",
        series_name="T-REX 2X Long DDD Daily Target ETF",
        prospectus_name="REX 2026 Prospectus",
    ))
    db_session.add(FundExtraction(
        filing_id=filing.id,
        series_id="S000333002",
        series_name="T-REX 2X Long EEE Daily Target ETF",
        prospectus_name="REX 2026 Prospectus",
    ))
    db_session.commit()

    phase1_2_sync_filings(
        db_session, since=date(2026, 5, 1),
        dry_run=False, trust_ciks=_trust_ciks(),
    )

    exts = db_session.query(FundExtraction).order_by(FundExtraction.series_id).all()
    # Sorted by series_id (S000333001 < S000333002): first keeps, second nulled
    assert exts[0].series_id == "S000333001"
    assert exts[0].prospectus_name == "REX 2026 Prospectus"
    assert exts[1].series_id == "S000333002"
    assert exts[1].prospectus_name is None


def test_no_series_id_falls_back_to_name_match(db_session, curated_trust):
    """An extraction with no series_id (~old 497s) matches an existing row by name."""
    # Pre-existing rex_products row, name-matchable
    db_session.add(RexProduct(
        name="T-REX 2X Long FFF Daily Target ETF",
        trust=CURATED_TRUST_NAME,
        product_suite="T-REX",
        status="Filed",
        cik=CURATED_CIK,
        latest_form="485APOS",
        initial_filing_date=date(2026, 4, 1),
    ))
    db_session.flush()

    filing = Filing(
        trust_id=curated_trust.id,
        accession_number="0001771146-26-000999",
        form="485BPOS",
        filing_date=date(2026, 5, 22),
        primary_link="https://sec.gov/Archives/noseries.htm",
        cik=CURATED_CIK,
        registrant=CURATED_TRUST_NAME,
    )
    db_session.add(filing)
    db_session.flush()
    # Extraction with NO series_id — falls back to (cik, name) match
    db_session.add(FundExtraction(
        filing_id=filing.id,
        series_name="T-REX 2X Long FFF Daily Target ETF",
    ))
    db_session.commit()

    stats = phase1_2_sync_filings(
        db_session, since=date(2026, 5, 1),
        dry_run=False, trust_ciks=_trust_ciks(),
    )

    # No new row — matched existing via name
    assert stats.new_products_inserted == 0
    assert stats.skipped_already_matched == 1
    assert db_session.query(RexProduct).count() == 1

    # Existing row got promoted to Effective
    db_session.expire_all()
    p = db_session.query(RexProduct).one()
    assert p.status == "Effective"
    assert p.latest_form == "485BPOS"
