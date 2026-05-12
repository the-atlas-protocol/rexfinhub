"""Macro overlay — daily snapshot of risk regime, credit, crypto, Fed cycle, sector leadership.

Wave F1 (2026-05-11). Feeds the Stock Recs B-renderer "Macro Backdrop" header.

Free-tier sources only:
  - yfinance: ^VIX, DX-Y.NYB (DXY), HYG, TLT, BTC-USD, ^IRX (13w T-bill = Fed proxy),
              XLK XLE XLF XLV XLI XLY XLP, SPY (benchmark).
  - FRED public CSV (no key): DFF (Fed funds rate), NAPM (ISM Manufacturing PMI).
              Both optional — skip-on-fail per indicator.

Output:
  data/analysis/macro_overlay.parquet  (one row per trading day, indicator + regime cols)

Cache:
  data/analysis/macro_overlay_cache.json  (1h TTL, mirrors freshness of the parquet)

Public API (called by B-renderer):
  load_latest_regime() -> dict[str, str]            # for header line
  format_backdrop_line(regime: dict) -> str         # the one-line summary
  refresh(force: bool = False) -> pd.DataFrame      # do the fetch + write
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).resolve().parents[3]   # rexfinhub/
OUT_DIR = _ROOT / "data" / "analysis"
PARQUET = OUT_DIR / "macro_overlay.parquet"
CACHE_META = OUT_DIR / "macro_overlay_cache.json"

CACHE_TTL_SEC = 60 * 60   # 1 hour

# ---------------------------------------------------------------------------
# Indicator definitions
# ---------------------------------------------------------------------------
# Display name -> yfinance ticker
_YF_TICKERS = {
    "vix":     "^VIX",
    "dxy":     "DX-Y.NYB",
    "hyg":     "HYG",
    "tlt":     "TLT",
    "btc":     "BTC-USD",
    "irx":     "^IRX",     # 13-week T-bill yield — Fed-funds proxy
    "spy":     "SPY",
    "xlk":     "XLK",
    "xle":     "XLE",
    "xlf":     "XLF",
    "xlv":     "XLV",
    "xli":     "XLI",
    "xly":     "XLY",
    "xlp":     "XLP",
}
_SECTORS = ["xlk", "xle", "xlf", "xlv", "xli", "xly", "xlp"]
_SECTOR_LABELS = {
    "xlk": "Tech", "xle": "Energy", "xlf": "Financials",
    "xlv": "Healthcare", "xli": "Industrials",
    "xly": "Cons. Disc.", "xlp": "Cons. Staples",
}

# FRED series (skip on fail). DFF is reliably free; ISM Manufacturing PMI was
# retired from the free FRED feed when ISM moved to a licensed distribution —
# we attempt 'NAPM' (the legacy ID) but expect a 404. When ism_pmi is absent we
# simply omit it from the snapshot; nothing else depends on it.
_FRED_SERIES = {
    "fedfunds_pct":  "DFF",     # daily Fed funds effective rate
    "ism_pmi":       "NAPM",    # monthly ISM PMI (legacy, often 404 — best-effort)
}

# ---------------------------------------------------------------------------
# Fetchers (each wrapped in try/except — skip on fail, never abort the run)
# ---------------------------------------------------------------------------
def _fetch_yfinance(period: str = "400d") -> pd.DataFrame:
    """Pull all yfinance tickers in a single threaded call. Returns wide Close-price DF
    indexed by date, columns = our short names. Empty DF if yfinance itself fails."""
    try:
        import yfinance as yf
    except ImportError:
        log.warning("yfinance not installed — macro overlay cannot run")
        return pd.DataFrame()

    tickers = " ".join(_YF_TICKERS.values())
    log.info("yfinance: pulling %d tickers, period=%s", len(_YF_TICKERS), period)
    try:
        raw = yf.download(
            tickers, period=period,
            auto_adjust=True, progress=False, threads=True,
        )
    except Exception as e:
        log.error("yfinance bulk download failed: %s", e)
        return pd.DataFrame()

    if raw.empty:
        log.warning("yfinance returned empty frame")
        return pd.DataFrame()

    # auto_adjust=True returns multi-index (field, ticker) when many tickers.
    if isinstance(raw.columns, pd.MultiIndex):
        if "Close" in raw.columns.get_level_values(0):
            close = raw["Close"]
        else:
            log.warning("yfinance frame missing Close level: %s", raw.columns[:3])
            return pd.DataFrame()
    else:
        # Single-ticker shape (shouldn't happen here, but defensive)
        close = raw[["Close"]] if "Close" in raw.columns else raw

    # Reverse-map ticker -> short-name and drop tickers that returned all-NaN
    inv = {v: k for k, v in _YF_TICKERS.items()}
    out_cols = {}
    for tk in close.columns:
        if tk in inv:
            series = close[tk].dropna()
            if not series.empty:
                out_cols[inv[tk]] = close[tk]
            else:
                log.warning("yfinance: no data for %s — skipping", tk)
    if not out_cols:
        return pd.DataFrame()

    df = pd.DataFrame(out_cols)
    df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
    df.index.name = "date"
    return df.sort_index()


def _fetch_fred(series_id: str, timeout: int = 10) -> Optional[pd.Series]:
    """Pull one FRED series via the public fredgraph CSV (no API key required).
    Returns date-indexed Series, or None on failure."""
    import requests
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"
    try:
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0 (rexfinhub macro)"},
                         timeout=timeout)
        r.raise_for_status()
    except Exception as e:
        log.warning("FRED %s fetch failed (%s) — skipping", series_id, e)
        return None
    try:
        # Inline parse (text body is small)
        from io import StringIO
        df = pd.read_csv(StringIO(r.text))
        # Columns are 'observation_date' and the series id
        # FRED CSVs use 'observation_date' (modern) or 'DATE' (legacy) — accept both.
        date_col = next((c for c in ("observation_date", "DATE") if c in df.columns), None)
        if date_col and series_id in df.columns:
            s = pd.to_numeric(df[series_id].replace(".", np.nan), errors="coerce")
            # Build a clean DatetimeIndex (no .dt — that only exists on Series).
            idx = pd.DatetimeIndex(pd.to_datetime(df[date_col])).tz_localize(None).normalize()
            s.index = idx
            s.name = series_id
            return s.dropna()
    except Exception as e:
        log.warning("FRED %s parse failed (%s) — skipping", series_id, e)
    return None


# ---------------------------------------------------------------------------
# Derived indicators
# ---------------------------------------------------------------------------
def _credit_spread_proxy(hyg: pd.Series, tlt: pd.Series) -> pd.Series:
    """HYG / TLT total-return ratio. HIGHER ratio = HY outperforming = TIGHT spreads.
    We invert the sign in the regime-tag step so 'tight' is the calm reading.
    Returned as 60-day-window z-score so the absolute number is comparable across cycles."""
    paired = pd.concat([hyg.rename("hyg"), tlt.rename("tlt")], axis=1).dropna()
    if paired.empty:
        return pd.Series(dtype=float)
    ratio = paired["hyg"] / paired["tlt"]
    z = (ratio - ratio.rolling(60).mean()) / ratio.rolling(60).std(ddof=0)
    z.name = "credit_z"
    return z


def _btc_vs_200dma(btc: pd.Series) -> pd.Series:
    """BTC close vs its 200d moving average. >0 = above 200d = bull regime."""
    ma200 = btc.rolling(200, min_periods=60).mean()
    pct = (btc / ma200 - 1.0) * 100.0
    pct.name = "btc_vs_200dma_pct"
    return pct


def _fed_change_30d(rate: pd.Series) -> pd.Series:
    """Change in fed-funds proxy (or actual DFF) over the trailing 30 calendar days.
    Positive => hiking, negative => cutting, near-zero => pause."""
    rate = rate.sort_index().ffill()
    delta = rate - rate.shift(30)
    delta.name = "fed_change_30d_bps"
    return delta * 100.0   # report in bps if input is in % units


def _sector_relative_1m(sec: pd.DataFrame, spy: pd.Series) -> pd.DataFrame:
    """1-month (~21 trading days) total return for each sector minus SPY.
    Returns a DataFrame of relative returns in pct points; column names = sector keys."""
    if spy.empty or sec.empty:
        return pd.DataFrame()
    rel = pd.DataFrame(index=sec.index)
    spy_21 = spy / spy.shift(21) - 1.0
    for col in sec.columns:
        sec_21 = sec[col] / sec[col].shift(21) - 1.0
        rel[col] = (sec_21 - spy_21) * 100.0
    return rel


# ---------------------------------------------------------------------------
# Regime tagging
# ---------------------------------------------------------------------------
def _tag_risk(vix: float) -> str:
    if not np.isfinite(vix):
        return "unknown"
    if vix < 15:
        return "calm"
    if vix < 25:
        return "normal"
    return "stressed"


def _tag_credit(credit_z: float) -> str:
    """Higher HYG/TLT z-score = HY outperforming Treasuries = tight spreads."""
    if not np.isfinite(credit_z):
        return "unknown"
    if credit_z > 0.5:
        return "tight"
    if credit_z < -0.5:
        return "wide"
    return "neutral"


def _tag_crypto(btc_vs_200: float) -> str:
    if not np.isfinite(btc_vs_200):
        return "unknown"
    if btc_vs_200 > 0:
        return "bull"
    return "bear"


def _tag_fed(delta_bps: float) -> str:
    """Direction of the 30d change in the fed-funds proxy. ~5bp dead-band for IRX noise."""
    if not np.isfinite(delta_bps):
        return "unknown"
    if delta_bps > 5:
        return "hiking"
    if delta_bps < -5:
        return "cutting"
    return "pause"


def _tag_leadership(rel_row: pd.Series) -> str:
    """Sector with the highest 1M relative return. Falls back to 'mixed' if all NaN."""
    cleaned = rel_row.dropna()
    if cleaned.empty:
        return "mixed"
    top = cleaned.idxmax()
    return _SECTOR_LABELS.get(top, top.upper())


# ---------------------------------------------------------------------------
# Build the daily snapshot table
# ---------------------------------------------------------------------------
def build_snapshot(yf_df: pd.DataFrame,
                   fed_funds: Optional[pd.Series] = None,
                   ism_pmi: Optional[pd.Series] = None) -> pd.DataFrame:
    """Combine raw indicators + derived metrics + regime tags into one daily DF.

    Index is restricted to *equity trading days* (where ^VIX has a print). BTC,
    DFF, NAPM are forward-filled onto this index — without that, BTC's 24/7
    schedule produces orphan rows with NaN equity columns and broken regime tags.
    """
    if yf_df.empty:
        return pd.DataFrame()

    # Anchor the index on equity trading days. ^VIX is the cleanest signal —
    # it prints exactly when the US equity market is open. SPY is the fallback.
    if "vix" in yf_df.columns:
        trading_idx = yf_df["vix"].dropna().index
    elif "spy" in yf_df.columns:
        trading_idx = yf_df["spy"].dropna().index
    else:
        trading_idx = yf_df.index

    yf_df = yf_df.reindex(trading_idx).ffill()
    out = pd.DataFrame(index=trading_idx)

    # Raw levels
    for col in ("vix", "dxy", "hyg", "tlt", "btc", "irx", "spy"):
        if col in yf_df.columns:
            out[col] = yf_df[col]

    # Sector levels (kept for transparency)
    sec_cols = [c for c in _SECTORS if c in yf_df.columns]
    sec_df = yf_df[sec_cols] if sec_cols else pd.DataFrame()

    # Derived
    if "hyg" in yf_df and "tlt" in yf_df:
        out["credit_z"] = _credit_spread_proxy(yf_df["hyg"], yf_df["tlt"])
    if "btc" in yf_df:
        out["btc_vs_200dma_pct"] = _btc_vs_200dma(yf_df["btc"])
    if "spy" in yf_df and not sec_df.empty:
        rel = _sector_relative_1m(sec_df, yf_df["spy"])
        for c in rel.columns:
            out[f"rel1m_{c}"] = rel[c]

    # Fed-funds source: prefer FRED DFF, else fall back to ^IRX (13w T-bill)
    fed_source = "fred:DFF"
    fed_series = fed_funds
    if fed_series is None and "irx" in yf_df:
        fed_series = yf_df["irx"]
        fed_source = "yfinance:^IRX"
    if fed_series is not None and not fed_series.empty:
        # Reindex to trading days, ffill
        aligned = fed_series.reindex(out.index).ffill()
        out["fedfunds_pct"] = aligned
        out["fed_change_30d_bps"] = _fed_change_30d(aligned)
    out.attrs["fed_source"] = fed_source

    # ISM monthly — ffilled across daily index
    if ism_pmi is not None and not ism_pmi.empty:
        out["ism_pmi"] = ism_pmi.reindex(out.index, method="ffill")

    # Regime tags (per row)
    rel_cols = [c for c in out.columns if c.startswith("rel1m_")]
    out["regime_risk"]       = out["vix"].apply(_tag_risk) if "vix" in out else "unknown"
    out["regime_credit"]     = (out["credit_z"].apply(_tag_credit)
                                if "credit_z" in out else "unknown")
    out["regime_crypto"]     = (out["btc_vs_200dma_pct"].apply(_tag_crypto)
                                if "btc_vs_200dma_pct" in out else "unknown")
    out["regime_fed"]        = (out["fed_change_30d_bps"].apply(_tag_fed)
                                if "fed_change_30d_bps" in out else "unknown")
    if rel_cols:
        # Strip the rel1m_ prefix for the leadership lookup
        rel_only = out[rel_cols].rename(columns={c: c.replace("rel1m_", "") for c in rel_cols})
        out["regime_leadership"] = rel_only.apply(_tag_leadership, axis=1)
    else:
        out["regime_leadership"] = "mixed"

    return out


# ---------------------------------------------------------------------------
# Cache + persistence
# ---------------------------------------------------------------------------
def _cache_fresh() -> bool:
    """True if the parquet was written within CACHE_TTL_SEC."""
    if not CACHE_META.exists() or not PARQUET.exists():
        return False
    try:
        meta = json.loads(CACHE_META.read_text())
        return (time.time() - meta.get("written_at", 0)) < CACHE_TTL_SEC
    except Exception:
        return False


def _write_cache_meta(rows: int) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_META.write_text(json.dumps({
        "written_at": time.time(),
        "written_at_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "rows": rows,
        "ttl_sec": CACHE_TTL_SEC,
    }, indent=2))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def refresh(force: bool = False, period: str = "400d") -> pd.DataFrame:
    """Pull all sources and persist the parquet. Honours the 1h cache unless force=True.
    Returns the full snapshot DF (whatever survived skip-on-fail)."""
    if not force and _cache_fresh():
        log.info("Macro overlay cache fresh — loading from %s", PARQUET)
        return pd.read_parquet(PARQUET)

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    yf_df = _fetch_yfinance(period=period)
    if yf_df.empty:
        log.error("yfinance pull returned no data — cannot build macro overlay")
        return pd.DataFrame()

    dff = _fetch_fred("DFF")
    napm = _fetch_fred("NAPM")

    snap = build_snapshot(yf_df, fed_funds=dff, ism_pmi=napm)
    if snap.empty:
        log.error("build_snapshot returned empty")
        return snap

    # (Index already anchored on equity trading days inside build_snapshot.)
    snap.to_parquet(PARQUET, index=True)
    _write_cache_meta(rows=len(snap))
    log.info("Macro overlay written: %d rows -> %s", len(snap), PARQUET)
    return snap


def load_latest_regime() -> dict[str, str]:
    """Return the regime tags for the most recent row. Used by B-renderer.
    Refreshes if cache is stale; returns {} if everything fails."""
    df = refresh(force=False)
    if df.empty:
        return {}
    last = df.iloc[-1]
    return {
        "risk":       str(last.get("regime_risk", "unknown")),
        "credit":     str(last.get("regime_credit", "unknown")),
        "crypto":     str(last.get("regime_crypto", "unknown")),
        "fed":        str(last.get("regime_fed", "unknown")),
        "leadership": str(last.get("regime_leadership", "mixed")),
        "as_of":      df.index[-1].strftime("%Y-%m-%d"),
    }


def format_backdrop_line(regime: dict[str, str]) -> str:
    """Render the one-line backdrop string used in the B-renderer header.

    Example: "Risk: normal | Credit: tight | Crypto: bull | Fed: pause | Leadership: Tech."
    """
    if not regime:
        return "Macro Backdrop: data unavailable."
    return (
        f"Risk: {regime.get('risk','?')} | "
        f"Credit: {regime.get('credit','?')} | "
        f"Crypto: {regime.get('crypto','?')} | "
        f"Fed: {regime.get('fed','?')} | "
        f"Leadership: {regime.get('leadership','?')}."
    )


# ---------------------------------------------------------------------------
# CLI entrypoint  (python -m screener.li_engine.data.macro)
# ---------------------------------------------------------------------------
def _main() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    df = refresh(force=True)
    if df.empty:
        print("FAILED — no data")
        return
    print(f"\nMacro overlay snapshot: {len(df)} rows -> {PARQUET}")
    print(f"Fed-funds source: {df.attrs.get('fed_source', '?')}")
    regime = load_latest_regime()
    print(f"\nLatest regime ({regime.get('as_of','?')}):")
    print(f"  {format_backdrop_line(regime)}")

    print("\nLast 5 daily rows (regime cols only):")
    cols = [c for c in df.columns if c.startswith("regime_")]
    print(df[cols].tail(5).to_string())

    print("\nLast row (all numeric cols):")
    num_cols = [c for c in df.columns if not c.startswith("regime_")]
    print(df[num_cols].iloc[-1].to_string())


if __name__ == "__main__":
    _main()
