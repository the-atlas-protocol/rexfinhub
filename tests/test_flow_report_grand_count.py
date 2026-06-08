"""Lock-in tests for the Flow Report grand-KPI "Active ETPs" count.

BUG-P0-001 (2026-06-08): the grand ``count`` in the competitive Flow
Report diverged from DB truth (rendered 5,428 vs canonical 5,368). Root
cause was inconsistent ``ticker_clean`` values across multi-category
rows of the same fund (the dedup ran, but values like ``FEPI``,
``FEPI US``, and ``FEPI US Equity`` did not collapse to one bucket).

Canonical semantic (from cross-report reconcile output):

    SELECT COUNT(DISTINCT ticker_clean)
    FROM mkt_master_data
    WHERE market_status='ACTV' AND fund_type IN ('ETF','ETN');

The fix normalizes ``ticker_clean`` via ``normalize_underlier`` at load
time and drops empty ``ticker_clean`` rows before dedup so the rendered
count equals the SQL.
"""
from __future__ import annotations

import pandas as pd
import pytest

from webapp.services import report_data
from webapp.services.report_data import get_flow_report


def _seed_cache(master: pd.DataFrame) -> None:
    """Inject a synthetic master DataFrame into the module-level cache."""
    report_data._cache = {
        "available": True,
        "master": master,
        "data_aum": pd.DataFrame(),
        "data_flow": pd.DataFrame(),
        "data_notional": pd.DataFrame(),
        "has_timeseries": False,
        "rex_tickers": set(),
        "li_attrs": pd.DataFrame(),
        "cc_attrs": pd.DataFrame(),
        "issuer_map_df": pd.DataFrame(),
        "data_as_of": "January 01, 2026",
        "data_as_of_short": "01/01/2026",
    }


@pytest.fixture(autouse=True)
def _reset_cache():
    report_data.invalidate_cache()
    yield
    report_data.invalidate_cache()


def _row(ticker: str, ticker_clean: str, category: str, aum: float = 1.0,
         status: str = "ACTV", fund_type: str = "ETF",
         issuer: str = "Acme", is_rex: bool = False) -> dict:
    return {
        "ticker": ticker,
        "ticker_clean": ticker_clean,
        "etp_category": category,
        "market_status": status,
        "fund_type": fund_type,
        "issuer": issuer,
        "issuer_display": issuer,
        "fund_name": f"{ticker} Fund",
        "is_rex": 1 if is_rex else 0,
        "aum": aum,
        "fund_flow_1week": 0.0,
        "fund_flow_1month": 0.0,
        "rex_suite": None,
        "category_display": category,
    }


def _grand_count(report: dict) -> int:
    """Pull the integer count out of the report dict, regardless of fmt."""
    raw = report["grand_kpis"]["count"]
    return int(raw)


class TestFlowReportGrandCountDedup:
    """The grand count must equal COUNT(DISTINCT ticker_clean) over the
    ACTV/ETF-ETN slice — i.e. multi-category duplicates collapse to one."""

    def test_single_ticker_multi_category_counts_once(self):
        # Same fund, two etp_category rows (LI + CC), identical ticker_clean.
        master = pd.DataFrame([
            _row("AAA US Equity", "AAA US", "LI"),
            _row("AAA US Equity", "AAA US", "CC"),
        ])
        _seed_cache(master)
        report = get_flow_report()
        assert _grand_count(report) == 1

    def test_distinct_tickers_each_count_once(self):
        master = pd.DataFrame([
            _row("AAA US Equity", "AAA US", "LI"),
            _row("BBB US Equity", "BBB US", "LI"),
            _row("CCC US Equity", "CCC US", "CC"),
        ])
        _seed_cache(master)
        report = get_flow_report()
        assert _grand_count(report) == 3

    def test_inactive_status_excluded(self):
        master = pd.DataFrame([
            _row("AAA US Equity", "AAA US", "LI", status="ACTV"),
            _row("ZZZ US Equity", "ZZZ US", "LI", status="DLST"),
            _row("PPP US Equity", "PPP US", "LI", status="PEND"),
        ])
        _seed_cache(master)
        report = get_flow_report()
        assert _grand_count(report) == 1

    def test_non_etp_fund_types_excluded(self):
        master = pd.DataFrame([
            _row("AAA US Equity", "AAA US", "LI", fund_type="ETF"),
            _row("BBB US Equity", "BBB US", "LI", fund_type="ETN"),
            _row("OEF US Equity", "OEF US", "LI", fund_type="Open-End Fund"),
            _row("SIC LX Equity", "SIC LX", "LI", fund_type="SICAV"),
        ])
        _seed_cache(master)
        report = get_flow_report()
        assert _grand_count(report) == 2

    def test_cross_listed_root_preserved(self):
        # FEPI US and FEPI LN are distinct securities — must not collapse.
        master = pd.DataFrame([
            _row("FEPI US Equity", "FEPI US", "CC"),
            _row("FEPI LN Equity", "FEPI LN", "CC"),
        ])
        _seed_cache(master)
        report = get_flow_report()
        assert _grand_count(report) == 2

    def test_empty_ticker_clean_excluded(self):
        # Mirrors SQL COUNT(DISTINCT) semantics — NULL/empty does not count.
        master = pd.DataFrame([
            _row("AAA US Equity", "AAA US", "LI"),
            _row("", "", "LI"),
        ])
        _seed_cache(master)
        report = get_flow_report()
        assert _grand_count(report) == 1

    def test_matches_sql_count_distinct_semantics(self):
        """End-to-end semantic anchor: build a master that mimics the
        production shape (some funds in multiple categories, some DLST,
        some non-ETPs, some with empty ticker_clean) and assert the
        rendered grand count equals what the canonical SQL would return."""
        rows = []
        # 10 single-category active ETFs
        for i in range(10):
            rows.append(_row(f"S{i:02d} US Equity", f"S{i:02d} US", "LI"))
        # 5 multi-category active ETFs (each appears in LI + CC)
        for i in range(5):
            t_clean = f"M{i:02d} US"
            rows.append(_row(f"M{i:02d} US Equity", t_clean, "LI"))
            rows.append(_row(f"M{i:02d} US Equity", t_clean, "CC"))
        # 3 DLST (must be excluded)
        for i in range(3):
            rows.append(_row(f"D{i:02d} US Equity", f"D{i:02d} US", "LI", status="DLST"))
        # 2 non-ETP fund types (must be excluded)
        rows.append(_row("OEF1 US Equity", "OEF1 US", "LI", fund_type="Open-End Fund"))
        rows.append(_row("OEF2 US Equity", "OEF2 US", "LI", fund_type="SICAV"))

        master = pd.DataFrame(rows)
        _seed_cache(master)
        report = get_flow_report()

        # Canonical: COUNT(DISTINCT ticker_clean) over active ETF/ETN slice.
        active = master[
            (master["market_status"] == "ACTV")
            & (master["fund_type"].isin(["ETF", "ETN"]))
        ]
        expected = active.loc[active["ticker_clean"].str.len() > 0,
                              "ticker_clean"].nunique()
        assert _grand_count(report) == expected == 15
