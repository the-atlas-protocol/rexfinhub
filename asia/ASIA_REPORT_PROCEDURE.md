# REX Asia Report — Procedure & Data Reference

**The definitive explanation of how the REX Asia monthly report works: what the data is, where it
comes from, how it's transformed, and how to run the pipeline.**

This is the *explanatory* companion to two sibling docs:
- `MONTHLY_RUNBOOK.md` — the paste-and-run command list (the **how**).
- `POSTMORTEM.md` — the Feb-2026 incident retrospective (the **what-went-wrong-before**).

This doc is the **what is going on and why**. Read it once to understand the system; use the runbook
each month to operate it.

---

## 1. What the report is

Each month we answer one question for REX leadership:

> **Of REX's global ETP assets, how much is held by investors in Asia — and where, in what, and which way is it moving?**

Concretely, for April 2026: of REX's **$7.15B** global AUM, **$1.40B (19.6%)** sits with Asian
investors, across 6 markets and 14 broker/vendor channels. We break that down by product suite
(T-REX, MicroSectors, Income, etc.), by country (Korea dominates), and by vendor, and we decompose
the month-over-month change into **market movement** vs **estimated investor flows**.

**Deliverables (per month):**
- 3 PDFs in `reports/YYYY-MM/`: the full report + a T-REX-only cut + a MicroSectors-only cut.
- The Excel ledger `REX_Asia_Monthly_Log.xlsx` — the full shares/AUM/NAV breakdown (see §9).

**Audience:** internal (COO, leadership) and a cleaned-up version for external communications.

---

## 2. The three numbers everything is built on: Shares, NAV, AUM

Everything in this system reduces to three quantities. Understand these and the rest follows.

| Term | What it is | Source |
|---|---|---|
| **Shares** | The number of fund shares held by investors at a given broker/vendor. **The fundamental unit.** | Broker files (Grace) for reported vendors; carried-forward + back-adjusted for others |
| **NAV (price)** | Month-end net asset value per share. | Bloomberg `data_nav` sheet |
| **AUM** | Assets under management = **Shares × NAV**. | Derived |

> **The golden identity: `AUM = Shares × NAV`.** Shares are what we track; NAV comes from Bloomberg;
> AUM is the product. When a vendor sends us *dollars* instead of shares, we infer shares = USD ÷ NAV.

**Why shares-first matters:** investor *behaviour* shows up in share counts (buying/selling), while
NAV moves with the *market*. Separating them lets us split AUM changes into "the market did this"
vs "investors did this" (see §4, Flows vs Market Move).

---

## 3. Where the data comes from (sources & hierarchy)

| Data | Source | Notes |
|---|---|---|
| **Asia AUM / shares** | **Grace (HK ops)** emails monthly broker files + a summary table | Authoritative for Asia. Brokers: KSD (Korea retail), SBI/Rakuten/Monex/Matsui (Japan), MooMoo (HK/SG/MY/JP), Futu/MooMoo (HK), ViewTrade (HK/SG/TW), SYFE, Asset Plus (Thai) |
| **Global AUM (ETFs)** | Bloomberg `data_aum` sheet, month-end row, $M | |
| **Global AUM (ETNs)** | Bloomberg `microsector` sheet, raw $ | **Overwrites `data_aum`** for the 20 MicroSectors ETNs — see §5 |
| **NAV** | Bloomberg `data_nav` | What we mark to. |
| **Market price** | Bloomberg `data_price` | *Not* used for AUM (NAV is). Differs from NAV for leveraged funds. |
| **Fund lifecycle** | Bloomberg `w1` sheet (inception / delist dates) | Drives which funds exist in a given month |
| **Institutional positions** | **13F filings** (e.g., Oriental Harbour Asset Management) | Quarterly, 45-day lag |

**Priority when sources disagree:** Grace's broker data is authoritative for Asia holdings;
Bloomberg is authoritative for global AUM, NAV, and lifecycle; the `microsector` sheet beats
`data_aum` for ETNs.

---

## 4. Methodology — how each number is produced

### 4a. Reported vendors (monthly)
KSD (Korea), SBI, Rakuten, Monex, Matsui, ViewTrade. Grace sends a fresh file each month → we use
**this month's reported shares × this month's NAV**. `source_type = 'reported'`.

### 4b. Held vendors (quarterly / frozen / delayed)
Futu/MooMoo HK (no data contract — frozen), the MooMoo regional books (quarterly), SYFE, and any
vendor whose data is late. Methodology — **the disclaimer printed on the report's methodology page**:

> *"In the event of delayed data, share counts are held constant and AUM is marked-to-market based on
> the latest month-end NAV. Share counts are updated and back-adjusted once the new data is received."*

So we **carry the last-known share count and reprice it at the current month's NAV.**
`source_type = 'repriced'`. These positions have **zero estimated flows by construction** (shares
didn't change in our books — we have no flow information for them).

### 4c. Aggregate-only vendors
Some vendors (MooMoo SG/MY via Grace's summary) give only a **country total**, no per-fund
breakdown. We allocate that total across funds using prior-month share proportions, scaled to match
Grace's aggregate. `source_type = 'scaled_aggregate'`.

### 4d. Flows vs Market Move (the MoM decomposition)
Each fund's month-over-month Asia AUM change is split into two parts:

- **Market move** = `prior_Asia_AUM × (global fund AUM ratio − 1)` — the change explained by the
  fund's overall market return.
- **Flows** = `(Asia AUM change) − (market move)` — the residual, interpreted as **estimated net
  investor flows** (creations/redemptions).

Both are **estimates**, and the method is **split-immune** (it uses AUM ratios, not raw share deltas).
The report's footnote says so: *"Flow estimates derived from shares outstanding and NAV data; actual
fund flows may differ."*

### 4e. The "% in Asia" denominator
`% in Asia = Asia AUM ÷ total REX global AUM`. The denominator is the **Bloomberg total across all
~91 REX tickers** (computed live in `enrich_report_data.py`), **not** the smaller DB subset. Using
the DB subset would inflate the percentage. (This was a past bug — see §5.)

---

## 5. Critical gotchas (each one has burned us)

1. **Reverse splits break naive repricing.** When a leveraged fund reverse-splits (e.g. SMUP went
   $0.42 → $12.22 = ~29×), holding *pre-split* shares × *post-split* NAV inflates its AUM ~29×.
   **Fix:** reprice held positions by **fund-total-AUM growth** (split-immune), and carry shares
   forward unchanged. **Detect a split:** month-end price ratio > 2× or < 0.5×. (April 2026: SMUP,
   EOSU, SNDU, GLXU, BERZ, CORD.)

2. **ETN AUM in Bloomberg `data_aum` is NOTIONAL, not real.** Always overwrite with the `microsector`
   sheet for the 20 MicroSectors ETNs. Skipping this silently corrupts global AUM.

3. **NAV ≠ market price** for leveraged ETFs. The DB stores **NAV** (`data_nav`). Auditing `price_usd`
   against `data_price` (market) produces false "mismatches" — that's expected, not a bug.

4. **`enrich` must be the LAST thing before the build.** `audit_report.py`'s `--output-enriched`
   defaults to `enriched_report_data.json` and writes a *different* format (DB-global denominator,
   no narrative/month label). If it overwrites the enrich output, the cover shows "Report Month"
   instead of the month and the wrong "% in Asia". **Run audit with a temp `--output-enriched`, and
   run `enrich_report_data.py` last.**

5. **History is immutable — except deliberate corrections.** Monthly loads only write the target
   month; delisted funds still appear in historical months. The one exception is an explicit
   correction, e.g. **Oriental Harbour** (sold out per its Q1-2026 13F) was zeroed **from March 2026
   onward** — March, not February, because the 13F is a Q1/March-quarter event.

6. **Bloomberg `microsector` has no weekend rows.** For weekend month-ends, walk back to Friday.

7. **Grace's "Mastui" typo** (for Matsui) is handled by the name map — don't "fix" the source.

8. **Quarterly vendors' `shares_outstanding` column is derived, not trustworthy in isolation.** For
   repriced/aggregate vendors, shares are inferred from AUM ÷ NAV; use the AUM, not the share field.

---

## 6. The data model (tables)

| Object | Grain | Key columns |
|---|---|---|
| `etp_exchange_monthly_aum` | (fund, vendor, month) — **the core fact table** | `shares_outstanding`, `exchange_aum_usd`, `source_type`, `original_aum_usd`, `data_as_of_month_id` |
| `etp_monthly_fund` | (fund, month) | `total_aum_usd` (global), `price_usd` (**NAV**) |
| `exchange` / `country` | vendor → country | |
| `calendar_month` | month | `month_id` ↔ `month_end` |
| `etp` | fund | `ticker`, `family_id` (suite) |
| **Views** (auto-computed) | — | `country_monthly_total_aum`, `asia_family_rollup`, `*_aum_report` — derive from the fact table, so fixing the fact table cascades automatically |

`source_type` legend: **reported** (fresh broker data) · **repriced** (held + marked to NAV) ·
**scaled_aggregate** (allocated from a country total).

---

## 7. The monthly pipeline (stages & why)

Exact commands live in `MONTHLY_RUNBOOK.md`. Here is each stage and its purpose. Postgres runs on
**port 5433**; start it first if down.

1. **Extract Grace's email** → broker `.xlsx` files into `grace_data/YYYY-MM/`.
2. **Pull Bloomberg** daily file (global AUM, NAV, lifecycle).
3. **Audit Grace vs config** (`audit_grace_vs_expected.py`) — flags new / missing / unblocked vendors
   so the config (§8) gets updated *before* loading.
4. **Add the `calendar_month` row** for the new month.
5. **Refresh global AUM** (`refresh_all_months.py`) — Bloomberg → `etp_monthly_fund`, reads `w1` for
   lifecycle, applies the ETN microsector overwrite. Backs up; single transaction; aborts if Asia
   sums change. *(Note: this touches global fund AUM only, NOT the Asia fact table.)*
6. **Load Asia vendor data** (`load_month.py`) — broker files → `etp_exchange_monthly_aum`, driven by
   `config/vendor_status.yaml` (reported / repriced / scaled / zeroed per vendor). Backs up first.
7. **Generate → Enrich** — `generate_report_data.py` (DB → `report_data.json`) then
   `enrich_report_data.py` (→ `enriched_report_data.json`: headlines, flows/market, narrative).
   **Enrich is the build input.**
8. **Audit** — `comprehensive_audit.py` / `audit_report.py` (write enriched to a **temp** file, §5#4)
   + `audit_deep.py`. Reconciles source → DB → enriched → rendered and checks math invariants.
9. **Build** — `build_reports.js` renders `report_v15.html` (with the enriched JSON injected) to 3
   PDFs; `build_excel_log_v2.py` writes the Excel ledger.
10. **Review & ship** — eyeball every page, confirm, distribute.

---

## 8. Vendor lifecycle config (`config/vendor_status.yaml`)

The single source of truth for per-vendor behaviour. Update it when Grace reports a structural change.

| Status | Meaning | Repricing |
|---|---|---|
| `active` | sends a per-fund file monthly | reported |
| `active_aggregate_only` | sends a country total only | scaled_aggregate |
| `frozen_permanent` | structurally can't report (no contract) | shares-held, repriced |
| `waiting_*` | quarterly/late; awaiting data | shares-held, repriced |
| `zeroed` | portfolio emptied / sold out | no rows |

| Event | Config change |
|---|---|
| New fund launches | nothing — `w1` handles it on next refresh |
| Fund delisted | nothing — `w1` Delist Date is authoritative |
| Vendor sold all shares | `status: zeroed`, `since:`, `reason:` |
| Vendor can't report | `status: frozen_permanent` |
| Quarterly → monthly | `cadence: monthly`, set `parser:` + `file_pattern:` |
| Waiting vendor sends first data | `status: active` (or `active_aggregate_only`) |

---

## 9. The Excel ledger — `REX_Asia_Monthly_Log.xlsx`

The accessible, at-a-glance breakdown of everything the report is built on. Sheets:
- `Summary` — at-a-glance dashboard for the latest month (matches the PDFs).
- `README` — legend.
- `Funds` — the fund universe (ticker, family, lifecycle, latest AUM).
- `Vendors`, `FX` — reference.
- **One sheet per month** (`2026-04`, …): each row is a fund; columns are:
  - **Identity**: Ticker, Family, Fund Name.
  - **Per vendor**: a `shares | USD $M` column pair, grouped under a country banner.
  - **Per fund**: `Global AUM $M`, **`Price $` (NAV)**, `Global Shares (M)`, `Source`,
    **`Asia Total $M`**, `Prior Asia $M`, `MoM $M`, `MoM %`, `% in Asia`, lifecycle, notes.
  - Color coding: green = reported, orange = derived/inferred, grey = frozen, yellow = first appearance.

Every month's `Asia Total` column sums to that month's headline (April: **$1,397.92M**).

---

## 10. Audit gates

| Script | Checks |
|---|---|
| `audit_grace_vs_expected.py` | source completeness vs config (missing / new / unblocked vendors) |
| `audit_report.py` / `comprehensive_audit.py` | layer reconciliation (DB ↔ enriched ↔ PDF), math invariants, Grace cross-check |
| `audit_deep.py` | raw broker files → DB → formula re-computation → chart data |
| `full_audit.py` | DB vs Bloomberg, all months. **Note:** its global-AUM and price "mismatches" are mostly historical (pre-2026 load) or NAV-vs-market-price artifacts — expected, not blockers |

**Invariant that must always hold:** per fund, `market_move + flows = AUM change`; and
`Σ funds = Σ countries = Σ exchanges = headline Asia AUM`.

---

## 11. Glossary

- **Suite / family** — REX product line: T-REX (leveraged single-stock), MicroSectors (3× ETNs),
  Income (EPI + Growth & Income), REX-Osprey, Other.
- **Vendor / exchange** — a broker or channel through which Asian investors hold REX funds.
- **Reported / Repriced / Scaled** — see §4 / §6.
- **Flows** — estimated net investor creations/redemptions (the non-market part of AUM change).
- **Market move** — the part of AUM change explained by the fund's market return.
- **% in Asia** — Asia AUM ÷ Bloomberg total REX AUM.
- **Frozen vendor** — one that structurally cannot send data (e.g. Futu HK, no contract).

---

*Maintained alongside the pipeline code in `C:/Projects/rexfinhub/asia`. When you change methodology or add
a vendor, update this doc, the runbook, and `vendor_status.yaml` together.*
