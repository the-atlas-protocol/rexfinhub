# Fix R3 — Body-extractor ticker regex tightening + manifest hygiene

**Branch**: `audit-fix-R3-extractor`
**Date**: 2026-05-11
**Scope**: forward-only (no DB cleanup of the existing 50K+ polluted rows)
**Files touched** (3, all sole-owner):
- `etp_tracker/body_extractors.py`
- `etp_tracker/step3.py`
- `etp_tracker/manifest.py`

## Problem

Stage 1 SEC ingestion audit found:
- Ticker `SYM` assigned to 1,498 rows across 54 trusts
- Ticker `SYMBO` assigned to 657 rows across 54 trusts
- Same pattern on REX (16 rows), APHU (11), LXOM (10), STPW (7)
- Fund names on these polluted rows: `Class R`, `-`, `I` (garbage)

Root cause: the body-extractor's `[A-Z0-9]{1,6}` regex captured truncated
fragments of the literal column header word `SYMBOL` (and `TICKER`). The
existing deny-lists (`_BAD_TICKERS` in step4, `_TICKER_STOPWORDS` in step3)
included `SYMBOL` itself but not its truncations.

Secondary issue: `record_success(..., extraction_count=0)` was being called
unconditionally after an extraction attempt completed without raising — so
silent zero-row outcomes were marked `success` and permanently bypassed retry.

## Fix

### 1. body_extractors.py — new validator + deny-list

Added `_BODY_BAD_TICKERS` (24 entries covering SYMBOL/TICKER truncations,
column headers, English connectives, currency codes, fund-related words,
corporate suffixes) and `_is_valid_body_ticker()` helper.

**Regex change**

| Before | After |
| --- | --- |
| `re.fullmatch(r"[A-Z0-9]{1,6}", tkr)` | `re.compile(r"^[A-Z][A-Z0-9]{1,4}$")` |

- Length narrowed from `1-6` to `2-5` (real US ETF tickers are 2-5; 1-char is
  share-class label, 6-char in body text is overwhelmingly a SYMBOL fragment)
- Leading character must be a letter (rejects `1ABC`, `2X` etc.)
- Combined with the deny-list check, rejects all observed pollution patterns

The validator replaces three identical regex calls in `body_extractors.py`:
- `extract_from_html_string` table-row branch
- `extract_from_html_string` line-split fallback branch
- `extract_from_primary_pdf` line-split branch

### 2. step3.py — extended `_TICKER_STOPWORDS`

The proximity-window extractor (`_extract_ticker_for_series_from_texts`) uses
the same shape of regex (`[A-Z0-9]{1,6}`) with `_valid_ticker()` filtering.
Extended `_TICKER_STOPWORDS` from 20 → 49 entries to match the body-extractor
deny-list. Notable additions:

```
SYM, SYMB, SYMBO, SYMBOLS, TIC, TICK, TICKE, TICKER, TICKERS,
COL, ROW, ITEM, PAGE, NUM, TOTAL, NULL, NAME, NA, N/A, TBA,
ALL, OR, OF, BY, EUR, GBP, JPY, CAD, AUD, ETN, FUNDS, CLASS,
SHARE, SHARES
```

### 3. manifest.py — `record_extraction_result()` + retry on `extracted_zero`

Added `record_extraction_result(manifest, accession, form, extraction_count)`:
- `count >= 1` → `status="success"` (same as before)
- `count == 0` → `status="extracted_zero"` (new) with `retry_count` increment

Updated `get_retry_accessions()` to include `extracted_zero` alongside `error`,
bounded by the same `max_retries=3` ceiling.

`record_success()` is preserved unchanged for the two intentional skip sites
in `step3.py` (EFFECT filings on 40-Act trusts; ETF-only triage skipping
non-ETF mutual funds). These are deliberate 0-row outcomes, not extraction
failures, and should never be retried.

Updated step3.py line 598 to call `record_extraction_result` instead of
`record_success` after the extraction-strategy dispatch.

## Verification

`temp/verify_fix_R3.py` exercises three layers:

```
=== Validator tests ===          21 reject + 11 accept cases — Validator failures: 0
=== Integration test ===         Synthetic HTML with SYMBOL/TICKER trash mixed
                                 with BMAX/AIPI valid rows
                                 Extracted symbols: ['AIPI', 'BMAX']
                                 Integration failures: 0
=== Manifest tests ===           4 cases: extracted_zero retry-eligible,
                                 success not retried, intentional skip stays
                                 success, retry_count caps at max_retries
                                 Manifest failures: 0

ALL TESTS PASSED
```

Imports cleanly:

```
$ python -c "from etp_tracker import step3, body_extractors, manifest; ..."
imports OK
record_extraction_result: <function record_extraction_result at 0x...>
_is_valid_body_ticker: True False    # BMAX accepted, SYM rejected
```

Live re-extraction on a known-bad filing was deferred — the validator and
integration tests cover the regression surface, and a real SEC fetch from a
worktree branch could pollute shared http_cache. The fix is conservative
(deny-list only adds rejections; never accepts something previously rejected),
so the worst-case impact on a real filing is the loss of a few legitimate
6-char tickers — none are known to exist in the REX/competitor universe.

## Rollback

```
git checkout main
git branch -D audit-fix-R3-extractor
```

No DB migrations, no manifest invalidation, no PIPELINE_VERSION bump. The
extracted_zero status will appear gradually as filings are re-processed and
will be retried up to 3 times each. Existing manifests with `success` rows
that should have been `extracted_zero` are NOT retroactively fixed (they will
remain `success` until the next manifest invalidation).

If `extracted_zero` retries cause excessive load on SEC, lower
`max_retries` in `get_retry_accessions(manifest, max_retries=1)` at the
caller site (currently `step3.py:533`).

## Deferred (Stage 2)

- Cleanup of existing 50K+ polluted rows (`SYM`, `SYMBO`, etc.) from the
  `fund_status` table. This is a separate dedup task per assignment.
- Cross-validation against a CBOE/known-good ticker universe before write.
  Not implemented here because the deny-list + tightened regex eliminates
  the observed pollution; CBOE-cross-check would be a defense-in-depth
  layer best added once the universe table is stable.
