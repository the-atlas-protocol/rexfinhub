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

1. **One canonical product identifier**. A synthetic UUID per fund/filing. Tickers, CUSIPs, ISINs, FIGIs, CIKs, SEC series IDs all map to it via a side table with `valid_from`/`valid_to`. Tickers being recycled by SEC during pre-launch never re-points an existing product. (See `DECISIONS/0006-canonical-product-id.md` — accepted.)
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
  status_current TEXT,            -- derived from status_history; cached
  is_rex         BOOLEAN          -- REX-branded (incl. BMO MicroSectors)
);

identifier_xref (
  canonical_id   UUID FK,
  id_type        TEXT,            -- 'ticker','cusip','isin','figi','cik','series_id','class_contract_id','bloomberg'
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
  weight         NUMERIC,         -- NULL = sole underlier
  effective_from TIMESTAMPTZ,
  effective_to   TIMESTAMPTZ      -- NULL = current
);

-- bi-temporal status  (Phase 5)
status_history (
  canonical_id   UUID FK,
  status         TEXT,            -- enum: under_consideration|filed|effective|target_list|listed|suspended|delisted|liquidated
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

Override edits happen via `/admin/classify-override/{canonical_id}` (POST/GET/DELETE). Audit log captures who/when/why.

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

> Live execution plan for all remaining work: `raw/ops/REBUILD-COMPLETION-PLAN_2026-05-19.md`.

Phase 0a — Security hardening. **DEFERRED** per Ryu 2026-05-19 — out of rebuild scope. Acute items (rotate the GitHub-exposed `ADMIN_PASSWORD`, verify `AZURE_CLIENT_SECRET` expiry) are carved out as Track 0 of the completion plan.
~~Phase 0b — Triage patches for BUG-01 through BUG-04~~ **SHIPPED 2026-05-19** — see `DECISIONS/0002-phase-0b-triage-patches.md`. BUG-01/02/03 mitigated at the scraper layer; BUG-04 closed; BUG-05 since mitigated (see `SYSTEM.md#known-bugs`).
~~Phase 1 — Cuts (round 1)~~ **SHIPPED 2026-05-19** — see `DECISIONS/0003-phase-1-cuts.md`. Cuts 1/2/4/5 done. Cut 3 closed by **ADR 0005** (`DECISIONS/0005-scraper-merge-analysis.md`): the three scraper pathways each have a distinct role (atom-watcher = discovery, fresh-poller = 15-min enrichment SLA, intraday-refresh = 4×/day artifact refresh) — all three kept; `rexfinhub-sec-scrape` renamed → `rexfinhub-intraday-refresh` with a wrapper that skips the duplicate scrape. Closeout = Track 2 of the completion plan.
~~Phase 2 — Admin pages~~ **SHIPPED 2026-05-19** — see `DECISIONS/0004-phase-2-admin-pages.md`. `/admin/cboe-cookie` replaces the SSH skill (paste-and-submit, 15 sec); inline "Target Inception" editor on `/operations/pipeline` writes through the audit-logging endpoint so edits register as manual overrides.
~~Phase 3 — Merge `capm_products` into `rex_products`~~ **STAGES 1-3 SHIPPED 2026-05-19** (PR #24, ADR 0007). Schema migrated; 74/74 CapM rows backfilled into `rex_products`; `/operations/products` route refactored. Stages 4-5 (dual-write grace + drop `capm_products`) = Track 4a of the completion plan.
~~Phase 4 — Build `product_master.canonical_id` + polymorphic `underlier_master`~~ **STAGES 1-5 SCHEMA + BACKFILL SHIPPED 2026-05-19** (PRs #31, #32). All 5 tables populated: product_master (541), identifier_xref (1,756), underlier_master, fund_underlier; `rex_products.canonical_id` set for every row. Remaining work = Phase 4b.
Phase 4b — Underlier completion: resolve the remaining unknown underliers (alt-coin / custom baskets typed as `basket`/`crypto_pair`; OpenFIGI FIGIs for equity/etp underliers), then drop the freeform underlier columns from `rex_products`. = Track 3 + Track 4b of the completion plan.
~~Phase 5 — Build `status_history` bi-temporal table~~ **STAGES 1-4 SHIPPED 2026-05-19** (PRs #32, #35, #39) — see `DECISIONS/0008-status-history-bitemporal.md`. 608 status_history rows; reconciler (dry-run) wired into bloomberg-chain; `rex_products.status_cached` added + auto-updated; 3-source rule for `Listed` promotion. Stage 5 (deprecate direct `rex_products.status` writes) = Track 5A of the completion plan.
Phase 5B — Pre-filing lifecycle: an admin surface to create `under_consideration` (pre-filing) products, plus a filing matcher that attaches an incoming 485-series filing to the pre-existing `canonical_id` rather than minting a duplicate. Raised by Ryu 2026-05-20; = Track 5B of the completion plan; ADR pending.
~~Phase 6 — Replace 6 CSVs with `classification_override` table~~ **STAGES 1-6 SHIPPED 2026-05-19** (PRs #32, #35-#38, #41, #53) — see `DECISIONS/0009-classification-override-and-assertions.md`. 486 override rows migrated; resolver service live; admin endpoints `/admin/classify-override/{canonical_id}` + HTMX inline-edit UI; 25 data-quality assertions in the 08:00 ET morning triage. Stage 7 (delete the 6 CSVs) = Track 4c of the completion plan.
~~Phase 7 Part B — State-file consolidation~~ **STAGES 1-2 SHIPPED 2026-05-19** (PRs #41, #55-#59). `system_flags`, `preflight_run`, `system_event` tables created + backfilled; all flag reads migrated to the `system_flags` helper; `/admin/system-state` page live. Stage 3 (delete the 14 legacy flag files) = Track 4d of the completion plan.
Phase 7 Part A — edgartools migration: **DESIGNED** in `DECISIONS/0010-edgartools-migration.md`. Replace the in-house SEC extraction stack with `edgartools` behind a compatibility shim. = Track 6 of the completion plan.

The structural rebuild is built; what remains is provably retiring the legacy paths (see the completion plan's Build·Prove·Retire principle) plus the edgartools migration.

### cuts

What gets removed:

- `capm_products` table → folded into `rex_products` (Phase 3)
- `manually_edited_fields` JSON column → replaced by `classification_override` (Phase 6)
- 09:00 ET morning classification sweep timer → redundant with preflight (Phase 1)
- Weekly trust universe sync timer → atom watcher covers it (Phase 1)
- 6 rule CSVs in `config/rules/` → one override table (Phase 6)
- 4 separate Bloomberg-chain ExecStartPost scripts → one consolidated script (Phase 1)
- `mkt_report_cache` pre-bake (if Render performance allows on-demand) — re-evaluate after Phase 4
- 14 state files (`.preflight_token`, `.preflight_decision.json`, `.preflight_result.json`, `.send_enabled`, `.pipeline_stages.jsonl`, `.gate_state_log.jsonl`, …) → consolidate to 3 tables (Phase 7B)
- Windows Task Scheduler jobs that mirror VPS timers (Phase 1)
- `config/email_recipients.txt` text-file fallback → DB is truth (Phase 1)
- SMTP fallback code path → Graph-only in production (Phase 1)

### known-gaps

- GAP-01: Open question — keep `mkt_report_cache` pre-bake or compute on demand? Re-evaluate once Render perf can be measured.
- GAP-02: Open question — automated CBOE login (feed username+password, system generates a session) vs the current paste-cookie flow. Cleaner UX but adds auth-handling surface area.
- GAP-03: Phase 5B (pre-filing lifecycle + filing matcher) has no ADR yet — raised by Ryu 2026-05-20. Write before implementing Track 5B.

Resolved & closed 2026-05-19/20: capm-merge ADR (landed as `0007`), canonical-product-id ADR (`0006`), survivorship rule (folded into ADR `0008`), weekly-trust-sync question (sync disabled per ADR `0003`).
