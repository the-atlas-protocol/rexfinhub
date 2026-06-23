"""Atomic batch sender for REX FinHub email reports.

Solves the race condition that surfaced 2026-04-27: separate SSH commands
for gate-open / send / gate-close allowed concurrent operations to interleave.
This script wraps the entire flow in a single Python process with a try/finally
guarantee that the gate is closed regardless of what happens mid-batch.

Usage (default is DRY-RUN — nothing is sent unless --send is passed):

    # Preview what would happen, no Graph API calls, no gate change
    python scripts/send_all.py --bundle all
    python scripts/send_all.py --bundle daily

    # Actually fire the bundle
    python scripts/send_all.py --bundle all --send

    # Test send to one recipient (uses bypass_gate, gate stays locked)
    python scripts/send_all.py --bundle daily --send --to ryuogawaelasmar@gmail.com

Bundles:
    all         — daily + weekly + li + income + flow + autocall + stock_recs
    daily       — daily only
    weekly      — weekly + li + income + flow (the four-report Mon bundle)
    autocall    — autocall only (RBC + CAIS + REX team)
    stock_recs  — stock_recs only

Exit codes:
    0   all reports in the bundle succeeded
    2   one or more reports failed (gate still locked at exit)
    3   bad arguments / setup error
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

GATE_FILE = PROJECT_ROOT / "config" / ".send_enabled"
INTENT_FILE = PROJECT_ROOT / "data" / ".send_intent.json"
GATE_LOG = PROJECT_ROOT / "data" / ".gate_state_log.jsonl"


# ---------------------------------------------------------------------------
# Bundle definitions — mirror REPORT_CATALOG / send_email.py builders so we
# don't reinvent. When report_registry.py refactor lands (plan task #6),
# this dict goes away and we iterate the registry instead.
# ---------------------------------------------------------------------------

def _build_daily(db) -> tuple[str, str]:
    from scripts.send_email import _build_daily_filing, _data_date
    return f"REX Daily ETP Report: {_data_date(db)}", _build_daily_filing(db)


def _build_weekly(db) -> tuple[str, str]:
    from etp_tracker.weekly_digest import build_weekly_digest_html
    from scripts.send_email import _data_date
    return f"REX Weekly ETP Report: {_data_date(db)}", build_weekly_digest_html(db, "")


def _build_li(db) -> tuple[str, str]:
    from scripts.send_email import _build_li, _data_date
    return f"REX ETP Leverage & Inverse Report: {_data_date(db)}", _build_li(db)


def _build_income(db) -> tuple[str, str]:
    from scripts.send_email import _build_income, _data_date
    return f"REX ETP Income Report: {_data_date(db)}", _build_income(db)


def _build_flow(db) -> tuple[str, str]:
    from scripts.send_email import _build_flow, _data_date
    return f"REX ETP Flow Report: {_data_date(db)}", _build_flow(db)


def _build_autocall(db) -> tuple[str, str]:
    from scripts.send_email import _build_autocall, _data_date
    return f"Autocallable ETF Weekly Update: {_data_date(db)}", _build_autocall(db)


def _build_stock_recs(db) -> tuple[str, str]:
    # Re-wired 2026-06-09 (Ryu): the stock_recs slot must send the current
    # T-REX Combined report (trex_combined_v9), NOT the old li_weekly_v2
    # "Stock Recommendations of the Week" report. They are different reports;
    # the pipeline was still pointing at the legacy one.
    html_path = PROJECT_ROOT / "reports" / f"trex_combined_{date.today().isoformat()}.html"
    # ALWAYS rebuild fresh at send time so we never ship a stale earlier bake.
    # (The old "if not exists" guard meant a file baked before a later fix would
    # be sent as-is.) Ryu 2026-06-09.
    try:
        # trex_combined_v9's entrypoint is build(), not main(). The old import
        # of a nonexistent `main` raised ImportError straight into the fallback
        # below, silently shipping a stale earlier bake on every send
        # (audit 2026-06-09).
        from screener.li_engine.analysis.trex_combined_v9 import build as build_main
        build_main()
    except Exception as _e:
        if not html_path.exists():
            raise
        print(f"WARN: trex_combined rebuild failed ({_e}); falling back to last good file")
    subject = f"T-REX Stock Recommendation System - {date.today().strftime('%B %d, %Y')}"
    return subject, html_path.read_text(encoding="utf-8")


def _build_blue_ocean(db) -> tuple[str, str]:
    html_path = PROJECT_ROOT / "reports" / f"blue_ocean_{date.today().isoformat()}.html"
    # ALWAYS rebuild fresh at send time (no stale earlier-bake). Ryu 2026-06-09.
    try:
        from screener.li_engine.analysis.blue_ocean_report import main as build_main
        build_main()
    except Exception as _e:
        if not html_path.exists():
            raise
        print(f"WARN: blue_ocean rebuild failed ({_e}); falling back to last good file")
    subject = f"Blue Ocean — L&I Overnight Trading · {date.today().strftime('%b %d, %Y')}"
    return subject, html_path.read_text(encoding="utf-8")


def _build_portfolio_suite(db) -> tuple[str, str]:
    from pathlib import Path
    from webapp.services.portfolio_suite_flow import build_html
    project_root = Path(__file__).resolve().parent.parent
    db_path = str(project_root / "data" / "etp_tracker.db")
    xlsm_path = str(project_root / "data" / "DASHBOARD" / "bloomberg_daily_file.xlsm")
    return build_html(db_path, xlsm_path)


def _build_microsectors(db) -> tuple[str, str]:
    # MicroSectors L&I Industry Report — the competitive weekly (5-page, brand fonts).
    # Brought into the repo from a chat artifact (scripts/microsectors_industry_report.py)
    # so it's reproducible. ALWAYS rebuild fresh so we never ship a stale bake.
    import subprocess
    html_path = PROJECT_ROOT / "reports" / f"microsectors_industry_{date.today().isoformat()}.html"
    try:
        subprocess.run([sys.executable, str(PROJECT_ROOT / "scripts" / "microsectors_industry_report.py")],
                       cwd=str(PROJECT_ROOT), check=True, timeout=300)
    except Exception as _e:
        if not html_path.exists():
            raise
        print(f"WARN: microsectors rebuild failed ({_e}); using last good file")
    subject = f"MicroSectors L&I Industry Report — {date.today().strftime('%B %d, %Y')}"
    return subject, html_path.read_text(encoding="utf-8")


# (key, builder, list_type, critical)
# critical=True means a failure aborts the rest of the bundle.
REPORTS = {
    "daily":           (_build_daily,           "daily",           True),
    "weekly":          (_build_weekly,          "weekly",          False),
    "li":              (_build_li,              "li",              False),
    "income":          (_build_income,          "income",          False),
    "flow":            (_build_flow,            "flow",            False),
    "autocall":        (_build_autocall,        "autocall",        False),
    "stock_recs":      (_build_stock_recs,      "stock_recs",      False),
    "blue_ocean":      (_build_blue_ocean,      "blue_ocean",      False),
    "portfolio_suite": (_build_portfolio_suite, "portfolio_suite", False),
    "microsectors":    (_build_microsectors,    "microsectors",    False),
}

BUNDLES = {
    "all":             ["daily", "weekly", "li", "income", "flow", "autocall", "stock_recs", "blue_ocean", "microsectors", "portfolio_suite"],
    "daily":           ["daily"],
    "weekly":          ["weekly", "li", "income", "flow"],
    "autocall":        ["autocall"],
    "stock_recs":      ["stock_recs"],
    "blue_ocean":      ["blue_ocean"],
    "portfolio_suite": ["portfolio_suite"],
    "microsectors":    ["microsectors"],
    "weekly_internal": ["daily", "weekly", "li", "income", "flow", "stock_recs", "portfolio_suite"],
    "weekly_external": ["autocall", "microsectors"],
}


# ---------------------------------------------------------------------------
# Gate management — atomic with try/finally
# ---------------------------------------------------------------------------

def _gate_log(action: str, state: str, actor: str, note: str = ""):
    """Append-only log of every gate state change. Forensic trail."""
    GATE_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "timestamp": _now_et(),
        "action": action,    # "open" | "close" | "read"
        "state": state,      # "true" | "false"
        "actor": actor,      # "send_all.py" | "systemd" | "atlas" | etc.
        "note": note,
    }
    with GATE_LOG.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def _now_et() -> str:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).isoformat(timespec="seconds")
    except Exception:
        return datetime.now().isoformat(timespec="seconds")


def preflight_blocks_send(result_file: Path, red_marker: Path) -> tuple[bool, str]:
    """Pure predicate (Stage C) — should a real send be HARD-BLOCKED?

    Blocks when the chain left a red marker OR the latest preflight result is a FAIL.
    A missing/unreadable result file does NOT block on its own (the marker is the
    authoritative red signal; absence of a result means preflight simply hasn't run in
    this flow, e.g. a one-off test). Extracted + unit-tested because this is the single
    guarantee that wrong data never reaches a recipient."""
    if red_marker.exists():
        return True, "preflight_red marker present"
    try:
        overall = json.loads(result_file.read_text(encoding="utf-8")).get("overall_status")
    except Exception:
        overall = None
    if overall == "fail":
        return True, "preflight overall_status=fail"
    return False, ""


def open_gate(actor: str = "send_all.py", note: str = ""):
    # ADR 0010: the DB-backed flag is authoritative; set_flag dual-writes the
    # legacy file. Writing only the file leaves the DB row stale -> L1 block.
    try:
        from webapp.services.system_flags import set_flag
        set_flag("send_enabled", True, set_by=actor, notes=note)
    except Exception:
        GATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        GATE_FILE.write_text("true", encoding="utf-8")
    _gate_log("open", "true", actor, note)


def close_gate(actor: str = "send_all.py", note: str = ""):
    # Verify the gate actually re-locks; a DB lock mid-send can otherwise leave it
    # silently OPEN (this happened 2026-06-22). Retry, then fail LOUD.
    import time as _t
    for _attempt in range(4):
        try:
            from webapp.services.system_flags import set_flag, get_flag
            set_flag("send_enabled", False, set_by=actor, notes=note)
            if get_flag("send_enabled") is False:
                _gate_log("close", "false", actor, note)
                return
        except Exception:
            try:
                GATE_FILE.parent.mkdir(parents=True, exist_ok=True)
                GATE_FILE.write_text("false", encoding="utf-8")
            except Exception:
                pass
        _t.sleep(0.4)
    try:
        GATE_FILE.write_text("false", encoding="utf-8")
    except Exception:
        pass
    print("  !! WARNING: gate close could not be verified after retries — forced file lock; CHECK THE GATE")
    _gate_log("close", "false", actor, note + " [close verification FAILED -- forced]")


def gate_state() -> str:
    if not GATE_FILE.exists():
        return "missing"
    return GATE_FILE.read_text(encoding="utf-8").strip().lower()


# ---------------------------------------------------------------------------
# Recipient resolution
# ---------------------------------------------------------------------------

def _resolve_recipients(list_type: str, override_to: str | None) -> list[str]:
    """Resolve recipient list. --to overrides DB lookup."""
    if override_to:
        return [override_to.strip()]
    from etp_tracker.email_alerts import _load_recipients
    return _load_recipients(list_type=list_type)


# ---------------------------------------------------------------------------
# Single-report send (used inside the atomic batch loop)
# ---------------------------------------------------------------------------

# --- already-sent guard (Ryu 2026-06-22): a report is sent once per period --------------
from pathlib import Path as _Path
_SEND_LOG_DB = str(_Path(__file__).resolve().parent.parent / "data" / "etp_tracker.db")


def _et_today():
    from datetime import datetime, timezone, timedelta
    return (datetime.now(timezone.utc) - timedelta(hours=4)).date()


def _period_key(key):
    t = _et_today()
    if key == "daily":
        return t.isoformat()                       # once per day
    if key == "blue_ocean":
        return t.strftime("%Y-%m")                 # once per month
    iso = t.isocalendar()
    return "%d-W%02d" % (iso[0], iso[1])           # weekly + the rest: once per ISO week


def _send_log_con():
    import sqlite3
    con = sqlite3.connect(_SEND_LOG_DB)
    con.execute("CREATE TABLE IF NOT EXISTS send_log (report_key TEXT, period_key TEXT, "
                "recipients INTEGER, sent_at TEXT, PRIMARY KEY(report_key, period_key))")
    return con


def _already_sent(key):
    con = _send_log_con()
    try:
        r = con.execute("SELECT sent_at FROM send_log WHERE report_key=? AND period_key=?",
                        (key, _period_key(key))).fetchone()
        return r[0] if r else None
    finally:
        con.close()


def _record_send(key, n):
    from datetime import datetime, timezone
    con = _send_log_con()
    try:
        con.execute("INSERT OR REPLACE INTO send_log (report_key, period_key, recipients, sent_at) "
                    "VALUES (?,?,?,?)", (key, _period_key(key), int(n),
                                         datetime.now(timezone.utc).isoformat()))
        con.commit()
    finally:
        con.close()


PDF_REPORTS = {"microsectors"}  # reports that ship a Chrome-rendered PDF attachment


def _render_report_pdf(html: str):
    """Render report HTML to PDF bytes via the bundled Chromium (Playwright).
    Returns None on failure so a render hiccup never blocks the email."""
    try:
        import tempfile, os
        from playwright.sync_api import sync_playwright
        with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False, encoding="utf-8") as _f:
            _f.write(html); _src = _f.name
        _out = _src[:-5] + ".pdf"
        with sync_playwright() as _p:
            _b = _p.chromium.launch()
            _pg = _b.new_page()
            _pg.goto("file://" + _src, wait_until="load")
            _pg.pdf(path=_out, print_background=True, prefer_css_page_size=True)
            _b.close()
        _data = open(_out, "rb").read()
        try: os.unlink(_src); os.unlink(_out)
        except OSError: pass
        return _data
    except Exception as _e:
        print(f"  WARN: PDF render failed: {_e}")
        return None


# --- baked-report reuse (Ryu 2026-06-23): reuse the HTML the chain already baked -----
# Root fix for slow sends: scripts/run_chain.py bakes every report to
# outputs/previews/<key>.html (promoted only on a GREEN preflight) and records
# outputs/previews/.baked.json: {key: {subject, file, built_at, data_date}}.
# We reuse that baked HTML at send time ONLY when it is provably fresh; on ANY doubt
# we fall back to rebuilding via the builder. A stale baked file is NEVER sent.
_LIVE_PREVIEWS = PROJECT_ROOT / "outputs" / "previews"
_BAKED_MANIFEST = _LIVE_PREVIEWS / ".baked.json"


def _expected_data_date() -> str:
    """The refresh date the send expects: REXFIN_ASOF_DATE if set, else ET today.
    Must mirror run_chain._refresh_data_date / data_engine._resolve_as_of so a baked
    stamp from this same refresh matches."""
    import os
    raw = os.environ.get("REXFIN_ASOF_DATE", "").strip()
    if raw:
        try:
            return datetime.strptime(raw, "%Y-%m-%d").date().isoformat()
        except ValueError:
            pass  # invalid -> fall through to today (loud handling lives in data_engine)
    return _et_today().isoformat()


def _rebuild_reason(key: str) -> str:
    """Human-readable WHY a report is being rebuilt instead of reused. Best-effort —
    re-derives the first failing freshness condition for the log line. Never raises."""
    try:
        if not _BAKED_MANIFEST.exists():
            return "no manifest"
        manifest = json.loads(_BAKED_MANIFEST.read_text(encoding="utf-8"))
        entry = manifest.get(key)
        if not isinstance(entry, dict):
            return "no manifest entry"
        baked = entry.get("data_date")
        exp = _expected_data_date()
        if not baked:
            return "manifest entry undated"
        if baked != exp:
            return f"stale data_date {baked} != {exp}"
        fname = entry.get("file") or f"{key}.html"
        if not (_LIVE_PREVIEWS / fname).exists():
            return "baked file missing"
        if not entry.get("subject"):
            return "no subject recorded"
        return "empty baked file"
    except Exception as e:
        return f"manifest error: {type(e).__name__}"


def _load_baked(key: str):
    """Return (subject, html) from the baked manifest IFF provably fresh, else None
    (caller rebuilds). Fail-safe by construction: missing manifest, missing entry,
    missing file, mismatched/parse-error data_date -> None. Never raises."""
    try:
        if not _BAKED_MANIFEST.exists():
            return None
        manifest = json.loads(_BAKED_MANIFEST.read_text(encoding="utf-8"))
        entry = manifest.get(key)
        if not isinstance(entry, dict):
            return None
        baked_date = entry.get("data_date")
        if not baked_date or baked_date != _expected_data_date():
            return None  # stale (or undated) bake -> rebuild
        fname = entry.get("file") or f"{key}.html"
        html_path = _LIVE_PREVIEWS / fname
        if not html_path.exists():
            return None
        subject = entry.get("subject")
        if not subject:
            return None  # no subject recorded -> rebuild rather than guess
        html = html_path.read_text(encoding="utf-8")
        if not html.strip():
            return None  # empty file -> rebuild
        return subject, html
    except Exception:
        return None  # ANY error -> rebuild. Correctness over speed.


def _send_one(key: str, db, override_to: str | None, dry_run: bool,
              bypass_gate: bool, allow_self_loop: bool,
              bypass_rate_limit: bool = False, force: bool = False,
              force_rebuild: bool = False) -> dict:
    """Build + (optionally) send one report. Returns status dict."""
    builder, list_type, critical = REPORTS[key]
    out: dict = {
        "key": key, "list_type": list_type, "critical": critical,
        "status": "pending", "subject": None, "html_size": 0,
        "recipients": [], "note": "", "source": None,
    }

    try:
        recipients = _resolve_recipients(list_type, override_to)
        if not recipients:
            out["status"] = "skipped"
            out["note"] = f"no recipients for list_type={list_type}"
            return out
        out["recipients"] = recipients
        # A TEST send (override_to / --to set) must NOT participate in the
        # real-send duplicate guard at all: it never reads the real send_log
        # (so a prior real send cannot block it) and never writes it (so it
        # cannot make a later real send read as "already sent"). This decouples
        # /sendalltome test sends from real sends -- the root of the
        # --force/duplicate-send mess. Ryu 2026-06-22.
        is_test_send = override_to is not None
        if (not dry_run) and (not force) and (not is_test_send):
            _prev = _already_sent(key)
            if _prev:
                out["status"] = "skipped"
                out["note"] = f"already sent for period {_period_key(key)} at {_prev}; use --force"
                return out

        # Prefer the HTML the chain already baked (provably fresh); else rebuild.
        _baked = None if force_rebuild else _load_baked(key)
        if _baked is not None:
            subject, html = _baked
            out["source"] = "baked"
            print(f"  [baked]   {key}: reused outputs/previews/{key}.html "
                  f"(data_date={_expected_data_date()})")
        else:
            _reason = "forced" if force_rebuild else _rebuild_reason(key)
            subject, html = builder(db)
            out["source"] = "rebuild"
            print(f"  [rebuild: {_reason}] {key}")
        out["subject"] = subject
        out["html_size"] = len(html)

        if dry_run:
            out["status"] = "dry_run"
            out["note"] = f"would send {len(html):,} chars to {len(recipients)} recipient(s)"
            if key in PDF_REPORTS:
                out["note"] += " (+PDF attachment)"
            return out

        attachments = None
        if key in PDF_REPORTS:
            from datetime import date as _date
            _pdf = _render_report_pdf(html)
            if _pdf:
                attachments = [(f"{key}_{_date.today().isoformat()}.pdf", _pdf, "application/pdf")]
                out["note"] = f"PDF attached ({len(_pdf):,} bytes)"
            else:
                out["note"] = "PDF render failed; sent HTML only"

        from etp_tracker.email_alerts import _send_html_digest
        ok = _send_html_digest(
            html_body=html,
            recipients=recipients,
            edition="daily",
            subject_override=subject,
            attachments=attachments,
            bypass_gate=bypass_gate,
            allow_self_loop=allow_self_loop,
            bypass_rate_limit=bypass_rate_limit,
        )
        out["status"] = "sent" if ok else "failed"
        # Only a REAL send writes the duplicate-guard send_log. A test send
        # (override_to set) must leave the real send_log untouched. Ryu 2026-06-22.
        if ok and not is_test_send:
            _record_send(key, len(recipients))
        if not ok:
            out["note"] = "blocked by safeguard or Graph API rejected (see audit log)"
    except Exception as e:
        out["status"] = "error"
        out["note"] = f"{type(e).__name__}: {e}"
        traceback.print_exc()
    return out


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Atomic batch sender for REX reports")
    ap.add_argument("--bundle", required=True, choices=sorted(BUNDLES.keys()))
    ap.add_argument("--force", action="store_true", help="override the already-sent guard")
    ap.add_argument("--force-rebuild", action="store_true",
                    help="ignore the baked staging HTML and rebuild every report from the "
                         "DB at send time (the old behavior). Use only to bypass the "
                         "baked-reuse fast path; freshness is otherwise auto-guarded.")
    ap.add_argument("--send", action="store_true",
                    help="Actually fire the sends. Without this flag, dry-run only.")
    ap.add_argument("--to",
                    help="Override DB recipients with a single test recipient.")
    ap.add_argument("--bypass-gate", action="store_true",
                    help="Pass bypass_gate=True to _send_html_digest. Use for test sends.")
    ap.add_argument("--allow-self-loop", action="store_true",
                    help="Permit recipients matching AZURE_SENDER (relasmar@rexfin.com). "
                         "Refused by default — production sends should never self-loop.")
    ap.add_argument("--bypass-rate-limit", action="store_true",
                    help="Skip the L6 per-recipient daily cap check. Use ONLY for "
                         "operator-driven re-sends after a freeze send already burned "
                         "the day's cap (e.g. 2026-05-19 19:30 ET retry). Logged in "
                         "send_audit.json with phase='bypass_rate_limit'.")
    ap.add_argument("--use-decision", action="store_true",
                    help="Read data/.preflight_decision.json and only fire if action=GO. "
                         "Verifies the recorded token against data/.preflight_token. "
                         "Used by the scheduled rexfinhub-daily.service for autonomous send-day.")
    args = ap.parse_args()
    # Builders invoke CLI main()s that re-parse sys.argv (e.g. blue_ocean_report.main).
    # Neutralize argv now that ours is parsed, so a report build never dies on
    # "unrecognized arguments" and abort the bundle mid-send. Ryu 2026-06-22.
    import sys as _sys; _sys.argv = _sys.argv[:1]

    # Decision-gate (autonomous send-day flow).
    # Refuse to proceed unless the dashboard recorded a GO and the token matches.
    if args.use_decision:
        decision_file = PROJECT_ROOT / "data" / ".preflight_decision.json"
        token_file = PROJECT_ROOT / "data" / ".preflight_token"

        def _decision_alert(subject: str, body: str) -> None:
            """Fire send_critical_alert AND append to gate log. Best-effort."""
            _gate_log("read", "standing_down", "send_all.py --use-decision", subject)
            try:
                from etp_tracker.email_alerts import send_critical_alert
                send_critical_alert(subject=subject, message=body)
            except Exception as _e:
                print(f"WARN: send_critical_alert failed: {_e}")

        if not decision_file.exists():
            _ts = _now_et()
            msg = (f"send_all.py was invoked with --use-decision at {_ts} ET "
                   f"but data/.preflight_decision.json does not exist. "
                   f"No bundle was sent. Dashboard click required to authorise send.")
            print(f"ABORT: --use-decision but no data/.preflight_decision.json. Dashboard click required.")
            _decision_alert(f"SEND STOOD DOWN -- no decision file at {_ts} ET", msg)
            return 3
        if not token_file.exists():
            _ts = _now_et()
            msg = (f"send_all.py was invoked with --use-decision at {_ts} ET "
                   f"but data/.preflight_token does not exist. "
                   f"No bundle was sent. Run preflight first.")
            print(f"ABORT: --use-decision but no data/.preflight_token. Run preflight first.")
            _decision_alert(f"SEND STOOD DOWN -- no preflight token at {_ts} ET", msg)
            return 3
        try:
            decision = json.loads(decision_file.read_text(encoding="utf-8"))
            token_info = json.loads(token_file.read_text(encoding="utf-8"))
        except Exception as e:
            _ts = _now_et()
            msg = (f"send_all.py --use-decision failed to parse decision/token files at {_ts} ET. "
                   f"Error: {e}. No bundle was sent.")
            print(f"ABORT: failed to read decision/token: {e}")
            _decision_alert(f"SEND STOOD DOWN -- decision/token parse error at {_ts} ET", msg)
            return 3
        if decision.get("token") != token_info.get("token"):
            _ts = _now_et()
            d_tok = decision.get("token", "?")[:8]
            p_tok = token_info.get("token", "?")[:8]
            msg = (f"send_all.py --use-decision detected a TOKEN MISMATCH at {_ts} ET. "
                   f"Decision token: {d_tok}... / Current preflight token: {p_tok}... "
                   f"This likely means preflight was re-run after the dashboard click. "
                   f"No bundle was sent. Click GO again on the fresh preflight.")
            print(f"ABORT: decision token does not match current preflight token. "
                  f"decision={d_tok} preflight={p_tok}")
            _decision_alert(f"SEND STOOD DOWN -- token mismatch at {_ts} ET", msg)
            return 3
        if decision.get("action", "").upper() != "GO":
            _ts = _now_et()
            action = decision.get("action", "?")
            reason = decision.get("reason", "no reason recorded")
            msg = (f"send_all.py --use-decision found action={action} (not GO) at {_ts} ET. "
                   f"Reason recorded: {reason}. No bundle was sent.")
            print(f"ABORT: decision recorded as {action}, not GO. Standing down.")
            _decision_alert(f"SEND STOOD DOWN -- decision={action} at {_ts} ET (reason: {reason})", msg)
            return 0
        _gate_log("read", "go_authorized", "send_all.py --use-decision",
                  f"token={decision.get('token','?')[:8]} recorded={decision.get('recorded_et','?')}")
        print(f"Decision: GO (token {decision.get('token','?')[:8]}, recorded {decision.get('recorded_et','?')})")

    # Stage C — hard-block any real send when the latest preflight is RED. This couples
    # the send checkpoint to a green build for BOTH the autonomous (--use-decision) and
    # the manual (--send) paths; previously only --use-decision was gated. A test send
    # (--to) or dry-run is allowed through so the operator can still preview.
    if args.send and not args.to:
        blocked, reason = preflight_blocks_send(
            PROJECT_ROOT / "data" / ".preflight_result.json",
            PROJECT_ROOT / "data" / ".preflight_red",
        )
        if blocked:
            _ts = _now_et()
            print(f"ABORT: send refused — {reason} at {_ts} ET. Fix the data and re-run the chain.")
            _gate_log("read", "blocked_red_preflight", "send_all.py", reason)
            return 2

    dry_run = not args.send
    bundle_keys = BUNDLES[args.bundle]

    # Footgun guard: --to is a test-send flag. NEVER touch the production gate
    # for a test send, and force bypass_gate=True so the test fires regardless
    # of gate state. This eliminates a class of mistake where someone runs
    # --send --to test@email.com expecting a test, and the gate auto-opens.
    if args.to:
        if not args.bypass_gate:
            args.bypass_gate = True
            print("NOTE: --to implies --bypass-gate (test-send mode). Forced bypass_gate=True.")

    print(f"=== send_all.py ===")
    print(f"  bundle:           {args.bundle} -> {bundle_keys}")
    print(f"  mode:             {'DRY-RUN' if dry_run else 'SEND'}")
    print(f"  override --to:    {args.to or '(use DB)'}")
    print(f"  bypass_gate:      {args.bypass_gate}")
    print(f"  allow_self_loop:  {args.allow_self_loop}")
    print(f"  initial gate:     {gate_state()}")
    print()

    # Open DB session once for the whole batch.
    try:
        from webapp.database import init_db, SessionLocal
        init_db()
        db = SessionLocal()
    except Exception as e:
        print(f"FATAL: DB init failed: {e}")
        return 3

    results: list[dict] = []
    initial_gate = gate_state()

    # Atomic try/finally — gate is GUARANTEED to return to 'false' on exit.
    # Skip gate management entirely in dry-run or when bypass_gate is on
    # (test sends shouldn't churn the production gate).
    manage_gate = (not dry_run) and (not args.bypass_gate)

    try:
        if manage_gate:
            open_gate(note=f"send_all.py bundle={args.bundle}")
            print(f"  GATE OPENED (was: {initial_gate})")
            print()

        for key in bundle_keys:
            print(f"--- {key} ---")
            res = _send_one(key, db,
                            override_to=args.to,
                            dry_run=dry_run,
                            bypass_gate=args.bypass_gate,
                            allow_self_loop=args.allow_self_loop,
                            bypass_rate_limit=args.bypass_rate_limit,
                            force=args.force,
                            force_rebuild=args.force_rebuild)
            results.append(res)
            print(f"  status:    {res['status']}")
            if res["subject"]:
                print(f"  subject:   {res['subject']}")
            if res["html_size"]:
                print(f"  size:      {res['html_size']:,} chars")
            if res["recipients"]:
                print(f"  recipients ({len(res['recipients'])}): {', '.join(res['recipients'][:5])}"
                      + (" ..." if len(res["recipients"]) > 5 else ""))
            if res["note"]:
                print(f"  note:      {res['note']}")
            print()
            if res["status"] == "failed" and res["critical"]:
                print(f"!! Critical report '{key}' failed — aborting remaining bundle items")
                break
    finally:
        if manage_gate:
            close_gate(note=f"send_all.py bundle={args.bundle} done")
            print(f"  GATE LOCKED (final state: {gate_state()})")
        try:
            db.close()
        except Exception:
            pass

    # Summary
    print("=== SUMMARY ===")
    n_sent = sum(1 for r in results if r["status"] == "sent")
    n_dry = sum(1 for r in results if r["status"] == "dry_run")
    n_failed = sum(1 for r in results if r["status"] in ("failed", "error"))
    n_skipped = sum(1 for r in results if r["status"] == "skipped")
    for r in results:
        marker = {"sent": "OK", "dry_run": "..", "failed": "XX",
                  "error": "ER", "skipped": "--", "pending": "??"}.get(r["status"], "??")
        _src = f"({r['source']})" if r.get("source") else ""
        print(f"  [{marker}] {r['key']:12s} {r['status']:8s} {_src:9s} {r['note']}")
    print(f"  Totals: sent={n_sent} dry_run={n_dry} failed={n_failed} skipped={n_skipped}")
    print(f"  Final gate: {gate_state()}")
    return 0 if n_failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
