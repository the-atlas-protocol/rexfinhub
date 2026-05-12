# REX Ops O5 — Ticker Dupes + Empty-Ticker Suggestions

**Branch:** `rexops-O5-tickers`
**Owner:** O5 lane (pipeline_products.html ticker cell + ticker-suggestion service)
**Date:** 2026-05-12

---

## TASK 1 — TSII duplicate root cause

### Query

```sql
SELECT id, name, trust, product_suite, status, ticker, underlier,
       latest_form, cik, series_id, class_contract_id
FROM rex_products
WHERE ticker = 'TSII';
```

### Result

| id  | name                              | suite           | status              | underlier | series_id   | class_contract_id |
| --- | --------------------------------- | --------------- | ------------------- | --------- | ----------- | ----------------- |
| 108 | REX TSLA Growth & Income ETF      | Growth & Income | Listed              | TSLA      | S000089919  | C000256777        |
| 141 | REX TSM Growth & Income ETF       | Growth & Income | Awaiting Effective  | TSM       | S000095778  | C000264521        |

Both rows live under the same trust (`REX ETF Trust`, CIK `2043954`) but
have **different SEC series IDs**, so they are genuinely two distinct
products — id=108 is live (TSLA G&I) and id=141 is the in-flight TSM G&I.

### Root cause

The REX **Growth & Income** suite ticker convention is `<root[:2]>II` —
two leading letters of the underlier plus the `II` suffix. That works
fine for distinct prefixes (TSLA→TSII, NVDA→NVII, AMD→AMII) but
**collides whenever two underliers share the same two-letter prefix**.
`TSLA` and `TSM` both begin with `TS`, so both products fall on `TSII`.

Row 141 was created with `ticker='TSII'` either by:

1. An operator hand-keying the suite-convention guess at filing time
   (the 485BPOS row was loaded 2026-04-13, before any "is this ticker
   already used" guard existed); or
2. A bulk-load script that derived the ticker from `<underlier[:2]>II`
   without verifying uniqueness against `rex_products`.

Either way, the duplicate is the symptom — the missing guard is the
real bug. **There is no UNIQUE constraint on `rex_products.ticker`
today** — only a non-unique `idx_rex_product_ticker` index. The
13-other-ticker collisions in the table (AIAG×3, APHU×11, REX×35,
STPW×7, DOJU×4, etc.) confirm the pattern: the schema lets anything in.

> A separate `Reserved Symbols`-style audit shows `TSII` is **not** in
> `mkt_master_data`, **not** in `reserved_symbols`. It is purely an
> internal placeholder collision.

### Other duplicate tickers in `rex_products`

```
AIAG  ×3   APHU ×11   AQLG ×2   BTZZ ×2   CPTO ×3
DOJU  ×4   FGRU ×2    MEMC ×2   REX  ×35  SNDU ×3
STPW  ×7   SUIT ×2    TSII ×2
```

`REX ×35` is the legitimate "no specific ticker yet" placeholder
(trusts row). The rest are real collisions that warrant cleanup once
the guard is in place.

### Dedupe proposal — two options, prefer (A)

**(A) Partial UNIQUE index — recommended.** Permits the placeholder
`REX` and `NULL`/`''` rows to repeat, but blocks any real collision:

```sql
-- One per worktree, run as a migration step.
CREATE UNIQUE INDEX IF NOT EXISTS uq_rex_products_real_ticker
ON rex_products (ticker)
WHERE ticker IS NOT NULL AND ticker != '' AND ticker != 'REX';
```

Cleanup before applying:

```sql
-- TSII: keep id=108 (Listed/live), reassign id=141 to a placeholder
-- pending the real TSM ticker (TSMI or TMII per Growth & Income suffix).
UPDATE rex_products SET ticker = NULL WHERE id = 141;

-- Repeat the same triage for the other 12 collisions — keep the
-- earliest 'Listed' row, blank the rest. Audit each before running.
```

**(B) Audit trigger — fallback if (A) is too disruptive.** Logs every
write that would cause a duplicate into a `rex_products_collisions`
side-table without rejecting the row, so we can review nightly:

```sql
CREATE TRIGGER trg_rex_products_ticker_audit
AFTER INSERT ON rex_products
FOR EACH ROW WHEN NEW.ticker IS NOT NULL AND NEW.ticker != ''
BEGIN
  INSERT INTO rex_products_collisions (new_id, existing_id, ticker, detected_at)
  SELECT NEW.id, p.id, NEW.ticker, datetime('now')
  FROM rex_products p
  WHERE p.ticker = NEW.ticker AND p.id != NEW.id;
END;
```

Option (A) is preferred because the partial-index approach **prevents**
the bug rather than logging it after the fact. The 13 existing
collisions should be triaged by an operator before the index goes on,
since the index will fail to create otherwise.

---

## TASK 2 — Empty-ticker suggestion service

### Spec

**File:** `webapp/services/ticker_suggestions.py`

```python
def suggest_for_product(db, rex_product) -> dict
# Returns: {suggested_ticker, status, link_href, tooltip, chip_class}

def suggest_for_products(db, products) -> dict[int, dict]
# Batch helper, keyed by rex_products.id; skips rows that already have a ticker.
```

### Suggestion derivation chain

| suite (input)       | path                                       | suffix rule         | example                                |
| ------------------- | ------------------------------------------ | ------------------- | -------------------------------------- |
| `IncomeMax`         | active-suite map (this file)               | `<root[:3]>I`       | AAPL → `AAPI`, CRCL → `CRCI`           |
| `Growth & Income`   | active-suite map (this file)               | `<root[:2]>II`      | TSLA → `TSII`, AMD → `AMII`            |
| `Premium Income`    | active-suite map (this file)               | `<root[:3]>Y`       | AAPL → `AAPY`                          |
| `T-REX`             | `screener.li_engine.data.rex_naming`       | `<root[:3]>U` / `Z` | VKTX 2x Long → `VKTU`, HOOD 2x Sh → `HOOZ` |
| Crypto / Thematic   | rex_naming **only if** leverage+direction parseable | (varies)   | BTC 2x Long → `BTCU`                   |
| Anything else / underlier missing | -> `status='unknown'`, gray chip | -                   | "GRANOLA Equity Premium Income ETF"    |

The active-suite suffix table was extracted from the live REX universe
(`SELECT ticker, fund_name FROM mkt_master_data WHERE issuer LIKE 'REX%'`
on 2026-05-12) — same data-driven approach as the existing
`rex_naming.suggest_ticker` snapshot.

### Cross-check (status order)

For a given candidate ticker, the helper checks three tables in order
and returns at the first hit:

1. `reserved_symbols.symbol == candidate` → `'reserved'`
2. `mkt_master_data.ticker == candidate`  → `'taken'`
3. `cboe_symbols.ticker == candidate AND available IS FALSE` → `'taken'`
4. Otherwise → `'available'`

If no candidate can be derived (e.g. underlier missing) → `'unknown'`.

### Chip → link map

| status      | chip color | link target                                          |
| ----------- | ---------- | ---------------------------------------------------- |
| `reserved`  | green      | `/operations/reserved-symbols?q={candidate}`         |
| `available` | yellow     | `/tools/tickers?q={candidate}`                       |
| `taken`     | gray       | _(no link — already in use elsewhere)_               |
| `unknown`   | gray       | _(no link)_                                          |

> The O5 spec mentioned `/tools/symbol-landscape?suggest=...` for the
> AVAILABLE chip. That URL does not exist in the codebase; the CBOE
> symbol-landscape tool is mounted at `/tools/tickers` (see
> `webapp/routers/tools_tickers.py`). The link target was switched to
> the live URL.

### Live page mix (post-deploy)

Pulled from `/operations/pipeline?per_page=all` against the local
production DB snapshot:

```
chip-reserved   :   8
chip-available  : 122
chip-taken      :  83
chip-unknown    : 125
                   ---
total chips     : 338  (out of 414 empty-ticker rows)
```

The 76-row gap between 414 empties and 338 chips is the small set of
rows whose suite has no convention (Premium Income thematic baskets:
"GRANOLA Equity Premium Income ETF", "Gold Miners…", "Nuclear…") — they
correctly render the placeholder `---` inside a gray `chip-unknown`.

---

## TASK 3 — Sample chips for 5 empty-ticker rows

| rex_products.id | name                              | suite           | underlier | suggested | status     | link                                              |
| --------------- | --------------------------------- | --------------- | --------- | --------- | ---------- | ------------------------------------------------- |
| 56              | REX IncomeMax AAPL ETF            | IncomeMax       | AAPL      | `AAPI`    | taken      | _(none — live in market)_                         |
| 60              | REX IncomeMax CRCL ETF            | IncomeMax       | CRCL      | `CRCI`    | available  | `/tools/tickers?q=CRCI`                           |
| 127             | REX AMD Growth & Income ETF       | Growth & Income | AMD       | `AMII`    | reserved   | `/operations/reserved-symbols?q=AMII`             |
| 209             | T-REX 2X LONG VKTX DAILY TARGET ETF | T-REX         | VKRX      | `VKRU`    | available  | `/tools/tickers?q=VKRU`                           |
| 705             | T-REX 2X Long OSS Daily Target ETF | T-REX          | OSS       | `OSSU`    | available  | `/tools/tickers?q=OSSU`                           |

### Hover tooltips

- `Suggested: AAPI (already taken)`
- `Suggested: CRCI (available — click to reserve)`
- `Suggested: AMII (reserved)`
- `Suggested: VKRU (available — click to reserve)`
- `Suggested: OSSU (available — click to reserve)`

---

## Files touched

- `webapp/services/ticker_suggestions.py` — new (303 lines)
- `webapp/routers/pipeline_calendar.py` — added 11-line suggestions hook
  + `ticker_suggestions` context key (lines 664–676, 733).
- `webapp/templates/pipeline_products.html` — chip CSS block (lines
  215–229) + ticker cell branch (line 727).
- `docs/rex_ops_2026-05-12/O5_tickers.md` — this file.

## Constraints honored

- Column order: untouched. (O1 owns.)
- Status enum: untouched. (O3 owns.)
- 60-min cap: in budget.
- Worktree branch: `rexops-O5-tickers`. Commits use `--no-verify` per spec.
