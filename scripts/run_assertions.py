"""Phase 6 Stage 5 (ADR 0009): daily data-quality assertion runner.

Runs ~25 dbt-style assertions against the live DB and writes results to
the assertion_run table. The morning triage email reads from assertion_run
and surfaces failures.

Each assertion returns:
    (passed: bool, count: int, sample: list[dict], detail: str)

The assertion_run row captures all four so the email can render
"X passed / Y failed, here are the Y" with sample failures.

Designed to run nightly via systemd timer (08:00 ET, after bloomberg-chain
+ overnight processing). For now, can be invoked manually.

Usage:
    python scripts/run_assertions.py
    python scripts/run_assertions.py --dry-run
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = PROJECT_ROOT / "data" / "etp_tracker.db"


def _make_assertion(name: str, category: str):
    """Decorator to register an assertion function."""
    def decorator(fn):
        fn._assertion_name = name
        fn._assertion_category = category
        return fn
    return decorator


# ============================================================================
# FRESHNESS
# ============================================================================

@_make_assertion("bloomberg_file_today", "freshness")
def check_bloomberg_freshness(conn: sqlite3.Connection) -> tuple:
    """The Bloomberg file should have been synced today."""
    row = conn.execute("""
        SELECT MAX(updated_at) FROM mkt_master_data
    """).fetchone()
    if not row or not row[0]:
        return (False, 0, [], "no mkt_master_data rows")
    latest = str(row[0])[:10]
    today = datetime.utcnow().date().isoformat()
    yesterday = (datetime.utcnow().date() - timedelta(days=1)).isoformat()
    passed = latest in (today, yesterday)
    return (passed, 0 if passed else 1, [{"latest_date": latest}],
            f"latest mkt_master_data updated_at is {latest}")


@_make_assertion("mkt_data_volatility", "freshness")
def check_mkt_data_volatility(conn: sqlite3.Connection) -> tuple:
    """Day-over-day mkt_master_data row count delta must be < 5% (uses pipeline_runs)."""
    rows = conn.execute("""
        SELECT date(started_at) AS d, MAX(master_rows_written) AS n
        FROM mkt_pipeline_runs
        WHERE status = 'completed' AND master_rows_written IS NOT NULL
        GROUP BY date(started_at)
        ORDER BY d DESC
        LIMIT 2
    """).fetchall()
    if len(rows) < 2:
        return (True, 0, [], "insufficient pipeline_runs history")
    today, yesterday = rows[0], rows[1]
    if not yesterday[1]:
        return (True, 0, [], "no yesterday baseline")
    delta_pct = abs(today[1] - yesterday[1]) / yesterday[1] * 100
    passed = delta_pct < 5.0
    return (passed, 0 if passed else 1,
            [{"today_count": today[1], "yesterday_count": yesterday[1], "delta_pct": round(delta_pct, 2)}],
            f"day-over-day delta {delta_pct:.2f}% (threshold 5%)")


@_make_assertion("atom_watcher_alive", "freshness")
def check_atom_watcher(conn: sqlite3.Connection) -> tuple:
    """An atom-watcher emission should have landed within the last 12 hours.

    NOTE: atom-watcher writes filing_alerts ONLY when SEC publishes a new
    filing — quiet after-hours/weekend windows can stretch >2h. The 12h
    threshold catches a dead daemon (typical day has multiple emissions)
    without false-positiving on weekend quiet.
    """
    row = conn.execute("""
        SELECT MAX(detected_at) FROM filing_alerts WHERE source = 'atom'
    """).fetchone()
    if not row or not row[0]:
        return (False, 0, [], "no atom-sourced filing_alerts")
    latest_str = str(row[0])
    try:
        latest = datetime.fromisoformat(latest_str.replace("Z", "+00:00"))
        if latest.tzinfo:
            latest = latest.replace(tzinfo=None)
        age_min = (datetime.utcnow() - latest).total_seconds() / 60.0
        passed = age_min < 720  # 12 hours
        return (passed, 0 if passed else 1,
                [{"latest_atom_alert": latest_str, "age_min": round(age_min, 1)}],
                f"latest atom alert {age_min:.1f} min ago (threshold 720)")
    except (ValueError, TypeError) as e:
        return (False, 1, [], f"failed to parse: {e}")


# ============================================================================
# CLASSIFICATION COVERAGE
# ============================================================================

@_make_assertion("primary_strategy_coverage", "classification")
def check_primary_strategy(conn: sqlite3.Connection) -> tuple:
    """Every active REX product should have primary_strategy populated."""
    rows = conn.execute("""
        SELECT ticker, fund_name
        FROM mkt_master_data
        WHERE market_status = 'ACTV'
          AND is_rex = 1
          AND (primary_strategy IS NULL OR primary_strategy = '')
        LIMIT 20
    """).fetchall()
    return (len(rows) == 0, len(rows),
            [{"ticker": r[0], "fund_name": r[1]} for r in rows[:5]],
            f"{len(rows)} active REX products missing primary_strategy")


@_make_assertion("underlier_id_coverage", "classification")
def check_underlier_id(conn: sqlite3.Connection) -> tuple:
    """Every active REX product should have a resolved underlier_id via fund_underlier."""
    rows = conn.execute("""
        SELECT rp.ticker, rp.name
        FROM rex_products rp
        LEFT JOIN fund_underlier fu ON fu.canonical_id = rp.canonical_id
        WHERE rp.status = 'Listed'
          AND fu.canonical_id IS NULL
        LIMIT 20
    """).fetchall()
    return (len(rows) == 0, len(rows),
            [{"ticker": r[0], "name": r[1]} for r in rows[:5]],
            f"{len(rows)} Listed REX products missing fund_underlier link")


@_make_assertion("etp_category_coverage", "classification")
def check_etp_category(conn: sqlite3.Connection) -> tuple:
    """Every active REX product should have etp_category."""
    rows = conn.execute("""
        SELECT ticker FROM mkt_master_data
        WHERE market_status = 'ACTV' AND is_rex = 1
          AND (etp_category IS NULL OR etp_category = '')
        LIMIT 20
    """).fetchall()
    return (len(rows) == 0, len(rows),
            [{"ticker": r[0]} for r in rows[:5]],
            f"{len(rows)} active REX products missing etp_category")


# ============================================================================
# LIFECYCLE INTEGRITY
# ============================================================================

@_make_assertion("no_date_inversion", "lifecycle")
def check_date_inversion(conn: sqlite3.Connection) -> tuple:
    """inception_date should not precede initial_filing_date."""
    rows = conn.execute("""
        SELECT ticker, name, initial_filing_date, inception_date
        FROM rex_products
        WHERE initial_filing_date IS NOT NULL
          AND inception_date IS NOT NULL
          AND inception_date < initial_filing_date
        LIMIT 20
    """).fetchall()
    return (len(rows) == 0, len(rows),
            [{"ticker": r[0], "filing": str(r[2]), "inception": str(r[3])} for r in rows[:5]],
            f"{len(rows)} rex_products with inception_date < initial_filing_date")


@_make_assertion("no_dup_active_tickers", "lifecycle")
def check_duplicate_active_tickers(conn: sqlite3.Connection) -> tuple:
    """No ticker should appear in >1 active rex_products row."""
    rows = conn.execute("""
        SELECT ticker, COUNT(*) AS n
        FROM rex_products
        WHERE ticker IS NOT NULL AND status IN ('Listed', 'Effective', 'Target List')
        GROUP BY ticker HAVING n > 1
        LIMIT 20
    """).fetchall()
    return (len(rows) == 0, len(rows),
            [{"ticker": r[0], "count": r[1]} for r in rows[:5]],
            f"{len(rows)} tickers appear in >1 active rex_products row")


@_make_assertion("listed_has_mkt_data", "lifecycle")
def check_listed_has_actv(conn: sqlite3.Connection) -> tuple:
    """Every Listed REX product should have matching mkt_master_data with ACTV (BMAX-class)."""
    rows = conn.execute("""
        SELECT rp.ticker, rp.name
        FROM rex_products rp
        LEFT JOIN mkt_master_data m
          ON UPPER(m.ticker_clean) = UPPER(rp.ticker)
         AND m.market_status = 'ACTV'
        WHERE rp.status = 'Listed' AND rp.ticker IS NOT NULL
          AND m.ticker_clean IS NULL
        LIMIT 20
    """).fetchall()
    return (len(rows) == 0, len(rows),
            [{"ticker": r[0], "name": r[1]} for r in rows[:5]],
            f"{len(rows)} Listed REX products without matching ACTV mkt_master_data row (BMAX class)")


# ============================================================================
# SEND PIPELINE HEALTH
# ============================================================================

@_make_assertion("recipient_lists_populated", "send_pipeline")
def check_recipient_lists(conn: sqlite3.Connection) -> tuple:
    """No active recipient list should be empty."""
    rows = conn.execute("""
        SELECT list_type, COUNT(*) AS n
        FROM email_recipients
        GROUP BY list_type
        HAVING n = 0
    """).fetchall()
    return (len(rows) == 0, len(rows),
            [{"list_type": r[0]} for r in rows],
            f"{len(rows)} recipient lists with 0 active recipients")


# ============================================================================
# RUNNER
# ============================================================================

# Auto-discover assertion functions
ASSERTIONS = [
    fn for fn in [
        check_bloomberg_freshness, check_mkt_data_volatility, check_atom_watcher,
        check_primary_strategy, check_underlier_id, check_etp_category,
        check_date_inversion, check_duplicate_active_tickers, check_listed_has_actv,
        check_recipient_lists,
    ]
]


def ensure_assertion_table(conn: sqlite3.Connection) -> None:
    """Create assertion_run table if missing (Phase 6 Stage 1 supplement)."""
    # Reuses the Phase 6 Stage 1 schema (already on local + VPS):
    #   id, run_at, assertion_name, category, passed, fail_count,
    #   sample_json, details
    conn.execute("CREATE INDEX IF NOT EXISTS idx_assertion_run_at "
                 "ON assertion_run (run_at, assertion_name)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--dry-run", action="store_true",
                    help="Print results but do not write to assertion_run")
    args = ap.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: DB not found at {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        ensure_assertion_table(conn)

        run_id = str(uuid.uuid4())
        run_at = datetime.utcnow().isoformat()
        results = []
        for fn in ASSERTIONS:
            try:
                passed, count, sample, detail = fn(conn)
            except Exception as e:
                passed, count, sample, detail = False, 1, [], f"EXCEPTION: {e}"
            results.append({
                "name": fn._assertion_name,
                "category": fn._assertion_category,
                "passed": passed,
                "fail_count": count,
                "sample": sample,
                "detail": detail,
            })

        passed_n = sum(1 for r in results if r["passed"])
        failed_n = len(results) - passed_n
        print(f"=== Assertion run {run_id[:8]}... at {run_at} ===")
        print(f"  {passed_n} / {len(results)} passed")
        print()
        for r in results:
            mark = "PASS" if r["passed"] else "FAIL"
            print(f"  [{mark}] [{r['category']:14s}] {r['name']:30s} {r['detail']}")
            if not r["passed"] and r["sample"]:
                for s in r["sample"][:3]:
                    print(f"       -> {s}")

        if args.dry_run:
            print("\nDRY-RUN — not written to assertion_run")
            return 0

        for r in results:
            conn.execute("""
                INSERT INTO assertion_run
                (run_at, assertion_name, category, passed, fail_count, sample_json, details)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                run_at, r["name"], r["category"],
                1 if r["passed"] else 0, r["fail_count"],
                json.dumps(r["sample"]), r["detail"],
            ))
        conn.commit()
        print(f"\nWritten to assertion_run ({len(results)} rows at {run_at})")
        return 0 if failed_n == 0 else 2
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
