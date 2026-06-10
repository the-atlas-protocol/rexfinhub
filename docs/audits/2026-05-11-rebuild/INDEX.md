# rexfinhub Full Integrity Audit — 2026-05-11

Multi-stage discovery + investigation + verification audit. Triggered after VPS preflight surfaced 100% NULL primary_strategy + 79 unclassified launches + 17 NULL issuer_display + 3 cross-series ticker dupes.

## Stage 1 — Discovery (read-only, parallel agents)

Each agent owns one surface, hunts for unknowns, writes findings to its own doc. No fixes proposed.

| # | Surface | Agent doc |
|---|---|---|
| 1 | SEC ingestion (step2/3/4/5, manifest, sec_client) | [01_sec_ingestion.md](01_sec_ingestion.md) |
| 2 | Bloomberg ingestion (Graph API, market_sync, brand derivation) | [01_bloomberg_ingestion.md](01_bloomberg_ingestion.md) |
| 3 | Classification engine (auto_classify, write_classifications, the 100% NULL primary_strategy mystery) | [01_classification.md](01_classification.md) |
| 4 | DB integrity sweep (dupes, NULLs, orphans, FK, type confusion) | [01_db_integrity.md](01_db_integrity.md) |
| 5 | Report builds (5 reports × build code × rendered output) | [01_report_builds.md](01_report_builds.md) |
| 6 | Send pathway (preflight, gate, decision file, SMTP, dedup) | [01_send_pathway.md](01_send_pathway.md) |
| 7 | Webapp cross-page consistency (5 funds × ~10 pages × ~10 fields) | [01_webapp_consistency.md](01_webapp_consistency.md) |
| 8 | Schedulers (every systemd timer + service, 7-day journal review) | [01_schedulers.md](01_schedulers.md) |
| 9 | CBOE + Symbol Reservation (475k/night ingestion + new ReservedSymbol surface) | [01_cboe_reserved.md](01_cboe_reserved.md) |
| 10 | 13F + structured notes ingestion | [01_13f_notes.md](01_13f_notes.md) |
| 11 | Auth + secrets + access | [01_auth_secrets.md](01_auth_secrets.md) |
| 12 | Caching layers (http_cache, screener_3x_cache, FilingAnalysis LLM cache) | [01_caching.md](01_caching.md) |
| 13 | Local vs VPS vs Render DB drift | [01_db_drift.md](01_db_drift.md) |
| 14 | CSV rules interlocking pipeline (fund_mapping + attributes + issuer_mapping) | [01_csv_rules.md](01_csv_rules.md) |
| 15 | REX-specific tables (RexProduct, CapM, ReservedSymbol, audit logs) | [01_rex_tables.md](01_rex_tables.md) |
| 16 | Recipient list + email deliverability | [01_recipients.md](01_recipients.md) |
| 17 | Financial number correctness (AUM, flows, returns) | [01_financial_numbers.md](01_financial_numbers.md) |
| 18 | Date + timezone correctness | [01_dates_tz.md](01_dates_tz.md) |

## Stage 2 — Investigation (one agent per high/critical Stage 1 finding)

Format: `02_<finding-id>_<short-name>.md`. Populated after Stage 1 lands.

## Stage 3 — Verification (independent reviewers + cross-checks)

| Type | Output |
|---|---|
| Reviewer A | 03_review_A.md |
| Reviewer B | 03_review_B.md |
| Reviewer C | 03_review_C.md |
| Cross-join consistency | 03_join_consistency.md |
| Report cell trace (20 random per report) | 03_report_cell_trace.md |

## Stage 4 — Triage + Fix

- `04_triage_card.md` — consolidated findings, ranked, must-fix-tonight vs log-for-later
- Fix execution logged in this index as PR/commit references

## Conventions

- Severity: critical (silent wrong data shipped) | high (visible wrong data) | medium (degraded) | low (cleanup)
- Fix size: trivial (<10 lines) | small (10-50) | medium (50-200) | large (>200) | architectural
- Every finding has: severity, surface (file:line or table.column), symptom, evidence, blast radius, hypothesis, fix size
- All artifacts are markdown, all live in this folder, all referenced from this index
