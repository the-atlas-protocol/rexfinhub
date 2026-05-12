"""Weekly grader — refresh outcome columns on past stock recommendations.

Wave E1 (2026-05-11). Cron entry, run weekly:

    python -m scripts.grade_recommendations

Idempotent: safe to re-run on the same day. Each pass refines outcome
columns; terminal statuses (launched/killed/abandoned) are sticky once
set, so re-running won't walk recs backward.

CLI flags:
    --dry-run   : compute changes, don't write
    --today YMD : pretend today is this date (for backfill testing)
    --stats     : after grading, print the hit-rate dashboard
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime
from pathlib import Path

# Make the project root importable when run as a script.
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from screener.li_engine.analysis.recommendation_history import (  # noqa: E402
    grade_open_recommendations,
    hit_rate_stats,
)


def _parse_today(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError as e:
        raise SystemExit(f"--today must be YYYY-MM-DD: {e}")


def main() -> int:
    p = argparse.ArgumentParser(description="Grade past stock recommendations.")
    p.add_argument("--dry-run", action="store_true",
                   help="Don't write — just log what would change.")
    p.add_argument("--today", type=str, default=None,
                   help="Pretend today is YYYY-MM-DD (for backfill testing).")
    p.add_argument("--stats", action="store_true",
                   help="Print the rolling 90d hit-rate dashboard after grading.")
    p.add_argument("--rolling-days", type=int, default=90,
                   help="Window for hit-rate computation (default 90).")
    args = p.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    log = logging.getLogger("grade_recommendations")

    today = _parse_today(args.today)

    if args.dry_run:
        # Dry-run: log open recs but don't write. We achieve this by
        # opening the DB read-only and counting candidates — the grader
        # itself doesn't have a dry-run flag (it's idempotent enough that
        # we don't need one in production). For now, just count.
        import sqlite3
        from screener.li_engine.analysis.recommendation_history import _DB
        if not Path(_DB).exists():
            log.info("[dry-run] DB does not exist at %s — nothing to grade", _DB)
            return 0
        try:
            conn = sqlite3.connect(f"file:{_DB}?mode=ro", uri=True)
        except sqlite3.OperationalError as e:
            log.warning("[dry-run] cannot open DB read-only: %s", e)
            return 0
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='recommendation_history'"
            ).fetchone()
            if not row:
                log.info("[dry-run] recommendation_history table not yet created")
                return 0
            n = conn.execute(
                "SELECT COUNT(*) FROM recommendation_history "
                "WHERE outcome_status IS NULL "
                "OR outcome_status NOT IN ('launched','killed','abandoned')"
            ).fetchone()[0]
        finally:
            conn.close()
        log.info("[dry-run] %d open recommendations would be considered for grading", n)
        return 0

    result = grade_open_recommendations(today=today)
    log.info(
        "Grading complete: %d rows updated, %d newly terminal",
        result.get("graded", 0), result.get("newly_terminal", 0),
    )

    if args.stats:
        stats = hit_rate_stats(rolling_days=args.rolling_days, today=today)
        print("\n=== Track Record (rolling {0}d) ===".format(args.rolling_days))
        print(f"As of:       {stats.get('as_of')}")
        print(f"HIGH:    {stats.get('high_hit', 0):>3}/{stats.get('high_total', 0):<3} "
              f"= {stats.get('high_hit_rate')}")
        print(f"MEDIUM:  {stats.get('medium_hit', 0):>3}/{stats.get('medium_total', 0):<3} "
              f"= {stats.get('medium_hit_rate')}")
        print(f"WATCH:   {stats.get('watch_hit', 0):>3}/{stats.get('watch_total', 0):<3} "
              f"= {stats.get('watch_hit_rate')}")
        print(f"Avg AUM 6mo: {stats.get('avg_aum_6mo')}")
        print(f"Tier accuracy (HIGH share of hits): {stats.get('tier_accuracy')}")
        if stats.get("sample_size_warning"):
            print("WARNING: < 10 HIGH-tier recs in window — small sample.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
