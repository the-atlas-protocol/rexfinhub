# Pipeline Rethink: Accurate Fund Status Tracking

**Date**: 2026-02-06
**Goal**: Know exactly where each fund stands in the registration pipeline
**Priority**: ACCURACY over complexity

---

## Key Discovery: How Effective Dates Actually Work

### What We Found in the T-REX Filing

**Filing**: 485BXT (Extension of Time) - October 30, 2025
**URL**: https://www.sec.gov/Archives/edgar/data/1771146/000199937125016569/

**Structure of the document**:

```
SGML HEADER (Legal Registration Data):
├── Series ID: S000091723
├── Series Name: "Tuttle Capital 2X Long BNB Daily Target ETF"  ← OLD NAME
└── Class-Contract ID: C000259456

HTML BODY (Prospectus Content):
├── Fund Name: "T-REX 2X Long BNB Daily Target ETF"  ← NEW NAME
├── Effective Date: "November 7, 2025"  ← EXPLICIT
└── Purpose: "designating a new effective date"
```

**Critical Insight**: The SGML and HTML can have DIFFERENT fund names!
- SGML = Legal name in SEC registration system
- HTML = Marketing/prospectus name (may be updated before SGML)

### How to Extract Accurate Effective Dates

**Pattern found in 485BXT filing (lines 148-149)**:
```html
☑ on November 7, 2025 pursuant to paragraph (b)
```

**Pattern found in filing body (lines 276-278)**:
```
"for the sole purpose of designating November 7, 2025 as the new
effective date for Post-Effective Amendment No. 216"
```

**Extraction regex patterns**:
```python
# Pattern 1: Checkbox effective date
r'on\s+(\w+\s+\d{1,2},?\s+\d{4})\s+pursuant to paragraph'

# Pattern 2: Explicit statement in body
r'designating\s+(\w+\s+\d{1,2},?\s+\d{4})\s+as the new effective date'

# Pattern 3: General effective statement
r'will become effective\s+(?:on\s+)?(\w+\s+\d{1,2},?\s+\d{4})'
```

---

## Fund Status Categories

Based on SEC filing rules, here's what each status means:

### Status 1: PENDING (In Registration)

**How to identify**:
- Has N-1A or 485APOS filing
- NO 485BPOS yet
- May have 485BXT (extensions)
- Effective date in the future

**What it means**:
- Fund has been filed but NOT yet available for trading
- Still going through SEC review or waiting for effective date

### Status 2: EFFECTIVE (Trading)

**How to identify**:
- Has 485BPOS (post-effective amendment for live funds)
- OR has EFFECT form
- OR effective date has passed

**What it means**:
- Fund is LIVE and available for trading
- Should have a ticker symbol
- Can verify with exchange listings

### Status 3: DELAYED (Extension Filed)

**How to identify**:
- Has 485BXT (extension of time) filings
- Explicit "delaying amendment" language
- Effective date keeps getting pushed

**What it means**:
- Fund is still pending but taking longer than expected
- May be waiting for regulatory approval

### Status 4: WITHDRAWN / DORMANT

**How to identify**:
- N-1A filed but no activity for 12+ months
- No updates or extensions
- May have explicit withdrawal filing

**What it means**:
- Fund registration was abandoned or put on hold

---

## Rethought Pipeline Steps

### Current Problems

1. **Step 3 extracts from SGML only** → Misses name changes in HTML body
2. **Step 4 is cluttered** → Too many columns, hard to see fund status
3. **Effective date logic is wrong** → Uses filing date instead of explicit date
4. **No issuer grouping** → Grouped by trust, not by product brand

### Proposed New Steps

#### Step 1: Fetch & Catalog (No Change)

Same as current step2 - fetch all filings for trust

#### Step 2: Extract Fund Details (Enhanced)

**Changes**:
1. Extract from BOTH SGML AND HTML body
2. When names differ: use HTML body name (it's more current)
3. Extract explicit effective dates from filing text
4. Track name changes per Series ID

**New columns**:
- `series_id` (permanent key)
- `sgml_name` (legal name from header)
- `prospectus_name` (name from HTML body)
- `explicit_effective_date` (from filing text)
- `form_type_effective_rule` (which 485 rule applies)

#### Step 3: Determine Fund Status (NEW)

For each Series ID, determine current status:

```python
def determine_fund_status(filings_for_series):
    """
    Determine where a fund stands in the registration pipeline.

    Returns: status, effective_date, confidence
    """

    # Sort filings by date
    filings = sorted(filings_for_series, key=lambda x: x['filing_date'])
    latest = filings[-1]

    # Check for 485BPOS (fund is effective/live)
    bpos_filings = [f for f in filings if f['form'].startswith('485B') and 'XT' not in f['form']]
    if bpos_filings:
        latest_bpos = bpos_filings[-1]
        return {
            'status': 'EFFECTIVE',
            'effective_date': latest_bpos['explicit_effective_date'] or latest_bpos['filing_date'],
            'source': '485BPOS',
            'confidence': 'HIGH'
        }

    # Check for 485BXT (extension - still pending)
    bxt_filings = [f for f in filings if '485BXT' in f['form']]
    if bxt_filings:
        latest_bxt = bxt_filings[-1]
        return {
            'status': 'PENDING',
            'effective_date': latest_bxt['explicit_effective_date'],
            'source': '485BXT',
            'confidence': 'HIGH'
        }

    # Check for 485APOS (initial filing)
    apos_filings = [f for f in filings if '485APOS' in f['form']]
    if apos_filings:
        latest_apos = apos_filings[-1]
        eff_date = latest_apos['explicit_effective_date']
        if not eff_date:
            # Default: 75 days from filing
            eff_date = latest_apos['filing_date'] + timedelta(days=75)

        return {
            'status': 'PENDING',
            'effective_date': eff_date,
            'source': '485APOS',
            'confidence': 'MEDIUM' if not latest_apos['explicit_effective_date'] else 'HIGH'
        }

    # N-1A only (very early stage)
    n1a_filings = [f for f in filings if f['form'] == 'N-1A']
    if n1a_filings:
        return {
            'status': 'PENDING',
            'effective_date': None,
            'source': 'N-1A',
            'confidence': 'LOW'
        }

    return {
        'status': 'UNKNOWN',
        'effective_date': None,
        'source': None,
        'confidence': 'NONE'
    }
```

#### Step 4: Pipeline Summary (Simplified)

**Columns for the final output** (what matters):

| Column | Description |
|--------|-------------|
| `series_id` | Permanent SEC identifier |
| `fund_name` | Current name (from latest prospectus) |
| `ticker` | Trading symbol (if effective) |
| `issuer` | Brand/issuer (REX, Tuttle, T-REX) |
| `status` | PENDING, EFFECTIVE, DELAYED |
| `effective_date` | When fund becomes/became effective |
| `latest_filing_date` | Most recent filing |
| `latest_form` | Most recent form type |
| `prospectus_link` | Link to latest prospectus |

**NOT needed in summary**:
- First seen date (move to history)
- First seen form (move to history)
- First link (move to history)
- Separate APOS/BPOS dates (consolidated into effective_date)
- Multiple links (just latest prospectus)

#### Step 5: Name History (Separate File)

For tracking name changes:

| Column | Description |
|--------|-------------|
| `series_id` | Permanent identifier |
| `name` | Fund name at this time |
| `effective_from` | When this name started |
| `effective_to` | When this name ended (NULL if current) |
| `source_filing` | Filing that introduced this name |

---

## Issuer Grouping Strategy

### Trust vs Issuer

**Trust** = Legal entity that files (e.g., ETF Opportunities Trust)
**Issuer** = Brand/company behind the product (e.g., REX, Tuttle)

Many trusts are "white label" - they file on behalf of multiple issuers:
- ETF Opportunities Trust → Tuttle, T-REX, others
- Exchange Listed Funds Trust → Multiple issuers
- Tidal Trust → Multiple issuers

### How to Identify Issuer

**Option 1: Name Pattern Matching**
```python
def identify_issuer(fund_name):
    patterns = {
        'T-REX': r'^T-REX',
        'Tuttle Capital': r'^Tuttle\s+Capital',
        'REX': r'^REX\s+',  # Note: separate from T-REX
        'GraniteShares': r'^GraniteShares',
        'Direxion': r'^Direxion',
        # Add more as needed
    }

    for issuer, pattern in patterns.items():
        if re.match(pattern, fund_name, re.IGNORECASE):
            return issuer

    return 'Unknown'
```

**Option 2: Manual Mapping Table**
```csv
series_id,issuer,sub_advisor,notes
S000091723,T-REX,Tuttle Capital,"Partnership product"
S000091724,T-REX,Tuttle Capital,"Partnership product"
S000082340,Tuttle Capital,,"Direct Tuttle product"
```

**Recommendation**: Start with pattern matching, add manual overrides for edge cases.

---

## Extraction Logic for Effective Dates

### Priority Order (Most Reliable First)

1. **Explicit checkbox date** in 485 filings
   - Pattern: `☑ on [DATE] pursuant to paragraph`
   - Most authoritative for pending funds

2. **Explicit statement in body**
   - Pattern: `designating [DATE] as the new effective date`
   - Also very reliable

3. **SGML header EFFECTIVENESS DATE**
   - Pattern: `EFFECTIVENESS DATE: YYYYMMDD`
   - Reliable when present

4. **Filing date for 485BPOS**
   - If 485BPOS and no explicit date, fund is effective on filing date
   - Rule 485(b) = immediate effectiveness

5. **Calculated: Filing date + 75 days for 485APOS**
   - Default SEC rule
   - Only use if no explicit date found

### Extraction Code

```python
import re
from datetime import datetime, timedelta
from bs4 import BeautifulSoup

def extract_effective_date(html_content, sgml_header, form_type, filing_date):
    """
    Extract the accurate effective date from a filing.

    Returns: (date, source, confidence)
    """

    # Parse HTML
    soup = BeautifulSoup(html_content, 'html.parser')
    text = soup.get_text()

    # Method 1: Checkbox pattern (highest confidence)
    checkbox_pattern = r'on\s+(\w+\s+\d{1,2},?\s+\d{4})\s+pursuant to paragraph'
    match = re.search(checkbox_pattern, text, re.IGNORECASE)
    if match:
        date_str = match.group(1)
        date = parse_date(date_str)
        if date:
            return date, 'CHECKBOX_EXPLICIT', 'HIGH'

    # Method 2: "designating X as the new effective date"
    designating_pattern = r'designating\s+(\w+\s+\d{1,2},?\s+\d{4})\s+as the new effective date'
    match = re.search(designating_pattern, text, re.IGNORECASE)
    if match:
        date_str = match.group(1)
        date = parse_date(date_str)
        if date:
            return date, 'BODY_EXPLICIT', 'HIGH'

    # Method 3: SGML header
    sgml_pattern = r'EFFECTIVENESS\s+DATE[:\s]+(\d{8})'
    match = re.search(sgml_pattern, sgml_header, re.IGNORECASE)
    if match:
        date_str = match.group(1)
        date = datetime.strptime(date_str, '%Y%m%d')
        return date, 'SGML_HEADER', 'HIGH'

    # Method 4: 485BPOS = immediate effectiveness
    if form_type.startswith('485BPOS') or form_type == '485BPOS':
        return filing_date, 'BPOS_IMMEDIATE', 'MEDIUM'

    # Method 5: 485APOS = 75 days (default rule)
    if '485APOS' in form_type:
        date = filing_date + timedelta(days=75)
        return date, 'APOS_DEFAULT_75', 'LOW'

    return None, None, 'NONE'


def parse_date(date_str):
    """Parse various date formats."""
    formats = [
        '%B %d, %Y',   # November 7, 2025
        '%B %d %Y',    # November 7 2025
        '%m/%d/%Y',    # 11/07/2025
        '%m/%d/%y',    # 11/07/25
    ]
    for fmt in formats:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None
```

---

## Name Change Detection

### Comparing SGML vs HTML Names

```python
def detect_name_change_in_filing(sgml_series_name, html_fund_names):
    """
    Compare SGML name to HTML names to detect name changes.

    Returns: (new_name, change_type) or (None, None)
    """

    # Normalize for comparison
    sgml_norm = normalize_name(sgml_series_name)

    for html_name in html_fund_names:
        html_norm = normalize_name(html_name)

        # Check if fundamentally same fund (ignore case, minor words)
        if is_same_fund(sgml_norm, html_norm):
            if sgml_norm != html_norm:
                # Same fund, different name = name change!
                return html_name, 'REBRAND'

    return None, None


def normalize_name(name):
    """Normalize fund name for comparison."""
    name = name.lower()
    name = re.sub(r'\s+', ' ', name)  # Normalize whitespace
    name = re.sub(r'daily\s*target', 'daily target', name)  # Standardize
    return name.strip()


def is_same_fund(name1, name2):
    """
    Determine if two names refer to the same fund.

    Strategy: Extract key identifiers (underlying asset, direction, multiplier)
    """

    # Extract components
    def extract_components(name):
        # Multiplier: 2X, 3X, -2X, etc.
        mult_match = re.search(r'(-?\d+)x', name, re.IGNORECASE)
        mult = mult_match.group(1) if mult_match else None

        # Direction: Long, Inverse, Short
        if 'inverse' in name or 'short' in name:
            direction = 'inverse'
        elif 'long' in name:
            direction = 'long'
        else:
            direction = None

        # Asset: XRP, BNB, Solana, Trump, etc.
        # This is the hardest part - might need pattern matching
        assets = ['xrp', 'bnb', 'sol', 'solana', 'trump', 'nvidia', 'nvda',
                  'tesla', 'tsla', 'melania', 'bonk', 'cardano', 'ada',
                  'litecoin', 'ltc', 'chainlink', 'link', 'polkadot']
        found_asset = None
        for asset in assets:
            if asset in name:
                found_asset = asset
                break

        return (mult, direction, found_asset)

    comp1 = extract_components(name1)
    comp2 = extract_components(name2)

    # If key components match, it's the same fund
    return comp1 == comp2
```

---

## Summary: New Pipeline Architecture

```
┌────────────────────────────────────────────────────┐
│ Step 1: Fetch Filings (No Change)                  │
│   - Get all filings from SEC for trust             │
│   - Filter to prospectus forms                     │
│   - Output: _1_all_filings.csv                     │
└────────────────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────┐
│ Step 2: Extract Details (Enhanced)                 │
│   - Parse SGML header (series_id, legal name)      │
│   - Parse HTML body (prospectus name, dates)       │
│   - Extract EXPLICIT effective dates               │
│   - Detect SGML vs HTML name discrepancies         │
│   - Output: _2_fund_extraction.csv                 │
└────────────────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────┐
│ Step 3: Determine Status (NEW)                     │
│   - For each series_id, determine:                 │
│     - Status: PENDING / EFFECTIVE / DELAYED        │
│     - Effective date (single, accurate date)       │
│     - Confidence level                             │
│   - Classify by issuer (pattern matching)          │
│   - Output: _3_fund_status.csv                     │
└────────────────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────┐
│ Step 4: Pipeline Summary (Simplified)              │
│   - One row per fund (by series_id)                │
│   - Clean columns: name, ticker, status, date      │
│   - Grouped by ISSUER (not trust)                  │
│   - Output: _4_pipeline_summary.csv                │
└────────────────────────────────────────────────────┘
                         │
                         ▼
┌────────────────────────────────────────────────────┐
│ Step 5: Name History (Separate)                    │
│   - Track all name changes per series_id           │
│   - Output: _5_name_history.csv                    │
└────────────────────────────────────────────────────┘
```

---

## Final Output: What Product Team Sees

### _4_pipeline_summary.csv (Primary View)

| series_id | fund_name | ticker | issuer | status | effective_date | latest_form | prospectus_link |
|-----------|-----------|--------|--------|--------|----------------|-------------|-----------------|
| S000091723 | T-REX 2X Long BNB Daily Target ETF | - | T-REX | PENDING | 2025-11-07 | 485BXT | [link] |
| S000091724 | T-REX 2X Long XRP Daily Target ETF | - | T-REX | PENDING | 2025-11-07 | 485BXT | [link] |
| S000080899 | T-REX 2X Long NVIDIA Daily Target ETF | NVDX | T-REX | EFFECTIVE | 2023-11-15 | 485BPOS | [link] |

**Grouped view by issuer**:
```
T-REX (Partnership with Tuttle)
├── PENDING: 10 funds (effective 2025-11-07)
└── EFFECTIVE: 45 funds

Tuttle Capital
├── PENDING: 5 funds
└── EFFECTIVE: 12 funds

REX
├── PENDING: 8 funds
└── EFFECTIVE: 21 funds
```

### _5_name_history.csv (When Needed)

| series_id | name | effective_from | effective_to | change_type |
|-----------|------|----------------|--------------|-------------|
| S000091723 | Tuttle Capital 2X Long BNB Daily Target ETF | 2025-01-28 | 2025-10-30 | ORIGINAL |
| S000091723 | T-REX 2X Long BNB Daily Target ETF | 2025-10-30 | NULL | REBRAND |

---

## Next Steps

1. **Implement enhanced extraction** in step2/step3
2. **Add effective date parsing** with explicit patterns
3. **Add name change detection** (SGML vs HTML comparison)
4. **Add issuer classification** (pattern matching)
5. **Simplify step4 output** (fewer columns, clearer status)
6. **Create separate name history** file

Ready to implement when you approve this approach.
