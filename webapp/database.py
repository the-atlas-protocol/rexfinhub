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
    # busy_timeout: when the DB is briefly locked (e.g. during the final
    # commit of Connection.backup() in the upload endpoint), readers retry
    # for up to 30s instead of raising SQLITE_BUSY. This keeps the webapp
    # serving normally during in-place DB swaps.
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


# --- 13F Holdings: separate database (local dev only) ---
HOLDINGS_DB_PATH = PROJECT_ROOT / "data" / "13f_holdings.db"

holdings_engine = create_engine(
    f"sqlite:///{HOLDINGS_DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(holdings_engine, "connect")
def _set_holdings_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA busy_timeout=30000")
    # Attach main site DB read-only so cross-DB joins work
    # (e.g. Holding JOIN MktMasterData, Trust, FundStatus)
    main_path = str(DB_PATH).replace("\\", "/")
    cursor.execute(f"ATTACH DATABASE '{main_path}' AS main_site")
    cursor.close()


HoldingsSessionLocal = sessionmaker(bind=holdings_engine)


class HoldingsBase(DeclarativeBase):
    pass


def init_holdings_db():
    """Create all 13F tables in the separate holdings database."""
    HOLDINGS_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    from webapp.models import Institution, Holding, CusipMapping  # noqa: F401
    HoldingsBase.metadata.create_all(bind=holdings_engine)


def get_holdings_db():
    """FastAPI dependency: yields a 13F holdings DB session, auto-closes."""
    db = HoldingsSessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables if they don't exist, and migrate missing columns."""
    import logging
    _log = logging.getLogger(__name__)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    # Integrity check on existing DB (fast — ~50ms for 1GB DB)
    if DB_PATH.exists():
        import sqlite3
        try:
            conn = sqlite3.connect(str(DB_PATH))
            result = conn.execute("PRAGMA quick_check").fetchone()
            conn.close()
            if result[0] != "ok":
                _log.error("DATABASE INTEGRITY CHECK FAILED: %s", result[0])
        except Exception as e:
            _log.error("Database integrity check error: %s", e)
    from webapp.models import (  # noqa: F401 - import to register models
        Trust, Filing, FundExtraction, FundStatus,
        NameHistory, AnalysisResult, PipelineRun,
        MktPipelineRun, MktFundMapping, MktIssuerMapping,
        MktCategoryAttributes, MktExclusion, MktRexFund,
        MktMasterData, MktTimeSeries, MktReportCache, MktStockData,
        MktFundClassification, MktMarketStatus, MktGlobalEtp,
        TrustRequest, DigestSubscriber,
        FilingAlert, TrustCandidate, LiveFeedItem,
    )
    Base.metadata.create_all(bind=engine)
    _migrate_missing_columns()


def _migrate_missing_columns():
    """Add columns that exist in models but not yet in SQLite tables.

    SQLAlchemy create_all() only creates new tables, not new columns on
    existing ones. This runs ALTER TABLE ADD COLUMN for anything missing.
    """
    import sqlite3
    conn = sqlite3.connect(str(DB_PATH))
    try:
        cur = conn.cursor()
        for table in Base.metadata.sorted_tables:
            # Get existing columns in the SQLite table
            cur.execute(f"PRAGMA table_info('{table.name}')")
            existing = {row[1] for row in cur.fetchall()}
            if not existing:
                continue  # table doesn't exist yet (create_all handles it)
            # Add any missing columns
            for col in table.columns:
                if col.name not in existing:
                    col_type = col.type.compile(dialect=engine.dialect)
                    try:
                        cur.execute(
                            f"ALTER TABLE '{table.name}' ADD COLUMN "
                            f"'{col.name}' {col_type}"
                        )
                    except sqlite3.OperationalError:
                        pass  # column already exists (race condition)
        conn.commit()
    finally:
        conn.close()


def get_db():
    """FastAPI dependency: yields a DB session, auto-closes."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
