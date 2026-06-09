---
doc: classification
status: canonical
updated: 2026-06-09
---

# CLASSIFICATION — the taxonomy contract

> What every ETP category MEANS, what attributes it carries, how the autonomous
> engine decides, and where every decision is recorded. This is the document the
> LLM tier is held to and the document a human consults to overrule it.
> Engine: `scripts/auto_classify.py` (daily, 09:00 ET unit). ADR 0011 context.

## Why this exists

REX tracks five proprietary product categories (never Morningstar/Bloomberg
schemes — [[feedback_no_competitor_copying]]). A fund that is live in Bloomberg
but missing its category silently corrupts every REX KPI (fund counts, AUM by
suite, market share). The old flow detected gaps but waited for human review
that never happened (1,669 proposals, 1 approved). The engine now classifies
autonomously; humans handle ONLY what two independent passes can't agree on.

## The five tracked categories

| Category | Definition (what Ryu wants out of it) | Key attributes (CSV columns) |
|---|---|---|
| **LI** | Leveraged & Inverse — any fund whose return is a leveraged (≥1.25x) or inverse multiple of an underlier, daily-reset or target-date. THE priority category: REX's T-REX + MicroSectors compete here. | `attributes_LI.csv`: map_li_category (Equity/Crypto/Commodity…), map_li_subcategory (Single Stock/Index/Basket), map_li_direction (Long/Short), map_li_leverage_amount (2.0, 3.0, -1.0…), map_li_underlier (the underlier ticker — feeds whitespace/race analysis) |
| **CC** | Covered Call / Income — options-income strategies: covered calls, synthetic income, autocallables, option-income ETFs (YieldMax/XETFS/GraniteShares-autocall class). REX competes via FEPI/AIPI/CEPI etc. | `attributes_CC.csv`: map_cc_underlier, cc_category (Single Stock/Broad Beta/Tech/…). Autocallables MUST get a CC attributes row (feeds /notes/tools/autocall). |
| **Crypto** | Direct crypto exposure — spot, futures, or staked single-coin or multi-coin funds (BTC, ETH, SOL, XRP, DOGE…). NOT crypto-equity funds (those are Thematic or LI on a crypto stock). | `attributes_Crypto.csv`: map_crypto_type (Spot/Futures/Staked), map_crypto_underlier (Bitcoin/Ethereum/Solana/…/Multi-Crypto) |
| **Defined** | Defined Outcome — buffer, floor, accelerator, barrier, ladder, hedged-equity structured-outcome funds (Innovator/FT-Vest class). | `attributes_Defined.csv`: map_defined_category (Buffer/Dual Buffer/Floor/Accelerator/Barrier/Ladder/Hedged Equity) |
| **Thematic** | Narrow secular-theme equity funds — AI, robotics, quantum, nuclear, cybersecurity, clean energy, space, drones… NOT broad sector funds (XLK is Other, not Thematic). | `attributes_Thematic.csv`: map_thematic_category (the theme) |

**Other** = real ETP, fits none of the five (broad-market beta, style factors,
plain bond ladders, single commodities, sector SPDRs). Recorded as a
**full-ticker exclusion** (`exclusions.csv` row with EMPTY etp_category) so it
stops counting as a gap. Non-ETPs don't belong in the universe at all
([[feedback_no_non_etps]]).

## Decision rules the engine enforces

1. **Leverage wins.** A leveraged thematic fund (2x quantum) is **LI**, not
   Thematic. A covered-call fund ON a leveraged ETF is **CC**.
2. **Crypto-EQUITY is not Crypto.** A fund holding crypto miners/treasuries is
   Thematic (or LI if leveraged on a single stock). Crypto = the asset itself.
3. **Autocallable ⇒ CC** with a `attributes_CC.csv` row, always (the autocall
   tool reads that file).
4. **Single-stock UCITS** (FEPI LN class) keep their LN suffix and classify
   like their US siblings.
5. **One primary category per ticker** (`is_primary=1`); cross-listings handled
   by exclusion pairs, not duplicate rows.
6. **REX funds are never Other.** If a REX/T-REX/MicroSectors fund reaches the
   LLM tier something upstream broke — classify it AND flag it in the journal.

## The engine — four tiers (scripts/auto_classify.py)

| Tier | Who decides | What happens |
|---|---|---|
| 0 Standing rulings | Ryu (in writing, encoded in `STANDING_RULINGS`) | Applied first, never re-asked. Current: OBTC US → exclude (2026-06-08 ruling). |
| 1 Rules | `classify_engine.scan_unmapped` — deterministic Bloomberg-field derivation (leverage flags, crypto flags, outcome types) | HIGH-confidence candidates **auto-applied** to fund_mapping + attributes CSVs (`source=atlas`). |
| 2 LLM ×2 | `ai_classify.classify_batch` (Haiku, cached taxonomy prompt) + an **independent critic pass** that tries to refute each proposal | AGREE + HIGH/MEDIUM → auto-applied (tracked) or excluded (Other). The two-pass design substitutes for the human who never reviewed the queue. |
| 3 Queue | Human (Ryu / review UI) | LOW confidence, critic DISAGREE, or no-verdict funds → `ClassificationProposal` queue. Queue is now small enough to mean something; surfaced in the 09:00 sweep email. |

Every decision — including dry-runs — is journaled to
`logs/auto_classify_YYYYMMDD.jsonl` with tier, source, confidence, rationale.

## Where the rules live (and the mirror discipline)

- **`data/rules/*.csv`** — runtime source of truth on the VPS (RULES_DIR).
- **`config/rules/*.csv`** — git-tracked mirror; the engine mirrors after every
  apply so git always carries current classification truth (audit 2026-06-09
  found 3-way drift; the mirror step ends it).
- `exclusions.csv` semantics: row with etp_category SET = "remove this wrong
  (ticker, category) pair"; row with etp_category EMPTY = "this ticker is
  outside all 5 categories" (gap audits skip it — preflight_check.py).

## Daily flow

```
Bloomberg sync (17:15) ──> mkt_master_data refreshed
09:00 unit: auto_classify --apply        (tiers 0-3, journaled)
           └─> classification_sweep      (reports: what was auto-applied,
                                          what's queued, residual gaps)
market pipeline rerun picks up new rules ──> KPIs correct everywhere
```

Strict preflight gating (`preflight_maintenance` flag OFF) is restored once
the engine demonstrably holds gaps near zero — the engine, not the human,
is now the thing keeping send-day classification clean.

### known-gaps
- GAP-01: Tier-2 model pinned to Haiku 4.5; revisit when model lineup changes.
- GAP-02: the 1,669-row legacy proposal queue needs a one-time triage
  (auto-expire >90d stale; rerun the rest through the engine).
- GAP-03: `apply_classifications` hardcodes source="atlas" — engine decisions
  should eventually carry source="atlas-auto-rules" / "atlas-auto-ai" for
  per-tier provenance in fund_mapping itself (journal carries it today).
