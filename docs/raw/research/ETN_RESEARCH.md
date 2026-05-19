# ETN Research: SEC Filing Process & Tracking

## What are ETNs?

Exchange-Traded Notes (ETNs) are **unsecured debt instruments** issued by banks under the Securities Act of 1933. Unlike ETFs (which are registered investment companies under the Investment Company Act of 1940), ETNs are structured as senior debt obligations of the issuing bank.

Key issuers: Barclays (iPath), JPMorgan, Deutsche Bank (db-X), Credit Suisse, UBS (ETRACS), BMO.

## SEC Filing Process

### Registration
- **S-3** (shelf registration): The issuer files a shelf registration statement covering a program of ETN issuances. A single S-3 can cover billions in aggregate principal.
- **S-3/A**: Amendments to the shelf registration.

### Per-Product Filings
- **424B2** (pricing supplement): Each individual ETN product gets its own pricing supplement filed under Rule 424(b)(2). This describes the specific terms: underlier, fee, maturity date, redemption features, etc.
- **FWP** (free writing prospectus): Marketing materials and term sheets filed under Rule 433.
- **8-K**: Material events (early redemptions, accelerations, delisting notices).

### What ETNs Do NOT File
- **No 485 forms** (485BPOS, 485APOS, 485BXT) - these are Investment Company Act forms
- **No N-1A** - ETF/mutual fund registration statement
- **No 497/497K** - prospectus supplements for investment companies

This is why our current pipeline (which monitors 485-series forms) completely ignores ETNs.

## Outstanding Shares / Notes

### The Problem
Unlike ETFs, the SEC does **not** require ETN issuers to report per-product outstanding note counts in their filings. ETN outstanding share data comes from:

1. **Bloomberg** (our current source): Daily `SHARES_OUT` field. This is the most reliable daily source.
2. **Exchange data**: NYSE/CBOE publish daily shares outstanding for listed products.
3. **Issuer websites**: Some issuers publish indicative values and notes outstanding (e.g., iPath website).
4. **SEC filings**: Only aggregate program-level data in 10-K/10-Q, not per-product.

### Conclusion
Bloomberg remains the best source for ETN shares outstanding. SEC filings cannot provide this at the product level.

## If We Want to Track ETN Products via SEC

### Approach
1. Add `424B2` to `etp_tracker/config.py` form types
2. Build a new extraction strategy for pricing supplements
3. Parse each issuer's 424B2 format to extract:
   - Product name / ticker
   - Underlier / index
   - Fee rate
   - Maturity date
   - CUSIP
   - Redemption features

### Challenges
- **No standard format**: Each issuer's 424B2 has a completely different HTML structure. Barclays, JPMorgan, and Deutsche Bank all use different templates.
- **Volume**: Major issuers have hundreds of 424B2 filings (one per product, plus updates).
- **Limited value-add**: The filing itself doesn't tell us anything Bloomberg doesn't already provide (AUM, price, volume).

### Potential Value
- **Early detection**: Spot new ETN products before Bloomberg picks them up
- **Fee changes**: Detect fee modifications filed as supplemental pricing supplements
- **Acceleration/redemption notices**: 8-K filings for ETN wind-downs (early warning)

## Recommendation

**No code changes at this time.** The ROI of building ETN-specific SEC parsers is low given:
1. Bloomberg already provides daily ETN data (AUM, shares, price)
2. ETN market is shrinking (issuers have been redeeming products since 2018)
3. Parser complexity is high (per-issuer templates)

If ETN tracking becomes a priority, the recommended approach is:
- Phase 1: Monitor 8-K filings from known ETN issuers for acceleration/redemption notices
- Phase 2: Build 424B2 parser for one issuer (Barclays/iPath as pilot)
- Phase 3: Extend to other issuers based on Phase 2 learnings
