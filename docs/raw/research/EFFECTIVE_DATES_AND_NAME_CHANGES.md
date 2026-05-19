# Effective Dates & Name Changes Analysis

**Date**: 2026-02-05
**Issues Identified**: Effective date logic needs improvement, name change tracking needed

---

## Issue 1: Effective Date Logic 🔴 NEEDS FIXING

### Current Status

**From verification results**:
- Total extraction records: 973
- Records WITH effective dates: 249 (25.6%) ❌
- Records WITH delaying amendments: 763 (78.4%)
- Funds in latest state with effective dates: 38/64 (59.4%)

**Problem**: Only ~26% of records have effective dates captured!

---

### Current Logic (Step 3 Extraction)

**3 Sources for Effective Dates** ([step3.py:40-161](etp_tracker/step3.py#L40-L161)):

```python
# Source 1: SGML Header
# Looks for: EFFECTIVENESS DATE: 20250201
eff_date = _extract_effectiveness_from_hdr(txt_text)

# Source 2: Body Text Patterns
# Looks for phrases like:
# - "will become effective on February 1, 2025"
# - "effective on 02/01/2025"
# - "effective on or about February 1, 2025"
eff_date_text, is_delaying = _find_effective_date_in_text(txt_text)

# Source 3: HTML/PDF Primary Documents
# Searches primary document for same patterns
```

**Delaying Amendment Detection**:
- Searches for phrases: "delaying amendment", "delay its effective date", "rule 485(a)", "rule 473"
- Sets flag if found

---

### What's WRONG with Current Logic

#### Problem 1: Incorrect 485BPOS Assumption

**Your concern is 100% VALID!**

Current logic: "If 485BPOS, assume effective date = filing date"

**Reality from SEC rules**:
```
485(a) - Registration becomes effective 60 days after filing
         UNLESS issuer requests delaying amendment

485(b) - Post-effective amendments for ALREADY EFFECTIVE funds
         Used for material changes after fund launches
         485BPOS (Post-Effective Amendment) ≠ Initial Effectiveness!
```

**What 485BPOS actually means**:
- Fund is **already trading**
- This is an **update** to an existing registration
- The amendment itself becomes effective **immediately** upon filing
- BUT this does NOT mean the fund FIRST went effective on this date!

**Example from your data**:
```
Fund: REX AAPL Growth & Income ETF
Latest BPOS: 2025-10-24
Latest Prospectus Effective: 2025-10-24  ← WRONG!

Reality: Fund likely went effective MONTHS earlier
The 10/24 filing is just an UPDATE to an already-live fund
```

#### Problem 2: Missing N-1A Initial Effectiveness

**N-1A filings**: Initial registration statements
- Filed BEFORE fund launches
- Automatically effective **60 days after filing** (Rule 485(a))
- OR effective on date specified in filing
- Often followed by 497 (definitive prospectus) on launch day

**Your system is NOT tracking**:
- N-1A filing date
- N-1A + 60 days calculation
- 497 "commencement of operations" dates

#### Problem 3: 497 Forms Misunderstood

**Form 497 types**:
- **497** - Definitive prospectus (often filed on fund launch day)
- **497K** - Summary prospectus update
- **497J** - Supplement (minor changes)

**Current logic**: Treats all 497s equally
**Reality**: Only plain "497" (without suffix) indicates potential launch date

---

### Correct Logic for Effective Dates

#### Rule 1: Initial Effectiveness (Fund Launch)

**Priority order** (authoritative → fallback):

1. **EFFECT form** (rare but authoritative)
   - Form type: "EFFECT"
   - States fund is now effective for trading
   - Use filing date as effective date

2. **Plain 497 without delaying amendment** (strong signal)
   - Filed AFTER N-1A or 485APOS
   - Often indicates commencement of operations
   - Look for phrases: "commencement of operations", "fund will commence operations"

3. **N-1A + 60 days** (default SEC rule)
   - Initial registration (N-1A)
   - Automatically effective 60 days after filing
   - UNLESS delaying amendment filed

4. **485APOS explicit date** (annual update, may contain launch info)
   - Look for "will become effective on [DATE]"
   - Explicitly stated effective date in filing text

5. **SGML Header EFFECTIVENESS DATE** (when present)
   - Rare but authoritative when present
   - Format: `EFFECTIVENESS DATE: 20250201`

#### Rule 2: Post-Effectiveness Amendments (485BPOS)

**485BPOS does NOT indicate initial effectiveness!**

For 485BPOS filings:
- Amendment effective immediately upon filing
- Fund is **already trading**
- Do NOT use this as "fund launch date"
- Use this for: "last material change date"

#### Rule 3: Delaying Amendments

If delaying amendment detected:
- N-1A + 60 days does NOT apply
- Fund effective date is POSTPONED
- Look for explicit "new effective date" in later filing
- Often requires manual review

---

### Proposed Fix: Enhanced Effective Date Logic

```python
def determine_initial_effectiveness(filing_records_for_series):
    """
    Determine when a fund FIRST became effective for trading.

    Args:
        filing_records_for_series: All filings for a specific Series ID,
                                   sorted by filing date

    Returns:
        (effective_date, source, confidence)
    """

    # Priority 1: EFFECT form (definitive)
    effect_filing = find_form_type(records, 'EFFECT')
    if effect_filing:
        return effect_filing.filing_date, 'EFFECT_FORM', 'HIGH'

    # Priority 2: Plain 497 with "commencement" language
    for filing in find_form_type(records, '497'):
        if has_commencement_language(filing.text):
            return filing.filing_date, '497_COMMENCEMENT', 'HIGH'

    # Priority 3: N-1A + 60 days (no delaying amendment)
    n1a_filing = find_first_form_type(records, 'N-1A')
    if n1a_filing and not n1a_filing.has_delaying_amendment:
        effective_date = n1a_filing.filing_date + timedelta(days=60)
        return effective_date, 'N1A_PLUS_60', 'MEDIUM'

    # Priority 4: 485APOS with explicit effective date
    apos_filing = find_first_form_type(records, '485APOS')
    if apos_filing:
        explicit_date = extract_explicit_effective_date(apos_filing.text)
        if explicit_date:
            return explicit_date, 'APOS_EXPLICIT', 'MEDIUM'

    # Priority 5: SGML header date
    for filing in records:
        sgml_date = extract_sgml_effectiveness_date(filing.text)
        if sgml_date:
            return sgml_date, 'SGML_HEADER', 'LOW'

    # Unable to determine
    return None, None, 'UNKNOWN'


def determine_last_material_change(filing_records_for_series):
    """
    Find the most recent MATERIAL change to the fund.
    This is different from initial effectiveness!
    """

    # 485BPOS = material changes to live fund
    bpos_filings = find_form_type(records, '485BPOS')
    if bpos_filings:
        latest = max(bpos_filings, key=lambda x: x.filing_date)
        return latest.filing_date, 'BPOS_MATERIAL_CHANGE'

    # 485APOS = annual update
    apos_filings = find_form_type(records, '485APOS')
    if apos_filings:
        latest = max(apos_filings, key=lambda x: x.filing_date)
        return latest.filing_date, 'APOS_ANNUAL_UPDATE'

    return None, None
```

### New Database Schema for Effective Dates

```sql
-- Enhanced effective_dates table
CREATE TABLE effective_dates (
    id SERIAL PRIMARY KEY,
    series_id INTEGER NOT NULL,

    -- INITIAL effectiveness (fund launch)
    initial_effective_date DATE,
    initial_effective_source VARCHAR(50),  -- EFFECT_FORM, 497_COMMENCEMENT, N1A_PLUS_60, etc.
    initial_effective_confidence VARCHAR(20),  -- HIGH, MEDIUM, LOW, UNKNOWN
    initial_effective_filing_id INTEGER,  -- Link to filing that established this

    -- Latest MATERIAL change
    last_material_change_date DATE,
    last_material_change_filing_id INTEGER,

    -- Delaying amendment status
    has_delaying_amendment BOOLEAN DEFAULT FALSE,
    delaying_amendment_filing_id INTEGER,

    -- Manual overrides (for human review)
    manual_effective_date DATE,
    manual_notes TEXT,
    reviewed_by VARCHAR(100),
    reviewed_at TIMESTAMP,

    FOREIGN KEY (series_id) REFERENCES series(id),
    FOREIGN KEY (initial_effective_filing_id) REFERENCES filings(id),
    FOREIGN KEY (last_material_change_filing_id) REFERENCES filings(id)
);
```

---

## Issue 2: Name Changes 🟡 TRACKING NEEDED

### Current Status

**Example from ETF Opportunities Trust**:

| Series ID  | Old Name (2023-10-16)                          | New Name (2024-05-17)                     |
|------------|-----------------------------------------------|-------------------------------------------|
| S000082340 | TUTTLE CAPITAL 2X LONG AI ETF                 | Tuttle Capital Daily 2X Long AI ETF       |
| S000082339 | TUTTLE CAPITAL 2X INVERSE AI ETF              | Tuttle Capital Daily 2X Inverse AI ETF    |

**Note**: T-REX funds are DIFFERENT series (S000087xxx), not rebrands!

---

### Why Name Changes Matter

1. **Portfolio tracking**: Users following "Tuttle Capital AI ETF" need to know it's now "Daily 2X Long AI"
2. **Historical continuity**: Same fund, different names across filings
3. **Ticker consistency**: Name changes don't always coincide with ticker changes
4. **Regulatory requirements**: SEC requires disclosure of name changes

---

### Current System Limitation

**Problem**: `step4.py` roll-up uses **latest name only**

```python
# Current step4 logic (simplified)
latest_record = df.sort_values('Filing Date').iloc[-1]  # Takes most recent
return latest_record['Series Name']  # Returns latest name only
```

**Result**: Older name is LOST from latest state file

---

### Proposed Solution: Name Change Tracking

#### Option 1: Name History Table (Database)

```sql
CREATE TABLE series_name_history (
    id SERIAL PRIMARY KEY,
    series_id INTEGER NOT NULL,

    series_name VARCHAR(500) NOT NULL,
    name_effective_from DATE,  -- When this name started
    name_effective_to DATE,    -- When this name ended (NULL if current)

    filing_id INTEGER,  -- Filing that introduced this name
    is_current BOOLEAN DEFAULT TRUE,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (series_id) REFERENCES series(id),
    FOREIGN KEY (filing_id) REFERENCES filings(id),

    -- Ensure only one current name per series
    UNIQUE (series_id, is_current) WHERE is_current = TRUE
);

-- Example data
INSERT INTO series_name_history VALUES
  (1, 15, 'TUTTLE CAPITAL 2X LONG AI ETF', '2023-10-16', '2024-05-16', 101, FALSE),
  (2, 15, 'Tuttle Capital Daily 2X Long AI ETF', '2024-05-17', NULL, 205, TRUE);
```

#### Option 2: CSV Enhancement (Current System)

Add columns to `_4_latest.csv`:

| Series ID | **Current Name** | **Previous Names** | **Name Change Dates** |
|-----------|------------------|--------------------|-----------------------|
| S000082340 | Tuttle Capital Daily 2X Long AI ETF | TUTTLE CAPITAL 2X LONG AI ETF | 2024-05-17 |

```python
# In step4.py roll-up
def track_name_changes(df):
    """
    Track name changes for a series across filings.
    """
    df_sorted = df.sort_values('Filing Date')

    current_name = df_sorted.iloc[-1]['Series Name']
    previous_names = []
    name_change_dates = []

    prev_name = None
    for idx, row in df_sorted.iterrows():
        name = row['Series Name']
        if prev_name and name != prev_name:
            # Name changed!
            previous_names.append(prev_name)
            name_change_dates.append(row['Filing Date'])
        prev_name = name

    return {
        'Current Name': current_name,
        'Previous Names': ' | '.join(previous_names),
        'Name Change Dates': ' | '.join(name_change_dates)
    }
```

#### Option 3: Alias/AKA Table (Recommended for Database)

```sql
CREATE TABLE series_aliases (
    id SERIAL PRIMARY KEY,
    series_id INTEGER NOT NULL,

    alias_type VARCHAR(50),  -- 'OFFICIAL_NAME', 'FORMER_NAME', 'SHORT_NAME', 'TICKER_BASED'
    alias_value VARCHAR(500) NOT NULL,

    valid_from DATE,
    valid_to DATE,
    is_current BOOLEAN DEFAULT FALSE,

    FOREIGN KEY (series_id) REFERENCES series(id),
    INDEX idx_alias_search (alias_value)
);

-- Search by any name
SELECT s.*
FROM series s
JOIN series_aliases a ON s.id = a.series_id
WHERE a.alias_value ILIKE '%Tuttle Capital AI%';
```

---

### Detection Logic for Name Changes

```python
def detect_name_change(old_name: str, new_name: str) -> bool:
    """
    Determine if two names represent a significant name change.

    Ignore:
    - Case changes (TUTTLE → Tuttle)
    - Minor punctuation (ETF vs. ETF or E.T.F.)
    - Whitespace normalization

    Detect:
    - Word additions/removals ("AI ETF" → "Daily 2X Long AI ETF")
    - Word substitutions ("Inverse" → "Short")
    """

    # Normalize
    old_normalized = normalize_fund_name(old_name)
    new_normalized = normalize_fund_name(new_name)

    if old_normalized == new_normalized:
        return False  # No meaningful change

    # Calculate similarity (Levenshtein distance or word overlap)
    similarity = calculate_similarity(old_normalized, new_normalized)

    if similarity < 0.7:  # Significant difference
        return True

    return False


def normalize_fund_name(name: str) -> str:
    """Normalize for comparison."""
    # Lowercase
    name = name.lower()

    # Remove common suffixes
    name = re.sub(r'\s+(etf|fund|trust|portfolio|series)$', '', name)

    # Normalize whitespace
    name = re.sub(r'\s+', ' ', name).strip()

    # Remove punctuation
    name = re.sub(r'[^\w\s]', '', name)

    return name
```

---

## Recommended Implementation Order

### Phase 1: Fix Effective Date Logic (High Priority)

1. **Update `step3.py` extraction**:
   - Add `initial_effectiveness` detection (EFFECT, 497 commencement, N-1A+60)
   - Add `last_material_change` detection (485BPOS)
   - Separate these two concepts!

2. **Update `step4.py` rollup**:
   - Show BOTH initial effective date AND last material change date
   - Add confidence level for effective date

3. **Add test cases**:
   - Test N-1A + 60 days calculation
   - Test EFFECT form detection
   - Test 497 commencement language

### Phase 2: Add Name Change Tracking (Medium Priority)

1. **CSV enhancement (quick win)**:
   - Add "Previous Names" and "Name Change Dates" columns to `_4_latest.csv`
   - Implement in `step4.py`

2. **Database schema (Phase 2 of roadmap)**:
   - Add `series_name_history` table
   - Add `series_aliases` table for search

3. **Detection logic**:
   - Implement name change detection
   - Flag for human review when major change detected

### Phase 3: Validation & Manual Review UI

1. **Build review interface**:
   - Show funds with LOW confidence effective dates
   - Show detected name changes
   - Allow manual override

2. **Add validation rules**:
   - Flag if effective date > 180 days from N-1A filing
   - Flag if name changed but ticker didn't (or vice versa)
   - Flag if multiple name changes in short period

---

## Quick Fix for Current Data

### Script: Recalculate Effective Dates

```python
def fix_effective_dates_for_rex():
    """
    Quick script to improve effective date accuracy for REX.
    Run this BEFORE database migration.
    """

    import pandas as pd
    from datetime import timedelta

    # Load extraction data
    df3 = pd.read_csv('outputs/REX ETF Trust/REX ETF Trust_3_Prospectus_Fund_Extraction.csv', dtype=str)
    df3['Filing Date'] = pd.to_datetime(df3['Filing Date'])

    # Group by Series ID
    for series_id, group in df3.groupby('Series ID'):
        group_sorted = group.sort_values('Filing Date')

        # Find initial effectiveness
        effect_row = group_sorted[group_sorted['Form'] == 'EFFECT']
        if not effect_row.empty:
            initial_eff_date = effect_row.iloc[0]['Filing Date']
            source = 'EFFECT_FORM'

        else:
            # Check for N-1A
            n1a_row = group_sorted[group_sorted['Form'] == 'N-1A']
            if not n1a_row.empty:
                first_n1a = n1a_row.iloc[0]
                if first_n1a.get('Delaying Amendment') != 'Y':
                    initial_eff_date = first_n1a['Filing Date'] + timedelta(days=60)
                    source = 'N1A_PLUS_60'
                else:
                    initial_eff_date = None
                    source = 'DELAYED'
            else:
                initial_eff_date = None
                source = 'UNKNOWN'

        # Update records
        df3.loc[group_sorted.index, 'Initial Effective Date'] = initial_eff_date
        df3.loc[group_sorted.index, 'Effective Date Source'] = source

    # Save corrected data
    df3.to_csv('outputs/REX ETF Trust/REX ETF Trust_3_CORRECTED.csv', index=False)
    print("Effective dates recalculated!")
```

---

## Summary & Next Steps

### ✅ What We Learned

1. **Effective dates are COMPLEX**:
   - 485BPOS ≠ Fund launch date
   - N-1A + 60 days is default rule
   - Multiple sources needed with confidence levels

2. **Name changes ARE happening**:
   - Tuttle Capital funds renamed in 2024
   - Need tracking for historical continuity

3. **Current system has gaps**:
   - 74% of records missing effective dates
   - Name history not preserved

### 🎯 Immediate Actions

1. ✅ **Document issues** (this file) - DONE
2. 🔜 **Write effective date fix** for step3.py
3. 🔜 **Add name change detection** to step4.py
4. 🔜 **Re-run pipeline** with improved logic
5. 🔜 **Design database schema** with lessons learned

### 📋 For Database Migration

Include in schema design:
- `effective_dates` table with confidence levels
- `series_name_history` table
- `series_aliases` table for search
- Manual review flags

---

**Ready to implement fixes?** Let me know if you want me to:
1. Update `step3.py` with improved effective date logic
2. Update `step4.py` with name change tracking
3. Create migration script for existing data
