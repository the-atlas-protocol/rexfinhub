---
doc: glossary
status: canonical
updated: 2026-05-19
---

# rexfinhub — Glossary

> Single source of canonical names. Every term used with semantic weight in any other doc MUST have an entry here. Cross-reference with `[[term-name]]` syntax.
>
> Entry shape (rigid — uniform chunking for retrieval):
> ```
> ### term-name
> **Definition**: ...
> **Where it lives**: db.table, code.path
> **Synonyms**: ...
> **Not to be confused with**: [[other-term]]
> **Status**: canonical | deprecated | proposed
> ```

---

### auto-go

**Definition**: Logic in `scripts/preflight_check.py` that writes `data/.preflight_decision.json` with `action=GO` when the 18:30 preflight returns `pass` (or `warn` with the `.autogo_on_warn` flag present). Replaces the prior manual GO-click on `/admin/reports/dashboard`.
**Where it lives**: `scripts/preflight_check.py` (PR #16), `data/.preflight_decision.json`.
**Synonyms**: auto-decision, auto-approve.
**Not to be confused with**: [[gate]] (gate-open is a separate timer at 19:00 that flips `.send_enabled` to true).
**Status**: canonical.

### bloomberg-pull

**Definition**: Automated retrieval of `bloomberg_daily_file.xlsm` from M365 SharePoint via Microsoft Graph API, twice daily (17:15 + 21:00 ET) on weekdays. Driven by `webapp/services/graph_files.py::download_bloomberg_from_sharepoint()` called from the `rexfinhub-bloomberg-chain.service`.
**Where it lives**: `webapp/services/graph_files.py`, systemd unit `rexfinhub-bloomberg.timer`.
**Synonyms**: bloomberg sync, sharepoint pull.
**Not to be confused with**: legacy Syncthing-from-laptop flow (deprecated; no longer in use).
**Status**: canonical.

### canonical-product-id

**Definition**: Synthetic UUID primary key for a REX product. Stable across ticker recycling, fund-name changes, and identifier rotations. Backed by `product_master.canonical_id` in the target architecture.
**Where it lives**: `product_master` table (proposed — see [TARGET.md#data-model](TARGET.md#data-model)).
**Synonyms**: product UUID, canonical ID.
**Not to be confused with**: [[rex-product]] row PK (currently `rex_products.id` integer).
**Status**: proposed (Phase 4 of the rebuild roadmap).

### capm-product

**Definition**: Row in the legacy `capm_products` table. Stores curated Capital Markets fields (fees, custodian, LMM, AP, BB ticker, prospectus link) for 74 REX products. Originally created before `rex_products` unified.
**Where it lives**: `capm_products` table; admin edit UI at `/admin/rex-products`.
**Synonyms**: capm row, curated product row.
**Not to be confused with**: [[rex-product]] (which covers all 552 REX products, but lacks the Capital Markets fields).
**Status**: deprecated — Phase 3 of rebuild merges these columns into `rex_products` and drops this table. See `DECISIONS/0002-merge-capm-and-rex-products.md` (proposed).

### cboe-cookie

**Definition**: 32-character lowercase-alphanumeric session token Bloomberg-style required to authenticate against the CBOE issuer portal for the nightly symbol-reservation scrape. Stored in `config/.env` as `CBOE_SESSION_COOKIE` on the VPS. Lifetime ~30-60 days.
**Where it lives**: `config/.env` on VPS; consumed by `webapp/services/cboe/*` and `scripts/run_cboe_scan.py`.
**Synonyms**: CBOE session, sessionid.
**Not to be confused with**: any other API key (CBOE is browser-session-based, not API-key-based).
**Status**: canonical. Rotation flow: paste 32-char token via `/cboe-cookie` skill (today) or `/admin/cboe-cookie` page (proposed Phase 2).

### effective-date

**Definition**: The SEC-stamped date on a 485BPOS filing when the registration becomes effective. Does NOT mean the fund is trading — only that it's legally allowed to. Distinct from [[inception-date]] (first trade) and from listing on an exchange.
**Where it lives**: `fund_status.effective_date`, `mkt_master_data.inception_date` (when Bloomberg copies it).
**Synonyms**: SEC effective date.
**Not to be confused with**: [[inception-date]], "Listed" status.
**Status**: canonical.

### etp-category

**Definition**: REX's proprietary taxonomy assigning each ETP to one of LI / CC / Crypto / Defined / Thematic (or NULL if out-of-scope). Populated by `market/auto_classify.py` + `config/rules/fund_mapping.csv` overrides; planned to migrate to `classification_override` table.
**Where it lives**: `mkt_master_data.etp_category`.
**Synonyms**: REX taxonomy category, fund category.
**Not to be confused with**: Bloomberg's own classification fields (which we ignore).
**Status**: canonical (today). Migration target: replace CSV-based overrides with `classification_override` table — Phase 6.

### fresh-poller

**Definition**: SEC EDGAR atom-feed watcher running every 15 minutes during market hours. Detects new 485-series filings within 1-3 minutes of SEC acceptance and inserts to `filing_alerts`. Auto-creates `trusts` rows for previously unknown CIKs.
**Where it lives**: `etp_tracker/atom_watcher.py`, `scripts/poll_fresh_filings.py`, systemd `rexfinhub-fresh-poller.timer`.
**Synonyms**: atom watcher, fresh-filings poller, edgar watcher.
**Not to be confused with**: the 4×/day batch scrape (`rexfinhub-sec-scrape.timer`), which is the slower fallback.
**Status**: canonical.

### gate

**Definition**: The file `data/.send_enabled` whose contents ("true" / "false") determine whether `send_all.py` may fire emails. Auto-opens at 19:00 ET, auto-closes at 20:00 ET via systemd timers. A try/finally guarantee ensures the gate locks after every send batch.
**Where it lives**: `data/.send_enabled`, opened/closed by `rexfinhub-gate-open.timer` + `rexfinhub-gate-close.timer`.
**Synonyms**: send gate, send-enabled flag.
**Not to be confused with**: [[auto-go]] (the decision-file mechanism that gates `send_all --use-decision` independent of the file gate).
**Status**: canonical.

### inception-date

**Definition**: First trading day of a fund (when the first NAV strikes and first trade prints). Distinct from [[effective-date]] (which is regulatory) and from filing date. Authoritative source: Bloomberg's `inception_date` field once `market_status=ACTV`, validated by observation of first trade.
**Where it lives**: `mkt_master_data.inception_date`, `rex_products.official_listed_date` (sometimes).
**Synonyms**: launch date, first-trade date.
**Not to be confused with**: [[effective-date]] (SEC), [[target-inception-date]] (planned launch, set by Ryu on `/operations/pipeline`).
**Status**: canonical.

### target-inception-date

**Definition**: Ryu's planned/expected launch date for a REX product, entered manually on `/operations/pipeline` when the product is in `Under Consideration` / `Target List` / `Filed` / `Effective` status. The data column is `rex_products.target_listing_date` — the schema and user vocabulary diverge here for historical reasons; ADR 0004 canonized that "target inception date" (user) = `target_listing_date` (column).
**Where it lives**: `rex_products.target_listing_date`. Edited via the **Target Inception** column on `/operations/pipeline` (yellow cells for non-Listed rows; click to edit when admin). Saves go through `POST /admin/rex-products/update/{id}`, which writes the value AND appends `target_listing_date` to `manually_edited_fields` so the daily Bloomberg-chain sweep skips this column on this row.
**Synonyms**: target inception, planned launch date, expected inception.
**Not to be confused with**: [[inception-date]] (actual first trading day, set after the fact), [[effective-date]] (SEC regulatory), `initial_filing_date` (when the 485APOS landed).
**Status**: canonical (label); the schema column will fold into the future `status_history` bi-temporal table at Phase 5.

### is-rex-flag

**Definition**: Boolean column `mkt_master_data.is_rex` indicating whether a product is REX-branded (including BMO MicroSectors which REX brand-licenses). Populated by classification pipeline + `config/rules/rex_funds.csv` override (today).
**Where it lives**: `mkt_master_data.is_rex` column.
**Synonyms**: REX flag.
**Not to be confused with**: the `rex_products` table membership (which is broader — includes pre-launch filings that aren't yet in `mkt_master_data`).
**Status**: canonical (but the rex_funds.csv override mechanism is deprecated — to be replaced by `classification_override` table).

### preflight

**Definition**: The 18:30 ET audit script (`scripts/preflight_check.py`) that runs 8 quality checks before the daily send window. Writes `data/.preflight_token` + `data/.preflight_result.json`. As of PR #16, also writes `data/.preflight_decision.json` via [[auto-go]] logic when overall_status=pass or warn-with-flag.
**Where it lives**: `scripts/preflight_check.py`, systemd `rexfinhub-preflight.timer`.
**Synonyms**: preflight audit, 18:30 check.
**Not to be confused with**: [[gate]] (preflight informs gate decision but doesn't open/close it directly).
**Status**: canonical.

### rex-product

**Definition**: Row in `rex_products` table — represents a single REX-branded fund or filing across all lifecycle states (Under Consideration → Filed → Effective → Target List → Listed → Delisted). 552 rows as of 2026-05-19.
**Where it lives**: `rex_products` table; admin edit UI at `/admin/rex-products`.
**Synonyms**: REX product row.
**Not to be confused with**: [[capm-product]] (a curated subset with extra fee/custodian fields — to be merged in Phase 3).
**Status**: canonical.

### send-pipeline

**Definition**: The 19:30 ET orchestrated daily flow that runs `scripts/run_daily.py` → `scripts/send_all.py --use-decision --send` → emails via Microsoft Graph API. On Monday fires bundle `all`; Tue-Fri fires bundle `daily` only.
**Where it lives**: `scripts/run_daily.py` + `scripts/send_all.py`, systemd `rexfinhub-daily.timer`.
**Synonyms**: daily send, send chain, evening pipeline.
**Not to be confused with**: [[preflight]] (separate audit step before send).
**Status**: canonical.

### survivorship

**Definition**: Deterministic rule for picking which data source wins when SEC, Bloomberg, CBOE, and manual overrides disagree on a single field. Per-field source priority (e.g., for `inception_date`: CBOE listing notice > exchange Form 25 > Bloomberg ACTV first-seen > manual). Not yet codified — planned as part of the rebuild.
**Where it lives**: planned in `webapp/services/survivorship.py` (proposed Phase 5).
**Synonyms**: source precedence, golden-record rule.
**Not to be confused with**: classification override (which is a manual write, not a survivorship rule).
**Status**: proposed.

### underlier-master

**Definition**: Polymorphic table mapping `underlier_id` → typed reference (equity / etp / index / crypto_pair / basket / commodity / fx / rate) with appropriate identifier per type (FIGI for stocks/ETPs/crypto via OpenFIGI; index_code for indices; etc.). Replaces string-based underlier columns in `mkt_master_data` and `rex_products`.
**Where it lives**: planned in `underlier_master` table (Phase 4).
**Synonyms**: underlier dimension, polymorphic underlier.
**Not to be confused with**: `map_li_underlier` / `map_cc_underlier` / `map_crypto_underlier` string columns (today's broken state).
**Status**: proposed.
