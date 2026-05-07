"""Cross-reference cboe_symbols against the known-active universe.

A taken (available=False) ticker splits two ways once we have a known-active
set (cboe_known_active populated from NASDAQ + SEC EDGAR):
  - active   = the base_ticker has a live US listing (stock or ETF)
  - reserved = NO live listing → another issuer claimed it for an
               unlaunched product (the competitor pipeline signal)

Labels in the table prefer mkt_master_data (REX-specific ETP context) and
fall back to cboe_known_active (broad public-listings metadata).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from webapp.models import (
    CboeKnownActive, CboeScanRun, CboeStateChange, CboeSymbol, MktMasterData,
)

DEFAULT_LIMIT = 200
MAX_LIMIT = 1000


def _mkt_label_subquery(db: Session):
    return (
        db.query(
            MktMasterData.ticker.label("ticker"),
            func.min(MktMasterData.fund_name).label("fund_name"),
            func.min(MktMasterData.issuer).label("issuer"),
            func.min(MktMasterData.listed_exchange).label("listed_exchange"),
            func.min(MktMasterData.etp_category).label("etp_category"),
        )
        .group_by(MktMasterData.ticker)
        .subquery()
    )


def _known_label_subquery(db: Session):
    """One representative known_active row per base_ticker (prefers ETF over
    stock for the label, since ETF rows tend to be sponsor-tagged)."""
    rank = case(
        (CboeKnownActive.sec_type == "etf", 1),
        (CboeKnownActive.sec_type == "stock", 2),
        else_=3,
    ).label("rank")
    return (
        db.query(
            CboeKnownActive.base_ticker.label("base_ticker"),
            func.min(CboeKnownActive.name).label("name"),
            func.min(CboeKnownActive.sec_type).label("sec_type"),
            func.min(CboeKnownActive.exchange).label("exchange"),
            func.min(CboeKnownActive.sector).label("sector"),
        )
        .group_by(CboeKnownActive.base_ticker)
        .subquery()
    )


def enriched_rows(
    db: Session,
    *,
    length: int | None = None,
    state: str | None = None,
    search: str | None = None,
    sort: str = "last_checked_desc",
    limit: int = DEFAULT_LIMIT,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Return (rows, total_matching). state ∈ {available, reserved, active, unknown, None}."""
    limit = max(1, min(limit, MAX_LIMIT))
    offset = max(0, offset)

    mkt = _mkt_label_subquery(db)
    known = _known_label_subquery(db)

    q = (
        db.query(
            CboeSymbol.ticker,
            CboeSymbol.length,
            CboeSymbol.available,
            CboeSymbol.last_checked_at,
            mkt.c.fund_name,
            mkt.c.issuer,
            mkt.c.listed_exchange,
            mkt.c.etp_category,
            known.c.name.label("known_name"),
            known.c.sec_type.label("known_sec_type"),
            known.c.exchange.label("known_exchange"),
            known.c.sector.label("known_sector"),
        )
        .outerjoin(mkt, CboeSymbol.ticker == mkt.c.ticker)
        .outerjoin(known, CboeSymbol.ticker == known.c.base_ticker)
    )

    if length is not None:
        q = q.filter(CboeSymbol.length == length)
    if state == "available":
        q = q.filter(CboeSymbol.available.is_(True))
    elif state == "active":
        q = q.filter(CboeSymbol.available.is_(False)).filter(known.c.base_ticker.isnot(None))
    elif state == "reserved":
        q = q.filter(CboeSymbol.available.is_(False)).filter(known.c.base_ticker.is_(None))
    elif state == "unknown":
        q = q.filter(CboeSymbol.available.is_(None))
    if search:
        s_upper = search.upper().strip()
        # Pattern syntax: '.' = single char, '*' = any chars (translated to SQL
        # LIKE _/%). Triggered only when the search contains a wildcard AND is
        # short — keeps the regular fund-name search behaviour intact.
        if any(c in s_upper for c in ".*?") and len(s_upper) <= 4:
            sql_pat = (
                s_upper.replace(".", "_").replace("?", "_").replace("*", "%")
            )
            q = q.filter(CboeSymbol.ticker.like(sql_pat))
        else:
            q = q.filter(
                (CboeSymbol.ticker.startswith(s_upper))
                | (mkt.c.fund_name.ilike(f"%{search}%"))
                | (known.c.name.ilike(f"%{search}%"))
            )

    total = q.count()

    if sort == "ticker":
        q = q.order_by(CboeSymbol.ticker)
    elif sort == "length":
        q = q.order_by(CboeSymbol.length, CboeSymbol.ticker)
    elif sort == "state":
        q = q.order_by(CboeSymbol.available, CboeSymbol.ticker)
    else:
        q = q.order_by(CboeSymbol.last_checked_at.desc().nullslast())

    results = q.offset(offset).limit(limit).all()

    rows: list[dict[str, Any]] = []
    for r in results:
        # Determine state
        if r.available is True:
            state_str = "available"
        elif r.available is False:
            state_str = "active" if (r.fund_name or r.known_name) else "reserved"
        else:
            state_str = "unknown"
        # Prefer mkt_master_data labels (REX/ETP context); fall back to known_active
        name = r.fund_name or r.known_name
        issuer = r.issuer or None
        exchange = r.listed_exchange or r.known_exchange
        category = r.etp_category or (r.known_sec_type.upper() if r.known_sec_type else None)

        rows.append(
            {
                "ticker": r.ticker,
                "length": r.length,
                "available": r.available,
                "state": state_str,
                "fund_name": name,
                "issuer": issuer,
                "exchange": exchange,
                "category": category,
                "last_checked_at": r.last_checked_at,
            }
        )
    return rows, total


def summary_counts(db: Session) -> dict[str, int]:
    """KPI strip: available / reserved / active / recently flipped.

    "active" = taken AND base_ticker present in cboe_known_active.
    "reserved" = taken AND NOT in cboe_known_active (the competitor signal).
    """
    available = (
        db.query(func.count(CboeSymbol.ticker))
        .filter(CboeSymbol.available.is_(True))
        .scalar()
        or 0
    )
    known_bases = {
        t[0] for t in db.query(CboeKnownActive.base_ticker).distinct()
    }
    taken_tickers = [
        t[0] for t in db.query(CboeSymbol.ticker).filter(CboeSymbol.available.is_(False)).all()
    ]
    active = sum(1 for t in taken_tickers if t in known_bases)
    reserved = sum(1 for t in taken_tickers if t not in known_bases)
    since = datetime.utcnow() - timedelta(hours=24)
    flipped = (
        db.query(func.count(CboeStateChange.id))
        .filter(CboeStateChange.detected_at >= since)
        .scalar()
        or 0
    )
    return {
        "available": available,
        "reserved": reserved,
        "active": active,
        "recently_flipped_24h": flipped,
    }


def last_scan(db: Session) -> dict[str, Any] | None:
    row = (
        db.query(CboeScanRun)
        .order_by(CboeScanRun.started_at.desc())
        .first()
    )
    if row is None:
        return None
    return {
        "id": row.id,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "status": row.status,
        "tier": row.tier,
        "tickers_checked": row.tickers_checked,
        "state_changes_detected": row.state_changes_detected,
        "error_message": row.error_message,
    }


def auth_health(db: Session) -> dict[str, Any]:
    """Inspect recent runs to surface cookie-expiry on the page.

    Returns a dict with `ok`, `last_completed_at` (most recent successful full
    sweep), `failed_streak` (consecutive failed runs since), `error_message`,
    and `days_stale` (since last completed sweep). When `ok` is False the page
    renders a red banner asking for cookie rotation.
    """
    last_completed = (
        db.query(CboeScanRun)
        .filter(CboeScanRun.status == "completed")
        .order_by(CboeScanRun.started_at.desc())
        .first()
    )
    last_run = (
        db.query(CboeScanRun)
        .order_by(CboeScanRun.started_at.desc())
        .first()
    )
    if last_run is None:
        return {"ok": True, "last_completed_at": None, "failed_streak": 0,
                "error_message": None, "days_stale": None}

    failed_streak = 0
    for r in (
        db.query(CboeScanRun)
        .order_by(CboeScanRun.started_at.desc())
        .all()
    ):
        if r.status == "failed":
            failed_streak += 1
        else:
            break

    last_completed_at = last_completed.started_at if last_completed else None
    days_stale: int | None = None
    if last_completed_at:
        days_stale = max(0, (datetime.utcnow() - last_completed_at).days)

    auth_failed = (
        last_run.status == "failed"
        and last_run.error_message is not None
        and any(
            kw in last_run.error_message.lower()
            for kw in ("cookie", "auth", "redirect", "login", "401", "403", "302")
        )
    )

    return {
        "ok": not auth_failed,
        "last_completed_at": last_completed_at,
        "failed_streak": failed_streak,
        "error_message": last_run.error_message,
        "days_stale": days_stale,
    }
