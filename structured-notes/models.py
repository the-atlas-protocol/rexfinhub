"""SQLAlchemy models for structured notes."""
from datetime import date, datetime
from sqlalchemy import String, Integer, Float, Boolean, Date, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


class Issuer(Base):
    __tablename__ = "issuers"

    id: Mapped[int] = mapped_column(primary_key=True)
    cik: Mapped[str] = mapped_column(String(10), unique=True, index=True)
    short_name: Mapped[str] = mapped_column(String(50))
    full_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="confirmed")  # confirmed / pending
    total_filings: Mapped[int] = mapped_column(Integer, default=0)
    filings_extracted: Mapped[int] = mapped_column(Integer, default=0)
    last_updated: Mapped[datetime | None] = mapped_column(DateTime)

    filings: Mapped[list["Filing"]] = relationship(back_populates="issuer")


class Filing(Base):
    __tablename__ = "filings"

    id: Mapped[int] = mapped_column(primary_key=True)
    issuer_id: Mapped[int] = mapped_column(ForeignKey("issuers.id"), index=True)
    accession_number: Mapped[str] = mapped_column(String(30), unique=True, index=True)
    filing_date: Mapped[date] = mapped_column(Date, index=True)
    form_type: Mapped[str] = mapped_column(String(20))
    primary_doc_url: Mapped[str] = mapped_column(String(500))
    is_inline_xbrl: Mapped[bool] = mapped_column(Boolean, default=False)
    extracted: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    extraction_error: Mapped[str | None] = mapped_column(String(500))

    issuer: Mapped["Issuer"] = relationship(back_populates="filings")
    products: Mapped[list["Product"]] = relationship(back_populates="filing")


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    filing_id: Mapped[int] = mapped_column(ForeignKey("filings.id"), index=True)
    parent_issuer: Mapped[str | None] = mapped_column(String(50))

    # Identifiers
    cusip: Mapped[str | None] = mapped_column(String(9), index=True)
    isin: Mapped[str | None] = mapped_column(String(12))

    # Product details
    product_name: Mapped[str | None] = mapped_column(Text)
    product_type: Mapped[str | None] = mapped_column(String(30), index=True)
    product_subtype: Mapped[str | None] = mapped_column(String(50))  # Finer classification: "phoenix", "worst-of", "PLUS", etc.
    is_preliminary: Mapped[bool] = mapped_column(Boolean, default=False)
    underlier_count: Mapped[int | None] = mapped_column(Integer)

    # Underlier details
    underlier_names: Mapped[str | None] = mapped_column(Text)  # Comma-separated: "AAPL, MSFT, AMZN"
    underlier_tickers: Mapped[str | None] = mapped_column(Text)  # Normalized tickers
    underlier_type: Mapped[str | None] = mapped_column(String(30))  # equity, index, etf, commodity, rate, mixed

    # Financial terms
    notional_amount: Mapped[float | None] = mapped_column(Float)
    denomination: Mapped[float | None] = mapped_column(Float)

    # Dates
    pricing_date: Mapped[date | None] = mapped_column(Date)
    settlement_date: Mapped[date | None] = mapped_column(Date)

    maturity_date: Mapped[date | None] = mapped_column(Date)
    coupon_rate: Mapped[float | None] = mapped_column(Float)  # Annualized decimal (0.08 = 8%)
    coupon_type: Mapped[str | None] = mapped_column(String(20))
    coupon_frequency: Mapped[str | None] = mapped_column(String(20))
    barrier_level: Mapped[float | None] = mapped_column(Float)  # Decimal (0.70 = 70% of initial)
    barrier_type: Mapped[str | None] = mapped_column(String(30))

    # Call structure (autocallables)
    call_premium: Mapped[float | None] = mapped_column(Float)  # One-time call return (decimal, 0.10 = 10%)
    call_level: Mapped[float | None] = mapped_column(Float)  # Auto-call trigger (decimal, 1.0 = 100% of initial)
    first_call_date: Mapped[date | None] = mapped_column(Date)
    call_frequency: Mapped[str | None] = mapped_column(String(20))  # quarterly, monthly, etc.
    is_memory_coupon: Mapped[bool] = mapped_column(Boolean, default=False)

    # Participation structure (growth/leveraged notes)
    participation_rate: Mapped[float | None] = mapped_column(Float)  # Upside multiplier (1.5 = 150%)
    upside_cap: Mapped[float | None] = mapped_column(Float)  # Max return (decimal, 0.20 = 20% cap)
    downside_leverage: Mapped[float | None] = mapped_column(Float)  # Downside multiplier if applicable

    # Classification
    asset_class: Mapped[str | None] = mapped_column(String(20), index=True)  # structured_note, etn, mtn, shelf, other

    # Prelim/final linkage
    linked_final_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("products.id"), index=True
    )  # For preliminary products: points to the corresponding final product

    # Quality
    confidence: Mapped[float | None] = mapped_column(Float)
    extraction_date: Mapped[datetime | None] = mapped_column(DateTime)

    filing: Mapped["Filing"] = relationship(back_populates="products")
    linked_final: Mapped["Product | None"] = relationship(
        remote_side="Product.id", foreign_keys="Product.linked_final_id"
    )
