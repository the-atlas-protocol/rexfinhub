# Audit ζ — Category Deep Dives
**Date**: 2026-05-05
**Scope**: Targeted quality checks for CC, Crypto, Defined Outcome, and Thematic categories
**DB queried**: `data/etp_tracker.db` (READ-ONLY)

---

## 1. CC — Covered Call / Income (322 ACTV funds)

### `mkt_category_attributes` row coverage

| Status | Count |
|---|---|
| Has `mkt_category_attributes` row | 314 |
| Missing `mkt_category_attributes` row | **8** |

### The 8 missing CC funds

| Ticker | Fund Name | Issuer | Classification Issue |
|---|---|---|---|
| `CWY US` | GRANITESHARES YIELDBOOST CRWV ETF | GraniteShares ETF Trust | YieldBoost on 2x ETF — should have `underlier_is_wrapper=TRUE` |
| `DPRE US` | VIRTUS DUFF & PHELPS REAL ESTATE INCOME ETF | Virtus ETF Trust II | Likely a fixed-income real estate income fund, not a pure CC strategy |
| `HYGM US` | AMPLIFY HYG HIGH YIELD 10% TARGET INCOME ETF | Amplify ETF Trust | HYG overlay — income target, not pure CC |
| `JHDG US` | JOHN HANCOCK HEDGED EQUITY ETF | John Hancock Exchange-Traded F | **Hedged Equity** — should be Risk Mgmt per declared taxonomy, not CC |
| `JUDO US` | JANUS HENDERSON US EQUITY ENHANCED INCOME ETF | Janus Detroit Street Trust | Enhanced income — may be covered call overlay |
| `LQDM US` | AMPLIFY LQD INVESTMENT GRADE 12% TARGET INCOME ETF | Amplify ETF Trust | LQD overlay — fixed-income CC variant |
| `MUYY US` | GRANITESHARES YIELDBOOST MU ETF | GraniteShares ETF Trust | YieldBoost on MU — wrapper=TRUE (MU underlying a 2x ETF) |
| `TMYY US` | GRANITESHARES YIELDBOOST TSM ETF | GraniteShares ETF Trust | YieldBoost on TSM — wrapper=TRUE |

### Key observations
- `JHDG US` is a clear misclassification: Hedged Equity belongs in Risk Mgmt, not CC. No `mkt_category_attributes` row because it was never CC in the first place.
- The three GraniteShares YieldBoost funds (`CWY`, `MUYY`, `TMYY`) are CC wrappers on 2x ETFs — `underlier_is_wrapper=TRUE` per the new taxonomy. They need attributes rows.
- Amplify's `HYGM` and `LQDM` are income-target overlays on bond ETFs — edge cases for the new taxonomy (mechanism=options, but asset_class=Fixed Income).

---

## 2. Crypto (105 ACTV funds)

**Note**: The task instructions cited 160 funds; DB shows 105 ACTV. Likely the 160 figure included non-ACTV / historical rows.

### `is_crypto` vs `etp_category='Crypto'` consistency

| is_crypto value | Count | % |
|---|---|---|
| `'Cryptocurrency'` | 86 | 81.9% |
| NULL | 19 | 18.1% |

**19 funds** in `etp_category='Crypto'` have `is_crypto IS NULL`. These are:

| Ticker | Fund Name | map_crypto_type | Issue |
|---|---|---|---|
| `BCOR US` | GRAYSCALE BITCOIN ADOPTERS ETF | Equity | Equity fund (crypto-adjacent companies), not a direct crypto fund |
| `BITQ US` | BITWISE CRYPTO INDUSTRY INNOVATORS ETF | Equity | Same — equity fund of crypto companies |
| `CRPT US` | FIRST TRUST SKYBRIDGE CRYPTO INDUSTRY AND DIGITAL ECONOMY ETF | Equity | Same |
| `DECO US` | STATE STREET GALAXY DIGITAL ASSET ECOSYSTEM ETF | Equity | Same |
| `EEE US` | CYBER HORNET S&P 500 AND ETHEREUM 75/25 STRATEGY ETF | Derivatives-based; futures-based | Hybrid S&P500/crypto — asset_class should be Multi-Asset |
| `FDIG US` | FIDELITY CRYPTO INDUSTRY AND DIGITAL PAYMENTS ETF | Equity | Equity fund |
| `FMKT US` | THE FREE MARKETS ETF | Hybrid (spot/multi + derivatives) | Multi-asset |
| `GLDB US` | IDX ALTERNATIVE FIAT ETF | Derivatives-based; futures-based | Currency/fiat hedge — not direct crypto |
| `HECO US` | STATE STREET GALAXY HEDGED DIGITAL ASSET ECOSYSTEM ETF | Equity | Equity fund |
| `MNRS US` | GRAYSCALE BITCOIN MINERS ETF | Equity | Equity fund (miners) |
| `NODE US` | VANECK ONCHAIN ECONOMY ETF | Equity | Equity fund |
| `OWNB US` | BITWISE BITCOIN STANDARD CORPORATIONS ETF | Equity | Equity fund (BTC treasury companies) |
| `SATO US` | INVESCO ALERIAN GALAXY CRYPTO ECONOMY ETF | Equity | Equity fund |
| `SPBC US` | SIMPLIFY US EQUITY PLUS BITCOIN STRATEGY ETF | Hybrid (spot/multi + derivatives) | Multi-Asset |
| `SSS US` | CYBER HORNET S&P 500 AND SOLANA 75/25 STRATEGY ETF | Derivatives-based; futures-based | Multi-Asset |
| `STCE US` | SCHWAB CRYPTO THEMATIC ETF | Equity | Equity fund |
| `WGMI US` | COINSHARES BITCOIN MINING ETF | Equity | Equity fund (miners) |
| `WTIP US` | WISDOMTREE INFLATION PLUS FUND | Hybrid (spot/multi + derivatives) | **Completely wrong category** — inflation protection product |
| `XXX US` | CYBER HORNET S&P 500 AND XRP 75/25 STRATEGY ETF | Derivatives-based; futures-based | Multi-Asset |

### Spot vs Futures breakdown (from `map_crypto_type`)

| map_crypto_type | Count |
|---|---|
| Spot Single Asset | 53 |
| Spot Multi-asset; passive | 7 |
| Derivatives-based; futures-based | 14 |
| Equity (crypto-adjacent) | 11 |
| Hybrid (spot/multi + derivatives) | 8 |
| Spot Multi-asset; thematic | 1 |
| Derivatives-based; income | 1 |
| Derivatives-based; leveraged | 2 |
| Spot (unqualified) | 5 |
| NULL | 3 |

### Key finding — Crypto category
**`WTIP US` (WisdomTree Inflation Plus Fund)** is a clear categorization error — an inflation-protection fund has no business in the Crypto category. The 10 Equity-type crypto-adjacent funds (`BCOR`, `BITQ`, `MNRS`, etc.) are borderline — under the new taxonomy these should be `asset_class=Equity / primary_strategy=Plain Beta / sub_strategy=Thematic` with theme=`Blockchain & Crypto`.

---

## 3. Defined Outcome (503 ACTV funds)

**Note**: Task cited 511 funds; DB shows 503 ACTV.

### `cap_pct` + `buffer_pct` pairing

| Has Cap | Has Buffer | Count |
|---|---|---|
| No | No | 503 |

**Both `cap_pct` and `buffer_pct` are 0/NULL for all 503 Defined Outcome funds.** The numeric attribute columns exist in `mkt_master_data` but have never been populated. This is the same Phase 3 gap as the new taxonomy columns — data not yet written.

### Buffer-without-cap check (illegal combo)
**Zero violations found** — because zero funds have any buffer_pct set. Cannot evaluate the illegal combo rule until cap/buffer data is populated.

### `map_defined_category` breakdown

| map_defined_category | Count | Notes |
|---|---|---|
| Buffer | 391 | 77.7% — dominant sub-type (Innovator, FT Vest, etc.) |
| Ladder | 23 | TLDR-type T-Bill ladder funds — **should be Fixed Income per declared taxonomy** |
| Dual Buffer | 19 | Not in declared taxonomy (closest = Dual Directional or Buffer) |
| Accelerator | 16 | Maps to `Growth` in declared taxonomy |
| Defined Volatility | 13 | Not in declared taxonomy |
| Outcome | 12 | Ambiguous — likely Buffer variants |
| Floor | 12 | Matches `Floor` in declared taxonomy |
| Barrier | 8 | Not in declared taxonomy |
| Hedged Equity | 6 | **Wrong primary strategy** — belongs in Risk Mgmt |
| Defined Risk | 2 | Not in declared taxonomy |
| NULL | 1 | Unclassified |

### Key findings — Defined Outcome
1. **Buffer-without-cap is untestable** until numeric attribute columns are populated.
2. **23 Ladder funds** (including TLDR) are misclassified — per Ryu's locked decision, TLDR is `Fixed Income / Plain Beta / duration_bucket=ultra_short`.
3. **6 Hedged Equity** funds are in the wrong primary category — Risk Mgmt per declared taxonomy.
4. **Dual Buffer** (19), **Barrier** (8), **Defined Volatility** (13) are undeclared sub-strategies that need mapping to the canonical taxonomy nodes.

---

## 4. Thematic (350 ACTV funds)

### `map_thematic_category` coverage

| Status | Count |
|---|---|
| Theme set | 348 |
| Theme NULL/empty | **2** |

Coverage is excellent — only 2 funds missing theme assignment.

### Theme name canonicalization audit

Identified canonical issues and duplicates:

| Issue Type | Themes Affected | Fund Count | Recommendation |
|---|---|---|---|
| **Near-duplicate: Space** | `Space` (5) vs `Space & Aerospace` (2) | 7 total | Merge → `Space & Aerospace` |
| **Overlap: AI adjacent** | `Artificial Intelligence` (29) vs `Innovation` (18) | — | Review: some Innovation funds may be AI-adjacent |
| **Overlap: Infrastructure** | `Infrastructure` (34) vs `Tech & Communications` (22) | — | Infrastructure has the largest count — verify it's not a catch-all |
| **Ambiguous: General Thematic** | `General Thematic` (11) | 11 | Placeholder — needs proper assignment |
| **Granularity mismatch: Cannabis** | `Cannabis and Psychedelics` (8) | 8 | Psychedelics is a distinct emerging theme — consider split |
| **Missing: Humanoid Robotics** | `Robotics & Automation` (10) includes humanoid funds | — | New sub-theme candidate per doc's taxonomy_proposals framework |
| **Potential retire** | `Future of Food` (1), `Quantum Computing` (1) | 2 | Only 1 fund each — monitor for liquidation |
| **Potential retire** | `Drones` (1) | 1 | Only 1 fund — may be too thin to stand alone (see DRNZ) |

### Full theme count table

| Theme | Count |
|---|---|
| Infrastructure | 34 |
| Artificial Intelligence | 29 |
| Tech & Communications | 22 |
| Healthcare | 21 |
| Innovation | 18 |
| Electric Car & Battery | 14 |
| Environment | 11 |
| General Thematic | 11 |
| Natural Resources | 11 |
| Defense | 10 |
| Robotics & Automation | 10 |
| Corporate Culture | 8 |
| Cannabis and Psychedelics | 8 |
| Consumer | 8 |
| IPO & SPAC | 8 |
| Blockchain & Crypto | 7 |
| Cloud Computing | 6 |
| EM Tech | 6 |
| Metaverse & Video Gaming | 6 |
| Nuclear | 6 |
| 5G | 4 |
| Clean Energy | 19 (was Clean Energy, counted separately) |
| Low Carbon | 7 |
| Space | 5 |
| Space & Aerospace | 2 |
| Agriculture | 5 |
| FinTech | 5 |
| Inflation | 5 |
| Travel, Vacation & Leisure | 5 |
| Water | 5 |
| Sports & Esports | 7 |
| Strategy | 9 |
| E-Commerce | 3 |
| Housing | 3 |
| Drones | 1 |
| Future of Food | 1 |
| Quantum Computing | 1 |
| NULL | 2 |

### Key finding — Thematic
**`Space` vs `Space & Aerospace`** is the clearest canonicalization error — 7 funds split across two nearly-identical labels. `General Thematic` (11 funds) is a problematic catch-all that needs cleanup. The taxonomy_proposals agent (Phase 8) should target `Humanoid Robotics` as a new sub-category of `Robotics & Automation`.
