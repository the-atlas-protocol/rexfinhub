"""Autocallable note simulator — page render + bootstrap data endpoint.

Phase 1 of the v3 URL migration: the handler implementations have been
renamed to ``_*_impl`` and are imported by
``webapp.routers.tools_simulators`` to be mounted under
``/tools/simulators/autocall``. The old ``/notes/tools/autocall*``
routes shrink to 301/307 redirects pointing at the new canonical URLs.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from webapp.database import get_db
from webapp.models import (
    AutocallCrisisPreset,
    AutocallIndexLevel,
    AutocallIndexMetadata,
    AutocallSweepCache,
)
from datetime import date as _date

from webapp.services.autocall_engine import (
    NoteParams,
    load_level_store,
    suggest_coupon as _suggest_coupon_heuristic,
)
from webapp.services.autocall_pricing import price_par_coupon as _bs_price
from webapp.services.autocall_sweep import sweep as run_sweep

router = APIRouter()
templates = Jinja2Templates(directory="webapp/templates")

# Categories selectable as worst-of references on the simulator page.
# 'autocall_product' rows ARE notes themselves and must not be picked as refs.
_REF_CATEGORIES = ("underlying", "strategy_underlying")


def _autocall_page_impl(request: Request):
    """Render the autocallable simulator shell. Data is fetched client-side."""
    return templates.TemplateResponse(
        "notes_autocall.html",
        {"request": request},
    )


def _autocall_data_impl(db: Session = Depends(get_db)):
    """Bootstrap JSON: metadata, presets, and level history per ticker."""
    metadata_rows = db.execute(
        select(AutocallIndexMetadata)
        .where(AutocallIndexMetadata.category != "autocall_product")
        .order_by(AutocallIndexMetadata.sort_order.asc())
    ).scalars().all()

    metadata = [
        {
            "ticker": m.ticker,
            "full_name": m.full_name,
            "short_name": m.short_name,
            "category": m.category,
            "sort_order": m.sort_order,
        }
        for m in metadata_rows
    ]

    preset_rows = db.execute(
        select(AutocallCrisisPreset).order_by(AutocallCrisisPreset.sort_order.asc())
    ).scalars().all()

    presets = [
        {
            "name": p.name,
            "start_date": p.start_date.isoformat(),
            "sort_order": p.sort_order,
        }
        for p in preset_rows
    ]

    ref_tickers = [
        m["ticker"] for m in metadata if m["category"] in _REF_CATEGORIES
    ]

    tickers: dict[str, dict[str, list]] = {t: {"dates": [], "levels": []} for t in ref_tickers}
    max_date_str = ""

    if ref_tickers:
        level_rows = db.execute(
            select(
                AutocallIndexLevel.ticker,
                AutocallIndexLevel.date,
                AutocallIndexLevel.level,
            )
            .where(AutocallIndexLevel.ticker.in_(ref_tickers))
            .order_by(AutocallIndexLevel.ticker.asc(), AutocallIndexLevel.date.asc())
        ).all()

        max_date = None
        for ticker, dt, level in level_rows:
            bucket = tickers[ticker]
            bucket["dates"].append(dt.isoformat())
            bucket["levels"].append(float(level))
            if max_date is None or dt > max_date:
                max_date = dt

        if max_date is not None:
            max_date_str = max_date.isoformat()

    payload = {
        "max_date": max_date_str,
        "metadata": metadata,
        "presets": presets,
        "tickers": tickers,
    }

    return JSONResponse(
        content=payload,
        headers={"Cache-Control": "public, max-age=3600"},
    )


def _hash_params(refs: list[str], params_dict: dict, coupon_mode: str) -> str:
    payload = {"refs": sorted(refs), "params": params_dict, "coupon_mode": coupon_mode}
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _autocall_sweep_impl(
    body: dict = Body(...),
    db: Session = Depends(get_db),
):
    """Distribution sweep across every valid issue date for a product spec.

    Body: {"refs": [...], "params": {...}, "coupon_mode": "manual"|"suggested"}.
    Returns the sweep payload plus a top-level `cached` flag.
    """
    refs_raw = body.get("refs")
    params_raw = body.get("params")
    coupon_mode = body.get("coupon_mode", "manual")
    if coupon_mode not in ("manual", "suggested"):
        raise HTTPException(status_code=400, detail="coupon_mode must be 'manual' or 'suggested'.")
    if not isinstance(refs_raw, list) or not isinstance(params_raw, dict):
        raise HTTPException(status_code=400, detail="Body must include 'refs' (list) and 'params' (object).")
    refs = [str(r) for r in refs_raw]
    if len(refs) < 1 or len(refs) > 5:
        raise HTTPException(status_code=400, detail="refs must contain 1 to 5 tickers.")

    try:
        params = NoteParams(**params_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid params: {exc}") from exc

    params_dict = {
        "tenor_months": params.tenor_months,
        "obs_freq_months": params.obs_freq_months,
        "coupon_rate_pa_pct": params.coupon_rate_pa_pct,
        "coupon_barrier_pct": params.coupon_barrier_pct,
        "ac_barrier_pct": params.ac_barrier_pct,
        "protection_barrier_pct": params.protection_barrier_pct,
        "memory": params.memory,
        "no_call_months": params.no_call_months,
    }
    h = _hash_params(refs, params_dict, coupon_mode)
    hit = db.query(AutocallSweepCache).filter_by(params_hash=h).first()
    if hit is not None:
        try:
            payload = json.loads(hit.payload_json)
        except (TypeError, ValueError):
            payload = None
        if payload is not None:
            payload["cached"] = True
            return payload

    store = load_level_store(db)
    missing = [r for r in refs if not store._dates.get(r)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown ref ticker(s): {', '.join(missing)}",
        )

    try:
        payload = run_sweep(refs, params, store, coupon_mode=coupon_mode)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.add(AutocallSweepCache(
        params_hash=h,
        payload_json=json.dumps(payload, default=str),
        computed_at=datetime.utcnow(),
    ))
    db.commit()

    payload["cached"] = False
    return payload


def _autocall_suggest_coupon_impl(
    body: dict = Body(...),
    db: Session = Depends(get_db),
):
    """Return a suggested annualized coupon for a single (refs, issue_date, params)
    scenario. Heuristic only — see disclaimer in the page."""
    refs_raw = body.get("refs")
    params_raw = body.get("params")
    issue_iso = body.get("issue_date")
    if not isinstance(refs_raw, list) or not isinstance(params_raw, dict) or not isinstance(issue_iso, str):
        raise HTTPException(status_code=400, detail="Body needs refs (list), params (object), issue_date (str).")
    refs = [str(r) for r in refs_raw]
    if not 1 <= len(refs) <= 5:
        raise HTTPException(status_code=400, detail="refs must be 1 to 5 tickers.")
    try:
        params = NoteParams(**params_raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=f"Invalid params: {exc}") from exc
    try:
        y, m, d = issue_iso.split("-")
        issue_d = _date(int(y), int(m), int(d))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail="issue_date must be YYYY-MM-DD")

    store = load_level_store(db, tickers=refs)
    # Try BS Monte Carlo first; fall back to vol-based heuristic.
    try:
        bs = _bs_price(refs, issue_d, params, store)
    except Exception as exc:
        bs = {"method": "fallback", "coupon_pa_pct": None, "error": str(exc)}
    if bs.get("method") == "mc" and bs.get("coupon_pa_pct") is not None:
        return {
            "coupon_pa_pct": bs["coupon_pa_pct"],
            "method": "bs_mc",
            "realized_vol": bs.get("realized_vol"),
            "correlation": bs.get("correlation"),
            "n_paths": bs.get("n_paths"),
        }
    sc = _suggest_coupon_heuristic(refs, issue_d, params, store)
    if sc is None:
        return {
            "coupon_pa_pct": None,
            "method": "none",
            "reason": "Insufficient history (need ~1y of returns).",
        }
    return {"coupon_pa_pct": sc, "method": "heuristic"}


# ---------------------------------------------------------------------------
# Phase 1 legacy redirects (old URL → new canonical URL).
# GET → 301 (permanent). POST → 307 (preserve method).
# ---------------------------------------------------------------------------


@router.get("/notes/tools/autocall")
def autocall_page_redirect():
    return RedirectResponse("/tools/simulators/autocall", status_code=301)


@router.get("/notes/tools/autocall/data")
def autocall_data_redirect():
    return RedirectResponse("/tools/simulators/autocall/data", status_code=301)


@router.post("/notes/tools/autocall/sweep")
def autocall_sweep_redirect():
    return RedirectResponse("/tools/simulators/autocall/sweep", status_code=307)


@router.post("/notes/tools/autocall/suggest-coupon")
def autocall_suggest_coupon_redirect():
    return RedirectResponse("/tools/simulators/autocall/suggest-coupon", status_code=307)
