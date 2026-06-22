"""Single wrapper that runs the 4 Bloomberg-chain post-steps in sequence.

Replaces 4 separate ``ExecStartPost=`` lines in
``rexfinhub-bloomberg-chain.service`` with one. Same end result, half the
systemd-unit noise, single place to add logging or skip-on-error logic.

Steps (in order):
    1. apply_fund_master.py        — seed/curated fund metadata overlay
    2. apply_underlier_overrides.py — manual underlier corrections
    3. apply_issuer_brands.py      — issuer-display canonicalisation
    4. apply_classification_sweep.py --apply --apply-medium — auto+medium fills

Exit code = max(individual exit codes). A non-zero step does NOT abort the
chain — every step runs so partial successes still apply.

Per ADR 0003 (Phase 1 cuts).
"""
from __future__ import annotations

import logging
import subprocess
import sys
import time
from pathlib import Path

log = logging.getLogger("apply_bloomberg_post_steps")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PY = "/home/jarvis/venv/bin/python"

STEPS = [
    # Ryu 2026-06-10: classification happens RIGHT AFTER the Bloomberg data
    # lands, not the next morning. classify_daily (tiers: rulings -> rules ->
    # LLM+critic -> queue) feeds BOTH layers — legacy CSVs AND fund_master.csv
    # (the full 3-axis taxonomy master) — so the very next step's restamp
    # already carries today's launches. The 09:00 run remains as catch-up +
    # the sweep report email.
    ("classify_daily",           [PY, str(PROJECT_ROOT / "scripts" / "classify_daily.py"),
                                  "--apply"]),
    # AI middleman (Ryu 2026-06-16): the rule engine queues funds it can't place;
    # the context-aware LLM classifier catches the ones with a tracked-category
    # signal (income-vs-bond, thematic, L&I, crypto) that the rules miss, so
    # upcoming launches are categorized correctly without manual touch. Idempotent
    # via its journal — only spends tokens on genuinely new funds.
    ("ai_classify_unmapped",     [PY, str(PROJECT_ROOT / "scripts" / "ai_classify_unmapped.py")]),
    # AI underlier-intel (Ryu 2026-06-17): classify every NEW competitor-filed L&I
    # underlier (foreign / us_stock / pre_ipo / crypto / basket) so the T-REX report's
    # Foreign and Pre-IPO filer-race sections self-heal — foreign-listed names
    # (SK Square, Leeno, Siemens, ...) flow into data/foreign/universe.parquet and
    # private names into the IPO sourcer. Idempotent via its journal.
    ("ai_underlier_intel",       [PY, str(PROJECT_ROOT / "scripts" / "ai_underlier_intel.py")]),
    ("ai_source_ipo",            [PY, str(PROJECT_ROOT / "scripts" / "ai_source_ipo.py")]),
    ("apply_fund_master",        [PY, str(PROJECT_ROOT / "scripts" / "apply_fund_master.py")]),
    # CIC-12 fix (2026-06-08): canonical identity must be assigned BEFORE
    # apply_underlier_overrides, which keys off canonical_id. Without this
    # ordering, new rex_products from the overnight sec-scrape lack
    # product_master + identifier_xref + fund_underlier links until the
    # next run, leaving the reconciler with nothing to evaluate.
    ("ensure_canonical_identity", [PY, str(PROJECT_ROOT / "scripts" / "ensure_canonical_identity.py")]),
    ("apply_underlier_overrides", [PY, str(PROJECT_ROOT / "scripts" / "apply_underlier_overrides.py")]),
    # B1 (2026-06-15): regenerate the regex brand map BEFORE stamping it, so new
    # funds (e.g. overnight PEND launches) get an issuer_display automatically
    # instead of accumulating as NULL until a manual derive run. derive preserves
    # any human-curated rows; apply then stamps the merged set onto mkt_master_data.
    ("derive_issuer_brands",     [PY, str(PROJECT_ROOT / "scripts" / "derive_issuer_brands.py")]),
    ("apply_issuer_brands",      [PY, str(PROJECT_ROOT / "scripts" / "apply_issuer_brands.py")]),
    ("apply_classification_sweep", [PY, str(PROJECT_ROOT / "scripts" / "apply_classification_sweep.py"),
                                     "--apply", "--apply-medium"]),
    # Phase 6 Stage 7 prerequisite (ADR 0009): apply override rows from
    # classification_override AFTER the legacy classify_sweep so the
    # canonical override-first resolution actually takes effect on the
    # mkt_master_data columns the reports read.
    ("apply_classification_overrides", [PY, str(PROJECT_ROOT / "scripts" / "apply_classification_overrides.py")]),
    # Refresh estimated_effective_date from the latest 485-series filing for
    # every product (by series_id) — the sync collapses multi-series filings
    # to one extraction, so funds filing repeated 485BXT extensions drift
    # stale otherwise.
    ("refresh_effective_dates", [PY, str(PROJECT_ROOT / "scripts" / "refresh_effective_dates.py"), "--apply"]),
    # ADR 0014: the ONE authoritative effective date (fund_extractions.effective_date,
    # parsed at ingestion) propagates to BOTH derived stores — fund_status (which the
    # T-REX report + the six competitor sections READ) and rex_products — by series_id,
    # latest 485 wins. This closes the dead-letter route that left every competitor
    # effective date NULL. Runs AFTER sec/classify, BEFORE the reconciler so the
    # reconciler consumes the fresh dates. Idempotent; never invents a date.
    ("propagate_effective_dates", [PY, str(PROJECT_ROOT / "scripts" / "propagate_effective_dates.py"), "--apply"]),
    # Phase 5 Stage 5 (Track 5A): status_reconciler in --apply mode. The
    # dry-run review (2026-05-20) validated the diff and fixed two bugs — the
    # ETN blind spot and demote-on-absent-evidence. The reconciler now
    # promotes on evidence and delists only on Bloomberg LIQU; it never
    # demotes mid-lifecycle on absent evidence, so running it live each night
    # is safe. status_history is the authority — it drives both
    # rex_products.status and status_cached. Diff still logged to
    # data/.status_reconciler.log.
    ("status_reconciler_apply", [PY, "-m", "webapp.services.status_reconciler", "--apply"]),
    # Re-stamp time-series enrichment from the now-final master so charts can't lag
    # the classification post-steps (the mkt_time_series drift bug — 2,625 tickers
    # stale as of 2026-06-18). Must run AFTER all master enrichment, BEFORE snapshot.
    ("restamp_time_series",     [PY, str(PROJECT_ROOT / "scripts" / "restamp_time_series.py")]),
    # WS-C1 (2026-06-15): capture the fully-enriched end-of-cycle state into the
    # append-only mkt_daily_snapshot so daily history is never lost to the next
    # sync's snapshot-replace. LAST data step — after all classification +
    # reconciliation — so the snapshot reflects the day's final truth. Idempotent
    # per date (last run of the day wins).
    ("snapshot_daily",          [PY, str(PROJECT_ROOT / "scripts" / "snapshot_daily.py")]),
    # Stage E (approved plan): regenerate the L&I-engine parquets INSIDE the chain
    # so the L&I / weekly / competitor reports never read days-old data. These six
    # modules used to run only on the Mon/Fri rexfinhub-parquet-rebuild timer — a
    # separate clock — which is exactly the "report reads whatever the last scattered
    # timer produced" staleness this plan removes. Order matches the retired timer
    # (universe -> timeseries -> filed -> competitor -> candidates -> whitespace).
    # The leading '-' tolerance is inherent: a non-zero step is logged, not fatal.
    ("parquet_universe_loader",  [PY, "-m", "screener.li_engine.analysis.universe_loader"]),
    ("parquet_bbg_timeseries",   [PY, "-m", "screener.li_engine.analysis.bbg_timeseries"]),
    ("parquet_filed_underliers", [PY, "-m", "screener.li_engine.analysis.filed_underliers"]),
    ("parquet_competitor_counts",[PY, "-m", "screener.li_engine.analysis.competitor_counts"]),
    ("parquet_launch_candidates",[PY, "-m", "screener.li_engine.analysis.launch_candidates"]),
    ("parquet_whitespace_v4",    [PY, "-m", "screener.li_engine.analysis.whitespace_v4"]),
    # Data-as-code (ADR 0011 E4): the engine's rules mutations get committed
    # and pushed daily so git stays the truth and the VPS tree stays clean
    # (the git_tree_clean assertion fired on exactly this drift, 2026-06-10).
    ("commit_rules_delta",      [PY, str(PROJECT_ROOT / "scripts" / "commit_rules_delta.py")]),
]


def main() -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    overall_rc = 0
    for name, cmd in STEPS:
        log.info("=== %s START ===", name)
        t0 = time.time()
        try:
            result = subprocess.run(cmd, check=False)
            rc = result.returncode
        except Exception as e:
            log.error("=== %s CRASHED: %s ===", name, e)
            rc = 1
        elapsed = time.time() - t0
        log.info("=== %s END (rc=%d, %.1fs) ===", name, rc, elapsed)
        if rc > overall_rc:
            overall_rc = rc
    return overall_rc


if __name__ == "__main__":
    sys.exit(main())
