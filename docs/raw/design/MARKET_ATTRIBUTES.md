# Market Attribute System

Universal key-value attributes that replace the per-category `map_*` columns. Each attribute applies across strategies where relevant.

## Current Attribute System (Legacy)

Per-category columns tied to specific `etp_category`:

| Category | Columns |
|----------|---------|
| LI | map_li_category, map_li_subcategory, map_li_direction, map_li_leverage_amount, map_li_underlier |
| CC | map_cc_underlier, map_cc_index |
| Crypto | map_crypto_is_spot, map_crypto_underlier |
| Defined | map_defined_category |
| Thematic | map_thematic_category |

**Problem**: Adding Fixed Income, Commodity, Sector, etc. would require new column groups for each. Doesn't scale.

## Universal Attribute Keys

14 universal keys that work across all strategies:

| Key | Values | Strategies |
|-----|--------|-----------|
| `direction` | Bull, Bear, Neutral | L&I, Income |
| `leverage_amount` | 1x, 1.5x, 2x, 3x, -1x, -2x, -3x | L&I |
| `underlier` | TSLA, SPY, Gold, Bitcoin, etc. | L&I, Income, Crypto, Commodity |
| `income_strategy` | Covered Call, 0DTE, Dividend, Premium, Buy-Write, Autocallable | Income |
| `duration` | Ultra Short, Short, Intermediate, Long | Fixed Income |
| `credit_quality` | Treasury, IG, HY, Municipal, Corporate, Convertible, MBS, TIPS | Fixed Income |
| `geography` | US, Japan, China, Europe, EM, International, Global | International, Sector |
| `sector` | Technology, Healthcare, Financials, Energy, ... (11 GICS) | Sector |
| `market_cap` | Large Cap, Mid Cap, Small Cap, All Cap | Broad Beta, Sector |
| `theme` | AI & Robotics, Clean Energy, Cybersecurity, Genomics, ... | Thematic |
| `outcome_type` | Buffer, Floor, Accelerator, Ladder | Defined Outcome |
| `commodity_type` | Gold, Silver, Oil, Natural Gas, Agriculture, Base Metals | Commodity |
| `crypto_type` | Spot, Index/Basket | Crypto |
| `sub_category` | Free-form refinement | Any |

## Auto-Extraction

The `market/auto_classify.py` engine auto-extracts attributes from Bloomberg fields:

### Leverage Attributes
- `direction`: extracted from fund name keywords (BULL/LONG -> Bull, BEAR/SHORT/INVERSE -> Bear)
- `leverage_amount`: extracted from name pattern (e.g., "2X" -> "2x") or BBG leverage_amount field
- `underlier`: extracted from `is_singlestock` field (strips Bloomberg suffix like " US", " Equity")

### Income Attributes
- `income_strategy`: keyword matching (Covered Call, 0DTE, YieldMax/YieldBoost -> Covered Call, Autocallable, Premium Income, Buy-Write, Dividend)

### Crypto Attributes
- `crypto_type`: Spot vs Index/Basket (looks for SPOT/PHYSICAL keywords)
- `underlier`: Bitcoin, Ethereum, Solana, XRP (keyword matching)

### Fixed Income Attributes
- `duration`: Ultra Short (floating rate, money market), Short (1-3yr), Intermediate (3-7yr), Long (10+yr)
- `credit_quality`: Treasury, Investment Grade, High Yield, Municipal, Corporate, Convertible, MBS, TIPS

### Commodity Attributes
- `commodity_type`: Gold, Silver, Oil, Natural Gas, Agriculture, Base Metals, Broad Commodity

### Thematic Attributes
- `theme`: AI & Robotics, Clean Energy, Cybersecurity, Genomics & Biotech, Cloud & SaaS, Space & Defense, Cannabis, Metaverse & Gaming, Fintech, Infrastructure, Water, Lithium & Battery

### Sector Attributes
- `sector`: 11 GICS-aligned sectors detected via name/index keyword matching

### Geography Attributes
- `geography`: 9 regions detected via name/index keyword matching

## Future: Unified Database Schema

The current per-category CSV files (`attributes_LI.csv`, `attributes_CC.csv`, etc.) will be replaced by a single `attributes.csv` with key-value pairs:

```
ticker,attribute_key,attribute_value,source
TQQQ,direction,Bull,auto
TQQQ,leverage_amount,3x,auto
TQQQ,underlier,QQQ,auto
JEPI,income_strategy,Covered Call,manual
BND,duration,Intermediate,auto
BND,credit_quality,Investment Grade,auto
```

This allows:
- Adding new attribute types without schema changes
- Mixing auto-detected and manually-verified values
- Tracking provenance (auto vs manual)
- One table instead of 5+ CSVs

## Related

- [[MARKET_PIPELINE]] -- full pipeline documentation
- [[MARKET_STRATEGIES]] -- strategy taxonomy and classification engine
- `market/auto_classify.py` -- attribute extraction code
- `market/config.py` -- ATTRIBUTE_KEYS list
