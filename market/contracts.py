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
    """{rule_id: {label, expected, filter, ...}} — headline figures with expected values."""
    return load_contract().get("numbers", {}) or {}


def universes() -> dict:
    """{rule_id: {label, filter, ...}} — the broad report bodies (no fixed expected)."""
    return load_contract().get("universes", {}) or {}


def _all_rules() -> dict:
    """Every filterable rule: headline numbers ∪ universes."""
    return {**numbers(), **universes()}


def get_rule(rule_id: str) -> dict:
    rule = _all_rules().get(rule_id)
    if rule is None:
        raise KeyError(f"unknown report rule_id: {rule_id!r}")
    return rule


def expected_values() -> dict:
    """{rule_id: expected_int} for every number that declares an `expected`."""
    return {rid: r["expected"] for rid, r in numbers().items() if "expected" in r}


def _build_mask(df, filt: dict):
    """Boolean row-mask for a structured filter block (no dedup). The one place the
    field→column logic lives, so every consumer matches identically."""
    import pandas as pd  # lazy — keeps module import-light

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
    return mask


def report_mask(df, rule_id: str):
    """The boolean row-mask for a rule (pre-dedup) — for callers that need to intersect
    membership with other masks (e.g. the flow report's per-suite loop)."""
    return _build_mask(df, get_rule(rule_id).get("filter", {}) or {})


def apply_report_filter(df, rule_id: str):
    """Return the subset of `df` (a master DataFrame) matching the rule's filter + dedup.

    The ONE place a number's membership is computed — reports and the gate both call it,
    so the three old hand-written filters can't diverge again.
    """
    filt = get_rule(rule_id).get("filter", {}) or {}
    out = df[_build_mask(df, filt)].copy()
    dedup_col = filt.get("dedup_by")
    if dedup_col and dedup_col in out.columns:
        out = out[out[dedup_col].astype(str).str.len() > 0]
        out = out.drop_duplicates(subset=[dedup_col], keep="first")
    return out


def count_for_rule(df, rule_id: str) -> int:
    """Headline count for a rule (len of the filtered, de-duped set)."""
    return len(apply_report_filter(df, rule_id))


# ---------------------------------------------------------------------------
# Reader 4 — report AUDIENCE + content rules (intent layer; jargon backstop)
# ---------------------------------------------------------------------------
# Internal table/column names that must NEVER appear in an external report. These
# are the plumbing — a partner reading "from rex_products + fund_status" is the leak
# this whole layer exists to stop. Lowercased; the audit matches case-insensitively
# with word-ish boundaries so prose like "the products page" never false-hits.
FORBIDDEN_EXTERNAL_TERMS = [
    "rex_products",
    "fund_status",
    "mkt_master_data",
    "mkt_daily_snapshot",
    "etp_category",
    "primary_category",
    "mkt_fund_mapping",
    "fund_extractions",
    "send_log",
    "email_recipients",
    "reserved_symbols",
    "filings",
    "issuer_mapping",
]


def reports() -> dict:
    """{report_key: {audience, recipients?, content_rules?}} from the contract."""
    return load_contract().get("reports", {}) or {}


def report_audience(key: str) -> str:
    """'external' | 'internal' for a report key. Defaults to 'internal' when the key
    is unknown — the SAFE default (an unknown report is treated as not-for-partners,
    so the jargon audit does not relax for it)."""
    return (reports().get(key, {}) or {}).get("audience", "internal")


def report_content_rules(key: str) -> list:
    """The list of content principles a report must satisfy (empty for internal)."""
    return list((reports().get(key, {}) or {}).get("content_rules", []) or [])
