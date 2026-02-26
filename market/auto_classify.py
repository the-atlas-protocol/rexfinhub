"""Auto-classification engine for ETF fund categorization.

Uses Bloomberg fields (asset_class_focus, fund_type, uses_leverage, is_crypto,
outcome_type, is_singlestock, fund_name) to auto-suggest:
  - strategy (expanded from 5 to 12+ categories)
  - underlier_type (Single Stock, Index, Commodity, etc.)
  - key attributes (direction, leverage_amount, underlier, duration, credit_quality, etc.)
  - confidence level (HIGH, MEDIUM, LOW)

This module is standalone -- no webapp dependencies.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import pandas as pd

from market.config import STRATEGIES  # noqa: F401

log = logging.getLogger(__name__)


@dataclass
class Classification:
    """Auto-classification result for a single fund."""
    ticker: str
    strategy: str
    confidence: str  # HIGH, MEDIUM, LOW
    reason: str
    underlier_type: str = ""
    attributes: dict[str, str] = field(default_factory=dict)


def classify_fund(row: pd.Series) -> Classification:
    """Classify a single fund from its Bloomberg data fields.

    Args:
        row: Series with columns: ticker, fund_name, issuer, asset_class_focus,
             fund_type, uses_leverage, leverage_amount, is_singlestock,
             is_crypto, outcome_type, fund_description, underlying_index,
             uses_derivatives, uses_swaps, is_40act, market_status

    Returns:
        Classification with strategy, confidence, attributes.
    """
    ticker = str(row.get("ticker", "")).strip()
    name = str(row.get("fund_name", "")).strip().upper()
    asset_class = str(row.get("asset_class_focus", "")).strip()
    fund_type = str(row.get("fund_type", "")).strip()
    uses_lev = _is_truthy(row.get("uses_leverage"))
    lev_amount = str(row.get("leverage_amount", "")).strip()
    is_ss_val = row.get("is_singlestock")
    is_crypto_val = str(row.get("is_crypto", "")).strip()
    outcome = str(row.get("outcome_type", "")).strip()
    description = str(row.get("fund_description", "")).strip().upper()
    underlying_idx = str(row.get("underlying_index", "")).strip().upper()
    text = f"{name} {description}"

    attrs: dict[str, str] = {}

    # --- Rule 1: Defined Outcome (highest priority -- very specific BBG field) ---
    if outcome and outcome.lower() not in ("", "nan", "none"):
        attrs["outcome_type"] = outcome
        return Classification(
            ticker=ticker,
            strategy="Defined Outcome",
            confidence="HIGH",
            reason=f"outcome_type={outcome}",
            underlier_type=_resolve_underlier_type(is_ss_val, ticker, name),
            attributes=attrs,
        )

    # --- Rule 2: Crypto ---
    if is_crypto_val.lower() == "cryptocurrency":
        _extract_crypto_attrs(name, is_ss_val, attrs)
        return Classification(
            ticker=ticker,
            strategy="Crypto",
            confidence="HIGH",
            reason=f"is_crypto=Cryptocurrency",
            underlier_type="Crypto Spot" if _is_spot_crypto(name) else "Crypto Index",
            attributes=attrs,
        )
    if _has_crypto_keywords(text):
        _extract_crypto_attrs(name, is_ss_val, attrs)
        return Classification(
            ticker=ticker,
            strategy="Crypto",
            confidence="MEDIUM",
            reason="crypto keywords in fund name",
            underlier_type="Crypto Spot" if _is_spot_crypto(name) else "Crypto Index",
            attributes=attrs,
        )

    # --- Rule 3: Leveraged & Inverse (uses_leverage=1) ---
    if uses_lev:
        _extract_leverage_attrs(name, lev_amount, is_ss_val, attrs)

        # Check if it's also an income/covered call product
        if _has_income_keywords(text):
            return Classification(
                ticker=ticker,
                strategy="Income / Covered Call",
                confidence="HIGH",
                reason=f"uses_leverage=1 + income keywords",
                underlier_type=_resolve_underlier_type(is_ss_val, ticker, name),
                attributes=attrs,
            )

        return Classification(
            ticker=ticker,
            strategy="Leveraged & Inverse",
            confidence="HIGH",
            reason=f"uses_leverage=1",
            underlier_type=_resolve_underlier_type(is_ss_val, ticker, name),
            attributes=attrs,
        )

    # --- Rule 4: Income / Covered Call (keyword-based) ---
    if _has_income_keywords(text):
        _extract_income_attrs(name, is_ss_val, attrs)
        return Classification(
            ticker=ticker,
            strategy="Income / Covered Call",
            confidence="MEDIUM" if _has_strong_income_keywords(text) else "LOW",
            reason="income/covered call keywords",
            underlier_type=_resolve_underlier_type(is_ss_val, ticker, name),
            attributes=attrs,
        )

    # --- Rule 5: Fixed Income ---
    if asset_class == "Fixed Income":
        _extract_fixed_income_attrs(name, description, attrs)
        return Classification(
            ticker=ticker,
            strategy="Fixed Income",
            confidence="HIGH",
            reason=f"asset_class_focus=Fixed Income",
            underlier_type="Index",
            attributes=attrs,
        )

    # --- Rule 6: Commodity ---
    if asset_class == "Commodity":
        _extract_commodity_attrs(name, attrs)
        return Classification(
            ticker=ticker,
            strategy="Commodity",
            confidence="HIGH",
            reason=f"asset_class_focus=Commodity",
            underlier_type=_resolve_underlier_type(is_ss_val, ticker, name),
            attributes=attrs,
        )

    # --- Rule 7: Alternative ---
    if asset_class == "Alternative":
        # Use is_crypto field for Alternative sub-strategies
        if is_crypto_val and is_crypto_val.lower() not in ("", "nan", "none", "cryptocurrency"):
            attrs["sub_category"] = is_crypto_val
        return Classification(
            ticker=ticker,
            strategy="Alternative",
            confidence="HIGH",
            reason=f"asset_class_focus=Alternative",
            underlier_type="Basket",
            attributes=attrs,
        )

    # --- Rule 7b: Specialty (VIX/Volatility, Currency, Income/Option, etc.) ---
    if asset_class == "Specialty":
        return _classify_specialty(ticker, name, description, text, is_ss_val, attrs)

    # --- Rule 7c: Real Estate ---
    if asset_class == "Real Estate":
        attrs["sector"] = "Real Estate"
        return Classification(
            ticker=ticker,
            strategy="Sector",
            confidence="HIGH",
            reason="asset_class_focus=Real Estate",
            underlier_type="Index",
            attributes=attrs,
        )

    # --- Rule 7d: Money Market ---
    if asset_class == "Money Market":
        attrs["duration"] = "Ultra Short"
        return Classification(
            ticker=ticker,
            strategy="Fixed Income",
            confidence="HIGH",
            reason="asset_class_focus=Money Market",
            underlier_type="Index",
            attributes=attrs,
        )

    # --- Rule 8: Mixed Allocation ---
    if asset_class == "Mixed Allocation":
        return Classification(
            ticker=ticker,
            strategy="Multi-Asset",
            confidence="HIGH",
            reason=f"asset_class_focus=Mixed Allocation",
            underlier_type="Basket",
            attributes=attrs,
        )

    # --- Rule 9: Thematic (keyword match on equity funds) ---
    if asset_class == "Equity" and _has_thematic_keywords(text):
        _extract_thematic_attrs(name, attrs)
        return Classification(
            ticker=ticker,
            strategy="Thematic",
            confidence="MEDIUM",
            reason="thematic keywords in fund name",
            underlier_type="Index",
            attributes=attrs,
        )

    # --- Rule 10: Sector (equity with sector focus) ---
    sector = _detect_sector(text, underlying_idx)
    if asset_class == "Equity" and sector:
        attrs["sector"] = sector
        return Classification(
            ticker=ticker,
            strategy="Sector",
            confidence="MEDIUM",
            reason=f"sector detected: {sector}",
            underlier_type="Index",
            attributes=attrs,
        )

    # --- Rule 11: International (equity with geographic focus) ---
    geo = _detect_geography(name, underlying_idx)
    if asset_class == "Equity" and geo:
        attrs["geography"] = geo
        return Classification(
            ticker=ticker,
            strategy="International",
            confidence="MEDIUM",
            reason=f"geography detected: {geo}",
            underlier_type="Index",
            attributes=attrs,
        )

    # --- Rule 12: Broad Beta (remaining passive equity) ---
    if asset_class == "Equity":
        return Classification(
            ticker=ticker,
            strategy="Broad Beta",
            confidence="LOW",
            reason="equity fund, no specific strategy signal",
            underlier_type="Index",
            attributes=attrs,
        )

    # --- Fallback ---
    return Classification(
        ticker=ticker,
        strategy="Unclassified",
        confidence="LOW",
        reason=f"asset_class={asset_class}, no matching rule",
        underlier_type="",
        attributes=attrs,
    )


def classify_all(etp_combined: pd.DataFrame) -> list[Classification]:
    """Classify all funds in the ETP dataset.

    Returns list of Classification objects, one per ticker.
    """
    results = []
    seen = set()
    for _, row in etp_combined.iterrows():
        ticker = str(row.get("ticker", "")).strip()
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        results.append(classify_fund(row))

    # Summary
    strategy_counts = {}
    for c in results:
        strategy_counts[c.strategy] = strategy_counts.get(c.strategy, 0) + 1
    log.info("Auto-classified %d funds: %s", len(results), strategy_counts)

    return results


def classify_to_dataframe(etp_combined: pd.DataFrame) -> pd.DataFrame:
    """Classify all funds and return as DataFrame.

    Columns: ticker, strategy, confidence, reason, underlier_type,
             plus one column per attribute key found.
    """
    results = classify_all(etp_combined)
    rows = []
    for c in results:
        row = {
            "ticker": c.ticker,
            "strategy": c.strategy,
            "confidence": c.confidence,
            "reason": c.reason,
            "underlier_type": c.underlier_type,
        }
        row.update(c.attributes)
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Attribute extractors
# ---------------------------------------------------------------------------

def _classify_specialty(
    ticker: str, name: str, description: str, text: str,
    is_ss_val, attrs: dict,
) -> Classification:
    """Classify Specialty asset class products.

    Breakdown: VIX/volatility, currency, income/option, trend, other.
    """
    # VIX / Volatility
    if re.search(r"\b(VIX|VOLATIL|CBOE|VXX|UVXY|SVXY|VIXY)\b", text):
        attrs["sub_category"] = "Volatility"
        return Classification(
            ticker=ticker,
            strategy="Alternative",
            confidence="HIGH",
            reason="Specialty: VIX/volatility keywords",
            underlier_type="Index",
            attributes=attrs,
        )

    # Currency
    if re.search(r"\b(CURRENCY|FOREX|FX\b|DOLLAR|EURO\b|YEN|POUND|SWISS\s*FRANC|USD|EUR|GBP|JPY)\b", text):
        attrs["sub_category"] = "Currency"
        return Classification(
            ticker=ticker,
            strategy="Alternative",
            confidence="HIGH",
            reason="Specialty: currency keywords",
            underlier_type="Currency",
            attributes=attrs,
        )

    # Income / Option overlay
    if re.search(r"\b(OPTION|INCOME|PREMIUM|PUT[\s-]*WRITE|COLLAR)\b", text):
        attrs["sub_category"] = "Option Strategy"
        return Classification(
            ticker=ticker,
            strategy="Income / Covered Call",
            confidence="MEDIUM",
            reason="Specialty: option/income keywords",
            underlier_type=_resolve_underlier_type(is_ss_val, ticker, name),
            attributes=attrs,
        )

    # Trend / Managed Futures
    if re.search(r"\b(TREND|MANAGED\s*FUTURES|CTA|MOMENTUM\s*STRATEGY)\b", text):
        attrs["sub_category"] = "Managed Futures"
        return Classification(
            ticker=ticker,
            strategy="Alternative",
            confidence="MEDIUM",
            reason="Specialty: trend/managed futures",
            underlier_type="Basket",
            attributes=attrs,
        )

    # Fallback for Specialty
    return Classification(
        ticker=ticker,
        strategy="Alternative",
        confidence="LOW",
        reason=f"Specialty: no specific sub-type detected",
        underlier_type="Basket",
        attributes=attrs,
    )


def _extract_leverage_attrs(name: str, lev_amount: str, is_ss_val, attrs: dict) -> None:
    """Extract direction and leverage_amount from fund name."""
    # Direction
    if re.search(r"\b(BULL|LONG)\b", name) and not re.search(r"\bSHORT\b", name):
        attrs["direction"] = "Bull"
    elif re.search(r"\b(BEAR|SHORT|INVERSE)\b", name):
        attrs["direction"] = "Bear"
    else:
        attrs["direction"] = "Neutral"

    # Leverage amount
    m = re.search(r"(-?\d+(?:\.\d+)?)[Xx]", name)
    if m:
        attrs["leverage_amount"] = m.group(1) + "x"
    elif lev_amount and lev_amount.lower() not in ("", "nan", "none"):
        attrs["leverage_amount"] = lev_amount

    # Underlier from is_singlestock
    if pd.notna(is_ss_val):
        ss = str(is_ss_val).strip()
        if ss:
            # Strip Bloomberg suffix
            underlier = re.sub(r"\s+(US|Curncy|Comdty|Index|Equity)$", "", ss)
            attrs["underlier"] = underlier


def _extract_income_attrs(name: str, is_ss_val, attrs: dict) -> None:
    """Extract income strategy type from fund name."""
    if re.search(r"\b(COVERED\s+CALL|0DTE|ODTE)\b", name):
        attrs["income_strategy"] = "Covered Call"
    elif re.search(r"\bAUTOCALLABLE\b", name):
        attrs["income_strategy"] = "Autocallable"
    elif re.search(r"\b(PREMIUM\s+INCOME|EQUITY\s+PREMIUM)\b", name):
        attrs["income_strategy"] = "Premium Income"
    elif re.search(r"\b(YIELDMAX|YIELDBOOST)\b", name):
        attrs["income_strategy"] = "Covered Call"
    elif re.search(r"\b(BUYWRITE|BUY-WRITE)\b", name):
        attrs["income_strategy"] = "Buy-Write"
    elif re.search(r"\bDIVIDEND\b", name):
        attrs["income_strategy"] = "Dividend"
    else:
        attrs["income_strategy"] = "Income"

    # Underlier
    if pd.notna(is_ss_val):
        ss = str(is_ss_val).strip()
        if ss:
            underlier = re.sub(r"\s+(US|Curncy|Comdty|Index|Equity)$", "", ss)
            attrs["underlier"] = underlier


def _extract_crypto_attrs(name: str, is_ss_val, attrs: dict) -> None:
    """Extract crypto-specific attributes."""
    if _is_spot_crypto(name):
        attrs["crypto_type"] = "Spot"
    else:
        attrs["crypto_type"] = "Index/Basket"

    # Underlier
    for coin, keywords in [
        ("Bitcoin", ["BITCOIN", "BTC"]),
        ("Ethereum", ["ETHEREUM", "ETH", "ETHER"]),
        ("Solana", ["SOLANA", "SOL"]),
        ("XRP", ["XRP", "RIPPLE"]),
    ]:
        if any(kw in name for kw in keywords):
            attrs["underlier"] = coin
            break


def _extract_fixed_income_attrs(name: str, description: str, attrs: dict) -> None:
    """Extract fixed income attributes: duration, credit_quality."""
    text = f"{name} {description}"

    # Duration
    if re.search(r"\b(ULTRA\s*SHORT|FLOATING\s*RATE|MONEY\s*MARKET|0-1\s*YEAR)\b", text):
        attrs["duration"] = "Ultra Short"
    elif re.search(r"\b(SHORT[\s-]*(TERM|DURATION)|1-3\s*YEAR|1-5\s*YEAR)\b", text):
        attrs["duration"] = "Short"
    elif re.search(r"\b(INTERMEDIATE|3-7\s*YEAR|5-10\s*YEAR|7-10\s*YEAR)\b", text):
        attrs["duration"] = "Intermediate"
    elif re.search(r"\b(LONG[\s-]*(TERM|DURATION)|10-20\s*YEAR|20\+\s*YEAR|25\+\s*YEAR|EXTENDED\s*DURATION)\b", text):
        attrs["duration"] = "Long"

    # Credit quality
    if re.search(r"\b(TREASURY|TREASURIES|T-BILL|GOVT|GOVERNMENT|SOVEREIGN)\b", text):
        attrs["credit_quality"] = "Treasury"
    elif re.search(r"\b(INVESTMENT\s*GRADE|IG\b|AGGREGATE|AGG\b)\b", text):
        attrs["credit_quality"] = "Investment Grade"
    elif re.search(r"\b(HIGH\s*YIELD|HY\b|JUNK|FALLEN\s*ANGEL|BELOW\s*INVESTMENT)\b", text):
        attrs["credit_quality"] = "High Yield"
    elif re.search(r"\b(MUNICIPAL|MUNI|TAX[\s-]*FREE|TAX[\s-]*EXEMPT)\b", text):
        attrs["credit_quality"] = "Municipal"
    elif re.search(r"\b(CORPORATE|CORP\b)\b", text):
        attrs["credit_quality"] = "Corporate"
    elif re.search(r"\b(CONVERTIBLE)\b", text):
        attrs["credit_quality"] = "Convertible"
    elif re.search(r"\b(MORTGAGE|MBS|AGENCY)\b", text):
        attrs["credit_quality"] = "Mortgage-Backed"
    elif re.search(r"\b(TIP[S]?\b|INFLATION)\b", text):
        attrs["credit_quality"] = "TIPS"


def _extract_commodity_attrs(name: str, attrs: dict) -> None:
    """Extract commodity type."""
    if re.search(r"\b(GOLD|GLD|PRECIOUS)\b", name):
        attrs["commodity_type"] = "Gold"
    elif re.search(r"\b(SILVER|SLV)\b", name):
        attrs["commodity_type"] = "Silver"
    elif re.search(r"\b(OIL|CRUDE|WTI|BRENT|PETROLEUM)\b", name):
        attrs["commodity_type"] = "Oil"
    elif re.search(r"\b(NATURAL\s*GAS|NATGAS)\b", name):
        attrs["commodity_type"] = "Natural Gas"
    elif re.search(r"\b(AGRICULTURE|CORN|WHEAT|SOYBEAN|COFFEE|SUGAR)\b", name):
        attrs["commodity_type"] = "Agriculture"
    elif re.search(r"\b(COPPER|ALUMINUM|STEEL|METALS|MINING)\b", name):
        attrs["commodity_type"] = "Base Metals"
    else:
        attrs["commodity_type"] = "Broad Commodity"


def _extract_thematic_attrs(name: str, attrs: dict) -> None:
    """Extract thematic category."""
    themes = [
        ("AI & Robotics", r"\b(ARTIFICIAL\s*INTELLIGENCE|AI\b|ROBOT|AUTONOMOUS)\b"),
        ("Clean Energy", r"\b(CLEAN\s*ENERGY|SOLAR|WIND|RENEWABLE|GREEN)\b"),
        ("Cybersecurity", r"\b(CYBER|CYBERSECURITY|SECURITY\s*TECH)\b"),
        ("Genomics & Biotech", r"\b(GENOMIC|BIOTECH|GENE|CRISPR)\b"),
        ("Cloud & SaaS", r"\b(CLOUD|SAAS|SOFTWARE\s*AS)\b"),
        ("Space & Defense", r"\b(SPACE|AEROSPACE|DEFENSE|DEFENCE)\b"),
        ("Cannabis", r"\b(CANNABIS|MARIJUANA|WEED)\b"),
        ("Metaverse & Gaming", r"\b(METAVERSE|GAMING|ESPORTS|VIDEO\s*GAME)\b"),
        ("Fintech", r"\b(FINTECH|FINANCIAL\s*TECH|PAYMENTS\s*TECH|BLOCKCHAIN\s*TECH)\b"),
        ("Infrastructure", r"\b(INFRASTRUCTURE|5G|DIGITAL\s*INFRA)\b"),
        ("Water", r"\b(WATER|CLEAN\s*WATER|AQUA)\b"),
        ("Lithium & Battery", r"\b(LITHIUM|BATTERY|EV\s*TECH)\b"),
    ]
    for theme_name, pattern in themes:
        if re.search(pattern, name):
            attrs["theme"] = theme_name
            return
    attrs["theme"] = "General Thematic"


# ---------------------------------------------------------------------------
# Keyword detectors
# ---------------------------------------------------------------------------

def _has_income_keywords(text: str) -> bool:
    return bool(re.search(
        r"\b(COVERED\s*CALL|OPTION\s*INCOME|PREMIUM\s*INCOME|YIELDMAX|YIELDBOOST|"
        r"BUYWRITE|BUY[\s-]*WRITE|EQUITY\s*PREMIUM|0DTE|ODTE|AUTOCALLABLE|"
        r"INCOME\s*STRATEGY|OPTION\s*OVERLAY)\b",
        text
    ))


def _has_strong_income_keywords(text: str) -> bool:
    return bool(re.search(
        r"\b(COVERED\s*CALL|YIELDMAX|YIELDBOOST|0DTE|ODTE|BUYWRITE|BUY[\s-]*WRITE|"
        r"AUTOCALLABLE|OPTION\s*INCOME\s*STRATEGY)\b",
        text
    ))


def _has_crypto_keywords(text: str) -> bool:
    return bool(re.search(
        r"\b(BITCOIN|BTC|ETHEREUM|ETH[^A-Z]|CRYPTO|BLOCKCHAIN|SOLANA|SOL\b|"
        r"XRP|RIPPLE|LITECOIN|DOGECOIN|DIGITAL\s*ASSET)\b",
        text
    ))


def _has_thematic_keywords(text: str) -> bool:
    return bool(re.search(
        r"\b(INNOVATION|GENOMIC|SPACE|ROBOT|FINTECH|CLOUD|METAVERSE|"
        r"CYBERSECURITY|CANNABIS|CLEAN\s*ENERGY|SOLAR|AUTONOMOUS|"
        r"ARTIFICIAL\s*INTELLIGENCE|AI\s+(?:AND|&)\s|LITHIUM|BATTERY|"
        r"DISRUPTIVE|NEXT\s*GEN|FUTURE)\b",
        text
    ))


_SECTOR_PATTERNS = {
    "Technology": r"\b(TECHNOLOGY|TECH\b|SEMICONDUCTOR|SOFTWARE|INFORMATION\s*TECH)\b",
    "Healthcare": r"\b(HEALTH\s*CARE|HEALTHCARE|BIOTECH|PHARMA|MEDICAL)\b",
    "Financials": r"\b(FINANCIAL|BANK|INSURANCE|FINL)\b",
    "Energy": r"\b(ENERGY|OIL\s*&\s*GAS|PETROLEUM|EXPLORATION\s*&\s*PROD)\b",
    "Consumer Discretionary": r"\b(CONSUMER\s*DISC|CONSUMER\s*CYCLICAL|RETAIL)\b",
    "Consumer Staples": r"\b(CONSUMER\s*STAPLE|FOOD\s*&\s*BEV)\b",
    "Industrials": r"\b(INDUSTRIAL|MANUFACTURING|TRANSPORT)\b",
    "Materials": r"\b(MATERIALS|MINING|METALS|STEEL|LUMBER)\b",
    "Utilities": r"\b(UTILIT|ELECTRIC\s*POWER|WATER\s*UTIL)\b",
    "Real Estate": r"\b(REAL\s*ESTATE|REIT|MORTGAGE\s*REIT)\b",
    "Communication Services": r"\b(COMMUNICATION|MEDIA|TELECOM)\b",
}


def _detect_sector(text: str, underlying_idx: str) -> str:
    combined = f"{text} {underlying_idx}"
    for sector, pattern in _SECTOR_PATTERNS.items():
        if re.search(pattern, combined):
            return sector
    return ""


_GEO_PATTERNS = {
    "China": r"\b(CHINA|CHINESE|CSI\s*300|SHANGHAI|HANG\s*SENG|MSCI\s*CHINA)\b",
    "Japan": r"\b(JAPAN|NIKKEI|TOPIX|MSCI\s*JAPAN)\b",
    "South Korea": r"\b(KOREA|KOSPI|MSCI\s*KOREA)\b",
    "India": r"\b(INDIA|NIFTY|MSCI\s*INDIA)\b",
    "Europe": r"\b(EUROPE|EURO\s*STOXX|FTSE\s*DEVELOPED|DAX|CAC|MSCI\s*EUROPE)\b",
    "Emerging Markets": r"\b(EMERGING\s*MARKET|EM\b|MSCI\s*EM\b|FRONTIER)\b",
    "International Developed": r"\b(INTERNATIONAL|EAFE|DEVELOPED\s*MARKET|EX[\s-]*US|ACWI)\b",
    "Latin America": r"\b(LATIN\s*AMERICA|BRAZIL|MEXICO|LATAM)\b",
    "Global": r"\b(GLOBAL|WORLD|ALL[\s-]*COUNTRY)\b",
}


def _detect_geography(name: str, underlying_idx: str) -> str:
    combined = f"{name} {underlying_idx}"
    for geo, pattern in _GEO_PATTERNS.items():
        if re.search(pattern, combined):
            return geo
    return ""


def _is_spot_crypto(name: str) -> bool:
    return bool(re.search(r"\b(SPOT|PHYSICAL)\b", name)) or \
        not re.search(r"\b(FUTURES|INDEX|BASKET|DIVERSIFIED)\b", name)


def _resolve_underlier_type(is_ss_val, ticker: str, name: str) -> str:
    """Resolve underlier_type from is_singlestock field."""
    if pd.isna(is_ss_val) or not str(is_ss_val).strip():
        return "Index"
    val = str(is_ss_val).strip()
    if val.endswith(" Curncy"):
        return "Currency"
    if val.endswith(" Comdty"):
        return "Commodity"
    if val.endswith(" Index"):
        return "Index"
    if val.endswith(" Equity"):
        return "Single Stock"
    if val.endswith(" US"):
        return "Single Stock"
    return "Single Stock"


def _is_truthy(val) -> bool:
    """Check if a Bloomberg boolean field is truthy."""
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return False
    s = str(val).strip().lower()
    return s in ("1", "1.0", "true", "y", "yes")
