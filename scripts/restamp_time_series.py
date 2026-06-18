"""Re-stamp mkt_time_series enrichment from the FINAL master, and assert no drift.

The drift bug: sync writes mkt_time_series.category_display / issuer_display / is_rex /
issuer_group / fund_category_key at INSERT time from the master snapshot — but the
classification post-steps (apply_issuer_brands, apply_classification_sweep, ...) enrich the
master AFTER that, and nothing ever re-stamps the time-series. So the chart/time-series
enrichment silently lags the master (37 rows drifted as of 2026-06-18).

Fix at the source: run this as the LAST enrichment post-step (after the master is final,
before snapshot_daily) so the time-series always reflects the master. Mirrors sync's own
per-ticker derivation exactly — `master.drop_duplicates(["ticker"], keep="first")` — so it
can never disagree with how the rows were first built. `--check` asserts zero drift (for a
preflight/CI guard) without writing.
"""
from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "etp_tracker.db"

# enrichment columns mirrored from master to time-series (order matters for binds).
# issuer_group is a time-series-only derived field (not stored on mkt_master_data), so it
# is intentionally NOT restamped here — it follows from issuer_display.
_COLS = ["category_display", "issuer_display", "is_rex", "fund_category_key"]


def _norm(vals) -> tuple:
    """Canonical comparable form: is_rex -> 0/1, text cols -> '' for NULL."""
    out = []
    for col, v in zip(_COLS, vals):
        if col == "is_rex":
            out.append(1 if v else 0)
        else:
            out.append("" if v is None else str(v))
    return tuple(out)


def _master_enrichment(cur) -> dict[str, tuple]:
    """One NORMALIZED enrichment row per ticker (first wins) — mirrors _insert_time_series."""
    cur.execute(f"SELECT ticker, {', '.join(_COLS)} FROM mkt_master_data")
    enr: dict[str, tuple] = {}
    for row in cur.fetchall():
        ticker = row[0]
        if ticker and ticker not in enr:  # keep="first"
            enr[ticker] = _norm(row[1:])
    return enr


def check_drift(db_path: Path | str = DB_PATH) -> list[dict]:
    """ts tickers whose enrichment differs from the per-ticker master (read-only)."""
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        enr = _master_enrichment(cur)
        cur.execute(f"SELECT DISTINCT ticker, {', '.join(_COLS)} FROM mkt_time_series")
        drift = []
        for row in cur.fetchall():
            ticker, ts_norm = row[0], _norm(row[1:])
            want = enr.get(ticker)
            if want is not None and ts_norm != want:
                drift.append({"ticker": ticker, "ts": ts_norm, "master": want})
        return drift
    finally:
        con.close()


def restamp(db_path: Path | str = DB_PATH) -> int:
    """Overwrite ts enrichment from the per-ticker master, only where it differs.
    Returns rows changed."""
    con = sqlite3.connect(str(db_path))
    try:
        cur = con.cursor()
        enr = _master_enrichment(cur)
        set_clause = ", ".join(f"{c}=?" for c in _COLS)
        where_diff = " OR ".join(f"IFNULL({c},'')<>IFNULL(?,'')" for c in _COLS)
        total = 0
        for ticker, vals in enr.items():  # vals already normalized (is_rex 0/1, '' for NULL)
            cur.execute(
                f"UPDATE mkt_time_series SET {set_clause} WHERE ticker=? AND ({where_diff})",
                (*vals, ticker, *vals),
            )
            total += cur.rowcount
        con.commit()
        return total
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="Re-stamp time-series enrichment from master")
    ap.add_argument("--db", default=str(DB_PATH))
    ap.add_argument("--check", action="store_true", help="report drift, write nothing (exit 1 if any)")
    args = ap.parse_args()

    if args.check:
        drift = check_drift(args.db)
        if drift:
            print(f"DRIFT: {len(drift)} ts tickers disagree with master, e.g. "
                  + ", ".join(d["ticker"] for d in drift[:8]))
            return 1
        print("no time-series enrichment drift")
        return 0

    before = len(check_drift(args.db))
    changed = restamp(args.db)
    after = len(check_drift(args.db))
    print(f"restamp: {changed} ts rows updated; drifted tickers {before} -> {after}")
    return 0 if after == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
