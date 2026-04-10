"""Classification data validation — checks for duplicates, orphans, mismatches.

Run on every market sync and display results on admin dashboard.
"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from market.config import RULES_DIR

log = logging.getLogger(__name__)

_TRACKED_CATEGORIES = ["LI", "CC", "Crypto", "Defined", "Thematic"]
_ATTR_FILES = {
    "LI": "attributes_LI.csv",
    "CC": "attributes_CC.csv",
    "Crypto": "attributes_Crypto.csv",
    "Defined": "attributes_Defined.csv",
    "Thematic": "attributes_Thematic.csv",
}


def validate_classifications() -> dict:
    """Run all classification data quality checks.

    Returns:
        {
            "issues": [{"type": str, "severity": str, "message": str, "tickers": list}],
            "summary": {"total_funds": int, "categories": dict, "issue_count": int},
        }
    """
    issues = []

    fm_path = RULES_DIR / "fund_mapping.csv"
    if not fm_path.exists():
        return {"issues": [{"type": "missing_file", "severity": "error",
                            "message": "fund_mapping.csv not found", "tickers": []}],
                "summary": {"total_funds": 0, "categories": {}, "issue_count": 1}}

    fm = pd.read_csv(fm_path, engine="python", on_bad_lines="skip")
    fm["ticker"] = fm["ticker"].astype(str).str.strip()
    fm_tickers = set(fm["ticker"])

    # 1. Duplicate tickers within same category
    dupes = fm[fm.duplicated(subset=["ticker", "etp_category"], keep=False)]
    if len(dupes) > 0:
        dupe_tickers = sorted(dupes["ticker"].unique().tolist())
        issues.append({
            "type": "duplicate",
            "severity": "error",
            "message": f"{len(dupe_tickers)} funds duplicated within same category",
            "tickers": dupe_tickers[:20],
        })

    # 2. Missing attributes (in fund_mapping but no attributes row)
    for cat in _TRACKED_CATEGORIES:
        attr_file = _ATTR_FILES.get(cat)
        if not attr_file:
            continue
        attr_path = RULES_DIR / attr_file
        mapped = set(fm[fm["etp_category"] == cat]["ticker"])
        if attr_path.exists():
            attr = pd.read_csv(attr_path, engine="python", on_bad_lines="skip")
            if "ticker" in attr.columns:
                attr["ticker"] = attr["ticker"].astype(str).str.strip()
                attr_tickers = set(attr["ticker"])
                missing = sorted(mapped - attr_tickers)
                if missing:
                    issues.append({
                        "type": "missing_attributes",
                        "severity": "warning",
                        "message": f"{len(missing)} {cat} funds missing attributes",
                        "tickers": missing[:20],
                    })
        else:
            issues.append({
                "type": "missing_file",
                "severity": "error",
                "message": f"attributes_{cat}.csv not found",
                "tickers": [],
            })

    # 3. Orphan attributes (in attributes CSV but not in fund_mapping for that category)
    for cat in _TRACKED_CATEGORIES:
        attr_file = _ATTR_FILES.get(cat)
        if not attr_file:
            continue
        attr_path = RULES_DIR / attr_file
        if not attr_path.exists():
            continue
        attr = pd.read_csv(attr_path, engine="python", on_bad_lines="skip")
        if "ticker" not in attr.columns:
            continue
        attr["ticker"] = attr["ticker"].astype(str).str.strip()
        mapped_to_cat = set(fm[fm["etp_category"] == cat]["ticker"])
        orphans = sorted(set(attr["ticker"]) - mapped_to_cat)
        if orphans:
            issues.append({
                "type": "orphan_attributes",
                "severity": "warning",
                "message": f"{len(orphans)} entries in attributes_{cat}.csv not mapped to {cat} in fund_mapping",
                "tickers": orphans[:20],
            })

    # Summary
    categories = {}
    for cat in _TRACKED_CATEGORIES:
        categories[cat] = len(fm[fm["etp_category"] == cat])

    return {
        "issues": issues,
        "summary": {
            "total_funds": len(fm),
            "categories": categories,
            "issue_count": len(issues),
        },
    }
