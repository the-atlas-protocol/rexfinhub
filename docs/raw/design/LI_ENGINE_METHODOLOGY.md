# L&I Recommender Engine — Methodology v0.1

**Owner**: Ryu El-Asmar
**Created**: 2026-04-22
**Status**: Approved (supersedes ad-hoc weights in `screener/config.py`)
**Related**: `docs/trex-strategy-system.md`, `temp/backtest_2x.py`, `temp/aum_correlation_analysis.py`

---

## Problem

The existing leveraged-ETF candidate screener has two structural weaknesses:

1. **Weights lack empirical grounding.** `screener/config.py:19-25` sets turnover=0.30, OI=0.30, market cap=0.20, volatility=0.10, short interest=0.10. Turnover/OI/market cap weights trace to correlation analysis in `temp/backtest_2x.py`, but volatility and short interest are design-driven. Thresholds ($10B market cap floor, 75/40 percentile cutoffs, -25/-15 competitive penalties) are uncalibrated.
2. **Target variable is contaminated.** Prior analysis regressed signals against product AUM. AUM = cumulative flows + market P&L of the underlier, so any signal that predicts the *underlier's* price movement gets credited as if it predicts *demand for the leveraged product*. BMNU is the canonical failure case: strong flows, falling AUM because the underlier sold off. AUM called BMNU a failure; flows called it a success. Flows were right.

## Target variable

**Forward 90-day net flows / starting AUM, winsorized [1%, 99%].**

- Flows isolate *demand for the product* from market P&L of the underlier.
- Normalizing by starting AUM removes scale bias between established and newly-launched funds.
- Winsorization blunts tiny-AUM outliers that would otherwise dominate.

### Current constraint and v0.1 compromise

Our dated snapshot history is **~52 days** (147 completed pipeline runs, 2026-02-25 to 2026-04-17). That is insufficient to compute forward 90-day flow changes between two snapshot dates. Rather than fabricate a target or postpone until late Q2, v0.1 uses:

- **Contemporaneous IC**: regress signals at time T against the trailing 90-day flow-to-AUM ratio *reported at time T*. This is a correlation, not a prediction, and will be labeled as such in all output.
- **Rolling recalibration** quarterly as the dated-snapshot history extends. Once we reach ~120 days of dated history, we switch to true forward-window IC.

This is an honest compromise, not a cover-up: the v0.1 weights describe "what is currently associated with high inflows," which is meaningfully different from "what predicts future inflows." Weekly reports will carry a one-line disclaimer until we clear 120 days.

## Signal panel — six pillars

| # | Pillar | Signals | Primary source | Notes |
|---|---|---|---|---|
| 1 | Liquidity / Demand | 30-day ADV, turnover (volume × price / shares outstanding), market cap | bbg `mkt_stock_data`, yfinance OHLCV | Strongest historical correlate (r ≈ 0.74 log) |
| 2 | Options Demand | total OI, put/call skew, OI change vs. 30-day avg | bbg `mkt_stock_data` | `Total OI` field already in data_json |
| 3 | Volatility / Attractiveness | 30-day realized vol, 90-day realized vol | bbg + yfinance | Retail leverage demand correlates with vol |
| 4 | Competitive White Space | 1 − (density of existing 2x/3x/4x products in the underlier's category) | `mkt_master_data` + `map_li_*` columns | Low density = open space, high density = saturated |
| 5 | Korean Overnight Demand | 1W OC Equity traded value, WoW delta, 1W/3M ratio | bbg `OC` sheet (Blue Ocean ATS) | Conditional signal — only 45% of universe has non-null OC data. Scored 0 when absent, not penalized |
| 6 | Social Sentiment | 24-hour mention count, mention delta, rank delta | ApeWisdom API (`/api/v1.0/filter/all-stocks`, `/filter/wallstreetbets`) | Raw attention counts, not NLP polarity. Finnhub social sentiment as backup |

Each signal is cross-sectionally z-scored on every pipeline run to handle magnitude drift over time. Scores are then clipped at ±3σ.

## Weighting method

**Spearman rank-IC between each signal and the target variable, pooled across all 147 runs.**

- Weights are proportional to |IC|.
- **Floor**: 5% per pillar (keeps signals alive even if they currently show low IC — prevents brittleness).
- **Cap**: 35% per pillar (prevents any single signal from dominating).
- Weights within a pillar (e.g., ADV vs. turnover vs. market cap in Pillar 1) use the same IC-proportional logic.
- Sentiment gets a prior weight of 10% for v0.1 because we have zero historical sentiment data to calibrate against.

### Why Spearman, not Pearson

Spearman is rank-based and robust to outliers, skewness, and the extreme non-linearity we see in flow data (single filings can produce 10x jumps). Pearson would give undue weight to whatever the biggest flow day was in the sample.

### Output of the weighting step

A JSON file `screener/config/weights.json` with:
- Per-signal IC, 95% confidence interval, weight
- Run date, data window, sample size
- Versioned — old weights retained for reproducibility

## Scoring workflow

```
for each (ticker, pipeline_run_id):
    for each signal:
        raw = extract from mkt_stock_data / mkt_master_data / OC sheet / ApeWisdom
        z = cross-sectional z-score within this run
        clipped = clip(z, -3, 3)
    pillar_scores = weighted avg of signals within each pillar
    final_score = weighted avg of pillar scores
```

## Scoring output

Per ticker, per run:
- `final_score` (0–100 percentile)
- `pillar_scores` (dict of 6)
- `top_contributors` (top 3 signals driving the score)
- `filing_status` (have we filed / launched / neither) — joined from `FundStatus`

## Recalibration cadence

- **Every pipeline run**: regenerate scores using latest weights.
- **Every Friday EOD**: log the current IC of each signal to `mkt_signal_ic` (new table) for drift tracking.
- **Every month**: recompute weights from the rolling window. Commit to `weights.json` with a new version tag. Diff weights vs. prior version — alert if any weight moves >10 percentage points (possible regime change or data issue).

## Competitive filter (kept from existing screener)

Underliers with existing REX 2x/3x products in the *same* leverage level are excluded from the "file candidates" list (they're already ours). They remain in the "launch candidates" list for underliers where we have filings but haven't launched.

## Report contents (weekly PDF)

1. **Executive summary** — top 3 file candidates, top 3 launch candidates, top 3 trending themes, one-line changes since last week
2. **Launch candidates** — underliers with existing REX filings, ranked by final_score, showing pillar breakdown and "days since filing"
3. **File candidates** — underliers without REX filings, ranked by final_score, filtered to market cap > $1B and some minimum 1Y ADV
4. **IPO pipeline** — upcoming and recent IPOs (Stock Analysis scrape) with preliminary scores based on available data
5. **Trending themes** — sentiment + volatility + OC demand rolled up by sector / theme YAML
6. **Weight and IC report** — current weights per pillar, most recent IC computation, flag any signal whose IC has flipped sign or moved >0.15

## Deferred to future sessions

- KSD (Korea Securities Depository) data integration
- Catalyst layer (earnings, analyst ratings, FDA calendar, etc.)
- Equity research ingestion
- Website wire-up of `/filings/landscape`, `/filings/candidates`, `/filings/evaluator` to new engine output
- True forward-window IC (requires 120+ days of dated history, est. mid-Q3)

## Risks and known limitations

| Risk | Mitigation |
|---|---|
| v0.1 uses contemporaneous IC, not forward IC | Disclaimer on every report. Auto-switch at 120 days of history |
| Historical bbg file archival is broken (22% coverage since Feb 25) | Minimal fix to `run_daily.py` forces daily Graph API pull |
| Sentiment has no historical data | Prior weight of 10%, recalibrate monthly as data accumulates |
| ApeWisdom universe is trending-tail-only (~870 tickers) | Treat "not in response" as zero attention, don't penalize |
| OC sheet has 45% ticker coverage | Signal is conditional-positive-only: present → up-weight, absent → neutral |
| Options OI beyond 7 weeks of our snapshots requires paid feed | v0.1 uses what we have; flag as backlog for Q3 if we want deeper OI history |
