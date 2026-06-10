# O6 — Underlier Interactivity on /operations/pipeline

**Owner:** rexops-O6 worktree
**Date:** 2026-05-12
**Branch:** `rexops-O6-underlier`

---

## 1. Ryu's ask

> "We should be able to click especially for T-REX to see competitors.
> Maybe we click the underlier and it transports us to a list of live /
> filed products. Based off what you know of rexfinhub what should we do
> here?"

## 2. Existing surface audit

| Surface | What it shows today | Verdict for this ask |
|---|---|---|
| `/stocks/{ticker}` | Per-stock signal page: Bloomberg whitespace_v4 score + ALL ETPs covering that underlier (queried from `mkt_master_data` via `map_li_underlier` / `map_cc_underlier`). Single-flat-list, no lifecycle split. | **Partial fit.** REX vs competitor not separated; lifecycle stage (filed / effective / listed) not surfaced; **bifurcation bug present** — `WHERE UPPER(TRIM(map_li_underlier)) = :t` matches `'NVDA'` only, missing the 16 `'NVDA US'` rows (see `01_webapp_consistency.md` F1). |
| `/market/underlier?underlier=NVDA` | Bloomberg AUM aggregator for one underlier — products list with AUM / yield / flows / 12-mo trend. Has `is_rex` flag. | **Partial fit.** Useful AUM detail, but **same bifurcation bug** (F2): NVDA and NVDA US render as two distinct buckets, so a click on either misses half the rows. No lifecycle split (only live funds; filed/effective products invisible). |
| `/funds/{ticker}` | Single-fund detail page; includes a "competitors on the same underlier" sub-section. | **Wrong entry point.** Requires a specific REX fund ticker. Can't be reached from a pipeline row whose REX fund is not yet listed. |
| `/intel/head-to-head?underlying=NVDA` | 13F holdings race — issuer-by-issuer institutional ownership for products sharing an underlier. | **Wrong axis.** Measures institutional ownership, not lifecycle / launch-race timing. Useful as a follow-on link, not the primary destination. |

**Conclusion:** the four existing surfaces collectively almost answer the
question — but each one is either bifurcated (F1/F2 bug), bound to a
single fund ticker, or focused on AUM rather than lifecycle race. None
of them shows, in one screen, "who filed first on NVDA, who launched
first, what REX has filed/listed, what competitors have filed/listed."

Linking the pipeline row directly to `/market/underlier?underlier=NVDA`
would have inherited the bifurcation bug and would have given the user
an AUM-centric page rather than a race-centric page, so we declined that
shortcut.

## 3. Decision: focused race view + chip-in-name-cell

A new dedicated surface `/operations/pipeline/underlier/{underlier}` was
added that:

1. Normalizes the URL token (strips `` US`` / `` Curncy`` / `` Equity``
   suffixes) and queries `mkt_master_data` with the same normalization
   applied on the column side — so `NVDA` matches both `'NVDA'` and
   `'NVDA US'` rows. **F1/F2 bifurcation fix is encapsulated in this
   route** (the broader fix to `/stocks/{ticker}` and
   `/market/underlier` is owned by O2 / a future cleanup).
2. Splits results into REX vs competitor.
3. Renders three race columns mirroring the existing pipeline funnel
   buckets: **Filed**, **Effective**, **Listed (live)**.
4. Adds a small filing-race timeline (most recent 20 events) and a
   "filed first / listed first" headline.
5. Offers quick actions ("File 3x on X (T-REX)", "Watch for Inverse",
   "Filter pipeline by X").
6. Links out to the four existing surfaces above so the user can dig
   deeper without re-typing the underlier.

**Why a side panel, not a redirect:** an in-page side panel preserves
the pipeline row context (the user's current filters, scroll position,
status edits-in-flight). The page-mode (`?modal=0`) is still available
as a permalink for bookmarking and for Shift+click / Ctrl+click users.

### Recommendation paragraph for Ryu

The pipeline page now has a small clickable underlier chip on every row
that has one (e.g. `NVDA ›`). A normal click opens a focused REX vs
competitor race panel inline — three columns (Filed / Effective /
Listed), a "filed first / listed first" headline, and quick actions for
the missing leverage/inverse slot. Shift+click opens the full
`/operations/pipeline/underlier/<X>` page. We deliberately built a new
dedicated surface rather than re-using `/market/underlier` because that
view is (a) bifurcated (NVDA vs `NVDA US` rendered as two distinct
buckets — bug F2 from the May 2026 audit, not yet fixed) and (b)
AUM-centric rather than lifecycle-race-centric. The new route handles
the bifurcation locally so the side-panel results are correct today;
fixing `/market/underlier` itself is left to a follow-up worktree.

## 4. Integration spec

### Routes added

| Method | Path | Purpose |
|---|---|---|
| GET | `/operations/pipeline/underlier/{underlier}` | Race view, full page or modal fragment (`?modal=1`). |
| GET | `/api/operations/underlier/{underlier}.json` | JSON payload for programmatic / future-client use. |

### Files added

- `C:/Projects/rexfinhub-O6/webapp/routers/underlier_view.py`
- `C:/Projects/rexfinhub-O6/webapp/templates/operations/underlier_race.html`
- `C:/Projects/rexfinhub-O6/webapp/templates/operations/_underlier_race_body.html`

### Files edited

- `C:/Projects/rexfinhub-O6/webapp/main.py`
   — registers the new router (one new `include_router` call, additive
   only).
- `C:/Projects/rexfinhub-O6/webapp/templates/pipeline_products.html`
   — Name cell (column 1) appends a chip when `p.underlier` is set.
     The Status / Form / column-count macros are untouched. A new
     `<div id="underlierModal">` shell is appended after the Add Modal,
     and a `<script>` block adds the `openUnderlierPanel(ev, ul)`
     handler. ESC and backdrop-click both dismiss the panel.

### Column-order safety

The brief reserved column ordering to O1. We did **not** add a column.
Instead the chip is rendered inside the existing **Name cell**. All
nine `data-col-idx` filter inputs (0…8) still map cleanly to their
respective `<td>` since the cell count is unchanged.

### Status-enum safety

We did not touch the status enum. The race-bucket helper in
`underlier_view.py` reads the *current* `FILED_STATUSES` /
`EFFECTIVE_STATUSES` / `LISTED_STATUSES` lifecycle groupings, which
mirror those already declared in `pipeline_calendar.py`. If O3
introduces new enum values, the bucket helper will drop unrecognized
statuses into the `preflight` bucket (visible under "Pre-filing" in the
Filed column) — fail-soft, not fail-loud.

### Bifurcation handling — concrete

```sql
WHERE UPPER(TRIM(REPLACE(REPLACE(REPLACE(REPLACE(
        map_li_underlier, ' US',''), ' Curncy',''),
        ' Equity',''), ' Index',''))) = :k
   OR UPPER(TRIM(REPLACE(REPLACE(REPLACE(REPLACE(
        map_cc_underlier, ' US',''), ' Curncy',''),
        ' Equity',''), ' Index',''))) = :k
```

The URL token is normalized in Python (`_normalize`) to the same key
shape. Net effect: `/operations/pipeline/underlier/NVDA` matches all
17 NVDA-underlier rows in `mkt_master_data`, whereas
`/stocks/NVDA` (today) matches 1 and `/market/underlier?underlier=NVDA`
(today) matches 1 of the two buckets.

## 5. Sample links for 5 underliers

Plug these into the running app to verify the integration:

| Underlier | Page mode | Side-panel (fetched) |
|---|---|---|
| NVDA  | `/operations/pipeline/underlier/NVDA`  | `/operations/pipeline/underlier/NVDA?modal=1`  |
| TSLA  | `/operations/pipeline/underlier/TSLA`  | `/operations/pipeline/underlier/TSLA?modal=1`  |
| MSTR  | `/operations/pipeline/underlier/MSTR`  | `/operations/pipeline/underlier/MSTR?modal=1`  |
| COIN  | `/operations/pipeline/underlier/COIN`  | `/operations/pipeline/underlier/COIN?modal=1`  |
| AAPL  | `/operations/pipeline/underlier/AAPL`  | `/operations/pipeline/underlier/AAPL?modal=1`  |

JSON variant (for piping into other tools):

```
GET /api/operations/underlier/NVDA.json
```

## 6. Out of scope (deliberately)

- Fixing `/market/underlier` to merge the NVDA / NVDA US buckets.
  Belongs in a follow-up worktree (touches `market_data.py` service
  and risks breaking the existing API consumers). The new race view
  handles the bifurcation locally so the user-visible result is
  correct *today*.
- Adding a standalone Underlier column to the pipeline table. Column
  order is O1's territory. If O1 lands an Underlier column, the chip in
  the Name cell can move into it with no functional change.
- 13F-holdings race. Already exists at `/intel/head-to-head` and is
  linked from the race view footer.
- A "Watch for Inverse" backend action. The button is wired to a
  pipeline filter today; the actual workflow (file a draft 485APOS) is
  PM-side and out of scope for this 60-min slot.

## 7. Risks & follow-ups

- **`p.underlier` is sparse on the REX side** (52% populated per the
  audit — 364 of 700ish rows). Rows without an underlier render no
  chip; the column-filter behavior on the Name cell is unaffected.
- **Inception-date string parsing is best-effort.** Bloomberg ships
  `inception_date` as text; rows with non-ISO formats are skipped from
  the timeline rather than erroring.
- The current Bloomberg-side AUM is summed across both `NVDA` and
  `NVDA US` rows once de-duped. If the same fund is double-counted
  across both buckets in `mkt_master_data` (it shouldn't be, but the
  audit found row-counts of 16 + 1 = 17 distinct rows), the AUM will be
  slightly inflated. Verify on real data after the route is live.
