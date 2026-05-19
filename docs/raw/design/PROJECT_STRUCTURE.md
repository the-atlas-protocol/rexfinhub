# ETP Filing Tracker - Project Structure Plan

**Version**: 2.0 (Database + API Architecture)

---

## New Directory Structure

```
rexfinhub/
│
├── etp_tracker/                    # Main application package
│   │
│   ├── __init__.py
│   │
│   ├── core/                       # Core parsing/extraction (existing logic)
│   │   ├── __init__.py
│   │   ├── sgml.py                 # ✅ KEEP - SGML parser
│   │   ├── body_extractors.py     # ✅ KEEP - HTML/PDF extraction
│   │   ├── sec_client.py           # ✅ KEEP - HTTP client with caching
│   │   ├── utils.py                # ✅ KEEP - Helper functions
│   │   └── config.py               # ✅ KEEP - Constants and SEC endpoints
│   │
│   ├── database/                   # Database layer (NEW)
│   │   ├── __init__.py
│   │   ├── models.py               # SQLAlchemy models (Trust, Filing, Series, etc.)
│   │   ├── connection.py           # Database connection management
│   │   ├── session.py              # Session factory and context managers
│   │   └── migrations/             # Alembic migration scripts
│   │       ├── versions/
│   │       └── env.py
│   │
│   ├── services/                   # Business logic layer (NEW)
│   │   ├── __init__.py
│   │   ├── filing_service.py       # Filing ingestion logic (refactored step2)
│   │   ├── extraction_service.py   # Fund extraction logic (refactored step3)
│   │   ├── rollup_service.py       # Latest state logic (refactored step4)
│   │   ├── trust_service.py        # Trust management operations
│   │   └── notification_service.py # Email/Slack alerts
│   │
│   ├── api/                        # REST API layer (NEW)
│   │   ├── __init__.py
│   │   ├── app.py                  # FastAPI application factory
│   │   ├── dependencies.py         # Dependency injection (DB sessions, etc.)
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── trusts.py           # /api/trusts endpoints
│   │   │   ├── filings.py          # /api/filings endpoints
│   │   │   ├── series.py           # /api/series endpoints
│   │   │   ├── tickers.py          # /api/tickers endpoints
│   │   │   └── health.py           # /api/health endpoint
│   │   └── schemas/                # Pydantic request/response schemas
│   │       ├── __init__.py
│   │       ├── trust.py
│   │       ├── filing.py
│   │       └── series.py
│   │
│   ├── scheduler/                  # Automation layer (NEW)
│   │   ├── __init__.py
│   │   ├── jobs.py                 # Scheduled job definitions
│   │   ├── runner.py               # APScheduler setup
│   │   └── config.py               # Schedule configuration
│   │
│   └── legacy/                     # Old CSV-based modules (DEPRECATED)
│       ├── __init__.py
│       ├── csvio.py                # CSV I/O (keep for migration only)
│       ├── paths.py                # File paths (keep for migration only)
│       ├── step2.py                # Old step2 (reference for refactoring)
│       ├── step3.py                # Old step3 (reference for refactoring)
│       ├── step4.py                # Old step4 (reference for refactoring)
│       └── run_pipeline.py         # Old pipeline (reference)
│
├── notebooks/                      # Jupyter notebooks
│   ├── ETP_Filing_Tracker_Interface.ipynb  # ✅ KEEP - Development interface
│   ├── data_exploration.ipynb      # NEW - Ad-hoc queries and analysis
│   └── migration_helper.ipynb      # NEW - CSV to database migration
│
├── tests/                          # Test suite (NEW)
│   ├── __init__.py
│   ├── conftest.py                 # pytest fixtures
│   ├── test_core/                  # Core module tests
│   │   ├── test_sgml.py
│   │   ├── test_extractors.py
│   │   └── test_sec_client.py
│   ├── test_database/              # Database tests
│   │   ├── test_models.py
│   │   └── test_queries.py
│   ├── test_services/              # Service layer tests
│   │   ├── test_filing_service.py
│   │   └── test_extraction_service.py
│   └── test_api/                   # API endpoint tests
│       ├── test_trusts_endpoints.py
│       └── test_filings_endpoints.py
│
├── scripts/                        # Utility scripts (NEW)
│   ├── migrate_csv_to_db.py        # One-time CSV → database migration
│   ├── run_pipeline.py             # New database-based pipeline runner
│   ├── backfill_trust.py           # Backfill filings for a trust
│   └── setup_db.py                 # Database initialization script
│
├── config/                         # Configuration files (NEW)
│   ├── development.env             # Dev environment variables (gitignored)
│   ├── production.env.template     # Production template (committed)
│   └── logging.yaml                # Logging configuration
│
├── docs/                           # Documentation (NEW)
│   ├── ARCHITECTURE.md             # ✅ CREATED - System architecture
│   ├── DATABASE_SCHEMA.md          # ✅ CREATED - Database design
│   ├── API_DOCUMENTATION.md        # Future - API endpoint docs
│   └── DEPLOYMENT.md               # Future - Deployment guide
│
├── .gitignore                      # ✅ CREATED - Git ignore rules
├── requirements.txt                # ✅ EXISTS - Python dependencies
├── requirements-dev.txt            # NEW - Development dependencies
├── .env.example                    # NEW - Environment variables template
├── alembic.ini                     # NEW - Alembic configuration
├── pytest.ini                      # NEW - pytest configuration
├── README.md                       # ✅ EXISTS - Project overview
└── ROADMAP.md                      # NEXT - Development roadmap
```

---

## Module Migration Map

### Core Modules (Move to `etp_tracker/core/`)

| Old Location           | New Location                     | Status |
|------------------------|----------------------------------|--------|
| `etp_tracker/sgml.py`           | `etp_tracker/core/sgml.py`       | Move   |
| `etp_tracker/body_extractors.py`| `etp_tracker/core/body_extractors.py` | Move |
| `etp_tracker/sec_client.py`     | `etp_tracker/core/sec_client.py` | Move   |
| `etp_tracker/utils.py`          | `etp_tracker/core/utils.py`      | Move   |
| `etp_tracker/config.py`         | `etp_tracker/core/config.py`     | Move   |

### Deprecated Modules (Move to `etp_tracker/legacy/`)

| Old Location           | New Location                     | Status |
|------------------------|----------------------------------|--------|
| `etp_tracker/csvio.py`          | `etp_tracker/legacy/csvio.py`    | Archive|
| `etp_tracker/paths.py`          | `etp_tracker/legacy/paths.py`    | Archive|
| `etp_tracker/step2.py`          | `etp_tracker/legacy/step2.py`    | Archive|
| `etp_tracker/step3.py`          | `etp_tracker/legacy/step3.py`    | Archive|
| `etp_tracker/step4.py`          | `etp_tracker/legacy/step4.py`    | Archive|
| `etp_tracker/run_pipeline.py`   | `etp_tracker/legacy/run_pipeline.py` | Archive|

---

## New Dependencies

### Production Dependencies (`requirements.txt`)

```txt
# Existing
beautifulsoup4==4.14.2
certifi==2025.10.5
lxml==6.0.2
numpy==2.3.4
pandas==2.3.3
pdfminer.six==20250506
requests==2.32.5
tenacity==9.1.2
tqdm==4.67.1

# NEW - Database
sqlalchemy==2.0.25
psycopg2-binary==2.9.9        # PostgreSQL driver
alembic==1.13.1               # Database migrations

# NEW - API
fastapi==0.109.2
uvicorn[standard]==0.27.0     # ASGI server
pydantic==2.6.0               # Data validation
pydantic-settings==2.1.0      # Settings management

# NEW - Scheduling
apscheduler==3.10.4

# NEW - Notifications
python-dotenv==1.0.1          # Environment variables
```

### Development Dependencies (`requirements-dev.txt`)

```txt
# Testing
pytest==8.0.0
pytest-cov==4.1.0             # Coverage reports
pytest-asyncio==0.23.3        # Async tests
httpx==0.26.0                 # API testing

# Code quality
black==24.1.1                 # Code formatter
ruff==0.2.0                   # Fast linter
mypy==1.8.0                   # Type checking

# Development tools
ipython==8.21.0
jupyter==1.0.0
```

---

## Configuration Management

### Environment Variables (`.env.example`)

```bash
# Database
DATABASE_URL=postgresql://user:password@localhost:5432/etp_tracker
DATABASE_POOL_SIZE=5

# SEC API
SEC_USER_AGENT=REX-SEC-Filer/1.0 (contact: your-email@example.com)
SEC_REQUEST_TIMEOUT=45
SEC_RATE_LIMIT_PAUSE=0.35

# Cache
HTTP_CACHE_DIR=/path/to/http_cache
HTTP_CACHE_MAX_AGE_HOURS=6

# API
API_HOST=0.0.0.0
API_PORT=8000
API_RELOAD=True                # Development only
API_SECRET_KEY=your-secret-key-here

# Scheduler
SCHEDULER_ENABLED=True
SCHEDULER_DAILY_RUN_TIME=08:00  # 8 AM daily

# Notifications
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
EMAIL_SMTP_HOST=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_FROM=alerts@yourdomain.com
EMAIL_TO=recipient@yourdomain.com
```

---

## Import Path Changes

### Before (Old imports)

```python
from etp_tracker.sgml import parse_sgml_series_classes
from etp_tracker.sec_client import SECClient
from etp_tracker.step2 import step2_submissions_and_prospectus
```

### After (New imports)

```python
# Core modules
from etp_tracker.core.sgml import parse_sgml_series_classes
from etp_tracker.core.sec_client import SECClient

# Services
from etp_tracker.services.filing_service import ingest_filings_for_trust
from etp_tracker.services.extraction_service import extract_funds_from_filing

# Database
from etp_tracker.database.models import Trust, Filing, Series
from etp_tracker.database.session import get_db_session
```

---

## API Structure Example

### Sample FastAPI endpoint (`etp_tracker/api/routes/trusts.py`)

```python
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List

from etp_tracker.database.session import get_db_session
from etp_tracker.database.models import Trust
from etp_tracker.api.schemas.trust import TrustResponse, TrustCreate

router = APIRouter(prefix="/api/trusts", tags=["trusts"])

@router.get("/", response_model=List[TrustResponse])
def list_trusts(db: Session = Depends(get_db_session)):
    """List all tracked trusts."""
    trusts = db.query(Trust).all()
    return trusts

@router.get("/{cik}", response_model=TrustResponse)
def get_trust(cik: str, db: Session = Depends(get_db_session)):
    """Get trust by CIK."""
    trust = db.query(Trust).filter(Trust.cik == cik).first()
    if not trust:
        raise HTTPException(status_code=404, detail="Trust not found")
    return trust

@router.post("/", response_model=TrustResponse, status_code=201)
def create_trust(trust_data: TrustCreate, db: Session = Depends(get_db_session)):
    """Add a new trust to track."""
    trust = Trust(**trust_data.dict())
    db.add(trust)
    db.commit()
    db.refresh(trust)
    return trust
```

---

## Service Layer Example

### Sample service (`etp_tracker/services/filing_service.py`)

```python
from sqlalchemy.orm import Session
from typing import List
import pandas as pd

from etp_tracker.core.sec_client import SECClient
from etp_tracker.database.models import Trust, Filing

def ingest_filings_for_trust(
    db: Session,
    client: SECClient,
    trust_id: int,
    since: str | None = None
) -> int:
    """
    Fetch and store filings for a trust (replaces old step2).

    Args:
        db: Database session
        client: SEC HTTP client
        trust_id: Trust ID in database
        since: Optional date filter (YYYY-MM-DD)

    Returns:
        Number of new filings ingested
    """
    trust = db.query(Trust).filter(Trust.id == trust_id).first()
    if not trust:
        raise ValueError(f"Trust {trust_id} not found")

    # Fetch submissions JSON (using existing sec_client logic)
    data = client.load_submissions_json(trust.cik)

    # Parse filings
    rec = data.get("filings", {}).get("recent", {})
    forms = rec.get("form", [])
    accessions = rec.get("accessionNumber", [])
    dates = rec.get("filingDate", [])
    primary_docs = rec.get("primaryDocument", [])

    new_count = 0
    for i in range(len(forms)):
        accession = accessions[i]

        # Check if already exists
        existing = db.query(Filing).filter(
            Filing.accession_number == accession
        ).first()
        if existing:
            continue

        # Create new filing record
        filing = Filing(
            trust_id=trust.id,
            accession_number=accession,
            form_type=forms[i],
            filing_date=dates[i],
            primary_document=primary_docs[i] if i < len(primary_docs) else None,
            # ... build URLs using core.config logic
        )
        db.add(filing)
        new_count += 1

    db.commit()
    return new_count
```

---

## Migration Steps (Old → New)

### Phase 1: Setup (Week 1)
1. Create new directory structure
2. Move core modules to `etp_tracker/core/`
3. Archive old modules to `etp_tracker/legacy/`
4. Set up database connection and models
5. Configure Alembic for migrations

### Phase 2: Database Layer (Week 2)
1. Implement SQLAlchemy models
2. Create initial Alembic migration
3. Write CSV migration script
4. Test database schema with sample data

### Phase 3: Service Layer (Week 3)
1. Refactor step2 → `filing_service.py`
2. Refactor step3 → `extraction_service.py`
3. Refactor step4 → `rollup_service.py`
4. Write unit tests for services

### Phase 4: API Layer (Week 4)
1. Set up FastAPI app structure
2. Implement trust/filing/series endpoints
3. Add API documentation (auto-generated by FastAPI)
4. Write API integration tests

### Phase 5: Automation (Week 5)
1. Implement scheduler jobs
2. Add notification service
3. Test scheduled runs
4. Create monitoring/health checks

---

## Testing Strategy

### Unit Tests
- **Core modules**: Test SGML parsing, ticker extraction, date parsing in isolation
- **Services**: Mock database, test business logic
- **API**: Mock services, test request/response handling

### Integration Tests
- **Database**: Test full CRUD operations
- **Pipeline**: End-to-end test from SEC fetch → database storage
- **API**: Test with real database (test database, not production)

### Fixtures (`tests/conftest.py`)
```python
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from etp_tracker.database.models import Base

@pytest.fixture(scope="session")
def test_db_engine():
    """Create test database engine."""
    engine = create_engine("postgresql://test:test@localhost:5432/etp_test")
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)

@pytest.fixture
def db_session(test_db_engine):
    """Create test database session."""
    Session = sessionmaker(bind=test_db_engine)
    session = Session()
    yield session
    session.rollback()
    session.close()
```

---

## ADHD-Friendly Workflow Tips

1. **One module at a time**: Don't try to refactor everything at once
2. **Keep old code working**: Legacy modules stay functional during migration
3. **Test frequently**: Run tests after each module migration
4. **Document as you go**: Update this file when structure changes
5. **Use notebooks for exploration**: Keep `notebooks/data_exploration.ipynb` for quick queries
6. **Clear todos**: Use GitHub issues or todo.txt for tracking small tasks

---

**Next**: See ROADMAP.md for week-by-week implementation plan
