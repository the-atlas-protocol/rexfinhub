"""Whitespace scorer v3 — retail-interest gate + demand-weighted scoring.

v1 was dominated by structural liquidity (survivorship bias).
v2 was dominated by extreme trailing returns (moon stocks already done).
v3 requires actual evidence of CURRENT retail interest via:
    (a) ApeWisdom mentions > 0, OR
    (b) Stock is in our curated themes list

Then scores with a demand-priority composite.

The idea: it's not enough for a stock to have HIGH vol or HIGH return —
someone has to currently CARE about it. Retail interest is the gate.
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
import pandas as pd

from screener.li_engine.analysis.whitespace_v2 import (
    DB, THEMES_YAML, _clean, _zscore,
    load_universe, annotate_product_coverage, load_apewisdom_map, load_themes,
)
from screener.li_engine.analysis.secular_trends import (
    load_themes_parquet as _load_secular_themes,
    OUT as SECULAR_OUT,
)

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
OUT = _ROOT / "data" / "analysis" / "whitespace_v3.parquet"


WEIGHTS = {
    # Positive
    "mentions_z":    0.22,   # retail is actually talking (THE live demand signal)
    "rvol_30d":      0.15,   # recent vol — retail leverage magnet
    "theme_bonus":   0.14,   # curated theme membership
    "ret_1m":        0.12,   # recent price move — current momentum (not 1y lag)
    "rvol_90d":      0.09,   # sustained vol regime
    "insider_pct":   0.08,   # insider alignment (post-launch validated)
    "ret_1y":        0.05,   # long-term trend (lagging — small weight only)
    # Negative
    "si_ratio":      -0.08,  # high SI predicts failure
    "inst_own_pct":  -0.07,  # institutional-heavy = retail avoids
}


# Static fallback — used only if data/analysis/secular_trends.parquet is
# missing or empty. The secular-trend auto-detector replaces this list
# nightly with data-driven themes (filing velocity + cross-issuer cadence +
# price momentum). The Feb-2026 build hard-coded six themes and missed the
# memory/HBM rotation entirely (SK Hynix +500%, MU +200%) — this fallback
# is now the safety net, not the primary signal.
_STATIC_HOT_THEMES = {
    "ai_infrastructure", "ai_applications", "quantum",
    "semiconductors", "space", "nuclear", "memory_hbm",
}


def _resolve_hot_themes() -> tuple[set[str], str]:
    """Return (themes, source_label).

    Source: 'parquet' if secular_trends.parquet has data; 'static' otherwise.
    """
    detected = _load_secular_themes()
    if detected:
        # Union with always-relevant static seeds so a single bad parquet run
        # cannot wipe out long-running themes.
        return detected | {"memory_hbm", "ai_infrastructure"}, "parquet"
    log.warning(
        "secular_trends.parquet missing/empty — falling back to static hot-theme list. "
        "Run `python -m screener.li_engine.analysis.secular_trends` to refresh."
    )
    return _STATIC_HOT_THEMES, "static"


HOT_THEMES, HOT_THEMES_SOURCE = _resolve_hot_themes()


def compute_score_v3(df: pd.DataFrame, themes: dict[str, list[str]],
                     mentions_map: dict[str, int]) -> pd.DataFrame:
    out = df.copy()

    # Retail attention
    out["mentions_24h"] = out.index.map(lambda t: mentions_map.get(t, 0))
    out["mentions_z"] = _zscore(out["mentions_24h"], log_transform=True)

    # Thematic tag — with hot-theme amplification
    theme_tickers = set()
    ticker_theme_map: dict[str, list[str]] = {}
    for theme, tks in themes.items():
        for t in tks:
            tc = _clean(t)
            theme_tickers.add(tc)
            ticker_theme_map.setdefault(tc, []).append(theme)
    out["is_thematic"] = out.index.isin(theme_tickers).astype(float)
    out["themes"] = out.index.map(lambda t: ", ".join(ticker_theme_map.get(t, [])))

    def _theme_multiplier(ticker):
        t_list = ticker_theme_map.get(ticker, [])
        if any(t in HOT_THEMES for t in t_list):
            return 3.0  # hot theme: 1.5x the normal 2.0 bonus
        return 2.0
    out["theme_bonus"] = out.index.map(_theme_multiplier) * out["is_thematic"]
    out["is_hot_theme"] = out.index.map(
        lambda t: int(any(theme in HOT_THEMES for theme in ticker_theme_map.get(t, [])))
    )

    # Z-scores for raw signals
    for col in ("rvol_30d", "rvol_90d", "ret_1m", "ret_1y",
                "si_ratio", "insider_pct", "inst_own_pct"):
        if col not in out.columns:
            out[f"{col}_z"] = 0
            continue
        out[f"{col}_z"] = _zscore(out[col])

    # Composite
    score = pd.Series(0.0, index=out.index)
    score += WEIGHTS["mentions_z"] * out["mentions_z"].fillna(0)
    score += WEIGHTS["rvol_30d"] * out["rvol_30d_z"].fillna(0)
    score += WEIGHTS["theme_bonus"] * out["theme_bonus"].fillna(0)
    score += WEIGHTS["ret_1m"] * out["ret_1m_z"].fillna(0)
    score += WEIGHTS["rvol_90d"] * out["rvol_90d_z"].fillna(0)
    score += WEIGHTS["insider_pct"] * out["insider_pct_z"].fillna(0)
    score += WEIGHTS["ret_1y"] * out["ret_1y_z"].fillna(0)
    score += WEIGHTS["si_ratio"] * out["si_ratio_z"].fillna(0)
    score += WEIGHTS["inst_own_pct"] * out["inst_own_pct_z"].fillna(0)

    out["composite_score"] = score
    out["score_pct"] = out["composite_score"].rank(pct=True) * 100
    return out


def apply_retail_gate(df: pd.DataFrame, min_mentions: int = 1) -> pd.DataFrame:
    """Require either (a) non-trivial retail mentions OR (b) thematic."""
    gate_mentions = df["mentions_24h"] >= min_mentions
    gate_theme = df["is_thematic"] == 1
    return df[gate_mentions | gate_theme].copy()


def top_drivers_v3(row: pd.Series) -> list[str]:
    driver_components = {
        "mentions_z":    ("retail mention volume", WEIGHTS["mentions_z"]),
        "theme_bonus":   ("thematic relevance", WEIGHTS["theme_bonus"]),
        "rvol_30d_z":    ("30-day realized volatility", WEIGHTS["rvol_30d"]),
        "ret_1m_z":      ("recent 1-month momentum", WEIGHTS["ret_1m"]),
        "rvol_90d_z":    ("90-day realized volatility", WEIGHTS["rvol_90d"]),
        "insider_pct_z": ("insider ownership", WEIGHTS["insider_pct"]),
        "ret_1y_z":      ("1-year price trend", WEIGHTS["ret_1y"]),
    }
    contribs = []
    for col, (label, w) in driver_components.items():
        v = row.get(col, 0) or 0
        contrib = v * w
        if contrib > 0.05:  # material contribution only
            contribs.append((label, contrib))
    contribs.sort(key=lambda x: -x[1])
    return [label for label, _ in contribs[:3]]


def negative_flags(row: pd.Series) -> list[str]:
    flags = []
    si = row.get("si_ratio", 0) or 0
    if si > 10:
        flags.append(f"elevated short interest (SI ratio {si:.1f})")
    inst = row.get("inst_own_pct", 0) or 0
    if inst > 85:
        flags.append(f"heavy institutional ownership ({inst:.0f}%)")
    ret_3m = row.get("ret_3m", 0) or 0
    if ret_3m > 80:
        flags.append(f"ran up {ret_3m:+.0f}% in 3m — mean-reversion risk")
    return flags


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    universe = load_universe()
    universe = annotate_product_coverage(universe)

    whitespace = universe[
        (universe["n_comp_products"] == 0) &
        (universe["n_rex_products"] == 0) &
        (universe["n_rex_filed_any"] == 0)
    ].copy()
    log.info("Whitespace: %d of %d tickers", len(whitespace), len(universe))

    mentions = load_apewisdom_map(set(whitespace.index))
    themes = load_themes()

    scored = compute_score_v3(whitespace, themes, mentions)
    log.info("Before retail gate: %d tickers", len(scored))

    gated = apply_retail_gate(scored, min_mentions=1)
    log.info("After retail gate (mentions>=1 OR thematic): %d tickers", len(gated))

    gated = gated.sort_values("composite_score", ascending=False)
    gated.to_parquet(OUT, compression="snappy")

    print("=" * 100)
    print("WHITESPACE v3 — RETAIL-GATED + DEMAND-WEIGHTED")
    print("=" * 100)
    print(f"Universe: {len(universe)} | Whitespace: {len(whitespace)} | Retail-gated: {len(gated)}")
    if HOT_THEMES_SOURCE == "parquet":
        print(f"Hot themes (secular_trends.parquet, {len(HOT_THEMES)}): "
              f"{', '.join(sorted(HOT_THEMES))}")
    else:
        print(f"Hot themes (STATIC FALLBACK, secular_trends.parquet missing): "
              f"{', '.join(sorted(HOT_THEMES))}")
    print()

    for i, ticker in enumerate(gated.head(25).index, 1):
        row = gated.loc[ticker]
        sector = (row.get("sector") or "—")[:22]
        mcap = row["market_cap"] / 1000
        rvol = row.get("rvol_90d", 0) or 0
        ret1m = row.get("ret_1m", 0) or 0
        ret1y = row.get("ret_1y", 0) or 0
        mentions = int(row.get("mentions_24h", 0) or 0)
        themes_str = row.get("themes", "")
        score = row["composite_score"]

        gate = "mentions" if mentions else ("thematic" if row.get("is_thematic") else "??")
        print(f"\n{i:>2}. {ticker:<6} {sector:<22} ${mcap:>5,.1f}B  vol={rvol:>3.0f}  "
              f"1m={ret1m:+5.0f}%  1y={ret1y:+5.0f}%  mentions={mentions:>3}  "
              f"[{gate}]  score={score:+.2f}")
        if themes_str:
            print(f"    theme: {themes_str}")
        for d in top_drivers_v3(row):
            print(f"    + {d}")
        for flag in negative_flags(row):
            print(f"    ! {flag}")


if __name__ == "__main__":
    main()
