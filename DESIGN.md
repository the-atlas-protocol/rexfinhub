# REXFINHUB DESIGN DOCUMENT v1.0

**Prepared:** 2026-03-18
**Purpose:** Authoritative specification for the complete rexfinhub redesign
**Audience:** Implementer agents, Ryu (final review)
**Constraint:** FastAPI + Jinja2 + vanilla CSS/JS. No React, no build step.
**Principle:** Intelligence platform, not marketing website.

---

## PART 1: DESIGN PHILOSOPHY

### What Rexfinhub Is

Rexfinhub is a **decision engine for an ETF issuer**. It answers:
- "What should REX file next?" (Filings + Market + Structured Notes convergence)
- "Who should we call?" (Ownership + Sales Intelligence)
- "What changed overnight?" (Morning brief across all pillars)
- "Where do we stand?" (Market position, AUM, flows, competitive landscape)

It is NOT a marketing website. It is NOT a fund brochure. It is a daily-use tool for financial professionals.

### What We Steal From Whom

| Source | What We Take | What We Ignore |
|--------|-------------|----------------|
| **Bloomberg Terminal** | Data density, semantic color, keyboard nav, compact rows, monospace for numbers | Full dark-only mode, complexity-as-status |
| **FactSet** | Sidebar filters, widget consistency, density modes, token-based design system | Desktop-only assumptions |
| **Morningstar** | Neutral chrome (data is the color), star/rating visual grammar, clean table typography | Slow load times, conservative aesthetic |
| **13Radar** | Sentiment verdict blocks, dual rankings (value vs conviction), activity badges | Consumer styling |
| **SRP** | Product type taxonomy, league tables, additive filter chips | Paywall patterns |
| **ProShares** | 6-tab detail pages, KPI strip above tabs, condensed typography | Editorial-first homepage |
| **iShares** | Goals-based discovery, "as of" timestamps, screener for large catalogs | Marketing hero, low data density |
| **Innovator ETFs** | Autocallable scatter plot (barrier vs coupon, zone coloring) | Narrow product focus |
| **RBC Structured Notes** | 4-tab product detail (Overview/Performance/Events/Documents) | Basic styling |

### Core UX Principles

1. **Navigation is fast, intelligence is deep.** Getting to a page: 1 click. Understanding what you see: progressive disclosure.
2. **Every page answers "so what?"** Data without a verdict is a spreadsheet. Every dashboard gets a prose summary.
3. **Cross-pillar threads, not silos.** When you see a ticker, you can follow it across all four pillars.
4. **Data freshness is explicit.** Every number shows "as of" date. Stale data is marked.
5. **Keyboard users are first-class.** Ctrl+K command palette is the power-user highway.
6. **Dark theme is the default.** Light theme is available but professionals prefer dark.
7. **Mobile is functional, not primary.** Tables scroll horizontally, nav collapses to accordion. Data density is a desktop concern.
8. **Green means up, red means down, and nothing else.** No decorative green. No decorative red.

---

## PART 2: DESIGN SYSTEM

### 2.1 Color Palette

**Keep existing CSS variable architecture.** Extend, don't replace.

#### Primary Palette (unchanged)
```css
--navy: #0f1923;          /* Nav, footer, dark surfaces */
--blue: #2196F3;          /* Links, primary actions */
```

#### Financial Data Colors (FIX: accessible versions)
```css
--data-positive: #059669;  /* Was #00C853 — failed WCAG AA. Emerald-600. */
--data-negative: #DC2626;  /* Rose-600. Slightly deeper than current #F44336. */
--data-neutral: #6B7280;   /* Gray-500 for unchanged/flat. */
```

#### Status Colors (unchanged — these are correct)
```css
--green: #059669;          /* EFFECTIVE — UPDATED from #00C853 */
--orange: #FF9800;         /* PENDING */
--red: #F44336;            /* DELAYED — keep for status badges, not data */
```

#### Surface Hierarchy (unchanged — already correct)
```css
--surface-0: #F8FAFC;     /* Page background */
--surface-1: #FFFFFF;     /* Cards, modals */
--surface-2: #F1F5F9;     /* Hover, nested widgets */
--surface-3: #E2E8F0;     /* Borders, separators */
```

#### Category Colors (unchanged — these encode pillar identity)
```css
--cat-li: #1E40AF;        /* Leverage & Inverse */
--cat-income: #059669;    /* Income */
--cat-crypto: #7C3AED;    /* Crypto */
--cat-defined: #D97706;   /* Defined Outcome */
--cat-thematic: #0891B2;  /* Thematic */
```

#### "Coming Soon" Color (NEW — replaces red)
```css
--coming-soon: #9CA3AF;   /* Gray-400. Muted, not alarming. */
```

#### Dark Theme Token Fix (CRITICAL)
The following tokens MUST be overridden in `[data-theme="dark"]`:
```css
[data-theme="dark"] {
  --gray-100: #1E293B;    /* Was bleeding light values */
  --gray-200: #334155;    /* Was #E5E7EB — wrong on dark */
  --border: #334155;      /* Match gray-200 dark */
  --surface-0: #0B1120;
  --surface-1: #111827;
  --surface-2: #1E293B;
  --surface-3: #334155;
}
```

### 2.2 Typography

**Keep Inter + JetBrains Mono.** Add condensed weight for headers.

```css
--font-sans: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
--font-mono: 'JetBrains Mono', 'Fira Code', Consolas, monospace;
--font-condensed: 'Inter Tight', 'Inter', sans-serif; /* NEW — for headers */
```

**Load Inter Tight from Google Fonts** (condensed variant of Inter — same family, tighter).

#### Type Scale (unchanged — already calibrated correctly)
```
--text-xs: 11px;   --text-sm: 12px;   --text-base: 13px;
--text-md: 14px;    --text-lg: 16px;    --text-xl: 20px;
--text-2xl: 24px;   --text-3xl: 32px;
```

#### Global Numeric Fix (CRITICAL — single highest-ROI CSS change)
```css
/* Apply to ALL numeric displays */
.rt-num, .kpi .num, .kpi-value, .intel-kpi .num,
.suite-metric-value, .hero-kpi-value, .ticker-val,
td[data-sort], .mono {
  font-variant-numeric: lining-nums tabular-nums;
  font-feature-settings: "tnum" 1, "lnum" 1;
}
```

### 2.3 Spacing

**Unchanged — the 4px base scale is correct.**

### 2.4 Component Updates

#### Tables
```css
/* Sticky headers — non-negotiable for long tables */
.data-table, .rt-table {
  border-collapse: separate;
  border-spacing: 0;
}
.data-table thead th, .rt-table thead th {
  position: sticky;
  top: 0;
  z-index: 2;
  background: var(--surface-1);
  border-bottom: 2px solid var(--border);
}

/* Numeric header alignment fix */
.rt-th.rt-num, th.num-col {
  text-align: right;
}

/* Compact density mode */
.table-dense td { padding: 5px 12px; }
.table-standard td { padding: 8px 12px; }
```

#### KPI Cards
```css
/* Remove consumer hover animation */
.kpi:hover, .suite-card:hover, .home-card:hover {
  transform: none;  /* Was translateY(-1px) — consumer fintech pattern */
  border-color: var(--blue);
  box-shadow: var(--shadow-md);
}

/* Add delta indicator line */
.kpi-delta {
  font-size: var(--text-xs);
  font-weight: 600;
  margin-top: 2px;
}
.kpi-delta.up { color: var(--data-positive); }
.kpi-delta.down { color: var(--data-negative); }
```

#### Verdict Block (NEW component)
```css
/* Prose summary at top of data pages */
.verdict {
  background: var(--surface-2);
  border-left: 4px solid var(--blue);
  border-radius: 0 6px 6px 0;
  padding: var(--sp-4) var(--sp-6);
  font-size: var(--text-md);
  line-height: 1.6;
  margin-bottom: var(--sp-6);
}
.verdict strong { color: var(--data-positive); }
.verdict .verdict-negative { color: var(--data-negative); }
.verdict .verdict-label {
  font-size: var(--text-xs);
  text-transform: uppercase;
  letter-spacing: 0.06em;
  color: var(--text-muted);
  margin-bottom: var(--sp-1);
}
```

#### Activity Badges (NEW — replace inline styles)
```css
.badge-new { background: #DBEAFE; color: #1E40AF; }
.badge-increased { background: #D1FAE5; color: #065F46; }
.badge-decreased { background: #FEE2E2; color: #991B1B; }
.badge-exited { background: #F3F4F6; color: #6B7280; }
.badge-unchanged { background: #F9FAFB; color: #9CA3AF; }

[data-theme="dark"] .badge-new { background: #1E3A5F; color: #93C5FD; }
[data-theme="dark"] .badge-increased { background: #064E3B; color: #6EE7B7; }
[data-theme="dark"] .badge-decreased { background: #7F1D1D; color: #FCA5A5; }
[data-theme="dark"] .badge-exited { background: #1F2937; color: #9CA3AF; }
```

#### Filter Chips (NEW — for sidebar filter panels)
```css
.filter-chip {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  padding: 4px 10px;
  background: var(--surface-2);
  border: 1px solid var(--border);
  border-radius: 16px;
  font-size: var(--text-sm);
  cursor: pointer;
}
.filter-chip .chip-remove {
  width: 14px; height: 14px;
  cursor: pointer;
  opacity: 0.6;
}
.filter-chip .chip-remove:hover { opacity: 1; }
.filter-chip-count {
  font-size: var(--text-sm);
  color: var(--text-muted);
  margin-left: var(--sp-2);
}
```

#### "As Of" Timestamp (NEW)
```css
.as-of {
  font-size: var(--text-xs);
  color: var(--text-muted);
  font-style: italic;
}
```

---

## PART 3: NAVIGATION ARCHITECTURE

### 3.1 Mega-Menu Specification

**Trigger:** Click-to-open (NOT hover). Click same trigger to close. Close on outside-click and Escape key.
**Hover enhancement:** For mouse users, open on hover after 200ms delay. This prevents accidental activation.
**Accessibility:** `aria-expanded` on trigger button. Focus trap within open panel. Escape closes.

**Structure: 4 pillars, each with 3 columns**

```
┌─ MARKET ────────────────────────────────────────────────────┐
│                                                              │
│  DASHBOARDS              ANALYSIS              TOOLS         │
│                                                              │
│  REX View                Issuer Analysis        Compare      │
│  AUM, flows, suite       Rank by AUM, flows     Side-by-side │
│  performance                                                 │
│                          Underlier Analysis      Calendar     │
│  Category View           Index/stock breakdown   Launch dates │
│  Competitive landscape                                       │
│  across 8 categories     Market Share            Monitor  ●  │
│                          Timeline + treemap      Live prices  │
│                                                              │
│  ● = Coming Soon                                             │
└──────────────────────────────────────────────────────────────┘
```

Repeat pattern for Filings, Ownership, Structured Notes.

**Each link has:**
- **Title** (bold, clickable)
- **Description** (1 line, muted text, explains what you'll see)

**"Coming Soon" items:**
- Gray text at 50% opacity
- Small `SOON` pill badge (background: var(--coming-soon), white text, 9px font, uppercase)
- `pointer-events: none`
- Placed at bottom of their column
- NO red color, NO `**` prefix

**Active page highlighting:**
- Pillar trigger: `border-bottom: 2px solid var(--blue)` when any child page is active
- Item within panel: `background: var(--surface-2)` + `border-left: 3px solid var(--blue)` on the currently active link

**Mobile behavior:**
- Hamburger opens full-screen overlay
- Each pillar is an accordion header
- Tap pillar → sub-items expand below (no columns, stacked vertically)
- All touch targets minimum 44px height

### 3.2 Navigation Content Map

#### Market Pillar
| Column | Link | URL | Description | Status |
|--------|------|-----|-------------|--------|
| Dashboards | REX View | /market/rex | Suite AUM, flows, competitive positioning | Live |
| Dashboards | Category View | /market/category | 8 ETP categories, market share | Live |
| Analysis | Issuer Analysis | /market/issuer | Rank issuers by AUM, flows, trends | Live |
| Analysis | Underlier Analysis | /market/underlier | Performance by index and stock | Live |
| Analysis | Market Share | /market/share | Share timeline + treemap | Live |
| Tools | Compare Products | /market/compare | Side-by-side AUM, flows, returns | Live |
| Tools | Launch Calendar | /market/calendar | Filing + launch timeline | Live |
| Tools | Market Monitor | /market/monitor | Live indices, commodities, crypto | SOON |

#### Filings Pillar
| Column | Link | URL | Description | Status |
|--------|------|-----|-------------|--------|
| Dashboards | Filing Activity | /dashboard | Today's SEC filings across 2,475 trusts | Live |
| Search | Search Funds | /funds/ | Find funds by name, ticker, trust | Live |
| Search | Search Filings | /filings/ | Filter by form type, date, trust | Live |
| Analysis | Filing Landscape | /screener/ | 3x/4x/5x competitive matrix | Live |
| Analysis | Bloomberg Scoring | /screener/3x-analysis | Demand-tiered product recommendations | Live |
| Analysis | 4x Analysis | /screener/4x | 4x leverage breakdown | Live |
| Tools | Evaluate Ticker | /screener/evaluate | Score a ticker for launch viability | Live |
| Tools | Fund Analytics | /analytics/ | Fund-level performance analytics | SOON |
| Tools | Fund Withdrawals | — | Track fund closures and withdrawals | SOON |

#### Ownership Pillar
| Column | Link | URL | Description | Status |
|--------|------|-----|-------------|--------|
| Dashboards | Market Overview | /intel/ | $289B ETP ownership landscape | Live |
| Dashboards | REX Quarter Report | /intel/rex | 120 institutions, $452M in REX products | Live |
| Search | Browse Institutions | /holdings/ | 10,535 institutions by AUM | Live |
| Search | Crossover Analysis | /holdings/crossover | Prospects: competitor holders to convert | Live |
| Analysis | Sales Intelligence | /intel/rex/sales | Momentum, concentration, state rankings | Live |
| Analysis | New REX Filers | /intel/rex/filers | Who started holding REX this quarter | Live |
| Analysis | Competitor Analysis | /intel/competitors | Competitor issuer ownership | Live |
| Tools | Country Intel | /intel/country | International holder breakdown | Live |
| Tools | Market Trends | /intel/trends | QoQ ownership trends | Live |
| Tools | Head-to-Head | /intel/head-to-head | Direct competitor comparison | SOON |

#### Structured Notes Pillar
| Column | Link | URL | Description | Status |
|--------|------|-----|-------------|--------|
| Dashboards | Market Overview | /notes/ | 594K products, 19 issuers, 30 years | Live |
| Dashboards | Issuer Dashboard | /notes/issuers | Market share, league tables | Live |
| Search | Product Search | /notes/search | Filter by issuer, type, underlier, coupon | Live |
| Analysis | Underlier Analysis | /notes/underliers | Most popular underliers in structured notes | NEW |
| Analysis | Autocallable Intel | /notes/autocallables | Scatter: barrier vs coupon, zone coloring | NEW |
| Tools | Barrier Distribution | — | Histogram of barrier levels across products | NEW (embed in overview) |

#### Standalone Links (outside pillars)
| Link | URL | Notes |
|------|-----|-------|
| Exports | /downloads/ | Data export center |
| Admin | /admin/ | Conditional — admin only |

### 3.3 Ticker Bar Specification

**Position:** Fixed below nav, full-width, 36px height.
**Content:** Two groups separated by a styled divider:

**Group 1 — Market Indices** (from yfinance or cached API):
`S&P 500 | NASDAQ | VIX | Gold | BTC`

**Group 2 — REX Top Products** (from mkt_master_data, is_rex=1):
`SOXL $X.XB | BULZ $X.XB | FNGU $X.XB | FEPI $X.XM | ...`

**Display per item:**
```
SOXL  $2.1B  ▲ +1.4%
```
- Ticker: bold, 12px
- Value: mono, 12px
- Change: colored (green/red), 11px, includes arrow

**Animation:** CSS `translateX` scroll, ~50px/sec. Pause on hover.
**Accessibility:** `role="marquee"`, `aria-hidden="true"` on duplicate content.
**Mobile:** Hidden below 768px.

### 3.4 Command Palette Enhancement (Ctrl+K)

**Current:** Search across pages, products, trusts, funds, filings.
**Enhancement:** Add function routing — typing a keyword offers navigation:

| Input | Suggested Result |
|-------|-----------------|
| "market" | → Market: REX View, Category View, Issuer Analysis |
| "SOXL" | → Fund: SOXL detail | Market: SOXL in compare | Ownership: SOXL holders | Notes: SOXL as underlier |
| "screener" | → Filings: Filing Landscape, Bloomberg Scoring |
| "new filers" | → Ownership: New REX Filers |

This is the cross-pillar thread. A single ticker search shows every surface where that entity appears.

### 3.5 Footer Specification

**Background:** `var(--navy)` — dark navy, matching the nav.
**Text:** White headings, `rgba(255,255,255,0.7)` for links.
**Layout:** 5 columns on desktop, stacked on mobile.

```
┌─────────────────────────────────────────────────────────────────────┐
│  REX FINANCIAL        MARKET          FILINGS        OWNERSHIP     │
│  INTELLIGENCE HUB                                                  │
│                       REX View        Filing Activity  Market      │
│  SEC filings, market  Category View   Search Funds     Overview   │
│  intelligence,        Issuer Analysis Search Filings   REX Report  │
│  ownership analytics, Underlier       Landscape        Institutions│
│  structured products. Compare         Bloomberg Score  Sales Intel │
│                                                                    │
│  STRUCTURED NOTES     DATA & TOOLS                                 │
│  Market Overview      Data Exports                                 │
│  Issuers              Email Digest                                 │
│  Product Search       Subscribe                                    │
│                                                                    │
│  ──────────────────────────────────────────────────────────────     │
│  Data: SEC EDGAR · Bloomberg · yfinance    Last sync: Mar 18 2026  │
│  © 2026 REX Financial. Internal use only.                          │
└─────────────────────────────────────────────────────────────────────┘
```

---

## PART 4: PAGE-BY-PAGE DESIGN MAP

### 4.1 Home Page — The Morning Brief

**Current:** Hero banner + 4 sections of cards.
**New concept:** Daily intelligence brief — "What changed since you last looked."

**Layout:**
```
┌─────────────────────────────────────────────────────────┐
│  REX FINANCIAL INTELLIGENCE HUB                         │
│  [4 KPI Cards: AUM | Flows | Today's Filings | Instit.]│
│  as of Mar 18, 2026                                     │
├─────────────────────────────────────────────────────────┤
│                                                         │
│  ┌─ TODAY'S BRIEF ────────────────────────────────────┐ │
│  │ 3 new filings since yesterday. ProShares filed     │ │
│  │ 485BPOS for UltraPro QQQ. 2 institutions exited   │ │
│  │ BULZ (total REX holders now 118). Goldman issued   │ │
│  │ 14 new autocallables referencing SOXL.             │ │
│  └────────────────────────────────────────────────────┘ │
│                                                         │
│  ┌─ MARKET ─────────┐  ┌─ FILINGS ──────────────────┐ │
│  │ REX View       → │  │ Filing Activity          → │ │
│  │ Category View  → │  │ Filing Landscape         → │ │
│  │ Issuer Analysis→ │  │ Bloomberg Scoring        → │ │
│  │ Compare        → │  │ Evaluate Ticker          → │ │
│  └──────────────────┘  └────────────────────────────┘ │
│                                                         │
│  ┌─ OWNERSHIP ──────┐  ┌─ STRUCTURED NOTES ─────────┐ │
│  │ Market Overview → │  │ Market Overview          → │ │
│  │ REX Report     → │  │ Issuer Dashboard         → │ │
│  │ Institutions   → │  │ Product Search           → │ │
│  │ Sales Intel    → │  │ Underlier Analysis       → │ │
│  └──────────────────┘  └────────────────────────────┘ │
│                                                         │
│  ┌─ DATA FRESHNESS ─────────────────────────────────┐  │
│  │ Market data: Mar 18  │ Filings: Mar 18          │  │
│  │ 13F data: Q4 2025    │ Structured notes: Mar 17 │  │
│  └──────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────┘
```

**The morning brief is rule-based (no LLM needed).** Server-side logic:
1. Count filings since yesterday
2. Check for notable filings (485BPOS from known competitors)
3. Compare current quarter 13F totals to prior
4. Count new structured notes referencing REX tickers
5. Assemble into 2-3 sentences, pass as `brief_text` to template

**KPI cards remain** but add delta indicators:
- AUM: `$3.8B ▲ +2.1% MoM`
- Flows: `$-41.6M` (red if negative)
- Filings: `12 today` (with "3 new since yesterday" if applicable)
- Institutions: `7,182 tracked`

**Pillar cards remain** but simplified — 4 links each (most important pages), not descriptive cards. The nav handles discovery; the home page handles quick access.

**Data freshness strip** at the bottom shows when each data source was last updated.

### 4.2 Market Pages

**REX View (/market/rex):** Already professional. Changes:
- Add verdict block: "REX AUM at $3.8B, up 2.1% MoM. GDXU leads at $2.1B. Flows negative this week (-$41.6M) driven by BULZ outflows."
- Add "as of" timestamp to KPI cards
- Remove hover translateY on suite cards
- Ensure tabular-nums on all numeric displays

**Category View (/market/category):** Already good. Changes:
- Add "as of" timestamp
- Sticky table headers

**Issuer Analysis (/market/issuer):** Changes:
- Add issuer detail pages (/market/issuer/{slug}) — tabbed: Overview | Products | Trends
- Ensure treemap loads correctly

**Market Share (/market/share):** Surface in nav. No design changes needed.

**Compare (/market/compare):** Currently placeholder. Implement:
- Side-by-side comparison of 2-4 products
- Columns: AUM, Flows (1W/1M/3M), Returns (1M/3M/1Y), Expense Ratio, Inception
- Product selector: typeahead search
- Chart: overlaid AUM trend lines

**Market Monitor (/market/monitor):** Mark as SOON in nav. When implemented:
- 4 sections: Indices | Commodities | Crypto | REX Products
- Auto-refresh every 60s (yfinance)
- Each item: ticker, price, daily change, sparkline

### 4.3 Filings Pages

**Filing Activity (/dashboard):** Already professional. Changes:
- Add verdict block: "12 filings today across 8 trusts. 3 are 485BPOS (fund updates). ProShares filed twice."
- Sticky table headers on filing table
- "As of" on trust grid

**Filing List (/filings/):** Changes:
- Filter chip display above results showing active filters
- Result count: "Showing 47 of 26,341 filings"
- Sticky headers

**Fund List (/funds/):** Changes:
- Add filter chip display
- Result count badge

**Fund Detail (/funds/{series_id}):** Changes:
- Add tabs: Overview | Filing History | 13F Holders
- "13F Holders" tab shows institutional holders of this specific fund (link to ownership pillar)
- This IS the cross-pillar thread — fund detail links to ownership data

**Screener Landscape (/screener/):** Already strong. Changes:
- Add "as of" on Bloomberg data
- Sticky headers on matrix

**Bloomberg Scoring (/screener/3x-analysis):** Already strong. Minimal changes.

**Evaluate Ticker (/screener/evaluate):** Changes:
- After evaluation, show cross-pillar context:
  - "This ticker appears in X structured notes products"
  - "Y institutions hold this ticker via 13F"
  - This is the convergence signal preview

### 4.4 Ownership Pages — Major Upgrades

**Market Overview (/intel/):** Changes:
- Add verdict block: "$289B in ETP institutional ownership. Top holder: Vanguard. REX products: $452M across 120 institutions."
- Replace static KPIs with delta indicators (+/- vs prior quarter)
- "As of Q4 2025" timestamp prominent

**REX Quarter Report (/intel/rex):** Changes:
- Add verdict block (auto-generated narrative): "In Q4 2025, REX products attracted 8 new institutional filers, the highest in 4 quarters. SOXL led with $31M in new interest."
- Dual ranking cards: "Top 10 by Value" | "Top 10 by Conviction" side-by-side
- New filers highlighted in a separate block above the full table

**Browse Institutions (/holdings/):** Major changes:
- Add holder type filter pills: All | Investment Adviser | Hedge Fund | Pension | Bank | Other
- Add "REX Holders" / "Prospects" toggle
- Show type counts in KPI row
- Sticky headers, compact density

**Fund Holdings (/holdings/fund/{ticker}):** Major changes:
- Add verdict block: "47 institutions hold $452M in SOXL. 8 new buyers (+$31M). 5 sellers (-$12M). Net: Bullish (+$19M)."
- Add "% of Fund" column (holder value / total fund value)
- Dual ranking: "Top 10 by Value" vs "Top 10 by Conviction"
- Dual-axis trend chart: AUM bars + holder count line
- "New This Quarter" highlighted section above table
- Activity badges as CSS classes (not inline styles)
- Quarter selector dropdown

**Institution Detail (/holdings/{cik}):** Changes:
- Add "% of ETP Portfolio" column
- Add tabs: Current Holdings | History | Crossover Opportunities
- Link to fund detail pages for each holding

**Crossover (/holdings/crossover):** Changes:
- Add horizontal bar chart: top 20 prospects by priority score
- Priority score: `comp_value * log(1 + comp_positions)`
- Color bars by number of competitor positions

**Sales Intelligence (/intel/rex/sales):** Changes:
- Default tab = Concentration (already done)
- Add verdict block for each tab
- Improve momentum tab with directional arrows

### 4.5 Structured Notes Pages — Major Upgrades

**Market Overview (/notes/):** Changes:
- Replace single-color annual bar chart with stacked bar (Income/Growth/Protection/Leverage)
- Add treemap for issuer market share (reuse market/treemap.html pattern)
- Add underlier frequency section: top 20 underliers by product count
- Add barrier distribution histogram
- Add coupon rate distribution histogram
- Add "Market Intelligence" summary card (auto-generated prose)
- Add product type distribution donut chart

**Issuer Dashboard (/notes/issuers):** Changes:
- Replace flat card grid with treemap (size = product count, color = market share)
- Add secondary view: issuer market share by year (trajectory chart)
- Each issuer card links to issuer detail page

**Issuer Detail (/notes/issuers/{name}):** NEW PAGE
- Total products, date range, avg coupon, avg barrier
- Issuance by year chart (issuer-specific)
- Product type breakdown donut
- Top underliers table
- Recent products list
- Autocallable scatter plot (if applicable)

**Product Search (/notes/search):** Major redesign:
- Replace inline 3-filter bar with sidebar filter panel:
  - Issuer (multi-select checkboxes with count badges)
  - Product Category (4 tiles: Income/Growth/Protection/Leverage)
  - Product Type (normalized sub-types)
  - Underlier (typeahead text search)
  - Coupon Range (min/max inputs or slider)
  - Barrier Range (min/max inputs)
  - Maturity Range (date inputs)
  - Filing Year (checkbox list)
  - Lifecycle Status (Active/Matured/Unknown)
- Additive filter chips above results
- Result count: "Showing 50 of 147,832 matching products"
- Pagination (remove 100-row hard limit, add LIMIT/OFFSET)
- CUSIP links to product detail page

**Product Detail (/notes/product/{cusip}):** NEW PAGE (RBC pattern)
- Tab 1 — Overview: CUSIP, issuer, type, underlier (clickable badges), coupon, barrier, maturity, filing date
- Tab 2 — Product Terms: full extracted name, SEC filing link, "View Prospectus" button
- Tab 3 — Market Context: how this product's coupon compares to average for that issuer+type
- Tab 4 — SEC Filing: direct link to SEC EDGAR

**Underlier Analysis (/notes/underliers):** NEW PAGE
- Ranked table: Underlier | Product Count | % of Total | Issuer Count | Avg Coupon | Avg Barrier | Most Common Type
- Top 20 by default, expandable
- Click underlier → filtered product search
- Cross-pillar: link to market data for that underlier

**Autocallable Intel (/notes/autocallables):** NEW PAGE (future, after product_type normalization)
- Innovator-style scatter plot: X = barrier level, Y = coupon rate
- Dots colored by zone (callable+payable, not callable+payable, not callable+not payable)
- Filter by issuer
- Hover: product name, CUSIP, underlier, maturity

### 4.6 Exports & Utility Pages

**Downloads (/downloads/):** Minimal changes — already functional.
**Email Digest (/digest/subscribe):** Minimal changes.
**Admin (/admin/):** Add tabs to organize sections (Trust Requests | Subscribers | Digest | Scoring | Status).

---

## PART 5: CROSS-PILLAR INTELLIGENCE

### 5.1 Entity Threading (via Command Palette)

When a user searches "SOXL" in Ctrl+K, results show:
1. **Market:** SOXL in REX View (AUM, flows)
2. **Filings:** SOXL fund detail (filing history)
3. **Ownership:** SOXL institutional holders
4. **Notes:** Structured notes referencing SOXL as underlier

This is NOT a separate page — it's enhanced search results that link to existing pages.

### 5.2 Cross-Pillar Context Blocks

On key pages, add small "See also" blocks that connect to other pillars:

**On Fund Detail (/funds/{series_id}):**
> "13F: 47 institutions hold SOXL ($452M) · Notes: 312 structured products reference SOXL"

**On Fund Holdings (/holdings/fund/{ticker}):**
> "Market: SOXL AUM $2.1B · Filings: Last filing Mar 15 · Notes: 312 products as underlier"

**On Product Search (when filtering by underlier "SOXL"):**
> "Market: SOXL AUM $2.1B · Ownership: 47 institutional holders"

These are 1-line strips, not full sections. They provide the thread without cluttering the page. They link to the relevant page in the other pillar.

### 5.3 Morning Brief (Home Page)

Rule-based, server-side generated. See Section 4.1.

### 5.4 Convergence Signals (Future — Phase 3)

A dedicated page showing underliers/tickers where multiple pillars show activity:
- High filing activity (multiple issuers filing 3x/4x)
- Growing institutional ownership
- High structured notes issuance
- Strong market performance

This is the "product opportunity score" — but it's Phase 3 work.

---

## PART 6: IMPLEMENTATION PRIORITY

### Phase 1 — Visual Foundation (one-shot, all CSS/template changes)

These changes touch no backend logic and can be done in parallel:

1. **CSS global fixes:**
   - Fix `--green` to `#059669` (WCAG compliance)
   - Add `font-variant-numeric: tabular-nums` globally
   - Fix dark theme token overrides (gray-200, border, surfaces)
   - Add sticky table headers
   - Fix numeric header alignment
   - Remove hover translateY on cards
   - Add .badge-new/.badge-increased/.badge-decreased classes
   - Add .verdict class
   - Add .filter-chip class
   - Add .as-of class
   - Add Inter Tight font import

2. **Navigation overhaul (base.html):**
   - Replace hover dropdowns with click-to-open mega-menu
   - 3-column layout per pillar
   - Descriptions under each link
   - Active page highlighting (pillar + item)
   - Coming soon: gray + SOON badge (not red)
   - Mobile accordion
   - All hidden pages surfaced

3. **Footer upgrade (base.html):**
   - Dark navy background
   - 5-column layout
   - Data freshness line
   - Move inline footer CSS to style.css

4. **Ticker bar enhancement (ticker.js + dashboard.py):**
   - Add market indices (S&P 500, NASDAQ, VIX, Gold, BTC)
   - Better spacing, larger font
   - Pause on hover
   - Hide on mobile

5. **Home page redesign (home.html):**
   - Add morning brief block (rule-based)
   - KPI cards with delta indicators
   - Simplified pillar cards (4 links each)
   - Data freshness strip

### Phase 2 — Page-Level Upgrades

6. **Ownership verdict blocks + data columns:**
   - Sentiment verdict on fund holdings pages
   - % of Fund column
   - Dual ranking cards (value vs conviction)
   - Holder type filter on institution browser
   - Activity badge CSS class migration
   - New filer highlight section

7. **Structured notes taxonomy + search:**
   - `normalize_product_type()` mapping function
   - 4-category tiles on search (Income/Growth/Protection/Leverage)
   - Sidebar filter panel (replace 3-filter bar)
   - Pagination (remove 100-row cap)
   - Stacked annual issuance chart
   - Issuer treemap
   - Underlier frequency table

8. **Cross-pillar context blocks:**
   - 1-line "See also" strips on fund detail, fund holdings, notes search
   - Enhanced Ctrl+K results showing all pillars

### Phase 3 — New Pages & Intelligence

9. **New routes:**
   - /notes/issuers/{name} — issuer detail page
   - /notes/product/{cusip} — product detail page (RBC 4-tab pattern)
   - /notes/underliers — underlier analysis page
   - /notes/autocallables — autocallable scatter plot (requires product_type normalization)

10. **Convergence signals page** (future)

---

## PART 7: VISUAL TEST CASES

Every change must pass these tests. Use Playwright to screenshot and verify.

### Navigation Tests
- [ ] Click "Market" in nav → mega-menu opens with 3 columns
- [ ] Click "Market" again → menu closes
- [ ] Press Escape → menu closes
- [ ] Click outside menu → menu closes
- [ ] On /market/rex → "Market" pillar trigger has blue bottom border
- [ ] On /market/rex → "REX View" item in dropdown has left accent + background highlight
- [ ] "Market Monitor" shows gray text + SOON badge, is not clickable
- [ ] Mobile: hamburger opens → tap "Market" → items expand as accordion
- [ ] All touch targets ≥ 44px height

### Ticker Bar Tests
- [ ] Ticker bar visible below nav on desktop
- [ ] Shows both market indices AND REX products
- [ ] Green/red colors match data direction (positive = green, negative = red)
- [ ] Hover pauses animation
- [ ] Hidden on mobile (< 768px)
- [ ] Real data, not fallback zeros

### Typography & Color Tests
- [ ] No `#00C853` green anywhere on white background (search CSS for old value)
- [ ] All numeric columns are right-aligned (including headers)
- [ ] Numbers in tables use tabular-nums (visually: columns of numbers align by decimal)
- [ ] No hover translateY animation on any card
- [ ] "As of" timestamps visible on every KPI card

### Footer Tests
- [ ] Footer background is dark navy (#0f1923)
- [ ] Footer text is white/light gray, legible
- [ ] 5 columns on desktop, stacked on mobile
- [ ] "Last sync" date is visible

### Dark Theme Tests
- [ ] Toggle to dark → no light-colored borders bleeding through
- [ ] All surfaces use correct dark values (not light --gray-200)
- [ ] Activity badges are legible in dark mode
- [ ] Charts re-render with dark grid/label colors

### Home Page Tests
- [ ] Morning brief text block is visible and populated
- [ ] KPI cards show delta indicators (▲/▼ with color)
- [ ] 4 pillar cards visible with quick links
- [ ] Data freshness strip shows last sync dates

### Ownership Page Tests (Phase 2)
- [ ] Fund holdings page shows verdict block at top
- [ ] Verdict includes: holder count, new buyers, sellers, net sentiment
- [ ] "% of Fund" column visible and right-aligned
- [ ] Dual ranking cards visible: "Top 10 by Value" | "Top 10 by Conviction"
- [ ] Institution browser has holder type filter pills
- [ ] Activity badges use CSS classes (inspect element: no inline background-color)

### Structured Notes Tests (Phase 2)
- [ ] Overview page shows stacked bar chart (4 colors for 4 categories)
- [ ] Issuer section shows treemap, not flat cards
- [ ] Search page has sidebar filter panel (left side)
- [ ] Filter chips appear above results when filters applied
- [ ] Result count updates as filters change
- [ ] Pagination controls visible (no 100-row cap)

---

## APPENDIX: PREREQUISITE DATA WORK

Before Phase 2 structured notes work can begin:

1. **Product type normalization:** Create `normalize_product_type(raw: str) -> str` in `notes.py`.
   Map raw 424B2 names to 8-10 canonical types, then to 4 categories.
   Example mappings:
   - "Autocallable Contingent Coupon Barrier Notes" → "Autocallable" → Income
   - "Auto-Callable Contingent Coupon Notes" → "Autocallable" → Income
   - "Buffered Return Enhanced Notes" → "Buffered Note" → Growth
   - "Principal Protected Notes" → "Principal Protected" → Protection

2. **Maturity status derivation:** Add computed field in Python:
   - `maturity_date > today` → "Active"
   - `maturity_date <= today` → "Matured"
   - `maturity_date is None` → "Unknown"

3. **Database indexes:** Add indexes on `(parent_issuer, product_type)` and `maturity_date` for the structured_notes.db before adding paginated queries on 594K rows.

4. **Market indices API:** Add yfinance endpoint for S&P 500, NASDAQ, VIX, Gold, BTC prices for the ticker bar. Cache with 5-minute TTL.

---

## PART 8: MOTION, POLISH & VISUAL DELIGHT

This section covers what makes the site feel *alive* — the difference between
"correct" and "I want to use this every day."

### 8.1 Page Transitions & Loading

**Page load progress bar (already exists — enhance it):**
```css
#page-progress {
  height: 3px;
  background: linear-gradient(90deg, var(--blue), #00BCD4);
  transition: width 0.3s ease;
}
```
- The gradient makes it feel more alive than a solid bar.
- Keep the loading-bar.js behavior, just upgrade the visual.

**Skeleton shimmer animation (enhance existing):**
```css
@keyframes shimmer {
  0% { background-position: -200% 0; }
  100% { background-position: 200% 0; }
}
.skeleton {
  background: linear-gradient(
    90deg,
    var(--surface-2) 25%,
    var(--surface-3) 50%,
    var(--surface-2) 75%
  );
  background-size: 200% 100%;
  animation: shimmer 1.5s ease-in-out infinite;
  border-radius: 4px;
}
```
- Apply to ALL loading states: KPI cards, chart containers, table rows.
- Show 5-8 skeleton rows while table data fetches.
- Show skeleton KPI cards (gray rectangles in the KPI shape) while /api/v1/home-kpis loads.

**Chart entry animations (Chart.js built-in):**
```javascript
// All Chart.js instances should use these animation defaults
Chart.defaults.animation = {
  duration: 600,
  easing: 'easeOutQuart'
};
// Bars grow upward, lines draw left-to-right
// This is free — Chart.js does it by default, just ensure duration is set
```

### 8.2 Micro-Interactions

**Card hover (institutional, not consumer):**
```css
/* Subtle border + shadow shift — card stays in place */
.home-card, .intel-card, .suite-card, .kpi {
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
}
.home-card:hover, .intel-card:hover, .suite-card:hover {
  border-color: var(--blue);
  box-shadow: 0 2px 12px rgba(33, 150, 243, 0.08);
}
/* NO transform, NO translateY, NO scale */
```

**Table row hover (data flash — Bloomberg-inspired):**
```css
tbody tr {
  transition: background-color 0.15s ease;
}
tbody tr:hover {
  background-color: rgba(33, 150, 243, 0.04);
  /* Very subtle blue tint — just enough to track your eye position */
}
[data-theme="dark"] tbody tr:hover {
  background-color: rgba(33, 150, 243, 0.08);
}
```

**Button press feedback:**
```css
.btn:active {
  transform: scale(0.97);
  transition: transform 0.1s ease;
}
```

**Filter pill activation:**
```css
.pill {
  transition: background-color 0.15s ease, color 0.15s ease, box-shadow 0.15s ease;
}
.pill.active {
  box-shadow: 0 0 0 2px var(--blue);
}
```

**Dropdown/mega-menu open:**
```css
.mega-panel {
  opacity: 0;
  transform: translateY(-8px);
  transition: opacity 0.2s ease, transform 0.2s ease;
  pointer-events: none;
}
.mega-panel.open {
  opacity: 1;
  transform: translateY(0);
  pointer-events: auto;
}
```
- Panels fade in and slide down slightly — feels responsive, not jarring.

**Verdict block entrance:**
```css
.verdict {
  animation: fadeSlideIn 0.4s ease-out;
}
@keyframes fadeSlideIn {
  from { opacity: 0; transform: translateY(8px); }
  to { opacity: 1; transform: translateY(0); }
}
```

**KPI number count-up (on page load):**
```javascript
// Animate KPI values from 0 to target
function animateValue(el, target, duration) {
  var start = 0;
  var startTime = null;
  function step(timestamp) {
    if (!startTime) startTime = timestamp;
    var progress = Math.min((timestamp - startTime) / duration, 1);
    var eased = 1 - Math.pow(1 - progress, 3); // easeOutCubic
    el.textContent = formatValue(start + (target - start) * eased);
    if (progress < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}
// Duration: 800ms. Fast enough to not annoy, slow enough to notice.
// Apply to: AUM, institution count, filing count, product count.
```

### 8.3 Scroll-Triggered Effects

**Section fade-in on scroll (lightweight, no library):**
```css
.section-animate {
  opacity: 0;
  transform: translateY(16px);
  transition: opacity 0.5s ease, transform 0.5s ease;
}
.section-animate.visible {
  opacity: 1;
  transform: translateY(0);
}
```
```javascript
// IntersectionObserver — runs once per element, no scroll jank
var observer = new IntersectionObserver(function(entries) {
  entries.forEach(function(entry) {
    if (entry.isIntersecting) {
      entry.target.classList.add('visible');
      observer.unobserve(entry.target);
    }
  });
}, { threshold: 0.1 });

document.querySelectorAll('.section-animate').forEach(function(el) {
  observer.observe(el);
});
```
- Apply to: home page pillar cards, KPI rows on sub-pages, chart containers.
- NOT on tables or nav — those must appear instantly.

**Sticky nav shadow on scroll:**
```css
.sticky-nav {
  transition: box-shadow 0.2s ease;
}
.sticky-nav.scrolled {
  box-shadow: 0 2px 16px rgba(0, 0, 0, 0.12);
}
```
```javascript
window.addEventListener('scroll', function() {
  document.querySelector('.sticky-nav')
    .classList.toggle('scrolled', window.scrollY > 10);
});
```

### 8.4 Data Visualization as Art

The charts and data visualizations ARE the visual interest. No stock photos.

**Treemap (issuer market share):**
- Already exists in market section. Reuse for structured notes issuers.
- The treemap IS the hero visual on the issuer page — large, colorful, interactive.
- Size encodes product count, color encodes category.

**Sparklines in KPI cards:**
```
┌─────────────────────────┐
│  REX AUM                │
│  $3.8B  ▲ +2.1%        │
│  ╱╲    ╱──╲   ╱──       │  ← 40px Chart.js sparkline
│ ╱  ╲╱╱    ╲_╱           │
└─────────────────────────┘
```
- Tiny line chart (40px tall, no axes, no labels) showing 12-month trend.
- Chart.js line chart with `aspectRatio: 3`, no grid, no ticks, 2px line width.
- Shows direction at a glance — is AUM trending up or down?

**Autocallable scatter plot (structured notes):**
- X: barrier level, Y: coupon rate
- Dots sized by notional (if available) or uniform
- Three color zones with subtle background fills
- This IS the "wow" chart for the CEO demo

**Dual-axis charts (ownership):**
- Bars for AUM + line for holder count
- Two y-axes, clearly labeled
- The divergence between these two signals IS the insight

**Donut charts with center label:**
```
    ╭─────╮
   │ ╭───╮ │
   │ │67%│ │    ← Center shows dominant category
   │ ╰───╯ │
    ╰─────╯
```
- Chart.js doughnut with `cutout: '70%'` and a center text plugin.
- Used for: product type distribution, issuer market share, holder type breakdown.

### 8.5 Iconography

**No stock photos. No emoji. SVG icons only.**

Use Feather Icons (already referenced in base.html) or Lucide Icons (Feather fork, more icons):
- Trending Up / Trending Down for KPI deltas
- Search for search fields
- Filter for filter panels
- Download for export buttons
- ChevronDown for dropdown triggers
- ExternalLink for SEC filing links
- Building for institution icons
- Briefcase for fund icons
- FileText for filing icons
- BarChart2 for chart sections

```html
<!-- Example: inline SVG icon -->
<svg class="icon" width="16" height="16" viewBox="0 0 24 24"
     fill="none" stroke="currentColor" stroke-width="2">
  <polyline points="23 6 13.5 15.5 8.5 10.5 1 18"></polyline>
</svg>
```

```css
.icon {
  width: 16px;
  height: 16px;
  vertical-align: -2px;
  stroke: currentColor;
}
.icon-sm { width: 14px; height: 14px; }
.icon-lg { width: 20px; height: 20px; }
```

### 8.6 Hero Section Visual Treatment

The home page hero should feel premium without stock photos:

```css
.home-hero {
  background: linear-gradient(
    135deg,
    var(--navy) 0%,
    #1a2a3a 40%,
    #0D3B66 70%,
    #0f1923 100%
  );
  /* Subtle animated gradient gives it life */
  background-size: 200% 200%;
  animation: heroGradient 8s ease-in-out infinite alternate;
  color: white;
  padding: var(--sp-16) var(--sp-8);
  border-radius: 0 0 12px 12px;
}
@keyframes heroGradient {
  0% { background-position: 0% 50%; }
  100% { background-position: 100% 50%; }
}
```
- The gradient shifts slowly — barely noticeable but adds depth.
- Navy tones only — stays institutional, never flashy.
- KPI cards inside the hero get a glass effect:

```css
.hero-kpi-box {
  background: rgba(255, 255, 255, 0.06);
  backdrop-filter: blur(8px);
  border: 1px solid rgba(255, 255, 255, 0.1);
  border-radius: 8px;
  padding: var(--sp-6);
}
```

### 8.7 Empty States

When a page has no data, don't show a blank table. Show:

```css
.empty-state {
  text-align: center;
  padding: var(--sp-12) var(--sp-8);
  color: var(--text-muted);
}
.empty-state-icon {
  width: 48px;
  height: 48px;
  margin: 0 auto var(--sp-4);
  opacity: 0.4;
  stroke: var(--text-muted);
}
.empty-state-title {
  font-size: var(--text-lg);
  font-weight: 600;
  margin-bottom: var(--sp-2);
}
.empty-state-desc {
  font-size: var(--text-sm);
  margin-bottom: var(--sp-4);
}
```
```html
<div class="empty-state">
  <svg class="empty-state-icon"><!-- Search icon --></svg>
  <div class="empty-state-title">No filings match your filters</div>
  <div class="empty-state-desc">Try adjusting the date range or form type.</div>
  <button class="btn btn-sm" onclick="clearFilters()">Clear all filters</button>
</div>
```

### 8.8 Toast Notifications

For feedback on actions (export started, filter applied, etc.):

```css
.toast {
  position: fixed;
  bottom: var(--sp-6);
  right: var(--sp-6);
  background: var(--navy);
  color: white;
  padding: var(--sp-3) var(--sp-6);
  border-radius: 8px;
  font-size: var(--text-sm);
  box-shadow: var(--shadow-lg);
  animation: toastIn 0.3s ease-out;
  z-index: 9999;
}
@keyframes toastIn {
  from { opacity: 0; transform: translateY(16px); }
  to { opacity: 1; transform: translateY(0); }
}
.toast.fade-out {
  animation: toastOut 0.3s ease-in forwards;
}
@keyframes toastOut {
  to { opacity: 0; transform: translateY(16px); }
}
```
- Auto-dismiss after 3 seconds.
- Use for: "CSV export started", "Filters cleared", "Copied to clipboard".

### 8.9 Animation Budget

**Rule: total animation on any page must not exceed 2 seconds of perceived motion.**

| Element | Duration | Trigger | Count |
|---------|----------|---------|-------|
| Page progress bar | 300ms | Page load | 1 |
| Skeleton shimmer | Continuous until data loads | Page load | N/A |
| KPI count-up | 800ms | Data arrival | 4-6 |
| Chart entry | 600ms | Data arrival | 1-3 |
| Section fade-in | 500ms | Scroll | 3-4 |
| Mega-menu open | 200ms | Click | 1 |
| Verdict slide-in | 400ms | Page load | 1 |
| Card hover | 200ms | Hover | N/A |
| Toast in/out | 300ms | Action | 1 |

None of these block interaction. All are `ease-out` (fast start, gentle end). No `ease-in-out` bouncing. No spring physics. Clean, professional, purposeful motion.

### 8.10 What NOT to Animate

- Tables — data appears instantly, no row-by-row animation
- Navigation links — instant response, no delays
- Theme toggle — instant color swap (no 500ms transition on background)
- Filter results — instant table update (no fade-out-fade-in)
- Sort columns — instant reorder
- Scroll — never hijack native scroll behavior

The principle: **animate arrivals, not interactions.** When data arrives, it can animate in. When the user acts, the response is instant.

---

**END OF DESIGN DOCUMENT**
