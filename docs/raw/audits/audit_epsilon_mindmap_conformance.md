# Audit ε — Mindmap Conformance
**Date**: 2026-05-05
**Scope**: Compare DB state (`mkt_master_data`) vs `docs/CLASSIFICATION_SYSTEM_PLAN.md` declared taxonomy
**DB queried**: `data/etp_tracker.db` (READ-ONLY)

---

## 1. Executive Summary

The new taxonomy (asset_class × primary_strategy × sub_strategy) declared in `CLASSIFICATION_SYSTEM_PLAN.md` has **not been applied to the DB at all**. Every single ACTV fund (5,144) has NULL for all three new columns. The plan is Phase 2 / Phase 3 work — the schema columns exist (added per Phase 2 migration) but no data has been written.

**Implication**: Audits ε, ζ, and η must work from the legacy `etp_category` field and the existing `map_*` attribute columns — the new taxonomy cannot be evaluated for data conformance because it is unpopulated.

---

## 2. DB State vs Declared Taxonomy

### New taxonomy columns — population status

| Column | ACTV funds with value | ACTV funds NULL |
|---|---|---|
| `asset_class` | 0 | 5,144 |
| `primary_strategy` | 0 | 5,144 |
| `sub_strategy` | 0 | 5,144 |

**Finding**: Phase 2 (schema migration) is complete — columns exist. Phase 3 (fund_master.csv seed + apply) has not been executed. Zero funds classified under the new taxonomy.

---

## 3. Legacy `etp_category` distribution (ACTV funds = 5,144)

| etp_category | Count | % of ACTV |
|---|---|---|
| NULL (unclassified) | 3,269 | 63.6% |
| LI | 595 | 11.6% |
| Defined | 503 | 9.8% |
| Thematic | 350 | 6.8% |
| CC | 322 | 6.3% |
| Crypto | 105 | 2.0% |

This matches the doc's stated problem: "64% of active ETPs (3,267 of 5,144) uncategorized" — confirmed (actual: 63.6%).

---

## 4. Over-specified (declared in doc, zero funds in DB)

Since the new taxonomy is fully unpopulated, every declared combo is technically "over-spec." Listing the most actionable ones — sub-strategies declared in the doc that have zero representation in new columns:

| Primary Strategy | Sub-Strategy | Doc Examples | DB Count |
|---|---|---|---|
| Income | Derivative Income > Covered Call | JEPI, TSLY | 0 |
| Income | Derivative Income > Put-Write | PUTW | 0 |
| Income | Derivative Income > Collared | FTSL | 0 |
| Income | Derivative Income > 0DTE | ODTE | 0 |
| Income | Structured Product > Autocallable | ATCL, CAIE | 0 |
| Income | Structured Product > ELN | (rare) | 0 |
| Defined Outcome | Buffer | BUFR, BJUL | 0 |
| Defined Outcome | Floor | FLJL, AAPR | 0 |
| Defined Outcome | Growth | XBJL, BFXU | 0 |
| Defined Outcome | Hybrid | DBJL, BHJL | 0 |
| Defined Outcome | Dual Directional | DDFA, DDTA | 0 |
| Defined Outcome | Box Spread | CBOX | 0 |
| Risk Mgmt | Hedged Equity | JHDG, HEQT | 0 |
| Risk Mgmt | Risk-Adaptive | SPDF, THMR | 0 |
| Risk Mgmt | Trend / Managed Futures | DBMF, KMLM | 0 |
| L&I | Long | TQQQ, NVDX | 0 |
| L&I | Short | SQQQ, NVDS | 0 |
| L&I | Stacked Returns | RSST, RSSY | 0 |
| Plain Beta | Broad | SPY, VOO | 0 |
| Plain Beta | Sector | XLK, XLE | 0 |
| Plain Beta | Thematic | ARKK, KWEB | 0 |
| Plain Beta | Style | SCHD, QUAL | 0 |
| Plain Beta | Single-Access | OBTC, IBIT | 0 |

---

## 5. Taxonomy drift — combos in DB but NOT declared

Not applicable for new taxonomy columns (all NULL). For the legacy `etp_category` system:

| Legacy Category | Issue vs Declared Taxonomy |
|---|---|
| `Defined` → `Hedged Equity` (6 funds) | `map_defined_category='Hedged Equity'` — doc places Hedged Equity under **Risk Mgmt**, not Defined Outcome |
| `Defined` → `Defined Volatility` (13 funds) | Not explicitly a declared sub-strategy in the doc |
| `Defined` → `Defined Risk` (2 funds) | Not explicitly a declared sub-strategy in the doc |
| `Defined` → `Outcome` (12 funds) | Ambiguous — likely should map to Buffer or Floor |
| `Defined` → `Barrier` (8 funds) | Not in declared sub-strategy list |
| `Defined` → `Ladder` (23 funds) | TLDR-type laddered products — doc classifies TLDR as Fixed Income / Plain Beta |

---

## 6. Illegal pairings (sub_strategy doesn't legally pair with primary)

Since new taxonomy is unpopulated, evaluated via legacy fields:

| Issue | Funds | Detail |
|---|---|---|
| `etp_category=LI` with `sub_strategy IS NULL` | 595 | All 595 LI funds have NULL sub_strategy — no Long/Short/Stacked classification in new columns |
| `etp_category=Defined` with `map_defined_category='Hedged Equity'` | 6 | Hedged Equity should be Risk Mgmt per declared taxonomy |
| `etp_category=CC` with no `mkt_category_attributes` row | 8 | JHDG (hedged equity classified as CC), HYGM, LQDM (amplify fixed-income overlays) |

**No Buffer sub_strategy found on LI funds** — that specific illegal pairing is clean.

---

## 7. Key Finding

**The new taxonomy is a ghost taxonomy.** The schema was migrated (Phase 2 done) but the data layer (Phase 3 — fund_master.csv seed + apply) has never executed. All 5,144 ACTV funds sit with NULL asset_class/primary_strategy/sub_strategy. The declared mindmap cannot be conformance-tested against real data until Phase 3 runs. Priority action: execute Phase 3 seed.

---

## 8. Action items

| Priority | Action |
|---|---|
| P0 | Execute Phase 3: build and apply `fund_master.csv` seed for 1,877 classified funds |
| P1 | Reclassify 6 `Defined/Hedged Equity` funds → `Risk Mgmt` |
| P1 | Resolve 23 `Defined/Ladder` funds — TLDR family should move to Fixed Income |
| P2 | Canonicalize ambiguous `Defined` sub-cats: `Outcome`, `Barrier`, `Defined Risk`, `Defined Volatility` → map to declared taxonomy nodes |
| P3 | Run Phase 6 backfill for 3,269 NULL `etp_category` funds |
