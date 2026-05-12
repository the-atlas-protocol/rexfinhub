# Wave F2 — Catalyst Calendar

**Date:** 2026-05-11
**Branch:** `audit-stockrecs-F2-catalysts`
**Owner:** stockrecs upgrade — F2

## Goal

Give every L&I launch recommendation a "why now" justification. Build a
catalyst calendar that maps tickers in `launch_candidates.parquet` to upcoming
events (earnings, FDA, conferences, regulatory) within a 90-day horizon.

## Files added

| File | Purpose |
| --- | --- |
| `screener/li_engine/data/__init__.py` | New data sub-package marker. |
| `screener/li_engine/data/catalysts.py` | Catalyst collector + renderer helpers. |
| `data/analysis/catalyst_calendar.parquet` | Output (gitignored, regenerated). |
| `data/analysis/catalyst_cache/` | Per-ticker JSON cache (24h TTL, gitignored). |

## Schema

Long-format parquet, one row per (ticker, catalyst):

```
ticker            str         — underlier symbol
catalyst_type     str         — earnings | fda | conference | regulatory
catalyst_date     datetime    — date of event (or recent FDA news)
source            str         — yfinance | fda_rss | conferences_2026 | sec
description       str         — human-readable label
confidence        str         — high | medium | low
```

Sorted by `(ticker, catalyst_date)`. Up to 2 catalysts per ticker, ranked by
confidence (high > medium > low) then soonest-first.

## Sources

1. **Earnings — `yfinance`** (high confidence)
   Pulls `Ticker.calendar["Earnings Date"]`. Filters to the next 90 days.
   Adds consensus EPS to the description when available.

2. **FDA — press-release RSS** (high confidence, recent-past only)
   Fetches the FDA press-release feed once per process. Matches each
   entry's title against the ticker's company name (yfinance `longName`
   with corp-suffix scrubbing). Includes items from the last 14 days as
   a fresh-news catalyst. Health-care sector tickers only.

3. **Conferences — static `_CONFERENCES_2026` list** (medium/high)
   - High-confidence: tickers explicitly pinned to a conference in
     `_TICKER_CONFERENCES` (e.g. NVDA → GTC, AAPL → WWDC).
   - Medium: any health-care ticker → BIO International (broad biotech draw).
   - **No sector-wide fallback** — that produced too much noise (AMC →
     Microsoft Build, AUR → Intel Vision). Only earnings + pinned
     conferences attach to non-pinned tickers.

4. **Regulatory — placeholder** (empty in v1)
   Slot reserved so renderers don't break when populated. SEC submissions
   parsing per ticker is out of scope for the 60-min window; will be added
   in F3 if useful.

## Cache

Per-ticker JSON under `data/analysis/catalyst_cache/<TICKER>.json` with a
24-hour TTL. Keeps yfinance calls cheap on re-runs (full build is ~20s warm,
~90s cold). FDA RSS is cached in-process for the run duration.

## Renderer helpers

```python
from screener.li_engine.data.catalysts import (
    build_catalyst_calendar,    # writes the parquet
    soonest_catalyst_per_ticker,  # DataFrame indexed by ticker
    why_now_tag,                # str like "EARNINGS in 5d: Q earnings (...)"
)
```

The recommendation renderer should call `why_now_tag(ticker)` per row to
produce the "why now" badge.

## Verification — sample 10 tickers

```
DOCN  -> EARNINGS in 84d: Q earnings (consensus EPS ~0.25)
FSLY  -> EARNINGS in 85d: Q earnings (consensus EPS ~0.07)
FMCC  -> EARNINGS in 79d: Q earnings (consensus EPS ~0.00)
FNMA  -> EARNINGS in 78d: Q earnings (consensus EPS ~0.00)
AUR   -> EARNINGS in 78d: Q earnings (consensus EPS ~-0.12)
TE    -> EARNINGS today: Q earnings (consensus EPS ~-0.10)
HBM   -> EARNINGS in 80d: Q earnings (consensus EPS ~0.35)
ASST  -> EARNINGS in 2d: Q earnings (consensus EPS ~-0.12)
TLRY  -> CONFERENCE in 34d: Conference: BIO International 2026
AKAM  -> EARNINGS in 87d: Q earnings (consensus EPS ~1.60)
```

## Spot-check 3 earnings dates (cross-checked yfinance calendar vs yfinance info)

| Ticker | catalyst_calendar | yfinance.calendar | yfinance.info.earningsTimestampStart |
| --- | --- | --- | --- |
| DOCN | 2026-08-04 | 2026-08-04 | 2026-08-04 |
| AKAM | 2026-08-06 | 2026-08-06 | 2026-08-06 |
| BKNG | 2026-07-29 | 2026-07-29 | 2026-07-29 |

All three match across both yfinance fields (analyst-estimate vendors).

## Coverage

- Total candidates: **154**
- Tickers with ≥1 catalyst: **77 (50.0%)**
- Total catalyst rows: **88** (75 earnings + 13 conferences)

The 50% gap is mostly:
- ETFs (SLV) — no earnings calendar
- Tickers with no earnings within the next 90d window
- Tickers that yfinance returns 404 for (delisted/non-US OTC)

## Constraints honored

- 60 minutes — single sitting.
- Free sources only (yfinance, FDA RSS, static conference list).
- Cache per-ticker.
- FDA / court data treated as best-effort: FDA RSS is included but court
  data is deferred.

## Open follow-ups (not blocking F2)

- Add Q3/Q4 conference dates as the year progresses.
- Wire `why_now_tag` into the report renderer (F-something downstream).
- Consider adding ex-dividend dates for income-strategy tickers.
- Court / SEC enforcement deadlines via `SEC.gov/litigation` RSS.
