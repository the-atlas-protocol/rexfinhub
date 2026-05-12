"""Classification engine for ATLAS /classify skill.

Scans Bloomberg data for unmapped funds in the 5 tracked categories,
determines correct etp_category + attributes, and writes to rules CSVs.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

from market.auto_classify import classify_fund, Classification
from market.config import RULES_DIR as _CONFIG_RULES_DIR

# Write to config/rules/ — the single source of truth consumed by the live site,
# market/config.py, webapp/routers/admin.py, and the mkt_* DB tables.
# (Previously wrote to data/rules/ which was a split-brain copy invisible to
# every other consumer; see docs/audit_2026-05-11/fix_R6.md.)
RULES_DIR = _CONFIG_RULES_DIR

log = logging.getLogger(__name__)

TRACKED_CATEGORIES = {"LI", "CC", "Crypto", "Defined", "Thematic"}

# auto_classify strategy -> etp_category
STRATEGY_MAP = {
    "Leveraged & Inverse": "LI",
    "Income / Covered Call": "CC",
    "Crypto": "Crypto",
    "Defined Outcome": "Defined",
    "Thematic": "Thematic",
}


# ---------------------------------------------------------------------------
# Resilient file helpers — create empty CSVs if missing so code never
# KeyErrors on a missing persistent disk mount.
# ---------------------------------------------------------------------------

def _ensure_rules_dir():
    """Create the rules dir if it doesn't exist."""
    RULES_DIR.mkdir(parents=True, exist_ok=True)


def _load_fund_mapping() -> pd.DataFrame:
    """Load fund_mapping.csv, returning empty DataFrame if missing."""
    _ensure_rules_dir()
    path = RULES_DIR / "fund_mapping.csv"
    if not path.exists():
        df = pd.DataFrame(columns=["ticker", "etp_category", "is_primary", "source"])
        df.to_csv(path, index=False)
        return df
    return pd.read_csv(path, engine="python", on_bad_lines="skip")


def _load_attributes(etp_cat: str) -> pd.DataFrame:
    """Load attributes CSV for a category, returning empty DataFrame if missing."""
    _ensure_rules_dir()
    name = {"LI": "attributes_LI.csv", "CC": "attributes_CC.csv",
            "Crypto": "attributes_Crypto.csv", "Defined": "attributes_Defined.csv",
            "Thematic": "attributes_Thematic.csv"}.get(etp_cat)
    if not name:
        return pd.DataFrame()
    path = RULES_DIR / name
    if not path.exists():
        df = pd.DataFrame(columns=["ticker"])
        df.to_csv(path, index=False)
        return df
    return pd.read_csv(path, engine="python", on_bad_lines="skip")


def _load_issuer_mapping() -> pd.DataFrame:
    """Load issuer_mapping.csv, returning empty DataFrame if missing."""
    _ensure_rules_dir()
    path = RULES_DIR / "issuer_mapping.csv"
    if not path.exists():
        df = pd.DataFrame(columns=["etp_category", "issuer", "issuer_nickname"])
        df.to_csv(path, index=False)
        return df
    return pd.read_csv(path, engine="python", on_bad_lines="skip")


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------

def scan_unmapped(since_days: int = 14) -> dict:
    """Scan Bloomberg for unmapped funds and classify them.

    Non-ACTV funds (PENDING/LIQUIDATED/DELISTED) are INCLUDED — they still have
    historical AUM and should be classified for backfill accuracy. KPIs filter
    to ACTV downstream; classification rules are status-agnostic.

    Returns dict with keys:
        candidates: list of dicts (funds that belong in 5 tracked categories)
        outside: list of dicts (funds outside tracked categories — LOW confidence)
        stale: list of dicts (non-ACTV funds still in fund_mapping — info only)
        summary: dict with counts
    """
    from webapp.services.data_engine import build_master_data

    master = build_master_data()
    fm = _load_fund_mapping()
    mapped_tickers = set(fm["ticker"].astype(str).str.strip())

    # Include all fund types (ETF/ETN) regardless of ACTV status.
    # Non-ACTV funds (liquidated, pending, delisted) still need classification
    # for historical AUM backfill. KPIs filter status downstream.
    scope = master.copy()
    if "fund_type" in scope.columns:
        scope = scope[scope["fund_type"].isin(["ETF", "ETN"])]

    # Find unmapped tickers
    scope = scope.drop_duplicates(subset=["ticker"], keep="first")
    unmapped = scope[~scope["ticker"].isin(mapped_tickers)].copy()
    # Keep reference to ACTV-filtered master for stale detection below
    active = master[master.get("market_status", pd.Series(dtype=str)) == "ACTV"].copy() if "market_status" in master.columns else master.copy()

    # Optionally filter to recent launches
    if since_days and "inception_date" in unmapped.columns:
        inception = pd.to_datetime(unmapped["inception_date"], errors="coerce")
        cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=since_days)
        recent = unmapped[inception >= cutoff].copy()
    else:
        recent = unmapped

    candidates = []
    outside = []

    for _, row in recent.iterrows():
        c = classify_fund(row)
        etp_cat = STRATEGY_MAP.get(c.strategy)

        info = {
            "ticker": str(row.get("ticker", "")).strip(),
            "fund_name": str(row.get("fund_name", "")),
            "issuer": str(row.get("issuer", "")),
            "asset_class": str(row.get("asset_class_focus", "")),
            "inception_date": str(row.get("inception_date", ""))[:10],
            "aum": float(pd.to_numeric(row.get("t_w4.aum", 0), errors="coerce") or 0),
            "strategy": c.strategy,
            "confidence": c.confidence,
            "reason": c.reason,
            "auto_attrs": c.attributes,
        }

        if etp_cat and c.confidence in ("HIGH", "MEDIUM"):
            info["etp_category"] = etp_cat
            info["attributes"] = _resolve_attributes(etp_cat, row, c)
            candidates.append(info)
        else:
            info["etp_category"] = None
            outside.append(info)

    # Stale funds (non-ACTV still in fund_mapping)
    stale = []
    if "market_status" in master.columns:
        for _, row in master[master["ticker"].isin(mapped_tickers)].iterrows():
            status = str(row.get("market_status", ""))
            if status not in ("ACTV", "Active", ""):
                stale.append({
                    "ticker": str(row.get("ticker", "")),
                    "fund_name": str(row.get("fund_name", "")),
                    "market_status": status,
                    "etp_category": str(
                        fm[fm["ticker"] == row["ticker"]]["etp_category"].values[0]
                    ) if not fm[fm["ticker"] == row["ticker"]].empty else "",
                })
        # Deduplicate
        seen = set()
        unique_stale = []
        for s in stale:
            if s["ticker"] not in seen:
                seen.add(s["ticker"])
                unique_stale.append(s)
        stale = unique_stale

    # Sort candidates by AUM descending
    candidates.sort(key=lambda x: x.get("aum", 0), reverse=True)

    return {
        "candidates": candidates,
        "outside": outside,
        "stale": stale,
        "summary": {
            "total_unmapped": len(unmapped),
            "recent_unmapped": len(recent),
            "candidates": len(candidates),
            "outside": len(outside),
            "stale": len(stale),
        },
    }


# ---------------------------------------------------------------------------
# Attribute resolution
# ---------------------------------------------------------------------------

def _resolve_attributes(etp_cat: str, row: pd.Series, c: Classification) -> dict:
    """Determine the correct attribute values for a fund."""
    name = str(row.get("fund_name", "")).upper()
    lev_pct = row.get("leverage_amount", "")
    single_stock = row.get("is_singlestock", "")
    underlying_idx = str(row.get("underlying_index", "")).upper()

    if etp_cat == "LI":
        return _resolve_li_attrs(name, lev_pct, single_stock, c)
    elif etp_cat == "CC":
        return _resolve_cc_attrs(name, row, c)
    elif etp_cat == "Crypto":
        return _resolve_crypto_attrs(name, row, c)
    elif etp_cat == "Defined":
        return _resolve_defined_attrs(name, row, c)
    elif etp_cat == "Thematic":
        return _resolve_thematic_attrs(name, row, c)
    return {}


def _resolve_li_attrs(name: str, lev_pct, single_stock, c: Classification) -> dict:
    """Resolve LI attributes: category, subcategory, direction, leverage, underlier."""
    attrs = {}

    # Category (asset type)
    if re.search(r"\bBITCOIN|BTC|ETHER|CRYPTO\b", name):
        attrs["map_li_category"] = "Crypto"
    elif re.search(r"\bGOLD|SILVER|OIL|CRUDE|COMMODITY\b", name):
        attrs["map_li_category"] = "Commodity"
    elif re.search(r"\bDOLLAR|EURO|YEN|CURRENCY|FX\b", name):
        attrs["map_li_category"] = "Currency"
    elif re.search(r"\bBOND|TREASURY|RATE|FIXED\b", name):
        attrs["map_li_category"] = "Fixed Income"
    elif re.search(r"\bVIX|VOLATIL\b", name):
        attrs["map_li_category"] = "Volatility"
    else:
        attrs["map_li_category"] = "Equity"

    # Subcategory
    ss_val = str(single_stock).strip() if pd.notna(single_stock) else ""
    if ss_val and ss_val.lower() not in ("", "nan", "none"):
        attrs["map_li_subcategory"] = "Single Stock"
    else:
        attrs["map_li_subcategory"] = "Index/Basket/ETF Based"

    # Direction
    if re.search(r"\b(BEAR|SHORT|INVERSE|ULTRA\s*SHORT)\b", name):
        attrs["map_li_direction"] = "Short"
    elif re.search(r"\b(BULL|LONG|ULTRA(?!SHORT))\b", name):
        attrs["map_li_direction"] = "Long"
    else:
        # Check leverage sign
        try:
            lev_val = float(str(lev_pct).replace("%", "").replace("x", ""))
            attrs["map_li_direction"] = "Short" if lev_val < 0 else "Long"
        except (ValueError, TypeError):
            attrs["map_li_direction"] = "Long"

    # Leverage amount
    m = re.search(r"(-?\d+(?:\.\d+)?)\s*[Xx]", name)
    if m:
        attrs["map_li_leverage_amount"] = abs(float(m.group(1)))
    else:
        try:
            val = abs(float(str(lev_pct).replace("%", "").replace("x", "")))
            if val > 0:
                attrs["map_li_leverage_amount"] = val / 100 if val > 10 else val
        except (ValueError, TypeError):
            attrs["map_li_leverage_amount"] = ""

    # Underlier
    if ss_val and ss_val.lower() not in ("", "nan", "none"):
        underlier = re.sub(r"\s+(US|Curncy|Comdty|Index|Equity)$", "", ss_val)
        attrs["map_li_underlier"] = underlier
    else:
        attrs["map_li_underlier"] = ""

    return attrs


def _resolve_cc_attrs(name: str, row: pd.Series, c: Classification) -> dict:
    """Resolve CC attributes: underlier, index, type, category."""
    attrs = {}
    ss_val = str(row.get("is_singlestock", "")).strip()
    uses_deriv = str(row.get("uses_derivatives", ""))
    uses_swaps = str(row.get("uses_swaps", ""))

    # cc_type: Synthetic (swaps/derivatives) vs Traditional (direct option writing)
    if uses_swaps in ("1", "1.0", "True"):
        attrs["cc_type"] = "Synthetic"
    elif uses_deriv in ("1", "1.0", "True"):
        attrs["cc_type"] = "Traditional"
    else:
        # Most modern income ETFs use synthetic
        attrs["cc_type"] = "Synthetic"

    # cc_category (what the fund covers)
    if ss_val and ss_val.lower() not in ("", "nan", "none"):
        attrs["cc_category"] = "Single Stock"
    elif re.search(r"\bNASDAQ|QQQ\b", name):
        attrs["cc_category"] = "Tech"
    elif re.search(r"\bS&P|SPY|SPX\b", name):
        attrs["cc_category"] = "Broad Beta"
    elif re.search(r"\bRUSSELL|SMALL\s*CAP\b", name):
        attrs["cc_category"] = "Small Caps"
    elif re.search(r"\bINTERNATIONAL|GLOBAL|EAFE|EMERGING\b", name):
        attrs["cc_category"] = "International"
    elif re.search(r"\bCRYPTO|BITCOIN|ETHER\b", name):
        attrs["cc_category"] = "Crypto"
    elif re.search(r"\bENERGY|OIL\b", name):
        attrs["cc_category"] = "Energy"
    elif re.search(r"\bREAL\s*ESTATE|REIT\b", name):
        attrs["cc_category"] = "Real Estate"
    elif re.search(r"\bBOND|FIXED|TREASURY\b", name):
        attrs["cc_category"] = "Fixed Income"
    elif re.search(r"\bCOMMODIT|GOLD|SILVER\b", name):
        attrs["cc_category"] = "Commodity"
    elif re.search(r"\bSECTOR|TECH|HEALTH|FINANC\b", name):
        attrs["cc_category"] = "Sector"
    else:
        attrs["cc_category"] = "Broad Beta"

    # Underlier
    if ss_val and ss_val.lower() not in ("", "nan", "none"):
        underlier = re.sub(r"\s+(US|Curncy|Comdty|Index|Equity)$", "", ss_val)
        attrs["map_cc_underlier"] = underlier
    else:
        attrs["map_cc_underlier"] = ""

    # Index
    idx = str(row.get("underlying_index", "")).strip()
    attrs["map_cc_index"] = idx if idx and idx.lower() not in ("nan", "none", "") else ""

    return attrs


def _resolve_crypto_attrs(name: str, row: pd.Series, c: Classification) -> dict:
    """Resolve Crypto attributes: type, underlier."""
    attrs = {}

    # Type
    if re.search(r"\b(FUTURES|DERIVATIVES)\b", name):
        if re.search(r"\bINCOME|YIELD|COVERED\b", name):
            attrs["map_crypto_type"] = "Derivatives-based; income"
        elif re.search(r"\bBUFFER|OUTCOME\b", name):
            attrs["map_crypto_type"] = "Derivatives-based; defined outcome"
        else:
            attrs["map_crypto_type"] = "Derivatives-based; futures-based"
    elif re.search(r"\bLEVERAGE|2X|3X|-2X|-3X\b", name):
        attrs["map_crypto_type"] = "Derivatives-based; leveraged"
    else:
        attrs["map_crypto_type"] = "Spot"

    # Underlier
    coin_patterns = [
        ("Bitcoin", r"\b(BITCOIN|BTC)\b"),
        ("Ethereum", r"\b(ETHEREUM|ETH(?:ER)?)\b"),
        ("Solana", r"\b(SOLANA|SOL)\b"),
        ("XRP", r"\b(XRP|RIPPLE)\b"),
        ("Litecoin", r"\bLITECOIN\b"),
        ("Dogecoin", r"\bDOGECOIN\b"),
        ("Multi-Crypto", r"\b(CRYPTO|DIGITAL\s*ASSET|BASKET)\b"),
    ]
    for coin, pattern in coin_patterns:
        if re.search(pattern, name):
            attrs["map_crypto_underlier"] = coin
            break
    else:
        attrs["map_crypto_underlier"] = ""

    return attrs


def _resolve_defined_attrs(name: str, row: pd.Series, c: Classification) -> dict:
    """Resolve Defined Outcome attributes: category."""
    outcome = str(row.get("outcome_type", "")).strip()

    if outcome and outcome.lower() not in ("", "nan", "none"):
        # Map Bloomberg outcome_type to our categories
        o = outcome.lower()
        if "buffer" in o and "dual" in o:
            cat = "Dual Buffer"
        elif "buffer" in o:
            cat = "Buffer"
        elif "accelerat" in o:
            cat = "Accelerator"
        elif "barrier" in o:
            cat = "Barrier"
        elif "floor" in o:
            cat = "Floor"
        elif "hedged" in o:
            cat = "Hedged Equity"
        elif "ladder" in o:
            cat = "Ladder"
        else:
            cat = "Outcome"
    else:
        # Infer from fund name
        n = name.upper()
        if "DUAL BUFFER" in n:
            cat = "Dual Buffer"
        elif "BUFFER" in n:
            cat = "Buffer"
        elif "ACCELERAT" in n:
            cat = "Accelerator"
        elif "BARRIER" in n:
            cat = "Barrier"
        elif "FLOOR" in n:
            cat = "Floor"
        elif "HEDGED EQUITY" in n or "HEDGE EQUITY" in n:
            cat = "Hedged Equity"
        elif "LADDER" in n:
            cat = "Ladder"
        elif "DEFINED VOLATIL" in n:
            cat = "Defined Volatility"
        elif "DEFINED RISK" in n:
            cat = "Defined Risk"
        else:
            cat = "Buffer"  # Most common

    return {"map_defined_category": cat}


def _resolve_thematic_attrs(name: str, row: pd.Series, c: Classification) -> dict:
    """Resolve Thematic attributes: category."""
    theme_patterns = [
        ("Artificial Intelligence", r"\b(AI\b|ARTIFICIAL\s*INTELLIGENCE|MACHINE\s*LEARN)\b"),
        ("Robotics & Automation", r"\b(ROBOT|AUTOMAT|AUTONOMOUS)\b"),
        ("Clean Energy", r"\b(CLEAN\s*ENERGY|SOLAR|WIND|RENEWABLE|GREEN\s*ENERGY)\b"),
        ("Cybersecurity", r"\b(CYBER|CYBERSECURITY)\b"),
        ("Genomics & Biotech", r"\b(GENOMIC|BIOTECH|GENE|CRISPR)\b"),
        ("Cloud Computing", r"\b(CLOUD|SAAS)\b"),
        ("Space & Aerospace", r"\b(SPACE|AEROSPACE)\b"),
        ("Defense", r"\b(DEFENSE|DEFENCE|MILITARY)\b"),
        ("Cannabis and Psychedelics", r"\b(CANNABIS|MARIJUANA|PSYCHEDELIC)\b"),
        ("Metaverse & Gaming", r"\b(METAVERSE|GAMING|ESPORTS|VIDEO\s*GAME)\b"),
        ("Fintech", r"\b(FINTECH|FINANCIAL\s*TECH)\b"),
        ("Infrastructure", r"\b(INFRASTRUCTURE|5G)\b"),
        ("Water", r"\b(WATER|CLEAN\s*WATER)\b"),
        ("Lithium & Battery", r"\b(LITHIUM|BATTERY|EV\s*TECH)\b"),
        ("ESG", r"\b(ESG|SUSTAINABLE|SUSTAINABILITY|RESPONSIBLE)\b"),
        ("Blockchain & Crypto", r"\b(BLOCKCHAIN)\b"),
        ("Consumer", r"\b(CONSUMER\s*TREND|MILLENNI|GEN\s*Z)\b"),
        ("Nuclear", r"\b(NUCLEAR|URANIUM)\b"),
        ("Quantum Computing", r"\b(QUANTUM)\b"),
        ("Agriculture", r"\b(AGRICULTURE|AGRI\s*BUSINESS|AGRI\s*TECH|FARM)\b"),
        ("Natural Resources", r"\b(NATURAL\s*RESOURCE)\b"),
        ("Sports", r"\b(SPORTS|ESPORTS|SPORT\s*BETTING)\b"),
        ("Housing", r"\b(HOUSING|HOME\s*BUILD|HOMEBUILDER[S]?)\b"),
    ]
    n = name.upper()
    for theme, pattern in theme_patterns:
        if re.search(pattern, n):
            return {"map_thematic_category": theme}

    # Check auto_classify attributes
    if c.attributes.get("theme"):
        return {"map_thematic_category": c.attributes["theme"]}

    return {"map_thematic_category": "General Thematic"}


# ---------------------------------------------------------------------------
# Write to CSVs
# ---------------------------------------------------------------------------

def apply_classifications(candidates: list[dict]) -> dict:
    """Write approved candidates to fund_mapping.csv and attributes CSVs.

    Resilient to missing files (creates empty CSVs if the rules dir is empty).
    Always deduplicates fund_mapping on ticker to clean up legacy duplicates.

    Returns dict with counts of what was written.
    """
    if not candidates:
        return {"fund_mapping": 0, "attributes": {}}

    fm = _load_fund_mapping()
    fm_path = RULES_DIR / "fund_mapping.csv"

    attr_counts = {}
    fm_new_rows = []

    for c in candidates:
        ticker = c["ticker"]
        etp_cat = c["etp_category"]
        attrs = c.get("attributes", {})

        # Skip if already mapped to the same category
        existing_tickers = set(fm["ticker"].astype(str).str.strip())
        if ticker in existing_tickers:
            # Already mapped — only add if category differs
            existing_cats = set(fm[fm["ticker"].astype(str).str.strip() == ticker]["etp_category"].astype(str))
            if etp_cat in existing_cats:
                continue

        # Add to fund_mapping
        fm_new_rows.append({
            "ticker": ticker,
            "etp_category": etp_cat,
            "is_primary": 1,
            "source": "atlas",
        })

        # Add to attributes CSV
        if attrs and etp_cat in ("LI", "CC", "Crypto", "Defined", "Thematic"):
            attr_df = _load_attributes(etp_cat)
            attr_file_map = {"LI": "attributes_LI.csv", "CC": "attributes_CC.csv",
                             "Crypto": "attributes_Crypto.csv", "Defined": "attributes_Defined.csv",
                             "Thematic": "attributes_Thematic.csv"}
            attr_path = RULES_DIR / attr_file_map[etp_cat]

            # Skip if already in attributes
            if not attr_df.empty and "ticker" in attr_df.columns:
                if ticker in set(attr_df["ticker"].astype(str).str.strip()):
                    continue

            new_row = {"ticker": ticker}
            new_row.update(attrs)
            attr_df = pd.concat([attr_df, pd.DataFrame([new_row])], ignore_index=True)
            attr_df.to_csv(attr_path, index=False)
            attr_counts[etp_cat] = attr_counts.get(etp_cat, 0) + 1

    # Write fund_mapping — always dedupe (fixes legacy duplicates like ODTE x2)
    if fm_new_rows:
        fm = pd.concat([fm, pd.DataFrame(fm_new_rows)], ignore_index=True)
    # Dedupe on (ticker, etp_category) — keeps one row per category assignment
    fm = fm.drop_duplicates(subset=["ticker", "etp_category"], keep="first")
    fm.to_csv(fm_path, index=False)

    # Also check issuer_mapping for new issuers
    _update_issuer_mapping(candidates)

    return {
        "fund_mapping": len(fm_new_rows),
        "attributes": attr_counts,
    }


def _update_issuer_mapping(candidates: list[dict]) -> int:
    """Add any new (etp_category, issuer) pairs to issuer_mapping.csv."""
    im = _load_issuer_mapping()
    im_path = RULES_DIR / "issuer_mapping.csv"

    known = set()
    if not im.empty:
        known = set(zip(im["etp_category"].astype(str), im["issuer"].astype(str)))

    new_rows = []
    for c in candidates:
        etp_cat = c["etp_category"]
        issuer = c.get("issuer", "")
        if not etp_cat or not issuer:
            continue
        key = (etp_cat, issuer)
        if key not in known:
            # Auto-generate a display name (strip Bloomberg suffixes)
            nickname = re.sub(r"\s*/\s*(USA|Fund Parent|Cayman|Delaware).*$", "", issuer)
            nickname = nickname.strip()
            new_rows.append({
                "etp_category": etp_cat,
                "issuer": issuer,
                "issuer_nickname": nickname,
            })
            known.add(key)

    if new_rows:
        im = pd.concat([im, pd.DataFrame(new_rows)], ignore_index=True)
        im.to_csv(im_path, index=False)

    return len(new_rows)


def remove_stale(tickers: list[str]) -> int:
    """Remove non-ACTV tickers from fund_mapping.csv."""
    fm_path = RULES_DIR / "fund_mapping.csv"
    fm = pd.read_csv(fm_path, engine="python", on_bad_lines="skip")
    before = len(fm)
    fm = fm[~fm["ticker"].isin(tickers)]
    fm.to_csv(fm_path, index=False)
    return before - len(fm)
