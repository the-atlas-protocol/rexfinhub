from __future__ import annotations
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from tqdm import tqdm
from .sec_client import SECClient
from .step2 import step2_submissions_and_prospectus
from .step3 import step3_extract_for_trust
from .step4 import step4_rollup_for_trust
from .step5 import step5_name_history_for_trust
from .manifest import clear_manifest
from .paths import output_paths_for_trust
from .run_summary import RunMetrics, save_run_summary

log = logging.getLogger(__name__)

# Max parallel workers for Step 3 (SEC rate limit: 10 req/s)
# 3 workers x 0.35s pause = ~8.6 req/s max (safe margin)
_DEFAULT_WORKERS = 3


def load_ciks_from_db(universe: str = "all") -> tuple[list[str], dict[str, str]]:
    """Load CIKs and name overrides from the trusts database table.

    Args:
        universe: "all" (every active trust), "curated" (source=curated only),
                  "discovered" (source=bulk_discovery only)

    Returns:
        (cik_list, overrides_dict) ready for run_pipeline().
    """
    try:
        from webapp.database import SessionLocal
        from webapp.models import Trust
        from sqlalchemy import select

        db = SessionLocal()
        try:
            query = select(Trust.cik, Trust.name).where(Trust.is_active == True)
            if universe == "curated":
                query = query.where(Trust.source == "curated")
            elif universe == "discovered":
                query = query.where(Trust.source == "bulk_discovery")
            # "all" = no additional filter

            rows = db.execute(query).all()
            ciks = [str(int(str(row[0]))) for row in rows]
            overrides = {str(int(str(row[0]))): row[1] for row in rows}
            log.info("Loaded %d CIKs from database (universe=%s)", len(ciks), universe)
            return ciks, overrides
        finally:
            db.close()
    except Exception as e:
        log.warning("Failed to load CIKs from database: %s. Falling back to trusts.py", e)
        return _load_ciks_fallback()


def _load_ciks_fallback() -> tuple[list[str], dict[str, str]]:
    """Fallback: load CIKs from the hardcoded trusts.py registry."""
    from .trusts import get_all_ciks, get_overrides
    ciks = list(get_all_ciks())
    overrides = dict(get_overrides())
    log.info("Loaded %d CIKs from trusts.py (fallback)", len(ciks))
    return ciks, overrides


def _step3_worker(trust_name: str, output_root: Path, user_agent: str,
                  timeout: int, pause: float, cache_dir: Path,
                  since: str | None = None, until: str | None = None) -> dict:
    """Process a single trust in Step 3 with its own SEC client."""
    client = SECClient(user_agent=user_agent, request_timeout=timeout,
                       pause=pause, cache_dir=cache_dir)
    return step3_extract_for_trust(client, output_root, trust_name,
                                   since=since, until=until)


def _record_pipeline_run(metrics: RunMetrics, triggered_by: str = "manual") -> None:
    """Write pipeline run stats to the pipeline_runs database table."""
    try:
        from webapp.database import SessionLocal
        from webapp.models import PipelineRun

        db = SessionLocal()
        try:
            run = PipelineRun(
                started_at=datetime.fromisoformat(metrics.started_at) if metrics.started_at else datetime.now(timezone.utc),
                finished_at=datetime.fromisoformat(metrics.finished_at) if metrics.finished_at else None,
                status="completed" if metrics.errors == 0 else "completed_with_errors",
                trusts_processed=metrics.trusts_processed,
                filings_found=metrics.new_filings,
                funds_extracted=sum(metrics.strategies.values()) if metrics.strategies else 0,
                error_message=f"{metrics.errors} errors" if metrics.errors else None,
                triggered_by=triggered_by,
            )
            db.add(run)
            db.commit()
            log.info("Pipeline run recorded in database (id=%d)", run.id)
        finally:
            db.close()
    except Exception as e:
        log.warning("Failed to record pipeline run in database: %s", e)


def run_pipeline(ciks: list[str], overrides: dict | None = None, since: str | None = None, until: str | None = None,
                 output_root: Path | str = "outputs", cache_dir: Path | str = "http_cache",
                 user_agent: str | None = None, request_timeout: int = 45, pause: float = 0.35,
                 refresh_submissions: bool = True, refresh_max_age_hours: int = 6, refresh_force_now: bool = False,
                 force_reprocess: bool = False, max_workers: int = _DEFAULT_WORKERS,
                 use_async: bool = False, use_daily_index: bool = False,
                 triggered_by: str = "manual") -> int:
    output_root = Path(output_root); cache_dir = Path(cache_dir)
    output_root.mkdir(parents=True, exist_ok=True); cache_dir.mkdir(parents=True, exist_ok=True)
    if not user_agent: user_agent = "REX-SEC-Filer/1.0 (contact: set USER_AGENT)"
    client = SECClient(user_agent=user_agent, request_timeout=request_timeout, pause=pause, cache_dir=cache_dir)

    metrics = RunMetrics()
    metrics.start()

    # Phase 2b: Daily index pre-flight — skip trusts with no new filings today
    skip_ciks = set()
    if use_daily_index and not force_reprocess:
        try:
            from .index_client import get_todays_485_filings
            known_ciks = set(str(int(str(c))) for c in ciks)
            result = get_todays_485_filings(known_ciks=known_ciks, user_agent=user_agent)
            active_ciks = {f["cik"] for f in result.get("known", [])}
            if active_ciks:
                skip_ciks = known_ciks - active_ciks
                log.info(
                    "Daily index: %d trusts have new filings, skipping %d",
                    len(active_ciks), len(skip_ciks)
                )
            else:
                log.info("Daily index: no new 485 filings today (weekend/holiday?). Running full pipeline.")
        except Exception as e:
            log.warning("Daily index check failed (%s). Running full pipeline.", e)

    active_ciks = [c for c in ciks if str(int(str(c))) not in skip_ciks]

    # Phase 2a: Async submissions fetch (optional, faster for large universes)
    if use_async and len(active_ciks) > 10:
        try:
            from .async_client import fetch_submissions_async
            log.info("Using async client for %d CIKs", len(active_ciks))
            # Pre-warm the submission cache with async fetches
            fetch_submissions_async(
                ciks=active_ciks,
                cache_dir=cache_dir,
                user_agent=user_agent,
                refresh_max_age_hours=refresh_max_age_hours,
            )
            log.info("Async cache warm complete")
        except Exception as e:
            log.warning("Async pre-fetch failed (%s). Falling back to sequential.", e)

    # Step 2: Fetch submissions (sequential reads from cache after async pre-warm)
    trusts = step2_submissions_and_prospectus(
        client=client, output_root=output_root, cik_list=active_ciks, overrides=overrides or {},
        since=since, until=until, refresh_submissions=refresh_submissions,
        refresh_max_age_hours=refresh_max_age_hours, refresh_force_now=refresh_force_now
    )

    # If force_reprocess, clear all manifests before Step 3
    if force_reprocess:
        log.info("Force reprocess: clearing all manifests")
        for t in trusts:
            paths = output_paths_for_trust(output_root, t)
            clear_manifest(paths["folder"])

    # Step 3: Extract filings (parallel - I/O bound, biggest bottleneck)
    # Auto-adjust per-worker pause to keep aggregate rate under 10 req/s
    effective_pause = max(pause, max_workers * 0.1)
    workers = min(max_workers, len(trusts))
    if workers > 1:
        lock = threading.Lock()
        pbar = tqdm(total=len(trusts), desc=f"Extract (Step 3, {workers}w)", leave=False)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_step3_worker, t, output_root, user_agent,
                            request_timeout, effective_pause, cache_dir,
                            since, until): t
                for t in trusts
            }
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception as e:
                    log.error("Step 3 error for %s: %s", futures[future], e)
                    result = {"new": 0, "skipped": 0, "errors": 1, "strategies": {}}
                with lock:
                    metrics.new_filings += result.get("new", 0)
                    metrics.skipped_filings += result.get("skipped", 0)
                    metrics.errors += result.get("errors", 0)
                    for strat, count in result.get("strategies", {}).items():
                        metrics.add_strategy(strat, count)
                    pbar.update(1)
        pbar.close()
    else:
        # Single-worker fallback
        for t in tqdm(trusts, desc="Extract (Step 3)", leave=False):
            result = step3_extract_for_trust(client, output_root, t,
                                            since=since, until=until)
            metrics.new_filings += result.get("new", 0)
            metrics.skipped_filings += result.get("skipped", 0)
            metrics.errors += result.get("errors", 0)
            for strat, count in result.get("strategies", {}).items():
                metrics.add_strategy(strat, count)

    # Steps 4 & 5: Local CSV processing (sequential - fast, no network)
    for t in tqdm(trusts, desc="Roll-up (Step 4)", leave=False):
        step4_rollup_for_trust(output_root, t)

    for t in tqdm(trusts, desc="Name History (Step 5)", leave=False):
        step5_name_history_for_trust(output_root, t)

    metrics.trusts_processed = len(trusts)
    metrics.finish()

    # Save run summary (JSON file + DB)
    save_run_summary(output_root, metrics)
    _record_pipeline_run(metrics, triggered_by=triggered_by)
    log.info(metrics.summary_line())
    print(metrics.summary_line())

    return len(trusts)
