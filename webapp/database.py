"""
Database module - SQLite via SQLAlchemy 2.0

Provides engine, session factory, and Base class for ORM models.
Database file lives at data/etp_tracker.db (relative to project root).
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False,
)


# Enable WAL mode for concurrent read+write
@event.listens_for(engine, "connect")
def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def init_db():
    """Create all tables if they don't exist."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    from webapp.models import (  # noqa: F401 - import to register models
        Trust, Filing, FundExtraction, FundStatus,
        NameHistory, AnalysisResult, PipelineRun,
        MktPipelineRun, MktFundMapping, MktIssuerMapping,
        MktCategoryAttributes, MktExclusion, MktRexFund,
        MktMasterData, MktTimeSeries, MktStockData,
        MktFundClassification, MktMarketStatus,
    )
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency: yields a DB session, auto-closes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
