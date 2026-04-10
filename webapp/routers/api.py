"""
REST API router for N8N and external integrations.

All endpoints require X-API-Key header for authentication.
Prefix: /api/v1
"""
from __future__ import annotations

import os
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, BackgroundTasks, Request, UploadFile, File
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import Trust, Filing, FundStatus, PipelineRun

router = APIRouter(prefix="/api/v1", tags=["api"])


def _load_api_key() -> str:
    """Load API key from .env or environment."""
    env_file = Path(__file__).resolve().parent.parent.parent / "config" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("API_KEY="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return os.environ.get("API_KEY", "")


def verify_api_key(x_api_key: str = Header(default="")):
    """Verify API key from X-API-Key header."""
    import hmac
    expected = _load_api_key()
    if not expected:
        raise HTTPException(status_code=503, detail="API not configured")
    if not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status_code=401, detail="Invalid API key")


@router.get("/health")
def api_health(_: None = Depends(verify_api_key)):
    """Health check. Requires X-API-Key."""
    return {"status": "ok", "version": "2.0.0"}


@router.get("/trusts")
def list_trusts(
    active_only: bool = True,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    """List all monitored trusts with fund counts."""
    query = select(
        Trust.id, Trust.cik, Trust.name, Trust.slug, Trust.is_rex, Trust.is_active,
        func.count(FundStatus.id).label("fund_count"),
    ).join(FundStatus, FundStatus.trust_id == Trust.id, isouter=True).group_by(Trust.id)

    if active_only:
        query = query.where(Trust.is_active == True)

    rows = db.execute(query).all()
    return [
        {
            "id": r.id, "cik": r.cik, "name": r.name, "slug": r.slug,
            "is_rex": r.is_rex, "is_active": r.is_active, "fund_count": r.fund_count,
        }
        for r in rows
    ]


@router.get("/funds")
def list_funds(
    status: str | None = None,
    trust: str | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    """Query funds with optional filters."""
    query = (
        select(FundStatus, Trust.name.label("trust_name"))
        .join(Trust, Trust.id == FundStatus.trust_id)
    )

    if status:
        query = query.where(FundStatus.status == status.upper())
    if trust:
        query = query.where(Trust.name.ilike(f"%{trust}%"))

    query = query.order_by(FundStatus.latest_filing_date.desc().nullslast()).limit(limit)
    rows = db.execute(query).all()

    return [
        {
            "id": r.FundStatus.id,
            "trust_name": r.trust_name,
            "fund_name": r.FundStatus.fund_name,
            "ticker": r.FundStatus.ticker,
            "status": r.FundStatus.status,
            "status_reason": r.FundStatus.status_reason,
            "effective_date": str(r.FundStatus.effective_date) if r.FundStatus.effective_date else None,
            "latest_form": r.FundStatus.latest_form,
            "latest_filing_date": str(r.FundStatus.latest_filing_date) if r.FundStatus.latest_filing_date else None,
            "series_id": r.FundStatus.series_id,
            "class_contract_id": r.FundStatus.class_contract_id,
        }
        for r in rows
    ]


@router.get("/filings/recent")
def recent_filings(
    days: int = 1,
    form: str | None = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    """Get recent filings within N days."""
    since = date.today() - timedelta(days=days)
    query = (
        select(Filing, Trust.name.label("trust_name"))
        .join(Trust, Trust.id == Filing.trust_id)
        .where(Filing.filing_date >= since)
        .order_by(Filing.filing_date.desc())
        .limit(limit)
    )

    if form:
        query = query.where(Filing.form == form.upper())

    rows = db.execute(query).all()
    return [
        {
            "id": r.Filing.id,
            "trust_name": r.trust_name,
            "accession_number": r.Filing.accession_number,
            "form": r.Filing.form,
            "filing_date": str(r.Filing.filing_date) if r.Filing.filing_date else None,
            "primary_link": r.Filing.primary_link,
        }
        for r in rows
    ]


@router.get("/pipeline/status")
def pipeline_status(db: Session = Depends(get_db), _: None = Depends(verify_api_key)):
    """Get the status of the most recent pipeline run."""
    run = db.execute(
        select(PipelineRun).order_by(PipelineRun.started_at.desc()).limit(1)
    ).scalar_one_or_none()

    if not run:
        return {"status": "never_run", "last_run": None}

    return {
        "status": run.status,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
        "trusts_processed": run.trusts_processed,
        "filings_found": run.filings_found,
        "error_message": run.error_message,
        "triggered_by": run.triggered_by,
    }


@router.post("/pipeline/run")
def trigger_pipeline(
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    """Trigger a pipeline run in the background."""
    from webapp.services.pipeline_service import run_pipeline_background, is_pipeline_running

    if is_pipeline_running():
        return {"status": "already_running", "message": "Pipeline is already running"}

    background_tasks.add_task(run_pipeline_background)
    return {"status": "started", "message": "Pipeline run started in background"}


# Legacy /digest/send removed (was CSV-based, dead code).
# Use /admin/digest/send (DB-based) or scripts/send_email.py instead.


@router.post("/db/upload")
async def upload_db(
    file: UploadFile = File(...),
    _: None = Depends(verify_api_key),
):
    """Replace the database file with an uploaded copy.

    Accepts raw or gzipped (.gz) SQLite DB files.
    Streams to disk in 64KB chunks to stay under Render's 512MB RAM.
    Gzipped uploads decompress in streaming chunks (never buffers full file).
    After replacement, re-initializes DB and re-warms caches so the app
    continues serving without a redeploy.
    """
    import gzip as _gzip
    from webapp.database import DB_PATH, engine, init_db

    is_gzipped = (file.filename or "").endswith(".gz") or file.content_type == "application/gzip"
    tmp_path = str(DB_PATH) + ".uploading"
    try:
        total_in = 0
        total_out = 0
        if is_gzipped:
            # Render disk is 1GB. Old DB (~455MB) + gz (~63MB) + decompressed (~455MB) = 973MB.
            # To stay under limit: stream gz to disk, delete old DB, then decompress.
            gz_tmp = tmp_path + ".gz"
            # Step 1: Stream compressed data to disk (455MB existing + 63MB gz = 518MB)
            with open(gz_tmp, "wb") as f:
                while True:
                    chunk = await file.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    total_in += len(chunk)
            # Step 2: Dispose engine and remove old DB + stale WAL/SHM files
            engine.dispose()
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.unlink(str(DB_PATH) + suffix)
                except OSError:
                    pass
            # Step 3: Decompress gz -> new DB (63MB gz + 455MB out = 518MB peak)
            with _gzip.open(gz_tmp, "rb") as gz_in:
                with open(tmp_path, "wb") as f_out:
                    while True:
                        chunk = gz_in.read(65536)
                        if not chunk:
                            break
                        f_out.write(chunk)
                        total_out += len(chunk)
            try:
                os.unlink(gz_tmp)
            except OSError:
                pass
        else:
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = await file.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    total_out += len(chunk)
            total_in = total_out
            # Dispose existing connections and clean up stale WAL/SHM files
            engine.dispose()
            for suffix in ("-wal", "-shm"):
                try:
                    os.unlink(str(DB_PATH) + suffix)
                except OSError:
                    pass

        # Move new DB into place
        shutil.move(tmp_path, str(DB_PATH))

        # Re-initialize: migrate schema + re-warm caches
        init_db()
        try:
            from webapp.main import _prewarm_caches
            _prewarm_caches()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Cache re-warm after DB upload failed (non-fatal): %s", e)

        in_mb = total_in / 1_000_000
        out_mb = total_out / 1_000_000
        msg = f"Database replaced ({out_mb:.1f} MB)"
        if is_gzipped:
            msg = f"Database replaced ({in_mb:.1f} MB gzipped -> {out_mb:.1f} MB)"
        return {"status": "ok", "message": msg}
    except Exception as e:
        for p in [tmp_path, tmp_path + ".gz"]:
            try:
                os.unlink(p)
            except OSError:
                pass
        import logging
        logging.getLogger(__name__).error("DB upload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


@router.post("/db/upload-notes")
async def upload_notes_db(
    file: UploadFile = File(...),
    _: None = Depends(verify_api_key),
):
    """Replace the structured_notes database with an uploaded copy.

    Streams to disk in 64KB chunks. Accepts raw or gzipped (.gz) SQLite DB.
    """
    import gzip as _gzip

    notes_db_path = Path("data/structured_notes.db")
    notes_db_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = str(notes_db_path) + ".uploading"

    try:
        is_gzipped = (file.filename or "").endswith(".gz") or file.content_type == "application/gzip"
        total_in = 0
        total_out = 0

        if is_gzipped:
            gz_tmp = tmp_path + ".gz"
            with open(gz_tmp, "wb") as f:
                while True:
                    chunk = await file.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    total_in += len(chunk)
            # Remove old DB to free space before decompressing
            try:
                os.unlink(str(notes_db_path))
            except OSError:
                pass
            with _gzip.open(gz_tmp, "rb") as gz_in:
                with open(tmp_path, "wb") as f_out:
                    while True:
                        chunk = gz_in.read(65536)
                        if not chunk:
                            break
                        f_out.write(chunk)
                        total_out += len(chunk)
            try:
                os.unlink(gz_tmp)
            except OSError:
                pass
        else:
            with open(tmp_path, "wb") as f:
                while True:
                    chunk = await file.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    total_out += len(chunk)
            total_in = total_out

        shutil.move(tmp_path, str(notes_db_path))

        in_mb = total_in / 1_000_000
        out_mb = total_out / 1_000_000
        msg = f"Notes DB replaced ({out_mb:.1f} MB)"
        if is_gzipped:
            msg = f"Notes DB replaced ({in_mb:.1f} MB gzipped -> {out_mb:.1f} MB)"
        return {"status": "ok", "message": msg}
    except Exception as e:
        for p in [tmp_path, tmp_path + ".gz"]:
            try:
                os.unlink(p)
            except OSError:
                pass
        import logging
        logging.getLogger(__name__).error("Notes DB upload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# ETP Screener API (public-facing, API key required)
# ---------------------------------------------------------------------------

from fastapi import Query


@router.get("/etp/screener", summary="ETP Screener Data",
            description="Returns all active ETF/ETN fund data with 50+ fields. "
                        "Use `scope` to filter: `all` (default), `rex` (REX products only), "
                        "`competitors` (non-REX only). Use `ticker` to filter to specific tickers (comma-separated). "
                        "Use `category` to filter by etp_category (LI, CC, Crypto, Defined, Thematic).")
def etp_screener(
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
    scope: str = Query(default="all", description="all | rex | competitors"),
    ticker: str = Query(default="", description="Comma-separated tickers (e.g. NVDX US,TSLL US)"),
    category: str = Query(default="", description="Filter by etp_category"),
    limit: int = Query(default=0, description="Max results (0 = all)"),
):
    """Full ETP screener dataset. Requires X-API-Key header."""
    import math
    from sqlalchemy import text as sa_text

    cols = (
        "ticker, fund_name, issuer_display, aum, market_status, "
        "etp_category, category_display, is_rex, rex_suite, "
        "total_return_1day, total_return_1week, total_return_1month, "
        "total_return_3month, total_return_6month, total_return_ytd, total_return_1year, "
        "total_return_3year, annualized_yield, "
        "expense_ratio, management_fee, average_vol_30day, open_interest, "
        "percent_short_interest, average_bidask_spread, nav_tracking_error, percentage_premium, "
        "average_percent_premium_52week, "
        "fund_flow_1day, fund_flow_1week, fund_flow_1month, fund_flow_3month, "
        "fund_flow_6month, fund_flow_ytd, fund_flow_1year, "
        "inception_date, fund_type, asset_class_focus, underlying_index, "
        "is_singlestock, uses_leverage, leverage_amount, outcome_type, is_crypto, "
        "strategy, underlier_type, cusip, listed_exchange, regulatory_structure, "
        "map_li_direction, map_li_leverage_amount, map_li_underlier, "
        "map_cc_underlier, map_crypto_underlier, map_defined_category, "
        "map_thematic_category, cc_type, cc_category, strategy_confidence, "
        "uses_derivatives, uses_swaps, is_40act, index_weighting_methodology"
    )
    query = f"SELECT {cols} FROM mkt_master_data WHERE market_status = 'ACTV' AND (fund_type = 'ETF' OR fund_type = 'ETN')"
    if scope == "rex":
        query += " AND is_rex = 1"
    elif scope == "competitors":
        query += " AND is_rex = 0"
    if category:
        query += f" AND etp_category = '{category}'"
    if ticker:
        tickers = [t.strip() for t in ticker.split(",") if t.strip()]
        if tickers:
            in_list = ",".join(f"'{t}'" for t in tickers)
            query += f" AND ticker IN ({in_list})"
    query += " ORDER BY aum DESC"
    if limit > 0:
        query += f" LIMIT {limit}"

    rows = db.execute(sa_text(query)).fetchall()
    col_names = [c.strip() for c in cols.split(",")]

    funds = []
    for row in rows:
        d = {}
        for i, col in enumerate(col_names):
            val = row[i]
            if val is None:
                d[col] = None
            elif isinstance(val, float):
                d[col] = None if (math.isnan(val) or math.isinf(val)) else round(val, 6)
            elif isinstance(val, int):
                d[col] = val
            else:
                d[col] = str(val)
        funds.append(d)

    return {"funds": funds, "count": len(funds), "scope": scope}


@router.get("/etp/rex-summary", summary="REX Fund Summary",
            description="Returns summary KPIs for REX products: total AUM, fund count, flows, top performer.")
def etp_rex_summary(
    db: Session = Depends(get_db),
    _: None = Depends(verify_api_key),
):
    """REX product summary. Requires X-API-Key header."""
    import math
    from sqlalchemy import text as sa_text

    rows = db.execute(sa_text(
        "SELECT ticker, fund_name, rex_suite, aum, "
        "total_return_1day, fund_flow_1week, fund_flow_1month "
        "FROM mkt_master_data WHERE is_rex = 1 AND market_status = 'ACTV' "
        "AND (fund_type = 'ETF' OR fund_type = 'ETN') "
        "ORDER BY aum DESC"
    )).fetchall()

    funds = []
    total_aum = 0
    best = None
    for r in rows:
        aum = float(r[3] or 0)
        ret_1d = float(r[4]) if r[4] and not math.isnan(float(r[4])) else None
        total_aum += aum
        fund = {"ticker": r[0], "fund_name": r[1], "suite": r[2], "aum": round(aum, 2),
                "return_1d": round(ret_1d, 4) if ret_1d else None,
                "flow_1w": round(float(r[5] or 0), 2), "flow_1m": round(float(r[6] or 0), 2)}
        funds.append(fund)
        if ret_1d is not None and (best is None or ret_1d > best["return_1d"]):
            best = fund

    return {
        "total_aum_millions": round(total_aum, 2),
        "fund_count": len(funds),
        "best_1d_performer": best,
        "funds": funds,
    }


@router.get("/returns", summary="Total Return Data",
            description="Fetches total return price series, drawdowns, growth of $10K, "
                        "annual returns, and return stats for any ETF/ETN symbols. "
                        "Data sourced from TotalRealReturns.com.")
def total_returns_api(
    _: None = Depends(verify_api_key),
    symbols: str = Query(description="Comma-separated tickers (e.g. NVII,NVDY,JEPI)"),
    start: str = Query(default="", description="Start date YYYY-MM-DD (optional)"),
    end: str = Query(default="", description="End date YYYY-MM-DD (optional)"),
):
    """Total return comparison data. Requires X-API-Key."""
    from scripts.scrape_total_returns import scrape

    symbol_list = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not symbol_list:
        raise HTTPException(status_code=400, detail="No symbols provided")
    if len(symbol_list) > 10:
        raise HTTPException(status_code=400, detail="Max 10 symbols per request")

    try:
        result = scrape(symbol_list, start, end)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Data fetch failed: {str(e)}")

    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])

    return result


@router.get("/docs", include_in_schema=False)
def api_docs_page(request: Request):
    """Public API documentation page."""
    from fastapi.templating import Jinja2Templates
    templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))
    base_url = str(request.base_url).rstrip("/")
    return templates.TemplateResponse("api_docs.html", {
        "request": request,
        "base_url": base_url,
    })
