"""I/O contract tests for the three apply scripts.

Creates a minimal test SQLite DB and tiny test CSVs, then exercises
preconditions, postconditions, and dry-run behaviour for:
  - scripts/apply_fund_master.py
  - scripts/apply_underlier_overrides.py
  - scripts/apply_issuer_brands.py

All tests are isolated: each gets its own temp directory so they never
touch the real production DB or CSVs.
"""
from __future__ import annotations

import csv
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db(path: Path) -> None:
    """Create a minimal mkt_master_data table matching the real schema."""
    con = sqlite3.connect(str(path))
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE mkt_master_data (
            id                   INTEGER PRIMARY KEY,
            ticker               VARCHAR(30) NOT NULL,
            fund_name            VARCHAR(300),
            issuer               VARCHAR(200),
            issuer_display       VARCHAR(200),
            asset_class          VARCHAR(30),
            primary_strategy     VARCHAR(40),
            sub_strategy         VARCHAR(80),
            concentration        VARCHAR(10),
            underlier_name       VARCHAR(60),
            underlier_is_wrapper BOOLEAN,
            root_underlier_name  VARCHAR(60),
            wrapper_type         VARCHAR(20),
            mechanism            VARCHAR(20),
            leverage_ratio       FLOAT,
            direction            VARCHAR(10),
            reset_period         VARCHAR(15),
            distribution_freq    VARCHAR(15),
            outcome_period_months INTEGER,
            cap_pct              FLOAT,
            buffer_pct           FLOAT,
            accelerator_multiplier FLOAT,
            barrier_pct          FLOAT,
            region               VARCHAR(30),
            duration_bucket      VARCHAR(20),
            credit_quality       VARCHAR(20),
            tax_structure        VARCHAR(20),
            qualified_dividends  BOOLEAN,
            map_li_underlier     VARCHAR(200),
            map_cc_underlier     VARCHAR(200),
            map_crypto_underlier VARCHAR(200),
            map_defined_category VARCHAR(100),
            map_thematic_category VARCHAR(100),
            updated_at           DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            is_rex               BOOLEAN NOT NULL DEFAULT 0
        )
    """)
    # Seed 5 test tickers
    tickers = [
        ("TESTA", "Test Fund A", "Issuer One"),
        ("TESTB", "Test Fund B", "Issuer One"),
        ("TESTC", "Test Fund C", "Issuer Two"),
        ("TESTD", "Test Fund D", "Issuer Two"),
        ("TESTE", "Test Fund E", "Issuer Three"),
    ]
    cur.executemany(
        "INSERT INTO mkt_master_data (ticker, fund_name, issuer) VALUES (?, ?, ?)",
        tickers,
    )
    con.commit()
    con.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def test_db(tmp_path: Path) -> Path:
    db = tmp_path / "test_etp.db"
    _make_db(db)
    return db


@pytest.fixture()
def fund_master_csv(tmp_path: Path) -> Path:
    """Minimal fund_master.csv with 4 rows (covers 4 of the 5 seeded tickers)."""
    p = tmp_path / "fund_master.csv"
    rows = [
        {
            "ticker": "TESTA", "fund_name": "Test Fund A", "issuer_brand": "Issuer One",
            "asset_class": "Equity", "primary_strategy": "Leveraged", "sub_strategy": "2x Long",
            "concentration": "Single", "underlier_name": "AAPL", "underlier_is_wrapper": "false",
            "root_underlier_name": "AAPL", "wrapper_type": "", "mechanism": "Swap",
            "leverage_ratio": "2.0", "direction": "Long", "reset_period": "Daily",
            "distribution_freq": "Monthly", "outcome_period_months": "", "cap_pct": "",
            "buffer_pct": "", "accelerator_multiplier": "", "barrier_pct": "",
            "region": "US", "duration_bucket": "", "credit_quality": "",
            "tax_structure": "RIC", "qualified_dividends": "false",
            "source": "test", "notes": "",
        },
        {
            "ticker": "TESTB", "fund_name": "Test Fund B", "issuer_brand": "Issuer One",
            "asset_class": "Equity", "primary_strategy": "Inverse", "sub_strategy": "1x Short",
            "concentration": "Single", "underlier_name": "TSLA", "underlier_is_wrapper": "false",
            "root_underlier_name": "TSLA", "wrapper_type": "", "mechanism": "Swap",
            "leverage_ratio": "-1.0", "direction": "Short", "reset_period": "Daily",
            "distribution_freq": "Monthly", "outcome_period_months": "", "cap_pct": "",
            "buffer_pct": "", "accelerator_multiplier": "", "barrier_pct": "",
            "region": "US", "duration_bucket": "", "credit_quality": "",
            "tax_structure": "RIC", "qualified_dividends": "false",
            "source": "test", "notes": "",
        },
        {
            "ticker": "TESTC", "fund_name": "Test Fund C", "issuer_brand": "Issuer Two",
            "asset_class": "Crypto", "primary_strategy": "Spot", "sub_strategy": "BTC Spot",
            "concentration": "Single", "underlier_name": "BTC", "underlier_is_wrapper": "false",
            "root_underlier_name": "BTC", "wrapper_type": "", "mechanism": "Physical",
            "leverage_ratio": "1.0", "direction": "Long", "reset_period": "",
            "distribution_freq": "", "outcome_period_months": "", "cap_pct": "",
            "buffer_pct": "", "accelerator_multiplier": "", "barrier_pct": "",
            "region": "US", "duration_bucket": "", "credit_quality": "",
            "tax_structure": "Grantor", "qualified_dividends": "false",
            "source": "test", "notes": "",
        },
        {
            "ticker": "TESTD", "fund_name": "Test Fund D", "issuer_brand": "Issuer Two",
            "asset_class": "Fixed Income", "primary_strategy": "Duration", "sub_strategy": "Long-Term",
            "concentration": "Broad", "underlier_name": "TLT", "underlier_is_wrapper": "true",
            "root_underlier_name": "US Treasuries", "wrapper_type": "ETF", "mechanism": "Physical",
            "leverage_ratio": "1.0", "direction": "Long", "reset_period": "",
            "distribution_freq": "Monthly", "outcome_period_months": "", "cap_pct": "",
            "buffer_pct": "", "accelerator_multiplier": "", "barrier_pct": "",
            "region": "US", "duration_bucket": "Long", "credit_quality": "Investment Grade",
            "tax_structure": "RIC", "qualified_dividends": "true",
            "source": "test", "notes": "",
        },
    ]
    with p.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return p


@pytest.fixture()
def underlier_overrides_csv(tmp_path: Path) -> Path:
    """Minimal underlier_overrides.csv with 3 rows."""
    p = tmp_path / "underlier_overrides.csv"
    rows = [
        {
            "ticker": "TESTA", "column_name": "map_li_underlier",
            "corrected_value": "Apple Inc.", "source": "test", "notes": "", "fixed_at": "",
        },
        {
            "ticker": "TESTB", "column_name": "map_li_underlier",
            "corrected_value": "Tesla Inc.", "source": "test", "notes": "", "fixed_at": "",
        },
        {
            "ticker": "TESTC", "column_name": "map_crypto_underlier",
            "corrected_value": "Bitcoin", "source": "test", "notes": "", "fixed_at": "",
        },
    ]
    with p.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return p


@pytest.fixture()
def issuer_brands_csv(tmp_path: Path) -> Path:
    """Minimal issuer_brand_overrides.csv with 4 rows."""
    p = tmp_path / "issuer_brand_overrides.csv"
    rows = [
        {"ticker": "TESTA", "issuer_display": "Issuer One Capital", "source": "test", "notes": ""},
        {"ticker": "TESTB", "issuer_display": "Issuer One Capital", "source": "test", "notes": ""},
        {"ticker": "TESTC", "issuer_display": "Issuer Two Asset Mgmt", "source": "test", "notes": ""},
        {"ticker": "TESTD", "issuer_display": "Issuer Two Asset Mgmt", "source": "test", "notes": ""},
    ]
    with p.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return p


# ---------------------------------------------------------------------------
# Utility: run a script via subprocess
# ---------------------------------------------------------------------------

def _run_script(script: str, args: list[str]) -> subprocess.CompletedProcess:
    script_path = Path(__file__).resolve().parent.parent / "scripts" / script
    result = subprocess.run(
        [sys.executable, str(script_path)] + args,
        capture_output=True,
        text=True,
    )
    return result


# ---------------------------------------------------------------------------
# apply_fund_master tests
# ---------------------------------------------------------------------------

class TestApplyFundMasterPreconditions:
    def test_missing_csv_exits_nonzero(self, test_db: Path, tmp_path: Path):
        missing = tmp_path / "nonexistent.csv"
        result = _run_script("apply_fund_master.py", [
            "--csv-path", str(missing),
            "--db-path", str(test_db),
        ])
        assert result.returncode != 0

    def test_missing_db_exits_nonzero(self, fund_master_csv: Path, tmp_path: Path):
        missing_db = tmp_path / "nonexistent.db"
        result = _run_script("apply_fund_master.py", [
            "--csv-path", str(fund_master_csv),
            "--db-path", str(missing_db),
        ])
        assert result.returncode != 0

    def test_empty_csv_exits_nonzero(self, test_db: Path, tmp_path: Path):
        empty_csv = tmp_path / "empty.csv"
        # Write only header, no data rows
        with empty_csv.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["ticker", "asset_class", "primary_strategy",
                                                    "sub_strategy"])
            writer.writeheader()
        result = _run_script("apply_fund_master.py", [
            "--csv-path", str(empty_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode != 0

    def test_missing_column_exits_nonzero(self, test_db: Path, tmp_path: Path):
        bad_csv = tmp_path / "bad_schema.csv"
        with bad_csv.open("w", encoding="utf-8", newline="") as f:
            # Missing 'ticker' and most required columns
            f.write("some_random_col\nvalue1\n")
        result = _run_script("apply_fund_master.py", [
            "--csv-path", str(bad_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode != 0


class TestApplyFundMasterDryRun:
    def test_dry_run_exits_zero(self, fund_master_csv: Path, test_db: Path):
        result = _run_script("apply_fund_master.py", [
            "--dry-run",
            "--csv-path", str(fund_master_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode == 0, result.stderr

    def test_dry_run_no_writes(self, fund_master_csv: Path, test_db: Path):
        result = _run_script("apply_fund_master.py", [
            "--dry-run",
            "--csv-path", str(fund_master_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode == 0

        # DB should have no asset_class values written
        con = sqlite3.connect(str(test_db))
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM mkt_master_data WHERE asset_class IS NOT NULL")
        count = cur.fetchone()[0]
        con.close()
        assert count == 0, f"Dry-run wrote {count} rows — expected 0"

    def test_dry_run_mentions_dry_run(self, fund_master_csv: Path, test_db: Path):
        result = _run_script("apply_fund_master.py", [
            "--dry-run",
            "--csv-path", str(fund_master_csv),
            "--db-path", str(test_db),
        ])
        assert "DRY-RUN" in result.stdout

    def test_dry_run_preconditions_pass_message(self, fund_master_csv: Path, test_db: Path):
        result = _run_script("apply_fund_master.py", [
            "--dry-run",
            "--csv-path", str(fund_master_csv),
            "--db-path", str(test_db),
        ])
        assert "Preconditions OK" in result.stdout


class TestApplyFundMasterActualRun:
    def test_updates_expected_rows(self, fund_master_csv: Path, test_db: Path):
        result = _run_script("apply_fund_master.py", [
            "--csv-path", str(fund_master_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode == 0, result.stderr

        con = sqlite3.connect(str(test_db))
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM mkt_master_data WHERE asset_class IS NOT NULL")
        count = cur.fetchone()[0]
        con.close()
        assert count == 4  # 4 rows in CSV, 1 seeded ticker not in CSV

    def test_spot_check_values(self, fund_master_csv: Path, test_db: Path):
        _run_script("apply_fund_master.py", [
            "--csv-path", str(fund_master_csv),
            "--db-path", str(test_db),
        ])
        con = sqlite3.connect(str(test_db))
        cur = con.cursor()
        cur.execute(
            "SELECT asset_class, primary_strategy FROM mkt_master_data WHERE ticker = 'TESTA'"
        )
        row = cur.fetchone()
        con.close()
        assert row is not None
        assert row[0] == "Equity"
        assert row[1] == "Leveraged"

    def test_postconditions_ok_in_output(self, fund_master_csv: Path, test_db: Path):
        result = _run_script("apply_fund_master.py", [
            "--csv-path", str(fund_master_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode == 0
        assert "Postconditions OK" in result.stdout


# ---------------------------------------------------------------------------
# apply_underlier_overrides tests
# ---------------------------------------------------------------------------

class TestApplyUnderlierPreconditions:
    def test_missing_csv_exits_nonzero(self, test_db: Path, tmp_path: Path):
        missing = tmp_path / "nonexistent.csv"
        result = _run_script("apply_underlier_overrides.py", [
            "--csv-path", str(missing),
            "--db-path", str(test_db),
        ])
        assert result.returncode != 0

    def test_missing_db_exits_nonzero(self, underlier_overrides_csv: Path, tmp_path: Path):
        missing_db = tmp_path / "nonexistent.db"
        result = _run_script("apply_underlier_overrides.py", [
            "--csv-path", str(underlier_overrides_csv),
            "--db-path", str(missing_db),
        ])
        assert result.returncode != 0

    def test_empty_csv_exits_nonzero(self, test_db: Path, tmp_path: Path):
        empty_csv = tmp_path / "empty_overrides.csv"
        with empty_csv.open("w", encoding="utf-8", newline="") as f:
            f.write("ticker,column_name,corrected_value\n")  # header only
        result = _run_script("apply_underlier_overrides.py", [
            "--csv-path", str(empty_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode != 0


class TestApplyUnderlierDryRun:
    def test_dry_run_exits_zero(self, underlier_overrides_csv: Path, test_db: Path):
        result = _run_script("apply_underlier_overrides.py", [
            "--dry-run",
            "--csv-path", str(underlier_overrides_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode == 0, result.stderr

    def test_dry_run_no_writes(self, underlier_overrides_csv: Path, test_db: Path):
        result = _run_script("apply_underlier_overrides.py", [
            "--dry-run",
            "--csv-path", str(underlier_overrides_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode == 0

        con = sqlite3.connect(str(test_db))
        cur = con.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM mkt_master_data WHERE map_li_underlier IS NOT NULL"
        )
        count = cur.fetchone()[0]
        con.close()
        assert count == 0, f"Dry-run wrote {count} map_li_underlier rows — expected 0"

    def test_dry_run_mentions_dry_run(self, underlier_overrides_csv: Path, test_db: Path):
        result = _run_script("apply_underlier_overrides.py", [
            "--dry-run",
            "--csv-path", str(underlier_overrides_csv),
            "--db-path", str(test_db),
        ])
        assert "DRY-RUN" in result.stdout

    def test_dry_run_preconditions_pass_message(self, underlier_overrides_csv: Path, test_db: Path):
        result = _run_script("apply_underlier_overrides.py", [
            "--dry-run",
            "--csv-path", str(underlier_overrides_csv),
            "--db-path", str(test_db),
        ])
        assert "Preconditions OK" in result.stdout


class TestApplyUnderlierActualRun:
    def test_updates_expected_rows(self, underlier_overrides_csv: Path, test_db: Path):
        result = _run_script("apply_underlier_overrides.py", [
            "--csv-path", str(underlier_overrides_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode == 0, result.stderr

    def test_spot_check_values(self, underlier_overrides_csv: Path, test_db: Path):
        _run_script("apply_underlier_overrides.py", [
            "--csv-path", str(underlier_overrides_csv),
            "--db-path", str(test_db),
        ])
        con = sqlite3.connect(str(test_db))
        cur = con.cursor()
        cur.execute("SELECT map_li_underlier FROM mkt_master_data WHERE ticker = 'TESTA'")
        row = cur.fetchone()
        cur.execute("SELECT map_crypto_underlier FROM mkt_master_data WHERE ticker = 'TESTC'")
        crypto_row = cur.fetchone()
        con.close()
        assert row[0] == "Apple Inc."
        assert crypto_row[0] == "Bitcoin"

    def test_postconditions_ok_in_output(self, underlier_overrides_csv: Path, test_db: Path):
        result = _run_script("apply_underlier_overrides.py", [
            "--csv-path", str(underlier_overrides_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode == 0
        assert "Postconditions OK" in result.stdout

    def test_idempotent_second_run(self, underlier_overrides_csv: Path, test_db: Path):
        """Second run should produce 0 updates (all no-ops)."""
        _run_script("apply_underlier_overrides.py", [
            "--csv-path", str(underlier_overrides_csv),
            "--db-path", str(test_db),
        ])
        result2 = _run_script("apply_underlier_overrides.py", [
            "--csv-path", str(underlier_overrides_csv),
            "--db-path", str(test_db),
        ])
        assert result2.returncode == 0
        assert "No-op" in result2.stdout or "noop" in result2.stdout.lower()


# ---------------------------------------------------------------------------
# apply_issuer_brands tests
# ---------------------------------------------------------------------------

class TestApplyIssuerBrandsPreconditions:
    def test_missing_csv_exits_nonzero(self, test_db: Path, tmp_path: Path):
        missing = tmp_path / "nonexistent.csv"
        result = _run_script("apply_issuer_brands.py", [
            "--csv-path", str(missing),
            "--db-path", str(test_db),
        ])
        assert result.returncode != 0

    def test_missing_db_exits_nonzero(self, issuer_brands_csv: Path, tmp_path: Path):
        missing_db = tmp_path / "nonexistent.db"
        result = _run_script("apply_issuer_brands.py", [
            "--csv-path", str(issuer_brands_csv),
            "--db-path", str(missing_db),
        ])
        assert result.returncode != 0

    def test_empty_csv_exits_nonzero(self, test_db: Path, tmp_path: Path):
        empty_csv = tmp_path / "empty_brands.csv"
        with empty_csv.open("w", encoding="utf-8", newline="") as f:
            f.write("ticker,issuer_display,source,notes\n")
        result = _run_script("apply_issuer_brands.py", [
            "--csv-path", str(empty_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode != 0

    def test_missing_column_exits_nonzero(self, test_db: Path, tmp_path: Path):
        bad_csv = tmp_path / "missing_col.csv"
        with bad_csv.open("w", encoding="utf-8", newline="") as f:
            f.write("ticker\nTESTA\n")  # missing issuer_display
        result = _run_script("apply_issuer_brands.py", [
            "--csv-path", str(bad_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode != 0


class TestApplyIssuerBrandsDryRun:
    def test_dry_run_exits_zero(self, issuer_brands_csv: Path, test_db: Path):
        result = _run_script("apply_issuer_brands.py", [
            "--dry-run",
            "--csv-path", str(issuer_brands_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode == 0, result.stderr

    def test_dry_run_no_writes(self, issuer_brands_csv: Path, test_db: Path):
        result = _run_script("apply_issuer_brands.py", [
            "--dry-run",
            "--csv-path", str(issuer_brands_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode == 0

        con = sqlite3.connect(str(test_db))
        cur = con.cursor()
        cur.execute(
            "SELECT COUNT(*) FROM mkt_master_data WHERE issuer_display IS NOT NULL"
        )
        count = cur.fetchone()[0]
        con.close()
        assert count == 0, f"Dry-run wrote {count} issuer_display rows — expected 0"

    def test_dry_run_mentions_dry_run(self, issuer_brands_csv: Path, test_db: Path):
        result = _run_script("apply_issuer_brands.py", [
            "--dry-run",
            "--csv-path", str(issuer_brands_csv),
            "--db-path", str(test_db),
        ])
        assert "DRY-RUN" in result.stdout

    def test_dry_run_preconditions_pass_message(self, issuer_brands_csv: Path, test_db: Path):
        result = _run_script("apply_issuer_brands.py", [
            "--dry-run",
            "--csv-path", str(issuer_brands_csv),
            "--db-path", str(test_db),
        ])
        assert "Preconditions OK" in result.stdout


class TestApplyIssuerBrandsActualRun:
    def test_updates_expected_rows(self, issuer_brands_csv: Path, test_db: Path):
        result = _run_script("apply_issuer_brands.py", [
            "--csv-path", str(issuer_brands_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode == 0, result.stderr

        con = sqlite3.connect(str(test_db))
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM mkt_master_data WHERE issuer_display IS NOT NULL")
        count = cur.fetchone()[0]
        con.close()
        assert count == 4  # 4 rows in CSV

    def test_spot_check_values(self, issuer_brands_csv: Path, test_db: Path):
        _run_script("apply_issuer_brands.py", [
            "--csv-path", str(issuer_brands_csv),
            "--db-path", str(test_db),
        ])
        con = sqlite3.connect(str(test_db))
        cur = con.cursor()
        cur.execute("SELECT issuer_display FROM mkt_master_data WHERE ticker = 'TESTA'")
        row_a = cur.fetchone()
        cur.execute("SELECT issuer_display FROM mkt_master_data WHERE ticker = 'TESTC'")
        row_c = cur.fetchone()
        cur.execute("SELECT issuer_display FROM mkt_master_data WHERE ticker = 'TESTE'")
        row_e = cur.fetchone()
        con.close()
        assert row_a[0] == "Issuer One Capital"
        assert row_c[0] == "Issuer Two Asset Mgmt"
        assert row_e[0] is None  # TESTE not in CSV — untouched

    def test_postconditions_ok_in_output(self, issuer_brands_csv: Path, test_db: Path):
        result = _run_script("apply_issuer_brands.py", [
            "--csv-path", str(issuer_brands_csv),
            "--db-path", str(test_db),
        ])
        assert result.returncode == 0
        assert "Postconditions OK" in result.stdout

    def test_idempotent_second_run(self, issuer_brands_csv: Path, test_db: Path):
        """Second run should produce 0 updates (all no-ops)."""
        _run_script("apply_issuer_brands.py", [
            "--csv-path", str(issuer_brands_csv),
            "--db-path", str(test_db),
        ])
        result2 = _run_script("apply_issuer_brands.py", [
            "--csv-path", str(issuer_brands_csv),
            "--db-path", str(test_db),
        ])
        assert result2.returncode == 0
        assert "No-ops" in result2.stdout
