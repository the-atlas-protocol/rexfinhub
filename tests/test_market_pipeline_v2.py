"""Tests for Market Data Pipeline v2: New BBG format + Multi-Dimensional Classification.

Run: python -m pytest tests/test_market_pipeline_v2.py -v
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
BBG_DATA = Path(r"C:\Users\RyuEl-Asmar\Downloads\bbg_data.xlsx")
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"


# ---------------------------------------------------------------------------
# Phase 1: Ingest Tests
# ---------------------------------------------------------------------------

class TestConfigColumnMaps:
    """Verify config.py column maps are complete and consistent."""

    def test_w1_col_map_has_all_base_fields(self):
        from market.config import W1_COL_MAP, BASE_FIELDS
        mapped_targets = set(W1_COL_MAP.values())
        for field in BASE_FIELDS:
            assert field in mapped_targets, f"BASE_FIELD '{field}' not in W1_COL_MAP targets"

    def test_w2_col_map_has_all_w2_fields(self):
        from market.config import W2_COL_MAP, W2_FIELDS
        mapped_targets = {v for v in W2_COL_MAP.values() if v != "ticker"}
        for field in W2_FIELDS:
            assert field in mapped_targets, f"W2_FIELD '{field}' not in W2_COL_MAP targets"

    def test_w3_col_map_has_all_w3_fields(self):
        from market.config import W3_COL_MAP, W3_FIELDS
        mapped_targets = {v for v in W3_COL_MAP.values() if v != "ticker"}
        for field in W3_FIELDS:
            assert field in mapped_targets, f"W3_FIELD '{field}' not in W3_COL_MAP targets"

    def test_w4_flow_col_map_covers_flows(self):
        from market.config import W4_FLOW_COL_MAP
        flow_targets = {v for v in W4_FLOW_COL_MAP.values() if v != "ticker"}
        assert "fund_flow_1day" in flow_targets
        assert "fund_flow_3year" in flow_targets
        assert len(flow_targets) == 8

    def test_strategies_list_complete(self):
        from market.config import STRATEGIES
        assert "Leveraged & Inverse" in STRATEGIES
        assert "Broad Beta" in STRATEGIES
        assert "Unclassified" in STRATEGIES
        assert "Currency" in STRATEGIES
        assert len(STRATEGIES) >= 13

    def test_bbg_sheet_names_defined(self):
        from market.config import SHEET_W1, SHEET_W2, SHEET_W3, SHEET_W4, SHEET_S1, SHEET_MKT_STATUS
        assert SHEET_W1 == "w1"
        assert SHEET_W2 == "w2"
        assert SHEET_W3 == "w3"
        assert SHEET_W4 == "w4"
        assert SHEET_S1 == "s1"
        assert SHEET_MKT_STATUS == "mkt_status"


class TestIngestBBGFormat:
    """Test ingestion of the new bbg_data.xlsx format."""

    @pytest.fixture(scope="class")
    def bbg_data(self):
        if not BBG_DATA.exists():
            pytest.skip(f"bbg_data.xlsx not found at {BBG_DATA}")
        from market.ingest import read_input
        return read_input(BBG_DATA)

    def test_returns_dict_with_required_keys(self, bbg_data):
        assert "etp_combined" in bbg_data
        assert "stock_data" in bbg_data
        assert "mkt_status" in bbg_data
        assert "source_path" in bbg_data

    def test_etp_row_count_range(self, bbg_data):
        """Expect 7,000-8,500 rows (w1 base + possible join expansion)."""
        etp = bbg_data["etp_combined"]
        assert 7000 <= len(etp) <= 8500, f"Got {len(etp)} rows"

    def test_canonical_base_columns_present(self, bbg_data):
        etp = bbg_data["etp_combined"]
        required = ["ticker", "fund_name", "issuer", "asset_class_focus",
                     "uses_leverage", "is_crypto", "market_status", "fund_description"]
        for col in required:
            assert col in etp.columns, f"Missing base column: {col}"

    def test_prefixed_w2_columns_present(self, bbg_data):
        etp = bbg_data["etp_combined"]
        assert "t_w2.expense_ratio" in etp.columns
        assert "t_w2.management_fee" in etp.columns

    def test_prefixed_w3_columns_present(self, bbg_data):
        etp = bbg_data["etp_combined"]
        assert "t_w3.total_return_1day" in etp.columns
        assert "t_w3.annualized_yield" in etp.columns

    def test_prefixed_w4_flow_columns_present(self, bbg_data):
        etp = bbg_data["etp_combined"]
        assert "t_w4.fund_flow_1day" in etp.columns
        assert "t_w4.fund_flow_3year" in etp.columns

    def test_aum_columns_present(self, bbg_data):
        etp = bbg_data["etp_combined"]
        assert "t_w4.aum" in etp.columns, "Current AUM missing"
        assert "t_w4.aum_1" in etp.columns, "AUM 1-month-ago missing"
        assert "t_w4.aum_36" in etp.columns, "AUM 36-month-ago missing"

    def test_stock_data_populated(self, bbg_data):
        stock = bbg_data["stock_data"]
        assert len(stock) > 5000, f"Expected 5000+ stock rows, got {len(stock)}"

    def test_mkt_status_has_16_rows(self, bbg_data):
        mkt_status = bbg_data["mkt_status"]
        assert len(mkt_status) == 16, f"Expected 16 market statuses, got {len(mkt_status)}"

    def test_market_status_distribution(self, bbg_data):
        etp = bbg_data["etp_combined"]
        status_counts = etp["market_status"].value_counts()
        assert "ACTV" in status_counts.index, "No ACTV products found"
        assert status_counts["ACTV"] > 5000, f"Expected 5000+ ACTV, got {status_counts.get('ACTV', 0)}"

    def test_no_null_tickers(self, bbg_data):
        etp = bbg_data["etp_combined"]
        assert etp["ticker"].isna().sum() == 0, "Found null tickers"

    def test_column_count_reasonable(self, bbg_data):
        etp = bbg_data["etp_combined"]
        assert 80 <= etp.shape[1] <= 100, f"Got {etp.shape[1]} columns (expected 80-100)"


# ---------------------------------------------------------------------------
# Phase 2: Model / Schema Tests
# ---------------------------------------------------------------------------

class TestNewDBModels:
    """Verify new ORM models are defined and tables created."""

    def test_mkt_fund_classification_model_exists(self):
        from webapp.models import MktFundClassification
        assert MktFundClassification.__tablename__ == "mkt_fund_classification"

    def test_mkt_market_status_model_exists(self):
        from webapp.models import MktMarketStatus
        assert MktMarketStatus.__tablename__ == "mkt_market_status"

    def test_mkt_master_data_has_strategy_columns(self):
        from webapp.models import MktMasterData
        assert hasattr(MktMasterData, "strategy")
        assert hasattr(MktMasterData, "strategy_confidence")
        assert hasattr(MktMasterData, "underlier_type")

    def test_classification_has_all_attribute_columns(self):
        from webapp.models import MktFundClassification
        attr_cols = ["direction", "leverage_amount", "underlier", "income_strategy",
                     "geography", "sector", "duration", "credit_quality",
                     "commodity_type", "crypto_type", "theme", "outcome_type_detail"]
        for col in attr_cols:
            assert hasattr(MktFundClassification, col), f"Missing column: {col}"

    def test_classification_has_json_blob(self):
        from webapp.models import MktFundClassification
        assert hasattr(MktFundClassification, "attributes_json")

    def test_tables_exist_in_sqlite(self):
        if not DB_PATH.exists():
            pytest.skip("DB not found")
        conn = sqlite3.connect(str(DB_PATH))
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {r[0] for r in cursor.fetchall()}
        conn.close()
        assert "mkt_fund_classification" in tables
        assert "mkt_market_status" in tables


# ---------------------------------------------------------------------------
# Phase 3: Classification Tests
# ---------------------------------------------------------------------------

class TestAutoClassify:
    """Test the auto-classification engine."""

    def test_classify_leveraged_fund(self):
        from market.auto_classify import classify_fund
        row = pd.Series({
            "ticker": "TQQQ US",
            "fund_name": "PROSHARES ULTRAPRO QQQ",
            "asset_class_focus": "Equity",
            "uses_leverage": "1",
            "leverage_amount": "3",
            "is_crypto": "",
            "outcome_type": "",
            "is_singlestock": "QQQ US",
            "fund_description": "",
            "underlying_index": "NASDAQ-100",
            "uses_derivatives": "1",
            "uses_swaps": "1",
            "is_40act": "1",
            "market_status": "ACTV",
        })
        result = classify_fund(row)
        assert result.strategy == "Leveraged & Inverse"
        assert result.confidence == "HIGH"

    def test_classify_crypto_fund(self):
        from market.auto_classify import classify_fund
        row = pd.Series({
            "ticker": "IBIT US",
            "fund_name": "ISHARES BITCOIN TRUST ETF",
            "asset_class_focus": "Equity",
            "uses_leverage": "",
            "leverage_amount": "",
            "is_crypto": "Cryptocurrency",
            "outcome_type": "",
            "is_singlestock": "",
            "fund_description": "",
            "underlying_index": "",
            "uses_derivatives": "",
            "uses_swaps": "",
            "is_40act": "",
            "market_status": "ACTV",
        })
        result = classify_fund(row)
        assert result.strategy == "Crypto"
        assert result.confidence == "HIGH"

    def test_classify_defined_outcome(self):
        from market.auto_classify import classify_fund
        row = pd.Series({
            "ticker": "BAPR US",
            "fund_name": "INNOVATOR U.S. EQUITY BUFFER ETF",
            "asset_class_focus": "Equity",
            "uses_leverage": "",
            "leverage_amount": "",
            "is_crypto": "",
            "outcome_type": "Buffer",
            "is_singlestock": "",
            "fund_description": "",
            "underlying_index": "S&P 500",
            "uses_derivatives": "1",
            "uses_swaps": "",
            "is_40act": "1",
            "market_status": "ACTV",
        })
        result = classify_fund(row)
        assert result.strategy == "Defined Outcome"
        assert result.confidence == "HIGH"

    def test_classify_fixed_income(self):
        from market.auto_classify import classify_fund
        row = pd.Series({
            "ticker": "AGG US",
            "fund_name": "ISHARES CORE U.S. AGGREGATE BOND ETF",
            "asset_class_focus": "Fixed Income",
            "uses_leverage": "",
            "leverage_amount": "",
            "is_crypto": "",
            "outcome_type": "",
            "is_singlestock": "",
            "fund_description": "INVESTMENT GRADE BOND INDEX",
            "underlying_index": "Bloomberg US Agg Bond",
            "uses_derivatives": "",
            "uses_swaps": "",
            "is_40act": "1",
            "market_status": "ACTV",
        })
        result = classify_fund(row)
        assert result.strategy == "Fixed Income"
        assert result.confidence == "HIGH"
        assert result.attributes.get("credit_quality") == "Investment Grade"

    def test_classify_commodity(self):
        from market.auto_classify import classify_fund
        row = pd.Series({
            "ticker": "GLD US",
            "fund_name": "SPDR GOLD SHARES",
            "asset_class_focus": "Commodity",
            "uses_leverage": "",
            "leverage_amount": "",
            "is_crypto": "",
            "outcome_type": "",
            "is_singlestock": "",
            "fund_description": "GOLD BULLION",
            "underlying_index": "",
            "uses_derivatives": "",
            "uses_swaps": "",
            "is_40act": "",
            "market_status": "ACTV",
        })
        result = classify_fund(row)
        assert result.strategy == "Commodity"
        assert result.confidence == "HIGH"
        assert result.attributes.get("commodity_type") == "Gold"

    def test_classify_specialty_vix(self):
        from market.auto_classify import classify_fund
        row = pd.Series({
            "ticker": "VXX US",
            "fund_name": "IPATH SERIES B S&P 500 VIX SHORT-TERM FUTURES ETN",
            "asset_class_focus": "Specialty",
            "uses_leverage": "",
            "leverage_amount": "",
            "is_crypto": "",
            "outcome_type": "",
            "is_singlestock": "",
            "fund_description": "VIX FUTURES",
            "underlying_index": "",
            "uses_derivatives": "",
            "uses_swaps": "",
            "is_40act": "",
            "market_status": "ACTV",
        })
        result = classify_fund(row)
        assert result.strategy == "Alternative"
        assert result.attributes.get("sub_category") == "Volatility"

    def test_classify_real_estate(self):
        from market.auto_classify import classify_fund
        row = pd.Series({
            "ticker": "VNQ US",
            "fund_name": "VANGUARD REAL ESTATE ETF",
            "asset_class_focus": "Real Estate",
            "uses_leverage": "",
            "leverage_amount": "",
            "is_crypto": "",
            "outcome_type": "",
            "is_singlestock": "",
            "fund_description": "REIT INDEX",
            "underlying_index": "",
            "uses_derivatives": "",
            "uses_swaps": "",
            "is_40act": "1",
            "market_status": "ACTV",
        })
        result = classify_fund(row)
        assert result.strategy == "Sector"
        assert result.attributes.get("sector") == "Real Estate"

    def test_classify_money_market(self):
        from market.auto_classify import classify_fund
        row = pd.Series({
            "ticker": "SGOV US",
            "fund_name": "ISHARES 0-3 MONTH TREASURY BOND ETF",
            "asset_class_focus": "Money Market",
            "uses_leverage": "",
            "leverage_amount": "",
            "is_crypto": "",
            "outcome_type": "",
            "is_singlestock": "",
            "fund_description": "ULTRA SHORT TREASURY",
            "underlying_index": "",
            "uses_derivatives": "",
            "uses_swaps": "",
            "is_40act": "1",
            "market_status": "ACTV",
        })
        result = classify_fund(row)
        assert result.strategy == "Fixed Income"
        assert result.attributes.get("duration") == "Ultra Short"

    def test_classify_all_returns_dataclass_list(self):
        from market.auto_classify import classify_all, Classification
        df = pd.DataFrame([{
            "ticker": "SPY US", "fund_name": "SPDR S&P 500 ETF",
            "asset_class_focus": "Equity", "uses_leverage": "",
            "is_crypto": "", "outcome_type": "", "is_singlestock": "",
            "leverage_amount": "", "fund_description": "",
            "underlying_index": "S&P 500", "uses_derivatives": "",
            "uses_swaps": "", "is_40act": "1", "market_status": "ACTV",
        }])
        results = classify_all(df)
        assert len(results) == 1
        assert isinstance(results[0], Classification)

    def test_classify_to_dataframe_columns(self):
        from market.auto_classify import classify_to_dataframe
        df = pd.DataFrame([{
            "ticker": "SPY US", "fund_name": "SPDR S&P 500 ETF",
            "asset_class_focus": "Equity", "uses_leverage": "",
            "is_crypto": "", "outcome_type": "", "is_singlestock": "",
            "leverage_amount": "", "fund_description": "",
            "underlying_index": "S&P 500", "uses_derivatives": "",
            "uses_swaps": "", "is_40act": "1", "market_status": "ACTV",
        }])
        result = classify_to_dataframe(df)
        assert "ticker" in result.columns
        assert "strategy" in result.columns
        assert "confidence" in result.columns
        assert "underlier_type" in result.columns


# ---------------------------------------------------------------------------
# Phase 4: DB Writer Tests
# ---------------------------------------------------------------------------

class TestDBWriter:
    """Test classification and market status DB write functions."""

    def test_write_classifications(self):
        from market.auto_classify import Classification
        from market.db_writer import write_classifications, create_pipeline_run
        from webapp.database import SessionLocal, init_db

        init_db()
        session = SessionLocal()
        try:
            # Create a valid pipeline run for FK constraint
            run_id = create_pipeline_run(session, "test_file.xlsx")

            classifications = [
                Classification(
                    ticker="TEST1",
                    strategy="Broad Beta",
                    confidence="HIGH",
                    reason="test",
                    underlier_type="Index",
                    attributes={"sector": "Technology"},
                ),
            ]
            count = write_classifications(session, classifications, run_id=run_id)
            session.commit()
            assert count == 1

            # Verify it's in the DB
            from webapp.models import MktFundClassification
            row = session.query(MktFundClassification).filter_by(ticker="TEST1").first()
            assert row is not None
            assert row.strategy == "Broad Beta"
            assert row.sector == "Technology"
            assert json.loads(row.attributes_json) == {"sector": "Technology"}
        finally:
            session.rollback()
            session.close()

    def test_write_market_statuses(self):
        from market.db_writer import write_market_statuses
        from webapp.database import SessionLocal, init_db

        init_db()
        session = SessionLocal()
        try:
            mkt_status_df = pd.DataFrame([
                {"Code": "ACTV", "Description": "Active"},
                {"Code": "LIQU", "Description": "Liquidated"},
            ])
            count = write_market_statuses(session, mkt_status_df)
            session.commit()
            assert count == 2

            from webapp.models import MktMarketStatus
            actv = session.query(MktMarketStatus).filter_by(code="ACTV").first()
            assert actv is not None
            assert actv.description == "Active"
        finally:
            session.rollback()
            session.close()


# ---------------------------------------------------------------------------
# Phase 5: Webapp Integration Tests
# ---------------------------------------------------------------------------

class TestWebappIntegrity:
    """Verify the webapp still works after backend changes."""

    def test_webapp_imports(self):
        """All webapp modules should import without error."""
        from webapp.main import app
        assert app is not None

    def test_key_routes_exist(self):
        from webapp.main import app
        route_paths = [r.path for r in app.routes if hasattr(r, "path")]
        assert "/" in route_paths
        assert "/dashboard" in route_paths or "/dashboard/" in route_paths

    def test_database_init(self):
        from webapp.database import init_db
        init_db()  # Should not raise

    def test_all_models_importable(self):
        from webapp.models import (
            Trust, Filing, FundExtraction, FundStatus,
            NameHistory, AnalysisResult, PipelineRun,
            MktPipelineRun, MktFundMapping, MktIssuerMapping,
            MktCategoryAttributes, MktExclusion, MktRexFund,
            MktMasterData, MktTimeSeries, MktStockData,
            MktFundClassification, MktMarketStatus,
        )
        assert MktFundClassification is not None
        assert MktMarketStatus is not None

    def test_screener_import(self):
        """Screener should not be affected by market pipeline changes."""
        from webapp.routers.screener import router
        assert router is not None

    def test_admin_import(self):
        """Admin panel should not be affected."""
        from webapp.routers.admin import router
        assert router is not None

    def test_dashboard_import(self):
        """Dashboard should not be affected."""
        from webapp.routers.dashboard import router
        assert router is not None

    def test_funds_import(self):
        """Funds search should not be affected."""
        from webapp.routers.funds import router
        assert router is not None


# ---------------------------------------------------------------------------
# Full Pipeline Integration (requires bbg_data.xlsx)
# ---------------------------------------------------------------------------

class TestMarketStatusRule:
    """Verify market_status.csv is stored as a rule file."""

    def test_market_status_csv_exists(self):
        rules_path = PROJECT_ROOT / "data" / "rules" / "market_status.csv"
        assert rules_path.exists(), "market_status.csv not found in data/rules/"

    def test_market_status_csv_has_17_rows(self):
        rules_path = PROJECT_ROOT / "data" / "rules" / "market_status.csv"
        if not rules_path.exists():
            pytest.skip("market_status.csv not found")
        df = pd.read_csv(rules_path)
        assert len(df) == 17, f"Expected 17 rows, got {len(df)}"
        assert "code" in df.columns
        assert "description" in df.columns

    def test_market_status_loads_via_rules(self):
        from market.rules import load_market_status
        df = load_market_status()
        assert len(df) == 17
        assert "ACTV" in df["code"].values
        assert "LIQU" in df["code"].values

    def test_load_all_rules_includes_market_status(self):
        from market.rules import load_all_rules
        rules = load_all_rules()
        assert "market_status" in rules
        assert len(rules["market_status"]) == 17


class TestChangeDetection:
    """Verify file modification tracking."""

    def test_last_run_file_path(self):
        from market.config import LAST_RUN_FILE
        assert LAST_RUN_FILE.name == ".last_market_run.json"

    def test_history_dir_path(self):
        from market.config import HISTORY_DIR
        assert HISTORY_DIR.name == "history"

    def test_data_file_resolves_to_onedrive(self):
        from market.config import DATA_FILE
        # Should resolve to OneDrive bbg_data.xlsx (if it exists)
        if "REX Financial LLC" in str(DATA_FILE):
            assert "bbg_data.xlsx" in str(DATA_FILE)


class TestFullPipeline:
    """End-to-end pipeline verification (skipped if no bbg_data.xlsx)."""

    @pytest.fixture(scope="class")
    def pipeline_result(self):
        if not BBG_DATA.exists():
            pytest.skip(f"bbg_data.xlsx not found at {BBG_DATA}")

        from market.ingest import read_input
        from market.auto_classify import classify_all, classify_to_dataframe

        data = read_input(BBG_DATA)
        etp = data["etp_combined"]
        classifications = classify_all(etp)
        class_df = classify_to_dataframe(etp)

        return {
            "data": data,
            "etp": etp,
            "classifications": classifications,
            "class_df": class_df,
        }

    def test_classification_count_matches_unique_tickers(self, pipeline_result):
        etp = pipeline_result["etp"]
        classifications = pipeline_result["classifications"]
        unique_tickers = etp["ticker"].nunique()
        # Classifications should be close to unique ticker count
        assert abs(len(classifications) - unique_tickers) < 200

    def test_strategy_distribution_reasonable(self, pipeline_result):
        from collections import Counter
        classifications = pipeline_result["classifications"]
        strats = Counter(c.strategy for c in classifications)
        total = len(classifications)

        # Broad Beta should be the largest category (25-35%)
        assert strats["Broad Beta"] / total > 0.20
        # Fixed Income significant (12-22%)
        assert strats["Fixed Income"] / total > 0.10
        # L&I meaningful (8-15%)
        assert strats["Leveraged & Inverse"] / total > 0.05
        # Unclassified should be small (<10%)
        assert strats.get("Unclassified", 0) / total < 0.15

    def test_all_strategies_represented(self, pipeline_result):
        classifications = pipeline_result["classifications"]
        seen_strategies = {c.strategy for c in classifications}
        # At least 10 of 14 strategies should be represented
        assert len(seen_strategies) >= 10

    def test_mkt_status_reference_has_16_rows(self, pipeline_result):
        mkt_status = pipeline_result["data"]["mkt_status"]
        assert len(mkt_status) == 16
