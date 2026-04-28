"""Morning classification sweep — catches gaps the morning after launches,
not the night of the send.

Runs the same audit_classification check that preflight uses, but on its own
09:00 ET schedule and with a more focused summary email (gaps + LLM-proposed
fixes only — no full preflight overhead).

Why separate from preflight: by the time preflight runs at 18:30 the same day,
Ryu has been working with stale classifications all day. Surfacing gaps at
09:00 lets him fix them mid-morning rather than scrambling at 18:30.

Usage (dry-run by default — no email sent unless --post-summary is passed):

    python scripts/classification_sweep.py
    python scripts/classification_sweep.py --post-summary

Exit codes:
    0   no gaps detected
    1   gaps found (still posts summary if --post-summary)
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.preflight_check import (
    audit_classification, _now_et, _status_badge,
)


def build_summary_html(audit: dict) -> str:
    gaps = audit.get("gaps", [])
    by_tier: dict[str, list[dict]] = {}
    for g in gaps:
        by_tier.setdefault(g.get("tier", "?"), []).append(g)

    sections = ""
    for tier, items in by_tier.items():
        rows = "".join(
            f'<tr><td style="padding:6px 12px;border-bottom:1px solid #ecf0f1;">'
            f'<code>{g["ticker"]}</code></td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #ecf0f1;font-size:12px;">{g.get("name","")}</td>'
            f'<td style="padding:6px 12px;border-bottom:1px solid #ecf0f1;font-size:11px;color:#7f8c8d;">{g.get("issuer","") or g.get("inception","")}</td></tr>'
            for g in items[:30]
        )
        sections += (
            f'<h3 style="margin:20px 0 8px;">{tier} ({len(items)})</h3>'
            f'<table style="width:100%;border-collapse:collapse;border:1px solid #dee2e6;">'
            f'<tr style="background:#1a1a2e;color:white;">'
            f'<th style="padding:8px 12px;text-align:left;">Ticker</th>'
            f'<th style="padding:8px 12px;text-align:left;">Fund Name</th>'
            f'<th style="padding:8px 12px;text-align:left;">Issuer / Inception</th></tr>'
            f'{rows}</table>'
        )

    if not gaps:
        sections = '<p style="color:#27ae60;font-weight:600;">No classification gaps detected. All ACTV funds tagged.</p>'

    overall = "pass" if not gaps else ("warn" if len(gaps) <= 5 else "fail")
    fix_hint = ""
    if gaps:
        fix_hint = (
            '<div style="margin:16px 0;padding:12px;background:#f4f5f6;border-radius:6px;font-size:12px;color:#566573;">'
            '<strong>To fix:</strong> add ticker rows to <code>config/rules/fund_mapping.csv</code> '
            '(<code>ticker,etp_category,is_primary,source</code>); for autocallables also add to '
            '<code>config/rules/attributes_CC.csv</code>; for issuers showing as NULL, add a '
            '<code>(category, raw_trust, brand)</code> row to <code>config/rules/issuer_mapping.csv</code>. '
            'Then run <code>python scripts/run_market_pipeline.py</code>.'
            '</div>'
        )

    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"></head>
<body style="font-family:-apple-system,sans-serif;color:#1a1a2e;padding:20px;max-width:720px;">
<h2 style="margin:0 0 8px;">REX Classification Sweep &mdash; {_now_et()}</h2>
<p style="font-size:14px;color:#566573;">Status: {_status_badge(overall)} &mdash; {audit["detail"]}</p>
{fix_hint}
{sections}
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description="Morning classification gap sweep")
    ap.add_argument("--post-summary", action="store_true",
                    help="Email findings to relasmar via send_critical_alert. "
                         "Without this flag, summary HTML is written to outputs/ only.")
    args = ap.parse_args()

    print(f"=== classification_sweep.py @ {_now_et()} ===\n")

    audit = audit_classification(db=None)
    print(f"Status: {audit['status']}")
    print(f"Detail: {audit['detail']}")
    n_gaps = len(audit.get("gaps", []))
    print(f"Gaps:   {n_gaps}\n")

    summary_html = build_summary_html(audit)
    out_html = PROJECT_ROOT / "outputs" / "classification_sweep_summary.html"
    out_html.parent.mkdir(parents=True, exist_ok=True)
    out_html.write_text(summary_html, encoding="utf-8")
    print(f"Summary HTML: {out_html} ({len(summary_html):,} chars)")

    if args.post_summary and n_gaps > 0:
        print("\nPosting summary via send_critical_alert ...")
        try:
            from etp_tracker.email_alerts import send_critical_alert
            ok = send_critical_alert(
                subject=f"REX Classification Sweep — {n_gaps} gap(s) — {date.today().isoformat()}",
                message=summary_html,
            )
            print(f"  {'SENT' if ok else 'FAILED'}")
        except Exception as e:
            print(f"  ERROR: {type(e).__name__}: {e}")
    elif args.post_summary:
        print("\nNo gaps — skipping summary email (no news is good news).")
    else:
        print(f"\nDRY-RUN — no email sent. Open in browser: {out_html}")

    return 0 if n_gaps == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
