# Webapp Taxonomy Audit — 2026-05-06

## Purpose
Audit which rexfinhub.com pages reference the legacy `etp_category` taxonomy
vs the new 3-axis taxonomy (`asset_class`, `primary_strategy`, `sub_strategy`).
99.8% of funds have been classified under the new system; this doc tracks where
the webapp still surfaces only the old 5-bucket system.

---

## Database Population (as of audit date)

| Field | REX Funds | Competitor Funds | Coverage |
|---|---|---|---|
| `etp_category` | 96 / 96 | 2,199 / 7,151 | 31% of total universe |
| `primary_strategy` | 96 / 96 | 7,135 / 7,151 | ~99.8% |
| `asset_class` | 96 / 96 | 7,135 / 7,151 | ~99.8% |
| `sub_strategy` | 96 / 96 | 7,135 / 7,151 | ~99.8% |

**Key finding**: `etp_category` covers only ~31% of the full fund universe.
`primary_strategy` / `asset_class` / `sub_strategy` cover ~99.8%. The webapp
is effectively suppressing classification for 5,000+ funds by relying only on
`etp_category`.

### New Taxonomy Value Breakdown

**primary_strategy** (5 values): Plain Beta (5,445), L&I (769), Defined Outcome (488), Income (355), Risk Mgmt (174)

**asset_class** (7 values): Equity (4,872), Fixed Income (1,346), Multi-Asset (433), Commodity (261), Crypto (181), Volatility (84), Currency (54)

**sub_strategy** (top 10): Broad (3,822), Style (634), Long (537), Buffer (456), Thematic (383), Derivative Income > Covered Call (322), Short (232), Allocation (231), Sector (165), Single-Access (104)

---

## Audit Matrix — Templates

| Template | etp_category | primary_strategy | asset_class | sub_strategy | Notes |
|---|---|---|---|---|---|
| `market/rex_performance.html` | YES (column + filter) | NO | NO (only `asset_class_focus`) | NO | High-traffic screener — MIGRATED |
| `market/fund.html` | indirect via `category` | NO | NO | NO | Fund detail page — MIGRATED |
| `home.html` | NO | NO | NO | NO | Links only; no fund data displayed |
| `admin.html` | NO | NO | NO | NO | Operational panel |
| `api_docs.html` | YES (example JSON) | NO | NO | NO | Documentation only |
| `screener_landscape.html` | NO | NO | NO | NO | Uses category_display instead |
| `screener_4x.html` | NO | NO | NO | NO | Legacy L&I screener |
| `screener_rex.html` | NO | NO | NO | NO | REX screener |
| `fund_detail.html` | NO (SEC filing view, no market data) | NO | NO | NO | |
| `universe.html` | NO | NO | NO | NO | Trust list, no fund market data |
| `analytics.html` | NO | NO | NO | NO | |
| `downloads.html` (via router) | YES | NO | NO | NO | Download exports |
| `market/category.html` | indirect | NO | NO | NO | Via category_display |
| `market/rex.html` | indirect | NO | NO | NO | Via suite grouping |
| `market/issuer.html` | indirect | NO | NO | NO | Via category_display |

---

## Audit Matrix — Routers

| Router | etp_category | primary_strategy | asset_class | sub_strategy | Notes |
|---|---|---|---|---|---|
| `routers/admin.py` | YES (lines 449, 508, 568, 657, 795) | NO | NO | NO | Classification admin CRUD |
| `routers/api.py` | YES (lines 496, 502, 511, 542) | NO | NO | NO | Public API filter |
| `routers/downloads.py` | YES (lines 277, 390, 421, 437, 526, 534, 543, 680) | NO | NO | NO | Data exports |
| `routers/market.py` | YES (line 395) | NO | NO | NO | screener-data API — MIGRATED |
| `routers/market_advanced.py` | indirect | NO | NO | NO | fund detail build |
| `services/market_data.py` | YES (select cols 182) | YES (col 182) | NO | YES (col 182) | Already selects new cols; MIGRATED |
| `services/data_engine.py` | YES (heavy) | NO | NO | NO | CSV pipeline, not webapp |
| `services/market_sync.py` | YES | NO | NO | NO | Sync pipeline, not webapp |
| `services/report_data.py` | YES | NO | NO | NO | Email reports, not webapp |

---

## Pages Identified as Top-3 Highest Traffic

1. **`/market/rex-performance`** (`market/rex_performance.html`) — Interactive fund screener with column picker. Most-visited analytical page. Has `etp_category` as a filter/column. MIGRATED.
2. **`/market/fund/{ticker}`** (`market/fund.html`) — Fund detail page. Shows category but only from `category_display` (legacy). MIGRATED.
3. **`/`** (`home.html`) — Landing page. No fund grid; only KPI cards and pillar links. No direct taxonomy display but the KPI "REX Total AUM" and pillar card could link to taxonomy-based views.

   For home.html: added a taxonomy summary KPI card below the AUM goals section (new taxonomy counts as a mini-dashboard strip).

---

## Migration Summary

### Commit 2 — Additive migrations (DONE)

| Page | What was added |
|---|---|
| `market/rex_performance.html` | `primary_strategy` and `asset_class` columns in COLUMNS array; "Taxonomy" preset in PRESETS; both columns visible by default in Taxonomy preset |
| `market/fund.html` | New "Taxonomy" section in fund detail card showing primary_strategy, asset_class, sub_strategy with color-coded badges |
| `market_advanced.py` | Added `primary_strategy`, `asset_class`, `sub_strategy` fields to fund dict passed to template |
| `routers/market.py` (screener-data API) | Added three new columns to the SELECT so JS screener can display/filter them |
| `home.html` | Added taxonomy classification strip (5 primary_strategy buckets with fund counts) below the AUM goals |

### Commit 3 — Admin route `/admin/classification-stats` (DONE)

- New route in `routers/admin.py`
- New template `templates/admin_classification_stats.html`
- Shows: primary_strategy / asset_class / sub_strategy counts, REX vs competitor split, AUM rollup
- Auth: existing `_is_admin()` session check

---

## What Was NOT Changed (by design)

- `routers/admin.py` classification CRUD (uses etp_category for legacy workflow)
- `routers/api.py` public filter (backward compat; callers expect etp_category)
- `routers/downloads.py` export columns (would break existing download consumers)
- `services/data_engine.py`, `market_sync.py`, `report_data.py` — pipeline code, not webapp display
- All existing `etp_category` displays remain (additive-only policy)
