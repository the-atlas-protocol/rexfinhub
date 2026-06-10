# Upgrade F1 — Stock Recs macro overlay

Branch: `audit-stockrecs-F1-macro`
Wave: F1 (macro). Feeds the Stock Recs B-renderer "Macro Backdrop" header.
Status: implemented + verified. FRED ISM/NAPM expectedly skipped (series retired).

## Files added

| File | LoC | Purpose |
|---|---|---|
| `screener/li_engine/data/__init__.py` | 1 | Package marker for the new `data/` sub-namespace under the L&I engine. |
| `screener/li_engine/data/macro.py` | ~340 | Macro overlay: pulls VIX/DXY/HYG/TLT/BTC/^IRX + 7 sector ETFs from yfinance and DFF/NAPM from FRED, derives credit-spread proxy + BTC vs 200dma + 1M sector relative perf, tags regime, writes daily-snapshot parquet. Skip-on-fail per indicator, 1h cache. |
| `data/analysis/macro_overlay.parquet` | 61 KB | Daily snapshot — 383 trading-day rows × 23 columns (raw indicators + derived metrics + regime tags). Gitignored (under `data/`). |
| `data/analysis/macro_overlay_cache.json` | 126 B | Cache freshness sidecar — `written_at` epoch + ISO timestamp + row count + TTL. |

## Architecture

```
yfinance (single threaded bulk pull, 14 tickers, ~2s)
   |
   +--> ^VIX, ^IRX, SPY, DX-Y.NYB, HYG, TLT, BTC-USD,
   |    XLK XLE XLF XLV XLI XLY XLP
   |
FRED public CSV (no API key, 10s timeout each, skip-on-fail)
   +--> DFF (Fed funds rate)        — preferred over ^IRX when available
   +--> NAPM (ISM PMI)              — typically 404 (series retired); best-effort
   |
   v
build_snapshot()
   - Anchor index on equity trading days (^VIX print) — NOT BTC's 24/7 calendar.
     Without this, weekend BTC rows produce orphan rows with NaN equity columns
     and unknown regime tags.
   - Forward-fill BTC/DFF/NAPM onto the trading-day index.
   - Derive credit_z (HYG/TLT 60d z-score), btc_vs_200dma_pct, rel1m_<sector>
     (sector 21d return minus SPY 21d return), fed_change_30d_bps.
   - Tag five regimes per row: risk, credit, crypto, fed, leadership.
   |
   v
data/analysis/macro_overlay.parquet (one row per trading day)
   |
   v
load_latest_regime() -> dict          # B-renderer header dict
format_backdrop_line(regime) -> str   # the formatted single-line header
```

## Output schema (23 columns)

| Column | Type | Source | Notes |
|---|---|---|---|
| `vix` | float | yfinance ^VIX | risk indicator |
| `dxy` | float | yfinance DX-Y.NYB | dollar level (raw, no regime tag yet) |
| `hyg` | float | yfinance HYG | HY corp bond ETF |
| `tlt` | float | yfinance TLT | 20+yr Treasury ETF |
| `btc` | float | yfinance BTC-USD | crypto risk regime |
| `irx` | float | yfinance ^IRX | 13w T-bill yield (Fed proxy fallback) |
| `spy` | float | yfinance SPY | benchmark for sector relative perf |
| `credit_z` | float | derived | 60d z-score of HYG/TLT ratio. >0.5 = tight, <-0.5 = wide |
| `btc_vs_200dma_pct` | float | derived | (BTC / 200d MA - 1) * 100. Sign drives bull/bear |
| `rel1m_xlk … rel1m_xlp` | float | derived | (sector_21d_ret - spy_21d_ret) * 100 |
| `fedfunds_pct` | float | FRED DFF or yfinance ^IRX | source recorded in `df.attrs['fed_source']` |
| `fed_change_30d_bps` | float | derived | trailing 30d delta in bps |
| `regime_risk` | str | tag | calm (<15) / normal (15-25) / stressed (>25) |
| `regime_credit` | str | tag | tight (z>0.5) / neutral / wide (z<-0.5) |
| `regime_crypto` | str | tag | bull (>200dma) / bear (≤200dma) |
| `regime_fed` | str | tag | hiking (>5bp) / pause / cutting (<-5bp) — 5bp dead-band for IRX noise |
| `regime_leadership` | str | tag | sector ETF with highest 1M relative return ("Tech", "Energy", …) |

## B-renderer integration contract

```python
from screener.li_engine.data.macro import load_latest_regime, format_backdrop_line

regime = load_latest_regime()                       # dict, refreshes if cache stale
header  = format_backdrop_line(regime)              # the one-line backdrop string
# -> "Risk: normal | Credit: tight | Crypto: bear | Fed: pause | Leadership: Tech."
```

If everything fails (yfinance dead + cache gone), `load_latest_regime()` returns `{}`
and `format_backdrop_line({})` returns `"Macro Backdrop: data unavailable."` — the
B-renderer should always be safe to call this without a try/except wrapper.

## Verification

```
$ python -m screener.li_engine.data.macro
yfinance: pulling 14 tickers, period=400d
FRED NAPM fetch failed (404 Client Error: Not Found ...) — skipping
Macro overlay written: 383 rows -> data/analysis/macro_overlay.parquet
Fed-funds source: fred:DFF

Latest regime (2026-05-11):
  Risk: normal | Credit: tight | Crypto: bear | Fed: pause | Leadership: Tech.

Last 5 daily rows (regime cols only):
           regime_risk regime_credit regime_crypto regime_fed regime_leadership
date
2026-05-05      normal         tight          bear      pause              Tech
2026-05-06      normal         tight          bear      pause              Tech
2026-05-07      normal         tight          bear      pause              Tech
2026-05-08      normal         tight          bear      pause              Tech
2026-05-11      normal         tight          bear      pause              Tech

Last row (all numeric cols):
vix                      18.379999
dxy                      98.135002
hyg                      79.980003
tlt                      85.559998
btc                   80186.765625
irx                       3.600000
spy                     739.299988
credit_z                  1.417113
btc_vs_200dma_pct       -13.645561
rel1m_xlk                15.916059
rel1m_xle                -8.403055
rel1m_xlf                -7.999425
rel1m_xlv               -11.705641
rel1m_xli                -6.754756
rel1m_xly                -3.066884
rel1m_xlp                -7.592954
fedfunds_pct              3.630000
fed_change_30d_bps       -1.000000
```

Reads of the today's tape:
- VIX 18.4 → mid of the 15-25 band → `normal`
- HYG/TLT z = +1.42 → strongly above mean → `tight` credit
- BTC at 80.2k vs 200dma is -13.6% → `bear`
- DFF 30d change = -1bp (within 5bp dead-band) → `pause`
- Tech +15.9% relative-perf vs SPY over 21d, all six other sectors negative → `Tech` leadership

All five regime tags are decisive (no `unknown`/`mixed`), which means the B-renderer
header has clean copy on day one.

## Decisions worth flagging

1. **Equity-trading-day anchor over calendar-day index.** Originally indexed on the
   union of all tickers, which produced a 1-2 day BTC-only "tail" of rows with
   NaN equity columns and `unknown` regime tags. Switched to `^VIX`'s print
   schedule as the canonical trading-day signal; ffill BTC/FRED onto it. Halved
   the row count (526 → 383) but every row is now decisive.

2. **^IRX as Fed-funds fallback.** FRED's DFF endpoint was intermittently slow
   from this network during dev (10-30s timeouts). The ^IRX 13w T-bill closely
   tracks Fed funds and comes in for free in the same yfinance call. Picked at
   build_snapshot time; the source string lands in `df.attrs['fed_source']` so
   downstream readers can disclaim it.

3. **NAPM is best-effort only.** ISM Manufacturing PMI moved off FRED's free
   feed years ago; the legacy `NAPM` ID 404s. Left the attempt in place as a
   try-skip so if ISM ever republishes (or we add a paid feed later) the column
   appears automatically. No regime tag depends on it.

4. **Credit spread proxy via HYG/TLT z-score, not actual OAS.** ICE BofA OAS
   series sit behind a paywall. HYG/TLT total-return ratio is the closest free
   proxy; z-scoring against a 60d window normalises across the cycle so
   `>0.5 = tight` is comparable in 2024 vs 2026.

5. **5bp dead-band on Fed regime tag.** ^IRX prints day-to-day noise of 1-3bp.
   Without a dead-band, `regime_fed` would flicker between `pause` and `cutting`
   on a daily basis. Even DFF moves slowly enough that 5bp resolution is fine
   for a header line.

6. **1h cache.** `load_latest_regime()` is called by the B-renderer, possibly
   inside a per-recommendation loop. Without caching, every render would hit
   yfinance for ~2s. The TTL sidecar `macro_overlay_cache.json` carries the
   freshness; `force=True` on `refresh()` bypasses it for batch rebuilds.

## Constraints honoured

- yfinance + free FRED CSV only (no API keys).
- Skip-on-fail per indicator: NAPM 404, BTC weekend gaps, future yfinance
  outages — all degrade gracefully without aborting the snapshot build.
- 1h cache via the sidecar JSON — no re-fetch within the TTL window.
- ~60 min budget — single dev session. Two iterations after first run to fix
  the FRED CSV `.dt`-on-DatetimeIndex bug and the BTC weekend orphan rows.

## Rollback

```
git rm screener/li_engine/data/macro.py screener/li_engine/data/__init__.py
rm data/analysis/macro_overlay.parquet data/analysis/macro_overlay_cache.json
```

The B-renderer should fall through to `format_backdrop_line({})` (the empty-dict
fallback) — i.e. emit `"Macro Backdrop: data unavailable."` until F1 is restored.
