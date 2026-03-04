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

# Full column spec for analysts — identity, classification, mappings, metrics
_MARKET_MASTER_COLS = [
    # Identity
    ("ticker", "Ticker"),
    ("ticker_clean", "Ticker (Clean)"),
    ("fund_name", "Fund Name"),
    ("cusip", "CUSIP"),
    # Issuer
    ("issuer", "Issuer (Bloomberg)"),
    ("issuer_display", "Issuer"),
    ("issuer_nickname", "Issuer Short"),
    # Classification
    ("etp_category", "Category Code"),
    ("category_display", "Category"),
    ("primary_category", "Primary Category"),
    ("fund_category_key", "Category Key"),
    ("is_rex", "REX Fund"),
    ("rex_suite", "REX Suite"),
    # Structure
    ("fund_type", "Fund Type"),
    ("listed_exchange", "Exchange"),
    ("inception_date", "Inception Date"),
    ("asset_class_focus", "Asset Class"),
    ("regulatory_structure", "Regulatory Structure"),
    ("market_status", "Market Status"),
    # Leverage / Strategy
    ("uses_leverage", "Uses Leverage"),
    ("leverage_amount", "Leverage Amount"),
    ("uses_derivatives", "Uses Derivatives"),
    ("uses_swaps", "Uses Swaps"),
    ("is_40act", "40 Act"),
    ("outcome_type", "Outcome Type"),
    ("is_singlestock", "Single Stock"),
    ("is_active", "Active Mgmt"),
    ("is_crypto", "Crypto"),
    ("underlying_index", "Underlying Index"),
    ("strategy", "Strategy"),
    ("strategy_confidence", "Strategy Confidence"),
    ("underlier_type", "Underlier Type"),
    # L&I Mappings
    ("map_li_category", "L&I Category"),
    ("map_li_subcategory", "L&I Subcategory"),
    ("map_li_direction", "L&I Direction"),
    ("map_li_leverage_amount", "L&I Leverage"),
    ("map_li_underlier", "L&I Underlier"),
    # CC Mappings
    ("cc_type", "CC Type"),
    ("cc_category", "CC Category"),
    ("map_cc_underlier", "CC Underlier"),
    ("map_cc_index", "CC Index"),
    # Other Mappings
    ("map_crypto_is_spot", "Crypto Spot"),
    ("map_crypto_underlier", "Crypto Underlier"),
    ("map_defined_category", "Defined Outcome Cat"),
    ("map_thematic_category", "Thematic Cat"),
    # AUM & Flows
    ("aum", "AUM ($M)"),
    ("fund_flow_1day", "Flow 1D ($M)"),
    ("fund_flow_1week", "Flow 1W ($M)"),
    ("fund_flow_1month", "Flow 1M ($M)"),
    ("fund_flow_3month", "Flow 3M ($M)"),
    ("fund_flow_6month", "Flow 6M ($M)"),
    ("fund_flow_ytd", "Flow YTD ($M)"),
    ("fund_flow_1year", "Flow 1Y ($M)"),
    ("fund_flow_3year", "Flow 3Y ($M)"),
    # Returns
    ("total_return_1day", "Return 1D"),
    ("total_return_1week", "Return 1W"),
    ("total_return_1month", "Return 1M"),
    ("total_return_3month", "Return 3M"),
    ("total_return_6month", "Return 6M"),
    ("total_return_ytd", "Return YTD"),
    ("total_return_1year", "Return 1Y"),
    ("total_return_3year", "Return 3Y"),
    # Cost & Yield
    ("expense_ratio", "Expense Ratio"),
    ("management_fee", "Management Fee"),
    ("annualized_yield", "Annualized Yield"),
    # Trading
    ("average_bidask_spread", "Avg Bid-Ask Spread"),
    ("nav_tracking_error", "NAV Tracking Error"),
    ("percentage_premium", "Premium/Discount"),
    ("average_percent_premium_52week", "Avg Premium 52W"),
    ("average_vol_30day", "Avg Vol 30D"),
    ("percent_short_interest", "Short Interest %"),
    ("open_interest", "Open Interest"),
]

_VALID_EXPORT_COLS = {col for col, _ in _MARKET_MASTER_COLS}


def _dedup_query(query, db: Session) -> list:
    """Run query and deduplicate by ticker_clean, keeping first occurrence."""
    rows = db.execute(query).scalars().all()
    seen = set()
    deduped = []
    for r in rows:
        tc = getattr(r, "ticker_clean", None) or getattr(r, "ticker", "")
        if tc in seen:
            continue
        seen.add(tc)
        deduped.append(r)
    return deduped


def _write_market_csv(rows: list, cols: list[tuple[str, str]]) -> str:
    """Write market data rows to CSV string."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _, label in cols])
    for r in rows:
        writer.writerow([getattr(r, col, "") for col, _ in cols])
    return buf.getvalue()


@router.get("/export/market/master")
def export_market_master(db: Session = Depends(get_db)):
    """CSV export of all market master data (deduplicated)."""
    rows = _dedup_query(
        select(MktMasterData).order_by(MktMasterData.etp_category, MktMasterData.ticker),
        db,
    )
    return StreamingResponse(
        iter([_write_market_csv(rows, _MARKET_MASTER_COLS)]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=market_master_data.csv"},
    )


@router.get("/export/market/rex-only")
def export_market_rex_only(db: Session = Depends(get_db)):
    """CSV export of REX products only (deduplicated)."""
    rows = _dedup_query(
        select(MktMasterData)
        .where(MktMasterData.is_rex == True)
        .order_by(MktMasterData.etp_category, MktMasterData.ticker),
        db,
    )
    return StreamingResponse(
        iter([_write_market_csv(rows, _MARKET_MASTER_COLS)]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=rex_products.csv"},
    )


@router.get("/export/market/li")
def export_market_li(db: Session = Depends(get_db)):
    """CSV export of Leveraged & Inverse category (deduplicated)."""
    rows = _dedup_query(
        select(MktMasterData)
        .where(MktMasterData.etp_category == "LI")
        .order_by(MktMasterData.ticker),
        db,
    )
    return StreamingResponse(
        iter([_write_market_csv(rows, _MARKET_MASTER_COLS)]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=li_funds.csv"},
    )


@router.get("/export/market/cc")
def export_market_cc(db: Session = Depends(get_db)):
    """CSV export of Income (Covered Call) category (deduplicated)."""
    rows = _dedup_query(
        select(MktMasterData)
        .where(MktMasterData.etp_category == "CC")
        .order_by(MktMasterData.ticker),
        db,
    )
    return StreamingResponse(
        iter([_write_market_csv(rows, _MARKET_MASTER_COLS)]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=cc_funds.csv"},
    )


@router.get("/export/market/category-summary")
def export_market_category_summary(db: Session = Depends(get_db)):
    """CSV export of market data aggregated by category (deduplicated first)."""
    from sqlalchemy import func

    # Use subquery to dedup by ticker first, then aggregate
    sub = (
        select(
            MktMasterData.id,
            func.row_number().over(
                partition_by=MktMasterData.ticker_clean,
                order_by=MktMasterData.id,
            ).label("rn"),
        ).subquery()
    )
    base = (
        select(MktMasterData)
        .join(sub, MktMasterData.id == sub.c.id)
        .where(sub.c.rn == 1)
    ).subquery()

    rows = db.execute(
        select(
            base.c.category_display,
            func.count(base.c.id).label("count"),
            func.sum(base.c.aum).label("total_aum"),
            func.sum(base.c.fund_flow_1week).label("flow_1w"),
            func.sum(base.c.fund_flow_1month).label("flow_1m"),
            func.sum(base.c.fund_flow_ytd).label("flow_ytd"),
            func.sum(base.c.fund_flow_1year).label("flow_1y"),
        )
        .where(base.c.category_display.isnot(None))
        .where(base.c.category_display != "")
        .group_by(base.c.category_display)
        .order_by(func.sum(base.c.aum).desc())
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Category", "Fund Count", "Total AUM ($M)", "Flow 1W ($M)", "Flow 1M ($M)", "Flow YTD ($M)", "Flow 1Y ($M)"])
    for r in rows:
        writer.writerow([
            r.category_display or "", r.count,
            round(r.total_aum or 0, 2), round(r.flow_1w or 0, 2),
            round(r.flow_1m or 0, 2), round(r.flow_ytd or 0, 2),
            round(r.flow_1y or 0, 2),
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=market_category_summary.csv"},
    )


@router.get("/export/market/issuer-summary")
def export_market_issuer_summary(db: Session = Depends(get_db)):
    """CSV export of market data aggregated by issuer (deduplicated first)."""
    from sqlalchemy import func

    sub = (
        select(
            MktMasterData.id,
            func.row_number().over(
                partition_by=MktMasterData.ticker_clean,
                order_by=MktMasterData.id,
            ).label("rn"),
        ).subquery()
    )
    base = (
        select(MktMasterData)
        .join(sub, MktMasterData.id == sub.c.id)
        .where(sub.c.rn == 1)
    ).subquery()

    rows = db.execute(
        select(
            base.c.issuer_display,
            base.c.etp_category,
            func.count(base.c.id).label("count"),
            func.sum(base.c.aum).label("total_aum"),
            func.sum(base.c.fund_flow_1week).label("flow_1w"),
            func.sum(base.c.fund_flow_1month).label("flow_1m"),
            func.sum(base.c.fund_flow_ytd).label("flow_ytd"),
        )
        .where(base.c.issuer_display.isnot(None))
        .group_by(base.c.issuer_display, base.c.etp_category)
        .order_by(func.sum(base.c.aum).desc())
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Issuer", "Category", "Fund Count", "Total AUM ($M)", "Flow 1W ($M)", "Flow 1M ($M)", "Flow YTD ($M)"])
    for r in rows:
        writer.writerow([
            r.issuer_display or "", r.etp_category or "", r.count,
            round(r.total_aum or 0, 2), round(r.flow_1w or 0, 2),
            round(r.flow_1m or 0, 2), round(r.flow_ytd or 0, 2),
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=market_issuer_summary.csv"},
    )


@router.get("/export/market/underlier-summary")
def export_market_underlier_summary(db: Session = Depends(get_db)):
    """CSV export of AUM/flows by underlier (single stock universe, deduplicated)."""
    from sqlalchemy import func

    sub = (
        select(
            MktMasterData.id,
            func.row_number().over(
                partition_by=MktMasterData.ticker_clean,
                order_by=MktMasterData.id,
            ).label("rn"),
        ).subquery()
    )
    base = (
        select(MktMasterData)
        .join(sub, MktMasterData.id == sub.c.id)
        .where(sub.c.rn == 1)
    ).subquery()

    rows = db.execute(
        select(
            base.c.map_li_underlier,
            func.count(base.c.id).label("count"),
            func.sum(base.c.aum).label("total_aum"),
            func.sum(base.c.fund_flow_1week).label("flow_1w"),
            func.sum(base.c.fund_flow_1month).label("flow_1m"),
            func.sum(base.c.fund_flow_ytd).label("flow_ytd"),
        )
        .where(base.c.map_li_underlier.isnot(None))
        .where(base.c.map_li_underlier != "")
        .group_by(base.c.map_li_underlier)
        .order_by(func.sum(base.c.aum).desc())
    ).all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Underlier", "Fund Count", "Total AUM ($M)", "Flow 1W ($M)", "Flow 1M ($M)", "Flow YTD ($M)"])
    for r in rows:
        writer.writerow([
            r.map_li_underlier or "", r.count,
            round(r.total_aum or 0, 2), round(r.flow_1w or 0, 2),
            round(r.flow_1m or 0, 2), round(r.flow_ytd or 0, 2),
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=underlier_summary.csv"},
    )


@router.get("/export/market/timeseries")
def export_market_timeseries(db: Session = Depends(get_db)):
    """CSV export of AUM time series (monthly snapshots, all tickers)."""
    from webapp.models import MktTimeSeries

    rows = db.execute(
        select(MktTimeSeries)
        .order_by(MktTimeSeries.ticker, MktTimeSeries.months_ago)
    ).scalars().all()

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Ticker", "Months Ago", "AUM ($M)", "Category", "Issuer", "REX Fund"])
    for r in rows:
        writer.writerow([
            r.ticker or "", r.months_ago,
            round(r.aum_value or 0, 2),
            r.category_display or "", r.issuer_display or "",
            r.is_rex,
        ])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=aum_timeseries.csv"},
    )


# ---------------------------------------------------------------------------
# Ad-hoc export API
# ---------------------------------------------------------------------------


@router.get("/export/adhoc")
def adhoc_export(
    db: Session = Depends(get_db),
    category: str | None = None,
    issuer: str | None = None,
    rex_only: bool = False,
    fund_type: str | None = None,
    columns: str | None = None,
    sort: str | None = None,
    limit: int | None = None,
):
    """Ad-hoc CSV export with optional filters, column selection, sorting, and limit.

    Query params:
        category: filter by etp_category (e.g. LI, CC)
        issuer: filter by issuer_display
        rex_only: if true, only REX products
        fund_type: filter by fund_type (e.g. ETF)
        columns: comma-separated column names (validated against _MARKET_MASTER_COLS)
        sort: column name, prefix with - for desc (e.g. -aum)
        limit: max rows
    """
    query = select(MktMasterData)

    if category:
        query = query.where(MktMasterData.etp_category == category)
    if issuer:
        query = query.where(MktMasterData.issuer_display == issuer)
    if rex_only:
        query = query.where(MktMasterData.is_rex == True)
    if fund_type:
        query = query.where(MktMasterData.fund_type == fund_type)

    # Sort
    if sort:
        desc = sort.startswith("-")
        sort_col = sort.lstrip("-")
        if sort_col in _VALID_EXPORT_COLS and hasattr(MktMasterData, sort_col):
            col_attr = getattr(MktMasterData, sort_col)
            query = query.order_by(col_attr.desc() if desc else col_attr)

    # Deduplicate first, then apply limit
    rows = _dedup_query(query, db)

    if limit and limit > 0:
        rows = rows[:min(limit, 10000)]

    # Column selection
    if columns:
        selected = [c.strip() for c in columns.split(",") if c.strip() in _VALID_EXPORT_COLS]
    else:
        selected = [col for col, _ in _MARKET_MASTER_COLS]

    if not selected:
        selected = [col for col, _ in _MARKET_MASTER_COLS]

    # Column labels
    col_label_map = dict(_MARKET_MASTER_COLS)
    headers = [col_label_map.get(c, c) for c in selected]

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for r in rows:
        writer.writerow([getattr(r, col, "") for col in selected])

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=export.csv"},
    )
