# Pipeline Page — Phase 2 Audit (2026-05-11)

**Scope:** `/operations/pipeline` — full-scale audit after Phase 1 redesign
shipped (commit `f05dbb9`) and Phase 2 wiring (this PR).

**Files in scope:**
- `C:/Projects/rexfinhub/webapp/routers/pipeline_calendar.py` (handler `_pipeline_products_impl`)
- `C:/Projects/rexfinhub/webapp/templates/pipeline_products.html`

**DB snapshot:** 531 REX-branded products. Most-recent `updated_at` =
**2026-04-14 20:16:54** — the table has not been touched in 27 days, which
is why Recent Activity reads empty for windows ≤ 14d.

---

## 1. Phase 2 Items Shipped

| # | Item | Status | Where |
|---|------|--------|-------|
| 1 | Rename "Urgent" → "Upcoming Filings (14D)" | done | `pipeline_products.html:260`, urgency-pill at `:395` |
| 2 | Rename "Overdue" → "Past Effective Date" + tooltip | done | `pipeline_products.html:272`, urgency-pill at `:397` |
| 3 | Recent Activity day-window pills (7/14/30/90) | done (Phase 1) + verified | `pipeline_calendar.py:242-247, 374-389`; template `:291-300` |
| 4 | Page-size selector (20/50/100/All) | done | backend `pipeline_calendar.py:223-240, 530-545`; pills `pipeline_products.html:411-422` |
| 5 | Sortable column headers (server-side) | done | macro `pipeline_products.html:436-452`; backend sort_map `pipeline_calendar.py:497-509` |

All six template-render checks pass; eight URL variants return 200 in
129–341ms (TestClient, warm cache).

---

## 2. Data Accuracy Issues (CRITICAL)

These are the rows a PM would flag as "obviously wrong":

### 2.1 Stale "Awaiting Effective" pipeline (high priority)

**412** products are past their `estimated_effective_date` and not yet
Effective/Listed/Delisted (the new "Past Effective Date" card). The five
oldest go back to **2016–2018**:

| Ticker | Est. Effective | Status | Days Past | Name |
|--------|---------------|--------|-----------|------|
| GHE | 2016-01-29 | Awaiting Effective | 3755d | REX GOLD HEDGED FTSE EM ETF |
| GHS | 2018-04-19 | Awaiting Effective | 2944d | REX GOLD HEDGED S&P 500 ETF |
| VMAX | 2018-04-19 | Awaiting Effective | 2944d | REX VolMAXX Long VIX |
| VMIN | 2018-04-19 | Awaiting Effective | 2944d | REX VolMAXX Short VIX |
| BKC | 2018-05-21 | Awaiting Effective | 2912d | REX BKCM ETF |

These are clearly **dead/abandoned filings** that were never moved to
Delisted. They pollute the funnel ("Awaiting Effective: 250" is wildly
overstated) and the Past Effective Date count (412 is mostly historical
debris, not actionable launch slip).

**Fix recommendation (out-of-scope for this PR):** one-time data cleanup
moving products with `estimated_effective_date < today - 365` AND
`status in ('Filed*', 'Awaiting Effective')` to a new `Abandoned` status.
Rough sweep: ~250 of 412 would qualify.

### 2.2 Status / form inconsistencies

`485BPOS` is the *post-effective* amendment — by definition the fund is
already effective. Yet:

- **268** rows have `status = "Awaiting Effective"` AND `latest_form = "485BPOS"`
- **6** rows have `status = "Filed"` AND `latest_form = "485BPOS"`
- **206** rows have `status = "Filed"` AND `latest_form = "485BXT"` (extension — should be "Filed (485B)" or similar lifecycle stage)

This is a **classifier mapping bug**, not a UI bug. The form-to-status
inference in whatever pipeline populates these rows is mis-mapping
post-effective forms to pre-effective statuses. The new 15-value status
enum (`Filed (485A)`, `Filed (485B)`, `Effective`) is in the dropdown but
nobody is writing those values — the table only contains 5 distinct
statuses (Awaiting Effective, Filed, Listed, Delisted, Research).

### 2.3 Listed without listed-date

Two `status="Listed"` rows have `official_listed_date IS NULL`:

- `PAAU` — T-REX 2X LONG PAAS DAILY TARGET ETF
- `SNDU` — T-REX 2X LONG SNDK DAILY TARGET ETF

Both should backfill from market_master_data inception_date.

### 2.4 Truncated Recent Activity coverage

Activity is a `updated_at` proxy — but `updated_at` only ticks when
something edits the row through admin endpoints. Daily SEC pipeline does
**not** update `rex_products`. So Recent Activity will show empty for any
day where no admin manually touched a row, even if the underlying SEC
status has changed. The empty-state message now surfaces this fact via
`last_updated_overall`.

**Fix recommendation (out-of-scope):** create
`rex_product_status_history(product_id, old_status, new_status, source, occurred_at)`
and populate from both admin edits AND the daily 485 ingest. Until then,
the activity feed is a stale-edit log, not a true activity log.

---

## 3. Visual / UX Issues

### 3.1 Funnel double-counts the lifecycle

The funnel has **7** stages but only **5** statuses are populated in the
DB. The "Counsel" and "Board" stages are perpetually 0, taking up space
that could go to more useful slices. The "Effective / Live" bucket lumps
Effective + Listed (one is a transient state, one is terminal).

Recommendation: either populate Counsel/Board statuses (admin workflow
needed) or hide empty stages dynamically. Not done in this PR — additive
data work.

### 3.2 "Days in stage" is misleading

The metric uses the most-recent of `(initial_filing_date,
official_listed_date, target_listing_date, updated_at)` as the anchor.
For a Listed product, this anchors on `official_listed_date` so "days in
stage" measures days-since-listing, which is the right thing. For a Filed
product without a target_listing_date, it can fall through to
`updated_at`, which advances every time *anything* on the row changes —
not when the *stage* changed. Result: a product that was filed 60 days
ago but had its `notes` field edited yesterday shows "1d in stage".

This is the same root cause as 2.4 — needs a real status-history table.

### 3.3 Sticky filter bar over-stickiness

`.filter-panel { position: sticky; top: 0 }` (line 111 of the template)
sticks to the viewport top. On a wide monitor this is fine; on a laptop
it covers ~1/3 of the visible table when scrolled. Consider:
- `top: 56px` to clear the global nav, OR
- collapse-on-scroll behavior so it shrinks to a one-line summary

### 3.4 Status pill color collisions

`STATUS_COLORS` (pipeline_calendar.py:117-133) assigns:
- Counsel Approved → `#22c55e` (light green)
- Board Approved → `#16a34a` (green)
- Listed → `#059669` (dark green)
- Effective → `#0d9488` (teal)

Four greens in close succession with low contrast. Hard to distinguish
on the table at a glance. Recommend re-coloring one of the approval
states to amber/yellow.

### 3.5 Action bar layout when "Show: All" warning fires

The new `.pagesize-warn` orange chip can wrap the `.pager-meta` element
to the next line when the URL has `?per_page=all` AND filters are
narrow. Acceptable but could be cleaner with a flex wrap rule.

---

## 4. Missing PM Columns

The current 9-column table covers identity + lifecycle dates + form, but
omits several fields a PM uses daily. Coverage data:

| Field | Coverage | In Schema? | Currently shown? |
|-------|---------|-----------|------------------|
| Underlier | 68% (364/531) | yes | NO — searchable but not visible |
| Direction (Long/Short) | populated for derivatives | yes | NO |
| Mgt fee | 10% (58/531) | yes | NO |
| LMM (Lead Market Maker) | 10% (56/531) | yes | NO |
| Exchange | 11% (59/531) | yes | NO |
| CIK / Series ID | 93% (498/531) | yes | NO |
| Trust | populated | yes | NO |

**Recommendation:**
1. Add **Underlier** + **Direction** columns to the default view (2x
   small columns, high signal per-pixel, 68% coverage justifies it).
2. Add a "Detail" column (modal or expanding row) for fee / LMM /
   exchange / CIK / trust — sparse fields that don't deserve a top-level
   column but are needed when a PM clicks into a product.
3. The fee/LMM/exchange coverage of 10% is itself an audit finding — these
   should be backfilled from Bloomberg `market_master_data` for the 225
   products that have a ticker.

---

## 5. Performance

Render times (TestClient, warm cache, local SQLite):

| URL | Time | Bytes |
|-----|------|-------|
| `/operations/pipeline` (default 50/page, cold filter) | 341ms | 141 KB |
| `?per_page=20` | 155ms | 92 KB |
| `?per_page=100` | 132ms | 225 KB |
| `?per_page=all` (477 rows) | 180ms | 831 KB |
| `?sort=ticker&dir=asc` | 154ms | 142 KB |
| `?sort=estimated_effective_date&dir=desc` | 291ms | 139 KB |
| `?recent_days=30` | 129ms | 152 KB |
| `?recent_days=90` | 142ms | 152 KB |

No regression vs Phase 1. The `per_page=all` variant is 5.9x the default
payload but still under 200ms server-side. Browser DOM cost on 477 rows
is the real risk; the warning chip warns about it but doesn't enforce.

The KPI block runs **~14 separate `count()` queries plus a per-suite
loop** (`pipeline_calendar.py:265-441`). On SQLite this is fine; on
Postgres in production it'll be a measurable hit. Not a regression but a
known carrying cost.

---

## 6. Mobile Responsiveness

Reviewed CSS breakpoints in `pipeline_products.html:36-38, 59, 65`:

| Breakpoint | Behavior | Verdict |
|------------|----------|---------|
| `<1100px` | Funnel collapses 7→4 cols (2 rows); activity-row collapses 2-col → 1-col | OK |
| `<900px` | Urgency cards 4→2 cols | OK |
| `<768px` | **No table breakpoint** | Table requires horizontal scroll on phones |

The `.table-scroll { overflow-x:auto }` wrapper saves us — the table is
horizontally scrollable on narrow viewports — but UX is poor (no column
priority, no card view fallback). The action-bar pills wrap awkwardly
under 600px. Acceptable for desktop-first product but a known gap.

The new `.pagesize-pills` and existing `.recent-pills` and `.urgency-pills`
all use `display:inline-flex; gap:Npx` which wraps cleanly on narrow
screens. Verified in DOM with no overflow issues.

---

## 7. Overall Feel — What's Still Missing

Ranked by PM-utility-per-effort:

1. **Owner / responsible-PM column.** No way to see "who is driving this
   product." Schema has no `owner` field. Add `RexProduct.owner` (string
   FK to a `users`/`pm` lookup table) and surface as 7th column.
2. **Last-touched-by audit trail.** The "Recent Activity" stream is
   anonymous — we know `updated_at` advanced but not who changed what.
   Same fix as 2.4 (status history table) but extend to all field edits.
3. **Bulk-edit affordance.** Admin can edit one row at a time inline. No
   way to set 20 rows from "Filed" → "Awaiting Effective" after a board
   meeting. Add a checkbox column + bulk-action bar.
4. **CSV export of the filtered table.** "Export Distributions CSV" is
   the only CSV button — there's no "export the current filter" for the
   pipeline rows themselves. PMs ask for this constantly.
5. **Stale-row warning.** Surface "rex_products last updated 27 days
   ago" as a top-of-page banner when last_updated_overall is > 14d old —
   right now this only shows in the empty-state of Recent Activity, but
   stale data is the root cause of half the issues in §2.
6. **Funnel→table linking is one-way.** Clicking a funnel stage filters
   the table by the *first* status in the bucket only (e.g. "Counsel"
   filters to "Counsel Review", drops the other two). Should pass an
   `OR`-list or use a status-group filter.

---

## 8. Acceptance Verification

```
341ms  200   141077b  /operations/pipeline
155ms  200    91695b  /operations/pipeline?per_page=20
132ms  200   224753b  /operations/pipeline?per_page=100
180ms  200   831303b  /operations/pipeline?per_page=all
154ms  200   142332b  /operations/pipeline?sort=ticker&dir=asc
291ms  200   139050b  /operations/pipeline?sort=estimated_effective_date&dir=desc
129ms  200   152274b  /operations/pipeline?recent_days=30
142ms  200   152274b  /operations/pipeline?recent_days=90
  [OK] Upcoming Filings (14D)
  [OK] Past Effective Date
  [OK] No old Urgent label
  [OK] Sort indicator markup
  [OK] Page size selector
  [OK] Recent days toggle
```

All six template checks pass; eight URL variants return 200 with no
regression.

---

## 9. Out-of-Scope Followups (proposed Phase 3)

- Data cleanup: archive the 250 pre-2020 stale "Awaiting Effective"
  rows to `Abandoned` status
- Schema: `rex_product_status_history` table; emit row from admin
  update endpoint AND from daily SEC pipeline
- Schema: `RexProduct.owner` field + admin-only owner edit
- Backfill `mgt_fee`, `lmm`, `exchange` from `market_master_data` join
  on ticker (raise coverage from 10% → ~42%)
- Add Underlier + Direction columns to default view
- Bulk-edit + CSV export of filtered rows
- Top-of-page stale-data banner when `updated_at` > 14d old
- Funnel-stage clicks should pass full status-group OR-list
