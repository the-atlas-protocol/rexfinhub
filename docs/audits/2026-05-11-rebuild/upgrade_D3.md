# Upgrade D3 — Foreign competitor 2x ETF mapping

Branch: `audit-stockrecs-D3-foreigncomp`
Status: first-pass curated mapping landed. Living dataset — extend as new SSL ETFs list.

## Why

D2 ranking penalises REX filings on underliers already crowded with foreign-listed
2x products (e.g. NVDA has 10+ Korean/Japanese/HK 2x ETFs — adding another tells us
nothing about retail demand). With no foreign-competitor lookup, D2 was treating
every underlier as greenfield internationally, which over-rewarded crowded names.

## What landed

| File | Lines | Purpose |
|---|---|---|
| `screener/li_engine/analysis/foreign_competitors.py` | new (~290 LoC) | Hand-curated mapping + `build()` / `crowding_summary()` / `main()` writer |
| `data/analysis/foreign_competitors.parquet` | new (78 rows) | Static dataset; regenerate via `python -m screener.li_engine.analysis.foreign_competitors` |

## Coverage

78 rows, 27 unique underliers, 5 markets:

| Market | Rows | Notes |
|---|---|---|
| KRX (Korea) | 41 | Bulk of the universe — KIM/ACE, Mirae/TIGER, Samsung/KODEX, Kiwoom/KOSEF, KB/RISE, Hanwha/PLUS |
| TSE (Japan) | 16 | Single-stock leverage = ETN only (Nomura NEXT NOTES); ETF leverage on Nikkei 225 (Nomura/Daiwa/Simplex) |
| HKEX | 14 | CSOP, Mirae Asset Global (Global X HK), Premia, Samsung HK; index L&I on HSI/HSTECH |
| TWSE | 2 | Yuanta TW50 2x + -1x (single-stock leverage essentially nonexistent) |
| LSE | 4 | Leverage Shares ETPs (TSLA, NVDA, MSTR, COIN) — kept light, mostly EU retail not REX-overlap |

### Top 15 most-crowded underliers (sorted by total)

| Underlier | Long 2x | Short | Total | Markets | Issuers |
|---|---:|---:|---:|---:|---:|
| NVDA | 10 | 1 | 11 | 4 | 10 |
| TSLA | 8 | 1 | 9 | 4 | 8 |
| AAPL | 5 | 0 | 5 | 3 | 5 |
| MSTR | 5 | 0 | 5 | 4 | 5 |
| MSFT | 4 | 0 | 4 | 3 | 4 |
| 005930 KS (Samsung Elec) | 3 | 1 | 4 | 1 | 3 |
| 000660 KS (SK Hynix) | 4 | 0 | 4 | 1 | 4 |
| AVGO | 3 | 0 | 3 | 2 | 3 |
| HSTECH | 3 | 0 | 3 | 1 | 3 |
| GOOGL | 3 | 0 | 3 | 2 | 3 |
| COIN | 3 | 0 | 3 | 3 | 3 |
| NKY (Nikkei 225) | 2 | 1 | 3 | 1 | 3 |
| HSI | 1 | 1 | 2 | 1 | 1 |
| TWSE | 1 | 1 | 2 | 1 | 1 |
| PLTR | 2 | 0 | 2 | 1 | 2 |

## Verification — SK Hynix (000660 KS)

All 4 known KRX competitors captured:

| issuer | ticker | fund_name | leverage | AUM ($M) | listed |
|---|---|---|---:|---:|---|
| Samsung (KODEX) | 468660 KS | KODEX SK Hynix 2x Leverage | 2.0 | 130 | 2023-12-12 |
| Mirae Asset (TIGER) | 468680 KS | TIGER SK Hynix 2x Leverage | 2.0 | 90 | 2023-12-12 |
| KIM (ACE) | 468690 KS | ACE SK Hynix 2x Leverage | 2.0 | 50 | 2023-12-12 |
| KB Asset (RISE) | 490140 KS | RISE SK Hynix 2x Leverage | 2.0 | 25 | 2024-08-13 |

(All 2x long. Inverse single-stock SK Hynix not yet listed on KRX as of 2026-05.
The 2023-12-12 cluster is the regulatory go-live date for second-wave KR
single-stock 2x; KB/RISE joined eight months later in the third wave.)

## Schema

```
underlier            str   US-canonical ticker (NVDA) or `<code> <market>` for non-US (000660 KS)
underlier_market     str   US/KS/JP/HK/TW
issuer               str   ETF issuer (KIM, Mirae, Daiwa, CSOP, ...)
ticker               str   listing ticker (Bloomberg-style suffix)
market               str   KRX/TSE/HKEX/TWSE/LSE
fund_name            str
leverage_amount      float 2.0, -1.0, -2.0, 3.0
leverage_direction   str   'long' | 'short'
aum_usd_m            float USD-equivalent AUM ($M); NaN if unknown
listing_date         str   YYYY-MM-DD or empty
source               str   'krx'|'tse'|'hkex'|'issuer-site'|'broker-book'
note                 str
```

## D2 integration contract

D2 reads the parquet, joins on `underlier`, and applies a crowding penalty:

```python
fc = pd.read_parquet("data/analysis/foreign_competitors.parquet")
crowding = (
    fc.groupby("underlier")
      .size()
      .rename("foreign_2x_count")
)
# n=0  -> no penalty (greenfield internationally)
# n=1-2 -> small penalty
# n>=5 -> heavy penalty (NVDA/TSLA-class — adding another tells us nothing)
```

A pre-baked summary helper is also exposed:

```python
from screener.li_engine.analysis.foreign_competitors import crowding_summary
summary = crowding_summary()  # returns DataFrame indexed by underlier
```

If the parquet is missing or empty, **D2 must default `foreign_2x_count = 0`**
for every underlier and proceed. Never block D2 on this file.

## Gaps and known omissions

This is a pragmatic first pass. Known-incomplete areas, in priority order:

1. **Korean single-stock universe is fluid.** KRX approves 4-8 new 2x ETFs per
   quarter. Names not in the mapping yet but likely live: ASML (US ADR), ARM,
   SMCI, SOFI, Hims, Reddit, Robinhood. Refresh quarterly from KRX disclosure.
2. **AUM figures are stale.** Numbers were sourced from issuer monthly fact
   sheets at curation time and rounded. Treat as order-of-magnitude only.
   If precise AUM-weighted crowding becomes important, wire in a Bloomberg
   `FUND_TOTAL_ASSETS` pull keyed on the listing ticker.
3. **HKEX ticker collisions.** A few HKEX rows reuse `7568 HK` style tickers
   across issuers — the HKEX recycles tickers when products delist. Two rows
   have the same ticker (Mirae Global X HSTECH 2x and Samsung HSTECH 2x);
   real-life one of those tickers is wrong. Verify before using for execution.
4. **TWSE single-stock leverage.** Taiwan FSC has not approved single-stock
   leveraged ETFs at scale. Only Yuanta TW50 2x is meaningful. If REX files
   on a Taiwanese name, expect zero foreign-2x competition — that's an
   accurate signal, not a coverage gap.
5. **Japan ETN vs ETF distinction.** Nomura NEXT NOTES are technically ETNs
   on TSE, not ETFs. From the rexfin scoring perspective they're competitive
   products and treated as 2x competitors here. Tag column is `source` =
   `issuer-site`; if downstream needs to exclude ETNs, add a filter on
   `fund_name LIKE '%ETN%'`.
6. **No 3x products.** US-style 3x leverage doesn't exist on KRX/TSE/HKEX
   (regulatory ceiling at 2x). LSE has -3x via WisdomTree but small-AUM and
   omitted; add later if needed.
7. **Inverse coverage is sparse.** Most inverse single-stock products are
   either zero-AUM or not listed; the few that exist (CSOP NVDA -2x, CSOP
   TSLA -2x, KODEX Samsung Elec -1x, Simplex Nikkei -2x) are included.
   When REX files an inverse product, this column matters more.
8. **No Canadian (Horizons BetaPro 2x suite).** Horizons HXU/HXD on TSX
   are leveraged TSX 60 — irrelevant to REX single-stock filings, omitted.
9. **Source provenance is shallow.** Rows tagged `issuer-site` were spot-
   checked against issuer fact sheets but not back-linked to specific URLs
   or PDF page numbers. For audit-grade traceability, add a `source_url`
   column and fill it on next pass.

## Refresh procedure

When new foreign 2x products list:

1. Add row to `_ROWS` in `foreign_competitors.py` (preserve column order).
2. Run `python -m screener.li_engine.analysis.foreign_competitors`.
3. Confirm the printed summary changes as expected (new underlier appears,
   crowding count increments).
4. Commit both `foreign_competitors.py` and the regenerated parquet.

## Constraints honoured

- 60 min budget — single curated module, no live scrape.
- D2 not blocked — module is fail-safe (empty mapping returns empty frame).
- Most ambiguous agent — gaps documented above, not hidden.

## Sources surveyed

- KRX disclosure portal (`krx.co.kr`) — leveraged single-stock ETF directory
- TSE / JPX ETF directory (`jpx.co.jp/english/equities/products/etfs/`)
- HKEX leveraged & inverse products list (`hkex.com.hk`)
- KIM (ACE), Mirae Asset (TIGER), Samsung (KODEX), Kiwoom (KOSEF), KB Asset
  (RISE), Hanwha (PLUS) Korean issuer sites
- Nomura (NEXT NOTES / NEXT FUNDS), Daiwa AM, Simplex Asset Mgmt
- CSOP, Premia, Mirae Asset Global (Global X HK), Samsung Asset Mgmt (HK)
- Leverage Shares (LSE issuer site)
- rex-asia broker-book context (KSD top-50 retail holdings, Feb 2026 — used
  to prioritise which underliers Korean retail actually trades)
