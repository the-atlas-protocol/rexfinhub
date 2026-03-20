"""Structured Notes — market overview from extraction database."""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.templating import Jinja2Templates

log = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")

# Prefer local D: drive (development), fall back to data/ (Render deployment)
_DB_PRIMARY = Path("D:/sec-data/databases/structured_notes.db")
_DB_FALLBACK = Path("data/structured_notes.db")
DB_PATH = _DB_PRIMARY if _DB_PRIMARY.exists() else _DB_FALLBACK


def _load_stats() -> dict:
    """Load structured notes stats from SQLite DB on D: drive."""
    import sqlite3

    stats = {
        "total_products": 0,
        "total_filings": 0,
        "issuers": 0,
        "date_min": "--",
        "date_max": "--",
        "by_issuer": [],
        "by_type": [],
        "by_year": [],
        "recent_products": [],
        "available": False,
    }

    if not DB_PATH.exists():
        return stats

    try:
        db = sqlite3.connect(str(DB_PATH))

        stats["total_products"] = db.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        stats["total_filings"] = db.execute("SELECT COUNT(*) FROM filings WHERE extracted = 1").fetchone()[0]
        stats["issuers"] = db.execute("SELECT COUNT(DISTINCT parent_issuer) FROM products").fetchone()[0]

        dates = db.execute("SELECT MIN(filing_date), MAX(filing_date) FROM filings WHERE extracted = 1").fetchone()
        stats["date_min"] = dates[0] or "--"
        stats["date_max"] = dates[1] or "--"

        # By issuer
        rows = db.execute("""
            SELECT parent_issuer, COUNT(*) as cnt
            FROM products GROUP BY parent_issuer
            ORDER BY cnt DESC
        """).fetchall()
        stats["by_issuer"] = [{"name": r[0], "count": r[1]} for r in rows]

        # By product type
        rows = db.execute("""
            SELECT COALESCE(product_type, 'unknown'), COUNT(*) as cnt
            FROM products GROUP BY product_type
            ORDER BY cnt DESC LIMIT 10
        """).fetchall()
        stats["by_type"] = [{"type": r[0], "count": r[1]} for r in rows]

        # By year
        rows = db.execute("""
            SELECT SUBSTR(f.filing_date, 1, 4) as yr, COUNT(*) as cnt
            FROM products p
            JOIN filings f ON p.filing_id = f.id
            WHERE f.filing_date IS NOT NULL
            GROUP BY SUBSTR(f.filing_date, 1, 4)
            ORDER BY yr DESC
            LIMIT 20
        """).fetchall()
        stats["by_year"] = [{"year": r[0], "count": r[1]} for r in rows if r[0]]

        # Recent products
        rows = db.execute("""
            SELECT p.product_name, p.parent_issuer, p.product_type,
                   p.underlier_tickers, p.coupon_rate, p.barrier_level,
                   f.filing_date, f.accession_number, i.cik
            FROM products p
            JOIN filings f ON p.filing_id = f.id
            JOIN issuers i ON f.issuer_id = i.id
            ORDER BY f.filing_date DESC
            LIMIT 15
        """).fetchall()
        stats["recent_products"] = [{
            "name": (r[0] or "")[:80],
            "issuer": r[1],
            "type": r[2] or "--",
            "underliers": r[3] or "--",
            "coupon": f"{r[4]*100:.1f}%" if r[4] else "--",
            "barrier": f"{r[5]*100:.0f}%" if r[5] else "--",
            "date": r[6],
            "filing_url": f"https://www.sec.gov/Archives/edgar/data/{r[8]}/{r[7].replace('-', '')}" if r[7] and r[8] else None,
        } for r in rows]

        stats["available"] = True
        db.close()
    except Exception as e:
        log.warning("Failed to load structured notes stats: %s", e)

    return stats


@router.get("/notes/")
def notes_overview(request: Request):
    """Structured notes market overview."""
    stats = _load_stats()
    return templates.TemplateResponse("notes_overview.html", {
        "request": request,
        "stats": stats,
    })


@router.get("/notes/issuers")
def notes_issuers(request: Request):
    """Issuer breakdown."""
    stats = _load_stats()
    return templates.TemplateResponse("notes_issuers.html", {
        "request": request,
        "stats": stats,
    })


@router.get("/notes/search")
def notes_search(request: Request, issuer: str = "", type: str = "", underlier: str = ""):
    """Product search."""
    import sqlite3

    results = []
    filters_applied = bool(issuer or type or underlier)

    if filters_applied and DB_PATH.exists():
        try:
            db = sqlite3.connect(str(DB_PATH))
            query = """
                SELECT p.product_name, p.parent_issuer, p.product_type,
                       p.underlier_tickers, p.coupon_rate, p.barrier_level,
                       p.maturity_date, f.filing_date, p.cusip,
                       f.accession_number, i.cik
                FROM products p
                JOIN filings f ON p.filing_id = f.id
                JOIN issuers i ON f.issuer_id = i.id
                WHERE 1=1
            """
            params = []
            if issuer:
                query += " AND p.parent_issuer = ?"
                params.append(issuer)
            if type:
                query += " AND p.product_type = ?"
                params.append(type)
            if underlier:
                query += " AND (p.underlier_tickers LIKE ? OR p.underlier_names LIKE ?)"
                params.extend([f"%{underlier}%", f"%{underlier}%"])
            query += " ORDER BY f.filing_date DESC LIMIT 100"

            rows = db.execute(query, params).fetchall()
            results = [{
                "name": (r[0] or "")[:80],
                "issuer": r[1],
                "type": r[2] or "--",
                "underliers": r[3] or "--",
                "coupon": f"{r[4]*100:.1f}%" if r[4] else "--",
                "barrier": f"{r[5]*100:.0f}%" if r[5] else "--",
                "maturity": r[6] or "--",
                "filed": r[7],
                "cusip": r[8] or "--",
                "sec_url": f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={r[10]}&type=424B2&dateb=&owner=include&count=10&search_text=&action=getcompany" if r[10] else None,
                "filing_url": f"https://www.sec.gov/Archives/edgar/data/{r[10]}/{r[9].replace('-', '')}" if r[9] and r[10] else None,
            } for r in rows]
            db.close()
        except Exception as e:
            log.warning("Search failed: %s", e)

    # Get filter options
    issuers = []
    types = []
    if DB_PATH.exists():
        try:
            db = sqlite3.connect(str(DB_PATH))
            issuers = [r[0] for r in db.execute("SELECT DISTINCT parent_issuer FROM products ORDER BY parent_issuer").fetchall()]
            types = [r[0] for r in db.execute("SELECT DISTINCT product_type FROM products WHERE product_type IS NOT NULL ORDER BY product_type").fetchall()]
            db.close()
        except Exception:
            pass

    return templates.TemplateResponse("notes_search.html", {
        "request": request,
        "results": results,
        "issuers": issuers,
        "types": types,
        "filter_issuer": issuer,
        "filter_type": type,
        "filter_underlier": underlier,
    })
