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
    is_preliminary: Mapped[bool] = mapped_column(Boolean, default=False)
    underlier_count: Mapped[int | None] = mapped_column(Integer)

    # Financial terms
    notional_amount: Mapped[float | None] = mapped_column(Float)
    denomination: Mapped[float | None] = mapped_column(Float)
    maturity_date: Mapped[date | None] = mapped_column(Date)
    coupon_rate: Mapped[float | None] = mapped_column(Float)  # Annualized decimal (0.08 = 8%)
    coupon_type: Mapped[str | None] = mapped_column(String(20))
    coupon_frequency: Mapped[str | None] = mapped_column(String(20))
    barrier_level: Mapped[float | None] = mapped_column(Float)  # Decimal (0.70 = 70% of initial)
    barrier_type: Mapped[str | None] = mapped_column(String(30))

    # Quality
    confidence: Mapped[float | None] = mapped_column(Float)
    extraction_date: Mapped[datetime | None] = mapped_column(DateTime)

    filing: Mapped["Filing"] = relationship(back_populates="products")
