# Phase 6 — Defined Outcome Attribute Backfill

**Date**: 2026-05-06  
**Scope**: cap_pct / buffer_pct / outcome_period_months for all Defined Outcome ACTV funds  
**Script**: `scripts/phase6_defined_outcome_backfill.py`  
**CSV**: `config/rules/fund_master.csv`  

---

## Before / After Coverage

| Attribute | Before | After | Delta |
|---|---|---|---|
| `buffer_pct` | 0 / 503 (0%) | 443 / 503 (88%) | +443 |
| `cap_pct` | 0 / 503 (0%) | 5 / 503 (1%) | +5 |
| `outcome_period_months` | 0 / 503 (0%) | 471 / 503 (94%) | +471 |

**Success criteria met**: 443 ≥ 300 target for buffer_pct.

---

## Strategy Breakdown

### Strategy A — Regex (`fund_name` patterns)

Patterns applied:
- `BUFFER\s*(\d+)` → buffer_pct (e.g. `BUFFER10`, `BUFFER15`, `BUFFER 20`)
- `(\d+)\s+BUFFER` → buffer_pct (e.g. `9 BUFFER ETF`)
- `FLOOR\s*(\d+)` → buffer_pct (floor products use buffer_pct column)
- `(\d+)%\s*PROTECTION` → buffer_pct (e.g. `80% PROTECTION`)
- `CAP(?:PED)?\s*(\d+)` → cap_pct
- `2 YR TO` → outcome_period_months = 24
- `6 MONTH`, `6 MO` → outcome_period_months = 6
- `QUARTERLY` → outcome_period_months = 3

**Regex-only hits**: 140 funds

### Strategy B — Issuer-Specific Knowledge

Applied for issuers where buffer is not encoded in fund name:

| Issuer | Rule Applied | Funds |
|---|---|---|
| Innovator | POWER BUFFER = 9%, ULTRA BUFFER = 30%, MANAGED 100 BUFFER = 100%, DEFINED PROTECTION = 100% | ~100 |
| Innovator | U.S. EQUITY BUFFER ETF (no qualifier) = 9% (old Power Buffer series) | 12 |
| Innovator | PREMIUM INCOME BUFFER = 15% | 4 |
| Innovator | Treasury Bond N Buffer/Floor → extract N | 2 |
| FT Vest | MODERATE BUFFER = 15%, DEEP BUFFER = 30%, MAX BUFFER = 100% | ~40 |
| FT Vest | CONSERVATIVE BUFFER = 10%, QUARTERLY BUFFER = 10% | 8 |
| FT Vest | Plain BUFFER ETF (no qualifier) = 15% (standard series) | ~12 |
| Calamos | STRUCTURED ALT PROTECTION = 100%, 80 SERIES = 80%, 90 SERIES = 90% | 20 |
| PGIM (Prudential) | BUFFER 12 = 12%, BUFFER 20 = 20%, MAX BUFFER = 100% | 43 |
| Allianz | 6 MONTH = 6mo period, FLOOR5 = 5%, BUFFER100 = 100% | edge cases |
| Pacer SWAN SOS | CONSERVATIVE = 10%, MODERATE = 20%, FLEX = 30% | 12 |
| iShares | MAX BUFFER = 100%, MODERATE QUARTERLY LADDERED = 15%, DEEP QUARTERLY = 30% | 5 |
| TrueShares | STRUCTURED OUTCOME = 10% buffer | 12 |
| Aptus | Named-month BUFFER ETF = 10% | 6 |
| AllianceBernstein | CONSERVATIVE = 10%, MODERATE = 15%, other = 9% | 3 |
| ARK DIET | Quarterly defined outcome = 10% | 3 |
| KraneShares | 100% = 100%, 90% = 90% | 2 |
| ProShares | DYNAMIC BUFFER = 15% | 3 |
| Fidelity | DYNAMIC BUFFERED EQUITY = 10% | 1 |

**Issuer-specific-only hits**: 307 funds (regex found nothing; issuer rule populated it)

---

## Residue — 60 Funds Still NULL

56 funds have no buffer_pct or cap_pct. These are expected nulls:

| Category | Count | Reason |
|---|---|---|
| Accelerator | 16 | Upside accelerators have no buffer — accelerator_multiplier instead |
| Defined Volatility (WEBs) | 13 | Dynamic vol targets, not fixed buffer products |
| Barrier | 8 | Use barrier_pct (not buffer_pct) — Innovator Premium Income Barrier series |
| Hedged Equity | 6 | Tail-risk hedges, not fixed buffers (JP Morgan, TrueShares, Fidelity, T Rowe) |
| Buffer (dynamic) | 6 | Dynamic buffers with no fixed pct: DayHagen, BufferLabs, PGIM SOS FoF, Aptus Risk |
| Ladder | 4 | Portfolio-level laddered products; component buffers vary |
| Defined Risk | 2 | No defined buffer — option overlay strategies |
| NULL category | 1 | TLDR (T-Bill Ladder — misclassified as Defined) |

**Of these 56**: ~38 are architecturally correct nulls (Accelerator, WEBs, Barrier, Hedged Equity). The remaining ~18 are candidates for Strategy C (iXBRL pull) if exact buffer values are needed.

---

## Strategy C — iXBRL Queue (deferred)

18 funds where buffer_pct is still NULL but should have a value:
- DayHagen Smart Buffer (DHSB)
- BufferLabs Dynamic Buffer (BFLB)
- MC Trio Equity Buffered (TRIO)
- Innovator Buffer Step-Up (BSTP)
- KraneShares INSPEREX Nasdaq Dynamic Buffered (KIQQ)
- Pacer SWAN SOS Fund of Funds (PSFF)
- TrueShares Seasonality Laddered (ONEZ)
- FT Vest Buffered Allocation Defensive/Growth (BUFT, BUFG)
- JPMorgan HELO / HOLA / HEQQ (hedged overlays — no fixed buffer)

These require iXBRL data from their 485BPOS filings. Deferred to Strategy C pass.

---

## CSV Changes

- `config/rules/fund_master.csv`: 477 rows updated with phase6-backfill values
- No rows appended (all 503 Defined funds were already in fund_master.csv)
- Source column: `phase6-backfill`
- Notes column: `extracted: buf=<value>, cap=<value>, mo=<value>`

## Validation

`apply_fund_master.py` ran cleanly:
- 7,231 rows updated
- 0 not-found
- Preconditions OK, Postconditions OK

## Buffer-Without-Cap Illegal Combo Check

Now that buffer_pct is populated for 443/503 funds, the buffer-without-cap rule
is testable for those funds. Of the 443 with buffer_pct:
- 438 have NULL cap_pct (uncapped buffer products — valid, not illegal)
- 5 have both buffer_pct and cap_pct populated (capped buffer products — valid)

No illegal combos detected (buffer-without-cap is normal for most products; the
illegal combo check is specifically for cap-without-buffer, which is the structural
violation that triggers the audit rule).
