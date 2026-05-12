"""Test-send wrapper — build a single report and send to a single test recipient.

Uses bypass_gate=True so the .send_enabled gate stays locked. Does NOT write
to .send_log.json (so test sends don't block tomorrow's automated dedup check).

Usage:
    python scripts/test_send.py daily      ryuogawaelasmar@gmail.com
    python scripts/test_send.py weekly     ryuogawaelasmar@gmail.com
    python scripts/test_send.py li         ryuogawaelasmar@gmail.com
    python scripts/test_send.py income     ryuogawaelasmar@gmail.com
    python scripts/test_send.py flow       ryuogawaelasmar@gmail.com
    python scripts/test_send.py autocall   ryuogawaelasmar@gmail.com
    python scripts/test_send.py stock_recs ryuogawaelasmar@gmail.com

Subject lines are prefixed with [TEST] so the recipient knows it's a preview.
"""
import sys
from datetime import datetime, date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

VALID_BUNDLES = ("daily", "weekly", "li", "income", "flow", "autocall", "stock_recs")


def _build(bundle: str, db) -> tuple[str, str]:
    """Return (subject_base, html) for the requested bundle."""
    if bundle == "daily":
        from scripts.send_email import _build_daily_filing
        return ("REX Daily ETP Report", _build_daily_filing(db))
    if bundle == "weekly":
        from etp_tracker.weekly_digest import build_weekly_digest_html
        return ("REX Weekly ETP Report", build_weekly_digest_html(db, ""))
    if bundle == "li":
        from scripts.send_email import _build_li
        return ("REX ETP Leverage & Inverse Report", _build_li(db))
    if bundle == "income":
        from scripts.send_email import _build_income
        return ("REX ETP Income Report", _build_income(db))
    if bundle == "flow":
        from scripts.send_email import _build_flow
        return ("REX ETP Flow Report", _build_flow(db))
    if bundle == "autocall":
        from scripts.send_email import _build_autocall
        return ("Autocallable ETF Weekly Update", _build_autocall(db))
    if bundle == "stock_recs":
        html_path = PROJECT_ROOT / "reports" / f"li_weekly_v2_{date.today().isoformat()}.html"
        if not html_path.exists():
            from screener.li_engine.analysis.weekly_v2_report import main as build_main
            build_main()
        return ("Stock Recommendations of the Week", html_path.read_text(encoding="utf-8"))
    raise ValueError(f"Unknown bundle: {bundle}")


def main():
    if len(sys.argv) != 3 or sys.argv[1].lower() not in VALID_BUNDLES:
        print("Usage: python scripts/test_send.py <bundle> <email>")
        print(f"Bundles: {' | '.join(VALID_BUNDLES)}")
        sys.exit(1)

    bundle = sys.argv[1].lower()
    to_email = sys.argv[2]

    print(f"=== TEST SEND: {bundle.upper()} -> {to_email} ===")

    from webapp.database import init_db, SessionLocal
    init_db()
    db = SessionLocal()
    try:
        date_str = datetime.now().strftime("%m/%d/%Y")
        print(f"  Building {bundle}...")
        title_base, html = _build(bundle, db)
        print(f"  Built {len(html):,} chars")

        subject = f"[TEST] {title_base}: {date_str}"

        from etp_tracker.email_alerts import _send_html_digest
        print(f"  Sending via Graph API to {to_email} (bypass_gate=True)...")
        ok = _send_html_digest(
            html_body=html,
            recipients=[to_email],
            edition="daily",
            subject_override=subject,
            bypass_gate=True,
        )
        if ok:
            print(f"\nSENT: {subject}")
            print(f"To:   {to_email}")
            return 0
        else:
            print("\nFAILED — check Graph API credentials")
            return 2
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
