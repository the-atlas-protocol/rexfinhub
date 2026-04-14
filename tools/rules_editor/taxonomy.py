"""Universal ETF/ETN taxonomy for fund classification.

Morningstar/Bloomberg-style hierarchy: primary_category → sub_category.
Independent of the 5 tracked REX categories (LI/CC/Crypto/Defined/Thematic)
which drive existing reports. This is the taxonomy the AI classifier uses
to fill the fund_taxonomy table.

Design notes:
  - Flat-ish: primary_category is a single enum, sub_category is free-text
    guided by prompt examples. Deep trees get confusing for the LLM.
  - Asset class is separate so we can screen by asset without picking a
    single primary category (a Gold Miners ETF is Equity primary, but
    asset_class='Commodity' for filters).
  - Tags (style/factor/thematic) are lists so a fund can be multiple things.
"""
from __future__ import annotations

# Primary categories — exhaustive top-level buckets
PRIMARY_CATEGORIES = [
    # Equity
    "Equity: US Broad",
    "Equity: US Large Cap",
    "Equity: US Mid Cap",
    "Equity: US Small Cap",
    "Equity: US Sector",
    "Equity: US Factor/Style",
    "Equity: International Developed",
    "Equity: Emerging Markets",
    "Equity: Single Country",
    "Equity: Global",
    # Fixed Income
    "Fixed Income: Treasury",
    "Fixed Income: Corporate IG",
    "Fixed Income: High Yield",
    "Fixed Income: Municipal",
    "Fixed Income: International",
    "Fixed Income: Inflation Protected",
    "Fixed Income: Floating Rate",
    "Fixed Income: Preferred/Convertible",
    # Commodity & Currency
    "Commodity: Precious Metals",
    "Commodity: Energy",
    "Commodity: Agriculture",
    "Commodity: Industrial Metals",
    "Commodity: Broad",
    "Currency",
    # Alternatives
    "Alternative: Real Estate",
    "Alternative: Infrastructure",
    "Alternative: Hedged Equity",
    "Alternative: Long-Short",
    "Alternative: Managed Futures",
    "Alternative: Multi-Asset",
    "Alternative: Volatility",
    # Strategy-defined (existing 5 + expansion)
    "Leveraged & Inverse",          # the REX LI bucket
    "Income: Covered Call/Options", # the REX CC bucket
    "Crypto",                        # the REX Crypto bucket
    "Defined Outcome",               # the REX Defined bucket
    "Thematic",                      # the REX Thematic bucket
    "Active Management",             # actively managed without a specific theme
    # Catch-all
    "Other / Unclassified",
]

# Asset class (orthogonal — used for screener filters)
ASSET_CLASSES = [
    "Equity",
    "Fixed Income",
    "Commodity",
    "Currency",
    "Real Estate",
    "Multi-Asset",
    "Crypto",
    "Volatility",
    "Other",
]

# Region
REGIONS = [
    "US",
    "International Developed",
    "Emerging Markets",
    "Frontier Markets",
    "Global",
    "Single Country",
    "N/A",
]

# US GICS-level sectors (for sector funds)
SECTORS = [
    "Communication Services",
    "Consumer Discretionary",
    "Consumer Staples",
    "Energy",
    "Financials",
    "Healthcare",
    "Industrials",
    "Information Technology",
    "Materials",
    "Real Estate",
    "Utilities",
]

# Style tags (free-text, but these are the common ones)
STYLE_TAGS_EXAMPLES = [
    "Value", "Growth", "Blend",
    "Large Cap", "Mid Cap", "Small Cap", "Micro Cap",
    "Dividend", "High Dividend", "Dividend Growth",
    "Quality", "ESG",
    "Active", "Passive",
    "Short Duration", "Long Duration",
    "Investment Grade", "High Yield",
]

# Factor tags
FACTOR_TAGS_EXAMPLES = [
    "Low Volatility", "Momentum", "Quality", "Value", "Size",
    "Multi-Factor", "Equal Weight", "Risk Parity", "Minimum Variance",
]

# Thematic tags (for Thematic category — also used across categories when relevant)
THEMATIC_TAGS_EXAMPLES = [
    "AI", "Robotics", "Clean Energy", "Cybersecurity", "Genomics", "Biotech",
    "Cloud Computing", "Space", "Defense", "Nuclear", "Uranium", "Quantum",
    "Metaverse", "Gaming", "Fintech", "5G/Infrastructure", "Water",
    "Lithium/Battery", "EV", "Agriculture", "Cannabis", "Psychedelic",
    "Sports Betting", "Housing", "SPAC", "Blockchain",
]


def taxonomy_prompt_section() -> str:
    """Return a formatted taxonomy reference for inclusion in the LLM system prompt."""
    lines = ["# Fund Taxonomy\n"]
    lines.append("## primary_category (pick exactly one)\n")
    for cat in PRIMARY_CATEGORIES:
        lines.append(f"- {cat}")
    lines.append("\n## asset_class (pick exactly one)\n")
    for a in ASSET_CLASSES:
        lines.append(f"- {a}")
    lines.append("\n## region (pick exactly one)\n")
    for r in REGIONS:
        lines.append(f"- {r}")
    lines.append("\n## sector (only for sector-specific equity funds; otherwise null)\n")
    for s in SECTORS:
        lines.append(f"- {s}")
    lines.append("\n## style_tags examples (zero or more, as a JSON list)\n")
    lines.append(", ".join(STYLE_TAGS_EXAMPLES))
    lines.append("\n\n## factor_tags examples\n")
    lines.append(", ".join(FACTOR_TAGS_EXAMPLES))
    lines.append("\n\n## thematic_tags examples\n")
    lines.append(", ".join(THEMATIC_TAGS_EXAMPLES))
    return "\n".join(lines)


def is_valid_primary(category: str) -> bool:
    return category in PRIMARY_CATEGORIES


def is_valid_asset_class(ac: str) -> bool:
    return ac in ASSET_CLASSES


def is_valid_region(r: str) -> bool:
    return r in REGIONS
