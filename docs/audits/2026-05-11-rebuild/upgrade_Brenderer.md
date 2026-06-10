# Wave B-renderer — Stock Recs v3 Renderer Rebuild

**Branch:** `audit-stockrecs-Brenderer`
**Owner file:** `screener/li_engine/analysis/weekly_v2_report.py`
**Layout flag:** `LAYOUT_VERSION = "v3"` (constant near top of module)
**Render artifact:** `outputs/previews/stock_recs_v3_preview.html`

---

## Card Spec

Per-ticker decision card — single email-table block, 4-panel grid, dark footer.

| Region | Contents |
|---|---|
| Header | Ticker + company name + sector subtitle, signal-strength chip, tier badge |
| Top-left panel (Thesis) | 2 paragraphs from `data/weekly_theses/<date>.json`. Falls back to "Thesis pending — `<company_line>`" when JSON missing or ticker absent |
| Top-right panel (Signals) | composite_score breakdown — top 6 signal contributors as horizontal bars (z-scores). Includes the absolute composite_score and percentile in the panel sub-header |
| Bottom-left panel (Competition) | REX filings count, competitor 2x-active count, competitor 485APOS-180d count, earliest-filer name+date, filing-race rank |
| Bottom-right panel (Risks) | Risk-flag chip row. "No flags" italic placeholder when clean |
| Footer (dark) | Suggested REX vehicle ticker + filing status badge (FILED / DRAFT / NONE) |

The card model dict is built by `_build_card_model()` and rendered by
`_render_card_v3()` so card composition stays separate from HTML markup —
future tweaks to copy/colors won't touch business logic.

---

## Tier Engine

Constants:
```python
TIER_HIGH_PCT   = 85
TIER_MEDIUM_PCT = 60
TIER_WATCH_PCT  = 40
```

Percentiles are computed once per render against the full whitespace
universe (`whitespace_v4.parquet`), passed in as a `percentiles` dict of
absolute composite_score thresholds.

```
HIGH    = score >= P85  AND  signal_strength in {STRONG, URGENT}  AND  no killer risk
MEDIUM  = score >= P60  AND  signal_strength in {MODERATE, STRONG, URGENT}
WATCH   = score >= P40  OR   ticker is a new entrant (vs prior-week recs)
DROP    = anything else (excluded from cards)
```

Signal strength is derived from the z-score components:
- **URGENT**: top z >= 2.5 AND >= 3 components z-mag >= 1.5
- **STRONG**: top z >= 2.0 AND >= 2 components z-mag >= 1.5
- **MODERATE**: top z >= 1.0
- **WEAK**: otherwise

A "killer risk" is any of `regulatory` or `momentum-fade`. These block the
HIGH tier even when score+signal qualify (preserves the rule that
flagged names cannot be top-of-list recommendations).

This week's run:
**17 HIGH · 7 MEDIUM · 8 WATCH · 3 KILLED** (32 cards total)

---

## Section Logic

Cards are bucketed by orientation × tier:

| Section | Filter | Sort | Color accent |
|---|---|---|---|
| Defensive — Should We Respond? | `orientation == DEFENSIVE` AND `tier in {HIGH, MEDIUM}` | score desc | `#e74c3c` |
| Offensive — Whitespace Shots | `orientation == OFFENSIVE` AND `tier in {HIGH, MEDIUM}` | score desc | `#27ae60` |
| Watch — Early Signals | `tier == WATCH` | score desc | `#0984e3` |
| Killed — Decayed Out | tickers in prior-week thesis JSON / `recommendation_history`, no longer in current rec set | alphabetical | `#95a5a6` |

**Orientation engine** (`_classify_orientation`):
- **DEFENSIVE**: a competitor filed within `DEFENSIVE_LOOKBACK_DAYS` (= 30) AND REX has `n_rex_filed_any == 0`. Read literally from `load_earliest_competitor_filing_dates()`.
- **OFFENSIVE**: anything else. Whitespace where REX should file first.

**Killed list** — `_build_killed_list`:
1. First tries to read from `recommendation_history` SQLite table (Wave E1 deliverable). If missing, the function logs an info message and falls back to step 2.
2. Otherwise scans `data/weekly_theses/*.json`, picks the most recent file at least 5 days old, and uses that ticker set as the prior-week baseline.
3. Each prior-week ticker absent from the current `{HIGH, MEDIUM, WATCH}` tier set is added with a one-line decay reason citing the new percentile / score (or "Dropped from universe" if no signal data exists).

The killed section is capped at 10 entries to keep the email tight.

---

## Risk-Flag Taxonomy

| Token | Label | Trigger |
|---|---|---|
| `capacity` | Capacity (<$1B mcap) | `market_cap < 1000` ($M) |
| `liquidity` | Liquidity (<$5M ADV) | `adv_30d * last_price < 5_000_000` |
| `single-name` | Single-Name | Fund / company name does not match index/etf/composite/sector regex (i.e. it is an idiosyncratic single-stock exposure — every whitespace candidate by definition) |
| `regulatory` | Regulatory | Sector/name/fund-name string contains any of: cannabis, marijuana, gambling, gaming, crypto-mining, bitcoin mining, casino |
| `momentum-fade` | Momentum Fade (-30% WoW) | Composite score dropped > 30% week-over-week (requires `prior_score` from history table — currently passed as `None` so this fires only when E1 lands) |

Two of these (`regulatory`, `momentum-fade`) are "killer risks" that downgrade a HIGH-tier card to MEDIUM regardless of score.

Renderer maps tokens → `(color, label)` via `_RISK_CHIP` dict. Adding a
new flag = one constant + one entry in `_derive_risk_flags`.

---

## Suggested REX Ticker

`_suggested_rex_ticker(ticker, row, launch_lookup)` returns `(symbol, status)`:

1. If the ticker has an entry in `launch_candidates.parquet` (REX has filed), use `rex_ticker` and map `rex_market_status` → status badge:
   - `FILED` / `EFFECTIVE` / `REGISTERED` → `FILED` (green)
   - anything else → `DRAFT` (amber)
2. Otherwise synthesise `(prospective) 2X <TICKER>` with `NONE` status (grey).

This keeps the recommendation actionable — if REX has paperwork in the queue, the card surfaces the existing ticker; otherwise it shows what we'd file.

---

## Graceful Degradation

The v3 path is wrapped in a `try/except` inside `main()`. On any exception
the function logs a warning and falls through to the v2 renderer (the
original `render()`). Inputs that can be missing without breaking v3:

- `data/weekly_theses/*.json` — cards show "Thesis pending" placeholder
- `recommendation_history` table — Killed list falls back to thesis-history scan; if that is also empty, Killed section says "Nothing decayed out of last week's recs."
- Any individual signal column — `_signal_breakdown` skips columns that are NaN or non-numeric.
- Config YAML overrides — handled by existing `_load_yaml_overrides` (untouched).

`LAYOUT_VERSION = "v2"` at the top of the module forces the legacy
renderer (verified working — 82 KB output, identical to pre-rebuild).

---

## Verification (this run)

| Check | Result |
|---|---|
| Cards rendered | 32 (≥ 5 required) |
| Defensive section present | yes |
| Offensive section present | yes |
| Watch section present | yes (8 cards) |
| Killed section present | yes (3 cards including synthetic ZZZX_DELISTED smoke test) |
| Tier badges (HIGH / MEDIUM / WATCH) all visible | yes |
| Risk chips visible | Capacity + Single-Name observed; Liquidity flag correctly absent (top-of-universe names all ADV-clean) |
| Thesis paragraphs from JSON | yes — RXT and LWLG show curated text |
| Suggested REX vehicle footer | yes |
| Filing status badges (NONE / DRAFT / FILED) | yes |
| Output file size | 297 KB (vs 82 KB v2 baseline) |
| Saved to canonical preview path | `outputs/previews/stock_recs_v3_preview.html` |
| v2 fallback still works | yes — verified by toggling `LAYOUT_VERSION = "v2"` |
| v3 with missing thesis dir | yes — graceful degradation, "Thesis pending" placeholders shown |
