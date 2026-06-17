"""Definition-library parity + behaviour tests.

Guards the single source of truth (market/definitions.py) against drift and
asserts it matches the human contract in docs/DEFINITIONS.md.
"""
from pathlib import Path

import pytest

from market.definitions import (
    INTERNAL_SUITES,
    SUITE_DISPLAYS,
    LIVE_STATUSES,
    FUTURE_STATUSES,
    LIVE_TRUSTS,
    PIPELINE_ONLY_TRUSTS,
    suite_of,
    suite_for_name,
    status_role,
    counts_in_live_kpi,
    present_value,
)

DOC = Path(__file__).resolve().parent.parent / "docs" / "DEFINITIONS.md"


# --- the nine suites, canonical order ---------------------------------------
EXPECTED_SUITES = [
    "MicroSectors",
    "T-REX",
    "Equity Premium Income",
    "Growth & Income",
    "IncomeMax",
    "Structured",
    "Thematic",
    "Crypto",
    "MoneyMarket",
]


def test_suite_list_and_order():
    assert SUITE_DISPLAYS == EXPECTED_SUITES
    assert len(INTERNAL_SUITES) == 9


def test_doc_lists_every_suite():
    text = DOC.read_text(encoding="utf-8")
    for s in EXPECTED_SUITES:
        assert s in text, f"docs/DEFINITIONS.md missing suite {s!r}"


# --- classifier behaviour ----------------------------------------------------
@pytest.mark.parametrize("ticker,name,expected", [
    ("SPAX US", "T-REX 2X LONG SPACEX DAILY TARGET ETF", "T-REX"),
    ("MSTZ US", "T-REX 2X INVERSE STRATEGY DAILY TARGET ETF", "T-REX"),
    ("FNGU US", "MICROSECTORS FANG INDEX 3X LEVERAGED ETN", "MicroSectors"),
    ("SSK US", "REX-OSPREY SOL + STAKING ETF", "Crypto"),
    ("FEPI US", "REX FANG & INNOVATION EQUITY PREMIUM INCOME ETF", "Equity Premium Income"),
    ("AAPY US", "REX AAPL GROWTH & INCOME ETF", "Growth & Income"),
    ("ULTI US", "REX INCOMEMAX OPTION STRATEGY ETF", "IncomeMax"),
    ("ATCL US", "REX AUTOCALLABLE INCOME ETF", "Structured"),
    ("DRNZ US", "REX DRONE ETF", "Thematic"),
    ("TLDR US", "THE LADDERED T-BILL ETF", "MoneyMarket"),
    # not a REX suite fund
    ("YMAX US", "YIELDMAX UNIVERSE FUND OF OPTION INCOME ETFS", None),
    ("CIEG US", "LEVERAGE SHARES 2X LONG CIEN DAILY ETF", None),
])
def test_suite_of(ticker, name, expected):
    assert suite_of(ticker, name) == expected


def test_tldr_needs_ticker_not_name():
    # name alone cannot classify MoneyMarket (no REX prefix); ticker override does.
    assert suite_for_name("THE LADDERED T-BILL ETF") is None
    assert suite_of("TLDR US", "THE LADDERED T-BILL ETF") == "MoneyMarket"


def test_patterns_never_tag_a_competitor():
    # a competitor whose name happens to contain a suite keyword but no REX anchor
    assert suite_of("XYZ US", "ACME GROWTH & INCOME ETF") is None
    assert suite_of("XYZ US", "ACME AUTOCALLABLE NOTE ETF") is None


# --- market status semantics -------------------------------------------------
def test_status_roles():
    assert status_role("ACTV") == "live"
    assert status_role("PEND") == "future"
    for s in ["LIQU", "DLST", "ACQU", "INAC", "EXPD", "TKCH", "UNLS", "PRNA", "WAT", None]:
        assert status_role(s) == "closed"


def test_counts_in_live_kpi():
    assert counts_in_live_kpi("ACTV") is True
    assert counts_in_live_kpi("PEND") is False
    assert counts_in_live_kpi("LIQU") is False


def test_present_value_rule():
    assert present_value("ACTV", 123.4) == 123.4
    assert present_value("LIQU", 999.0) == 0.0   # closed -> $0 today
    assert present_value("DLST", 50.0) == 0.0
    assert present_value("PEND", 10.0) is None    # future -> excluded


# --- trusts ------------------------------------------------------------------
def test_trusts():
    assert "REX ETF Trust" in LIVE_TRUSTS
    assert "ETF Opportunities Trust" in LIVE_TRUSTS
    assert "World Funds Trust" in LIVE_TRUSTS
    assert "Exchange Listed Funds Trust" in PIPELINE_ONLY_TRUSTS
    assert "Exchange Listed Funds Trust" not in LIVE_TRUSTS


def test_status_sets():
    assert LIVE_STATUSES == frozenset({"ACTV"})
    assert FUTURE_STATUSES == frozenset({"PEND"})
