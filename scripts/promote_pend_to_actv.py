"""Promote mkt_master_data.market_status from PEND -> ACTV when inception_date < today.

Standalone CLI wrapper around etp_tracker.reconciler.promote_pend_to_actv().
The same function is called by the daily reconciler timer; this script
exists so an operator can run the promotion ad-hoc (dry-run or apply)
without triggering the full SEC-index pull.

Usage:
    python scripts/promote_pend_to_actv.py --dry-run    # show candidates
    python scripts/promote_pend_to_actv.py --apply       # commit changes

Issue ref: #104 — 50 PEND rows have past inception dates not promoted.
"""
from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Promote mkt_master_data PEND rows with past inception to ACTV.",
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--dry-run", action="store_true",
        help="Identify candidates only — do not write.",
    )
    group.add_argument(
        "--apply", action="store_true",
        help="Commit the PEND -> ACTV flip.",
    )
    args = parser.parse_args(argv)

    # Default to dry-run if neither flag passed (safer).
    dry_run = True if not args.apply else False

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("promote_pend_to_actv")

    from webapp.database import init_db, SessionLocal
    from etp_tracker.reconciler import promote_pend_to_actv

    init_db()
    db = SessionLocal()
    try:
        stats = promote_pend_to_actv(db, dry_run=dry_run)
    finally:
        db.close()

    mode = "DRY-RUN" if dry_run else "APPLY"
    log.info(
        "[%s] candidates=%d promoted=%d",
        mode, stats.candidates, stats.promoted,
    )
    print(f"[{mode}] candidates={stats.candidates} promoted={stats.promoted}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
