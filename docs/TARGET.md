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

1. **One canonical product identifier**. A synthetic UUID per fund/filing. Tickers, CUSIPs, ISINs, FIGIs, CIKs, SEC series IDs all map to it via a side table with `valid_from`/`valid_to`. Tickers being recycled by SEC during pre-launch never re-points an existing product. (See `DECISIONS/0004-canonical-product-id.md` — proposed.)
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

Phase 0a — Security hardening (2-3 days). MUST go first.
Phase 0b — Triage patches for BUG-01 through BUG-05 (2-3 hours).
Phase 1 — Cuts: kill 09:00 sweep, kill weekly trust universe sync, merge 4 Bloomberg-chain post-steps to 1 script, extend D-drive sync (1 week).
Phase 2 — Admin pages: `/admin/cboe-cookie`, inline `target_inception_date` editor on `/operations/pipeline` (1 week).
Phase 3 — Merge `capm_products` into `rex_products`. Drop `capm_products` (1 week).
Phase 4 — Build `product_master.canonical_id` + polymorphic `underlier_master` (2-3 weeks).
Phase 5 — Build `status_history` bi-temporal table; 3-source rule for `Listed` promotion; auto-detect Bloomberg-vanished funds (2 weeks).
Phase 6 — Replace 6 CSVs with `classification_override` table; ship ~25 data-quality assertions; move pipeline summary to 08:00 ET (1-2 weeks).
Phase 7 — Migrate to `edgartools`; decommission `manually_edited_fields` JSON; consolidate state files (1 week).

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
- GAP-02: ADR for canonical-product-id not yet written. Will land as `DECISIONS/0004-canonical-product-id.md` before Phase 4.
- GAP-03: ADR for survivorship rule table not yet written. Will land before Phase 5.
- GAP-04: Open question — keep `mkt_report_cache` pre-bake or compute on demand? Re-evaluate after Phase 4 ships and Render perf can be measured.
- GAP-05: Open question — keep weekly trust universe sync as quarterly metadata backfill OR delete entirely? Ryu prefers delete; needs one more verification that atom watcher truly catches every new ETP issuer's first 485APOS within 1-3 min.
- GAP-06: Open question — automated CBOE login (feed username+password, system generates session) vs current paste-cookie flow. Cleaner UX but adds auth-handling surface area. Pending Phase 2 decision.
