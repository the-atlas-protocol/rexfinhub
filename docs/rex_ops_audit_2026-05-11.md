# REX Ops Audit + Redesign Proposal — 2026-05-11

Scope: `/operations/pipeline` (Pipeline) and `/operations/products` (Products / "Capital Markets"). Read-only investigation. No code changed.

---

## 1. Current State Inventory

### 1.1 Pipeline page (`/operations/pipeline`)

| Aspect | Value |
|---|---|
| Router | `webapp/routers/pipeline_calendar.py:_pipeline_products_impl` (lines 187-429) |
| Template | `webapp/templates/pipeline_products.html` (635 lines) |
| Data source | `rex_products` table — 702 rows |
| Filter applied | `_rex_only_filter` (lines 138-175): name ILIKE "REX %"/"T-REX "/"REX-OSPREY"/"MicroSectors" + trust ILIKE "%REX%", minus Tuttle/Defiance/Osprey Bitcoin/Hedgeye/etc. |
| Container width | `.page-wrap { max-width:1500px; }` (line 6 of template) |
| Query limit | `.limit(1000)` (router line 385) — renders all 702 rows in one HTML page |
| Pagination | **None** — single scrolling table |
| Sorting | Server-side via `?sort=` + client JS via `sortTable()` |

**Row counts by status** (rex-only filter):

| Status | Count | Notes |
|---|---:|---|
| Awaiting Effective | 327 | Mostly 485APOS already in 75-day window |
| Filed | 273 | Pre-effective |
| Listed | 85 | Live + matches master tracker "Listed" count |
| Delisted | 11 | Cold — should be hidden by default |
| Research | 6 | |
| **Total** | **702** | |

The other 9 lifecycle statuses (Counsel Review, Pending Board, Board Approved, etc.) currently have **0 rows in DB** despite being in `VALID_STATUSES`. The status enum was extended to 15 values but data was never backfilled.

**Counts by suite**: T-REX 480, Crypto 59, Growth & Income 49, IncomeMax 48, Thematic 29, MicroSectors ETN 21, Premium Income 14, Autocallable 1, T-Bill 1.

**Field completeness** (% populated of 702):

| Column | Populated | % | Used in current table? |
|---|---:|---:|---|
| name | 702 | 100% | Y |
| status | 702 | 100% | Y |
| product_suite | 702 | 100% | Y |
| initial_filing_date | 696 | 99% | Y |
| latest_form | 696 | 99% | Y |
| latest_prospectus_link | 675 | 96% | Y |
| series_id / class_contract_id | 664 | 95% | N |
| estimated_effective_date | 584 | 83% | Y |
| cik | 451 | 64% | N |
| underlier | 364 | 52% | Y |
| ticker | 309 | 44% | Y |
| official_listed_date | 90 | 13% | Y |
| target_listing_date | 74 | 11% | Y (column "In") |
| seed_date | 74 | 11% | N |
| exchange / fund_admin | 59 | 8% | N |
| mgt_fee | 59 | 8% | N |
| lmm | 56 | 8% | N |
| cu_size | 54 | 8% | N |
| tracking_index | 53 | 8% | N |
| starting_nav | 21 | 3% | N |
| **direction** | **0** | **0%** | N (column never wired) |
| **notes** | **0** | **0%** | N |

Current visible columns (10): Ticker · Name · Suite · Status · Underlier · Filed · Est. Effective · In (days) · Listed · Form. Three KPI grids above table (Lifecycle, Activity, Next Launches card), six urgency pills, four lifecycle-stage filter pills, then table + collapsed suite breakdown.

### 1.2 Products page (`/operations/products`)

| Aspect | Value |
|---|---|
| Router | `webapp/routers/capm.py:_capm_index_impl` (lines 48-137) |
| Template | `webapp/templates/capm.html` (401 lines) |
| Data source | `capm_products` + `capm_trust_aps` tables |
| Container width | `.page-wrap { max-width:1500px; }` (line 6 of template) |
| **capm_products rows in DB** | **0** |
| **capm_trust_aps rows in DB** | **0** |
| Tabs | "Products" (suite tabs T-REX/REX/REX-OSPREY/BMO) + "Trust & APs" |
| Import script | `scripts/import_capm.py` — reads `~/Downloads/Capital Markets Product List .xlsx` |

The page renders fine; the table just hits the `{% else %}` empty-state branch ("No products found · Run the import script to populate data."). **Source file exists** at `C:/Users/RyuEl-Asmar/Downloads/Capital Markets Product List .xlsx` — import has just never been run on this DB.

`VALID_SUITES` in `capm.py:36` = `["T-REX", "REX", "REX-OSPREY", "BMO"]`. The page hard-codes a `T-REX` BMO `REX-OSPREY` model that is **different** from the pipeline's 9-suite taxonomy. Two schemas, one product line.

---

## 2. Specific UX / Data Problems

| # | Severity | Problem |
|---|:-:|---|
| P1 | **HIGH** | Pipeline page dumps all 702 rows in a single scrolling table. T-REX (480) drowns out everything else. No pagination; no row grouping. |
| P2 | **HIGH** | Products page is empty (`capm_products` is 0 rows) so the whole pillar appears broken. Import script exists, source xlsx is at `~/Downloads/`, but no scheduled job populates it. |
| P3 | **HIGH** | Lifecycle enum was widened from 6 → 15 statuses (`Counsel Review`, `Pending Board`, `Board Approved`, etc.) but **no rows have any of the 9 new statuses populated**. The fancy color-coded pills are decoration over empty buckets. |
| P4 | MED | 15 of 21 `rex_products` columns (lmm, exchange, mgt_fee, fund_admin, cu_size, starting_nav, tracking_index, cik, series_id, class_contract_id, direction, notes, seed_date, target_listing_date) are either ≤13% populated or **not surfaced in the table at all**. Underutilized schema. |
| P5 | MED | No status-change history table. The calendar's "status_changes" feature scrapes `updated_at` (any field touch), so it conflates a typo fix with a board approval. |
| P6 | MED | "Suite" taxonomies disagree across pages. Pipeline = 9 suites (T-REX, Premium Income, Growth & Income, IncomeMax, Crypto, Thematic, Autocallable, T-Bill, MicroSectors ETN). Products = 4 (T-REX, REX, REX-OSPREY, BMO). Same products, two truths. |
| P7 | MED | The "Capital Markets" name is internal jargon. PM team thinks "Products" — already the menu label — but the page header still says "Capital Markets". |
| P8 | LOW | Cold-state rows (Listed > 1 year, Delisted) are not visually demoted — they sort to the top by status alphabetically ("Awaiting…" sorts before "Listed"), so the table opens on 327 stale rows. |
| P9 | LOW | Filter form `<select>` controls (status, suite) wrap onto two rows and look like leftover scaffolding alongside the prettier pill-style "Quick" + "Status" rows below them. |
| P10 | LOW | `urgency_counts.overdue` selects on `target_listing_date < today` but only 74 rows have a target listing date — so it materially undercounts overdue items. Should fall back to `estimated_effective_date < today AND status NOT IN ('Listed','Delisted')`. |
| P11 | LOW | Suite filter dropdown shows "Autocallable (1)" and "T-Bill (1)" — these are individual products, not suites. They've been mis-classified or the suite list is over-granular. |
| P12 | LOW | Page width `max-width:1500px` wastes the right 20–25% of a 1920px monitor. With 10 columns it could be using the full viewport. |

---

## 3. Master Tracker Gaps — What We Should Pull In

Master xlsm: `C:/Users/RyuEl-Asmar/REX Financial LLC/.../REX Master Product Development Tracker.xlsm`.

**`Pipeline Update` sheet (454 rows × 13 cols)** — 1:1 overlap with our model. Useful field: `New?` flag (=`NEW` on 81 rows, NULL on 371) → drives the "what's new this cycle" feed.

**`Pipeline` sheet (507 rows × 42 substantive cols)** — header at row 10, data row 11+. Columns we **do not** persist today:

| Master column | Where it goes | Why we want it |
|---|---|---|
| Target Listing (col 9) | Already in model, mostly NULL | Backfill the 11% → 100% from xlsm so "overdue" KPI is correct |
| Seed Date (col 10) | Already in model, 11% pop | Backfill |
| Fiscal Year End (col 20) | Already added in #93 | Backfill |
| **DRP Index (col 23)** | NEW field | Distribution reinvestment plan reference index — flag for any income suite |
| **Auditors/TAX (col 24)** | NEW field | Tax pillar admin — Cohen, Tait Weller, etc. |
| **33 or '40 Act (col 25)** | NEW field | Regulatory regime — drives prospectus form rules |
| **RIC vs C-Corp (col 26)** | NEW field | Tax election — drives whether 1099 vs K-1 |
| **Cayman [T/F] (col 27)** | NEW field | Offshore master-feeder for crypto products |
| Fund accounting/custody (col 28) | Already in model under `custodian` (capm) | Cross-pop into rex_products |
| **Available in Japan (col 30)** | NEW field | Distribution-channel flag |
| **Target Distr-Frequency (col 31)** | NEW field | Monthly/Weekly/Quarterly — drives calendar projection |
| **Distr Target (col 32)** | NEW field | Target yield — used in autocall + premium income marketing |
| **Adviser (col 33)** | NEW field | REX Advisers / Vident / Tidal |
| **Sub-Adviser (col 34)** | NEW field | Where sub-advised |
| **Variable Fee (col 37)** | NEW field | For unitary-fee disclosures |
| **Cut Off (col 38)** | NEW field (time-of-day for AP order) | Capital markets ops |
| **Starting NAV (col 39)** | Already in model, 3% pop | Backfill |
| **AP (col 40)** | NEW field | Authorized participant — also in capm_trust_aps but per-product specifically |
| **ISIN (col 41)** | NEW field | Foreign distribution identifier |
| **CUSIP (col 42)** | NEW field | Settlement identifier — every fund needs one |

**`Launch Calendar` sheet (91 rows × 74 cols)** — grid by year × month, holding planned launch dates. Already drives the `target_listing_date` field but only at 11% population. Worth a one-time backfill script.

**`Pre-IPO T-REX` sheet (14 rows)** — 6 products whose underliers are private companies (Discord, Anduril, Anthropic, Figure AI, SpaceX, Toss, SK Hynix). These need a "Pre-IPO underlier" flag in the UI so PMs can see which approvals are blocked on the underlying company's IPO.

**Status data delta**:

| Status | DB count | Master `Pipeline` sheet | Comment |
|---|---:|---:|---|
| Listed | 85 | 61 | DB ahead — recent launches not in master, or master not updated |
| Filed | 273 | 412 | DB way behind — master tracks ALL 485A filings |
| Awaiting Effective | 327 | 0 | DB invented this — master keeps it as `Filed` |
| Delisted | 11 | 14 | Off by 3 |
| Target List | 0 | 4 | Sync gap |
| Research | 6 | 6 | Match |

The DB's "Awaiting Effective" is **synthetic** — generated by date math. Master simply tags everything pre-effective as `Filed`. We should keep the synthetic split for display but acknowledge the master truth.

---

## 4. Redesign Spec — PM-Lens Pipeline Page

Audience: PM team needs (a) where is the bottleneck, (b) what's overdue, (c) what changed this week, (d) edit-without-leaving-page, (e) drill on a single fund.

### 4.1 Layout sketch (top → bottom)

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  REX Operations — Pipeline                          [today] May 11, 2026 ▾   │
│  Lifecycle of REX-branded products from research to listing.                 │
├──────────────────────────────────────────────────────────────────────────────┤
│  FUNNEL  (collapsible, default open)                                         │
│  ┌──────┬──────┬──────┬──────┬──────┬──────┬──────┐                          │
│  │Res 6 │Tgt 0 │Cnsl 0│Brd 0 │Filed │ Awtg │Listd │  ←  click any bar = filter│
│  │  ▌   │  ▌   │  ▌   │  ▌   │ 273  │ 327  │  85  │                          │
│  └──────┴──────┴──────┴──────┴──────┴──────┴──────┘                          │
│                                                                              │
│  URGENCY CARDS  (above-the-fold action queue, 4 cards)                       │
│  ┌────────────────┬────────────────┬────────────────┬────────────────┐       │
│  │ URGENT (<14d)  │ THIS MONTH (30)│ OVERDUE        │ NEW THIS WEEK  │       │
│  │   N funds   ▶  │   N funds   ▶  │   N funds   ▶  │   N funds   ▶  │       │
│  └────────────────┴────────────────┴────────────────┴────────────────┘       │
│                                                                              │
│  RECENT ACTIVITY (last 14 days)               [collapsible, default open]    │
│  • May 9  ATCL  Filed → Listed       (suite color bar)                       │
│  • May 7  TBLP  Awaiting → Listed                                            │
│  • May 5  DRNZ  Estimated effective updated 2026-06-10 → 2026-06-24          │
│  • ... (limit 20, link "View all")                                           │
├──────────────────────────────────────────────────────────────────────────────┤
│  STICKY FILTER BAR                                                           │
│  [search ticker/name]  [Status ▾]  [Suite ▾]  [Urgency ▾]  [Show cold ☐]    │
│  [+ Add product]                            [Export xlsx]                    │
├──────────────────────────────────────────────────────────────────────────────┤
│  TABLE — 50 rows/page, default sort: status urgency desc                     │
│  ┌─────┬──────────────────────┬──────────┬────────────┬──────┬─────────┬───┐ │
│  │TKR  │ Name                 │ Suite    │ Status     │ Days │ Est Eff │ ▾ │ │
│  │ATCL │ REX Autocall Inc ETF │ Autocall │ Listed     │  -   │ 2025-12 │ ▸ │ │
│  └─────┴──────────────────────┴──────────┴────────────┴──────┴─────────┴───┘ │
│  (click row arrow → expand inline: full RexProduct fields + recent filings + │
│   notes + change-log; ESC or click again to collapse)                        │
│                                                                              │
│  Showing 50 of 702  •  ‹ prev  Page 1 2 3 ... 15  next ›                     │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 4.2 Section specs

| Section | Backed by | Notes |
|---|---|---|
| **Funnel bars** | `GROUP BY status` on rex_products | Each bar is a `<a href="?status=X">` link; show counts only (skip empty buckets entirely so the 9 unused stages collapse). |
| **Urgency cards** | Existing `urgency_counts` dict | Reuse current logic; rename "Upcoming (60d)" → "This Month (30d)" to match how PMs think. Cards link to filtered table. |
| **Recent activity** | NEW: `rex_product_status_history` table (`product_id, from_status, to_status, changed_field, old_value, new_value, changed_at, changed_by`). Phase-2 work — Phase 1 falls back to `WHERE updated_at >= today-14d ORDER BY updated_at DESC LIMIT 20` with a disclaimer "based on last-edit timestamp". |
| **Filter bar** | Existing GET params | Make sticky on scroll (`position:sticky; top:0`). Add "Show cold (Delisted + Listed > 365d)" checkbox — default OFF. |
| **Table columns (default)** | rex_products | Ticker · Name · Suite (pill) · Status (pill) · Days-in-stage · Est Eff · Target List · Latest Form (link) |
| **Days-in-stage** | DERIVED `(today - updated_at).days` clamped to a "stuck > 30d" red badge | Replaces current "In" column which is days-until-effective |
| **Row expand** | XHR fetches `/operations/pipeline/{id}/detail` → returns HTML partial | Shows full schema + filings history + notes editor. No page reload. |
| **Pagination** | LIMIT/OFFSET, default 50 | URL state: `?page=2&per=50` |
| **Hide-by-default** | Server-side filter: `WHERE NOT (status='Delisted' OR (status='Listed' AND official_listed_date < today-365d))` unless `?show_cold=1` | Cuts visible rows from 702 → ~~~ 530 |
| **Inline edit** | Existing `/admin/products/update/{id}` endpoint | Already supports per-field PATCH — keep as is |
| **Export xlsx** | NEW: `/operations/pipeline/export.xlsx?<filters>` | Currently no xlsx export; CSV-only via distributions endpoint |

### 4.3 Schema additions (Phase 2)

```sql
-- New columns on rex_products (additive, all nullable):
ALTER TABLE rex_products ADD COLUMN cusip VARCHAR(12);
ALTER TABLE rex_products ADD COLUMN isin VARCHAR(20);
ALTER TABLE rex_products ADD COLUMN auditor VARCHAR(100);
ALTER TABLE rex_products ADD COLUMN act_type VARCHAR(10);          -- '33 or '40
ALTER TABLE rex_products ADD COLUMN ric_or_ccorp VARCHAR(10);
ALTER TABLE rex_products ADD COLUMN cayman BOOLEAN;
ALTER TABLE rex_products ADD COLUMN japan_available BOOLEAN;
ALTER TABLE rex_products ADD COLUMN distribution_frequency VARCHAR(20);
ALTER TABLE rex_products ADD COLUMN distribution_target VARCHAR(20);
ALTER TABLE rex_products ADD COLUMN adviser VARCHAR(100);
ALTER TABLE rex_products ADD COLUMN sub_adviser VARCHAR(100);
ALTER TABLE rex_products ADD COLUMN variable_fee VARCHAR(50);
ALTER TABLE rex_products ADD COLUMN cut_off VARCHAR(20);
ALTER TABLE rex_products ADD COLUMN ap_name VARCHAR(200);
ALTER TABLE rex_products ADD COLUMN drp_index VARCHAR(200);
ALTER TABLE rex_products ADD COLUMN is_preipo BOOLEAN DEFAULT 0;   -- flag from Pre-IPO sheet

CREATE TABLE rex_product_status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id INTEGER NOT NULL REFERENCES rex_products(id),
    from_status VARCHAR(30),
    to_status VARCHAR(30),
    changed_field VARCHAR(50),
    old_value TEXT,
    new_value TEXT,
    changed_at DATETIME NOT NULL,
    changed_by VARCHAR(100)
);
```

Backfill script reads master `Pipeline` sheet and updates by `series_id` join.

---

## 5. Products Page Recovery Plan

### 5.1 Data source

| Option | Status | Recommend |
|---|---|---|
| A. Run `python scripts/import_capm.py` once, cron weekly | Source file exists at `~/Downloads/Capital Markets Product List .xlsx` | **YES — Phase 1** |
| B. Auto-derive `capm_products` from `rex_products` for Listed funds | Possible — most fields overlap | Phase 2 once new columns are added (section 4.3); then `capm_products` becomes a VIEW, not a table. |
| C. Manual admin entry only | Already supported by `_capm_update_impl` | Fallback for one-offs |

**Phase 1 action**: run `python scripts/import_capm.py` against the existing xlsx; verify 85+ rows (matching Listed) populate. The import script already handles 4 suite sheets + ALL PRODUCTS classification sheet.

### 5.2 "Capital Markets" label cleanup

| File | Line | Change |
|---|---:|---|
| `webapp/templates/capm.html` | 2 | `{% block title %}Capital Markets — REX Financial{% endblock %}` → `{% block title %}Products — REX Financial{% endblock %}` |
| `webapp/templates/capm.html` | 129 | `<h1>Capital Markets</h1>` → `<h1>Products</h1>` |
| `webapp/templates/home.html` | 765 | `<!-- Capital Markets -->` (comment, optional rename) |
| `webapp/templates/home.html` | 770 | `Capital Markets` → `Products` (pillar card title) |

Note: `models.py:1011, 1035` and `routers/capm.py:1, 55` use "Capital Markets" in docstrings only — not user-visible, but rename for clarity if doing a sweep. The class names `CapMProduct` / `CapMTrustAP` stay (renaming is a migration). Tab labels "Trust & APs" stay — that's an industry term, not internal jargon.

### 5.3 Suite taxonomy reconciliation

`VALID_SUITES` in capm.py:36 = `["T-REX", "REX", "REX-OSPREY", "BMO"]` is a **brand-family** split (how prospectuses are filed). Pipeline's `SUITE_COLORS` (9 suites) is a **strategy** split (what the fund does). Both have valid uses, but the Products page should let users filter on either. Recommend: show brand-family tabs at top + strategy filter dropdown.

---

## 6. Width Refactor Proposal

### 6.1 Current state

| File | Container | Width |
|---|---|---:|
| `webapp/static/css/style.css:54,143` | `.container` (global, from base.html) | 1280px |
| `webapp/templates/pipeline_products.html:6` | `.page-wrap` | 1500px |
| `webapp/templates/capm.html:6` | `.page-wrap` | 1500px |
| `webapp/templates/admin_products.html:6` | `.page-wrap` | 1500px |
| `webapp/templates/pipeline_calendar.html:6` | `.page-wrap` | 1320px |
| `webapp/templates/pipeline_summary.html:6` | `.page-wrap` | 1280px |
| `webapp/templates/admin_reports_preview.html:6` | `.page-wrap` | 1280px |
| `webapp/templates/downloads.html:6` | `.export-page` | 1100px |
| `webapp/templates/filings_hub.html:9` | inline | 1280px |
| `webapp/templates/home.html:13` | inline | 1280px |
| `webapp/templates/market/calendar.html:7` | inline | 1400px |
| `webapp/static/css/style.css:1260, 1313` | (component sub-widths) | 1280px |

11 templates + 1 CSS variable in scope.

### 6.2 Proposed single rule

Add to `webapp/static/css/style.css` after line 143:

```css
/* Full-width pages — for data-dense screens (tables, calendars, dashboards).
   Honors the system gutter (32px = 2 × var(--sp-6)) and caps at 1900px so
   line-length stays readable on ultrawide monitors. */
.full-width-page {
  max-width: min(100% - 32px, 1900px);
  margin: 0 auto;
  padding: var(--sp-5) var(--sp-6);
}
```

Optionally, change `--container-width` at line 54:
```css
--container-width: min(100% - 32px, 1900px);   /* was: 1280px */
```

This single-line change widens every `.container` site-wide; combined with `.full-width-page` for the bespoke `.page-wrap` blocks, the audit reduces to 11 find-and-replaces.

### 6.3 Find / replace plan

For each file in 6.1 that uses `.page-wrap`, change the class to `full-width-page` and **delete** the inline `max-width:Xpx; margin:0 auto; padding:20px` rule from `<style>`. Net: ~11 edits, all in `<style>` blocks at the top of templates. Zero template body changes.

---

## 7. Priority-Ordered Execution Plan

| # | Phase | Effort | Task | Files |
|---|---|---|---|---|
| 1 | P0 | 5 min | Run `python scripts/import_capm.py` to populate `capm_products` and `capm_trust_aps`. | (no code change) |
| 2 | P0 | 5 min | Rename "Capital Markets" → "Products" in user-visible labels. | `webapp/templates/capm.html` (lines 2, 129); `webapp/templates/home.html` (line 770) |
| 3 | P0 | 30 min | Width refactor: add `.full-width-page` CSS; convert 11 templates. | `webapp/static/css/style.css`; 11 template `<style>` blocks |
| 4 | P1 | 2 hr | Pipeline: server-side pagination (50/page), hide cold-state by default. | `webapp/routers/pipeline_calendar.py` (`_pipeline_products_impl`, add LIMIT/OFFSET + `show_cold` param); `webapp/templates/pipeline_products.html` (pagination UI) |
| 5 | P1 | 2 hr | Pipeline: replace 5 KPI cards + Next Launches block with funnel-bar chart + 4 urgency cards + Recent Activity feed. | `webapp/templates/pipeline_products.html` (lines 175-207 replaced) |
| 6 | P1 | 1 hr | Pipeline: fix overdue counter to use estimated_effective_date fallback when target_listing_date is null. | `webapp/routers/pipeline_calendar.py` (lines 278-289) |
| 7 | P1 | 2 hr | Pipeline: row-expand inline detail view (no page reload). | `webapp/templates/pipeline_products.html` + new `pipeline_products_detail.html` partial; new GET route `/operations/pipeline/{id}/detail` |
| 8 | P1 | 30 min | Pipeline: sticky filter bar on scroll. | `webapp/templates/pipeline_products.html` (`.filter-panel` CSS) |
| 9 | P2 | 4 hr | Master tracker backfill: import xlsm into rex_products by series_id; populate cusip/isin/auditor/act_type/ric_or_ccorp/cayman/japan_available/distribution_frequency/distribution_target/adviser/sub_adviser/ap_name + backfill seed_date, target_listing_date, starting_nav. | New `scripts/import_master_tracker.py` + 15 ALTER TABLE migrations |
| 10 | P2 | 2 hr | Schema: create `rex_product_status_history` table; wire admin update endpoint to insert audit rows. | `webapp/models.py`; `webapp/routers/admin.py` (admin products update); new Alembic migration |
| 11 | P2 | 1 hr | Calendar: switch `status_changes` source from `updated_at` to the new history table. | `webapp/routers/pipeline_calendar.py` (`_render_month`, lines 633-658) |
| 12 | P2 | 2 hr | Pipeline: real funnel-bar chart with click-to-filter on the new (currently empty) lifecycle stages, once backfill populates them. | `webapp/templates/pipeline_products.html` (depends on #9) |
| 13 | P2 | 1 hr | Pre-IPO underlier flag column + filter. | `webapp/models.py` (`is_preipo`); `webapp/templates/pipeline_products.html` |
| 14 | P3 | 4 hr | Products page: derive `capm_products` as a SQL view over `rex_products` (now that schemas overlap); deprecate the import script in favor of live data. | `webapp/models.py`; new DB view |
| 15 | P3 | 2 hr | Suite taxonomy reconciliation: add `brand_family` (T-REX / REX / REX-OSPREY / BMO) AND keep `product_suite` (strategy). Surface both filters on Products page. | `webapp/models.py`; both templates |
| 16 | P3 | 1 hr | Calendar page (Priority #3): apply same width refactor + filter UX consistency. | `webapp/templates/pipeline_calendar.html` |

---

## Appendix A — File path index

| Pillar | Path | Purpose |
|---|---|---|
| Pipeline router | `C:/Projects/rexfinhub/webapp/routers/pipeline_calendar.py` | `_pipeline_products_impl`, calendar handlers, redirects |
| Pipeline template | `C:/Projects/rexfinhub/webapp/templates/pipeline_products.html` | 635-line page with KPIs + filters + table |
| Products router | `C:/Projects/rexfinhub/webapp/routers/capm.py` | `_capm_index_impl`, export, update |
| Products template | `C:/Projects/rexfinhub/webapp/templates/capm.html` | 401-line page with suite tabs + Trust&APs tab |
| Calendar template | `C:/Projects/rexfinhub/webapp/templates/pipeline_calendar.html` | Month calendar w/ event types |
| Models | `C:/Projects/rexfinhub/webapp/models.py` | `RexProduct` (line 900), `CapMTrustAP` (1010), `CapMProduct` (1034) |
| Import scripts | `C:/Projects/rexfinhub/scripts/import_capm.py`, `import_product_tracker.py` | Populate from xlsx |
| Global CSS | `C:/Projects/rexfinhub/webapp/static/css/style.css` | `--container-width` (line 54), `.container` (line 143) |
| Operations mount | `C:/Projects/rexfinhub/webapp/routers/operations.py` | Mounts `/operations/{pipeline,products,calendar}` |
| Master xlsm | `C:/Users/RyuEl-Asmar/REX Financial LLC/REX Financial LLC - Rex Financial LLC/Product Development/MasterFiles/MASTER Product Tracker/REX Master Product Development Tracker.xlsm` | Source of truth — 18 sheets, "Pipeline" has 42 cols |
| CapM source xlsx | `C:/Users/RyuEl-Asmar/Downloads/Capital Markets Product List .xlsx` | Drives `capm_products` |
| App DB | `C:/Projects/rexfinhub/data/etp_tracker.db` | Live SQLite — 702 rex_products, 0 capm_products |
