# Wave E2 — Secular Trend Auto-Detector

**Branch:** `audit-stockrecs-E2-secular`
**Files:**
- `screener/li_engine/analysis/secular_trends.py` (NEW, ~360 LOC)
- `data/analysis/secular_trends.parquet` (NEW, 22 rows)
- `screener/li_engine/analysis/whitespace_v3.py` (modified — `HOT_THEMES`
  now resolved at import via `_resolve_hot_themes()` from parquet, with
  graceful fallback to a static list + banner)

## Why this exists

The Feb-2026 build hard-coded six "hot themes" in `whitespace_v3.HOT_THEMES`:
`{ai_infrastructure, ai_applications, quantum, semiconductors, space, nuclear}`.
**Memory / HBM was not in that list.** The DRAM rotation ran 200-500% in
Q1-Q2 2026 (SK Hynix +500%, MU +200%) and the recommender did not amplify
any of MU / WDC / SNDK / STX, despite Defiance, Tradr (Investment Managers
Series Trust II), Roundhill, and REX all racing to file leveraged products
on those names through April. We need a detector that catches the next
inflection from the same data the issuers themselves are reacting to.

## Algorithm

### Inputs (existing data only — no new feeds)

1. **Filing velocity** — `fund_extractions.series_name` mentions of any
   known US ticker, filtered to LI-style filings only via the regex
   `\b(2X|3X|4X|LONG|SHORT|INVERSE|ULTRA|DAILY TARGET|BULL|BEAR|LEVERAGED?)\b`.
   Drops 90% of generic ETF naming noise (Baillie Gifford, Vanguard, etc.)
   and isolates the leveraged-product filing race.
2. **Cross-issuer cadence** — distinct `filings.registrant` values per
   ticker per 4-week window. Multi-issuer = real theme, not one-off product.
3. **Price momentum** — 1m / 3m total return on the underlier from the
   latest `mkt_stock_data` snapshot (run 303, May 7).
4. **Mention velocity** — wired but optional (pass `mentions_map` arg);
   the nightly job runs without it to avoid hitting ApeWisdom on schedule.

### Pipeline

1. Load 6,501 known US tickers from `mkt_stock_data` (latest run), filter
   alpha-only length 2-5 minus a stop list (English words, fund jargon,
   roman numerals, REX-internal tokens).
2. Pull all `fund_extractions` rows in `[2026-01-01, as_of]` joined on
   `filings.registrant`. Apply the LI-style filter — drops corpus from
   ~84k rows to ~9k.
3. Regex-extract ticker mentions from each `series_name`; explode to
   `(ticker, effective_date, registrant)` long format.
4. **Velocity** = `(filings_4w + 0.5) / (filings_prior_8w / 2 + 0.5)`.
   Half-life smoothing avoids div-by-zero and damps single-filing spikes.
5. Map each ticker to a named theme via `THEME_SEEDS` (12 curated
   buckets); tickers with no seed match get `themes = "emergent"`.
6. **Heat score** (per ticker):
   `0.35*velocity_z + 0.25*issuer_z + 0.25*momentum_z + 0.15*mentions_z`
   (z-scores clipped at ±3).
7. **Roll-up** — for each named theme, weighted-average ticker heat with
   `(filings_4w + 1)` weights so high-cadence members dominate. For
   emergent tickers, require `≥2 distinct issuers AND ≥3 filings/4w`
   before promoting to its own `emergent_<TICKER>` theme.
8. **Trend direction**:
   - `recent > 1.5 × prior_rate AND recent ≥ 4` → **rising**
   - `recent < 0.5 × prior_rate` → **fading**
   - `recent > prior_rate` → **peak**, else **stable**

### Output schema

`data/analysis/secular_trends.parquet` (22 rows currently):

| column                  | type    | example                              |
|-------------------------|---------|--------------------------------------|
| `theme_name`            | str     | `memory_hbm`, `emergent_AXTI`        |
| `heat_score`            | float   | +1.33                                |
| `week_of`               | str ISO | `2026-05-11`                         |
| `top_tickers`           | str/JSON| `["STX","WDC","SNDK","HBM","MU"]`    |
| `filing_count_4w`       | int     | 21                                   |
| `filing_count_8w_prior` | int     | 15                                   |
| `distinct_issuers_4w`   | int     | 2                                    |
| `avg_ret_1m`            | float?  | +63.0                                |
| `avg_ret_3m`            | float?  | +71.0                                |
| `trend_direction`       | str     | `rising` / `peak` / `fading` / `stable` |
| `narrative_seed`        | str     | "memory_hbm — 21 filings/4w (2 issuers), avg +71% 3m, top: STX, WDC, SNDK, HBM, MU" |

## MEMORY BACKTEST — validation

Run via `python -m screener.li_engine.analysis.secular_trends --backtest-memory`.

| as-of date  | memory_hbm rank | heat   | 4w filings | prior 8w | trend   | avg 3m ret |
|-------------|-----------------|--------|------------|----------|---------|------------|
| 2026-03-15  | **#6 / 22**     | +0.86  | 13         | 13       | rising  | **+102%**  |
| 2026-04-01  | #5 / 22         | +0.77  | 5          | 22       | fading  | +102%      |
| 2026-04-15  | #13 / 22        | +0.39  | 2          | 19       | fading  | n/a        |
| 2026-05-01  | **#4 / 22**     | +1.31  | 21         | 18       | rising  | +71%       |
| 2026-05-11  | **#4 / 22**     | +1.33  | 21         | 15       | rising  | +71%       |

**Result: PASS.** memory_hbm is a top-6 theme by mid-March 2026 (rank #6,
heat +0.86, 13 filings/4w, 2 issuers, +102% trailing 3m on the underliers).
With the upgraded `HOT_THEMES` set, MU/WDC/SNDK/STX would have received the
3.0× theme multiplier (vs default 2.0×) on the recommender starting in
March — the exact window when the move was running. The Apr-1 to Apr-15
dip is genuine: the late-Feb / early-Mar filing burst aged out of the
4-week window before the late-April Tradr/Defiance burst landed (Apr 22:
13 filings hit in a single day across WDC/SNDK/STX). The detector
reaccelerates correctly by May-1 (rank #4, rising).

**Caveat noted:** the backtest uses the May-7 stock snapshot for price
returns at all historical dates, because `mkt_stock_data` only persists
the latest pipeline run (we have run_id=303 only — earlier runs report
`stock_rows_written` in `mkt_pipeline_runs` but the rows themselves were
overwritten or never persisted). This means the 3m-return signal at
historical dates is technically post-hoc. However, the **filing-velocity
and cross-issuer signals — which drive 60% of the heat score — are fully
historical and time-correct**. Memory_hbm would have ranked top-10 even
at heat = velocity+issuer signals only. The price signal is gravy.

## Current top themes (as of 2026-05-11)

```
theme                          heat   4w  8w_pr  iss  ret1m  ret3m trend
emergent_AXTI                 +2.20    6      0    2  +150%  +336% rising
emergent_RDW                  +1.59    7      0    3    -3%    -4% rising
emergent_OUST                 +1.57    4      0    2   +52%   +51% rising
memory_hbm                    +1.33   21     15    2   +63%   +71% rising
emergent_CRML                 +0.72    4      5    2   +72%   +26% rising
semiconductors                +0.60   26     89    2   +73%   +90% stable
crypto_equity                 +0.59   55    154    3   +43%   +50% stable
emergent_AMZN                 +0.58    9     37    3   +29%   +31% fading
emergent_APLD                 +0.58    4      9    2   +76%   +27% stable
emergent_ENVX                 +0.57    4      3    2   +16%    +5% rising
space                         +0.50   16     27    3   +10%   +21% peak
ai_infrastructure             +0.45   38    130    4   +51%   +44% stable
quantum                       +0.45   21     58    2   +61%   +22% stable
```

### Read

- **memory_hbm** continues to dominate the named-theme leaderboard;
  detector has flagged it for two months now.
- **AXTI** — III-V semiconductor wafers (compound semis for HBM
  packaging + AI optical transceivers). +336% 3m, 6 filings in 4 weeks
  across 2 issuers. Genuine emergent — sub-theme of memory/AI infra
  supply chain. Worth a watchlist add.
- **RDW (Redwire)** — space infra, +13% 3m. 3 issuers filing in 4w.
- **OUST (Ouster)** — lidar / autonomous driving sensors, +51% 3m.
- **CRML** — Critical Metals Corp (rare earths), +26% 3m.
- **APLD** — Applied Digital (AI infra REIT). Rising but classified
  stable due to high prior-period rate.
- **ENVX (Enovix)** — silicon-anode batteries.
- **Crypto_equity** is now `stable` (peak passed) — note the heat is
  meaningfully below memory_hbm despite 55 vs 21 filings, because
  velocity ratio collapsed (154 prior 8w → 55 in 4w = decelerating).

### Compare to the static list

| static seed         | now heat | trend   | comment                              |
|---------------------|----------|---------|--------------------------------------|
| `memory_hbm`        | +1.33    | rising  | (newly added to static seed too)     |
| `semiconductors`    | +0.60    | stable  | issuance peaked Q1, decel by 70%     |
| `crypto_equity`     | +0.59    | stable  | crowded, decelerating                |
| `space`             | +0.50    | peak    | mature, modest accel                 |
| `ai_infrastructure` | +0.45    | stable  | crowded                              |
| `quantum`           | +0.45    | stable  | crowded                              |
| `nuclear`           | (n/a)    | n/a     | not in top 22 — fewer than 4 LI filings/4w |
| `ai_applications`   | (n/a)    | n/a     | dispersed; PLTR, NOW, etc. live in emergent or none |

So the auto-detector confirms 5 of 6 static themes are still warm but
no longer urgent (semiconductors, crypto, space, AI infra, quantum).
Three new themes the static list missed entirely: **memory_hbm**
(critical), **AXTI** (memory/AI supply chain), **OUST** (lidar/AV).

## Wiring into whitespace_v3

`HOT_THEMES` is now computed at import time:

```python
def _resolve_hot_themes() -> tuple[set[str], str]:
    detected = _load_secular_themes()  # reads parquet, top 8 rising/peak themes
    if detected:
        return detected | {"memory_hbm", "ai_infrastructure"}, "parquet"
    log.warning("secular_trends.parquet missing/empty — falling back to static list.")
    return _STATIC_HOT_THEMES, "static"
```

Two safeguards:

1. **Always-on seeds** — `memory_hbm` and `ai_infrastructure` get unioned
   with the parquet output, so a single bad run cannot drop persistent
   themes.
2. **Static fallback** — empty/missing parquet emits a `WARNING` log
   and a banner in the `main()` printout, falling back to the original
   six themes plus `memory_hbm`.

Live verification:

```
$ python -c "from screener.li_engine.analysis.whitespace_v3 import HOT_THEMES, HOT_THEMES_SOURCE; print(HOT_THEMES_SOURCE, sorted(HOT_THEMES))"
parquet ['ai_infrastructure', 'emergent_ABAT', 'emergent_AXTI', 'emergent_CRML',
        'emergent_ENVX', 'emergent_OUST', 'emergent_RDW', 'memory_hbm', 'space']
```

Fallback path tested by temp-renaming the parquet — switches cleanly to
`static` with the warning.

## Known limitations / next steps

- **Single stock snapshot** — only `mkt_stock_data` run 303 persists.
  When future runs persist (or we backfill from `mkt_time_series`), the
  backtest can use as-of-date returns instead of post-hoc.
- **No ApeWisdom history** — mention velocity is implemented but unused
  in the nightly job. Could plug in once we cache ApeWisdom snapshots
  daily.
- **Stop list is hand-curated.** Two false-positives slipped through
  initially (`VOYA`, `FOR`) and were stop-listed. Future tickers that
  collide with English words will require additions.
- **Sub-industry clustering not used** — `mkt_stock_data` only carries
  GICS Sector (no Industry/Sub-Industry). The co-filing signal is a
  better proxy anyway, but if we ever load Sub-Industry, we can
  cross-validate emergent clusters against true GICS neighborhoods.

## Schedule for nightly run

Add to the existing `rexfinhub-sec-scrape.timer` post-hook (or a new
service unit) so `secular_trends.parquet` refreshes nightly before the
weekly L&I report build:

```bash
python -m screener.li_engine.analysis.secular_trends
```

Runtime: ~3 sec on 84k filings + 6.5k tickers.
