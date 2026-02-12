"""Screener Service - Orchestrate scoring pipeline and DB sync."""
from __future__ import annotations

import logging
import threading
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from webapp.database import SessionLocal
from webapp.models import ScreenerResult, ScreenerUpload

log = logging.getLogger(__name__)

_screener_lock = threading.Lock()
_screener_running = False


def is_screener_running() -> bool:
    return _screener_running


def run_screener_pipeline(upload_id: int) -> None:
    """Run full screener scoring pipeline in background, sync results to DB."""
    global _screener_running

    if not _screener_lock.acquire(blocking=False):
        log.warning("Screener already running, skipping")
        return

    _screener_running = True
    db = SessionLocal()

    try:
        upload = db.execute(
            select(ScreenerUpload).where(ScreenerUpload.id == upload_id)
        ).scalar_one_or_none()

        if not upload:
            log.error("Upload %d not found", upload_id)
            return

        from screener.data_loader import load_all
        from screener.scoring import compute_percentile_scores, derive_rex_benchmarks, apply_threshold_filters
        from screener.regression import build_training_set, train_model, predict_aum
        from screener.competitive import compute_competitive_density
        from screener.filing_match import match_filings

        # 1. Load data (2 sheets: stock_data + etp_data)
        log.info("Screener: loading data...")
        data = load_all()
        stock_df = data["stock_data"]
        etp_df = data["etp_data"]

        upload.stock_rows = len(stock_df)
        upload.etp_rows = len(etp_df)
        upload.filing_rows = 0  # Filing data now comes from pipeline DB
        db.commit()

        # 2. Score
        log.info("Screener: scoring %d stocks...", len(stock_df))
        benchmarks = derive_rex_benchmarks(etp_df, stock_df)
        scored = compute_percentile_scores(stock_df)
        scored = apply_threshold_filters(scored, benchmarks)

        # 3. Regression
        log.info("Screener: training regression model...")
        training = build_training_set(etp_df, stock_df)
        model_result = None
        if training is not None and len(training) >= 10:
            model_result = train_model(training)
            if model_result:
                scored = predict_aum(model_result, scored)
                upload.model_type = model_result.model_type
                upload.model_r_squared = model_result.r_squared

        # 4. Competitive density
        log.info("Screener: computing competitive density...")
        density = compute_competitive_density(etp_df)
        density_lookup = {}
        if not density.empty:
            for _, row in density.iterrows():
                # Map underlier (with " US") to clean ticker
                underlier = str(row["underlier"]).replace(" US", "").replace(" Curncy", "")
                density_lookup[underlier] = row

        # 5. Filing match (uses etp_data underlier mapping + pipeline DB)
        log.info("Screener: matching filings...")
        scored = match_filings(scored, etp_df)

        # 6. Write to DB (atomic: delete old, insert new)
        log.info("Screener: writing %d results to DB...", len(scored))
        db.execute(delete(ScreenerResult).where(ScreenerResult.upload_id != upload_id))

        results = []
        for _, row in scored.iterrows():
            ticker_clean = str(row.get("ticker_clean", row.get("Ticker", ""))).upper()
            density_info = density_lookup.get(ticker_clean, {})

            results.append(ScreenerResult(
                upload_id=upload_id,
                ticker=str(row.get("Ticker", "")),
                company_name=None,
                sector=str(row.get("GICS Sector", "")) if row.get("GICS Sector") else None,
                composite_score=float(row.get("composite_score", 0)),
                predicted_aum=float(row["predicted_aum"]) if "predicted_aum" in row and row.get("predicted_aum") else None,
                predicted_aum_low=float(row["predicted_aum_low"]) if "predicted_aum_low" in row and row.get("predicted_aum_low") else None,
                predicted_aum_high=float(row["predicted_aum_high"]) if "predicted_aum_high" in row and row.get("predicted_aum_high") else None,
                mkt_cap=float(row.get("Mkt Cap", 0)) if row.get("Mkt Cap") else None,
                call_oi_pctl=float(row.get("Total OI_pctl", 0)) if row.get("Total OI_pctl") else None,
                total_oi_pctl=float(row.get("Total OI_pctl", 0)) if row.get("Total OI_pctl") else None,
                volume_pctl=float(row.get("Turnover / Traded Value_pctl", 0)) if row.get("Turnover / Traded Value_pctl") else None,
                passes_filters=bool(row.get("passes_filters", False)),
                filing_status=str(row.get("filing_status", "Not Filed")),
                competitive_density=str(density_info.get("density_category", "")) if isinstance(density_info, dict) and density_info.get("density_category") else None,
                competitor_count=int(density_info.get("product_count", 0)) if isinstance(density_info, dict) and density_info.get("product_count") else None,
                total_competitor_aum=float(density_info.get("total_aum", 0)) if isinstance(density_info, dict) and density_info.get("total_aum") else None,
            ))

        db.bulk_save_objects(results)
        upload.status = "completed"
        db.commit()

        log.info("Screener pipeline complete: %d results saved", len(results))

    except Exception as e:
        log.error("Screener pipeline failed: %s", e, exc_info=True)
        try:
            upload = db.execute(
                select(ScreenerUpload).where(ScreenerUpload.id == upload_id)
            ).scalar_one_or_none()
            if upload:
                upload.status = "failed"
                upload.error_message = str(e)[:500]
                db.commit()
        except Exception:
            pass

    finally:
        db.close()
        _screener_running = False
        _screener_lock.release()
