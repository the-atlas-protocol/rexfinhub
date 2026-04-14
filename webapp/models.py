"""
SQLAlchemy ORM models for the ETP Filing Tracker database.

Tables mirror the CSV pipeline output with added relational integrity.
"""
from __future__ import annotations

from datetime import datetime, date

from sqlalchemy import (
    Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from webapp.database import Base, HoldingsBase


class Trust(Base):
    __tablename__ = "trusts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cik: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    is_rex: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    added_by: Mapped[str | None] = mapped_column(String(100))
    # Universal tracking columns (Phase 1a)
    entity_type: Mapped[str | None] = mapped_column(String(30))  # etf_trust | mutual_fund | grantor_trust | unknown
    regulatory_act: Mapped[str | None] = mapped_column(String(20))  # 40_act | 33_act | unknown
    sic_code: Mapped[str | None] = mapped_column(String(10))
    filing_count: Mapped[int | None] = mapped_column(Integer)  # total 485-series filings on record
    first_filed: Mapped[date | None] = mapped_column(Date)
    last_filed: Mapped[date | None] = mapped_column(Date)
    source: Mapped[str | None] = mapped_column(String(30))  # curated | bulk_discovery | watcher
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    filings: Mapped[list[Filing]] = relationship(back_populates="trust", cascade="all, delete-orphan")
    fund_statuses: Mapped[list[FundStatus]] = relationship(back_populates="trust", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_trusts_entity_type", "entity_type"),
        Index("idx_trusts_source", "source"),
    )


class Filing(Base):
    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trust_id: Mapped[int] = mapped_column(Integer, ForeignKey("trusts.id"), nullable=False)
    accession_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    form: Mapped[str] = mapped_column(String(20), nullable=False)
    filing_date: Mapped[date | None] = mapped_column(Date)
    primary_document: Mapped[str | None] = mapped_column(String(200))
    primary_link: Mapped[str | None] = mapped_column(Text)
    submission_txt_link: Mapped[str | None] = mapped_column(Text)
    cik: Mapped[str] = mapped_column(String(20), nullable=False)
    registrant: Mapped[str | None] = mapped_column(String(200))
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    trust: Mapped[Trust] = relationship(back_populates="filings")
    extractions: Mapped[list[FundExtraction]] = relationship(back_populates="filing", cascade="all, delete-orphan")
    analyses: Mapped[list[AnalysisResult]] = relationship(back_populates="filing")

    __table_args__ = (
        Index("idx_filings_trust", "trust_id"),
        Index("idx_filings_form", "form"),
        Index("idx_filings_date", "filing_date"),
    )


class FundExtraction(Base):
    __tablename__ = "fund_extractions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filing_id: Mapped[int] = mapped_column(Integer, ForeignKey("filings.id"), nullable=False)
    series_id: Mapped[str | None] = mapped_column(String(30))
    series_name: Mapped[str | None] = mapped_column(String(300))
    class_contract_id: Mapped[str | None] = mapped_column(String(30))
    class_contract_name: Mapped[str | None] = mapped_column(String(300))
    class_symbol: Mapped[str | None] = mapped_column(String(20))
    extracted_from: Mapped[str | None] = mapped_column(String(50))
    effective_date: Mapped[date | None] = mapped_column(Date)
    effective_date_confidence: Mapped[str | None] = mapped_column(String(20))
    delaying_amendment: Mapped[bool] = mapped_column(Boolean, default=False)
    prospectus_name: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    filing: Mapped[Filing] = relationship(back_populates="extractions")

    __table_args__ = (
        Index("idx_extractions_series", "series_id"),
        Index("idx_extractions_filing", "filing_id"),
    )


class FundStatus(Base):
    __tablename__ = "fund_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trust_id: Mapped[int] = mapped_column(Integer, ForeignKey("trusts.id"), nullable=False)
    series_id: Mapped[str | None] = mapped_column(String(30))
    class_contract_id: Mapped[str | None] = mapped_column(String(30))
    fund_name: Mapped[str] = mapped_column(String(300), nullable=False)
    sgml_name: Mapped[str | None] = mapped_column(String(300))
    prospectus_name: Mapped[str | None] = mapped_column(String(300))
    ticker: Mapped[str | None] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(Text)
    effective_date: Mapped[date | None] = mapped_column(Date)
    effective_date_confidence: Mapped[str | None] = mapped_column(String(20))
    latest_form: Mapped[str | None] = mapped_column(String(20))
    latest_filing_date: Mapped[date | None] = mapped_column(Date)
    prospectus_link: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    trust: Mapped[Trust] = relationship(back_populates="fund_statuses")

    __table_args__ = (
        UniqueConstraint("trust_id", "series_id", "class_contract_id", name="uq_fund_status"),
        Index("idx_fund_status_trust", "trust_id"),
        Index("idx_fund_status_status", "status"),
        Index("idx_fund_status_ticker", "ticker"),
    )


class NameHistory(Base):
    __tablename__ = "name_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    series_id: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    name_clean: Mapped[str | None] = mapped_column(String(300))
    first_seen_date: Mapped[date | None] = mapped_column(Date)
    last_seen_date: Mapped[date | None] = mapped_column(Date)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source_form: Mapped[str | None] = mapped_column(String(20))
    source_accession: Mapped[str | None] = mapped_column(String(30))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_name_history_series", "series_id"),
    )


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filing_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("filings.id"))
    fund_status_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("fund_status.id"))
    analysis_type: Mapped[str] = mapped_column(String(50), nullable=False)
    prompt_used: Mapped[str | None] = mapped_column(Text)
    result_text: Mapped[str] = mapped_column(Text, nullable=False)
    result_html: Mapped[str | None] = mapped_column(Text)
    model_used: Mapped[str | None] = mapped_column(String(50))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    requested_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    filing: Mapped[Filing | None] = relationship(back_populates="analyses")

    __table_args__ = (
        Index("idx_analysis_filing", "filing_id"),
    )


class PipelineRun(Base):
    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    trusts_processed: Mapped[int] = mapped_column(Integer, default=0)
    filings_found: Mapped[int] = mapped_column(Integer, default=0)
    funds_extracted: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)
    triggered_by: Mapped[str | None] = mapped_column(String(100))


# --- Screener Models ---

class ScreenerUpload(Base):
    __tablename__ = "screener_uploads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    file_name: Mapped[str] = mapped_column(String(200), nullable=False)
    stock_rows: Mapped[int] = mapped_column(Integer, default=0)
    etp_rows: Mapped[int] = mapped_column(Integer, default=0)
    filing_rows: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(20), default="processing", nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[str | None] = mapped_column(String(100))
    model_type: Mapped[str | None] = mapped_column(String(50))
    model_r_squared: Mapped[float | None] = mapped_column(Float)

    results: Mapped[list[ScreenerResult]] = relationship(back_populates="upload", cascade="all, delete-orphan")


class ScreenerResult(Base):
    __tablename__ = "screener_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    upload_id: Mapped[int] = mapped_column(Integer, ForeignKey("screener_uploads.id"), nullable=False)
    ticker: Mapped[str] = mapped_column(String(30), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(200))
    sector: Mapped[str | None] = mapped_column(String(100))
    composite_score: Mapped[float] = mapped_column(Float, nullable=False)
    predicted_aum: Mapped[float | None] = mapped_column(Float)
    predicted_aum_low: Mapped[float | None] = mapped_column(Float)
    predicted_aum_high: Mapped[float | None] = mapped_column(Float)
    mkt_cap: Mapped[float | None] = mapped_column(Float)
    call_oi_pctl: Mapped[float | None] = mapped_column(Float)
    total_oi_pctl: Mapped[float | None] = mapped_column(Float)
    volume_pctl: Mapped[float | None] = mapped_column(Float)
    passes_filters: Mapped[bool] = mapped_column(Boolean, default=False)
    filing_status: Mapped[str | None] = mapped_column(String(100))
    competitive_density: Mapped[str | None] = mapped_column(String(50))
    competitor_count: Mapped[int | None] = mapped_column(Integer)
    total_competitor_aum: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    upload: Mapped[ScreenerUpload] = relationship(back_populates="results")

    __table_args__ = (
        Index("idx_screener_upload", "upload_id"),
        Index("idx_screener_score", "composite_score"),
        Index("idx_screener_ticker", "ticker"),
    )


# --- Market Intelligence Models ---

class MktPipelineRun(Base):
    __tablename__ = "mkt_pipeline_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)
    source_file: Mapped[str | None] = mapped_column(String(500))
    etp_rows_read: Mapped[int] = mapped_column(Integer, default=0)
    master_rows_written: Mapped[int] = mapped_column(Integer, default=0)
    ts_rows_written: Mapped[int] = mapped_column(Integer, default=0)
    stock_rows_written: Mapped[int] = mapped_column(Integer, default=0)
    unmapped_count: Mapped[int] = mapped_column(Integer, default=0)
    new_issuer_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)


class MktFundMapping(Base):
    __tablename__ = "mkt_fund_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(30), nullable=False)
    etp_category: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("ticker", "etp_category", name="uq_mkt_fund_mapping"),
        Index("idx_mkt_fund_mapping_ticker", "ticker"),
    )


class MktIssuerMapping(Base):
    __tablename__ = "mkt_issuer_mapping"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    etp_category: Mapped[str] = mapped_column(String(20), nullable=False)
    issuer: Mapped[str] = mapped_column(String(200), nullable=False)
    issuer_nickname: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("etp_category", "issuer", name="uq_mkt_issuer_mapping"),
        Index("idx_mkt_issuer_mapping_cat", "etp_category"),
    )


class MktCategoryAttributes(Base):
    __tablename__ = "mkt_category_attributes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    map_li_category: Mapped[str | None] = mapped_column(String(100))
    map_li_subcategory: Mapped[str | None] = mapped_column(String(100))
    map_li_direction: Mapped[str | None] = mapped_column(String(50))
    map_li_leverage_amount: Mapped[str | None] = mapped_column(String(20))
    map_li_underlier: Mapped[str | None] = mapped_column(String(200))
    map_cc_underlier: Mapped[str | None] = mapped_column(String(200))
    map_cc_index: Mapped[str | None] = mapped_column(String(200))
    cc_type: Mapped[str | None] = mapped_column(String(50))
    cc_category: Mapped[str | None] = mapped_column(String(100))
    map_crypto_type: Mapped[str | None] = mapped_column(String(100))
    map_crypto_underlier: Mapped[str | None] = mapped_column(String(200))
    map_defined_category: Mapped[str | None] = mapped_column(String(100))
    map_thematic_category: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class MktExclusion(Base):
    __tablename__ = "mkt_exclusions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(30), nullable=False)
    etp_category: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("ticker", "etp_category", name="uq_mkt_exclusion"),
    )


class MktRexFund(Base):
    __tablename__ = "mkt_rex_funds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class MktMasterData(Base):
    __tablename__ = "mkt_master_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("mkt_pipeline_runs.id"))
    # Base fields
    ticker: Mapped[str] = mapped_column(String(30), nullable=False)
    fund_name: Mapped[str | None] = mapped_column(String(300))
    issuer: Mapped[str | None] = mapped_column(String(200))
    listed_exchange: Mapped[str | None] = mapped_column(String(50))
    inception_date: Mapped[str | None] = mapped_column(String(30))
    fund_type: Mapped[str | None] = mapped_column(String(100))
    asset_class_focus: Mapped[str | None] = mapped_column(String(100))
    regulatory_structure: Mapped[str | None] = mapped_column(String(100))
    index_weighting_methodology: Mapped[str | None] = mapped_column(String(200))
    underlying_index: Mapped[str | None] = mapped_column(String(300))
    is_singlestock: Mapped[str | None] = mapped_column(String(20))
    is_active: Mapped[str | None] = mapped_column(String(20))
    uses_derivatives: Mapped[str | None] = mapped_column(String(20))
    uses_swaps: Mapped[str | None] = mapped_column(String(20))
    is_40act: Mapped[str | None] = mapped_column(String(20))
    uses_leverage: Mapped[str | None] = mapped_column(String(20))
    leverage_amount: Mapped[str | None] = mapped_column(String(50))
    outcome_type: Mapped[str | None] = mapped_column(String(100))
    is_crypto: Mapped[str | None] = mapped_column(String(20))
    cusip: Mapped[str | None] = mapped_column(String(30))
    market_status: Mapped[str | None] = mapped_column(String(50))
    fund_description: Mapped[str | None] = mapped_column(Text)
    # W2 metrics
    expense_ratio: Mapped[float | None] = mapped_column(Float)
    management_fee: Mapped[float | None] = mapped_column(Float)
    average_bidask_spread: Mapped[float | None] = mapped_column(Float)
    nav_tracking_error: Mapped[float | None] = mapped_column(Float)
    percentage_premium: Mapped[float | None] = mapped_column(Float)
    average_percent_premium_52week: Mapped[float | None] = mapped_column(Float)
    average_vol_30day: Mapped[float | None] = mapped_column(Float)
    percent_short_interest: Mapped[float | None] = mapped_column(Float)
    open_interest: Mapped[float | None] = mapped_column(Float)
    # W3 returns
    total_return_1day: Mapped[float | None] = mapped_column(Float)
    total_return_1week: Mapped[float | None] = mapped_column(Float)
    total_return_1month: Mapped[float | None] = mapped_column(Float)
    total_return_3month: Mapped[float | None] = mapped_column(Float)
    total_return_6month: Mapped[float | None] = mapped_column(Float)
    total_return_ytd: Mapped[float | None] = mapped_column(Float)
    total_return_1year: Mapped[float | None] = mapped_column(Float)
    total_return_3year: Mapped[float | None] = mapped_column(Float)
    annualized_yield: Mapped[float | None] = mapped_column(Float)
    # W5 price returns (current-day, no lag)
    price_return_1day: Mapped[float | None] = mapped_column(Float)
    price_return_2day: Mapped[float | None] = mapped_column(Float)
    price_return_3day: Mapped[float | None] = mapped_column(Float)
    price_return_5day: Mapped[float | None] = mapped_column(Float)
    price_return_1month: Mapped[float | None] = mapped_column(Float)
    price_return_3month: Mapped[float | None] = mapped_column(Float)
    price_return_6month: Mapped[float | None] = mapped_column(Float)
    price_return_ytd: Mapped[float | None] = mapped_column(Float)
    price_return_1year: Mapped[float | None] = mapped_column(Float)
    # W4 flows + AUM (stored as JSON string for aum_1..aum_36 to avoid 36 columns)
    fund_flow_1day: Mapped[float | None] = mapped_column(Float)
    fund_flow_1week: Mapped[float | None] = mapped_column(Float)
    fund_flow_1month: Mapped[float | None] = mapped_column(Float)
    fund_flow_3month: Mapped[float | None] = mapped_column(Float)
    fund_flow_6month: Mapped[float | None] = mapped_column(Float)
    fund_flow_ytd: Mapped[float | None] = mapped_column(Float)
    fund_flow_1year: Mapped[float | None] = mapped_column(Float)
    fund_flow_3year: Mapped[float | None] = mapped_column(Float)
    aum: Mapped[float | None] = mapped_column(Float)
    aum_history_json: Mapped[str | None] = mapped_column(Text)  # JSON: {"aum_1": val, ..., "aum_36": val}
    # Enrichment
    etp_category: Mapped[str | None] = mapped_column(String(20))
    issuer_nickname: Mapped[str | None] = mapped_column(String(200))
    category_display: Mapped[str | None] = mapped_column(String(100))
    issuer_display: Mapped[str | None] = mapped_column(String(200))
    is_rex: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    fund_category_key: Mapped[str | None] = mapped_column(String(200))
    primary_category: Mapped[str | None] = mapped_column(String(20))
    rex_suite: Mapped[str | None] = mapped_column(String(50))
    # Category attributes
    map_li_category: Mapped[str | None] = mapped_column(String(100))
    map_li_subcategory: Mapped[str | None] = mapped_column(String(100))
    map_li_direction: Mapped[str | None] = mapped_column(String(50))
    map_li_leverage_amount: Mapped[str | None] = mapped_column(String(20))
    map_li_underlier: Mapped[str | None] = mapped_column(String(200))
    map_cc_underlier: Mapped[str | None] = mapped_column(String(200))
    map_cc_index: Mapped[str | None] = mapped_column(String(200))
    map_crypto_type: Mapped[str | None] = mapped_column(String(100))
    map_crypto_underlier: Mapped[str | None] = mapped_column(String(200))
    map_defined_category: Mapped[str | None] = mapped_column(String(100))
    map_thematic_category: Mapped[str | None] = mapped_column(String(100))
    # Covered call / structured product attributes
    cc_type: Mapped[str | None] = mapped_column(String(50))
    cc_category: Mapped[str | None] = mapped_column(String(100))
    ticker_clean: Mapped[str | None] = mapped_column(String(30))
    # Multi-dimensional classification columns (from auto-classify)
    strategy: Mapped[str | None] = mapped_column(String(50))
    strategy_confidence: Mapped[str | None] = mapped_column(String(10))
    underlier_type: Mapped[str | None] = mapped_column(String(50))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("ticker", "etp_category", name="uq_mkt_master_data"),
        Index("idx_mkt_master_ticker", "ticker"),
        Index("idx_mkt_master_category", "etp_category"),
        Index("idx_mkt_master_cat_display", "category_display"),
        Index("idx_mkt_master_run", "pipeline_run_id"),
        Index("idx_mkt_master_cusip", "cusip"),
    )


class MktTimeSeries(Base):
    __tablename__ = "mkt_time_series"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("mkt_pipeline_runs.id"))
    ticker: Mapped[str] = mapped_column(String(30), nullable=False)
    months_ago: Mapped[int] = mapped_column(Integer, nullable=False)
    aum_value: Mapped[float | None] = mapped_column(Float)
    as_of_date: Mapped[date | None] = mapped_column(Date)
    category_display: Mapped[str | None] = mapped_column(String(100))
    issuer_display: Mapped[str | None] = mapped_column(String(200))
    is_rex: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    issuer_group: Mapped[str | None] = mapped_column(String(200))
    fund_category_key: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        Index("idx_mkt_ts_ticker_month", "ticker", "months_ago"),
        Index("idx_mkt_ts_category", "category_display"),
        Index("idx_mkt_ts_fck", "fund_category_key"),
        Index("idx_mkt_ts_run", "pipeline_run_id"),
    )


class MktReportCache(Base):
    """Pre-computed report data stored as JSON.

    Reports are computed during the local sync pipeline and serialized
    here so Render can serve them without holding DataFrames in memory.
    """
    __tablename__ = "mkt_report_cache"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("mkt_pipeline_runs.id"))
    report_key: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    data_json: Mapped[str | None] = mapped_column(Text)
    data_as_of: Mapped[str | None] = mapped_column(String(30))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class MktStockData(Base):
    __tablename__ = "mkt_stock_data"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("mkt_pipeline_runs.id"))
    ticker: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    data_json: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class MktFundClassification(Base):
    """Multi-dimensional fund classification (FactSet-style).

    One row per (ticker, pipeline_run). Stores strategy + underlier_type
    as independent dimensions, with key attributes flattened for indexing
    plus a JSON blob for full flexibility.
    """
    __tablename__ = "mkt_fund_classification"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("mkt_pipeline_runs.id"))
    ticker: Mapped[str] = mapped_column(String(30), nullable=False)
    # Dimension 1: Strategy
    strategy: Mapped[str | None] = mapped_column(String(50))
    confidence: Mapped[str | None] = mapped_column(String(10))
    reason: Mapped[str | None] = mapped_column(String(300))
    # Dimension 2: Underlier type
    underlier_type: Mapped[str | None] = mapped_column(String(50))
    # Dimension 3: Key attributes (flattened for indexing)
    direction: Mapped[str | None] = mapped_column(String(30))
    leverage_amount: Mapped[str | None] = mapped_column(String(20))
    underlier: Mapped[str | None] = mapped_column(String(200))
    income_strategy: Mapped[str | None] = mapped_column(String(50))
    geography: Mapped[str | None] = mapped_column(String(50))
    sector: Mapped[str | None] = mapped_column(String(50))
    duration: Mapped[str | None] = mapped_column(String(30))
    credit_quality: Mapped[str | None] = mapped_column(String(50))
    commodity_type: Mapped[str | None] = mapped_column(String(50))
    crypto_type: Mapped[str | None] = mapped_column(String(30))
    theme: Mapped[str | None] = mapped_column(String(100))
    outcome_type_detail: Mapped[str | None] = mapped_column(String(50))
    # Full attributes as JSON blob for flexibility
    attributes_json: Mapped[str | None] = mapped_column(Text)
    # Product structure
    product_structure: Mapped[str | None] = mapped_column(String(30))
    # Override tracking
    is_manual_override: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("ticker", "pipeline_run_id", name="uq_mkt_classification"),
        Index("idx_mkt_class_ticker", "ticker"),
        Index("idx_mkt_class_strategy", "strategy"),
        Index("idx_mkt_class_underlier_type", "underlier_type"),
        Index("idx_mkt_class_run", "pipeline_run_id"),
        Index("idx_mkt_class_sector", "sector"),
        Index("idx_mkt_class_geography", "geography"),
    )


class ClassificationProposal(Base):
    """Review queue for fund classification proposals.

    Auto-classify writes proposals here instead of directly to CSVs.
    Proposals are reviewed in the admin panel before being applied.
    """
    __tablename__ = "classification_proposals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(30), nullable=False)
    fund_name: Mapped[str | None] = mapped_column(String(300))
    issuer: Mapped[str | None] = mapped_column(String(200))
    aum: Mapped[float | None] = mapped_column(Float)
    # Classification
    proposed_category: Mapped[str | None] = mapped_column(String(20))  # LI, CC, Crypto, Defined, Thematic
    proposed_strategy: Mapped[str | None] = mapped_column(String(50))
    confidence: Mapped[str | None] = mapped_column(String(10))  # HIGH, MEDIUM, LOW
    reason: Mapped[str | None] = mapped_column(Text)
    attributes_json: Mapped[str | None] = mapped_column(Text)  # Resolved attributes as JSON
    # Review state
    status: Mapped[str] = mapped_column(String(20), default="pending", nullable=False)  # pending, approved, rejected
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    review_notes: Mapped[str | None] = mapped_column(Text)
    # Timestamps
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_cls_proposal_ticker", "ticker"),
        Index("idx_cls_proposal_status", "status"),
    )


class MktGlobalEtp(Base):
    """Global ETP universe supplement data (~16,534 rows).

    Aggregates key columns from 7 Bloomberg global sheets (assets, cost,
    performance, flows, liquidity, gics, geographic, structure), joined by
    ticker. Infrastructure table -- no UI yet.
    """
    __tablename__ = "mkt_global_etp"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    pipeline_run_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("mkt_pipeline_runs.id"))
    # Identity
    ticker: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[str | None] = mapped_column(String(300))
    # Assets
    class_aum: Mapped[float | None] = mapped_column(Float)
    fund_aum: Mapped[float | None] = mapped_column(Float)
    nav: Mapped[float | None] = mapped_column(Float)
    holdings_count: Mapped[int | None] = mapped_column(Integer)
    # Cost
    expense_ratio: Mapped[float | None] = mapped_column(Float)
    mgmt_fee: Mapped[float | None] = mapped_column(Float)
    bid_ask_spread: Mapped[float | None] = mapped_column(Float)
    nav_tracking_error: Mapped[float | None] = mapped_column(Float)
    premium: Mapped[float | None] = mapped_column(Float)
    # Performance
    return_mtd: Mapped[float | None] = mapped_column(Float)
    return_5y: Mapped[float | None] = mapped_column(Float)
    return_10y: Mapped[float | None] = mapped_column(Float)
    high_52w: Mapped[float | None] = mapped_column(Float)
    low_52w: Mapped[float | None] = mapped_column(Float)
    yield_12m: Mapped[float | None] = mapped_column(Float)
    # Liquidity
    volume_1d: Mapped[float | None] = mapped_column(Float)
    avg_volume_30d: Mapped[float | None] = mapped_column(Float)
    implied_liquidity: Mapped[float | None] = mapped_column(Float)
    agg_traded_val: Mapped[float | None] = mapped_column(Float)
    # Structure
    fund_type: Mapped[str | None] = mapped_column(String(100))
    structure: Mapped[str | None] = mapped_column(String(100))
    is_ucits: Mapped[str | None] = mapped_column(String(20))
    leverage: Mapped[str | None] = mapped_column(String(50))
    inception_date: Mapped[str | None] = mapped_column(String(30))
    # Sector/Geo (packed as JSON)
    gics_json: Mapped[str | None] = mapped_column(Text)
    geo_json: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("idx_mkt_global_etp_ticker", "ticker"),
        Index("idx_mkt_global_etp_run", "pipeline_run_id"),
    )


class MktMarketStatus(Base):
    """Reference table for market status codes (16 rows from BBG)."""
    __tablename__ = "mkt_market_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(100))



# --- 13F Institutional Holdings Models ---

class Institution(HoldingsBase):
    """13F filing institutions (hedge funds, asset managers)."""
    __tablename__ = "institutions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cik: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(300), nullable=False)
    city: Mapped[str | None] = mapped_column(String(100))
    state_or_country: Mapped[str | None] = mapped_column(String(10))
    manager_type: Mapped[str | None] = mapped_column(String(50))
    aum_total: Mapped[float | None] = mapped_column(Float)
    filing_count: Mapped[int] = mapped_column(Integer, default=0)
    last_filed: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    holdings: Mapped[list[Holding]] = relationship(back_populates="institution", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_institutions_name", "name"),
    )


class Holding(HoldingsBase):
    """Individual position from 13F-HR infotable."""
    __tablename__ = "holdings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    institution_id: Mapped[int] = mapped_column(Integer, ForeignKey("institutions.id"), nullable=False)
    report_date: Mapped[date] = mapped_column(Date, nullable=False)
    filing_accession: Mapped[str | None] = mapped_column(String(30))
    issuer_name: Mapped[str | None] = mapped_column(String(300))
    cusip: Mapped[str | None] = mapped_column(String(12))
    value_usd: Mapped[float | None] = mapped_column(Float)  # full dollars (post-2023 SEC format; pre-2023 was thousands — normalize at ingestion)
    shares: Mapped[float | None] = mapped_column(Float)
    share_type: Mapped[str | None] = mapped_column(String(10))  # SH | PRN
    investment_discretion: Mapped[str | None] = mapped_column(String(10))  # SOLE | DFND | OTR
    voting_sole: Mapped[int | None] = mapped_column(Integer)
    voting_shared: Mapped[int | None] = mapped_column(Integer)
    voting_none: Mapped[int | None] = mapped_column(Integer)
    is_tracked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    institution: Mapped[Institution] = relationship(back_populates="holdings")

    __table_args__ = (
        Index("idx_holdings_institution", "institution_id"),
        Index("idx_holdings_cusip", "cusip"),
        Index("idx_holdings_report_date", "report_date"),
        Index("idx_holdings_date_cusip", "report_date", "cusip"),
        Index("idx_holdings_tracked", "is_tracked"),
        Index("idx_holdings_tracked_date", "is_tracked", "report_date"),
    )


class CusipMapping(HoldingsBase):
    """Links CUSIP to our fund universe."""
    __tablename__ = "cusip_mappings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cusip: Mapped[str] = mapped_column(String(12), unique=True, nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(20))
    fund_name: Mapped[str | None] = mapped_column(String(300))
    trust_id: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str | None] = mapped_column(String(30))  # mkt_master | manual
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_cusip_mappings_ticker", "ticker"),
    )



# --- Admin Request Models ---

class TrustRequest(Base):
    __tablename__ = "trust_requests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cik: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False, index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class DigestSubscriber(Base):
    __tablename__ = "digest_subscribers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(20), default="PENDING", nullable=False, index=True)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class EmailRecipient(Base):
    """Per-report email recipients stored in DB (not text files).

    list_type determines which reports this recipient receives:
      - "daily"    → Daily ETP Report
      - "weekly"   → Weekly Report
      - "li"       → L&I Report
      - "income"   → Income Report
      - "flow"     → Flow Report
      - "autocall" → Autocallable Update (external)
      - "private"  → BCC on all sends
    """
    __tablename__ = "email_recipients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(200), nullable=False)
    list_type: Mapped[str] = mapped_column(String(20), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    added_by: Mapped[str | None] = mapped_column(String(100))

    __table_args__ = (
        UniqueConstraint("email", "list_type", name="uq_recipient_email_list"),
        Index("idx_recipient_list", "list_type"),
        Index("idx_recipient_active", "is_active"),
    )



# --- Fund Distribution Schedule ---

class FundDistribution(Base):
    """Distribution schedule for a REX fund.

    Sourced from the master REX_Distribution_Calendar_2026.xlsx + per-fund
    Excel files. One row per distribution event (declaration/ex/record/payable).
    Used by the pipeline calendar to show dividend events.
    """
    __tablename__ = "fund_distributions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(20), nullable=False)
    fund_name: Mapped[str | None] = mapped_column(String(200))
    declaration_date: Mapped[date | None] = mapped_column(Date)
    ex_date: Mapped[date] = mapped_column(Date, nullable=False)
    record_date: Mapped[date | None] = mapped_column(Date)
    payable_date: Mapped[date | None] = mapped_column(Date)
    amount: Mapped[float | None] = mapped_column(Float)  # optional $ amount (not in current Excel)
    source_file: Mapped[str | None] = mapped_column(String(200))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("ticker", "ex_date", name="uq_fund_dist_ticker_ex"),
        Index("idx_fund_dist_ticker", "ticker"),
        Index("idx_fund_dist_ex", "ex_date"),
        Index("idx_fund_dist_payable", "payable_date"),
    )


class NyseHoliday(Base):
    """NYSE holiday calendar. Highlights market-closed days on the pipeline calendar."""
    __tablename__ = "nyse_holidays"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    holiday_date: Mapped[date] = mapped_column(Date, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("holiday_date", name="uq_nyse_holiday_date"),
        Index("idx_nyse_holiday_date", "holiday_date"),
    )


# --- REX Product Pipeline ---

class RexProduct(Base):
    """REX product lifecycle tracker — from research to listing.

    Replaces the Excel-based REX Master Product Development Tracker.
    470 products across 8 suites, tracked through:
      Research → Target List → Filed → Awaiting Effective → Listed → Delisted
    """
    __tablename__ = "rex_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    trust: Mapped[str | None] = mapped_column(String(200))
    product_suite: Mapped[str] = mapped_column(String(50), nullable=False)  # T-REX, IncomeMax, Premium Income, etc.
    status: Mapped[str] = mapped_column(String(30), nullable=False)  # Research, Target List, Filed, Awaiting Effective, Listed, Delisted
    ticker: Mapped[str | None] = mapped_column(String(20))
    underlier: Mapped[str | None] = mapped_column(String(100))
    direction: Mapped[str | None] = mapped_column(String(20))  # Long, Short, Both

    # Filing lifecycle dates
    initial_filing_date: Mapped[date | None] = mapped_column(Date)
    estimated_effective_date: Mapped[date | None] = mapped_column(Date)
    target_listing_date: Mapped[date | None] = mapped_column(Date)
    seed_date: Mapped[date | None] = mapped_column(Date)
    official_listed_date: Mapped[date | None] = mapped_column(Date)

    # SEC identifiers
    latest_form: Mapped[str | None] = mapped_column(String(20))
    latest_prospectus_link: Mapped[str | None] = mapped_column(Text)
    cik: Mapped[str | None] = mapped_column(String(20))
    series_id: Mapped[str | None] = mapped_column(String(20))
    class_contract_id: Mapped[str | None] = mapped_column(String(20))

    # Operational details
    lmm: Mapped[str | None] = mapped_column(String(100))  # Lead Market Maker
    exchange: Mapped[str | None] = mapped_column(String(20))
    mgt_fee: Mapped[float | None] = mapped_column(Float)
    tracking_index: Mapped[str | None] = mapped_column(String(200))
    fund_admin: Mapped[str | None] = mapped_column(String(100))
    cu_size: Mapped[int | None] = mapped_column(Integer)
    starting_nav: Mapped[float | None] = mapped_column(Float)

    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_rex_product_suite", "product_suite"),
        Index("idx_rex_product_status", "status"),
        Index("idx_rex_product_ticker", "ticker"),
        Index("idx_rex_product_series", "series_id"),
    )


# --- Filing Watcher Models ---

class FilingAlert(Base):
    __tablename__ = "filing_alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # trust_id is nullable: Tier 1 atom watcher inserts alerts for unknown
    # CIKs (every SEC filer, not just curated trusts). Tier 2 enricher
    # resolves CIK -> trust, auto-creating trusts as needed.
    trust_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("trusts.id"), nullable=True)
    cik: Mapped[str | None] = mapped_column(String(20))
    accession_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    form_type: Mapped[str] = mapped_column(String(20), nullable=False)
    filed_date: Mapped[date | None] = mapped_column(Date)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    processed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    # Tier 1 -> Tier 2 handoff fields
    source: Mapped[str | None] = mapped_column(String(30))  # atom | reconciler | bulk | manual
    enrichment_status: Mapped[int] = mapped_column(Integer, default=0, nullable=False)  # 0=pending, 1=done, 2=failed, 3=skipped
    enrichment_error: Mapped[str | None] = mapped_column(Text)
    primary_doc_url: Mapped[str | None] = mapped_column(Text)
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    company_name: Mapped[str | None] = mapped_column(String(200))

    trust: Mapped["Trust | None"] = relationship()

    __table_args__ = (
        Index("idx_filing_alerts_trust", "trust_id"),
        Index("idx_filing_alerts_processed", "processed"),
        Index("idx_filing_alerts_filed", "filed_date"),
        Index("idx_filing_alerts_cik", "cik"),
        Index("idx_filing_alerts_enrichment", "enrichment_status"),
    )


class TrustCandidate(Base):
    __tablename__ = "trust_candidates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cik: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    company_name: Mapped[str] = mapped_column(String(200), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    last_seen: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    filing_count: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    form_types_seen: Mapped[str | None] = mapped_column(Text)
    etf_trust_score: Mapped[float | None] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(20), default="new", nullable=False)
    notes: Mapped[str | None] = mapped_column(Text)
    reviewed_by: Mapped[str | None] = mapped_column(String(100))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (
        Index("idx_trust_candidates_status", "status"),
        Index("idx_trust_candidates_score", "etf_trust_score"),
    )
