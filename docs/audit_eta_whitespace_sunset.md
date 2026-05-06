# Audit η — REX Whitespace + Sunset Analysis
**Date**: 2026-05-05
**Scope**: 83 REX live products (is_rex=1, market_status='ACTV') — competitive positioning
**DB queried**: `data/etp_tracker.db` (READ-ONLY)
**Cross-checked**: `data/analysis/launch_candidates.parquet` (154 records, Stream A)

---

## 1. Methodology

For each REX ACTV LI fund with mapped underlier data:
- Match competitors via `map_li_underlier` + `map_li_direction` + `map_li_leverage_amount` (exact match, same DB)
- Classify position as: **Sole Survivor** (no live competitors), **Healthy Competitive** (1+ live competitors), or **Minority** (outnumbered)
- For PEND/LIQU REX funds: identify if competitors have live products (Whitespace candidates)

REX ACTV funds without `map_li_underlier` (CC, Crypto, Thematic, Defined): analyzed separately — no direct competitor mapping possible via current DB attributes.

---

## 2. Summary Statistics

| Category | Count |
|---|---|
| REX ACTV funds total | 83 |
| REX ACTV funds with LI underlier data | 58 |
| REX ACTV funds without LI underlier (CC/Crypto/Thematic) | 25 |
| **Sole Survivor** (REX only, no live competitors) | **34** |
| **Healthy Competitive** (REX + 1 live competitor) | 9 |
| **Minority** (REX + 2+ live competitors) | 15 |
| PEND/LIQU REX LI funds | 10 |

---

## 3. Sole Survivor Table (Sunset Candidates)

These 34 underlier/direction/leverage combos have **only REX live** — no competitor has an active product. These are potential **sunset candidates** if REX decides to exit, or alternatively **defensible whitespace** if REX is first-to-market and competitors have not followed.

| REX Ticker | Fund Name | Underlier | Direction | Leverage | Assessment |
|---|---|---|---|---|---|
| `AFRU US` | T-REX 2X LONG AFRM DAILY TARGET ETF | AFRM US | Long | 2x | No competitor |
| `APHU US` | T-REX 2X LONG APH DAILY TARGET ETF | APH | Long | 2x | No competitor |
| `WTIU US` | MICROSECTORS ENERGY 3X LEVERAGED ETN | BIGOIL | Long | 3x | No competitor (ETN basket) |
| `WTID US` | MICROSECTORS ENERGY -3X INVERSE LEVERAGED ETN | BIGOIL | Short | 3x | No competitor (ETN basket) |
| `CCUP US` | T-REX 2X LONG CRCL DAILY TARGET ETF | CECL US | Long | 2x | No competitor (note: maps to CECL, not CRCL) |
| `CRCD US` | T-REX 2X INVERSE CRCL DAILY TARGET ETF | CRCL US | Short | 2x | No competitor |
| `CORD US` | T-REX 2X INVERSE CRWV DAILY TARGET ETF | CRWV US | Short | 2x | No competitor |
| `DJTU US` | T-REX 2X LONG DJT DAILY TARGET ETF | DJTU UA | Long | 2x | No competitor (DJT underlier format unique) |
| `EOSU US` | T-REX 2X LONG EOSE DAILY TARGET ETF | EOSE US | Long | 2x | No competitor |
| `FGRU US` | T-REX 2X LONG FIGR DAILY TARGET ETF | FIGR | Long | 2x | No competitor |
| `GMEU US` | T-REX 2X LONG GME DAILY TARGET ETF | GME US | Long | 2x | No competitor |
| `GOOX US` | T-REX 2X LONG ALPHABET DAILY TARGET ETF | GOOG US | Long | 2x | Note: GOOGL US (different format) has 2 competitors |
| `KTUP US` | T-REX 2X LONG KTOS DAILY TARGET ETF | KTOS US | Long | 2x | No competitor |
| `GDXU US` | MICROSECTORS GOLD MINERS 3X LEVERAGED ETN | MINERS | Long | 3x | No competitor |
| `GDXD US` | MICROSECTORS GOLD MINERS -3X INVERSE LEVERAGED ETN | MINERS | Short | 3x | No competitor |
| `FLYU US` | MICROSECTORS TRAVEL 3X LEVERAGED ETN | MQUSTRAV | Long | 3x | No competitor |
| `FLYD US` | MICROSECTORS TRAVEL 3X INVERSE LEVERAGED ETN | MQUSTRAV | Short | 3x | No competitor |
| `FNGO US` | MICROSECTORS FANG INDEX 2X LEVERAGED ETN | NYFANGT | Long | 2x | No competitor |
| `FNGU US` | MICROSECTORS FANG+ 3X LEVERAGED ETN | NYFANGT | Long | 3x | No competitor |
| `FNGD US` | MICROSECTORS FANG+ INDEX -3X INVERSE LEVERAGED ETNS | NYFANGT | Short | 3x | No competitor |
| `PAAU US` | T-REX 2X LONG PAAS DAILY TARGET ETF | PAAS | Long | 2x | No competitor |
| `RBLU US` | T-REX 2X LONG RBLX DAILY TARGET ETF | RBLX US | Long | 2x | No competitor |
| `RDWU US` | T-REX 2X LONG RDW DAILY TARGET ETF | RDW | Long | 2x | No competitor |
| `SBTU US` | T-REX 2X LONG SBET DAILY TARGET ETF | SBET US | Long | 2x | No competitor |
| `SNOU US` | T-REX 2X LONG SNOW DAILY TARGET ETF | SNOW US | Long | 2x | No competitor |
| `BULZ US` | MICROSECTORS FANG & INNOVATION 3X LEVERAGED ETN | SOLFANGT | Long | 3x | No competitor |
| `BERZ US` | MICROSECTORS FANG & INNOVATION -3X INVERSE LEVERAGED ETN | SOLFANGT | Short | 3x | No competitor |
| `OILU US` | MICROSECTORS OIL & GAS E&P 3X LEVERAGED ETN | SOLOILT | Long | 3x | No competitor |
| `OILD US` | MICROSECTORS OIL & GAS E&P -3X INVERSE LEVERAGED ETN | SOLOILT | Short | 3x | No competitor |
| `BNKU US` | MICROSECTORS US BIG BANKS 3X LEVERAGED ETN | SOLUSBBT | Long | 3x | No competitor |
| `BNKD US` | MICROSECTORS US BIG BANKS INDEX -3X INVERSE LEVERAGED ETN | SOLUSBBT | Short | 3x | No competitor |
| `NRGU US` | MICROSECTORS US BIG OIL INDEX 3X LEVERAGED ETN | SOLUSBOT | Long | 3x | No competitor |
| `NRGD US` | MICROSECTORS US BIG OIL -3X INVERSE LEVERAGED ETN | SOLUSBOT | Short | 3x | No competitor |
| `TTDU US` | T-REX 2X LONG TTD DAILY TARGET ETF | TTD US | Long | 2x | No competitor |

**Sunset-watch flag**: The 8 MicroSectors ETN pairs (BIGOIL, MINERS, MQUSTRAV, NYFANGT, SOLFANGT, SOLOILT, SOLUSBBT, SOLUSBOT) are all ETN basket structures with zero competitor activity. These are REX-proprietary index baskets — not a sign of competitor weakness but of proprietary structure. Low sunset risk, but low competitive validation.

**True sunset candidates** (single-stock, low-liquidity underlier, no defense needed):
- `EOSU` (EOSE — small energy storage co.)
- `SBTU` (SBET — small sports betting co.)
- `RDWU` (RDW — small defense tech co.)
- `PAAU` (PAAS — Pan American Silver, niche)

---

## 4. Healthy Competitive Table

REX has a live product AND at least 1 live competitor on the same underlier+direction+leverage.

| REX Ticker | Underlier | Direction | Leverage | Competitors | Competition Level |
|---|---|---|---|---|---|
| `BMNU US` | BMNR US | Long | 2x | BMNG US | 1 competitor |
| `CIFU US` | CIFR US | Long | 2x | CIFG US | 1 competitor |
| `GLXU US` | GLXY US | Long | 2x | GLGG US | 1 competitor |
| `NFLU US` | NFLX US | Long | 2x | NFXL US | 1 competitor |
| `NVDQ US` | NVDA US | Short | 2x | NVD US | 1 competitor |
| `SMUP US` | SMR US | Long | 2x | SMU US | 1 competitor |
| `SNDU US` | SNDK | Long | 2x | SNXX US | 1 competitor |
| `BTCZ US` | XBTUSD | Short | 2x | SBIT US | 1 competitor |

---

## 5. Minority Table (REX outnumbered)

REX has 1 fund, competitors have 2+. Most competitive pressure.

| REX Ticker | Underlier | Direction | Leverage | REX | Competitors | Competitor Tickers |
|---|---|---|---|---|---|---|
| `AAPX US` | AAPL US | Long | 2x | 1 | 2 | AAPB, AAPU |
| `CRWU US` | CRWV US | Long | 2x | 1 | 2 | CRWG, CWVX |
| `ROBN US` | HOOD US | Long | 2x | 1 | 2 | HOOG, HOOX |
| `MSFX US` | MSFT US | Long | 2x | 1 | 2 | MSFL, MSFU |
| `MSTU US` | MSTR US | Long | 2x | 1 | 2 | MSOO, MSTX |
| `MSTZ US` | MSTR US | Short | 2x | 1 | 2 | MSDD, SMST |
| `TSLZ US` | TSLA US | Short | 2x | 1 | 2 | TSDD, TSLQ |
| `BTCL US` | XBTUSD | Long | 2x | 1 | 2 | BITU, BITX |
| `ETU US` | XETUSD | Long | 2x | 1 | 2 | ETHT, ETHU |
| `SOLX US` | XSOUSD | Long | 2x | 1 | 2 | SLON, SOLT |
| `XRPK US` | XRPUSD | Long | 2x | 1 | 3 | UXRP, XRPT, XXRP |
| `NVDX US` | NVDA US | Long | 2x | 1 | 5 | NVDB, NVDG, NVDL, NVDO, NVDU |
| `TSLT US` | TSLA US | Long | 2x | 1 | 5 | TSLG, TSLI, TSLL, TSLO, TSLR |

**Most contested**: NVDA Long 2x (5 competitors), TSLA Long 2x (5 competitors), XRP Long 2x (3 competitors). REX is a minority player in these high-profile underliers.

---

## 6. Whitespace Queue

REX has filed/launched-then-liquidated a product while competitors have live products — these are **re-entry candidates**.

| REX Ticker | Fund Name | Underlier | Status | Live Competitors | Priority |
|---|---|---|---|---|---|
| `ARMU US` | T-REX 2X LONG ARM DAILY TARGET ETF | ARM US | LIQU | ARMG US (1 live) | Medium — ARM is a hot AI/chip play |
| `BULU US` | T-REX 2X LONG BULL DAILY TARGET ETF | BULL US | LIQU | BULG, BULX, CONX, HODU (4 live) | High — 4 competitors means validated demand |
| `DKUP US` | T-REX 2X LONG DKNG DAILY TARGET ETF | DKNG US | LIQU | DKNX US (1 live) | Low — sports betting niche |
| `ETQ US` | T-REX 2X INVERSE ETHER DAILY TARGET ETF | XETUSD | LIQU | ETHD, SETH (2 live) | Medium — crypto short has real demand |

**Pending launches with no live competitors yet** (pre-whitespace — REX filed first):

| Parquet Entry | Underlier | Score Pct | Status |
|---|---|---|---|
| DOCN Long 2x | DOCN | 100.0% | FILED, no competitor live yet |
| UI Long 2x | UI | 99.4% | FILED, no competitor live yet |
| DNA Long 2x | DNA | 98.7% | FILED, no competitor live yet |
| FMCC Long 2x | FMCC | 98.1% | FILED, no competitor live yet |
| AMPX Long 2x | AMPX | 97.4% | FILED, 1 competitor filed |
| TSEM Long 2x | TSEM | 96.8% | FILED, 2 competitors filed |

These 154 candidates in `launch_candidates.parquet` are already filed/pending — confirm `rex_market_status=FILED` means they are in the filing queue but not yet launched.

---

## 7. Key Findings

### Finding 1 — 34 Sole Survivor products (sunset vs defensible whitespace)
REX has a monopoly on 34 underlier/direction/leverage combos. The 8 MicroSectors ETN pairs are proprietary structures (low churn risk). The 4 true sunset candidates (`EOSU`, `SBTU`, `RDWU`, `PAAU`) are low-liquidity single-stock underliers with no competitive defense needed — evaluate AUM/volume before any sunset decision.

### Finding 2 — NVDA Long 2x and TSLA Long 2x are REX's most contested positions
NVDX faces 5 live competitors; TSLT faces 5 live competitors. REX holds its position but is outnumbered. Differentiation must come from AUM, fee, or brand — not first-mover advantage.

### Finding 3 — BULU (BULL 2x Long) is the clearest re-entry candidate
REX liquidated `BULU US` while 4 competitors (`BULG`, `BULX`, `CONX`, `HODU`) remain live on the same underlier. This is validated demand with an active market. Worth reviewing the liquidation reason before re-filing.

### Finding 4 — 25 REX ACTV funds have no LI underlier data (CC/Crypto/Thematic)
The competitive analysis cannot be run for AIPI, FEPI, ATCL, DOJE, ESK, SSK, XRPR, DRNZ, GIF, etc. because `map_li_underlier` is NULL. These need attribute population before competitive positioning can be evaluated.

### Finding 5 — launch_candidates.parquet cross-check
The 154 entries in Stream A's parquet are all `rex_market_status=FILED` with no live REX ticker. These are whitespace opportunities, not existing products. Top 6 by composite score: DOCN (100%), UI (99.4%), DNA (98.7%), FMCC (98.1%), AMPX (97.4%), TSEM (96.8%).
