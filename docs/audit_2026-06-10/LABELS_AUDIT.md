# Report Label-Accuracy Audit — 2026-06-10

> Ryu's question: "have we been accurately labeling the info or simply just reading off
> the databases — for every new L&I or Income fund, added to KPIs/tables? accurately
> pushed into Single Stock / Index/Basket/ETF?" 18-agent investigation + adversarial
> verification against the 2026-06-09 23:00 prod snapshot. All fixes below are LIVE.

## The direct answer
Reports DO flow through the curated taxonomy (etp_category + map_li_*/cc_*), not raw
Bloomberg reads. But the labels feeding them had 5 confirmed defect classes — all in
the CLASSIFICATION layer, not the report code. The report code faithfully renders
whatever the taxonomy says; the taxonomy was wrong in specific, systematic ways.

## Confirmed defects → fixes (all applied + restamped + committed 2026-06-10)

1. **11 single-stock leveraged launches labeled "Index/Basket/ETF Based" with blank
   underlier** (AMKL→AMKR, AMPU→AMPX, ASTY→ASTS, MUZ→MU, ONG→ON [also wrongly
   Commodity], OSSL→OSS, POEL→POET, STXL→STX, VELL→VELO, WYFL→WYFI, INFH→INFQ;
   mostly Defiance Daily Target launches) + EUVX inverse error (leveraged ETF-on-ETF
   labeled Single Stock with fake underlier "EUV"). Impact: competitor AUM/flows on
   REX battlegrounds (ASTS/STX/MU/ON) undercounted in every sent report's
   single-stock tables. **FIXED: Single Stock + correct underliers; EUVX → Index/Basket.**

2. **2 leveraged-crypto launches routed Crypto instead of LI** (TXXH 2X HYPE, XBNB 2X
   BNB; convention: 620 leveraged funds in LI). Invisible to the entire L&I email.
   **FIXED → LI with leverage attrs.**

3. **3 income launches misrouted** — FIYY (YieldBoost 20Y+ Treasuries → was LI),
   WRTH (options income → was LI), QVOL (option income → was Defined/Buffer): absent
   from the Income report, polluting L&I/Defined. Plus **3 income funds with NULL
   category entirely**: KHPI ($391M!), TOPW (HIGH-confidence proposal pending since
   2026-04-14 in the dead queue), JPO. **FIXED: all 6 → CC with attribute rows.**

4. **6 newest REX launches missing from rex_suite_mapping.csv** (AXTU/ASUP/LITU/TEUP
   → T-REX; AIQD/AIQU → MicroSectors): the Flow report's suite KPI said 36 REX funds
   while the issuer table in the SAME email said 40 (MicroSectors 20 vs 22).
   **FIXED: rows added (suffixed form per the data_engine join); stamps at next sync.**

5. **34 plain-vanilla equity funds stamped CC by the AI tier on 2026-06-09**
   (VDG/VDV "Dividend Growth", FEMG, BUYB "Buyback", MFS/Baron/DFA/American Century
   funds...) — ~9% Income-report fund-count inflation (~0.25% AUM), bogus style-box
   rows in the cc_category table, and ~24 plain-beta issuers entrenched as CC issuers
   in issuer_mapping. Root cause: "dividend/growth/quality" reads income-ish to an
   LLM, and the critic shared the blind spot. **FIXED: all 34 reverted → full-ticker
   exclusions + fund_master Plain Beta rows; 8 wrong issuer rows dropped. ACTV CC
   count 403 → 375.**

## Engine hardening (prevents recurrence — live before tonight's 17:15 run)
- Classifier + critic prompts: explicit CC criteria (options mechanics REQUIRED;
  dividend/quality/value/buyback NEVER CC), leveraged crypto = LI,
  "<N>X LONG <TICKER>" = LI/Single Stock + underlier.
- **Deterministic vetoes** in `scripts/classify_daily.py` that bypass model judgment:
  CC proposal without option words in the fund name → queue; leveraged single-name
  proposed as non-LI → queue.

## Freshness verdict (verified)
- A fund first appearing in today's 17:15 Bloomberg file reaches TOMORROW's 19:30
  email (T+1) — same latency as the old 09:00 design; the 2026-06-10 move of
  classification into the chain did NOT create a new blind spot and improved
  DB/website freshness to T+0 21:00.
- Same-night cache drift is real but email-safe: caches bake pre-classification
  (proven: YBMN counted as L&I in the cache, CC live), the 21:00 re-sync re-bakes,
  and the sent L&I/Income emails currently dodge stale caches because run_daily's
  19:30 classification step creates a pipeline-run row that flips the cache-staleness
  check into a live rebuild. **WARNING (registered): that protection is accidental —
  "fixing" the stuck daily_classify run rows would silently re-expose the stale-cache
  path. Proper fix = move cache baking to a post-step (debt register).**

## Registered follow-ups
- Retro-review the 771 unreviewed 'atlas' bulk rows in fund_mapping (the source of
  essentially all cross-category contamination) through the hardened critic.
- 266/685 ACTV LI funds (39%) have NULL map_li_underlier — underlier backfill campaign
  (weakens underlier rollups; mostly older funds).
- cc_type Traditional/Synthetic KPI split excludes 68 of 403 CC funds (64 NULL + 4
  Autocallable cc_type).
- Cache-bake post-step redesign (see freshness warning above).
- Legacy proposal queue: 1,724 pending / 1 approved — triage or retire the queue UI.
