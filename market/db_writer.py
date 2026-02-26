"""Write market pipeline output to SQLite mkt_* tables (full refresh)."""
from __future__ import annotations

import json
import logging
from datetime import datetime

import pandas as pd
from sqlalchemy.orm import Session

from market.config import W4_PREFIX, ALL_ATTR_COLS, ATTRIBUTE_KEYS

log = logging.getLogger(__name__)


def create_pipeline_run(session: Session, source_file: str) -> int:
    """Create a new pipeline run record. Returns the run ID."""
    from webapp.models import MktPipelineRun

    run = MktPipelineRun(
        started_at=datetime.utcnow(),
        status="running",
        source_file=source_file,
    )
    session.add(run)
    session.flush()  # get the ID
    log.info("Pipeline run created: id=%d", run.id)
    return run.id


def finish_pipeline_run(
    session: Session,
    run_id: int,
    *,
    status: str = "completed",
    etp_rows_read: int = 0,
    master_rows_written: int = 0,
    ts_rows_written: int = 0,
    stock_rows_written: int = 0,
    unmapped_count: int = 0,
    new_issuer_count: int = 0,
    error_message: str | None = None,
) -> None:
    """Update the pipeline run record with final stats."""
    from webapp.models import MktPipelineRun

    run = session.get(MktPipelineRun, run_id)
    if run:
        run.finished_at = datetime.utcnow()
        run.status = status
        run.etp_rows_read = etp_rows_read
        run.master_rows_written = master_rows_written
        run.ts_rows_written = ts_rows_written
        run.stock_rows_written = stock_rows_written
        run.unmapped_count = unmapped_count
        run.new_issuer_count = new_issuer_count
        run.error_message = error_message
        session.commit()
        log.info("Pipeline run %d finished: %s", run_id, status)


def write_master_data(
    session: Session,
    master_df: pd.DataFrame,
    run_id: int,
) -> int:
    """Write q_master_data to mkt_master_data (full refresh).

    Returns number of rows written.
    """
    from webapp.models import MktMasterData

    # Clear existing data
    session.query(MktMasterData).delete()

    count = 0
    for _, row in master_df.iterrows():
        kwargs = _master_row_to_kwargs(row, run_id)
        session.add(MktMasterData(**kwargs))
        count += 1

        # Batch flush every 500 rows for performance
        if count % 500 == 0:
            session.flush()

    session.flush()
    log.info("mkt_master_data: %d rows written", count)
    return count


def write_time_series(
    session: Session,
    ts_df: pd.DataFrame,
    run_id: int,
) -> int:
    """Write q_aum_time_series_labeled to mkt_time_series (full refresh).

    Returns number of rows written.
    """
    from webapp.models import MktTimeSeries

    # Clear existing data
    session.query(MktTimeSeries).delete()

    count = 0
    for _, row in ts_df.iterrows():
        kwargs = {
            "pipeline_run_id": run_id,
            "ticker": str(row.get("ticker", "")),
            "months_ago": int(row.get("months_ago", 0)),
            "aum_value": _safe_float(row.get("aum_value")),
            "as_of_date": _safe_date(row.get("as_of_date")),
            "category_display": _safe_str(row.get("category_display")),
            "issuer_display": _safe_str(row.get("issuer_display")),
            "is_rex": bool(row.get("is_rex", False)),
            "issuer_group": _safe_str(row.get("issuer_group")),
            "fund_category_key": _safe_str(row.get("fund_category_key")),
        }
        session.add(MktTimeSeries(**kwargs))
        count += 1

        if count % 1000 == 0:
            session.flush()

    session.flush()
    log.info("mkt_time_series: %d rows written", count)
    return count


def write_stock_data(
    session: Session,
    stock_df: pd.DataFrame,
    run_id: int,
) -> int:
    """Write stock_data to mkt_stock_data as JSON blobs (full refresh).

    Returns number of rows written.
    """
    from webapp.models import MktStockData

    if stock_df.empty:
        log.info("mkt_stock_data: no stock data to write")
        return 0

    # Clear existing data
    session.query(MktStockData).delete()

    # Determine ticker column
    ticker_col = None
    for candidate in ["ticker", "Ticker", "TICKER"]:
        if candidate in stock_df.columns:
            ticker_col = candidate
            break

    if ticker_col is None:
        # Store entire sheet as one blob
        session.add(MktStockData(
            pipeline_run_id=run_id,
            ticker="_ALL",
            data_json=stock_df.to_json(orient="records", default_handler=str),
        ))
        log.info("mkt_stock_data: stored as single blob (%d rows)", len(stock_df))
        return 1

    count = 0
    for ticker, group in stock_df.groupby(ticker_col):
        data_json = group.to_json(orient="records", default_handler=str)
        session.add(MktStockData(
            pipeline_run_id=run_id,
            ticker=str(ticker),
            data_json=data_json,
        ))
        count += 1

        if count % 100 == 0:
            session.flush()

    session.flush()
    log.info("mkt_stock_data: %d tickers written", count)
    return count


def write_classifications(
    session: Session,
    classifications: list,
    run_id: int,
) -> int:
    """Write auto-classification results to mkt_fund_classification (full refresh).

    Args:
        classifications: list of Classification objects from auto_classify.
        run_id: pipeline run ID.

    Returns number of rows written.
    """
    from webapp.models import MktFundClassification

    # Clear existing data
    session.query(MktFundClassification).delete()

    count = 0
    for c in classifications:
        attrs_json = json.dumps(c.attributes) if c.attributes else None

        kwargs = {
            "pipeline_run_id": run_id,
            "ticker": c.ticker,
            "strategy": c.strategy,
            "confidence": c.confidence,
            "reason": c.reason[:300] if c.reason else None,
            "underlier_type": c.underlier_type or None,
            "attributes_json": attrs_json,
            "is_manual_override": False,
        }

        # Flattened attribute columns (for fast SQL filtering)
        for key in ATTRIBUTE_KEYS:
            val = c.attributes.get(key, None)
            # Map to DB column names (outcome_type -> outcome_type_detail in DB)
            db_col = "outcome_type_detail" if key == "outcome_type" else key
            if db_col == "sub_category" or db_col == "market_cap":
                continue  # not in DB schema
            kwargs[db_col] = val

        # Product structure from regulatory_structure (not available on Classification)
        kwargs["product_structure"] = None

        session.add(MktFundClassification(**kwargs))
        count += 1

        if count % 500 == 0:
            session.flush()

    session.flush()
    log.info("mkt_fund_classification: %d rows written", count)
    return count


def write_market_statuses(
    session: Session,
    mkt_status_df: pd.DataFrame,
) -> int:
    """Write market status reference table from the mkt_status sheet.

    Returns number of rows written.
    """
    from webapp.models import MktMarketStatus

    if mkt_status_df.empty:
        log.info("mkt_market_status: no data to write")
        return 0

    # Clear existing data
    session.query(MktMarketStatus).delete()

    count = 0
    for _, row in mkt_status_df.iterrows():
        # Auto-detect column names (could be "Code"/"Description" or other variants)
        code = None
        description = None
        for col in mkt_status_df.columns:
            cl = col.lower().strip()
            if cl in ("code", "market status", "status", "mkt_status"):
                code = _safe_str(row[col])
            elif cl in ("description", "desc", "name", "status_description"):
                description = _safe_str(row[col])

        if not code:
            # Try first two columns positionally
            cols = list(mkt_status_df.columns)
            code = _safe_str(row[cols[0]]) if len(cols) > 0 else None
            description = _safe_str(row[cols[1]]) if len(cols) > 1 else None

        if code:
            session.add(MktMarketStatus(code=code, description=description))
            count += 1

    session.flush()
    log.info("mkt_market_status: %d rows written", count)
    return count


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _master_row_to_kwargs(row: pd.Series, run_id: int) -> dict:
    """Convert a master DataFrame row to MktMasterData constructor kwargs."""
    kwargs = {"pipeline_run_id": run_id}

    # Base fields (unprefixed)
    base_str = [
        "ticker", "fund_name", "issuer", "listed_exchange", "inception_date",
        "fund_type", "asset_class_focus", "regulatory_structure",
        "index_weighting_methodology", "underlying_index", "is_singlestock",
        "is_active", "uses_derivatives", "uses_swaps", "is_40act",
        "uses_leverage", "leverage_amount", "outcome_type", "is_crypto",
        "cusip", "market_status",
    ]
    for col in base_str:
        kwargs[col] = _safe_str(row.get(col))

    kwargs["fund_description"] = _safe_str(row.get("fund_description"))

    # W2 metrics (prefixed with t_w2. in DataFrame)
    w2_float = [
        "expense_ratio", "management_fee", "average_bidask_spread",
        "nav_tracking_error", "percentage_premium",
        "average_percent_premium_52week", "average_vol_30day",
        "percent_short_interest", "open_interest",
    ]
    for col in w2_float:
        kwargs[col] = _safe_float(row.get(f"t_w2.{col}"))

    # W3 returns (prefixed with t_w3.)
    w3_float = [
        "total_return_1day", "total_return_1week", "total_return_1month",
        "total_return_3month", "total_return_6month", "total_return_ytd",
        "total_return_1year", "total_return_3year", "annualized_yield",
    ]
    for col in w3_float:
        kwargs[col] = _safe_float(row.get(f"t_w3.{col}"))

    # W4 flows (prefixed with t_w4.)
    w4_flow = [
        "fund_flow_1day", "fund_flow_1week", "fund_flow_1month",
        "fund_flow_3month", "fund_flow_6month", "fund_flow_ytd",
        "fund_flow_1year", "fund_flow_3year",
    ]
    for col in w4_flow:
        kwargs[col] = _safe_float(row.get(f"t_w4.{col}"))

    # AUM current
    kwargs["aum"] = _safe_float(row.get("t_w4.aum"))

    # AUM history as JSON blob
    aum_hist = {}
    for i in range(1, 37):
        val = _safe_float(row.get(f"t_w4.aum_{i}"))
        if val is not None:
            aum_hist[f"aum_{i}"] = val
    kwargs["aum_history_json"] = json.dumps(aum_hist) if aum_hist else None

    # Enrichment columns
    kwargs["etp_category"] = _safe_str(row.get("etp_category"))
    kwargs["issuer_nickname"] = _safe_str(row.get("issuer_nickname"))
    kwargs["category_display"] = _safe_str(row.get("category_display"))
    kwargs["issuer_display"] = _safe_str(row.get("issuer_display"))
    kwargs["is_rex"] = bool(row.get("is_rex", False))
    kwargs["fund_category_key"] = _safe_str(row.get("fund_category_key"))

    # Category attributes (prefixed with q_category_attributes. in DataFrame)
    for col in ALL_ATTR_COLS:
        kwargs[col] = _safe_str(row.get(f"q_category_attributes.{col}"))

    # Multi-dimensional classification columns (merged from auto-classify)
    kwargs["strategy"] = _safe_str(row.get("strategy"))
    kwargs["strategy_confidence"] = _safe_str(row.get("strategy_confidence"))
    kwargs["underlier_type"] = _safe_str(row.get("underlier_type"))

    return kwargs


def _safe_str(val) -> str | None:
    """Convert value to string, returning None for NaN/None."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if pd.isna(val):
        return None
    s = str(val).strip()
    return s if s else None


def _safe_float(val) -> float | None:
    """Convert value to float, returning None for NaN/None."""
    if val is None:
        return None
    try:
        f = float(val)
        return None if pd.isna(f) else f
    except (ValueError, TypeError):
        return None


def _safe_date(val):
    """Convert value to date, returning None for NaN/None."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if pd.isna(val):
        return None
    if hasattr(val, "date"):
        return val.date() if callable(val.date) else val.date
    return None
