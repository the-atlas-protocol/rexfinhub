"""Database setup."""
from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from config import DB_PATH

class Base(DeclarativeBase):
    pass

_engine = None
SessionLocal = None

def _set_sqlite_pragma(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")  # Wait 30s on locks instead of failing
    cursor.close()

def init_db():
    global _engine, SessionLocal
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(
        f"sqlite:///{DB_PATH}?timeout=30",
        echo=False,
        connect_args={"check_same_thread": False},
    )
    event.listen(_engine, "connect", _set_sqlite_pragma)
    SessionLocal = sessionmaker(bind=_engine)
    Base.metadata.create_all(_engine)

def get_db():
    init_db()
    return SessionLocal()
