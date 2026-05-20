---
doc: target
status: canonical
version: v4
updated: 2026-05-19
---

# rexfinhub — Target Architecture (To-Be)

> Canonical reference for what we're building toward. Future tense. AS-IS lives in `SYSTEM.md`. Decisions justifying the target live in `DECISIONS/`.
>
> When a phase ships, its content migrates from `### phases` here into `SYSTEM.md` and gets struck through here (preserve the heading for backlinks).

### principles

The design invariants we won't compromise on:

1. **One canonical product identifier**. A synthetic UUID per fund/filing. Tickers, CUSIPs, ISINs, FIGIs, CIKs, SEC series IDs all map to it via a side table with `valid_from`/`valid_to`. Tickers being recycled by SEC during pre-launch never re-points an existing product. (See `DECISIONS/0006-canonical-product-id.md` — proposed.)
2. **Polymorphic underlier reference**. Underliers are typed (equity / etp / index / crypto_pair / basket / commodity / fx / rate), not string tickers. Resolution via [OpenFIGI](https://www.openfigi.com/) where applicable.
3. **Bi-temporal lifecycle**. Every status change appends a row to `status_history`; nothing updates in place. `valid_from`/`valid_to` (reality time) + `tx_from`/`tx_to` (knowledge time) per SCD-2 / SQL:2011 patterns.
4. **Deterministic survivorship**. When sources disagree, a declared per-field source priority resolves the conflict. No ad-hoc winner-picking.
5. **Daily ops as data-quality assertions**. ~25 dbt-style tests run every morning; failures become triage items in the 08:00 ET summary. Ryu's job is to triage, not to redo pipeline work.
6. **Single override table replaces all rule CSVs**. One `classification_override(product_id, field_name, value, set_by, reason)` table; admin UI writes one row per override; no text-file editing.
7. **Self-service admin via webapp**. Cookie rotation, target-inception entry, send-pause toggle — all happen via `/admin/*` pages. No chat-in-the-middle, no SSH-edit-config, no Syncthing-from-laptop.

### data-model

```sql
-- canonical-product-id  (Phase 4)
product_master (
  canonical_id   UUID PK,
  created_at     TIMESTAMPTZ,
  fund_name      TEXT,           -- denormalized convenience
  status_current TEXT             -- derived from status_history; cached
);

identifier_xref (
  canonical_id   UUID FK,
  id_type        TEXT,            -- 'ticker','cusip','isin','figi','cik','series_id','class_contract_id'
  id_value       TEXT,
  valid_from     TIMESTAMPTZ,
  valid_to       TIMESTAMPTZ      -- NULL = current
);

-- polymorphic underlier  (Phase 4)
underlier_master (
  underlier_id   UUID PK,
  underlier_type TEXT             -- enum: equity|etp|index|crypto_pair|basket|commodity|fx|rate
  primary_figi   TEXT,            -- for equity/etp/crypto where FIGI applies
  ticker         TEXT,            -- equity/etp
  index_provider TEXT,            -- 'S&P','MSCI','Bloomberg'
  index_code     TEXT,            -- 'SPX','BMAXATCL'
  crypto_base    TEXT,            -- 'BTC'
  crypto_quote   TEXT,            -- 'USD'
  display_symbol TEXT             -- 'XBTUSD'
);

fund_underlier (
  canonical_id   UUID FK,
  underlier_id   UUID FK,
  weight         NUMERIC,
  effective_period TSRANGE
);

-- bi-temporal status  (Phase 5)
status_history (
  canonical_id   UUID FK,
  status         TEXT,            -- enum: filed|effective|trading|suspended|delisted|liquidated
  valid_from     TIMESTAMPTZ,
  valid_to       TIMESTAMPTZ,
  tx_from        TIMESTAMPTZ,
  tx_to          TIMESTAMPTZ,
  source         TEXT,            -- 'sec_485bpos','cboe_listing_notice','bloomberg_actv_first_seen','manual'
  evidence       JSONB
);
```

### classification

Replace all 6 rule CSVs with a single override table (Phase 6):

```sql
classification_override (
  canonical_id  UUID FK,
  field_name    TEXT,             -- 'etp_category','issuer_display','is_rex','primary_strategy', ...
  value         TEXT,             -- NULL = explicit blacklist
  set_by        TEXT,             -- 'admin' | 'auto_classifier' | 'manual'
  set_at        TIMESTAMPTZ,
  reason        TEXT,
  PRIMARY KEY (canonical_id, field_name)
);
```

Resolution at read time:
```
final(canonical_id, field) = override(canonical_id, field)
                          ?? bloomberg_value(canonical_id, field)
                          ?? auto_classify(canonical_id, field)
                          ?? NULL
```

Override edits happen via `/admin/products/{canonical_id}` form. Audit log captures who/when/why.

### survivorship

Per-field source priority. When two sources have a non-NULL value, the higher-priority source wins:

| Field | Priority order (highest first) |
|---|---|
| `inception_date` | CBOE listing notice → Exchange Form 8-A → Bloomberg ACTV first-seen → Manual override |
| `trading_status` | CBOE listing notice + first trade observed → Bloomberg ACTV → SEC effective_date |
| `expense_ratio` | REX-filed prospectus → Bloomberg `expense_ratio` |
| `fund_name` | SEC class_contract_name → Bloomberg `fund_name` → Manual override |
| `is_rex` | REX-filed prospectus signature → classification_override → Bloomberg issuer hint |
| `etp_category` | classification_override → auto_classifier → Bloomberg taxonomy (last resort) |

Codified in `webapp/services/survivorship.py` (Phase 5).

### ops-as-assertions

~25 dbt-style tests run after every Bloomberg sync (17:30 ET) and reported in the 08:00 ET summary email:

- Bloomberg file mtime ≥ today's 17:00 ET (freshness)
- `mkt_master_data` row count change < 5% day-over-day (catastrophic-change detector)
- Every active REX product has `primary_strategy` populated (classification gap)
- Every Listed `rex_product` has matching `mkt_master_data` row with `market_status='ACTV'` (BMAX-case detector)
- Flow report KPI matches issuer-table sum for each suite (the $10.8M vs $16.4M bug class)
- No ticker appears in >1 active `rex_products` row (duplicate-ticker audit — Bug 2 class)
- Every active REX product has resolved `underlier_id` (Bug 1 class)
- Pre-launch `rex_products.status` never `Listed` unless 3-source rule satisfied
- `AZURE_CLIENT_SECRET` expiry > 30 days away (secret-rotation reminder)
- Send-log has expected count of yesterday's emails (delivery confirmation)

Failures → "X passed / Y failed, here are the Y" in the 08:00 email. Ryu triages.

### phases

Phase 0a — Security hardening (2-3 days). DEFERRED per Ryu 2026-05-19.
~~Phase 0b — Triage patches for BUG-01 through BUG-04~~ **SHIPPED 2026-05-19** — see `DECISIONS/0002-phase-0b-triage-patches.md`. BUG-01/02/03 mitigated at the scraper layer; BUG-04 surfaces via audit. BUG-05 still open, deferred to Phase 6.
~~Phase 1 — Cuts (round 1)~~ **PARTIALLY SHIPPED 2026-05-19** — see `DECISIONS/0003-phase-1-cuts.md`. Cuts 1/2/4/5 done. Cut 3 analysis closed by **ADR 0005** (`DECISIONS/0005-scraper-merge-analysis.md`): three pathways look redundant but each has a distinct role (atom-watcher = discovery, fresh-poller = 15-min enrichment SLA, sec-scrape = 4×/day artifact refresh). Decision: keep all three; rename `rexfinhub-sec-scrape` → `rexfinhub-intraday-refresh` and skip the duplicate scrape step. Implementation pending.
Phase 1 — Cuts: kill 09:00 sweep, kill weekly trust universe sync, merge 4 Bloomberg-chain post-steps to 1 script, extend D-drive sync (1 week).
~~Phase 2 — Admin pages~~ **SHIPPED 2026-05-19** — see `DECISIONS/0004-phase-2-admin-pages.md`. `/admin/cboe-cookie` replaces the SSH skill (paste-and-submit, 15 sec). Inline target-inception editor on `/operations/pipeline`: column renamed to "Target Inception", `＋ set` affordance on empty cells, JS endpoint switched to `/admin/rex-products/update/{id}` so edits register as manual overrides in `manually_edited_fields` (was silently clobbered by the daily Bloomberg-chain sweep before this fix).
Phase 2 — Admin pages: `/admin/cboe-cookie`, inline `target_inception_date` editor on `/operations/pipeline` (1 week).
~~Phase 3 — Merge `capm_products` into `rex_products`~~ **STAGES 1-3 SHIPPED 2026-05-19** (PR #24, ADR 0007). Schema migrated; 74/74 CapM rows backfilled into `rex_products`; `/operations/products` route refactored to 2-way merge. Stages 4-5 (grace period + drop table) deferred to ≥2026-05-26.
Phase 3 — Merge `capm_products` into `rex_products`. Drop `capm_products` (1 week).
~~Phase 4 — Build `product_master.canonical_id` + polymorphic `underlier_master`~~ **STAGES 1-5 SCHEMA + BACKFILL SHIPPED 2026-05-19** (PRs #31, #32). All 5 new tables populated: product_master (541), identifier_xref (1,756), underlier_master (477; 64 unknowns queued for OpenFIGI resolution), fund_underlier (507). rex_products.canonical_id set for every row. Remaining work: OpenFIGI resolution for unknowns + drop freeform columns from rex_products (deferred to Phase 4b after coordinated rollout).
Phase 4 — Build `product_master.canonical_id` + polymorphic `underlier_master` (2-3 weeks).
~~Phase 5 — Build `status_history` bi-temporal table~~ **STAGES 1-4 SHIPPED 2026-05-19** (PRs #32, #35, #39). 608 status_history rows; reconciler (dry-run by default) wired into bloomberg-chain post-steps; `rex_products.status_cached` column added + backfilled + auto-updated by reconciler. Remaining: Stage 5 (deprecate direct writes to `rex_products.status`) after operator validates 3+ days of reconciler dry-run diffs.
Phase 5 — Build `status_history` bi-temporal table; 3-source rule for `Listed` promotion; auto-detect Bloomberg-vanished funds (2 weeks). **DESIGNED** in `DECISIONS/0008-status-history-bitemporal.md` (proposed); implementation starts after Phase 4 completes (~2026-06-10).
~~Phase 6 — Replace 6 CSVs with `classification_override` table~~ **STAGES 1-6 SHIPPED 2026-05-19** (PRs #32, #35, #36, #37, #38, #41). 486 override rows migrated; resolver service in place; admin endpoints `/admin/classify-override/{canonical_id}` POST/GET/DELETE; 15 assertions running daily; `rexfinhub-morning-triage.timer` enabled (08:00 ET) — first fire tomorrow; `scripts/apply_classification_overrides.py` wired into bloomberg-chain so overrides take effect nightly. Remaining: Stage 7 (delete CSVs after one-week stable read from override table) + HTMX inline-edit UI on /operations/products.
Phase 6 — Replace 6 CSVs with `classification_override` table; ship ~25 data-quality assertions; move pipeline summary to 08:00 ET (1-2 weeks). **DESIGNED** in `DECISIONS/0009-classification-override-and-assertions.md` (proposed); implementation starts after Phase 5 completes (~2026-06-25).
~~Phase 7 Part B — State-file consolidation~~ **STAGE 1 SHIPPED 2026-05-19** (PR #41). `system_flags` (5 rows from 5 flag files), `preflight_run` (1 row from latest result+decision+token), `system_event` (105 rows from event-log tails) tables created + initial backfill. Dual-read window opens; files remain authoritative until Stage 2+ flips reads.
Phase 7 Part A — edgartools migration: pending (massive; 3-4 weeks). **DESIGNED** in `DECISIONS/0010-edgartools-migration.md` (proposed); implementation starts ≥ 2026-07-15.

Total: 11-14 weeks. Phase 0 delivers immediate wins; Phases 1-2 deliver UX wins; Phases 3-7 are the structural rebuild.

### cuts

What gets removed:

- `capm_products` table → folded into `rex_products` (Phase 3)
- `manually_edited_fields` JSON column → replaced by `classification_override` (Phase 6)
- 09:00 ET morning classification sweep timer → redundant with preflight (Phase 1)
- Weekly trust universe sync timer → atom watcher covers it (Phase 1)
- 6 rule CSVs in `config/rules/` → one override table (Phase 6)
- 4 separate Bloomberg-chain ExecStartPost scripts → one consolidated script (Phase 1)
- `mkt_report_cache` pre-bake (if Render performance allows on-demand) — re-evaluate after Phase 4
- 5+ state files (`.preflight_token`, `.preflight_decision.json`, `.preflight_result.json`, `.send_enabled`, `.pipeline_stages.jsonl`, `.gate_state_log.jsonl`) → consolidate to 2-3 (Phase 6)
- Windows Task Scheduler jobs that mirror VPS timers (Phase 1)
- `config/email_recipients.txt` text-file fallback → DB is truth (Phase 1)
- SMTP fallback code path → Graph-only in production (Phase 1)
- One of {`rexfinhub-fresh-poller.timer`, `rexfinhub-sec-scrape.timer` × 4} → keep fresh-poller, retire batch scrape OR vice versa (Phase 1)

### known-gaps

- GAP-01: ADR for the `capm_products` merge not yet written. Will land as `DECISIONS/0002-merge-capm-and-rex-products.md` before Phase 3.
- GAP-02: ADR for canonical-product-id not yet written. Will land as `DECISIONS/0006-canonical-product-id.md` before Phase 4 (re-numbered from 0004 — 0004 is Phase 2 admin pages; 0005 is the Cut 3 scraper merge analysis).
- GAP-03: ADR for survivorship rule table not yet written. Will land before Phase 5.
- GAP-04: Open question — keep `mkt_report_cache` pre-bake or compute on demand? Re-evaluate after Phase 4 ships and Render perf can be measured.
- GAP-05: Open question — keep weekly trust universe sync as quarterly metadata backfill OR delete entirely? Ryu prefers delete; needs one more verification that atom watcher truly catches every new ETP issuer's first 485APOS within 1-3 min.
- GAP-06: Open question — automated CBOE login (feed username+password, system generates session) vs current paste-cookie flow. Cleaner UX but adds auth-handling surface area. Pending Phase 2 decision.
