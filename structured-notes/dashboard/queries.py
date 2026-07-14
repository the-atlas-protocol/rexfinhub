"""Dashboard queries using raw sqlite3.

Safe to run alongside extraction — no SQLAlchemy, no locks.
All functions take a sqlite3.Connection and return plain dicts/lists.
"""
import sqlite3
import sys
from pathlib import Path
from collections import Counter

# Ensure project root is importable for config
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DB_PATH

# Base filter: finals only, structured notes only (excludes ETN/MTN/shelf)
_SN_FILTER = "p.is_preliminary = 0 AND (p.asset_class = 'structured_note' OR p.asset_class IS NULL)"


def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = sqlite3.Row
    return conn


def _year_clause(year: int | None, date_col: str = "f.filing_date") -> tuple[str, list]:
    """Return (SQL fragment, params) for optional year filter."""
    if year:
        return f"AND CAST(strftime('%Y', {date_col}) AS INTEGER) = ?", [year]
    return "", []


# ---------------------------------------------------------------------------
# KPIs
# ---------------------------------------------------------------------------

def kpis(conn: sqlite3.Connection, year: int | None = None) -> dict:
    """Top-level KPI metrics."""
    yc, yp = _year_clause(year)

    # Total products (all types)
    r = conn.execute(f"""
        SELECT
            COUNT(*) as total_products,
            SUM(CASE WHEN is_preliminary = 0 THEN 1 ELSE 0 END) as finals,
            SUM(CASE WHEN is_preliminary = 1 THEN 1 ELSE 0 END) as prelims,
            COUNT(DISTINCT parent_issuer) as issuer_count
        FROM products p
        JOIN filings f ON p.filing_id = f.id
        WHERE 1=1 {yc}
    """, yp).fetchone()

    # Clean metrics: structured_note only, finals only
    clean = conn.execute(f"""
        SELECT
            SUM(p.notional_amount) as total_notional,
            AVG(CASE WHEN p.coupon_rate > 0 THEN p.coupon_rate END) as avg_coupon,
            AVG(p.barrier_level) as avg_barrier,
            COUNT(*) as sn_count
        FROM products p
        JOIN filings f ON p.filing_id = f.id
        WHERE {_SN_FILTER} {yc}
    """, yp).fetchone()

    # Extraction progress
    total_filings = conn.execute("SELECT COUNT(*) FROM filings").fetchone()[0]
    extracted = conn.execute("SELECT COUNT(*) FROM filings WHERE extracted = 1").fetchone()[0]

    return {
        "total_products": r["total_products"],
        "finals": r["finals"],
        "prelims": r["prelims"],
        "sn_count": clean["sn_count"],
        "total_notional": clean["total_notional"] or 0,
        "avg_coupon": clean["avg_coupon"] or 0,
        "avg_barrier": clean["avg_barrier"] or 0,
        "issuer_count": r["issuer_count"],
        "total_filings": total_filings,
        "extracted_filings": extracted,
        "extraction_pct": round(extracted / total_filings * 100, 1) if total_filings else 0,
    }


# ---------------------------------------------------------------------------
# Charts
# ---------------------------------------------------------------------------

def monthly_issuance(conn: sqlite3.Connection, year: int | None = None, months: int = 24) -> list[dict]:
    """Monthly product count and notional for line chart."""
    yc, yp = _year_clause(year)
    rows = conn.execute(f"""
        SELECT strftime('%Y-%m', f.filing_date) as month,
               COUNT(p.id) as product_count,
               SUM(p.notional_amount) as total_notional
        FROM products p
        JOIN filings f ON p.filing_id = f.id
        WHERE {_SN_FILTER} {yc}
        GROUP BY month
        ORDER BY month DESC
        LIMIT ?
    """, yp + [months]).fetchall()
    return [{"month": r["month"], "count": r["product_count"],
             "notional": r["total_notional"] or 0} for r in reversed(rows)]


def product_type_mix(conn: sqlite3.Connection, year: int | None = None) -> list[dict]:
    """Product type distribution for donut chart."""
    yc, yp = _year_clause(year)
    rows = conn.execute(f"""
        SELECT COALESCE(p.product_type, 'unknown') as ptype,
               COUNT(*) as cnt,
               SUM(p.notional_amount) as notional
        FROM products p
        JOIN filings f ON p.filing_id = f.id
        WHERE {_SN_FILTER} {yc}
        GROUP BY ptype
        ORDER BY cnt DESC
    """, yp).fetchall()
    total = sum(r["cnt"] for r in rows)
    return [{"type": r["ptype"], "count": r["cnt"],
             "pct": round(r["cnt"] / total * 100, 1) if total else 0,
             "notional": r["notional"] or 0} for r in rows]


def issuer_share(conn: sqlite3.Connection, year: int | None = None) -> list[dict]:
    """Issuer market share for horizontal bar chart."""
    yc, yp = _year_clause(year)
    rows = conn.execute(f"""
        SELECT p.parent_issuer as issuer,
               COUNT(*) as cnt,
               SUM(p.notional_amount) as notional
        FROM products p
        JOIN filings f ON p.filing_id = f.id
        WHERE {_SN_FILTER} AND p.parent_issuer IS NOT NULL {yc}
        GROUP BY p.parent_issuer
        ORDER BY cnt DESC
    """, yp).fetchall()
    total = sum(r["cnt"] for r in rows)
    return [{"issuer": r["issuer"], "count": r["cnt"],
             "pct": round(r["cnt"] / total * 100, 1) if total else 0,
             "notional": r["notional"] or 0} for r in rows]


def top_underliers(conn: sqlite3.Connection, year: int | None = None, limit: int = 15) -> list[dict]:
    """Most popular underliers for horizontal bar chart."""
    yc, yp = _year_clause(year)
    rows = conn.execute(f"""
        SELECT p.underlier_names FROM products p
        JOIN filings f ON p.filing_id = f.id
        WHERE {_SN_FILTER} AND p.underlier_names IS NOT NULL
          AND p.underlier_names != '' {yc}
    """, yp).fetchall()

    counter: Counter = Counter()
    noise = {"Inc.", "Inc", "Corp.", "Corp", "Co.", "Ltd.", "PLC", "LLC",
             "Class A", "Class B", "N.A.", "Ltd", "Group",
             "Common Stock", "Ordinary Shares", "the", "and", "of"}
    for r in rows:
        parts = [x.strip() for x in r["underlier_names"].split(",")]
        for name in parts:
            if name and len(name) > 1 and name not in noise:
                counter[name] += 1

    total = sum(counter.values())
    return [{"name": name, "count": cnt,
             "pct": round(cnt / total * 100, 1) if total else 0}
            for name, cnt in counter.most_common(limit)]


def barrier_distribution(conn: sqlite3.Connection, year: int | None = None) -> list[dict]:
    """Barrier level distribution for histogram."""
    yc, yp = _year_clause(year)
    rows = conn.execute(f"""
        SELECT
            CASE
                WHEN p.barrier_level < 0.50 THEN '<50%'
                WHEN p.barrier_level < 0.60 THEN '50-60%'
                WHEN p.barrier_level < 0.70 THEN '60-70%'
                WHEN p.barrier_level < 0.80 THEN '70-80%'
                WHEN p.barrier_level < 0.90 THEN '80-90%'
                ELSE '90%+'
            END as bucket,
            COUNT(*) as cnt
        FROM products p
        JOIN filings f ON p.filing_id = f.id
        WHERE p.barrier_level IS NOT NULL AND {_SN_FILTER} {yc}
        GROUP BY bucket
        ORDER BY MIN(p.barrier_level)
    """, yp).fetchall()
    return [{"bucket": r["bucket"], "count": r["cnt"]} for r in rows]


def coupon_distribution(conn: sqlite3.Connection, year: int | None = None) -> list[dict]:
    """Coupon rate distribution for autocallables."""
    yc, yp = _year_clause(year)
    rows = conn.execute(f"""
        SELECT
            CASE
                WHEN p.coupon_rate < 0.05 THEN '<5%'
                WHEN p.coupon_rate < 0.08 THEN '5-8%'
                WHEN p.coupon_rate < 0.10 THEN '8-10%'
                WHEN p.coupon_rate < 0.12 THEN '10-12%'
                WHEN p.coupon_rate < 0.15 THEN '12-15%'
                ELSE '15%+'
            END as bucket,
            COUNT(*) as cnt
        FROM products p
        JOIN filings f ON p.filing_id = f.id
        WHERE p.coupon_rate IS NOT NULL AND p.coupon_rate > 0
          AND p.product_type = 'autocallable' AND {_SN_FILTER} {yc}
        GROUP BY bucket
        ORDER BY MIN(p.coupon_rate)
    """, yp).fetchall()
    return [{"bucket": r["bucket"], "count": r["cnt"]} for r in rows]


def annual_issuance(conn: sqlite3.Connection) -> list[dict]:
    """Annual product count and notional (no year filter — shows all years)."""
    rows = conn.execute(f"""
        SELECT CAST(strftime('%Y', f.filing_date) AS INTEGER) as year,
               COUNT(p.id) as cnt,
               SUM(p.notional_amount) as notional
        FROM products p
        JOIN filings f ON p.filing_id = f.id
        WHERE {_SN_FILTER}
        GROUP BY year
        ORDER BY year
    """).fetchall()
    return [{"year": r["year"], "count": r["cnt"],
             "notional": r["notional"] or 0} for r in rows]


# ---------------------------------------------------------------------------
# Explorer
# ---------------------------------------------------------------------------

def explorer_products(
    conn: sqlite3.Connection,
    issuer: str | None = None,
    product_type: str | None = None,
    underlier: str | None = None,
    year: int | None = None,
    page: int = 1,
    per_page: int = 50,
) -> dict:
    """Paginated product list for explorer table."""
    conditions = [_SN_FILTER]
    params: list = []

    if issuer:
        conditions.append("p.parent_issuer = ?")
        params.append(issuer)
    if product_type:
        conditions.append("p.product_type = ?")
        params.append(product_type)
    if underlier:
        conditions.append("p.underlier_names LIKE ?")
        params.append(f"%{underlier}%")
    if year:
        conditions.append("CAST(strftime('%Y', f.filing_date) AS INTEGER) = ?")
        params.append(year)

    where = " AND ".join(conditions)

    # Count
    total = conn.execute(f"""
        SELECT COUNT(*) FROM products p
        JOIN filings f ON p.filing_id = f.id
        WHERE {where}
    """, params).fetchone()[0]

    # Data
    offset = (page - 1) * per_page
    rows = conn.execute(f"""
        SELECT p.id, f.filing_date, p.parent_issuer, p.product_type,
               p.product_subtype, p.underlier_names, p.underlier_tickers,
               p.cusip, p.notional_amount, p.coupon_rate, p.barrier_level,
               p.maturity_date, p.upside_cap, p.participation_rate,
               p.asset_class, p.confidence
        FROM products p
        JOIN filings f ON p.filing_id = f.id
        WHERE {where}
        ORDER BY f.filing_date DESC
        LIMIT ? OFFSET ?
    """, params + [per_page, offset]).fetchall()

    products = []
    for r in rows:
        products.append({
            "id": r["id"],
            "filing_date": r["filing_date"],
            "issuer": r["parent_issuer"],
            "product_type": r["product_type"],
            "subtype": r["product_subtype"],
            "underliers": r["underlier_names"],
            "tickers": r["underlier_tickers"],
            "cusip": r["cusip"],
            "notional": r["notional_amount"],
            "coupon": r["coupon_rate"],
            "barrier": r["barrier_level"],
            "maturity": r["maturity_date"],
            "cap": r["upside_cap"],
            "participation": r["participation_rate"],
            "asset_class": r["asset_class"],
            "confidence": r["confidence"],
        })

    return {
        "products": products,
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": (total + per_page - 1) // per_page,
    }


def filter_options(conn: sqlite3.Connection) -> dict:
    """Get available filter values for dropdowns."""
    issuers = [r[0] for r in conn.execute(
        "SELECT DISTINCT parent_issuer FROM products WHERE parent_issuer IS NOT NULL ORDER BY parent_issuer"
    ).fetchall()]

    types = [r[0] for r in conn.execute(
        "SELECT DISTINCT product_type FROM products WHERE product_type IS NOT NULL ORDER BY product_type"
    ).fetchall()]

    years = [r[0] for r in conn.execute(
        "SELECT DISTINCT CAST(strftime('%Y', filing_date) AS INTEGER) as yr FROM filings WHERE extracted=1 ORDER BY yr DESC"
    ).fetchall()]

    return {"issuers": issuers, "types": types, "years": years}
