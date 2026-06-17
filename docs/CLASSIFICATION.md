---
doc: classification
status: canonical
updated: 2026-06-10
---

# CLASSIFICATION — the FULL-SCALE taxonomy contract

> **CANONICAL POINTER (2026-06-16):** the single source of truth for internal
> suites, market-status KPI rules, and trusts is now **`docs/DEFINITIONS.md` +
> `market/definitions.py`**. This file remains the detailed
> external-taxonomy / rules reference that complements it — read `DEFINITIONS.md`
> first for any suite/status/trust question.

> Every ETP in the universe gets a complete multi-dimensional classification —
> not just the five REX-tracked strategy buckets. This is the contract the
> autonomous engine is held to. Design lineage:
> `C:\Projects\rexfinhub\docs\raw\legacy\CLASSIFICATION_SYSTEM_PLAN.md` (the locked plan).
> Engine: `C:\Projects\rexfinhub\scripts\classify_daily.py` — runs **inside the
> 17:15/21:00 Bloomberg chain immediately after the data lands** (and 09:00 as
> catch-up + report).

## The two layers (and which one is THE system)

**FULL-SCALE (the system): the 3-axis taxonomy + attributes.**
Master: `C:\Projects\rexfinhub\config\rules\fund_master.csv` (full universe,
one row per ticker, 28 columns). DB home: 23 columns on `mkt_master_data`
(`C:\Projects\rexfinhub\webapp\models.py:474-496`), restamped from the CSV by
`C:\Projects\rexfinhub\scripts\apply_fund_master.py` right after every Bloomberg
sync (the sync's full-snapshot replace wipes them — the restamp is structurally
mandatory, never optional).

**LEGACY (a derived projection): the 5 tracked categories.**
`config/rules/fund_mapping.csv` → `mkt_master_data.etp_category`
(LI/CC/Crypto/Defined/Thematic) + `attributes_*.csv` → `map_*` columns.
Still what every money page/report reads today (verified: report_data.py,
downloads.py, api.py, holdings_intel.py, cboe cross_reference, the entire
screener/li_engine universe). The engine maintains BOTH from one decision —
legacy keeps the reports working; fund_master is the truth the system
converges on. Cutover of readers happens consumer-by-consumer (see §Migration).

## Axis 1 — asset_class (what the fund OWNS)

`Equity · Fixed Income · Multi-Asset · Commodity · Crypto · Volatility · Currency`

## Axis 2 — primary_strategy (what the fund DOES)

| Value | Meaning |
|---|---|
| `Plain Beta` | Unlevered exposure, broad or narrow — the default for most ETPs |
| `L&I` | Leveraged (≥1.25x) or inverse exposure, daily-reset or target — REX's T-REX/MicroSectors battleground |
| `Income` | Option/structured income generation (covered call, put-write, 0DTE/weekly-pay, autocallable…) |
| `Defined Outcome` | Buffer/floor/accelerator/barrier structured outcomes |
| `Risk Mgmt` | Hedged equity, tail risk, trend/managed futures, risk-adaptive |

## Axis 3 — sub_strategy (the refinement; hierarchical with `>`)

Observed vocabulary: Broad · Style · Sector · Thematic · Single-Access ·
Allocation · Growth · Long · Short · Buffer · Floor · Dual Directional ·
Hedged Equity · Tail Risk · Trend/Managed Futures · Risk-Adaptive · VIX ·
Bond Ladder · Box Spread · Alternatives · Alternative Income ·
Event-Driven/Merger Arb · `Derivative Income > Covered Call` ·
`Derivative Income > Put-Write` · `Derivative Income > 0DTE / Weekly-Pay` ·
`Structured Product Income > Autocallable` · Single-Currency · Deferred Income

## The attribute columns (orthogonal, per-fund)

| Column | Vocabulary / type | Populated today |
|---|---|---|
| `concentration` | single \| basket | L&I/CC singles |
| `underlier_name` | direct underlier (ticker or named asset) | L&I/CC/Crypto |
| `underlier_is_wrapper` / `root_underlier_name` / `wrapper_type` | wrapper resolution: when the underlier is itself a fund, resolve the root economic exposure (e.g. CC on a 2x NVDA ETF → root NVDA). wrapper_type: standalone \| fund_of_funds \| laddered \| synthetic \| feeder | **designed, 0% — backfill campaign** |
| `mechanism` | physical \| swap \| futures \| options \| structured_note \| synthetic | 100% |
| `leverage_ratio` / `direction` / `reset_period` | float / long \| short \| neutral / daily \| weekly \| monthly \| none | L&I rows |
| `distribution_freq` | weekly \| monthly \| quarterly… | **0% — backfill** |
| `outcome_period_months` / `cap_pct` / `buffer_pct` / `accelerator_multiplier` / `barrier_pct` | Defined-Outcome/autocall economics | partial (Defined) |
| `region` | US \| DM-ex-US \| EM \| EMEA \| APAC \| LatAm \| country | **0% — backfill** |
| `duration_bucket` | ultra_short \| short \| intermediate \| long \| ultra_long | **0% — backfill (Fixed Income)** |
| `credit_quality` | treasury \| ig \| hy \| junk \| muni \| mixed | **0% — backfill (Fixed Income)** |
| `tax_structure` | 40_act \| grantor_trust \| mlp_k1 \| partnership \| uit | partial |
| `qualified_dividends` | bool | **0% — backfill** |

## The engine — how every fund gets classified

`scripts/classify_daily.py`, running **inside the Bloomberg chain** (17:15 +
21:00, before the `apply_fund_master` restamp) and at 09:00 (catch-up + the
sweep report email + same-morning `apply_fund_master` stamp):

| Tier | Decider | Writes |
|---|---|---|
| 0 | Standing rulings (Ryu, in code — e.g. OBTC out of scope) | exclusions |
| 1 | Rule engine (deterministic Bloomberg-field derivation) | **legacy CSVs + a full 28-col fund_master row** (via `scripts/fund_master_writer.py`, mapping codified in `scripts/build_fund_master_seed.py`) |
| 2 | LLM (Haiku, cached prompt) + **independent critic pass** | same dual write on AGREE+confident; "Other" verdicts get a real **Plain-Beta full-universe row** via the universal rule cascade (`scripts/universal_classify_funds.py`) — Other is not a taxonomy hole |
| 3 | Queue (`ClassificationProposal`) | critic-DISAGREE / LOW only |

Plus: issuer-display registration, CC-attribute fill (the autocall tool's
`attributes_CC.csv`), and a **drift-heal pass** — any ACTV fund carrying a
legacy category but missing its fund_master row gets translated and appended
(this is what backfills the 2026-05-01→06-09 frozen-feeder window).

Every decision journaled: `logs/auto_classify_YYYYMMDD.jsonl`.
`fund_master.csv` is in the data/config mirror set — the 2026-05-01 CSV desync
class is closed. The day's rules delta is **committed and pushed to git**
nightly (`scripts/commit_rules_delta.py`, final chain step).

## Precedence (who wins)

1. `classification_override` DB rows (admin, per-field) — applied by
   `apply_classification_overrides.py` after everything else.
2. `fund_master.csv` curated/engine rows — restamped by `apply_fund_master.py`
   (chain step right after sync).
3. `apply_classification_sweep.py` heuristics — gap-fill only, never overwrites.
4. Known defect (registered): the 19:30 `write_classifications` sync-back can
   overwrite 3-axis values until the 21:00 restamp — ordering-protected today,
   precedence-guard fix on the debt register.

## Migration to full-layer readers

Money paths (reports/exports/API/li_engine) still read legacy `etp_category` +
`map_li_*`. Cutover rules when migrating a consumer: map via
`primary_strategy='L&I'` ↔ `etp_category='LI'` etc.; **direction vocabulary**
is lowercase `long/short` in fund_master (no `Tactical`; ~90% of non-L&I rows
empty by design); report caches are computed BEFORE the restamp (inside
`sync_market_data`) — any cached report that consumes the 23 columns must move
its computation to a post-step first.

### known-gaps
- GAP-01: attribute backfill campaign (region/duration/credit/distribution/
  qualified_dividends/wrapper-resolution ≈ 0%) — LLM batch over the universe.
- GAP-02: money-path readers on legacy projection (by design until cutover).
- GAP-03: db_writer 3-axis sync-back precedence guard.
- GAP-04: legacy proposal-queue triage (1,737 rows incl. 2026-06 additions).
- GAP-05: `mkt_fund_classification` table — stale third encoding, zero
  readers; retire via build-prove-retire.
