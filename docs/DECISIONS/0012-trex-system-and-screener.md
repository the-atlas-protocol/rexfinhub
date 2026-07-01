---
adr: 0012
title: T-REX System (REX Ops) + Screener (Tools) — the two T-REX-related surfaces
status: accepted
date: 2026-07-01
---

# 0012 — T-REX System + Screener

## Context

The public `/tools/li/candidates` page ("L&I Filing/Launch Candidates") was the
front door to REX's leveraged/inverse launch intelligence, but it (a) surfaced
only 2 of the 5 candidate lanes, (b) used the email report's zebra tables rather
than the approved web components, and (c) overlapped conceptually with the
full-fund landscape at `/market/rex-performance`. Ryu asked for a finalized,
on-brand experience and a clear split.

## Decision

Two distinct surfaces:

1. **T-REX System** — the competitive-intelligence / "what to launch" view.
   Six lanes: REX pipeline, filing whitespace, inverse gap, launch-anyway,
   foreign-listed, pre-IPO — plus the AI investigator and downloadable PDFs.
   - Route stays `/tools/li/candidates` (redirects intact); **nav moves to
     REX Ops > Strategy** and the label becomes "T-REX System".
   - Rationale: this is internal strategy/decision support, which is what
     REX Ops is for.

2. **Screener** — the full live-fund landscape at **Tools > Screener**
   (`/tools/screener`). On-brand K-A KPIs + T-B table over every ACTV ETF/ETN,
   with a **one-click L&I (category) filter**, REX-only toggle, search, sort,
   CSV. Reuses the existing `/market/api/screener-data` endpoint.
   - Supersedes the `/market/rex-performance` screener experience (that page is
     left in place per build-prove-retire; retire only after the new one is
     proven).

## Consequences

- Both surfaces render the approved STYLE_GUIDE components (no zebra striping,
  pill status badges, 40px rows). The T-REX lane builder emits one on-brand
  table style used by the page and the PDFs, so they match.
- Score is the canonical `li_engine_daily.final_score` (0–100) everywhere;
  unscored foreign/pre-IPO underliers show "—", not 0.
- Status is Filed / Effective only.
- Bloomberg data is permitted on these public surfaces (supersedes the prior
  "no Bloomberg on public site" convention for `/tools/li` and `/tools/screener`).
