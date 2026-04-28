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
    html_path = PROJECT_ROOT / "reports" / f"li_weekly_v2_{date.today().isoformat()}.html"
    if not html_path.exists():
        from screener.li_engine.analysis.weekly_v2_report import main as build_main
        build_main()
    subject = f"Stock Recommendations of the Week - {date.today().strftime('%B %d, %Y')}"
    return subject, html_path.read_text(encoding="utf-8")


# (key, builder, list_type, critical)
# critical=True means a failure aborts the rest of the bundle.
REPORTS = {
    "daily":      (_build_daily,      "daily",      True),
    "weekly":     (_build_weekly,     "weekly",     False),
    "li":         (_build_li,         "li",         False),
    "income":     (_build_income,     "income",     False),
    "flow":       (_build_flow,       "flow",       False),
    "autocall":   (_build_autocall,   "autocall",   False),
    "stock_recs": (_build_stock_recs, "stock_recs", False),
}

BUNDLES = {
    "all":        ["daily", "weekly", "li", "income", "flow", "autocall", "stock_recs"],
    "daily":      ["daily"],
    "weekly":     ["weekly", "li", "income", "flow"],
    "autocall":   ["autocall"],
    "stock_recs": ["stock_recs"],
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


def open_gate(actor: str = "send_all.py", note: str = ""):
    GATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    GATE_FILE.write_text("true", encoding="utf-8")
    _gate_log("open", "true", actor, note)


def close_gate(actor: str = "send_all.py", note: str = ""):
    GATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    GATE_FILE.write_text("false", encoding="utf-8")
    _gate_log("close", "false", actor, note)


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

def _send_one(key: str, db, override_to: str | None, dry_run: bool,
              bypass_gate: bool, allow_self_loop: bool) -> dict:
    """Build + (optionally) send one report. Returns status dict."""
    builder, list_type, critical = REPORTS[key]
    out: dict = {
        "key": key, "list_type": list_type, "critical": critical,
        "status": "pending", "subject": None, "html_size": 0,
        "recipients": [], "note": "",
    }

    try:
        recipients = _resolve_recipients(list_type, override_to)
        if not recipients:
            out["status"] = "skipped"
            out["note"] = f"no recipients for list_type={list_type}"
            return out
        out["recipients"] = recipients

        subject, html = builder(db)
        out["subject"] = subject
        out["html_size"] = len(html)

        if dry_run:
            out["status"] = "dry_run"
            out["note"] = f"would send {len(html):,} chars to {len(recipients)} recipient(s)"
            return out

        from etp_tracker.email_alerts import _send_html_digest
        ok = _send_html_digest(
            html_body=html,
            recipients=recipients,
            edition="daily",
            subject_override=subject,
            bypass_gate=bypass_gate,
            allow_self_loop=allow_self_loop,
        )
        out["status"] = "sent" if ok else "failed"
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
    ap.add_argument("--send", action="store_true",
                    help="Actually fire the sends. Without this flag, dry-run only.")
    ap.add_argument("--to",
                    help="Override DB recipients with a single test recipient.")
    ap.add_argument("--bypass-gate", action="store_true",
                    help="Pass bypass_gate=True to _send_html_digest. Use for test sends.")
    ap.add_argument("--allow-self-loop", action="store_true",
                    help="Permit recipients matching AZURE_SENDER (relasmar@rexfin.com). "
                         "Refused by default — production sends should never self-loop.")
    ap.add_argument("--use-decision", action="store_true",
                    help="Read data/.preflight_decision.json and only fire if action=GO. "
                         "Verifies the recorded token against data/.preflight_token. "
                         "Used by the scheduled rexfinhub-daily.service for autonomous send-day.")
    args = ap.parse_args()

    # Decision-gate (autonomous send-day flow).
    # Refuse to proceed unless the dashboard recorded a GO and the token matches.
    if args.use_decision:
        decision_file = PROJECT_ROOT / "data" / ".preflight_decision.json"
        token_file = PROJECT_ROOT / "data" / ".preflight_token"
        if not decision_file.exists():
            print("ABORT: --use-decision but no data/.preflight_decision.json. Dashboard click required.")
            return 3
        if not token_file.exists():
            print("ABORT: --use-decision but no data/.preflight_token. Run preflight first.")
            return 3
        try:
            decision = json.loads(decision_file.read_text(encoding="utf-8"))
            token_info = json.loads(token_file.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"ABORT: failed to read decision/token: {e}")
            return 3
        if decision.get("token") != token_info.get("token"):
            print(f"ABORT: decision token does not match current preflight token. "
                  f"decision={decision.get('token','?')[:8]} preflight={token_info.get('token','?')[:8]}")
            return 3
        if decision.get("action", "").upper() != "GO":
            print(f"ABORT: decision recorded as {decision.get('action','?')}, not GO. Standing down.")
            return 0
        print(f"Decision: GO (token {decision.get('token','?')[:8]}, recorded {decision.get('recorded_et','?')})")

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
                            allow_self_loop=args.allow_self_loop)
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
        print(f"  [{marker}] {r['key']:12s} {r['status']:8s} {r['note']}")
    print(f"  Totals: sent={n_sent} dry_run={n_dry} failed={n_failed} skipped={n_skipped}")
    print(f"  Final gate: {gate_state()}")
    return 0 if n_failed == 0 else 2


if __name__ == "__main__":
    sys.exit(main())
