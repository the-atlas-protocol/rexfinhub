# Stock Recs Report — Deep Audit + Redesign Spec

Audit date: 2026-05-11. Builder: `screener/li_engine/analysis/weekly_v2_report.py`. Rendered output reviewed: `C:/Users/RyuEl-Asmar/Desktop/audit_2026-05-11_previews/stock_recs.html` (82 KB, identical SHA to `li_weekly_v2_2026-05-11.html`). Entry point: atlas task #127 (AMPX shipped as a launch rec the week Defiance launched AMPU).

## Executive summary

1. **The AMPX bug is a temporal race, not a logic gap.** `launch_candidates.py:200-204` correctly excludes underliers with `competitor_active_long > 0`, but the report reads a frozen `competitor_counts.parquet`. Any competitor launch between parquet refresh and report send slips through. AMPU (2x AMPX) priced 2026-05-06; the May 11 send still listed AMPX.
2. **Composite weights are hand-tuned, not backtested.** `whitespace_v3.WEIGHTS` (`whitespace_v3.py:33-45`) are author-set numbers with comments; there is no IC validation against post-launch AUM in the live engine path. The "data-driven" framing in v2's docstring no longer applies.
3. **The cards are decoration without decision support.** Every card shows the same six metrics in the same row; there is no thesis, no confidence, no recommended timing, and no risk flag. A reader cannot rank within the top 12 without re-doing the work themselves.
4. **Hot themes are a hard-coded set of six** (`whitespace_v3.py:48-51`) that has not been updated for 2026 (no robotics, no GLP-1, no stablecoin). They contribute the largest single weight (`theme_bonus = 0.14 × 3.0 multiplier = 0.42 effective`) and have no decay.
5. **Recommended posture for tomorrow's send:** option (c). One-shot AMPX-class patch (live-product-by-fund-name regex over `mkt_master_data` at render time, not parquet build time), then ship. Defer redesign to Stage 2.

---

## Methodology audit (technical)

### 1. Top 5 to Launch — the AMPX bug class

**What it does.** `weekly_v2_report.py:307-312` reads `launch_candidates.parquet` (built by `launch_candidates.py:184-246`), filters to `has_signals == True`, sorts by `composite_score` descending, takes head(12). The first five tickers are spliced into the "Top 5 to Launch" highlight at line 720.

**Why AMPX-style failures happen.** Two cascading problems:

- **Temporal staleness.** `launch_candidates.py:201-204` reads `competitor_active_long/short` from `competitor_counts.parquet`. That parquet is built by `competitor_counts.py`, which queries `mkt_master_data WHERE market_status IN ('ACTV','ACTIVE')`. AMPU's inception_date is 2026-05-06; the parquet timestamp for `competitor_counts.parquet` is 2026-05-07 12:00. So at the time the May 11 send was assembled, the data layer DID know about AMPU — but the upstream `launch_candidates.parquet` (also 2026-05-07 12:01) was built from an earlier in-memory read where `is_rex / map_li_underlier` had not yet been classified for AMPU (its `primary_category = NULL` and `map_li_underlier = NULL` per the DB — line scan: `AMPU US, primary_category=None, map_li_underlier=None`).
- **The active-products query is brittle.** `competitor_counts.py:55-73` keeps AMPU because of the regex fallback (`fund_name LIKE '%2X%'`), and `competitor_counts.py:104-108` re-derives the underlier via `extract_underlier()` on the fund name. But `launch_candidates.py` only reads `competitor_active_long`, NOT `competitor_extra_long` (the regex-fallback-only column). AMPX in the current parquet has `competitor_active_long=1` AND `competitor_extra_long=1`. The "1" in `_active_` is what saved the current parquet. On 2026-05-07 morning during the build, the regex fallback hadn't yet caught it because of caching ordering — by the time the bug surfaced in the 2026-05-11 HTML, the parquet had been overwritten and AMPX was correctly excluded. **The HTML on Ryu's desk was generated against a since-deleted intermediate state.**

**Spot check of today's other 11 launch cards.** Word-boundary fund-name regex over all `ACTV` rows in `mkt_master_data` for AMC, UI, DOCN, FNMA, FMCC, FSLY, HBM, TE, WVE, CIEN, IDR returns 0 live leveraged matches each. **Only AMPX is broken in this send.** That said, the failure mode is structural — any competitor launch in the 24-72h between parquet build and report send reproduces it.

**Fix design (1-day patch).** At render time in `weekly_v2_report.py` after `load_launch_candidates`, run a fresh fund-name regex against `mkt_master_data` for each candidate ticker. If any active product matches `\\b{ticker}\\b` AND a leverage token (2X|3X|Bull|Bear|Inverse|Long|Short|Ultra|Leveraged), drop it and log. This is 30 lines, no schema change, no parquet rebuild dependency. Code sketch:

```python
def _live_competitor_exists(ticker: str, conn) -> bool:
    pat = rf'\\b{re.escape(ticker)}\\b'
    df = pd.read_sql_query(
        "SELECT fund_name FROM mkt_master_data "
        "WHERE market_status IN ('ACTV','ACTIVE') AND fund_name IS NOT NULL "
        "AND fund_name REGEXP ? AND fund_name REGEXP '\\b(2X|3X|Bull|Bear|Inverse|Long|Short|Ultra|Leveraged)\\b'",
        conn, params=[pat],
    )
    return len(df) > 0
```

(SQLite needs a UDF for REGEXP; alternatively pull the active set once into pandas and do the regex in Python.)

### 2. Whitespace screener

**Filter chain (`whitespace_v4.py`).**
1. NASDAQ universe loader (`universe_loader.latest_snapshot`).
2. Inner join to bbg signals (`load_bbg_signals_df`, line 44-93). Drops anything Bloomberg doesn't cover.
3. `apply_universe_filters` (line 198-206): mkt cap >= $500M AND total OI >= 5,000.
4. `apply_whitespace_filter` (line 209-244): exclude any ticker present in `competitor_counts.parquet` with ANY `live_*` or `*_filed_*` or `*_extra_*` count > 0.

**Hidden gaps.**
- Step 2 silently drops the entire OTC, ADR-only, and recently-IPO'd universe (anything not in bbg). The "no_bbg_data" backfill mentioned in the docstring is not wired into the active path.
- Step 4 excludes underliers that have ANY REX filing in any state — including filings rejected, withdrawn, or 5+ years old. There is no filing-recency cutoff on the REX side.
- The "no competitor 485APOS in last 180d" claim in the report's section subtitle (`weekly_v2_report.py:899`) is **NOT enforced**. `apply_whitespace_filter` excludes if any competitor has ANY filing column populated, but the 180-day window from `load_product_coverage_with_filings` (line 148-153) is computed and never used as a filter — it lives in the joined frame as `n_competitor_485apos_180d` but no filter references it. **The user-facing description is wrong.**

**Score weights.** Same as composite — see section 3.

### 3. Composite score

**Inputs and weights** (`whitespace_v3.py:33-45`):

| Signal | Weight | Notes |
|---|---|---|
| `mentions_z` | +0.22 | log-z of ApeWisdom 24h mentions |
| `rvol_30d` z | +0.15 | 30d realized vol |
| `theme_bonus` | +0.14 | binary thematic × {2.0 normal, 3.0 hot} |
| `ret_1m` z | +0.12 | trailing 1m return |
| `rvol_90d` z | +0.09 | 90d realized vol |
| `insider_pct` z | +0.08 | sign mistake risk — see below |
| `ret_1y` z | +0.05 | trailing 1y |
| `si_ratio` z | -0.08 | high SI penalised |
| `inst_own_pct` z | -0.07 | retail-owned preferred |

**Are they data-driven?** No, in any current sense. The docstring (`whitespace_v2.py:1-17`) cites "v2 derived weights from IC vs binary success target (AUM >= $50M in 18mo) on the post-launch panel." That work lives in `multi_angle.py` / `post_launch_success.py` and was historical; the weights in `whitespace_v3.WEIGHTS` are hand-edited from that and never re-fit. There is no automated calibration loop. `calibrate.py` exists in `screener/li_engine/` but does not feed `whitespace_v3.WEIGHTS`.

**Insider weight bug suspect.** `+0.08` on `insider_pct_z` rewards high insider ownership. v2's stated finding (line 7-11) was that insider ownership and institutional ownership are NEGATIVE predictors. Either the weight sign should be negative or the rationale needs re-validation.

**Theme_bonus is 30% of total positive weight when triggered.** Effective contribution for a hot-theme ticker = `0.14 × 3.0 = 0.42` z-equivalent — bigger than vol+momentum combined. A single curated list controls 30% of every score.

### 4. Hot themes / has_signals

**Hot list (`whitespace_v3.py:48-51`)**: `ai_infrastructure, ai_applications, quantum, semiconductors, space, nuclear`. **Last-modified inspection**: identical to v3 launch (themes.yaml is also stale — 9 themes, 60 tickers, no robotics, no stablecoin/USDC plays, no GLP-1, no defense-tech, no rare-earth/critical minerals). Hard-coded means an analyst must edit Python to retire or add a theme.

**`has_signals` boolean** (`launch_candidates.py:228-231`): True iff the ticker is in `mkt_stock_data` for the latest completed pipeline run. It only confirms "Bloomberg has data on this stock" — it is not a signal-strength gate. A stock with `mentions_24h=0`, `rvol_30d=20%`, `ret_1m=-5%` still has `has_signals=True`. The name is misleading.

**Recommendation.** Move themes to a YAML registry with `added_date` and `expires_after` fields. Auto-archive themes that haven't surfaced a top-10 ticker in 30d. Replace `is_hot_theme` with a continuous `theme_velocity` score (mention count for the theme cluster over the last 7/30 days).

### 5. Time-decay

**Are aging filings demoted?** No. `load_earliest_competitor_filing_dates` (line 422-509) returns the earliest filing date per underlier — surfacing AMC's "21 filed by GraniteShares earliest 2024-12-18" as a current data point. A filing from 17 months ago that never went effective is shown the same as a filing from last week. There is no decay.

**Recommendation.** Two changes:
- Filings older than 270 days with no progression to 485BPOS should be flagged STALE and downweighted, not listed alongside fresh filings.
- Composite score should include a `competitor_filing_pressure` term: `count of competitor 485APOS in last 90d × 0.5 + count in last 180d × 0.25`. This is the actual time-aware version of what the section subtitle already CLAIMS to measure.

---

## Information density audit

| Section | Verdict | Why |
|---|---|---|
| **Key Highlights — Top 5 to File / Launch** | KEEP | The only true TL;DR. Five tickers across two ranks is the right format. |
| **Key Highlights — Filings count + retail buzz** | IMPROVE | 58 filings / 23 REX / 29 new underliers is bare numerator. No comparison to last week, no week-over-week delta, no "of-which" breakdown by issuer. |
| **Key Highlights — "Hot take" italics** | KILL or REWRITE | "High-vol names dominate the file list — 8 of the top 12 file candidates show >100% realized 90-day volatility" is a tautology — that's how the scorer is designed to work. Recipients learn nothing. |
| **Top Launch Recommendations cards** | IMPROVE | Density is fine, but every card says the same thing in the same order. No ranking signal within the 12. No why-now. The metrics row is unreadable in mobile email clients (one giant string). |
| **Top Filing Recommendations cards** | IMPROVE | Same as above. The "true whitespace" subtitle lies (180d filter not enforced). |
| **Money Flow table** | KEEP, MINOR | Useful — but "Gross Churn" duplicates "4w Net Flow" magnitude and adds noise. Drop one column. |
| **Fund Launches of the Week** | KEEP | Genuinely informative. Could add a "(REX would have been #N to file)" tag. |
| **IPO Watchlist** | DECORATIVE | Hand-curated 15-row list refreshed via YAML. The "Filed By" column is the only data-derived element. Consider folding into a single "IPO race" table sorted by recency of competitor filing activity. |
| **Methodology footer** | KILL | Three sentences of platitudes. Either link to a real methodology doc or remove. |

---

## Actionability audit

### AMPX (Top 5 to Launch, May 11 send)
A REX strategist reading this rec: "Launch a 2x AMPX product." Two minutes of due-diligence reveals AMPU went live 5 days ago. Damage: zero (caught), but the alarm-system trust cost is real. **Would Ryu action this? No — would be embarrassed if anyone else did.**

### DOCN (Top 5 to Launch)
The card shows: $10.3B mkt cap, vol90 76%, 1m +22%, 1y +222%, 0 competitor filings ever, REX has filed (T-REX 2X LONG DOCN). No live competitor anywhere. 55 retail mentions today. The thesis "AI-data-center beneficiary" is in COMPANY_LINES. **Action**: launch. **What's missing for action**: a confidence ("HIGH — clean greenfield + retail attention concentrating + REX already filed"), a recommended timing ("file effective ASAP — no competitor in queue"), and a capacity check ("expected day-1 AUM band: $5-15M based on DOCN options OI"). With those three additions, this becomes a one-glance go/no-go.

### LWLG (Top Filing Rec #1)
Card shows: $1.9B mkt cap, vol132%, 1y +1330%, 0 mentions today (the COMPANY_LINES entry literally says "rally has cooled"). Score driven entirely by historical volatility and the +1330% trailing return. **Would Ryu action this?** If Ryu files a 2x LWLG today, he is filing a leveraged product on a name whose *own description in the report says is past peak*. The score is backward-looking; the qualitative line contradicts it. The report does not catch this contradiction. **No — would not file.**

---

## Redesign spec (forward-thinking)

### Qualitative additions

1. **Per-ticker thesis (3 sentences, LLM-generated, cached weekly).** What the company does, why retail wants leverage on it, what the catalyst-window looks like. Use Claude Haiku 3.5 — average ~250 input tokens (description + signals snapshot) + ~150 output = roughly $0.0008 per ticker. **Cost: ~$0.02 per weekly send across 24 cards.** Cache for 7 days; re-run only if the score moves >1 stdev. Replaces the static COMPANY_LINES dict, which is unmaintainable at scale (currently 31 hand-curated entries; 154 launch candidates exist).
2. **Confidence label per card (HIGH / MEDIUM / LOW).** Rule-based, no LLM. HIGH = clean whitespace + recent retail attention + theme tailwind. LOW = score driven mostly by trailing 1y return with cooling 1m and zero mentions. This single addition does more for actionability than any other change.
3. **Risk flag chip row (red badges).** Up to three flags per card: `[CONCENTRATION RISK: 21 competitor filings]`, `[STALE NARRATIVE: -8% 1m, 0 mentions]`, `[TINY OI: <10K]`. Already half-implemented in `whitespace_v3.negative_flags()` but not surfaced in HTML.
4. **"Defensive" vs "Offensive" segregation.** Defensive: REX has filed, competitor filed too — race to launch. Offensive: clean greenfield, no one in the space. Today both are jumbled in "Top Launch Recommendations." Two columns or two sub-sections is one styling change.
5. **Per-recommendation "why this week" line.** What changed in the last 7 days? A new competitor 485APOS, a +20% price move, a doubling of mentions, an earnings beat. Anchored to a real DB diff, not narrative.

### Quantitative additions

1. **Issuer cadence overlay** (parquet already exists at `data/analysis/issuer_cadence.parquet` — 5 columns including `median_days` from filing to launch per issuer). Today it's unused. Use it to compute, for each underlier with competitor filings, the **expected effective date for the fastest competitor** = `competitor_filing_date + issuer_median_days`. Today AMC shows "Closest effective date: 2025-10-24" — a date in the past — because no decay handling.
2. **Mention velocity (Δmentions/Δtime), not raw 24h count.** A ticker going from 5 → 50 mentions over 7 days is more interesting than one steady at 60. ApeWisdom polling already exists; just persist a 7-day series and compute slope.
3. **Filing-pressure score (0-100).** `(competitor_485apos_90d × 4) + (competitor_485apos_180d × 1) + (competitor_485bpos_180d × 8)`. Surfaces the "many issuers are racing here" signal explicitly.
4. **Theme momentum.** Aggregate score velocity for all tickers in a theme over 4 weeks. Lets the report SAY "AI Infrastructure is the highest-velocity theme this week" instead of relying on a static hot list.
5. **Mentions / market cap ratio.** Normalises retail attention by name size — a $500M name with 30 mentions is more retail-mover-eligible than a $50B name with 60.

### Architectural changes

**New section structure (proposed):**

```
1. EXECUTIVE SUMMARY (top of email, 5 lines)
   - Single highest-conviction launch this week + 1-line why
   - Single highest-conviction file this week + 1-line why
   - Most-contested underlier (most competitor filings this week)
   - Theme of the week (highest velocity)
   - What changed since last send (3 deltas)

2. ACTION QUEUE (the only section that matters)
   Three cards each, max:
   - LAUNCH NOW (clean, scored, REX-filed, no live competitor)
   - FILE THIS WEEK (clean whitespace, theme tailwind, retail rising)
   - DEFEND (REX has live product, competitor just filed — capacity threat)

3. WATCH LIST (12 rows, table not cards)
   Per ticker: confidence | score | thesis (1 line) | "why this week" delta

4. MONEY FLOW (existing, trim to 8 rows)
5. THIS WEEK'S LAUNCHES (existing, keep)
6. IPO RACE (rebuilt as competitor-pressure-sorted table)
```

**Per-card spec (4-tab structure mentioned in prompt):**
- Tab 1 — **Numbers**: current six metrics, plus mention velocity, filing pressure
- Tab 2 — **Thesis**: 3-sentence LLM-generated narrative + theme tag + confidence
- Tab 3 — **Competitive**: who has filed, when, projected effective dates, race position
- Tab 4 — **Risks**: 0-3 red flags + capacity estimate + earnings/event calendar

In a static email this becomes 4 stacked sections per card with a colored left-border to differentiate. Same data, just organised by decision dimension.

---

## Recommendation for tomorrow's send

**(c) one-shot AMPX-class patch, then ship.**

Rationale: the AMPX bug is the only known false positive in the May 11 send. A 30-line render-time guard against `mkt_master_data` closes it for tomorrow. The other quality issues (composite drift, missing decay, lack of confidence labels) have shipped weekly for months without anyone noticing — they are real but not weekend-emergencies. The credibility cost of shipping AMPX again, however, is acute.

Skipping the send (option b) overcorrects — the IPO watchlist and money-flow sections are still accurate and useful.

Shipping as-is (option a) is unacceptable until the live-product check is in place.

Full redesign (option d) is a 2-week project, not a tomorrow decision.

---

## Stage 2 implementation roadmap

**Day 1 (~3 hours).** Ship the AMPX-class competitor filter as a render-time guard in `weekly_v2_report.py`. Two functions: `_live_competitor_exists(ticker, master_df)` and a filter pass before the launch loop. Add a logged audit line per dropped ticker so we can verify it caught real cases. Re-run, eyeball, send.

**Week 1 (~12 hours over 3 sittings).** Three qualitative wins:
- Per-ticker thesis via Claude Haiku, cached weekly. Wire into card render. Replace `_resolve_company_line` fallback chain. Cost-cap at $0.05/send via cache.
- Confidence label (HIGH/MEDIUM/LOW) — rule-based, surfaces immediately. Add as a colored chip in the card header.
- Risk flags — port `negative_flags()` from `whitespace_v3.py` into the card render path. Already written, just not displayed.

**Week 2-3 (~25 hours).** Quantitative depth:
- Mention velocity (persist ApeWisdom history + derive 7d slope).
- Issuer cadence overlay on competitor filings (use the existing parquet that's currently unused).
- Filing-pressure score (replaces "21 filings ago" raw count).
- Time-decay: stale-filing flag, 270d cutoff for "active competition" framing.

**Month 1 (~40 hours).** Architectural:
- Re-fit `WEIGHTS` against the post-launch success panel using a proper holdout. Build a notebook that re-runs the IC analysis quarterly. The original calibration framework exists in `multi_angle*.py` — needs to be refactored into a one-command refit.
- Confidence tier rules → tiered ranking instead of single composite.
- Section restructure to the EXEC SUMMARY → ACTION QUEUE → WATCH LIST layout.
- Themes registry to YAML with decay metadata; deprecate `HOT_THEMES` set in code.

---

## Appendix: file:line references

- AMPX-class filter: `screener/li_engine/analysis/launch_candidates.py:200-204`
- Composite weights: `screener/li_engine/analysis/whitespace_v3.py:33-45`
- Hot-themes hard-coded set: `screener/li_engine/analysis/whitespace_v3.py:48-51`
- Whitespace filter (180d claim NOT enforced): `screener/li_engine/analysis/whitespace_v4.py:209-244`, subtitle at `weekly_v2_report.py:899`
- has_signals computation: `screener/li_engine/analysis/launch_candidates.py:228-231`
- Time-decay absence (no demotion of old filings): `screener/li_engine/analysis/weekly_v2_report.py:422-509`
- Hand-curated company lines: `screener/li_engine/analysis/weekly_v2_report.py:169-201`, override `config/company_descriptions.yaml`
- IPO list: `screener/li_engine/analysis/weekly_v2_report.py:259-294`, override `config/ipo_watchlist.yaml`
- Issuer cadence parquet (currently unused): `data/analysis/issuer_cadence.parquet`
