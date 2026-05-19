"""Phase 6 Stage 3 (ADR 0009): override-first classification resolver.

Wraps the legacy classifier. Resolution priority per field:
    1. classification_override (admin/manual wins absolutely)
    2. Bloomberg classification (mkt_master_data has the field)
    3. Auto-classifier fallback (existing rule-based logic)

Reads from `classification_override` table populated by Phase 6 Stages 1+2
(486 rows migrated from the 7 rule CSVs on 2026-05-19).

NULL values in `classification_override` mean "explicit blacklist — never
classify this field for this product, even if auto would". The resolver
returns None and the caller honors it.

Usage:
    from webapp.services.classification_resolver import resolve_field
    val = resolve_field(canonical_id, "etp_category", db=db)
"""
from __future__ import annotations

import logging
import sqlite3
from typing import Any

log = logging.getLogger(__name__)


_SENTINEL = object()  # distinguishes "blacklist" (None) from "no override"


def get_override(
    conn: sqlite3.Connection,
    canonical_id: str,
    field_name: str,
) -> Any:
    """Return the override value for (canonical_id, field_name).

    Returns:
        - The string value if an override exists with non-NULL value
        - None if an override exists with NULL value (explicit blacklist)
        - _SENTINEL if no override exists (caller falls through to next source)
    """
    row = conn.execute(
        "SELECT value FROM classification_override "
        "WHERE canonical_id = ? AND field_name = ?",
        (canonical_id, field_name),
    ).fetchone()
    if row is None:
        return _SENTINEL
    return row[0]  # may be None = blacklist


def get_bloomberg_value(
    conn: sqlite3.Connection,
    canonical_id: str,
    field_name: str,
) -> Any:
    """Look up the Bloomberg-derived value for this field by joining
    canonical_id -> identifier_xref -> mkt_master_data.

    Field name maps to a column on mkt_master_data (best-effort).
    """
    # Translate logical field_name -> mkt_master_data column name
    column_map = {
        "etp_category": "etp_category",
        "issuer_display": "issuer_display",
        "primary_strategy": "primary_strategy",
        "asset_class": "asset_class",
        "sub_strategy": "sub_strategy",
        "leverage_ratio": "leverage_amount",
        "direction": "direction",
        "underlier_name": "underlier_name",
    }
    col = column_map.get(field_name)
    if not col:
        return _SENTINEL

    row = conn.execute(f"""
        SELECT m.{col}
        FROM identifier_xref ix
        JOIN mkt_master_data m
          ON (UPPER(m.ticker) = UPPER(ix.id_value)
              OR UPPER(m.ticker_clean) = UPPER(ix.id_value))
        WHERE ix.canonical_id = ?
          AND ix.id_type = 'ticker'
          AND ix.valid_to IS NULL
        LIMIT 1
    """, (canonical_id,)).fetchone()
    if row is None or row[0] is None:
        return _SENTINEL
    return row[0]


def resolve_field(
    canonical_id: str,
    field_name: str,
    *,
    db: sqlite3.Connection | None = None,
    auto_classifier=None,
) -> Any:
    """Resolve the final value for (canonical_id, field_name).

    Priority: classification_override > Bloomberg > auto_classifier > None.

    Args:
        auto_classifier: optional callable(canonical_id, field_name) -> Any.
            If not provided, returns None after exhausting override + Bloomberg.

    Returns:
        The resolved value, or None if nothing applies (or if explicit blacklist).
    """
    if db is None:
        from webapp.database import SessionLocal
        # The resolver accepts both SQLAlchemy Session and raw sqlite3.Connection
        session = SessionLocal()
        try:
            conn = session.connection().connection
            return _resolve(conn, canonical_id, field_name, auto_classifier)
        finally:
            session.close()
    elif hasattr(db, "connection"):  # SQLAlchemy Session
        conn = db.connection().connection
        return _resolve(conn, canonical_id, field_name, auto_classifier)
    else:  # raw sqlite3.Connection
        return _resolve(db, canonical_id, field_name, auto_classifier)


def _resolve(conn, canonical_id: str, field_name: str, auto_classifier) -> Any:
    # 1. Override
    override = get_override(conn, canonical_id, field_name)
    if override is not _SENTINEL:
        return override  # value or None (blacklist)

    # 2. Bloomberg
    bbg = get_bloomberg_value(conn, canonical_id, field_name)
    if bbg is not _SENTINEL:
        return bbg

    # 3. Auto-classifier
    if auto_classifier is not None:
        try:
            return auto_classifier(canonical_id, field_name)
        except Exception as e:
            log.warning("auto_classifier failed for (%s, %s): %s",
                        canonical_id, field_name, e)
    return None


def set_override(
    canonical_id: str,
    field_name: str,
    value: str | None,
    *,
    set_by: str,
    reason: str = "",
    db: sqlite3.Connection | None = None,
) -> None:
    """Insert or replace a classification_override row.

    Use value=None to explicitly blacklist a field (returns None on resolve
    even when Bloomberg / auto would classify it).
    """
    from datetime import datetime
    now = datetime.utcnow().isoformat()

    if db is None:
        from webapp.database import SessionLocal
        session = SessionLocal()
        try:
            conn = session.connection().connection
            _set(conn, canonical_id, field_name, value, set_by, reason, now)
            session.commit()
        finally:
            session.close()
    elif hasattr(db, "connection"):
        conn = db.connection().connection
        _set(conn, canonical_id, field_name, value, set_by, reason, now)
        db.commit()
    else:
        _set(db, canonical_id, field_name, value, set_by, reason, now)
        db.commit()


def _set(conn, canonical_id, field_name, value, set_by, reason, now):
    conn.execute("""
        INSERT INTO classification_override
        (canonical_id, field_name, value, set_by, set_at, reason)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (canonical_id, field_name)
        DO UPDATE SET value = excluded.value,
                      set_by = excluded.set_by,
                      set_at = excluded.set_at,
                      reason = excluded.reason
    """, (canonical_id, field_name, value, set_by, now, reason))
