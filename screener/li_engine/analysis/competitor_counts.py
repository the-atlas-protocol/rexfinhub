"""Per-underlier competitor counts, separated by direction (long/short).

Combines:
    - Active products from mkt_master_data
    - Filed-but-not-active products from fund_extractions regex (filed_underliers)

Returns counts per underlier:
    n_competitors_active_long
    n_competitors_active_short
    n_competitors_filed_long  (filed by competitor, not yet active)
    n_competitors_filed_short
    rex_active_long, rex_active_short  (REX exposure)
    rex_filed_long, rex_filed_short
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
OUT = _ROOT / "data" / "analysis" / "competitor_counts.parquet"


def _clean(t):
    return t.split()[0].upper().strip() if isinstance(t, str) else ""


# Reuse the patterns and infer direction
LONG_RE = re.compile(r"\b(?:Long|Bull|Ultra(?!Short)|2X|3X)\b", re.IGNORECASE)
SHORT_RE = re.compile(r"\b(?:Short|Inverse|Bear|UltraShort|UltraPro\s+Short)\b", re.IGNORECASE)


def _infer_direction(name: str) -> str:
    if not isinstance(name, str):
        return "unknown"
    if SHORT_RE.search(name):
        return "short"
    if LONG_RE.search(name):
        return "long"
    return "unknown"


def build() -> pd.DataFrame:
    conn = sqlite3.connect(str(DB))
    try:
        # Active products from master data — relaxed filter so newly-launched
        # products with NULL classification still get caught via fund_name regex
        active = pd.read_sql_query(
            """
            SELECT map_li_underlier, is_rex, market_status, fund_name,
                   map_li_direction, primary_category
            FROM mkt_master_data
            WHERE market_status IN ('ACTV', 'ACTIVE')
              AND fund_name IS NOT NULL
              AND (
                  primary_category = 'LI'
                  OR fund_name LIKE '%2X%'
                  OR fund_name LIKE '%3X%'
                  OR fund_name LIKE '%Inverse%'
                  OR fund_name LIKE '%Bull%'
                  OR fund_name LIKE '%Bear%'
                  OR fund_name LIKE '%Ultra%'
              )
            """,
            conn,
        )

        # Filed (any status) from master data
        filed_master = pd.read_sql_query(
            """
            SELECT map_li_underlier, is_rex, market_status, fund_name,
                   map_li_direction
            FROM mkt_master_data
            WHERE primary_category = 'LI'
              AND map_li_underlier IS NOT NULL
              AND map_li_underlier != ''
            """,
            conn,
        )

        # Pull all fund extractions for fund_name regex parsing
        all_fe = pd.read_sql_query(
            """
            SELECT fe.series_name, fe.class_contract_name, f.registrant
            FROM fund_extractions fe
            LEFT JOIN filings f ON f.id = fe.filing_id
            """,
            conn,
        )
    finally:
        conn.close()

    # Active counts per underlier per direction.
    # Use map_li_underlier when present, else fund_name regex extraction.
    from screener.li_engine.analysis.filed_underliers import extract_underlier
    active["underlier"] = active["map_li_underlier"].astype(str).map(_clean)
    needs_regex = (active["underlier"] == "") | (active["underlier"] == "NONE") | active["map_li_underlier"].isna()
    active.loc[needs_regex, "underlier"] = active.loc[needs_regex, "fund_name"].apply(
        lambda n: extract_underlier(n) or ""
    )
    active = active[active["underlier"] != ""]

    def _direction_from_row(row):
        _v = row.get("map_li_direction")
        d = ("" if (_v is None or (isinstance(_v, float) and pd.isna(_v))) else str(_v)).lower()
        if "short" in d or "inverse" in d:
            return "short"
        if "long" in d:
            return "long"
        _fn = row.get("fund_name")
        fn = "" if (_fn is None or (isinstance(_fn, float) and pd.isna(_fn))) else str(_fn)
        return _infer_direction(fn)

    active["dir"] = active.apply(_direction_from_row, axis=1)

    # Aggregate active
    rex_active = active[active["is_rex"] == 1].groupby(["underlier", "dir"]).size().unstack(fill_value=0)
    comp_active = active[active["is_rex"] == 0].groupby(["underlier", "dir"]).size().unstack(fill_value=0)

    rex_active.columns = [f"rex_active_{c}" for c in rex_active.columns]
    comp_active.columns = [f"competitor_active_{c}" for c in comp_active.columns]

    # Filed but NOT active
    not_active = filed_master[~filed_master["market_status"].isin(["ACTV", "ACTIVE"])].copy()
    not_active["underlier"] = not_active["map_li_underlier"].astype(str).map(_clean)
    not_active = not_active[not_active["underlier"] != ""]
    not_active["dir"] = not_active.apply(_direction_from_row, axis=1)

    rex_filed = not_active[not_active["is_rex"] == 1].groupby(["underlier", "dir"]).size().unstack(fill_value=0)
    comp_filed = not_active[not_active["is_rex"] == 0].groupby(["underlier", "dir"]).size().unstack(fill_value=0)
    rex_filed.columns = [f"rex_filed_{c}" for c in rex_filed.columns]
    comp_filed.columns = [f"competitor_filed_{c}" for c in comp_filed.columns]

    # Layer: from regex on fund names — pulls in filings not yet in master_data
    # Use the filed_underliers parquet if available
    filed_extra_path = _ROOT / "data" / "analysis" / "filed_underliers.parquet"
    extra_counts = pd.DataFrame()
    if filed_extra_path.exists():
        # Re-scan with direction classification
        from screener.li_engine.analysis.filed_underliers import (
            extract_underlier, PATTERNS, STOP_TICKERS,
        )
        all_fe["underlier"] = all_fe["series_name"].apply(extract_underlier)
        fb = all_fe["underlier"].isna()
        all_fe.loc[fb, "underlier"] = all_fe.loc[fb, "class_contract_name"].apply(extract_underlier)
        all_fe = all_fe.dropna(subset=["underlier"])
        all_fe["underlier"] = all_fe["underlier"].str.upper()
        all_fe["dir"] = all_fe["series_name"].apply(_infer_direction)

        # Detect REX vs not — registrant pattern
        all_fe["is_rex"] = all_fe["registrant"].astype(str).str.contains(
            r"REX|ETF Opportunities Trust", case=False, na=False, regex=True
        )

        comp_extra = all_fe[~all_fe["is_rex"]].groupby(["underlier", "dir"]).size().unstack(fill_value=0)
        comp_extra.columns = [f"competitor_extra_{c}" for c in comp_extra.columns]

        rex_extra = all_fe[all_fe["is_rex"]].groupby(["underlier", "dir"]).size().unstack(fill_value=0)
        rex_extra.columns = [f"rex_extra_{c}" for c in rex_extra.columns]
        extra_counts = comp_extra.join(rex_extra, how="outer")

    out = (rex_active.join(comp_active, how="outer")
           .join(rex_filed, how="outer")
           .join(comp_filed, how="outer"))
    if not extra_counts.empty:
        out = out.join(extra_counts, how="outer")
    out = out.fillna(0).astype(int)

    log.info("Competitor counts: %d underliers", len(out))
    return out


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    df = build()
    df.to_parquet(OUT, compression="snappy")
    log.info("Wrote %s (%d rows)", OUT, len(df))

    print("\nSample (top 15 by total competitor activity):")
    df["total_competitor"] = df.filter(like="competitor_").sum(axis=1)
    print(df.sort_values("total_competitor", ascending=False).head(15)[
        [c for c in df.columns if c != "total_competitor"]
    ].to_string())


if __name__ == "__main__":
    main()
