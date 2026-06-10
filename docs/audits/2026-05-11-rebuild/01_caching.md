# Stage 1 Audit — Caching Layers

Generated: 2026-05-11T19:00-04:00 ET
Agent: caching
Scope: READ-ONLY. No cache invalidations. No deletions. No file rewrites outside this report.

## Summary

There are **eleven distinct caches** spanning four storage tiers (in-memory, file system, SQLite, Render persistent disk). The overall picture is healthier than expected — the SEC HTTP cache has an LRU pruner, the LLM `filing_analyses` table costs are negligible (~$0.80 since launch), the prebaked reports + `mkt_report_cache` were rebuilt today (2026-05-11 16:18 VPS / 22:16 UTC) and ride together with the daily DB upload, and the screener cache has explicit invalidation hooks tied to pipeline runs. **Atlas memory is wrong on three points**: (1) http_cache is no longer ~13 GB — it's 615 MB locally + 5.2 GB on VPS; (2) `D:/sec-data/cache/rexfinhub/` does not exist on this machine — the project writes to `C:/Projects/rexfinhub/cache/` because `D:` is not mounted; and (3) FilingAnalysis cache is NOT keyed on `(filing_id, model)` — it's keyed on `filing_id` alone with a UNIQUE constraint, so an Opus → Sonnet upgrade would silently serve old Sonnet text forever.

The single most concerning finding is **a 27 GB orphan**: `C:/Projects/rexfinhub/temp_cache/` (23,447 files dated 2026-03-17, untouched since) is the abandoned remnant of a one-time copy from the old C: drive layout, with no code path that reads or writes it. It's a free 27 GB recovery on a machine with 4 GB free on C:. The second concern is **schema drift in the LLM cache**: the writer model column is unfilled-as-key, so Sonnet 4.5 → 4.6 → 4.7 transitions will not trigger re-analysis. The third is the **SEC HTTP cache has no TTL**: a 485BPOS amendment to a previously cached filing will return the original prospectus body forever (mitigated only by submissions-JSON 6-hour refresh + If-Modified-Since, which catches the *list* of filings but never the *body*).

The Render `mkt_report_cache` staleness check (line 542 of `report_data.py`) compares `pipeline_run_id` to the latest run, but the `screener_3x` row was written with `pipeline_run_id = None` — so that staleness check is a no-op for screener data and the cache is "fresh forever" until explicitly invalidated.

## Cache inventory

| Cache name | Location | Size | TTL | Eviction policy | Last refreshed |
|---|---|---|---|---|---|
| **SEC http (`cache/web`)** | `C:/Projects/rexfinhub/cache/web` (local) + `/home/jarvis/rexfinhub/cache/web` (VPS) | 238 MB local / 5.0 GB VPS (4,456 files) | None | LRU pruner if >5 GB (`SEC_CACHE_MAX_MB`), 1h prune cooldown | 2026-05-11 18:26 (local), 16:04 (VPS, last_prune marker) |
| **SEC submissions JSON** | `cache/submissions/CIK*.json` | 64 MB local / 497 MB VPS (449 files local, 16,399 VPS) | 6h conditional (If-Modified-Since) | Never | 2026-05-11 18:24 |
| **SEC iXBRL/sec subdir** | `cache/sec/web` + `cache/sec/submissions` | 615 MB | Same as parent | Same LRU | 2026-05-11 18:18 |
| **temp_cache (ORPHAN)** | `C:/Projects/rexfinhub/temp_cache` | **27 GB** (628 MB submissions, 26 GB web, 23,447 files) | None | None | 2026-03-17 (untouched 55 days — dead) |
| **screener_3x in-memory** | `webapp/services/screener_3x_cache.py` `_cache: dict` | ~550 KB serialized | None | Explicit `invalidate_cache()` on pipeline run | 2026-05-11 22:18 (DB row) |
| **mkt_report_cache (DB)** | `etp_tracker.db` table | 1.1 MB total (li 174 KB, cc 292 KB, flow 90 KB, screener 551 KB) | None | Recompute when `pipeline_run_id < latest` (broken for screener, see findings) | 2026-05-11 22:16 (li/cc/flow), 22:18 (screener) |
| **filing_analyses (LLM)** | `etp_tracker.db` table | 30 rows / ~$0.80 lifetime cost | None | Never (canonical-set semantics) | 2026-05-01 23:42 (last write) |
| **market_data in-memory DataFrames** | `webapp/services/market_data.py` `_master_df` + `_ts_df` | 7,361 master rows + 272K ts rows | None ("Cache lives until explicitly invalidated") | Explicit on pipeline run | startup |
| **report_data in-memory** | `webapp/services/report_data.py` `_cache: dict` | varies | None | Explicit `invalidate_cache()` | startup |
| **holdings_intel in-memory** | `webapp/services/holdings_intel.py` `_cache` | per-key | **600s TTL** | Time-based | per-request |
| **data_freshness in-memory** | `webapp/services/data_freshness.py` `_cache` | small dict | **60s TTL** | Time-based | per-request |
| **prebaked reports (HTML)** | `data/prebaked_reports/*.html` (local + Render persistent disk) | 2.0 MB local / 2.3 MB VPS (9 files) | None | Overwritten on next prebake run | local 2026-04-14 (28 days stale!), VPS 2026-05-11 16:16-16:19 |
| **bulk_sync ETag** | `cache/sec/bulk_sync_etag.txt` + `bulk_sync_company.idx` | small | ETag-validated | Replaced on 200 OK | not present locally |
| **edgar_sponsor_cache.json** | `data/edgar_sponsor_cache.json` | 366 B | "30d old" comment | Refreshed if >30d | 2026-05-05 23:52 |
| **bbg_file local mirror** | `data/DASHBOARD/bloomberg_daily_file.xlsm` | 5 MB | Graph API freshness check | Re-downloaded if SharePoint newer | per-fetch |
| **autocall_sweep_cache (DB)** | `etp_tracker.db` table | 0 rows (empty) | None | Wiped on admin "reload index data" | never used |
| **screener cache.json** | `data/SCREENER/cache.json` | 105 KB | None | Overwritten on `/admin/upload/screener-cache` | 2026-03-05 (stale, but fallback only when DB miss on Render) |
| **discovered_trusts.json** | `data/discovered_trusts.json` | 4.9 MB | None | Overwritten by trust discovery script | 2026-05-11 18:22 |

Counted as caches — distinct from primary data stores (`etp_tracker.db`, `13f_holdings.db`, `structured_notes.db`).

## Findings (ordered by severity)

### CRIT-1 — `temp_cache/` is a 27 GB orphan with zero readers
- **What**: `C:/Projects/rexfinhub/temp_cache/web` holds 23,447 files (26 GB) all dated 2026-03-17. `temp_cache/submissions` holds 628 MB. Nothing in the codebase references `temp_cache/`. Only `cache/` is read or written by `sec_client.py`.
- **Why this matters**: This is dead disk on a machine with 4 GB free on C:. It corresponds to the documented "276 GB http_cache being copied to D:" migration in atlas memory — the source side was orphaned after the copy and then `D:` was unmounted.
- **Verification**: `_DEFAULT_CACHE_DIR = SEC_CACHE_DIR or .../cache/sec` (sec_client.py:15). `SEC_CACHE_DIR` env var is not set. No reference to `temp_cache` in any `.py` file (grep returns none).
- **Stage 2 action**: safe to `rm -rf C:/Projects/rexfinhub/temp_cache/` after one final visual inspection; reclaims 27 GB.

### CRIT-2 — FilingAnalysis cache key ignores model upgrades
- **What**: `webapp/models.py:182-184` declares `filing_id: ... unique=True, index=True` — only `filing_id` is the cache key. The `writer_model` and `selector_model` columns are stored but **not part of the unique index**.
- **Why this matters**: `claude_service.py:200` pins `WRITER_MODEL = "claude-sonnet-4-6"`. If Ryu bumps to Sonnet 4.7 (or rolls back), the cache will return Sonnet 4.6 narratives forever for any filing with an existing row. The `run_analysis_for_day()` short-circuit at line 184 (`if len(rendered) >= MAX_PICKS or not uncached`) explicitly documents this as "canonical-set semantics" — once 3 picks exist for a date, no further LLM work happens for that date, ever.
- **Evidence**: 30 rows total, 100% on `claude-sonnet-4-6` writer + `claude-haiku-4-5-20251001` selector. Last write 2026-05-01 23:42. There are 32 uncached new-form filings since 2026-04-23 (53 candidates - 21 cached) — these would only be analyzed if a *fresh* date with no cached entries comes in, OR if the daily picks for an uncached date < 3.
- **Stage 2 action**: either (a) make `(filing_id, writer_model)` the unique key and treat model upgrades as cache invalidation, or (b) add a `force_refresh=True` parameter to `run_analysis_for_day()` and a manual admin endpoint.

### CRIT-3 — `mkt_report_cache` staleness check is a no-op for `screener_3x`
- **What**: `report_data.py:542` returns `None` (force rebuild) when `row.pipeline_run_id < latest_run`. But the screener row in `mkt_report_cache` has `pipeline_run_id = None` (verified by direct query). The Python `<` comparison on `None < int` either raises `TypeError` (caught silently in the `except Exception`) or short-circuits past the staleness check.
- **Why this matters**: Once Render loads the screener cache from DB, it never invalidates based on pipeline freshness. The only refresh path on Render is the explicit `/admin/upload/screener-cache` endpoint, which today is **never called** in production (there is no automation).
- **Evidence**: `mkt_report_cache` rows for `li_report`, `cc_report`, `flow_report` all have `pipeline_run_id = 304`. Only `screener_3x` has `NULL`. The save path in `screener_3x_cache.py:save_to_db` does not pass `pipeline_run_id`.
- **Stage 2 action**: pass `pipeline_run_id` when saving `screener_3x` (one-line fix in `_compute_and_cache_screener` in `market_sync.py`). Also wrap the comparison in a `None`-safe guard in `report_data.py`.

### HIGH-1 — SEC HTTP cache has no TTL — amended filings serve stale forever
- **What**: `sec_client.py:fetch_text` and `fetch_bytes` check `_find_cached(url, ext)` first, return immediately on hit. Body URLs (Archives/edgar/data/.../.txt) have no freshness check, no ETag, no If-Modified-Since (only `load_submissions_json` uses If-Modified-Since, lines 222-228).
- **Why this matters**: SEC permits filers to amend filings (485BPOS-amendments, 497 supplements). The amended body lives at the same URL. Once cached, our code returns the original body indefinitely. For 485BPOS specifically — which feeds iXBRL effective-date extraction — a stale cache could publish a wrong "fund became EFFECTIVE on..." date in the daily digest.
- **Evidence**: `_CACHE_PRUNE_INTERVAL_SEC = 3600` only governs the pruner, not freshness. There is no `If-Modified-Since` for body fetches. SEC HTTP responses do return `Last-Modified` headers we ignore.
- **Mitigation already in place**: the LRU pruner evicts oldest files when total > 5 GB, so very old cached bodies do eventually fall out — but for any filing accessed in the last 5 GB of activity, it's effectively permanent.
- **Stage 2 action**: add a `max_age_hours` parameter to `fetch_text`/`fetch_bytes`; for 485BPOS bodies specifically, refetch if cached file age > 24h.

### HIGH-2 — Local prebaked reports are 28 days stale
- **What**: `C:/Projects/rexfinhub/data/prebaked_reports/*.html` mtime is **2026-04-14 11:42-11:43** for all 9 files. The VPS copies are fresh (2026-05-11 16:16-19), but local devs hitting the local site would see April 14 reports.
- **Why this matters**: Low — `/admin/reports/preview/{key}/raw` serves whatever HTML is on disk with no "stale" indicator (admin_reports.py:99-121). Local development cannot tell, by looking at the page, that the data is 28 days old.
- **Stage 2 action**: add a `baked_at` timestamp banner to the served HTML (the `meta.json` is already written but not displayed). Or run `python scripts/prebake_reports.py --no-upload` locally.

### HIGH-3 — `discovered_trusts.json` is never invalidated
- **What**: `data/discovered_trusts.json` (4.9 MB, written today 18:22) is the output of trust-discovery scans. There is no rotation; the file is overwritten each scan. If a scan partially fails and writes a truncated JSON, downstream loaders could read incomplete data.
- **Stage 2 action**: write to `discovered_trusts.json.tmp` then atomic rename (currently a direct overwrite based on the lack of `.tmp` rename in the writer).

### MED-1 — Atlas memory entry on rexfinhub cache path is wrong
- **What**: Memory says "rexfinhub cache now writes to `D:/sec-data/cache/rexfinhub/` with bucketed subfolders". `D:/` is not mounted on this machine (verified). The actual path is `C:/Projects/rexfinhub/cache/` (default), with bucketed subfolders. The `SEC_CACHE_DIR` env var that would override this is not set.
- **Stage 2 action**: update atlas memory or set `SEC_CACHE_DIR=D:/sec-data/cache/rexfinhub` in `.env` and re-mount D:.

### MED-2 — Atlas memory: "filing analysis ~35s/~$0.09 cold" overstates cost
- **What**: Memory says cold FilingAnalysis is ~$0.09. Actual measured per-row cost from 30 rows is **$0.027 mean** (range $0.009-$0.030), not $0.09.
- **Why**: probably old Sonnet 4.5 estimates pre-prompt optimization, or per-day rather than per-filing.
- **Total lifetime cost: $0.80** across 30 analyses spanning 2026-04-23 → 2026-05-01. At today's cadence (~3 picks/day × $0.027 = $0.08/day, $30/year), this is operationally free even if we run it daily forever.

### MED-3 — submissions JSON cache cannot detect deleted CIKs
- **What**: `load_submissions_json` only refreshes on age >= 6h. If SEC removes a CIK (rare but possible — bad-actor delisting), the cached JSON will be served until its mtime ages out. No 404 detection.
- **Stage 2 action**: low priority. Bound by 6h refresh.

### MED-4 — Two-tier `cache/` (`cache/sec/...` vs `cache/...`) is confusing
- **What**: Local layout has both `cache/web/` (238 MB, 145 files) AND `cache/sec/web/` (551 MB, 4,456 files). The `_DEFAULT_CACHE_DIR` is `cache/sec`, so `cache/sec/web/` is the live cache. `cache/web/` is from a previous layout. `_find_cached()` (line 130) checks "new bucketed path first, then old flat path" — the "old flat path" is the legacy migration target.
- **Stage 2 action**: deletable after verification — but only 240 MB, low priority compared to the 27 GB orphan.

### LOW-1 — `autocall_sweep_cache` table is empty (0 rows)
- **What**: The SHA256-keyed memoization table for autocall distribution sweeps has zero entries. This is fine if no one has run a sweep recently, but worth noting that the autocall page may always be cold.

### LOW-2 — `analysis_results` table is empty (0 rows)
- **What**: A separate LLM analysis cache table exists alongside `filing_analyses`, but is unused. Leftover from an earlier design.

## Cost / disk impact

### LLM cost (FilingAnalysis)
- **Lifetime spend: $0.80** across 30 analyses (2026-04-23 → 2026-05-01).
- **Per-analysis: $0.027 mean** (Sonnet 4.6 writer @ $3/M in + $15/M out, Haiku 4.5 selector ~free).
- **Per-day projected: ~$0.08** (3 picks × $0.027).
- **Per-year projected: ~$30/year** at current cadence.
- **Risk**: model upgrade with key change would re-run all 30 = $0.80 one-time. Negligible.
- **Recommendation**: do not put a cost cap; the spend is meaningless. Do add `(filing_id, writer_model)` to the unique key.

### Disk usage (local C: drive)
| Path | Size | Status |
|---|---|---|
| `cache/` | 916 MB | live, pruned to 5 GB cap |
| `temp_cache/` | **27 GB** | **ORPHAN — recoverable** |
| `data/etp_tracker.db` (+ wal/shm) | 706 MB | live primary store |
| `data/etp_tracker.db.bak*` | 1.97 GB | 2 backup copies |
| `data/13f_holdings.db` | 849 MB | live primary store |
| `data/etp_tracker_deploy.db` | 605 MB | another stale copy (Mar 16) |
| `data/structured_notes.db` | (varies) | shared with structured-notes project |
| `data/prebaked_reports/` | 2 MB | tiny, but 28 days stale |

**Recoverable space without code changes: ~30 GB** (27 GB temp_cache + 1.97 GB DB backups + 605 MB stale deploy DB).

### Disk usage (VPS)
| Path | Size |
|---|---|
| `cache/sec` | 5.2 GB (under 5 GB cap, will trigger prune) |
| `cache/web` | 5.0 GB |
| `cache/submissions` | 497 MB |
| `data/etp_tracker.db` | 654 MB |
| `data/prebaked_reports` | 2.3 MB |

VPS disk discipline is good. The LRU pruner is doing its job (`.last_prune` marker dated 2026-05-11 16:04).

### Render persistent disk
- 1 GB mount at `/opt/render/project/src/data` (per `render.yaml:25-28`).
- Holds: `etp_tracker.db` (post-compaction, ~600 MB), `prebaked_reports/*.html` (~2 MB), screener `cache.json`.
- **Survives deploys**: yes (persistent disk).
- **Lost on deploy**: in-memory caches (`screener_3x`, `market_data`, `report_data`, `holdings_intel`, `data_freshness`) — but `_prewarm_caches()` in `webapp/main.py:166` reloads them synchronously before health endpoint reports OK, so the cold-start window is invisible to users.

## Hunting questions answered

1. **HTTP cache TTL**: None for body fetches. 6h for submissions JSON (with If-Modified-Since). Amended filings → stale forever. **Real risk for 485BPOS effective dates.**
2. **screener_3x_cache pre-warm**: Yes, `_prewarm_caches()` calls `warm_cache(db=db)` synchronously at startup. Render serves from `mkt_report_cache` table; local computes from Excel. No user ever waits for first compute.
3. **FilingAnalysis cache key**: `filing_id` only (UNIQUE constraint). Model upgrades **do NOT trigger re-analysis**. Critical bug for production model bumps.
4. **Prebake**: 9 reports baked nightly by `run_daily.py` step 8.5 (line 950). Served from disk regardless of staleness, no "stale" indicator. Local copies 28 days behind VPS.
5. **ETP performance cache**: `mkt_report_cache` (DB table) + `_cache` dicts in `report_data.py` and `market_data.py`. All invalidated together on `_compute_and_cache_reports` (market_sync.py:639-644).
6. **Render persistent vs ephemeral**: persistent → DB, prebaked HTML, screener cache.json. Ephemeral → all in-memory caches; warmed on startup before `/health` returns OK.
7. **D: drive cache claim**: D: not mounted; project writes to `C:/Projects/rexfinhub/cache/` with bucketed `web/{ab}/` subdirs. Atlas memory is wrong.
8. **LLM cost runaway**: $0.80 lifetime over 30 rows. ~$30/year projected. Non-issue.
9. **Cache coherency on BBG sync**: yes — `_compute_and_cache_reports` calls `report_data.invalidate_cache()`, `market_data.invalidate_cache()`, and `inv_screener()` before writing fresh DB rows. Screener `pipeline_run_id` not propagated, breaking the per-row staleness check (CRIT-3).
10. **HTTP cache validation**: only `load_submissions_json` uses If-Modified-Since (304 → use cache + bump mtime). Body fetches blind-serve from disk.

## Stage 2 priority queue

1. **CRIT-1**: `rm -rf temp_cache/` after visual inspection — frees 27 GB on a 4-GB-free disk.
2. **CRIT-2**: change FilingAnalysis unique key to `(filing_id, writer_model)`. Two-line migration + one-line model schema change.
3. **CRIT-3**: pass `pipeline_run_id` when saving `screener_3x`; add None-safe staleness guard.
4. **HIGH-1**: add `max_age_hours` to `sec_client.fetch_text` for 485BPOS bodies.
5. **HIGH-2**: add `baked_at` banner to `/admin/reports/preview/{key}/raw`.
6. **MED-1**: fix atlas memory note about D: drive cache path.

## Files referenced
- `C:/Projects/rexfinhub/webapp/services/screener_3x_cache.py`
- `C:/Projects/rexfinhub/webapp/services/market_data.py` (lines 414-470)
- `C:/Projects/rexfinhub/webapp/services/market_sync.py` (lines 626-670)
- `C:/Projects/rexfinhub/webapp/services/report_data.py` (lines 524-565)
- `C:/Projects/rexfinhub/webapp/services/data_freshness.py` (lines 27-30, 60s TTL)
- `C:/Projects/rexfinhub/webapp/services/holdings_intel.py` (lines 24-39, 600s TTL)
- `C:/Projects/rexfinhub/webapp/services/bbg_file.py`
- `C:/Projects/rexfinhub/webapp/services/cboe/live.py`
- `C:/Projects/rexfinhub/webapp/main.py` (lines 163-218, prewarm + lifespan)
- `C:/Projects/rexfinhub/webapp/models.py` (lines 173-203 FilingAnalysis, 518-531 MktReportCache, 1316-1326 AutocallSweepCache)
- `C:/Projects/rexfinhub/webapp/routers/admin_reports.py`
- `C:/Projects/rexfinhub/webapp/routers/admin.py` (line 1648 `/upload/screener-cache`)
- `C:/Projects/rexfinhub/etp_tracker/sec_client.py`
- `C:/Projects/rexfinhub/etp_tracker/filing_analysis.py`
- `C:/Projects/rexfinhub/etp_tracker/bulk_sync.py` (ETag handling)
- `C:/Projects/rexfinhub/scripts/prebake_reports.py`
- `C:/Projects/rexfinhub/scripts/run_daily.py` (line 943-971 prebake step)
- `C:/Projects/rexfinhub/scripts/edgar_sponsor_lookup.py` (sponsor cache)
- `C:/Projects/rexfinhub/render.yaml` (persistent disk config)

## Sign-off
Stage 1 read-only complete. No caches were invalidated, no files outside this report were written, no deletions performed. The 27 GB `temp_cache/` orphan is the standout finding.
