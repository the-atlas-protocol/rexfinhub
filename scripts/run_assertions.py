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


@_make_assertion("send_log_yesterday", "send_pipeline")
def check_send_log_yesterday(conn: sqlite3.Connection) -> tuple:
    """Yesterday should have at least 1 successful send (Mon-Fri).

    Reads .send_audit.json file if present; warns if absent.
    """
    audit_path = PROJECT_ROOT / "data" / ".send_audit.json"
    if not audit_path.exists():
        return (True, 0, [], "no .send_audit.json (likely local-dev DB)")
    try:
        import json as _j
        entries = _j.loads(audit_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        return (False, 1, [], f"failed to read send_audit: {e}")
    yesterday = (datetime.utcnow().date() - timedelta(days=1)).isoformat()
    wkday = (datetime.utcnow().date() - timedelta(days=1)).weekday()
    if wkday >= 5:
        return (True, 0, [], f"yesterday {yesterday} was weekend")
    successes = [e for e in entries
                 if e.get("phase") == "result" and e.get("allowed")
                 and str(e.get("timestamp", "")).startswith(yesterday)]
    return (len(successes) >= 1, 0 if successes else 1,
            [{"yesterday": yesterday, "successful_sends": len(successes)}],
            f"{len(successes)} successful sends on {yesterday}")


@_make_assertion("autogo_decision_recent", "send_pipeline")
def check_autogo_decision_recent(conn: sqlite3.Connection) -> tuple:
    """Today's auto-GO decision file should exist + be < 24h old (on weekdays)."""
    decision_path = PROJECT_ROOT / "data" / ".preflight_decision.json"
    if datetime.utcnow().date().weekday() >= 5:
        return (True, 0, [], "weekend; no decision file expected")
    if not decision_path.exists():
        return (True, 0, [], "no .preflight_decision.json (local-dev DB or pre-preflight)")
    try:
        age_h = (datetime.utcnow().timestamp() - decision_path.stat().st_mtime) / 3600
    except OSError as e:
        return (False, 1, [], f"stat failed: {e}")
    passed = age_h < 24
    return (passed, 0 if passed else 1,
            [{"age_h": round(age_h, 1)}],
            f"preflight_decision.json age {age_h:.1f}h")


# ============================================================================
# REPORTS KPI CONSISTENCY
# ============================================================================

@_make_assertion("rex_kpi_aum_consistency", "reports_kpi")
def check_rex_aum_consistency(conn: sqlite3.Connection) -> tuple:
    """BUG-05 class: any fund with issuer_display='REX' should also have
    is_rex=1 (the asymmetric direction). The opposite — is_rex=1 with
    issuer_display='MicroSectors' or other REX-licensed brands — is
    correct data, not a bug.
    """
    rows = conn.execute("""
        SELECT ticker, fund_name, COALESCE(aum, 0) AS aum
        FROM mkt_master_data
        WHERE market_status = 'ACTV'
          AND UPPER(TRIM(issuer_display)) = 'REX'
          AND COALESCE(is_rex, 0) != 1
        ORDER BY aum DESC
        LIMIT 20
    """).fetchall()
    return (len(rows) == 0, len(rows),
            [{"ticker": r[0], "fund_name": (r[1] or "")[:40],
              "aum_M": round(r[2]/1e6, 2)} for r in rows[:5]],
            f"{len(rows)} ACTV funds with issuer_display='REX' but is_rex=0 (AXTU class)")


@_make_assertion("active_etp_count_sanity", "reports_kpi")
def check_active_count(conn: sqlite3.Connection) -> tuple:
    """Active ETP count should be in [1000, 10000] — anything outside is suspect."""
    n = conn.execute("""
        SELECT COUNT(DISTINCT ticker_clean) FROM mkt_master_data
        WHERE market_status = 'ACTV' AND fund_type IN ('ETF', 'ETN')
    """).fetchone()[0] or 0
    passed = 1000 <= n <= 10000
    return (passed, 0 if passed else 1,
            [{"active_etp_count": n}],
            f"active ETP count: {n} (expected [1000, 10000])")


# ============================================================================
# PHASE 4 / 5 / 6 INTEGRITY (added 2026-05-19 evening)
# ============================================================================

@_make_assertion("canonical_id_coverage", "integrity")
def check_canonical_id(conn: sqlite3.Connection) -> tuple:
    """Every rex_products row should have a canonical_id (Phase 4 Stage 2)."""
    rows = conn.execute("""
        SELECT id, ticker, name FROM rex_products
        WHERE canonical_id IS NULL OR canonical_id = ''
        LIMIT 20
    """).fetchall()
    return (len(rows) == 0, len(rows),
            [{"id": r[0], "ticker": r[1], "name": (r[2] or "")[:40]} for r in rows[:5]],
            f"{len(rows)} rex_products without canonical_id")


@_make_assertion("identifier_xref_consistency", "integrity")
def check_xref_consistency(conn: sqlite3.Connection) -> tuple:
    """No fund-level identifier should map to >1 canonical_id with valid_to=NULL.

    Catches ticker recycling errors at the data layer. Only checks id_types
    that are fund-unique. CIK is excluded (one CIK = one trust = many funds).
    """
    # id_types that should be 1:1 with canonical_id
    UNIQUE_TYPES = ("ticker", "series_id", "class_contract_id",
                    "bloomberg", "figi", "cusip", "isin")
    placeholders = ",".join("?" for _ in UNIQUE_TYPES)
    rows = conn.execute(f"""
        SELECT id_type, id_value, COUNT(DISTINCT canonical_id) AS n
        FROM identifier_xref
        WHERE valid_to IS NULL
          AND id_type IN ({placeholders})
        GROUP BY id_type, id_value
        HAVING n > 1
        LIMIT 20
    """, UNIQUE_TYPES).fetchall()
    return (len(rows) == 0, len(rows),
            [{"id_type": r[0], "id_value": r[1], "canonical_id_count": r[2]} for r in rows[:5]],
            f"{len(rows)} (id_type, id_value) pairs with >1 canonical_id (excl. cik)")


@_make_assertion("override_canonical_id_valid", "integrity")
def check_override_canonical_id(conn: sqlite3.Connection) -> tuple:
    """Every classification_override row should reference a real canonical_id."""
    rows = conn.execute("""
        SELECT co.canonical_id, co.field_name
        FROM classification_override co
        LEFT JOIN product_master pm ON pm.canonical_id = co.canonical_id
        WHERE pm.canonical_id IS NULL
        LIMIT 20
    """).fetchall()
    return (len(rows) == 0, len(rows),
            [{"canonical_id": r[0][:8] + "...", "field_name": r[1]} for r in rows[:5]],
            f"{len(rows)} classification_override rows reference unknown canonical_id")


@_make_assertion("status_history_current_exists", "integrity")
def check_status_history_current(conn: sqlite3.Connection) -> tuple:
    """Every product_master should have exactly one open status_history row (valid_to=NULL)."""
    rows = conn.execute("""
        SELECT pm.canonical_id, COUNT(sh.id) AS n_open
        FROM product_master pm
        LEFT JOIN status_history sh
          ON sh.canonical_id = pm.canonical_id AND sh.valid_to IS NULL
        GROUP BY pm.canonical_id
        HAVING n_open != 1
        LIMIT 20
    """).fetchall()
    return (len(rows) == 0, len(rows),
            [{"canonical_id": r[0][:8] + "...", "open_rows": r[1]} for r in rows[:5]],
            f"{len(rows)} product_master rows without exactly 1 open status_history row")


@_make_assertion("status_cached_matches_history", "integrity")
def check_status_cached(conn: sqlite3.Connection) -> tuple:
    """rex_products.status_cached should match the current open status_history.status."""
    rows = conn.execute("""
        SELECT rp.ticker, rp.status_cached, sh.status AS history_status
        FROM rex_products rp
        JOIN status_history sh
          ON sh.canonical_id = rp.canonical_id AND sh.valid_to IS NULL
        WHERE rp.canonical_id IS NOT NULL
          AND COALESCE(rp.status_cached, '') != COALESCE(sh.status, '')
        LIMIT 20
    """).fetchall()
    return (len(rows) == 0, len(rows),
            [{"ticker": r[0], "cached": r[1], "history": r[2]} for r in rows[:5]],
            f"{len(rows)} rex_products where status_cached drifted from status_history")


# ============================================================================
# RUNNER
# ============================================================================

# Auto-discover assertion functions
ASSERTIONS = [
    fn for fn in [
        check_bloomberg_freshness, check_mkt_data_volatility, check_atom_watcher,
        check_primary_strategy, check_underlier_id, check_etp_category,
        check_date_inversion, check_duplicate_active_tickers, check_listed_has_actv,
        check_recipient_lists, check_send_log_yesterday, check_autogo_decision_recent,
        check_rex_aum_consistency, check_active_count,
        # Phase 4/5/6 integrity (added 2026-05-19 evening)
        check_canonical_id, check_xref_consistency, check_override_canonical_id,
        check_status_history_current, check_status_cached,
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
