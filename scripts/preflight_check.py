"""Pre-send audit — runs ~1h before the daily send window.

Goal: surface every issue that would normally require a manual investigation
during send-day. Posts ONE summary email to Ryu so the send-day interaction
budget is "click GO or HOLD" rather than "iterate for 5 hours".

Audits performed:
    1. Bloomberg file freshness  (alert if > 12h)
    2. Classification gaps       (ACTV funds w/ NULL etp_category, NULL issuer_display,
                                  CC funds missing from attributes_CC.csv)
    3. NULL data scan            (total_return_*, fund_flow_*, aum across ACTV ETPs)
    4. Recipient diff            (live DB vs config/expected_recipients.json)
    5. Preview build             (call send_email.py preview for daily + weekly bundle;
                                  also build stock_recs HTML; record sizes)
    6. Idempotency token         (writes data/.preflight_token; send_all.py requires
                                  matching token to fire — prevents stale re-runs)

Usage (dry-run is the DEFAULT — no email is sent unless --post-summary is passed):

    python scripts/preflight_check.py
    python scripts/preflight_check.py --post-summary

Exit codes:
    0   all audits pass (or warnings only)
    1   one or more critical audits failed (BBG > 12h, classification > N gaps, etc.)
    3   bad arguments / setup error
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
TOKEN_FILE = DATA_DIR / ".preflight_token"
RESULT_FILE = DATA_DIR / ".preflight_result.json"
MAINTENANCE_FLAG = DATA_DIR / ".preflight_maintenance"
EXPECTED_RECIPIENTS = PROJECT_ROOT / "config" / "expected_recipients.json"
# Overridable so run_chain can gate a STAGING build before promoting it to the live
# previews dir (Stage C: stage -> preflight -> promote-on-green).
PREVIEW_DIR = Path(os.environ.get("REX_PREVIEW_DIR", str(PROJECT_ROOT / "outputs" / "previews")))


def _maintenance_window_active() -> bool:
    """Permanently disabled (approved plan, Stage C): the maintenance escape hatch
    is what let wrong data ship — a WARN-only downgrade meant a real defect previewed
    and sent silently. There is no longer any way to wave a failing audit through;
    fix the data (the Stage-B cascade resolves most of it automatically) instead.

    Kept as a no-op returning False so the existing `if _maintenance_window_active()`
    branches collapse to their strict (FAIL) path without restructuring every audit."""
    return False

# Thresholds — alert if exceeded
BBG_MAX_AGE_HOURS = 12
NEW_FUND_LOOKBACK_DAYS = 14
NULL_RETURN_PCT_THRESHOLD = 50.0   # >50% NULL = systemic ingest gap
NULL_FLOW_PCT_THRESHOLD = 25.0     # >25% NULL = stale data
RECIPIENT_DIFF_THRESHOLD = 3       # >3 net adds/removes = require manual confirm


def _now_et() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")
    except Exception:
        return datetime.now().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# Audit 1: Bloomberg file freshness
# ---------------------------------------------------------------------------

def audit_bloomberg(db) -> dict:
    """Check Bloomberg file mtime via Graph API (or local fallback)."""
    out = {"name": "Bloomberg freshness", "status": "pass", "detail": ""}
    try:
        from webapp.services.bbg_file import get_bloomberg_file, BloombergGraphError
    except ImportError:
        from webapp.services.bbg_file import get_bloomberg_file
        BloombergGraphError = Exception  # type: ignore
    try:
        bbg_path = get_bloomberg_file()
        age_hours = (datetime.now().timestamp() - bbg_path.stat().st_mtime) / 3600
        out["detail"] = f"{bbg_path.name} is {age_hours:.1f}h old"
        if age_hours > BBG_MAX_AGE_HOURS:
            out["status"] = "fail"
            out["detail"] += f" (>{BBG_MAX_AGE_HOURS}h threshold)"
        else:
            out["status"] = "pass"
    except Exception as e:
        out["status"] = "fail"
        out["detail"] = f"Graph API error: {type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------------------
# Audit 2: Classification gaps
# ---------------------------------------------------------------------------

def audit_ticker_dupes_recent(db) -> dict:
    """Phase 1.4 — detect ticker bleed in fund_extractions added in last 24h.

    Catches regressions of the multi-fund accession bleed bug fixed
    2026-04-30 in step3.py. If a (registrant, ticker) appears on >1 series
    in extractions added in the last 24 hours, flag for investigation.
    """
    out = {"name": "Ticker dupes (24h)", "status": "pass", "detail": "", "rows": []}
    import sqlite3
    db_path = PROJECT_ROOT / "data" / "etp_tracker.db"
    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        cur.execute("""
            SELECT f.registrant, fe.class_symbol, fe.series_name, f.accession_number,
                   f.filing_date, f.form
            FROM fund_extractions fe
            JOIN filings f ON f.id = fe.filing_id
            WHERE fe.class_symbol IS NOT NULL
              AND fe.class_symbol != ''
              AND f.filing_date >= date('now','-1 day')
              AND f.registrant IS NOT NULL
        """)
        from collections import defaultdict
        groups = defaultdict(set)
        rows = cur.fetchall()
        for reg, sym, ser, *_ in rows:
            groups[(reg, sym)].add(ser)
        bleed = [(k, v) for k, v in groups.items() if len(v) > 1]
        con.close()

        if not bleed:
            out["detail"] = f"no ticker dupes in {len(rows)} extractions added in last 24h"
        else:
            base_detail = f"{len(bleed)} (registrant, ticker) pairs duplicated across series"
            if _maintenance_window_active():
                out["status"] = "warn"
                out["detail"] = ("MAINTENANCE WINDOW ACTIVE — " + base_detail
                                 + " — remove data/.preflight_maintenance to restore strict gating")
            else:
                out["status"] = "fail"
                out["detail"] = base_detail
            out["rows"] = [
                {"registrant": k[0], "ticker": k[1], "series_count": len(v),
                 "series_names": sorted(v)[:5]}
                for k, v in bleed[:10]
            ]
    except Exception as e:
        out["status"] = "fail"
        out["detail"] = f"query error: {type(e).__name__}: {e}"
    return out


def audit_duplicate_tickers(db) -> dict:
    """Hard gate: no ticker may have >1 row in mkt_master_data.

    A ticker double-classified in fund_mapping.csv (e.g. listed PRIMARY under
    two etp_categories) produces duplicate mkt_master_data rows, which then
    render twice in reports (ACYS rendered twice in the autocall 30-Day Volume
    section, 2026-06-23). This is a structural integrity failure of the master
    table — fail loudly so the chain promote + sends are gated until the source
    mapping is fixed, rather than masking it downstream in report builders.
    """
    out = {"name": "Duplicate tickers (mkt_master_data)", "status": "pass",
           "detail": "", "rows": []}
    import sqlite3
    db_path = PROJECT_ROOT / "data" / "etp_tracker.db"
    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        cur.execute("""
            SELECT ticker, COUNT(*) AS n,
                   GROUP_CONCAT(DISTINCT etp_category) AS cats
            FROM mkt_master_data
            WHERE ticker IS NOT NULL AND ticker != ''
            GROUP BY ticker
            HAVING COUNT(*) > 1
            ORDER BY n DESC, ticker
        """)
        dupes = cur.fetchall()
        con.close()
        if not dupes:
            out["detail"] = "no duplicate tickers in mkt_master_data"
        else:
            out["status"] = "fail"
            out["detail"] = (f"{len(dupes)} ticker(s) with >1 row in mkt_master_data "
                             f"(double-classified at source — fix fund_mapping.csv)")
            out["rows"] = [
                {"ticker": t, "row_count": n, "categories": cats}
                for t, n, cats in dupes[:20]
            ]
    except Exception as e:
        out["status"] = "fail"
        out["detail"] = f"query error: {type(e).__name__}: {e}"
    return out


def audit_classification(db) -> dict:
    """Three-tier check: etp_category NULL, issuer_display NULL, CC missing from attributes."""
    import sqlite3
    db_path = PROJECT_ROOT / "data" / "etp_tracker.db"
    out = {"name": "Classification gaps", "status": "pass", "detail": "", "gaps": []}
    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        # Tier 1: NULL etp_category on recent ACTV launches
        cur.execute(f"""
            SELECT ticker, fund_name, inception_date, issuer
            FROM mkt_master_data
            WHERE market_status='ACTV'
              AND etp_category IS NULL
              AND date(inception_date) >= date('now','-{NEW_FUND_LOOKBACK_DAYS} days')
            ORDER BY inception_date DESC
        """)
        tier1 = [{"ticker": r[0], "name": r[1], "inception": r[2], "issuer": r[3],
                  "tier": "etp_category NULL"} for r in cur.fetchall()]
        # Full-ticker exclusions (exclusions.csv rows with EMPTY etp_category)
        # mean "outside all 5 tracked categories" — acknowledged, not a gap.
        # Pair-exclusions (etp_category set) still only undo a wrong pair.
        # (Engine session 2026-06-09: lets auto_classify's Other verdicts and
        # standing rulings like OBTC stop nagging forever.)
        try:
            import pandas as _pd
            _exc = _pd.read_csv(PROJECT_ROOT / "config" / "rules" / "exclusions.csv")
            _full = set(_exc[_exc["etp_category"].isna()]["ticker"].astype(str).str.strip())
            tier1 = [g for g in tier1 if g["ticker"] not in _full]
        except Exception:
            pass
        # Tier 2: NULL issuer_display on ACTV ETPs (any age)
        cur.execute("""
            SELECT ticker, fund_name, issuer
            FROM mkt_master_data
            WHERE market_status='ACTV'
              AND etp_category IS NOT NULL
              AND (issuer_display IS NULL OR issuer_display='')
            ORDER BY ticker LIMIT 30
        """)
        tier2 = [{"ticker": r[0], "name": r[1], "issuer": r[2],
                  "tier": "issuer_display NULL"} for r in cur.fetchall()]
        # Tier 3: ACTV CC funds NOT in attributes_CC.csv
        try:
            import pandas as pd
            attrs = pd.read_csv(PROJECT_ROOT / "config" / "rules" / "attributes_CC.csv",
                                engine="python", on_bad_lines="skip")
            in_csv = set(str(t).split()[0] for t in attrs["ticker"].dropna())
        except Exception:
            in_csv = set()
        cur.execute("""
            SELECT ticker, fund_name
            FROM mkt_master_data
            WHERE market_status='ACTV' AND etp_category='CC'
        """)
        tier3 = []
        for r in cur.fetchall():
            tk = str(r[0]).split()[0]
            if tk not in in_csv:
                tier3.append({"ticker": r[0], "name": r[1], "tier": "CC missing from attributes_CC.csv"})
        con.close()

        gaps = tier1 + tier2 + tier3
        out["gaps"] = gaps
        if not gaps:
            out["detail"] = "no gaps detected"
        else:
            base_detail = (f"{len(tier1)} unclassified new launches, "
                           f"{len(tier2)} NULL issuer_display, "
                           f"{len(tier3)} CC funds missing CC attributes")
            if _maintenance_window_active():
                out["status"] = "warn"
                out["detail"] = ("MAINTENANCE WINDOW ACTIVE — " + base_detail
                                 + " — remove data/.preflight_maintenance to restore strict gating")
            else:
                out["status"] = "warn" if len(gaps) <= 5 else "fail"
                out["detail"] = base_detail
    except Exception as e:
        out["status"] = "fail"
        out["detail"] = f"query error: {type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------------------
# Audit 3: NULL data scan
# ---------------------------------------------------------------------------

def audit_null_data(db) -> dict:
    """Check NULL pct on critical columns across ACTV ETPs."""
    import sqlite3
    db_path = PROJECT_ROOT / "data" / "etp_tracker.db"
    out = {"name": "NULL data", "status": "pass", "detail": "", "columns": []}
    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM mkt_master_data WHERE market_status='ACTV'")
        total = cur.fetchone()[0]
        if total == 0:
            out["status"] = "fail"
            out["detail"] = "no ACTV funds in DB"
            return out

        cols_to_check = [
            ("total_return_1day", NULL_RETURN_PCT_THRESHOLD),
            ("total_return_1week", NULL_RETURN_PCT_THRESHOLD),
            ("total_return_1month", NULL_RETURN_PCT_THRESHOLD),
            ("fund_flow_1day", NULL_FLOW_PCT_THRESHOLD),
            ("fund_flow_1week", NULL_FLOW_PCT_THRESHOLD),
            ("fund_flow_1month", NULL_FLOW_PCT_THRESHOLD),
            ("aum", NULL_FLOW_PCT_THRESHOLD),
        ]
        any_fail = False
        for col, thresh in cols_to_check:
            try:
                cur.execute(f"SELECT COUNT(*) FROM mkt_master_data WHERE market_status='ACTV' AND {col} IS NULL")
                n_null = cur.fetchone()[0]
                pct = n_null / total * 100
                row = {"column": col, "null_pct": round(pct, 1), "threshold_pct": thresh}
                if pct > thresh:
                    row["status"] = "fail"
                    any_fail = True
                else:
                    row["status"] = "pass"
                out["columns"].append(row)
            except Exception as e:
                out["columns"].append({"column": col, "status": "error", "error": str(e)})
                any_fail = True
        con.close()

        bad = [c for c in out["columns"] if c.get("status") == "fail"]
        if bad:
            out["status"] = "fail" if any(c["null_pct"] > 90 for c in bad) else "warn"
            out["detail"] = ", ".join(f"{c['column']}: {c['null_pct']}%" for c in bad)
        else:
            out["detail"] = "all critical columns under threshold"
    except Exception as e:
        out["status"] = "fail"
        out["detail"] = f"scan error: {type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------------------
# Audit 4: Recipient diff vs snapshot
# ---------------------------------------------------------------------------

def audit_recipients(db) -> dict:
    """Compare live DB recipients against expected_recipients.json snapshot."""
    out = {"name": "Recipient diff", "status": "pass", "detail": "", "diffs": {}}
    try:
        snapshot = json.loads(EXPECTED_RECIPIENTS.read_text(encoding="utf-8"))
        expected = snapshot.get("recipients_by_list", {})
    except Exception as e:
        out["status"] = "fail"
        out["detail"] = f"cannot read snapshot: {e}"
        return out

    try:
        import sqlite3
        db_path = PROJECT_ROOT / "data" / "etp_tracker.db"
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        cur.execute("SELECT list_type, email FROM email_recipients ORDER BY list_type, LOWER(email)")
        live: dict[str, list[str]] = {}
        for lt, em in cur.fetchall():
            live.setdefault(lt, []).append(em)
        con.close()

        all_lists = set(expected) | set(live)
        total_diff = 0
        for lt in sorted(all_lists):
            exp = set(e.lower() for e in expected.get(lt, []))
            cur_set = set(e.lower() for e in live.get(lt, []))
            adds = sorted(cur_set - exp)
            removes = sorted(exp - cur_set)
            if adds or removes:
                out["diffs"][lt] = {"added": adds, "removed": removes}
                total_diff += len(adds) + len(removes)

        if total_diff == 0:
            out["detail"] = "live DB matches snapshot exactly"
        elif total_diff <= RECIPIENT_DIFF_THRESHOLD:
            out["status"] = "warn"
            out["detail"] = f"{total_diff} small diff(s) — review before send"
        else:
            out["status"] = "fail"
            out["detail"] = f"{total_diff} diffs exceed threshold ({RECIPIENT_DIFF_THRESHOLD}) — manual confirm required"
    except Exception as e:
        out["status"] = "fail"
        out["detail"] = f"DB query error: {type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------------------
# Audit 5: Preview build (size + freshness only — does NOT regenerate; the
# daily pipeline + weekly preview command write the files)
# ---------------------------------------------------------------------------

def audit_previews(db) -> dict:
    out = {"name": "Previews on disk", "status": "pass", "detail": "", "files": []}
    # Filenames MUST match what the autonomous chain writes — run_chain.build_reports
    # iterates send_all.REPORTS and writes outputs/previews/<key>.html. The gate scans
    # exactly those names so a green build promotes and a missing report blocks (the
    # old daily_filing/weekly_report/... names were never written by the chain and made
    # the gate report everything missing). One preview naming convention, gate-checked.
    expected = [
        ("daily.html", PREVIEW_DIR / "daily.html"),
        ("weekly.html", PREVIEW_DIR / "weekly.html"),
        ("li.html", PREVIEW_DIR / "li.html"),
        ("income.html", PREVIEW_DIR / "income.html"),
        ("flow.html", PREVIEW_DIR / "flow.html"),
        ("autocall.html", PREVIEW_DIR / "autocall.html"),
        ("stock_recs.html", PREVIEW_DIR / "stock_recs.html"),
    ]
    missing: list[str] = []
    stale: list[str] = []
    for label, path in expected:
        row = {"file": label, "path": str(path)}
        if not path.exists():
            row["status"] = "missing"
            missing.append(label)
        else:
            age_hours = (datetime.now().timestamp() - path.stat().st_mtime) / 3600
            row["size_bytes"] = path.stat().st_size
            row["age_hours"] = round(age_hours, 1)
            if age_hours > 6:
                row["status"] = "stale"
                stale.append(label)
            else:
                row["status"] = "fresh"
        out["files"].append(row)
    if missing:
        out["status"] = "fail"
        out["detail"] = f"missing: {', '.join(missing)}"
    elif stale:
        out["status"] = "warn"
        out["detail"] = f"stale (>6h): {', '.join(stale)}"
    else:
        out["detail"] = "all 7 previews present and fresh"
    return out


# ---------------------------------------------------------------------------
# Audit 6: Data freshness (parquet files + BBG xlsm)
# ---------------------------------------------------------------------------

def audit_data_freshness(db) -> dict:
    """Verify BBG xlsm and L&I Engine parquet files are fresh enough for reports."""
    out = {"name": "Data freshness", "status": "pass", "detail": "", "items": []}
    issues = []
    bbg = PROJECT_ROOT / "data" / "DASHBOARD" / "bloomberg_daily_file.xlsm"
    if bbg.exists():
        age_h = (datetime.now().timestamp() - bbg.stat().st_mtime) / 3600
        item = {"file": bbg.name, "age_hours": round(age_h, 1), "threshold_hours": 12}
        if age_h > 12:
            item["status"] = "fail"
            issues.append(f"BBG file {age_h:.1f}h old (threshold 12h)")
        else:
            item["status"] = "pass"
        out["items"].append(item)
    else:
        issues.append("BBG xlsm not found at data/DASHBOARD/bloomberg_daily_file.xlsm")
        out["items"].append({"file": "bloomberg_daily_file.xlsm", "status": "missing"})

    parquets = [
        "bbg_timeseries_panel",
        "competitor_counts",
        "filed_underliers",
        "launch_candidates",
        "whitespace_v4",
    ]
    for p in parquets:
        f = PROJECT_ROOT / "data" / "analysis" / f"{p}.parquet"
        if not f.exists():
            issues.append(f"MISSING {p}.parquet")
            out["items"].append({"file": f"{p}.parquet", "status": "missing"})
        else:
            age_h = (datetime.now().timestamp() - f.stat().st_mtime) / 3600
            item = {"file": f"{p}.parquet", "age_hours": round(age_h, 0), "threshold_hours": 168}
            if age_h > 168:  # 7 days
                item["status"] = "fail"
                issues.append(f"{p}.parquet {age_h:.0f}h old (threshold 168h)")
            else:
                item["status"] = "pass"
            out["items"].append(item)

    if issues:
        base_detail = "; ".join(issues)
        if _maintenance_window_active():
            out["status"] = "warn"
            out["detail"] = ("MAINTENANCE WINDOW ACTIVE — " + base_detail
                             + " — remove data/.preflight_maintenance to restore strict gating")
        else:
            out["status"] = "fail"
            out["detail"] = base_detail
    else:
        out["detail"] = "all data sources fresh"
    return out


# ---------------------------------------------------------------------------
# Audit 7: Attribution completeness (primary_strategy + issuer_display)
# ---------------------------------------------------------------------------

def audit_attribution_completeness(db) -> dict:
    """Verify report attribution columns are populated on ACTV funds."""
    out = {"name": "Attribution completeness", "status": "pass", "detail": ""}
    try:
        from sqlalchemy import text
        # Use the passed-in SQLAlchemy session if available; fall back to a new one.
        _close_local = False
        if db is None:
            try:
                from webapp.database import SessionLocal
                db = SessionLocal()
                _close_local = True
            except Exception as e:
                out["status"] = "fail"
                out["detail"] = f"cannot open DB session: {e}"
                return out

        try:
            total = db.execute(
                text("SELECT count(*) FROM mkt_master_data WHERE market_status='ACTV'")
            ).scalar() or 0
            null_strat = db.execute(
                text("SELECT count(*) FROM mkt_master_data WHERE market_status='ACTV' AND primary_strategy IS NULL")
            ).scalar() or 0
            null_iss = db.execute(
                text("SELECT count(*) FROM mkt_master_data WHERE market_status='ACTV' AND issuer_display IS NULL")
            ).scalar() or 0
        finally:
            if _close_local:
                try:
                    db.close()
                except Exception:
                    pass

        issues = []
        if total > 0:
            pct_strat = 100 * null_strat / total
            pct_iss = 100 * null_iss / total
            if pct_strat > 5:
                issues.append(f"NULL primary_strategy {pct_strat:.1f}% (threshold 5%)")
            if pct_iss > 15:
                issues.append(f"NULL issuer_display {pct_iss:.1f}% (threshold 15%)")
            if issues:
                if _maintenance_window_active():
                    # Operator opted into maintenance window — downgrade to warn so
                    # the daily send can still go out while upstream fixes (R1/R2
                    # apply_classification_sweep, etc.) propagate primary_strategy /
                    # issuer_display. Remove data/.preflight_maintenance to restore
                    # strict gating.
                    out["status"] = "warn"
                    out["detail"] = ("MAINTENANCE WINDOW ACTIVE — "
                                     + "; ".join(issues)
                                     + " — remove data/.preflight_maintenance to restore strict gating")
                else:
                    out["status"] = "fail"
                    out["detail"] = "; ".join(issues)
            else:
                out["detail"] = (
                    f"{total} ACTV: NULL strat={null_strat} ({pct_strat:.1f}%), "
                    f"NULL issuer={null_iss} ({pct_iss:.1f}%)"
                )
        else:
            out["status"] = "warn"
            out["detail"] = "no ACTV funds found in mkt_master_data"
    except Exception as e:
        out["status"] = "fail"
        out["detail"] = f"query error: {type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------------------
# Idempotency token
# ---------------------------------------------------------------------------

def write_token() -> str:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    token = str(uuid.uuid4())
    payload = {
        "token": token,
        "created_et": _now_et(),
        "valid_for_hours": 4,
    }
    TOKEN_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return token


def write_result_json(audits: list[dict], token: str) -> None:
    """Write structured preflight result to data/.preflight_result.json.

    This lets /admin/health (and any automation) read preflight state directly
    without parsing the HTML summary email.

    Schema:
        {
          "timestamp": "<ISO-8601 ET>",
          "overall_status": "pass" | "warn" | "fail",
          "token": "<uuid>",
          "audits": {
            "<audit_name_snake>": {"status": "pass"|"warn"|"fail", "detail": "..."},
            ...
          }
        }
    """
    overall = "pass"
    if any(a["status"] == "fail" for a in audits):
        overall = "fail"
    elif any(a["status"] in ("warn", "error") for a in audits):
        overall = "warn"

    audit_map: dict = {}
    for a in audits:
        # Convert audit name to a safe snake_case key
        key = a.get("name", "unknown").lower().replace(" ", "_").replace("/", "_")
        audit_map[key] = {
            "status": a.get("status", "unknown"),
            "detail": a.get("detail", ""),
        }

    payload = {
        "timestamp": _now_et(),
        "overall_status": overall,
        "token": token,
        "audits": audit_map,
    }
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    RESULT_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")


# ---------------------------------------------------------------------------
# Summary HTML
# ---------------------------------------------------------------------------

_STATUS_COLORS = {"pass": "#27ae60", "warn": "#e67e22", "fail": "#e74c3c", "error": "#c0392b"}


def _status_badge(status: str) -> str:
    color = _STATUS_COLORS.get(status, "#7f8c8d")
    label = status.upper()
    return (f'<span style="display:inline-block;padding:2px 8px;border-radius:3px;'
            f'font-size:10px;font-weight:700;color:white;background:{color};">{label}</span>')


def build_summary_html(audits: list[dict], token: str) -> str:
    rows = []
    for a in audits:
        rows.append(
            f'<tr><td style="padding:8px 12px;border-bottom:1px solid #ecf0f1;">'
            f'<strong>{a["name"]}</strong></td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #ecf0f1;">{_status_badge(a["status"])}</td>'
            f'<td style="padding:8px 12px;border-bottom:1px solid #ecf0f1;font-size:12px;color:#566573;">{a["detail"]}</td></tr>'
        )
    table = "".join(rows)

    # Detailed sections per audit
    details_html = ""
    for a in audits:
        if a["name"] == "Classification gaps" and a.get("gaps"):
            inner = "".join(
                f'<li>{g["ticker"]} — {g.get("name","")} ({g["tier"]})</li>'
                for g in a["gaps"][:20]
            )
            details_html += f'<h3 style="margin:20px 0 8px;">Classification gaps</h3><ul>{inner}</ul>'
        if a["name"] == "NULL data" and a.get("columns"):
            inner = "".join(
                f'<tr><td>{c["column"]}</td><td>{c.get("null_pct","--")}%</td>'
                f'<td>{_status_badge(c.get("status","--"))}</td></tr>'
                for c in a["columns"]
            )
            details_html += (f'<h3 style="margin:20px 0 8px;">NULL data scan</h3>'
                             f'<table style="border-collapse:collapse;">'
                             f'<tr><th>Column</th><th>NULL %</th><th>Status</th></tr>'
                             f'{inner}</table>')
        if a["name"] == "Recipient diff" and a.get("diffs"):
            inner = ""
            for lt, d in a["diffs"].items():
                inner += f'<li><strong>{lt}</strong>: '
                if d.get("added"):
                    inner += f'<span style="color:#27ae60;">+{", +".join(d["added"])}</span> '
                if d.get("removed"):
                    inner += f'<span style="color:#e74c3c;">-{", -".join(d["removed"])}</span>'
                inner += '</li>'
            details_html += f'<h3 style="margin:20px 0 8px;">Recipient diffs</h3><ul>{inner}</ul>'
        if a["name"] == "Previews on disk":
            def _fmt_size(v):
                if isinstance(v, int):
                    return f"{v:,}"
                return "--"
            def _fmt_age(v):
                if isinstance(v, (int, float)):
                    return f"{v}h"
                return "--"
            inner = "".join(
                f'<tr><td>{f["file"]}</td>'
                f'<td>{_fmt_size(f.get("size_bytes"))}</td>'
                f'<td>{_fmt_age(f.get("age_hours"))}</td>'
                f'<td>{_status_badge(f.get("status","--"))}</td></tr>'
                for f in a.get("files", [])
            )
            details_html += (f'<h3 style="margin:20px 0 8px;">Previews</h3>'
                             f'<table style="border-collapse:collapse;">'
                             f'<tr><th>File</th><th>Bytes</th><th>Age</th><th>Status</th></tr>'
                             f'{inner}</table>')

    overall = "pass"
    if any(a["status"] == "fail" for a in audits):
        overall = "fail"
    elif any(a["status"] == "warn" for a in audits):
        overall = "warn"

    # Big top CTA — visually impossible to miss, replaces the buried mid-page version.
    # Color signals action: green = safe to GO, amber = warnings to review, red = HOLD.
    cta_color, cta_bg, cta_border, cta_action = {
        "pass": ("#27ae60", "#ecfdf5", "#27ae60", "GO recommended — all checks pass"),
        "warn": ("#e67e22", "#fff7ed", "#e67e22", "GO with caveats — review warnings below"),
        "fail": ("#e74c3c", "#fef2f2", "#e74c3c", "HOLD recommended — investigate before send"),
    }[overall]

    dashboard_url = "https://rex-etp-tracker.onrender.com/admin/reports/dashboard"

    cta = (
        f'<div style="margin:0 0 20px;padding:20px;background:{cta_bg};border-radius:8px;'
        f'border-left:6px solid {cta_border};">'
        f'<div style="font-size:18px;font-weight:700;color:{cta_color};margin-bottom:10px;">'
        f'{cta_action}</div>'
        f'<div style="margin-bottom:14px;font-size:13px;color:#1a1a2e;">'
        f'Click <a href="{dashboard_url}" style="color:#0984e3;font-weight:700;">'
        f'Send-Day Dashboard</a> to GO/HOLD via the admin UI, OR run on VPS:'
        f'</div>'
        f'<code style="display:block;background:#1a1a2e;color:#e8e8e8;padding:10px 12px;'
        f'border-radius:4px;font-size:12px;overflow-x:auto;">'
        f'python scripts/send_all.py --bundle all --send'
        f'</code>'
        f'<div style="font-size:11px;color:#7f8c8d;margin-top:8px;">'
        f'Token: <code>{token[:8]}…</code> &middot; valid 4h'
        f'</div></div>'
    )

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,sans-serif;color:#1a1a2e;padding:20px;max-width:720px;">
<h2 style="margin:0 0 8px;">REX Send-Day Summary &mdash; {_now_et()}</h2>
<p style="font-size:14px;color:#566573;margin:0 0 16px;">Overall: {_status_badge(overall)}</p>
{cta}
<table style="width:100%;border-collapse:collapse;margin-top:8px;">
<thead><tr style="background:#1a1a2e;color:white;">
<th style="padding:8px 12px;text-align:left;">Audit</th>
<th style="padding:8px 12px;text-align:left;">Status</th>
<th style="padding:8px 12px;text-align:left;">Detail</th>
</tr></thead>
<tbody>{table}</tbody></table>
{details_html}
</body></html>"""


# ---------------------------------------------------------------------------
# Audit 9: Report charts present (B2 — no silently-chartless reports)
# ---------------------------------------------------------------------------

def audit_report_charts(db) -> dict:
    """Assert the L&I + Income previews actually contain their market-share charts.

    B2: chart generation used to fail silently (return "") and ship a chartless
    report. Each report has 2 segments (Single Stock + Index/ETF), each carrying
    a REX-position chart. Fewer than expected — or the presence of the new visible
    failure banner — means a chart silently vanished -> HALT the send.
    """
    out = {"name": "Report charts present", "status": "pass", "detail": "", "rows": []}
    checks = [
        ("li.html", PREVIEW_DIR / "li.html", 2),
        ("income.html", PREVIEW_DIR / "income.html", 2),
    ]
    failed: list[str] = []
    for label, path, expected_n in checks:
        if not path.exists():
            # audit_previews already flags missing files; don't double-fail here.
            out["rows"].append({"file": label, "status": "skipped (missing preview)"})
            continue
        html = path.read_text(encoding="utf-8", errors="ignore")
        n = html.count('alt="REX Position"')
        has_banner = "could not be generated" in html
        row = {"file": label, "charts": n, "expected": expected_n,
               "status": "ok"}
        if n < expected_n or has_banner:
            row["status"] = "fail"
            failed.append(f"{label} ({n}/{expected_n}"
                          f"{', error-banner present' if has_banner else ''})")
        out["rows"].append(row)
    if failed:
        out["status"] = "fail"
        out["detail"] = "market-share charts missing/failed: " + "; ".join(failed)
    else:
        out["detail"] = "all expected market-share charts present"
    return out


# ---------------------------------------------------------------------------
# Audit 10: Forbidden status strings in rendered previews (NEW — hard FAIL)
# ---------------------------------------------------------------------------

# A status rendered into a cell/badge is the full text content of an HTML element.
# Matching ">Pending<" (not the bare word) targets rendered status values and avoids
# false positives in prose. canonical_status() must have mapped these away already.
_FORBIDDEN_STATUS_RE = re.compile(
    r">\s*(Pending|Delayed|Target List)\s*<", re.IGNORECASE
)


def audit_status_canonical(db) -> dict:
    """Stage C: scan every built preview for a non-canonical status that leaked into a
    rendered cell. canonical_status() is the single source — any 'Pending'/'Delayed'/
    'Target List' on a surface means a render site bypassed it. Hard FAIL."""
    out = {"name": "Status canonical", "status": "pass", "detail": "", "hits": []}
    scanned = 0
    for path in sorted(PREVIEW_DIR.glob("*.html")) if PREVIEW_DIR.exists() else []:
        try:
            html = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scanned += 1
        found = {m.group(1).title() for m in _FORBIDDEN_STATUS_RE.finditer(html)}
        if found:
            out["hits"].append({"file": path.name, "statuses": sorted(found)})
    if out["hits"]:
        out["status"] = "fail"
        out["detail"] = ("non-canonical status rendered in: "
                         + "; ".join(f"{h['file']} ({', '.join(h['statuses'])})"
                                     for h in out["hits"]))
    else:
        out["detail"] = f"no forbidden status in {scanned} previews"
    return out


# ---------------------------------------------------------------------------
# Audit 11: AI semantic review (NEW — judges correctness, not just format)
# ---------------------------------------------------------------------------

def audit_ai_semantic_review(db) -> dict:
    """Stage C: an AI reviewer of the assembled KPIs looks for *semantic* defects the
    mechanical checks can't see — a KPI that disagrees with the real REX lineup, an
    issuer_display that's a legal entity not a brand, a number that doesn't make sense.

    Degrades gracefully: if the API key isn't configured the audit is a PASS-skip (the
    cheap deterministic audits still gate). ADVISORY ONLY (2026-06-22) — it never
    fails the gate; findings surface as warnings for Ryu's review. The deterministic
    checks (contract numbers, microsectors override, status, drift) do the blocking."""
    out = {"name": "AI semantic review", "status": "pass", "detail": "", "defects": []}
    try:
        from webapp.services import claude_service
    except Exception as e:
        out["detail"] = f"reviewer unavailable ({type(e).__name__}); deterministic gates still apply"
        return out
    if not claude_service.is_configured():
        out["detail"] = "API key not configured; skipped (deterministic gates still apply)"
        return out

    # Gather the facts to judge: REX lineup count + a sample of issuer_displays.
    import sqlite3
    db_path = PROJECT_ROOT / "data" / "etp_tracker.db"
    facts: dict = {}
    try:
        con = sqlite3.connect(str(db_path))
        cur = con.cursor()
        cur.execute("SELECT COUNT(*) FROM mkt_master_data WHERE market_status='ACTV' AND COALESCE(is_rex,0)=1")
        facts["rex_actv_count"] = cur.fetchone()[0]
        cur.execute("""SELECT issuer_display, COUNT(*) FROM mkt_master_data
                       WHERE market_status='ACTV' AND COALESCE(TRIM(issuer_display),'')<>''
                       GROUP BY issuer_display ORDER BY COUNT(*) DESC LIMIT 60""")
        facts["issuer_displays"] = [{"brand": r[0], "n": r[1]} for r in cur.fetchall()]
        con.close()
    except sqlite3.Error as e:
        out["detail"] = f"could not gather facts ({e}); skipped"
        return out

    try:
        defects = claude_service.review_report_facts(facts)
    except Exception as e:
        out["detail"] = f"reviewer call failed ({type(e).__name__}); deterministic gates still apply"
        return out

    high = [d for d in defects if str(d.get("severity", "")).upper() == "HIGH"]
    out["defects"] = defects
    # ADVISORY (2026-06-22): the AI reviewer judges plausibility without ground truth
    # and false-blocked a clean run (called the canonical count 79 "low" and the real
    # 191-fund issuer "Corgi" a test value). It now WARNS only — never fails the gate.
    # Deterministic checks with ground truth do the blocking; these notes are for Ryu.
    if high:
        out["status"] = "warn"
        out["detail"] = "ADVISORY (HIGH — review before send): " + "; ".join(f"{d.get('issue','?')}" for d in high[:5])
    elif defects:
        out["status"] = "warn"
        out["detail"] = f"ADVISORY — {len(defects)} observation(s), non-blocking"
    else:
        out["detail"] = "no semantic defects found"
    return out


def audit_contract_numbers(db) -> dict:
    """Stage C: every headline number must equal its contract expected value.

    Reads the report-rules contract (config/contracts/report_numbers.yaml) and recomputes
    each number from the live master via the ONE filter (apply_report_filter), then compares
    to the declared `expected`. A mismatch is a HARD FAIL — a number that drifted from its
    rule must not ship. (Phase 6 will let a launch/closure explain an expected change; until
    then any drift blocks, which is the safe default.)"""
    out = {"name": "Contract numbers", "status": "pass", "detail": "", "mismatches": []}
    try:
        from market import contracts
        from webapp.services.report_data import _load_from_db
    except Exception as e:  # noqa: BLE001
        out["status"] = "warn"
        out["detail"] = f"contract/report_data import failed ({type(e).__name__}); skipped"
        return out
    if db is None:
        out["status"] = "warn"
        out["detail"] = "no DB session; contract numbers not checked"
        return out
    try:
        master = _load_from_db(db)["master"]
    except Exception as e:  # noqa: BLE001
        out["status"] = "warn"
        out["detail"] = f"could not load master ({type(e).__name__}); skipped"
        return out

    checked = 0
    for rid, expected in contracts.expected_values().items():
        got = contracts.count_for_rule(master, rid)
        checked += 1
        if got != expected:
            out["mismatches"].append({"rule": rid, "expected": expected, "got": got})
        # member-level check where the contract pins exact members (e.g. NVII/TSII/WMTI)
        members = contracts.get_rule(rid).get("expected_members")
        if members:
            sel = contracts.apply_report_filter(master, rid)
            actual = sorted(str(t).replace(" US", "").strip() for t in sel.get("ticker_clean", []))
            if actual != sorted(members):
                out["mismatches"].append({"rule": rid, "expected_members": sorted(members),
                                          "got_members": actual})

    if out["mismatches"]:
        out["status"] = "fail"
        out["detail"] = "; ".join(
            (f"{m['rule']}: expected {m['expected']} got {m['got']}" if "expected" in m
             else f"{m['rule']}: members {m['got_members']} != {m['expected_members']}")
            for m in out["mismatches"]
        )
    else:
        out["detail"] = f"all {checked} contract numbers tie out"
    return out


def audit_timeseries_drift(db) -> dict:
    """Stage C: the time-series enrichment must match the master (it's re-stamped as the
    last enrichment post-step). Any drift means charts disagree with the reports — HARD
    FAIL so the inconsistency can't ship."""
    out = {"name": "Time-series drift", "status": "pass", "detail": ""}
    try:
        from scripts.restamp_time_series import check_drift
    except Exception as e:  # noqa: BLE001
        out["status"] = "warn"
        out["detail"] = f"restamp import failed ({type(e).__name__}); skipped"
        return out
    try:
        drift = check_drift()
    except Exception as e:  # noqa: BLE001
        out["status"] = "warn"
        out["detail"] = f"drift check failed ({type(e).__name__}); skipped"
        return out
    if drift:
        out["status"] = "fail"
        out["detail"] = (f"{len(drift)} time-series tickers disagree with master "
                         f"(e.g. {', '.join(d['ticker'] for d in drift[:6])}) — "
                         "restamp_time_series did not run or failed")
    else:
        out["detail"] = "time-series enrichment matches master"
    return out



def audit_microsectors_override(db) -> dict:
    """Stage C (Tier-2 structural invariant): the MicroSectors true-AUM override MUST
    actually apply. For each reliable ETN the DB AUM must equal the override sheet's
    value (not raw inflated Bloomberg). Catches a silent override failure — e.g. the
    duplicate-date crash that left FNGU at $2,161M vs its true ~$944M. HARD FAIL."""
    out = {"name": "MicroSectors override", "status": "pass", "detail": "", "mismatches": []}
    try:
        import pandas as pd
        from market import microsectors as _ms
        from webapp.services.bbg_file import get_bloomberg_file
    except Exception as e:
        out["status"] = "warn"
        out["detail"] = f"override check unavailable ({type(e).__name__}); skipped"
        return out
    try:
        ov = _ms.read_overrides(pd.ExcelFile(get_bloomberg_file()))
    except Exception as e:
        out["status"] = "fail"
        out["detail"] = f"read_overrides raised ({type(e).__name__}: {e}) — override NOT applied; ETN AUM is raw Bloomberg"
        return out
    import sqlite3
    db_path = PROJECT_ROOT / "data" / "etp_tracker.db"
    checked = 0
    try:
        con = sqlite3.connect(str(db_path)); cur = con.cursor()
        for tk, vals in ov.items():
            if not isinstance(vals, dict):
                continue
            exp = vals.get("aum")
            if exp is None:
                continue
            cur.execute("SELECT aum FROM mkt_master_data WHERE ticker=? AND market_status='ACTV'", (tk,))
            row = cur.fetchone()
            if not row or row[0] is None:
                continue
            checked += 1
            got = float(row[0])
            if exp and abs(got - exp) / abs(exp) > 0.02:
                out["mismatches"].append({"ticker": tk, "override": round(exp, 1), "db": round(got, 1)})
        con.close()
    except sqlite3.Error as e:
        out["status"] = "warn"
        out["detail"] = f"DB read failed ({e}); skipped"
        return out
    if out["mismatches"]:
        out["status"] = "fail"
        out["detail"] = "override NOT baked for " + ", ".join(
            f"{m['ticker']} (db {m['db']} vs override {m['override']})" for m in out["mismatches"][:6])
    else:
        out["detail"] = f"all {checked} MicroSectors ETNs match the override"
    return out


# ---------------------------------------------------------------------------
# Audit 12: External jargon backstop (NEW — hard FAIL; root-cause seatbelt)
# ---------------------------------------------------------------------------

def audit_external_jargon(db) -> dict:
    """BACKSTOP for the report-audience intent layer (market/contracts PART 5).

    For every report whose contract audience is EXTERNAL (it leaves the building to
    a partner), scan its built staging preview for any FORBIDDEN_EXTERNAL_TERMS —
    internal table/column names like rex_products / fund_status. This is the seatbelt:
    layer 1 (the contract content_rules) should make such a leak un-authorable; this
    audit guarantees one can't ship even if it slips through. Hard FAIL, naming the
    report + the offending term. Matched case-insensitively with word-ish boundaries
    so prose ("the products page") never false-hits."""
    out = {"name": "External jargon", "status": "pass", "detail": "", "hits": []}
    try:
        from market import contracts
    except Exception as e:  # noqa: BLE001
        out["status"] = "warn"
        out["detail"] = f"contract import failed ({type(e).__name__}); skipped"
        return out

    terms = contracts.FORBIDDEN_EXTERNAL_TERMS
    # \b is too loose for snake_case (underscore is a word char), so anchor each term
    # on non-word boundaries we control: (^|[^\w]) ... ([^\w]|$). Underscores inside the
    # term are literal, so "rex_products" matches "rex_products" but not "myrex_products".
    patterns = {
        t: re.compile(r"(?:^|[^A-Za-z0-9_])" + re.escape(t) + r"(?:[^A-Za-z0-9_]|$)",
                      re.IGNORECASE)
        for t in terms
    }

    external = [k for k in contracts.reports() if contracts.report_audience(k) == "external"]
    scanned = 0
    for key in sorted(external):
        path = PREVIEW_DIR / f"{key}.html"
        if not path.exists():
            # audit_previews flags missing files for the daily set; external weeklies
            # may legitimately be absent on a given run — skip, do not fail.
            continue
        try:
            html = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scanned += 1
        for term, rx in patterns.items():
            if rx.search(html):
                out["hits"].append({"report": key, "term": term})

    if out["hits"]:
        out["status"] = "fail"
        out["detail"] = ("internal jargon in external report(s): "
                         + "; ".join(f"{h['report']} -> {h['term']}" for h in out["hits"]))
    else:
        out["detail"] = f"no forbidden internal terms in {scanned} external preview(s)"
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Pre-send audit + summary email")
    ap.add_argument("--post-summary", action="store_true",
                    help="Send the summary email (via send_critical_alert to relasmar). "
                         "Without this flag, summary HTML is written to outputs/preflight_summary.html only.")
    args = ap.parse_args()

    print(f"=== preflight_check.py @ {_now_et()} ===\n")

    # DB session for builders that want one (most audits hit sqlite directly).
    db = None
    try:
        from webapp.database import init_db, SessionLocal
        init_db()
        db = SessionLocal()
    except Exception as e:
        print(f"WARN: DB session init failed (non-fatal for audits): {e}")

    audits = []
    for fn in (audit_bloomberg, audit_classification, audit_duplicate_tickers,
               audit_ticker_dupes_recent,
               audit_null_data, audit_recipients, audit_previews,
               audit_data_freshness, audit_attribution_completeness,
               audit_report_charts, audit_status_canonical,
               audit_contract_numbers, audit_timeseries_drift,
               audit_microsectors_override, audit_external_jargon,
               audit_ai_semantic_review):
        print(f"--- {fn.__name__} ---")
        try:
            res = fn(db)
        except Exception as e:
            res = {"name": fn.__name__, "status": "error",
                   "detail": f"unhandled: {type(e).__name__}: {e}"}
        audits.append(res)
        print(f"  status: {res['status']}")
        print(f"  detail: {res['detail']}")
        print()

    token = write_token()
    print(f"Idempotency token: {token} (written to {TOKEN_FILE})\n")

    write_result_json(audits, token)
    print(f"Structured result written: {RESULT_FILE}")

    # --- AUTO-GO (zero-touch) -------------------------------------------
    # When preflight returns clean PASS, auto-write the decision file so
    # send_all.py --use-decision can fire without a human dashboard click.
    # Override: touch data/.send_paused to disable auto-GO (manual mode).
    overall = "pass"
    if any(a["status"] == "fail" for a in audits):
        overall = "fail"
    elif any(a["status"] in ("warn", "error") for a in audits):
        overall = "warn"

    # Phase 7 Part B Stage 2 (ADR 0010): prefer DB-backed system_flags reads;
    # fall back to legacy file-exists check when the helper isn't importable.
    paused_flag = DATA_DIR / ".send_paused"
    decision_file = DATA_DIR / ".preflight_decision.json"
    try:
        from webapp.services.system_flags import get_flag as _flag
        _send_paused = _flag("send_paused")
        _autogo_on_warn = _flag("autogo_on_warn")
    except ImportError:
        _send_paused = paused_flag.exists()
        _autogo_on_warn = (DATA_DIR / ".autogo_on_warn").exists()

    auto_go_action = None
    auto_go_reason = None
    if _send_paused:
        auto_go_reason = f"send_paused flag is set"
    elif overall == "pass":
        auto_go_action = "GO"
        auto_go_reason = f"auto-GO: preflight overall_status=pass at {_now_et()}"
    elif overall == "warn":
        # WARN auto-GO is configurable; default OFF until explicit opt-in
        if _autogo_on_warn:
            auto_go_action = "GO"
            auto_go_reason = (f"auto-GO: preflight overall_status=warn with "
                              f"autogo_on_warn flag set at {_now_et()}")
        else:
            auto_go_reason = (f"no auto-GO: overall=warn; set autogo_on_warn "
                              f"flag to enable WARN auto-GO")
    else:
        auto_go_reason = f"no auto-GO: overall_status={overall}"

    if auto_go_action:
        decision_payload = {
            "token": token,
            "action": auto_go_action,
            "reason": auto_go_reason,
            "decided_at": _now_et(),
            "decided_by": "preflight_auto",
        }
        decision_file.write_text(json.dumps(decision_payload, indent=2),
                                 encoding="utf-8")
        print(f"AUTO-GO: decision written ({decision_file}) — {auto_go_reason}")
    else:
        # Remove stale decision file from prior auto-GO so we don't
        # accidentally fire on yesterday's token.
        if decision_file.exists():
            try:
                decision_file.unlink()
                print(f"AUTO-GO skipped — removed stale decision file ({auto_go_reason})")
            except OSError:
                pass
        else:
            print(f"AUTO-GO skipped — {auto_go_reason}")

    # --- Stage tracker --------------------------------------------------
    # Append-only JSONL recording every pipeline stage outcome. Read by
    # the end-of-day summary email so each stage's status is verifiable.
    stage_log = DATA_DIR / ".pipeline_stages.jsonl"
    try:
        with stage_log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({
                "stage": "preflight",
                "timestamp": _now_et(),
                "overall_status": overall,
                "auto_go_action": auto_go_action,
                "auto_go_reason": auto_go_reason,
                "token": token[:8],
            }) + "\n")
    except OSError as e:
        print(f"WARN: stage log append failed: {e}")

    summary_html = build_summary_html(audits, token)
    out_html = PROJECT_ROOT / "outputs" / "preflight_summary.html"
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(summary_html, encoding="utf-8")
    print(f"Summary HTML written: {out_html} ({len(summary_html):,} chars)")

    if args.post_summary and overall == "fail":
        # Stage D — silence by default: only email when there's something to ACT on
        # (a HOLD). On green/warn the routine summary is noise; run_chain sends the
        # single "reports ready" message instead. The full HTML is always on disk.
        print("\nPreflight FAIL — posting HOLD summary via send_critical_alert ...")
        try:
            from etp_tracker.email_alerts import send_critical_alert
            ok = send_critical_alert(
                subject=f"REX Send-Day HOLD — {date.today().isoformat()}",
                message=summary_html,
                subject_prefix="[PREFLIGHT]",
            )
            print(f"  {'SENT' if ok else 'FAILED'}")
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
    elif args.post_summary:
        print(f"\nPreflight {overall.upper()} — summary written to disk, no email "
              f"(Stage D: green/warn is not emailed; run_chain sends 'reports ready').")
    else:
        print(f"\nDRY-RUN — no email sent. Open the file in a browser to review:")
        print(f"  {out_html}")

    if db is not None:
        try:
            db.close()
        except Exception:
            pass

    overall_fail = any(a["status"] == "fail" for a in audits)
    return 1 if overall_fail else 0


if __name__ == "__main__":
    sys.exit(main())
