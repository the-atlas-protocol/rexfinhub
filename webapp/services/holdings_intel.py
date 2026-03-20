"""
Holdings Intelligence service layer.

Core analytics engine for the 13F Intelligence Hub. All functions take a
SQLAlchemy Session and a quarter string (e.g. "2025-12-31"), returning
plain dicts/lists suitable for JSON or template rendering.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date

from sqlalchemy import func, select, distinct, and_, not_
from sqlalchemy.orm import Session

from webapp.models import Institution, Holding, MktMasterData, CusipMapping

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Cache infrastructure
# ---------------------------------------------------------------------------
_cache: dict[str, dict] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 600  # seconds


def _cached(key: str, fn):
    """Return cached value if fresh, otherwise call fn and cache result."""
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
            return entry["data"]
    result = fn()
    with _cache_lock:
        _cache[key] = {"data": result, "ts": time.time()}
    return result


# ---------------------------------------------------------------------------
# US state codes (for international filtering)
# ---------------------------------------------------------------------------
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA",
    "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD",
    "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC",
    "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY",
    "DC",
}

ASIA_CODES = {"K3", "K7", "K8", "M0", "M5", "U0", "W1"}

# Country code -> display name for common international codes
COUNTRY_NAMES = {
    "K3": "Hong Kong", "K7": "Singapore", "K8": "South Korea",
    "M0": "China", "M5": "Japan", "U0": "Taiwan", "W1": "India",
    "X2": "United Kingdom", "V8": "Australia", "Y7": "Canada",
    "2M": "Switzerland", "X0": "Germany", "X3": "France",
    "C5": "Brazil", "X1": "Ireland", "I6": "Israel",
    "V7": "Sweden", "V0": "Norway", "V4": "Netherlands",
}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_value(val: float | None) -> str:
    """Format USD value: $1.2T, $3.4B, $5.6M, $7.8K."""
    if val is None:
        return "--"
    v = abs(val)
    if v >= 1_000_000_000_000:
        return f"${v / 1_000_000_000_000:.1f}T"
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:,.0f}"


def fmt_pct(val: float | None) -> str:
    """Format percentage: +5.2% or -3.1%."""
    if val is None:
        return "--"
    sign = "+" if val >= 0 else ""
    return f"{sign}{val:.1f}%"


def _quarter_label(q: str) -> str:
    """Convert '2025-12-31' to \"Q4 '25\"."""
    d = date.fromisoformat(q)
    qnum = (d.month - 1) // 3 + 1
    return f"Q{qnum} '{d.year % 100:02d}"


# ---------------------------------------------------------------------------
# Quarter helpers
# ---------------------------------------------------------------------------

def get_available_quarters(db: Session) -> list[str]:
    """Return distinct report_date strings, descending."""
    rows = db.execute(
        select(distinct(Holding.report_date))
        .order_by(Holding.report_date.desc())
    ).scalars().all()
    return [str(d) for d in rows]


def get_latest_quarter(db: Session) -> str | None:
    """Return the most recent report_date string."""
    val = db.execute(select(func.max(Holding.report_date))).scalar()
    return str(val) if val else None


def get_prior_quarter(db: Session, quarter: str) -> str | None:
    """Return the report_date just before the given quarter."""
    q_date = date.fromisoformat(quarter)
    val = db.execute(
        select(func.max(Holding.report_date))
        .where(Holding.report_date < q_date)
    ).scalar()
    return str(val) if val else None


# ---------------------------------------------------------------------------
# Base query builders
# ---------------------------------------------------------------------------

def _etp_filter():
    """Filter to only categorized ETPs (excludes regular stocks/bonds in master data)."""
    return MktMasterData.etp_category.isnot(None)


def _issuer_col():
    """Issuer column with fallback: issuer_display → issuer."""
    return func.coalesce(MktMasterData.issuer_display, MktMasterData.issuer).label("issuer")


def _base_holdings_join():
    """Holdings JOIN mkt_master_data on cusip."""
    return (
        select(Holding, MktMasterData)
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
    )


def _rex_filter():
    """SQLAlchemy filter for REX products."""
    return MktMasterData.is_rex == True  # noqa: E712


def _non_rex_filter():
    """SQLAlchemy filter for non-REX products."""
    return MktMasterData.is_rex == False  # noqa: E712


# ---------------------------------------------------------------------------
# Core query functions
# ---------------------------------------------------------------------------

def get_hub_kpis(db: Session, quarter: str) -> dict:
    """Hub-level KPIs for a given quarter."""
    cache_key = f"hub_kpis_{quarter}"
    return _cached(cache_key, lambda: _compute_hub_kpis(db, quarter))


def _compute_hub_kpis(db: Session, quarter: str) -> dict:
    q_date = date.fromisoformat(quarter)
    prior_q = get_prior_quarter(db, quarter)

    # Total metrics (holdings joined to mkt_master_data)
    base = (
        select(
            func.sum(Holding.value_usd).label("total_aum"),
            func.count(distinct(func.coalesce(MktMasterData.issuer_display, MktMasterData.issuer))).label("total_issuers"),
            func.count(distinct(MktMasterData.ticker)).label("total_products"),
            func.count(distinct(Holding.institution_id)).label("total_filers"),
        )
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
    )
    row = db.execute(base).one()
    total_aum = row.total_aum or 0
    total_issuers = row.total_issuers or 0
    total_products = row.total_products or 0
    total_filers = row.total_filers or 0

    # New filers: in current quarter but not in prior
    new_filers = 0
    if prior_q:
        prior_date = date.fromisoformat(prior_q)
        current_ids = select(distinct(Holding.institution_id)).join(
            MktMasterData, Holding.cusip == MktMasterData.cusip
        ).where(Holding.report_date == q_date)
        prior_ids = select(distinct(Holding.institution_id)).join(
            MktMasterData, Holding.cusip == MktMasterData.cusip
        ).where(Holding.report_date == prior_date)
        new_filers = db.execute(
            select(func.count()).select_from(
                current_ids.except_(prior_ids).subquery()
            )
        ).scalar() or 0

    # REX metrics
    rex_base = (
        select(
            func.sum(Holding.value_usd).label("rex_aum"),
            func.count(distinct(Holding.institution_id)).label("rex_holders"),
            func.count(distinct(MktMasterData.ticker)).label("rex_products"),
        )
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .where(_rex_filter())
    )
    rex_row = db.execute(rex_base).one()
    rex_aum = rex_row.rex_aum or 0
    rex_holders = rex_row.rex_holders or 0
    rex_products = rex_row.rex_products or 0

    # REX new filers
    rex_new_filers = 0
    if prior_q:
        prior_date = date.fromisoformat(prior_q)
        rex_current = select(distinct(Holding.institution_id)).join(
            MktMasterData, Holding.cusip == MktMasterData.cusip
        ).where(Holding.report_date == q_date, _rex_filter())
        rex_prior = select(distinct(Holding.institution_id)).join(
            MktMasterData, Holding.cusip == MktMasterData.cusip
        ).where(Holding.report_date == prior_date, _rex_filter())
        rex_new_filers = db.execute(
            select(func.count()).select_from(
                rex_current.except_(rex_prior).subquery()
            )
        ).scalar() or 0

    return {
        "total_aum": total_aum,
        "total_aum_fmt": fmt_value(total_aum),
        "total_issuers": total_issuers,
        "total_products": total_products,
        "total_filers": total_filers,
        "new_filers": new_filers,
        "rex_aum": rex_aum,
        "rex_aum_fmt": fmt_value(rex_aum),
        "rex_holders": rex_holders,
        "rex_new_filers": rex_new_filers,
        "rex_products": rex_products,
    }


def get_holdings_by_product(
    db: Session, quarter: str, rex_only: bool = False, vertical: str | None = None
) -> list[dict]:
    """Holdings aggregated by product (ticker)."""
    cache_key = f"by_product_{quarter}_{rex_only}_{vertical}"
    return _cached(cache_key, lambda: _compute_holdings_by_product(db, quarter, rex_only, vertical))


def _compute_holdings_by_product(
    db: Session, quarter: str, rex_only: bool, vertical: str | None
) -> list[dict]:
    q_date = date.fromisoformat(quarter)
    stmt = (
        select(
            MktMasterData.ticker,
            MktMasterData.fund_name,
            func.coalesce(MktMasterData.issuer_display, MktMasterData.issuer).label("issuer_display"),
            MktMasterData.category_display,
            MktMasterData.etp_category,
            MktMasterData.is_rex,
            func.sum(Holding.value_usd).label("total_aum"),
            func.count(distinct(Holding.institution_id)).label("holder_count"),
            func.sum(Holding.shares).label("total_shares"),
        )
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .group_by(
            MktMasterData.ticker,
            MktMasterData.fund_name,
            func.coalesce(MktMasterData.issuer_display, MktMasterData.issuer).label("issuer_display"),
            MktMasterData.category_display,
            MktMasterData.etp_category,
            MktMasterData.is_rex,
        )
        .order_by(func.sum(Holding.value_usd).desc())
    )
    if rex_only:
        stmt = stmt.where(_rex_filter())
    if vertical:
        stmt = stmt.where(MktMasterData.category_display == vertical)

    rows = db.execute(stmt).all()
    return [
        {
            "ticker": r.ticker,
            "security_name": r.fund_name,
            "issuer": r.issuer_display,
            "vertical": r.category_display,
            "product_type": r.etp_category,
            "is_rex": r.is_rex,
            "total_aum": r.total_aum or 0,
            "total_aum_fmt": fmt_value(r.total_aum),
            "holder_count": r.holder_count or 0,
            "total_shares": r.total_shares or 0,
        }
        for r in rows
    ]


def get_holdings_by_issuer(
    db: Session, quarter: str, rex_only: bool = False
) -> list[dict]:
    """Holdings aggregated by issuer."""
    cache_key = f"by_issuer_{quarter}_{rex_only}"
    return _cached(cache_key, lambda: _compute_holdings_by_issuer(db, quarter, rex_only))


def _compute_holdings_by_issuer(db: Session, quarter: str, rex_only: bool) -> list[dict]:
    q_date = date.fromisoformat(quarter)
    stmt = (
        select(
            _issuer_col(),
            func.sum(Holding.value_usd).label("total_aum"),
            func.count(distinct(MktMasterData.ticker)).label("product_count"),
            func.count(distinct(Holding.institution_id)).label("holder_count"),
        )
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .group_by(func.coalesce(MktMasterData.issuer_display, MktMasterData.issuer))
        .order_by(func.sum(Holding.value_usd).desc())
    )
    if rex_only:
        stmt = stmt.where(_rex_filter())

    rows = db.execute(stmt).all()
    return [
        {
            "issuer": r.issuer,
            "total_aum": r.total_aum or 0,
            "total_aum_fmt": fmt_value(r.total_aum),
            "product_count": r.product_count or 0,
            "holder_count": r.holder_count or 0,
        }
        for r in rows
    ]


def get_holdings_by_vertical(
    db: Session, quarter: str, rex_only: bool = False
) -> list[dict]:
    """Holdings aggregated by vertical (category_display)."""
    cache_key = f"by_vertical_{quarter}_{rex_only}"
    return _cached(cache_key, lambda: _compute_holdings_by_vertical(db, quarter, rex_only))


def _compute_holdings_by_vertical(db: Session, quarter: str, rex_only: bool) -> list[dict]:
    q_date = date.fromisoformat(quarter)
    stmt = (
        select(
            MktMasterData.category_display.label("vertical"),
            func.sum(Holding.value_usd).label("total_aum"),
            func.count(distinct(MktMasterData.ticker)).label("product_count"),
            func.count(distinct(Holding.institution_id)).label("holder_count"),
        )
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .group_by(MktMasterData.category_display)
        .order_by(func.sum(Holding.value_usd).desc())
    )
    if rex_only:
        stmt = stmt.where(_rex_filter())

    rows = db.execute(stmt).all()
    return [
        {
            "vertical": r.vertical,
            "total_aum": r.total_aum or 0,
            "total_aum_fmt": fmt_value(r.total_aum),
            "product_count": r.product_count or 0,
            "holder_count": r.holder_count or 0,
        }
        for r in rows
    ]


def get_new_filers(
    db: Session, quarter: str, rex_only: bool = False
) -> list[dict]:
    """Institutions filing for the first time in this quarter."""
    cache_key = f"new_filers_{quarter}_{rex_only}"
    return _cached(cache_key, lambda: _compute_new_filers(db, quarter, rex_only))


def _compute_new_filers(db: Session, quarter: str, rex_only: bool) -> list[dict]:
    q_date = date.fromisoformat(quarter)
    prior_q = get_prior_quarter(db, quarter)
    if not prior_q:
        return []
    prior_date = date.fromisoformat(prior_q)

    # Subqueries for current and prior quarter institution IDs
    rex_clause = _rex_filter() if rex_only else True  # noqa: E712

    current_ids_sq = (
        select(Holding.institution_id.label("iid"))
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .where(rex_clause)
        .distinct()
    ).subquery()

    prior_ids_sq = (
        select(Holding.institution_id.label("iid"))
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == prior_date)
        .where(rex_clause)
        .distinct()
    ).subquery()

    # New = current minus prior
    new_ids_sq = (
        select(current_ids_sq.c.iid)
        .where(current_ids_sq.c.iid.not_in(
            select(prior_ids_sq.c.iid)
        ))
    ).subquery()

    # Get details for new filers
    stmt = (
        select(
            Holding.institution_id,
            Institution.cik,
            Institution.name.label("filingmanager_name"),
            Institution.state_or_country.label("filingmanager_stateorcountry"),
            MktMasterData.ticker,
            MktMasterData.category_display.label("vertical"),
            _issuer_col(),
            func.sum(Holding.value_usd).label("total_value"),
            func.sum(Holding.shares).label("total_shares"),
        )
        .join(Institution, Holding.institution_id == Institution.id)
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .where(Holding.institution_id.in_(select(new_ids_sq.c.iid)))
        .where(rex_clause)
        .group_by(
            Holding.institution_id,
            Institution.cik,
            Institution.name,
            Institution.state_or_country,
            MktMasterData.ticker,
            MktMasterData.category_display,
            func.coalesce(MktMasterData.issuer_display, MktMasterData.issuer).label("issuer_display"),
        )
        .order_by(func.sum(Holding.value_usd).desc())
    )

    rows = db.execute(stmt).all()
    return [
        {
            "institution_id": r.institution_id,
            "cik": r.cik,
            "filingmanager_name": r.filingmanager_name,
            "filingmanager_stateorcountry": r.filingmanager_stateorcountry,
            "ticker": r.ticker,
            "vertical": r.vertical,
            "issuer": r.issuer,
            "total_value": r.total_value or 0,
            "total_value_fmt": fmt_value(r.total_value),
            "total_shares": r.total_shares or 0,
        }
        for r in rows
    ]


def get_top_filers(
    db: Session, quarter: str, rex_only: bool = False, limit: int = 50
) -> list[dict]:
    """Top filers by total value."""
    cache_key = f"top_filers_{quarter}_{rex_only}_{limit}"
    return _cached(cache_key, lambda: _compute_top_filers(db, quarter, rex_only, limit))


def _compute_top_filers(db: Session, quarter: str, rex_only: bool, limit: int) -> list[dict]:
    q_date = date.fromisoformat(quarter)
    stmt = (
        select(
            Holding.institution_id,
            Institution.cik,
            Institution.name.label("filingmanager_name"),
            Institution.state_or_country.label("filingmanager_stateorcountry"),
            func.sum(Holding.value_usd).label("total_value"),
            func.sum(Holding.shares).label("total_shares"),
            func.count(distinct(MktMasterData.ticker)).label("product_count"),
        )
        .join(Institution, Holding.institution_id == Institution.id)
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .group_by(
            Holding.institution_id,
            Institution.cik,
            Institution.name,
            Institution.state_or_country,
        )
        .order_by(func.sum(Holding.value_usd).desc())
        .limit(limit)
    )
    if rex_only:
        stmt = stmt.where(_rex_filter())

    rows = db.execute(stmt).all()
    return [
        {
            "institution_id": r.institution_id,
            "cik": r.cik,
            "filingmanager_name": r.filingmanager_name,
            "filingmanager_stateorcountry": r.filingmanager_stateorcountry,
            "total_value": r.total_value or 0,
            "total_value_fmt": fmt_value(r.total_value),
            "total_shares": r.total_shares or 0,
            "product_count": r.product_count or 0,
        }
        for r in rows
    ]


def get_trend_data(db: Session, rex_only: bool = False) -> list[dict]:
    """Quarterly trend across all available quarters."""
    cache_key = f"trend_{rex_only}"
    return _cached(cache_key, lambda: _compute_trend_data(db, rex_only))


def _compute_trend_data(db: Session, rex_only: bool) -> list[dict]:
    quarters = get_available_quarters(db)
    if not quarters:
        return []

    result = []
    # Reverse so we iterate chronologically
    for q in reversed(quarters):
        q_date = date.fromisoformat(q)
        stmt = (
            select(
                func.sum(Holding.value_usd).label("aum"),
                func.count(distinct(Holding.institution_id)).label("filer_count"),
                func.count(distinct(MktMasterData.ticker)).label("product_count"),
            )
            .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
            .where(Holding.report_date == q_date)
        )
        if rex_only:
            stmt = stmt.where(_rex_filter())

        row = db.execute(stmt).one()

        # New filers for this quarter
        prior_q = get_prior_quarter(db, q)
        new_filers = 0
        if prior_q:
            prior_date = date.fromisoformat(prior_q)
            rex_clause = _rex_filter() if rex_only else True  # noqa: E712
            current_ids = (
                select(distinct(Holding.institution_id))
                .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
                .where(Holding.report_date == q_date)
                .where(rex_clause)
            )
            prior_ids = (
                select(distinct(Holding.institution_id))
                .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
                .where(Holding.report_date == prior_date)
                .where(rex_clause)
            )
            new_filers = db.execute(
                select(func.count()).select_from(
                    current_ids.except_(prior_ids).subquery()
                )
            ).scalar() or 0

        result.append({
            "quarter": q,
            "label": _quarter_label(q),
            "aum": row.aum or 0,
            "aum_fmt": fmt_value(row.aum),
            "filer_count": row.filer_count or 0,
            "product_count": row.product_count or 0,
            "new_filers": new_filers,
        })

    return result


def get_country_data(
    db: Session, quarter: str, rex_only: bool = False
) -> list[dict]:
    """International holders (non-US state_or_country)."""
    cache_key = f"country_{quarter}_{rex_only}"
    return _cached(cache_key, lambda: _compute_country_data(db, quarter, rex_only))


def _compute_country_data(db: Session, quarter: str, rex_only: bool) -> list[dict]:
    q_date = date.fromisoformat(quarter)
    stmt = (
        select(
            Holding.institution_id,
            Institution.name.label("filingmanager_name"),
            Institution.city,
            Institution.state_or_country.label("country_code"),
            MktMasterData.ticker,
            MktMasterData.category_display.label("vertical"),
            func.sum(Holding.value_usd).label("total_value"),
            func.sum(Holding.shares).label("total_shares"),
        )
        .join(Institution, Holding.institution_id == Institution.id)
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .where(Institution.state_or_country.not_in(US_STATES))
        .where(Institution.state_or_country.isnot(None))
        .group_by(
            Holding.institution_id,
            Institution.name,
            Institution.city,
            Institution.state_or_country,
            MktMasterData.ticker,
            MktMasterData.category_display,
        )
        .order_by(func.sum(Holding.value_usd).desc())
    )
    if rex_only:
        stmt = stmt.where(_rex_filter())

    rows = db.execute(stmt).all()
    return [
        {
            "institution_id": r.institution_id,
            "filingmanager_name": r.filingmanager_name,
            "city": r.city,
            "country_code": r.country_code,
            "country_name": COUNTRY_NAMES.get(r.country_code, r.country_code),
            "ticker": r.ticker,
            "vertical": r.vertical,
            "total_value": r.total_value or 0,
            "total_value_fmt": fmt_value(r.total_value),
            "total_shares": r.total_shares or 0,
        }
        for r in rows
    ]


def get_asia_data(db: Session, quarter: str) -> list[dict]:
    """Asian holders of REX products."""
    cache_key = f"asia_{quarter}"
    return _cached(cache_key, lambda: _compute_asia_data(db, quarter))


def _compute_asia_data(db: Session, quarter: str) -> list[dict]:
    q_date = date.fromisoformat(quarter)
    stmt = (
        select(
            Holding.institution_id,
            Institution.name.label("filingmanager_name"),
            Institution.city,
            Institution.state_or_country.label("country_code"),
            MktMasterData.ticker,
            MktMasterData.category_display.label("vertical"),
            func.sum(Holding.value_usd).label("total_value"),
            func.sum(Holding.shares).label("total_shares"),
        )
        .join(Institution, Holding.institution_id == Institution.id)
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .where(Institution.state_or_country.in_(ASIA_CODES))
        .where(_rex_filter())
        .group_by(
            Holding.institution_id,
            Institution.name,
            Institution.city,
            Institution.state_or_country,
            MktMasterData.ticker,
            MktMasterData.category_display,
        )
        .order_by(func.sum(Holding.value_usd).desc())
    )

    rows = db.execute(stmt).all()
    return [
        {
            "institution_id": r.institution_id,
            "filingmanager_name": r.filingmanager_name,
            "city": r.city,
            "country_code": r.country_code,
            "country_name": COUNTRY_NAMES.get(r.country_code, r.country_code),
            "ticker": r.ticker,
            "vertical": r.vertical,
            "total_value": r.total_value or 0,
            "total_value_fmt": fmt_value(r.total_value),
            "total_shares": r.total_shares or 0,
        }
        for r in rows
    ]


def get_us_state_data(
    db: Session, quarter: str, rex_only: bool = True
) -> list[dict]:
    """Holdings aggregated by US state."""
    cache_key = f"us_state_{quarter}_{rex_only}"
    return _cached(cache_key, lambda: _compute_us_state_data(db, quarter, rex_only))


_STATE_NAMES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
}


def _compute_us_state_data(db: Session, quarter: str, rex_only: bool) -> list[dict]:
    q_date = date.fromisoformat(quarter)
    stmt = (
        select(
            Institution.state_or_country.label("state"),
            func.count(distinct(Holding.institution_id)).label("filers"),
            func.count(distinct(MktMasterData.ticker)).label("products"),
            func.sum(Holding.value_usd).label("rex_aum"),
        )
        .join(Institution, Holding.institution_id == Institution.id)
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .where(Institution.state_or_country.in_(US_STATES))
        .group_by(Institution.state_or_country)
        .order_by(func.sum(Holding.value_usd).desc())
    )
    if rex_only:
        stmt = stmt.where(_rex_filter())

    rows = db.execute(stmt).all()
    return [
        {
            "state": r.state,
            "state_name": _STATE_NAMES.get(r.state, r.state),
            "filers": r.filers or 0,
            "products": r.products or 0,
            "rex_aum": r.rex_aum or 0,
            "rex_aum_fmt": fmt_value(r.rex_aum),
        }
        for r in rows
    ]


def get_competitor_new_filers(db: Session, quarter: str) -> list[dict]:
    """New filers for non-REX products only."""
    cache_key = f"comp_new_filers_{quarter}"
    return _cached(cache_key, lambda: _compute_competitor_new_filers(db, quarter))


def _compute_competitor_new_filers(db: Session, quarter: str) -> list[dict]:
    q_date = date.fromisoformat(quarter)
    prior_q = get_prior_quarter(db, quarter)
    if not prior_q:
        return []
    prior_date = date.fromisoformat(prior_q)

    current_ids_sq = (
        select(Holding.institution_id.label("iid"))
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .where(_non_rex_filter())
        .distinct()
    ).subquery()

    prior_ids_sq = (
        select(Holding.institution_id.label("iid"))
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == prior_date)
        .where(_non_rex_filter())
        .distinct()
    ).subquery()

    new_ids_sq = (
        select(current_ids_sq.c.iid)
        .where(current_ids_sq.c.iid.not_in(
            select(prior_ids_sq.c.iid)
        ))
    ).subquery()

    stmt = (
        select(
            Holding.institution_id,
            Institution.cik,
            Institution.name.label("filingmanager_name"),
            Institution.state_or_country.label("filingmanager_stateorcountry"),
            MktMasterData.ticker,
            MktMasterData.category_display.label("vertical"),
            _issuer_col(),
            func.sum(Holding.value_usd).label("total_value"),
            func.sum(Holding.shares).label("total_shares"),
        )
        .join(Institution, Holding.institution_id == Institution.id)
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .where(Holding.institution_id.in_(select(new_ids_sq.c.iid)))
        .where(_non_rex_filter())
        .group_by(
            Holding.institution_id,
            Institution.cik,
            Institution.name,
            Institution.state_or_country,
            MktMasterData.ticker,
            MktMasterData.category_display,
            func.coalesce(MktMasterData.issuer_display, MktMasterData.issuer).label("issuer_display"),
        )
        .order_by(func.sum(Holding.value_usd).desc())
    )

    rows = db.execute(stmt).all()
    return [
        {
            "institution_id": r.institution_id,
            "cik": r.cik,
            "filingmanager_name": r.filingmanager_name,
            "filingmanager_stateorcountry": r.filingmanager_stateorcountry,
            "ticker": r.ticker,
            "vertical": r.vertical,
            "issuer": r.issuer,
            "total_value": r.total_value or 0,
            "total_value_fmt": fmt_value(r.total_value),
            "total_shares": r.total_shares or 0,
        }
        for r in rows
    ]


def get_qoq_changes(
    db: Session, quarter: str, rex_only: bool = False
) -> list[dict]:
    """Quarter-over-quarter AUM and filer changes by product."""
    cache_key = f"qoq_{quarter}_{rex_only}"
    return _cached(cache_key, lambda: _compute_qoq_changes(db, quarter, rex_only))


def _compute_qoq_changes(db: Session, quarter: str, rex_only: bool) -> list[dict]:
    q_date = date.fromisoformat(quarter)
    prior_q = get_prior_quarter(db, quarter)
    if not prior_q:
        return []
    prior_date = date.fromisoformat(prior_q)

    rex_clause = _rex_filter() if rex_only else True  # noqa: E712

    # Current quarter by product
    current_stmt = (
        select(
            MktMasterData.ticker,
            MktMasterData.fund_name.label("security_name"),
            MktMasterData.category_display.label("vertical"),
            func.sum(Holding.value_usd).label("current_aum"),
            func.count(distinct(Holding.institution_id)).label("current_filers"),
        )
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .where(rex_clause)
        .group_by(MktMasterData.ticker, MktMasterData.fund_name, MktMasterData.category_display)
    )
    current_rows = {r.ticker: r for r in db.execute(current_stmt).all()}

    # Prior quarter by product
    prior_stmt = (
        select(
            MktMasterData.ticker,
            func.sum(Holding.value_usd).label("prior_aum"),
            func.count(distinct(Holding.institution_id)).label("prior_filers"),
        )
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == prior_date)
        .where(rex_clause)
        .group_by(MktMasterData.ticker)
    )
    prior_rows = {r.ticker: r for r in db.execute(prior_stmt).all()}

    all_tickers = set(current_rows.keys()) | set(prior_rows.keys())
    result = []
    for ticker in all_tickers:
        curr = current_rows.get(ticker)
        prev = prior_rows.get(ticker)

        current_aum = (curr.current_aum or 0) if curr else 0
        prior_aum = (prev.prior_aum or 0) if prev else 0
        current_filers = (curr.current_filers or 0) if curr else 0
        prior_filers = (prev.prior_filers or 0) if prev else 0

        qoq_dollar = current_aum - prior_aum
        qoq_pct = round((current_aum - prior_aum) / prior_aum * 100, 1) if prior_aum else 0.0

        result.append({
            "ticker": ticker,
            "security_name": curr.security_name if curr else "",
            "vertical": curr.vertical if curr else "",
            "current_aum": current_aum,
            "current_aum_fmt": fmt_value(current_aum),
            "prior_aum": prior_aum,
            "prior_aum_fmt": fmt_value(prior_aum),
            "qoq_dollar": qoq_dollar,
            "qoq_dollar_fmt": fmt_value(abs(qoq_dollar)) if qoq_dollar else "--",
            "qoq_pct": qoq_pct,
            "qoq_pct_fmt": fmt_pct(qoq_pct),
            "current_filers": current_filers,
            "prior_filers": prior_filers,
        })

    result.sort(key=lambda x: abs(x["qoq_dollar"]), reverse=True)
    return result


def get_head_to_head(db: Session, quarter: str, underlying: str) -> list[dict]:
    """Products sharing the same underlying_index or map_li_underlier."""
    cache_key = f"h2h_{quarter}_{underlying}"
    return _cached(cache_key, lambda: _compute_head_to_head(db, quarter, underlying))


def _compute_head_to_head(db: Session, quarter: str, underlying: str) -> list[dict]:
    q_date = date.fromisoformat(quarter)
    if not underlying:
        return []

    stmt = (
        select(
            MktMasterData.ticker,
            MktMasterData.fund_name.label("security_name"),
            _issuer_col(),
            MktMasterData.category_display.label("vertical"),
            MktMasterData.is_rex,
            MktMasterData.underlying_index,
            MktMasterData.map_li_underlier,
            func.sum(Holding.value_usd).label("total_aum"),
            func.count(distinct(Holding.institution_id)).label("holder_count"),
            func.sum(Holding.shares).label("total_shares"),
        )
        .join(MktMasterData, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .where(
            (MktMasterData.underlying_index == underlying)
            | (MktMasterData.map_li_underlier == underlying)
        )
        .group_by(
            MktMasterData.ticker,
            MktMasterData.fund_name,
            func.coalesce(MktMasterData.issuer_display, MktMasterData.issuer).label("issuer_display"),
            MktMasterData.category_display,
            MktMasterData.is_rex,
            MktMasterData.underlying_index,
            MktMasterData.map_li_underlier,
        )
        .order_by(func.sum(Holding.value_usd).desc())
    )

    rows = db.execute(stmt).all()
    return [
        {
            "ticker": r.ticker,
            "security_name": r.security_name,
            "issuer": r.issuer,
            "vertical": r.vertical,
            "is_rex": r.is_rex,
            "underlying_index": r.underlying_index,
            "map_li_underlier": r.map_li_underlier,
            "total_aum": r.total_aum or 0,
            "total_aum_fmt": fmt_value(r.total_aum),
            "holder_count": r.holder_count or 0,
            "total_shares": r.total_shares or 0,
        }
        for r in rows
    ]


def get_distinct_verticals(db: Session, quarter: str) -> list[str]:
    """Distinct category_display values for the given quarter."""
    q_date = date.fromisoformat(quarter)
    rows = db.execute(
        select(distinct(MktMasterData.category_display))
        .join(Holding, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .where(MktMasterData.category_display.isnot(None))
        .order_by(MktMasterData.category_display)
    ).scalars().all()
    return [str(v) for v in rows]


def get_distinct_underlyings(db: Session, quarter: str) -> list[str]:
    """Distinct underlier values for head-to-head dropdown."""
    q_date = date.fromisoformat(quarter)
    li = db.execute(
        select(distinct(MktMasterData.map_li_underlier))
        .join(Holding, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .where(MktMasterData.map_li_underlier.isnot(None))
        .where(MktMasterData.map_li_underlier != "")
    ).scalars().all()
    ui = db.execute(
        select(distinct(MktMasterData.underlying_index))
        .join(Holding, Holding.cusip == MktMasterData.cusip).where(_etp_filter())
        .where(Holding.report_date == q_date)
        .where(MktMasterData.underlying_index.isnot(None))
        .where(MktMasterData.underlying_index != "")
    ).scalars().all()
    combined = sorted(set(str(v) for v in li) | set(str(v) for v in ui))
    return combined


def get_country_breakdown(data: list[dict]) -> list[dict]:
    """Aggregate country_data into per-country summary."""
    by_country: dict[str, dict] = {}
    for row in data:
        code = row.get("country_code", "")
        if code not in by_country:
            by_country[code] = {
                "name": row.get("country_name", code),
                "code": code,
                "total_value": 0,
                "filer_count": set(),
            }
        by_country[code]["total_value"] += row.get("total_value", 0)
        by_country[code]["filer_count"].add(row.get("institution_id"))

    result = []
    for entry in by_country.values():
        result.append({
            "name": entry["name"],
            "code": entry["code"],
            "total_value": entry["total_value"],
            "total_value_fmt": fmt_value(entry["total_value"]),
            "filer_count": len(entry["filer_count"]),
        })
    result.sort(key=lambda x: x["total_value"], reverse=True)
    return result
