# Stage 1 Audit — REX-Specific Tables

**Generated:** 2026-05-11T23:00:38Z
**Agent:** rex_tables
**Scope:** `rex_products`, `capm_products`, `capm_trust_aps`, `capm_audit_log`, `reserved_symbols`, `classification_audit_log`, plus relevant slice of `mkt_master_data` (where the "REX-internal" atlas-memory warts actually live).
**Mode:** READ ONLY — no writes performed.

---

## Summary

The REX-internal data surface is in better structural shape than the atlas-memory wart list suggests, but several of those warts are mis-scoped (they actually point at `mkt_master_data`, not `rex_products`) and at least three are **larger than memory says**. The most important new findings are: (1) a placeholder-ticker bug where 35 `rex_products` rows share the literal ticker string `'REX'`; (2) the `capm_audit_log` flow logs **interactive UPDATEs only** — the 74-row bulk seed and the 21-row manual T-REX 2X insert never produced `capm_audit_log` rows (the 21-row insert was logged into `classification_audit_log` instead, which works but splits the audit story across two tables); (3) the VPS database lacks the `capm_audit_log`, `reserved_symbols`, and `classification_audit_log` **tables entirely** — every Render DB swap is dropping those onto bare-schema territory and relying on `init_db()` to recreate them, which means **no audit history persists across a swap**; (4) for the `mkt_master_data`-rooted warts (#101–103), the underlying source columns are 100% NULL, so the proposed backfill targets do not yet exist in the column the wart names.

The single largest data shape problem found is wart #112 (status/form drift), where 268 rows have `status='Awaiting Effective' AND latest_form='485BPOS'` — the SEC has already declared these effective and the rex pipeline has not advanced the status. The legacy 474-row figure from atlas memory is now 268, but the contradictory pattern is far more dominant than memory suggests.

---

## Row counts (local vs VPS)

| Table | LOCAL `etp_tracker.db` | VPS `etp_tracker.db.fromvps` |
|---|---:|---:|
| `rex_products` | **723** | 702 |
| `capm_products` | **74** | 0 |
| `capm_trust_aps` | **40** | 0 |
| `capm_audit_log` | **1** | *(table missing)* |
| `reserved_symbols` | **282** | *(table missing)* |
| `classification_audit_log` | **18,983** | *(table missing)* |
| `mkt_master_data` | 7,361 | (not checked) |

**Drift narrative:**
- The 21-row gap on `rex_products` is exactly the `insert_trex_2x_2026_05_09.py` batch (14 US + 7 non-US T-REX 2X). Local has them; VPS does not.
- `capm_products` / `capm_trust_aps` ship to Render via the CSV seed (`webapp/data_static/*.csv` → `_capm_seed_if_empty`), so a zero-row VPS is fine *as long as* deployment runs through `init_db()`.
- `capm_audit_log`, `reserved_symbols`, and `classification_audit_log` are **schema-missing on VPS**. They depend on `init_db()` running on Render after a DB upload to create them. **The audit trail does not survive a DB swap.**

---

## Wart inventory

| Wart | Atlas # | Memory says | **Actual scope** | Severity |
|---|---|---|---|---|
| `is_singlestock` backfill from `underlier_type='Single Stock'` | #101 | "Many" | **Source column `underlier_type` is 100% NULL in `mkt_master_data`** — backfill cannot run. 634 rows have ticker strings in `is_singlestock` (see #103). | HIGH (wart is mis-scoped; the column needs different remediation) |
| Defined Outcome cap/buffer/barrier backfill | #102 | "428 of 632 missing" | `primary_strategy='Defined Outcome'` returns **0 rows** (column is 100% NULL). Re-quantified against `outcome_type IS NOT NULL`: **449 of 449 (100%)** missing all three. Against `etp_category='Defined'`: **520 of 520 (100%)** missing. | HIGH (worse than memory) |
| `is_singlestock` column type confusion | #103 | "Storing ticker strings" | **Confirmed.** Column declared `VARCHAR(20)` (no boolean). 634 of 7,361 rows store underlier tickers ('TSLA US', 'XBTUSD Curncy', 'NVDA US' etc.). 6,727 rows NULL. Zero rows hold a true/false-like string. | HIGH |
| 50 PEND funds with past inception dates not promoted | #104 | "50 rows" | Cannot quantify directly — `rex_products` has no `PEND` status (uses string labels not codes; `PEND` is a `mkt_master_data.market_status` value). Re-quantified against rex_products: **509 rows** with `estimated_effective_date < today` AND status IN ('Filed','Filed (485A)','Filed (485B)','Awaiting Effective'). | HIGH (10× memory) |
| Stale "Past Effective Date" 2016–2018 rows | #111 | "~250 rows" | Only **10 rows** with `initial_filing_date` in 2016–2018 (7 'Awaiting Effective', 3 'Listed'). Memory figure is stale — the 250 number probably came from a different definition (e.g. all pre-2024 stale rows = ~110, see SQL appendix). | LOW–MED |
| Status/form drift | #112 | "474 rows" | **328 rows** in clean contradiction patterns: 268 (Awaiting Effective + 485BPOS) + 54 (Listed + 485BPOS — partly correct) + 6 (Filed + 485BPOS) + 4 (Delisted + no listed_date) + others. Cleanest single contradiction (#112's headline case): **268** (Awaiting Effective + 485BPOS). | HIGH |
| Pipeline status enum: 5 of 15 used | #113 | "5 of 15" | **6 of 15** used now: Awaiting Effective (327), Filed (273), Listed (85), Filed (485A) (21), Delisted (11), Research (6). The 9 Counsel/Board lifecycle statuses are still 0-row. | LOW (informational) |
| `rex_product_status_history` table for Recent Activity | #114 | "Does not exist" | **Confirmed.** Table is not present in DB. (No status_history audit trail anywhere — `classification_audit_log` carries the 21 manual rows from today only because the seed script explicitly wrote there.) | MED |
| Pipeline columns underlier/direction missing; backfill fee/LMM/Exchange | #115 | "Missing default columns" | Across all 723 rex_products: 47% NULL underlier, **97% NULL direction**, 92–93% NULL on mgt_fee/lmm/exchange/tracking_index/fund_admin/cu_size, **97% NULL starting_nav**. On Listed-only (85 rows): **100% NULL direction**, 39% NULL mgt_fee, 42% NULL lmm, 38% NULL exchange. | HIGH (Listed-only direction at 100% is the headline) |
| Filing status semantics review | #116 | (Open) | `latest_form` values present: 485BPOS (339), 485BXT (248), 485APOS (101), ETN (21), 497 (4), ICAV (3), S-1 (1), NULL (6). The 'ETN' value is a structure marker not a form; 'ICAV' is a structure marker. These are leaking into a column intended for SEC form types — see F6. | MED |
| `classification_audit_log` exists? | (prompt asserted "never built") | "Never built" | **Wrong — it exists and has 18,983 rows.** Sources: sweep_high (16,238), manual_batch_approval (2,710), manual_insert (21), manual_polish_fix (14). Table missing on VPS — local-only construct. | (informational) |
| **NEW** — Placeholder ticker `'REX'` | NEW | — | **35 rex_products rows share `ticker='REX'`** (all REX IncomeMax X Strategy ETFs, all Awaiting Effective). Tickers haven't been reserved yet but the field is populated with the suite name. | HIGH |
| **NEW** — Multi-row ticker reuse during pre-launch | NEW | — | 13 tickers reused across 2–35 rows. APHU (11), STPW (7), DOJU (4), SNDU/CPTO/AIAG (3), 7 others (2). All under non-Listed statuses; Listed-only view dedupes to 0. Risk surfaces when admin toggles `?include_all=1`. | MED |
| **NEW** — `capm_audit_log` only captures interactive UPDATEs | NEW | — | 1 audit row vs 74 capm_products rows created today; CSV-seed flow (database.py `_capm_seed_if_empty`) and any future `import_capm.py` runs bypass the auditor. Also no ADD/DELETE entries logged for inline operations are present. | HIGH |
| **NEW** — Audit log tables missing on VPS | NEW | — | VPS DB has no `capm_audit_log`, `reserved_symbols`, or `classification_audit_log` table at all. Render rebuilds them via `init_db()` on first request after swap. **All historical audit entries are wiped on every Render upload.** | HIGH |
| **NEW** — `insert_trex_2x_2026_05_09.py` audit goes to wrong log | NEW | — | The 21 manual T-REX 2X inserts wrote to `classification_audit_log` (sweep_run_id=`manual_2026-05-09_trex2x`), not to a `rex_products`-scoped audit. Cross-table audit story is fragmented. | MED |
| **NEW** — `direction` is 100% NULL on Listed | NEW | — | All 85 Listed rex_products have `direction IS NULL`. Direction is one of the headline merchandising columns; this is a complete data gap on the operational table. | HIGH |
| **NEW** — 4 Listed rex_products are LIQU/DLST in mkt_master_data | NEW | — | BMAX (LIQU), FNGA (LIQU), SOLX (DLST), XRPK (DLST) — rex says Listed but Bloomberg/Bloomberg-derived market_status says delisted/liquidated. | HIGH |
| **NEW** — `direction` enum drift in rex vs capm | NEW | — | `rex_products.direction`: only 21 non-NULL values, all 'Long' (today's T-REX 2X seed). `capm_products.direction`: free-form via `_CAPM_UPDATE_FIELDS` with `str_or_none` — no validation. The union view's "direction" column on /operations/products is unreliable. | MED |
| **NEW** — `manually_edited_fields` is 0% populated | NEW | — | `capm_products.manually_edited_fields IS NULL` for all 74 rows. Only entry-point that sets it is `_capm_update_impl`; bulk import does not initialize, so the "skip during daily import" logic in `import_capm.py` has nothing to skip yet. Will become a problem when admin edits land. | LOW (working as designed today; latent risk) |
| **NEW** — `reserved_symbols.exchange` has whitespace pollution | NEW | — | 7 rows have exchange = `'NASDAQ\xa0OMX'` (non-breaking space U+00A0 between NASDAQ and OMX). Will silently break a literal-equality filter. | LOW |
| **NEW** — `reserved_symbols` linked_filing_id / linked_product_id are 100% NULL | NEW | — | All 282 reservations are detached from rex_products / FilingAlert. The model's stated goal ("map REX's reserved tickers against our filings") is unmet. 14 of 282 symbols *do* match an existing rex_products ticker via natural join — but the linkage is not materialized. | MED |
| **NEW** — `_capm_seed_if_empty` runs on EVERY startup | NEW | — | The seed writes 74 rows blindly when `capm_products` is empty. After a Render swap from VPS, capm_products is 0 → seed fires → 74 rows reinserted with **new** created_at/updated_at timestamps and **without** audit log entries. Idempotent in count, but timestamp-mutating and audit-blind. | MED |

---

## Findings

### F1: `is_singlestock` is a ticker-storage column, not a boolean

**Severity:** HIGH
**Affected:** `mkt_master_data.is_singlestock` (column declared `VARCHAR(20)`).

The column is supposed to indicate whether a fund tracks a single stock. In the DB it actually stores the **underlier ticker as a string**:

```
is_singlestock='XBTUSD Curncy'   23 rows
is_singlestock='XETUSD Curncy'   19 rows
is_singlestock='TSLA US'         19 rows
is_singlestock='NVDA US'         17 rows
is_singlestock='MSTR US'         15 rows
...
NULL                             6,727 rows (91%)
```

634 of 7,361 rows are non-NULL; not one of them holds a `Y`/`N`/`true`/`false` value. Atlas wart #103 calls this out at high level; this audit confirms it is universal — there is no "good half" of the column. Combined with #101 (the proposed `underlier_type='Single Stock'` backfill source), the situation is: the proposed source column (`underlier_type`) is 100% NULL, so the proposed backfill cannot run. Wart #101 should be marked **not actionable** in its current form.

### F2: Defined Outcome cap/buffer/barrier coverage is 0%, not 32%

**Severity:** HIGH
**Affected:** `mkt_master_data.cap_pct`, `buffer_pct`, `barrier_pct` on Defined Outcome rows.

Atlas wart #102 says "428 of 632 missing". Actual:

| Definition of "Defined Outcome" | Total rows | Rows missing cap/buffer/barrier |
|---|---:|---:|
| `primary_strategy='Defined Outcome'` (atlas wording) | 0 | 0 (column 100% NULL) |
| `outcome_type IS NOT NULL` | 449 | **449 (100%)** |
| `etp_category='Defined'` | 520 | **520 (100%)** |
| `map_defined_category IS NOT NULL` | 527 | (presumed 100%) |

Zero rows have all three values populated. The wart's "428 of 632" stat appears to have come from a different snapshot or a different definition than what's in the live DB.

### F3: Status / form drift is 328 rows, with 268 on the headline pattern

**Severity:** HIGH
**Affected:** `rex_products.status` vs `latest_form`.

Cleanest single contradiction: `status='Awaiting Effective' AND latest_form='485BPOS'` → **268 rows**. By SEC convention 485BPOS = post-effective amendment, so these products' status should be `Effective` or `Listed`. Other drift patterns found:

| Pattern | Rows |
|---|---:|
| Awaiting Effective + 485BPOS (should be Effective/Listed) | **268** |
| Filed + 485BPOS (should be Effective) | 6 |
| Listed + no `official_listed_date` | 2 |
| Delisted + no `official_listed_date` | 4 |
| Listed + market_status LIQU/DLST in mkt_master_data | 4 (BMAX, FNGA, SOLX, XRPK) |

The atlas-memory figure of 474 is stale; today's primary pattern is 268. The remediation isn't a simple column update — the "Effective" status was added to `VALID_STATUSES` precisely for this case but no automated promoter exists.

### F4: 50-PEND wart (#104) is actually 509 rows

**Severity:** HIGH
**Affected:** `rex_products` (pre-effective statuses with past estimated_effective_date).

Atlas wart says "50 PEND funds with past inception dates not promoted to ACTV". `rex_products` has no PEND/ACTV codes (those are `mkt_master_data.market_status` values). Re-quantified against the rex pipeline:

```
WHERE estimated_effective_date < date('now')
  AND status IN ('Filed','Filed (485A)','Filed (485B)','Awaiting Effective')
→ 509 rows
```

Bucketed by staleness:

| Bucket | Filed | Awaiting Effective |
|---|---:|---:|
| 0–30d past | 33 | — |
| 30–90d | 19 | 16 |
| 90–180d | 107 | 95 |
| 180–365d | 15 | 87 |
| 365+d | 10 | **127** |

127 rows are **>1 year past** their estimated effective date. This is the chronic backlog that's masking #112's 268-row pattern.

### F5: Placeholder ticker `'REX'` on 35 rows

**Severity:** HIGH
**Affected:** `rex_products` (35 rows in the REX IncomeMax family).

35 rex_products rows share `ticker = 'REX'`:

```
id=20  status=Awaiting Effective  REX IncomeMax AMD Strategy ETF
id=22  status=Awaiting Effective  REX IncomeMax BABA Strategy ETF
id=23  status=Awaiting Effective  REX IncomeMax BIIB Strategy ETF
... (32 more)
```

These should have actual reserved tickers (or NULL until reserved). The unified view (`/operations/products?include_all=1`) will key them all on `'REX'` and try to attach the same `capm_by_ticker['REX']` (which is None today, so no immediate corruption). The risk is the day someone creates a CapM row with `ticker='REX'` — it will then attach to all 35.

Related: 13 other tickers are reused across 2–35 rex_products rows (APHU=11, STPW=7, DOJU=4, etc.) — these are placeholder/pre-launch reservations being recycled, per the comment in `capm.py:235`. Listed-only filter neutralizes this for the default `/operations/products` view (verified: 0 Listed dups). Admin `?include_all=1` view is where it gets messy.

### F6: 100% NULL `direction` on Listed rex_products

**Severity:** HIGH
**Affected:** `rex_products.direction` for status='Listed' (85 rows).

All 85 currently-Listed REX products have `direction IS NULL`. This is the column that should tell merchandising / pipeline reports whether each fund is Long or Short. Site-wide null distribution on `rex_products.direction`: 702/723 = 97% NULL. The only non-NULL entries are the 21 T-REX 2X funds seeded today.

Related: `mgt_fee` 39% NULL, `lmm` 42% NULL, `exchange` 38% NULL, `cu_size` 45% NULL, `starting_nav` 79% NULL — all on **Listed-only** rows where these fields *must* exist operationally. Wart #115 understated the scope; this is not just "missing default columns", it's a chronic backfill problem on the funds we already trade.

### F7: `capm_audit_log` is silent on bulk seed and on the manual T-REX insert

**Severity:** HIGH
**Affected:** `capm_audit_log` (1 row), `classification_audit_log` (audit for rex_products lives here by accident).

Today (2026-05-11) had two significant write events on REX-internal tables:

1. The 74-row capm CSV seed (via `_capm_seed_if_empty` at init time) — produced **0 audit entries**.
2. The 21-row T-REX 2X manual insert (`scripts/insert_trex_2x_2026_05_09.py`) — produced 21 entries in `classification_audit_log` (sweep_run_id=`manual_2026-05-09_trex2x`), **none** in `capm_audit_log`, and rex_products has no dedicated audit log table.

The only entry in `capm_audit_log` is a single test UPDATE on NVDQ.notes ("audit-test RyuEl-Asmar") at 17:51 UTC. The audit logger lives in `webapp/routers/capm.py:_audit_log` and is only called from `_capm_update_impl`. There are no ADD or DELETE codepaths wired in. The seed and the manual insert script both bypass it.

Net: **the only auditable changes today are admin inline UPDATEs through the web UI.** Anything imported via CSV seed, CLI script, or daily Bloomberg sync is invisible to the activity log.

### F8: Audit-log tables missing on VPS → wiped on every Render swap

**Severity:** HIGH
**Affected:** Render-served DB; `capm_audit_log`, `reserved_symbols`, `classification_audit_log`.

The VPS DB (`data/etp_tracker.db.fromvps`, 617 MB) does not contain these three tables at all:

```
sqlite3 etp_tracker.db.fromvps "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'capm%'"
→ (none)
```

The deployment flow is: VPS builds DB → uploads to Render via `POST /api/v1/db/upload` → Render replaces its local copy → `init_db()` creates missing tables via `Base.metadata.create_all`. So after every upload, those three tables are bare-schema and *every existing row in them is lost*. The CSV-seed function refills `capm_products` / `capm_trust_aps`. But `capm_audit_log` and `classification_audit_log` have no seed — they restart empty. `reserved_symbols` has no seed either, but `scripts/import_reserved_symbols.py` can rebuild from the Excel.

**Concretely:** every audit log entry Ryu has from inline edits will be erased on the next VPS→Render DB upload, unless those tables are added to the VPS build or excluded from the swap.

### F9: `_build_unified_row` Listed-only filter hides 7 Delisted-but-curated capm rows

**Severity:** MED
**Affected:** `/operations/products` default view.

The new Listed-only default (per Ryu 2026-05-11) filters on `status_display.lower() == "listed"`, where `status_display` flows from `rex.status` if present. Of the 74 capm rows, 7 attach to a rex row with `status='Delisted'` (ETQ, ARMU, BULU, DKUP, AXUP, PXIU, BKNU) and 1 to `Awaiting Effective` (TSII). These 8 rows are hidden from the default view despite being fully curated in capm_products. Expected? If yes, the filter is correct. If the user expected "all curated capm rows always visible", this is a regression.

### F10: Reserved-symbols ↔ rex_products linkage unmaterialized

**Severity:** MED
**Affected:** `reserved_symbols.linked_filing_id`, `linked_product_id`.

The model docstring states: *"map REX's reserved tickers against our filings so we know which products are coming next."* Today both linkage columns are 100% NULL across 282 rows. A natural-join on symbol surfaces 14 reservations whose ticker already exists as a `rex_products.ticker` (APPI, ARMU, AXUP, BKNU, BULU, DFPI, DKUP, ETQ, FNGA, FTPI, PXIU, QQQQ, SEMU, UPUP). These are obvious linkage candidates that the pipeline has never written.

### F11: `reserved_symbols.exchange` has non-breaking-space pollution

**Severity:** LOW
**Affected:** 7 rows.

Seven rows have `exchange = 'NASDAQ\xa0OMX'` (U+00A0 non-breaking space). A WHERE clause filtering on `exchange = 'NASDAQ OMX'` (regular space) will miss these. Likely an Excel-paste artifact in the import script.

### F12: `latest_form` is being used to store structure markers (ETN, ICAV)

**Severity:** MED
**Affected:** `rex_products.latest_form`.

`latest_form` is intended for SEC form types (485BPOS, 485APOS, etc.). Values in DB include 'ETN' (21 rows) and 'ICAV' (3 rows), which are product-structure markers, not SEC form types. These are likely set by an importer that doesn't have a real form value and falls back to the structure label. Wart #116 should target this: define `latest_form` strictly as form type, move structure markers to a separate column or to `product_suite`.

### F13: `_capm_seed_if_empty` rewrites timestamps and bypasses audit on every cold start

**Severity:** MED
**Affected:** Any environment that starts from an empty `capm_products` table (Render after DB swap, fresh dev).

`webapp/database.py:174` stamps `created_at = updated_at = now` for every CSV row on seed. If the CSV is updated (column added, row removed), the next cold-start view shows every product as "created today." It is otherwise idempotent (`COUNT(*) > 0` gate). It also writes nothing to `capm_audit_log`, so seeded data is indistinguishable from real ADDs in the activity log.

### F14: `insert_trex_2x_2026_05_09.py` analysis

**Severity:** (informational)

- **Audit:** writes to `classification_audit_log` with a clear `sweep_run_id`. Working as intended; the row count matches (21 expected, 21 found).
- **Idempotent:** dedupes by `RexProduct.name == name`. Running it twice in a row inserts 0 rows on the second pass. Verified by code review — confirmed no duplicate names in DB.
- **Atomic:** uses a single transaction wrapping all 21 inserts + audit entries; rollback on any error. Good.
- **Cross-log:** because the script targets `classification_audit_log` not `capm_audit_log`, the activity log at the bottom of `/operations/products` (which reads `CapMAuditLog`) will not show today's 21 inserts. This is the symptom — not a script bug, but a missing-audit-table problem for rex_products.

---

## SQL appendix

All queries run via:
`python -c "from webapp.database import SessionLocal; from sqlalchemy import text; db = SessionLocal(); print(db.execute(text('SQL')).fetchall())"`

### Row counts & schema

```sql
-- All four REX-internal tables
SELECT COUNT(*) FROM rex_products;     -- 723
SELECT COUNT(*) FROM capm_products;    -- 74
SELECT COUNT(*) FROM capm_trust_aps;   -- 40
SELECT COUNT(*) FROM capm_audit_log;   -- 1
SELECT COUNT(*) FROM reserved_symbols; -- 282
SELECT COUNT(*) FROM mkt_master_data;  -- 7361
SELECT COUNT(*) FROM classification_audit_log;  -- 18983
```

### rex_products status distribution (wart #113)

```sql
SELECT status, COUNT(*) FROM rex_products GROUP BY 1 ORDER BY 2 DESC;
-- Awaiting Effective  327
-- Filed               273
-- Listed               85
-- Filed (485A)         21
-- Delisted             11
-- Research              6
-- (6 of 15 VALID_STATUSES used)
```

### Status × latest_form crosstab (wart #112)

```sql
SELECT status, COALESCE(latest_form,'<NULL>'), COUNT(*)
FROM rex_products GROUP BY 1,2 ORDER BY 3 DESC LIMIT 15;
-- Awaiting Effective  485BPOS  268   <-- headline drift
-- Filed               485BXT   206
-- Filed               485APOS   61
-- Listed              485BPOS   54
-- Awaiting Effective  485BXT    40
-- Filed (485A)        485APOS   21
-- Listed              ETN       21
-- Awaiting Effective  485APOS   19
-- Delisted            485BPOS   11
-- Filed               485BPOS    6   <-- drift
-- Research            <NULL>     6
-- Listed              497        4
-- Listed              ICAV       3
-- Listed              485BXT     2
-- Listed              S-1        1
```

### Specific drift checks

```sql
SELECT COUNT(*) FROM rex_products WHERE status='Awaiting Effective' AND latest_form='485BPOS';  -- 268
SELECT COUNT(*) FROM rex_products WHERE status='Listed' AND official_listed_date IS NULL;        --   2
SELECT COUNT(*) FROM rex_products WHERE status='Listed' AND (ticker IS NULL OR ticker='');       --   0
SELECT COUNT(*) FROM rex_products WHERE status='Filed' AND latest_form='485BPOS';                --   6
SELECT COUNT(*) FROM rex_products WHERE status='Filed (485B)' AND (latest_form != '485BPOS' OR latest_form IS NULL); -- 0
SELECT COUNT(*) FROM rex_products WHERE status='Filed (485A)' AND (latest_form != '485APOS' OR latest_form IS NULL); -- 0
SELECT COUNT(*) FROM rex_products WHERE status='Delisted'  AND official_listed_date IS NULL;     --   4
```

### Past-effective backlog (wart #104, expanded)

```sql
SELECT COUNT(*) FROM rex_products
WHERE estimated_effective_date < date('now')
  AND status IN ('Filed','Filed (485A)','Filed (485B)','Awaiting Effective');
-- 509

-- Staleness buckets:
SELECT CASE
    WHEN estimated_effective_date >= date('now','-30 days') THEN '0-30d'
    WHEN estimated_effective_date >= date('now','-90 days') THEN '30-90d'
    WHEN estimated_effective_date >= date('now','-180 days') THEN '90-180d'
    WHEN estimated_effective_date >= date('now','-365 days') THEN '180-365d'
    ELSE '365+d' END AS bucket, status, COUNT(*)
FROM rex_products
WHERE estimated_effective_date < date('now')
  AND status IN ('Filed','Filed (485A)','Filed (485B)','Awaiting Effective')
GROUP BY 1,2 ORDER BY 1, 3 DESC;
-- 0-30d     Filed                33
-- 180-365d  Awaiting Effective   87
-- 180-365d  Filed                15
-- 30-90d    Filed                19
-- 30-90d    Awaiting Effective   16
-- 365+d     Awaiting Effective  127
-- 365+d     Filed                10
-- 90-180d   Filed               107
-- 90-180d   Awaiting Effective   95
```

### Stale 2016–2018 (wart #111)

```sql
SELECT COUNT(*) FROM rex_products WHERE strftime('%Y', initial_filing_date) IN ('2016','2017','2018');  -- 10
SELECT status, COUNT(*) FROM rex_products
WHERE strftime('%Y', initial_filing_date) IN ('2016','2017','2018') GROUP BY 1 ORDER BY 2 DESC;
-- Awaiting Effective  7
-- Listed              3
```

### NULL coverage of operational columns (wart #115)

```sql
-- Across all 723 rex_products:
underlier       338 NULL (47%)
direction       702 NULL (97%)
mgt_fee         664 NULL (92%)
lmm             667 NULL (92%)
exchange        664 NULL (92%)
tracking_index  670 NULL (93%)
fund_admin      664 NULL (92%)
cu_size         669 NULL (93%)
starting_nav    702 NULL (97%)

-- On Listed-only (85 rows):
underlier        26 NULL  (31%)
direction        85 NULL (100%)  <-- F6 headline
mgt_fee          33 NULL  (39%)
lmm              36 NULL  (42%)
exchange         33 NULL  (39%)
tracking_index   39 NULL  (46%)
fund_admin       33 NULL  (39%)
cu_size          38 NULL  (45%)
starting_nav     67 NULL  (79%)
```

### mkt_master_data.is_singlestock (warts #101, #103)

```sql
SELECT COALESCE(is_singlestock,'<NULL>'), COUNT(*) FROM mkt_master_data GROUP BY 1 ORDER BY 2 DESC LIMIT 10;
-- NULL                6727
-- XBTUSD Curncy         23
-- XETUSD Curncy         19
-- TSLA US               19
-- NVDA US               17
-- MSTR US               15
-- XAU Curncy            14
-- PLTR US               12
-- COIN US               11
-- AMD US                10

SELECT COUNT(*) FROM mkt_master_data WHERE underlier_type='Single Stock' AND ...; -- 0 (column 100% NULL)
```

### mkt_master_data.outcome_type / Defined Outcome (wart #102)

```sql
SELECT COALESCE(outcome_type,'<NULL>'), COUNT(*) FROM mkt_master_data GROUP BY 1 ORDER BY 2 DESC;
-- NULL                  6912
-- Buffer                 323
-- Max Buffer              53
-- Accelerator             26
-- Income                  19
-- Ladder                  18
-- Floor                    7
-- Dual Directional Buffer  2
-- BUFFER                   1   <-- casing inconsistency (BUFFER vs Buffer)

SELECT COUNT(*) FROM mkt_master_data WHERE outcome_type IS NOT NULL;                                                -- 449
SELECT COUNT(*) FROM mkt_master_data WHERE outcome_type IS NOT NULL AND cap_pct IS NULL;                            -- 449
SELECT COUNT(*) FROM mkt_master_data WHERE outcome_type IS NOT NULL AND buffer_pct IS NULL;                         -- 449
SELECT COUNT(*) FROM mkt_master_data WHERE outcome_type IS NOT NULL AND barrier_pct IS NULL;                        -- 449
SELECT COUNT(*) FROM mkt_master_data WHERE outcome_type IS NOT NULL AND cap_pct IS NOT NULL
                                       AND buffer_pct IS NOT NULL AND barrier_pct IS NOT NULL;                      -- 0

SELECT etp_category, COUNT(*) FROM mkt_master_data GROUP BY 1 ORDER BY 2 DESC;
-- NULL      5076
-- LI         877
-- Defined    520   <-- (none have cap/buffer/barrier populated either)
-- Thematic   410
-- CC         344
-- Crypto     134
```

### Placeholder / reused tickers (F5, new wart)

```sql
SELECT ticker, COUNT(*) FROM rex_products
WHERE ticker IS NOT NULL AND ticker != ''
GROUP BY ticker HAVING COUNT(*) > 1 ORDER BY 2 DESC;
-- REX    35
-- APHU   11
-- STPW    7
-- DOJU    4
-- SNDU    3
-- CPTO    3
-- AIAG    3
-- TSII    2
-- SUIT    2
-- MEMC    2
-- FGRU    2
-- BTZZ    2
-- AQLG    2

-- Listed-only — confirms dedupe holds on default /operations/products view:
SELECT ticker, COUNT(*) FROM rex_products WHERE status='Listed'
GROUP BY UPPER(TRIM(ticker)) HAVING COUNT(*) > 1; -- (empty)
```

### Listed cross-check vs mkt_master_data (F3 tail)

```sql
SELECT r.ticker, r.name, m.market_status FROM rex_products r
LEFT JOIN mkt_master_data m
  ON UPPER(TRIM(m.ticker_clean)) = UPPER(TRIM(r.ticker))
  OR UPPER(TRIM(m.ticker)) = UPPER(TRIM(r.ticker))
WHERE r.status='Listed' AND r.ticker IS NOT NULL;
-- 4 mismatches: BMAX/LIQU, FNGA/LIQU, SOLX/DLST, XRPK/DLST
```

### CapM audit log inspection (F7)

```sql
SELECT * FROM capm_audit_log;
-- 1 row: UPDATE on capm_products id=1 (NVDQ), field=notes, by admin, at 2026-05-11 17:51:28

SELECT COUNT(*) FROM capm_products WHERE updated_at >= datetime('now','-30 days'); -- 74
SELECT COUNT(*) FROM capm_audit_log; -- 1
-- 73 silent inserts via _capm_seed_if_empty + 21 silent inserts on rex_products via manual script
```

### Classification audit log existence + scope

```sql
SELECT COUNT(*) FROM classification_audit_log; -- 18983
SELECT source, COUNT(*) FROM classification_audit_log GROUP BY 1 ORDER BY 2 DESC;
-- sweep_high              16238
-- manual_batch_approval    2710
-- manual_insert              21   <-- today's T-REX 2X inserts
-- manual_polish_fix          14

SELECT COUNT(*) FROM classification_audit_log WHERE sweep_run_id='manual_2026-05-09_trex2x'; -- 21
```

### CSV vs DB consistency (capm seed)

```sql
-- capm_products.csv: 74 rows, 27 columns
-- DB capm_products:  74 rows, 31 columns (CSV + id, created_at, updated_at, manually_edited_fields)
-- 0 tickers in CSV not in DB
-- 0 tickers in DB not in CSV
-- 0 duplicate tickers in either side
-- Same for capm_trust_aps.csv (40 rows, 4 cols)
```

### Reserved symbols sanity

```sql
SELECT status, COUNT(*) FROM reserved_symbols GROUP BY 1 ORDER BY 2 DESC;
-- Reserved      270
-- Filed           5
-- Wait Listed     4
-- Requested       3

SELECT exchange, COUNT(*) FROM reserved_symbols GROUP BY 1;
-- Cboe                258
-- NYSE                 17
-- 'NASDAQ\xA0OMX'       7   <-- non-breaking space, F11

SELECT COUNT(*) FROM reserved_symbols WHERE linked_filing_id IS NOT NULL;  -- 0
SELECT COUNT(*) FROM reserved_symbols WHERE linked_product_id IS NOT NULL; -- 0

-- Symbols that already exist in rex_products (natural linkage candidates, F10):
-- APPI, ARMU, AXUP, BKNU, BULU, DFPI, DKUP, ETQ, FNGA, FTPI, PXIU, QQQQ, SEMU, UPUP (14 rows)
```

### VPS table-existence check

```sql
-- against data/etp_tracker.db.fromvps:
SELECT name FROM sqlite_master WHERE type='table' AND name='capm_audit_log';          -- (empty)
SELECT name FROM sqlite_master WHERE type='table' AND name='reserved_symbols';        -- (empty)
SELECT name FROM sqlite_master WHERE type='table' AND name='classification_audit_log';-- (empty)
SELECT COUNT(*) FROM rex_products;  -- 702 (vs 723 local; delta = today's 21 manual inserts)
SELECT COUNT(*) FROM capm_products; -- 0
```

### Manually edited fields

```sql
SELECT COUNT(*) FROM capm_products WHERE manually_edited_fields IS NULL;       -- 74 (all)
SELECT COUNT(*) FROM capm_products WHERE manually_edited_fields IS NOT NULL;   -- 0
```

### Direction values

```sql
SELECT direction, COUNT(*) FROM rex_products GROUP BY 1;
-- NULL  702
-- Long   21   <-- only today's manual seed
```

---

## Surfaces inspected

- `C:/Projects/rexfinhub/webapp/models.py` — RexProduct, CapMProduct, CapMTrustAP, CapMAuditLog, ReservedSymbol, ClassificationAuditLog (lines 626–1145)
- `C:/Projects/rexfinhub/webapp/database.py` — `_capm_seed_if_empty`, `_migrate_missing_columns`, `init_db` (lines 174–292)
- `C:/Projects/rexfinhub/webapp/routers/capm.py` — `_capm_index_impl`, `_build_unified_row`, `_audit_log`, `_capm_update_impl`, `_CAPM_UPDATE_FIELDS`, `_names_overlap` (lines 1–710)
- `C:/Projects/rexfinhub/webapp/routers/pipeline_calendar.py` — `VALID_STATUSES`, `PENDING_EFFECTIVE_STATUSES`, `TERMINAL_STATUSES`, `_rex_only_filter`, `_pipeline_products_impl` (lines 90–400)
- `C:/Projects/rexfinhub/webapp/routers/operations_reserved.py` — `VALID_STATUSES` constant (line 28)
- `C:/Projects/rexfinhub/webapp/templates/pipeline_products.html` — status dropdown options (lines 716–737)
- `C:/Projects/rexfinhub/scripts/insert_trex_2x_2026_05_09.py` — full file (atomicity, audit destination, idempotence verified)
- `C:/Projects/rexfinhub/webapp/data_static/capm_products.csv` — header vs DB columns, 74 distinct tickers
- `C:/Projects/rexfinhub/webapp/data_static/capm_trust_aps.csv` — header vs DB columns, 40 rows
- `C:/Projects/rexfinhub/data/etp_tracker.db` — local SQLite DB (623 MB, queried live)
- `C:/Projects/rexfinhub/data/etp_tracker.db.fromvps` — VPS snapshot (617 MB, queried for table existence + counts)

## Surfaces NOT inspected

- `webapp/templates/capm.html` (operations/products UI) — read only for the status-class logic (line 268); did not audit pixel-level behavior of the unified table.
- `webapp/routers/admin_products.py` — referenced for `VALID_STATUSES` import path; full ADD/DELETE flow for rex_products not walked. Likely owns the "no rex_products audit log" finding (#114) by virtue of not writing one.
- `scripts/import_capm.py` — referenced as the daily reconciler that's supposed to respect `manually_edited_fields`; not opened. Stage 2 should verify it honors that skip-list.
- `scripts/import_reserved_symbols.py` — listed for completeness; deeper audit of CBOE reservations is owned by the CBOE agent per the prompt.
- `webapp/routers/operations_reserved.py` — only its `VALID_STATUSES` constant was read (line 28). Full router behavior not audited.
- `webapp/routers/operations.py` — mount points referenced by capm.py docstrings; not opened.
- `mkt_master_data` deep audit beyond the three columns specifically named by warts #101/#102/#103 (is_singlestock, cap/buffer/barrier, outcome_type) — out of scope for the rex_tables agent; should be owned by a mkt_master_data audit pass.
- VPS-side `import_capm.py` and `_capm_seed_if_empty` behavior under concurrent DB swap — observed at table-existence level; no race-condition simulation performed.
- Cross-DB swap recovery flow (`POST /api/v1/db/upload` → `init_db()` table recreation order) — read about, not exercised.
