# Upgrade A2 — Stock Recs time-decay layer

Branch: `audit-stockrecs-A2-decay`
Source: Wave A2 of full-scale Stock Recs upgrade.
Status: implemented + verified. AMC composite drops 95.7% (0.791 → 0.034) once
its 600-day-old REX filing is decayed.

## Problem

Stock Recs treated every signal as equally fresh. The AMC card on the L&I
report showed `Closest effective date: 2025-10-24` — eight months in the past
with no follow-up amendment from REX — and AMC still ranked above tickers
with active filings. There was no concept of:

1. Mention staleness — ApeWisdom's 24h count is meaningless if our last
   fetch was a month ago.
2. Filing staleness — a REX filing from Sept 2024 with no amendments since
   is functionally a dead lead, not "in progress".
3. Competitor recency — a competitor 485APOS from 12 months ago does not
   represent the same competitive pressure as one from last week.

## Files changed (3, sole-owner scope)

| File | Δ | Purpose |
|---|---|---|
| `screener/li_engine/signals.py` | +98 LoC | Decay constants + 3 helper fns: `apply_mention_decay`, `rex_filing_decay_factor`, `competitor_filing_recency_weight`. Single source of truth for tuning. Also stamps `mentions_fetched_at` on sentiment loads. |
| `screener/li_engine/analysis/whitespace_v3.py` | +50 LoC | Imports decay primitives, applies mention-batch age decay, emits `decay_factor` + `composite_score_pre_decay` columns. Filing-decay placeholders default to 1.0 (no filing context at whitespace stage). |
| `screener/li_engine/analysis/launch_candidates.py` | +95 LoC | New `load_filing_dates_for_underliers()` queries per-underlier last-REX/last-competitor filing dates. After v3 scoring, overrides decay placeholders with real per-underlier values, recomputes final composite, emits `is_stale_filing` + `display_effective_label` for the report layer. |

## Decay model

```
decay_factor = mention_decay × rex_filing_decay × competitor_filing_decay
composite_score = composite_score_pre_decay × decay_factor
```

### Mention decay (exponential, half-life)

Within `MENTION_FRESH_DAYS=14` of the fetch: no decay.
Beyond it: `0.5 ** ((age_days - 14) / 7)`.

| Mention age | Decay factor |
|---|---|
| 0d (fresh fetch) | 1.000 |
| 14d | 1.000 (still fresh) |
| 21d | 0.500 (one half-life past fresh) |
| 28d | 0.250 |
| 35d | 0.125 |

Fresh API runs (the normal case) get decay 1.0, so the existing pipeline is
unchanged in practice. The decay activates if mentions are persisted and
re-consumed days later, or when ApeWisdom is offline and we fall back to a
cached map.

### REX filing decay (step / cliff)

Step penalty matches how a PM mentally writes off a stale idea — there is no
"slowly fading" of a 90-day-old filing, it just becomes deprioritised.

| Days since last REX filing | Decay factor |
|---|---|
| < 90d | 1.00 (still active) |
| 90d–180d | 0.50 (STALE) |
| > 180d | 0.20 (DEAD) |

### Competitor filing decay (linear, 180-day audit window)

Per competitor filing, weight = `1 - (age_days / 180)` for ages in [0, 180];
zero outside the window. Per underlier, the `competitor_filing_decay`
multiplier is the **mean** of the in-window weights — so an underlier with
fresh comp filings gets a multiplier near 1.0, one with only 6-month-old
filings gets ~0.0, one with no in-window filings gets 1.0 (no recent
pressure → no penalty).

## Verification — 5 sample tickers

Run: `python -m screener.li_engine.analysis.launch_candidates`
Run date: 2026-05-11

| Ticker | Pre-decay | Post-decay | Decay factor | Days since REX filing | Stale? | Display label |
|---|---|---|---|---|---|---|
| **AMC** | 0.791 | **0.034** | **0.043** | 600 | YES | `2024-09-10 (~DEAD — 600d since last REX filing, no follow-up)` |
| UI | 0.963 | 0.963 | 1.000 | 12 | no | `2026-01-12` |
| DOCN | 0.710 | 0.710 | 1.000 | 33 | no | — (no closest_effective_date) |
| LINK | 0.660 | **0.330** | **0.500** | 104 | YES | `2025-11-14 (~STALE — 104d since last REX filing, no follow-up)` |
| FMCC | 0.573 | **0.287** | **0.500** | 91 | YES | `2025-12-19 (~STALE — 91d since last REX filing, no follow-up)` |

### AMC drill-down — every decay component

```
composite_score_pre_decay     0.7914
composite_score (post-decay)  0.0343
decay_factor                  0.0433
  mention_decay               1.000  (live ApeWisdom fetch — fresh)
  rex_filing_decay            0.200  (600d > REX_FILING_DEAD_DAYS=180)
  competitor_filing_decay     0.217  (mean recency of 2 in-window comp filings)
days_since_rex_filing         600    (last filing 2024-09-19)
days_since_competitor_filing  141    (last comp filing 2025-12-22)
n_comp_filings_180d           2
last_rex_filing_date          2024-09-19
last_comp_filing_date         2025-12-22
closest_effective_date_raw    2024-09-10
display_effective_label       2024-09-10 (~DEAD — 600d since last REX filing, no follow-up)
is_stale_filing               True
```

AMC's score drops 95.7%. It now ranks below every active candidate, which is
the correct outcome — the desk hasn't filed anything new on AMC in nearly two
years.

### Population-level impact

```
Stale (>90d since last REX filing): 45 of 154 candidates
```

Roughly a third of all current launch candidates get demoted by either the
50% (STALE) or 80% (DEAD) penalty. Several previously-top-ranked tickers fall
out of the visible top 15.

## Surfacing in report (Task 5)

Per Wave A2 scope (`ONLY your 3 files`), the report template change itself is
out of scope — but I emitted everything the report needs:

- `display_effective_label` is a fully pre-formatted string the report can
  drop in directly, replacing its current inline `closest_effective_date`
  rendering. Includes `(~STALE — Nd ago, no follow-up)` /
  `(~DEAD — Nd ago, no follow-up)` annotations.
- `is_stale_filing` is a bool the report can use to apply
  `text-decoration: line-through` styling.
- `days_since_rex_filing`, `decay_factor`, `composite_score_pre_decay`
  columns let the report show "demoted from #N to #M".

The report-side template tweak (1-line change in
`screener/li_engine/analysis/weekly_v2_report.py::_section_card`) is left
for the Wave that owns the report layer — flagged here so it doesn't get
lost.

## Tuning

All decay constants live at the top of `screener/li_engine/signals.py`:

```python
MENTION_FRESH_DAYS = 14
MENTION_HALFLIFE_DAYS = 7.0
REX_FILING_STALE_DAYS = 90
REX_FILING_DEAD_DAYS = 180
REX_FILING_STALE_FACTOR = 0.50
REX_FILING_DEAD_FACTOR = 0.20
COMPETITOR_AUDIT_DAYS = 180
```

Both `whitespace_v3.py` and `launch_candidates.py` import these — no
duplication, no drift.

## What the parquet now exposes

`launch_candidates.parquet` adds these columns:

| Column | Type | Meaning |
|---|---|---|
| `composite_score_pre_decay` | float | What the score would be without time-decay (for transparency / before-after diff) |
| `composite_score` | float | Final score, post-decay (replaces old composite) |
| `decay_factor` | float | Product of all three decay components |
| `mention_decay` | float | Mention-batch staleness multiplier |
| `rex_filing_decay` | float | REX-filing staleness multiplier (step) |
| `competitor_filing_decay` | float | Competitor-filing recency multiplier (linear avg) |
| `days_since_rex_filing` | int | Days since most recent REX filing on this underlier |
| `days_since_competitor_filing` | float | Days since most recent competitor filing |
| `is_stale_filing` | bool | True if `days_since_rex_filing >= 90` |
| `display_effective_label` | str/None | Pre-formatted "Closest effective date" string with STALE/DEAD annotation |
| `last_rex_filing_date` | datetime | For audit trail |
| `last_comp_filing_date` | datetime | For audit trail |
| `closest_effective_date_raw` | datetime | The bare date the report previously showed |
| `n_comp_filings_180d` | int | Count of competitor filings within audit window |

`whitespace_v3.parquet` adds the same set, with filing-related columns set
to `NaN`/`1.0`/`False` (no filing context at the whitespace stage).
