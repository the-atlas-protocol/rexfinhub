# T-REX Strategy System — Project Plan

**Owner**: Ryu El-Asmar
**Stakeholder**: Scott Acheychek
**Created**: 2026-03-23
**Status**: Proposed

---

## Summary

Build an internal intelligence system that answers one question every day: **What should T-REX file, launch, and kill — and why?**

The leverage & inverse ETF space is growing rapidly. Our filing and launch decisions have been reactionary — week-to-week responses to whatever is trending. This has cost us market share. Competitors who move first on an underlying capture 60-80% of eventual category AUM. We need a system that sees opportunities before they're obvious, validates them with data, and gives us conviction to act.

This system runs on three time horizons:
- **Daily dashboard**: What changed overnight? New filings, volume spikes, sentiment shifts.
- **Weekly review**: What's building? Sustained traction vs. noise. Emerging themes.
- **Monthly strategy brief**: What should we file? What should we launch? What's underperforming? PDF-ready for leadership.

Every day's data is captured and stored. This creates the historical record needed to validate our models — a stock that trends for one day is noise; a stock that builds volume for three weeks while competitors haven't filed is an opportunity.

---

## Why This Matters Now

### Market share erosion is accelerating

The single-stock leveraged ETF category has grown from ~$15B to ~$40B+ in 18 months. REX (T-REX) was an early mover. We are no longer.

- Competitors are filing faster, launching wider, and capturing first-mover AUM
- Our filing-to-launch pipeline has no systematic intake — we file what feels right that week
- We have no framework for evaluating whether an opportunity is worth the filing cost and operational overhead

### Our Asia position reveals both strength and vulnerability

36% of T-REX AUM now comes from Asia, primarily Korean retail. This is simultaneously:
- **A moat**: No competitor has this distribution. Korean demand data is a unique signal.
- **A concentration risk**: If Korean retail rotates, our AUM drops faster than competitors'.
- **An untapped signal**: What Korean traders buy tells us what US retail will buy next (and vice versa). We're not using this.

### Tuttle has started building — we need our own angle

Our partner Tuttle Capital has built a daily watchlist system (Nick Minter, VP). It tracks ~500 tickers using US social sentiment (StockTwits, Reddit, YouTube) + volume momentum. It's a useful demand signal, but it has clear gaps:

| Tuttle Covers | Tuttle Misses |
|---|---|
| US retail buzz (Reddit, StockTwits, YouTube) | International demand (Korea = 36% of T-REX AUM) |
| Daily volume momentum spikes | Supply-side competitive intelligence |
| Social sentiment scoring | Post-launch success prediction |
| Top-50 "trending" tickers | Which products actually gather sustainable AUM |
| | Institutional demand signals (13F) |
| | Filing pipeline dynamics (who filed what, when, for what underlying) |

We should not duplicate Tuttle's social sentiment work. We should build what they can't: the supply-side intelligence, international demand layer, and the strategic recommendation engine that turns signals into filing decisions.

---

## System Architecture

### Three Pillars

```
PILLAR 1                    PILLAR 2                    PILLAR 3
Supply Intelligence         Demand Signals              Portfolio Strategy
─────────────────           ──────────────              ──────────────────
Competitive landscape       Korean retail demand         Current T-REX health
Filing pipeline tracker     Institutional signals (13F)  Concentration analysis
White space scoring         Volume/momentum (Tuttle+)    Launch success model
Gap analysis                Options market activity      Kill/sunset criteria
Filing-to-launch timing     Bloomberg flow data          Revenue optimization
```

### Data Flow

```
INPUTS (Daily)                    PROCESSING                      OUTPUTS
──────────────                    ──────────────                   ───────
Bloomberg daily_file        ───>  Historical DB capture     ───>  Daily Dashboard
SEC EDGAR filings           ───>  Scoring engine            ───>  Weekly Digest
Tuttle watchlist (weekly)   ───>  Trend detection           ───>  Monthly Strategy Brief
Rexfinhub screener data     ───>  Opportunity ranking       ───>  Filing Recommendations
Asia broker reports         ───>  Supply gap analysis       ───>  Launch/Kill List
13F quarterly data          ───>  Portfolio health scoring  ───>  Board-Ready PDF
```

---

## Phase 1: Foundation (Weeks 1-2)

Build the data backbone. No analysis yet — just capture, store, and display.

### 1.1 Historical Data Capture Pipeline

**Problem**: We have daily Bloomberg data but don't store it historically. Each day's file overwrites the last. We can't answer "how has FIVE's volume trended over the past 3 months?" without this.

**Build**:
- Daily snapshot table: `strategy_daily` — ticker, date, price, volume, aum, flows, short_interest, options_oi
- Ingestion script that runs after Bloomberg file load, appends to history
- Retention: indefinite (rows are small, ~5K tickers x 365 days = 1.8M rows/year)

### 1.2 Competitive Filing Tracker Enhancement

**Problem**: The screener landscape page tracks filings but doesn't track the *timeline* — when competitors file vs. when they launch, and what happens to AUM after launch.

**Build**:
- Filing lifecycle tracking: filed → effective → launched → AUM at 30/60/90/180 days
- Competitor velocity scoring: how fast does each issuer go from filing to launch?
- New filing alerts: daily check for new S-1/485-A filings on tracked underlyings

### 1.3 Underlying Universe Map

**Problem**: No single view of "every stock that could be a leveraged ETF" with its current competitive status.

**Build**:
- Master list: every underlying with market cap > $5B
- For each: existing leveraged products (by issuer, leverage, direction), filing status, white space
- Scoring: market cap, options OI, average daily volume, sector

---

## Phase 2: Intelligence Layer (Weeks 3-5)

Turn stored data into signals. This is where we differentiate from Tuttle.

### 2.1 Supply Gap Analyzer

Score every possible product slot: **underlying x leverage x direction**.

```
Score = w1 * market_cap_rank
      + w2 * options_oi_rank
      + w3 * adv_rank
      + w4 * (1 - competitor_density)     # fewer competitors = higher score
      + w5 * sector_momentum
      + w6 * korea_demand_signal          # our unique edge
```

Output: ranked list of "file-worthy" opportunities with clear reasoning.

### 2.2 Post-Launch Success Model

Analyze every leveraged ETF launched in the past 24 months:
- What % gathered >$50M AUM within 6 months?
- What attributes predict success? (market cap of underlying, sector, first-mover advantage, launch timing)
- What predicts failure? (crowded space, low underlying volume, wrong timing)

This answers: "If we file for 2x FIVE, what's the probability it reaches $100M AUM?"

### 2.3 Korean Demand Intelligence

REX's unique advantage. No competitor has this data.

- What T-REX products do Korean retail traders hold? (from KSD broker data)
- Which underlyings are Korean traders interested in that we DON'T have products for?
- Cross-reference Korean trading trends with US filing opportunities
- Research: Korean financial communities (Naver Cafe, etc.) for emerging themes

### 2.4 Institutional Signal Layer

From 13F data already in rexfinhub:
- Which institutions are building positions in leveraged ETFs?
- Which underlyings have growing institutional interest but no leveraged product?
- Which REX products are losing institutional holders (and to whom)?

---

## Phase 3: Decision Engine (Weeks 6-8)

Synthesize signals into actionable recommendations.

### 3.1 Daily Dashboard

Web page in rexfinhub (`/strategy/dashboard`):
- **Today's Movers**: Stocks with unusual volume + no leveraged product
- **Filing Alerts**: New competitor filings in the last 24 hours
- **Portfolio Health**: T-REX product health scores (AUM trend, flow momentum, spread)
- **Watchlist**: User-curated list of stocks under consideration, with daily data updates
- Historical charts for any ticker (from Phase 1 data capture)

### 3.2 Weekly Digest

Automated email (extends existing rexfinhub email system):
- Top 10 supply-gap opportunities (ranked by composite score)
- Competitor filing activity summary
- T-REX portfolio health changes
- Korean demand signals
- Tuttle watchlist highlights (curated, not duplicated)

### 3.3 Monthly T-REX Strategy Brief

PDF report (ReportLab, same pattern as Asia report):
- **Executive Summary**: Market landscape, REX position, key changes
- **Filing Recommendations**: Top 5 underlyings to file for, with full analysis
- **Launch Recommendations**: Filed products ready to launch, with timing analysis
- **Portfolio Review**: Underperforming products, concentration risk, revenue analysis
- **Competitive Moves**: What competitors did this month, impact assessment
- **Korea Spotlight**: Asia demand trends relevant to filing decisions

---

## Phase 4: Refinement (Ongoing)

### 4.1 Model Validation

Historical data (Phase 1) enables backtesting:
- Did our "high opportunity" scores from 3 months ago predict actual AUM growth?
- Are our demand signals leading or lagging?
- Adjust weights and thresholds based on outcomes

### 4.2 Tuttle Integration

- Ingest Tuttle's weekly watchlist as one input signal (not the primary driver)
- Cross-reference their social sentiment with our supply-gap analysis
- Where they see demand AND we see a supply gap = highest conviction opportunities

### 4.3 Automated Filing Pipeline

Long-term: when conviction is high enough, auto-generate filing recommendation memos with supporting data, competitive analysis, and projected AUM scenarios.

---

## What We're NOT Building

- A social media scraping system (Tuttle does this; we use their output as one input)
- A trading signal tool (this is for product strategy, not portfolio management)
- A public-facing product (this is internal intelligence only)

---

## Technical Approach

### Data Storage

New tables in rexfinhub SQLite (or PostgreSQL when migrated):

```
strategy_daily          — daily ticker snapshots (price, volume, aum, flows)
strategy_watchlist      — user-curated tickers under consideration
strategy_opportunities  — scored supply-gap opportunities
strategy_filings        — enhanced filing lifecycle tracking
strategy_recommendations — generated recommendations with supporting data
```

### Integration with Existing Rexfinhub

- New router: `webapp/routers/strategy.py`
- New templates: `webapp/templates/strategy/`
- Extends existing Bloomberg data service + SEC pipeline
- Uses existing 13F holdings infrastructure
- Shares email infrastructure with daily digest system

### Tooling

- **Data**: Python + pandas + SQLAlchemy (existing rexfinhub stack)
- **Dashboard**: FastAPI + Jinja2 + Chart.js (existing patterns)
- **Reports**: ReportLab for PDFs (proven in Asia report)
- **Korean data**: Manual initially (from Grace/Asia team), automated as sources identified

---

## Timeline

| Phase | Weeks | Deliverables |
|-------|-------|-------------|
| 1: Foundation | 1-2 | Historical data capture, filing lifecycle tracking, underlying universe map |
| 2: Intelligence | 3-5 | Supply gap analyzer, post-launch success model, Korean demand, institutional signals |
| 3: Decision Engine | 6-8 | Daily dashboard, weekly digest, monthly strategy brief |
| 4: Refinement | 9+ | Model validation, Tuttle integration, automated recommendations |

First usable output (daily dashboard + weekly digest) by end of Week 5.
First monthly strategy brief by end of Week 8.

---

## Success Criteria

After 3 months:
1. Every T-REX filing decision is backed by quantitative analysis, not gut feel
2. We identify opportunities before competitors file (leading, not reacting)
3. Monthly strategy brief is the primary input for product committee meetings
4. Historical data validates our scoring model (>60% accuracy on AUM prediction)
5. Korean demand signal has influenced at least one filing decision

---

## Relationship to Rexfinhub Master Plan

This project accelerates and deepens several items from the existing master plan:

| Master Plan Item | Status | This Project |
|---|---|---|
| 3.1 White Space Analyzer | Sprint 2 planned | Becomes core of Supply Gap Analyzer (Phase 2.1) |
| 3.5 Product Health Score | Sprint 2 planned | Integrated into Portfolio Strategy pillar |
| 3.4 Institutional Crossover | Sprint 2 planned | Feeds into Institutional Signal Layer (Phase 2.4) |
| 5.3 Launch Timing Analyzer | Phase 5 | Pulled forward into Post-Launch Success Model (Phase 2.2) |
| 6.4 Recommendation Engine | Phase 6 | Becomes Monthly Strategy Brief (Phase 3.3) |

The T-REX Strategy System is the master plan's analytical engine, focused and accelerated with a clear mandate: own the T-REX product strategy.
