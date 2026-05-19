# SEC Identifier System for Fund Tracking

**Date**: 2026-02-05
**Purpose**: Understanding SEC IDs for accurate name change and fund tracking

---

## Overview: The SEC Identifier Hierarchy

The SEC uses a **hierarchical identification system** for investment companies:

```
CIK (Company/Trust Level)
└── Series ID (Fund Level)
    └── Class-Contract ID (Share Class Level)
```

Each level has a **permanent, unique identifier** that **never changes** even when names change.

---

## 1. CIK (Central Index Key)

**What it is**: Unique identifier for the legal entity (Trust)

**Format**: 10-digit number (often shown with leading zeros)

**Example**:
- `0001771146` = ETF Opportunities Trust
- `0002043954` = REX ETF Trust

**Key Property**:
- ✅ PERMANENT - Never changes
- ✅ One CIK per legal entity
- ✅ Used as primary key for trust-level tracking

**Use Case**: Identifying which trust a filing belongs to

---

## 2. Series ID (Fund Level)

**What it is**: Unique identifier for a fund series within a trust

**Format**: `Sxxxxxxxxx` (S followed by 9 digits)

**Example**:
```
S000091732 = Tuttle Capital 2X Long Trump Daily Target ETF
S000080899 = T-REX 2X LONG NVIDIA DAILY TARGET ETF
```

**Key Property**:
- ✅ PERMANENT - Never changes even when fund name changes
- ✅ Assigned at fund registration (N-1A filing)
- ✅ Stays the same through:
  - Name changes
  - Strategy changes
  - Ticker changes
  - Manager changes

**Use Case**:
- **PRIMARY KEY for fund tracking**
- Linking filings to funds
- Detecting name changes (same ID, different name)
- Tracking fund history across filings

### Name Change Example (Detected in Your Data)

```
Series ID: S000082340

Filing Date   | Form    | Series Name
--------------|---------|------------------------------------------
2023-10-16    | 485BXT  | TUTTLE CAPITAL 2X LONG AI ETF
2024-05-17    | 497     | Tuttle Capital Daily 2X Long AI ETF

Same fund, same Series ID, name changed from:
  "2X LONG AI" → "Daily 2X Long AI"
```

---

## 3. Class-Contract ID (Share Class Level)

**What it is**: Unique identifier for a specific share class within a series

**Format**: `Cxxxxxxxxx` (C followed by 9 digits)

**Example**:
```
Series: S000091732 (Tuttle Capital 2X Long Trump Daily Target ETF)
└── Class: C000259465 (typically same name as series for ETFs)
```

**Key Property**:
- ✅ PERMANENT - Never changes
- ✅ Each share class has unique ID
- ✅ ETFs typically have only ONE class (unlike mutual funds)

**Use Case**:
- Tracking specific share classes
- Linking ticker symbols (tickers are assigned to classes, not series)
- Distinguishing share classes in multi-class funds

### Ticker-to-Class Relationship

```
Class-Contract ID: C000243471
├── Series: S000080899 (T-REX 2X LONG NVIDIA DAILY TARGET ETF)
├── Ticker: NVDX (assigned to this specific class)
└── Exchange: NYSE Arca
```

---

## 4. Practical Application: Your Tuttle-REX Data

### Finding 1: Tuttle and T-REX are SEPARATE Series

| Brand | Trust | Series Count | Series ID Range |
|-------|-------|--------------|-----------------|
| Tuttle Capital | ETF Opportunities Trust | 93 | S000082xxx, S000091xxx |
| T-REX | ETF Opportunities Trust | 256 | S000080xxx, S000087xxx, S000096xxx |

**NO overlap in Series IDs** = These are separate fund launches, NOT rebrands.

### Finding 2: What Name Changes DO Exist

Minor name changes within Tuttle Capital products:

```
S000082340:
  2023-10-16: "TUTTLE CAPITAL 2X LONG AI ETF"
  2024-05-17: "Tuttle Capital Daily 2X Long AI ETF"
              ↑ Added "Daily" to clarify daily target

S000091723:
  2025-01-27: "Tuttle Capital 2X Long BNP Daily Target ETF"
  2025-01-28: "Tuttle Capital 2X Long BNB Daily Target ETF"
              ↑ Fixed typo: BNP → BNB
```

These are tracked correctly because **Series ID remains constant**.

---

## 5. Database Schema for Proper Tracking

### Core Tables

```sql
-- Trusts (CIK level)
CREATE TABLE trusts (
    id SERIAL PRIMARY KEY,
    cik VARCHAR(10) UNIQUE NOT NULL,  -- The permanent identifier
    name VARCHAR(255),                 -- Current legal name
    short_name VARCHAR(100),           -- Display name
    INDEX idx_trusts_cik (cik)
);

-- Series (Fund level) - PRIMARY TRACKING KEY
CREATE TABLE series (
    id SERIAL PRIMARY KEY,
    trust_id INTEGER NOT NULL,
    series_id VARCHAR(20) UNIQUE NOT NULL,  -- S000xxxxxx - PERMANENT
    current_name VARCHAR(500),               -- Latest known name
    current_ticker VARCHAR(10),              -- Latest ticker (via class)
    status VARCHAR(20) DEFAULT 'active',
    inception_date DATE,

    FOREIGN KEY (trust_id) REFERENCES trusts(id),
    INDEX idx_series_id (series_id)
);

-- Classes (Share class level)
CREATE TABLE classes (
    id SERIAL PRIMARY KEY,
    series_id INTEGER NOT NULL,
    class_contract_id VARCHAR(20) UNIQUE NOT NULL,  -- C000xxxxxx - PERMANENT
    current_name VARCHAR(500),
    current_ticker VARCHAR(10),

    FOREIGN KEY (series_id) REFERENCES series(id),
    INDEX idx_class_contract_id (class_contract_id)
);
```

### Name History Tables

```sql
-- Track ALL names a series has had
CREATE TABLE series_name_history (
    id SERIAL PRIMARY KEY,
    series_id INTEGER NOT NULL,         -- FK to series.id
    sec_series_id VARCHAR(20) NOT NULL, -- S000xxxxxx for cross-reference

    name VARCHAR(500) NOT NULL,
    effective_from DATE NOT NULL,       -- First filing with this name
    effective_to DATE,                  -- NULL if current

    source_filing_id INTEGER,           -- Filing where name first appeared
    is_current BOOLEAN DEFAULT FALSE,

    FOREIGN KEY (series_id) REFERENCES series(id),
    INDEX idx_name_history_series (series_id),
    INDEX idx_name_search (name)
);

-- Search across all names (current and historical)
-- Example: Find fund even if searching by old name
SELECT s.*, snh.name as historical_name
FROM series s
JOIN series_name_history snh ON s.id = snh.series_id
WHERE snh.name ILIKE '%Tuttle Capital 2X Long AI%';
```

### Brand/Partnership Tracking

```sql
-- Track brand relationships (Tuttle-REX partnership)
CREATE TABLE brand_associations (
    id SERIAL PRIMARY KEY,
    trust_id INTEGER NOT NULL,

    brand_name VARCHAR(100) NOT NULL,   -- "Tuttle Capital", "T-REX"
    partner_name VARCHAR(100),          -- "REX" for partnership products

    -- Which series belong to this brand
    series_prefix VARCHAR(50),          -- Pattern like "Tuttle%"

    FOREIGN KEY (trust_id) REFERENCES trusts(id)
);

-- Insert brand relationships
INSERT INTO brand_associations VALUES
  (1, 1, 'Tuttle Capital', 'REX', 'Tuttle%'),
  (2, 1, 'T-REX', 'REX', 'T-REX%');
```

---

## 6. Query Examples

### Find All Funds for Tuttle-REX Partnership

```sql
-- All funds under ETF Opportunities Trust with Tuttle or T-REX branding
SELECT
    s.series_id,
    s.current_name,
    s.current_ticker,
    CASE
        WHEN s.current_name ILIKE 'Tuttle%' THEN 'Tuttle Capital'
        WHEN s.current_name ILIKE 'T-REX%' THEN 'T-REX'
        ELSE 'Other'
    END as brand
FROM series s
JOIN trusts t ON s.trust_id = t.id
WHERE t.cik = '0001771146'  -- ETF Opportunities Trust
  AND (s.current_name ILIKE 'Tuttle%' OR s.current_name ILIKE 'T-REX%')
ORDER BY brand, s.current_name;
```

### Detect Name Changes

```sql
-- Find all series that have had name changes
SELECT
    s.series_id,
    COUNT(DISTINCT snh.name) as name_count,
    STRING_AGG(DISTINCT snh.name, ' → ' ORDER BY snh.effective_from) as name_history
FROM series s
JOIN series_name_history snh ON s.id = snh.series_id
GROUP BY s.series_id
HAVING COUNT(DISTINCT snh.name) > 1;
```

### Track Filing History for a Fund

```sql
-- All filings for a specific fund (by permanent Series ID)
SELECT
    f.filing_date,
    f.form_type,
    snh.name as fund_name_at_time,
    f.accession_number
FROM filings f
JOIN filing_series fs ON f.id = fs.filing_id
JOIN series s ON fs.series_id = s.id
LEFT JOIN series_name_history snh ON s.id = snh.series_id
    AND f.filing_date >= snh.effective_from
    AND (f.filing_date <= snh.effective_to OR snh.effective_to IS NULL)
WHERE s.series_id = 'S000082340'  -- Tuttle Capital 2X Long AI ETF
ORDER BY f.filing_date;
```

---

## 7. Implementation for Your System

### Current CSV Enhancement

Add these columns to `_4_latest.csv`:

| Column | Description | Example |
|--------|-------------|---------|
| `SEC_Series_ID` | Permanent identifier | S000082340 |
| `SEC_Class_ID` | Class identifier | C000249879 |
| `Current_Name` | Latest name | Tuttle Capital Daily 2X Long AI ETF |
| `Previous_Names` | Pipe-separated list | TUTTLE CAPITAL 2X LONG AI ETF |
| `Name_Change_Dates` | When names changed | 2024-05-17 |
| `Brand` | Brand family | Tuttle Capital |
| `Partner` | Partnership | REX |

### Python Detection Logic

```python
def track_fund_identity(filing_records_for_series: pd.DataFrame) -> dict:
    """
    Track fund identity using PERMANENT SEC Series ID.

    Args:
        filing_records_for_series: All records with same Series ID

    Returns:
        dict with current name, all names, brand classification
    """
    records = filing_records_for_series.sort_values('Filing Date')

    series_id = records.iloc[0]['Series ID']
    class_id = records.iloc[0]['Class-Contract ID']

    # Track name changes
    names_over_time = []
    prev_name = None

    for _, row in records.iterrows():
        current_name = row['Series Name']
        if current_name != prev_name:
            names_over_time.append({
                'name': current_name,
                'effective_from': row['Filing Date'],
                'form': row['Form']
            })
        prev_name = current_name

    current_name = names_over_time[-1]['name']

    # Classify brand
    brand = classify_brand(current_name)

    return {
        'series_id': series_id,
        'class_id': class_id,
        'current_name': current_name,
        'previous_names': [n['name'] for n in names_over_time[:-1]],
        'name_change_count': len(names_over_time) - 1,
        'brand': brand,
        'first_seen': names_over_time[0]['effective_from'],
    }


def classify_brand(name: str) -> str:
    """Classify fund by brand based on naming pattern."""
    name_upper = name.upper()

    if 'TUTTLE' in name_upper:
        return 'Tuttle Capital'
    elif 'T-REX' in name_upper or 'TREX' in name_upper:
        return 'T-REX'
    elif 'REX ' in name_upper:  # Note space to avoid matching T-REX
        return 'REX'
    else:
        return 'Unknown'
```

---

## 8. Summary

### The Key Insight

**Series ID is the PERMANENT identifier for funds.**

When tracking funds:
1. ✅ Use `Series ID` (S000xxxxxx) as primary key
2. ✅ Names can change, Series ID never does
3. ✅ Track name history linked to Series ID
4. ✅ Link tickers to `Class-Contract ID` (C000xxxxxx)

### For Tuttle-REX Partnership

- Tuttle Capital funds and T-REX funds are **SEPARATE series**
- Both brands operate under **ETF Opportunities Trust** (CIK 1771146)
- Track by brand using name patterns: `Tuttle%` vs `T-REX%`
- Use Series ID to track any future name changes within each brand

### Next Steps

1. Add `SEC_Series_ID` and `SEC_Class_ID` columns to outputs
2. Implement name change detection using Series ID grouping
3. Add brand classification to roll-up logic
4. Consider brand_associations table in database design

---

**This is the foundation for accurate fund tracking.** The SEC designed this system specifically to handle name changes, mergers, and rebrands while maintaining continuity.
