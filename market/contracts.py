"""Report-rules contract — the single machine-readable source of report intent.

Loads `config/contracts/report_numbers.yaml` and exposes it to the three readers:

  1. the AI classifier  -> `category_intent_text()` builds the per-category intent block
     spliced into the classification prompt (so "bonds are not income" lives in ONE place).
  2. the safety check    -> `expected_values()` + `apply_report_filter()` let preflight
     compare each headline number against its rule.
  3. the reports         -> `apply_report_filter(df, rule_id)` builds the ONE mask + dedup
     for a number, replacing the three hand-written filters in report_data.py.

Design goals (mirror market/resolve.py): dependency-light at import time (stdlib + yaml;
pandas imported lazily only inside apply_report_filter), offline-safe (a missing/broken
file degrades to {} with a warning, never crashes the chain), and cached so the YAML is
read once per process.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PATH = PROJECT_ROOT / "config" / "contracts" / "report_numbers.yaml"


@lru_cache(maxsize=1)
def load_contract(path: str | None = None) -> dict:
    """Parse the report-rules YAML. Returns {} on any failure (never raises)."""
    p = Path(path) if path else CONTRACT_PATH
    if not p.exists():
        log.warning("report contract not found at %s; readers fall back to defaults", p)
        return {}
    try:
        import yaml
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        if not isinstance(data, dict):
            log.error("report contract %s is not a mapping; ignoring", p)
            return {}
        return data
    except Exception as e:  # noqa: BLE001 — a bad contract must not crash the pipeline
        log.error("failed to load report contract %s: %s", p, e)
        return {}


# ---------------------------------------------------------------------------
# Reader 1 — the AI classifier
# ---------------------------------------------------------------------------
def category_definitions() -> dict:
    """{'LI': {name, means, is_not, looks_wrong_if}, 'CC': {...}}."""
    return load_contract().get("definitions", {}) or {}


def category_intent_text() -> str:
    """Render the per-category intent as a prompt block for the AI classifier.

    Empty string if the contract is unavailable, so the caller can splice it in
    unconditionally without breaking when the file is missing.
    """
    defs = category_definitions()
    if not defs:
        return ""
    lines = ["", "PER-CATEGORY INTENT (authoritative — decide by MEANING, not the label):"]
    for code, d in defs.items():
        means = " ".join((d.get("means") or "").split())
        is_not = " ".join((d.get("is_not") or "").split())
        wrong = " ".join((d.get("looks_wrong_if") or "").split())
        seg = f"- {code} ({d.get('name', code)}): {means}"
        if is_not:
            seg += f" NOT: {is_not}"
        if wrong:
            seg += f" LOOKS WRONG IF: {wrong}"
        lines.append(seg)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Readers 2 & 3 — the numbers (gate + reports)
# ---------------------------------------------------------------------------
def numbers() -> dict:
    """{rule_id: {label, expected, filter, ...}}."""
    return load_contract().get("numbers", {}) or {}


def get_rule(rule_id: str) -> dict:
    rule = numbers().get(rule_id)
    if rule is None:
        raise KeyError(f"unknown report rule_id: {rule_id!r}")
    return rule


def expected_values() -> dict:
    """{rule_id: expected_int} for every number that declares an `expected`."""
    return {rid: r["expected"] for rid, r in numbers().items() if "expected" in r}


def apply_report_filter(df, rule_id: str):
    """Return the subset of `df` (a master DataFrame) matching the rule's filter.

    Builds the mask from the rule's structured `filter` block and applies dedup. This
    is the ONE place a headline number's membership is computed — reports and the gate
    both call it, so the three old hand-written filters can't diverge again.
    """
    import pandas as pd  # lazy — keeps module import-light

    filt = get_rule(rule_id).get("filter", {}) or {}
    mask = pd.Series(True, index=df.index)

    if filt.get("is_rex"):
        mask &= df["is_rex"].fillna(0).astype(bool) if "is_rex" in df.columns else False
    if "etp_category" in filt and "etp_category" in df.columns:
        mask &= df["etp_category"] == filt["etp_category"]
    if "market_status" in filt and "market_status" in df.columns:
        mask &= df["market_status"] == filt["market_status"]
    if "suite" in filt and "rex_suite" in df.columns:
        mask &= df["rex_suite"] == filt["suite"]
    if "fund_type" in filt and "fund_type" in df.columns:
        mask &= df["fund_type"].isin(filt["fund_type"])
    if "category_display_contains" in filt and "category_display" in df.columns:
        mask &= df["category_display"].astype(str).str.contains(
            filt["category_display_contains"], case=False, na=False, regex=False
        )

    out = df[mask].copy()

    dedup_col = filt.get("dedup_by")
    if dedup_col and dedup_col in out.columns:
        out = out[out[dedup_col].astype(str).str.len() > 0]
        out = out.drop_duplicates(subset=[dedup_col], keep="first")
    return out


def count_for_rule(df, rule_id: str) -> int:
    """Headline count for a rule (len of the filtered, de-duped set)."""
    return len(apply_report_filter(df, rule_id))
