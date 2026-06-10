# Ownership Pillar — Database Schema Reference

## Your Database: `data/13f_holdings.db`

Three tables. All use SQLAlchemy ORM via `HoldingsBase` in `webapp/models.py`.

### institutions

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | Auto-increment |
| cik | String(20) | SEC Central Index Key, unique, not null |
| name | String(300) | Institution name, not null |
| city | String(100) | Nullable |
| state_or_country | String(10) | Nullable |
| manager_type | String(50) | Nullable (not currently populated) |
| aum_total | Float | Nullable (not currently populated) |
| filing_count | Integer | Incremented each ingestion |
| last_filed | Date | Nullable |
| created_at | DateTime | Auto: utcnow |
| updated_at | DateTime | Auto: utcnow, updates on change |

Indexes: `idx_institutions_name` on `name`.

### holdings

The main data table. ~3.47M rows for one quarter of data.

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | Auto-increment |
| institution_id | Integer FK | -> institutions.id, not null |
| report_date | Date | Period of report (quarter-end date), not null |
| filing_accession | String(30) | SEC accession number — deduplication key |
| issuer_name | String(300) | Name of the security issuer |
| cusip | String(12) | CUSIP identifier |
| value_usd | Float | **Always in full dollars** (pre-2023 data x1000 at ingestion) |
| shares | Float | Number of shares/units |
| share_type | String(10) | `SH` (shares) or `PRN` (principal amount) |
| investment_discretion | String(10) | `SOLE`, `DFND` (defined), or `OTR` (other) |
| voting_sole | Integer | Sole voting authority count |
| voting_shared | Integer | Shared voting authority count |
| voting_none | Integer | No voting authority count |
| is_tracked | Boolean | True when CUSIP matches our fund universe |
| created_at | DateTime | Auto: utcnow |

Indexes: `institution_id`, `cusip`, `report_date`, `(report_date, cusip)`, `is_tracked`, `(is_tracked, report_date)`.

**Performance note:** Always filter `is_tracked=True` first in web queries. This reduces the working set from millions of rows to thousands.

### cusip_mappings

Bridge table linking CUSIPs to fund tickers and trusts.

| Column | Type | Notes |
|--------|------|-------|
| id | Integer PK | Auto-increment |
| cusip | String(12) | Unique |
| ticker | String(20) | Bloomberg-style ticker (e.g., `SOXL US`) |
| fund_name | String(300) | Full fund name |
| trust_id | Integer | References main DB `trusts.id` (no enforced FK cross-DB) |
| source | String(30) | `mkt_master`, `manual`, or `holdings_enrichment` |
| created_at | DateTime | Auto: utcnow |

Index: `idx_cusip_mappings_ticker` on `ticker`.

---

## Main Database Tables You Can Read

These live in `data/etp_tracker.db`. Access via the API, CSV files, or dual DB sessions (see OWNERSHIP_PILLAR.md).

### fund_status

| Column | Type | Use for |
|--------|------|---------|
| ticker | String | Fund ticker (may be null for pending funds) |
| fund_name | String | Full SEC fund name |
| series_id | String | SEC Series ID — use for `/funds/{series_id}` links |
| trust_id | Integer | FK to trusts table |
| status | String | `EFFECTIVE`, `PENDING`, `DELAYED` |
| effective_date | Date | When the fund became effective |
| latest_form | String | Most recent SEC form type filed |
| latest_filing_date | Date | Date of most recent filing |

### trusts

| Column | Type | Use for |
|--------|------|---------|
| id | Integer | Primary key |
| cik | String | SEC CIK |
| name | String | Full trust name |
| slug | String | URL-safe name — use for `/trusts/{slug}` links |
| is_rex | Boolean | Is this a REX/T-REX trust |
| is_active | Boolean | Actively monitored |

### mkt_master_data

Bloomberg-enriched market data. Unique key: `(ticker, etp_category)`. A ticker CAN appear under multiple categories.

| Column | Type | Use for |
|--------|------|---------|
| ticker | String | Bloomberg ticker (e.g., `SOXL US`) |
| fund_name | String | Full fund name |
| aum | Float | Assets under management |
| etp_category | String | `LI`, `CC`, `Crypto`, `Defined`, `Thematic` |
| is_rex | Boolean | REX product flag |
| rex_suite | String | Suite name: `T-REX`, `MicroSectors`, `REX`, etc. |
| market_status | String | `ACTV`, `DLST`, `LIQU`, etc. |
| issuer_nickname | String | Short display name for the issuer |
| cusip | String | CUSIP identifier |

---

## Value Formatting Convention

Use this helper (already in holdings.py) for consistent USD display:

```python
def _fmt_value(val: float | None) -> str:
    if val is None:
        return "--"
    v = abs(val)
    if v >= 1_000_000_000_000:
        return f"${v / 1_000_000_000_000:.1f}T"
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:,.0f}"
```

## QoQ Change Logic

Holdings data is quarterly. The standard comparison:
1. Find `latest_date` = max `report_date` where `is_tracked=True`
2. Find `prior_date` = max `report_date` strictly before `latest_date`
3. Compare values at both dates per institution or per fund
4. Label changes: `NEW` (not in prior), `EXITED` (not in current), `INCREASED`, `DECREASED`, `UNCHANGED`
