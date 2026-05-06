"""Build the L&I weekly recommender HTML and send via Azure Graph API.

Test-send mode by default: sends only to relasmar@rexfin.com using
bypass_gate=True (no need to flip config/.send_enabled).

Usage:
    python -m screener.li_engine.analysis.send_li_report                  # test → relasmar only
    python -m screener.li_engine.analysis.send_li_report --to email@x     # one-off recipient
"""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    p = argparse.ArgumentParser()
    p.add_argument("--to", default="relasmar@rexfin.com",
                   help="Recipient email (default: relasmar@rexfin.com)")
    p.add_argument("--rebuild", action="store_true",
                   help="Force rebuild of report HTML before sending")
    p.add_argument("--dry-run", action="store_true",
                   help="Build HTML, show subject/recipient, do NOT send")
    args = p.parse_args()

    # 1. Ensure HTML report exists (rebuild if requested or missing)
    html_path = _ROOT / "reports" / f"li_weekly_v2_{date.today().isoformat()}.html"
    if args.rebuild or not html_path.exists():
        log.info("Building report HTML...")
        from screener.li_engine.analysis.weekly_v2_report import main as build_main
        build_main()

    if not html_path.exists():
        log.error("Report HTML not found at %s", html_path)
        return 1

    html = html_path.read_text(encoding="utf-8")
    log.info("Loaded report: %s (%.1f KB)", html_path, len(html) / 1024)

    subject = f"Stock Recommendations of the Week — {date.today().strftime('%B %d, %Y')}"
    recipients = [args.to]

    if args.dry_run:
        log.info("DRY RUN — would send")
        log.info("  Subject: %s", subject)
        log.info("  To: %s", recipients)
        return 0

    # 2. Send via Azure Graph API with bypass_gate (test send mechanism)
    from etp_tracker.email_alerts import _send_html_digest
    log.info("Sending via Azure Graph API to %s...", recipients)
    ok = _send_html_digest(
        html_body=html,
        recipients=recipients,
        edition="daily",
        subject_override=subject,
        bypass_gate=True,
    )

    if ok:
        log.info("Sent successfully.")
        print(f"\nSent: {subject}")
        print(f"To:   {', '.join(recipients)}")
        return 0
    else:
        log.error("Send FAILED — check Graph API credentials")
        return 1


if __name__ == "__main__":
    sys.exit(main())
