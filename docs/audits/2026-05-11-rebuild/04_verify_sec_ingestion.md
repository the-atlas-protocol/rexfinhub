# Verify Audit — SEC Ingestion (post-Wave 1+2+4)
Generated: 2026-05-11T22:00:00Z
Compares to: 01_sec_ingestion.md
DB inspected: `data/etp_tracker.db` (623.8 MB, mtime 2026-05-11 18:18, 213,810 fund_status rows, max filing_date 2026-05-04)

## Scope of changes inspected

`git diff dbe56c5^..6470819 --stat etp_tracker/` (only 4 files in the SEC ingestion surface were touched by the audit fix waves):

```
etp_tracker/body_extractors.py  | 58 +++++++++++++++++++++++++++++++++++++++---
etp_tracker/email_alerts.py     | 27 +++++++++++++++++---     (R7 timezone, out of scope here)
etp_tracker/manifest.py         | 52 ++++++++++++++++++++++++++++++++++---
etp_tracker/step3.py            | 32 ++++++++++++++++++++---
4 files changed, 156 insertions(+), 13 deletions(-)
```

R3 is the only fix that landed inside this audit's surface. R5/R6/R7/R8/R9/T1 modified other surfaces (cache, CSV, TZ, auth, preflight, classification). T2 added `scripts/cleanup_sgml_dupes_2026_05_11.py` but the cleanup_T2.md doc states "Real cleanup will run on VPS in Wave 5" — script was not applied to `data/etp_tracker.db` locally (no `data/etp_tracker.db.pre-T2-*.bak` files, no `temp/cleanup_sgml_dupes_*.json` audit logs).

## Stage 1 finding status

### F1: Cross-trust series replication
- Before: 22,255 distinct series_ids, 4,644 multi-trust series, 55,694 dup rows
- Now: **22,255 / 4,644 / 55,694** (identical)
- Worst trusts unchanged: Columbia ETF Trust I (963), Columbia ETF Trust (866), 3× American Funds entities (774 each)
- Top series_ids still spanning 42 trusts each: `S000026126`, `S000002160`, `S000002088`, `S000002071`
- Status: **PERSISTING**. F1 was deferred (cleanup_T2 doc explicitly defers the "55,694-row cross-trust series replication cleanup" to Wave 6 / R4). No code change in step2 fan-out logic.

### F2: Same-trust ticker bleed
- Before: REX×16, APHU×11, LXOM×10, STPW×7
- Now: **REX×16, APHU×11, LXOM×10, STPW×7** (identical)
- Additional bleed pattern surfaced: `VSCG` × 8 funds in one trust (not flagged in Stage 1 but matches the same proximity-window root cause)
- Status: **PERSISTING**. R3 narrowed the body-extractor regex to 2-5 chars and added a deny-list, but did NOT change `_extract_ticker_for_series_from_texts` proximity logic in `step3.py:48-68` (still uses ±600-char window). REX/APHU/LXOM/STPW/VSCG all pass `_valid_ticker` because they ARE valid 3-4 letter tickers; the bleed is a proximity false-positive, not a regex-shape problem. Plus: the existing 50K+ polluted rows are not retroactively cleaned (R3 is forward-only; doc cleanup_T2.md Section "Out-of-scope sibling pollution" defers this to Wave 6).

### F3: Schema enables F1 at write layer
- Before: `uq_fund_status UNIQUE (trust_id, series_id, class_contract_id)`
- Now: **`uq_fund_status UNIQUE (trust_id, series_id, class_contract_id)`** (identical)
- Status: **PERSISTING**. No schema migration in this wave.

### F4: Cross-trust ticker collision (junk tickers)
- Before: SYM 54/1498, SYMBO 54/657, plus 8 PIMCO-pattern tickers under 42 trusts
- Now: **SYM 54/1498, SYMBO 54/657** (identical) — plus same 8 PIMCO tickers (`PACOX`, `PAULX`, `PCCOX`, `PCUZX`, `PRCOX`, `PREFX`, `PRHSX`, `RCLIX`) and 6 more (`RRCOX`, `TEEFX`, `THISX`, `TRULX`, `TRZLX`) under 42 trusts each
- Status: **PERSISTING (forward-only, code-fix only).** R3 prevents NEW SYM/SYMBO writes (validator + deny-list). No retroactive cleanup of existing 2,155 polluted rows. T2's cleanup script is narrow (AQR/CLASS only) and was NOT applied to the local DB (no backup/audit log present). The PIMCO 42× tickers are F1-cascade artifacts, not body-extractor regex problems — F1 fix is the only thing that would clear them.

### F5: Manifest records `success` for 0-row extractions
- Before: `record_success(..., len(rows))` called for any non-raising attempt
- Now: `etp_tracker/step3.py:622` calls `record_extraction_result(...)` instead. New status `extracted_zero` introduced (`manifest.py:117-122`), and `get_retry_accessions` now includes `extracted_zero` alongside `error` (`manifest.py:67-71`), bounded by `max_retries=3`.
- Disk evidence: 0 `extracted_zero` rows across 2,335 manifest files (because the pipeline has not re-run since R3 landed — last `fund_extractions.created_at = 2026-05-04 13:20:26`).
- Status: **RESOLVED at code layer; effect not yet visible in manifest data.** Will materialise on next pipeline run.

### F6: Silent exception swallowing in body extractors and SEC client
- Before: 11 `except Exception: pass` sites in `sec_client.py`, 5+ in `body_extractors.py`
- Now: **Same count.** R3 did not touch these except blocks. `sec_client.py:159, 181, 201, 228, 234, 243, 257, 271, 282, 299` still silently swallow.
- Status: **PERSISTING**. R3 partially mitigates the manifest blind-spot of F6 by introducing `extracted_zero` retry, but the underlying network/parse error suppression remains. A genuine 503 from SEC during fetch will still produce `extracted_zero` rather than `error` — three retries then permanent silent omission.

### F7: Same-trust series_id mis-attribution
- Before: `series_id=S000002797` under trust 2322 had 43 distinct fund_names
- Now: **trust 2322 has 43 distinct fund_names for `S000002797`** (identical)
- Wider scan: **1,034 (trust_id, series_id) pairs have >1 distinct fund_name** in fund_status. Several have 30+ distinct names per series within a single trust (Lincoln annuity products family).
- Status: **PERSISTING**. No code change to SGML series-class pairing logic in `sgml.py` or to step3 proximity binding. Stage-2 work item.

### F8: `_BAD_TICKERS` deny-list is incomplete
- Before: `step4.py` deny-list was 7 tokens (`SYMBOL`, `NAN`, `N/A`, `NA`, `NONE`, `TBD`, `""`)
- Now: R3 added two new deny-lists upstream of step4:
  - `body_extractors.py:26-43` — `_BODY_BAD_TICKERS` (~52 tokens)
  - `step3.py:24-41` — `_TICKER_STOPWORDS` (49 tokens, expanded from 20)
- step4 deny-list itself unchanged; the new deny-lists at extractor + proximity layers prevent the truncations from being written in the first place.
- Status: **RESOLVED for new writes** (forward-only). Existing 2,155+ polluted rows persist (see F4). See N1 below for the regression risk this creates.

### F9: Strategy label leaking into `effective_date_confidence`
- Before: 1 row (id=6420, ticker=PSMBX) had `effective_date_confidence = 'full'`
- Now: **1 row (`effective_date_confidence = 'full'`)** (identical — same row).
- Distribution: HEADER=203,203 / HIGH=7,837 / NULL=2,598 / MEDIUM=171 / **'full'=1**.
- Status: **PERSISTING**. No code touch. One-row anomaly indicates a writer bug somewhere that hasn't recurred in any subsequent write.

### Structural note: `watcher_atom: lightweight rollup pending step4`
- Before: 6,201 rows in PENDING with this status_reason
- Now: **need re-query** below.

## NEW findings (post-fix introduced)

### N1: R3 deny-list rejects three real ETF tickers (`USD`, `JPY`, `AND`)
- **Severity**: high (regression risk, not yet manifested in DB)
- **Surface**: `etp_tracker/body_extractors.py:26-43` (`_BODY_BAD_TICKERS`) and `etp_tracker/step3.py:24-41` (`_TICKER_STOPWORDS`)
- **Evidence**: existing legitimate fund_status rows that future re-extractions would now drop:
  - `USD` → trust 4, "ProShares Ultra Semiconductors" (EFFECTIVE, latest 2025-09-23)
  - `JPY` → trust 181, "Lazard Japanese Equity ETF" (EFFECTIVE, latest 2026-04-23)
  - `AND` → trust 33, "Global X FTSE Andean 40 ETF" (EFFECTIVE, but old: latest 2017-03-17)
- **Mechanism**: R3 added currency codes (`USD`, `EUR`, `GBP`, `JPY`, `CAD`, `AUD`) and English connectives (`AND`, `OR`, `OF`, `THE`, `FOR`, `WITH`, `BY`, `ALL`) to the deny-list to plug column-header pollution. These tokens are also valid US ETF tickers in active circulation. On the next pipeline run that re-processes Lazard's 2026-04-23 N-PX/485, the `JPY` ticker will be silently dropped from `fund_extractions`, then `extracted_zero` retry will fire 3 times and finally lose the row to the manifest.
- **Hypothesis**: R3 was tested with synthetic fixtures (`temp/verify_fix_R3.py`, per fix_R3.md "Live re-extraction on a known-bad filing was deferred"). The deny-list expansion did not cross-reference the existing legitimate-ticker universe.
- **Mitigation direction**: replace blind deny-list with deny-with-context (e.g. only reject `USD`/`JPY`/`AND` when found inside a column-header line, or when the same fund-name + ticker pair lacks SGML support). Or whitelist-check against a known ETF universe before rejecting.

### N2: T2 cleanup not applied to local DB (production-equivalent state unchanged)
- **Severity**: medium (operational, not a code bug)
- **Surface**: `data/etp_tracker.db` vs. `cleanup_T2.md` plan
- **Evidence**:
  - 7 rows of `(AQR Funds, CLASS)` in the 24h preflight window remain in `fund_extractions` (verified by re-running the T2 selection criteria)
  - 54 total `(AQR Funds, CLASS)` rows + 185 total `class_symbol='CLASS'` rows still present
  - No `data/etp_tracker.db.pre-T2-*.bak` backup file (script was never `--apply`'d locally)
  - No `temp/cleanup_sgml_dupes_*.json` audit log
  - cleanup_T2.md Section "Plan for VPS application (Wave 5)" confirms this is intentional — VPS apply is queued for Wave 5
- **Hypothesis**: not a defect; documents the gap between the "Wave 1+2+4 fixes landed" claim and the actual data state on this DB. The local DB remains polluted until either (a) Wave 5 runs against VPS and the result is re-pulled, or (b) the same script is run locally with `--apply --preflight-window-only`.

### N3: Pipeline has not run since R3 landed — code-only verification only
- **Severity**: low (observability gap, not a defect)
- **Surface**: data freshness vs. fix landing time
- **Evidence**:
  - Last `fund_extractions.created_at` = 2026-05-04 13:20:26
  - Last `fund_status.updated_at` = 2026-05-04 13:20:26
  - R3 commit (95c745e) merged 2026-05-11
  - 0 manifest entries with `status='extracted_zero'` across 2,335 manifest files
- **Hypothesis**: the R3 fix's runtime behaviour (extracted_zero status, retry path, narrowed regex) cannot be validated against real SEC traffic until the next pipeline run. All current verification is code-walk + synthetic-test based.

### N4: 6 additional same-trust bleed tickers surfaced in re-scan
- **Severity**: high (same root cause as F2, wider blast radius than reported in Stage 1)
- **Surface**: `etp_tracker/step3.py:48-68` `_extract_ticker_for_series_from_texts`
- **Evidence**: re-scanning `(trust_id, ticker)` pairs with >5 distinct fund_names yields:
  - `REX` × 16 funds (known)
  - `APHU` × 11 (known)
  - `LXOM` × 10 (known)
  - **`VSCG` × 8 funds** — same trust (NEW, not in Stage 1)
  - `STPW` × 7 (known)
  - **`AEAXX` × 7 funds across 5 trusts** — F1+F2 cascade, real fund ticker but spread across multiple American Funds CIKs
- **Hypothesis**: Stage 1 spot-checked the top 4-5 tickers; full enumeration finds the same proximity-window failure mode in additional brand tickers. R3's regex narrowing (1-6 → 2-5 chars) does not address this — these are all 4-5 char real tickers passing the validator.

## Verdict

- Resolved at code: F5 (manifest hygiene), F8 (deny-lists). 2 of 9.
- Persisting: F1, F2, F3, F4, F6, F7, F9. 7 of 9. F4 mitigated forward but historical 2,155 rows remain.
- New: N1 (real-ticker over-rejection regression), N2 (T2 not applied locally), N3 (R3 unverified at runtime), N4 (additional same-trust bleed surface). 4 new.

**Overall: WATCH**. The two code fixes that landed (R3) are correct and conservative for their stated targets. They do not regress the existing data, but N1 represents a forward-looking regression risk that will manifest on the next pipeline run when 3 known legitimate-ticker filings are re-processed. The 7 persisting findings are all explicitly Stage-2 or Wave-5/6 work items. No FAIL because no fix made anything strictly worse in the DB; no PASS because most of the audit surface is unchanged and one new regression risk was introduced.

**Recommended priority for next wave**:
1. N1 fix — context-sensitive deny-list or whitelist cross-check before R3 rolls into a pipeline run that re-touches USD/JPY/AND filings
2. F1/F3 schema + step2 fan-out — single biggest data-quality lever (clears 55,694 rows, unblocks F2/F4 cascade)
3. F6 — surface SEC client errors so F5's `extracted_zero` retry can route to `error` instead, getting better operator signal

## DB queries run (re-verification)

```sql
-- F1
SELECT COUNT(DISTINCT series_id), SUM(CASE WHEN n_trusts>1 THEN 1 ELSE 0 END), SUM(CASE WHEN n_trusts>1 THEN n_trusts ELSE 0 END)
FROM (SELECT series_id, COUNT(DISTINCT trust_id) AS n_trusts FROM fund_status WHERE series_id!='' GROUP BY series_id);
-- → (22255, 4644, 55694)

-- F2 / N4
SELECT ticker, COUNT(DISTINCT fund_name) AS n_funds, COUNT(*) AS rows FROM fund_status
WHERE ticker!='' AND ticker IS NOT NULL
GROUP BY trust_id, ticker HAVING n_funds>5 ORDER BY 2 DESC LIMIT 10;
-- → REX 16, APHU 11, LXOM 10, VSCG 8, STPW 7, AEAXX 7×5 trusts

-- F4
SELECT ticker, COUNT(DISTINCT trust_id), COUNT(*) FROM fund_status WHERE ticker IN ('SYM','SYMBO') GROUP BY ticker;
-- → SYM 54/1498, SYMBO 54/657

-- F7
SELECT COUNT(*) FROM (
  SELECT trust_id, series_id, COUNT(DISTINCT fund_name) AS n FROM fund_status
  WHERE series_id IS NOT NULL AND series_id!='' GROUP BY trust_id, series_id HAVING n>1
);
-- → 1034

-- F9
SELECT effective_date_confidence, COUNT(*) FROM fund_status GROUP BY effective_date_confidence;
-- → HEADER 203203, HIGH 7837, NULL 2598, MEDIUM 171, full 1

-- N1
SELECT trust_id, fund_name, status, latest_filing_date FROM fund_status WHERE ticker IN ('USD','JPY','AND');
-- → USD ProShares Ultra Semiconductors EFFECTIVE 2025-09-23
-- → JPY Lazard Japanese Equity ETF EFFECTIVE 2026-04-23
-- → AND Global X FTSE Andean 40 ETF EFFECTIVE 2017-03-17

-- N2
SELECT COUNT(*) FROM fund_extractions fe JOIN filings f ON fe.filing_id=f.id
WHERE f.registrant='AQR Funds' AND fe.class_symbol='CLASS'
AND f.filing_date >= (SELECT date(MAX(filing_date),'-1 day') FROM filings);
-- → 7 (T2 not applied locally)

-- N3
SELECT MAX(created_at) FROM fund_extractions;  -- 2026-05-04
SELECT MAX(updated_at) FROM fund_status;       -- 2026-05-04
```
