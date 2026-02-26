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

from webapp.database import Base


class Trust(Base):
    __tablename__ = "trusts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cik: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    is_rex: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    added_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    filings: Mapped[list[Filing]] = relationship(back_populates="trust", cascade="all, delete-orphan")
    fund_statuses: Mapped[list[FundStatus]] = relationship(back_populates="trust", cascade="all, delete-orphan")


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
    map_crypto_is_spot: Mapped[str | None] = mapped_column(String(20))
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
    # Category attributes
    map_li_category: Mapped[str | None] = mapped_column(String(100))
    map_li_subcategory: Mapped[str | None] = mapped_column(String(100))
    map_li_direction: Mapped[str | None] = mapped_column(String(50))
    map_li_leverage_amount: Mapped[str | None] = mapped_column(String(20))
    map_li_underlier: Mapped[str | None] = mapped_column(String(200))
    map_cc_underlier: Mapped[str | None] = mapped_column(String(200))
    map_cc_index: Mapped[str | None] = mapped_column(String(200))
    map_crypto_is_spot: Mapped[str | None] = mapped_column(String(20))
    map_crypto_underlier: Mapped[str | None] = mapped_column(String(200))
    map_defined_category: Mapped[str | None] = mapped_column(String(100))
    map_thematic_category: Mapped[str | None] = mapped_column(String(100))
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


class MktMarketStatus(Base):
    """Reference table for market status codes (16 rows from BBG)."""
    __tablename__ = "mkt_market_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(10), unique=True, nullable=False)
    description: Mapped[str | None] = mapped_column(String(100))
