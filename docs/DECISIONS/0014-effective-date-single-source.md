---
doc: decision
id: 0014
title: Effective dates — parse at ingestion, one source of truth, no reconciler band-aid
status: accepted
date: 2026-06-22
---

# ADR 0014 — Effective dates: scrape every filing 100%, one authoritative date

## Context

Effective-date tracking has been "fixed" 5+ times (see git: 835d520 +75d guess, 056c8f7
per-series refresh, e8b639e/fdbdf77 485APOS election parser, c1c8d3a fund_status fallback,
30d7aa1 reconciler status-gating). Each patched a symptom. The root flaw:

- The parser (`etp_tracker/step3.py`) is **best-effort with silent NULL fallback**, and its
  glyph→`[X]` substitution can mark a whole cover page "checked" (Wingdings-per-char filings)
  and grab the wrong election — right answers came partly by luck (75-day dominant).
- **Three** stores hold the date with no single authority: `fund_extractions.effective_date`
  (parse), `fund_status.effective_date` (SEC feed — the table the T-REX report READS), and
  `rex_products.estimated_effective_date` (REX cache). They diverge silently.
- The parse only ever propagated to `rex_products`; `fund_status` was a **dead-letter route**
  → every competitor effective date stayed NULL → blank "Earliest Eff."
- Cascading required **manual** scripts; a **reconciler** papered over the gaps.

The SEC reality (17 CFR 230.485): the effective date is a **sequence** —
485APOS (Rule 485(a)) schedules it (60/75-day election or explicit date) → 485BXT
(Rule 485(b)(1)(iii)) **delays/replaces** it → 485BPOS (Rule 485(b)) makes it **actually
effective**. The **latest 485-family filing by filing date is authoritative.**

## Decision

1. **Bulletproof parser, every filing, at ingestion.** `robust_election` (entity-aware:
   reads raw ballot-box entities ☐/☑/☒/X; accepts a date only when its immediately-preceding
   box is genuinely checked) replaces the lossy parser, run form-correctly:
   - 485APOS → cover-page election (60d/75d/immediate/explicit).
   - 485BPOS → the stated effective date (full body), status → Effective when ≤ today.
   - 485BXT → the **new** designated date (full body), honoring the delay; status stays Filed.
   Validate (date > filing_date; 2015 ≤ year ≤ 2031). **No estimation. No silent NULL** — a
   genuinely-unelected filing stays NULL truthfully and is logged.
2. **One authoritative date.** `fund_extractions.effective_date` is THE source. `fund_status`
   and `rex_products` are **derived from it at ingestion** (latest 485 per series wins). The
   dead-letter route is closed: the propagation runs in the chain, not via manual refresh.
3. **Status derived from the latest filing at ingestion** — not deferred to a daily reconciler.
4. **Retire the band-aids:** delete the +75d guess; the reconciler stops writing dates
   (consumes only); `refresh_effective_dates`/`backfill_485a_elections` become audit-only.
5. **Tripwire, not fixer:** a daily assertion FAILS LOUD if any 485 filed >7 days ago has a
   NULL effective date, or if the three stores diverge for a series. It surfaces a parse miss
   to fix at the source; it never silently writes a date.

## Consequences

Every 485-family filing is parsed truthfully at ingestion; one date propagates everywhere;
status follows the latest filing; no reconciler or estimation patches a gap. A parse miss is
loud, not hidden. This is the end-state ADRs 0008/0012 intended for status, now extended to
the effective date itself.

## known-gaps

- Barclays iPath / basket-label "underliers" carry no 485 election — legitimately date-less.
- Multi-year backlog (pre-2024) filings without a re-scrape stay NULL until the one-time full
  scrape runs (`scripts/scrape_li_effective_dates.py`, extendable beyond L&I).
