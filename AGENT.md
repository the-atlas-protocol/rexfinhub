# AGENT: Trust-Expansion
**Task**: TASK-F — Trust Expansion (100+ New SEC Trusts)
**Branch**: feature/trust-expansion
**Status**: DONE

## Progress Reporting
Write timestamped progress to: `.agents/progress/Trust-Expansion.md`
Format:
```
## [HH:MM] Status: X/Y issuers processed
- Added: Trust Name (CIK: XXXXXXXXXX)
- Skipped: Issuer Name (reason)
```

Update every 10 issuers processed. This is a LONG-RUNNING task.

## Your Files
- `etp_tracker/trusts.py` (add new trusts)
- `.agents/progress/Trust-Expansion.md` (detailed discovery report)

## STEP 1: Explore the Data

```python
import pandas as pd
from pathlib import Path

# Try OneDrive path first, fall back to local
LOCAL = Path(r"C:\Users\RyuEl-Asmar\REX Financial LLC\REX Financial LLC - Rex Financial LLC\Product Development\MasterFiles\MASTER Data\The Dashboard.xlsx")
FALLBACK = Path("data/DASHBOARD/The Dashboard.xlsx")
DATA_FILE = LOCAL if LOCAL.exists() else FALLBACK

# Read issuers from data_import
df = pd.read_excel(DATA_FILE, sheet_name='data_import')
print("Columns:", df.columns.tolist()[:20])

# Find the issuer column
issuer_col = next((c for c in df.columns if 'issuer' in str(c).lower()), None)
print("Issuer col:", issuer_col)

if issuer_col:
    issuers = df[issuer_col].dropna().unique()
    print(f"Unique issuers: {len(issuers)}")
    print(issuers[:30])
```

Write the full list of unique issuers to progress file.

## STEP 2: Read Existing Trusts

Read `etp_tracker/trusts.py` to get ALL currently tracked trust names and CIKs. Build a set of existing trust slugs/names to check against.

```python
# In trusts.py, TRUST_CIKS looks like:
# "trust-slug": (CIK_INT, "Full Trust Name"),
```

Extract ALL existing trust names (lowercase) for comparison.

## STEP 3: ETN Issuers to Skip

These issuers file ETNs (Exchange Traded Notes), NOT 485 forms. Skip them:
- BMO, Deutsche Bank, DB, UBS, ETRACS, JP Morgan, JPM, Barclays, Credit Suisse,
- Citigroup, HSBC, Bank of America, BAML, Wells Fargo, Goldman Sachs, Morgan Stanley

Skip any issuer with these names/prefixes.

## STEP 4: Fund Types That Don't File 485

These typically file S-1 or N-2 (not 485) — skip for 485 search but note them:
- Interval funds (typically N-2)
- Business Development Companies (BDC)
- Closed-end funds (CEF)
- Plain ETNs

## STEP 5: EDGAR Search Process

For each issuer NOT already covered AND not an ETN:

```python
import requests
import time
import json

HEADERS = {"User-Agent": "REX-ETP-Tracker research@rexfin.com"}

def search_edgar(issuer_name: str) -> list:
    """Search EDGAR for 485BPOS filings from this issuer."""
    # Clean issuer name (remove /USA, /FUND PARENT, etc.)
    clean = issuer_name.split('/')[0].strip()

    url = f'https://efts.sec.gov/LATEST/search-index?q="{clean}"&forms=485BPOS&dateRange=custom&startdt=2020-01-01'
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        time.sleep(0.35)  # Rate limit: 10 req/s max
        if resp.status_code != 200:
            return []
        data = resp.json()
        hits = data.get('hits', {}).get('hits', [])
        return hits
    except Exception as e:
        print(f"Error searching {issuer_name}: {e}")
        return []


def verify_cik(cik: int) -> dict:
    """Verify CIK via submissions JSON. Returns {name, cik} or empty dict."""
    padded = str(cik).zfill(10)
    url = f"https://data.sec.gov/submissions/CIK{padded}.json"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        time.sleep(0.35)
        if resp.status_code != 200:
            return {}
        data = resp.json()
        return {"name": data.get("name", ""), "cik": cik}
    except Exception:
        return {}
```

For each search result:
1. Extract `_source.entity_id` (or `_source.display_names[0]`) — this gives the entity name
2. Extract `_source.file_num` or `_id` to get the CIK
3. Verify the CIK via submissions JSON
4. Check if the entity name matches the issuer (don't add wrong entities)

## STEP 6: Add Verified Trusts to trusts.py

Read `etp_tracker/trusts.py` carefully first. Then use `add_trust()` function (it exists in the file) OR manually add to the `TRUST_CIKS` dict.

To use add_trust():
```python
from etp_tracker.trusts import add_trust
# add_trust(cik: int, name: str, slug: str)
# slug is lowercase-hyphenated form of name
add_trust(1234567890, "ProShares Trust III", "proshares-trust-iii")
```

OR directly append to TRUST_CIKS dict in trusts.py — read the file to see the format.

CRITICAL: NEVER ADD A TRUST WITHOUT VERIFYING THE CIK via submissions JSON. Never guess.

## STEP 7: Priority Issuers to Check

These issuers are highly likely to have 485-filing trusts not yet tracked:

**L&I Single Stock (high priority)**:
- Tradr 2X Long / 2X Short funds (various trusts)
- GraniteShares (multiple trusts)
- Leverage Shares (UK-based, may not file US 485)
- T-Rex 2X / 4X funds (very new issuer)
- Bitwise (equity leveraged, separate from crypto)
- REXSHARES (this is REX's own)

**L&I Index (check for additional trusts)**:
- ProShares may have ProShares Trust, ProShares Trust II, ProShares Trust III
- Direxion may have multiple trusts
- Rafferty Asset Management

**Income / Covered Call**:
- Defiance ETFs
- Amplify ETFs
- NEOS Investments
- Roundhill Investments
- YieldMax ETF Trust (check if all sub-trusts tracked)
- Simplify Asset Management

**Defined Outcome**:
- Innovator ETFs (Innovator Capital Management — multiple series trusts)
- First Trust Buffer ETFs (First Trust Portfolios — many trusts)
- Pacer ETFs
- AIM ETF Products

**Crypto**:
- 21Shares US (Bitcoin ETF)
- Bitwise Bitcoin ETF Trust
- VanEck Bitcoin Trust
- Fidelity Wise Origin Bitcoin Fund
- BlackRock/iShares Bitcoin Trust

**Thematic**:
- Themes ETF Trust
- Global X ETFs (additional trusts beyond what's tracked)
- WisdomTree (any additional trusts)

## Discovery Report Format

Write detailed results to `.agents/progress/Trust-Expansion.md`:
```markdown
# Trust Expansion Discovery Report
Generated: [DATE]

## Summary
- Issuers checked: X
- Trusts added: Y
- Skipped (ETN): Z
- Skipped (already tracked): W
- Manual review needed: V

## Added Trusts
| Trust Name | CIK | Issuer | Category |
|-----------|-----|--------|----------|
| ProShares Trust III | 0001234567 | ProShares | L&I |

## Skipped - Already Tracked
- ProShares → ProShares Trust (CIK: XXXXXXXXXX) already in trusts.py

## Skipped - No 485 Filings
- DB (Deutsche Bank ETNs) — files ETN prospectuses, not 485 forms

## Manual Review Needed
- [Issuer] — multiple possible EDGAR matches, need human verification
```

## Commit Convention
```
git add etp_tracker/trusts.py .agents/progress/Trust-Expansion.md
git commit -m "feat: Trust expansion - add N new verified SEC trusts from Bloomberg issuer data"
```

## Done Criteria
- [ ] All Bloomberg issuers checked against existing trusts
- [ ] At least 10 new verified trusts added to trusts.py
- [ ] Every added trust has CIK verified via submissions JSON
- [ ] Discovery report written to .agents/progress/Trust-Expansion.md
- [ ] No unverified CIKs added
