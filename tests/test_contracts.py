"""Offline tests for the report-rules contract (market/contracts.py).

Proves the YAML loads, the five headline numbers + two category definitions are present,
the AI-intent block carries the meaning (not just the label), and apply_report_filter
builds the correct mask + dedup — including the LI single-stock dedup that the old
hand-written filter was missing.
"""
import pandas as pd

from market import contracts


def test_contract_loads_with_five_numbers_and_two_defs():
    c = contracts.load_contract()
    assert c, "contract failed to load"
    nums = contracts.numbers()
    for rid in [
        "trex_single_stock_li_actv",
        "microsectors_index_li_actv",
        "rex_income_single_stock_actv",
        "rex_income_index_actv",
        "rex_total_actv",
    ]:
        assert rid in nums, f"missing number {rid}"
    assert set(contracts.category_definitions()) >= {"LI", "CC"}


def test_expected_values_match_approved():
    exp = contracts.expected_values()
    assert exp["trex_single_stock_li_actv"] == 41
    assert exp["microsectors_index_li_actv"] == 22
    assert exp["rex_income_single_stock_actv"] == 3
    assert exp["rex_income_index_actv"] == 8
    assert exp["rex_total_actv"] == 79


def test_intent_text_carries_meaning_not_just_label():
    txt = contracts.category_intent_text().lower()
    # the whole point: the AI is told CC means options income and bonds are excluded
    assert "option" in txt
    assert "bond" in txt
    assert "daily-reset" in txt or "leverage" in txt


def _master():
    # ticker_clean intentionally has a duplicate (TREXX appears twice) to exercise dedup
    return pd.DataFrame(
        [
            # REX LI single-stock live ETF -> counts in T-REX
            dict(ticker="TREXX US", ticker_clean="TREXX", is_rex=1, etp_category="LI",
                 market_status="ACTV", fund_type="ETF",
                 category_display="Leverage & Inverse - Single Stock", rex_suite="T-REX"),
            # duplicate row for same fund (multi-category) -> must NOT double-count
            dict(ticker="TREXX US", ticker_clean="TREXX", is_rex=1, etp_category="LI",
                 market_status="ACTV", fund_type="ETF",
                 category_display="Leverage & Inverse - Single Stock", rex_suite="T-REX"),
            # REX LI index live -> excluded from single-stock number
            dict(ticker="IDXL US", ticker_clean="IDXL", is_rex=1, etp_category="LI",
                 market_status="ACTV", fund_type="ETF",
                 category_display="Leverage & Inverse - Index/Basket/ETF Based", rex_suite="T-REX"),
            # REX LI single-stock but PENDING -> excluded (active_only)
            dict(ticker="PENDX US", ticker_clean="PENDX", is_rex=1, etp_category="LI",
                 market_status="PEND", fund_type="ETF",
                 category_display="Leverage & Inverse - Single Stock", rex_suite="T-REX"),
            # REX CC single-stock live -> income single stock
            dict(ticker="NVII US", ticker_clean="NVII", is_rex=1, etp_category="CC",
                 market_status="ACTV", fund_type="ETF",
                 category_display="Income - Single Stock", rex_suite="Income"),
            # non-REX LI single-stock -> excluded from REX numbers
            dict(ticker="COMP US", ticker_clean="COMP", is_rex=0, etp_category="LI",
                 market_status="ACTV", fund_type="ETF",
                 category_display="Leverage & Inverse - Single Stock", rex_suite=None),
        ]
    )


def test_trex_single_stock_dedups_and_excludes():
    df = _master()
    n = contracts.count_for_rule(df, "trex_single_stock_li_actv")
    # one fund (deduped from 2 rows); index/pending/non-rex all excluded
    assert n == 1, f"expected 1, got {n}"


def test_income_single_stock_filter():
    df = _master()
    sel = contracts.apply_report_filter(df, "rex_income_single_stock_actv")
    assert list(sel["ticker_clean"]) == ["NVII"]


def test_rex_total_counts_only_rex_active():
    df = _master()
    # REX + ACTV, deduped: TREXX, IDXL, NVII == 3 (PENDX excluded, COMP non-rex excluded)
    assert contracts.count_for_rule(df, "rex_total_actv") == 3
