# GOAL — Finalize all 10 reports so they tie out and are ready to send

_Written 2026-06-16. Execution checklist for the per-report review. "Done" = Ryu
opens the previews, every number is correct and consistent, and the reports are
ready to send tonight with no further instructions — AND the nightly workflow
keeps them correct going forward (classification + effective dates self-heal)._

## The one sentence
Every figure in every report traces to live, correct data through the single
definition library; no stale cache, no miscategorized fund, no missing chart, no
blank effective date — and the pipeline keeps it that way automatically.

## Definition of done (the tests)
1. **Every number ties out** across reports: T-REX = 41, REX income single-stock = 3,
   suites read from `market/definitions.py`, no liquidated fund in any present-day
   KPI, MicroSectors AUM = the override (not raw Bloomberg).
2. **No stale cache** in any emailed report — all compute from live master data.
3. **Classification is correct AND self-healing**: an AI middleman in the nightly
   workflow classifies every new fund with context (income vs bond, thematic,
   L&I, crypto, defined) and a human-readable audit; today's funds are corrected.
4. **Every launched fund shows its real effective date** — scraped from the SEC
   filing, not a +75-day projection. Funds not yet effective are clearly marked.
5. **All 10 reports build with full content** and are visually correct (charts,
   colors, sections), previewable in Chrome.
6. Ready to send: shadow-gated, previews reviewed, nothing sent without Ryu's go.

## Issue ledger (Ryu's 2026-06-16 review) — status
| # | Report | Issue | Status |
|---|---|---|---|
| 1 | Weekly | Remove filing-activity section | ✅ done |
| 2 | Weekly | Yielders leaking liquidated funds | ✅ done (ticker-norm + ACTV) |
| 3 | Weekly | Landscape 53/24 counts wrong | ⏳ pin the render path |
| 4 | L&I | MicroSectors in single-stock (DULL/AIQU/AIQD) | ✅ done |
| 5 | L&I | Issuer REX 40 vs 41 | ✅ done (stale cache) |
| 6 | L&I | $81B 1yr-ago MicroSectors | ✅ done (stale cache) |
| 7 | L&I | Top REX KPI wrong | ✅ done (stale cache) |
| 8 | Income | "Others 80.2%" / YieldMax missing | ✅ done (stale cache) |
| 9 | Income | REX 10 products / single-stock count | ✅ done (REX ss = 3) |
| 10 | Flow | Volume chart green + ATCL blue | ✅ done |
| 11 | Flow | REX highlighted blue in EVERY chart | ⏳ 3 charts left |
| 12 | Flow | List PEND + filed autocallables at bottom | ⏳ build section |
| 13 | Daily | Upcoming launches miscategorized (Social 50…) | ✅ AI-classified (verify) |
| 14 | Daily | Thematic categorization restored | ✅ AI-classified (verify) |
| 15 | T-REX | Every filing must show its REAL effective date | ⏳ scrape coverage |
| 16 | Portfolio Suite | Missing 2 of 6 charts | ✅ done (Structured rename) |
| 17 | Portfolio Suite | MicroSectors correctness | ✅ excluded by design (confirmed) |

## Root causes proven
- **Stale persisted cache** (mkt_report_cache) drove #5–#9: the email read a cache
  that lagged reality. Fix: email builders compute `use_cache=False`.
- **Suite rename fallout** (Autocallable→Structured) hid the Structured suite in the
  autocall email, screener archive, and Portfolio Suite (#16). Fixed everywhere.
- **Keyword classification** can't tell bond-"income" from option-"income" (#13/14).
  Fix: an AI middleman with context.

## Remaining work to finish autonomously
1. **Flow** (#11/#12): REX-blue in `_flow_bars`, `_issuer_share_bars`,
   `_horizontal_bar_chart`; append a "Pending & Filed Autocallable Launches"
   section (PEND: ACYQ/AUTB/AUTG + any filed-not-launched).
2. **Effective dates** (#15): audit `fund_status.effective_date` coverage for
   LAUNCHED REX/competitor funds; ensure the scraper captures the 485BPOS effective
   date; the T-REX report shows the real date for every launched filing and a clear
   "pending" marker (not a fake +75 date) for not-yet-effective ones.
3. **AI classifier in the workflow**: `scripts/ai_classify_unmapped.py` — runs the
   context-aware LLM classifier on unmapped funds, writes an audited
   classifications journal, applies HIGH/MEDIUM to fund_mapping.csv; wired into the
   nightly `apply_bloomberg_post_steps` chain so new funds self-classify.
4. **Weekly 53/24** (#3): find the render path and correct/remove it.
5. **Final**: rebuild all 10 fresh, verify each number, deliver previews, push,
   refresh the main repo, leave shadow-gated.

## Verification at the end
- All 10 previews open, correct, consistent (the ledger above all ✅).
- `python scripts/build_previews.py` on the main repo reproduces them on fresh data.
- Commits pushed to origin/main; main repo synced; nothing sent.
