"""L&I Filing/Launch Candidates + Evaluator — parquet-backed.

Reads directly from ``data/analysis/*.parquet``, mirroring the visual
structure of ``screener/li_engine/analysis/weekly_v2_report.py``.

Sections (top to bottom):
  1. Hero KPI bar (this week's L&I 485APOS, REX filings, new underliers,
     top retail mention).
  2. Launch Queue cards x12 (``launch_candidates.parquet``,
     ``has_signals=True``, sorted by ``composite_score``).
  3. Filing Whitespace cards x12 (``whitespace_v4.parquet``, top 12 by
     ``composite_score``).
  4. Inline Evaluator panel (POST -> ``compute_score_v3()`` directly).
  5. Money Flow table (``bbg_timeseries_panel.parquet`` if present).

Replaces the PR-1 stub that proxied to ``filings._candidates_impl``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from screener.li_engine.analysis.formatters import (
    fmt_mcap,
    fmt_oi,
    fmt_pct,
    pretty_themes,
    resolve_company_line,
)
from screener.li_engine.analysis.whitespace_v3 import WEIGHTS
from screener.li_engine.analysis.weekly_v2_report import (
    load_earliest_competitor_filing_dates,
    load_filings_count_this_week,
    load_top_mentions,
)
from webapp.dependencies import get_db

log = logging.getLogger(__name__)

router = APIRouter(prefix="/tools/li", tags=["tools-li"])
templates = Jinja2Templates(directory="webapp/templates")

PARQUET_DIR = Path(__file__).resolve().parent.parent.parent / "data" / "analysis"


# ---------------------------------------------------------------------------
# Parquet helpers
# ---------------------------------------------------------------------------

def _load_parquet(name: str) -> pd.DataFrame:
    """Read a parquet from data/analysis/, returning empty DF on any error."""
    p = PARQUET_DIR / name
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_parquet(p)
    except Exception as exc:  # pragma: no cover - defensive
        log.warning("failed to read %s: %s", p, exc)
        return pd.DataFrame()


def _safe_int(v: Any, default: int = 0) -> int:
    try:
        if v is None or pd.isna(v):
            return default
        return int(v)
    except Exception:
        return default


def _safe_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None or pd.isna(v):
            return default
        return float(v)
    except Exception:
        return default


def _row_get(row: pd.Series, key: str, default=None):
    """Series.get with NaN-safe default coalescing."""
    if key in row.index:
        v = row[key]
        if v is None:
            return default
        try:
            if pd.isna(v):
                return default
        except (TypeError, ValueError):
            pass
        return v
    return default


# ---------------------------------------------------------------------------
# Card builders (shared shape between launches + whitespace)
# ---------------------------------------------------------------------------

def _build_card(ticker: str, row: pd.Series, *, is_launch: bool) -> dict:
    """Convert a parquet row into a template-friendly card dict."""
    sector = _row_get(row, "sector", "") or ""
    themes_raw = _row_get(row, "themes", "") or ""
    fund_name = (_row_get(row, "rex_fund_name", "")
                 or _row_get(row, "fund_name", "")
                 or "")

    card = {
        "ticker": ticker,
        "sector": sector or "—",
        "is_hot_theme": bool(_row_get(row, "is_hot_theme", False) or False),
        "themes": pretty_themes(themes_raw),
        "company_line": resolve_company_line(
            ticker,
            sector=sector if sector and sector != "—" else None,
            fund_name=fund_name or None,
        ),
        "market_cap": fmt_mcap(_row_get(row, "market_cap")),
        "rvol_90d": _safe_float(_row_get(row, "rvol_90d")),
        "ret_1m": _safe_float(_row_get(row, "ret_1m")),
        "ret_1y": _safe_float(_row_get(row, "ret_1y")),
        "open_interest": fmt_oi(_row_get(row, "total_oi")),
        "si_ratio": _safe_float(_row_get(row, "si_ratio")),
        "mentions": _safe_int(_row_get(row, "mentions_24h")),
        "composite_score": _safe_float(_row_get(row, "composite_score")),
        "score_pct": _safe_float(_row_get(row, "score_pct")),
    }

    if is_launch:
        card["competitor_filed"] = _safe_int(_row_get(row, "competitor_filed_total"))
        card["rex_fund_name"] = _row_get(row, "rex_fund_name", "") or ""
        card["rex_ticker"] = _row_get(row, "rex_ticker", "") or ""
        card["direction"] = _row_get(row, "direction", "") or ""
        card["leverage"] = _row_get(row, "leverage", "") or ""
    return card


# ---------------------------------------------------------------------------
# GET — main page
# ---------------------------------------------------------------------------

@router.get("/candidates")
def candidates(request: Request, db: Session = Depends(get_db)):
    # 1. Hero KPI bar -------------------------------------------------------
    try:
        kpis = load_filings_count_this_week() or {}
    except Exception as exc:
        log.warning("KPI load failed: %s", exc)
        kpis = {}

    try:
        top_ticker, top_count = load_top_mentions()
    except Exception:
        top_ticker, top_count = "—", 0
    kpis.setdefault("top_mention_ticker", top_ticker)
    kpis.setdefault("top_mention_count", top_count)

    # 2. Launch queue -------------------------------------------------------
    launches: list[dict] = []
    launches_df = _load_parquet("launch_candidates.parquet")
    if not launches_df.empty:
        if "has_signals" in launches_df.columns:
            df = launches_df[launches_df["has_signals"] == True]  # noqa: E712
        else:
            df = launches_df
        if "composite_score" in df.columns:
            df = df.sort_values("composite_score", ascending=False)
        df = df.head(12)
        for ticker, row in df.iterrows():
            launches.append(_build_card(str(ticker), row, is_launch=True))

    # Effective-date projections (per launch ticker)
    try:
        eff_dates = load_earliest_competitor_filing_dates() or {}
    except Exception as exc:
        log.warning("effective-date load failed: %s", exc)
        eff_dates = {}
    for item in launches:
        info = eff_dates.get(item["ticker"]) or {}
        item["earliest_filing_date"] = info.get("earliest_filing_date")
        item["earliest_issuer"] = info.get("earliest_issuer", "")
        item["closest_effective_date"] = info.get("closest_effective_date")
        item["projected_effective_date"] = info.get("projected_effective_date")
        item["projected_basis"] = info.get("projected_basis", "")

    # 3. Filing whitespace --------------------------------------------------
    whitespace: list[dict] = []
    whitespace_df = _load_parquet("whitespace_v4.parquet")
    filed_df = _load_parquet("filed_underliers.parquet")
    if not whitespace_df.empty:
        df = whitespace_df
        if "composite_score" in df.columns:
            df = df.sort_values("composite_score", ascending=False)
        df = df.head(12)
        for ticker, row in df.iterrows():
            card = _build_card(str(ticker), row, is_launch=False)
            n_filings = 0
            if not filed_df.empty and ticker in filed_df.index:
                try:
                    n_filings = _safe_int(filed_df.loc[ticker, "n_filings_total"])
                except Exception:
                    n_filings = 0
            card["n_filings_total"] = n_filings
            whitespace.append(card)

    # 5. Money flow ---------------------------------------------------------
    money_flow: list[dict] = []
    flow_df = _load_parquet("bbg_timeseries_panel.parquet")
    if not flow_df.empty and "metric" in flow_df.columns:
        try:
            comp_df = _load_parquet("competitor_counts.parquet")
            df = flow_df[flow_df["metric"] == "daily_flow"].copy()
            df["date"] = pd.to_datetime(df["date"], errors="coerce")
            df = df.dropna(subset=["date"])
            cutoff = df["date"].max() - pd.Timedelta(days=28)
            recent = df[df["date"] >= cutoff].copy()
            if "ticker" in recent.columns:
                recent["underlier"] = recent["ticker"].astype(str).str.split().str[0]
                agg = recent.groupby("underlier")["value"].agg(
                    flow_4w="sum",
                    churn_4w=lambda x: x.abs().sum(),
                )
                agg = agg.assign(abs_flow=agg["flow_4w"].abs())
                agg = agg.sort_values("abs_flow", ascending=False).head(12)
                for ticker, row in agg.iterrows():
                    entry = {
                        "ticker": ticker,
                        "flow_4w": _safe_float(row.get("flow_4w")),
                        "churn_4w": _safe_float(row.get("churn_4w")),
                        "competitor_long": 0,
                        "competitor_short": 0,
                    }
                    if not comp_df.empty and ticker in comp_df.index:
                        crow = comp_df.loc[ticker]
                        entry["competitor_long"] = _safe_int(
                            crow.get("competitor_active_long", 0)
                            if hasattr(crow, "get") else 0
                        )
                        entry["competitor_short"] = _safe_int(
                            crow.get("competitor_active_short", 0)
                            if hasattr(crow, "get") else 0
                        )
                    money_flow.append(entry)
        except Exception as exc:
            log.warning("money flow load failed: %s", exc)
            money_flow = []

    return templates.TemplateResponse(
        "tools/li_candidates.html",
        {
            "request": request,
            "kpis": kpis,
            "launches": launches,
            "whitespace": whitespace,
            "money_flow": money_flow,
            "has_money_flow": len(money_flow) > 0,
        },
    )


# ---------------------------------------------------------------------------
# POST — inline evaluator
# ---------------------------------------------------------------------------

@router.post("/candidates/evaluate")
def evaluate(request: Request, tickers: str = Form(...)):
    """Inline evaluator — replaces the standalone /filings/evaluator POST.

    Accepts a comma-separated list of tickers, looks each up in the engine's
    parquets (whitespace_v4 first, then launch_candidates), and returns the
    composite score, percentile, top weighted-driver contributions, and
    theme metadata as JSON.
    """
    ticker_list = [t.strip().upper() for t in (tickers or "").split(",") if t.strip()]
    if not ticker_list:
        return JSONResponse({"results": []})

    whitespace_df = _load_parquet("whitespace_v4.parquet")
    launches_df = _load_parquet("launch_candidates.parquet")

    results: list[dict] = []
    for tk in ticker_list:
        row = None
        source = None
        if not whitespace_df.empty and tk in whitespace_df.index:
            row = whitespace_df.loc[tk]
            source = "whitespace"
        elif not launches_df.empty and tk in launches_df.index:
            row = launches_df.loc[tk]
            source = "launches"

        if row is None:
            results.append({
                "ticker": tk,
                "found": False,
                "message": "No engine data — ticker not in whitespace or launch candidates.",
            })
            continue

        # Top drivers — largest |weight * z|.
        contributions: list[dict] = []
        for signal_name, weight in WEIGHTS.items():
            z_col = f"{signal_name}_z" if f"{signal_name}_z" in row.index else signal_name
            if z_col in row.index:
                z = _safe_float(row.get(z_col))
                contributions.append({
                    "signal": signal_name,
                    "z": z,
                    "weight": weight,
                    "contribution": z * weight,
                })
        contributions.sort(key=lambda c: abs(c["contribution"]), reverse=True)

        results.append({
            "ticker": tk,
            "found": True,
            "source": source,
            "composite_score": _safe_float(row.get("composite_score")),
            "score_pct": _safe_float(row.get("score_pct")),
            "top_drivers": contributions[:5],
            "themes": pretty_themes(row.get("themes", "")),
            "is_hot_theme": bool(_row_get(row, "is_hot_theme", False) or False),
            "sector": _row_get(row, "sector", "") or "",
            "market_cap": fmt_mcap(_row_get(row, "market_cap")),
        })

    return JSONResponse({"results": results})
