# Cross-pillar walk — 2026-05-08

Verifies 5 representative tickers are addressable + cross-linked across every relevant page in the v3 architecture.

## Tickers tested

- **NVDX** — REX flagship 2x leveraged
- **DJTU** — REX recent launch
- **NVDL** — GraniteShares competitor
- **JEPI** — JPMorgan covered-call

## Walk results

Per ticker, every page that should mention it. `loaded` = 200 OK; `ticker_present` = ticker symbol appears in HTML; `links_to_canonical` = `/funds/{ticker}` link present in markup.

### NVDX (REX flagship 2x leveraged)

| Page | URL | Status | Loaded | Ticker Present | Canonical Link |
|---|---|---|---|---|---|
| home page | `/` | 200 | OK | no | no |
| fund detail | `/funds/NVDX` | 200 | OK | YES | no |
| SEC ETP dashboard | `/sec/etp/` | 200 | OK | no | no |
| filings explorer | `/sec/etp/filings` | 200 | OK | no | no |
| L&I landscape | `/sec/etp/leverageandinverse` | 200 | OK | YES | YES |
| ETP calendar | `/tools/calendar` | 200 | OK | no | no |
| L&I candidates | `/tools/li/candidates` | 200 | OK | no | no |
| REX Ops calendar | `/operations/calendar` | 200 | OK | no | no |
| REX Ops pipeline | `/operations/pipeline` | 200 | OK | YES | YES |

### DJTU (REX recent launch)

| Page | URL | Status | Loaded | Ticker Present | Canonical Link |
|---|---|---|---|---|---|
| home page | `/` | 200 | OK | no | no |
| fund detail | `/funds/DJTU` | 200 | OK | YES | no |
| SEC ETP dashboard | `/sec/etp/` | 200 | OK | no | no |
| filings explorer | `/sec/etp/filings` | 200 | OK | no | no |
| L&I landscape | `/sec/etp/leverageandinverse` | 200 | OK | YES | YES |
| ETP calendar | `/tools/calendar` | 200 | OK | no | no |
| L&I candidates | `/tools/li/candidates` | 200 | OK | no | no |
| REX Ops calendar | `/operations/calendar` | 200 | OK | no | no |
| REX Ops pipeline | `/operations/pipeline` | 200 | OK | YES | YES |

### NVDL (GraniteShares competitor)

| Page | URL | Status | Loaded | Ticker Present | Canonical Link |
|---|---|---|---|---|---|
| home page | `/` | 200 | OK | no | no |
| fund detail | `/funds/NVDL` | 200 | OK | YES | no |
| SEC ETP dashboard | `/sec/etp/` | 200 | OK | no | no |
| filings explorer | `/sec/etp/filings` | 200 | OK | no | no |
| L&I landscape | `/sec/etp/leverageandinverse` | 200 | OK | YES | YES |
| ETP calendar | `/tools/calendar` | 200 | OK | no | no |
| L&I candidates | `/tools/li/candidates` | 200 | OK | YES | no |
| REX Ops calendar | `/operations/calendar` | 200 | OK | no | no |
| REX Ops pipeline | `/operations/pipeline` | 200 | OK | no | no |

### JEPI (JPMorgan covered-call)

| Page | URL | Status | Loaded | Ticker Present | Canonical Link |
|---|---|---|---|---|---|
| home page | `/` | 200 | OK | no | no |
| fund detail | `/funds/JEPI` | 200 | OK | YES | no |
| SEC ETP dashboard | `/sec/etp/` | 200 | OK | no | no |
| filings explorer | `/sec/etp/filings` | 200 | OK | no | no |
| L&I landscape | `/sec/etp/leverageandinverse` | 200 | OK | no | no |
| ETP calendar | `/tools/calendar` | 200 | OK | no | no |
| L&I candidates | `/tools/li/candidates` | 200 | OK | YES | no |
| REX Ops calendar | `/operations/calendar` | 200 | OK | no | no |
| REX Ops pipeline | `/operations/pipeline` | 200 | OK | no | no |

## Cross-link verification (from /funds/{ticker})

Each fund detail page should link OUT to:
- `/issuers/{name}` for the issuer
- `/stocks/{underlier}` for the underlier (if L&I or covered call)
- `/funds/{competitor}` for competitor products
- `/filings/{id}` for filings
- `/trusts/{slug}` for the trust

| Ticker | -> /issuers/ | -> /stocks/ | -> /funds/ (competitor) | -> /filings/ | -> /trusts/ |
|---|---|---|---|---|---|
| NVDX | YES | YES | YES | YES | YES |
| DJTU | YES | YES | no | YES | YES |
| NVDL | YES | YES | YES | YES | YES |
| JEPI | YES | no | YES | YES | YES |

## Summary

- 36/36 page loads successful (200 OK)
- 5 representative tickers walked across 9 pages each

**Pass criteria:** all pages load, canonical /funds/{ticker} URL is reachable for each, cross-links to /issuers/, /filings/, /trusts/ present on detail pages.