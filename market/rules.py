"""CSV rule loader and validator for the market data pipeline."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from market.config import (
    RULES_DIR, RULE_FILES, CATEGORY_ATTR_MAP, ALL_ATTR_COLS,
)

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_fund_mapping(rules_dir: Path | None = None) -> pd.DataFrame:
    """Load fund_mapping.csv -> DataFrame[ticker, etp_category]."""
    path = _resolve("fund_mapping", rules_dir)
    df = _read(path)
    df = df[["ticker", "etp_category"]].dropna(subset=["ticker"])
    df = df.drop_duplicates(subset=["ticker", "etp_category"])
    log.info("fund_mapping: %d rows loaded", len(df))
    return df


def load_issuer_mapping(rules_dir: Path | None = None) -> pd.DataFrame:
    """Load issuer_mapping.csv -> DataFrame[etp_category, issuer, issuer_nickname]."""
    path = _resolve("issuer_mapping", rules_dir)
    df = _read(path)
    df = df[["etp_category", "issuer", "issuer_nickname"]].dropna(
        subset=["etp_category", "issuer"]
    )
    df = df.drop_duplicates(subset=["etp_category", "issuer"])
    log.info("issuer_mapping: %d rows loaded", len(df))
    return df


def load_exclusions(rules_dir: Path | None = None) -> pd.DataFrame:
    """Load exclusions.csv -> DataFrame[ticker, etp_category]."""
    path = _resolve("exclusions", rules_dir)
    if not path.exists():
        return pd.DataFrame(columns=["ticker", "etp_category"])
    df = _read(path)
    df = df[["ticker", "etp_category"]].dropna(subset=["ticker"])
    df = df.drop_duplicates(subset=["ticker", "etp_category"])
    log.info("exclusions: %d rows loaded", len(df))
    return df


def load_rex_funds(rules_dir: Path | None = None) -> pd.DataFrame:
    """Load rex_funds.csv -> DataFrame[ticker]."""
    path = _resolve("rex_funds", rules_dir)
    df = _read(path)
    df = df[["ticker"]].dropna(subset=["ticker"])
    df = df.drop_duplicates()
    log.info("rex_funds: %d rows loaded", len(df))
    return df


def load_category_attributes(rules_dir: Path | None = None) -> pd.DataFrame:
    """Load all per-category attribute CSVs and merge on ticker.

    Returns a single DataFrame with columns: ticker + all map_* columns.
    """
    rd = rules_dir or RULES_DIR
    result = None

    for cat, attr_cols in CATEGORY_ATTR_MAP.items():
        fname = RULE_FILES.get(f"attributes_{cat}")
        if not fname:
            continue
        path = rd / fname
        if not path.exists():
            log.debug("Attribute file not found: %s", path)
            continue

        df = _read(path)
        expected = ["ticker"] + attr_cols
        available = [c for c in expected if c in df.columns]
        if "ticker" not in available:
            log.warning("No ticker column in %s", path.name)
            continue
        df = df[available].dropna(subset=["ticker"]).drop_duplicates(subset=["ticker"])

        if result is None:
            result = df
        else:
            result = result.merge(df, on="ticker", how="outer")

    if result is None:
        result = pd.DataFrame(columns=["ticker"] + ALL_ATTR_COLS)

    log.info("category_attributes: %d tickers loaded", len(result))
    return result


def load_market_status(rules_dir: Path | None = None) -> pd.DataFrame:
    """Load market_status.csv -> DataFrame[code, description]."""
    path = _resolve("market_status", rules_dir)
    if not path.exists():
        log.warning("market_status.csv not found at %s", path)
        return pd.DataFrame(columns=["code", "description"])
    df = _read(path)
    df = df[["code", "description"]].dropna(subset=["code"])
    df = df.drop_duplicates(subset=["code"])
    log.info("market_status: %d rows loaded", len(df))
    return df


def load_all_rules(rules_dir: Path | None = None) -> dict[str, pd.DataFrame]:
    """Load all rule CSVs into a dict."""
    return {
        "fund_mapping": load_fund_mapping(rules_dir),
        "issuer_mapping": load_issuer_mapping(rules_dir),
        "exclusions": load_exclusions(rules_dir),
        "rex_funds": load_rex_funds(rules_dir),
        "market_status": load_market_status(rules_dir),
        "category_attributes": load_category_attributes(rules_dir),
    }


def validate_rules(rules: dict[str, pd.DataFrame]) -> list[str]:
    """Validate loaded rules. Returns list of warning messages (empty = OK)."""
    warnings = []

    fm = rules.get("fund_mapping", pd.DataFrame())
    if fm.empty:
        warnings.append("fund_mapping is empty")
    else:
        valid_cats = {"LI", "CC", "Crypto", "Defined", "Thematic"}
        bad = set(fm["etp_category"].unique()) - valid_cats
        if bad:
            warnings.append(f"fund_mapping has unknown categories: {sorted(bad)}")

    im = rules.get("issuer_mapping", pd.DataFrame())
    if im.empty:
        warnings.append("issuer_mapping is empty")

    rex = rules.get("rex_funds", pd.DataFrame())
    if rex.empty:
        warnings.append("rex_funds is empty")

    return warnings


def sync_rules_to_db(rules: dict[str, pd.DataFrame], session) -> None:
    """Write rule DataFrames to the mkt_* rule tables (full refresh)."""
    from webapp.models import (
        MktFundMapping, MktIssuerMapping, MktCategoryAttributes,
        MktExclusion, MktRexFund,
    )

    # fund_mapping
    session.query(MktFundMapping).delete()
    for _, row in rules["fund_mapping"].iterrows():
        session.add(MktFundMapping(
            ticker=str(row["ticker"]).strip(),
            etp_category=str(row["etp_category"]).strip(),
        ))

    # issuer_mapping
    session.query(MktIssuerMapping).delete()
    for _, row in rules["issuer_mapping"].iterrows():
        session.add(MktIssuerMapping(
            etp_category=str(row["etp_category"]).strip(),
            issuer=str(row["issuer"]).strip(),
            issuer_nickname=str(row["issuer_nickname"]).strip(),
        ))

    # exclusions
    session.query(MktExclusion).delete()
    for _, row in rules["exclusions"].iterrows():
        session.add(MktExclusion(
            ticker=str(row["ticker"]).strip(),
            etp_category=str(row["etp_category"]).strip(),
        ))

    # rex_funds
    session.query(MktRexFund).delete()
    for _, row in rules["rex_funds"].iterrows():
        session.add(MktRexFund(ticker=str(row["ticker"]).strip()))

    # category_attributes
    session.query(MktCategoryAttributes).delete()
    attrs = rules["category_attributes"]
    for _, row in attrs.iterrows():
        kwargs = {"ticker": str(row["ticker"]).strip()}
        for col in ALL_ATTR_COLS:
            if col in row.index and pd.notna(row[col]):
                kwargs[col] = str(row[col]).strip()
        session.add(MktCategoryAttributes(**kwargs))

    session.commit()
    log.info("Rules synced to DB (%d fund_mapping, %d issuer_mapping, %d exclusions, %d rex_funds, %d attributes)",
             len(rules["fund_mapping"]), len(rules["issuer_mapping"]),
             len(rules["exclusions"]), len(rules["rex_funds"]),
             len(rules["category_attributes"]))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve(rule_name: str, rules_dir: Path | None = None) -> Path:
    """Resolve path to a rule CSV file."""
    rd = rules_dir or RULES_DIR
    fname = RULE_FILES[rule_name]
    return rd / fname


def _read(path: Path) -> pd.DataFrame:
    """Read a CSV file with project-standard robustness settings."""
    if not path.exists():
        raise FileNotFoundError(f"Rule file not found: {path}")
    return pd.read_csv(path, dtype=str, engine="python", on_bad_lines="skip")
