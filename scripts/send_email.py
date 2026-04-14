"""
Send or preview REX email reports.

Commands (via bash aliases):
    send daily       REX Daily ETP Report
    send weekly      Weekly Report + L&I + Income + Flow
    preview daily    Open daily report in browser
    preview weekly   Open all weekly reports in browser
"""
from __future__ import annotations

import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
os.chdir(PROJECT_ROOT)

DASHBOARD_URL = "https://rex-etp-tracker.onrender.com"
PREVIEW_DIR = PROJECT_ROOT / "outputs" / "previews"
SEND_LOG = PROJECT_ROOT / "data" / ".send_log.json"


def _save_and_open(html: str, name: str):
    """Write HTML to file and open in browser."""
    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
    path = PREVIEW_DIR / f"{name}.html"
    path.write_text(html, encoding="utf-8")
    print(f"  {name}: {len(html):,} chars -> {path.name}")
    webbrowser.open(str(path.resolve()))


def _get_db():
    from webapp.database import SessionLocal
    return SessionLocal()


def _send_via_smtp(html: str, subject: str, list_type: str = "daily"):
    """Send HTML email to recipients for a specific report type."""
    from etp_tracker.email_alerts import _load_recipients, _load_private_recipients, _send_html_digest
    recipients = _load_recipients(list_type=list_type)
    private = _load_private_recipients()
    if not recipients and not private:
        print(f"  SKIP {subject} (no recipients for list_type={list_type})")
        return False
    ok = True
    if recipients:
        ok = _send_html_digest(html, recipients, subject_override=subject)
    if private:
        _send_html_digest(html, private, subject_override=subject)
    return ok


def _load_send_log() -> dict:
    """Load the send log (tracks what was sent today to prevent duplicates)."""
    import json
    if SEND_LOG.exists():
        try:
            return json.loads(SEND_LOG.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _record_send(key: str):
    """Record that a report/bundle was sent. Tracks per-report for dedup."""
    import json
    log = _load_send_log()
    today = datetime.now().strftime("%Y-%m-%d")
    if today not in log:
        log[today] = {}
    log[today][key] = datetime.now().strftime("%H:%M")
    SEND_LOG.parent.mkdir(parents=True, exist_ok=True)
    SEND_LOG.write_text(json.dumps(log, indent=2), encoding="utf-8")


def _already_sent_today(key: str) -> str | None:
    """Check if a report was already sent today. Returns time if yes, None if no."""
    log = _load_send_log()
    today = datetime.now().strftime("%Y-%m-%d")
    return log.get(today, {}).get(key)


def _already_sent_this_week(key: str) -> str | None:
    """Check if a report was already sent this ISO week. Returns 'date HH:MM' if yes."""
    log = _load_send_log()
    now = datetime.now()
    iso_year, iso_week, _ = now.isocalendar()
    for date_str, reports in log.items():
        if key not in reports:
            continue
        try:
            d = datetime.strptime(date_str, "%Y-%m-%d")
            dy, dw, _ = d.isocalendar()
            if dy == iso_year and dw == iso_week:
                return f"{date_str} {reports[key]}"
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------------------
# Daily bundle: Filing Report + L&I + Income + Flow
# ---------------------------------------------------------------------------

def _build_daily_filing(db) -> str:
    from etp_tracker.email_alerts import build_digest_html_from_db
    return build_digest_html_from_db(db, DASHBOARD_URL, edition="daily")


def _build_li(db) -> str:
    from webapp.services.report_emails import build_li_email
    html, _ = build_li_email(DASHBOARD_URL, db)
    return html


def _build_income(db) -> str:
    from webapp.services.report_emails import build_cc_email
    html, _ = build_cc_email(DASHBOARD_URL, db)
    return html


def _build_flow(db) -> str:
    from webapp.services.report_emails import build_flow_email
    html, _ = build_flow_email(DASHBOARD_URL, db)
    return html


def _build_autocall(db) -> str:
    from webapp.services.report_emails import build_autocall_email
    html, _ = build_autocall_email(DASHBOARD_URL, db)
    return html


def _build_intelligence_brief(db) -> str:
    from etp_tracker.intelligence_brief import build_intelligence_brief
    return build_intelligence_brief(db, lookback_days=1)


def _build_filing_screener(db) -> str:
    from screener.filing_screener_report import build_filing_screener_report
    return build_filing_screener_report(max_picks=5)


def _build_product_status(db) -> str:
    from etp_tracker.product_status_report import build_product_status_report
    return build_product_status_report(db)


def _data_date(db) -> str:
    """Get data date (MM/DD/YYYY) from report cache for email subjects."""
    from webapp.services.report_data import get_li_report
    data = get_li_report(db)
    return data.get("data_as_of_short", datetime.now().strftime("%m/%d/%Y"))


# (title, filename, builder, list_type for DB recipients)
DAILY_REPORTS = [
    ("REX Daily ETP Report", "daily_filing", _build_daily_filing, "daily"),
    # DISABLED (WIP): Intelligence Brief — preview only until data + copy approved
    # ("REX Filing Intelligence Brief", "intelligence_brief", _build_intelligence_brief, "intelligence"),
]

WEEKLY_REPORTS = [
    ("REX Weekly ETP Report", "weekly_report", None, "weekly"),  # special handler
    ("REX ETP Leverage & Inverse Report", "li_report", _build_li, "li"),
    ("REX ETP Income Report", "income_report", _build_income, "income"),
    ("REX ETP Flow Report", "flow_report", _build_flow, "flow"),
    # DISABLED (WIP): Filing Screener — preview only until data + copy approved
    # ("T-REX Filing Candidate Screener", "filing_screener", _build_filing_screener, "screener"),
]

# Monday-only report (separate from weekly bundle) — ALL DISABLED (WIP)
MONDAY_REPORTS = [
    # ("REX Product Pipeline", "product_status", _build_product_status, "pipeline"),
]

# Autocall report has its own recipient list (external distribution)
AUTOCALL_REPORT = ("Autocallable ETF Weekly Update", "autocall_report", _build_autocall)

def _load_autocall_recipients() -> list[str]:
    path = PROJECT_ROOT / "config" / "autocall_recipients.txt"
    if path.exists():
        return [l.strip() for l in path.read_text().splitlines() if l.strip() and not l.startswith("#")]
    return []


def do_daily(preview: bool):
    db = _get_db()
    force = "--force" in [a.lower() for a in sys.argv[1:]]
    try:
        date = _data_date(db)
        for base_title, filename, builder, list_type in DAILY_REPORTS:
            subject = f"{base_title}: {date}"
            if not preview and not force:
                prev = _already_sent_today(filename)
                if prev:
                    print(f"\n  BLOCKED: {filename} already sent today at {prev}")
                    continue
            print(f"\n  Building {subject}...")
            html = builder(db)
            if preview:
                _save_and_open(html, filename)
            else:
                ok = _send_via_smtp(html, subject, list_type=list_type)
                print(f"  {'Sent' if ok else 'FAILED'}: {subject}")
                if ok:
                    _record_send(filename)
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Weekly bundle: Weekly Report + L&I + Income + Flow
# ---------------------------------------------------------------------------

def do_weekly(preview: bool):
    from etp_tracker.weekly_digest import build_weekly_digest_html, send_weekly_digest

    db = _get_db()
    force = "--force" in [a.lower() for a in sys.argv[1:]]
    try:
        date = _data_date(db)

        # Weekly report
        # Previous structure had a bug: `if not preview and not force` + `elif preview`
        # skipped the entire block when both preview=False AND force=True. Restructure
        # so --force correctly bypasses the dedup check but still sends.
        weekly_subject = f"REX Weekly ETP Report: {date}"
        if preview:
            print(f"\n  Building {weekly_subject}...")
            html = build_weekly_digest_html(db, DASHBOARD_URL)
            _save_and_open(html, "weekly_report")
        else:
            prev = None if force else _already_sent_this_week("weekly_report")
            if prev:
                print(f"\n  BLOCKED: weekly_report already sent this week ({prev})")
            else:
                print(f"\n  Building {weekly_subject}...")
                ok = send_weekly_digest(db, DASHBOARD_URL)
                print(f"  {'Sent' if ok else 'FAILED'}: {weekly_subject}")
                if ok:
                    _record_send("weekly_report")

        # Market reports (L&I, Income, Flow)
        for base_title, filename, builder, list_type in WEEKLY_REPORTS:
            if builder is None:
                continue  # weekly_report handled above
            subject = f"{base_title}: {date}"
            if not preview and not force:
                prev = _already_sent_this_week(filename)
                if prev:
                    print(f"\n  BLOCKED: {filename} already sent this week ({prev})")
                    continue
            print(f"\n  Building {subject}...")
            html = builder(db)
            if preview:
                _save_and_open(html, filename)
            else:
                ok = _send_via_smtp(html, subject, list_type=list_type)
                print(f"  {'Sent' if ok else 'FAILED'}: {subject}")
                if ok:
                    _record_send(filename)

        # Monday-only: Product Status Report (separate email, own recipient list)
        is_monday = datetime.now().weekday() == 0
        if is_monday or preview:
            for base_title, filename, builder, list_type in MONDAY_REPORTS:
                subject = f"{base_title}: Week of {datetime.now().strftime('%b %d, %Y')}"
                if not preview and not force:
                    prev = _already_sent_this_week(filename)
                    if prev:
                        print(f"\n  BLOCKED: {filename} already sent this week ({prev})")
                        continue
                print(f"\n  Building {subject}...")
                html = builder(db)
                if preview:
                    _save_and_open(html, filename)
                else:
                    ok = _send_via_smtp(html, subject, list_type=list_type)
                    print(f"  {'Sent' if ok else 'FAILED'}: {subject}")
                    if ok:
                        _record_send(filename)
        elif not preview:
            print(f"\n  SKIP: Product Pipeline report (Monday only, today is {datetime.now().strftime('%A')})")

        # Autocall report — PREVIEW ONLY (never auto-send, external distribution)
        base_title, filename, builder = AUTOCALL_REPORT
        subject = f"{base_title}: {date}"
        print(f"\n  Building {subject}...")
        html = builder(db)
        if preview:
            _save_and_open(html, filename)
        else:
            print(f"  SKIP: Autocall is preview-only (send manually via separate workflow)")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def do_market_share(preview: bool):
    """Generate market share analysis (4 categories x 2 charts + summary table)."""
    from scripts.generate_market_share_charts import main as gen_charts

    print("\n  Generating market share charts...")
    gen_charts()

    report_dir = PROJECT_ROOT / "reports"
    html_file = sorted(report_dir.glob("rex_market_share_analysis_*.html"), reverse=True)
    if not html_file:
        print("  ERROR: no HTML file generated")
        return

    html_path = html_file[0]
    html = html_path.read_text(encoding="utf-8")

    if preview:
        # Copy to previews dir + open
        dest = PREVIEW_DIR / "market_share.html"
        PREVIEW_DIR.mkdir(parents=True, exist_ok=True)
        dest.write_text(html, encoding="utf-8")
        print(f"  market_share: {len(html):,} chars -> {dest.name}")
        webbrowser.open(str(dest.resolve()))
    else:
        subject = f"REX Market Share Analysis: {datetime.now().strftime('%m/%d/%Y')}"
        ok = _send_via_smtp(html, subject)
        print(f"  {'Sent' if ok else 'FAILED'}: {subject}")


VALID_BUNDLES = ("daily", "weekly", "market_share", "all")


def main():
    args = [a.lower() for a in sys.argv[1:]]

    if len(args) < 2 or args[0] not in ("send", "preview") or args[1] not in VALID_BUNDLES:
        print("Usage:")
        print("  send daily          REX Daily ETP Report")
        print("  send weekly         Weekly Report + L&I + Income + Flow")
        print("  send market_share   Market Share Analysis (CEO charts)")
        print("  preview daily       Open daily report in browser")
        print("  preview weekly      Open all weekly reports in browser")
        print("  preview market_share  Open market share analysis in browser")
        print("  preview all         Open daily + weekly reports in browser")
        sys.exit(0)

    action = args[0]
    bundle = args[1]
    preview = action == "preview"

    label = "PREVIEW" if preview else "SEND"
    print(f"=== [{label}] {bundle.upper()} ({datetime.now().strftime('%Y-%m-%d %H:%M')}) ===")

    # Sync market data from Bloomberg using the single canonical path.
    # This ensures dedup, classification, and report caching all happen correctly.
    print("\n  Syncing market data from Bloomberg...")
    try:
        from webapp.database import init_db, SessionLocal as _SyncSL
        from webapp.services.market_sync import sync_market_data

        init_db()
        _sync_session = _SyncSL()
        try:
            result = sync_market_data(_sync_session)
            m_count = result.get("master_rows", 0)
            print(f"  Market sync: {m_count} funds synced")
        finally:
            _sync_session.close()
    except Exception as e:
        print(f"  Market sync failed (non-fatal): {e}")
        import traceback; traceback.print_exc()

    # Per-report duplicate guards are inside do_daily/do_weekly.
    # Daily: blocks same-day resend per report.
    # Weekly: blocks same-week resend per report.
    # Use --force to override all guards.

    if bundle == "daily":
        do_daily(preview)
    elif bundle == "weekly":
        do_weekly(preview)
    elif bundle == "market_share":
        do_market_share(preview)
    else:  # "all"
        do_daily(preview)
        do_weekly(preview)

    print("\nDone.")


if __name__ == "__main__":
    main()
