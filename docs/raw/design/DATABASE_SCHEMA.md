# ETP Filing Tracker - Database Schema Design

**Database**: PostgreSQL (or MySQL compatible)
**ORM**: SQLAlchemy
**Version**: 1.0 Initial Design

---

## Schema Overview

```
┌─────────────┐
│   Trusts    │──┐
└─────────────┘  │
                 │ 1:N
                 ▼
┌─────────────────┐       ┌──────────────────┐
│    Filings      │──────▶│  Filing_Series   │ (join table)
└─────────────────┘  1:N  └──────────────────┘
                               │ N:1
                               ▼
┌─────────────┐           ┌──────────────┐
│   Series    │──────────▶│   Classes    │
└─────────────┘   1:N     └──────────────┘
     │
     │ 1:N
     ▼
┌──────────────────┐
│ Ticker_History   │ (track symbol changes over time)
└──────────────────┘


┌──────────────────┐
│ Effective_Dates  │ (linked to Filings)
└──────────────────┘
```

---

## Table Definitions

### 1. `trusts` - ETP Issuers

Represents an ETP trust/issuer (e.g., REX ETF Trust, Vanguard, iShares).

```sql
CREATE TABLE trusts (
    id SERIAL PRIMARY KEY,
    cik VARCHAR(10) UNIQUE NOT NULL,              -- SEC Central Index Key
    name VARCHAR(255) NOT NULL,                   -- Legal name
    short_name VARCHAR(100),                      -- Display name (e.g., "REX")
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    -- Indexes
    INDEX idx_trusts_cik (cik)
);
```

**Sample Data**:
| id | cik     | name                  | short_name |
|----|---------|----------------------|------------|
| 1  | 2043954 | REX ETF Trust II     | REX        |
| 2  | 0000102909 | Vanguard Index Funds  | Vanguard   |

---

### 2. `filings` - SEC Filing Documents

Each row is a single SEC filing (485A, 485B, 497, etc.).

```sql
CREATE TABLE filings (
    id SERIAL PRIMARY KEY,
    trust_id INTEGER NOT NULL,

    accession_number VARCHAR(30) UNIQUE NOT NULL,  -- e.g., 0001999371-25-016273
    form_type VARCHAR(20) NOT NULL,                -- 485A, 485B, 497, EFFECT, etc.
    filing_date DATE NOT NULL,

    -- Document URLs
    primary_document VARCHAR(500),                 -- Primary HTML/PDF filename
    primary_link TEXT,                             -- Full URL to primary doc
    submission_txt_link TEXT,                      -- Full submission .txt URL

    -- Extracted metadata
    is_delaying_amendment BOOLEAN DEFAULT FALSE,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (trust_id) REFERENCES trusts(id) ON DELETE CASCADE,

    -- Indexes
    INDEX idx_filings_trust (trust_id),
    INDEX idx_filings_date (filing_date),
    INDEX idx_filings_form (form_type),
    INDEX idx_filings_accession (accession_number)
);
```

**Sample Data**:
| id | trust_id | accession_number       | form_type | filing_date |
|----|----------|------------------------|-----------|-------------|
| 1  | 1        | 0001999371-25-016273   | 485BPOS   | 2025-01-28  |
| 2  | 1        | 0001999371-25-015001   | 497       | 2025-01-15  |

---

### 3. `series` - Fund Series

A series represents a fund within a trust. Series IDs are from SGML headers.

```sql
CREATE TABLE series (
    id SERIAL PRIMARY KEY,
    trust_id INTEGER NOT NULL,

    -- SGML identifiers
    series_id VARCHAR(50),                         -- From <SERIES-ID> tag (may be non-unique across trusts)
    series_name VARCHAR(500) NOT NULL,             -- Fund name

    -- Status
    status VARCHAR(20) DEFAULT 'active',           -- active, liquidated, merged
    inception_date DATE,                           -- Fund launch date
    termination_date DATE,                         -- If liquidated

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (trust_id) REFERENCES trusts(id) ON DELETE CASCADE,

    -- Indexes
    INDEX idx_series_trust (trust_id),
    INDEX idx_series_name (series_name),
    UNIQUE (trust_id, series_id)                   -- Series ID unique within trust
);
```

**Sample Data**:
| id | trust_id | series_id | series_name                          | status |
|----|----------|-----------|--------------------------------------|--------|
| 1  | 1        | S000075832| REX FinTech Disruption ETF          | active |
| 2  | 1        | S000075833| REX Bitcoin Strategy ETF            | active |

---

### 4. `classes` - Share Classes

Share classes within a series (often just one class per ETF).

```sql
CREATE TABLE classes (
    id SERIAL PRIMARY KEY,
    series_id INTEGER NOT NULL,

    -- SGML identifiers
    class_contract_id VARCHAR(50),                 -- From <CLASS-CONTRACT-ID>
    class_name VARCHAR(500),                       -- Class name (often blank for ETFs)

    -- Current ticker (for quick lookups)
    current_ticker VARCHAR(10),                    -- Most recent ticker

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE CASCADE,

    -- Indexes
    INDEX idx_classes_series (series_id),
    INDEX idx_classes_ticker (current_ticker),
    UNIQUE (series_id, class_contract_id)
);
```

**Sample Data**:
| id | series_id | class_contract_id | class_name | current_ticker |
|----|-----------|-------------------|------------|----------------|
| 1  | 1         | C000257891        | NULL       | FINQ           |
| 2  | 2         | C000257892        | NULL       | BTCQ           |

---

### 5. `ticker_history` - Ticker Symbol Changes

Tracks ticker symbols over time (symbols can change via rebranding).

```sql
CREATE TABLE ticker_history (
    id SERIAL PRIMARY KEY,
    class_id INTEGER NOT NULL,
    filing_id INTEGER,                             -- Filing where this ticker appeared

    ticker VARCHAR(10) NOT NULL,

    -- Detection method
    extraction_method VARCHAR(50),                 -- SGML-TXT, TITLE-PAREN, LABEL-WINDOW

    -- Valid date range
    valid_from DATE,                               -- When this ticker started
    valid_to DATE,                                 -- NULL if current
    is_current BOOLEAN DEFAULT TRUE,

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (class_id) REFERENCES classes(id) ON DELETE CASCADE,
    FOREIGN KEY (filing_id) REFERENCES filings(id) ON DELETE SET NULL,

    -- Indexes
    INDEX idx_ticker_class (class_id),
    INDEX idx_ticker_symbol (ticker),
    INDEX idx_ticker_current (is_current)
);
```

**Sample Data**:
| id | class_id | filing_id | ticker | extraction_method | valid_from | is_current |
|----|----------|-----------|--------|-------------------|------------|------------|
| 1  | 1        | 1         | FINQ   | SGML-TXT          | 2024-01-15 | TRUE       |
| 2  | 2        | 2         | BTCQ   | TITLE-PAREN       | 2024-03-20 | TRUE       |

---

### 6. `effective_dates` - Registration Effectiveness

Tracks when filings become effective (when funds can start trading).

```sql
CREATE TABLE effective_dates (
    id SERIAL PRIMARY KEY,
    filing_id INTEGER NOT NULL,
    series_id INTEGER,                             -- NULL if filing-wide effective date

    effective_date DATE NOT NULL,

    -- Source of date
    source VARCHAR(50),                            -- HEADER, BODY-TEXT, APOS-FALLBACK
    is_estimated BOOLEAN DEFAULT FALSE,            -- True for APOS+75 days fallback

    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (filing_id) REFERENCES filings(id) ON DELETE CASCADE,
    FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE CASCADE,

    -- Indexes
    INDEX idx_effective_filing (filing_id),
    INDEX idx_effective_series (series_id),
    INDEX idx_effective_date (effective_date)
);
```

**Sample Data**:
| id | filing_id | series_id | effective_date | source       | is_estimated |
|----|-----------|-----------|----------------|--------------|--------------|
| 1  | 1         | 1         | 2025-02-01     | BODY-TEXT    | FALSE        |
| 2  | 2         | NULL      | 2025-01-20     | HEADER       | FALSE        |

---

### 7. `filing_series` - Join Table (Many-to-Many)

Links filings to series. A filing can mention multiple series, and a series appears in multiple filings.

```sql
CREATE TABLE filing_series (
    id SERIAL PRIMARY KEY,
    filing_id INTEGER NOT NULL,
    series_id INTEGER NOT NULL,

    -- Was this series newly introduced in this filing?
    is_new_series BOOLEAN DEFAULT FALSE,

    -- Order in which series appears in filing
    series_order INTEGER,

    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (filing_id) REFERENCES filings(id) ON DELETE CASCADE,
    FOREIGN KEY (series_id) REFERENCES series(id) ON DELETE CASCADE,

    -- Indexes
    UNIQUE (filing_id, series_id),
    INDEX idx_fs_filing (filing_id),
    INDEX idx_fs_series (series_id)
);
```

---

## Sample Queries

### Query 1: Get all active funds for a trust

```sql
SELECT
    t.name AS trust_name,
    s.series_name,
    c.current_ticker,
    s.inception_date
FROM series s
JOIN trusts t ON s.trust_id = t.id
LEFT JOIN classes c ON c.series_id = s.id
WHERE t.cik = '2043954'
  AND s.status = 'active'
ORDER BY s.series_name;
```

### Query 2: Get recent filings with fund count

```sql
SELECT
    f.filing_date,
    f.form_type,
    f.accession_number,
    COUNT(DISTINCT fs.series_id) AS fund_count
FROM filings f
JOIN filing_series fs ON f.id = fs.filing_id
WHERE f.trust_id = (SELECT id FROM trusts WHERE cik = '2043954')
  AND f.filing_date >= '2025-01-01'
GROUP BY f.id, f.filing_date, f.form_type, f.accession_number
ORDER BY f.filing_date DESC;
```

### Query 3: Track ticker changes for a fund

```sql
SELECT
    s.series_name,
    th.ticker,
    th.valid_from,
    th.valid_to,
    th.extraction_method,
    f.filing_date,
    f.form_type
FROM ticker_history th
JOIN classes c ON th.class_id = c.id
JOIN series s ON c.series_id = s.id
LEFT JOIN filings f ON th.filing_id = f.id
WHERE s.series_id = 'S000075832'
ORDER BY th.valid_from DESC;
```

### Query 4: Funds with upcoming effective dates

```sql
SELECT
    s.series_name,
    c.current_ticker,
    ed.effective_date,
    ed.source,
    f.form_type,
    f.filing_date
FROM effective_dates ed
JOIN filings f ON ed.filing_id = f.id
JOIN series s ON ed.series_id = s.id
LEFT JOIN classes c ON c.series_id = s.id
WHERE ed.effective_date >= CURRENT_DATE
  AND ed.effective_date <= CURRENT_DATE + INTERVAL '30 days'
ORDER BY ed.effective_date;
```

### Query 5: Latest state per fund (replaces step4 CSV)

```sql
WITH latest_filings AS (
    SELECT
        fs.series_id,
        f.id AS filing_id,
        f.filing_date,
        f.form_type,
        ROW_NUMBER() OVER (
            PARTITION BY fs.series_id
            ORDER BY
                CASE WHEN f.form_type LIKE '485BPOS%' THEN 1 ELSE 2 END,
                f.filing_date DESC
        ) AS rn
    FROM filing_series fs
    JOIN filings f ON fs.filing_id = f.id
)
SELECT
    t.name AS trust,
    s.series_name,
    c.current_ticker AS ticker,
    lf.form_type,
    lf.filing_date,
    ed.effective_date
FROM latest_filings lf
JOIN series s ON lf.series_id = s.id
JOIN trusts t ON s.trust_id = t.id
LEFT JOIN classes c ON c.series_id = s.id
LEFT JOIN effective_dates ed ON ed.filing_id = lf.filing_id AND ed.series_id = s.id
WHERE lf.rn = 1
  AND s.status = 'active'
ORDER BY t.name, s.series_name;
```

---

## Indexes Strategy

**Primary Lookups**:
- Trust by CIK: `idx_trusts_cik`
- Filings by date range: `idx_filings_date`
- Filings by trust: `idx_filings_trust`
- Current tickers: `idx_classes_ticker`, `idx_ticker_current`

**Performance Considerations**:
- Add composite index on `(trust_id, filing_date)` for common queries
- Consider partitioning `filings` table by year if data grows large (10k+ filings)
- Materialized view for "latest state" query if performance becomes issue

---

## Data Migration from CSV

**Step 1**: Load trusts
```python
# Read existing CSVs, extract unique CIKs
# INSERT INTO trusts (cik, name) VALUES (...)
```

**Step 2**: Load filings
```python
# Read {trust}_1_all.csv
# INSERT INTO filings (trust_id, accession_number, form_type, ...)
```

**Step 3**: Load series/classes
```python
# Read {trust}_3_extracted.csv
# Parse unique series, insert into series table
# Insert classes with tickers
```

**Step 4**: Build relationships
```python
# Parse filing → series mappings from _3_extracted.csv
# INSERT INTO filing_series (filing_id, series_id)
```

**Step 5**: Load effective dates
```python
# Extract effective dates from _3_extracted.csv
# INSERT INTO effective_dates (filing_id, series_id, effective_date, ...)
```

---

## SQLAlchemy Models (Preview)

```python
from sqlalchemy import Column, Integer, String, Date, Boolean, Text, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

Base = declarative_base()

class Trust(Base):
    __tablename__ = 'trusts'

    id = Column(Integer, primary_key=True)
    cik = Column(String(10), unique=True, nullable=False, index=True)
    name = Column(String(255), nullable=False)
    short_name = Column(String(100))
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    filings = relationship("Filing", back_populates="trust", cascade="all, delete-orphan")
    series = relationship("Series", back_populates="trust", cascade="all, delete-orphan")

class Filing(Base):
    __tablename__ = 'filings'

    id = Column(Integer, primary_key=True)
    trust_id = Column(Integer, ForeignKey('trusts.id'), nullable=False, index=True)
    accession_number = Column(String(30), unique=True, nullable=False, index=True)
    form_type = Column(String(20), nullable=False, index=True)
    filing_date = Column(Date, nullable=False, index=True)
    primary_document = Column(String(500))
    primary_link = Column(Text)
    submission_txt_link = Column(Text)
    is_delaying_amendment = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    trust = relationship("Trust", back_populates="filings")
    series = relationship("Series", secondary="filing_series", back_populates="filings")
    effective_dates = relationship("EffectiveDate", back_populates="filing", cascade="all, delete-orphan")

# Additional models: Series, Class, TickerHistory, EffectiveDate, FilingSeries
# (Full implementation in future PR)
```

---

## Future Enhancements

1. **Full-text search**: Add `tsvector` column to `series.series_name` for fast name search
2. **Audit trail**: Track who/when modified records
3. **Document storage**: Store full filing text in `filing_content` table
4. **Holdings data**: Add tables for NPORT-P holdings if expanding scope
5. **User management**: Add `users` table for multi-user access control

---

**Next**: See ROADMAP.md for implementation timeline
