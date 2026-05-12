# O1 — REX Ops PIPELINE Layout / Column Rewrite

**Branch:** `rexops-O1-layout`
**Worktree:** `C:/Projects/rexfinhub-O1`
**Files touched (sole owner):**
- `webapp/templates/pipeline_products.html`
- `webapp/routers/pipeline_calendar.py` (route handler + context dict only; DB queries untouched)

**Read-only respected:** `webapp/routers/capm.py`, `pipeline_calendar.py` DB queries, `webapp/models.py`, all other templates.

---

## Before / After (page layout description)

### Before
1. Breadcrumb + `<h1>REX Pipeline</h1>` + subtitle
2. **By Suite** KPI pills (suite chips, totals)
3. **Pipeline Funnel** (7-stage funnel, ~middle of the page)
4. **Urgency** cards (4 status cards)
5. **Recent Activity** feed (left, ~340px) + **Quick Stats** sidebar (right, 1fr) in a 2-column grid
6. Sticky filter bar (Search / Status / Suite / Hide-terminal / Apply)
7. Quick urgency pills row
8. Action bar (`+ Add Product` admin, Export CSV, Pipeline Calendar, page-size pills, pager meta)
9. Products table — columns: `Ticker | Name | Suite | Status | Days in stage | Filed | Est. Effective | Listed | Latest Form` (9 cols)
10. Pagination

### After
1. Breadcrumb + `<h1>REX Pipeline</h1>` + subtitle
2. **Pipeline Funnel** — moved to TOP of page, full-width
3. **Urgency** cards — unchanged position relative to funnel
4. **By Suite** KPI pills — moved BELOW funnel/urgency, sits directly ABOVE the products table
5. Sticky filter bar — unchanged
6. Quick urgency pills row — unchanged
7. Action bar — unchanged
8. Products table — 12 columns in the new order:
   `Trust | Suite | Ticker | Fund Name | Underlier | Status | Days in Stage | Initial Filing Date | Latest Filing Date | Effective Date | Latest Prospectus | Inception/Target Date`
9. Pagination — unchanged

Smoke-tested with `TestClient`: page returns 200 (~180 KB), funnel section index < By Suite section index < products table index; all 12 column headers render; no markup matches for `<div class="activity-row"`, `Recent Activity (last`, or `Total products (all)`.

---

## Removed sections (HTML markup + supporting context dict keys)

### Removed HTML blocks
| Block | Source location (pre-edit lines) | What it was |
|------|---------------------------------|------|
| `<div class="activity-row">` ... `</div>` | `pipeline_products.html` 421–530 | Recent Activity feed (left) + Quick Stats sidebar (right), 2-col grid |
| Old `<h2>By Suite</h2>` + `<div class="suite-kpis">` block above the funnel | `pipeline_products.html` 340–362 | Per-suite pills (moved, not deleted) |
| Old `<h2>Pipeline Funnel</h2>` + caption + 7-stage `<div class="funnel">` | `pipeline_products.html` 365–385 | Funnel (moved, not deleted) |

The Funnel and By Suite blocks were preserved (relocated, see Layout section). Only the Recent Activity + Quick Stats blocks were deleted outright.

### Removed CSS rules
The CSS for `.activity-row`, `.activity-card`, `.quick-card`, `.activity-head`, `.activity-body`, `.activity-row-item`, `.activity-ticker`, `.activity-name`, `.activity-status`, `.activity-when`, `.activity-prospectus`, `.activity-empty`, `.activity-head-row`, `.recent-pills`, `.recent-pill`, `.quick-list`, `.quick-list-item` (block previously at lines 95–148) was deleted in the same edit and replaced with a one-line dead-code marker.

### Removed Python context dict keys
Deleted from the `templates.TemplateResponse(...)` payload in `pipeline_calendar.py` `_pipeline_products_impl`:

- `listed`, `filed`, `awaiting`, `research` (Quick Stats numeric rows)
- `filings_last_7d`, `launches_last_30d`, `effectives_next_30d`, `effectives_next_90d` (Quick Stats activity rows)
- `next_launches` (was unused by template even before this edit)
- `avg_cycle`, `min_cycle`, `max_cycle`, `cycle_sample` (Quick Stats cycle-time row + tail vars)
- `recent_activity` (Recent Activity feed source)
- `recent_days` (window selector for Recent Activity)
- `last_updated_overall` (empty-state copy for Recent Activity)

Kept (still in payload):
- `total` — used by the `All` suite-kpi pill above the products table.
- `funnel`, `funnel_max` — drive the moved-to-top funnel.
- `urgency_counts`, `status_counts`, `suite_counts`, `suite_breakdown`, `suite_colors` — drive urgency cards + By Suite pills + filter bar.

The underlying DB queries that compute the removed variables are LEFT IN PLACE — per the lane constraint, only the context dict was trimmed. O3 owns the DB query layer; reaping unused queries is their call, not O1's.

---

## Default status filter

Already defaults to `All` and was kept that way. The status select renders `<option value="">All</option>` as the first option, and the route handler defaults `status=None` which applies no `RexProduct.status==...` filter. `TestClient` smoke test confirmed: the products table renders 50 rows (default per_page) on a clean `/operations/pipeline` GET with no query params. No `selected` attribute lands on any inner status option when `filter_status` is empty.

---

## Column order — final

Left → right, exactly as specified:

| # | Column | Sort key | Source |
|---|--------|----------|--------|
| 1 | Trust | `trust` | `RexProduct.trust` (truncated to 30 chars in cell) |
| 2 | Suite | `suite` | `RexProduct.product_suite` (rendered as colored `.suite-pill`) |
| 3 | Ticker | `ticker` | `RexProduct.ticker` (linked to `/funds/<TICKER>`) |
| 4 | Fund Name | `name` | `RexProduct.name` (truncated to 60 chars) |
| 5 | Underlier | `underlier` | `RexProduct.underlier` |
| 6 | Status | `status` | `RexProduct.status` (admin: dropdown; viewer: pill) |
| 7 | Days in Stage | `days_in_stage` | Derived in route handler (max of dates → today) |
| 8 | Initial Filing Date | `initial_filing_date` | `RexProduct.initial_filing_date` |
| 9 | Latest Filing Date | `latest_filing_date` | Derived: `max(initial_filing_date, official_listed_date, target_listing_date)` |
| 10 | Effective Date | `estimated_effective_date` | `RexProduct.estimated_effective_date` |
| 11 | Latest Prospectus | (unsortable) | `RexProduct.latest_form` ∈ `{'485APOS','485BPOS','497'}` → link to `latest_prospectus_link`; otherwise em-dash |
| 12 | Inception/Target Date | `target_listing_date` | If status == `Listed` → `official_listed_date`; else `target_listing_date` (yellow cell when not Listed) |

### Latest Prospectus column logic

```jinja
{% set lp_form = p.latest_form or '' %}
{% set lp_is_pros = lp_form in ('485APOS', '485BPOS', '497') %}
{% if lp_is_pros and p.latest_prospectus_link %}
  <a href="..." class="prospect-link">{{ lp_form }} · {{ p.initial_filing_date }}</a>
{% elif lp_is_pros %}
  <span>{{ lp_form }}</span>
{% else %}
  <span style="color:#cbd5e1;">---</span>
{% endif %}
```

Renders the form code plus the initial filing date as the accession proxy (RexProduct doesn't store the accession number on the row — `latest_prospectus_link` is the SEC URL, which itself contains the accession). Smoke test on the dev DB: 89 of 100 sampled rows surface a clickable prospectus link; the remaining 11 are 485BXT / ETN / ICAV / S-1 / NULL forms and correctly show em-dash.

### Inception/Target Date yellow rule

```jinja
{% set is_listed = (p.status == 'Listed') %}
{% set inception_date = p.official_listed_date if is_listed else p.target_listing_date %}
...
<td ... {% if not is_listed and inception_date %}style="background:#fef9c3; color:#854d0e; font-weight:600;" title="Projected / target inception — not yet Listed"{% endif %}>
  {{ inception_date.strftime('%Y-%m-%d') if inception_date else '---' }}
</td>
```

Yellow fires only when `status != 'Listed'` AND there IS a date to show. Smoke tested: 10 rows in dev DB match (CBAL, CTEN, BTCA, etc., all `Filed` with `target_listing_date=2026-04-01`+); `/operations/pipeline?status=Listed` page has 0 yellow cells; `/operations/pipeline?status=Filed` page contains the yellow style.

### Suppressed columns (vs. previous layout)

The old "Listed" date column and "Latest Form" column are no longer rendered. Their data still ships on the row (`official_listed_date`, `latest_form`) — the former is folded into the new "Inception/Target Date" column (used when status=Listed), the latter is folded into the new "Latest Prospectus" column (used when the form is a prospectus type).

---

## Sort behaviour

- All 11 SQL-backed columns sort server-side via the existing `sort_map` (extended with `trust`, `underlier`, `target_listing_date`, `latest_filing_date`).
- `days_in_stage` continues to sort in-Python within the slice (existing behaviour).
- `latest_filing_date` SQL-orders by `initial_filing_date` as a proxy, then re-sorts the page in Python on the derived max-date value.
- `Latest Prospectus` is intentionally NOT sortable (it's a partial-link column; sorting by form code adds noise without value).

---

## Risk notes

- The DB queries that produce `filings_last_7d` / `launches_last_30d` / `effectives_next_30d` / `effectives_next_90d` / `recent_activity` etc. still run on every page load even though no template consumes them. This is by design (lane constraint: O3 owns DB queries). Suggest follow-up ticket for O3 to reap these queries in a later cleanup.
- The `toggleSection()` JS helper at the bottom of the template (lines ~845+) is now dead code (no caller). Left in place — harmless and would be needed if Quick Stats / Recent Activity are ever reinstated.
- The dev DB at `C:/Projects/rexfinhub/data/etp_tracker.db` was copied into the worktree at `C:/Projects/rexfinhub-O1/data/etp_tracker.db` for the smoke test; this file is gitignored so the copy is local-only.
