"""
Tests for get_cc_report() filtering logic.

Anchors the INCOME-BUG-001/003/004/005 fixes from the 2026-06-08 audit:
the CC cohort must be restricted to ACTV + ETF/ETN AND deduped by
ticker_clean before any KPI / segment computation. Without those steps,
multi-category ticker rows inflate counts, AUM, and per-fund flows.
"""
from __future__ import annotations

import pandas as pd
import pytest

from webapp.services import report_data


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    """Force a clean cache for every test, then restore."""
    monkeypatch.setattr(report_data, "_cache", {})
    yield
    monkeypatch.setattr(report_data, "_cache", {})


def _make_master() -> pd.DataFrame:
    """Synthetic master that exercises every filter the CC cohort needs.

    Includes:
      - 2 active CC ETFs (one REX, one peer) — should be kept
      - 1 active CC ETN (REX) — should be kept
      - 1 LIQU/non-active CC ETF — should be dropped
      - 1 active CC OEFI (open-end fund) — should be dropped
      - 1 non-CC ETF (LI) — should be dropped
      - 1 duplicate row of the active REX CC ETF (multi-category join) —
        should collapse via dedup so AUM / flow / count are not doubled
    """
    rows = [
        # REX CC, active, ETF — kept
        dict(ticker_clean="REXA", etp_category="CC", cc_category="Single Stock",
             market_status="ACTV", fund_type="ETF", is_rex=True,
             aum=100.0, fund_flow_1week=5.0, fund_flow_1month=10.0,
             fund_flow_ytd=20.0, annualized_yield=80.0, issuer_display="REX",
             fund_name="REX A"),
        # Duplicate of REXA (multi-category) — must be deduped away
        dict(ticker_clean="REXA", etp_category="CC", cc_category="Single Stock",
             market_status="ACTV", fund_type="ETF", is_rex=True,
             aum=100.0, fund_flow_1week=5.0, fund_flow_1month=10.0,
             fund_flow_ytd=20.0, annualized_yield=80.0, issuer_display="REX",
             fund_name="REX A"),
        # Peer CC, active, ETF — kept
        dict(ticker_clean="PEERA", etp_category="CC", cc_category="Single Stock",
             market_status="ACTV", fund_type="ETF", is_rex=False,
             aum=200.0, fund_flow_1week=2.0, fund_flow_1month=4.0,
             fund_flow_ytd=8.0, annualized_yield=50.0, issuer_display="Peer",
             fund_name="Peer A"),
        # REX CC, active, ETN, Index — kept
        dict(ticker_clean="REXI", etp_category="CC", cc_category="Index/ETF/Basket",
             market_status="ACTV", fund_type="ETN", is_rex=True,
             aum=300.0, fund_flow_1week=7.0, fund_flow_1month=12.0,
             fund_flow_ytd=25.0, annualized_yield=30.0, issuer_display="REX",
             fund_name="REX I"),
        # Non-active CC ETN — dropped by market_status
        dict(ticker_clean="DEAD", etp_category="CC", cc_category="Single Stock",
             market_status="LIQU", fund_type="ETN", is_rex=False,
             aum=999.0, fund_flow_1week=99.0, fund_flow_1month=99.0,
             fund_flow_ytd=99.0, annualized_yield=99.0, issuer_display="Ghost",
             fund_name="Dead"),
        # Open-end fund inside CC (rare but possible) — dropped by fund_type
        dict(ticker_clean="OEFI1", etp_category="CC", cc_category="Single Stock",
             market_status="ACTV", fund_type="OEFI", is_rex=False,
             aum=500.0, fund_flow_1week=50.0, fund_flow_1month=50.0,
             fund_flow_ytd=50.0, annualized_yield=10.0, issuer_display="OEF Co",
             fund_name="OEF One"),
        # LI ETF — dropped by etp_category
        dict(ticker_clean="LIONE", etp_category="LI", cc_category=None,
             market_status="ACTV", fund_type="ETF", is_rex=False,
             aum=400.0, fund_flow_1week=4.0, fund_flow_1month=4.0,
             fund_flow_ytd=4.0, annualized_yield=0.0, issuer_display="LI Co",
             fund_name="LI One"),
    ]
    df = pd.DataFrame(rows)
    # The real loader coerces these numeric and fills NaNs.
    for col in ("aum", "fund_flow_1week", "fund_flow_1month",
                "fund_flow_ytd", "annualized_yield"):
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    df["total_return_1week"] = 0.0
    df["total_return_1month"] = 0.0
    return df


def _seed_cache(monkeypatch) -> None:
    master = _make_master()
    rex_tickers = set(master.loc[master["is_rex"] == True, "ticker_clean"].tolist())
    cache = {
        "available": True,
        "master": master,
        "data_aum": pd.DataFrame(),
        "data_flow": pd.DataFrame(),
        "data_notional": pd.DataFrame(),
        "has_timeseries": False,
        "rex_tickers": rex_tickers,
        "data_as_of": "June 08, 2026",
        "data_as_of_short": "06/08/2026",
    }
    monkeypatch.setattr(report_data, "_cache", cache)
    # Bypass the DB-cache JSON lookup so we exercise the live computation.
    monkeypatch.setattr(report_data, "_read_report_cache", lambda *a, **k: None)
    monkeypatch.setattr(report_data, "_ON_RENDER", False)


def test_cc_cohort_drops_non_active_and_non_etf(monkeypatch):
    """BUG-001: only ACTV ETFs/ETNs in CC, no dupes."""
    _seed_cache(monkeypatch)
    report = report_data.get_cc_report(db=None)
    kpis = report["kpis"]
    # 3 unique active CC ETFs/ETNs: REXA, PEERA, REXI. DEAD/OEFI1/LIONE dropped;
    # the duplicate REXA row collapses to one.
    assert kpis["count"] == 3, f"expected 3 deduped active CC funds, got {kpis['count']}"


def test_cc_cohort_does_not_double_count_aum(monkeypatch):
    """BUG-005: AUM sum must reflect deduped rows only."""
    _seed_cache(monkeypatch)
    report = report_data.get_cc_report(db=None)
    # REXA 100 + PEERA 200 + REXI 300 = $600M -> formatter prints "$600.0M"
    assert report["kpis"]["total_aum"] == "$600.0M"


def test_cc_index_segment_aum_only_counts_index_funds(monkeypatch):
    """BUG-005: Index/ETF/Basket segment AUM excludes Single Stock + dedup."""
    _seed_cache(monkeypatch)
    report = report_data.get_cc_report(db=None)
    # Only REXI is Index — $300M.
    assert report["index_kpis"]["total_aum"] == "$300.0M"
    assert report["index_kpis"]["count"] == 1


def test_cc_ss_segment_rex_count_matches_deduped_rex_funds(monkeypatch):
    """BUG-004: SS REX count comes from deduped cohort."""
    _seed_cache(monkeypatch)
    report = report_data.get_cc_report(db=None)
    # REXA is the only REX SS fund after dedup.
    assert report["ss_kpis"]["rex_count"] == 1
    assert report["ss_kpis"]["count"] == 2  # REXA + PEERA


def test_cc_rex_flow_not_doubled_by_duplicate_rows(monkeypatch):
    """BUG-003: per-REX-fund flow comes from a deduped cohort.

    Without dedup, REXA appears twice with flow=5 each, and the
    'top REX mover' selector would see flow=5 (one row) but the SUMS
    would be 10. Either way, the deduped value (5.0) must be what
    rex_funds reports.
    """
    _seed_cache(monkeypatch)
    report = report_data.get_cc_report(db=None)
    rex = {f["ticker"]: f for f in report["rex_funds"]}
    assert "REXA" in rex and "REXI" in rex
    assert rex["REXA"]["flow_1w"] == pytest.approx(5.0)
    assert rex["REXI"]["flow_1w"] == pytest.approx(7.0)
    # Top mover by abs flow is REXI (7 > 5), not a doubled REXA (would be 10).
    top = max(report["rex_funds"], key=lambda f: abs(f["flow_1w"]))
    assert top["ticker"] == "REXI"


def test_cc_rex_total_aum_not_doubled(monkeypatch):
    """BUG-001/003: REX AUM total reflects each REX ticker exactly once."""
    _seed_cache(monkeypatch)
    report = report_data.get_cc_report(db=None)
    # REXA 100 + REXI 300 = $400M (not $500M which is what a doubled REXA gives).
    assert report["rex_kpis"]["total_aum"] == "$400.0M"
    assert report["rex_kpis"]["count"] == 2
