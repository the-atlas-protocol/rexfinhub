# Market Strategy Taxonomy

Expanded classification system that replaces the original 5-category `etp_category` (LI, CC, Crypto, Defined, Thematic) with 13 strategies covering the full ETF universe.

## Strategy List

| Strategy | Count | % | Primary Signal |
|----------|-------|---|----------------|
| Broad Beta | 1,411 | 28.0% | Equity funds with no specific strategy signal |
| Fixed Income | 934 | 18.6% | `asset_class_focus=Fixed Income` |
| Leveraged & Inverse | 615 | 12.2% | `uses_leverage=1` |
| International | 487 | 9.7% | Geographic keywords in name/index |
| Defined Outcome | 406 | 8.1% | `outcome_type` field populated |
| Sector | 346 | 6.9% | GICS sector keywords in name/index |
| Crypto | 190 | 3.8% | `is_crypto=Cryptocurrency` or crypto keywords |
| Income / Covered Call | 176 | 3.5% | Income/covered call keywords |
| Thematic | 151 | 3.0% | Innovation/disruption keywords |
| Multi-Asset | 131 | 2.6% | `asset_class_focus=Mixed Allocation` |
| Commodity | 87 | 1.7% | `asset_class_focus=Commodity` |
| Alternative | 68 | 1.4% | `asset_class_focus=Alternative` |
| Unclassified | 30 | 0.6% | No matching rule |

## Classification Engine

Located in `market/auto_classify.py`. Standalone module with no webapp dependencies.

### Rule Priority Chain (12 rules, highest priority first)

1. **Defined Outcome** -- `outcome_type` BBG field is populated (Buffer, Floor, Accelerator, etc.)
2. **Crypto** -- `is_crypto=Cryptocurrency` or crypto keywords (Bitcoin, BTC, Ethereum, etc.)
3. **Leveraged & Inverse** -- `uses_leverage=1` (also checks for income keywords to reclassify)
4. **Income / Covered Call** -- Keywords: covered call, YieldMax, 0DTE, autocallable, premium income, buy-write
5. **Fixed Income** -- `asset_class_focus=Fixed Income`
6. **Commodity** -- `asset_class_focus=Commodity`
7. **Alternative** -- `asset_class_focus=Alternative`
8. **Multi-Asset** -- `asset_class_focus=Mixed Allocation`
9. **Thematic** -- Equity + thematic keywords (AI, clean energy, genomics, cybersecurity, etc.)
10. **Sector** -- Equity + GICS sector keywords (11 sectors)
11. **International** -- Equity + geographic keywords (9 regions)
12. **Broad Beta** -- Remaining equity funds (fallback)

### Confidence Levels

- **HIGH** (47.9%) -- Bloomberg field directly matches (asset_class_focus, uses_leverage, outcome_type, is_crypto)
- **MEDIUM** (22.8%) -- Keyword match with strong signal (covered call + leveraged, sector detected)
- **LOW** (29.3%) -- Weak signal or fallback classification (Broad Beta, generic equity)

### Underlier Types

| Type | Count | Description |
|------|-------|-------------|
| Index | 4,162 | Index/basket underlying |
| Single Stock | 411 | Individual equity |
| Basket | 199 | Multi-asset basket |
| Crypto Spot | 184 | Single cryptocurrency |
| Currency | 25 | FX underlying |
| Commodity | 15 | Commodity underlying |
| Crypto Index | 6 | Crypto index/basket |

## Mapping from Old to New

| Old etp_category | New Strategy |
|-----------------|-------------|
| LI | Leveraged & Inverse |
| CC | Income / Covered Call |
| Crypto | Crypto |
| Defined | Defined Outcome |
| Thematic | Thematic |
| (not in old system) | Broad Beta, Fixed Income, Sector, International, Commodity, Alternative, Multi-Asset |

The old 5-category system only covered ~1,900 of ~5,000 funds. The new system classifies 99.4% (5,002/5,032).

## Sector Detection (GICS-aligned)

11 sectors detected via keyword matching on fund name + underlying index:

- Technology (semiconductor, software, information tech)
- Healthcare (biotech, pharma, medical)
- Financials (bank, insurance)
- Energy (oil & gas, petroleum)
- Consumer Discretionary (retail, consumer cyclical)
- Consumer Staples (food & beverage)
- Industrials (manufacturing, transport)
- Materials (mining, metals, steel)
- Utilities (electric power)
- Real Estate (REIT, mortgage REIT)
- Communication Services (media, telecom)

## Geographic Detection

9 regions detected via keyword matching:

- China (CSI 300, Hang Seng, MSCI China)
- Japan (Nikkei, TOPIX, MSCI Japan)
- South Korea (KOSPI, MSCI Korea)
- India (Nifty, MSCI India)
- Europe (Euro Stoxx, DAX, CAC, MSCI Europe)
- Emerging Markets (MSCI EM, Frontier)
- International Developed (EAFE, ex-US, ACWI)
- Latin America (Brazil, Mexico, LATAM)
- Global (World, All-Country)

## Usage

```python
from market.ingest import read_input
from market.auto_classify import classify_to_dataframe

data = read_input()
classifications = classify_to_dataframe(data["etp_combined"])

# DataFrame columns: ticker, strategy, confidence, reason, underlier_type,
#                    plus attribute columns (direction, leverage_amount, etc.)
```

## Related

- [[MARKET_PIPELINE]] -- full pipeline documentation
- [[MARKET_ATTRIBUTES]] -- universal attribute system
- `market/auto_classify.py` -- source code
- `market/config.py` -- STRATEGIES list, ETP_CATEGORY_TO_STRATEGY mapping
