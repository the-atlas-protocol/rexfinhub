# Clear classification maintenance window — 2026-05-12

## Context

The VPS file `data/.preflight_maintenance` (empty, dated 2026-05-11 21:04) was set during the audit/rebuild to allow Bloomberg-chain sends despite classifier gaps. Today's `rexfinhub-classification-sweep.service` ran at 09:00 ET and reported:

- 47 unclassified new launches (`etp_category` NULL)
- 14 NULL `issuer_display` rows
- 7 CC funds missing rows in `attributes_CC.csv`

The sweep exit-code is non-zero because gaps exceed threshold, so strict gating stays disabled until the gaps are filled and the flag is removed.

Sweep summary HTML (19,136 chars, 2026-05-12T09:00:03-04:00) was pulled from `jarvis@46.224.126.196:/home/jarvis/rexfinhub/outputs/classification_sweep_summary.html`. The HTML body displays only 30 of the 47 unclassified rows; the other 17 are presumably truncated by the formatter. The next sweep run on VPS will report the residual.

## Files changed (this commit)

| File | Rows added | Notes |
|---|---:|---|
| `config/rules/fund_mapping.csv` | 17 | Thematic category for newly-launched funds |
| `config/rules/attributes_Thematic.csv` | 17 | Sub-category for each Thematic row above |
| `config/rules/exclusions.csv` | 9 | Funds that don't fit any rex category |
| `config/rules/issuer_mapping.csv` | 3 | New `(category, raw_trust, brand)` triples |
| `config/rules/issuer_brand_overrides.csv` | 10 | Per-ticker brand overrides where issuer_mapping conflict made a category-level rule unsafe |
| `config/rules/attributes_CC.csv` | 7 | 7 from the sweep list (PLGI was already present) |

## etp_category NULL — visible 30 (47 reported)

### Mapped (17 → Thematic)

| Ticker | Fund Name | Category | Sub-category | Why |
|---|---|---|---|---|
| WDAI US | PACER S&P WORLD 3AI TOP 300 ETF | Thematic | Artificial Intelligence | "AI" thematic |
| PSAI US | PACER S&P 500 3AI TOP 100 ETF | Thematic | Artificial Intelligence | "AI" thematic |
| CMAG US | CORGI MAG 7 ETF | Thematic | Innovation | Mag 7 = mega-cap tech innovation |
| WDIG US | WISDOMTREE EFFICIENT RARE EARTH PLUS STRATEGIC METALS FUND | Thematic | Transition Metals | "rare earth + strategic metals" |
| GASZ US | CORGI NATURAL GAS POWER & TURBINES ETF | Thematic | Natural Resources | natural gas |
| NICO US | HEXIS ACTIVE NICOTINE ENGAGEMENT ETF | Thematic | Consumer | consumer sin-stock |
| STYL US | CORGI LIFESTYLE BRANDS ETF | Thematic | Consumer | lifestyle brands |
| GLAM US | CORGI BEAUTY SKINCARE & AESTHETICS ETF | Thematic | Consumer | beauty/skincare |
| YUNG US | CORGI LONGEVITY CONSUMER ETF | Thematic | Consumer | longevity consumer |
| WNDR US | CORGI TRAVEL & LEISURE ETF | Thematic | Travel, Vacation & Leisure | direct match |
| EUV US | CORGI LITHOGRAPHY & SEMICONDUCTOR PHOTONICS ETF | Thematic | Tech & Communications | semis (no Semiconductor sub-cat exists) |
| CBOT US | CORGI ROBOTS & HUMANOIDS ETF | Thematic | Robotics & Automation | direct match |
| BZZ US | CORGI DRONES & URBAN AIR MOBILITY ETF | Thematic | Drones | direct match |
| DOCK US | CORGI PORTS RAIL & FREIGHT ETF | Thematic | Infrastructure | logistics infrastructure |
| HULL US | CORGI SHIPPING & GLOBAL LOGISTICS ETF | Thematic | Infrastructure | logistics infrastructure |
| AV US | CORGI AEROSPACE & COMMERCIAL AVIATION ETF | Thematic | Space & Aerospace | aerospace |
| ODDZ US | CORGI SPORTS BETTING & GAMBLING ETF | Thematic | Sports & Esports | direct match |

### Excluded (9)

| Ticker | Fund Name | Reason |
|---|---|---|
| KMCA US | PLUS KOREA MANUFACTURING CORE ALLIANCE INDEX ETF | country_focus (Korea-only, no country sub-cat) |
| NYNY US | CORGI NYC BASED ETF | regional_focus (NYC-only) |
| BUYB US | PROSHARES S&P 500 BUYBACK ARISTOCRATS ETF | factor_only (buyback factor — no rex bucket) |
| CLUB US | BILLIONAIRES CLUB ETF | factor_only (mimic-holdings strategy) |
| TPFC US | TIMOTHY PLAN FREE CASH FLOW ETF | factor_only |
| TPFG US | TIMOTHY PLAN FREE CASH FLOW GROWTH ETF | factor_only |
| TPFI US | TIMOTHY PLAN FIXED INCOME ETF | fixed_income |
| PCEB US | POLEN EURO HIGH YIELD BOND ETF | fixed_income |
| CLOO US | NYLI INVESTMENT GRADE CLO ETF | fixed_income |

### Already classified (4 — no fund_mapping change needed)

| Ticker | Existing mapping |
|---|---|
| KYC US | Thematic |
| XA US | Thematic |
| BLCK US | Crypto |
| WATS US | Thematic |

### Truncated tail (17 reported, not visible in HTML)

The sweep summary's first table contains only 30 rows out of the 47 reported in the header. The next sweep run after the chain re-fires will surface any unclassified residual.

## issuer_display NULL (14)

| Ticker | Trust string | Fix |
|---|---|---|
| CMAY US | Corgi ETF Trust I | `issuer_brand_overrides.csv`: `Corgi` |
| CTMA US | Corgi ETF Trust I | `issuer_brand_overrides.csv`: `Corgi` |
| EMMY US | Corgi ETF Trust I | `issuer_brand_overrides.csv`: `Corgi` |
| HMAY US | Corgi ETF Trust I | `issuer_brand_overrides.csv`: `Corgi` |
| IDMY US | Corgi ETF Trust I | `issuer_brand_overrides.csv`: `Corgi` |
| KHPI US | Managed Portfolio Series/Kensi | `issuer_mapping.csv`: `CC,Managed Portfolio Series/Kensi,Kensington` |
| LBAY US | Tidal Trust I | `issuer_mapping.csv`: `CC,Tidal Trust I,Leatherback` |
| MAYC US | Corgi ETF Trust I | `issuer_brand_overrides.csv`: `Corgi` |
| PLGI US | Collaborative Investment Serie | `issuer_mapping.csv`: `CC,Collaborative Investment Serie,PL` |
| QMY US | Corgi ETF Trust I | `issuer_brand_overrides.csv`: `Corgi` |
| QQMY US | Corgi ETF Trust I | `issuer_brand_overrides.csv`: `Corgi` |
| RSIT US | Tidal Trust II | `issuer_brand_overrides.csv`: `Return Stacked` (Tidal Trust II is already mapped to `Defiance` at LI category, can't reassign) |
| SCMY US | Corgi ETF Trust I | `issuer_brand_overrides.csv`: `Corgi` |
| XNDX US | Investment Managers Series Tru | already mapped (`LI,Investment Managers Series Tru,Tradr`) — chain re-run will populate |

### Why brand overrides instead of issuer_mapping for the Corgi MAY series

`issuer_mapping.csv` keys on `(etp_category, raw_trust_prefix)`. The row `Defined,Corgi ETF Trust I,Corgi ETF Trust I` already exists, but at sweep time the nine MAY-series tickers had been freshly classified into `Defined` so the prior `apply_issuer_brands` ExecStartPost hadn't run yet. Per-ticker brand overrides are the surgical fix — they guarantee correct `issuer_display` regardless of chain ordering.

## CC funds missing attributes_CC rows (7)

Best-effort heuristics from fund name. Marked `Traditional` unless name implies a synthetic / option-overlay wrapper.

| Ticker | Fund Name | Underlier | Index | cc_type | cc_category |
|---|---|---|---|---|---|
| ALTY US | GLOBAL X ALTERNATIVE INCOME ETF | (basket) | Basket | Traditional | Alternatives |
| BTYB US | VISTASHARES BITBONDS 5 YR ENHANCED WEEKLY DISTRIBUTION ETF | (basket) | Basket | Synthetic | Alternatives |
| DEFR US | APTUS DEFERRED INCOME ETF | (none) | SPX | Traditional | Broad Beta |
| KHPI US | KENSINGTON HEDGED PREMIUM INCOME ETF | (none) | SPX | Traditional | Broad Beta |
| LBAY US | LEATHERBACK LONG/SHORT ALTERNATIVE YIELD ETF | (basket) | Basket | Traditional | Alternatives |
| TOPW US | ROUNDHILL TOP WEEKLYPAY ETF | (basket) | Basket | Synthetic | Alternatives |
| JPO US | YIELDMAX JP OPTION INCOME STRATEGY ETF | JPM | (none) | Synthetic | Single Stock |

PLGI US was already in both `fund_mapping.csv` (as CC) and `attributes_CC.csv` (as `Broad Beta` Basket) — the sweep's NULL `issuer_display` flag for PLGI was due to missing issuer_mapping, fixed via the new `CC,Collaborative Investment Serie,PL` row.

## SSH commands for Atlas / Ryu (run after merge to main)

```bash
# 1. Pull merged config on VPS and re-run the classification chain step
ssh jarvis@46.224.126.196 "cd /home/jarvis/rexfinhub && git pull --ff-only && \
  /home/jarvis/venv/bin/python scripts/apply_classification_sweep.py --apply --apply-medium"

# 2. Inspect the new sweep summary (residual gap count)
ssh jarvis@46.224.126.196 "cat /home/jarvis/rexfinhub/outputs/classification_sweep_summary.html | head -20"

# 3. If residual gaps are zero (or only legitimate exclusions), remove the maintenance flag
ssh jarvis@46.224.126.196 "rm /home/jarvis/rexfinhub/data/.preflight_maintenance"

# 4. (Optional) Confirm strict gating restored
ssh jarvis@46.224.126.196 "systemctl --user status rexfinhub-classification-sweep.service"
```

## Residual gap forecast

| Section | Before | Resolved here | Expected residual |
|---|---:|---:|---:|
| etp_category NULL | 47 | 17 mapped + 9 excluded + 4 pre-existing = 30 | 17 (the truncated tail — TBD) |
| issuer_display NULL | 14 | 13 | 1 (XNDX — chain re-fire will populate) |
| CC missing attrs | 7 | 7 | 0 |

After the chain re-runs, the sweep should drop close to zero. Any residual will be from the 17 funds that weren't visible in this morning's HTML — handle them in the next pass.
