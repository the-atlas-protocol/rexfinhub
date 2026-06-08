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
    ("apply_fund_master",        [PY, str(PROJECT_ROOT / "scripts" / "apply_fund_master.py")]),
    # CIC-12 fix (2026-06-08): canonical identity must be assigned BEFORE
    # apply_underlier_overrides, which keys off canonical_id. Without this
    # ordering, new rex_products from the overnight sec-scrape lack
    # product_master + identifier_xref + fund_underlier links until the
    # next run, leaving the reconciler with nothing to evaluate.
    ("ensure_canonical_identity", [PY, str(PROJECT_ROOT / "scripts" / "ensure_canonical_identity.py")]),
    ("apply_underlier_overrides", [PY, str(PROJECT_ROOT / "scripts" / "apply_underlier_overrides.py")]),
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
    # Phase 5 Stage 5 (Track 5A): status_reconciler in --apply mode. The
    # dry-run review (2026-05-20) validated the diff and fixed two bugs — the
    # ETN blind spot and demote-on-absent-evidence. The reconciler now
    # promotes on evidence and delists only on Bloomberg LIQU; it never
    # demotes mid-lifecycle on absent evidence, so running it live each night
    # is safe. status_history is the authority — it drives both
    # rex_products.status and status_cached. Diff still logged to
    # data/.status_reconciler.log.
    ("status_reconciler_apply", [PY, "-m", "webapp.services.status_reconciler", "--apply"]),
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
