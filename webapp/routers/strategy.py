"""Strategy router — L&I Whitespace Engine web views.

Routes:
    /strategy                 -> overview dashboard
    /strategy/whitespace      -> ranked whitespace candidate table
    /strategy/race            -> filing race clock (competitor 485APOS cadence)
    /strategy/ticker/{t}      -> per-ticker deep-dive card

All pages read from the parquet artifacts produced by
screener.li_engine.analysis.* modules. No DB writes.

Defensive posture (2026-05-12 incident):
    The strategy parquets are produced by an out-of-band VPS pipeline and
    uploaded to Render via /api/v1/parquets/upload. If the pipeline shifts
    a column or upload skips a file, this router used to 500 with a bare
    KeyError. Each route now wraps its body in try/except that logs the
    full traceback and falls back to ``strategy/empty.html`` so the
    /strategy/* surface area never goes dark on a schema drift.

    Helper accessors (``_col``, ``_safe_get``) treat missing columns and
    NaN values as soft-absent — the column is rendered as "—" rather
    than crashing the response.
"""
from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

log = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")

_ROOT = Path(__file__).resolve().parent.parent.parent
WS_PARQUET = _ROOT / "data" / "analysis" / "whitespace_v4.parquet"
WS_V1_PARQUET = _ROOT / "data" / "analysis" / "whitespace_candidates.parquet"
RACE_PARQUET = _ROOT / "data" / "analysis" / "filing_race.parquet"
CADENCE_PARQUET = _ROOT / "data" / "analysis" / "issuer_cadence.parquet"


def _load_whitespace() -> pd.DataFrame:
    """Prefer v4; fall back to v1 if v4 not built yet.

    Returns an empty DataFrame on any read error rather than raising —
    callers handle the empty case explicitly.
    """
    for path in (WS_PARQUET, WS_V1_PARQUET):
        if not path.exists():
            continue
        try:
            return pd.read_parquet(path)
        except Exception:
            log.exception("Failed to read whitespace parquet at %s", path)
            continue
    return pd.DataFrame()


def _safe_get(row: pd.Series, col: str, default=None):
    """Return ``row[col]`` if present and not-NaN, else ``default``.

    Wraps ``pd.Series.get`` with a NaN check so callers can chain
    ``or "default"`` without surfacing ``np.nan`` (truthy) into templates.
    """
    if col not in row.index:
        return default
    val = row.get(col, default)
    try:
        if pd.isna(val):
            return default
    except (TypeError, ValueError):
        # pd.isna chokes on non-scalar (e.g. lists) — those are real values
        return val
    return val


def _fmt_mcap(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    if v >= 100_000:
        return f"${v/1000:.0f}B"
    if v >= 1000:
        return f"${v/1000:.1f}B"
    return f"${v:,.0f}M"


def _fmt_pct(v, places=0) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:+.{places}f}%"


def _fmt_num(v, places=0) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{v:,.{places}f}"


def _empty_response(request: Request, message: str) -> HTMLResponse:
    return templates.TemplateResponse("strategy/empty.html", {
        "request": request,
        "message": message,
    })


@router.get("/strategy", response_class=HTMLResponse)
def strategy_home(request: Request):
    try:
        ws = _load_whitespace()

        if ws.empty:
            return _empty_response(
                request,
                "No whitespace parquet yet. Run /li-report or whitespace_v4 first.",
            )

        # KPIs — all guarded against missing columns
        n_universe = len(ws)
        n_thematic = (
            int((ws["is_thematic"] == 1).sum()) if "is_thematic" in ws.columns else 0
        )
        n_high_mentions = (
            int((ws["mentions_24h"] >= 10).sum()) if "mentions_24h" in ws.columns else 0
        )

        # Age of data
        if WS_PARQUET.exists():
            file_mtime = WS_PARQUET.stat().st_mtime
        elif WS_V1_PARQUET.exists():
            file_mtime = WS_V1_PARQUET.stat().st_mtime
        else:
            file_mtime = datetime.now().timestamp()
        data_age_hours = (datetime.now().timestamp() - file_mtime) / 3600

        # Top 10 — sort only if score column exists, else stable order
        if "composite_score" in ws.columns:
            top10 = ws.sort_values("composite_score", ascending=False).head(10)
        else:
            log.warning("whitespace parquet missing composite_score; "
                        "rendering unsorted top-10")
            top10 = ws.head(10)

        top10_rows = []
        for ticker in top10.index:
            r = top10.loc[ticker]
            top10_rows.append({
                "ticker": ticker,
                "sector": _safe_get(r, "sector", "—") or "—",
                "mcap": _fmt_mcap(_safe_get(r, "market_cap")),
                "rvol": _fmt_num(_safe_get(r, "rvol_90d")),
                "ret_1m": _fmt_pct(_safe_get(r, "ret_1m")),
                "ret_1y": _fmt_pct(_safe_get(r, "ret_1y")),
                "mentions": int(_safe_get(r, "mentions_24h", 0) or 0),
                "themes": _safe_get(r, "themes", "") or "",
                "score": f"{(_safe_get(r, 'composite_score', 0) or 0):+.2f}",
                "is_thematic": bool(_safe_get(r, "is_thematic", 0)),
            })

        return templates.TemplateResponse("strategy/home.html", {
            "request": request,
            "n_universe": n_universe,
            "n_thematic": n_thematic,
            "n_high_mentions": n_high_mentions,
            "data_age_hours": f"{data_age_hours:.1f}",
            "data_source": "whitespace_v4" if WS_PARQUET.exists() else "whitespace_v1",
            "top10": top10_rows,
        })
    except Exception as exc:
        log.exception("/strategy crashed")
        return _empty_response(
            request,
            f"Strategy dashboard temporarily unavailable: {type(exc).__name__}. "
            f"See server logs for details.",
        )


@router.get("/strategy/whitespace", response_class=HTMLResponse)
def strategy_whitespace(
    request: Request,
    sector: str | None = Query(None),
    min_mcap: float | None = Query(None, description="Min market cap in $M"),
    require_mentions: bool = Query(False),
    require_theme: bool = Query(False),
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=10, le=200),
):
    try:
        ws = _load_whitespace()
        if ws.empty:
            return _empty_response(request, "No whitespace data. Run /li-report first.")

        # Filters — only apply when the underlying column exists
        filtered = ws.copy()
        if sector and "sector" in filtered.columns:
            filtered = filtered[filtered["sector"] == sector]
        if min_mcap is not None and "market_cap" in filtered.columns:
            filtered = filtered[filtered["market_cap"] >= min_mcap]
        if require_mentions and "mentions_24h" in filtered.columns:
            filtered = filtered[filtered["mentions_24h"] > 0]
        if require_theme and "is_thematic" in filtered.columns:
            filtered = filtered[filtered["is_thematic"] == 1]

        if "composite_score" in filtered.columns:
            filtered = filtered.sort_values("composite_score", ascending=False)
        else:
            log.warning("whitespace parquet missing composite_score; rendering unsorted")

        total = len(filtered)
        start = (page - 1) * per_page
        end = start + per_page
        page_rows = filtered.iloc[start:end]

        rows = []
        for ticker in page_rows.index:
            r = page_rows.loc[ticker]
            rows.append({
                "ticker": ticker,
                "sector": _safe_get(r, "sector", "—") or "—",
                "mcap": _fmt_mcap(_safe_get(r, "market_cap")),
                "mcap_raw": _safe_get(r, "market_cap", 0) or 0,
                "rvol_90d": _fmt_num(_safe_get(r, "rvol_90d")),
                "ret_1m": _fmt_pct(_safe_get(r, "ret_1m")),
                "ret_1y": _fmt_pct(_safe_get(r, "ret_1y")),
                "total_oi": _fmt_num(_safe_get(r, "total_oi")),
                "si_ratio": f"{(_safe_get(r, 'si_ratio', 0) or 0):.1f}",
                "inst_own": f"{(_safe_get(r, 'inst_own_pct', 0) or 0):.0f}%",
                "mentions": int(_safe_get(r, "mentions_24h", 0) or 0),
                "themes": _safe_get(r, "themes", "") or "",
                "is_thematic": bool(_safe_get(r, "is_thematic", 0)),
                "score": f"{(_safe_get(r, 'composite_score', 0) or 0):+.2f}",
                "score_pct": int(_safe_get(r, "score_pct", 0) or 0),
            })

        # Sector dropdown — only build if column exists
        if "sector" in ws.columns:
            sectors = sorted({s for s in ws["sector"].dropna().tolist() if s})
        else:
            sectors = []
        n_pages = (total + per_page - 1) // per_page

        return templates.TemplateResponse("strategy/whitespace.html", {
            "request": request,
            "rows": rows,
            "total": total,
            "page": page,
            "n_pages": n_pages,
            "per_page": per_page,
            "sectors": sectors,
            "current_sector": sector,
            "current_min_mcap": min_mcap,
            "require_mentions": require_mentions,
            "require_theme": require_theme,
        })
    except Exception as exc:
        log.exception("/strategy/whitespace crashed")
        return _empty_response(
            request,
            f"Whitespace table temporarily unavailable: {type(exc).__name__}. "
            f"See server logs for details.",
        )


@router.get("/strategy/ticker/{ticker}", response_class=HTMLResponse)
def strategy_ticker(request: Request, ticker: str):
    """Per-ticker deep-dive. Uses ticker_analyze on demand."""
    ticker = ticker.upper().strip()
    try:
        from screener.li_engine.analysis.ticker_analyze import rank_against_universe
        data = rank_against_universe([ticker])
    except Exception as e:
        log.exception("ticker_analyze failed")
        return templates.TemplateResponse("strategy/empty.html", {
            "request": request,
            "message": f"Error analyzing {ticker}: {e}",
        })

    info = data.get(ticker, {})
    if "error" in info:
        return templates.TemplateResponse("strategy/ticker.html", {
            "request": request,
            "ticker": ticker,
            "error": info["error"],
            "info": None,
        })

    # Format for template
    signals = info.get("signals") or {}
    coverage = info.get("product_coverage") or {}
    score = info.get("score") or {}
    display = {
        "ticker": ticker,
        "sector": signals.get("sector") or "—",
        "market_cap": _fmt_mcap(signals.get("market_cap")),
        "last_price": _fmt_num(signals.get("last_price"), 2),
        "pct_of_52w_high": f"{(info.get('pct_of_52w_high') or 0):.0%}" if info.get("pct_of_52w_high") else "—",
        "ret_1m": _fmt_pct(signals.get("ret_1m")),
        "ret_3m": _fmt_pct(signals.get("ret_3m")),
        "ret_6m": _fmt_pct(signals.get("ret_6m")),
        "ret_1y": _fmt_pct(signals.get("ret_1y")),
        "rvol_30d": _fmt_num(signals.get("rvol_30d")),
        "rvol_90d": _fmt_num(signals.get("rvol_90d")),
        "total_oi": _fmt_num(signals.get("total_oi")),
        "call_oi": _fmt_num(signals.get("call_oi")),
        "put_oi": _fmt_num(signals.get("put_oi")),
        "si_ratio": _fmt_num(signals.get("si_ratio"), 2),
        "insider_pct": f"{signals.get('insider_pct') or 0:.1f}%" if signals.get("insider_pct") else "—",
        "inst_own_pct": f"{signals.get('inst_own_pct') or 0:.0f}%" if signals.get("inst_own_pct") else "—",
        "themes": info.get("themes") or [],
        "mentions_24h": info.get("mentions_24h") or 0,
        "news_sentiment": _fmt_num(signals.get("news_sentiment_bbg"), 2),
    }

    return templates.TemplateResponse("strategy/ticker.html", {
        "request": request,
        "ticker": ticker,
        "info": display,
        "coverage": coverage,
        "score": score,
        "error": None,
    })


@router.get("/strategy/race", response_class=HTMLResponse)
def strategy_race(request: Request):
    try:
        cadence = None
        if CADENCE_PARQUET.exists():
            try:
                _df = pd.read_parquet(CADENCE_PARQUET)
                # Index might be named "issuer" (current) or unnamed (older
                # builds) — reset_index produces "issuer" or "index"; rename
                # to be tolerant of both.
                cadence = _df.reset_index().rename(columns={"index": "issuer"})
            except Exception:
                log.exception("Failed to read cadence parquet at %s", CADENCE_PARQUET)
                cadence = None

        race = None
        if RACE_PARQUET.exists():
            try:
                race = pd.read_parquet(RACE_PARQUET)
            except Exception:
                log.exception("Failed to read race parquet at %s", RACE_PARQUET)
                race = None

        cadence_rows = []
        if cadence is not None and not cadence.empty:
            for _, r in cadence.iterrows():
                cadence_rows.append({
                    "issuer": _safe_get(r, "issuer", "—") or "—",
                    "is_rex": bool(_safe_get(r, "is_rex", False)),
                    "n_launches": int(_safe_get(r, "n_launches", 0) or 0),
                    "median_days": int(_safe_get(r, "median_days", 0) or 0),
                    "min_days": int(_safe_get(r, "min_days", 0) or 0),
                    "max_days": int(_safe_get(r, "max_days", 0) or 0),
                })

        race_rows = []
        if race is not None and not race.empty and "days_until_launch" in race.columns:
            upcoming = race[race["days_until_launch"] > 0].copy()
            for _, r in upcoming.head(30).iterrows():
                days = int(_safe_get(r, "days_until_launch", 0) or 0)
                urgency = "urgent" if days < 30 else "normal"
                fdate = _safe_get(r, "filing_date")
                pdate = _safe_get(r, "projected_launch")
                registrant = _safe_get(r, "registrant", "") or "—"
                race_rows.append({
                    "filing_date": str(fdate.date()) if fdate is not None and hasattr(fdate, "date") else "—",
                    "underlier": _safe_get(r, "underlier", "—") or "—",
                    "registrant": registrant[:40] if isinstance(registrant, str) else "—",
                    "projected_launch": str(pdate.date()) if pdate is not None and hasattr(pdate, "date") else "—",
                    "days_until_launch": days,
                    "rex_has_reacted": bool(_safe_get(r, "rex_has_reacted", False)),
                    "urgency": urgency,
                })

        return templates.TemplateResponse("strategy/race.html", {
            "request": request,
            "cadence": cadence_rows,
            "race": race_rows,
            "race_available": race is not None and not race.empty,
        })
    except Exception as exc:
        log.exception("/strategy/race crashed")
        return _empty_response(
            request,
            f"Filing race view temporarily unavailable: {type(exc).__name__}. "
            f"See server logs for details.",
        )
