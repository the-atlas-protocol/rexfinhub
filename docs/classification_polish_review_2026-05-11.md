# Classification Polish Review — 2026-05-11

**Scope:** `mkt_master_data` rows where `market_status IN ('ACTV','PEND')` (5,498 rows total).
**Method:** SQL anomaly scans across 8 categories. READ-ONLY — no DB writes, no commits.
**Note:** Total rows in table = 7,486 (includes 1,809 LIQU + other inactive states).

---

## Distributions (baseline)

| primary_strategy | count | | asset_class | count |
|---|---:|---|---|---:|
| Plain Beta | 3,949 | | Equity | 3,798 |
| L&I | 653 | | Fixed Income | 1,043 |
| Defined Outcome | 632 | | Multi-Asset | 274 |
| Income | 235 | | Crypto | 201 |
| Risk Mgmt | 14 | | Commodity | 126 |
| **NULL** | **15** | | Currency | 31 |
|  |  | | Volatility | 14 |
|  |  | | **NULL** | **11** |

---

## Category 1 — Strategy mismatch (name vs primary_strategy)

### 1a. "Buffer / Floor / Defined" in name but `primary_strategy != 'Defined Outcome'` — **9 rows · HIGH**

| ticker | fund_name | primary_strategy |
|---|---|---|
| IBFR US | INNOVATOR INTERNATIONAL DEVELOPED MANAGED 10 BUFFER ETF | NULL |
| KBFR US | INNOVATOR US SMALL CAP MANAGED 10 BUFFER ETF | NULL |
| NBFR US | INNOVATOR NASDAQ-100 MANAGED 10 BUFFER ETF | NULL |
| XBFR US | INNOVATOR EQUITY MANAGED 10 BUFFER ETF | NULL |
| BFEW US | FT VEST LADDERED US EQUITY EQUAL WEIGHT BUFFER ETF | NULL |
| BUFE US | FT VEST LADDERED EMERGING MARKETS BUFFER ETF | NULL |
| SPBU US | ALLIANZIM BUFFER15 UNCAPPED ALLOCATION ETF | Plain Beta |
| SPBW US | ALLIANZIM BUFFER20 ALLOCATION ETF | Plain Beta |
| SPBX US | ALLIANZIM 6 MONTH BUFFER10 ALLOCATION ETF | Plain Beta |

**Recommended fix:** Reclassify all 9 → `primary_strategy='Defined Outcome'`. The "Managed Buffer" Innovator suite + Allianz "BufferXX" suite are textbook defined-outcome wrappers.
**Type:** SAFE auto-fix (rule: `fund_name LIKE '%Buffer%' AND primary_strategy != 'Defined Outcome'` → set to Defined Outcome).

### 1b. L&I keywords in name but `primary_strategy != 'L&I'` — **5 rows · MED**

| ticker | fund_name | primary_strategy |
|---|---|---|
| GWGB US | TUTTLE CAPITAL INVERSE ESG ETF | Plain Beta |
| HDGE US | ADVISORSHARES RANGER EQUITY BEAR ETF | Risk Mgmt |
| QBER US | TRUESHARES QUARTERLY BEAR HEDGE ETF | Risk Mgmt |
| QBUL US | TRUESHARES QUARTERLY BULL HEDGE ETF | Plain Beta |
| RFIX US | SIMPLIFY BOND BULL ETF | Plain Beta |

**Recommended fix:** MANUAL REVIEW. "Bull/Bear Hedge" funds are typically active long/short, not L&I. GWGB (Inverse ESG) is the only clear L&I. Don't auto-fix — Bull/Bear in fund names is heavily overloaded.

### 1c. Income/CC keywords but `primary_strategy != 'Income'` — **30 rows · HIGH**

| ticker | fund_name | primary_strategy |
|---|---|---|
| APRH US | INNOVATOR PREMIUM INCOME 20 BARRIER ETF - APRIL | Defined Outcome |
| APRJ US | INNOVATOR PREMIUM INCOME 30 BARRIER ETF - APRIL | Defined Outcome |
| BAGY US | AMPLIFY BITCOIN MAX INCOME COVERED CALL ETF | Plain Beta |
| BCCC US | GLOBAL X BITCOIN COVERED CALL ETF | Plain Beta |
| BITK US | TUTTLE CAPITAL BITCOIN 0DTE COVERED CALL ETF | Plain Beta |
| BPI US | GRAYSCALE BITCOIN PREMIUM INCOME ETF | Plain Beta |
| BTCC US | GRAYSCALE BITCOIN COVERED CALL ETF | Plain Beta |
| CEPI US | REX CRYPTO EQUITY PREMIUM INCOME ETF | Plain Beta |
| EHCC US | GLOBAL X ETHEREUM COVERED CALL ETF | Plain Beta |
| EHY US | AMPLIFY ETHEREUM MAX INCOME COVERED CALL ETF | Plain Beta |

**Recommended fix:** SAFE auto-fix for explicit "Covered Call" + "Premium Income" + "BuyWrite" funds → `primary_strategy='Income'`. Innovator "PREMIUM INCOME ## BARRIER" stays as Defined Outcome (barrier wrapper) — these are the exception.
**Note:** REX-issued CEPI is in this list — high visibility miss.

---

## Category 2 — Asset class mismatch

### 2a. Crypto/Bitcoin/Ether in name, `asset_class != 'Crypto'` — **0 rows · LOW**
Clean. No miscoded crypto funds.

### 2b. Bond/Treasury keywords, `asset_class != 'Fixed Income'` — **12 rows · LOW**

All 12 are legitimate hybrid products: currency-hedged EM bond ETFs (`Currency` is intentional — the FX hedge dominates returns) and multi-asset blends (BlackSwan = T-bills + options on SPY).

| ticker | fund_name | asset_class |
|---|---|---|
| AGRH US | ISHARES INTEREST RATE HEDGED U.S. AGGREGATE BOND ETF | Multi-Asset |
| EMLC US | VANECK J. P. MORGAN EM LOCAL CURRENCY BOND ETF | Currency |
| SWAN US | AMPLIFY BLACKSWAN GROWTH & TREASURY CORE ETF | Multi-Asset |
| UCON US | FIRST TRUST SMITH UNCONSTRAINED BOND ETF | Multi-Asset |

**Recommended fix:** None. False positive — these classifications are defensible.

### 2c. Gold/Silver/Oil/Energy keywords, `asset_class != 'Commodity'` — **58 rows · LOW**

Mostly legit: gold/silver MINERS (equity exposure to miners, not the metal) and equity refiners.

**Recommended fix:** None broadly. One worth a manual look: `BTGD` (STKD 100% Bitcoin & 100% Gold) currently `Crypto` — could argue Multi-Asset.

---

## Category 3 — Leverage attribute mismatch

### 3a. "2X/3X" in name but `leverage_ratio` NULL — **0 rows · LOW**
Clean. Leverage backfill is complete for explicit ratios.

### 3b. `leverage_ratio` populated but `direction` NULL — **1 row · LOW**

| ticker | fund_name | leverage_ratio | direction |
|---|---|---:|---|
| MARU US | ALLIANZIM US EQUITY BUFFER15 UNCAPPED MAR ETF | 2.0 | NULL |

**Fix:** Likely shouldn't have a leverage_ratio at all (it's a defined-outcome buffer, not L&I). Strip `leverage_ratio` to NULL. MANUAL REVIEW.

### 3c. `direction` populated but `leverage_ratio` NULL — **2 rows · LOW**

| ticker | fund_name | direction |
|---|---|---|
| QTAC US | Q3 ALL-SEASON TACTICAL ADVANTAGE ETF | neutral |
| SPXB US | PROSHARES BITCOIN-DENOMINATED S&P 500 ETF | neutral |

**Fix:** `direction='neutral'` is conceptually fine without a leverage ratio for non-L&I products. SAFE — no action needed, or strip direction for consistency.

---

## Category 4 — REX flagging

### 4a. `is_rex=True` but issuer not in {REX, T-REX, MicroSectors} — **2 rows · HIGH**

| ticker | fund_name | issuer_display |
|---|---|---|
| OBTC US | OSPREY BITCOIN TRUST | Osprey Funds LLC/USA |
| TLDR US | THE LADDERED T-BILL ETF | REX ETF Trust |

**Fix:**
- `OBTC` — Osprey was acquired by REX (Bitwise wholesale before that?). MANUAL REVIEW: confirm deal status, then decide if `is_rex` stays.
- `TLDR` — issuer_display is the trust name, not canonical. Should be `REX`. SAFE auto-fix.

### 4b. `is_rex=False` but issuer IS REX — **1 row · HIGH**

| ticker | fund_name | issuer_display | is_rex |
|---|---|---|---|
| AXTU US | T-REX 2X LONG AXTI DAILY TARGET ETF | REX | 0 |

**Fix:** SAFE auto-fix → `is_rex=True`. Clear miss for a T-REX product.

### 4c. REX funds with NULL `primary_strategy` — **0 rows · LOW**
Clean.

---

## Category 5 — Single-stock indicator gap

### 5a. `underlier_type='Single Stock'` but `is_singlestock` not Y/True — **423 rows · HIGH (SCHEMA BUG)**

**Root cause:** the `is_singlestock` column is being misused. It was meant to be a Y/N flag, but is currently storing the **underlier ticker symbol**:

| ticker | fund_name | underlier_type | is_singlestock |
|---|---|---|---|
| AAPB US | GRANITESHARES 2X LONG AAPL DAILY ETF | Single Stock | AAPL US |
| AAPU US | DIREXION DAILY AAPL BULL 2X ETF | Single Stock | AAPL US |
| AAPX US | T-REX 2X LONG APPLE DAILY TARGET ETF | Single Stock | AAPL US |
| AAPY US | KURV YIELD PREMIUM STRATEGY APPLE AAPL ETF/DE | Single Stock | AAPL US |
| ABNG US | LEVERAGE SHARES 2X LONG ABNB DAILY ETF | Single Stock | ABNB US |
| ABNY US | YIELDMAX ABNB OPTION INCOME STRATEGY ETF | Single Stock | ABNB US Equity |

**Distinct values in `is_singlestock` column:** mostly tickers like `AAPL US`, `TSLA US`, `NVDA US`, `COIN US`, plus a few `Curncy` and `Comdty` suffixes. Only `Y/N/Unknown` exist as proper flag values; 564 rows in ACTV/PEND have ticker-shaped strings here.

**Fix:** This is the schema/pipeline bug already tracked as **task #101 (`Backfill is_singlestock from underlier_type='Single Stock'`)**. Two-step fix:
1. Move ticker payload from `is_singlestock` → `underlier_name` (or `map_li_underlier`) where missing.
2. Set `is_singlestock = 'Y'` for all 423.

**Type:** SAFE auto-fix once destination column is confirmed not to clobber existing data.

### 5b. `is_singlestock=True` but `underlier_type != 'Single Stock'` — **0 rows · LOW**
N/A given 5a's misuse — flag is never set to "True" in the boolean sense.

---

## Category 6 — Defined Outcome attributes

### 6a. `Defined Outcome` strategy but cap/buffer/barrier/accelerator ALL NULL — **428 of 632 rows · HIGH (68% missing)**

This is a coverage gap, not strictly an error. The Phase 6 backfill (task #52) was marked completed but only populated **204 of 632** Defined Outcome funds. Remaining 428 still have no quantified outcome attributes — meaning the screener can't filter them by cap or buffer level.

**Recommended fix:** Re-run Phase 6 backfill with broader source coverage (Innovator/FT Vest/Allianz prospectuses). MANUAL REVIEW pipeline question — what data source is hitting the wall?

### 6b. `cap_pct` populated but `outcome_type` empty — **2 rows · LOW**

Trivial. SAFE auto-fix — derive `outcome_type` from non-NULL cap/buffer/barrier presence.

---

## Category 7 — Issuer suspicious values

### 7a. Truncated issuer names — **4 rows · HIGH**

| issuer_display | rows |
|---|---:|
| 21Shares Polkadot ETF/Fund Par | 1 |
| 21Shares Sui ETF/Fund Parent | 1 |
| Bitwise Avalanche ETF/Fund Par | 1 |
| Collaborative Investment Serie | 1 |

**Fix:** Bloomberg field truncation at 30 chars. SAFE auto-fix via canonicalization map:
- "21Shares Polkadot ETF/Fund Par" → `21Shares`
- "21Shares Sui ETF/Fund Parent" → `21Shares`
- "Bitwise Avalanche ETF/Fund Par" → `Bitwise`
- "Collaborative Investment Serie" → `Collaborative Investment Series`

### 7b. Whitespace-difference issuers — **0 rows · LOW**
Clean.

### 7c. Issuers with only 1 fund total — **51 distinct issuers · MED**

Notable orphans worth a canonicalization look:

| issuer_display | likely canonical |
|---|---|
| Adaptiv | (real boutique — keep) |
| Amplius | (real — keep) |
| BNY | likely BNY Mellon |
| ETF Architect | (real — keep) |
| ETF Opportunities Trust/IDX Ad | TRUNCATED → IDX Advisors |
| ETF Opportunities Trust/Tuttle | TRUNCATED → Tuttle Capital |
| ETF Series Solutions/Aptus Cap | TRUNCATED → Aptus Capital |
| ETF Series Solutions/Defiance | Defiance |
| GraniteShares ETF Trust | GraniteShares |
| Harbor ETF Trust | Harbor |
| John Hancock Exchange-Traded F | TRUNCATED → John Hancock |
| Listed Funds Trust/Core Altern | TRUNCATED → Core Alternative |
| Osprey Funds LLC/USA | Osprey (or REX if absorbed) |
| Pando Asset AG/Switzerland | Pando |
| RBB ETFs/F/m Investments | F/m Investments |
| REX ETF Trust | REX (← also affects 4a TLDR) |
| Series Portfolio Trust | (generic shell — needs sponsor lookup) |
| T Rowe Price Exchange-Traded F | TRUNCATED → T Rowe Price |
| Teucrium Commodity Trust | Teucrium |
| Tortoise Capital Series Trust | Tortoise |
| Two Roads / Hypatia | Hypatia |
| Ultimus Managers Trust/Q3 Asse | TRUNCATED → Q3 Asset |
| Vegashares ETF Trust | VegaShares |
| Virtus ETF Trust II | Virtus |
| Wedbush Series Trust | (sponsor lookup) |
| iShares Delaware Trust Sponsor | iShares |

**Fix:** MANUAL REVIEW + extend `issuer_canonicalization.csv`. About 18 of these are clearly Bloomberg field truncation that should map to a known parent issuer.

---

## Category 8 — Status oddities

### 8a. `market_status='LIQU'` but `is_active='Y'` — **483 rows · HIGH (SYSTEMATIC BUG)**

The `is_active` boolean is not being updated when funds liquidate. Bloomberg's `market_status` flips to LIQU but the legacy `is_active` flag stays `Y`. 483 dead funds appear active.

**Fix:** SAFE auto-fix. Single SQL:
```sql
UPDATE mkt_master_data SET is_active = 'N' WHERE market_status IN ('LIQU','DLST','EXPD','ACQU') AND is_active='Y';
```
Then patch the ingestion to derive `is_active` from `market_status` going forward.

### 8b. `market_status='PEND'` but inception_date in past — **50 of 113 PEND rows · HIGH**

44% of PEND funds have past inception dates — they've launched but aren't being flipped to ACTV. Examples:

| ticker | fund_name | inception_date |
|---|---|---|
| 2578189D US | GOLDMAN SACHS ACCESS US TREASURY BOND ETF | 2020-07-16 |
| ARMX US | DEFIANCE DAILY TARGET 2X LONG ARM ETF | 2025-05-19 |
| DGOO US | KURV GOOGLE GOOGL 2X SHORT ETF | 2022-12-01 |
| EX US | HASHDEX ETHEREUM STRATEGY ETF | 2023-10-02 |
| EEEE US | 4E QUALITY GROWTH ETF | 2024-12-04 |

**Fix:** MANUAL REVIEW. Some of these likely never launched (PEND filings withdrawn) — should be re-statused to PRNA or DLST. Some genuinely launched and need ACTV flip.

PEND breakdown:
- Total PEND: 113
- Past inception: 50
- Future inception: 38 (legitimately pending)
- NULL inception: 23

### 8c. ACTV with future inception_date — **0 rows · LOW**
Clean.

---

## Top 10 Most-Impactful Fixes (by row count × severity)

| Rank | Fix | Rows | Severity | Type |
|---:|---|---:|---|---|
| 1 | `is_active='N'` for all LIQU funds (#8a) | 483 | HIGH | SAFE auto-fix |
| 2 | `is_singlestock='Y'` + move ticker payload (#5a / #101) | 423 | HIGH | SAFE auto-fix |
| 3 | Phase 6 Defined Outcome attribute backfill (#6a) | 428 | HIGH | MANUAL pipeline work |
| 4 | PEND → ACTV/PRNA reconciliation (#8b) | 50 | HIGH | MANUAL review |
| 5 | Income strategy reclassification — CC/Premium Income funds (#1c) | 28 | HIGH | SAFE auto-fix (excl. Innovator Barrier) |
| 6 | Defined Outcome reclassification — Buffer suite (#1a) | 9 | HIGH | SAFE auto-fix |
| 7 | Issuer truncation cleanup — extend canonicalization map (#7a + #7c truncations) | ~22 | MED | SAFE auto-fix via mapping |
| 8 | `is_rex=True` for AXTU (#4b) | 1 | HIGH | SAFE auto-fix |
| 9 | `issuer_display='REX'` for TLDR (#4a) | 1 | HIGH | SAFE auto-fix |
| 10 | OBTC `is_rex` review (#4a) | 1 | MED | MANUAL review |

---

## Safe vs Manual Summary

**SAFE auto-fixes (~960 rows of immediate cleanup):**
- 8a: 483 LIQU is_active resets
- 5a: 423 is_singlestock backfills (with ticker payload migration)
- 1c: ~28 Income reclassifications
- 1a: 9 Buffer Defined Outcome reclassifications
- 7a + 7c truncations: ~22 issuer canonicalization additions
- 4a (TLDR) + 4b (AXTU): 2 REX flags
- 6b: 2 outcome_type derivations

**MANUAL REVIEW required:**
- 6a: 428 Defined Outcome attribute backfill (needs pipeline work)
- 8b: 50 PEND status reconciliation
- 1b: 5 L&I keyword false positives
- 4a (OBTC): 1 REX status (corp action)
- 7c: ~30 single-fund issuers (real vs orphan)
