"""Tests for screener/li_engine/analysis/blue_ocean_report.py.

Synthesises a tiny Bloomberg-shaped DataFrame + a tiny mkt_master_data
SQLite DB, runs build_html(), and verifies the three required sections
render and that REX / MicroSectors rows survive into the appendix.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from screener.li_engine.analysis import blue_ocean_report  # noqa: E402


def _fake_bo_df() -> pd.DataFrame:
    """Mimic the BlueOcean sheet shape that pd.read_excel returns."""
    return pd.DataFrame({
        "Ticker": ["NVDX US", "MSTU US", "AAPL US", "NOISE US"],
        "1M Traded Value": [200_000_000, 150_000_000, 50_000_000, 10_000],
        "3M Traded Value": [500_000_000, 400_000_000, 120_000_000, 30_000],
        "Ticker.1": ["NVDX US", "MSTU US", "AAPL US", "NOISE US"],
        "1M Traded Value.1": [5_000_000, 3_000_000, 2_000_000, 100],
        "3M Traded Value.1": [12_000_000, 8_000_000, 5_000_000, 300],
    })


def _seed_db(db_path: Path) -> None:
    """Create a minimal mkt_master_data table with the columns the report reads."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute("""
            CREATE TABLE mkt_master_data (
                ticker TEXT,
                fund_name TEXT,
                issuer TEXT,
                issuer_nickname TEXT,
                map_li_underlier TEXT,
                primary_category TEXT,
                market_status TEXT
            )
        """)
        rows = [
            ("NVDX US", "T-REX 2X Long NVDA Daily", "ETF Opportunities", "REX", "NVDA US", "LI", "ACTV"),
            ("MSTU US", "T-REX 2X Long MSTR Daily", "ETF Opportunities", "REX", "MSTR US", "LI", "ACTV"),
            ("AAPL US", "MicroSectors Apple 3X Bull", "Bank of Montreal", "MicroSectors", "AAPL US", "LI", "ACTV"),
            ("NOISE US", "Rareview 2x Something", "Rareview", None, "XYZ US", "LI", "ACTV"),
        ]
        conn.executemany(
            "INSERT INTO mkt_master_data "
            "(ticker, fund_name, issuer, issuer_nickname, map_li_underlier, primary_category, market_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture()
def fake_inputs(tmp_path, monkeypatch):
    db_path = tmp_path / "etp_tracker.db"
    _seed_db(db_path)

    # Patch pd.read_excel inside the module so we don't need a real xlsm.
    monkeypatch.setattr(
        blue_ocean_report.pd, "read_excel",
        lambda *a, **kw: _fake_bo_df(),
    )
    return {"db": db_path, "xlsm": tmp_path / "dummy.xlsm"}


def test_build_html_contains_three_sections(fake_inputs):
    html = blue_ocean_report.build_html(
        xlsm_path=fake_inputs["xlsm"], db_path=fake_inputs["db"],
    )
    # Header
    assert "Blue Ocean" in html and "L&amp;I Overnight Trading" in html
    # Three table section headings
    assert "Blue Ocean by Issuer" in html
    assert "Top Underliers by Blue Ocean Turnover" in html
    assert "Appendix" in html and "REX" in html and "MicroSectors" in html
    # Footer
    assert "Source: Bloomberg." in html
    # Three <table> tags expected (one per section)
    assert html.count("<table") == 3


def test_build_html_drops_noise_issuers(fake_inputs):
    """Rareview-style noise (max BO < $100K) must be dropped from Table 1."""
    html = blue_ocean_report.build_html(
        xlsm_path=fake_inputs["xlsm"], db_path=fake_inputs["db"],
    )
    assert "Rareview" not in html


def test_build_html_keeps_rex_and_microsectors(fake_inputs):
    """REX and MicroSectors are highlighted in Table 1 + listed in appendix."""
    html = blue_ocean_report.build_html(
        xlsm_path=fake_inputs["xlsm"], db_path=fake_inputs["db"],
    )
    # Tickers stripped of " US" suffix in the appendix
    assert ">NVDX<" in html
    assert ">AAPL<" in html
    # REX-row highlight colour from the by-issuer renderer
    assert "#eef3f8" in html


def test_main_writes_output_file(tmp_path, fake_inputs, monkeypatch, capsys):
    out = tmp_path / "blue_ocean_test.html"
    monkeypatch.setattr(
        sys, "argv",
        ["blue_ocean_report.py", "--out", str(out),
         "--xlsm", str(fake_inputs["xlsm"]), "--db", str(fake_inputs["db"])],
    )
    rc = blue_ocean_report.main()
    assert rc == 0
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert "Blue Ocean by Issuer" in body
