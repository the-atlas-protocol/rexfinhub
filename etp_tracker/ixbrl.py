"""
iXBRL extraction for OEF-tagged SEC filings.

Extracts structured data from inline XBRL tags embedded in
485BPOS and some 497 filing HTML. Uses the OEF (Open-End Fund)
and DEI (Document and Entity Information) taxonomies.

Falls back gracefully to empty dict if no iXBRL tags found.
"""
from __future__ import annotations
import re

# Tag extraction patterns
_IX_NONNUMERIC = re.compile(
    r'<ix:nonNumeric[^>]*name=["\']([^"\']+)["\'][^>]*>(.*?)</ix:nonNumeric>',
    re.DOTALL,
)
_IX_NONFRACTION = re.compile(
    r'<ix:nonFraction[^>]*name=["\']([^"\']+)["\'][^>]*>(.*?)</ix:nonFraction>',
    re.DOTALL,
)

# Concepts we extract
_TEXT_CONCEPTS = {
    "oef:ProspectusDate":               "prospectus_date",
    "dei:EntityRegistrantName":         "registrant_name",
    "dei:EntityCentralIndexKey":        "cik",
    "dei:DocumentType":                 "document_type",
    "dei:DocumentPeriodEndDate":        "period_end_date",
    "oef:ObjectivePrimaryTextBlock":    "objective_text",
    "oef:StrategyNarrativeTextBlock":   "strategy_text",
    "oef:RiskTextBlock":                "risk_text",
}

_NUMERIC_CONCEPTS = {
    "oef:ExpensesOverAssets":           "expense_ratio",
    "oef:ManagementFeesOverAssets":     "management_fee",
    "oef:NetExpensesOverAssets":        "net_expense_ratio",
    "oef:FeeWaiverOrReimbursementOverAssets": "fee_waiver",
    "oef:DistributionAndService12b1FeesOverAssets": "distribution_fee",
    "oef:OtherExpensesOverAssets":      "other_expenses",
}


def _clean_html(text: str) -> str:
    """Strip HTML tags from extracted text block content."""
    return re.sub(r"<[^>]+>", " ", text).strip()


def _parse_numeric(text: str) -> float | None:
    """Parse a percentage value from iXBRL numeric content."""
    cleaned = re.sub(r"<[^>]+>", "", text).strip()
    cleaned = cleaned.replace("%", "").replace(",", "").strip()
    if not cleaned or cleaned == "-":
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def extract_ixbrl_facts(html_text: str) -> dict:
    """Extract OEF and DEI facts from inline XBRL tags.

    Args:
        html_text: Raw HTML content of an iXBRL-enabled filing.

    Returns:
        Dict with structured facts. Keys include:
          prospectus_date, registrant_name, cik, document_type,
          expense_ratio, management_fee, net_expense_ratio,
          objective_text, strategy_text, risk_text
        Empty dict if no iXBRL tags found.
    """
    if not html_text or "<ix:" not in html_text:
        return {}

    result = {}

    # Extract text/date concepts (ix:nonNumeric)
    for match in _IX_NONNUMERIC.finditer(html_text):
        concept_name = match.group(1)
        value = match.group(2)

        if concept_name in _TEXT_CONCEPTS:
            field = _TEXT_CONCEPTS[concept_name]
            # Only keep first occurrence (most filings repeat per-series context)
            if field not in result:
                cleaned = _clean_html(value).strip()
                if cleaned:
                    result[field] = cleaned

    # Extract numeric concepts (ix:nonFraction)
    for match in _IX_NONFRACTION.finditer(html_text):
        concept_name = match.group(1)
        value = match.group(2)

        if concept_name in _NUMERIC_CONCEPTS:
            field = _NUMERIC_CONCEPTS[concept_name]
            if field not in result:
                parsed = _parse_numeric(value)
                if parsed is not None:
                    result[field] = parsed

    return result


def has_ixbrl(html_text: str) -> bool:
    """Quick check whether HTML contains inline XBRL tags."""
    return bool(html_text) and "<ix:" in html_text
