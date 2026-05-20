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
    ("apply_underlier_overrides", [PY, str(PROJECT_ROOT / "scripts" / "apply_underlier_overrides.py")]),
    ("apply_issuer_brands",      [PY, str(PROJECT_ROOT / "scripts" / "apply_issuer_brands.py")]),
    ("apply_classification_sweep", [PY, str(PROJECT_ROOT / "scripts" / "apply_classification_sweep.py"),
                                     "--apply", "--apply-medium"]),
    # Phase 6 Stage 7 prerequisite (ADR 0009): apply override rows from
    # classification_override AFTER the legacy classify_sweep so the
    # canonical override-first resolution actually takes effect on the
    # mkt_master_data columns the reports read.
    ("apply_classification_overrides", [PY, str(PROJECT_ROOT / "scripts" / "apply_classification_overrides.py")]),
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
