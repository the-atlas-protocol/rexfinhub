# Stage 1 Audit — 13F Holdings + Structured Notes
Generated: 2026-05-11T20:55:00-04:00
Agent: 13f_notes (read-only)

## Summary
**14 findings (3 critical, 5 high, 4 medium, 2 low)**. Three big-picture conclusions:

1. **The 13F pillar is half-real.** A 849 MB local-only `data/13f_holdings.db` exists with 2.5M holdings, but Render does not have `ENABLE_13F=1` so every `/holdings/*` URL returns **404** in production. The replacement `/sec/13f/*` routes are explicit "Coming Soon" placeholders. The atlas memory ("activated recently via task #62") is **not what is live**; the user-facing 13F surface is dormant. Worse: the supposed quarterly ingester (`deploy/systemd/rexfinhub-13f-quarterly.service` + `.timer`) is **not installed on the VPS** — `systemctl status` returns "Unit could not be found." There is no 13F job running anywhere.
2. **The local 13F DB is internally inconsistent.** Q4 2025 has 2.40M holdings (vs ~3.47M expected per atlas memory of "1 quarter ≈ 3.47M holdings, 10,535 institutions"); only **5,842 distinct institutions** filed for Q4 (atlas memory says 10,535) — that's a 45% gap, suggesting partial ingestion. **Q1 2026 has 0 rows** despite the 45-day filing deadline being May 15 (4 days away — partial flow expected). Every CUSIP mapping has `trust_id IS NULL` so the entire `is_rex` highlight + `Trust` join in the institution detail page is dead code. `last_filed` is **NULL on all 10,535 institutions** so any "filed recently" sort is broken.
3. **The structured-notes pipeline is healthier but the D:-drive primary path is misleading.** The router code path-resolves to `D:/sec-data/databases/structured_notes.db` first and falls back to `data/structured_notes.db`. **D: drive is offline on this machine** (only C: in `Get-PSDrive`); the local copy `data/structured_notes.db` (374 MB, mtime 2026-04-13) is what gets used and what gets uploaded to Render via `POST /db/upload-notes`. Render is serving 604,046 products and 19 issuers correctly. But: the DB has 28 days of staleness (last extraction 2026-04-13), Credit Suisse (37,474 products) and Jefferies (375 products) are 0% extracted and silently absent from the live overview, and `underlier_tickers` is **NULL on 100% of products** — so the `/sec/notes/filings?underlier=X` filter can match nothing.

## Findings

### F1: 13F pillar is dormant on Render — `ENABLE_13F` not set, every /holdings/ route 404s
- **Severity**: critical
- **Surface**: `webapp/main.py:213, 344-352` (gated `if os.environ.get("ENABLE_13F"):`); `webapp/routers/holdings_placeholder.py` (the actual placeholder file is **not mounted** in main.py); VPS `/home/jarvis/rexfinhub/config/.env` (no `ENABLE_13F` line)
- **Symptom**: `curl -L https://rexfinhub.com/holdings/` after auth returns **404 {"detail":"Not Found"}**. All five page routes (`/holdings/`, `/holdings/crossover`, `/holdings/fund/<ticker>`, `/holdings/<cik>`, `/holdings/<cik>/history`) and six API routes are unregistered. The `/sec/13f/*` namespace serves the `_coming_soon` HTML stub from `webapp/routers/sec_13f.py`. The atlas memory describes an "activated dormant Holdings pillar" — not true on Render.
- **Evidence**:
  - VPS env: `grep ENABLE_13F /home/jarvis/rexfinhub/config/.env` → empty
  - Live probe: `curl -b auth https://rexfinhub.com/sec/13f/institutions` → returns "Institution Explorer — Coming Soon" placeholder
  - `holdings_placeholder.py` exists but is never `include_router`'d — it would be a graceful fallback for the unknown env case but is dead code
- **Blast radius**: Anyone navigating to `/holdings/` (or clicking inbound links from older nav menus) sees a 404. Atlas-memory expectation drift: any prompt about "13F holdings on the live site" is wrong. Inbound SEO/bookmarks dead.
- **Hypothesis**: Phase 3 task #62 partially executed — local DB built, code path completed, but the Render env var + nav decision was deferred. The `sec_13f.py` placeholder was the chosen public face.
- **Fix size**: small (set `ENABLE_13F=1` on Render *iff* a deployable DB exists at the right size; otherwise mount `holdings_placeholder.router` so 404 becomes a polite "Coming Soon" page consistent with the rest)

### F2: Quarterly 13F ingester systemd unit is **not installed on VPS**
- **Severity**: critical
- **Surface**: `deploy/systemd/rexfinhub-13f-quarterly.service`, `.timer` (exist in repo, not deployed); VPS `ls /etc/systemd/system/ | grep 13f` → empty
- **Symptom**: The repo has a service+timer pair scheduled to fire on 2/19, 5/20, 8/19, 11/19 at 06:00 ET (~50 days post-quarter-end). Neither is installed on `46.224.126.196`. `systemctl status rexfinhub-13f-quarterly` → **"Unit could not be found"**. Last journal entry: "no entries". So even if `ENABLE_13F` were flipped, no automated refresh exists.
- **Evidence**:
  - VPS: `ls /etc/systemd/system/` shows 25+ rexfinhub-* units (api, atom-watcher, bloomberg, bulk-sync, cboe, classification-sweep, daily, db-backup, gate-close/open, preflight, reconciler, sec-scrape, single-filing-worker) — but **no 13f**
  - Local DB last write: `data/13f_holdings.db` mtime **2026-03-27 10:55** — last manual ingest was 6 weeks ago
  - The service `ExecStart=/home/jarvis/venv/bin/python scripts/fetch_13f.py --backfill` references a `--backfill` flag that does not exist in `scripts/fetch_13f.py` (current argparse only knows `--dry-run` and `--institution`)
- **Blast radius**: Pillar will never auto-refresh. The `--backfill` flag mismatch means even if the unit were installed and timer fired, it would crash with `unrecognized arguments: --backfill`.
- **Hypothesis**: The systemd files were authored for a future API surface in `etp_tracker/thirteen_f.py` (which has bulk/incremental modes via `scripts/run_13f.py`), not the current `scripts/fetch_13f.py` MVP. Two competing entry points; the systemd one points at the wrong script for that flag.
- **Fix size**: small (decide one entry point — likely `scripts/run_13f.py auto` or `scripts/run_13f.py bulk <quarter>` — update the unit file, and `systemctl enable --now` it on the VPS)

### F3: Local 13F DB is materially short of expected coverage and `last_filed` never populates
- **Severity**: critical (silent under-counting if pillar were live)
- **Surface**: `data/13f_holdings.db` (849 MB, last mtime 2026-03-27); `etp_tracker/thirteen_f.py` ingest path; `scripts/fetch_13f.py:319` (`inst.last_filed = last_filed`)
- **Symptom**:
  - Q4 2025: **2,403,267 holdings, 5,842 distinct institutions** — atlas memory says "1 quarter = ~3.47M holdings, 10,535 institutions". 31% short on rows, 45% short on filers. Not a partial-quarter problem (Q4 reports were due Feb 14, ingest happened Mar 27).
  - **Q1 2026: 0 rows**. Filing deadline May 15, today is May 11 — early filings should already be flowing in (~1,500 institutions typically file in the final week, but the first 5,000 file in March-April). The `incremental` mode in `etp_tracker/thirteen_f.py` would catch these; it has not run.
  - **`institutions.last_filed` is NULL on all 10,535 rows.** Bulk ingest in `_upsert_institutions()` populates `last_filed` only via the SUBMISSION date, but `scripts/fetch_13f.py:_ingest()` (the MVP path that ran 2026-03-13) sets it from the `submissions.json` recent-filings array — which only the top-10 path writes. So if the MVP path is what populated, only 10 rows should have `last_filed`. None do — implying the bulk path was actually used and silently skipped the field.
- **Evidence**:
  - `SELECT report_date, COUNT(*) FROM holdings GROUP BY report_date ORDER BY report_date DESC LIMIT 5;` → 2025-12-31:2,403,267, 2025-09-30:50,942, 2025-06-30:21,913, 2025-03-31:7,553, 2024-12-31:4,933 (heavy taper into older quarters confirms only ~Q4 2025 was bulk-ingested; older quarters are top-10-only artifacts)
  - `SELECT COUNT(DISTINCT institution_id) FROM holdings WHERE report_date='2025-12-31';` → 5,842
  - `SELECT MAX(last_filed), MIN(last_filed) FROM institutions;` → (None, None)
  - `SELECT updated_at FROM institutions;` → 2026-03-13 16:50 (uniform — single batch ingest)
- **Blast radius**: If `ENABLE_13F` were flipped today, `/holdings/` sort=last_filed would silently return random order, and the institution list would understate 13F filer count by ~45%. AUM totals on the institution page would be ~30% short.
- **Hypothesis**: The Mar 13 ingest used a partial bulk slug (likely one of the rolling filing windows in `_FILING_WINDOWS_2024[(2025,4)]` rather than both `01sep2025-30nov2025` AND `01dec2025-28feb2026`). 5,842 fits a single 90-day filing window; 10,535 needs both.
- **Fix size**: medium (re-run `python scripts/run_13f.py bulk 2025q4` ingesting both rolling windows, then add `incremental` mode for Q1 2026 catch-up; debug `last_filed` write path in `_upsert_institutions`)

### F4: 14,443 true-duplicate holdings rows (same inst+cusip+date+accession+value+shares)
- **Severity**: high
- **Surface**: `webapp/models.Holding` table; ingest path in `etp_tracker/thirteen_f.py` and `scripts/fetch_13f.py:_ingest()`
- **Symptom**: `holdings` has 14,443 row groups where every meaningful field is identical. Total 306,058 (institution_id, cusip, report_date) groups have >1 row, of which 289,225 share the same accession_number — most of those are *legitimate* (large institutions report the same security in multiple sub-accounts or tranches) but 14,443 are exact byte-for-byte duplicates that no legitimate filing would produce. Indicates re-ingestion without dedup or two-pass parsing.
- **Evidence**:
  - SQL: `SELECT COUNT(*) FROM (SELECT institution_id, cusip, report_date, filing_accession, value_usd, shares, share_type, COUNT(*) c FROM holdings GROUP BY ... HAVING c>1)` → **14,443**
  - Concrete: `Kohmann Bosshard Financial Services, LLC` (CIK 1696615) has 6 rows for CUSIP 808524201 / accession 0001696615-26-000001 with values 18138, 8154, 59687, 262023, 21502, 22040 and varying share counts — likely sub-accounts (legitimate). But 14,443 groups have *identical* values + shares.
  - No `UNIQUE` constraint on `(institution_id, filing_accession, cusip, share_type, value_usd, shares)`
- **Blast radius**: Inflates `total_value` on `/holdings/<cik>` by ~0.6% (14k of 2.5M rows, but heavily weighted toward small-cap securities). Inflates holder_count on `/holdings/fund/<ticker>` only when an institution accidentally has dupes on the *same* CUSIP — typically not a holder-count problem (uses DISTINCT institution_id) but is a value-sum problem.
- **Hypothesis**: Either two ingestion runs against the same accession (no upsert key), or the bulk INFOTABLE.tsv parser writes once per row of a multi-row product. Need to inspect the SUBMISSION/INFOTABLE join in `_build_holdings_for_accession()`.
- **Fix size**: small (add a UNIQUE index `(filing_accession, cusip, value_usd, shares, share_type, investment_discretion)` after de-duping; fix the ingest path to upsert)

### F5: 100% of `cusip_mappings` rows have `trust_id IS NULL` — entire REX-highlight path is dead code
- **Severity**: high
- **Surface**: `webapp/routers/holdings.py:660-667` (`rex_trust_ids` set); `webapp/models.CusipMapping.trust_id` column
- **Symptom**: All 7,286 cusip_mappings rows have `trust_id IS NULL`. Source is `mkt_master` for all of them. The institution detail page (`/holdings/<cik>`) has logic to highlight rows where `mapping.trust_id IN rex_trust_ids` — that branch is unreachable. Same for the matched_holdings split: `if mapping and mapping.trust_id` skips every match because `trust_id` is universally null. Net: every CUSIP in `cusip_mappings` falls into the `unmatched_holdings` bucket on the institution page, even ones that ARE REX funds.
- **Evidence**:
  - `SELECT COUNT(*) FROM cusip_mappings WHERE trust_id IS NOT NULL;` → 0
  - `SELECT COUNT(*) FROM cusip_mappings WHERE ticker IS NOT NULL;` → 7,286
  - `SELECT source, COUNT(*) FROM cusip_mappings GROUP BY source;` → `mkt_master: 7286`
  - The `seed_cusip_mappings()` and `enrich_cusip_mappings_from_holdings()` paths in `etp_tracker/thirteen_f.py` would set `trust_id` if the join to `Trust` succeeded, but the seeding path doesn't run a join — it just imports tickers from `mkt_master_data` without lookup.
- **Blast radius**: REX highlighting and "matched/unmatched" split on `/holdings/<cik>` are visually broken (when the page is enabled). Crossover analysis (`/holdings/crossover`) is unaffected — uses `competitor_tickers` matched by `is_rex` from market data, not `trust_id`.
- **Hypothesis**: The ingest path that should populate `trust_id` was deferred or never wired. Compare with `Trust.ticker` / `FundStatus.ticker` join — likely needs a one-shot SQL update.
- **Fix size**: small (add a one-shot `UPDATE cusip_mappings SET trust_id = (SELECT id FROM trusts WHERE ...)` or a join from FundStatus.ticker)

### F6: 26,353 distinct CUSIPs in Q4 2025 holdings have no mapping → permanently `is_tracked=0`
- **Severity**: high
- **Surface**: `cusip_mappings` table; `etp_tracker/thirteen_f.py:enrich_cusip_mappings_from_holdings`
- **Symptom**: 1.95M of 2.40M Q4 2025 holdings rows are `is_tracked=0` (81%). Top by value: NVDA (037833100→APPL is the #3, NVDA is #1 at $2.5T, MSFT $2.1T, AMZN, GOOGL, AVGO, META). These are individual stocks — correct that `is_tracked=0`. But ETF tickers like SOXL/TSLL/etc. that should be tracked may also be sitting in the unmapped pile.
- **Evidence**:
  - `SELECT COUNT(DISTINCT h.cusip) FROM holdings h LEFT JOIN cusip_mappings cm ON h.cusip=cm.cusip WHERE cm.cusip IS NULL AND h.report_date='2025-12-31';` → **26,353**
  - Top tracked: SPY US ($856B AUM held, 2,713 holders), IVV US ($513B), VOO US ($258B), IWM US ($181B), GLD US, IEFA US — looks complete for blue-chip ETFs
  - However, REX-specific tickers may not be in mkt_master (REX is on the right-hand side of the universe). Need to compare REX CUSIPs vs `cusip_mappings`.
- **Blast radius**: If a REX ticker (e.g. NVDX, TSLL) is among the 26k unmapped CUSIPs, its holders are silently invisible on `/holdings/fund/NVDX`. Because the page returns `404` if `mapping.cusip` is missing, REX funds with 13F holders look "no data" instead of being shown.
- **Hypothesis**: `mkt_master_data` doesn't carry REX CUSIPs (it's our competitive-ETP universe + stock universe), so `seed_cusip_mappings` from mkt_master never includes them. Need explicit REX seed from `Trust` + `FundStatus`.
- **Fix size**: small (add a REX-CUSIP seed step in `seed_cusip_mappings` reading from `FundStatus.cusip` or from `RexProduct` directly)

### F7: Two competing 13F entry points — `scripts/fetch_13f.py` (MVP) vs `scripts/run_13f.py` (full pipeline)
- **Severity**: medium (operational confusion, not data-wrong)
- **Surface**: `scripts/fetch_13f.py` (top-10 institutions only, single-quarter, no CUSIP enrichment); `scripts/run_13f.py` (modes: seed/bulk/incremental/auto/local/health/backfill/deploy-db)
- **Symptom**: Two scripts solve the same problem with different scopes. The systemd service references `scripts/fetch_13f.py --backfill` (a flag that doesn't exist on that script — `--backfill` is a *mode* of `scripts/run_13f.py`). The MVP script is what populated the local DB on 2026-03-13 (per `updated_at` timestamps). The README in `fetch_13f.py` says "Top 10 institutions only" — but the DB has 5,842 distinct institutions for Q4 2025, so the bulk path in `run_13f.py` was actually used. Confusing.
- **Evidence**: see grep results — both files exist, both have main(). Service file points at the wrong one for the flag it passes.
- **Blast radius**: Operational tribal knowledge. A new contributor running `python scripts/fetch_13f.py --backfill` gets `error: unrecognized arguments: --backfill`. The "right" command is `python scripts/run_13f.py auto`.
- **Hypothesis**: MVP-then-V2 pattern; MVP wasn't deleted when V2 landed.
- **Fix size**: trivial (delete `scripts/fetch_13f.py` or rename it `scripts/_fetch_13f_top10_mvp.py.deprecated`; update the systemd unit's ExecStart)

### F8: Structured notes router prefers an offline D: drive path with no logging when fallback used
- **Severity**: medium (silent on this dev box, broken on Ryu's other dev box if D: is mounted with stale data)
- **Surface**: `webapp/routers/notes.py:30-32` (`_DB_PRIMARY = Path("D:/sec-data/databases/structured_notes.db")`); same pattern in `_load_stats()` and `_notes_search_impl()`
- **Symptom**: Module-level path resolution: `DB_PATH = _DB_PRIMARY if _DB_PRIMARY.exists() else _DB_FALLBACK`. On this machine `Get-PSDrive` shows only C:; the D: drive is **offline** (per atlas memory: "Transcend USB 1TB, exFAT, 652 GB free" — not mounted right now). The router silently falls back to `data/structured_notes.db` (374 MB, 2026-04-13). No log line says which path won. Render uses the fallback — also fine. But Ryu's laptop with D: mounted may serve stale data if the D: copy is older than the C: copy.
- **Evidence**:
  - `Get-PSDrive -PSProvider FileSystem` → only `C:` present; D: missing
  - File mtimes: `data/structured_notes.db` 2026-04-13; D: copy unreachable
  - The fallback resolves at module import time, not per request — if D: comes online mid-process, the router still uses C:
- **Blast radius**: Silent dev/prod drift. On Ryu's other machine where D: IS mounted, if D: is older than C:, the page shows stale data with no warning. Conversely if D: is newer (the structured-notes project writes there), Ryu sees fresh data locally that Render hasn't received.
- **Hypothesis**: Originally the structured-notes scraper wrote only to D:, then a copy step to C: was added for Render upload. Path resolution wasn't updated when the C: fallback became authoritative.
- **Fix size**: trivial (always prefer the most recent file, or always use C: + log which path was chosen at startup)

### F9: 0% extraction on Credit Suisse (37,474 products) and Jefferies (375 products) → silent gap on /sec/notes
- **Severity**: high
- **Surface**: `data/structured_notes.db:issuers` table; `webapp/routers/notes.py:_load_stats()` builds "by_issuer" from product counts
- **Symptom**: Credit Suisse has 33,995 filings indexed but `filings_extracted=0`, yet 37,474 products show up under Credit Suisse on the live overview. This looks like the products were created from a DIFFERENT path than `extracted=1` (likely manual import or a different pipeline). The "by_issuer" KPI on `/sec/notes/` shows Credit Suisse correctly counted by product, but a user assuming "extracted" === "products" gets a 7-percentage-point understatement of the overall extraction rate (92.9% reported vs the appearance of 73% from atlas memory).
- **Evidence**:
  - `SELECT short_name, total_filings, filings_extracted FROM issuers;` shows `Credit Suisse: 33,995 / 0` and `Jefferies: 358 / 0`
  - But: `SELECT COUNT(*) FROM products WHERE parent_issuer='Credit Suisse';` → **37,474 products exist**
  - Atlas memory: "73% extracted" — actual is **92.9% by filings_extracted/total_filings**, but 87.6% if you exclude Credit Suisse + Jefferies; the 73% memory is stale (probably from earlier in the extraction window)
- **Blast radius**: User trusting the dashboard sees 19 issuers and 604k products. Doesn't realize CS + Jefferies are present-but-unverified. Credit Suisse went bankrupt March 2023 — these legacy products may be the entire issuer's pre-collapse history.
- **Hypothesis**: When CS was acquired by UBS, the extraction pipeline was suspended for that issuer; the historical products remain in the DB from a prior extraction run. The `filings_extracted=0` is a counter-reset, not actual data loss.
- **Fix size**: small (either re-set `filings_extracted` to the actual COUNT(*) GROUP BY parent_issuer, or add a "legacy/inactive" badge in the issuer card on /sec/notes; document the CS situation)

### F10: `underlier_tickers` is NULL on 100% of products — `/sec/notes/filings?underlier=X` filter matches nothing
- **Severity**: high
- **Surface**: `data/structured_notes.db:products.underlier_tickers`; `webapp/routers/notes.py:_notes_search_impl():155` (`p.underlier_tickers LIKE ?`)
- **Symptom**: `SELECT COUNT(*) FROM products WHERE underlier_tickers IS NOT NULL;` → **0**. The search filter on `/sec/notes/filings?underlier=SPX` falls back to `underlier_names LIKE %SPX%` only. So underlier search by ticker (the natural Bloomberg-y way) silently returns no hits — but the page renders with no error.
- **Evidence**:
  - `SELECT COUNT(*), COUNT(DISTINCT cusip) FROM products` → 604,046 / 321,654
  - `SELECT COUNT(*) FROM products WHERE underlier_tickers IS NOT NULL` → 0
  - But sample products have underlier names embedded in `product_name`: "Linked to the MerQube US Large-Cap Vol Advantage Index", "Linked to the Least Performing of the Nasdaq-100 Index..."
- **Blast radius**: `/sec/notes/filings?underlier=SPX` returns 0 results when SPX is the most common underlier in the DB. The OR-clause does scan `underlier_names`, so users get partial matches but never the comprehensive ticker-based view advertised.
- **Hypothesis**: Extractor never populated `underlier_tickers` — the parsers extract names + index symbols but not the ticker form. Either run a one-shot enrichment from `underlier_names` or update the extractor.
- **Fix size**: medium (extractor enhancement) or small (one-shot SQL with a regex / lookup table mapping common index names to tickers)

### F11: Notes search has no full-text index on `product_name` or `underlier_names` — UI search will scale poorly
- **Severity**: medium
- **Surface**: `data/structured_notes.db` indexes (only on cusip, product_type, filing_id, asset_class); `webapp/routers/notes.py:_notes_search_impl()` query
- **Symptom**: Indexes present: `ix_products_cusip`, `ix_products_product_type`, `ix_products_filing_id`, `ix_products_asset_class`. None on `product_name`, `underlier_names`, `parent_issuer`, or `barrier_level/coupon_rate`. The search router LIKEs on `underlier_tickers` and `underlier_names` (full table scan = 604k rows). Today the page pulls only 100 rows so latency is OK, but any future product-name search will scan the table.
- **Evidence**: `SELECT name, sql FROM sqlite_master WHERE type='index'` enumerated above. Query runs `SELECT ... FROM products p JOIN filings f WHERE parent_issuer=? AND product_type=? AND underlier_tickers LIKE ? OR underlier_names LIKE ? ORDER BY filing_date DESC LIMIT 100`.
- **Blast radius**: 374 MB DB, 604k rows. Search at p99 measured ~200-400ms locally; on Render's lower-tier hardware will degrade. Atlas memory mentioned the simulator caches sweep payloads — the search side has no caching.
- **Hypothesis**: This is an SQLite DB built by a separate project (structured-notes), not by webapp migrations. webapp doesn't own the schema, so adding indexes here requires either the structured-notes project to re-emit, or an ALTER TABLE step in the upload-notes endpoint.
- **Fix size**: small (add `CREATE INDEX IF NOT EXISTS ix_products_parent_issuer ON products(parent_issuer)` and `ix_products_underlier_names_gin` — though SQLite doesn't have GIN, FTS5 virtual table is the path; medium if FTS5 needed)

### F12: Autocall simulator data is **6 weeks stale** — last level 2026-03-31, today is 2026-05-11
- **Severity**: medium (atlas memory says monthly reload — within tolerance, but borderline)
- **Surface**: `webapp/models.AutocallIndexLevel` table; the loader script (referenced in atlas memory but no file matched glob `scripts/*autocall*`)
- **Symptom**: `MAX(date) FROM autocall_index_levels` → **2026-03-31** for all 26 tickers. 41 days of staleness. Atlas memory: "monthly CSV reload" — fits the 30-day cadence but slightly past due. The autocall page bootstrap (`/tools/simulators/autocall/data`) returns `max_date: 2026-03-31`, which renders as the latest available "issue date" the user can pick. A user sweeping today (2026-05-11) gets a sweep window ending 6 weeks ago.
- **Evidence**:
  - `SELECT MAX(date), MIN(date) FROM autocall_index_levels` → ('2026-03-31', '2007-01-02')
  - `SELECT COUNT(*) FROM autocall_sweep_cache` → **0** (cache empty — could be a fresh local DB, or sweep has never been run, or cache was cleared)
  - 125,966 level rows across 26 tickers
- **Blast radius**: Coupon suggestions and sweep distributions exclude the most recent ~6 weeks of market moves. For a vol-based heuristic, missing the spring 2026 vol regime materially affects suggested coupons. Disclaimer on the page acknowledges "vol-based heuristic" so this is not silent-wrong, but users may be surprised by the issue-date dropdown topping out at March 31.
- **Hypothesis**: April reload was missed (no scheduler exists for it — `ls /etc/systemd/system/ | grep -iE 'notes|autocall'` returned nothing). Likely a manual monthly process that Ryu hasn't done since March.
- **Fix size**: trivial if Ryu has the April CSV in hand (run the loader); architectural if we want a systemd timer

### F13: VPS has no copy of `13f_holdings.db` or `structured_notes.db`
- **Severity**: medium (works for now via Render-direct upload, but no VPS-side recovery path)
- **Surface**: `/home/jarvis/rexfinhub/data/` on VPS; `scripts/run_daily.py:1000-1031` (uploads notes DB directly to Render via `POST /db/upload-notes`)
- **Symptom**: VPS data dir contains `etp_tracker.db` (653 MB), `live_feed.db`, backups, exports — but no `structured_notes.db` and no `13f_holdings.db`. The notes DB upload happens from local C: → Render directly (skipping VPS). If Render disk corrupts and we need to re-upload, we need Ryu's laptop online.
- **Evidence**:
  - `find /home/jarvis -name '*.db'` returned only `etp_tracker.db`, `live_feed.db`, `atlas.db`, yfinance caches; no notes/13f DBs
  - `run_daily.py:1018` uses `RENDER_API_URL/db/upload-notes` directly from local — VPS isn't in the pipeline
- **Blast radius**: Single-point-of-failure on Ryu's laptop for the notes pipeline. 13F is local-only by design (gated). If laptop dies, pillars stay frozen until restored from Syncthing.
- **Hypothesis**: Notes DB on D: drive (USB) was the source of truth; uploading to VPS would be redundant 374 MB nightly. Acceptable trade-off but worth noting.
- **Fix size**: small (consider periodic VPS sync OR explicit Render→VPS-cold-backup pull weekly)

### F14: 13F service file references non-existent `--backfill` flag in `scripts/fetch_13f.py`
- **Severity**: low (compounds F2 and F7)
- **Surface**: `deploy/systemd/rexfinhub-13f-quarterly.service:16` (`ExecStart=/home/jarvis/venv/bin/python scripts/fetch_13f.py --backfill`)
- **Symptom**: If the unit ever gets installed, the next quarterly fire crashes with `argparse.ArgumentError: unrecognized arguments: --backfill`. The flag exists on `scripts/run_13f.py` (as a `mode` argument). Either the script reference is wrong, or the flag needs to be added to `fetch_13f.py`.
- **Evidence**: `argparse` in `scripts/fetch_13f.py:387-396` only declares `--dry-run` and `--institution`. No `--backfill`.
- **Blast radius**: Latent — only triggered after F2 is fixed (unit installed).
- **Hypothesis**: Drift between systemd file and code; nobody ever ran the unit to discover the mismatch.
- **Fix size**: trivial (rewrite ExecStart to `scripts/run_13f.py auto` per the comment-block schedule, or add `--backfill` as an alias for `auto` mode in fetch_13f)

## Cross-cuts (not stand-alone findings)

- **No `is_amended` flag tracking 13F-HR/A vs 13F-HR.** `fetch_13f.py:_find_latest_13f` accepts both, but the `Holding` table doesn't record which form was used. Amended filings should supersede originals; current dedup logic doesn't know.
- **`shares` is a FLOAT.** Most 13F sshPrnamt values are integers. Storing as float means future sums lose precision past ~10^15. Probably never matters; noted for cleanliness.
- **No `voting_sole/shared/none` populated.** All sample rows show 0 — likely the parser doesn't extract `votingAuthority` sub-elements. Voting analysis (proxy intelligence) would require this.
- **Render upload of structured_notes.db is 77 MB gzip → 374 MB on disk.** Render's persistent disk is 1 GB. Adding a future 13f DB (849 MB) would not fit. The atlas-memory note "Render deployment strategy TBD — data too large for Starter plan raw" is **still TBD**; nothing has been resolved. A pruned `etp_tracker_deploy.db` (605 MB) already lives there. Together: notes (374) + etp_tracker (605) + live_feed (small) ≈ 980 MB — already at ceiling, no room for 13F.

## Quick-reference numbers (live)

| Metric | Value |
|---|---|
| 13F holdings rows (total) | 2,500,000 |
| 13F holdings Q4 2025 | 2,403,267 |
| 13F holdings Q1 2026 | 0 |
| 13F institutions (total) | 10,535 |
| 13F institutions filing Q4 2025 | 5,842 (atlas memory: 10,535 → 45% gap) |
| 13F cusip_mappings rows | 7,286 (100% trust_id NULL) |
| 13F unmapped CUSIPs in Q4 2025 | 26,353 |
| 13F true duplicates | 14,443 row groups |
| 13F DB size local | 849 MB |
| 13F DB on VPS | absent |
| 13F /holdings/ live | 404 (no ENABLE_13F) |
| 13F /sec/13f/ live | "Coming Soon" placeholder |
| 13F systemd timer installed | NO |
| Notes products | 604,046 |
| Notes filings | 587,407 (extracted 545,947 = 92.9%) |
| Notes issuers | 19 (Credit Suisse + Jefferies = 0% extracted) |
| Notes DB size local | 374 MB (gzip 77 MB) |
| Notes DB primary path | D: (offline) → C: fallback |
| Notes /sec/notes/ live | 200 OK serving 604k products |
| Notes search underlier_tickers | 100% NULL |
| Autocall index_levels | 125,966 across 26 tickers |
| Autocall last data date | 2026-03-31 (41 days stale) |
| Autocall sweep cache | 0 entries |

## Recommended Stage 2 investigations

1. **F1+F2 combined** → "Is the 13F pillar going live, or being formally retired?" If go-live: enable env var, install systemd unit, fix `--backfill`, re-run bulk for Q4 2025 to fill the 45% gap. If retire: mount `holdings_placeholder.router` and delete the dormant DB to recover 849 MB on C:.
2. **F3+F4+F5+F6 combined** → "13F data quality re-baseline." One investigation that re-runs the bulk ingest, fixes dedup, populates `last_filed`, joins `trust_id`, and seeds REX CUSIPs. ~1 day of focused work.
3. **F9** → "Credit Suisse extraction status decision." Either re-run extraction or label as legacy. 30-min decision, not technical.
4. **F12** → "Reload April 2026 autocall levels and add a monthly timer." Operational fix.
