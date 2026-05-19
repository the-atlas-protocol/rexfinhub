> **ARCHIVED** — This plan was superseded by organic development. See CLAUDE.md for current state.

# ETP Filing Tracker - Development Roadmap

**Start Date**: 2026-02-05
**Current Phase**: Foundation & Planning ✅
**Target**: Production-ready multi-trust tracker with API and automation

---

## Overview

This roadmap transforms the current CSV-based REX-only tracker into a scalable, database-driven system that tracks multiple ETP trusts with REST API access and automated updates.

**Guiding Principles**:
- ✅ **Keep it working**: Don't break existing functionality
- 🎯 **One thing at a time**: Complete each phase before moving on
- 🧪 **Test everything**: Write tests as you build, not after
- 📝 **Document progress**: Update this file as you complete tasks
- 🔄 **Iterate**: Start simple, add complexity gradually

---

## Phase 0: Foundation & Planning ✅ (COMPLETED)

**Duration**: 1 day
**Status**: ✅ **DONE**

### Completed Tasks
- [x] Set up .gitignore
- [x] Document current architecture (ARCHITECTURE.md)
- [x] Design database schema (DATABASE_SCHEMA.md)
- [x] Plan project structure (PROJECT_STRUCTURE.md)
- [x] Create development roadmap (this file)
- [ ] **NEXT**: Create initial Git commit

### Deliverables
- ✅ Complete documentation of current system
- ✅ Clear migration plan
- ✅ Database schema design
- ✅ Project structure blueprint

---

## Phase 1: Repository & Environment Setup

**Duration**: 1-2 days
**Status**: 🔜 **NEXT UP**

### Goals
Clean up repository, set up proper Git workflow, prepare development environment for database work.

### Tasks

#### 1.1 Git Setup
- [ ] Create initial commit with current working code
- [ ] Create `main` branch (rename from master if needed)
- [ ] Create `develop` branch for ongoing work
- [ ] Add meaningful commit: "chore: initial commit - CSV-based pipeline v1.0"
- [ ] Tag current state as `v1.0-csv-baseline`

#### 1.2 Environment Files
- [ ] Create `.env.example` template
- [ ] Create `config/development.env.template`
- [ ] Create `requirements-dev.txt` with development dependencies
- [ ] Document environment setup in README.md

#### 1.3 Database Setup (Local PostgreSQL)
- [ ] Install PostgreSQL locally (or use Docker)
- [ ] Create `etp_tracker` database
- [ ] Create database user with appropriate permissions
- [ ] Test connection with psql or pgAdmin
- [ ] Document database setup steps

#### 1.4 Python Environment
- [ ] Create virtual environment: `python -m venv venv`
- [ ] Activate venv and install existing requirements
- [ ] Install dev dependencies (pytest, black, etc.)
- [ ] Verify all imports work

### Deliverables
- ✅ Clean Git history with tagged baseline
- ✅ Working development environment
- ✅ PostgreSQL database running locally
- ✅ Environment configuration templates

### Success Criteria
- Can run existing notebook without errors
- PostgreSQL accessible and responding
- All dependencies installed in venv

---

## Phase 2: Database Foundation

**Duration**: 3-5 days
**Status**: ⏳ **UPCOMING**

### Goals
Implement database models, set up migrations, establish database connection patterns.

### Tasks

#### 2.1 Project Restructure
- [ ] Create new directory structure (see PROJECT_STRUCTURE.md)
- [ ] Create `etp_tracker/core/` directory
- [ ] Move core modules: sgml.py, body_extractors.py, sec_client.py, utils.py, config.py
- [ ] Update imports in moved files
- [ ] Create `etp_tracker/database/` directory
- [ ] Create `etp_tracker/legacy/` directory
- [ ] Move old pipeline modules to legacy/

#### 2.2 Database Models (SQLAlchemy)
- [ ] Create `etp_tracker/database/models.py`
- [ ] Implement `Trust` model
- [ ] Implement `Filing` model
- [ ] Implement `Series` model
- [ ] Implement `Class` model
- [ ] Implement `TickerHistory` model
- [ ] Implement `EffectiveDate` model
- [ ] Implement `FilingSeries` join table
- [ ] Add relationships between models
- [ ] Write docstrings for each model

#### 2.3 Database Connection
- [ ] Create `etp_tracker/database/connection.py`
- [ ] Implement database URL from environment
- [ ] Create engine with connection pooling
- [ ] Add retry logic for connection failures
- [ ] Create `etp_tracker/database/session.py`
- [ ] Implement session factory
- [ ] Create context manager for sessions
- [ ] Add `get_db_session()` dependency for FastAPI

#### 2.4 Alembic Setup
- [ ] Initialize Alembic: `alembic init etp_tracker/database/migrations`
- [ ] Configure `alembic.ini` with database URL
- [ ] Update `env.py` to import models
- [ ] Create initial migration: `alembic revision --autogenerate -m "initial schema"`
- [ ] Review generated migration
- [ ] Apply migration: `alembic upgrade head`
- [ ] Verify tables created in PostgreSQL

#### 2.5 Basic Database Tests
- [ ] Create `tests/test_database/` directory
- [ ] Write `conftest.py` with test database fixtures
- [ ] Test Trust model CRUD operations
- [ ] Test Filing model with foreign keys
- [ ] Test Series → Class relationships
- [ ] Test TickerHistory queries

### Deliverables
- ✅ All database tables created in PostgreSQL
- ✅ SQLAlchemy models with proper relationships
- ✅ Alembic migrations working
- ✅ Test suite for database layer

### Success Criteria
- Can create/read/update/delete records via SQLAlchemy
- Relationships work correctly (trust.filings, series.classes, etc.)
- All tests pass
- No schema warnings from Alembic

---

## Phase 3: Data Migration (CSV → Database)

**Duration**: 2-3 days
**Status**: ⏳ **UPCOMING**

### Goals
Migrate existing CSV data into PostgreSQL while preserving all information.

### Tasks

#### 3.1 Migration Script
- [ ] Create `scripts/migrate_csv_to_db.py`
- [ ] Read existing `_1_all.csv` for REX ETF Trust
- [ ] Create Trust record for REX (CIK 2043954)
- [ ] Load all filings into `filings` table
- [ ] Read `_3_extracted.csv`
- [ ] Create Series records (deduplicate by series_id)
- [ ] Create Class records
- [ ] Create TickerHistory records
- [ ] Create EffectiveDate records
- [ ] Build FilingSeries relationships
- [ ] Add progress bars (tqdm)
- [ ] Add logging for migration status

#### 3.2 Data Validation
- [ ] Count records in CSV vs database
- [ ] Verify no duplicate accession numbers
- [ ] Check all foreign keys valid
- [ ] Verify ticker symbols match CSV
- [ ] Compare latest state query vs `_4_latest.csv`
- [ ] Document any data discrepancies

#### 3.3 Migration Notebook
- [ ] Create `notebooks/migration_helper.ipynb`
- [ ] Show before/after record counts
- [ ] Display sample records from each table
- [ ] Test sample queries from DATABASE_SCHEMA.md
- [ ] Validate data integrity

### Deliverables
- ✅ All CSV data migrated to PostgreSQL
- ✅ Migration script that can be re-run safely
- ✅ Data validation report

### Success Criteria
- Database contains all records from CSVs
- Queries return expected results
- No orphaned records (broken foreign keys)
- Can recreate `_4_latest.csv` from database query

---

## Phase 4: Service Layer (Business Logic)

**Duration**: 4-6 days
**Status**: ⏳ **UPCOMING**

### Goals
Refactor pipeline logic (step2/3/4) into database-aware services.

### Tasks

#### 4.1 Trust Service
- [ ] Create `etp_tracker/services/trust_service.py`
- [ ] `add_trust(db, cik, name)` - Add new trust to track
- [ ] `get_trust_by_cik(db, cik)` - Fetch trust
- [ ] `list_trusts(db)` - List all trusts
- [ ] Write tests for trust service

#### 4.2 Filing Service (replaces step2)
- [ ] Create `etp_tracker/services/filing_service.py`
- [ ] `ingest_filings_for_trust(db, client, trust_id)` - Fetch and store filings
- [ ] Use `sec_client.load_submissions_json()` from core
- [ ] Check for duplicate accession numbers before inserting
- [ ] Update existing records if metadata changed
- [ ] Return count of new vs updated filings
- [ ] Add logging
- [ ] Write tests (mock SEC client)

#### 4.3 Extraction Service (replaces step3)
- [ ] Create `etp_tracker/services/extraction_service.py`
- [ ] `extract_funds_from_filing(db, client, filing_id)` - Extract series/tickers
- [ ] Use `core.sgml.parse_sgml_series_classes()`
- [ ] Use ticker extraction logic from old step3
- [ ] Use effective date extraction logic
- [ ] Create/update Series, Class, TickerHistory, EffectiveDate records
- [ ] Create FilingSeries relationships
- [ ] Handle delaying amendments flag
- [ ] Add logging
- [ ] Write tests (mock HTTP responses)

#### 4.4 Rollup Service (replaces step4)
- [ ] Create `etp_tracker/services/rollup_service.py`
- [ ] `get_latest_state(db, trust_id)` - Query latest per fund
- [ ] Implement priority logic: BPOS > APOS > APOS+75
- [ ] Return DataFrame or list of dicts
- [ ] Cache results (optional: use `@lru_cache`)
- [ ] Write tests

#### 4.5 Pipeline Orchestrator
- [ ] Create `scripts/run_pipeline.py` (new version)
- [ ] Accept CIK list as argument
- [ ] Call filing_service → extraction_service for each trust
- [ ] Add progress tracking (tqdm)
- [ ] Add error handling (continue on failure, log errors)
- [ ] Test end-to-end pipeline

### Deliverables
- ✅ Services replace all step2/3/4 CSV logic
- ✅ Database-first pipeline working
- ✅ Unit tests for all services
- ✅ Command-line script to run pipeline

### Success Criteria
- Can run `python scripts/run_pipeline.py --cik 2043954`
- New filings ingested into database
- Series/tickers extracted correctly
- Services have >80% test coverage

---

## Phase 5: REST API

**Duration**: 4-6 days
**Status**: ⏳ **UPCOMING**

### Goals
Build FastAPI application with endpoints for querying trusts, filings, series, and tickers.

### Tasks

#### 5.1 API Foundation
- [ ] Create `etp_tracker/api/app.py`
- [ ] Set up FastAPI app with CORS
- [ ] Add `etp_tracker/api/dependencies.py` for DB sessions
- [ ] Create `/api/health` endpoint (returns status, DB connection)
- [ ] Add error handlers (404, 500, etc.)
- [ ] Configure logging
- [ ] Test basic server: `uvicorn etp_tracker.api.app:app --reload`

#### 5.2 Pydantic Schemas
- [ ] Create `etp_tracker/api/schemas/trust.py`
  - `TrustResponse`, `TrustCreate`
- [ ] Create `etp_tracker/api/schemas/filing.py`
  - `FilingResponse`, `FilingListResponse`
- [ ] Create `etp_tracker/api/schemas/series.py`
  - `SeriesResponse`, `SeriesWithTickerResponse`
- [ ] Add validators and examples

#### 5.3 Trust Endpoints
- [ ] Create `etp_tracker/api/routes/trusts.py`
- [ ] `GET /api/trusts` - List all trusts
- [ ] `GET /api/trusts/{cik}` - Get trust by CIK
- [ ] `POST /api/trusts` - Add new trust
- [ ] `GET /api/trusts/{cik}/filings` - List filings for trust
- [ ] `GET /api/trusts/{cik}/series` - List series for trust
- [ ] Write API tests with `httpx`

#### 5.4 Filing Endpoints
- [ ] Create `etp_tracker/api/routes/filings.py`
- [ ] `GET /api/filings` - List filings (with filters: date range, form type)
- [ ] `GET /api/filings/{accession}` - Get filing details
- [ ] `GET /api/filings/{accession}/series` - Series in filing
- [ ] `POST /api/filings/refresh/{cik}` - Trigger filing ingestion
- [ ] Write API tests

#### 5.5 Series Endpoints
- [ ] Create `etp_tracker/api/routes/series.py`
- [ ] `GET /api/series` - List series (with filters: trust, status)
- [ ] `GET /api/series/{id}` - Get series details
- [ ] `GET /api/series/{id}/ticker-history` - Ticker changes
- [ ] `GET /api/series/{id}/filings` - Filings mentioning series
- [ ] Write API tests

#### 5.6 Special Queries
- [ ] Create `etp_tracker/api/routes/queries.py`
- [ ] `GET /api/queries/latest-state` - Latest state per fund (step4 equivalent)
- [ ] `GET /api/queries/recent-filings` - Recent filings across all trusts
- [ ] `GET /api/queries/upcoming-effective-dates` - Funds going effective soon
- [ ] `GET /api/queries/new-funds` - Recently launched funds
- [ ] Write API tests

#### 5.7 API Documentation
- [ ] Add OpenAPI descriptions to all endpoints
- [ ] Add examples to schemas
- [ ] Document query parameters
- [ ] Generate API docs: Visit `/docs` (auto-generated by FastAPI)
- [ ] Create `docs/API_DOCUMENTATION.md` with usage examples

### Deliverables
- ✅ Working REST API on `http://localhost:8000`
- ✅ All CRUD operations for trusts, filings, series
- ✅ Special query endpoints for analysis
- ✅ API tests with >80% coverage
- ✅ Auto-generated OpenAPI documentation

### Success Criteria
- Can query all trusts via API
- Can filter filings by date/form type
- Can get latest state for all funds
- API responses match expected schemas
- All tests pass

---

## Phase 6: Automation & Scheduling

**Duration**: 2-3 days
**Status**: ⏳ **UPCOMING**

### Goals
Automate pipeline runs with scheduling, add basic monitoring.

### Tasks

#### 6.1 Scheduler Setup
- [ ] Create `etp_tracker/scheduler/jobs.py`
- [ ] Implement `refresh_all_trusts()` job
  - Fetch trusts from database
  - Run filing_service + extraction_service for each
  - Log results
- [ ] Create `etp_tracker/scheduler/runner.py`
- [ ] Set up APScheduler with database job store
- [ ] Configure daily run schedule (e.g., 8 AM)
- [ ] Add health check job (every 5 minutes)

#### 6.2 Standalone Scheduler Script
- [ ] Create `scripts/run_scheduler.py`
- [ ] Load schedule from environment
- [ ] Start scheduler in foreground
- [ ] Add signal handlers (graceful shutdown)
- [ ] Test scheduled execution

#### 6.3 Windows Task Scheduler Integration
- [ ] Document how to add script to Task Scheduler
- [ ] Create `.bat` file to activate venv and run scheduler
- [ ] Test scheduled run on Windows
- [ ] Document in `docs/DEPLOYMENT.md`

### Deliverables
- ✅ Automated daily pipeline runs
- ✅ Scheduler script that can run as background service
- ✅ Windows Task Scheduler setup guide

### Success Criteria
- Scheduler runs daily without manual intervention
- Logs show successful pipeline executions
- Can stop/start scheduler cleanly

---

## Phase 7: Notifications & Alerts

**Duration**: 2-3 days
**Status**: ⏳ **UPCOMING**

### Goals
Add email and Slack notifications for new filings and effective dates.

### Tasks

#### 7.1 Notification Service
- [ ] Create `etp_tracker/services/notification_service.py`
- [ ] Implement `send_email(subject, body, recipients)`
  - Use SMTP (e.g., Gmail, SendGrid)
  - Load credentials from environment
- [ ] Implement `send_slack(message, webhook_url)`
  - Use Slack webhook API
- [ ] Add `format_filing_alert(filing)` - Format filing info
- [ ] Add `format_effective_date_alert(series, date)` - Format date alert

#### 7.2 Alert Logic
- [ ] Add change detection to filing_service
  - Track "last seen filing date" per trust
  - Return new filings since last run
- [ ] Add effective date monitoring
  - Query upcoming effective dates (next 7 days)
  - Return funds going effective soon
- [ ] Integrate alerts into scheduler job
  - After each pipeline run, send alerts for new items

#### 7.3 Testing
- [ ] Test email sending (use mailtrap.io for dev)
- [ ] Test Slack webhook
- [ ] Test alert formatting
- [ ] Add mock tests for notification service

### Deliverables
- ✅ Email notifications for new filings
- ✅ Slack notifications (optional)
- ✅ Alerts for upcoming effective dates
- ✅ Configurable notification preferences

### Success Criteria
- Receive email when new filing appears
- Receive alert for funds going effective in next 7 days
- Can disable notifications via environment variable

---

## Phase 8: Dashboard/UI (TBD Approach)

**Duration**: 5-7 days
**Status**: ⏳ **FUTURE**

### Goals
Build interface for browsing filings and analyzing competitive landscape.

### Options (Choose based on preference)

#### Option A: Streamlit Dashboard (Fastest)
- [ ] Create `etp_tracker/dashboard/app.py`
- [ ] Home page: Trust list with stats
- [ ] Filings page: Searchable table with filters
- [ ] Series page: Fund list with ticker history
- [ ] Analysis page: Competitive landscape charts
- [ ] Run: `streamlit run etp_tracker/dashboard/app.py`

#### Option B: FastAPI + Simple HTML (More Control)
- [ ] Create HTML templates in `etp_tracker/api/templates/`
- [ ] Add Jinja2 templating to FastAPI
- [ ] Build pages: Trust list, Filing search, Series detail
- [ ] Use HTMX for interactivity (optional)
- [ ] Serve via existing API app

#### Option C: Jupyter Notebook Interface (Familiar)
- [ ] Enhance `notebooks/ETP_Filing_Tracker_Interface.ipynb`
- [ ] Add database query cells (replace CSV reads)
- [ ] Add interactive widgets (ipywidgets)
- [ ] Use Voilà to convert to dashboard
- [ ] Run: `voila notebooks/ETP_Filing_Tracker_Interface.ipynb`

### Decision Point
**Recommendation**: Start with **Option C** (Jupyter + Voilà) since you're already comfortable with notebooks. Migrate to Streamlit (Option A) later if you want standalone deployment.

---

## Phase 9: Production Hardening

**Duration**: 3-5 days
**Status**: ⏳ **FUTURE**

### Goals
Prepare system for cloud deployment and production use.

### Tasks

#### 9.1 Security
- [ ] Add API authentication (JWT tokens or API keys)
- [ ] Implement rate limiting
- [ ] Add input validation for all endpoints
- [ ] Use prepared statements (SQLAlchemy handles this)
- [ ] Store secrets in vault (AWS Secrets Manager, etc.)
- [ ] Add HTTPS support (reverse proxy with nginx)

#### 9.2 Performance
- [ ] Add database indexes for common queries
- [ ] Implement caching (Redis or in-memory)
- [ ] Add pagination to list endpoints
- [ ] Optimize slow queries (EXPLAIN ANALYZE)
- [ ] Add connection pooling tuning

#### 9.3 Monitoring
- [ ] Add structured logging (JSON format)
- [ ] Set up log aggregation (CloudWatch, Papertrail, etc.)
- [ ] Add metrics (Prometheus, Datadog, etc.)
- [ ] Create health check dashboard
- [ ] Set up error alerting (Sentry, etc.)

#### 9.4 Deployment
- [ ] Create Dockerfile
- [ ] Create docker-compose.yml (app + postgres)
- [ ] Write deployment guide for AWS/DigitalOcean
- [ ] Set up CI/CD (GitHub Actions)
- [ ] Document backup/restore procedures

### Deliverables
- ✅ Production-ready application
- ✅ Docker containers
- ✅ Deployment documentation
- ✅ Monitoring and alerting

---

## Phase 10: Multi-Trust Expansion

**Duration**: 1-2 days
**Status**: ⏳ **FUTURE**

### Goals
Test system with multiple trusts, validate scalability.

### Tasks

#### 10.1 Add More Trusts
- [ ] Identify 5-10 major ETP issuers (Vanguard, iShares, SPDR, etc.)
- [ ] Add CIKs to database
- [ ] Run pipeline for each trust
- [ ] Verify data quality

#### 10.2 Validation
- [ ] Compare extracted data with SEC filings manually
- [ ] Check ticker symbol accuracy
- [ ] Validate effective dates
- [ ] Document any parser issues

#### 10.3 Optimization
- [ ] Parallelize trust processing (use ThreadPoolExecutor)
- [ ] Tune HTTP cache settings
- [ ] Optimize database queries for multiple trusts

### Deliverables
- ✅ 10+ trusts tracked in database
- ✅ Validated data quality
- ✅ Optimized performance

---

## Quick Reference: What's Next?

### ✅ Just Finished
- Phase 0: Documentation and planning

### 🔜 Up Next (Start Here!)
**Phase 1: Repository & Environment Setup**
1. Create initial Git commit
2. Set up PostgreSQL database
3. Create environment config files

**First 3 tasks**:
1. `git add -A && git commit -m "chore: initial commit - CSV pipeline v1.0"`
2. `git tag v1.0-csv-baseline`
3. Install PostgreSQL and create `etp_tracker` database

---

## Progress Tracking

| Phase | Status | Start Date | End Date | Notes |
|-------|--------|------------|----------|-------|
| 0: Foundation | ✅ Done | 2026-02-05 | 2026-02-05 | Docs complete |
| 1: Repo Setup | 🔜 Next | | | |
| 2: Database | ⏳ Upcoming | | | |
| 3: Migration | ⏳ Upcoming | | | |
| 4: Services | ⏳ Upcoming | | | |
| 5: API | ⏳ Upcoming | | | |
| 6: Scheduler | ⏳ Upcoming | | | |
| 7: Alerts | ⏳ Upcoming | | | |
| 8: Dashboard | 🤔 TBD | | | Need to pick approach |
| 9: Production | ⏳ Future | | | |
| 10: Multi-Trust | ⏳ Future | | | |

---

## Estimated Timeline

- **Phases 1-3** (Foundation + Database): **2 weeks**
- **Phases 4-5** (Services + API): **2 weeks**
- **Phases 6-7** (Automation + Alerts): **1 week**
- **Phase 8** (Dashboard): **1 week**
- **Phases 9-10** (Production + Scale): **1 week**

**Total**: ~7 weeks (part-time) or ~3.5 weeks (full-time)

---

## Notes & Decisions

**2026-02-05**: Created roadmap. User wants to focus on foundation before jumping into new features. Perfect ADHD-friendly approach.

**Decision Log**:
- Database: PostgreSQL (vs SQLite) - User's choice
- Tech stack: Keep current scraping libs, add SQLAlchemy + FastAPI
- Deployment: Local now, cloud later
- Dashboard: TBD (will decide in Phase 8)

**Future Considerations**:
- Content analysis: Store full filing text for competitive analysis?
- Real-time: WebSocket updates for dashboard?
- Multi-user: User accounts and permissions?
- Holdings data: Expand to NPORT-P filings?

---

**Remember**:
- One phase at a time
- Test as you go
- Update this file when you complete tasks
- Celebrate small wins! 🎉
