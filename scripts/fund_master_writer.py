"""Single write path into the FULL-SCALE taxonomy master (fund_master.csv).

fund_master.csv (config/rules/, 7,231 rows x 28 cols) is the Layer-1 curated
master of the 3-axis taxonomy (asset_class x primary_strategy x sub_strategy
+ attribute columns). scripts/apply_fund_master.py replays it onto the 23
mkt_master_data taxonomy columns twice daily (Bloomberg-chain post-step 1).

Until 2026-06-10 NOTHING fed new rows into the CSV — it was frozen at the
2026-05-01 export, so every new launch had NULL taxonomy forever (the gap
Ryu flagged). This module is the missing feeder, used by classify_daily:

  - candidate_to_master_row(): translate a legacy-engine candidate
    (etp_category + map_* attributes) into a full 28-column row, reusing the
    codified mapping in scripts/build_fund_master_seed.py.
  - other_to_master_row(): for funds OUTSIDE the 5 tracked categories, run
    the universal rule cascade (scripts/universal_classify_funds.classify)
    so they get a real Plain-Beta/etc. row instead of disappearing into
    exclusions.csv — fund_master is full-universe by design.
  - append_rows(): dedupe-on-ticker append with provenance tags.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.rules_editor.classify_engine import RULES_DIR  # noqa: E402
from scripts.build_fund_master_seed import (  # noqa: E402
    COLUMNS, derive_asset_class, derive_primary_and_sub, derive_attributes,
)
from scripts.universal_classify_funds import classify as universal_classify  # noqa: E402

FUND_MASTER = "fund_master.csv"


def _source_tag() -> str:
    return f"atlas-auto-{date.today().isoformat()}"


def candidate_to_master_row(c: dict) -> dict:
    """Legacy-engine candidate -> full 28-column fund_master row.

    Candidate shape (classify_daily tiers 1-2): ticker, fund_name, issuer,
    etp_category, attributes={map_li_*|map_cc_*|map_crypto_*|map_defined_*...},
    plus optional asset_class (Bloomberg asset_class_focus) from the scan.
    """
    attrs_in = c.get("attributes", {}) or {}
    pseudo = {
        "etp_category": c.get("etp_category", ""),
        "asset_class_focus": c.get("asset_class", ""),
        "is_crypto": "true" if c.get("etp_category") == "Crypto" else "",
        "map_li_direction": attrs_in.get("map_li_direction", ""),
        "leverage_amount": str(attrs_in.get("map_li_leverage_amount", "") or ""),
        "map_li_underlier": attrs_in.get("map_li_underlier", ""),
        "map_cc_underlier": attrs_in.get("map_cc_underlier", ""),
        "map_crypto_underlier": attrs_in.get("map_crypto_underlier", ""),
        "cc_category": attrs_in.get("cc_category", ""),
        "outcome_type": attrs_in.get("map_defined_category", ""),
        "uses_swaps": "true" if c.get("etp_category") == "LI" else "",
        "uses_derivatives": "true" if c.get("etp_category") in ("CC", "Defined") else "",
        "regulatory_structure": "",
    }
    primary, sub = derive_primary_and_sub(pseudo)
    row = {col: "" for col in COLUMNS}
    row.update({
        "ticker": c["ticker"],
        "fund_name": c.get("fund_name", ""),
        "asset_class": derive_asset_class(pseudo),
        "primary_strategy": primary,
        "sub_strategy": sub,
        "source": _source_tag(),
        "notes": "auto: legacy candidate translated (classify_daily)",
    })
    row.update({k: v for k, v in derive_attributes(pseudo, primary, sub).items()
                if k in COLUMNS})
    return row


def other_to_master_row(fund: dict, rationale: str = "") -> dict:
    """Outside-the-5-categories fund -> full-universe fund_master row.

    Runs the universal rule cascade; falls back to Plain Beta/Broad with the
    Bloomberg asset class when no rule fires (the design default — every fund
    deserves a row; 'Other' is not a taxonomy hole).
    """
    res = universal_classify(
        ticker=fund.get("ticker", ""),
        fund_name=fund.get("fund_name", ""),
        asset_class_focus=fund.get("asset_class", ""),
        is_crypto="",
        uses_leverage="",
        etp_category="",
    ) or {}
    row = {col: "" for col in COLUMNS}
    row.update({
        "ticker": fund.get("ticker", ""),
        "fund_name": fund.get("fund_name", ""),
        "asset_class": res.get("asset_class")
                       or derive_asset_class({"asset_class_focus": fund.get("asset_class", "")}),
        "primary_strategy": res.get("primary_strategy", "Plain Beta"),
        "sub_strategy": res.get("sub_strategy", "Broad"),
        "mechanism": res.get("mechanism", "physical"),
        "wrapper_type": res.get("wrapper_type", "standalone"),
        "source": _source_tag(),
        "notes": (f"auto: outside tracked categories ({rationale[:120]})"
                  if rationale else "auto: outside tracked categories"),
    })
    for k in ("leverage_ratio", "direction", "reset_period", "concentration",
              "underlier_name", "tax_structure"):
        if res.get(k):
            row[k] = res[k]
    return row


def append_rows(rows: list[dict], dry_run: bool = False) -> int:
    """Dedupe-on-ticker append to RULES_DIR/fund_master.csv. Returns added count."""
    if not rows:
        return 0
    path = RULES_DIR / FUND_MASTER
    df = pd.read_csv(path, dtype=str).fillna("") if path.exists() else pd.DataFrame(columns=COLUMNS)
    have = set(df["ticker"].astype(str).str.strip())
    fresh = [r for r in rows if r.get("ticker") and r["ticker"] not in have]
    if not fresh or dry_run:
        return len(fresh)
    add = pd.DataFrame(fresh)[[c for c in COLUMNS]]
    pd.concat([df, add], ignore_index=True).to_csv(path, index=False)
    return len(fresh)


def missing_from_master(tickers: list[str]) -> set[str]:
    """Which of these tickers have no fund_master row (full-layer gap check)."""
    path = RULES_DIR / FUND_MASTER
    if not path.exists():
        return set(tickers)
    have = set(pd.read_csv(path, usecols=["ticker"], dtype=str)["ticker"].str.strip())
    return {t for t in tickers if t and t not in have}
