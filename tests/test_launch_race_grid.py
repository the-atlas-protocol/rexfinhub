"""Tests for screener.li_engine.analysis.launch_race_grid."""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from screener.li_engine.analysis import launch_race_grid as lrg


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------
def test_normalize_underlier_key_drops_us_suffix():
    assert lrg._normalize_underlier_key("NVDA US") == "NVDA"
    assert lrg._normalize_underlier_key("nvda us") == "NVDA"
    assert lrg._normalize_underlier_key("NVDA US Equity") == "NVDA"


def test_normalize_underlier_key_preserves_foreign_exchange():
    assert lrg._normalize_underlier_key("005930 KS") == "005930 KS"
    assert lrg._normalize_underlier_key("FEPI LN") == "FEPI LN"
    assert lrg._normalize_underlier_key("7203 JP") == "7203 JP"


def test_normalize_underlier_key_idempotent():
    for s in ("NVDA", "NVDA US", "FEPI LN", "005930 KS"):
        once = lrg._normalize_underlier_key(s)
        twice = lrg._normalize_underlier_key(once)
        assert once == twice


def test_normalize_underlier_key_empty():
    assert lrg._normalize_underlier_key(None) == ""
    assert lrg._normalize_underlier_key("") == ""
    assert lrg._normalize_underlier_key("   ") == ""


def test_extract_underlier_from_trex_name():
    assert lrg._extract_underlier_from_name("T-REX 2X LONG NVDA DAILY TARGET ETF") == "NVDA"
    assert lrg._extract_underlier_from_name("T-REX 2X INVERSE DRAM DAILY TARGET ETF") == "DRAM"


def test_extract_underlier_from_competitor_name():
    assert lrg._extract_underlier_from_name(
        "Direxion Daily NVDA Bull 2X Shares"
    ) == "NVDA"
    assert lrg._extract_underlier_from_name(
        "GraniteShares 2x Long TSLA Daily ETF"
    ) == "TSLA"


def test_extract_underlier_pre_ipo_label():
    assert lrg._extract_underlier_from_name(
        "T-REX 2X Long Anthropic Daily Target ETF"
    ) == "ANTHROPIC"
    assert lrg._extract_underlier_from_name(
        "T-REX 2X LONG OPENAI DAILY TARGET ETF"
    ) == "OPENAI"


def test_short_issuer():
    assert lrg._short_issuer("DIREXION SHARES ETF TRUST") == "Direxion"
    assert lrg._short_issuer("GraniteShares ETF Trust") == "GraniteShares"
    assert lrg._short_issuer(None) == "—"


# ---------------------------------------------------------------------------
# Grid build — end-to-end with an in-memory DB
# ---------------------------------------------------------------------------
@pytest.fixture
def conn() -> sqlite3.Connection:
    c = sqlite3.connect(":memory:")
    c.executescript("""
        CREATE TABLE rex_products (
            id INTEGER PRIMARY KEY,
            name TEXT, ticker TEXT, series_id TEXT, product_suite TEXT,
            status TEXT, direction TEXT, underlier TEXT, underlying_ticker TEXT,
            initial_filing_date TEXT, estimated_effective_date TEXT,
            latest_form TEXT
        );
        CREATE TABLE filings (
            id INTEGER PRIMARY KEY,
            filing_date TEXT, form TEXT, registrant TEXT, accession_number TEXT
        );
        CREATE TABLE fund_extractions (
            id INTEGER PRIMARY KEY,
            filing_id INTEGER, series_name TEXT, class_symbol TEXT
        );
    """)
    return c


def test_grid_rex_first_when_we_filed_earlier(conn):
    today = date.today()
    rex_eff = (today + timedelta(days=30)).isoformat()
    rex_filing = (today - timedelta(days=45)).isoformat()
    conn.execute(
        "INSERT INTO rex_products(name,status,series_id,underlier,initial_filing_date,estimated_effective_date,latest_form) VALUES (?,?,?,?,?,?,?)",
        ("T-REX 2X LONG NVDA DAILY TARGET ETF", "Filed", "S000111", "NVDA US",
         rex_filing, rex_eff, "485APOS"),
    )
    # Competitor filed 10 days ago — projected effective in 65 days. REX wins.
    comp_filing = (today - timedelta(days=10)).isoformat()
    conn.execute(
        "INSERT INTO filings(filing_date,form,registrant,accession_number) VALUES (?,?,?,?)",
        (comp_filing, "485APOS", "DIREXION SHARES ETF TRUST", "acc-1"),
    )
    conn.execute(
        "INSERT INTO fund_extractions(filing_id,series_name,class_symbol) VALUES (?,?,?)",
        (1, "Direxion Daily 2X Long NVDA ETF", "NVDX"),
    )
    conn.commit()
    rex = lrg.load_rex_pipeline(conn)
    comp = lrg.load_competitor_filings(conn)
    grid = lrg.build_grid(rex, comp)
    assert len(grid) == 1
    row = grid.iloc[0]
    assert row["underlier"] == "NVDA"
    assert row["competitor_count"] == 1
    assert row["winner"] == "REX_FIRST"
    assert "Direxion" in row["competitor_issuers"]


def test_grid_comp_first_when_their_projected_eff_earlier(conn):
    today = date.today()
    rex_eff = (today + timedelta(days=120)).isoformat()
    conn.execute(
        "INSERT INTO rex_products(name,status,series_id,initial_filing_date,estimated_effective_date,latest_form) VALUES (?,?,?,?,?,?)",
        ("T-REX 2X LONG TSLA DAILY TARGET ETF", "Filed", "S000222",
         (today - timedelta(days=10)).isoformat(), rex_eff, "485APOS"),
    )
    # Competitor filed 40 days ago — projected eff in 35 days, well before ours.
    conn.execute(
        "INSERT INTO filings(filing_date,form,registrant,accession_number) VALUES (?,?,?,?)",
        ((today - timedelta(days=40)).isoformat(), "485APOS",
         "GRANITESHARES ETF TRUST", "acc-2"),
    )
    conn.execute(
        "INSERT INTO fund_extractions(filing_id,series_name,class_symbol) VALUES (?,?,?)",
        (1, "GraniteShares 2x Long TSLA Daily ETF", "TSLG"),
    )
    conn.commit()
    rex = lrg.load_rex_pipeline(conn)
    comp = lrg.load_competitor_filings(conn)
    grid = lrg.build_grid(rex, comp)
    assert grid.iloc[0]["winner"] == "COMP_FIRST"


def test_grid_no_comp_when_underlier_unique(conn):
    today = date.today()
    conn.execute(
        "INSERT INTO rex_products(name,status,series_id,underlier,initial_filing_date,estimated_effective_date,latest_form) VALUES (?,?,?,?,?,?,?)",
        ("T-REX 2X LONG AKAM DAILY TARGET ETF", "Filed", "S000333", "AKAM",
         today.isoformat(), (today + timedelta(days=75)).isoformat(), "485APOS"),
    )
    conn.commit()
    rex = lrg.load_rex_pipeline(conn)
    comp = lrg.load_competitor_filings(conn)
    grid = lrg.build_grid(rex, comp)
    assert grid.iloc[0]["winner"] == "NO_COMP"
    assert grid.iloc[0]["competitor_issuers"] == "—"


def test_render_html_includes_kpis_and_header(conn):
    conn.execute(
        "INSERT INTO rex_products(name,status,series_id,initial_filing_date,estimated_effective_date,latest_form) VALUES (?,?,?,?,?,?)",
        ("T-REX 2X LONG NVDA DAILY TARGET ETF", "Filed", "S000444",
         "2026-05-01", "2026-07-15", "485APOS"),
    )
    conn.commit()
    rex = lrg.load_rex_pipeline(conn)
    grid = lrg.build_grid(rex, pd.DataFrame())
    html = lrg.render_html(grid, run_date=date(2026, 6, 8))
    assert "REX Launch Race Grid" in html
    assert "REX FIRST" in html
    assert "Pre-Launch REX" in html
    assert "T-REX 2X LONG NVDA" in html
