# Cross-Referential Integrity Report — Audit delta

**Generated**: 2026-05-05  
**Database**: `data/etp_tracker.db`  
**Scope**: READ-ONLY — no database writes

## Overview

| Check | Description | Severity | Violations |
|---|---|---|---|
| CX-01 | etp_category='LI' vs strategy='Leveraged & Inverse' disagreement | HIGH | 10 |
| CX-02 | etp_category='Crypto' funds where is_crypto != 'Cryptocurrency' | MEDIUM | 19 |
| CX-03 | REX-brand funds (issuer_display IN ('REX','Rex Shares ETFs')) with is_rex != 1 | HIGH | 0 |
| CX-04 | is_rex=1 but issuer_display is not a known REX/MicroSectors brand | MEDIUM | 1 |
| CX-05 | direction='Short' with positive leverage amount (sign convention mismatch) | MEDIUM | 157 |
| CX-06 | etp_category='CC' funds missing a row in mkt_category_attributes | HIGH | 8 |
| CX-07 | map_li_underlier plain-format values with no matching ETF ticker | LOW | 80 |
| CX-08 | ACTV funds with inception_date = 'NaT' (pandas null sentinel leaked to DB) | MEDIUM | 5 |
| CX-09 | ACTV funds with inception_date before 1990-01-01 (pre-ETF era) | MEDIUM | 14 |
| CX-10 | ACTV funds with aum IS NULL and inception_date > 30 days ago | LOW | 0 |

---

## CX-01 — etp_category='LI' vs strategy='Leveraged & Inverse' disagreement

**Severity**: HIGH  
**Violation count**: 10

*etp_category=LI but strategy != 'Leveraged & Inverse' (0 funds): none found.*

### strategy='Leveraged & Inverse' but etp_category != 'LI' (10 funds)

| Ticker | Fund Name | etp_category | strategy |
|---|---|---|---|
| `AXTX US` | TRADR 2X LONG AXTI DAILY ETF | NULL | Leveraged & Inverse |
| `CPNX US` | TRADR 2X LONG CPNG DAILY ETF | NULL | Leveraged & Inverse |
| `LITZ US` | TRADR 2X SHORT LITE DAILY ETF | NULL | Leveraged & Inverse |
| `MPWX US` | TRADR 2X LONG MPWR DAILY ETF | NULL | Leveraged & Inverse |
| `SNDQ US` | TRADR 2X SHORT SNDK DAILY ETF | NULL | Leveraged & Inverse |
| `STXL US` | DEFIANCE DAILY TARGET 2X LONG STX ETF | NULL | Leveraged & Inverse |
| `STXX US` | TRADR 2X LONG STX DAILY ETF | NULL | Leveraged & Inverse |
| `UCOP US` | PROSHARES ULTRA COPPER K-1 FREE ETF | NULL | Leveraged & Inverse |
| `UPAL US` | PROSHARES ULTRA PALLADIUM K-1 FREE ETF | NULL | Leveraged & Inverse |
| `UPLT US` | PROSHARES ULTRA PLATINUM K-1 FREE ETF | NULL | Leveraged & Inverse |

---

## CX-02 — etp_category='Crypto' funds where is_crypto != 'Cryptocurrency'

**Severity**: MEDIUM  
**Violation count**: 19

### Affected funds (19)

| Ticker | Fund Name | is_crypto value |
|---|---|---|
| `BCOR US` | GRAYSCALE BITCOIN ADOPTERS ETF | NULL |
| `BITQ US` | BITWISE CRYPTO INDUSTRY INNOVATORS ETF | NULL |
| `CRPT US` | FIRST TRUST SKYBRIDGE CRYPTO INDUSTRY AND DIGITAL ECONOMY ET | NULL |
| `DECO US` | STATE STREET GALAXY DIGITAL ASSET ECOSYSTEM ETF | NULL |
| `EEE US` | CYBER HORNET S&P 500 AND ETHEREUM 75/25 STRATEGY ETF | NULL |
| `FDIG US` | FIDELITY CRYPTO INDUSTRY AND DIGITAL PAYMENTS ETF | NULL |
| `FMKT US` | THE FREE MARKETS ETF | NULL |
| `GLDB US` | IDX ALTERNATIVE FIAT ETF | NULL |
| `HECO US` | STATE STREET GALAXY HEDGED DIGITAL ASSET ECOSYSTEM ETF | NULL |
| `MNRS US` | GRAYSCALE BITCOIN MINERS ETF | NULL |
| `NODE US` | VANECK ONCHAIN ECONOMY ETF | NULL |
| `OWNB US` | BITWISE BITCOIN STANDARD CORPORATIONS ETF | NULL |
| `SATO US` | INVESCO ALERIAN GALAXY CRYPTO ECONOMY ETF | NULL |
| `SPBC US` | SIMPLIFY US EQUITY PLUS BITCOIN STRATEGY ETF | NULL |
| `SSS US` | CYBER HORNET S&P 500 AND SOLANA 75/25 STRATEGY ETF | NULL |
| `STCE US` | SCHWAB CRYPTO THEMATIC ETF | NULL |
| `WGMI US` | COINSHARES BITCOIN MINING ETF | NULL |
| `WTIP US` | WISDOMTREE INFLATION PLUS FUND | NULL |
| `XXX US` | CYBER HORNET S&P 500 AND XRP 75/25 STRATEGY ETF | NULL |

---

## CX-03 — REX-brand funds (issuer_display IN ('REX','Rex Shares ETFs')) with is_rex != 1

**Severity**: HIGH  
**Violation count**: 0

*Affected funds (0): none found.*

---

## CX-04 — is_rex=1 but issuer_display is not a known REX/MicroSectors brand

**Severity**: MEDIUM  
**Violation count**: 1

> **Note**: MicroSectors is a REX brand. Osprey (OBTC) is an edge case — verify manually.

### Affected funds (1)

| Ticker | Fund Name | issuer_display | is_rex |
|---|---|---|---|
| `OBTC US` | OSPREY BITCOIN TRUST | Osprey | 1 |

---

## CX-05 — direction='Short' with positive leverage amount (sign convention mismatch)

**Severity**: MEDIUM  
**Violation count**: 157

> **Note**: This is a KNOWN CONVENTION ISSUE. The legacy system always stores leverage as a positive magnitude. The new taxonomy requires negative values for short funds. Phase 6 backfill must negate map_li_leverage_amount -> leverage_ratio for all short-direction funds.

### Short funds with positive leverage_amount — top 30 of 157

| Ticker | Fund Name | direction | leverage_amount |
|---|---|---|---|
| `BERZ US` | MICROSECTORS FANG & INNOVATION -3X INVERSE LEVERAGED ETN | Short | 3.0 |
| `BNKD US` | MICROSECTORS US BIG BANKS INDEX -3X INVERSE LEVERAGED ETN | Short | 3.0 |
| `CARD US` | MAX AUTO INDUSTRY -3X INVERSE LEVERAGED ETN | Short | 3.0 |
| `DRV US` | DIREXION DAILY REAL ESTATE BEAR 3X ETF | Short | 3.0 |
| `DULL US` | MICROSECTORS GOLD -3X INVERSE LEVERAGED ETN | Short | 3.0 |
| `EDZ US` | DIREXION DAILY MSCI EMERGING MARKETS BEAR 3X ETF | Short | 3.0 |
| `FAZ US` | DIREXION DAILY FINANCIAL BEAR 3X ETF | Short | 3.0 |
| `FLYD US` | MICROSECTORS TRAVEL 3X INVERSE LEVERAGED ETN | Short | 3.0 |
| `FNGD US` | MICROSECTORS FANG+ INDEX -3X INVERSE LEVERAGED ETNS DUE JANU | Short | 3.0 |
| `GDXD US` | MICROSECTORS GOLD MINERS -3X INVERSE LEVERAGED ETN | Short | 3.0 |
| `HIBS US` | DIREXION DAILY S&P 500 HIGH BETA BEAR 3X ETF | Short | 3.0 |
| `JETD US` | MAX AIRLINES -3X INVERSE LEVERAGED ETN | Short | 3.0 |
| `LABD US` | DIREXION DAILY S&P BIOTECH BEAR 3X ETF | Short | 3.0 |
| `NRGD US` | MICROSECTORS US BIG OIL -3X INVERSE LEVERAGED ETN | Short | 3.0 |
| `OILD US` | MICROSECTORS OIL & GAS EXPLORATION & PRODUCTION -3X INVERSE  | Short | 3.0 |
| `SDOW US` | PROSHARES ULTRAPRO SHORT DOW30 | Short | 3.0 |
| `SMDD US` | PROSHARES ULTRAPRO SHORT MIDCAP400 | Short | 3.0 |
| `SOXS US` | DIREXION DAILY SEMICONDUCTOR BEAR 3X ETF | Short | 3.0 |
| `SPXS US` | DIREXION DAILY S&P 500 BEAR 3X ETF | Short | 3.0 |
| `SPXU US` | PROSHARES ULTRAPRO SHORT S&P 500 | Short | 3.0 |
| `SQQQ US` | PROSHARES ULTRAPRO SHORT QQQ | Short | 3.0 |
| `SRTY US` | PROSHARES ULTRAPRO SHORT RUSSELL2000 | Short | 3.0 |
| `TECS US` | DIREXION DAILY TECHNOLOGY BEAR 3X ETF | Short | 3.0 |
| `TMV US` | DIREXION DAILY 20+ YEAR TREASURY BEAR 3X ETF | Short | 3.0 |
| `TTT US` | PROSHARES ULTRAPRO SHORT 20+ YEAR TREASURY | Short | 3.0 |
| `TYO US` | DIREXION DAILY 7-10 YEAR TREASURY BEAR 3X ETF | Short | 3.0 |
| `TZA US` | DIREXION DAILY SMALL CAP BEAR 3X ETF | Short | 3.0 |
| `WEBS US` | DIREXION DAILY DOW JONES INTERNET BEAR 3X ETF | Short | 3.0 |
| `WTID US` | MICROSECTORS ENERGY -3X INVERSE LEVERAGED ETN | Short | 3.0 |
| `YANG US` | DIREXION DAILY FTSE CHINA BEAR 3X ETF | Short | 3.0 |

---

## CX-06 — etp_category='CC' funds missing a row in mkt_category_attributes

**Severity**: HIGH  
**Violation count**: 8

### Affected funds (8)

| Ticker | Fund Name | issuer_display | cc_type |
|---|---|---|---|
| `CWY US` | GRANITESHARES YIELDBOOST CRWV ETF | GraniteShares | NULL |
| `DPRE US` | VIRTUS DUFF & PHELPS REAL ESTATE INCOME ETF | Virtus | NULL |
| `HYGM US` | AMPLIFY HYG HIGH YIELD 10% TARGET INCOME ETF | Amplify | NULL |
| `JHDG US` | JOHN HANCOCK HEDGED EQUITY ETF | John Hancock | NULL |
| `JUDO US` | JANUS HENDERSON US EQUITY ENHANCED INCOME ETF | Janus Henderson | NULL |
| `LQDM US` | AMPLIFY LQD INVESTMENT GRADE 12% TARGET INCOME ETF | Amplify | NULL |
| `MUYY US` | GRANITESHARES YIELDBOOST MU ETF | GraniteShares | NULL |
| `TMYY US` | GRANITESHARES YIELDBOOST TSM ETF | GraniteShares | NULL |

---

## CX-07 — map_li_underlier plain-format values with no matching ETF ticker

**Severity**: LOW  
**Violation count**: 80

> **Note**: 168 Bloomberg-format underliers (e.g., 'AAPL US') also have no ETF ticker match. This is EXPECTED — those are stock/commodity tickers. Only the plain-format values above are potentially problematic (typos, short ticker variants, or index codes that should reference an ETF underlier).

### Plain-format orphan underliers — top 30 of 80

| map_li_underlier | Fund count using this underlier |
|---|---|
| `AAOI` | 1 |
| `ADBE` | 1 |
| `AMZ` | 1 |
| `AMZN` | 1 |
| `APH` | 1 |
| `APLD` | 1 |
| `ASML` | 1 |
| `ASTS` | 1 |
| `AXP` | 1 |
| `BABA` | 1 |
| `BE` | 1 |
| `BIGOIL` | 2 |
| `Basket` | 3 |
| `CEFX` | 1 |
| `CLSK` | 1 |
| `COHR` | 1 |
| `COPX` | 1 |
| `COST` | 1 |
| `CRML` | 1 |
| `DBODIXX` | 1 |
| `DJTU UA` | 1 |
| `DJUSDIVT` | 1 |
| `DNN` | 1 |
| `EWY US Equity` | 1 |
| `FCX` | 1 |
| `FIGR` | 1 |
| `GLW` | 1 |
| `HL` | 1 |
| `IBM` | 1 |
| `IREN` | 1 |

---

## CX-08 — ACTV funds with inception_date = 'NaT' (pandas null sentinel leaked to DB)

**Severity**: MEDIUM  
**Violation count**: 5

### Affected funds (5)

| Ticker | Fund Name | issuer_display | etp_category |
|---|---|---|---|
| `IBCB GR` | ISHARES EMERGING ASIA LOCAL GOVT BOND UCITS ETF | iShares | NULL |
| `JPM US` | JPMORGAN CHASE & CO | JP Morgan | NULL |
| `OBNB US` | OSPREY BNB CHAIN TRUST | Osprey | NULL |
| `PHBI US` | PHARMAGREEN BIOTECH INC | NULL | NULL |
| `PINS US` | PINTEREST INC | NULL | NULL |

---

## CX-09 — ACTV funds with inception_date before 1990-01-01 (pre-ETF era)

**Severity**: MEDIUM  
**Violation count**: 14

### Affected funds (14)

| Ticker | Fund Name | inception_date | issuer_display | fund_type |
|---|---|---|---|---|
| `FTMU US` | FRANKLIN MUNICIPAL INCOME ETF | 1976-12-31 00:00:00 | Franklin Templeton | ETF |
| `LDRX US` | SGI ENHANCED MARKET LEADERS ETF | 1977-01-03 00:00:00 | NULL | ETF |
| `FTCA US` | FRANKLIN CALIFORNIA MUNICIPAL INCOME ETF | 1983-04-29 00:00:00 | Franklin Templeton | ETF |
| `FTNY US` | FRANKLIN NEW YORK MUNICIPAL INCOME ETF | 1983-09-02 00:00:00 | Franklin Templeton | ETF |
| `JMTG US` | JPMORGAN MORTGAGE-BACKED SECURITIES ETF | 1983-12-31 00:00:00 | JP Morgan | ETF |
| `EVTR US` | EATON VANCE TOTAL RETURN BOND ETF | 1984-11-14 00:00:00 | Eaton Vance | ETF |
| `FTMH US` | FRANKLIN MUNICIPAL HIGH YIELD ETF | 1985-09-09 00:00:00 | Franklin Templeton | ETF |
| `AMUN US` | ABRDN ULTRA SHORT MUNICIPAL INCOME ACTIVE ETF | 1986-03-17 00:00:00 | abrdn | ETF |
| `NYM US` | AB NEW YORK INTERMEDIATE MUNICIPAL ETF | 1989-01-09 00:00:00 | AB (AllianceBernstein) | ETF |
| `HYBX US` | TCW HIGH YIELD BOND ETF | 1989-02-01 00:00:00 | TCW | ETF |
| `FTPA US` | FRANKLIN PENNSYLVANIA MUNICIPAL INCOME ETF | 1989-07-21 00:00:00 | Franklin Templeton | ETF |
| `FTMA US` | FRANKLIN MASSACHUSETTS MUNICIPAL INCOME ETF | 1989-10-23 00:00:00 | Franklin Templeton | ETF |
| `FTMN US` | FRANKLIN MINNESOTA MUNICIPAL INCOME ETF | 1989-10-23 00:00:00 | Franklin Templeton | ETF |
| `FTOH US` | FRANKLIN OHIO MUNICIPAL INCOME ETF | 1989-10-23 00:00:00 | Franklin Templeton | ETF |

---

## CX-10 — ACTV funds with aum IS NULL and inception_date > 30 days ago

**Severity**: LOW  
**Violation count**: 0

*Affected funds — top 30 of 0: none found.*

---

## Prioritised fix list

### HIGH severity (fix before next pipeline run)
- **CX-01** (10 violations): etp_category='LI' vs strategy='Leveraged & Inverse' disagreement
- **CX-06** (8 violations): etp_category='CC' funds missing a row in mkt_category_attributes

### MEDIUM severity (fix this week)
- **CX-05** (157 violations): direction='Short' with positive leverage amount (sign convention mismatch)
- **CX-02** (19 violations): etp_category='Crypto' funds where is_crypto != 'Cryptocurrency'
- **CX-09** (14 violations): ACTV funds with inception_date before 1990-01-01 (pre-ETF era)
- **CX-08** (5 violations): ACTV funds with inception_date = 'NaT' (pandas null sentinel leaked to DB)
- **CX-04** (1 violations): is_rex=1 but issuer_display is not a known REX/MicroSectors brand

### LOW severity (track; fix in Phase 6)
- **CX-07** (80 violations): map_li_underlier plain-format values with no matching ETF ticker