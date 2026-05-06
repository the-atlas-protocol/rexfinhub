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

from webapp.database import get_live_feed_db
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

    Reverted to the file-rename approach after Connection.backup() crashed
    Render mid-upload (likely contention with SQLAlchemy's pool during page
    copy under load). The brief engine.dispose() + rename window is known
    to work; the in-place backup approach needs more investigation.

    Accepts raw or gzipped (.gz) SQLite DB files. Streams to disk in 64KB
    chunks to stay under Render's 512MB RAM cap.
    """
    import gzip as _gzip
    from webapp.database import DB_PATH, engine, init_db

    is_gzipped = (file.filename or "").endswith(".gz") or file.content_type == "application/gzip"
    tmp_path = str(DB_PATH) + ".uploading"
    try:
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
            engine.dispose()
            for suffix in ("-wal", "-shm"):
                try:
                    os.unlink(str(DB_PATH) + suffix)
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
            engine.dispose()
            for suffix in ("-wal", "-shm"):
                try:
                    os.unlink(str(DB_PATH) + suffix)
                except OSError:
                    pass

        shutil.move(tmp_path, str(DB_PATH))

        init_db()
        # Schedule cache re-warm in the background so the upload handler
        # can return immediately. Running _prewarm_caches() inline with a
        # big freshly-swapped DB can take minutes, during which the POST
        # connection times out and Render flags the worker as stuck —
        # producing "Response ended prematurely" and a multi-minute
        # recovery window. The background thread also catches its own
        # failures so a broken prewarm never takes the service down.
        try:
            import threading
            def _warm():
                try:
                    from webapp.main import _prewarm_caches
                    _prewarm_caches()
                except Exception as _warm_exc:
                    import logging
                    logging.getLogger(__name__).warning(
                        "Background cache re-warm failed (non-fatal): %s", _warm_exc
                    )
            threading.Thread(target=_warm, name="post-upload-warm", daemon=True).start()
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning("Could not schedule cache re-warm: %s", e)

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
# Pre-baked reports — VPS generates HTML, uploads here, admin reads static file
# ---------------------------------------------------------------------------

PREBAKED_REPORTS_DIR = Path("data/prebaked_reports")

# Allowed report keys — prevents path traversal and restricts to known reports
ALLOWED_REPORT_KEYS = {
    "daily_filing", "weekly_report", "li_report", "income_report", "flow_report",
    "autocall_report", "intelligence_brief", "filing_screener", "product_status",
}


@router.post("/reports/upload/{report_key}")
async def upload_prebaked_report(
    report_key: str,
    file: UploadFile = File(...),
    _: None = Depends(verify_api_key),
):
    """Upload a pre-baked report HTML. VPS builds it; Render just serves it.

    The file is written to data/prebaked_reports/{report_key}.html with an
    atomic temp-file-then-rename. A sidecar .meta.json records baked_at.
    """
    import json as _json
    from datetime import datetime as _dt

    if report_key not in ALLOWED_REPORT_KEYS:
        raise HTTPException(status_code=400, detail=f"Unknown report key: {report_key}")

    PREBAKED_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    target = PREBAKED_REPORTS_DIR / f"{report_key}.html"
    tmp = target.with_suffix(".html.uploading")
    meta = PREBAKED_REPORTS_DIR / f"{report_key}.meta.json"

    try:
        total = 0
        with open(tmp, "wb") as f:
            while True:
                chunk = await file.read(65536)
                if not chunk:
                    break
                f.write(chunk)
                total += len(chunk)

        if total < 100:  # reject empty / tiny files
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise HTTPException(status_code=400, detail=f"Report too small: {total} bytes")

        shutil.move(str(tmp), str(target))

        meta.write_text(_json.dumps({
            "report_key": report_key,
            "size_bytes": total,
            "baked_at": _dt.utcnow().isoformat() + "Z",
        }))

        return {
            "status": "ok",
            "report_key": report_key,
            "size_bytes": total,
            "path": str(target),
        }
    except HTTPException:
        raise
    except Exception as e:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        import logging
        logging.getLogger(__name__).error("Report upload failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"Upload failed: {str(e)[:200]}")


@router.get("/reports/list")
def list_prebaked_reports(_: None = Depends(verify_api_key)):
    """List all pre-baked reports with metadata."""
    import json as _json

    if not PREBAKED_REPORTS_DIR.exists():
        return {"reports": []}

    reports = []
    for key in sorted(ALLOWED_REPORT_KEYS):
        html_path = PREBAKED_REPORTS_DIR / f"{key}.html"
        meta_path = PREBAKED_REPORTS_DIR / f"{key}.meta.json"
        if not html_path.exists():
            continue
        meta = {}
        if meta_path.exists():
            try:
                meta = _json.loads(meta_path.read_text())
            except Exception:
                pass
        reports.append({
            "report_key": key,
            "size_bytes": html_path.stat().st_size,
            "baked_at": meta.get("baked_at"),
            "exists": True,
        })
    return {"reports": reports}


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

    # Build parameterized query — user inputs bound as :params, never interpolated.
    where_clauses = [
        "market_status = 'ACTV'",
        "(fund_type = 'ETF' OR fund_type = 'ETN')",
    ]
    bind_params: dict[str, object] = {}

    if scope == "rex":
        where_clauses.append("is_rex = 1")
    elif scope == "competitors":
        where_clauses.append("is_rex = 0")

    if category:
        where_clauses.append("etp_category = :category")
        bind_params["category"] = category

    tickers: list[str] = []
    if ticker:
        tickers = [t.strip() for t in ticker.split(",") if t.strip()]
        if tickers:
            # SQLAlchemy text() supports :param_N style for IN lists
            placeholders = ", ".join(f":ticker_{i}" for i in range(len(tickers)))
            where_clauses.append(f"ticker IN ({placeholders})")
            for i, t in enumerate(tickers):
                bind_params[f"ticker_{i}"] = t

    sql = f"SELECT {cols} FROM mkt_master_data WHERE {' AND '.join(where_clauses)} ORDER BY aum DESC"
    if limit > 0:
        sql += f" LIMIT {limit}"

    rows = db.execute(sa_text(sql), bind_params).fetchall()
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


# ---------------------------------------------------------------------------
# Live feed — real-time filing notifications
# ---------------------------------------------------------------------------
#
# Why a separate code path: the full /db/upload endpoint does engine.dispose()
# + file-rename + init_db() + prewarm, which takes Render offline for ~4
# minutes. That's unacceptable for "breaking-news filing detected" UX.
#
# These endpoints do single-row inserts and single-row reads against a
# dedicated `live_feed` table. No engine dispose, no swap, no restart. The
# VPS atom watcher + single_filing_worker POST here every time they see
# something new. Browsers poll /live/recent every 30s and toast new rows.

@router.post("/live/push")
async def live_push(
    request: Request,
    _: None = Depends(verify_api_key),
    db: Session = Depends(get_live_feed_db),
):
    """Push a single filing into the live feed.

    Body (JSON):
      {
        "accession_number": "0001234567-26-000123",  # required, unique
        "form": "485BPOS",                            # required
        "cik": "1683471",                             # optional
        "company_name": "ETF Opportunities Trust",    # optional
        "trust_id": 42,                               # optional
        "trust_slug": "etf-opportunities-trust",      # optional
        "trust_name": "ETF Opportunities Trust",      # optional
        "filed_date": "2026-04-14",                   # optional, ISO date
        "primary_doc_url": "https://www.sec.gov/...", # optional
        "source": "atom"                              # optional: atom|reconciler|bulk
      }

    Upsert by accession_number — safe to call multiple times for the same
    filing (idempotent). Returns {"status": "ok", "id": N, "created": bool}.

    Also opportunistically prunes the table to the last 500 rows to keep
    the feed tight and queries O(1).
    """
    from datetime import datetime as _dt, date as _date
    from webapp.models import LiveFeedItem

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    accession = (body.get("accession_number") or "").strip()
    form = (body.get("form") or "").strip()
    if not accession or not form:
        raise HTTPException(
            status_code=400,
            detail="accession_number and form are required",
        )

    filed_date = None
    fd_raw = body.get("filed_date")
    if fd_raw:
        try:
            filed_date = _date.fromisoformat(str(fd_raw)[:10])
        except (ValueError, TypeError):
            pass

    existing = db.execute(
        select(LiveFeedItem).where(LiveFeedItem.accession_number == accession)
    ).scalar_one_or_none()

    created = False
    if existing:
        existing.form = form
        existing.cik = body.get("cik") or existing.cik
        existing.company_name = body.get("company_name") or existing.company_name
        existing.trust_id = body.get("trust_id") or existing.trust_id
        existing.trust_slug = body.get("trust_slug") or existing.trust_slug
        existing.trust_name = body.get("trust_name") or existing.trust_name
        existing.filed_date = filed_date or existing.filed_date
        existing.primary_doc_url = body.get("primary_doc_url") or existing.primary_doc_url
        existing.source = body.get("source") or existing.source
        row = existing
    else:
        row = LiveFeedItem(
            detected_at=_dt.utcnow(),
            accession_number=accession,
            cik=body.get("cik"),
            form=form,
            company_name=body.get("company_name"),
            trust_id=body.get("trust_id"),
            trust_slug=body.get("trust_slug"),
            trust_name=body.get("trust_name"),
            filed_date=filed_date,
            primary_doc_url=body.get("primary_doc_url"),
            source=body.get("source"),
        )
        db.add(row)
        created = True

    db.commit()
    db.refresh(row)

    # Prune: keep only the most recent 500 rows. Cheap, bounded.
    # Only run on create (not every update).
    if created:
        total = db.execute(select(func.count()).select_from(LiveFeedItem)).scalar() or 0
        if total > 500:
            from sqlalchemy import delete
            # Find the 500th newest id, delete anything older
            cutoff_id = db.execute(
                select(LiveFeedItem.id)
                .order_by(LiveFeedItem.detected_at.desc())
                .offset(500)
                .limit(1)
            ).scalar()
            if cutoff_id:
                db.execute(delete(LiveFeedItem).where(LiveFeedItem.id < cutoff_id))
                db.commit()

    return {"status": "ok", "id": row.id, "created": created}


@router.get("/live/recent")
def live_recent(
    since: str = "",
    limit: int = 50,
    db: Session = Depends(get_live_feed_db),
):
    """Poll for filings newer than `since`. No auth — this is a public feed.

    Query params:
      since: ISO datetime (UTC). If empty, returns the most recent `limit`
        rows regardless of age.
      limit: max rows to return (default 50, capped at 200).

    Returns:
      {
        "items": [ {...}, ... ],
        "latest": "2026-04-14T19:42:06",   # ISO UTC of newest row in response
        "count": N
      }
    """
    from datetime import datetime as _dt
    from webapp.models import LiveFeedItem

    if limit < 1:
        limit = 1
    if limit > 200:
        limit = 200

    q = select(LiveFeedItem)
    if since:
        try:
            since_dt = _dt.fromisoformat(since.replace("Z", ""))
            q = q.where(LiveFeedItem.detected_at > since_dt)
        except (ValueError, TypeError):
            pass  # ignore malformed since, return most recent

    q = q.order_by(LiveFeedItem.detected_at.desc()).limit(limit)
    rows = db.execute(q).scalars().all()

    items = []
    latest = None
    for r in rows:
        items.append({
            "id": r.id,
            "detected_at": r.detected_at.isoformat() if r.detected_at else None,
            "accession_number": r.accession_number,
            "form": r.form,
            "cik": r.cik,
            "company_name": r.company_name,
            "trust_id": r.trust_id,
            "trust_slug": r.trust_slug,
            "trust_name": r.trust_name,
            "filed_date": r.filed_date.isoformat() if r.filed_date else None,
            "primary_doc_url": r.primary_doc_url,
            "source": r.source,
        })
        if latest is None and r.detected_at:
            latest = r.detected_at.isoformat()

    return {"items": items, "latest": latest, "count": len(items)}


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
