"""
Weekly Digest semantic-audit regression tests (audit 2026-06-08).

Anchors the canonical definitions reconciled across the eight per-report
audits so that the four bugs identified in the weekly digest cannot
regress silently.

Each test maps to one bug:
  - BUG-2: T-REX suite product count must NOT under-count REX products
           tagged only via `issuer_display`.
  - BUG-3: Total REX AUM must use UNION(is_rex, issuer_display='REX')
           semantics — strict `is_rex == True` alone misses issuer-only rows.
  - BUG-4: "Newly Effective" / "Pending" filing KPIs are fund-level
           counts; they must count DISTINCT series_id, not share-class rows.

BUG-1 (Active ETPs 5,362 vs 5,368) is a data-sync / cache freshness
issue with no code-level fix in the builder; see open_issues in the
PR description.
"""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _make_master_df() -> pd.DataFrame:
    """Minimal master frame with the columns get_rex_summary touches.

    Three REX-flagged rows + one issuer-only REX row + one non-REX row,
    so the UNION mask produces 4 REX products vs 3 under strict is_rex.
    """
    return pd.DataFrame(
        [
            # Strict is_rex=True T-REX rows (count: 2)
            {
                "ticker": "TSLT", "ticker_clean": "TSLT", "fund_name": "T-REX 2X Tesla",
                "is_rex": True, "issuer_display": "REX",
                "rex_suite": "T-REX", "category_display": "Leverage & Inverse - Single Equity",
                "primary_category": "LI", "fund_type": "ETF", "market_status": "ACTV",
                "t_w4.aum": 500.0, "t_w4.aum_1": 480.0,
                "t_w4.fund_flow_1week": 10.0, "t_w4.fund_flow_1month": 20.0,
                "t_w4.fund_flow_3month": 30.0, "t_w4.fund_flow_6month": 40.0,
                "t_w4.fund_flow_ytd": 50.0, "t_w4.fund_flow_1year": 60.0,
            },
            {
                "ticker": "NVDX", "ticker_clean": "NVDX", "fund_name": "T-REX 2X NVDA",
                "is_rex": True, "issuer_display": "REX",
                "rex_suite": "T-REX", "category_display": "Leverage & Inverse - Single Equity",
                "primary_category": "LI", "fund_type": "ETF", "market_status": "ACTV",
                "t_w4.aum": 700.0, "t_w4.aum_1": 650.0,
                "t_w4.fund_flow_1week": 15.0, "t_w4.fund_flow_1month": 25.0,
                "t_w4.fund_flow_3month": 35.0, "t_w4.fund_flow_6month": 45.0,
                "t_w4.fund_flow_ytd": 55.0, "t_w4.fund_flow_1year": 65.0,
            },
            # Issuer-only REX row (is_rex flag missing) — must be INCLUDED by union
            {
                "ticker": "REXX", "ticker_clean": "REXX", "fund_name": "REX Issuer-Only Fund",
                "is_rex": False, "issuer_display": "REX",
                "rex_suite": "T-REX", "category_display": "Leverage & Inverse - Single Equity",
                "primary_category": "LI", "fund_type": "ETF", "market_status": "ACTV",
                "t_w4.aum": 200.0, "t_w4.aum_1": 180.0,
                "t_w4.fund_flow_1week": 5.0, "t_w4.fund_flow_1month": 10.0,
                "t_w4.fund_flow_3month": 15.0, "t_w4.fund_flow_6month": 20.0,
                "t_w4.fund_flow_ytd": 25.0, "t_w4.fund_flow_1year": 30.0,
            },
            # MicroSectors REX row (suite breakdown sanity)
            {
                "ticker": "BULZ", "ticker_clean": "BULZ", "fund_name": "MicroSectors FANG+",
                "is_rex": True, "issuer_display": "REX",
                "rex_suite": "MicroSectors", "category_display": "Leverage & Inverse - Index/Basket",
                "primary_category": "LI", "fund_type": "ETN", "market_status": "ACTV",
                "t_w4.aum": 1000.0, "t_w4.aum_1": 950.0,
                "t_w4.fund_flow_1week": 0.0, "t_w4.fund_flow_1month": 0.0,
                "t_w4.fund_flow_3month": 0.0, "t_w4.fund_flow_6month": 0.0,
                "t_w4.fund_flow_ytd": 0.0, "t_w4.fund_flow_1year": 0.0,
            },
            # Non-REX competitor row (must NOT be counted)
            {
                "ticker": "TSLL", "ticker_clean": "TSLL", "fund_name": "Direxion 2X Tesla",
                "is_rex": False, "issuer_display": "Direxion",
                "rex_suite": None, "category_display": "Leverage & Inverse - Single Equity",
                "primary_category": "LI", "fund_type": "ETF", "market_status": "ACTV",
                "t_w4.aum": 1500.0, "t_w4.aum_1": 1400.0,
                "t_w4.fund_flow_1week": 50.0, "t_w4.fund_flow_1month": 100.0,
                "t_w4.fund_flow_3month": 150.0, "t_w4.fund_flow_6month": 200.0,
                "t_w4.fund_flow_ytd": 250.0, "t_w4.fund_flow_1year": 300.0,
            },
        ]
    )


@pytest.fixture()
def patched_market(monkeypatch):
    """Patch _load_master to return the deterministic test frame."""
    from webapp.services import market_data

    df = _make_master_df()

    monkeypatch.setattr(market_data, "_load_master", lambda _db: df.copy())
    # Prevent issuer canonicalization from touching the test frame (the
    # AUTO canon CSV is irrelevant for these semantic tests).
    monkeypatch.setattr(
        market_data,
        "_apply_issuer_canonicalization",
        lambda d: d,
    )
    # Reset cache safety net
    market_data.invalidate_cache()
    yield market_data
    market_data.invalidate_cache()


# ---------------------------------------------------------------------------
# BUG-3 — Total REX AUM UNION semantics
# ---------------------------------------------------------------------------
def test_bug3_rex_summary_union_semantics_includes_issuer_only_rex(patched_market):
    """get_rex_summary must UNION is_rex flag with issuer_display='REX'.

    Without the union, the issuer-only REXX row ($200M) is dropped and
    total REX AUM is under-reported.
    """
    summary = patched_market.get_rex_summary(db=None, fund_structure="ETF,ETN")
    kpis = summary["kpis"]

    # 4 REX products (2 strict + 1 issuer-only + 1 MicroSectors)
    assert kpis["count"] == 4, (
        f"Expected 4 REX products under UNION semantics, got {kpis['count']}. "
        "Strict is_rex == True only would yield 3."
    )
    # AUM = 500 + 700 + 200 + 1000 = 2400
    assert kpis["total_aum"] == 2400.0, (
        f"Expected REX AUM $2,400M (UNION); got ${kpis['total_aum']}M. "
        "Strict is_rex would yield $2,200M, missing the issuer-only row."
    )


# ---------------------------------------------------------------------------
# BUG-2 — T-REX suite count must include issuer-only REX rows
# ---------------------------------------------------------------------------
def test_bug2_trex_suite_count_includes_issuer_only_rex(patched_market):
    """The per-suite breakdown must inherit the UNION mask.

    T-REX suite should count: TSLT + NVDX + REXX = 3 products. Without
    the union fix, only TSLT + NVDX = 2 would be counted.
    """
    summary = patched_market.get_rex_summary(db=None, fund_structure="ETF,ETN")
    suites = {s["name"]: s for s in summary["suites"]}

    assert "T-REX" in suites, f"T-REX suite missing from summary; got {list(suites)}"
    trex = suites["T-REX"]
    assert trex["kpis"]["count"] == 3, (
        f"T-REX suite count should be 3 (TSLT+NVDX+REXX); got {trex['kpis']['count']}. "
        "Under-counting indicates the union mask was not applied before suite slicing."
    )
    # AUM: 500 + 700 + 200 = 1400
    assert trex["kpis"]["total_aum"] == 1400.0


# ---------------------------------------------------------------------------
# BUG-4 — Filing KPIs count distinct funds, not share classes
# ---------------------------------------------------------------------------
def test_bug4_filing_metrics_count_distinct_series(db_session):
    """_gather_filing_data must count distinct series_id for Pending / Newly Effective.

    FundStatus has one row per share class — counting Filing.id over-reports
    by ~10x in the live DB (one fund can carry 5-10 share classes).
    """
    from webapp.models import Trust, FundStatus
    from etp_tracker.weekly_digest import _gather_filing_data

    trust = Trust(cik="0001111111", name="Audit Trust", slug="audit-trust", is_active=True)
    db_session.add(trust)
    db_session.flush()

    today = date.today()
    # One PENDING fund, 3 share classes
    for cls in ("C000000001", "C000000002", "C000000003"):
        db_session.add(FundStatus(
            trust_id=trust.id,
            series_id="S000000001",
            class_contract_id=cls,
            fund_name="Pending Multi-Class Fund",
            ticker=f"PND{cls[-1]}",
            status="PENDING",
            latest_form="485APOS",
            latest_filing_date=today,
        ))
    # One EFFECTIVE fund with 2 share classes, effective yesterday
    for cls in ("C000000004", "C000000005"):
        db_session.add(FundStatus(
            trust_id=trust.id,
            series_id="S000000002",
            class_contract_id=cls,
            fund_name="Effective Multi-Class Fund",
            ticker=f"EFF{cls[-1]}",
            status="EFFECTIVE",
            effective_date=today - timedelta(days=1),
            latest_form="485BPOS",
            latest_filing_date=today - timedelta(days=1),
        ))
    db_session.commit()

    data = _gather_filing_data(db_session, days=7)

    # Distinct series count, not 3+2=5 share-class rows.
    assert data["pending_funds"] == 1, (
        f"PENDING should count distinct series (1 fund / 3 classes); got {data['pending_funds']}."
    )
    assert data["newly_effective"] == 1, (
        f"EFFECTIVE should count distinct series (1 fund / 2 classes); got {data['newly_effective']}."
    )
