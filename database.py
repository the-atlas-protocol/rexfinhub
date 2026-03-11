"""Database setup."""
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from config import DB_PATH

class Base(DeclarativeBase):
    pass

_engine = None
SessionLocal = None

def init_db():
    global _engine, SessionLocal
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    _engine = create_engine(f"sqlite:///{DB_PATH}", echo=False)
    SessionLocal = sessionmaker(bind=_engine)
    Base.metadata.create_all(_engine)

def get_db():
    init_db()
    return SessionLocal()
