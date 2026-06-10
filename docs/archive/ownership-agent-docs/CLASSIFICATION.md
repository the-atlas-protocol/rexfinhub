# Fund Classification System

Ryu maintains a 5-category classification for all tracked ETPs. Your ownership pages should use these for grouping, filtering, and display. **These files are read-only for you.**

## The 5 Categories

| Code | Full Name | What It Covers | Example Products |
|------|-----------|----------------|-----------------|
| `LI` | Leveraged & Inverse | Daily leveraged ETFs (2x, 3x, 4x, 5x) — long or short | SOXL, TQQQ, NVDX, TSLT |
| `CC` | Covered Call / Income | Funds writing options for income | JEPI, FEPI, QYLD, JEPQ |
| `Crypto` | Cryptocurrency | Spot BTC/ETH, crypto equity, crypto derivatives | IBIT, ETHA, BITQ |
| `Defined` | Defined Outcome | Buffer, floor, barrier — structured payoff ETFs | BUFC, FLAO |
| `Thematic` | Thematic | AI, clean energy, cybersecurity, sector themes | ARKK, BOTZ, DRNZ |

## CSV Files (in `config/rules/`)

### fund_mapping.csv — Master category assignment

| Column | Example | Meaning |
|--------|---------|---------|
| `ticker` | `SOXL US` | Bloomberg ticker with exchange suffix |
| `etp_category` | `LI` | Which category this fund belongs to |
| `is_primary` | `1` | Primary assignment (a fund can be in multiple categories) |
| `source` | `manual` or `atlas` | How it was classified |

**2,259 entries.** A ticker can appear multiple times if it belongs to more than one category.

### rex_funds.csv — REX product list

| Column | Example | Meaning |
|--------|---------|---------|
| `ticker` | `FEPI US` | Bloomberg ticker for a REX-issued fund |

**99 entries.** Single column. Drives the `is_rex=True` flag.

### rex_suite_mapping.csv — REX branded suites

| Column | Example | Meaning |
|--------|---------|---------|
| `ticker` | `TSLT US` | Bloomberg ticker |
| `rex_suite` | `T-REX` | Branded suite name |

Suite values: `T-REX`, `MicroSectors`, `REX`, `Osprey`, `Equity Premium Income`, `Growth & Income`, `Crypto`, `Defined Outcome`, `Thematic`

### issuer_mapping.csv — Display names

| Column | Example | Meaning |
|--------|---------|---------|
| `etp_category` | `LI` | Category context |
| `issuer` | `Tidal Trust I` | Raw Bloomberg trust name |
| `issuer_nickname` | `Direxion` | Clean short display name |

### competitor_groups.csv — REX vs competitors

| Column | Example | Meaning |
|--------|---------|---------|
| `group_name` | `Single Stock: NVDA` | Competitive segment label |
| `rex_ticker` | `NVDX` | REX fund in this segment |
| `peer_ticker` | `NVDL` | Competitor fund |

Useful for the crossover analysis page — which institutions hold competitors but not REX.

### Category-specific attributes

Each category has its own attributes file:

**attributes_LI.csv:**
- `map_li_category`: Equity, Commodity, Fixed Income, Crypto, Currency, Volatility
- `map_li_subcategory`: Single Stock, Index/Basket/ETF Based
- `map_li_direction`: Long, Short
- `map_li_leverage_amount`: 2.0, 3.0, -1.0, etc.
- `map_li_underlier`: Bloomberg ticker of the underlying (e.g., `AAPL US`)

**attributes_CC.csv:**
- `map_cc_underlier`: Single stock underlier if applicable
- `map_cc_index`: SPX, NDX, RUT, Basket, etc.
- `cc_type`: Traditional, Synthetic
- `cc_category`: Broad Beta, Tech, Small Caps, Fixed Income, Crypto

**attributes_Crypto.csv:**
- `map_crypto_type`: Spot Single Asset, Equity, Derivatives-based, Hybrid
- `map_crypto_underlier`: BTC only, alt-coin only, multi-token crypto

**attributes_Defined.csv:**
- `map_defined_category`: Buffer, Floor, Dual Buffer, Accelerator, Barrier, Hedged Equity, Ladder

**attributes_Thematic.csv:**
- `map_thematic_category`: AI, Clean Energy, Cybersecurity, Healthcare, FinTech, Space, etc.

## How to Read These in Code

```python
import pandas as pd

# Load fund categories
fund_map = pd.read_csv('config/rules/fund_mapping.csv', engine='python', on_bad_lines='skip')

# Get all REX tickers
rex_tickers = pd.read_csv('config/rules/rex_funds.csv', engine='python')['ticker'].tolist()

# Get suite mapping
suites = pd.read_csv('config/rules/rex_suite_mapping.csv', engine='python')

# Check if a ticker is REX
is_rex = 'FEPI US' in rex_tickers  # True

# Get a ticker's category
category = fund_map[fund_map['ticker'] == 'SOXL US']['etp_category'].iloc[0]  # 'LI'

# Get L&I attributes for a ticker
li_attrs = pd.read_csv('config/rules/attributes_LI.csv', engine='python')
soxl = li_attrs[li_attrs['ticker'] == 'SOXL US'].iloc[0]
# soxl['map_li_leverage_amount'] = '3.0'
# soxl['map_li_direction'] = 'Long'
# soxl['map_li_underlier'] = 'SOX Index'
```

**Important:** Always use `engine='python'` and `on_bad_lines='skip'` with pandas CSV reads. The default C engine crashes on certain CSV edge cases.

## Market Status Codes

From `config/rules/market_status.csv`:

| Code | Meaning | Show on site? |
|------|---------|---------------|
| `ACTV` | Active / trading | Yes — primary display |
| `DLST` | Delisted | Historical only |
| `LIQU` | Liquidated | Historical only |
| `PEND` | Pending Listing | Yes — upcoming |
| `HALT` | Halted | Flag it |
| `SUSP` | Suspended | Flag it |
| `EXPD` | Expired | No |

For most ownership pages, filter to `ACTV` products only unless showing historical trends.
