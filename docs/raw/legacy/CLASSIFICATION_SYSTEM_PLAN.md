# REX FinHub Classification System — Plan & Reference

**Status**: PLANNING (locked taxonomy + plan; not yet implemented)
**Owner**: Ryu El-Asmar
**Last updated**: 2026-04-30

---

## 1. Context

The current `mkt_master_data.etp_category` 5-category system (LI / CC / Crypto / Defined / Thematic) leaves **64% of active ETPs (3,267 of 5,144)** uncategorized — every plain-beta, fixed-income, sector, international, multi-asset, alternative, commodity, and currency fund falls through. The auto-classifier (`market/auto_classify.py`) already knows 13 strategy buckets but the DB column doesn't reflect it. Result: the preflight gap detector keeps flagging 49+ gaps every morning, and we keep hand-patching `fund_mapping.csv` per-ticker without ever fixing the root.

This plan overhauls classification end-to-end: a richer taxonomy, a single source of truth, automated detection with a human-in-the-loop queue, and an agent that can propose taxonomy evolution as the market shifts (especially in Thematic).

---

## 2. Ryu's locked decisions (incorporated below)

| Decision | Notes |
|---|---|
| **3 axes**: Asset Class × Primary Strategy × Attributes | Settled in Tue afternoon design conversation |
| **7 asset classes** | Equity, Fixed Income, Commodity, Crypto, Multi-Asset, Currency, Volatility |
| **5 primary strategies** | Plain Beta, Income, Defined Outcome, Risk Mgmt, L&I |
| **Stacked Returns → L&I** | RSST family, even though asset_class is Multi-Asset |
| **Box Spread → Defined Outcome** | CBOX classified as Defined / Box Spread, not Tax-Optimized |
| **Cash Equivalent NOT a separate asset class** | TLDR is `Fixed Income / Plain Beta / duration_bucket=ultra_short / credit_quality=treasury` |
| **Single Asset Trust → Plain Beta / Single-Access** | OBTC, IBIT, GLD-trust = passive access, not "Other" |
| **No Anthropic API ever** | Use Claude Code CLI on Max plan for any LLM work |
| **Historical ticker bleed: fix manually, no rescrape** | Going-forward fix in step3.py is enough; flag + clean existing rows |
| **Same ticker across different issuers = OK** | Dedup is per-issuer (or per-trust), not global |
| **Bracket tickers (`[OpenAI]`, `[Anthropic]`, etc.) need manual review** | Ryu reviews first, then AI may assist |
| **The classifier agent should propose taxonomy evolution** | Especially in Thematic — propose new sub-strategies, merge similar themes, retire dead ones |

---

## 3. Full taxonomy mindmap (with examples)

### Asset Class — what the fund OWNS

| Asset Class | Examples |
|---|---|
| Equity | SPY, VOO, VTI, sector funds, single-stock ETFs (~70% of universe) |
| Fixed Income | AGG, BND, TLT, BIL, MUB, TLDR (T-bill ladder), ultra-short Treasury |
| Commodity | GLD, SLV, USO, DBC, PALL |
| Crypto | IBIT, OBTC, ETHE, BITX |
| Multi-Asset | AOR, AOA, RSST (stacked, but primary=L&I per lock), MHIG/MHIP |
| Currency | UUP, FXE, FXY, FXB |
| Volatility | VXX, UVXY, SVXY |

### Primary Strategy + Sub-strategies (the differentiator)

```
PLAIN BETA   — passive or active access; no derivative income, no buffer
├── Broad             SPY, VOO, VTI, IVV, AGG, BND, DBC
├── Sector            XLK, XLE, XLF, XLV, IBB, KRE, VNQ, KIE
├── Thematic          ARKK, KWEB, ICLN, AIQ, JEDI, ORBX, ROBO, DRIV
├── Style             SCHD, NOBL, VYM (Dividend factor — owns div stocks, no overlay)
│                     QUAL, MTUM, USMV, IWD, IWF, VLUE
└── Single-Access     OBTC, IBIT (Bitcoin trusts); GLD, IAU (gold trusts)


INCOME   — primary intent: yield via DERIVATIVES or STRUCTURED NOTES
         (NOT plain dividend ETFs — those go to Plain Beta/Style)
├── Derivative Income
│   ├── Covered Call / Buy-Write
│   │     JEPI, JEPQ (active CC); QYLD, XYLD (systematic CC)
│   │     TSLY, NVDY, MSTY (YieldMax single-stock)
│   │     ANV, TLA (GraniteShares single-stock)
│   │     TMYY, MUYY, CWY (YieldBoost CC on 2x ETF)
│   │     NVII (REX 1.25x leverage + CC overlay)
│   │     YMAX, YMAG, GIF (FoF wrapper of CC funds)
│   │     GLDN, SLVX (Commodity asset class, CC overlay)
│   ├── Put-Write       PUTW
│   ├── Collared        FTSL
│   └── 0DTE / Weekly   ODTE; Roundhill weekly-distribution products
└── Structured Product Income
    ├── Autocallable
    │     CAIE, CAIQ, CAGE (Calamos)
    │     ACYN, ACYS (FT Vest)
    │     ATCL (REX)
    │     ACEI, ACII (Innovator)
    │     PAYH, PAYM (TrueShares)
    │     SBAR (Simplify)
    │     JELA, JELM, JELH (Janus Equity Linked Income)
    │     Defiance BTC/Gold/NDX/Silver Autocallable
    └── ELN (non-autocallable structured note — rare)


DEFINED OUTCOME   — predetermined payoff curve known at start
├── Buffer            BUFR, BUFB; BJUL, BJUN, BJAN (Innovator monthly)
├── Floor             FLJL, FLAU; AAPR, AOCT (Allianz Floor)
├── Growth            XBJL, XBJN (Innovator Accelerated)
│                     BFXU, BFOU (FT Vest Uncapped Accelerator)
├── Hybrid            DBJL, BHJL (Innovator legacy combos — many delisted)
├── Dual Directional  DDFA, DDTA (Innovator)
└── Box Spread        CBOX (Calamos Tax-Aware Collateral)


RISK MGMT (Active / Adaptive)   — active downside intervention WITHOUT buffer
├── Hedged Equity     JHDG, HEQT
├── Risk-Adaptive     SPDF, THMR, RPAR
└── Trend / Managed Futures
                      DBMF, KMLM, CTA


L&I   — straight directional (any leverage, any reset, NO cap, NO income)
├── Long              QLD, SSO, TQQQ, UPRO, SOXL, FNGU, BULZ
│                     NVDX, TSLT, NVDU, FNGO, MSTU (single-stock 2x/3x)
├── Short             PSQ, SH, SQQQ, SPXU, SOXS, BERZ
│                     NVDS, TSLS (single-stock short)
└── Stacked Returns   RSST, RSSY, RSBT (asset_class=Multi-Asset, primary=L&I per Ryu's lock)
```

### Attributes (orthogonal columns — apply to every fund)

```
# Underlier identification
concentration:        single | basket
underlier_name:       NVDA | SPX | NDX | BTC | gold | basket-id
underlier_is_wrapper: bool  (TRUE if direct underlier is itself an ETF — e.g., YieldBoost on a 2x ETF)
root_underlier_name:  the ROOT economic exposure when wrapper=TRUE

# Wrapper / packaging
wrapper_type:         standalone | fund_of_funds | laddered | synthetic | feeder

# Mechanism (HOW exposure is implemented)
mechanism:            physical | swap | futures | options | structured_note | synthetic

# Quantitative
leverage:             numeric (1.0, 1.25, 1.5, 1.75, 2.0, 3.0)
direction:            long | short | neutral
reset_period:         daily | weekly | monthly | quarterly | none
distribution_freq:    daily | weekly | monthly | quarterly | annual | none
outcome_period:       N months (Defined Outcome only)
cap_pct, buffer_pct, accelerator_multiplier, barrier_pct: numeric

# Asset characteristics
region:               US | DM-ex-US | EM | EMEA | APAC | LatAm | Country-specific
duration_bucket:      ultra_short (<1y) | short (1-3y) | intermediate (3-7y) | long (7-15y) | ultra_long (>15y)
credit_quality:       treasury | ig | hy | junk | muni | mixed
is_active:            bool (already in BBG)

# Tax & regulatory
tax_structure:        40_act | mlp_k1 | grantor_trust | partnership | uit
qualified_dividends:  bool

# Identity / lifecycle
ticker, fund_name, issuer (raw), issuer_brand (curated),
inception_date, market_status, is_rex
```

---

## 4. Detection methodology — how funds get classified going forward

Four-layer pipeline, increasing intelligence. Each layer falls through to the next when it can't decide.

### Layer 1 — Manual override (highest priority)
**Source**: `config/rules/fund_master.csv`
Atlas + Ryu-curated. Wins over all auto-detection. Single source of truth.

### Layer 2 — Auto-classifier keyword rules (existing)
**Source**: `market/auto_classify.py`
Pattern-matches fund name + Bloomberg fields (`uses_leverage`, `is_crypto`, `regulatory_structure`). Fast, deterministic. Handles ~75% of new launches.

### Layer 3 — Pattern matching against similar funds (cheap, no LLM)
For an unclassified fund, find K nearest already-classified funds by:
- Issuer match (same trust → likely same brand pattern)
- Name n-gram similarity (Jaccard on trigrams)
- Bloomberg field overlap (asset_class_focus, regulatory_structure, etc.)

If 3+ neighbors agree on classification with high confidence, propose it.

### Layer 4 — Scheduled remote agent / routine (Max plan, no API, no VPS load)
**Implementation**: Anthropic Routine (scheduled remote agent) created via the `/schedule` skill. Runs in the cloud on Anthropic infrastructure, authenticated via Max-plan OAuth — no API key, no per-token billing, no headless-systemd auth pain.

**Why this beats a subprocess `claude --print`**:
- Auth works cleanly (OAuth, not interactive prompt)
- Cloud-executed — zero VPS resource load
- Built for batch agentic work (cron + webhook + manual triggers)
- Output as PR / commit / webhook — clean integration with our git workflow

**Inputs piped to the agent**:
- Fund name + ticker
- 485APOS prospectus excerpt (Investment Strategy section, ~2-3 KB)
- Bloomberg fields summary
- Reference: the canonical taxonomy doc at `docs/CLASSIFICATION_SYSTEM_PLAN.md` itself (this document)

**Agent returns** (JSON):
```json
{
  "asset_class": "Equity",
  "primary_strategy": "Income",
  "sub_strategy": "Derivative Income > Covered Call",
  "concentration": "single",
  "underlier_name": "NVDA",
  "underlier_is_wrapper": true,
  "root_underlier_name": "NVDA",
  "wrapper_type": "standalone",
  "mechanism": "options",
  "leverage": 2.0,
  "confidence": 0.94,
  "reasoning": "...",
  "evidence_quotes": ["...the Fund will write call options on..."],
  "taxonomy_proposals": []
}
```

Confidence routing:
- `>= 0.90` → auto-apply, log to audit
- `0.70 - 0.89` → queue to `/admin/classify/pending` for one-click accept/edit
- `< 0.70` → leave NULL, surface in preflight

**Special: `taxonomy_proposals`** — the agent can also flag:
```json
{
  "taxonomy_proposals": [
    {
      "type": "new_sub_strategy",
      "parent": "Plain Beta > Thematic",
      "proposed_name": "Humanoid Robotics",
      "rationale": "5 funds in the last 60 days target humanoid-specific robotics — distinct from broad Robotics theme",
      "example_funds": ["XYZH", "HMNB", "ROBT2"]
    },
    {
      "type": "merge_themes",
      "themes": ["Space Exploration", "Space Tech"],
      "rationale": "Holdings overlap >85%; funds use these labels interchangeably"
    },
    {
      "type": "retire_theme",
      "theme": "3D Printing",
      "rationale": "Last fund liquidated 2024-Q2; no active products"
    }
  ]
}
```

These get reviewed weekly. Especially relevant for Thematic which evolves rapidly.

---

## 5. Implementation plan — 9 phases

### PHASE 1 — Historical ticker cleanup (1-2h, MANUAL-FIRST per Ryu)

Per Ryu: don't rescrape (too intensive); identify bad rows + clean manually.

**1.1** `scripts/audit_ticker_duplicates.py`
- Scan `fund_extractions` for duplicates: same `class_symbol` assigned to >1 series **within the same accession + same trust**
- (Different issuers using same ticker is OK — means one issuer gave it up)
- Output: `docs/ticker_review_queue.csv` with columns:
  `accession, trust, series_name, class_symbol, suggested_action (KEEP|NULL|MANUAL), reason`

**1.2** Manual review pass (Ryu)
- Open `ticker_review_queue.csv`
- Confirm/correct each row's `suggested_action`
- Special handling for bracket tickers (`[OpenAI]`, `[Anthropic]`, etc.) — Ryu reviews these directly first

**1.3** `scripts/apply_ticker_cleanup.py`
- Reads the reviewed CSV
- Applies NULL or KEEP to fund_extractions accordingly
- Audit log to `data/.ticker_cleanup_log.jsonl`
- Optional: re-rolls up `fund_status` and `mkt_master_data` ticker fields

**1.4** Going-forward error catcher
- Add to preflight: detect any fund_extractions row added in last 24h where `class_symbol` is duplicated within (accession + issuer)
- Surface as a new audit tier in the daily preflight summary

### PHASE 2 — Taxonomy doc + DB schema (1h)

**2.1** This document IS the canonical reference. Atlas + Ryu both point to it.

**2.2** Migration: extend `mkt_master_data` schema
- Add columns: `asset_class`, `primary_strategy`, `sub_strategy`
- Add attribute columns: `concentration`, `underlier_name`, `underlier_is_wrapper`, `root_underlier_name`, `wrapper_type`, `mechanism`, `leverage`, `direction`, `reset_period`, `distribution_freq`, `outcome_period`, `cap_pct`, `buffer_pct`, `accelerator_multiplier`, `barrier_pct`, `region`, `duration_bucket`, `credit_quality`, `tax_structure`, `qualified_dividends`
- Preserve existing `etp_category` for backwards-compat (will be derived from new fields)

### PHASE 3 — `fund_master.csv` + migration (2-3h)

**3.1** Schema definition + seed file
- Columns match the new mkt_master_data columns
- Seed with the existing 1,877 classified funds, mapped from old 5-cat → new taxonomy
- Atlas drafts; Ryu spot-checks ~50 random rows for accuracy

**3.2** `scripts/build_classification_csvs.py`
- Generates legacy `fund_mapping.csv`, `attributes_CC.csv`, `attributes_LI.csv`, `issuer_mapping.csv` from `fund_master.csv`
- Backwards-compat shim — old code keeps working

**3.3** `scripts/apply_fund_master.py`
- Writes `fund_master.csv` values into `mkt_master_data` new columns
- Idempotent (safe to re-run)

### PHASE 4 — Auto-classifier alignment (1h)

**4.1** `market/auto_classify.py`
- Emit `(asset_class, primary_strategy, sub_strategy)` triples instead of single strategy
- Update keyword rules where new sub-strategies have clear signals

**4.2** `derive.py`
- Read from `fund_master.csv` first (Layer 1)
- Fall back to auto-classifier (Layer 2)

### PHASE 5 — Scheduled routine for classification + taxonomy proposals (1.5-2h)

**Pattern**: cloud-executed scheduled agent on Max plan (NOT a VPS subprocess). Created via the `/schedule` skill.

**5.1** Create the routine
- Schedule: daily 09:15 ET (15 min after `rexfinhub-classification-sweep` writes the gap list)
- Trigger: cron + manual override + webhook (for on-demand backfills)
- Inputs the routine reads:
  - `outputs/classification_gaps.json` (gap list from sweep)
  - `docs/CLASSIFICATION_SYSTEM_PLAN.md` (this taxonomy doc — its own canonical reference)
  - Per-gap-fund: prospectus excerpt + BBG fields, fetched via a lightweight read-only API endpoint on the VPS

**5.2** Routine output: GitHub PR to the rexfinhub repo
- Edits `config/rules/fund_master.csv` with proposed classifications
- PR description contains per-fund reasoning, confidence, evidence quotes
- Confidence-bucketed:
  - `>= 0.90` → ready-to-merge rows (auto-mergeable if you set up auto-merge for label `auto-classify-high-confidence`)
  - `0.70 - 0.89` → "needs review" rows (you eye these in the PR diff)
  - `< 0.70` → leaves NULL with a note in the PR description for manual handling
- PR also surfaces a "Taxonomy proposals" section: new sub-strategies, merges, retirements

**5.3** Verification step BEFORE first scheduled run
- Manually invoke the routine once with a small fixture (5 known funds)
- Confirm: PR opens correctly, JSON output is well-formed, classifications are reasonable
- If something's off, fix the system prompt / inputs and re-run before scheduling

**5.4** Webapp `/admin/classify/pending` (optional, secondary)
- For the rare case Atlas wants to bypass the PR workflow (e.g., emergency fix)
- Pulls from the same proposals PR's content
- Most edits should still go through PR review, not this UI

### PHASE 6 — Backfill 3,267 NULL funds (1-2h)

**6.1** Run the LLM classifier across all NULL `etp_category` ACTV funds
**6.2** Auto-apply >= 0.90 confidence
**6.3** Surface < 0.90 to pending queue
**6.4** Special: bracket-ticker funds (`[OpenAI]`, `[Anthropic]`, etc.) → ALWAYS go to manual queue regardless of LLM confidence (Ryu's instruction)

### PHASE 7 — Reporting + dashboard (1-2h)

**7.1** Update report builders to use new fields where they add precision
- Autocall report: `WHERE primary_strategy='Income' AND sub_strategy='Autocallable'` instead of fragile `attributes_CC.csv` lookup
- Income report: `WHERE primary_strategy='Income'`
- L&I report: `WHERE primary_strategy='L&I'`
- Sector breakdown becomes trivial: filter by `sub_strategy='Sector'`

**7.2** New `/admin/classify/dashboard`
- Full taxonomy explorer
- Counts per node
- Drill into any leaf to see all funds
- Trend over time (new launches per category)

### PHASE 8 — Taxonomy evolution agent (1h)

**8.1** `scripts/taxonomy_evolution_sweep.py`
- Runs weekly (new systemd timer)
- Reviews recent classifications for `taxonomy_proposals` from the LLM
- Aggregates patterns: "5 humanoid-robotics funds in 60 days" / "3D Printing has 0 active products"
- Posts a weekly digest email to Ryu with proposed additions/merges/retirements
- Ryu approves or rejects via simple webapp links

**8.2** Logging
- All accepted taxonomy changes go to `docs/CLASSIFICATION_SYSTEM_PLAN.md` (this doc) as a versioned changelog at the bottom
- The doc itself is the system's memory

### PHASE 9 — Validation + monitoring (ongoing)

**9.1** Preflight gap-detector reflects the new taxonomy
- Tier 1: NULL `asset_class` (rare — the 7 categories cover everything)
- Tier 2: NULL `primary_strategy` for ACTV ETPs (was the "49 gaps" problem — should drop to <5)
- Tier 3: NULL `issuer_brand` (existing check, still valid)
- Tier 4: Tickers in fund_extractions duplicated within (accession + issuer) (NEW — catches the bug recurring)

**9.2** Weekly accuracy sample
- Atlas randomly samples 20 funds per week
- Posts their full classification + reasoning
- Ryu spot-checks; corrections feed back to `fund_master.csv`

---

## 6. Cost / resource summary

| Item | Cost |
|---|---|
| Anthropic API | **$0** — using Claude Code CLI on Max plan instead |
| Storage | +20 columns × ~5,000 ACTV rows × 50 bytes ≈ 5 MB. Negligible |
| Compute | Pipeline run +1-2 min for LLM tier-3 classification (~50 funds/run × ~30s/fund) |
| Total time-to-implement | 8-12 hours focused work, 9 phases |

---

## 7. Implementation order for first session

Recommended starting order (your call):

1. **Phase 1** — ticker cleanup (audit + review queue + apply). Visible win on the 17 T-REX rows.
2. **Phase 2** — DB schema migration (low-risk, additive only).
3. **Phase 3** — fund_master.csv seed (the foundation everything else builds on).
4. *Pause — Ryu spot-checks the seed CSV. Validate before continuing.*
5. **Phase 4** — auto-classifier alignment.
6. **Phase 5** — Claude Code CLI integration. **Verification step required first**: prove `claude --print` works in batch mode under Max plan from a non-interactive systemd service environment.
7. **Phase 6** — backfill the 3,267 NULL funds.
8. *Pause — Ryu validates ~50 random LLM-classified funds.*
9. **Phase 7-9** — reporting, dashboard, evolution agent, monitoring.

---

## 8. Known unknowns / things to verify before we start

1. **Routine setup on Ryu's Max plan** — the `/schedule` skill exists in the Atlas environment. Need to confirm the Anthropic Routines preview is enrolled (per memory: "preview enrollment required"). If not enrolled yet, that's a one-time signup before Phase 5.
2. **Prospectus text source** — for new funds without a baked-in 485 doc on disk, do we pull live from EDGAR each time, or rely on cached versions? Live pulls add latency but ensure freshness. The routine will need access to either a VPS read-only endpoint OR direct EDGAR access.
3. **CSV vs DB** for the master: keeping `fund_master.csv` as source of truth + DB as derived view is clean. Alternative: make the DB the source and `fund_master.csv` is a generated export. CSV-first is simpler for git diffs and Atlas/Ryu manual edits, plus it pairs cleanly with the routine's PR-based output.
4. **Bracket-ticker funds** — how many in the system today? Snapshot count helps size the manual review effort.
5. **Routine GitHub permissions** — the routine opens PRs. Needs a GitHub App or PAT with PR write access to `the-atlas-protocol/rexfinhub`. One-time setup.

---

## Changelog (append below as taxonomy evolves)

- 2026-04-30: Initial draft. 7 asset classes, 5 primary strategies, full sub-strategies + attributes locked. Implementation plan (9 phases) defined. Awaiting kickoff approval.
