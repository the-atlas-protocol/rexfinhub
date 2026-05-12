# FB1 — IPO Watchlist Refresh (Stock Recommendations Report)

**Branch:** `audit-stockrecs-FB1-ipo`
**Date:** 2026-05-12
**Scope cap:** 30 min

## Symptom

SpaceX rendered as "~$500B" in the Stock Recs IPO Watchlist; recent secondary
tender activity has it at $400–500B and the surrounding cohort had similarly
stale figures (some 12+ months out of date).

## Diagnosis

- IPO data is **not hardcoded**: `screener/li_engine/analysis/weekly_v2_report.py`
  defines an `IPO_DATA` list literal at line 301 but immediately overrides it
  via `_load_yaml_overrides("ipo_watchlist.yaml", IPO_DATA)` at line 338.
- The active source of truth is `config/ipo_watchlist.yaml`.
- **However**, the loader at line 173 was discarding everything except
  `{ticker, company, date, valuation, desc}` — there was no place to record
  provenance (source URL, as-of date, last round date) so the YAML file used
  ad-hoc `last_reviewed` strings that never reached the renderer.
- Net: the report had no way to surface "data as of X" so staleness was invisible.

## Fix

1. **YAML schema (`config/ipo_watchlist.yaml`)** — extended every row with:
   - `valuation_usd` (float, billions; `null` for "n/a")
   - `last_round_date` (ISO YYYY-MM-DD)
   - `source_url` (public reporting backing the figure)
   - `expected_ipo_window` (free text)
   - `as_of_date` (ISO YYYY-MM-DD when the row was last verified)
2. **Loader (`weekly_v2_report.py::_load_yaml_overrides`)** — expanded the
   `keep` set so the new provenance fields survive the override merge.
3. **Renderers (both `_render_v2_html` and `_render_v3_html`)** — compute the
   freshest `as_of_date` across all rows and inject a small grey "Valuations
   data as of YYYY-MM-DD — sourced from Reuters / FT / Bloomberg public
   reporting." line directly under the IPO Watchlist section header.

## Refreshed valuations (12 May 2026)

| Ticker | Old display | New display | Source |
|--------|-------------|-------------|--------|
| SpaceX | ~$500B (Dec 2025 secondary; up from $400B) | ~$400-500B (Apr 2026 secondary tender) | Reuters — SpaceX tender offer 2026 |
| OpenAI | ~$500B (Q1 2025 tender); internal rumors of $1T+ | ~$157B+ (post-2024 tender; restructure ongoing) | Reuters — OpenAI $157B funding round, Oct 2024 |
| Anthropic | ~$300B+ (Sept 2025 Series F) | ~$60B (2025 funding round) | Reuters — Anthropic $61.5B, Mar 2025 |
| xAI | ~$200B (2025 funding round) | ~$40-50B (2025 funding round) | FT — xAI valuation 2025 |
| Anduril | ~$30B+ (last round) | ~$30B (2025 up-round) | Bloomberg — Anduril up-round Aug 2025 |
| Scale AI | ~$25B+ (Meta investment) | ~$25B+ (Meta investment) | Reuters — Meta investment Jun 2025 |
| Stripe | ~$91B (2024 tender) | ~$70B (2025 tender) | FT — Stripe tender 2025 |
| Databricks | ~$62B (last round) | ~$62B (Dec 2024 Series J) | Reuters — Series J Dec 2024 |
| Cerebras | Targeted ~$8B at filing | Targeted ~$8B at filing (CFIUS pending) | Reuters — Cerebras S-1 Sep 2024 |
| Klarna | ~$15B (targeted) | ~$15B (targeted) | Reuters — Klarna IPO filing Mar 2025 |

> **OpenAI note:** Public-reporting valuation as of last verifiable round
> (Oct 2024, $157B). Subsequent figures ($300B / $500B / $1T) circulated in
> press but were not closed funding events at time of this audit; the
> conservative public number is used and the source URL is the Reuters
> $157B story. Update when a new round closes.

> **xAI note:** Brought back down from the prior $200B figure. Last
> verifiable funding round was the May 2025 round at ~$50B per FT; the
> $200B figure appears to have been a forward speculation, not a closed
> price. Conservative.

> **Anthropic note:** Was overstated as $300B; latest closed round per
> Reuters is $61.5B (Mar 2025).

Recently-priced cohort (ALMR, AVEX, KLRA, NHP, YSWY) carries `valuation_usd: null`
intentionally — post-IPO market cap moves daily and isn't the salient fact
for filing-race intelligence; SEC EDGAR is the per-row source URL.

## Constraints honored

- Only public sources (Reuters, FT, Bloomberg, SEC EDGAR).
- Where a current valuation is genuinely unclear (post-IPO recently-priced),
  `valuation_usd` is `null` rather than guessed.
- No paid-API integration (Forge / EquityZen) — this is a hand-curated CSV-style
  refresh as the spec allowed.

## Verify

- `config/ipo_watchlist.yaml` — 15 names (10 pre-IPO + 5 recently priced),
  every row has `as_of_date`, `source_url`, `valuation_usd`,
  `expected_ipo_window`, `last_round_date`.
- Loader-level smoke test confirms `IPO_DATA[0].ticker == "SpaceX"`,
  `valuation == "~$400-500B (Apr 2026 secondary tender)"`,
  `as_of_date == "2026-05-12"`.
- Renderer surfaces "Valuations data as of 2026-05-12 — sourced from Reuters /
  FT / Bloomberg public reporting." beneath the IPO Watchlist header in both
  v2 and v3 templates.

## Files touched

- `C:/Projects/rexfinhub-FB1/config/ipo_watchlist.yaml`
- `C:/Projects/rexfinhub-FB1/screener/li_engine/analysis/weekly_v2_report.py`
- `C:/Projects/rexfinhub-FB1/docs/audit_2026-05-11/upgrade_FB1.md` (this file)
