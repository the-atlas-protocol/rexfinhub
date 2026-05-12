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

from webapp.database import Base, HoldingsBase, LiveFeedBase


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


class FilingAnalysis(Base):
    """Cached LLM analysis of a new fund filing ("Top Filings of the Day").

    One row per (filing, writer_model) pair. Re-runs of the daily pipeline
    pull from this cache instead of making fresh LLM calls.

    Audit R5 (2026-05-11): the legacy schema had ``UNIQUE(filing_id)`` only,
    which meant a writer-model upgrade (e.g. Sonnet -> Opus) silently served
    the stale narrative forever. The unique key now includes ``writer_model``
    so a model upgrade triggers re-analysis. The simple ``filing_id`` column
    index is retained for lookup speed (non-unique now).
    """
    __tablename__ = "filing_analyses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    filing_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("filings.id"), nullable=False, index=True,
    )
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    prospectus_url: Mapped[str | None] = mapped_column(String)
    objective_excerpt: Mapped[str | None] = mapped_column(Text)
    strategy_excerpt: Mapped[str | None] = mapped_column(Text)
    filing_title: Mapped[str | None] = mapped_column(String)
    strategy_type: Mapped[str | None] = mapped_column(String)
    underlying: Mapped[str | None] = mapped_column(String)
    structure: Mapped[str | None] = mapped_column(String)
    portfolio_holding: Mapped[str | None] = mapped_column(String)
    distribution: Mapped[str | None] = mapped_column(String)
    narrative: Mapped[str | None] = mapped_column(Text)
    interestingness: Mapped[float | None] = mapped_column(Float)
    selector_reason: Mapped[str | None] = mapped_column(String)
    selector_model: Mapped[str | None] = mapped_column(String)
    writer_model: Mapped[str | None] = mapped_column(String)
    tokens_in: Mapped[int | None] = mapped_column(Integer)
    tokens_out: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Float)

    __table_args__ = (
        UniqueConstraint(
            "filing_id", "writer_model",
            name="uq_filing_analyses_filing_writer",
        ),
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
    # 3-axis taxonomy (CLASSIFICATION_SYSTEM_PLAN.md — populated via classification sweep)
    asset_class: Mapped[str | None] = mapped_column(String(30))
    primary_strategy: Mapped[str | None] = mapped_column(String(40))
    sub_strategy: Mapped[str | None] = mapped_column(String(80))
    concentration: Mapped[str | None] = mapped_column(String(10))
    underlier_name: Mapped[str | None] = mapped_column(String(60))
    underlier_is_wrapper: Mapped[bool | None] = mapped_column(Boolean)
    root_underlier_name: Mapped[str | None] = mapped_column(String(60))
    wrapper_type: Mapped[str | None] = mapped_column(String(20))
    mechanism: Mapped[str | None] = mapped_column(String(20))
    leverage_ratio: Mapped[float | None] = mapped_column(Float)
    direction: Mapped[str | None] = mapped_column(String(10))
    reset_period: Mapped[str | None] = mapped_column(String(15))
    distribution_freq: Mapped[str | None] = mapped_column(String(15))
    outcome_period_months: Mapped[int | None] = mapped_column(Integer)
    cap_pct: Mapped[float | None] = mapped_column(Float)
    buffer_pct: Mapped[float | None] = mapped_column(Float)
    accelerator_multiplier: Mapped[float | None] = mapped_column(Float)
    barrier_pct: Mapped[float | None] = mapped_column(Float)
    region: Mapped[str | None] = mapped_column(String(30))
    duration_bucket: Mapped[str | None] = mapped_column(String(20))
    credit_quality: Mapped[str | None] = mapped_column(String(20))
    tax_structure: Mapped[str | None] = mapped_column(String(20))
    qualified_dividends: Mapped[bool | None] = mapped_column(Boolean)
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


class ClassificationAuditLog(Base):
    """Audit log for every value written by the classification sweep.

    Records every change to mkt_master_data classification columns (the 3-axis
    taxonomy + ~20 attribute columns). Used to verify the no-overwrite safeguard
    held and to roll back if a sweep misbehaves. Append-only.

    source values:
      'sweep_high'    HIGH-confidence auto-apply
      'sweep_medium'  MED-confidence auto-apply (if --apply-medium passed)
      'proposal'      Wrote to ClassificationProposal queue (no DB change)
      'conflict'      Skipped — existing value differs from suggestion
      'rollback'      Manual rollback action
    """
    __tablename__ = "classification_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sweep_run_id: Mapped[str | None] = mapped_column(String(40))  # ISO timestamp tag
    ticker: Mapped[str] = mapped_column(String(30), nullable=False)
    column_name: Mapped[str] = mapped_column(String(60), nullable=False)
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(30))
    confidence: Mapped[str | None] = mapped_column(String(10))
    reason: Mapped[str | None] = mapped_column(Text)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_cls_audit_ticker", "ticker"),
        Index("idx_cls_audit_run", "sweep_run_id"),
        Index("idx_cls_audit_source", "source"),
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
    ~720 products across 8 suites, tracked through the collapsed 6-state
    lifecycle adopted 2026-05-12:

      Under Consideration → Filed → Effective → Target List → Listed → Delisted

    The granular Counsel / Board / 485A-vs-485B distinctions previously
    encoded in this column now live in (a) ``latest_form`` for the SEC
    form-type detail and (b) the ``capm_audit_log`` for the historical
    stage progression. See scripts/migrate_rex_status_2026-05-12.py.
    """
    __tablename__ = "rex_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    trust: Mapped[str | None] = mapped_column(String(200))
    product_suite: Mapped[str] = mapped_column(String(50), nullable=False)  # T-REX, IncomeMax, Premium Income, etc.
    status: Mapped[str] = mapped_column(String(30), nullable=False)  # Under Consideration, Target List, Filed, Effective, Listed, Delisted
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
    # JSON-encoded list of field names that have been manually overridden by
    # an admin via /admin/rex-products/update/{id}. The daily classifier +
    # bloomberg-chain sweeps consult this to avoid clobbering admin edits.
    manually_edited_fields: Mapped[str | None] = mapped_column(Text)
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


class CapMTrustAP(Base):
    """Capital Markets — Trust and Authorized Participants registry.

    One row per (trust, AP) pair. The source Excel sheet ("Trust & APs")
    lays trusts out as four side-by-side columns, each with that trust's
    authorized participants listed vertically. We normalize that into a
    long-format table so filtering/sorting on the web is trivial.
    """
    __tablename__ = "capm_trust_aps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trust_name: Mapped[str] = mapped_column(String(200), nullable=False)
    ap_name: Mapped[str | None] = mapped_column(String(200))
    sort_order: Mapped[int | None] = mapped_column(Integer)  # position within the trust's AP list
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("trust_name", "ap_name", name="uq_capm_trust_ap"),
        Index("idx_capm_trust_name", "trust_name"),
    )


class CapMProduct(Base):
    """Capital Markets product list — operational and classification data."""
    __tablename__ = "capm_products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    fund_name: Mapped[str] = mapped_column(String(300), nullable=False)
    ticker: Mapped[str | None] = mapped_column(String(20))
    bb_ticker: Mapped[str | None] = mapped_column(String(30))
    inception_date: Mapped[date | None] = mapped_column(Date)
    trust: Mapped[str | None] = mapped_column(String(200))
    issuer: Mapped[str | None] = mapped_column(String(200))
    exchange: Mapped[str | None] = mapped_column(String(20))
    cu_size: Mapped[str | None] = mapped_column(String(20))
    fixed_fee: Mapped[str | None] = mapped_column(String(20))
    variable_fee: Mapped[str | None] = mapped_column(String(50))
    cut_off: Mapped[str | None] = mapped_column(String(20))
    custodian: Mapped[str | None] = mapped_column(String(100))
    lmm: Mapped[str | None] = mapped_column(String(100))
    prospectus_link: Mapped[str | None] = mapped_column(Text)
    suite_source: Mapped[str | None] = mapped_column(String(30))
    our_category: Mapped[str | None] = mapped_column(String(50))
    product_type: Mapped[str | None] = mapped_column(String(50))
    category: Mapped[str | None] = mapped_column(String(50))
    sub_category: Mapped[str | None] = mapped_column(String(50))
    direction: Mapped[str | None] = mapped_column(String(20))
    leverage: Mapped[str | None] = mapped_column(String(10))
    underlying_ticker: Mapped[str | None] = mapped_column(String(50))
    underlying_name: Mapped[str | None] = mapped_column(String(300))
    expense_ratio: Mapped[float | None] = mapped_column(Float)
    competitor_products: Mapped[str | None] = mapped_column(Text)
    bmo_suite: Mapped[str | None] = mapped_column(String(50))
    notes: Mapped[str | None] = mapped_column(Text)
    # Manual-override tracking: JSON list of field names that have been
    # manually edited via the admin inline editor. The daily import_capm.py
    # script must skip these fields when reconciling fresh xlsx data so
    # human edits are not silently overwritten.
    manually_edited_fields: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_capm_ticker", "ticker"),
        Index("idx_capm_suite", "suite_source"),
    )


class CapMAuditLog(Base):
    """Append-only audit log for admin write actions on CapM tables.

    Wired by capm.py update / add / delete endpoints. Surfaced at the bottom
    of /operations/products as the "Activity Log" section so Ryu can see
    every change at a glance — guards against accidental data loss.
    """
    __tablename__ = "capm_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # ADD / UPDATE / DELETE
    table_name: Mapped[str] = mapped_column(String(50), nullable=False)  # capm_products / capm_trust_aps
    row_id: Mapped[int | None] = mapped_column(Integer)
    field_name: Mapped[str | None] = mapped_column(String(100))
    old_value: Mapped[str | None] = mapped_column(Text)
    new_value: Mapped[str | None] = mapped_column(Text)
    row_label: Mapped[str | None] = mapped_column(String(200))  # ticker or trust name for human readability
    changed_by: Mapped[str | None] = mapped_column(String(100))  # session user or "admin"
    changed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_capm_audit_changed_at", "changed_at"),
        Index("idx_capm_audit_table_row", "table_name", "row_id"),
    )


class ReservedSymbol(Base):
    """REX's own ticker reservations with exchanges (Cboe / NYSE / etc.).

    Source: C:/Users/RyuEl-Asmar/Downloads/Reserved Symbols.xlsx (Master sheet,
    ~283 rows). Tracks ticker, exchange, expiration, status, rationale, suite.

    Distinct from CboeSymbol (which is the full 475K-ticker CBOE universe scan
    showing who-has-what across all issuers). This table is REX's CURATED list
    of symbols we've personally reserved, with our internal metadata.

    Goal (per Ryu 2026-05-11): map REX's reserved tickers against our filings
    so we know which products are coming next, and integrate with the Symbol
    Landscape tool to suggest new reservations using our 3-axis taxonomy.
    """
    __tablename__ = "reserved_symbols"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    exchange: Mapped[str | None] = mapped_column(String(20))      # Cboe / NYSE
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)           # reservation expiration
    status: Mapped[str | None] = mapped_column(String(30))        # Reserved / Active / Expired / etc.
    rationale: Mapped[str | None] = mapped_column(Text)           # Rationale for Reservation
    suite: Mapped[str | None] = mapped_column(String(50))         # Meme / Crypto / Leverage / etc.
    # Linkage (filled later by mapping work)
    linked_filing_id: Mapped[int | None] = mapped_column(Integer)      # if mapped to a rex_products filing
    linked_product_id: Mapped[int | None] = mapped_column(Integer)     # if assigned to a live product
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("exchange", "symbol", name="uq_reserved_symbol_exchange"),
        Index("idx_reserved_symbol", "symbol"),
        Index("idx_reserved_status", "status"),
        Index("idx_reserved_suite", "suite"),
    )


# --- CBOE Symbol Reservation Models ---
# Tracks availability of 1-4 letter tickers on CBOE's issuer-only symbol
# reservation portal. Reserved-but-not-active tickers = competitor pipeline intel.

class CboeSymbol(Base):
    """A ticker in CBOE's symbol-reservation universe.

    One row per 1-4 letter uppercase combo observed by the scanner. `available`
    is tri-state: null = never scanned, True = free to reserve, False = active
    or already reserved. The market_data join (ticker -> mkt_master_data.ticker)
    tells us *why* unavailable symbols are taken.
    """
    __tablename__ = "cboe_symbols"

    ticker: Mapped[str] = mapped_column(String(4), primary_key=True)
    length: Mapped[int] = mapped_column(Integer, nullable=False)
    available: Mapped[bool | None] = mapped_column(Boolean)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_seen_available_at: Mapped[datetime | None] = mapped_column(DateTime)
    first_seen_taken_at: Mapped[datetime | None] = mapped_column(DateTime)
    state_change_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_cboe_symbols_available_length", "available", "length"),
        Index("idx_cboe_symbols_last_checked", "last_checked_at"),
    )


class CboeStateChange(Base):
    """Append-only log of availability flips. Feeds the "Recent changes" UI."""
    __tablename__ = "cboe_state_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(4), ForeignKey("cboe_symbols.ticker"), nullable=False)
    old_state: Mapped[str] = mapped_column(String(12), nullable=False)  # available | taken | unknown
    new_state: Mapped[str] = mapped_column(String(12), nullable=False)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_cboe_state_changes_ticker", "ticker"),
        Index("idx_cboe_state_changes_detected", "detected_at"),
    )


class CboeScanRun(Base):
    """One row per scan run. Used to resume interrupted scans and to show
    "last scan: Nh ago" on the UI."""
    __tablename__ = "cboe_scan_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="running", nullable=False)  # running | completed | failed
    tier: Mapped[str | None] = mapped_column(String(30))  # 1-letter | 2-letter | 3-letter | daily | 4-letter-batch | full
    tickers_checked: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    state_changes_detected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_ticker_scanned: Mapped[str | None] = mapped_column(String(4))
    concurrency: Mapped[int | None] = mapped_column(Integer)
    error_message: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("idx_cboe_scan_runs_started", "started_at"),
    )


class CboeKnownActive(Base):
    """All US-listed securities pulled from public sources (NASDAQ screeners,
    NASDAQ Trader symbol files, SEC EDGAR). Used to split CBOE "taken" rows
    into truly-active listings vs reservations-without-listings (the intel).

    `base_ticker` is the alpha-only prefix (1-4 chars) used to join against
    cboe_symbols.ticker. Tickers whose base alpha exceeds 4 chars are skipped
    (CBOE only exposes 1-4 letter symbols)."""
    __tablename__ = "cboe_known_active"

    full_ticker: Mapped[str] = mapped_column(String(20), primary_key=True)
    base_ticker: Mapped[str] = mapped_column(String(4), nullable=False)
    name: Mapped[str | None] = mapped_column(String(300))
    sec_type: Mapped[str | None] = mapped_column(String(20))  # stock | etf | other
    exchange: Mapped[str | None] = mapped_column(String(50))
    sector: Mapped[str | None] = mapped_column(String(100))
    industry: Mapped[str | None] = mapped_column(String(100))
    market_cap: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    refreshed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_cboe_known_active_base", "base_ticker"),
        Index("idx_cboe_known_active_source", "source"),
    )


class LiveFeedItem(LiveFeedBase):
    """Rolling real-time feed of new filings surfaced by the atom watcher.

    Lives in a DEDICATED database file (data/live_feed.db) so the daily
    DB upload (which replaces etp_tracker.db wholesale) cannot wipe it.
    That dedicated DB is managed by init_live_feed_db() / LiveFeedBase /
    live_feed_engine in webapp/database.py.

    VPS single_filing_worker POSTs each successful enrichment to
    /api/v1/live/push on Render, which UPSERTs one row here.
    Browsers poll /api/v1/live/recent?since=<ts> every 30s and toast
    any new items.
    """
    __tablename__ = "live_feed"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    accession_number: Mapped[str] = mapped_column(String(30), unique=True, nullable=False)
    cik: Mapped[str | None] = mapped_column(String(20))
    form: Mapped[str] = mapped_column(String(20), nullable=False)
    company_name: Mapped[str | None] = mapped_column(String(200))
    trust_id: Mapped[int | None] = mapped_column(Integer)  # no FK — trust may live in Render DB only, or not at all
    trust_slug: Mapped[str | None] = mapped_column(String(100))
    trust_name: Mapped[str | None] = mapped_column(String(200))
    filed_date: Mapped[date | None] = mapped_column(Date)
    primary_doc_url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(String(30))  # atom | reconciler | bulk

    __table_args__ = (
        Index("idx_live_feed_detected", "detected_at"),
        Index("idx_live_feed_trust", "trust_id"),
    )


# ---------------------------------------------------------------------------
# Autocall simulator (single-page tool under /notes/tools/autocall)
# ---------------------------------------------------------------------------

class AutocallIndexMetadata(Base):
    """One row per reference index. `category` controls dropdown visibility:
    'underlying' and 'strategy_underlying' are selectable as refs;
    'autocall_product' is loaded for completeness but hidden from pickers
    (those indices ARE autocall products, not valid worst-of references).
    """
    __tablename__ = "autocall_index_metadata"

    ticker: Mapped[str] = mapped_column(String(40), primary_key=True)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    short_name: Mapped[str] = mapped_column(String(80), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AutocallIndexLevel(Base):
    """Daily close level per (date, ticker). Long format.
    Pre-inception cells in the source xlsx ('#N/A') are NOT inserted."""
    __tablename__ = "autocall_index_levels"

    date: Mapped[date] = mapped_column(Date, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(40), primary_key=True)
    level: Mapped[float] = mapped_column(Float, nullable=False)

    __table_args__ = (
        Index("idx_autocall_levels_ticker_date", "ticker", "date"),
    )


class AutocallCrisisPreset(Base):
    """Quick-pick buttons for the issue-date scrubber."""
    __tablename__ = "autocall_crisis_presets"

    name: Mapped[str] = mapped_column(String(60), primary_key=True)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class AutocallSweepCache(Base):
    """Memoized distribution-sweep results.

    Key = sha256 of (refs + tenor + freq + barriers + memory + no_call).
    Wiped whenever fresh index data is loaded (admin reload endpoint).
    """
    __tablename__ = "autocall_sweep_cache"

    params_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)


class RecommendationHistory(Base):
    """Append-only log of every weekly stock recommendation we surface.

    Wave E1 (2026-05-11). Separate namespace (`stockrec_*` columns are absent;
    table is named `recommendation_history` so we don't collide with the
    `mkt_*` market-data namespace). One row per (week, ticker, tier).

    Lifecycle:
      1. Each weekly_v2 render appends rows for every recommendation in
         that build (HIGH/MEDIUM/WATCH tiers across launch + filing
         sections). `outcome_status` starts as NULL.
      2. The grading job (`scripts/grade_recommendations.py`) walks rows
         where `outcome_status IS NULL` (or where the previous grade was
         not yet terminal) and updates the outcome columns by inspecting
         the current state of `filings`, `mkt_master_data`, `mkt_time_series`.
      3. Hit-rate / track-record dashboards read from this table and
         compute aggregates (rolling 90d HIGH-confidence hit rate,
         average AUM 6mo post-launch, tier accuracy).

    Idempotency rules:
      - Inserts: enforced by UNIQUE(week_of, ticker, confidence_tier).
        A rerun of the weekly job for the same week is a no-op.
      - Grading: each pass overwrites `outcome_*` with the latest
        observation; `graded_at` is bumped each time. Terminal statuses
        (launched/killed/abandoned) are sticky once set — the grader
        only refines `outcome_aum_*` for them, never reverts the status.
    """
    __tablename__ = "recommendation_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # When the row was written (UTC timestamp at append).
    generated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    # ISO date of the Monday of the week the report covers (week-stable key).
    week_of: Mapped[date] = mapped_column(Date, nullable=False)
    # Underlier ticker (e.g. "NVDA"); always upper-cased, no Bloomberg suffix.
    ticker: Mapped[str] = mapped_column(String(30), nullable=False)
    # Best-effort company / fund name at the time of recommendation.
    fund_name: Mapped[str | None] = mapped_column(String(300))
    # HIGH | MEDIUM | WATCH (string for forward-compat).
    confidence_tier: Mapped[str] = mapped_column(String(10), nullable=False)
    # Composite score from the v4 whitespace / launch scorer at append time.
    composite_score: Mapped[float | None] = mapped_column(Float)
    # First ~280 chars of the rendered thesis line — enough for audit
    # without bloating the table.
    thesis_snippet: Mapped[str | None] = mapped_column(Text)
    # If the recommendation pointed to a specific REX product (launch
    # candidate), the suggested ticker. Whitespace recs leave this NULL.
    suggested_rex_ticker: Mapped[str | None] = mapped_column(String(30))
    # Which section the rec came from: "launch" | "filing" | "money_flow".
    section: Mapped[str | None] = mapped_column(String(20))

    # ----- Outcome columns (filled by the grading job) -----
    # rex_filed | competitor_filed | abandoned | launched | killed | NULL
    outcome_status: Mapped[str | None] = mapped_column(String(20))
    # When the outcome was first observed (UTC).
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime)
    # AUM of the launched product at +6mo and +12mo (NULL until matured).
    outcome_aum_6mo: Mapped[float | None] = mapped_column(Float)
    outcome_aum_12mo: Mapped[float | None] = mapped_column(Float)
    # Last time the grader touched this row.
    graded_at: Mapped[datetime | None] = mapped_column(DateTime)
    # Optional ticker of the launched/filed product the grader matched.
    matched_product_ticker: Mapped[str | None] = mapped_column(String(30))
    # Free-form note from the grader (e.g. "matched on map_li_underlier").
    grading_note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint(
            "week_of", "ticker", "confidence_tier",
            name="uq_recommendation_history_week_ticker_tier",
        ),
        Index("idx_rec_history_ticker", "ticker"),
        Index("idx_rec_history_week", "week_of"),
        Index("idx_rec_history_tier_status", "confidence_tier", "outcome_status"),
        Index("idx_rec_history_generated", "generated_at"),
    )


class ApiAuditLog(Base):
    """Append-only audit log for sensitive API endpoints.

    Wired by /api/v1/db/upload (and /db/upload-notes) so every database swap
    is recorded with the source IP, payload size, and outcome. The log is
    intentionally schema-light so it can absorb other endpoints later
    (parquet uploads, prebaked report uploads) without migration.
    """
    __tablename__ = "api_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    route: Mapped[str] = mapped_column(String(120), nullable=False)
    method: Mapped[str] = mapped_column(String(10), nullable=False)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(500))
    success: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status_code: Mapped[int | None] = mapped_column(Integer)
    payload_size: Mapped[int | None] = mapped_column(Integer)  # bytes
    detail: Mapped[str | None] = mapped_column(Text)            # error message or note
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    __table_args__ = (
        Index("idx_api_audit_route_created", "route", "created_at"),
        Index("idx_api_audit_ip_created", "ip", "created_at"),
    )


class ReservedSymbolAuditLog(Base):
    """Append-only audit log for admin writes on the reserved_symbols table.

    Mirrors the CapMAuditLog shape but collapses per-field rows into a single
    `changes` JSON blob (e.g. {"status": ["Reserved", "Active"], ...}). One
    audit row per ADD / UPDATE / DELETE action, regardless of how many fields
    moved — keeps the audit feed readable on the /operations/reserved-symbols
    page.

    Defaults are stamped UTC by Python (datetime.utcnow); display layers
    render in ET (the systemd unit pins TZ=America/New_York for the VPS).
    """
    __tablename__ = "reserved_symbols_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    reserved_symbol_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("reserved_symbols.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(20), nullable=False)  # ADD / UPDATE / DELETE
    changes: Mapped[str | None] = mapped_column(Text)  # JSON: {field: [old, new], ...} or full row snapshot
    actor: Mapped[str | None] = mapped_column(String(100))  # session user or "admin"
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )

    __table_args__ = (
        Index("idx_reserved_audit_created_at", "created_at"),
        Index("idx_reserved_audit_symbol", "reserved_symbol_id"),
        Index("idx_reserved_audit_action", "action"),
    )


class RexProductStatusHistory(Base):
    """Append-only history of `rex_products.status` transitions.

    Captures every status change so we can reconstruct a fund's pipeline
    journey (Under Consideration → Target List → Filed → Effective → Listed
    → Delisted) — the per-row equivalent of the bulk classification audit
    log. Populated by code paths that mutate ``rex_products.status``
    (admin edits, pipeline reconciler, manual sweeps).

    Also used as the re-home target for the 21 stranded T-REX 2X rows
    previously written to ``classification_audit_log`` under
    ``sweep_run_id='manual_2026-05-09_trex2x'``.
    """
    __tablename__ = "rex_product_status_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    rex_product_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("rex_products.id", ondelete="SET NULL")
    )
    old_status: Mapped[str | None] = mapped_column(String(30))
    new_status: Mapped[str | None] = mapped_column(String(30))
    changed_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False,
    )
    changed_by: Mapped[str | None] = mapped_column(String(100))
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        Index("idx_rex_status_hist_product", "rex_product_id"),
        Index("idx_rex_status_hist_changed_at", "changed_at"),
    )
