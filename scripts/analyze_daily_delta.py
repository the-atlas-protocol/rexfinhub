"""Explain what changed since the last run — launches and closures.

Diffs the two most recent `mkt_daily_snapshot` dates to find funds that went
PENDING/other -> ACTV (launches) and ACTV -> LIQU/DLST (closures), attributes each to
its fund name + whether it's a REX product, and renders a one-line-per-event summary for
the "reports ready" message.

Why this exists: a launch is when a headline number is *supposed* to move. The contract
gate (preflight audit_contract_numbers) goes red on any change to a REX number — this
script supplies the WHY so the held report explains itself:

  - REX launch/closure  -> changed a REX contract number -> gate red -> you confirm by
    bumping the number's `expected` in config/contracts/report_numbers.yaml ("your yes").
  - Competitor launch    -> touches no REX number -> no gate trip -> just reported here.

Pure core (`compute_delta`) is unit-tested offline; `analyze` reads the live snapshots.
Dependency-light (stdlib only).
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"

_LIVE = "ACTV"
_CLOSED = {"LIQU", "DLST"}


def compute_delta(today: list[dict], prev: dict[str, dict]) -> dict:
    """Pure diff. `today` = list of {ticker, fund_name, market_status, is_rex};
    `prev` = {ticker: {market_status, ...}}. Returns {launches: [...], closures: [...]},
    each event {ticker, fund_name, is_rex}."""
    launches, closures = [], []
    for row in today:
        t = row.get("ticker")
        now = row.get("market_status")
        was = (prev.get(t) or {}).get("market_status")
        if was is None or was == now:
            continue
        event = {"ticker": t, "fund_name": row.get("fund_name", ""),
                 "is_rex": bool(row.get("is_rex"))}
        if now == _LIVE and was != _LIVE:
            launches.append(event)
        elif now in _CLOSED and was not in _CLOSED:
            closures.append(event)
    return {"launches": launches, "closures": closures}


def _read_two_latest_snapshots(db_path: Path):
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        cur.execute("SELECT DISTINCT snapshot_date FROM mkt_daily_snapshot "
                    "ORDER BY snapshot_date DESC LIMIT 2")
        dates = [r[0] for r in cur.fetchall()]
        if len(dates) < 2:
            return None, None, dates
        cur_date, prev_date = dates[0], dates[1]
        cols = "ticker, fund_name, market_status, COALESCE(is_rex,0)"
        cur.execute(f"SELECT {cols} FROM mkt_daily_snapshot WHERE snapshot_date=?", (cur_date,))
        today = [{"ticker": r[0], "fund_name": r[1], "market_status": r[2], "is_rex": r[3]}
                 for r in cur.fetchall()]
        cur.execute(f"SELECT {cols} FROM mkt_daily_snapshot WHERE snapshot_date=?", (prev_date,))
        prev = {r[0]: {"market_status": r[2]} for r in cur.fetchall()}
        return today, prev, dates
    finally:
        con.close()


def analyze(db_path: Path | str = DB_PATH) -> dict:
    """Read the two latest snapshots and compute the delta. Returns
    {available, dates, launches, closures} — available=False when <2 snapshots exist."""
    today, prev, dates = _read_two_latest_snapshots(Path(db_path))
    if today is None:
        return {"available": False, "dates": dates, "launches": [], "closures": []}
    d = compute_delta(today, prev)
    return {"available": True, "dates": dates, **d}


def format_delta(delta: dict) -> str:
    """One human line per event, REX flagged for confirmation. '' when nothing changed."""
    if not delta.get("available"):
        return ""
    lines = []

    def _line(e, verb):
        tag = "  [REX — confirm]" if e["is_rex"] else ""
        nm = f" ({e['fund_name']})" if e.get("fund_name") else ""
        return f"  - {e['ticker']}{nm} {verb}{tag}"

    for e in delta.get("launches", []):
        lines.append(_line(e, "launched (went live)"))
    for e in delta.get("closures", []):
        lines.append(_line(e, "closed"))
    if not lines:
        return ""
    rex = any(e["is_rex"] for e in delta.get("launches", []) + delta.get("closures", []))
    head = "LAUNCHES & CLOSURES since last run:"
    if rex:
        head += ("\n(A REX change moved a tracked number — the gate is held until you confirm "
                 "by updating that number's `expected` in config/contracts/report_numbers.yaml.)")
    return head + "\n" + "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Explain launches/closures since the last run")
    ap.add_argument("--db", default=str(DB_PATH))
    args = ap.parse_args()
    delta = analyze(args.db)
    if not delta["available"]:
        print(f"(need 2 snapshots to diff; have {delta['dates']})")
        return 0
    txt = format_delta(delta)
    print(txt or f"no launches/closures between {delta['dates'][1]} and {delta['dates'][0]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
