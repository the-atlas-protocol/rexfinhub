"""Heartbeat self-check — the system reports its own silent failures.

The disk-full and missed-scrape incidents were SILENT: a timer or chain step failed
and nothing surfaced until a report came out wrong. This closes that gap (approved
plan, Stage 5 / "literally could not be better"): once a day it asserts that every
expected stage actually ran AND succeeded today, and that the data it feeds on is
fresh. Anything missing or failed → ONE consolidated alert (rate-limited to once per
day so it never becomes clutter).

Sources of truth (no new bookkeeping):
  - data/.pipeline_stages.jsonl   — append-only stage outcomes (run_chain + preflight)
  - data/etp_tracker.db mtime     — DB freshness
  - data/DASHBOARD/bloomberg_daily_file.xlsm mtime — Bloomberg freshness

Usage:
    python scripts/healthcheck.py             # evaluate; exit non-zero if unhealthy
    python scripts/healthcheck.py --alert      # also email one consolidated alert
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, date, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

DATA_DIR = PROJECT_ROOT / "data"
STAGES_LOG = DATA_DIR / ".pipeline_stages.jsonl"
ALERT_MARKER = DATA_DIR / ".healthcheck_alerted"

# The stages that MUST succeed on a normal build day. Keep in sync with run_chain's
# step labels + preflight. A stage absent from today's log == a silent failure.
EXPECTED_STAGES = [
    "bloomberg_pull_sync",
    "post_steps_classify_ai_enrich",
    "build_all_reports",
    "preflight",
    "promote_or_block",
]

# Freshness ceilings (hours).
DB_MAX_AGE_H = 30
BBG_MAX_AGE_H = 30


def _read_stage_records(path: Path = STAGES_LOG) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


def _is_today(ts: str, today: date) -> bool:
    """Tolerant date match on an ISO timestamp (with or without tz)."""
    if not ts:
        return False
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00")).date() == today
    except (ValueError, TypeError):
        return ts[:10] == today.isoformat()


def evaluate_health(records: list[dict], expected: list[str], today: date) -> tuple[bool, list[str]]:
    """PURE — given today's stage records, return (healthy, issues).

    A stage is healthy when it has at least one record TODAY whose outcome is not a
    failure (rc==0 / status in pass/warn / ok flag). Missing == silent failure.
    Pure so the heartbeat logic is unit-tested without files."""
    issues: list[str] = []
    todays = [r for r in records if _is_today(r.get("timestamp", ""), today)]
    by_stage: dict[str, list[dict]] = {}
    for r in todays:
        by_stage.setdefault(r.get("stage", "?"), []).append(r)

    for stage in expected:
        recs = by_stage.get(stage)
        if not recs:
            issues.append(f"{stage}: no successful run recorded today (silent failure?)")
            continue
        if not any(_stage_ok(r) for r in recs):
            last = recs[-1]
            issues.append(f"{stage}: ran but FAILED today "
                          f"(rc={last.get('rc')}, status={last.get('overall_status') or last.get('status')})")
    return (not issues), issues


def _stage_ok(rec: dict) -> bool:
    """A stage record counts as success on any of the conventions the writers use."""
    if rec.get("rc") is not None:
        return rec.get("rc") == 0
    status = rec.get("overall_status") or rec.get("status")
    if status is not None:
        return str(status).lower() in ("pass", "warn", "ok", "green", "success")
    return bool(rec.get("ok", False))


def _freshness_issues(now: datetime) -> list[str]:
    issues: list[str] = []
    for label, path, ceil_h in (
        ("etp_tracker.db", DATA_DIR / "etp_tracker.db", DB_MAX_AGE_H),
        ("bloomberg_daily_file.xlsm", DATA_DIR / "DASHBOARD" / "bloomberg_daily_file.xlsm", BBG_MAX_AGE_H),
    ):
        if not path.exists():
            issues.append(f"{label}: MISSING")
            continue
        age_h = (now.timestamp() - path.stat().st_mtime) / 3600
        if age_h > ceil_h:
            issues.append(f"{label}: stale ({age_h:.0f}h > {ceil_h}h)")
    return issues


def _already_alerted(today: date) -> bool:
    try:
        return ALERT_MARKER.exists() and ALERT_MARKER.read_text(encoding="utf-8").strip() == today.isoformat()
    except OSError:
        return False


def _mark_alerted(today: date) -> None:
    try:
        ALERT_MARKER.parent.mkdir(parents=True, exist_ok=True)
        ALERT_MARKER.write_text(today.isoformat(), encoding="utf-8")
    except OSError:
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--alert", action="store_true",
                    help="Email one consolidated alert if unhealthy (rate-limited to once/day).")
    args = ap.parse_args()

    today = date.today()
    now = datetime.now()
    healthy, issues = evaluate_health(_read_stage_records(), EXPECTED_STAGES, today)
    issues += _freshness_issues(now)
    healthy = healthy and not _freshness_issues(now)

    if healthy:
        print(f"healthcheck OK — all {len(EXPECTED_STAGES)} stages succeeded today; data fresh")
        return 0

    print(f"healthcheck UNHEALTHY ({len(issues)} issue(s)):")
    for i in issues:
        print(f"  - {i}")

    if args.alert and not _already_alerted(today):
        try:
            from etp_tracker.email_alerts import send_critical_alert
            send_critical_alert(
                subject=f"REX heartbeat — {len(issues)} silent failure(s)",
                message=("The daily heartbeat found stages that did not run/succeed or data that is "
                         "stale:\n  - " + "\n  - ".join(issues)),
                subject_prefix="[HEARTBEAT]")
            _mark_alerted(today)
        except Exception as e:
            print(f"  (alert failed: {e})")
    return 1


if __name__ == "__main__":
    sys.exit(main())
