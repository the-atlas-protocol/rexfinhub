# Audit Beta — Classification Correctness Report

**Generated**: 2026-05-06 03:52 UTC  
**Sample**: 482 funds (up to 100 per bucket)  
**Method**: Rule-based name-marker validation  

---

## Per-Bucket Summary

| Bucket | Sampled | CONFIRMED | SUSPECT | AMBIGUOUS | SUSPECT % |
|--------|---------|-----------|---------|-----------|-----------|
| Plain Beta | 100 | 90 | 10 | 0 | 10.0% |
| L&I | 100 | 98 | 2 | 0 | 2.0% |
| Defined Outcome | 100 | 84 | 14 | 2 | 14.0% |
| Income | 100 | 88 | 8 | 4 | 8.0% |
| Risk Mgmt | 82 | 47 | 22 | 13 | 26.8% |

**Overall SUSPECT**: 56 / 482 (11.6%)  
**Overall AMBIGUOUS**: 19  
**Overall CONFIRMED**: 407  

---

## Top 20 Most-Suspect Funds

| # | Ticker | Fund Name | Strategy | Suspected | Why |
|---|--------|-----------|----------|-----------|-----|
| 1 | DRKY US | VISTASHARES TARGET 15 DRUKMACRO DISTRIBUTION ETF | Plain Beta | Risk Mgmt | Plain Beta fund name contains non-beta marker(s): ['MACRO'] |
| 2 | GVIP US | GOLDMAN SACHS HEDGE INDUSTRY VIP ETF | Plain Beta | Risk Mgmt | Plain Beta fund name contains non-beta marker(s): ['HEDG', 'HEDGE'] |
| 3 | ATTR US | ARIN TACTICAL TAIL RISK ETF | Plain Beta | Risk Mgmt | Plain Beta fund name contains non-beta marker(s): ['TAIL', 'TACTICAL'] |
| 4 | SAMM US | STRATEGAS MACRO MOMENTUM ETF | Plain Beta | Risk Mgmt | Plain Beta fund name contains non-beta marker(s): ['MACRO'] |
| 5 | CDC US | VICTORYSHARES US EQ INCOME ENHANCED VOLATILITY WTD ETF | Plain Beta | Income | Plain Beta fund name contains non-beta marker(s): ['INCOME'] |
| 6 | ONEY US | STATE STREET SPDR RUSSELL 1000 YIELD FOCUS ETF | Plain Beta | Income | Plain Beta fund name contains non-beta marker(s): ['YIELD'] |
| 7 | UDI US | USCF DIVIDEND INCOME FUND | Plain Beta | Income | Plain Beta fund name contains non-beta marker(s): ['INCOME'] |
| 8 | SELV US | SEI ENHANCED LOW VOLATILITY US LARGE CAP ETF | Plain Beta | Risk Mgmt | Plain Beta fund name contains non-beta marker(s): ['LOW VOLATILITY'] |
| 9 | GGM US | GGM MACRO ALIGNMENT ETF | Plain Beta | Risk Mgmt | Plain Beta fund name contains non-beta marker(s): ['MACRO'] |
| 10 | HDGE US | ADVISORSHARES RANGER EQUITY BEAR ETF | Plain Beta | L&I | Plain Beta fund name contains non-beta marker(s): ['BEAR'] |
| 11 | TSL US | GRANITESHARES 1.25 LONG TSLA DAILY ETF | L&I | Unknown | L&I fund name missing expected leverage/direction marker |
| 12 | BTGD US | STKD 100% BITCOIN & 100% GOLD ETF | L&I | Unknown | L&I fund name missing expected leverage/direction marker |
| 13 | TLDR US | THE LADDERED T-BILL ETF | Defined Outcome | Unknown | Defined Outcome fund name missing expected buffer/outcome marker |
| 14 | CPSY US | CALAMOS S&P 500 STRUCTURED ALT PROTECTION ETF - JANUARY | Defined Outcome | Unknown | Defined Outcome fund name missing expected buffer/outcome marker |
| 15 | CPSU US | CALAMOS S&P 500 STRUCTURED ALT PROTECTION ETF - JUNE | Defined Outcome | Unknown | Defined Outcome fund name missing expected buffer/outcome marker |
| 16 | CBTO US | CALAMOS BITCOIN 80 SERIES STRUCTURED ALT PROTECTION ETF... | Defined Outcome | Unknown | Defined Outcome fund name missing expected buffer/outcome marker |
| 17 | CPSO US | CALAMOS S&P 500 STRUCTURED ALT PROTECTION ETF - OCTOBER | Defined Outcome | Unknown | Defined Outcome fund name missing expected buffer/outcome marker |
| 18 | CPRY US | CALAMOS RUSSELL 2000 STRUCTURED ALT PROTECTION ETF - JA... | Defined Outcome | Unknown | Defined Outcome fund name missing expected buffer/outcome marker |
| 19 | UXOC US | FT VEST US EQUITY UNCAPPED ACCELERATOR ETF - OCTOBER | Defined Outcome | Unknown | Defined Outcome fund name missing expected buffer/outcome marker |
| 20 | CPRO US | CALAMOS RUSSELL 2000 STRUCTURED ALT PROTECTION ETF - OC... | Defined Outcome | Unknown | Defined Outcome fund name missing expected buffer/outcome marker |

---

## Methodology

Each fund name is uppercased and scanned for keyword markers per strategy:

- **L&I**: `2X`, `3X`, `LONG`, `SHORT`, `INVERSE`, `BULL`, `BEAR`, `LEVERAG`, `ULTRA`, `DIREXION`, `LEVERAGED`
- **Income**: `COVERED CALL`, `BUY-WRITE`, `YIELDMAX`, `INCOME`, `PREMIUM`, `0DTE`, `WEEKLYPAY`, `YIELD`, `OPTION INCOME`, `AUTOCALLABLE`
- **Defined Outcome**: `BUFFER`, `FLOOR`, `ACCELERATED`, `CAP`, `OUTCOME`, `DEFINED`, `STRUCTURED OUTCOME`, `BARRIER`, `DEFINED PROTECTION`
- **Risk Mgmt**: `HEDGED`, `RISK`, `MANAGED FUTURES`, `TAIL`, `DEFENSE`, `MANAGED RISK`, `MANAGED VOLATILITY`, `LOW VOLATILITY`, `MINIMUM VOLATILITY`, `ALTERNATIVE`
- **Plain Beta**: CONFIRMED if none of the above markers fire; SUSPECT if any fire

### Caveats

- Name-only heuristic — some funds (e.g., ProShares, Direxion) are L&I without encoding it in their product name
- Defined Outcome funds (TrueShares, AllianzIM) sometimes use series date suffixes — may be under-detected
- SUSPECT does not mean wrong — it is a flag for human review

---

*Output file*: `docs/classification_qa_residue.csv`  
*Script*: `scripts/audit_classification_correctness.py`  