"""End-of-day pipeline summary — confirms every stage of the daily flow.

Reads the existing pipeline state files:
  - data/.preflight_result.json       — audit outcomes + overall status
  - data/.preflight_decision.json     — GO/HOLD decision + reason
  - data/.gate_state_log.jsonl        — gate open/close history
  - data/.pipeline_stages.jsonl       — stage log (preflight + future stages)
  - data/.send_log.json               — last successful send per bundle

Builds an HTML summary and emails it to relasmar@rexfin.com via
send_critical_alert (bypasses gate). Run after gate-close (~20:00 ET) so
all stages have logged by then.

Override: touch data/.summary_paused to disable.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
ET = ZoneInfo("America/New_York")

PREFLIGHT_RESULT = DATA_DIR / ".preflight_result.json"
DECISION_FILE = DATA_DIR / ".preflight_decision.json"
GATE_LOG = DATA_DIR / ".gate_state_log.jsonl"
STAGES_LOG = DATA_DIR / ".pipeline_stages.jsonl"
SEND_LOG = DATA_DIR / ".send_log.json"
PAUSED_FLAG = DATA_DIR / ".summary_paused"


def _now_et() -> str:
    return datetime.now(ET).strftime("%Y-%m-%dT%H:%M:%S%z")


def _read_json(path: Path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _today_only(entries: list[dict], key: str) -> list[dict]:
    today = date.today().isoformat()
    return [e for e in entries if (e.get(key) or "").startswith(today)]


def _status_badge(status: str) -> str:
    colors = {
        "pass": "#27ae60", "ok": "#27ae60", "sent": "#27ae60", "go": "#27ae60",
        "warn": "#e67e22", "standing_down": "#7f8c8d",
        "fail": "#e74c3c", "failed": "#e74c3c", "error": "#c0392b", "hold": "#e74c3c",
        "missing": "#7f8c8d", "skipped": "#7f8c8d",
    }
    s = (status or "missing").lower()
    color = colors.get(s, "#7f8c8d")
    return (f'<span style="background:{color};color:white;padding:2px 8px;'
            f'border-radius:6px;font-size:11px;font-weight:700;text-transform:uppercase;'
            f'letter-spacing:0.5px;">{s}</span>')


def _row(stage: str, status: str, detail: str, ts: str = "") -> str:
    return (
        f'<tr>'
        f'<td style="padding:8px 12px;border-bottom:1px solid #ecf0f1;font-weight:600;color:#1a1a2e;">{stage}</td>'
        f'<td style="padding:8px 12px;border-bottom:1px solid #ecf0f1;">{_status_badge(status)}</td>'
        f'<td style="padding:8px 12px;border-bottom:1px solid #ecf0f1;color:#566573;font-family:Consolas,monospace;font-size:11px;">{ts}</td>'
        f'<td style="padding:8px 12px;border-bottom:1px solid #ecf0f1;color:#566573;font-size:12px;">{detail}</td>'
        f'</tr>'
    )


def build_summary() -> str:
    today_iso = date.today().isoformat()
    rows = []

    # --- Bloomberg chain (17:15) ---
    # No explicit log; infer from preflight bloomberg audit result.
    pf = _read_json(PREFLIGHT_RESULT) or {}
    bbg_audit = (pf.get("audits") or {}).get("audit_bloomberg", {})
    rows.append(_row(
        "1. Bloomberg sync (17:15 ET)",
        bbg_audit.get("status") or "missing",
        bbg_audit.get("detail") or "no audit data",
        pf.get("timestamp", "")[:19] if pf else "",
    ))

    # --- Classification (part of bloomberg chain) ---
    cls_audit = (pf.get("audits") or {}).get("audit_classification", {})
    rows.append(_row(
        "2. Classification sweep",
        cls_audit.get("status") or "missing",
        cls_audit.get("detail") or "no audit data",
        pf.get("timestamp", "")[:19] if pf else "",
    ))

    # --- Preflight (18:30) ---
    pf_status = pf.get("overall_status", "missing")
    rows.append(_row(
        "3. Preflight audit (18:30 ET)",
        pf_status,
        f"token={pf.get('token','?')[:8]}... · 8 audits run · overall={pf_status}",
        pf.get("timestamp", "")[:19] if pf else "",
    ))

    # --- Decision (auto-GO or manual) ---
    decision = _read_json(DECISION_FILE)
    if decision:
        decision_status = (decision.get("action") or "").lower()
        decided_by = decision.get("decided_by", "manual")
        rows.append(_row(
            "4. GO/HOLD decision",
            decision_status,
            f"by={decided_by} · reason={(decision.get('reason') or '')[:80]}",
            decision.get("decided_at", "")[:19],
        ))
    else:
        rows.append(_row(
            "4. GO/HOLD decision",
            "missing",
            "No decision file written. Send would stand down.",
            "",
        ))

    # --- Gate transitions today ---
    gate_today = _today_only(_read_jsonl(GATE_LOG), "timestamp")
    if gate_today:
        opens = sum(1 for e in gate_today if e.get("action") == "open")
        closes = sum(1 for e in gate_today if e.get("action") == "close")
        stood_down = sum(1 for e in gate_today if "stood_down" in (e.get("note") or "").lower()
                         or "standing_down" in (e.get("state") or "").lower())
        last_ts = gate_today[-1].get("timestamp", "")[:19]
        rows.append(_row(
            "5. Gate transitions",
            "ok" if opens else "warn",
            f"open={opens} close={closes} stood_down={stood_down} (last: {last_ts})",
            last_ts,
        ))
    else:
        rows.append(_row(
            "5. Gate transitions", "missing", "No gate activity today", "",
        ))

    # --- Send results ---
    send_log = _read_json(SEND_LOG) or {}
    today_sends = send_log.get(today_iso, {}) if isinstance(send_log, dict) else {}
    if today_sends:
        sent_list = ", ".join(f"{k}@{v}" for k, v in today_sends.items())
        rows.append(_row(
            "6. Email sends",
            "sent",
            f"{len(today_sends)} report(s): {sent_list}",
            "",
        ))
    else:
        rows.append(_row(
            "6. Email sends",
            "missing",
            "No sends recorded for today — pipeline either stood down or send_log not updated",
            "",
        ))

    # --- Stage log entries today ---
    stages_today = _today_only(_read_jsonl(STAGES_LOG), "timestamp")
    n_stages = len(stages_today)

    # --- HTML envelope ---
    overall_color = "#27ae60" if pf_status == "pass" else ("#e67e22" if pf_status == "warn" else "#e74c3c")
    overall_label = pf_status.upper() if pf_status else "MISSING"

    body = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>REX Pipeline Summary — {today_iso}</title></head>
<body style="margin:0;padding:24px;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1a1a2e;">
<table width="720" cellpadding="0" cellspacing="0" border="0" style="margin:0 auto;background:#ffffff;border-radius:8px;box-shadow:0 2px 12px rgba(0,0,0,0.06);">
<tr><td style="background:#1a1a2e;padding:22px 28px;color:white;">
  <div style="font-size:20px;font-weight:700;letter-spacing:-0.5px;">REX Daily Pipeline Summary</div>
  <div style="font-size:12px;color:#9bb1cc;margin-top:6px;">{today_iso} · generated {_now_et()}</div>
  <div style="margin-top:14px;">
    <span style="background:{overall_color};color:white;padding:4px 12px;border-radius:6px;font-size:13px;font-weight:700;letter-spacing:0.5px;">OVERALL: {overall_label}</span>
    <span style="color:#9bb1cc;font-size:12px;margin-left:10px;">{n_stages} stage(s) logged today</span>
  </div>
</td></tr>
<tr><td style="padding:20px 28px;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;">
  <tr style="background:#f4f5f6;">
    <th style="text-align:left;padding:10px 12px;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:#566573;">Stage</th>
    <th style="text-align:left;padding:10px 12px;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:#566573;">Status</th>
    <th style="text-align:left;padding:10px 12px;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:#566573;">Time</th>
    <th style="text-align:left;padding:10px 12px;font-size:11px;text-transform:uppercase;letter-spacing:0.5px;color:#566573;">Detail</th>
  </tr>
  {"".join(rows)}
</table>
</td></tr>
<tr><td style="padding:14px 28px;border-top:1px solid #ecf0f1;background:#fafbfc;font-size:11px;color:#566573;">
  Overrides: <code>touch data/.send_paused</code> to disable auto-GO ·
  <code>touch data/.autogo_on_warn</code> for auto-GO on WARN ·
  <code>touch data/.summary_paused</code> to suppress this email.
</td></tr>
</table>
</body></html>"""
    return body


def main():
    if PAUSED_FLAG.exists():
        print(f"PAUSED ({PAUSED_FLAG}) — summary not sent")
        return 0
    html = build_summary()
    out = PROJECT_ROOT / "outputs" / "pipeline_summary.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"Summary HTML written: {out}")

    if os.environ.get("DRY_RUN"):
        print("DRY_RUN — skipping email")
        return 0

    try:
        from etp_tracker.email_alerts import send_critical_alert
        ok = send_critical_alert(
            subject=f"REX Pipeline Summary — {date.today().isoformat()}",
            message=html,
            subject_prefix="[PIPELINE]",
        )
        print(f"  {'SENT' if ok else 'FAILED'}")
        return 0 if ok else 1
    except Exception as e:
        print(f"  ERROR: {type(e).__name__}: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
