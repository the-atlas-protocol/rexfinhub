"""
Downloads router - File downloads and CSV exports.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path

from fastapi import APIRouter, Depends, Request, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Trust, Filing, FundExtraction, FundStatus, MktMasterData

router = APIRouter(prefix="/downloads")
templates = Jinja2Templates(directory="webapp/templates")

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
OUTPUTS_DIR = PROJECT_ROOT / "outputs"

# REX trusts appear first in exports
_PRIORITY_TRUSTS = ["REX ETF Trust", "ETF Opportunities Trust"]


def _safe_path(requested: str) -> Path:
    """Resolve a requested path and ensure it's within OUTPUTS_DIR."""
    resolved = (OUTPUTS_DIR / requested).resolve()
    if not str(resolved).startswith(str(OUTPUTS_DIR.resolve())):
        raise HTTPException(status_code=403, detail="Access denied")
    if not resolved.exists():
        raise HTTPException(status_code=404, detail="File not found")
    return resolved


@router.get("/")
def downloads_page(request: Request, db: Session = Depends(get_db)):
    """List available downloads."""
    summary_files = []
    trust_files = []
    digest_files = []

    if OUTPUTS_DIR.exists():
        # Summary Excel files
        for f in sorted(OUTPUTS_DIR.glob("*.xlsx")):
            summary_files.append({
                "name": f.name,
                "path": f.name,
                "size": f"{f.stat().st_size / 1024:.0f} KB",
            })

        # Daily digest
        digest_path = OUTPUTS_DIR / "daily_digest.html"
        if digest_path.exists():
            digest_files.append({
                "name": "daily_digest.html",
                "path": "daily_digest.html",
                "size": f"{digest_path.stat().st_size / 1024:.0f} KB",
            })

        # Per-trust CSV files
        for folder in sorted(OUTPUTS_DIR.iterdir()):
            if not folder.is_dir():
                continue
            csvs = []
            for csv_file in sorted(folder.glob("*.csv")):
                csvs.append({
                    "name": csv_file.name,
                    "path": f"{folder.name}/{csv_file.name}",
                    "size": f"{csv_file.stat().st_size / 1024:.0f} KB",
                })
            if csvs:
                trust_files.append({
                    "trust_name": folder.name,
                    "files": csvs,
                })

    # Active trusts for per-trust filing exports (REX trusts first)
    all_trusts_raw = db.execute(
        select(Trust).where(Trust.is_active == True)
    ).scalars().all()

    def _sort_key(t):
        if t.name in _PRIORITY_TRUSTS:
            return (0, _PRIORITY_TRUSTS.index(t.name))
        return (1, t.name)

    all_trusts = sorted(all_trusts_raw, key=_sort_key)

    # Build a lookup of trust_files by trust_name for template use
    trust_files_map = {tf["trust_name"]: tf["files"] for tf in trust_files}

    return templates.TemplateResponse("downloads.html", {
        "request": request,
        "summary_files": summary_files,
        "trust_files": trust_files,
        "trust_files_map": trust_files_map,
        "digest_files": digest_files,
        "all_trusts": all_trusts,
        "total_trust_count": len(all_trusts),
    })


@router.get("/file")
def download_file(path: str):
    """Serve a file from the outputs directory."""
    resolved = _safe_path(path)
    return FileResponse(
        resolved,
        filename=resolved.name,
        media_type="application/octet-stream",
    )


@router.get("/export/funds")
def export_funds_csv(db: Session = Depends(get_db)):
    """Live CSV export of all fund statuses."""
    results = db.execute(
        select(FundStatus, Trust.name.label("trust_name"))
        .join(Trust, Trust.id == FundStatus.trust_id)
        .order_by(Trust.name, FundStatus.fund_name)
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Trust", "Fund Name", "Ticker", "Series ID",
        "Status", "Effective Date", "Latest Form",
        "Latest Filing Date", "Status Reason",
    ])
    for row in results:
        f = row.FundStatus
        writer.writerow([
            row.trust_name,
            f.fund_name,
            f.ticker or "",
            f.series_id or "",
            f.status or "",
            f.effective_date or "",
            f.latest_form or "",
            f.latest_filing_date or "",
            f.status_reason or "",
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=funds_export.csv"},
    )


@router.get("/export/filings")
def export_filings_csv(db: Session = Depends(get_db)):
    """Live CSV export of all filings across all trusts."""
    results = db.execute(
        select(
            Filing,
            Trust.name.label("trust_name"),
            FundExtraction.series_name,
            FundExtraction.class_name,
            FundExtraction.ticker,
            FundExtraction.effective_date,
            FundExtraction.confidence,
        )
        .join(Trust, Trust.id == Filing.trust_id)
        .outerjoin(FundExtraction, FundExtraction.filing_id == Filing.id)
        .order_by(Trust.name, Filing.filing_date.desc())
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Trust", "Filing Date", "Form", "Accession Number",
        "Series Name", "Class Name", "Ticker",
        "Effective Date", "Confidence", "Primary Link",
    ])
    for row in results:
        f = row.Filing
        writer.writerow([
            row.trust_name,
            f.filing_date or "",
            f.form or "",
            f.accession_number or "",
            row.series_name or "",
            row.class_name or "",
            row.ticker or "",
            row.effective_date or "",
            row.confidence or "",
            f.primary_link or "",
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=filings_export.csv"},
    )


@router.get("/export/trust/{trust_id}/filings")
def export_trust_filings(trust_id: int, db: Session = Depends(get_db)):
    """Export all filings for a specific trust as CSV."""
    trust = db.execute(
        select(Trust).where(Trust.id == trust_id)
    ).scalar_one_or_none()
    if not trust:
        raise HTTPException(status_code=404, detail="Trust not found")

    results = db.execute(
        select(
            Filing,
            FundExtraction.series_name,
            FundExtraction.class_name,
            FundExtraction.ticker,
            FundExtraction.effective_date,
            FundExtraction.confidence,
        )
        .outerjoin(FundExtraction, FundExtraction.filing_id == Filing.id)
        .where(Filing.trust_id == trust_id)
        .order_by(Filing.filing_date.desc(), FundExtraction.series_name)
    ).all()

    slug = trust.slug or trust.name.lower().replace(" ", "-")
    filename = f"{slug}_filings.csv"

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "Filing Date", "Form", "Accession Number",
        "Series Name", "Class Name", "Ticker",
        "Effective Date", "Confidence", "Primary Link",
    ])
    for row in results:
        f = row.Filing
        writer.writerow([
            f.filing_date or "",
            f.form or "",
            f.accession_number or "",
            row.series_name or "",
            row.class_name or "",
            row.ticker or "",
            row.effective_date or "",
            row.confidence or "",
            f.primary_link or "",
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


# ---------------------------------------------------------------------------
# Market data exports
# ---------------------------------------------------------------------------
_MARKET_MASTER_COLS = [
    ("ticker", "Ticker"),
    ("fund_name", "Fund Name"),
    ("issuer", "Issuer"),
    ("issuer_display", "Issuer Display"),
    ("etp_category", "Category"),
    ("category_display", "Category Display"),
    ("listed_exchange", "Exchange"),
    ("inception_date", "Inception Date"),
    ("fund_type", "Fund Type"),
    ("asset_class_focus", "Asset Class"),
    ("market_status", "Market Status"),
    ("is_rex", "REX Fund"),
    ("aum", "AUM ($M)"),
    ("fund_flow_1day", "Flow 1D ($M)"),
    ("fund_flow_1week", "Flow 1W ($M)"),
    ("fund_flow_1month", "Flow 1M ($M)"),
    ("fund_flow_ytd", "Flow YTD ($M)"),
    ("fund_flow_1year", "Flow 1Y ($M)"),
    ("expense_ratio", "Expense Ratio"),
    ("annualized_yield", "Annualized Yield"),
    ("total_return_1day", "Return 1D"),
    ("total_return_1week", "Return 1W"),
    ("total_return_1month", "Return 1M"),
    ("total_return_ytd", "Return YTD"),
    ("total_return_1year", "Return 1Y"),
    ("average_vol_30day", "Avg Vol 30D"),
    ("open_interest", "Open Interest"),
    ("uses_leverage", "Uses Leverage"),
    ("leverage_amount", "Leverage Amount"),
]


@router.get("/export/market/master")
def export_market_master(db: Session = Depends(get_db)):
    """CSV export of all market master data."""
    rows = db.execute(
        select(MktMasterData).order_by(MktMasterData.etp_category, MktMasterData.ticker)
    ).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _, label in _MARKET_MASTER_COLS])
    for r in rows:
        writer.writerow([getattr(r, col, "") for col, _ in _MARKET_MASTER_COLS])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=market_master_data.csv"},
    )


@router.get("/export/market/category-summary")
def export_market_category_summary(db: Session = Depends(get_db)):
    """CSV export of market data aggregated by category."""
    from sqlalchemy import func

    rows = db.execute(
        select(
            MktMasterData.category_display,
            func.count(MktMasterData.id).label("count"),
            func.sum(MktMasterData.aum).label("total_aum"),
            func.sum(MktMasterData.fund_flow_1week).label("flow_1w"),
            func.sum(MktMasterData.fund_flow_1month).label("flow_1m"),
            func.sum(MktMasterData.fund_flow_ytd).label("flow_ytd"),
        )
        .where(MktMasterData.category_display.isnot(None))
        .where(MktMasterData.category_display != "")
        .group_by(MktMasterData.category_display)
        .order_by(func.sum(MktMasterData.aum).desc())
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Category", "Fund Count", "Total AUM ($M)", "Flow 1W ($M)", "Flow 1M ($M)", "Flow YTD ($M)"])
    for r in rows:
        writer.writerow([
            r.category_display or "",
            r.count,
            round(r.total_aum or 0, 2),
            round(r.flow_1w or 0, 2),
            round(r.flow_1m or 0, 2),
            round(r.flow_ytd or 0, 2),
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=market_category_summary.csv"},
    )


@router.get("/export/market/issuer-summary")
def export_market_issuer_summary(db: Session = Depends(get_db)):
    """CSV export of market data aggregated by issuer."""
    from sqlalchemy import func

    rows = db.execute(
        select(
            MktMasterData.issuer_display,
            MktMasterData.etp_category,
            func.count(MktMasterData.id).label("count"),
            func.sum(MktMasterData.aum).label("total_aum"),
            func.sum(MktMasterData.fund_flow_1week).label("flow_1w"),
            func.sum(MktMasterData.fund_flow_1month).label("flow_1m"),
            func.sum(MktMasterData.fund_flow_ytd).label("flow_ytd"),
        )
        .where(MktMasterData.issuer_display.isnot(None))
        .group_by(MktMasterData.issuer_display, MktMasterData.etp_category)
        .order_by(func.sum(MktMasterData.aum).desc())
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Issuer", "Category", "Fund Count", "Total AUM ($M)", "Flow 1W ($M)", "Flow 1M ($M)", "Flow YTD ($M)"])
    for r in rows:
        writer.writerow([
            r.issuer_display or "",
            r.etp_category or "",
            r.count,
            round(r.total_aum or 0, 2),
            round(r.flow_1w or 0, 2),
            round(r.flow_1m or 0, 2),
            round(r.flow_ytd or 0, 2),
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=market_issuer_summary.csv"},
    )
