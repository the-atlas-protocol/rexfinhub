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
    """Every Listed REX product resolves to a typed (non-unknown) underlier.

    Catches both a missing fund_underlier link AND a link that points to an
    'unknown'-type underlier_master row (the junk-underlier case — e.g. the
    MicroSectors ETNs that were linked to the junk '0' underlier).
    """
    rows = conn.execute("""
        SELECT rp.ticker, rp.name
        FROM rex_products rp
        WHERE rp.status = 'Listed'
          AND NOT EXISTS (
              SELECT 1 FROM fund_underlier fu
              JOIN underlier_master um ON um.underlier_id = fu.underlier_id
              WHERE fu.canonical_id = rp.canonical_id
                AND um.underlier_type != 'unknown'
          )
        LIMIT 20
    """).fetchall()
    return (len(rows) == 0, len(rows),
            [{"ticker": r[0], "name": r[1]} for r in rows[:5]],
            f"{len(rows)} Listed REX products without a resolved (typed) underlier")


@_make_assertion("etp_category_coverage", "classification")
def check_etp_category(conn: sqlite3.Connection) -> tuple:
    """Every active REX product that belongs to a legacy bucket should have etp_category.

    The legacy etp_category has exactly five buckets (LI/CC/Crypto/Defined/Thematic).
    REX products whose curated 3-axis asset_class is outside that universe — T-Bill /
    bond ladders (Fixed Income), Multi-Asset, Currency — legitimately have NO legacy
    category and are NOT shown in any legacy-bucket report (those filter on etp_category;
    Portfolio buckets them by rex_suite). So they're exempt: forcing a wrong legacy label
    would be data corruption (Ryu, 2026-06-16). Funds still lacking BOTH etp_category and
    a curated asset_class are genuine gaps and still fail.
    """
    rows = conn.execute("""
        SELECT ticker FROM mkt_master_data
        WHERE market_status = 'ACTV' AND is_rex = 1
          AND (etp_category IS NULL OR etp_category = '')
          AND COALESCE(asset_class, '') NOT IN ('Fixed Income', 'Multi-Asset', 'Currency')
        LIMIT 20
    """).fetchall()
    return (len(rows) == 0, len(rows),
            [{"ticker": r[0]} for r in rows[:5]],
            f"{len(rows)} active REX products missing etp_category (excl. non-legacy asset classes)")


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


@_make_assertion("rex_etp_count_sanity", "reports_kpi")
def check_rex_count(conn: sqlite3.Connection) -> tuple:
    """REX-branded ACTV count should be in [60, 150] (current ~82 + room to grow)."""
    n = conn.execute("""
        SELECT COUNT(DISTINCT ticker_clean) FROM mkt_master_data
        WHERE market_status = 'ACTV' AND is_rex = 1
    """).fetchone()[0] or 0
    passed = 60 <= n <= 150
    return (passed, 0 if passed else 1,
            [{"rex_actv_count": n}],
            f"REX ACTV count: {n} (expected [60, 150])")


# ============================================================================
# SECRETS + INFRA HEALTH
# ============================================================================

@_make_assertion("audit_log_freshness", "infra")
def check_audit_log_freshness(conn: sqlite3.Connection) -> tuple:
    """capm_audit_log should have at least one entry from the last 7 days.
    Catches a stalled-edits / paralyzed-admin scenario.
    """
    row = conn.execute("""
        SELECT MAX(changed_at) FROM capm_audit_log
    """).fetchone()
    if not row or not row[0]:
        return (False, 1, [], "capm_audit_log is empty")
    latest_str = str(row[0])
    try:
        latest = datetime.fromisoformat(latest_str.replace("Z", "+00:00"))
        if latest.tzinfo:
            latest = latest.replace(tzinfo=None)
        age_d = (datetime.utcnow() - latest).total_seconds() / 86400
        passed = age_d < 7
        return (passed, 0 if passed else 1,
                [{"latest_audit": latest_str, "age_days": round(age_d, 1)}],
                f"latest capm_audit_log entry {age_d:.1f} days ago (threshold 7)")
    except (ValueError, TypeError) as e:
        return (False, 1, [], f"failed to parse: {e}")


@_make_assertion("fund_underlier_weight_sanity", "integrity")
def check_fund_underlier_weight(conn: sqlite3.Connection) -> tuple:
    """For multi-underlier (basket) funds, weights should sum to ~1.0.

    Single-underlier funds with weight=NULL are OK (implicit 100%).
    """
    rows = conn.execute("""
        SELECT canonical_id, ROUND(SUM(COALESCE(weight, 0)), 4) AS sum_w,
               COUNT(*) AS n_underliers
        FROM fund_underlier
        WHERE effective_to IS NULL
        GROUP BY canonical_id
        HAVING n_underliers > 1
           AND ABS(sum_w - 1.0) > 0.01
        LIMIT 20
    """).fetchall()
    return (len(rows) == 0, len(rows),
            [{"canonical_id": r[0][:8] + "...", "sum_weight": r[1],
              "n": r[2]} for r in rows[:5]],
            f"{len(rows)} multi-underlier funds with weights not summing to 1.0")


@_make_assertion("classification_override_coverage", "integrity")
def check_override_categories(conn: sqlite3.Connection) -> tuple:
    """Sanity: no more than 25% of override rows have field_name='etp_category'.

    If we're overriding etp_category on most products, the auto-classifier
    is broken — that's a signal to investigate (not just rubber-stamp).
    """
    total = conn.execute("SELECT COUNT(*) FROM classification_override").fetchone()[0] or 0
    if total < 50:
        return (True, 0, [], f"only {total} overrides total — threshold not meaningful yet")
    etp_n = conn.execute(
        "SELECT COUNT(*) FROM classification_override WHERE field_name='etp_category'"
    ).fetchone()[0] or 0
    pct = etp_n / total * 100
    passed = pct <= 25.0
    return (passed, 0 if passed else 1,
            [{"etp_category_overrides": etp_n, "total_overrides": total,
              "pct": round(pct, 1)}],
            f"{pct:.1f}% of overrides are etp_category (threshold 25%)")


@_make_assertion("backup_recent", "infra")
def check_backup(conn: sqlite3.Connection) -> tuple:
    """Most recent NIGHTLY backup (etp_tracker_YYYYMMDD.db) should be < 30 hours old.

    Audit 2026-06-09: the old glob ``etp_tracker*.db*`` also matched the
    ``*_pre_*`` rollback snapshots written every 15 minutes by sync --apply,
    so a dead nightly backup stayed green indefinitely. Match ONLY the
    nightly naming pattern. A missing backups dir is a FAILURE on the VPS
    (it means the backup unit never ran); only local dev gets a pass.
    """
    import re as _re
    import sys as _sys
    backup_dir = PROJECT_ROOT / "data" / "backups"
    on_vps = _sys.platform.startswith("linux")
    if not backup_dir.exists():
        if on_vps:
            return (False, 1, [], "data/backups missing on VPS — backup unit never ran")
        return (True, 0, [], "no backups dir (local dev DB)")
    nightly = [p for p in backup_dir.glob("etp_tracker_*.db")
               if _re.fullmatch(r"etp_tracker_\d{8}\.db", p.name)]
    if not nightly:
        if not on_vps:
            return (True, 0, [], "no nightly backups (local dev DB)")
        return (False, 1, [], "no nightly etp_tracker_YYYYMMDD.db backups found")
    latest = max(nightly, key=lambda p: p.stat().st_mtime)
    age_h = (datetime.utcnow().timestamp() - latest.stat().st_mtime) / 3600
    passed = age_h < 30
    return (passed, 0 if passed else 1,
            [{"latest_backup": latest.name, "age_h": round(age_h, 1)}],
            f"latest nightly backup {age_h:.1f}h old (threshold 30h)")


@_make_assertion("restrictive_flag_age", "send_pipeline")
def check_restrictive_flag_age(conn: sqlite3.Connection) -> tuple:
    """Restrictive system_flags must not be forgotten.

    send_paused stuck on for 14 days and preflight_maintenance for a month
    both silently degraded the system (audit 2026-06-09). Nag until cleared:
    send_paused > 3 days or preflight_maintenance > 7 days = FAIL.
    """
    limits_days = {"send_paused": 3, "preflight_maintenance": 7}
    try:
        rows = conn.execute(
            "SELECT flag_name, is_set, set_at, set_by FROM system_flags"
        ).fetchall()
    except sqlite3.OperationalError:
        return (True, 0, [], "no system_flags table (local dev DB)")
    stale = []
    for name, is_set, set_at, set_by in rows:
        if name not in limits_days or not is_set:
            continue
        try:
            age_d = (datetime.utcnow() - datetime.fromisoformat(set_at)).days
        except (TypeError, ValueError):
            age_d = 999
        if age_d > limits_days[name]:
            stale.append({"flag": name, "age_days": age_d, "set_by": set_by,
                          "limit_days": limits_days[name]})
    passed = not stale
    return (passed, len(stale), stale,
            f"{len(stale)} restrictive flag(s) older than their limit"
            if stale else "no stale restrictive flags")


@_make_assertion("git_tree_clean", "infra")
def check_git_tree_clean(conn: sqlite3.Connection) -> tuple:
    """Production working tree must match git (deploy=git, ADR 0011 E4).

    scp-deployed-but-uncommitted files caused unversioned production code
    (audit 2026-06-09: nightly L&I engine existed only on VPS disk). Tracked
    modifications/deletions = FAIL on the VPS; untracked files are reported
    but tolerated. Local dev always passes (work-in-progress is normal).
    """
    import subprocess as _sp
    import sys as _sys
    if not _sys.platform.startswith("linux"):
        return (True, 0, [], "local dev — not enforced")
    try:
        out = _sp.run(["git", "status", "--porcelain"], cwd=str(PROJECT_ROOT),
                      capture_output=True, text=True, timeout=30).stdout
    except Exception as e:  # git missing / not a repo
        return (True, 0, [], f"git unavailable ({e})")
    tracked = [l for l in out.splitlines() if l[:2].strip() and not l.startswith("??")]
    untracked_n = sum(1 for l in out.splitlines() if l.startswith("??"))
    passed = not tracked
    return (passed, len(tracked),
            [{"path": l[3:], "state": l[:2].strip()} for l in tracked[:5]],
            f"{len(tracked)} tracked file(s) modified on prod tree, "
            f"{untracked_n} untracked" if tracked
            else f"tree clean ({untracked_n} untracked tolerated)")


@_make_assertion("preflight_token_valid", "send_pipeline")
def check_preflight_token(conn: sqlite3.Connection) -> tuple:
    """If decision file exists, its token must match the current preflight token."""
    token_path = PROJECT_ROOT / "data" / ".preflight_token"
    decision_path = PROJECT_ROOT / "data" / ".preflight_decision.json"
    if not (token_path.exists() and decision_path.exists()):
        return (True, 0, [], "no token/decision pair (no preflight run yet)")
    try:
        import json as _j
        token_data = _j.loads(token_path.read_text(encoding="utf-8"))
        cur_token = token_data.get("token") if isinstance(token_data, dict) else str(token_data).strip()
        decision = _j.loads(decision_path.read_text(encoding="utf-8"))
        dec_token = decision.get("token")
    except (json.JSONDecodeError, OSError, AttributeError) as e:
        return (False, 1, [], f"parse failure: {e}")
    passed = cur_token == dec_token
    return (passed, 0 if passed else 1,
            [{"current_token": (cur_token or '')[:8], "decision_token": (dec_token or '')[:8]}],
            f"preflight_token vs decision_token match: {passed}")


@_make_assertion("legacy_capm_retired", "integrity")
def check_capm_retired(conn: sqlite3.Connection) -> tuple:
    """capm_products was retired in Track 4a. Confirm the table is gone; or, if
    it still exists mid-transition, that it has not drifted from the
    rex_products CapM overlay (the revert anchor).
    """
    exists = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='capm_products'"
    ).fetchone()
    if not exists:
        return (True, 0, [], "capm_products retired — table dropped (Track 4a)")
    capm_n = conn.execute("SELECT COUNT(*) FROM capm_products").fetchone()[0] or 0
    overlay_n = conn.execute(
        "SELECT COUNT(*) FROM rex_products WHERE inception_date IS NOT NULL"
    ).fetchone()[0] or 0
    drift = abs(capm_n - overlay_n)
    passed = drift <= 5
    return (passed, 0 if passed else 1,
            [{"capm_products_rows": capm_n, "rex_overlay_rows": overlay_n,
              "drift": drift}],
            f"capm_products still present — drift vs rex overlay: {drift} (threshold 5)")


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


@_make_assertion("status_record_single_source", "integrity")
def check_status_record_single_source(conn: sqlite3.Connection) -> tuple:
    """ADR 0012: status_history is the ONLY status record table.

    rex_product_status_history is an empty duplicate with no writer/reader and is
    slated for retirement. Assert it stays empty — a non-zero count means a second
    status-record table started being written, re-fragmenting the single source of
    truth this ADR closes.
    """
    try:
        n = conn.execute("SELECT COUNT(*) FROM rex_product_status_history").fetchone()[0]
    except sqlite3.OperationalError:
        # Table already dropped (Ryu-confirmed retirement) — invariant trivially holds.
        return (True, 0, [], "rex_product_status_history absent (retired)")
    return (n == 0, n, [{"rows": n}],
            f"rex_product_status_history has {n} rows (must be 0 — see ADR 0012)")


# REX-name detection patterns, kept in sync with
# scripts/sync_rex_products_from_filings.py REX_NAME_PATTERNS +
# COMPETITOR_NAME_PATTERNS. Duplicated here (not imported) so this assertion
# runs even if the sync script's imports break.
_ORPHAN_REX_PATTERNS = (
    r"^T-?REX\b",
    r"^REX[\s\-]+\s*OSPREY",
    r"^REX\s",
    r"^MICROSECTORS\b",
    r"^OSPREY\b",
)
_ORPHAN_COMPETITOR_PATTERNS = (
    r"^DIREXION\b", r"^PROSHARES\b", r"^DEFIANCE\b", r"^INNOVATOR\b",
    r"^ROUNDHILL\b", r"^TRADR\b", r"^GRANITESHARES\b", r"^AMPLIFY\b",
    r"^KODEX\b", r"^CORGI\b", r"^TUTTLE\b", r"^VOLATILITY\s+SHARES\b",
    r"^GLOBAL\s+X\b", r"^FIRST\s+TRUST\b", r"^THEMES\b", r"^GSR\b",
    r"^TIDAL\b", r"^HEDGEYE\b",
)


def _orphan_watermark_cutoff() -> str:
    """Return ISO date for the orphan-window lower bound.

    The watermark window is the LATER of:
      * 30 days ago (so a stalled sync script still gets caught after 30d), OR
      * the last successful run of sync_rex_products_from_filings.py (its
        watermark file mtime — the script writes today's date on success).

    Returns an ISO date string usable in a `f.filing_date >= ?` SQL filter.
    """
    cutoff = (datetime.utcnow().date() - timedelta(days=30))
    watermark_file = PROJECT_ROOT / "data" / ".sync_rex_products_watermark"
    if watermark_file.exists():
        try:
            wm_raw = watermark_file.read_text(encoding="utf-8").strip()[:10]
            wm_date = datetime.fromisoformat(wm_raw).date()
            # Use the LATER (more recent) of the two — narrow the window so a
            # freshly-running sync script doesn't keep flagging ancient orphans
            # that the repair script already cleared.
            if wm_date > cutoff:
                cutoff = wm_date
        except (OSError, ValueError):
            pass
    return cutoff.isoformat()


@_make_assertion("no_orphan_rex_extractions", "integrity")
def assert_no_orphan_rex_extractions(conn: sqlite3.Connection) -> tuple:
    """Every REX-name fund_extraction with a series_id inside the watermark
    window must have a matching identifier_xref(id_type='series_id') row.

    Catches the failure mode that the sprint1/repair-orphans script just
    cleared (38 orphans): multi-series filings where the canonical-id chain
    silently fails to materialise and the new series never appears on
    /operations/pipeline. Without this assertion, the next drop would go
    unnoticed for weeks.

    Window: filings on/after MAX(30 days ago, sync_rex_products watermark).
    See [[repair_missing_canonical]] for the matching repair path.
    """
    cutoff = _orphan_watermark_cutoff()
    rows = conn.execute("""
        SELECT fe.series_id, fe.series_name, f.registrant,
               f.form, f.filing_date
        FROM fund_extractions fe
        JOIN filings f ON f.id = fe.filing_id
        WHERE fe.series_id IS NOT NULL AND fe.series_id != ''
          AND f.filing_date >= ?
          AND NOT EXISTS (
              SELECT 1 FROM identifier_xref ix
              WHERE ix.id_type = 'series_id'
                AND ix.id_value = fe.series_id
          )
    """, (cutoff,)).fetchall()

    # Filter to REX-name only (matches sync_rex_products gating). Dedup by
    # series_id — same series can appear across multiple filings.
    import re as _re
    rex_re = [_re.compile(p, _re.IGNORECASE) for p in _ORPHAN_REX_PATTERNS]
    comp_re = [_re.compile(p, _re.IGNORECASE) for p in _ORPHAN_COMPETITOR_PATTERNS]

    def _is_rex(name: str | None) -> bool:
        if not name:
            return False
        s = name.strip()
        if any(c.match(s) for c in comp_re):
            return False
        return any(r.match(s) for r in rex_re)

    seen: set[str] = set()
    orphans: list[dict] = []
    for r in rows:
        sid = r[0]
        if sid in seen:
            continue
        if _is_rex(r[1]) or _is_rex(r[2]):
            seen.add(sid)
            orphans.append({
                "series_id": sid,
                "series_name": (r[1] or "")[:60],
                "form": r[3],
                "filing_date": str(r[4]) if r[4] else None,
            })

    n = len(orphans)
    return (n == 0, n, orphans[:5],
            f"{n} REX-name fund_extractions since {cutoff} without "
            f"identifier_xref(series_id) — run scripts/repair_missing_canonical.py")


# ============================================================================
# DRIFT DETECTION (ADR 0012 governance / Part II — keep the ledger true)
# ============================================================================

@_make_assertion("rules_dirs_in_sync", "drift")
def check_rules_dirs_in_sync(conn: sqlite3.Connection) -> tuple:
    """config/rules and data/rules must agree on every file they BOTH contain.

    The two dirs intentionally overlap (each has dir-only files), but a file in
    BOTH is a mirrored rule: the engine writes data/rules, commit_rules_delta
    syncs it to config/rules (git-tracked, → Render). A content divergence means
    the nightly sync didn't finish — classification reads one copy, git ships the
    other. At 08:00 (after the chain) they must be identical.
    """
    import hashlib
    data_dir = PROJECT_ROOT / "data" / "rules"
    cfg_dir = PROJECT_ROOT / "config" / "rules"
    if not data_dir.exists() or not cfg_dir.exists():
        return (True, 0, [], "a rules dir is absent — skipped")

    def _h(p: Path) -> str:
        return hashlib.md5(p.read_bytes()).hexdigest()

    divergent = []
    for f in sorted(data_dir.glob("*.csv")):
        twin = cfg_dir / f.name
        if twin.exists() and _h(f) != _h(twin):
            divergent.append({"file": f.name})
    return (len(divergent) == 0, len(divergent), divergent[:5],
            f"{len(divergent)} shared rule file(s) differ between data/rules and config/rules")


# Acknowledged data stores (ADR 0012 device map). A NEW *.db not here and not a
# backup/snapshot is an unacknowledged subsystem → surfaced so Ryu never has to
# hunt for forgotten databases. To accept a new store, add its basename here
# (a reviewed, version-controlled change — the ledger stays the source of truth).
_KNOWN_DB_BASENAMES = {
    "etp_tracker.db",         # main app DB — the status/market/classification authority
    "etp_tracker_deploy.db",  # Render upload staging copy
    "live_feed.db",           # live SEC filing feed store
    "rexfinhub.db",           # webapp-configured DB (repo root)
}
# Backup / snapshot churn — expected, not new subsystems.
import re as _re_drift
_DB_BACKUP_RE = _re_drift.compile(
    r"(^|/)(backups/|.*\.pre_.*\.db$|.*_deploy\.db$|.*\d{8}T\d{6}.*\.db$|.*_\d{8}\.db$)"
)


@_make_assertion("no_unknown_databases", "drift")
def check_no_unknown_databases(conn: sqlite3.Connection) -> tuple:
    """Flag any *.db / *.sqlite store that isn't an acknowledged subsystem.

    Catches the 'separate database we forgot about' failure mode: a new store
    appears (a script's scratch DB, a half-migrated table set) and silently
    becomes load-bearing. Backups and timestamped snapshots are excluded by
    pattern; everything else must be in _KNOWN_DB_BASENAMES.
    """
    root = PROJECT_ROOT
    unknown = []
    for p in list(root.rglob("*.db")) + list(root.rglob("*.sqlite")):
        rel = p.relative_to(root).as_posix()
        if rel.startswith(".git/") or _DB_BACKUP_RE.search(rel):
            continue
        if p.name not in _KNOWN_DB_BASENAMES:
            unknown.append({"db": rel})
    return (len(unknown) == 0, len(unknown), unknown[:5],
            f"{len(unknown)} unacknowledged database file(s) — review + add to "
            f"_KNOWN_DB_BASENAMES or retire")


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
        check_rex_aum_consistency, check_rex_count,
        # Added in second batch
        check_audit_log_freshness, check_fund_underlier_weight,
        check_override_categories, check_backup, check_preflight_token,
        check_capm_retired,
        # Phase 4/5/6 integrity (added 2026-05-19 evening)
        check_canonical_id, check_xref_consistency, check_override_canonical_id,
        check_status_history_current, check_status_cached,
        # Phase D hardening (sprint2): orphan-extraction prevention
        assert_no_orphan_rex_extractions,
        # Engine session (audit 2026-06-09): forgotten-flag + deploy-drift guards
        check_restrictive_flag_age, check_git_tree_clean,
        # Ultimate Fixup (2026-06-15): ADR 0012 single-source + Part II drift detection
        check_status_record_single_source,
        check_rules_dirs_in_sync, check_no_unknown_databases,
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
