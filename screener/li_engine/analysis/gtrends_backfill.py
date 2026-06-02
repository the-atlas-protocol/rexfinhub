"""Google Trends 5-year relative-interest backfill for the L&I underlier universe.

Fills the biggest historical-data gap in our ApeWisdom/social methodology:
ApeWisdom is a 24-hour snapshot; Google Trends gives 5+ years of weekly
relative-interest data. Cross-ticker comparability is achieved by anchoring
every batch on TSLA (always position 0 in `kw_list`), so `rel = val / TSLA_val`
for each date is comparable across batches.

Design notes
------------
* pytrends 4.9.x with modern urllib3 BREAKS if you pass `retries=` to
  `TrendReq(...)` — it triggers `method_whitelist` errors deep in urllib3's
  Retry object. We handle retries manually with try/except + sleep instead.
* Batch = [TSLA, t1, t2, t3, t4]. 5 per call is pytrends' max.
* Sleep 12s between batches (10-15s window).
* 429/backoff: exponential 30s -> 60s -> 120s -> 240s, then skip batch.
* Progress is persisted to `gtrends_progress.json` after EVERY batch so
  interruptions are recoverable.
* Output parquet is written at the end from the in-memory panel, but we also
  append per-batch CSVs to `gtrends_batches/` as crash insurance.

Usage
-----
    python -m screener.li_engine.analysis.gtrends_backfill
    python -m screener.li_engine.analysis.gtrends_backfill --pilot   # first 10 tickers only
    python -m screener.li_engine.analysis.gtrends_backfill --sanity  # plot TSLA/NVDA/AAPL only
"""
from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Iterable

import pandas as pd

from webapp.services.ticker_normalize import normalize_underlier as _clean

try:
    from pytrends.request import TrendReq
except ImportError:  # pragma: no cover
    raise RuntimeError("pytrends required: pip install pytrends")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("gtrends_backfill")

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
OUT_DIR = _ROOT / "data" / "analysis"
PARQUET = OUT_DIR / "gtrends_panel.parquet"
PROGRESS = OUT_DIR / "gtrends_progress.json"
BATCH_DIR = OUT_DIR / "gtrends_batches"

ANCHOR = "TSLA"
TIMEFRAME = "today 5-y"
BATCH_SIZE = 5  # TSLA + 4 tickers
SLEEP_BETWEEN = 12.0  # seconds between batches
BACKOFFS = [30, 60, 120, 240]  # seconds on 429/failure

# Bloomberg-only tickers / currency pairs / indices with no Google Trends signal.
# These are skipped entirely — no point wasting rate-limit budget.
_SKIP_PATTERNS = (
    "CURNCY", "CMDTY", "INDEX",
)
_SKIP_EXACT = {
    # Bloomberg index/factor codes
    "M00IMV$T", "M2US000$", "M2USSNQ", "MQUSTRAV", "MVBIZD", "MVMORT",
    "NYFANGT", "PCARSNTR", "PJETSNTR", "RU10GRTR", "RU10VATR", "RU20INTR",
    "SPVXSTIT", "SPXT", "TSPY", "DJUSDIVT", "DJTU", "FDRTR", "CECL", "CEFX",
    "DBODIXX", "METV", "MINERS", "BASKET", "BIGOIL", "BETZ", "TDAQ", "SIA",
    # Currency / crypto pairs
    "EURUSD", "JPYUSD", "USDJPY", "USDXAU", "XAG", "XAU",
    "XBTUSD", "XDGUSD", "XETUSD", "XRPUSD", "XSOUSD",
    # Single-letter / ambiguous
    "B", "U", "FLY", "KEY", "BULL", "CLA", "SOL",
}


def get_universe(db_path: Path = DB) -> list[str]:
    """Pull L&I underliers, dedupe, strip Bloomberg suffixes, drop non-equities."""
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT DISTINCT map_li_underlier FROM mkt_master_data "
            "WHERE primary_category='LI' AND map_li_underlier IS NOT NULL "
            "AND map_li_underlier != ''"
        ).fetchall()
    finally:
        conn.close()

    tickers = {_clean(r[0]) for r in rows if r[0]}
    tickers.discard("")
    tickers = {
        t for t in tickers
        if not any(x in t for x in _SKIP_PATTERNS) and t not in _SKIP_EXACT
    }
    # Ensure anchor is present
    tickers.add(ANCHOR)
    return sorted(tickers)


def load_progress() -> dict:
    if PROGRESS.exists():
        try:
            return json.loads(PROGRESS.read_text())
        except Exception as e:  # pragma: no cover
            log.warning("progress file unreadable, starting fresh: %s", e)
    return {"completed": [], "failed": []}


def save_progress(state: dict) -> None:
    PROGRESS.parent.mkdir(parents=True, exist_ok=True)
    PROGRESS.write_text(json.dumps(state, indent=2))


def _build_client() -> TrendReq:
    """Modern pytrends landmine: pytrends 4.9.2 calls `Retry(method_whitelist=...)`
    whenever `retries > 0 OR backoff_factor > 0`, and urllib3 2.x removed
    `method_whitelist` (renamed to `allowed_methods`). We MUST pass both as 0
    and do all retry/backoff ourselves in `fetch_batch`.
    """
    return TrendReq(
        hl="en-US",
        tz=300,
        timeout=(10, 30),
        retries=0,
        backoff_factor=0,
    )


def fetch_batch(
    pytrends: TrendReq,
    batch: list[str],
    timeframe: str = TIMEFRAME,
) -> pd.DataFrame:
    """Fetch one 5-ticker batch. Returns wide DataFrame indexed by date.

    Manual retry with exponential backoff on 429 / network errors.
    """
    assert batch[0] == ANCHOR, f"batch must start with anchor {ANCHOR}, got {batch[0]}"
    for attempt, wait in enumerate([0] + BACKOFFS, start=0):
        if wait:
            log.warning("backoff %ss before retry %d for %s", wait, attempt, batch)
            time.sleep(wait)
        try:
            pytrends.build_payload(kw_list=batch, timeframe=timeframe, geo="")
            df = pytrends.interest_over_time()
            if df is None or df.empty:
                log.warning("empty frame for batch %s (attempt %d)", batch, attempt)
                continue
            if "isPartial" in df.columns:
                df = df.drop(columns=["isPartial"])
            return df
        except Exception as e:  # pytrends wraps requests errors in its own types
            msg = str(e)
            log.warning("batch %s attempt %d failed: %s", batch, attempt, msg[:200])
            # don't retry on obvious permanent failures
            if "400" in msg and "bad request" in msg.lower():
                break
    return pd.DataFrame()


def melt_batch(wide: pd.DataFrame, batch: list[str]) -> pd.DataFrame:
    """Wide (date index, ticker cols) -> long (date, ticker, raw, rel_vs_TSLA)."""
    if wide.empty:
        return pd.DataFrame(columns=["date", "ticker", "raw_interest", "relative_interest_vs_TSLA"])
    anchor_col = wide[ANCHOR].astype(float)
    rows = []
    for ticker in batch:
        if ticker not in wide.columns:
            continue
        ser = wide[ticker].astype(float)
        # avoid /0: if TSLA=0 for some week, rel is NaN
        rel = ser / anchor_col.where(anchor_col > 0)
        for dt, raw_val, rel_val in zip(wide.index, ser, rel):
            rows.append({
                "date": pd.Timestamp(dt).normalize(),
                "ticker": ticker,
                "raw_interest": float(raw_val) if pd.notna(raw_val) else None,
                "relative_interest_vs_TSLA": float(rel_val) if pd.notna(rel_val) else None,
            })
    return pd.DataFrame(rows)


def make_batches(tickers: list[str], completed: set[str]) -> list[list[str]]:
    """Chunk non-anchor tickers into groups of 4, prepend anchor each batch."""
    todo = [t for t in tickers if t != ANCHOR and t not in completed]
    batches: list[list[str]] = []
    for i in range(0, len(todo), BATCH_SIZE - 1):  # 4 per batch + anchor
        chunk = todo[i:i + BATCH_SIZE - 1]
        batches.append([ANCHOR] + chunk)
    return batches


def run_backfill(
    pilot: bool = False,
    sleep_between: float = SLEEP_BETWEEN,
) -> Path:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    BATCH_DIR.mkdir(parents=True, exist_ok=True)

    universe = get_universe()
    log.info("universe: %d tickers (anchor=%s)", len(universe), ANCHOR)

    state = load_progress()
    completed = set(state.get("completed", []))
    failed = set(state.get("failed", []))
    log.info("resume state: %d completed, %d failed", len(completed), len(failed))

    batches = make_batches(universe, completed)
    if pilot:
        batches = batches[:3]  # ~12 tickers
        log.info("PILOT mode: %d batches", len(batches))

    if not batches:
        log.info("nothing to do — all tickers in progress file")
        return PARQUET

    pytrends = _build_client()
    all_long: list[pd.DataFrame] = []

    # Load any existing parquet so --resume doesn't start empty
    if PARQUET.exists():
        try:
            all_long.append(pd.read_parquet(PARQUET))
            log.info("loaded existing panel: %d rows", len(all_long[0]))
        except Exception as e:  # pragma: no cover
            log.warning("could not reload parquet: %s", e)

    for i, batch in enumerate(batches, start=1):
        log.info("[%d/%d] fetching %s", i, len(batches), batch)
        wide = fetch_batch(pytrends, batch)
        if wide.empty:
            log.error("batch FAILED after backoffs: %s", batch)
            for t in batch:
                if t != ANCHOR:
                    failed.add(t)
        else:
            long_df = melt_batch(wide, batch)
            all_long.append(long_df)
            # per-batch CSV as insurance
            stamp = time.strftime("%Y%m%d_%H%M%S")
            long_df.to_csv(BATCH_DIR / f"batch_{i:03d}_{stamp}.csv", index=False)
            for t in batch:
                if t != ANCHOR:
                    completed.add(t)
                    failed.discard(t)
            log.info("  -> %d rows, tickers: %s",
                     len(long_df), [c for c in wide.columns if c != ANCHOR])

        state["completed"] = sorted(completed)
        state["failed"] = sorted(failed)
        save_progress(state)

        # write incremental parquet
        if all_long:
            try:
                combined = pd.concat(all_long, ignore_index=True)
                combined = combined.drop_duplicates(subset=["date", "ticker"], keep="last")
                combined.to_parquet(PARQUET, index=False)
            except Exception as e:  # pragma: no cover
                log.warning("parquet write failed: %s", e)

        if i < len(batches):
            time.sleep(sleep_between)

    log.info("DONE. completed=%d failed=%d",
             len(completed), len(failed))
    log.info("output: %s", PARQUET)
    return PARQUET


def sanity_check(tickers: Iterable[str] = ("TSLA", "NVDA", "AAPL")) -> dict:
    """Quick numeric sanity check — no plot library required.

    Returns per-ticker: min/max/mean raw interest, date of peak, peak value,
    and the 2020-2021 mean (bull run window).
    """
    if not PARQUET.exists():
        raise FileNotFoundError(f"no panel at {PARQUET}")
    df = pd.read_parquet(PARQUET)
    out = {}
    for t in tickers:
        sub = df[df["ticker"] == t].dropna(subset=["raw_interest"])
        if sub.empty:
            out[t] = {"status": "no data"}
            continue
        peak_row = sub.loc[sub["raw_interest"].idxmax()]
        bull = sub[(sub["date"] >= "2020-01-01") & (sub["date"] <= "2021-12-31")]
        out[t] = {
            "rows": len(sub),
            "date_min": str(sub["date"].min().date()),
            "date_max": str(sub["date"].max().date()),
            "raw_mean": round(float(sub["raw_interest"].mean()), 2),
            "raw_peak": round(float(peak_row["raw_interest"]), 2),
            "peak_date": str(peak_row["date"].date()),
            "bull_2020_21_mean": round(float(bull["raw_interest"].mean()), 2) if not bull.empty else None,
        }
    return out


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--pilot", action="store_true", help="run first 3 batches only (~12 tickers)")
    p.add_argument("--sanity", action="store_true", help="just run sanity check on existing parquet")
    p.add_argument("--sleep", type=float, default=SLEEP_BETWEEN)
    args = p.parse_args(argv)

    if args.sanity:
        result = sanity_check()
        print(json.dumps(result, indent=2))
        return 0

    run_backfill(pilot=args.pilot, sleep_between=args.sleep)

    # Post-run sanity
    try:
        result = sanity_check()
        log.info("SANITY CHECK:\n%s", json.dumps(result, indent=2))
    except Exception as e:  # pragma: no cover
        log.warning("sanity check failed: %s", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
