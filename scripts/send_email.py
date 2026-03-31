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


def _send_via_smtp(html: str, subject: str):
    """Send HTML email to all configured recipients."""
    from etp_tracker.email_alerts import _load_recipients, _load_private_recipients, _send_html_digest
    recipients = _load_recipients()
    private = _load_private_recipients()
    if not recipients and not private:
        print(f"  SKIP {subject} (no recipients)")
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


def _record_send(bundle: str):
    """Record that a bundle was sent today."""
    import json
    log = _load_send_log()
    today = datetime.now().strftime("%Y-%m-%d")
    if today not in log:
        log[today] = {}
    log[today][bundle] = datetime.now().strftime("%H:%M")
    SEND_LOG.parent.mkdir(parents=True, exist_ok=True)
    SEND_LOG.write_text(json.dumps(log, indent=2), encoding="utf-8")


def _already_sent_today(bundle: str) -> str | None:
    """Check if a bundle was already sent today. Returns time if yes, None if no."""
    log = _load_send_log()
    today = datetime.now().strftime("%Y-%m-%d")
    return log.get(today, {}).get(bundle)


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


def _data_date(db) -> str:
    """Get data date (MM/DD/YYYY) from report cache for email subjects."""
    from webapp.services.report_data import get_li_report
    data = get_li_report(db)
    return data.get("data_as_of_short", datetime.now().strftime("%m/%d/%Y"))


DAILY_REPORTS = [
    ("REX Daily ETP Report", "daily_filing", _build_daily_filing),
]

WEEKLY_REPORTS = [
    ("REX Weekly ETP Report", "weekly_report", None),  # special handler
    ("REX ETP Leverage & Inverse Report", "li_report", _build_li),
    ("REX ETP Income Report", "income_report", _build_income),
    ("REX ETP Flow Report", "flow_report", _build_flow),
    ("REX Autocallable ETF Report", "autocall_report", _build_autocall),
]


def do_daily(preview: bool):
    db = _get_db()
    try:
        date = _data_date(db)
        for base_title, filename, builder in DAILY_REPORTS:
            subject = f"{base_title}: {date}"
            print(f"\n  Building {subject}...")
            html = builder(db)
            if preview:
                _save_and_open(html, filename)
            else:
                ok = _send_via_smtp(html, subject)
                print(f"  {'Sent' if ok else 'FAILED'}: {subject}")
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Weekly bundle: Weekly Report + L&I + Income + Flow
# ---------------------------------------------------------------------------

def do_weekly(preview: bool):
    from etp_tracker.weekly_digest import build_weekly_digest_html, send_weekly_digest

    db = _get_db()
    try:
        date = _data_date(db)

        # Weekly report
        weekly_subject = f"REX Weekly ETP Report: {date}"
        print(f"\n  Building {weekly_subject}...")
        if preview:
            html = build_weekly_digest_html(db, DASHBOARD_URL)
            _save_and_open(html, "weekly_report")
        else:
            ok = send_weekly_digest(db, DASHBOARD_URL)
            print(f"  {'Sent' if ok else 'FAILED'}: {weekly_subject}")

        # Market reports (L&I, Income, Flow)
        for base_title, filename, builder in WEEKLY_REPORTS:
            if builder is None:
                continue  # weekly_report handled above
            subject = f"{base_title}: {date}"
            print(f"\n  Building {subject}...")
            html = builder(db)
            if preview:
                _save_and_open(html, filename)
            else:
                ok = _send_via_smtp(html, subject)
                print(f"  {'Sent' if ok else 'FAILED'}: {subject}")
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

    # Always sync market data from Bloomberg before generating reports
    # so DB reflects the latest inception dates, market status, AUM, etc.
    # Uses the full market pipeline (auto-classify + queues + DB + reports).
    print("\n  Syncing market data from Bloomberg...")
    try:
        from market.config import DATA_FILE as _MKT_DATA_FILE, RULES_DIR as _MKT_RULES_DIR
        from market.rules import load_all_rules
        from market.ingest import read_input
        from market.derive import derive_dim_fund_category
        from market.transform import run_transform
        from market.auto_classify import classify_all, classify_to_dataframe
        from market.queues import build_queues_report
        from webapp.database import init_db, SessionLocal as _SyncSL
        from market.db_writer import (
            create_pipeline_run, finish_pipeline_run,
            write_master_data, write_time_series, write_stock_data,
            write_classifications, write_market_statuses,
        )
        from market.rules import sync_rules_to_db

        init_db()
        rules = load_all_rules(_MKT_RULES_DIR)
        data = read_input(_MKT_DATA_FILE)
        etp = data["etp_combined"]
        fm = rules["fund_mapping"]
        im = rules["issuer_mapping"]
        dim = derive_dim_fund_category(
            fund_mapping=fm, issuer_mapping=im,
            rex_funds=rules["rex_funds"],
            category_attributes=rules["category_attributes"],
            etp_combined=etp,
        )
        result = run_transform(etp, rules, dim)
        master = result["master"]
        ts = result["ts"]

        # Auto-classify + merge
        classifications = classify_all(etp)
        class_df = classify_to_dataframe(etp)
        class_merge = class_df[["ticker", "strategy", "confidence", "underlier_type"]].copy()
        class_merge = class_merge.rename(columns={"confidence": "strategy_confidence"})
        for col in ["strategy", "strategy_confidence", "underlier_type"]:
            if col in master.columns:
                master = master.drop(columns=[col])
        master = master.merge(class_merge, on="ticker", how="left")

        # Queues report
        queues = build_queues_report(etp, fm, im)

        # Write to DB
        _sync_session = _SyncSL()
        try:
            run_id = create_pipeline_run(_sync_session, str(_MKT_DATA_FILE))
            sync_rules_to_db(rules, _sync_session)
            m_count = write_master_data(_sync_session, master, run_id)
            ts_count = write_time_series(_sync_session, ts, run_id)
            s_count = write_stock_data(_sync_session, data["stock_data"], run_id)
            write_classifications(_sync_session, classifications, run_id)
            mkt_status_rule = rules.get("market_status", None)
            if mkt_status_rule is not None and not mkt_status_rule.empty:
                write_market_statuses(_sync_session, mkt_status_rule)
            finish_pipeline_run(
                _sync_session, run_id, status="completed",
                etp_rows_read=len(etp), master_rows_written=m_count,
                ts_rows_written=ts_count, stock_rows_written=s_count,
                unmapped_count=queues["summary"]["unmapped_count"],
                new_issuer_count=queues["summary"]["new_issuer_count"],
            )
            _sync_session.commit()

            # Cache reports
            from webapp.services.market_sync import _compute_and_cache_reports
            from webapp.models import MktReportCache
            from sqlalchemy import delete
            _sync_session.execute(delete(MktReportCache))
            _sync_session.flush()
            cached_keys = _compute_and_cache_reports(_sync_session, master, run_id)
            _sync_session.commit()
            print(f"  Market pipeline: {m_count} funds, {len(classifications)} classified, {queues['summary']['unmapped_count']} unmapped, {len(cached_keys)} reports cached")
        finally:
            _sync_session.close()
    except Exception as e:
        print(f"  Market sync failed (non-fatal): {e}")
        import traceback; traceback.print_exc()

    # Duplicate send guard (preview is always allowed, per-bundle)
    force = "--force" in [a.lower() for a in sys.argv[1:]]

    def _guard_and_run(b: str, fn):
        if not preview:
            sent_at = _already_sent_today(b)
            if sent_at and not force:
                print(f"\n  SKIPPED: {b} already sent today at {sent_at}.")
                return False
        fn(preview)
        if not preview:
            _record_send(b)
        return True

    if bundle == "daily":
        _guard_and_run("daily", do_daily)
    elif bundle == "weekly":
        _guard_and_run("weekly", do_weekly)
    elif bundle == "market_share":
        _guard_and_run("market_share", do_market_share)
    else:  # "all"
        d = _guard_and_run("daily", do_daily)
        w = _guard_and_run("weekly", do_weekly)
        if not preview and not d and not w:
            print("\n  All reports already sent today. Use --force to resend.")

    print("\nDone.")


if __name__ == "__main__":
    main()
