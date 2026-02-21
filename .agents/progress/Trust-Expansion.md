# Trust Expansion Discovery Report
Generated: 2026-02-20

## Summary
- Bloomberg issuers checked: 366
- ETN issuers skipped: 11
- Already tracked (matched existing): 129
- Candidates evaluated via EDGAR: 98
- **Trusts added: 72**
- Skipped (no 485 filings): 73
- EDGAR search errors (DNS/rate limit): 26
- No unverified CIKs added

## Process
1. Extracted 366 unique issuers from Bloomberg `data_import` sheet
2. Filtered 11 ETN issuers (BMO, Deutsche Bank, Barclays, UBS ETRACS, JP Morgan, Goldman Sachs ETNs)
3. Matched 129 issuers against existing 122 trusts in trusts.py
4. Searched remaining 226 candidates against EDGAR full-text search API (`efts.sec.gov/LATEST/search-index`)
5. Verified each discovered CIK via SEC submissions JSON (`data.sec.gov/submissions/CIK{padded}.json`)
6. Filtered results to exclude mutual funds, variable insurance trusts, and non-ETF entities
7. Added 72 verified ETF trusts to `etp_tracker/trusts.py`

## Added Trusts (72)

### Defined Outcome (2)
| Trust Name | CIK | Category |
|-----------|-----|----------|
| Innovator ETFs Trust II | 1595106 | Defined Outcome |
| PGIM Rock ETF Trust | 1992104 | Defined Outcome |

### Commodity / Thematic (11)
| Trust Name | CIK | Category |
|-----------|-----|----------|
| Sprott Funds Trust | 1728683 | Commodity/Thematic |
| USCF ETF Trust | 1597389 | Commodity |
| Tema ETF Trust | 1944285 | Thematic |
| Siren ETF Trust | 1796383 | Thematic |
| SP Funds Trust | 1989916 | Thematic |
| Abacus FCF ETF Trust | 1604813 | Thematic |
| Truth Social Funds | 1040674 | Thematic |
| AltShares Trust | 1779306 | Thematic |
| Reality Shares ETF Trust | 1573496 | Thematic |
| Procure ETF Trust II | 1727398 | Thematic |
| Procure ETF Trust I | 1704174 | Thematic |

### First Trust (additional) (3)
| Trust Name | CIK | Category |
|-----------|-----|----------|
| First Trust Exchange-Traded Fund VI | 1552740 | Multi-Strategy |
| First Trust Exchange-Traded Fund V | 1549548 | Multi-Strategy |
| First Trust Exchange-Traded AlphaDEX Fund | 1383496 | Multi-Strategy |

### Large Platform ETF Trusts (21)
| Trust Name | CIK | Category |
|-----------|-----|----------|
| SPDR Index Shares Funds | 1168164 | Multi-Strategy |
| iShares Trust | 1100663 | Multi-Strategy |
| iShares U.S. ETF Trust | 1524513 | Multi-Strategy |
| Invesco Exchange-Traded Fund Trust | 1209466 | Multi-Strategy |
| Gabelli ETFs Trust | 1748425 | Multi-Strategy |
| Columbia ETF Trust | 1415845 | Multi-Strategy |
| Columbia ETF Trust I | 1551950 | Multi-Strategy |
| Columbia ETF Trust II | 1450501 | Multi-Strategy |
| Capital Group Equity ETF Trust I | 2034928 | Multi-Strategy |
| Janus Detroit Street Trust | 1500604 | Multi-Strategy |
| Putnam ETF Trust | 1845809 | Multi-Strategy |
| American Century ETF Trust | 1710607 | Multi-Strategy |
| Virtus ETF Trust II | 1648403 | Multi-Strategy |
| TCW ETF Trust | 1831313 | Multi-Strategy |
| Touchstone ETF Trust | 1919700 | Multi-Strategy |
| Federated Hermes ETF Trust | 1849998 | Multi-Strategy |
| Alger ETF Trust | 1807486 | Multi-Strategy |
| GMO ETF Trust | 1981627 | Multi-Strategy |
| Wedbush Series Trust | 2055464 | Multi-Strategy |
| Harris Oakmark ETF Trust | 2035827 | Multi-Strategy |
| Investment Managers Series Trust | 1318342 | Multi-Strategy |

### Fixed Income ETF Trusts (4)
| Trust Name | CIK | Category |
|-----------|-----|----------|
| BondBloxx ETF Trust | 1879238 | Fixed Income |
| Harbor ETF Trust | 1860434 | Multi-Strategy |
| PIMCO ETF Trust | 1450011 | Fixed Income |
| DoubleLine ETF Trust | 1886172 | Fixed Income |

### Multi-Strategy Platforms (31)
| Trust Name | CIK | Category |
|-----------|-----|----------|
| Natixis ETF Trust II | 1728860 | Multi-Strategy |
| Natixis ETF Trust | 1526787 | Multi-Strategy |
| Thornburg ETF Trust | 2038383 | Multi-Strategy |
| Thrivent ETF Trust | 1896670 | Multi-Strategy |
| ALPS Series Trust | 1558107 | Multi-Strategy |
| X-Square Series Trust | 1901443 | Multi-Strategy |
| Lattice Strategies Trust | 1605803 | Multi-Strategy |
| New York Life Investments ETF Trust | 1415995 | Multi-Strategy |
| New York Life Investments Active ETF Trust | 1426439 | Multi-Strategy |
| John Hancock Exchange-Traded Fund Trust | 1478482 | Multi-Strategy |
| ALPS ETF Trust | 1414040 | Multi-Strategy |
| Principal Exchange-Traded Funds | 1572661 | Multi-Strategy |
| AGF Investments Trust | 1479599 | Multi-Strategy |
| SEI Exchange Traded Funds | 1888997 | Multi-Strategy |
| Russell Investments Exchange Traded Funds | 2042513 | Multi-Strategy |
| Legg Mason ETF Investment Trust | 1645194 | Multi-Strategy |
| Legg Mason ETF Investment Trust II | 1792795 | Multi-Strategy |
| Franklin ETF Trust | 1551895 | Multi-Strategy |
| MFS Active Exchange Traded Funds Trust | 2018846 | Multi-Strategy |
| Lazard Active ETF Trust | 2051630 | Multi-Strategy |
| Tidal Trust IV | 2043390 | Multi-Strategy |
| 2023 ETF Series Trust | 1969674 | Multi-Strategy |
| Spinnaker ETF Series | 1484018 | Multi-Strategy |
| Arrow ETF Trust | 1532206 | Multi-Strategy |
| Davis Fundamental ETF Trust | 1670310 | Multi-Strategy |
| Man ETF Series Trust | 2065379 | Multi-Strategy |
| THOR Financial Technologies Trust | 1924447 | Multi-Strategy |
| FIS Trust | 2069687 | Multi-Strategy |
| ETFis Series Trust I | 1559109 | Multi-Strategy |
| Build Funds Trust | 1875710 | Multi-Strategy |
| Nomura ETF Trust | 1969995 | Multi-Strategy |

## Skipped - ETN Issuers (11)
- BMO ETNs/United States
- BMO MAX ETNs
- Barclays Bank PLC
- DB AG ETNs/USA
- DB ETNs/USA
- ETRACS ETNs/UBS AG/London/USA
- Goldman Sachs ETNs/USA
- Invesco DB Multi-Sector Commod (ETN-like)
- Invesco DB US Dollar Index Trust (already tracked)
- JP Morgan ETNs/USA
- JP Morgan Exchange-Traded Fund (already tracked separately)

## Skipped - Already Tracked (129 issuers)
Key matches include:
- REX ETF Trust (CIK: 2043954)
- ProShares Trust (CIK: 1174610)
- ProShares Trust II (CIK: 1415311)
- Direxion Shares ETF Trust (CIK: 1424958)
- GraniteShares ETF Trust (CIK: 1689873)
- Volatility Shares Trust (CIK: 1884021)
- Roundhill ETF Trust (CIK: 1976517)
- Tidal Trust I/II/III (CIKs: 1742912, 1924868, 1722388)
- ETF Series Solutions (CIK: 1540305)
- Themes ETF Trust (CIK: 1976322)
- Innovator ETFs Trust (CIK: 1415726)
- First Trust Exchange-Traded Fund I/II/III/IV/VII/VIII
- Global X Funds (CIK: 1432353)
- VanEck ETF Trust (CIK: 1137360)
- WisdomTree Trust (CIK: 1350487)
- BlackRock ETF Trust I/II
- Goldman Sachs ETF Trust I/II
- NEOS ETF Trust (CIK: 1848758)
- Simplify Exchange Traded Funds (CIK: 1810747)
- And 110+ more...

## Skipped - No 485 Filings Found (notable)
These issuers were searched but had no 485BPOS filings:
- Amplify Commodity Trust - no 485 (S-1/commodity trust)
- Teucrium Commodity Trust - no 485 (S-1/commodity trust)
- DWS Xtrackers - no 485 results found
- Horizon Kinetics ETF - no 485 results found
- Various issuers affected by DNS rate limiting toward end of search

## Notes
- Total trust count after expansion: **194 trusts** (was 122)
- All 72 new CIKs confirmed to have recent 485BPOS filings
- Search used EDGAR full-text search index API with 0.35s rate limiting
- Some later searches failed due to DNS resolution issues (SEC rate limiting)
- Mutual fund entities (Putnam, MFS, American Century individual funds, etc.) were filtered out
- Variable insurance trusts, tax-exempt funds, and non-ETF entities excluded
