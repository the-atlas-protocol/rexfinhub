"""Dump the recipient list for every report into a readable notepad.

Answers "who do we send each report to?" — reads the authoritative email_recipients
DB table (the same source send_all.py uses), grouped per report, with the bundle each
report ships in. Writes outputs/recipients_<date>.txt and prints it.

    python scripts/show_recipients.py
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from webapp.database import init_db, SessionLocal
    from webapp.services.recipients import get_recipients
    from scripts.send_all import REPORTS, BUNDLES

    init_db()
    db = SessionLocal()
    today = date.today().isoformat()
    lines = [
        f"REX FINHUB — REPORT RECIPIENTS  ({today})",
        "Source: email_recipients DB table (what send_all.py actually uses).",
        "Send gate: config/.send_enabled — sends are blocked while false (shadow mode).",
        "=" * 64,
        "",
    ]
    grand = set()
    for key, (_builder, list_type, critical) in REPORTS.items():
        try:
            rec = get_recipients(db, list_type) or []
        except Exception as e:
            rec = []
            lines.append(f"{key.upper()}  (list_type={list_type})")
            lines.append(f"  ⚠ no recipient list configured ({str(e)[:60]}) — not sent via send_all")
            lines.append("")
            continue
        grand.update(rec)
        bundles = [b for b, keys in BUNDLES.items() if key in keys]
        flag = " [CRITICAL/daily-gated]" if critical else ""
        lines.append(f"{key.upper()}  (list_type={list_type}){flag}")
        lines.append(f"  ships in bundle(s): {', '.join(bundles) or '(none)'}")
        lines.append(f"  recipients: {len(rec)}")
        for r in rec:
            lines.append(f"     - {r}")
        if not rec:
            lines.append("     (NONE — this report would send to nobody)")
        lines.append("")
    lines.append("=" * 64)
    lines.append(f"DISTINCT recipients across all reports: {len(grand)}")
    for r in sorted(grand):
        lines.append(f"  - {r}")
    db.close()

    out = ROOT / "outputs" / f"recipients_{today}.txt"
    out.parent.mkdir(parents=True, exist_ok=True)
    text = "\n".join(lines)
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"\n[written to {out}]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
