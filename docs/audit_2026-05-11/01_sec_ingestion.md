# Stage 1 Audit — SEC Ingestion
Generated: 2026-05-11T00:00:00Z
Agent: sec_ingestion (respawn)

## Summary
9 findings: 4 critical, 4 high, 1 medium.

- F1 (CRITICAL) — Cross-trust series replication (4,644 series, 55,694 dup rows)
- F2 (CRITICAL) — Same-trust ticker bleed (REX/APHU/LXOM/STPW + many more)
- F3 (CRITICAL) — Schema enables F1: `uq_fund_status` includes `trust_id` so the same `(series_id, class_contract_id)` is allowed under N trusts
- F4 (CRITICAL) — Cross-trust ticker collision: tickers like `SYM` (54 trusts, 1,498 rows) and `SYMBO` (54 trusts, 657 rows) attached to nonsense fund names
- F5 (HIGH) — Manifest records `success` for 0-row extractions: silent fetch/parse failures permanently bypass retry
- F6 (HIGH) — Body extractors swallow ALL exceptions and return empty lists (network errors, parse errors, decompression errors all become "no funds extracted")
- F7 (HIGH) — Same-trust series_id mis-attribution: completely different funds share the same series_id within ONE trust (e.g. S000002797 has 43 distinct fund names under one trust_id 2322)
- F8 (HIGH) — `_BAD_TICKERS` filter does not catch truncated junk (`SYMBO` from `SYMBOL`); 6-char regex limit creates new junk values that bypass the deny-list
- F9 (MEDIUM) — `effective_date_confidence='full'` row exists; the strategy label is being written into the confidence column for at least one row (column-misalignment bug)

Plus structural notes:
- `filings` table is properly UNIQUE on `accession_number` — no accession dupes (verified: 626,936 rows = 626,936 distinct accessions, 0 orphans).
- `fund_status` UNIQUE constraint is `(trust_id, series_id, class_contract_id)` — the trust_id inclusion is what permits F1 at write time.
- `fund_status` has 6,201 rows still in `watcher_atom: lightweight rollup pending step4` state — incomplete pipeline runs are leaving rows half-processed.

## Findings

### F1: Cross-trust series replication
- **Severity**: critical
- **Symptom**: The same `series_id` is ingested under multiple `trust_id` rows.
- **Magnitude (re-verified 2026-05-11)**:
  - 22,255 distinct series_ids in `fund_status`
  - 4,644 series (~21%) appear under more than one trust
  - 55,694 total rows attributable to the duplication
  - Top examples: `S000026126`, `S000002160`, `S000002088` each appear under 42 distinct trust_ids (all T. Rowe Price related CIKs)
- **Mechanism**:
  - `etp_tracker/step2.py:79` iterates the trust CIK list and calls `load_all_submissions_for_cik` per CIK.
  - When a given accession is registered against multiple related CIKs (T. Rowe family, American Funds family, Columbia ETF family), each per-CIK pass picks up the same accession and emits its own row in step3 output.
  - `webapp/services/sync_service.py:192` upserts `(trust_id, series_id, class_contract_id)` — schema permits the dup, so a new row is written for each trust.
- **Worst-affected trusts** (rows attributable to bleed):
  - Columbia ETF Trust I — 963
  - Columbia ETF Trust — 866
  - 12+ American Funds entities — 774 each
- **Status**: Confirmed by prior agent. Re-verified at scale this run.
- **Fix direction (out of scope)**: Either (a) deduplicate accessions across the trust set in step2 before per-CIK fan-out, or (b) make `(series_id, class_contract_id)` globally unique and resolve trust by primary registrant, or (c) accept the dup but tag a `primary_trust_id` for display.

### F2: Same-trust ticker bleed (still present despite recent fix)
- **Severity**: critical
- **Symptom**: A single ticker is assigned to many distinct funds within one trust.
- **Re-verified 2026-05-11**:
  - `REX` → 16 distinct funds (REX IncomeMax AMD, BABA, BIIB, DIS, EEM, GDXJ, GOOG, META, MSFT, MSTR, PYPL, SLV, SMH, SNOW, TLRY, V — all under trust_id 11)
  - `APHU` → 11 distinct funds
  - `LXOM` → 10 distinct funds
  - `STPW` → 7 distinct funds
- **Code reference**: `etp_tracker/step3.py:421-436` — the recent fix tracks `used_tickers` within a single filing and clears duplicate assignments. Logic is correct for *within-filing* bleed but does NOT prevent the same ticker from being independently re-assigned to other funds in *subsequent* filings — once `REX` has been mis-assigned across N filings (each filing legitimately containing the REX brand name in its proximity window), each fund inherits it from its own filing run.
- **Root cause**: `_extract_ticker_for_series_from_texts` uses ±600 char proximity. For multi-fund filings where the trust brand "REX" appears in headers, footers, and unrelated text near every series block, every series gets `REX` as its ticker. The within-filing dedupe only prevents intra-filing collisions; it does not disqualify obviously-wrong matches like 4-letter brand tokens.
- **Status**: Confirmed by prior agent. Re-verified at scale this run.

### F3: Schema enables F1 at the write layer
- **Severity**: critical
- **Symptom**: `fund_status` UNIQUE constraint includes `trust_id`, so the same `(series_id, class_contract_id)` is allowed under N trusts.
- **Evidence**:
```
CONSTRAINT uq_fund_status UNIQUE (trust_id, series_id, class_contract_id)
```
- **Implication**: Even if step2/step3 deduped perfectly, the schema would not catch the bug. Any future regression in dedup logic silently corrupts the DB.
- **Status**: New finding (this run).

### F4: Cross-trust ticker collision (junk tickers like `SYM`, `SYMBO`)
- **Severity**: critical
- **Symptom**: Single tickers attached to dozens of trusts and hundreds of rows, with garbage fund names.
- **Magnitude**:
  - `SYM` — 54 trusts, **1,498 rows**, fund_names like `"Class R"`, `"CLASS R"`
  - `SYMBO` — 54 trusts, 657 rows, fund_names like `"-"`, `"I"`
  - `PACOX`, `PAULX`, `PCCOX`, `PCUZX`, `PRCOX`, `PREFX`, `PRHSX`, `RCLIX` — each under 42 trusts (T. Rowe family — likely a different mechanism: F1 cascade)
- **Root cause** (`SYM`/`SYMBO`):
  - `etp_tracker/body_extractors.py:63` uses `re.fullmatch(r"[A-Z0-9]{1,6}", tkr)` which matches arbitrary 1–6 char alphanumeric strings.
  - "SYMBOL" header text gets truncated to "SYMBO" by upstream parsing (the column header literal is being captured as a value).
  - "SYM" similarly comes from "SYMBOL" or "SYM." truncation.
  - `etp_tracker/step4.py:8` defines `_BAD_TICKERS = {"SYMBOL", "NAN", "N/A", "NA", "NONE", "TBD", ""}` — does NOT include `SYM`, `SYMBO`, `TICKE`, `TICKER`, etc.
- **Status**: New finding (this run).

### F5: Manifest records `success` for 0-row extractions
- **Severity**: high
- **Symptom**: A filing that yields zero extracted funds (because of fetch failure, parse failure, or genuine empty body) is recorded as `status="success", extraction_count=0` and will never be retried.
- **Code reference**:
  - `etp_tracker/step3.py:598` — `record_success(manifest, accession, form, len(extracted_rows))` is called even if `extracted_rows == []`.
  - `etp_tracker/manifest.py:49` — `get_processed_accessions` filters by `status == "success"` regardless of `extraction_count`.
- **Implication**: One bad run with transient SEC errors permanently masks the affected filings. Bumping `PIPELINE_VERSION` is the only escape.
- **Status**: New finding (this run).

### F6: Silent exception swallowing in body extractors and SEC client
- **Severity**: high
- **Symptom**: Network failures, parse failures, decompression errors all silently degrade to "no rows extracted".
- **Code references**:
  - `etp_tracker/body_extractors.py:24, 30, 69, 89, 99` — `except Exception: pass` / `return rows, ""`.
  - `etp_tracker/sec_client.py:117, 159, 181, 201, 228, 234, 243, 257, 271, 282, 299` — 11 silent excepts. Some are best-effort cache writes (acceptable), but `fetch_text`, `fetch_json`, `fetch_bytes` cache fallbacks treat fetch errors as cache-only-misses without surfacing the original network error.
  - `etp_tracker/step3.py:56, 99, 105, 350, 482` — wider catch-all blocks in extraction helpers.
- **Compounding effect**: Combined with F5, transient errors become permanent silent data omissions.
- **Status**: New finding (this run).

### F7: Same-trust series_id mis-attribution
- **Severity**: high
- **Symptom**: Within a single trust, the same `series_id` value is attached to many distinct fund names.
- **Evidence**: `series_id = S000002797` under trust_id 2322 has 43 distinct fund_names ("Lincoln Investor Advantage Advisory", "Lincoln ChoicePlus Assurance Prime", "Lincoln ChoicePlus Assurance Series", etc. — Lincoln annuity products). These appear across both `EFFECTIVE` and `PENDING` rows.
- **Mechanism (probable)**: When step3 parses a filing's SGML header containing several series, it pairs SGML series IDs with proximity-extracted fund names. If the SGML order does not match the body order, IDs slide. Without a strong join key (e.g., series ID embedded in body markup), proximity wins and produces wrong pairings.
- **Cascade with F1**: Once a wrong (series_id, fund_name) pairing is created under one trust, F1's per-CIK fan-out replicates it to all related CIKs.
- **Status**: New finding (this run).

### F8: `_BAD_TICKERS` deny-list is incomplete
- **Severity**: high
- **Symptom**: The deny-list at `etp_tracker/step4.py:8` filters `SYMBOL` / `NAN` / `N/A` / `NA` / `NONE` / `TBD` / `""`, but does not catch the truncated forms produced by the 6-char regex limit in body extractors.
- **Missing entries** (observed in DB): `SYM`, `SYMBO`, `TICKE`, `TICKER` (likely), and proximity-bleed brand tokens like `REX`, `APHU`, `LXOM`, `STPW` (these are arguably real symbols too, so a hard deny-list is the wrong fix — see F2).
- **Status**: New finding (this run).

### F9: Strategy label leaking into `effective_date_confidence` column
- **Severity**: medium
- **Symptom**: `fund_status.id = 6420, fund_name = 'Class B', ticker = PSMBX, latest_form = 497` has `effective_date_confidence = 'full'`. The string `'full'` is the extraction strategy label, not a confidence value.
- **Expected confidence values**: `IXBRL`, `HEADER`, `HIGH`, `MEDIUM`, `None`. Strategy labels are `header_only`, `s1_metadata`, `full`, `full+ixbrl`.
- **Implication**: Suggests at least one code path is writing `Extraction Strategy` into the `Effective Date Confidence` column. Only 1 row affected (so far), but indicates a column-misalignment bug.
- **Status**: New finding (this run).

## DB queries run

```sql
-- F1 magnitude
SELECT
  COUNT(DISTINCT series_id) AS total_series,
  SUM(CASE WHEN n_trusts > 1 THEN 1 ELSE 0 END) AS multi_trust_series,
  SUM(CASE WHEN n_trusts > 1 THEN n_trusts ELSE 0 END) AS dup_rows
FROM (
  SELECT series_id, COUNT(DISTINCT trust_id) AS n_trusts
  FROM fund_status WHERE series_id != ''
  GROUP BY series_id
);
-- → (22255, 4644, 55694)

-- F2 ticker bleed
SELECT ticker, COUNT(DISTINCT fund_name), COUNT(*)
FROM fund_status
WHERE ticker IN ('REX','APHU','LXOM','STPW') GROUP BY ticker;
-- → REX 16/16, APHU 11/11, LXOM 10/10, STPW 7/7

-- F4 cross-trust ticker collision
SELECT ticker, COUNT(DISTINCT trust_id) AS n_trusts, COUNT(*) AS rows
FROM fund_status WHERE ticker != ''
GROUP BY ticker HAVING n_trusts > 1
ORDER BY n_trusts DESC LIMIT 10;
-- → SYM 54/1498, SYMBO 54/657, plus 8 PIMCO-pattern tickers under 42 trusts each

-- F7 same-series different-fund check
SELECT trust_id, fund_name FROM fund_status WHERE series_id='S000002797' LIMIT 10;
-- → trust 2322 has many distinct Lincoln annuity products under one series_id

-- F9 strategy label leak
SELECT id, fund_name, ticker, latest_form FROM fund_status WHERE effective_date_confidence='full';
-- → 1 row: id=6420, Class B / PSMBX / 497

-- Schema check
SELECT sql FROM sqlite_master WHERE type='table' AND name IN ('filings','fund_status');
-- → filings UNIQUE(accession_number); fund_status UNIQUE(trust_id, series_id, class_contract_id)

-- Orphans
SELECT COUNT(*) FROM filings WHERE trust_id IS NULL;            -- 0
SELECT COUNT(*) FROM filings f LEFT JOIN trusts t ON f.trust_id = t.id WHERE t.id IS NULL;  -- 0

-- Status reason distribution
SELECT status, status_reason, COUNT(*) FROM fund_status
GROUP BY status, status_reason ORDER BY 3 DESC LIMIT 10;
-- → 6201 rows in PENDING with reason 'watcher_atom: lightweight rollup pending step4'
```

## Surfaces inspected
- `etp_tracker/step2.py` (full)
- `etp_tracker/step3.py` (lines 1-110, 350-490, 540-615 — extraction loop, ticker proximity, manifest writes)
- `etp_tracker/step4.py` (lines 1-290 — status determination, dedup logic, BAD_TICKERS list)
- `etp_tracker/manifest.py` (full)
- `etp_tracker/body_extractors.py` (lines 1-110)
- `etp_tracker/sec_client.py` (silent-except scan only)
- `etp_tracker/ixbrl.py` (silent-except scan only — clean, 1 silent except)
- `webapp/services/sync_service.py` (lines 100-330 — DB upsert path)
- `webapp/database.py` schema via `sqlite_master`

## Surfaces NOT inspected (for Stage 2)
- `etp_tracker/sgml.py` — SGML header parser; would clarify F7 mechanism (does the parser preserve series-to-class pairing or does it return parallel arrays that step3 zips incorrectly?)
- `etp_tracker/sec_client.py` cache freshness logic (`refresh_max_age_hours`) — H5 unverified: can a stale `submissions/CIK*.json` cache return wrong filing list?
- `etp_tracker/step5.py` (NameHistory writes) — unverified for orphan/dup behavior
- `etp_tracker/run_pipeline.py` orchestration — unverified for partial-failure semantics that could leave 6,201 rows in `watcher_atom` state
- Whether F1's 4,644 multi-trust series correspond to (a) genuine SEC cross-registration or (b) step2 fan-out artifact — needs spot-check against EDGAR for one example
- Source of `'full'` string in `effective_date_confidence` for row id 6420 — would need to grep for column writes
- `webapp/services/sync_service.py:286` — what does the BAD_TICKERS post-write deduplication do for already-corrupted rows? (step4 dedup happens before sync; corrupted rows already in DB are not retroactively cleaned)

## Notes for Stage 2
- F3 + F1 + F2 + F4 are interdependent. Fixing F1 alone (dedupe at step2) will reduce F2/F4 cardinality but won't eliminate ticker-bleed within a single filing.
- F5 + F6 form a positive feedback loop with F1: a transient SEC outage during one CIK's pass leaves stale data while the other related CIKs' passes succeed — the trust whose fetch failed gets ZERO rows recorded as "success", forever bypassing retry, while the other trusts get the (now-incorrectly-attributed) data.
- The `watcher_atom: lightweight rollup pending step4` status (6,201 rows) suggests an upstream watcher process is writing partial fund_status rows that step4 was supposed to finalize but did not. Stage 2 should grep for `watcher_atom` in the codebase and trace the pipeline interaction.
