# Report Numbers — single source + intent per metric

> One place. Every number in every report derives from `mkt_master_data` (the synced
> Bloomberg master) filtered by the rules in `market/definitions.py` + `docs/DEFINITIONS.md`.
> This doc records WHAT each headline number means, WHERE it comes from, and the INTENT
> behind its filter — so a number is never "off" without a documented reason.

## The one source

- **Master table:** `mkt_master_data` (one row per ETP, refreshed by `sync_market_data`).
- **Classification axis:** `etp_category` (LI / CC / Crypto / Defined / Thematic / NULL=untracked)
  + `category_display` (adds the Single-Stock vs Index/Basket axis). Set by the nightly
  classify chain (rules → AI middlemen) + curated `fund_master.csv`; false positives parked
  in `config/rules/exclusions.csv`.
- **Suite / REX identity:** `market/definitions.py` (`suite_of`, `attach_rex_suite`) — the
  single source for the 9 REX suites, trusts, and the market-status KPI rule.
- **Issuer/brand:** `issuer_display`, derived ONLY by `scripts/derive_issuer_brands.py`
  (fund-name brand patterns) → `issuer_brand_overrides.csv` → stamped by
  `apply_issuer_brands.py`, which runs a **legal-trust leak gate** (no report may show a
  legal entity like "Tidal Trust II" as the issuer; only First Trust / Northern Trust are
  brands that legitimately contain "Trust").
- **Effective dates:** `rex_products.estimated_effective_date`, from the 485APOS cover-page
  ELECTION parsed by `etp_tracker/step3.py:_parse_485a_election` → `fund_status` →
  propagated to `rex_products` by `series_id` (`refresh_effective_dates.py`). Real designated
  elections (60/75-day or explicit date), never a blanket guess.

## Canonical counts (must tie on every report)

| Metric | Value | Source / filter (intent) |
|---|---|---|
| T-REX single-stock L&I | **41** | `etp_category='LI'` + `category_display` Single Stock + `is_rex`/T-REX, **ACTV only** |
| MicroSectors index L&I | **22** | MicroSectors suite, index/basket L&I, ACTV (incl. DULL/AIQU/AIQD) |
| REX total ACTV ETP | **79** | `is_rex` + `market_status='ACTV'`, all suites |
| REX income single-stock | **3** | `etp_category='CC'` + Single Stock + REX (NVII, TSII, WMTI) |
| REX income index | **8** | `etp_category='CC'` + Index/Basket + REX |

**ACTV-only is the universal rule** for "current" counts (PEND=pre-launch and LIQU/DLST=closed
are excluded). The landscape *issuer-table* product counts apply the same ACTV filter
(this is why the weekly REX single-stock reads 41, not 53).

## Definition intent (the lines that catch us)

- **Income (CC) = income via OPTIONS** (covered-call / option-strategy / autocallable). Plain
  fixed-income (bonds, treasuries, munis, CLOs), plain dividend equity, and hedged-equity
  collars are **NOT** income — they have no option-income strategy. They live outside the
  tracked categories (`exclusions.csv`). (If REX ever wants a dedicated *dividend-income*
  bucket, that is a DEFINITIONS change, not a silent reclassification.)
- **L&I = daily-reset 2x/3x or inverse/short.** A 130/30 long-short or an ultrashort-treasury
  fund is not daily-reset leverage and is not L&I.
- **L&I market total = single-stock + index + (rare) uncategorised.** When the headline market
  count exceeds single-stock + index, the gap is uncategorised-subcategory funds — categorise
  or declassify them, don't let them float.
- **Autocallable upcoming = REX (rex_products Filed/Delayed) + competitors
  (fund_status PENDING/DELAYED)** — the full incoming-supply landscape, not just REX's PEND.

## Verification gates (so a regression can't ship)

- **Issuer leak gate** (`apply_issuer_brands.check_postconditions`): 0 ACTV funds may show a
  legal-entity issuer (allowlist First Trust, Northern Trust).
- **Preflight** (`preflight_check.py`): KPI tie-out + attribution completeness + chart presence.
- **Effective-date coverage:** recent (≤2yr) 485APOS PENDING/DELAYED should resolve to an
  election; the residue is genuinely abandoned shelf filings (>2yr) or indeterminate cover
  layouts — not chased.
