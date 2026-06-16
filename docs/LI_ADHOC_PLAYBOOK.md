---
doc: li-adhoc-playbook
status: canonical
updated: 2026-06-11
owner: Ryu / ATLAS
---

# L&I Scoring & Ad-Hoc Analysis — Playbook

The reference for any L&I scoring or ad-hoc "should we file / launch these tickers"
request. Read this first so no request starts from a blank sheet. Companion to the
sent **T-REX Recommendations report** (`screener/li_engine/analysis/trex_combined_v9.py`).

## 1. The canonical score — one system, everything uses it

**`li_engine_daily.final_score`, weights `v1.0.1`.** This is the absolute-latest scorer
and the one with the **historical data we've been accruing** (one row per ticker per
`run_date`, persisted daily by the scheduled scorer — see tasks "daily score persistence").

- **A stock scores the same whether or not REX has filed on it.** Filing status is NOT
  an input to the score. Never show a different number for a filed vs unfiled underlier.
- Exact formula (`run_v1.py`), clipped 0–100:
  **Attention (≤34) + Liquidity (≤25) + Theme (≤20) + Momentum (≤12) + Volatility (≤9) + Race (≤~4) − SI penalty (≤8)**
  - Attention = percentile of 24h social mentions (floor 5 mentions, below = 0)
  - Liquidity = percentile of log(turnover) = ADV × price
  - Theme = hot theme 20 · regular thematic 10 · untagged 0 (direct award)
  - Momentum = percentile of 1-year total return
  - Volatility = percentile of 90-day realized vol (higher = better, L&I edge)
  - Race = (competitor filing count, capped 3 / 3) × ~4 — small filing-race bonus
  - SI penalty = scales above the short-interest median, up to −8
  - NOTE: REX's own filing never moves the score (filing-agnostic holds), but **competitor
    race activity nudges it slightly** via the Race bucket. To make it 100% landscape-independent,
    drop the Race term — open decision.
- Display the **RAW score** (see §4 Display conventions, 2026-06-12) — real spread, sorts cleanly. Percentile compresses the top (70 names tie at 99–100), so it is no longer the display default.
- History: query `li_engine_daily` across `run_date`s; display percentile-per-day so values are comparable.

```sql
SELECT ticker, final_score FROM li_engine_daily
WHERE run_date = (SELECT MAX(run_date) FROM li_engine_daily);
```

### Known inconsistency (migration gap — do NOT propagate)
The production T-REX report's **whitespace ranking (Sections 1/3) still reads
`whitespace_v4.parquet`**, a *different* scorer (different weights + universe). It gives
materially different numbers (e.g. FLNC 99.6 there vs 95.9 on li_engine_daily; rank order
even flips). **Canonical is li_engine_daily v1.0.1.** The report's whitespace sections
should be migrated onto li_engine_daily; until then, ad-hoc work uses li_engine_daily for
*all* tickers and ignores whitespace_v2/v3/v4 (those parquets are legacy, flagged for retirement).

## 2. REX position & competitor data (reuse the report's loaders — never reinvent)

From `trex_combined_v9`:
- `load_rex_position()` → underlier → **Live** (we trade it) / **Filed** (in registration) / **Not in**.
- `load_underlier_competition()` → underlier → # distinct non-REX filers + earliest effective date.
- `load_underlier_live()` → underlier → top live competitor AUM, has_long, has_inv.

Per-competitor **filing timeline** (for the ad-hoc table): parse `fund_status`
(fund_name → issuer via `_comp_issuer_of`, underlier via `_comp_underlier_of`, with
`status` + `effective_date`); join live AUM/inception from `mkt_master_data`
(`primary_category='LI'`, `market_status='ACTV'`, `is_rex=0`, long).

## 3. Conventions

- **Single-stock scope.** Baskets / sector ETNs / commodities are excluded throughout.
- **Dormant rule.** A Filed/Effective filing whose projected effective date is >183 days
  past and still unlaunched = abandoned shelf filing → excluded from launch candidates.
- **Already-launched.** Never list an underlier in a "to launch" set if REX already trades
  a product on it (rex_pos == Live). A higher-leverage extension being filed does not change that.
- **Naming.** Products are named inconsistently (live 2x "T-REX 2X Long NVIDIA" → NVDA;
  filed 4x "...NVDA"). Resolve via `mkt_master_data.is_singlestock`/`map_li_underlier`, not name-parsing.

## 4. The "T-REX Recommendation Brief" (the scoped ad-hoc deliverable)

A **summarized version of the T-REX Recommendation System scoped to a requested review list.**
HTML, T-REX style (navy header), NOT Excel. No methodology footer / disclaimer.
Generator: `screener/li_engine/analysis/recommendation_brief.py`. Reviewed tickers are
highlighted; each section also surfaces the system's top picks. Under each name, list every
competitor filer (issuer · status · effective date · live AUM) — the filing timeline.

### Verdict taxonomy (one score, the verdict is what changes)

| Section | Condition | Verdict |
|---|---|---|
| **1 · Recommend to File** | No live **long** product anywhere (REX filed or not — irrelevant), scores well | **File** — ranked by score |
| **2 · Live, ≤2 Competitors** | 1–2 live competitor longs, ordered by **total** live AUM, with track record (AUM now / 1mo / 3mo). >2 competitors are NOT shown here. | If REX **filed** AND demand bar met → **Launch anyway**. Else → **facts only, no call** |
| **3 · Inverse Opportunities** | A live long exists (total ≥ ~$50M) but **no live inverse** anywhere | **Always recommend — file the inverse** |
| **4 · Filed, Sole Filer** | REX filed AND zero other issuers filed | **Launch on your timing** (catalyst — no competitor pressure) |

- **Demand bar (Section 2):** measured on **total** live AUM across all competitors (not the
  largest): proven if 1-month flow ≥ +$25M **or** total ≥ $75M. Show the trajectory as track record.
- Score for every row = `li_engine_daily` v1.0.1 (§1), identical regardless of REX filing status.

### Display conventions (Ryu, 2026-06-12)
- **Show the RAW li_engine score, not the percentile.** The percentile compresses (70 names tie at 99–100) and sorts flat; raw (0–~90, median 16, max ~87) has real spread and orders cleanly. Sort tables by raw score descending.
- **Status is Filed or Effective only** — never Pending/Delayed. Map: Pending/Delayed/Filed → Filed; Active/Listed/Effective → Effective. (NB: the main report deliberately keeps granular status per a 2026-06-09 call — this binary is for the ad-hoc views only. The original "no Pending/Delayed" ask was an Atlas_Hub chat, not recorded here until now.)
- Live section = **exactly 1** live competitor (cleanest proven-demand); inverse section shows the **inverse filing race** (T-REX + competitors).

### Foreign & Pre-IPO Competition view (`foreign_ipo_brief.py`)
- Two sections only: **Foreign Stocks** + **Pre-IPO (private only)**. No "recently priced" section.
- **Data-driven universe — NOT a curated seed.** Build the name list from what's actually been filed: REX from `rex_products` (authoritative for our status), competitors from `fund_status`. A curated seed/yaml WILL miss names (Viva Republica, Hanwha, Metaplanet, Hyundai, Discord, Quantinuum, Figure were all missing until 2026-06-12). Pull REX status from `rex_products` directly so a name we filed never shows "Not filed". Match on the SHORT name (fund names say "Anduril", not "Anduril Industries"), space-insensitive.
- **Foreign-LISTED only — exclude US-ADRs.** A company with a LIQUID exchange-listed US ADR (TSM, SONY, TM, ASML, JD, BABA, ARM…) is already US-tradeable as a normal 2x underlier — exclude it. Companies with only OTC pink-sheet ADRs (Nintendo NTDOY, SoftBank SFTBY, Tencent TCEHY, BYD BYDDY — illiquid, no options) are kept: the foreign listing is the only viable underlier.
- **Pre-IPO = genuinely private only.** Drop any name already public (detected via presence in the `mkt_stock_data` equity universe — e.g. Klarna, xAI). Valuations come from `ipo_watchlist.yaml` with their **as-of date + source** — never modeled/invented.

## 5. T-REX report anatomy (`trex_combined_v9` → `scripts/send_all.py`)

1. Whitespace ranking (top underliers, no live product) — *uses whitespace_v4, see §1 gap*
2. Pipeline — T-REX 2X filed-not-launched, ranked by li_engine_daily underlier_score
3. Whitespace top 100
4. Inverse gap — long exists, no inverse
5. **Underliers REX Should Enter** — 1–2 competitor longs, REX not in, top AUM >$100M
6. Foreign megacap underliers
7. Pre-IPO watchlist & recent IPOs (YAML-backed)
8. T-REX delisting watch (<$15M @ 6mo+)
9. Scoring track record (flow backtest)

## 6. Quick reference

| Need | Source |
|---|---|
| Canonical score + history | `li_engine_daily` (final_score, pillar_scores_json, signal_values_json) v1.0.1 |
| REX position | `trex_combined_v9.load_rex_position()` |
| Competitor filers + dates | `fund_status` + `load_underlier_competition()` |
| Live competitor AUM | `mkt_master_data` (ACTV, is_rex=0) + `load_underlier_live()` |
| Our filings & status | `rex_products` (note: status can lag; cross-check live table) |
| Ad-hoc HTML generator | `screener/li_engine/analysis/adhoc_html.py` |
| DB | `data/etp_tracker.db` (VPS `/home/jarvis/rexfinhub/`) |
