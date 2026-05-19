---
adr: 0004
title: Phase 2 — Self-service admin pages (CBOE cookie + target inception)
status: accepted
date: 2026-05-19
deciders: Ryu El-Asmar
---

# ADR 0004 — Phase 2: self-service admin pages

## Context

`TARGET.md#phases` defines Phase 2 as two surfaces that close the gap between the [[ideal-day]] (three-touchpoint workday) and reality:

1. **`/admin/cboe-cookie`** — replaces the `cboe-cookie` SSH skill, which has been the only way to rotate `CBOE_SESSION_COOKIE` since the 2026-05-12 lockdown. Ryu pastes a 32-char token into a webapp form instead of running an SSH-skill that:
   - edits the VPS `.env` via `sed`
   - calls `live_check("AAPL")` to confirm auth
   - dispatches the recovery sweep + Render upload chain
   - takes ~5 minutes of operator attention per rotation, repeated every 24-48 hours

2. **Inline `target_inception_date` editor on `/operations/pipeline`** — described in `RUNBOOK.md#touchpoint-pipeline-inception-date` as "no inline-edit UI yet." Investigation revealed the field actually exists (`rex_products.target_listing_date`) and was already wired through `admin_products.update_product()` via the column 12 cell on the pipeline products table — but:
   - the column header read "Inception/Target" (ambiguous; sounded like an analytics field, not an editable one)
   - blank cells rendered `---` with no affordance suggesting they were clickable
   - the JS pointed at the legacy `/admin/products/update/{id}` endpoint instead of the audit-logging `/admin/rex-products/update/{id}` endpoint, so Ryu's inputs were saving but **not being marked as manual overrides** in `manually_edited_fields` — meaning the next Bloomberg-chain sweep could silently clobber them.

The fix for #2 is therefore mostly **discoverability + plumbing-correctness**, not a new feature.

## Decision

### Surface 1 — `/admin/cboe-cookie`

**Webapp routes** (in `webapp/routers/admin.py`):

```
GET  /admin/cboe-cookie    → renders admin_cboe_cookie.html with current status
POST /admin/cboe-cookie    → validates token shape, forwards to VPS
```

The handler tolerates three paste shapes (the regex `[a-z0-9]{32}` extracts the bare token from any of them):
- bare 32-char token (`ur6mnjf3t634fmxdmt1hq0nccyupmrc4`)
- `sessionid=<token>`
- full `Cookie: sessionid=<token>; csrftoken=...` header

On Render, the handler proxies to a new endpoint on the VPS pipeline API:

```
POST /pipeline/cboe-rotate?token=<32chars>    → rewrites .env + probes + dispatches sweep
GET  /pipeline/cboe-status                    → returns rotated_at + age + last sweep state
```

The VPS endpoint runs the rotation inline so the response carries the verdict, then fires the recovery sweep (`refresh_cboe_known_active.py` → `run_cboe_scan.py --tier full` → `run_daily.py --upload`) in the background via `_run_in_background`. Sweep state is exposed via `GET /pipeline/cboe-status` so the page can show progress on subsequent loads. A `data/.cboe_rotated_at` ISO-8601 timestamp file records the most recent rotation; the page derives "age in hours" from it for the freshness indicator.

The SSH skill (`~/.claude/skills/cboe-cookie/`) is **retained** as a fallback path. Operationally, the webapp is the primary surface, and the skill remains usable for cases where the webapp itself is unreachable.

### Surface 2 — inline target inception on `/operations/pipeline`

Three changes in `webapp/templates/pipeline_products.html`:

1. **Column header**: `"Inception/Target"` → `"Target Inception"` so the editable nature is clearer (no slash, no ambiguity about whether it's actual vs. projected).
2. **Cell affordance**: when admin AND status ≠ Listed AND no value set, render `"＋ set"` instead of `"---"`. Cursor switches to pointer on yellow projected cells.
3. **JS endpoint correction**: `makeInlineEditable('#pipelineTable', '/admin/products/update/{id}')` → `'/admin/rex-products/update/{id}'`. The new endpoint is the audit-logging variant — every inline edit now (a) writes to `capm_audit_log` with old/new values, (b) appends the field name to `manually_edited_fields` so the daily Bloomberg-chain sweep skips this column on this row, and (c) for status edits, appends a `rex_product_status_history` row.

**No schema migration is needed.** The field `target_listing_date` is the same field the docs called `target_inception_date`; this ADR canonizes that "target inception date" = `rex_products.target_listing_date` in the data layer, with the user-facing column label updated to match. The glossary entry stays as `target-inception-date` (user vocabulary) but resolves to `target_listing_date` in the schema.

### Naming reconciliation

| User vocab (canonical) | Data column           | Set by                                                  |
|---|---|---|
| target inception date  | `target_listing_date` | Inline edit on `/operations/pipeline` (Ryu's input)     |
| official inception     | `official_listed_date`| 3-source rule promotes when first trade observed        |
| estimated effective    | `estimated_effective_date` | SEC 485APOS + 75d default, or 485BPOS explicit     |
| initial filing         | `initial_filing_date` | SEC pipeline                                            |

Phase 5 will fold all four into the `status_history` bi-temporal table, at which point the column names become an internal detail.

## Consequences

**Wins**:
- The CBOE cookie rotation drops from a ~5-minute SSH-skill exercise to a 15-second paste-and-submit. Page shows freshness so Ryu can see *before* the page banner expires whether a rotation is needed.
- `target_inception_date` edits now persist as actual manual overrides — they survive the next Bloomberg-chain run instead of being silently clobbered when the auto-classifier re-derives `target_listing_date` from filing parses.
- Audit trail: every inline date/text edit on the pipeline page now writes to `capm_audit_log` (was: silently mutated the row with no record).

**Trade-offs**:
- Dual-mode `_call_vps` proxy: the admin route runs on Render but the rotation actually executes on the VPS. If VPS is unreachable the rotation fails; the SSH skill is the documented fallback.
- The auto-extraction regex `[a-z0-9]{32}` accepts a token embedded anywhere in the form value. This is permissive on purpose (tolerates messy paste) but means a copy-paste accident could in theory pick up the wrong substring if the user pasted an entire devtools dump containing multiple 32-char runs. Mitigated by the `live_check("AAPL")` probe that fires inline — a wrong token fails the probe and the sweep is not dispatched, so cost of a misextracted token is bounded.

**Revert path**: delete `/admin/cboe-cookie` route + template + `/pipeline/cboe-rotate` endpoint; restore line 1149 of `pipeline_products.html` to `/admin/products/update/{id}`; restore column 12 cell rendering. SSH skill flow continues to work either way.

## Alternatives considered

- **Add a new `target_inception_date` column** distinct from `target_listing_date`. Rejected — `target_listing_date` already serves exactly this role and is referenced by the calendar's "Launch" event renderer, daily classifier, and Bloomberg-chain sweep. Adding a parallel column would create a name-only distinction that the docs would have to keep explaining forever. ADR-level decision: the runbook's `target_inception_date` IS `target_listing_date`; we update the user-facing label, not the schema.
- **Auto-rotate the CBOE cookie via login form scraping**. Rejected (kept on `TARGET.md#known-gaps` GAP-06). The clean UX requires holding the CBOE login password somewhere, adding an auth-handling surface. Phase 2 keeps the paste-flow; auto-login is a Phase 5+ consideration if a Selenium/Playwright helper proves stable.
- **Read-only "cookie status" page without rotation capability**. Rejected — half-shipping the touchpoint forces Ryu back to the SSH skill anyway. The whole point is replacing the touchpoint.
