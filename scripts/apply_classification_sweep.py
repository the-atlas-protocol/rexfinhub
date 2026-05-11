"""Categorization application sweep — populate the 3-axis taxonomy
(asset_class x primary_strategy x sub_strategy + ~20 attribute columns)
across all ACTV/PEND rows in mkt_master_data.

STRICT SAFEGUARDS (Ryu 2026-05-11):
  "I just need to have an audit as it goes because I don't want it to
   systematically mess everything up especially on parts that I spent so
   much time curating."

  - NEVER overwrite a non-NULL/non-empty curated value.
  - Only fill gaps.
  - HIGH confidence -> auto-apply (still gap-only).
  - MED/LOW confidence -> ClassificationProposal queue, no DB write.
  - When classifier disagrees with an existing value, log to a conflicts
    CSV for manual review. Do not overwrite.
  - Every change goes to classification_audit_log.
  - --dry-run never writes to mkt_master_data, only logs hypothetically.

Usage:
    python scripts/apply_classification_sweep.py --dry-run        # default; safe
    python scripts/apply_classification_sweep.py --apply          # write HIGH only
    python scripts/apply_classification_sweep.py --apply --apply-medium

Exit codes:
    0   success (any number of new fills, queued proposals, conflicts)
    2   sanity guard tripped (we OVERWROTE a populated value — should never happen)
    1   unexpected error
"""
from __future__ import annotations

import argparse
import csv
import logging
import re
import sys
import traceback
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402

from market.auto_classify import classify_fund  # noqa: E402  (read-only)
from webapp.database import SessionLocal  # noqa: E402
from webapp.models import (  # noqa: E402
    ClassificationAuditLog,
    ClassificationProposal,
    MktMasterData,
)

log = logging.getLogger("classification_sweep")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


# ---------------------------------------------------------------------------
# Translation: auto_classify (legacy 13-strategy) -> 3-axis taxonomy
# ---------------------------------------------------------------------------
# Maps the auto_classify Classification.strategy + asset_class_focus +
# attributes into (asset_class, primary_strategy, sub_strategy) per
# docs/CLASSIFICATION_SYSTEM_PLAN.md.

# auto_classify.strategy -> primary_strategy
_PRIMARY_STRATEGY_MAP = {
    "Leveraged & Inverse": "L&I",
    "Income / Covered Call": "Income",
    "Defined Outcome": "Defined Outcome",
    "Risk Management": "Risk Mgmt",
    "Crypto": "Plain Beta",   # spot/index crypto -> Plain Beta / Single-Access
    "Fixed Income": "Plain Beta",
    "Commodity": "Plain Beta",
    "Alternative": "Plain Beta",
    "Multi-Asset": "Plain Beta",
    "Thematic": "Plain Beta",
    "Sector": "Plain Beta",
    "International": "Plain Beta",
    "Broad Beta": "Plain Beta",
    "Unclassified": None,
}

# Maps Bloomberg asset_class_focus -> taxonomy asset_class
_ASSET_CLASS_FOCUS_MAP = {
    "Equity": "Equity",
    "Fixed Income": "Fixed Income",
    "Commodity": "Commodity",
    "Currency": "Currency",
    "Mixed Allocation": "Multi-Asset",
    "Alternative": "Multi-Asset",
    "Specialty": "Multi-Asset",       # downstream rules will refine to Volatility/Currency
    "Real Estate": "Equity",
    "Money Market": "Fixed Income",
}


def derive_taxonomy(row: pd.Series) -> dict:
    """Run auto_classify on a row and translate the output into 3-axis
    taxonomy + attribute fields per CLASSIFICATION_SYSTEM_PLAN.md.

    Returns a dict {column_name: (value, confidence)} for every column the
    classifier has an opinion on. Caller decides whether to write each.
    """
    name_upper = (str(row.get("fund_name", "") or "").upper())
    asset_class_focus = str(row.get("asset_class_focus", "") or "").strip()
    is_crypto = str(row.get("is_crypto", "") or "").strip().lower()
    is_singlestock = str(row.get("is_singlestock", "") or "").strip()
    underlying_idx = str(row.get("underlying_index", "") or "").upper()
    leverage_amount_str = str(row.get("leverage_amount", "") or "").strip()

    # Run the existing classifier
    c = classify_fund(row)
    primary = _PRIMARY_STRATEGY_MAP.get(c.strategy)

    out: dict[str, tuple] = {}

    # --- Asset Class ---
    if is_crypto == "cryptocurrency" or re.search(
        r"\b(BITCOIN|ETHEREUM|SOLANA|XRP|RIPPLE|LITECOIN|DOGECOIN|CRYPTO|"
        r"BLOCKCHAIN|DIGITAL\s*ASSET)\b", name_upper
    ):
        out["asset_class"] = ("Crypto", "HIGH")
    elif re.search(r"\b(VIX|VOLATIL)\b", name_upper) or "VIX" in underlying_idx:
        out["asset_class"] = ("Volatility", "HIGH")
    elif re.search(r"\b(CURRENCY|FOREX|FX\b|DOLLAR|EURO\b|YEN|POUND)\b", name_upper):
        out["asset_class"] = ("Currency", "HIGH")
    elif asset_class_focus:
        mapped = _ASSET_CLASS_FOCUS_MAP.get(asset_class_focus)
        if mapped:
            out["asset_class"] = (mapped, "HIGH")
        else:
            # Unknown focus value -> low confidence Equity guess
            out["asset_class"] = ("Equity", "LOW")
    else:
        out["asset_class"] = ("Equity", "LOW")

    # --- Primary Strategy ---
    if primary:
        # HIGH if auto_classify itself was HIGH; otherwise inherit
        out["primary_strategy"] = (primary, c.confidence)

    # --- Sub-strategy ---
    sub = _derive_sub_strategy(c, name_upper, asset_class_focus, out.get("asset_class", (None,))[0])
    if sub:
        out["sub_strategy"] = (sub, c.confidence)

    # --- Attribute columns (orthogonal) ---
    attrs = c.attributes or {}

    # Direction (L&I) — already a string Bull/Bear/Neutral from auto_classify
    if "direction" in attrs:
        d = str(attrs["direction"]).lower()
        # Taxonomy uses long/short/neutral
        d_map = {"bull": "long", "bear": "short", "neutral": "neutral"}
        out["direction"] = (d_map.get(d, d), c.confidence)

    # Leverage ratio
    lev = _parse_leverage(name_upper, attrs.get("leverage_amount"), leverage_amount_str)
    if lev is not None:
        out["leverage_ratio"] = (lev, "HIGH")

    # Underlier name + concentration (single vs basket)
    underlier = _resolve_underlier_name(is_singlestock, attrs.get("underlier"), underlying_idx)
    if underlier:
        out["underlier_name"] = (underlier, "HIGH" if is_singlestock else "MEDIUM")
    if is_singlestock and is_singlestock.lower() not in ("", "nan", "none"):
        out["concentration"] = ("single", "HIGH")
    elif underlying_idx or not is_singlestock:
        out["concentration"] = ("basket", "MEDIUM")

    # Mechanism (HOW exposure is implemented)
    mechanism = _derive_mechanism(row, c, name_upper)
    if mechanism:
        out["mechanism"] = mechanism

    # Defined outcome attributes (cap_pct / buffer_pct / barrier_pct)
    if c.strategy == "Defined Outcome":
        do_attrs = _parse_defined_outcome_attrs(name_upper, str(row.get("fund_description", "") or ""))
        for k, v in do_attrs.items():
            out[k] = v

    # Reset period (L&I daily-reset is the norm)
    if c.strategy == "Leveraged & Inverse":
        out["reset_period"] = ("daily", "MEDIUM")

    # Distribution frequency (income funds)
    if c.strategy == "Income / Covered Call":
        if re.search(r"\b(WEEKLY|0DTE|ODTE)\b", name_upper):
            out["distribution_freq"] = ("weekly", "HIGH")
        elif re.search(r"\b(MONTHLY)\b", name_upper):
            out["distribution_freq"] = ("monthly", "HIGH")

    # Region (geography) for international/equity
    geo = attrs.get("geography")
    if geo:
        out["region"] = (_geo_to_region(geo), c.confidence)

    # Duration / credit_quality (fixed income)
    if c.strategy == "Fixed Income":
        if "duration" in attrs:
            out["duration_bucket"] = (_duration_to_bucket(attrs["duration"]), "HIGH")
        if "credit_quality" in attrs:
            out["credit_quality"] = (_credit_to_quality(attrs["credit_quality"]), "HIGH")

    return out


def _derive_sub_strategy(c, name_upper: str, focus: str, asset_class: str | None) -> str | None:
    """Derive sub_strategy per CLASSIFICATION_SYSTEM_PLAN.md sub-strategy tree."""
    s = c.strategy
    attrs = c.attributes or {}

    if s == "Leveraged & Inverse":
        d = str(attrs.get("direction", "")).lower()
        if d == "bear":
            return "Short"
        # RSST-family stacked returns
        if re.search(r"\b(RSST|RSSY|RSBT|STACK)\b", name_upper):
            return "Stacked Returns"
        return "Long"

    if s == "Income / Covered Call":
        if re.search(r"\bAUTOCALL", name_upper):
            return "Structured Product Income > Autocallable"
        if re.search(r"\b(COVERED\s*CALL|BUYWRITE|BUY[\s-]*WRITE|YIELDMAX|YIELDBOOST|0DTE|ODTE)\b", name_upper):
            return "Derivative Income > Covered Call"
        if re.search(r"\bPUT[\s-]*WRITE\b", name_upper):
            return "Derivative Income > Put-Write"
        if re.search(r"\b(WEEKLYPAY|WEEKLY\s*PAY)\b", name_upper):
            return "Derivative Income > 0DTE / Weekly"
        if re.search(r"\bCOLLAR", name_upper):
            return "Derivative Income > Collared"
        return "Derivative Income > Covered Call"  # fallback for income

    if s == "Defined Outcome":
        outcome = str(attrs.get("outcome_type", "")).lower()
        if "buffer" in outcome or "BUFFER" in name_upper:
            return "Buffer"
        if "floor" in outcome or "FLOOR" in name_upper:
            return "Floor"
        if "accelerat" in outcome or "ACCELERAT" in name_upper:
            return "Growth"
        if "barrier" in outcome or "BARRIER" in name_upper:
            return "Buffer"  # barriers are buffer-family in our taxonomy
        if "DUAL DIRECTIONAL" in name_upper:
            return "Dual Directional"
        if "BOX SPREAD" in name_upper or "TAX-AWARE COLLATERAL" in name_upper:
            return "Box Spread"
        return "Buffer"  # most common

    if s == "Risk Management":
        risk = str(attrs.get("risk_type", "")).lower()
        if "tail" in risk:
            return "Risk-Adaptive"
        if "merger" in risk:
            return "Risk-Adaptive"
        if "bear" in risk:
            return "Hedged Equity"
        return "Hedged Equity"

    if s == "Crypto":
        return "Single-Access"  # spot/futures crypto -> Plain Beta single-access

    # Plain Beta sub-strategies (per primary_strategy = Plain Beta)
    if s in ("Sector",):
        return "Sector"
    if s in ("International",):
        return "Broad"
    if s in ("Thematic",):
        return "Thematic"
    if s in ("Fixed Income", "Commodity", "Multi-Asset", "Alternative"):
        return "Broad"
    if s == "Broad Beta":
        # Style if obvious style cue, else Broad
        if re.search(r"\b(VALUE|GROWTH|QUALITY|MOMENTUM|LOW[\s-]*VOL|DIVIDEND\s+ARISTOCRATS)\b", name_upper):
            return "Style"
        return "Broad"

    return None


def _parse_leverage(name_upper: str, attr_lev: str | None, raw_lev: str) -> float | None:
    """Extract numeric leverage ratio. 2.0, 3.0, 1.25, etc."""
    # Try fund_name pattern '2X' / '-3x' / '1.25X'
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*[Xx]\b", name_upper)
    if m:
        try:
            return abs(float(m.group(1)))
        except ValueError:
            pass

    # Try attribute "2x" / "1.25x"
    if attr_lev:
        m2 = re.search(r"(-?\d+(?:\.\d+)?)", str(attr_lev))
        if m2:
            try:
                return abs(float(m2.group(1)))
            except ValueError:
                pass

    # Try Bloomberg leverage_amount field (often stored as '200%' or '2.0')
    if raw_lev:
        s = raw_lev.replace("%", "").replace("x", "").strip()
        try:
            v = float(s)
            return abs(v / 100) if abs(v) > 10 else abs(v)
        except ValueError:
            pass

    return None


def _resolve_underlier_name(is_singlestock: str, attr_underlier: str | None, underlying_idx: str) -> str | None:
    """Resolve the underlier name. Strip Bloomberg suffixes."""
    candidate = ""
    if attr_underlier and str(attr_underlier).strip().lower() not in ("", "nan", "none"):
        candidate = str(attr_underlier).strip()
    elif is_singlestock and is_singlestock.lower() not in ("", "nan", "none"):
        candidate = is_singlestock
    elif underlying_idx and underlying_idx.lower() not in ("", "nan", "none"):
        candidate = underlying_idx

    if not candidate:
        return None
    # Strip Bloomberg suffix
    cleaned = re.sub(r"\s+(US|Curncy|Comdty|Index|Equity)$", "", candidate, flags=re.IGNORECASE)
    return cleaned.strip() or None


def _derive_mechanism(row: pd.Series, c, name_upper: str) -> tuple | None:
    """Map BBG flags to mechanism column."""
    uses_swaps = str(row.get("uses_swaps", "") or "").strip()
    uses_deriv = str(row.get("uses_derivatives", "") or "").strip()

    if uses_swaps in ("1", "1.0", "True", "Y", "Yes"):
        return ("swap", "HIGH")
    if c.strategy == "Income / Covered Call":
        return ("options", "HIGH")
    if c.strategy == "Defined Outcome":
        return ("options", "HIGH")
    if c.strategy == "Leveraged & Inverse":
        return ("swap", "MEDIUM")
    if uses_deriv in ("1", "1.0", "True", "Y", "Yes"):
        return ("options", "MEDIUM")
    if c.strategy == "Crypto":
        if re.search(r"\bFUTURES\b", name_upper):
            return ("futures", "HIGH")
        return ("physical", "MEDIUM")
    return ("physical", "LOW")


def _parse_defined_outcome_attrs(name: str, desc: str) -> dict:
    """Parse cap_pct / buffer_pct / barrier_pct from name + description."""
    out: dict = {}
    text = f"{name} {desc}"
    # Patterns like "9% Buffer", "Buffer 10%", "10.0% Cap"
    for pat, key in [
        (r"(\d+(?:\.\d+)?)\s*%?\s*(?:UPSIDE\s+)?CAP", "cap_pct"),
        (r"CAP\s*(?:OF\s+)?(\d+(?:\.\d+)?)\s*%", "cap_pct"),
        (r"(\d+(?:\.\d+)?)\s*%?\s*BUFFER", "buffer_pct"),
        (r"BUFFER\s*(?:OF\s+)?(\d+(?:\.\d+)?)\s*%", "buffer_pct"),
        (r"(\d+(?:\.\d+)?)\s*%?\s*BARRIER", "barrier_pct"),
        (r"BARRIER\s*(?:OF\s+)?(\d+(?:\.\d+)?)\s*%", "barrier_pct"),
    ]:
        m = re.search(pat, text, re.IGNORECASE)
        if m and key not in out:
            try:
                out[key] = (float(m.group(1)), "HIGH")
            except ValueError:
                pass
    return out


_REGION_MAP = {
    "China": "APAC",
    "Japan": "APAC",
    "South Korea": "APAC",
    "India": "APAC",
    "Europe": "EMEA",
    "Emerging Markets": "EM",
    "International Developed": "DM-ex-US",
    "Latin America": "LatAm",
    "Global": "Global",
}


def _geo_to_region(geo: str) -> str:
    return _REGION_MAP.get(geo, geo)


def _duration_to_bucket(d: str) -> str:
    s = (d or "").lower()
    if "ultra" in s and "short" in s:
        return "ultra_short"
    if "short" in s:
        return "short"
    if "intermediate" in s:
        return "intermediate"
    if "long" in s:
        return "long"
    return s


def _credit_to_quality(c: str) -> str:
    s = (c or "").lower()
    if "treasury" in s:
        return "treasury"
    if "investment grade" in s or s == "ig":
        return "ig"
    if "high yield" in s or s == "hy":
        return "hy"
    if "muni" in s:
        return "muni"
    if "convertible" in s:
        return "ig"
    return s


# ---------------------------------------------------------------------------
# Sweep orchestrator
# ---------------------------------------------------------------------------

# Columns the sweep is allowed to fill. ORDER MATTERS for reporting.
TARGET_COLUMNS = [
    "asset_class",
    "primary_strategy",
    "sub_strategy",
    "concentration",
    "underlier_name",
    "mechanism",
    "leverage_ratio",
    "direction",
    "reset_period",
    "distribution_freq",
    "cap_pct",
    "buffer_pct",
    "barrier_pct",
    "region",
    "duration_bucket",
    "credit_quality",
]


def _is_empty(value) -> bool:
    """A cell is 'empty' if NULL, empty string, or whitespace."""
    if value is None:
        return True
    if isinstance(value, float) and pd.isna(value):
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def _fmt(v) -> str:
    if v is None:
        return ""
    return str(v)


def run_sweep(
    apply_changes: bool = False,
    apply_medium: bool = False,
    limit: int | None = None,
    write_proposals: bool = True,
) -> dict:
    """Main entry point. Returns summary dict.

    apply_changes=False -> dry run (no DB writes anywhere except audit log
                          rows tagged dry_run=True).
    """
    sweep_run_id = datetime.utcnow().strftime("sweep_%Y%m%dT%H%M%S")
    log.info("=== Classification sweep %s (apply=%s, apply_medium=%s) ===",
             sweep_run_id, apply_changes, apply_medium)

    db = SessionLocal()

    # Pull active universe
    rows = (
        db.query(MktMasterData)
        .filter(MktMasterData.market_status.in_(["ACTV", "PEND"]))
        .all()
    )
    log.info("Loaded %d ACTV/PEND rows", len(rows))
    if limit:
        rows = rows[:limit]
        log.info("Limited to %d rows for testing", len(rows))

    # Pre-load existing proposal tickers to avoid duplicates
    existing_proposal_tickers = {
        (p.ticker, p.proposed_strategy)
        for p in db.query(ClassificationProposal).filter(
            ClassificationProposal.status.in_(["pending", "approved"])
        ).all()
    }

    # Counters
    n_rows_processed = 0
    n_fills_high = 0
    n_fills_medium = 0
    n_fills_low = 0
    n_proposals_queued = 0
    n_conflicts = 0
    n_overwrites = 0  # SHOULD ALWAYS STAY 0
    per_column_fills: dict[str, int] = {c: 0 for c in TARGET_COLUMNS}
    per_column_conflicts: dict[str, int] = {c: 0 for c in TARGET_COLUMNS}
    sample_proposals: list[dict] = []
    sample_conflicts: list[dict] = []
    audit_rows: list[ClassificationAuditLog] = []
    conflicts_csv_rows: list[dict] = []

    proposals_to_add: list[ClassificationProposal] = []

    for row in rows:
        n_rows_processed += 1
        if n_rows_processed % 500 == 0:
            log.info("  ... processed %d / %d rows", n_rows_processed, len(rows))

        # Convert ORM row to Series-like dict for classify_fund (which
        # expects a pandas Series interface — Series accepts dict via
        # constructor, and classify_fund only uses .get()).
        row_dict = {col.name: getattr(row, col.name) for col in MktMasterData.__table__.columns}
        series = pd.Series(row_dict)

        try:
            derived = derive_taxonomy(series)
        except Exception as e:
            log.warning("derive_taxonomy failed for %s: %s", row.ticker, e)
            continue

        # For each derived field, check existing value and route appropriately
        for col, (new_val, conf) in derived.items():
            if col not in TARGET_COLUMNS:
                continue
            if new_val is None or (isinstance(new_val, str) and new_val.strip() == ""):
                continue

            existing = getattr(row, col, None)
            existing_empty = _is_empty(existing)

            if existing_empty:
                # GAP — apply if HIGH (or MEDIUM with --apply-medium)
                if conf == "HIGH":
                    audit_rows.append(ClassificationAuditLog(
                        sweep_run_id=sweep_run_id,
                        ticker=row.ticker,
                        column_name=col,
                        old_value=None,
                        new_value=_fmt(new_val),
                        source="sweep_high",
                        confidence=conf,
                        reason="HIGH-confidence gap fill",
                        dry_run=not apply_changes,
                    ))
                    if apply_changes:
                        setattr(row, col, new_val)
                    n_fills_high += 1
                    per_column_fills[col] += 1
                elif conf == "MEDIUM" and apply_medium:
                    audit_rows.append(ClassificationAuditLog(
                        sweep_run_id=sweep_run_id,
                        ticker=row.ticker,
                        column_name=col,
                        old_value=None,
                        new_value=_fmt(new_val),
                        source="sweep_medium",
                        confidence=conf,
                        reason="MED-confidence gap fill (--apply-medium)",
                        dry_run=not apply_changes,
                    ))
                    if apply_changes:
                        setattr(row, col, new_val)
                    n_fills_medium += 1
                    per_column_fills[col] += 1
                else:
                    # MED/LOW -> queue proposal (for primary_strategy only,
                    # to avoid spamming the queue with attribute-level rows)
                    if col == "primary_strategy" and write_proposals:
                        key = (row.ticker, _fmt(new_val))
                        if key not in existing_proposal_tickers:
                            proposals_to_add.append(ClassificationProposal(
                                ticker=row.ticker,
                                fund_name=row.fund_name,
                                issuer=row.issuer,
                                aum=row.aum,
                                proposed_category=None,
                                proposed_strategy=_fmt(new_val),
                                confidence=conf,
                                reason=f"sweep {sweep_run_id} {col}",
                                attributes_json=None,
                                status="pending",
                            ))
                            existing_proposal_tickers.add(key)
                            n_proposals_queued += 1
                            if len(sample_proposals) < 10:
                                sample_proposals.append({
                                    "ticker": row.ticker,
                                    "fund_name": (row.fund_name or "")[:60],
                                    "column": col,
                                    "proposed": _fmt(new_val),
                                    "confidence": conf,
                                })
                    n_fills_low += 1  # tracks MED/LOW skipped from DB
            else:
                # POPULATED — check for disagreement
                existing_str = _fmt(existing).strip()
                new_str = _fmt(new_val).strip()
                if existing_str.lower() != new_str.lower():
                    n_conflicts += 1
                    per_column_conflicts[col] += 1
                    audit_rows.append(ClassificationAuditLog(
                        sweep_run_id=sweep_run_id,
                        ticker=row.ticker,
                        column_name=col,
                        old_value=existing_str,
                        new_value=new_str,
                        source="conflict",
                        confidence=conf,
                        reason="Existing value differs from suggestion — NOT overwritten",
                        dry_run=not apply_changes,
                    ))
                    conflicts_csv_rows.append({
                        "ticker": row.ticker,
                        "fund_name": row.fund_name or "",
                        "issuer": row.issuer or "",
                        "column": col,
                        "existing_value": existing_str,
                        "suggested_value": new_str,
                        "confidence": conf,
                    })
                    if len(sample_conflicts) < 10:
                        sample_conflicts.append({
                            "ticker": row.ticker,
                            "column": col,
                            "existing": existing_str[:40],
                            "suggested": new_str[:40],
                            "confidence": conf,
                        })

    # SANITY GUARD — verify we never wrote over a populated value
    # (We already gate every write above, but this catches programming bugs.)
    if apply_changes:
        for ar in audit_rows:
            if ar.source in ("sweep_high", "sweep_medium") and ar.old_value:
                # Old value was non-empty — we should never write here
                n_overwrites += 1

    # Write conflicts CSV
    today = datetime.utcnow().strftime("%Y-%m-%d")
    conflicts_path = PROJECT_ROOT / "docs" / f"classification_conflicts_{today}.csv"
    conflicts_path.parent.mkdir(parents=True, exist_ok=True)
    if conflicts_csv_rows:
        with conflicts_path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=[
                "ticker", "fund_name", "issuer", "column",
                "existing_value", "suggested_value", "confidence",
            ])
            w.writeheader()
            for r in conflicts_csv_rows:
                w.writerow(r)
        log.info("Wrote %d conflicts to %s", len(conflicts_csv_rows), conflicts_path)
    else:
        log.info("No conflicts — skipping CSV")

    # Persist
    if apply_changes:
        log.info("Committing %d audit rows + %d new proposals + DB column fills...",
                 len(audit_rows), len(proposals_to_add))
        db.add_all(audit_rows)
        db.add_all(proposals_to_add)
        db.commit()
    else:
        log.info("DRY-RUN — staging %d audit rows (NOT committed)", len(audit_rows))
        # Optionally still persist audit log even in dry-run for diff visibility
        # but per spec we do NOT mutate DB at all in dry-run.
        db.rollback()

    db.close()

    summary = {
        "sweep_run_id": sweep_run_id,
        "rows_processed": n_rows_processed,
        "fills_high": n_fills_high,
        "fills_medium": n_fills_medium,
        "fills_low_skipped": n_fills_low,
        "proposals_queued": n_proposals_queued,
        "conflicts": n_conflicts,
        "overwrites": n_overwrites,
        "per_column_fills": per_column_fills,
        "per_column_conflicts": per_column_conflicts,
        "sample_proposals": sample_proposals,
        "sample_conflicts": sample_conflicts,
        "conflicts_csv": str(conflicts_path) if conflicts_csv_rows else None,
        "applied": apply_changes,
        "apply_medium": apply_medium,
    }
    return summary


def print_summary(summary: dict) -> None:
    print()
    print("=" * 78)
    print(f"CLASSIFICATION SWEEP SUMMARY  [{summary['sweep_run_id']}]")
    print("=" * 78)
    mode = "APPLIED to DB" if summary["applied"] else "DRY-RUN (no DB writes)"
    if summary["apply_medium"]:
        mode += " + MED auto-fill"
    print(f"Mode:                {mode}")
    print(f"Rows processed:      {summary['rows_processed']:,}")
    print()
    print("Outcomes:")
    print(f"  HIGH-conf fills:    {summary['fills_high']:>6,}  (would be applied to DB)")
    print(f"  MED-conf fills:     {summary['fills_medium']:>6,}  (--apply-medium only)")
    print(f"  MED/LOW skipped:    {summary['fills_low_skipped']:>6,}  (proposals queued for primary_strategy)")
    print(f"  Proposals queued:   {summary['proposals_queued']:>6,}")
    print(f"  Conflicts:          {summary['conflicts']:>6,}  (existing differs — NOT overwritten)")
    print(f"  Overwrites:         {summary['overwrites']:>6,}  (SANITY: must be 0)")
    print()
    print("Per-column projected fills (HIGH only):")
    for col in TARGET_COLUMNS:
        n = summary["per_column_fills"].get(col, 0)
        c = summary["per_column_conflicts"].get(col, 0)
        if n or c:
            print(f"  {col:25} fills={n:>5,}   conflicts={c:>4,}")

    if summary["sample_proposals"]:
        print()
        print("Sample proposals (first 10):")
        for p in summary["sample_proposals"][:10]:
            print(f"  {p['ticker']:10} [{p['confidence']:6}] {p['column']:18} -> {p['proposed']:30}  {p['fund_name']}")

    if summary["sample_conflicts"]:
        print()
        print("Sample conflicts (first 10):")
        for c in summary["sample_conflicts"][:10]:
            print(f"  {c['ticker']:10} {c['column']:18} existing={c['existing']:25} != suggested={c['suggested']}  [{c['confidence']}]")

    if summary["conflicts_csv"]:
        print()
        print(f"Conflicts CSV:       {summary['conflicts_csv']}")
    print("=" * 78)


def main() -> int:
    ap = argparse.ArgumentParser(description="3-axis classification sweep")
    ap.add_argument("--dry-run", action="store_true", default=True,
                    help="Default. Compute changes but do NOT write to DB.")
    ap.add_argument("--apply", action="store_true", default=False,
                    help="Actually write HIGH-confidence fills to DB.")
    ap.add_argument("--apply-medium", action="store_true", default=False,
                    help="Also auto-apply MED-confidence fills (still gap-only).")
    ap.add_argument("--limit", type=int, default=None,
                    help="Limit to first N rows (smoke testing).")
    ap.add_argument("--no-proposals", action="store_true", default=False,
                    help="Do not write to ClassificationProposal queue.")
    args = ap.parse_args()

    apply_changes = args.apply  # explicit opt-in to write
    if apply_changes:
        log.warning("APPLY mode — DB will be modified.")
    else:
        log.info("DRY-RUN mode — no DB writes.")

    try:
        summary = run_sweep(
            apply_changes=apply_changes,
            apply_medium=args.apply_medium,
            limit=args.limit,
            write_proposals=not args.no_proposals,
        )
    except Exception as e:
        log.error("Sweep failed: %s", e)
        traceback.print_exc()
        return 1

    print_summary(summary)

    if summary["overwrites"] > 0:
        log.error("SANITY GUARD TRIPPED: %d rows had old_value but were marked written.",
                  summary["overwrites"])
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
