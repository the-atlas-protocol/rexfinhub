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


# --- Live feed: separate database that survives main-DB swaps ---
#
# Why separate: the daily /api/v1/db/upload replaces etp_tracker.db wholesale.
# If live_feed lives in that DB, every upload wipes the rolling feed. A
# dedicated file avoids that — the daily upload only touches etp_tracker.db,
# so live_feed.db keeps its rows across uploads.
LIVE_FEED_DB_PATH = PROJECT_ROOT / "data" / "live_feed.db"

live_feed_engine = create_engine(
    f"sqlite:///{LIVE_FEED_DB_PATH}",
    connect_args={"check_same_thread": False},
    echo=False,
)


@event.listens_for(live_feed_engine, "connect")
def _set_live_feed_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


LiveFeedSessionLocal = sessionmaker(bind=live_feed_engine)


class LiveFeedBase(DeclarativeBase):
    pass


def init_live_feed_db():
    """Create the live_feed table in its dedicated database file."""
    LIVE_FEED_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    from webapp.models import LiveFeedItem  # noqa: F401 — registers the table
    LiveFeedBase.metadata.create_all(bind=live_feed_engine)


def get_live_feed_db():
    """FastAPI dependency: yields a live-feed DB session, auto-closes."""
    db = LiveFeedSessionLocal()
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
        NameHistory, AnalysisResult, FilingAnalysis, PipelineRun,
        MktPipelineRun, MktFundMapping, MktIssuerMapping,
        MktCategoryAttributes, MktExclusion, MktRexFund,
        MktMasterData, MktTimeSeries, MktReportCache, MktStockData,
        MktFundClassification, MktMarketStatus, MktGlobalEtp,
        TrustRequest, DigestSubscriber,
        FilingAlert, TrustCandidate,
        CapMProduct, CapMTrustAP, CapMAuditLog,
        CboeSymbol, CboeStateChange, CboeScanRun, CboeKnownActive,
        AutocallIndexMetadata, AutocallIndexLevel,
        AutocallCrisisPreset, AutocallSweepCache,
        RecommendationHistory,  # Wave E1 (2026-05-11) — stock-rec self-grading
    )
    Base.metadata.create_all(bind=engine)
    _migrate_missing_columns()
    _autocall_seed_if_empty()
    _capm_seed_if_empty()


def _capm_seed_if_empty():
    """Seed capm_products and capm_trust_aps from bundled CSVs if empty.

    Background: capm_products is curated locally (Excel import on Ryu's
    desktop) and is NOT regenerated by the VPS daily pipeline. When the
    VPS uploads its DB to Render, the freshly-swapped DB has zero capm
    rows — wiping the /operations/products page until Ryu manually
    re-uploads from desktop. Auto-seeding from a checked-in CSV makes
    the page survive any DB swap (VPS upload, deploy reset, etc.).

    Idempotent: only inserts when the table is empty.

    Audit R5 (2026-05-11): also bail out if the capm_audit_log has any
    entries — a non-empty audit trail means an admin has made manual
    edits that we must NOT silently overwrite by re-seeding from the
    checked-in CSV. If the table got truncated AND there is an audit
    history, the DB is in an inconsistent state and a human should
    handle it explicitly rather than have the seeder paper over it.
    """
    import csv as _csv
    import logging as _log_m
    _l = _log_m.getLogger(__name__)

    # Guard: if capm_audit_log has any entries, do NOT reseed silently.
    try:
        import sqlite3
        _conn = sqlite3.connect(str(DB_PATH))
        try:
            _cur = _conn.cursor()
            # Confirm table exists before querying.
            _cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='capm_audit_log'"
            )
            if _cur.fetchone():
                _cur.execute("SELECT COUNT(*) FROM capm_audit_log")
                _audit_count = (_cur.fetchone() or [0])[0]
                if _audit_count > 0:
                    _l.info(
                        "CapM seed skipped: capm_audit_log has %d entries — "
                        "refusing to overwrite admin edits. If the data is "
                        "actually missing, restore from backup or reseed manually.",
                        _audit_count,
                    )
                    return
        finally:
            _conn.close()
    except Exception as _e:
        _l.warning("CapM seed: audit-log guard check failed (proceeding): %s", _e)

    seeds = [
        ("capm_products.csv", "capm_products"),
        ("capm_trust_aps.csv", "capm_trust_aps"),
    ]
    for fname, tname in seeds:
        csv_path = PROJECT_ROOT / "webapp" / "data_static" / fname
        if not csv_path.exists():
            continue
        try:
            import sqlite3
            conn = sqlite3.connect(str(DB_PATH))
            try:
                cur = conn.cursor()
                # Skip if already populated — the uploaded DB is authoritative.
                cur.execute(f"SELECT COUNT(*) FROM {tname}")
                if (cur.fetchone() or [0])[0] > 0:
                    continue
                # created_at / updated_at are NOT NULL with Python-side
                # defaults (datetime.utcnow), so a raw-SQL INSERT must
                # provide them explicitly. Stamp every seeded row with
                # the current UTC timestamp.
                from datetime import datetime as _dt
                now = _dt.utcnow().isoformat(sep=" ", timespec="microseconds")
                with open(csv_path, "r", encoding="utf-8", newline="") as f:
                    reader = _csv.reader(f)
                    headers = next(reader, None)
                    if not headers:
                        continue
                    full_headers = headers + ["created_at", "updated_at"]
                    placeholders = ",".join("?" for _ in full_headers)
                    cols = ",".join(f"[{h}]" for h in full_headers)
                    sql = f"INSERT INTO {tname} ({cols}) VALUES ({placeholders})"
                    rows = [
                        tuple(v if v != "" else None for v in row) + (now, now)
                        for row in reader
                    ]
                    cur.executemany(sql, rows)
                    conn.commit()
                _l.info("Seeded %s with %d rows from %s", tname, len(rows), fname)
            finally:
                conn.close()
        except Exception as e:
            _l.warning("CapM seed failed for %s (non-fatal): %s", tname, e)


def _autocall_seed_if_empty():
    """Seed autocall_* tables from the bundled CSV if they're empty.

    Runs on every startup but only inserts when the levels table is empty
    (idempotent). Lets Render pick up the dataset without a manual upload.
    """
    import logging as _log_m
    _l = _log_m.getLogger(__name__)
    csv_path = PROJECT_ROOT / "webapp" / "data_static" / "autocall_index_levels.csv"
    if not csv_path.exists():
        return
    try:
        from webapp.models import AutocallIndexLevel
        from webapp.services.autocall_data_loader import load
        from sqlalchemy.orm import Session as _S
        with _S(engine) as s:
            n = s.query(AutocallIndexLevel).limit(1).count()
            if n > 0:
                return
        with _S(engine) as s:
            summary = load(csv_path, s)
        _l.info("Autocall data seeded: %d rows, %d tickers, %s -> %s",
                summary["rows"], summary["tickers"],
                summary["date_min"], summary["date_max"])
    except Exception as e:
        _l.warning("Autocall seed failed (non-fatal): %s", e)


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
