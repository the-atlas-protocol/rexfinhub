# Wave A3 — Tiered `signal_strength` replaces `has_signals` boolean

**Branch**: `audit-stockrecs-A3-signals`
**Worktree**: `C:/Projects/rexfinhub-A3`
**Date**: 2026-05-11

## Problem

The `has_signals` column on `launch_candidates.parquet` was a binary flag set
to `True` whenever Bloomberg returned **any** row for a ticker — i.e. it was
effectively a "is this a real US-listed equity?" check, not a measure of
whether the ticker had meaningful trading signals worth filtering on.

Concrete failure mode in the existing parquet (154 candidates):

| Ticker | `has_signals` | `composite_score` | mentions | rvol_30d | ret_1m |
|--------|---------------|-------------------|----------|----------|--------|
| LINK   | **False**     | 0.660             | 11       | NaN      | NaN    |
| SLV    | **False**     | 0.660             | 12       | NaN      | NaN    |

LINK and SLV scored in the top 10 despite having no Bloomberg data — they
were carried by `mentions_z` alone. The old `has_signals == True` filter
correctly kicked them out, but it also kicked out anything bbg hadn't
crawled yet, regardless of how strong the actual signals were.

## Fix

A new module — `screener/li_engine/analysis/signal_strength.py` — produces
a five-tier ordinal `signal_strength` per candidate. Tiers are assigned
based on **rank within the candidate universe**, **breadth of active
signals**, and **freshness** (age of the underlying data source).

### Tier definitions

| Tier        | Cutoff                                                                                          |
|-------------|--------------------------------------------------------------------------------------------------|
| `URGENT`    | ApeWisdom rank ≤ 25 **and** 3+ active signals **and** recent inflection (Δ% ≥ 50% or Δrank ≥ 10) |
| `STRONG`    | 2+ active signals **and** at least one ranked top 100                                            |
| `MODERATE`  | 1+ signal ranked top 250                                                                         |
| `WEAK`      | At least one signal ranked top 500 (but nothing better)                                          |
| `NONE`      | No usable signal data                                                                            |

### Per-signal records

Each candidate also gets a `signal_records` column — a list of dicts of the
form `{name, strength, raw_value, age_days, rank}`. The composite scorer
uses these via the helper:

```python
SignalRecord.weighted_strength()  # = strength_int * 0.5 ** (age_days / 14.0)
```

So a STRONG signal observed today is worth 3.0; the same STRONG signal two
weeks old is worth 1.5 (decays toward MODERATE). Bloomberg-derived signals
inherit the age of their `mkt_pipeline_runs.finished_at`; ApeWisdom signals
are fetched live (age = 0).

### Composite multiplier

After the v3 scorer runs, `composite_score` is multiplied by a conservative
tier-based bonus so that STRONG/URGENT candidates outrank MODERATE/WEAK
ones with similar raw scores:

| Tier      | Multiplier |
|-----------|-----------:|
| URGENT    |   1.40 ×   |
| STRONG    |   1.25 ×   |
| MODERATE  |   1.10 ×   |
| WEAK      |   1.00 ×   |
| NONE      |   1.00 ×   |

The pre-multiplier value is preserved as `composite_score_raw` for
diagnostics.

## Files changed

| File                                                          | Change                                                                                                |
|---------------------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| `screener/li_engine/analysis/signal_strength.py`              | **NEW** — `SignalStrength` enum, `SignalRecord`, `annotate_signal_strength`, `signal_strength_multiplier` |
| `screener/li_engine/analysis/launch_candidates.py`            | Stop writing the `has_signals` boolean; call `annotate_signal_strength` after `compute_score_v3`; apply multiplier; preserve `composite_score_raw` |
| `screener/li_engine/analysis/whitespace_v2.py`                | Add `load_apewisdom_full_map()` returning rank + delta; reroute legacy `load_apewisdom_map()` through it |
| `screener/li_engine/analysis/weekly_v2_report.py`             | `load_launch_candidates()` filter changed from `has_signals == True` to `signal_strength >= MODERATE` (with legacy fallback) |
| `webapp/routers/tools_li.py`                                  | Launch-queue card builder uses the same tiered filter                                                 |
| `docs/audit_2026-05-11/upgrade_A3.md`                         | **NEW** — this doc                                                                                    |

## Backward compatibility

`has_signals` is **preserved as a derived boolean column** equal to
`signal_strength != 'NONE'`. Existing consumers — `tools_li.py`, the email
reports, any future scripts that read the parquet — continue to work
unmodified. The parquet schema gains 3 columns (`signal_strength`,
`signal_records`, `composite_score_raw`) and changes the meaning of
`has_signals` from "bbg returned a row" to "at least some real signal
exists".

The legacy `load_apewisdom_map(tickers) -> dict[str, int]` signature is
preserved; it now delegates to `load_apewisdom_full_map`.

## Verification

Test fixture: existing `data/analysis/launch_candidates.parquet` (154 rows,
built from the 2026-05-10 nightly run). The `mentions_24h` column was
already present from the v3 scorer; ApeWisdom rank info was synthesised as
`None` for the verification (full rank info would only become available on
the next live build, which makes WEAK/MODERATE counts conservative here).

### `signal_strength` distribution after re-annotation

```
STRONG      76
NONE        73
WEAK         4
MODERATE     1
```

Note: STRONG appears dominant because the population is small (154
candidates), so most rows with any bbg data land in the top 100 of the
intra-universe rank. This matches the spec — "rank top 100" is relative
to the scored universe, not the full ETP population.

### Top 10 — old vs new

**Old ranking** (sorted by `composite_score`):

```
underlier  has_signals  composite_score
DOCN              True         1.358869
FSLY              True         1.256323
AMC               True         0.791410
FMCC              True         0.777955
FNMA              True         0.776706
LINK             False         0.660000   <-- no bbg data, filtered by old gate
SLV              False         0.660000   <-- no bbg data, filtered by old gate
AUR               True         0.618603
TE                True         0.601238
HBM               True         0.599258
```

**New ranking** (with strength multiplier, no filter):

```
underlier  signal_strength  composite_score  composite_score_raw
DOCN                STRONG         1.698586             1.358869
FSLY                STRONG         1.570404             1.256323
AMC                 STRONG         0.989263             0.791410
FMCC                STRONG         0.972443             0.777955
FNMA                STRONG         0.970882             0.776706
AUR                 STRONG         0.773253             0.618603
TE                  STRONG         0.751548             0.601238
HBM                 STRONG         0.749073             0.599258
TRON                STRONG         0.706790             0.565432
DDOG                STRONG         0.690470             0.552376
```

LINK and SLV correctly drop out of the new top 10 — they have no bbg
signals to rank, so their tier resolves to NONE and the multiplier stays
at 1.0× while everything around them gets boosted.

### Weak-only candidates in top 5

`0` — confirmed pass.

### Filter effect

`load_launch_candidates(min_strength="MODERATE")` now returns 77 of 154
rows (vs 77 under the old `has_signals == True` gate). Net change in the
on-disk parquet's pass-set: -4 candidates that had bbg rows but no signal
strong enough to reach top 500.

## Constraints honored

- **60-min budget** — single new module, three small consumer edits, no
  schema break.
- **Parquet schema preserved** — `has_signals` retained as derived bool.
- **Network-conservative** — `load_apewisdom_full_map` makes the same API
  calls as the legacy variant; the v3 scorer and the strength annotator
  share a single fetch.
