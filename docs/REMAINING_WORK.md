# Remaining work — rexfinhub (handoff 2026-06-22)

Captured after a long session. Root causes are pinned (3 investigations). Each item below
has the exact file:line + fix so it can be executed cleanly in a fresh context. Gate stays
locked; nothing sends without Ryu's go.

## DONE this session (all pushed to main, VPS clean)
- Self-correcting contract system, June-18 backdating, MicroSectors override fix + invariant,
  AI reviewer → advisory, git-hygiene fix, restore drill verified, cadence doc.
- Recipients: microsectors send-bug (VALID_LIST_TYPES), amendments (BMO on micro+blue_ocean),
  gking-first.
- **TECL + 4 sector/baskets** (TECS/TPOR/MAGX/WLDU) reclassified Single Stock→Index in
  attributes_LI.csv → L&I single $54.0B → $47.5B.
- **Pre-IPO key fix** (234be03): trex_combined_v9 load_preipo_competition now reads
  `high_profile_pre_ipo` (was `high_profile`) → Pre-IPO section will populate.

## ANSWERED (no fix needed)
- **MicroSectors flows (~$96M): CORRECT.** Genuine net inflows, 3.16% flow/AUM, within the
  month's normal range. Not a bug.

## TODO 1 — Single-underlier reframe (Ryu's definition)
**Definition (confirmed):** single underlier = tracks ONE thing: single stock + single
commodity + single crypto. NOT single: sector/index (TECL ✓done), **and a 2x on a single
ETF is NOT single** (an ETF is itself a basket → Index/Basket/ETF side).
**Work:**
1. In `config/rules/attributes_LI.csv` set `map_li_subcategory`:
   - single-commodity (AGQ/UGL/UCO/BOIL/GLL/SLV-type) + single-crypto funds currently "Index"
     → **"Single Stock"** (the single bucket token) so they join the single-underlier bucket.
   - any fund whose `map_li_underlier` is a single ETF (e.g. 2x QQQ/SOXX/IBIT) currently
     "Single Stock" → **"Index"**.
   (data_engine derives category_display from map_li_subcategory: contains "Single Stock"
   → CAT_LI_SS else CAT_LI_INDEX — `webapp/services/data_engine.py:927-937`.)
2. **Relabel** the displayed "Single Stock" → "Single Underlier". Canonical constants
   `market/config.py:185 CAT_LI_SS`, `:188 CAT_CC_SS`. CAUTION: 29 quoted literal
   "Single Stock" matches across ~12 files (some are the Bloomberg COLUMN map at
   config.py:93 — DO NOT change that; some are cc_category, a different axis). Safer path:
   rename the CAT_*_SS *values*, then sweep the functional literal comparisons
   (report_data, generate_competitor_excel, screener/analysis_3x, filing_match, scoring,
   the contract YAML `category_display_contains`). Verify with the contract count + report build.
3. **Repoint MicroSectors report off Bloomberg is_singlestock → our category_display**:
   `scripts/microsectors_industry_report.py:74` (WHERE primary_category='LI'),
   `:80 li["is_ss"]=is_singlestock.notna()` → use category_display single/basket instead.
   Then both reports compute single-underlier the same way and the 51-vs-47.5 gap closes.
**Verify:** L&I single-underlier ≈ MicroSectors single-underlier; contract T-REX=41 still holds
(REX single-stock-equity funds stay single).

## TODO 2 — Foreign competitor capture (ProShares) is STALE
`ai_underlier_intel` RAN today (14:52, rc=0) but in 0.5s with **zero new candidates** —
`data/foreign/universe.parquet` frozen Jun 17 (54 rows), missing recent ProShares foreign
single-stock filings (Dongshan, Montage, HGTECH, BIWIN, Unimicron, Ibiden, Accton). The
filings ARE in the DB (51 ProShares 485APOS in 60d, 7 foreign Jun 16-18) but aren't reaching
ai_underlier_intel's CANDIDATE query.
**Fix:** open `scripts/ai_underlier_intel.py`, find its candidate source (what set of
"new competitor underliers" it pulls). It's missing the recent filed underliers. Point it at
the recent competitor filings (rex_products/fund_status filed in last N days, or
filed_underliers.parquet) and clear the relevant `_already_seen` so the 7 Jun 16-18 foreign
underliers get classified → universe.parquet → T-REX Foreign section. Then rebuild.

## TODO 3 — Competitor "Earliest Eff" empty (T-REX system report)
6 competitor sections (Imminent, Recent Filings, Pipeline, Whitespace, Inverse Gap, Launch
Anyway) read `fund_status.effective_date` via `load_underlier_competition()`
(`trex_combined_v9.py:275-309`, rendered `_comp_cells():338-348`). ~2,123 competitor
fund_status rows have NULL effective_date (recent DELAYED filings). `refresh_effective_dates.py`
only populates `rex_products` (REX), never `fund_status` (competitors).
**Fix:** extend effective-date parsing to `fund_status.effective_date` for competitor rows
where the 485 election was parseable. NOTE: many are genuinely DELAYED (no date elected yet)
— those blanks are CORRECT; only fill where a date exists. Lower priority (mostly legitimate).

## TODO 4 — Send guard + 4 commands (build BEFORE any real send)
**No already-sent guard exists** (confirmed — nothing in send_all/email_alerts checks).
1. Add a `send_log` table (report, period_key=date or ISO-week or month, recipients, sent_at)
   and a guard in `scripts/send_all._send_one` (or `_resolve`): refuse if this report already
   sent for its period (daily=today, weekly=this ISO week, blue_ocean=this month) unless
   `--force`. Records on every successful send.
2. Four skills (each wraps send_all + respects gate + the new guard):
   - `/sendalltome` → `send_all --bundle all --send --to relasmar@rexfin.com`
   - `/senddaily` → `--bundle daily --send`
   - `/sendweekly` → weekly bundle + daily (`--bundle weekly` then `--bundle daily`, or a
     combined bundle) `--send`
   - `/sendblueocean` → `--bundle blue_ocean --send` (1st-of-month item)
   Bundles already exist in `send_all.py:165 BUNDLES`; `--to` override at `:247`.

## Notes
- VPS at origin/main, clean. Send gate `config/.send_enabled` closed.
- blue_ocean + microsectors now have EXTERNAL BMO recipients; autocall has CAIS/RBC. A live
  send of those leaves the building.
