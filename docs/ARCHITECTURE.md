# ETP Filing Tracker - Architecture Documentation

**Version**: 1.0 (CSV-based pipeline)
**Last Updated**: 2026-02-05

---

## System Overview

The ETP Filing Tracker is a modular Python system that monitors SEC EDGAR filings for Exchange-Traded Product (ETP) trusts. It extracts fund information, ticker symbols, and effective dates from regulatory filings.

**Current State**: CSV-based pipeline focused on REX ETF Trust
**Target State**: Multi-trust tracker with PostgreSQL database, REST API, and automated scheduling

---

## Data Flow (Current Implementation)

```
┌──────────────────┐
│   SEC EDGAR API  │ (data.sec.gov/submissions)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Step 2: Fetch   │ Load submissions JSON, filter prospectus forms
│  & Filter        │ Output: _1_all.csv, _2_prospectus.csv
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Step 3: Extract │ Parse SGML, extract tickers, find effective dates
│  Fund Details    │ Output: _3_extracted.csv
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│  Step 4: Roll-up │ Latest record per fund (BPOS > APOS priority)
│  Latest State    │ Output: _4_latest.csv
└──────────────────┘
```

---

## Module Responsibilities

### Core Data Modules (Keep for Database Migration)

#### `sgml.py` - SGML Parser
**Purpose**: Parse SEC SGML headers to extract series and class information
**Key Function**: `parse_sgml_series_classes(txt: str) -> list[dict]`
- Handles both `<NEW-SERIES>` and `<SERIES>` tags
- Extracts Series ID, Series Name, Class-Contract ID, Class Name
- Pulls ticker symbols from SGML when available

**Why Keep**: Core parsing logic is solid and format-agnostic

#### `body_extractors.py` - Document Text Extraction
**Purpose**: Extract text from HTML documents and PDFs in filings
**Key Functions**:
- `iter_txt_documents()` - Iterate through documents in submission .txt
- `extract_from_html_string()` - Convert HTML to plain text
- `extract_from_primary_html()` / `extract_from_primary_pdf()` - Extract from primary documents

**Why Keep**: Well-tested extraction logic, works with various filing formats

#### `sec_client.py` - HTTP Client
**Purpose**: Fetch data from SEC EDGAR with caching and rate limiting
**Features**:
- Automatic retry with exponential backoff (tenacity)
- Local file-based HTTP cache (http_cache/)
- Respects SEC rate limits (0.35s pause between requests)
- User-agent header management

**Why Keep**: Robust HTTP client with SEC-specific features

#### `utils.py` - Helper Functions
**Purpose**: Normalization, validation, common utilities
**Key Functions**:
- `is_prospectus_form()` - Identify prospectus-related filings
- `normalize_spacing()` - Clean whitespace
- `safe_str()` - Safe string conversion

**Why Keep**: Reusable utilities

#### `config.py` - Constants
**Purpose**: Configuration constants and SEC endpoints
**Contains**:
- SEC API URLs
- Form type definitions (485A, 485B, 497, N-1A, etc.)
- Default user agent

**Why Keep**: Centralized configuration

---

### Pipeline Modules (Refactor for Database)

#### `step2.py` - Submissions & Prospectus Filtering
**Current**: Fetches submissions JSON, writes to CSV
**Functions**:
- `load_all_submissions_for_cik()` - Get all filings for a CIK
- `step2_submissions_and_prospectus()` - Filter prospectus forms

**Outputs**:
- `{trust}_1_all.csv` - All filings
- `{trust}_2_prospectus.csv` - Prospectus-related filings only

**Future**: Replace CSV writes with database inserts

#### `step3.py` - Fund Extraction
**Current**: Parses SGML + extracts tickers from text, writes to CSV
**Key Logic**:
- Parse SGML for series/class structure
- Extract ticker symbols using multiple strategies:
  1. SGML header ticker tags
  2. Series name + ticker pattern: "Fund Name (TICK)"
  3. Label-based search: "Ticker: TICK" near series name
- Extract effective dates from filing text
- Detect delaying amendments

**Outputs**:
- `{trust}_3_extracted.csv` - Fund records with tickers and dates

**Future**: Database model for Fund/Series records

#### `step4.py` - Latest State Roll-up
**Current**: Consolidates latest info per fund, writes to CSV
**Key Logic**:
- Group by Series ID or Class-Contract ID
- Prioritize: BPOS filing > APOS filing > APOS+75 days fallback
- Keep most recent record per fund

**Outputs**:
- `{trust}_4_latest.csv` - Current state of each fund

**Future**: Database views or materialized queries

---

### Support Modules

#### `csvio.py` - CSV I/O
**Current**: Read/write CSV with deduplication
**Future**: Replaced by SQLAlchemy database models

#### `paths.py` - File Path Management
**Current**: Generate output file paths, SEC URLs
**Future**: Less relevant with database storage

#### `run_pipeline.py` - Pipeline Orchestrator
**Current**: Run all 4 steps sequentially
**Future**: Service layer for orchestrating database operations

---

## Current Output Structure

```
outputs/
├── REX ETF Trust_1_all.csv              # All filings
├── REX ETF Trust_2_prospectus.csv       # Prospectus forms only
├── REX ETF Trust_3_extracted.csv        # Extracted fund details
└── REX ETF Trust_4_latest.csv           # Latest state per fund
```

---

## Key Design Decisions

### ✅ What Works Well

1. **Modular separation** - Clear boundaries between parsing, fetching, extraction
2. **SGML-first approach** - Authoritative series/class data from structured headers
3. **Multi-strategy ticker extraction** - Fallback patterns increase success rate
4. **HTTP caching** - Reduces SEC API load, faster re-runs
5. **Effective date detection** - Captures both header dates and text-based dates

### ⚠️ Current Limitations

1. **CSV storage** - Difficult to query, no relationships, no integrity constraints
2. **Single-trust focus** - File naming assumes one trust at a time
3. **No history tracking** - Latest state only, no change history
4. **Manual execution** - Requires notebook run, no automation
5. **No alerting** - Can't notify on new filings or effective dates

---

## Migration Path to Database Architecture

### Phase 1: Database Foundation
- [ ] Design schema (Trusts, Filings, Funds, Series, Effective Dates)
- [ ] Set up PostgreSQL database
- [ ] Create SQLAlchemy models
- [ ] Migrate existing CSV data

### Phase 2: Refactor Pipeline
- [ ] Replace csvio.py with database layer
- [ ] Update step2/3/4 to use database
- [ ] Keep core parsing modules (sgml.py, body_extractors.py)

### Phase 3: API & Services
- [ ] Build FastAPI REST endpoints
- [ ] Add authentication
- [ ] Create query interfaces

### Phase 4: Automation & Alerts
- [ ] Scheduled pipeline runs (APScheduler)
- [ ] Email/Slack notification service
- [ ] Change detection and alerting

### Phase 5: UI/Dashboard
- [ ] API-first approach (UI consumes REST API)
- [ ] Decision pending: Streamlit vs Flask vs Jupyter-based

---

## Dependencies

**Core**:
- `pandas` - Data manipulation
- `requests` + `tenacity` - HTTP with retries
- `beautifulsoup4` + `lxml` - HTML parsing
- `pdfminer.six` - PDF text extraction

**Future Additions**:
- `sqlalchemy` + `psycopg2` - PostgreSQL database
- `fastapi` + `uvicorn` - REST API
- `apscheduler` - Task scheduling
- `pydantic` - Data validation

---

## File Structure (Current)

```
rexfinhub/
├── etp_tracker/              # Main package
│   ├── __init__.py
│   ├── config.py            # Constants ✅ Keep
│   ├── utils.py             # Helpers ✅ Keep
│   ├── sec_client.py        # HTTP client ✅ Keep
│   ├── sgml.py              # SGML parser ✅ Keep
│   ├── body_extractors.py   # Text extraction ✅ Keep
│   ├── csvio.py             # CSV I/O ⚠️ Replace
│   ├── paths.py             # File paths ⚠️ Less relevant
│   ├── step2.py             # Submissions ⚠️ Refactor
│   ├── step3.py             # Extraction ⚠️ Refactor
│   ├── step4.py             # Roll-up ⚠️ Refactor
│   └── run_pipeline.py      # Orchestrator ⚠️ Refactor
├── notebooks/
│   └── ETP_Filing_Tracker_Interface.ipynb
├── http_cache/              # SEC response cache (gitignored)
├── outputs/                 # CSV outputs (gitignored)
├── requirements.txt
└── README.md
```

---

## Performance Characteristics

**Current Bottlenecks**:
1. SEC rate limiting (0.35s per request) - unavoidable
2. PDF text extraction - can be slow for large documents
3. Sequential processing - could parallelize across multiple trusts

**Optimizations in Place**:
- HTTP caching reduces redundant requests
- Submissions JSON refresh logic (only re-fetch if stale)

---

## Security Considerations

**Current**:
- User-agent header required by SEC
- No authentication (local use only)
- No sensitive data stored

**Future**:
- Database credentials management (.env file)
- API authentication (JWT tokens)
- Input validation for CIK inputs
- Rate limiting for API endpoints

---

## Testing Strategy

**Current**: Manual testing via Jupyter notebook
**Future**:
- Unit tests for parsers (sgml.py, body_extractors.py)
- Integration tests for pipeline
- Database migration tests
- API endpoint tests

---

## Questions for Future Architecture

1. **Multi-tenancy**: Single database for all trusts, or separate databases?
2. **Real-time vs Batch**: Should new filings trigger immediate processing or wait for scheduled run?
3. **Content storage**: Store full filing text in database or just metadata + links?
4. **API versioning**: Version API endpoints from the start?
5. **Dashboard approach**: Streamlit (simple), Flask (flexible), or Django (full-featured)?

---

**Next Steps**: See ROADMAP.md for implementation plan
