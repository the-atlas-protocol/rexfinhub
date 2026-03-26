# API Reference — Endpoints You Can Use

These existing REST API endpoints let you access fund, trust, and filing data without needing a local copy of the main database.

**Base URL:** `http://localhost:8000` (local) or `https://rexfinhub.com` (production)

**Authentication:** All `/api/v1/*` endpoints require `X-API-Key` header. For local dev, set `API_KEY` in your `config/.env` and use that value.

---

## Trusts

### GET /api/v1/trusts

List all monitored trusts with fund counts.

**Parameters:**
- `active_only` (bool, default true) — filter to active trusts only

**Response:**
```json
[
  {
    "id": 1,
    "cik": "0001064642",
    "name": "ProShares Trust",
    "slug": "proshares-trust",
    "is_rex": false,
    "is_active": true,
    "fund_count": 142
  }
]
```

---

## Funds

### GET /api/v1/funds

Query funds with optional filters.

**Parameters:**
- `status` (string) — filter by status: `EFFECTIVE`, `PENDING`, `DELAYED`
- `trust` (string) — filter by trust name (partial match)
- `limit` (int, default 100) — max results

**Response:**
```json
[
  {
    "id": 456,
    "trust_name": "REX ETF Trust",
    "fund_name": "REX FANG & Innovation Equity Premium Income ETF",
    "ticker": "FEPI",
    "status": "EFFECTIVE",
    "status_reason": "485BPOS filed",
    "effective_date": "2023-10-12",
    "latest_form": "485BPOS",
    "latest_filing_date": "2026-03-15",
    "series_id": "S000078123",
    "class_contract_id": "C000234567"
  }
]
```

Use `series_id` to link to fund detail pages: `/funds/{series_id}`

---

## Filings

### GET /api/v1/filings/recent

Get recent filings within N days.

**Parameters:**
- `days` (int, default 1) — look back period
- `form` (string) — filter by form type: `485BPOS`, `485APOS`, `485BXT`, `497`, `497K`
- `limit` (int, default 50) — max results

**Response:**
```json
[
  {
    "id": 12345,
    "trust_name": "ProShares Trust",
    "accession_number": "0001564590-26-012345",
    "form": "485BPOS",
    "filing_date": "2026-03-25",
    "primary_link": "https://www.sec.gov/Archives/..."
  }
]
```

---

## Pipeline Status

### GET /api/v1/pipeline/status

Get the status of the most recent pipeline run.

**Response:**
```json
{
  "status": "completed",
  "started_at": "2026-03-25T21:00:00",
  "finished_at": "2026-03-25T21:20:36",
  "trusts_processed": 236,
  "filings_found": 3,
  "error_message": null,
  "triggered_by": "manual"
}
```

---

## Health

### GET /api/v1/health

No auth required. Returns `{"status": "ok", "version": "2.0.0"}`.

---

## Cross-Pillar Linking

When your ownership pages reference funds or trusts, use these URL patterns:

| Link to | URL pattern | Example |
|---------|-------------|---------|
| Fund detail | `/funds/{series_id}` | `/funds/S000078123` |
| Trust detail | `/trusts/{slug}` | `/trusts/rex-etf-trust` |
| Filing explorer | `/filings/explorer?q={query}` | `/filings/explorer?q=SOXL` |
| Stock analysis | `/screener/stock/{ticker}` | `/screener/stock/NVDA` |
| Filing landscape | `/filings/landscape` | Direct link |
| Home page | `/` | Breadcrumbs |

Get `series_id` from the `/api/v1/funds` response. Get `slug` from the `/api/v1/trusts` response.
