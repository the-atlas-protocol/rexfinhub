#!/usr/bin/env python3
"""Track 5 — keep the Phase 4/5 canonical-identity model live for NEW products.

`sync_rex_products_from_filings.py` creates rex_products rows from new SEC
filings but assigns NO canonical identity. Without this step every new fund is
invisible to product_master / identifier_xref / underlier_master /
fund_underlier — and therefore to the status reconciler and every
canonical_id-keyed feature. The Phase 4/5 model would silently decay after the
one-time backfill.

This runs the four idempotent Phase 4 backfills in dependency order:

    backfill_product_master.py    -> mints canonical_id + product_master rows
    backfill_identifier_xref.py   -> ticker / cik / series / bloomberg xrefs
    backfill_underlier_master.py  -> typed underliers for any new strings
    backfill_fund_underlier.py    -> links funds to their underliers

Each backfill skips rows already covered, so a nightly run only does work for
products added since the last run. Wired into the bloomberg-chain post-steps
BEFORE `status_reconciler` — the reconciler iterates `product_master`, so the
new product_master rows minted here are what let it pick up (and initialize
the status_history of) every newly-filed fund.

Idempotent. A run with no new products is a no-op. Non-zero exit of any single
backfill is surfaced but does not abort the rest (overall rc = max).

Usage:
    python scripts/ensure_canonical_identity.py [--db PATH]
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

# Dependency order matters: product_master mints canonical_id first; the rest
# key off it. backfill_underlier_master is deliberately NOT here — it is a
# heuristic re-classifier that would mint noise rows on a nightly cadence;
# new underlier strings are handled targeted, surfaced by the
# underlier_id_coverage assertion. backfill_fund_underlier links new products
# to EXISTING underlier_master rows only.
STEPS = [
    "backfill_product_master.py",
    "backfill_identifier_xref.py",
    "backfill_fund_underlier.py",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None,
                    help="Optional DB path passed through to each backfill step.")
    args = ap.parse_args()

    overall_rc = 0
    for script in STEPS:
        path = PROJECT_ROOT / "scripts" / script
        cmd = [PY, str(path)]
        if args.db:
            cmd += ["--db", args.db]
        print(f"\n=== ensure_canonical_identity: {script} ===", flush=True)
        result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
        if result.returncode != 0:
            print(f"  WARN: {script} exited {result.returncode}")
            overall_rc = max(overall_rc, result.returncode)

    print(f"\nensure_canonical_identity done (rc={overall_rc}).")
    return overall_rc


if __name__ == "__main__":
    sys.exit(main())
