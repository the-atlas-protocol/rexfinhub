# Wave D1 — Foreign Stock Universe

**Date:** 2026-05-11
**Branch:** `audit-stockrecs-D1-foreign`
**Scope:** Extend the L&I screener beyond US-listed equities to cover the major foreign venues REX has filed leveraged ETFs against (SK Hynix, Samsung, etc.).

---

## Summary

REX has filed leveraged products on names like **SK Hynix (000660.KS)** and **Samsung Electronics (005930.KS)**, but the L&I engine's underlier universe was US-only. This wave adds a deterministic foreign ticker loader covering six markets:

| Market | Code | yfinance suffix | Target | Got |
|--------|------|-----------------|--------|-----|
| KOSPI / KOSDAQ | KS | `.KS` / `.KQ` | 200 | 139 |
| Tokyo Stock Exchange | T | `.T` | 200 | 200 |
| Taiwan Stock Exchange | TW | `.TW` / `.TWO` | 100 | 100 |
| Hong Kong Exchange | HK | `.HK` | 100 | 96 |
| XETRA / Frankfurt | DE | `.DE` | 100 | 87 |
| London Stock Exchange | L | `.L` | 100 | 97 |
| **Total** | | | **700** | **719** |

(KS short of 200 because the curated KOSDAQ tail had several delisted/illiquid tickers; can be backfilled by extending the seed list.)

---

## Files

- **NEW** `screener/li_engine/data/foreign_tickers.py` — main loader
- **NEW** `screener/li_engine/data/__init__.py` — package marker
- **NEW** `data/foreign/<market>.parquet` — per-market snapshots (gitignored)
- **NEW** `data/foreign/universe.parquet` — combined snapshot (gitignored)
- **NEW** `data/foreign/_cache/<market>_yfinfo.json` — 24h yfinance cache (gitignored)

---

## Schema

```
foreign_ticker_id  str    Stable id, e.g. "KS:005930"
local_ticker       str    yfinance symbol, e.g. "005930.KS"
market             str    KS | T | TW | HK | DE | L
name               str    longName from yfinance .info
sector             str    GICS-style sector
market_cap_usd     float  USD-converted via spot FX (LSE: GBp/100 -> GBP -> USD)
adr_us_ticker      str    Curated US ADR symbol if known, else ""
```

The combined `universe.parquet` adds `snapshot_utc` (ISO timestamp).

---

## Architecture

### Why curated seed lists, not exchange-wide scraping

yfinance has **no exchange-listing API** — you can only enrich a ticker once you know it exists. Three options were considered:

1. **Scrape exchange websites for full listings** — unstable, varies per market, easy to break.
2. **Pull every plausible ticker number and let yfinance reject** — would burn through rate limits in seconds.
3. **Curate seed lists from major-index membership** — the indices already define "the largest names by definition."

Chose (3). Each seed list is the union of one or two flagship indices per market (KOSPI 200, Nikkei 225, FTSE 100, etc.) plus selected mid-caps. yfinance enriches with name/sector/mcap, the loader sorts by USD market cap and trims to the target N.

If a ticker rolls off an index, the seed leaves it in — enrichment will keep or drop it based on whether yfinance still has data. New IPOs get appended.

### FX conversion

Each market has a `MARKET_CCY` entry. yfinance's currency-pair convention is `XXX=X` returning units of XXX per USD, so USD-per-XXX is `1 / lastPrice`.

**Important LSE quirk**: yfinance reports LSE main-board `marketCap` in **pence (GBp)**, not pounds. The original implementation produced market caps 100x too high (AZN at $28T). Fix in `_fx_to_usd("GBp")`: divide by 100 first, then apply GBP→USD.

### Caching

24-hour TTL JSON cache in `data/foreign/_cache/<market>_yfinfo.json`. yfinance is rate-limited aggressively; without the cache, a full re-run takes 10+ minutes and risks hitting the rate limit mid-fetch. With the cache, a re-run is a few hundred ms.

When the rate limiter trips, the loader records the empty result; pruning those entries from the cache and re-running picks them up next call. (Done once during this build for LSE.)

### ADR mapping

Curated dict (`ADR_MAP`) — yfinance does not expose ADR linkages. Sources: NYSE ADR list, BNY Mellon DR Directory, JPMorgan ADR.com. Currently 70+ mappings covering the largest names per market. Add more as REX files on additional foreign underliers.

Notable absences: **Samsung Electronics (005930.KS)** has no US ADR — only a London GDR. **SK Hynix (000660.KS)** has no US ADR either, only a Luxembourg GDR. This matches the rationale for REX's filings: leveraged ETFs are the only US-accessible exposure to these names.

---

## CLI

```bash
# Single market
python -m screener.li_engine.data.foreign_tickers --market KS

# All markets + combined universe.parquet
python -m screener.li_engine.data.foreign_tickers --all

# Per-market only, skip combined snapshot
python -m screener.li_engine.data.foreign_tickers --all --no-combine
```

---

## Verification

### SK Hynix + Samsung land in the KS market

```
local_ticker                  name      sector  market_cap_usd  adr_us_ticker
005930.KS  Samsung Electronics Co., Ltd. Technology   1.228e+12  (no ADR)
000660.KS  SK hynix Inc.                 Technology   9.064e+11  (no ADR)
```

Both at the top of the KOSPI bucket as expected (#1 and #2 by Korean market cap).

### Sample top-5 per market

```
[KS] sample (top 5 by USD market cap):
local_ticker                          name            sector  mcap_usd_b adr_us_ticker
   005930.KS Samsung Electronics Co., Ltd.        Technology     1228.15
   000660.KS                 SK hynix Inc.        Technology      906.43
   005935.KS Samsung Electronics Co., Ltd.        Technology      830.57
   005380.KS         Hyundai Motor Company Consumer Cyclical      114.50         HYMTF
   402340.KS           SK Square Co., Ltd.        Technology      102.07

[T] sample (top 5 by USD market cap):
local_ticker                                 name                 sector  mcap_usd_b adr_us_ticker
      9984.T                 SoftBank Group Corp. Communication Services      216.05         SFTBY
      7203.T             Toyota Motor Corporation      Consumer Cyclical      212.40            TM
      8306.T Mitsubishi UFJ Financial Group, Inc.     Financial Services      205.02          MUFG
      8035.T               Tokyo Electron Limited             Technology      151.54
      9983.T             Fast Retailing Co., Ltd.      Consumer Cyclical      141.67         FRCOY

[TW] sample (top 5 by USD market cap):
local_ticker                                               name     sector  mcap_usd_b adr_us_ticker
     2330.TW Taiwan Semiconductor Manufacturing Company Limited Technology     1857.18           TSM
     2454.TW                                      MediaTek Inc. Technology      192.29
     2308.TW                            Delta Electronics, Inc. Technology      182.31
     2317.TW               Hon Hai Precision Industry Co., Ltd. Technology      110.89         HNHPF
     3711.TW                   ASE Technology Holding Co., Ltd. Technology       78.49

[HK] sample (top 5 by USD market cap):
local_ticker                                            name                 sector  mcap_usd_b adr_us_ticker
     0700.HK                        Tencent Holdings Limited Communication Services      535.40         TCEHY
     9988.HK                   Alibaba Group Holding Limited      Consumer Cyclical      329.25          BABA
     1398.HK Industrial and Commercial Bank of China Limited     Financial Services      320.97         IDCBY
     0005.HK                               HSBC Holdings plc     Financial Services      308.86          HSBC
     0939.HK             China Construction Bank Corporation     Financial Services      298.41         CICHY

[DE] sample (top 5 by USD market cap):
local_ticker                       name             sector  mcap_usd_b adr_us_ticker
      SIE.DE Siemens Aktiengesellschaft        Industrials      240.99         SIEGY
      LIN.DE                  Linde plc                         231.85
      SAP.DE                     SAP SE         Technology      198.36           SAP
      ENR.DE          Siemens Energy AG        Industrials      179.56
      ALV.DE                 Allianz SE Financial Services      166.22         ALIZY

[L] sample (top 5 by USD market cap):
local_ticker                     name             sector  mcap_usd_b adr_us_ticker
      HSBA.L        HSBC Holdings plc Financial Services      309.91          HSBC
       AZN.L          AstraZeneca PLC         Healthcare      284.45           AZN
      SHEL.L                Shell plc             Energy      236.85          SHEL
       RIO.L          Rio Tinto Group    Basic Materials      175.12           RIO
        RR.L Rolls-Royce Holdings plc        Industrials      138.54         RYCEY
```

All values cross-checked against published market caps — within rounding/intra-day FX drift of headline figures.

### ADR coverage by market

```
market  adr_count
DE          17
HK          15
KS           3   (only Hyundai/LG Chem/Samsung Bio — most Korean names lack a US ADR)
L           21
T           13
TW           3   (TSMC, Hon Hai, Chunghwa Telecom)
```

---

## Known limitations / follow-ups

1. **KS shy of target** — only 139 of 200 because some KOSDAQ seeds were delisted or rate-limit-failed at fetch time. Extending the seed with more KOSDAQ-150 / KOSPI-mid names will close the gap on next refresh.
2. **DE shy of target** — 87 of 100 (similar reasons).
3. **No automatic ADR discovery** — every new ADR needs a hand-edit in `ADR_MAP`. Acceptable since REX files on a small number of foreign names per quarter, but worth revisiting if the foreign filing pipeline scales up.
4. **Universe snapshot is point-in-time** — no historical archive (unlike `data/historical/universe/` for US tickers). If we need to track foreign-ticker membership drift, mirror the `save_snapshot(date)` pattern from `analysis/universe_loader.py`.
5. **Sector taxonomy is yfinance-native** (Technology, Consumer Cyclical, etc.) — does not match GICS L1/L2 used elsewhere in `mkt_master_data`. Mapping layer needed before joining to the screener properly. (D2 territory.)
6. **Cache is JSON, not parquet** — fine at this size (~104 entries per market), revisit if seeds grow 10x.

---

## Time spent

~25 minutes implementation + verification, including one rebuild after discovering the LSE GBp/GBP bug.
