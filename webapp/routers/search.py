"""
EDGAR Search router - Search for trusts/registrants on SEC EDGAR.
Users can search and submit monitoring requests. Admin adds trusts from local system.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Trust
from webapp.services.sec_search import search_trusts, verify_cik

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
REQUESTS_FILE = PROJECT_ROOT / "trust_requests.txt"


@router.get("/search/")
def search_page(request: Request, q: str = "", db: Session = Depends(get_db)):
    """EDGAR search page. Shows results when ?q= is provided."""
    results = []
    existing_ciks = set()

    if q.strip():
        results = search_trusts(q)

        # Mark which CIKs we already monitor
        existing_ciks = set(
            row[0] for row in db.execute(select(Trust.cik)).all()
        )

    return templates.TemplateResponse("search.html", {
        "request": request,
        "q": q,
        "results": results,
        "existing_ciks": existing_ciks,
    })


@router.get("/search/verify/{cik}")
def verify_page(request: Request, cik: str, db: Session = Depends(get_db)):
    """Verify a CIK and show entity details before requesting monitoring."""
    details = verify_cik(cik)
    if not details:
        return templates.TemplateResponse("search_verify.html", {
            "request": request,
            "error": f"CIK {cik} not found on SEC EDGAR.",
            "details": None,
            "submitted": False,
        })

    # Check if already monitored
    existing = db.execute(
        select(Trust).where(Trust.cik == cik)
    ).scalar_one_or_none()

    return templates.TemplateResponse("search_verify.html", {
        "request": request,
        "details": details,
        "already_monitored": existing is not None,
        "error": None,
        "submitted": False,
    })


@router.post("/search/request")
def request_trust(request: Request, cik: str = Form(""), name: str = Form(""), db: Session = Depends(get_db)):
    """Submit a monitoring request. Admin reviews and adds from local system."""
    if not cik or not name:
        return templates.TemplateResponse("search_verify.html", {
            "request": request,
            "error": "Missing CIK or trust name.",
            "details": None,
            "submitted": False,
        })

    # Check if already monitored
    existing = db.execute(
        select(Trust).where(Trust.cik == cik)
    ).scalar_one_or_none()

    if existing:
        return templates.TemplateResponse("search_verify.html", {
            "request": request,
            "details": {"cik": cik, "name": name},
            "already_monitored": True,
            "error": None,
            "submitted": False,
        })

    # Log the request to file for admin review
    timestamp = datetime.now().isoformat(timespec="seconds")
    line = f"PENDING|{cik}|{name}|{timestamp}\n"
    with open(REQUESTS_FILE, "a", encoding="utf-8") as f:
        f.write(line)

    # Re-fetch details for the confirmation page
    details = verify_cik(cik)
    if not details:
        details = {"cik": cik, "name": name}

    return templates.TemplateResponse("search_verify.html", {
        "request": request,
        "details": details,
        "already_monitored": False,
        "error": None,
        "submitted": True,
    })
