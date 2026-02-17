from __future__ import annotations
import logging
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


def run_pipeline(ciks: list[str], overrides: dict | None = None, since: str | None = None, until: str | None = None,
                 output_root: Path | str = "outputs", cache_dir: Path | str = "http_cache",
                 user_agent: str | None = None, request_timeout: int = 45, pause: float = 0.35,
                 refresh_submissions: bool = True, refresh_max_age_hours: int = 6, refresh_force_now: bool = False,
                 force_reprocess: bool = False) -> int:
    output_root = Path(output_root); cache_dir = Path(cache_dir)
    output_root.mkdir(parents=True, exist_ok=True); cache_dir.mkdir(parents=True, exist_ok=True)
    if not user_agent: user_agent = "REX-SEC-Filer/1.0 (contact: set USER_AGENT)"
    client = SECClient(user_agent=user_agent, request_timeout=request_timeout, pause=pause, cache_dir=cache_dir)

    metrics = RunMetrics()
    metrics.start()

    trusts = step2_submissions_and_prospectus(
        client=client, output_root=output_root, cik_list=ciks, overrides=overrides or {},
        since=since, until=until, refresh_submissions=refresh_submissions,
        refresh_max_age_hours=refresh_max_age_hours, refresh_force_now=refresh_force_now
    )

    # If force_reprocess, clear all manifests before Step 3
    if force_reprocess:
        log.info("Force reprocess: clearing all manifests")
        for t in trusts:
            paths = output_paths_for_trust(output_root, t)
            clear_manifest(paths["folder"])

    for t in tqdm(trusts, desc="Extract (Step 3)", leave=False):
        result = step3_extract_for_trust(client, output_root, t)
        metrics.new_filings += result.get("new", 0)
        metrics.skipped_filings += result.get("skipped", 0)
        metrics.errors += result.get("errors", 0)
        for strat, count in result.get("strategies", {}).items():
            metrics.add_strategy(strat, count)

    for t in tqdm(trusts, desc="Roll-up (Step 4)", leave=False):
        step4_rollup_for_trust(output_root, t)

    for t in tqdm(trusts, desc="Name History (Step 5)", leave=False):
        step5_name_history_for_trust(output_root, t)

    metrics.trusts_processed = len(trusts)
    metrics.finish()

    # Save run summary
    save_run_summary(output_root, metrics)
    log.info(metrics.summary_line())
    print(metrics.summary_line())

    return len(trusts)
