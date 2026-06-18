# DEFINITIONS — the REX ETP definition library (human contract)

_The single source of truth for how we define the ETP landscape. This document is
the human-readable contract; the executable encoding is `market/definitions.py`,
and `tests/test_definitions.py` asserts the two never drift. If you change a
definition, change it in **one** place and both update together._

Goal this serves: every fact has one definition, every report reads it, the answer
is the same everywhere. See `docs/GOAL.md`.

**Enforcement & repair contract** (ADR 0013): a fact that the deterministic rules
can't place is resolved by the self-healing cascade (`market/resolve.py`:
rules → Bloomberg `fund_description` → AI web search → human queue), every rendered
status routes through `canonical_status()`, and the preflight gate BLOCKS the
build/send on any fact that is wrong (string scan + AI semantic review). Any fact in
a report must trace to its single source here + that gate.

---

## 1. Two views of the world

We always hold two views. They are allowed to collide — that is by design, and the
library encodes the link between them.

| View | What it is | Where it lives |
|---|---|---|
| **Internal** | REX's own products, grouped into our nine **suites**. A suite is a REX strategy/marketing concept, not a market taxonomy. | `INTERNAL_SUITES`, `suite_of()` |
| **External** | The whole ETP market under an objective taxonomy (Leveraged & Inverse, Income/Covered-Call, Crypto, Defined-Outcome, Thematic, …) plus a single-stock-vs-index axis. How we see competitors. | the classification system (`auto_classify` + `fund_master.csv`) |

**Why they collide (real examples):**
- **T-REX** and **MicroSectors** are both externally *Leveraged & Inverse*. What
  splits them internally is the underlier: T-REX = **single stock**, MicroSectors =
  **index / basket**.
- **Growth & Income** is externally *Income / Covered-Call*, but specifically the
  **single-stock** kind. **Equity Premium Income** is the *index/basket* kind.

So: one external class can split across two internal suites, and two internal suites
can share one external class. Neither view is "more correct" — they answer different
questions (how do WE sell it vs. where does it sit in the market).

---

## 2. The nine internal suites

Canonical order and names. The **display name is the value stored in `rex_suite`** —
this is what reports group and count by.

| Suite (display) | Abbr | External class | Definition | Classifier rule |
|---|---|---|---|---|
| **MicroSectors** | | L&I / Index-Basket | REX's index/basket leverage & inverse suite (BMO-issued ETNs, REX-branded) | name starts `MICROSECTORS` |
| **T-REX** | | L&I / Single-Stock | REX's single-stock leverage & inverse suite (2X Long + 2X Short on one underlier) | name starts `T-REX` |
| **Equity Premium Income** | EPI | Income / Covered-Call (index) | Covered-call income on indices and baskets | name starts `REX` + contains `PREMIUM INCOME` |
| **Growth & Income** | G&I | Income / Covered-Call (single-stock) | Income written on single stocks | name starts `REX` + contains `GROWTH & INCOME` |
| **IncomeMax** | | Income / Option-Strategy | Option-strategy income (ULTI) | name starts `REX` + contains `INCOMEMAX` |
| **Structured** | | Structured / Autocallable | Autocallable income (ATCL) | name starts `REX` + contains `AUTOCALL` |
| **Thematic** | | Thematic Equity | Thematic equity; currently only the Drone ETF (DRNZ) | name starts `REX` + contains `DRONE` |
| **Crypto** | | Crypto (spot/staking) | REX-Osprey crypto spot & staking | name starts `REX-OSPREY` |
| **MoneyMarket** | | Money Market / T-Bill | Laddered T-bill / money market (TLDR) | ticker override `TLDR` (name "THE LADDERED T-BILL ETF" has no REX prefix) |

**Classifier = `suite_of(ticker, fund_name)`** (returns the display name or `None`).
- **Name-first, self-maintaining.** A fund that launches tomorrow named "T-REX 2X
  LONG …" classifies as T-REX with zero manual edits. This is why the suite count
  never goes stale.
- **Ticker overrides** exist only for our funds whose *name* does not encode the
  suite (today: just `TLDR`). Kept tiny on purpose — not a dumping ground.
- Patterns are REX-name-anchored, so they can never tag a competitor.

> The old failure: `rex_suite` came from a hand-maintained ticker CSV that lagged
> every launch, so the Flow report counted T-REX one way (stale column → 40) while
> name-based reports counted another (→ 41). Name derivation removes that whole
> class of bug. **The number is an output of the one rule, not a value we hand-set.**

---

## 3. Market status — what counts in a KPI

Daily Bloomberg files carry many statuses. For a **present-day KPI** the rule is:

| Status | Role | In a present-day KPI |
|---|---|---|
| `ACTV` | **live** | Counted — real count, real AUM, real flows |
| `PEND` | **future** (may launch) | **Excluded entirely** — not a product yet (pipeline/launch views only) |
| `LIQU`, `DLST`, `ACQU`, `INAC`, `EXPD`, `TKCH`, `UNLS`, `PRNA`, … | **closed** | Present-day AUM & flow = **$0** even if Bloomberg still prints a stale figure. History stays real (a time series keeps past values; only "today" is zero). |

Encoded as: `status_role()` → `live`/`future`/`closed`; `counts_in_live_kpi()` (True
only for `ACTV`); `present_value(status, raw)` → real value / `0.0` / `None`
(excluded). **Closed is defined by exclusion** (anything not live and not future), so
a closure code we have never seen still behaves correctly.

---

## 4. Trusts — the legal wrappers

| Trust | Role |
|---|---|
| REX ETF Trust | live products |
| ETF Opportunities Trust | live products |
| World Funds Trust | live products |
| Exchange Listed Funds Trust | **pipeline only** — filed long ago, we will NOT launch from it |

`LIVE_TRUSTS` and `PIPELINE_ONLY_TRUSTS` in the library.

---

## 5. Filing fields — what the filing scripts capture

These feed directly into our **internal** pipeline (`FILING_FIELDS`):

`fund_name` · `ticker` (if available) · `effective_date` · `filing_type` ·
`filing_trust` · `filing_date`

---

## 6. Still to define (the per-report Q&A)

This library fixes *suites, statuses, trusts, views*. The next layer — **every KPI,
chart, and description in each of the 10 reports** — is defined report-by-report in a
Q&A pass, each one tracing to a source here. That is the offered next exercise.

Out of scope here (referenced, not restated): the full external classification
taxonomy (`auto_classify` + `fund_master.csv`) and the issuer/brand mapping
(`issuer_mapping.csv` + `derive_issuer_brands.py`). The library names the link to
each; their internals live in their own modules.
