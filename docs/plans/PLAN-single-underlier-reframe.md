---
rank: 4
leverage: high — real classification correctness bug affecting the L&I report,
  MicroSectors Industry Report, and the contract's single-vs-index counts; Ryu
  already confirmed the correct definition (docs/REMAINING_WORK.md TODO 1), it just
  hasn't been executed to completion.
---

# PLAN — Fix single-underlier classification (single-commodity/crypto → Single Stock bucket)

## Goal

Ryu's confirmed definition (`docs/REMAINING_WORK.md` TODO 1, written 2026-06-22):
**"single underlier" = tracks ONE thing** — a single stock, a single commodity, or
a single crypto asset. It explicitly does **NOT** include a 2x on a single ETF
(an ETF is itself a basket, so that belongs on the Index/Basket/ETF side) even
though a naive reading might call "one ticker" = "single."

As of this session, verified directly against `config/rules/attributes_LI.csv`:
- **28 rows still have `map_li_subcategory == "Index"`** (the bare 2-letter-code
  legacy value, distinct from the 461 rows correctly coded
  `"Index/Basket/ETF Based"`). Sampled: `AGQ US,Commodity,Index,...` (2x silver),
  `UGL US`, `UCO US`, `BOIL US`, `GLL US`, `SLV`-type commodity funds — these are
  **single-commodity** funds and per Ryu's definition belong in the single bucket,
  not Index.
- Crypto rows (`BITI`, `BITU`, `BITX`, `BTCL`, `ETHD`, `ETHU`, etc. — 40+ rows,
  `map_li_category == "Crypto"`) are **already** coded `"Single Stock"` — spot-
  checked, these are correct as-is (single-crypto = single, matches Ryu's rule).
  Do not re-touch these; they're evidence the pattern works, not evidence of a
  remaining bug.
- ETF-underlier funds (2x QQQ/SOXX-style — `MQQQ`, `QQQD`, `QQQP`, `QQQU`, `SMQ`,
  `SQQQ`, `TQQQ`, `QQQW` and similar) are **already** coded
  `"Index/Basket/ETF Based"`, correctly on the non-single side. Also already
  correct — do not re-touch.

So the **actual remaining gap** is narrower than `docs/REMAINING_WORK.md` TODO 1
originally scoped it (parts of that TODO — the ETF-underlier reclassification and
the MicroSectors report repoint via `category_display` — are already done, see
`scripts/microsectors_industry_report.py:78` which already reads
`category_display` not Bloomberg `is_singlestock`). The remaining work is:
**the 28 "Index"-coded single-commodity rows need to become "Single Stock."**

This closes a real, currently-live discrepancy between the L&I report's single-
underlier count and the definition Ryu has explicitly confirmed — every day this
sits open, ~28 funds are miscounted on the Index side of the L&I report.

## Exact files to touch

1. `C:\Foundry\Rexfinhub\config\rules\attributes_LI.csv` (data fix — the 28 rows)
2. `C:\Foundry\Rexfinhub\market\config.py` (read-only reference: `CAT_LI_SS`
   line 184, `CAT_LI_INDEX` line 185 — confirm no code change needed here)
3. `C:\Foundry\Rexfinhub\webapp\services\data_engine.py` (read-only reference:
   lines ~915-940, `category_display` derivation from `map_li_subcategory` —
   confirm the `.str.contains("single")` logic already handles this correctly
   once the CSV is fixed, since it matches on the substring "single" case-
   insensitively)
4. `C:\Foundry\Rexfinhub\scripts\classify_daily.py` (check: does the autonomous
   classification engine re-derive `map_li_subcategory` for these 28 tickers on
   its next run and potentially revert your CSV fix? Read its logic for how it
   decides "Index" vs commodity/crypto rows before assuming a one-time CSV edit
   is durable.)

## Step-by-step

1. Run `awk -F, '$3=="Index"{print}' config/rules/attributes_LI.csv` to get the
   exact list of 28 rows currently coded bare `"Index"` (not
   `"Index/Basket/ETF Based"`). For each row, check `$2` (the `map_li_category`
   column) — Ryu's rule only reclassifies **Commodity** and **Crypto** category
   rows to Single Stock; if any of the 28 rows are `Equity` category (a genuine
   index/basket equity fund that just happens to use the legacy short code),
   those must stay Index/Basket and instead just get their subcategory string
   normalized to the full `"Index/Basket/ETF Based"` value for consistency —
   do NOT reclassify an equity-index fund to Single Stock.
2. For every row where `map_li_category == "Commodity"` (and any `Crypto` rows
   that somehow still say bare "Index" — verify none do, per your findings
   above they're already "Single Stock"), change column 3
   (`map_li_subcategory`) from `Index` to `Single Stock`.
3. Verify the `map_li_underlier` column (column 6) for each changed row is a
   **single** underlier ticker/index (e.g. `XAG Curncy` for AGQ = single silver
   spot) and not itself a basket — this is the actual test of Ryu's rule. If any
   of the 28 rows has a multi-asset underlier in column 6, do not reclassify it;
   flag it to Ryu instead.
4. Any `Equity`-category rows found in step 1 that are NOT single-underlier: set
   their `map_li_subcategory` to the full string `"Index/Basket/ETF Based"`
   (matching the other 461 rows) rather than leaving the ambiguous bare
   `"Index"` value — this prevents future confusion about whether "Index" means
   the same thing as "Index/Basket/ETF Based" (it does, but two spellings of the
   same value is exactly the kind of split-definition bug `docs/GOAL.md` warns
   against).
5. After the CSV edit, check whether `scripts/classify_daily.py`'s nightly
   09:00 sweep (per `ARCHITECTURE.md` §6) would overwrite these 28 rows back to
   "Index" on its next autonomous run — read how it decides `map_li_subcategory`
   for commodity/crypto funds. If the autonomous engine's rule logic doesn't yet
   encode "single-commodity/crypto = Single Stock," your CSV fix will be
   reverted within 24 hours. If so, the actual fix belongs in the rule engine
   (Tier 1 deterministic rules, per `ARCHITECTURE.md` §6's tier table) or in
   `market/definitions.py` if that's where the human-contract definition should
   live going forward — do not just patch the CSV and walk away if the engine
   will silently undo it.
6. Re-run (or trace through, if a live re-run isn't safe locally) `apply_fund_master.py`
   / `apply_classification_sweep.py` (the post-steps that restamp
   `mkt_master_data` from these CSVs, per `ARCHITECTURE.md` §4) to confirm the 23
   restamped columns on `mkt_master_data` pick up the corrected subcategory.

## Edge cases a weaker model would miss

- **Don't touch the 461 rows already coded `"Index/Basket/ETF Based"`** or the
  `Single Stock` crypto rows — this plan is scoped to exactly the 28 bare-"Index"
  rows, filtered further to Commodity/Crypto category. Re-running a broad
  find/replace on "Index" → "Single Stock" would wrongly convert genuine
  index/basket equity funds.
- **The CSV has a stray malformed row** — the `awk` histogram in exploration
  showed one literal value `map_li_subcategory` appearing as a row VALUE (not
  just the header), meaning there's a duplicate/malformed header or blank line
  somewhere in the file. Find and fix or explicitly ignore this before running
  bulk edits, or your edit script may corrupt that row further.
- **This CSV is the git-mirror of `data/rules/` on the VPS** (per
  `ARCHITECTURE.md` §3 — "`data/rules/` = runtime master on VPS, `config/rules/`
  = git mirror"). A local edit here does not take effect on production until
  the VPS's copy is updated (via `git pull` or the `classify_daily` mirror step)
  — this plan only produces the correct diff; deploying it to VPS and running a
  fresh classification sweep is a separate, later step that needs Ryu's
  awareness (it changes production numbers).
- **The contract's T-REX=41 count must NOT move.** Per HANDOFF_2026-06-17_reports.md's
  canonical numbers, T-REX single-stock L&I ACTV = 41. This plan only touches
  Commodity/Crypto rows (AGQ, UGL, BITX, etc. — none of which are REX products),
  so the REX suite counts should be completely unaffected. If your change alters
  the T-REX=41 or MicroSectors=22 counts, you've touched something you shouldn't
  have — stop and check which row caused it.
- **`"Single Stock"` is a legacy label being renamed to "Single Underlier"** per
  the (partially superseded) TODO 1 write-up — do NOT do that renaming as part
  of this plan; it's a separate, much larger sweep across ~12 files
  (`config.py` CAT_*_SS constants + all their literal-string comparisons) that
  REMAINING_WORK.md flagged as its own risky step. This plan is scoped to the
  28-row data fix only.

## Acceptance criteria

1. `awk -F, '$3=="Index" && $2=="Commodity"' config/rules/attributes_LI.csv`
   returns **zero rows** after the fix (all reclassified to Single Stock).
2. `awk -F, '{print $3}' config/rules/attributes_LI.csv | sort | uniq -c` shows
   the bare `"Index"` bucket either gone entirely or containing only rows you
   deliberately normalized to `"Index/Basket/ETF Based"` in step 4 — no
   ambiguous bare `"Index"` values remain for any row you touched.
3. Rebuild the L&I report locally (find the preview build entrypoint per
   `docs/HANDOFF_2026-06-17_reports.md`'s "/refreshdata" workflow description, or
   the direct builder call for `report_emails.py` `li` subset) and confirm the
   single-underlier count increased by exactly the number of Commodity rows you
   reclassified (spot-check AGQ/UGL/UCO/BOIL/GLL now appear in the single-
   underlier bucket).
4. Confirm T-REX (41) and MicroSectors (22) ACTV counts are unchanged (per
   HANDOFF's canonical numbers) — these are REX-suite counts, orthogonal to this
   external-classification fix.
5. If step 5 of the "step-by-step" section above finds that `classify_daily.py`
   will revert this fix on its next scheduled run, that finding itself — plus a
   recommendation for where the durable fix belongs — is an acceptable
   deliverable even if the rule-engine change is out of scope for this pass;
   report it clearly rather than silently shipping a CSV fix that reverts in 24h.
