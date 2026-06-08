"""Semantic-audit anchor tests for trex_combined_v9 + foreign_filings + ipo_watchlist.

These lock in the fixes from the 2026-06-08 semantic audit:
  BUG-19 — T-REX 2X pipeline must include BOTH Long and Inverse products.
  BUG-20 — foreign_filings rollup: 'filed' must beat 'pending' in REX status priority.
  BUG-21 — config/ipo_watchlist.yaml must not list Cerebras as a pre-IPO target
           ('Filed S-1' state); Cerebras IPO'd in 2024 and 2x products exist.

Tests use an in-memory SQLite DB or direct function calls — no production DB
dependency. The point is to anchor the SEMANTIC behavior of each fix.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest
import yaml


# ---------------------------------------------------------------------------
# BUG-19 — Pipeline must include T-REX 2X Inverse
# ---------------------------------------------------------------------------
def _make_pipeline_db(tmp_path: Path) -> Path:
    """Build a tiny rex_products + li_engine_daily DB for pipeline tests."""
    db_path = tmp_path / "etp_tracker.db"
    conn = sqlite3.connect(str(db_path))
    conn.executescript(
        """
        CREATE TABLE rex_products (
            ticker TEXT,
            name TEXT,
            direction TEXT,
            status TEXT,
            initial_filing_date TEXT,
            estimated_effective_date TEXT,
            underlier TEXT,
            underlying_ticker TEXT
        );
        CREATE TABLE li_engine_daily (
            ticker TEXT,
            final_score REAL,
            run_date TEXT
        );
        INSERT INTO rex_products VALUES
          ('NVDL', 'T-REX 2X LONG NVDA DAILY TARGET ETF', 'Long', 'Filed',
           '2026-05-01', NULL, 'NVDA', 'NVDA'),
          ('MUDL', 'T-REX 2X INVERSE MU DAILY TARGET ETF', 'Inverse', 'Filed',
           '2026-05-10', NULL, 'MU', 'MU'),
          ('TSLS', 'T-REX 2X Inverse TSLA Daily Target ETF', 'Inverse', 'Under Consideration',
           '2026-04-22', NULL, 'TSLA', 'TSLA'),
          ('AAPU', 'T-REX 2X Long AAPL Daily Target ETF', 'Long', 'Listed',
           '2026-01-15', '2026-02-01', 'AAPL', 'AAPL');
        INSERT INTO li_engine_daily VALUES
          ('NVDA', 87.0, '2026-06-08'),
          ('MU',   72.5, '2026-06-08'),
          ('TSLA', 65.0, '2026-06-08');
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_pipeline_includes_both_long_and_inverse(tmp_path, monkeypatch):
    """BUG-19: T-REX 2X Inverse products must appear in the pipeline result."""
    db_path = _make_pipeline_db(tmp_path)

    # Patch the module-level DB constant before importing/using the function
    from screener.li_engine.analysis import trex_combined_v9 as mod
    monkeypatch.setattr(mod, "DB", db_path)

    df = mod.load_trex_2x_long_pipeline()
    fund_names = set(df["fund_name"].str.upper())

    # Long product still included
    assert any("T-REX 2X LONG NVDA" in n for n in fund_names), (
        "Long product NVDA must remain in pipeline"
    )
    # Inverse products now included
    assert any("T-REX 2X INVERSE MU" in n for n in fund_names), (
        "BUG-19 regression: T-REX 2X INVERSE MU must appear in pipeline"
    )
    assert any("T-REX 2X INVERSE TSLA" in n for n in fund_names), (
        "BUG-19 regression: T-REX 2X Inverse TSLA must appear in pipeline"
    )
    # Listed products still excluded
    assert not any("AAPL" in n for n in fund_names), (
        "Listed/Effective products must remain excluded"
    )


def test_pipeline_extracts_underlier_from_inverse_fund_name(tmp_path, monkeypatch):
    """BUG-19: underlier_clean must populate for Inverse fund names too."""
    db_path = _make_pipeline_db(tmp_path)

    from screener.li_engine.analysis import trex_combined_v9 as mod
    monkeypatch.setattr(mod, "DB", db_path)

    df = mod.load_trex_2x_long_pipeline()
    # Find the MU inverse row
    mu_row = df[df["fund_name"].str.upper().str.contains("INVERSE MU")]
    assert not mu_row.empty
    # Underlier should be extracted as MU (from underlier column or fund name regex)
    assert mu_row.iloc[0]["underlier_clean"] == "MU"


# ---------------------------------------------------------------------------
# BUG-20 — foreign_filings rollup: filed beats pending
# ---------------------------------------------------------------------------
def test_foreign_rollup_filed_beats_pending():
    """BUG-20: when REX has both PENDING and FILED rows on the same foreign
    underlier, rex_status must resolve to 'filed' (closer to launch), not
    'pending' (stalled)."""
    from screener.li_engine.analysis import foreign_filings as ff

    # Simulate Samsung: one FILED T-REX product + one PENDING T-REX product
    statuses = pd.DataFrame([
        {"fund_name": "T-REX 2X LONG SAMSUNG DAILY TARGET ETF", "ticker": "SMSU",
         "status": "FILED", "latest_filing_date": "2026-05-10",
         "trust_name": "ETF Opportunities Trust", "foreign_ticker": "005930.KS"},
        {"fund_name": "T-REX 2X INVERSE SAMSUNG DAILY TARGET ETF", "ticker": None,
         "status": "PENDING", "latest_filing_date": "2026-04-01",
         "trust_name": "ETF Opportunities Trust", "foreign_ticker": "005930.KS"},
    ])
    extractions = pd.DataFrame(columns=[
        "series_name", "class_contract_name", "class_symbol",
        "registrant", "form", "filing_date", "cik", "accession_number",
        "foreign_ticker",
    ])

    rolled = ff._rollup(extractions, statuses)
    assert len(rolled) == 1
    row = rolled.iloc[0]
    assert row["foreign_ticker"] == "005930.KS"
    assert row["rex_status"] == "filed", (
        f"BUG-20 regression: expected 'filed' (priority over pending), got '{row['rex_status']}'"
    )


def test_foreign_rollup_active_still_wins():
    """Sanity: 'active' must still beat both 'filed' and 'pending'."""
    from screener.li_engine.analysis import foreign_filings as ff

    statuses = pd.DataFrame([
        {"fund_name": "Active Product", "ticker": "AAA",
         "status": "ACTIVE", "latest_filing_date": "2026-01-01",
         "trust_name": "T-REX", "foreign_ticker": "005930.KS"},
        {"fund_name": "Filed Product", "ticker": "BBB",
         "status": "FILED", "latest_filing_date": "2026-05-10",
         "trust_name": "T-REX", "foreign_ticker": "005930.KS"},
        {"fund_name": "Pending Product", "ticker": "CCC",
         "status": "PENDING", "latest_filing_date": "2026-03-01",
         "trust_name": "T-REX", "foreign_ticker": "005930.KS"},
    ])
    extractions = pd.DataFrame(columns=[
        "series_name", "class_contract_name", "class_symbol",
        "registrant", "form", "filing_date", "cik", "accession_number",
        "foreign_ticker",
    ])
    rolled = ff._rollup(extractions, statuses)
    assert rolled.iloc[0]["rex_status"] == "active"


def test_foreign_rollup_pending_only_still_renders_pending():
    """If only a PENDING row exists, rex_status remains 'pending'."""
    from screener.li_engine.analysis import foreign_filings as ff

    statuses = pd.DataFrame([
        {"fund_name": "Pending Only", "ticker": "PPP",
         "status": "PENDING", "latest_filing_date": "2026-03-01",
         "trust_name": "T-REX", "foreign_ticker": "0700.HK"},
    ])
    extractions = pd.DataFrame(columns=[
        "series_name", "class_contract_name", "class_symbol",
        "registrant", "form", "filing_date", "cik", "accession_number",
        "foreign_ticker",
    ])
    rolled = ff._rollup(extractions, statuses)
    assert rolled.iloc[0]["rex_status"] == "pending"


# ---------------------------------------------------------------------------
# BUG-21 — ipo_watchlist.yaml Cerebras hygiene
# ---------------------------------------------------------------------------
def test_cerebras_not_in_high_profile_pre_ipo():
    """BUG-21: Cerebras IPO'd in 2024. It must not appear in the pre-IPO list."""
    yaml_path = (Path(__file__).resolve().parent.parent
                 / "config" / "ipo_watchlist.yaml")
    assert yaml_path.exists(), f"missing {yaml_path}"
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    hp_companies = [r.get("company", "").lower()
                    for r in (data.get("high_profile_pre_ipo") or [])]
    assert not any("cerebras" in c for c in hp_companies), (
        "BUG-21 regression: Cerebras must not be in high_profile_pre_ipo "
        "(it already IPO'd and 2x products exist)"
    )


def test_cerebras_marked_as_priced_with_explanation():
    """BUG-21: If Cerebras is kept in the YAML at all, it must be in
    recently_priced with desc noting the 2x ETF coverage already exists."""
    yaml_path = (Path(__file__).resolve().parent.parent
                 / "config" / "ipo_watchlist.yaml")
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    rp = data.get("recently_priced") or []
    cerebras_rows = [r for r in rp
                     if "cerebras" in (r.get("company", "").lower())]
    # Either removed entirely OR moved to recently_priced with proper desc
    if cerebras_rows:
        row = cerebras_rows[0]
        desc = (row.get("desc") or "").lower()
        assert "2x" in desc or "complete" in (row.get("expected_ipo_window", "").lower()), (
            "If Cerebras stays in YAML, recently_priced desc must note IPO complete "
            "and/or 2x products live"
        )
