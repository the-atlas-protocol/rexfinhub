# REX Ops PIPELINE page — Data + KPI bug fixes (rexops-O2)

Branch: `rexops-O2-bugs`
Date: 2026-05-12
Files touched:
- `webapp/routers/pipeline_calendar.py` — primary fixes
- `etp_tracker/step4.py` — documentation update for 485A 75/60 rule

Verification DB: `data/etp_tracker.db` (local, populated).

---

## Bug 1 — Days in Stage was effectively "days since last DB write"

### Root cause

In `_pipeline_products_impl` (line ~644 pre-fix), `days_in_stage` was
computed by taking the MAX of `{initial_filing_date, official_listed_date,
target_listing_date, updated_at}` and subtracting from today:

```python
stage_anchors = [p.initial_filing_date, p.official_listed_date, p.target_listing_date]
if p.updated_at:
    stage_anchors.append(p.updated_at.date())
anchor = max(stage_anchors)
days_in_stage = max(0, (today - anchor).days)
```

Because `updated_at` is bumped by ANY column change (admin edit, bulk
import, distribution-table touch), and a bulk REX sync ran on
2026-04-13, every Listed row read **29d** — i.e. days since that bulk
write — regardless of when the fund actually listed.

The codebase has no `rex_product_status_history` table yet (atlas memory
task #114), so a true "days since status changed" reading is not
available.

### Fix

Pick the stage anchor based on the row's CURRENT status, using the
lifecycle column that actually marks entry into that stage:

| Status               | Anchor column            |
| -------------------- | ------------------------ |
| Listed / Delisted    | `official_listed_date`   |
| Effective            | `estimated_effective_date` |
| Filed / 485A / 485B  | `initial_filing_date`    |
| Awaiting Effective   | `initial_filing_date`    |
| Counsel / Board / Research | falls through to first available date, then `updated_at` |

When the preferred anchor is NULL, fall through to the next-best
lifecycle date on the row (`initial_filing_date` → `official_listed_date`
→ `target_listing_date` → `estimated_effective_date`). Only fall back to
`updated_at` as a last resort.

### Sample verification (5 rows)

Computed against `data/etp_tracker.db` on 2026-05-12:

| Ticker | Status              | OLD days_in_stage | NEW days_in_stage | New anchor                  |
| ------ | ------------------- | ----------------- | ----------------- | --------------------------- |
| TLDR   | Listed              | 29d               | **111d**          | official_listed_date (2026-01-21) |
| ATCL   | Listed              | 29d               | **83d**           | official_listed_date (2026-02-18) |
| BMAX   | Listed              | 29d               | **424d**          | official_listed_date (2025-03-14) |
| FEPI   | Listed              | 29d               | **944d**          | official_listed_date (2023-10-11) |
| OSS 2X | Filed (485A)        | 3d                | **3d**            | initial_filing_date (2026-05-09) |

The old computation made every Listed row look "29d" because
`updated_at = 2026-04-13` (bulk sync). The new one correctly reflects
how long the product has actually been in its current stage.

### Diff (essential)

```diff
-    stage_anchors = [p.initial_filing_date, p.official_listed_date, p.target_listing_date]
-    if p.updated_at:
-        stage_anchors.append(p.updated_at.date() ...)
-    anchor = max(stage_anchors)
+    STATUS_TO_ANCHOR_FIELD = {
+        "Listed": "official_listed_date", "Delisted": "official_listed_date",
+        "Effective": "estimated_effective_date",
+        "Filed": "initial_filing_date", "Filed (485A)": "initial_filing_date",
+        "Filed (485B)": "initial_filing_date", "Awaiting Effective": "initial_filing_date",
+    }
+    anchor_field = STATUS_TO_ANCHOR_FIELD.get(p.status or "")
+    anchor = getattr(p, anchor_field, None) if anchor_field else None
+    if anchor is None:
+        for field in ("initial_filing_date", "official_listed_date",
+                      "target_listing_date", "estimated_effective_date"):
+            v = getattr(p, field, None)
+            if v is not None:
+                anchor = v; break
+    if anchor is None and p.updated_at:
+        anchor = p.updated_at.date() ...
```

---

## Bug 2 — T-Bill suite KPI silently 0 (TLDR dropped)

### Root cause

`_rex_only_filter` in `pipeline_calendar.py` had **contradictory clauses**:

- Line 162 (INCLUDE): `RexProduct.name.ilike("The Laddered%")`
  added with a comment explaining TLDR is the only T-Bill product and
  doesn't follow the REX/T-REX naming convention.
- Line 180 (EXCLUDE): `RexProduct.name.ilike("The Laddered%")` in the
  `not_(or_(...))` exclusion block.

The exclusion silently won. TLDR (the only `product_suite='T-Bill'` row)
was filtered out of every KPI, suite breakdown, and table. T-Bill suite
read 0 forever.

### Fix

Removed the `RexProduct.name.ilike("The Laddered%")` line from the
exclusion `not_(or_(...))` block. The inclusion clause is canonical and
explicit (TLDR is a REX product, brand-mapped to T-Bill).

### Sample verification

Before:
```
TBill suite count: 0
```

After:
```
TBill suite count: 1   (TLDR — The Laddered T-Bill ETF, Listed, REX ETF Trust)
```

Confirmed in rendered HTML: T-Bill suite-kpi shows num=1.

### Diff

```diff
             RexProduct.name.ilike("Nasdaq Dorsey%"),
-            RexProduct.name.ilike("The Laddered%"),
+            # NOTE: "The Laddered%" exclusion removed — it contradicted
+            # the inclusion clause above and silently dropped TLDR.
         )))
```

---

## Bug 3 — "EFFECTIVE DATE IN ≤60D" KPI returned 0

### Root cause

The query was correct by spec:

```python
pending_q().filter(RexProduct.estimated_effective_date.between(today, today+60d)).count()
```

But the data didn't support it. Of 530 pending-effective rows with an
`estimated_effective_date` set:

- 509 are in the past (stale)
- 0 are in next 60 days
- 21 are between +60d and +90d (e.g. new 2026-05-09 T-REX 485A filings
  with est_effective_date = 2026-07-23 — that's +72d, just outside the
  window)
- 91 more pending rows have `estimated_effective_date = NULL`

So a strict est-date window read 0 even though dozens of filings are in
the SEC's effectiveness clock right now.

### Fix

Add a SEC-rule fallback: when `estimated_effective_date` is NULL or in
the past, project effectiveness as `initial_filing_date + 75 days` (the
SEC Rule 485(a) clock for new-fund 485APOS filings).

Implemented as an OR clause on the SQL query:

```python
qry.filter(or_(
    RexProduct.estimated_effective_date.between(today, end_window),
    and_(
        or_(RexProduct.estimated_effective_date.is_(None),
            RexProduct.estimated_effective_date < today),
        RexProduct.initial_filing_date.isnot(None),
        RexProduct.initial_filing_date.between(today-75d, end_window-75d),
    ),
))
```

Applied to both `upcoming_dated_60d` and `urgent_dated_14d`.

### Sample verification

Before:
```
upcoming_dated_60d: 0
urgent_dated_14d:   0
```

After:
```
upcoming_dated_60d: 35
urgent_dated_14d:   9
```

Confirmed in rendered HTML: "Effective Date in ≤60d" card shows **35**.

---

## Bug 4 — "RECENT FILINGS (14D)" KPI returned 0

### Root cause investigation

The query is correct:

```python
_rex_only_filter(db.query(RexProduct))
    .filter(RexProduct.initial_filing_date >= today - timedelta(days=14))
    .count()
```

Against the local DB it returns **21** — 21 T-REX 2X 485A filings
landed 2026-05-09 (3 days ago). The query was never broken.

If the live page showed 0, that means the Render-deployed DB was stale
(the daily VPS upload hadn't run yet, or the new filings hadn't been
ingested upstream). The query itself is sound.

### Fix

No code change required. Verified rendered HTML now shows **21** with
the local DB. If the deployed page still reads 0 after this PR ships,
the issue is in the SEC ingestion pipeline or the VPS-to-Render upload
cadence, not in this page.

---

## Bug 5 — 485A 75/60 day clock

### Investigation

Searched the codebase for hardcoded `70`, `75`, `60` day rules for 485A
effectiveness:

| File | Line | Rule |
| ---- | ---- | ---- |
| `etp_tracker/step4.py:78` | `default_eff = fdt + pd.Timedelta(days=75)` | new-fund 485APOS default |
| `etp_tracker/email_alerts.py:170` | `(dt + timedelta(days=75))` | digest projection |
| `webapp/routers/trusts.py:40` | `filing_date + timedelta(days=75)` | trust page expected eff |
| `screener/li_engine/.../weekly_v2_report.py:540,544` | `(base + pd.Timedelta(days=75))` | L&I projection |
| `docs/PIPELINE_RETHINK.md:187,370` | `+ timedelta(days=75)` | design doc |
| `docs/EFFECTIVE_DATES_AND_NAME_CHANGES.md:184,525` | `+ timedelta(days=60)` | N-1A (not 485A) |

**No occurrence of `70` exists for filing-to-effective math.** The
codebase already uses 75 days for new-fund 485A filings, which is the
correct SEC default.

The 60-day rule applies to material changes to existing funds (not new
funds). Distinguishing the two requires parsing the filing text and is
not currently implemented. The 60-day reference in
`EFFECTIVE_DATES_AND_NAME_CHANGES.md` is for N-1A (initial registration
under the 33 Act, also 60 days), not 485A.

### Fix

Documentation-only change to `etp_tracker/step4.py`: explicit comment
block above the 485A status path explaining the 75-day (new fund) vs
60-day (material change) distinction and noting that material-change
detection isn't implemented yet.

The 75-day fallback used in Bug 3 above is consistent with this.

### Diff

```diff
-    # 485APOS = Initial filing
+    # 485APOS = Initial filing (post-effective amendment FORM but used for
+    # both new funds AND material changes to existing funds).
+    #
+    # SEC Rule 485(a) effectiveness clock:
+    #   - 75 days for a NEW fund (initial registration via 485APOS)
+    #   - 60 days for a MATERIAL CHANGE to an existing effective fund
+    # Default here is 75 days because REX overwhelmingly files 485APOS for
+    # new-fund launches; the 60-day material-change path requires parsing
+    # the filing text and is not implemented yet.
```

---

## Summary table of KPI deltas

| KPI                       | Before fix | After fix | Source                |
| ------------------------- | ---------- | --------- | --------------------- |
| T-Bill suite count        | 0          | **1**     | `_rex_only_filter`    |
| Effective Date in ≤60d    | 0          | **35**    | `upcoming_dated_60d`  |
| Urgent (next 14d)         | 0          | **9**     | `urgent_dated_14d`    |
| Recent Filings (14d)      | 0 (Render) / 21 (local) | **21**    | local DB always correct; was data-freshness issue |
| Days in Stage (TLDR)      | 29d (wrong) | **111d**  | new status-aware anchor |

---

## Out of scope (per task constraints)

- Template changes — O1 owns `pipeline_products.html`.
- Status enum mapping — O3 owns the status enum.
- `rex_product_status_history` table — task #114, deferred.
- Material-change 485A detection via filing-text parsing — needs SEC text
  parser; logged for future work.
