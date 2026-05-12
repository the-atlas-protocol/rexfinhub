# Wave D2 — Foreign Filings Tracking

**Branch:** `audit-stockrecs-D2-foreignfilings`
**Status:** Complete
**Time budget:** 60 min

## Problem

`launch_candidates.parquet` is keyed on US tickers. Fund filings whose
underlier is foreign-listed (e.g. **"T-REX 2X LONG SK HYNIX DAILY TARGET ETF"**
references KRX 000660, **"REX ASML Growth & Income ETF"** references
Euronext ASML.AS) get dropped because:

1. The fund-name regex in `filed_underliers.py` was built for 2-6 letter
   uppercase US tickers (`AXTI`, `TSLA`, ...). Multi-word names like
   "SK HYNIX" don't match.
2. Even if a ticker is recovered, joining against `mkt_master_data` by
   US-style symbol misses ADRs / dual listings.

REX has clearly been moving into this lane (T-REX 2X SK Hynix, REX ASML
Growth & Income, T-REX 3X NVO). Competitors are crowding in too —
GraniteShares, KraneShares, Themes, Direxion, Corgi, Amplify, Precidian,
ProShares all have SK Hynix or ASML 2x filings. We need an
"International" recommendations section.

## Solution

New module **`screener/li_engine/analysis/foreign_filings.py`** with the
parallel pipeline:

```
1. Load foreign universe (D1's data/foreign/universe.parquet, fall back
   to a 30-name built-in seed if D1 hasn't landed yet).
2. Pandas-side substring-keyword scan of fund_extractions + fund_status
   joined with filings.registrant.
3. Roll up per foreign_ticker: rex_status (active/pending/filed/none),
   competitor_filings_total, competitor_distinct_issuers,
   competitor_2x_status, competitor_active_count, latest filing date.
4. Annotate market/sector/market_cap_usd from the universe.
5. Suppress underliers where REX has an ACTIVE product (already-launched
   markets — same convention as US launch_candidates).
6. Rank by (rex_status > market_cap_log > sector_strength). No Reddit
   or options signals for foreign tonight — those need D3 + foreign
   data sources.
7. Write data/analysis/foreign_launch_candidates.parquet.
```

`launch_candidates.main()` now invokes `foreign_filings.main()` after the
US table is written, with a try/except so a foreign-side failure cannot
break the US pipeline.

## Files

| Path | Action |
|---|---|
| `screener/li_engine/analysis/foreign_filings.py` | **NEW** (≈340 LOC) |
| `screener/li_engine/analysis/launch_candidates.py` | extended `main()` to chain foreign build |
| `data/analysis/foreign_launch_candidates.parquet` | **NEW** output (21 rows) |
| `docs/audit_2026-05-11/upgrade_D2.md` | this file |

## Schema (parallel to US table)

| Column | Type | Notes |
|---|---|---|
| `foreign_ticker` | str | e.g. `000660.KS`, `ASML.AS`, `2330.TW` |
| `name` | str | underlier display name |
| `market` | str | KRX / TSE / HKEX / TWSE / AMS / NYSE (for ADRs) |
| `sector` | str | GICS-aligned |
| `market_cap_usd` | float | USD billions |
| `rex_status` | str | `active` / `pending` / `filed` / `none` (active rows are filtered out) |
| `rex_fund_name` | str | most-recent REX fund name |
| `rex_ticker` | str | if assigned |
| `rex_latest_filing` | datetime | filing_date of newest REX entry |
| `competitor_filings_total` | int | row count from fund_extractions |
| `competitor_distinct_funds` | int | distinct series_name |
| `competitor_distinct_issuers` | int | distinct registrant |
| `competitor_2x_status` | str | `active` / `filed` / `none` |
| `competitor_active_count` | int | EFFECTIVE/ACTIVE/LIVE rows in fund_status |
| `sector_strength` | float | static for D2 (1.5–5.0); replaced by E2 secular-trend |
| `rex_status_rank` | int | 4/3/2/1 |
| `market_cap_score` | float | log10(cap) − 8.0, clipped to 0..3.5 |
| `composite_score` | float | `rex_rank*10 + cap_score*2 + sector_strength` |

## Sub-task: REX-registrant matcher fix

First implementation used `re.compile(r"REX\|ETF Opportunities", re.IGNORECASE)`.
Word boundary missing — `"DiREXion"` (a major competitor) matched as REX,
which mis-attributed Direxion's "Direxion Daily SONY Bear 1X ETF" as a
REX product and incorrectly bumped 6758.T to `rex_status=active`.

Fix: `re.compile(r"\b(?:T-?REX|REX|ETF Opportunities)\b", re.IGNORECASE)`.

Verified manually:
```
'REX ETF Trust'                  -> True
'ETF Opportunities Trust'        -> True
'Direxion Shares ETF Trust'      -> False  (was: True — bug)
'Tradr Shares'                   -> False
'GraniteShares ETF Trust'        -> False
'Themes ETF Trust'               -> False
'Precidian ETFs Trust'           -> False
```

## Verification

### End-to-end run

```
$ python -m screener.li_engine.analysis.foreign_filings
INFO: Using seed foreign universe: 30 rows (D1 not yet available)
INFO: fund_extractions foreign matches: 915 rows
INFO: fund_status foreign matches: 79 rows
INFO: Raw foreign filings rolled-up: 23 underliers
INFO: Wrote data/analysis/foreign_launch_candidates.parquet (21 rows)
```

### Top-10 sample (sorted by composite_score)

| # | foreign_ticker | name | market | sector | rex_status | rex_fund_name | comp_filings | comp_issuers | comp_2x | mkt_cap_usd | score |
|---|---|---|---|---|---|---|--:|--:|---|--:|--:|
| 0 | 000660.KS | SK Hynix Inc. | KRX | Memory | **pending** | T-REX 2X LONG SK HYNIX DAILY TARGET ETF | 12 | 8 | filed | 130 B | 31.23 |
| 1 | NVO | Novo Nordisk ADR | NYSE | Health Care | **pending** | T-REX 3X Long NVO Daily Target ETF | 135 | 5 | active | 340 B | 30.00 |
| 2 | 2330.TW | Taiwan Semiconductor Mfg | TWSE | Semiconductors | none | – | 6 | 1 | active | 900 B | 22.00 |
| 3 | 005930.KS | Samsung Electronics Co Ltd | KRX | Semiconductors | none | – | 36 | 3 | active | 420 B | 22.00 |
| 4 | SAP | SAP SE | NYSE | Information Technology | none | – | 32 | 2 | active | 280 B | 20.89 |
| 5 | 0700.HK | Tencent Holdings Ltd | HKEX | Communication Services | none | – | 3 | 1 | filed | 500 B | 20.50 |
| 6 | BABA | Alibaba Group ADR | NYSE | Consumer Discretionary | none | – | 3 | 1 | filed | 300 B | 19.95 |
| 7 | 7203.T | Toyota Motor Corp | TSE | Consumer Discretionary | none | – | 31 | 1 | active | 280 B | 19.89 |
| 8 | AZN | AstraZeneca ADR | NASDAQ | Health Care | none | – | 31 | 1 | active | 220 B | 19.68 |
| 9 | 6758.T | Sony Group Corp | TSE | Communication Services | none | – | 30 | 2 | active | 120 B | 19.66 |

(table also includes SoftBank, BYD, MELI, RIO, JD.com HK, Baidu, PBR, Vale, Li Auto, XPeng — 21 rows total)

### Filtered (ASML, TSM) — REX already active

ASML.AS and TSM both surfaced in the rollup but were correctly suppressed
by the `rex_status != "active"` filter:

- **REX ASML Growth & Income ETF** (REX ETF Trust, latest filing 2026-04-29)
- **REX TSM Growth & Income ETF** (REX ETF Trust)

## Key DB queries

### Foreign-keyword scan of fund_extractions

```sql
SELECT fe.series_name, fe.class_contract_name, fe.class_symbol,
       f.registrant, f.form, f.filing_date, f.cik, f.accession_number
FROM fund_extractions fe
JOIN filings f ON f.id = fe.filing_id
WHERE fe.series_name IS NOT NULL
```

(keyword matching — substring + word-boundary fallback — done in pandas
because a SQL OR-chain over 30 universe rows × multiple keywords each
becomes unreadable and unindexable)

### fund_status pull (for active/effective annotation)

```sql
SELECT fs.fund_name, fs.ticker, fs.status, fs.effective_date,
       fs.latest_form, fs.latest_filing_date,
       t.name AS trust_name, t.cik
FROM fund_status fs
JOIN trusts t ON t.id = fs.trust_id
WHERE fs.fund_name IS NOT NULL
```

## Notes & follow-ups

- **D1 dependency** — currently using a 30-name seed universe. When D1's
  `data/foreign/universe.parquet` lands, `load_foreign_universe()`
  prefers it automatically. Schema D1 should provide:
  `foreign_ticker, name, market, sector, market_cap_usd, name_keywords (list[str])`.
  If D1 omits `name_keywords`, we synthesise from the leading comma-split
  segment of `name`, but explicit keywords are far more reliable
  (e.g. "SK HYNIX" vs "SK Hynix Inc.,Class A").
- **Sector strength is static** — `STATIC_SECTOR_STRENGTH` table embedded
  in the module. When E2 (secular-trend scoring) lands, swap to its
  output rather than the static dict.
- **No Reddit/options signals** for foreign tickers tonight. ApeWisdom
  doesn't track non-US tickers cleanly, and the bbg etp_data sheet is
  ETF-side. D3 (foreign competitor signals) is the next layer.
- **ADR vs primary listing** — many seed entries (NVO, BABA, SAP, MELI,
  TSM, RIO, BHP, VALE, PBR, AZN, INFY, SHOP, SE, RACE) are US-listed
  ADRs and could potentially also live in the US table. They show up
  here because their fund-NAMES (not tickers) reference foreign issuers.
  D1 should resolve the ADR/primary mapping cleanly so the B-renderer
  doesn't double-list them.
- **Hong Kong dual-listing dedupe** — `9988.HK` (Alibaba HK) is in the
  seed but with empty `name_keywords` so it doesn't match (BABA already
  catches "ALIBABA"). When D1 lands, this should be handled by linking
  HK and ADR rows to a single `cluster_id`.

## Constraints honoured

- 60-minute budget: complete.
- Read-only on D1's foreign universe: confirmed; falls back gracefully
  when `data/foreign/universe.parquet` is absent.
- Empty parquet OK if no REX foreign filings: implemented in
  `main()` — writes empty parquet with full schema rather than skipping.
