# Attribute Completeness Report — Audit gamma

**Generated**: 2026-05-05  
**Database**: `data/etp_tracker.db`  
**Scope**: `mkt_master_data WHERE market_status='ACTV'` (5,144 funds)

## Context

The new taxonomy columns (`primary_strategy`, `leverage_ratio`, `direction`, etc.)
were added in Phase 2 of the classification plan but the backfill pipeline (Phase 6)
has not run yet. All new column counts are 0%. This audit therefore uses LEGACY
columns as proxies to measure real attribute completeness. Where a legacy proxy
exists, the finding is labelled HIGH or CRITICAL. Where only new columns apply,
the finding is labelled LOW (expected gap pending Phase 6).

### Strategy bucket mapping (legacy → new taxonomy)

| primary_strategy | Legacy filter |
|---|---|
| L&I | `etp_category = 'LI'` |
| Income | `etp_category = 'CC'` |
| Defined Outcome | `etp_category = 'Defined'` |
| Plain Beta | `etp_category = 'Thematic'` + NULL-category plain strategies |
| Risk Mgmt | `strategy = 'Alternative'` |

---

## L&I

**Fund count**: 595

### Attribute population

| Attribute | Populated | % | Severity |
|---|---|---|---|
| `leverage_ratio (map_li_leverage_amount)` | 576 / 595 | 96.8% | HIGH |
| `direction (map_li_direction)` | 595 / 595 | 100.0% | CRITICAL |
| `reset_period (proxy: uses_derivatives)` | 0 / 595 | 0.0% | HIGH |
| `mechanism (proxy: uses_swaps)` | 0 / 595 | 0.0% | HIGH |
| `underlier_name (map_li_underlier)` | 387 / 595 | 65.0% | CRITICAL |

### Fix queue — missing leverage_ratio (19 shown)

| Ticker | Fund Name |
|---|---|
| `BEGS US` | RAREVIEW 2X BULL CRYPTOCURRENCY & PRECIOUS METALS ETF |
| `BTGD US` | STKD 100% BITCOIN & 100% GOLD ETF |
| `CHNL US` | CHAINLINK ETF |
| `HCMT US` | DIREXION HCM TACTICAL ENHANCED US ETF |
| `MVPL US` | MILLER VALUE PARTNERS LEVERAGE ETF |
| `ORR US` | MILITIA LONG/SHORT EQUITY ETF |
| `RAAA US` | RECKONER YIELD ENHANCED AAA CLO ETF |
| `RSBA US` | RETURN STACKED BONDS & MERGER ARBITRAGE ETF |
| `RSBT US` | RETURN STACKED BONDS & MANAGED FUTURES ETF |
| `RSBY US` | RETURN STACKED BONDS & FUTURES YIELD ETF |
| `RSSB US` | RETURN STACKED GLOBAL STOCKS & BONDS ETF |
| `RSST US` | RETURN STACKED US STOCKS & MANAGED FUTURES ETF |
| `RSSX US` | RETURN STACKED U.S. STOCKS & GOLD/BITCOIN ETF |
| `RSSY US` | RETURN STACKED U.S. STOCKS & FUTURES YIELD ETF |
| `STLR US` | STELLAR ETF |
| `TDAX US` | TDAQ LIFT ETF |
| `TSYX US` | TSPY LIFT ETF |
| `UPSD US` | APTUS LARGE CAP UPSIDE ETF |
| `WTIB US` | USCF OIL PLUS BITCOIN STRATEGY FUND |

### Fix queue — missing underlier_name (30 shown)

| Ticker | Fund Name |
|---|---|
| `ADZCF US` | DB AGRICULTURE SHORT EXCHANGE TRADED NOTES |
| `AIBD US` | DIREXION DAILY AI AND BIG DATA BEAR 2X ETF |
| `AIBU US` | DIREXION DAILY AI AND BIG DATA BULL 2X ETF |
| `BDDXF US` | DB BASE METALS DOUBLE LONG EXCHANGE TRADED NOTES |
| `BIB US` | PROSHARES ULTRA NASDAQ BIOTECHNOLOGY |
| `BIS US` | PROSHARES ULTRASHORT NASDAQ BIOTECHNOLOGY |
| `BOMMF US` | DB BASE METALS DOUBLE SHORT EXCHANGE TRADED NOTES |
| `BOSXF US` | DB BASE METALS SHORT EXCHANGE TRADED NOTES |
| `BRZU US` | DIREXION DAILY MSCI BRAZIL BULL 2X ETF |
| `BZQ US` | PROSHARES ULTRASHORT MSCI BRAZIL CAPPED |
| `CHAU US` | DIREXION DAILY CSI 300 CHINA A SHARE BULL 2X ETF |
| `CHNL US` | CHAINLINK ETF |
| `CHNU US` | 2X CHAINLINK ETF |
| `CPXR US` | USCF DAILY TARGET 2X COPPER INDEX ETF |
| `CRDX US` | 2X CARDANO ETF |
| `CSM US` | PROSHARES LARGE CAP CORE PLUS |
| `CURE US` | DIREXION DAILY HEALTHCARE BULL 3X ETF |
| `CWEB US` | DIREXION DAILY CSI CHINA INTERNET INDEX BULL 2X ETF |
| `CXRN US` | TEUCRIUM 2X DAILY CORN ETF |
| `DAGXF US` | DB AGRICULTURE DOUBLE LONG EXCHANGE TRADED NOTES |
| `DDM US` | PROSHARES ULTRA DOW30 |
| `DDPXF US` | DB COMMODITY SHORT EXCHANGE TRADED NOTES |
| `DEENF US` | DB COMMODITY DOUBLE SHORT EXCHANGE TRADED NOTES |
| `DFEN US` | DIREXION DAILY AEROSPACE & DEFENSE BULL 3X ETF |
| `DGP US` | DB GOLD DOUBLE LONG EXCHANGE TRADED NOTES |
| `DGZ US` | DB GOLD SHORT EXCHANGE TRADED NOTES |
| `DIG US` | PROSHARES ULTRA ENERGY |
| `DOG US` | PROSHARES SHORT DOW30 |
| `DPST US` | DIREXION DAILY REGIONAL BANKS BULL 3X ETF |
| `DRIP US` | DIREXION DAILY S&P OIL & GAS EXP. & PROD. BEAR 2X ETF |

---

## Income

**Fund count**: 322

### Attribute population

| Attribute | Populated | % | Severity |
|---|---|---|---|
| `underlier_name (map_cc_underlier)` | 113 / 322 | 35.1% | CRITICAL |
| `mechanism (cc_type)` | 314 / 322 | 97.5% | HIGH |
| `sub_strategy (cc_category)` | 314 / 322 | 97.5% | HIGH |
| `distribution_freq` | 0 / 322 | 0.0% | LOW |

### Fix queue — missing underlier_name (30 shown)

| Ticker | Fund Name |
|---|---|
| `ACEI US` | INNOVATOR EQUITY AUTOCALLABLE INCOME STRATEGY ETF |
| `ACII US` | INNOVATOR INDEX AUTOCALLABLE INCOME STRATEGY ETF |
| `ACIO US` | APTUS COLLARED INVESTMENT OPPORTUNITY ETF |
| `ACKY US` | VISTASHARES TARGET 15 ACKTIVIST SELECT INCOME ETF |
| `ACYN US` | FT VEST LADDERED AUTOCALLABLE BARRIER & INCOME ETF |
| `ACYS US` | FT VEST LADDERED AUTOCALLABLE BARRIER & RESILIENT INCOME ETF |
| `AIPI US` | REX AI EQUITY PREMIUM INCOME ETF |
| `ATCL US` | REX AUTOCALLABLE INCOME ETF |
| `BAGY US` | AMPLIFY BITCOIN MAX INCOME COVERED CALL ETF |
| `BALI US` | ISHARES U.S. LARGE CAP PREMIUM INCOME ACTIVE ETF |
| `BALQ US` | ISHARES NASDAQ PREMIUM INCOME ACTIVE ETF |
| `BIGY US` | YIELDMAX TARGET 12 BIG 50 OPTION INCOME ETF |
| `BITY US` | AMPLIFY BITCOIN 2% MONTHLY OPTION INCOME ETF |
| `BLOX US` | NICHOLAS CRYPTO INCOME ETF |
| `BNDY US` | HORIZON CORE BOND ETF |
| `BPI US` | GRAYSCALE BITCOIN PREMIUM INCOME ETF |
| `BTCC US` | GRAYSCALE BITCOIN COVERED CALL ETF |
| `BTCI US` | NEOS BITCOIN HIGH INCOME ETF |
| `BUCK US` | SIMPLIFY TREASURY OPTION INCOME ETF |
| `BUYW US` | MAIN BUYWRITE ETF |
| `BWVTF US` | IPATH CBOE S&P 500 BUYWRITE INDEX ETN |
| `CAGE US` | CALAMOS AUTOCALLABLE GROWTH ETF |
| `CAIE US` | CALAMOS AUTOCALLABLE INCOME ETF |
| `CAIQ US` | CALAMOS NASDAQ AUTOCALLABLE INCOME ETF |
| `CANQ US` | CALAMOS NASDAQ EQUITY & INCOME ETF |
| `CEGI LN` | REX CRYPTO EQUITY INCOME & GROWTH UCITS ETF |
| `CEPI US` | REX CRYPTO EQUITY PREMIUM INCOME ETF |
| `CHPY US` | YIELDMAX SEMICONDUCTOR PORTFOLIO OPTION INCOME ETF |
| `CSHI US` | NEOS ENHANCED INCOME 1-3 MONTH T-BILL ETF |
| `CVRD US` | MADISON COVERED CALL ETF |

### Fix queue — missing mechanism (8 shown)

| Ticker | Fund Name |
|---|---|
| `CWY US` | GRANITESHARES YIELDBOOST CRWV ETF |
| `DPRE US` | VIRTUS DUFF & PHELPS REAL ESTATE INCOME ETF |
| `HYGM US` | AMPLIFY HYG HIGH YIELD 10% TARGET INCOME ETF |
| `JHDG US` | JOHN HANCOCK HEDGED EQUITY ETF |
| `JUDO US` | JANUS HENDERSON US EQUITY ENHANCED INCOME ETF |
| `LQDM US` | AMPLIFY LQD INVESTMENT GRADE 12% TARGET INCOME ETF |
| `MUYY US` | GRANITESHARES YIELDBOOST MU ETF |
| `TMYY US` | GRANITESHARES YIELDBOOST TSM ETF |

---

## Defined Outcome

**Fund count**: 503

### Attribute population

| Attribute | Populated | % | Severity |
|---|---|---|---|
| `sub_strategy (map_defined_category)` | 502 / 503 | 99.8% | CRITICAL |
| `mechanism (proxy: uses_derivatives)` | 0 / 503 | 0.0% | HIGH |
| `cap_pct (new col — Phase 6 pending)` | 0 / 503 | 0.0% | LOW |
| `buffer_pct (new col — Phase 6 pending)` | 0 / 503 | 0.0% | LOW |
| `outcome_period_months (new col — Phase 6 pending)` | 0 / 503 | 0.0% | LOW |

### Fix queue — missing sub_strategy (1 shown)

| Ticker | Fund Name |
|---|---|
| `TLDR US` | THE LADDERED T-BILL ETF |

---

## Plain Beta

**Fund count**: 3,586
 (350 Thematic + 3,236 broad plain beta)

### Attribute population

| Attribute | Populated | % | Severity |
|---|---|---|---|
| `sub_strategy / theme (map_thematic_category) [Thematic only]` | 348 / 350 | 99.4% | CRITICAL |
| `mechanism (proxy: uses_derivatives) [Thematic]` | 323 / 350 | 92.3% | HIGH |
| `mechanism (proxy: uses_derivatives) [Plain Beta]` | 2,761 / 3,236 | 85.3% | HIGH |
| `concentration (proxy: is_singlestock) [Plain Beta]` | 39 / 3,236 | 1.2% | LOW |

---

## Risk Mgmt

**Fund count**: 82

### Attribute population

| Attribute | Populated | % | Severity |
|---|---|---|---|
| `mechanism (new col — Phase 6 pending)` | 0 / 82 | 0.0% | LOW |
| `sub_strategy (new col — Phase 6 pending)` | 0 / 82 | 0.0% | LOW |
| `mechanism (proxy: uses_derivatives)` | 70 / 82 | 85.4% | HIGH |

### Fix queue — sample funds (no sub_strategy) (15 shown)

| Ticker | Fund Name |
|---|---|
| `AAVM US` | ALPHA ARCHITECT GLOBAL FACTOR EQUITY ETF |
| `ADPV US` | ADAPTIV SELECT ETF |
| `AGRH US` | ISHARES INTEREST RATE HEDGED U.S. AGGREGATE BOND ETF |
| `AHLT US` | AMERICAN BEACON AHL TREND ETF |
| `AINT US` | FINQ DOLLAR NEUTRAL US LARGE CAP AI-MANAGED EQUITY ETF |
| `ALLW US` | STATE STREET BRIDGEWATER ALL WEATHER ETF |
| `ARB US` | ALTSHARES MERGER ARBITRAGE ETF |
| `ASGM US` | VIRTUS ALPHASIMPLEX GLOBAL MACRO ETF |
| `ASMF US` | VIRTUS ALPHASIMPLEX MANAGED FUTURES ETF |
| `BENJ US` | HORIZON LANDMARK ETF |
| `BOXX US` | ALPHA ARCHITECT 1-3 MONTH BOX ETF |
| `BTAL US` | AGF US MARKET NEUTRAL ANTI-BETA FUND |
| `CBLS US` | CLOUGH HEDGED EQUITY ETF |
| `CBOX US` | CALAMOS TAX-AWARE COLLATERAL ETF |
| `CEW US` | WISDOMTREE EMERGING CURRENCY STRATEGY FUND |

---

## Summary — worst attributes per strategy

| Strategy | Worst attribute | % populated |
|---|---|---|
| L&I | `reset_period (proxy: uses_derivatives)` | 0.0% |
| Income | `distribution_freq` | 0.0% |
| Defined Outcome | `mechanism (proxy: uses_derivatives)` | 0.0% |
| Plain Beta | `concentration (proxy: is_singlestock) [Plain Beta]` | 1.2% |
| Risk Mgmt | `mechanism (new col — Phase 6 pending)` | 0.0% |

---

## Notes

- `reset_period` and `mechanism` have no direct legacy column for L&I. The proxy
  columns (`uses_derivatives`, `uses_swaps`) have 0% population for the LI bucket,
  which likely reflects a Bloomberg data gap rather than a true missing attribute.
- All 0% LOW-severity findings are expected: these are new Phase 2 columns that
  Phase 6 (LLM backfill) will populate. They appear here to establish the baseline.
- `distribution_freq` for Income has no legacy proxy — will require Phase 6.
- Risk Mgmt has no dedicated `etp_category` in the legacy system; the `strategy=
  'Alternative'` proxy undercounts (hedged equity, trend-following, etc. may be
  in NULL-category funds). True bucket size unknown until Phase 6 classifies them.