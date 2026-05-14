"""Underlier race view — pipeline-focused aggregator.

GET /operations/pipeline/underlier/{underlier}

Renders a lifecycle-race comparison for a single underlier (e.g. NVDA):

  Filed                 |  Effective                 |  Listed
  -------------------------------------------------------------
  REX rows              |  REX rows                  |  REX rows
  Competitor rows       |  Competitor rows           |  Competitor rows

Distinct from:
  * /market/underlier         — Bloomberg AUM / yield / flows aggregator
                                (does NOT split by lifecycle stage; has the
                                NVDA vs "NVDA US" bifurcation bug)
  * /stocks/{ticker}          — single-stock signal page (whitespace_v4 +
                                covered ETPs; also bifurcated)
  * /funds/{ticker}           — single-fund detail (only shows competitors of
                                that ONE fund, not the broader underlier)
  * /intel/head-to-head       — 13F-holdings race (institutional ownership,
                                not lifecycle / launch-race timing)

Owner: rexops-O6 worktree. See docs/rex_ops_2026-05-12/O6_underlier.md.

Bifurcation fix
---------------
`mkt_master_data.map_li_underlier` is stored as ``'NVDA US'`` for 16 of 17
NVDA rows and bare ``'NVDA'`` for 1, per
docs/audit_2026-05-11/01_webapp_consistency.md (issues F1/F2). REX
``rex_products.underlier`` is stored bare (``'NVDA'``). This route MATCHES
BOTH FORMATS by stripping the trailing `` US`` / `` Curncy`` suffix on the
mkt-master side at query time and comparing case-insensitively against the
URL token. The URL is always the bare underlier (``/underlier/NVDA``); the
query handles both DB representations.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pathlib import Path
from sqlalchemy import func, or_, text as sa_text
from sqlalchemy.orm import Session

from webapp.dependencies import get_db
from webapp.models import RexProduct

log = logging.getLogger(__name__)

router = APIRouter(tags=["operations"])
templates = Jinja2Templates(
    directory=str(Path(__file__).parent.parent / "templates")
)


# Lifecycle buckets — three columns shown to the PM in race view.
#
# These mirror the ``valid_statuses`` enum surfaced on the main pipeline
# page (`pipeline_products.html`) so a row that drops out of one column
# always appears in another. Counsel/Board statuses bucket under "filed"
# only when the product has already been filed with the SEC; pure pre-
# filing statuses (Research, Target List) are excluded from the race view
# because the race itself is about who got to market first.
FILED_STATUSES = [
    "Filed",
    "Filed (485A)",
    "Filed (485B)",
    "Awaiting Effective",
]
EFFECTIVE_STATUSES = ["Effective"]
LISTED_STATUSES = ["Listed", "Delisted"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalize(token: str | None) -> str:
    """URL token -> canonical comparison key (uppercase, no `` US``)."""
    if not token:
        return ""
    t = str(token).strip().upper()
    # Strip Bloomberg suffixes.
    for suffix in (" US", " CURNCY", " EQUITY", " INDEX"):
        if t.endswith(suffix):
            t = t[: -len(suffix)].strip()
    return t


def _bare_sql(col_expr: str) -> str:
    """SQL expression that strips Bloomberg suffixes from a column.

    Used in raw-SQL queries against mkt_master_data so a URL token of
    ``NVDA`` matches both ``NVDA`` and ``NVDA US`` (the F1/F2 bifurcation
    bug from the May 2026 audit).
    """
    return (
        f"UPPER(TRIM(REPLACE(REPLACE(REPLACE(REPLACE("
        f"{col_expr}, ' US',''), ' Curncy',''), ' Equity',''), ' Index','')))"
    )


def _rex_products_for_underlier(db: Session, key: str) -> list[RexProduct]:
    """REX pipeline rows whose underlier matches ``key`` (case-insensitive,
    suffix-stripped)."""
    # Use func.upper + replace for portability across SQLite. RexProduct
    # underliers are stored bare almost always, but cheap to normalize.
    stmt = (
        db.query(RexProduct)
        .filter(RexProduct.underlier.isnot(None))
        .filter(
            func.upper(
                func.trim(
                    func.replace(
                        func.replace(RexProduct.underlier, " US", ""),
                        " Curncy",
                        "",
                    )
                )
            )
            == key
        )
        .order_by(RexProduct.status, RexProduct.initial_filing_date.asc())
    )
    return stmt.all()


def _competitor_rows(db: Session, key: str) -> list[dict]:
    """Bloomberg-listed competitor (and REX) ETPs covering this underlier.

    Returns flat dicts so the template doesn't need to know about ORM
    objects. ``is_rex`` is the canonical REX-vs-competitor flag.
    """
    sql = sa_text(
        f"""
        SELECT
            ticker, fund_name, issuer_display, issuer,
            aum, map_li_underlier, map_cc_underlier,
            map_li_direction, map_li_leverage_amount,
            inception_date, market_status, is_rex
        FROM mkt_master_data
        WHERE {_bare_sql('map_li_underlier')} = :k
           OR {_bare_sql('map_cc_underlier')} = :k
        ORDER BY COALESCE(is_rex, 0) DESC,
                 aum DESC NULLS LAST
        LIMIT 200
        """
    )
    rows = db.execute(sql, {"k": key}).fetchall()
    out: list[dict] = []
    for r in rows:
        is_li = bool(r.map_li_underlier and str(r.map_li_underlier).strip())
        out.append({
            "ticker": (r.ticker or "").replace(" US", "").strip(),
            "ticker_raw": r.ticker or "",
            "fund_name": r.fund_name or "",
            "issuer": r.issuer_display or r.issuer or "",
            "aum": float(r.aum or 0),
            "leverage": r.map_li_leverage_amount or "",
            "direction": r.map_li_direction or "",
            "coverage_type": "L&I" if is_li else "Covered Call",
            "inception_date": r.inception_date or "",
            "market_status": r.market_status or "",
            "is_rex": bool(r.is_rex),
        })
    return out


def _bucket_rex(rex_rows: list[RexProduct]) -> dict[str, list[RexProduct]]:
    """Split REX rows into filed / effective / listed columns."""
    buckets: dict[str, list[RexProduct]] = {
        "filed": [], "effective": [], "listed": [], "preflight": [],
    }
    for p in rex_rows:
        if p.status in LISTED_STATUSES:
            buckets["listed"].append(p)
        elif p.status in EFFECTIVE_STATUSES:
            buckets["effective"].append(p)
        elif p.status in FILED_STATUSES:
            buckets["filed"].append(p)
        else:
            # Counsel / Board / Research / Target List — pre-filing.
            buckets["preflight"].append(p)
    return buckets


def _race_timeline(
    rex_rows: list[RexProduct], comp_rows: list[dict]
) -> list[dict]:
    """Build a chronological filed-/listed-first event log."""
    events: list[dict] = []
    # Per Ryu 2026-05-13: "REX does not file Tuttle products except T-REX."
    # rex_products contains non-REX rows (Tuttle Capital, GSR, Hedgeye etc.)
    # because the underlying SEC scraper indexes the whole trust universe.
    # Use a name-prefix check to decide the actor label — match the same
    # whitelist that _rex_only_filter on the pipeline page uses.
    _REX_NAME_PREFIXES = ("REX ", "T-REX ", "REX-OSPREY", "REX- OSPREY",
                          "MICROSECTORS")
    def _actor_for(p) -> str:
        nm = (p.name or "").upper()
        if any(nm.startswith(pfx) for pfx in _REX_NAME_PREFIXES):
            return "REX"
        if "TUTTLE" in nm:
            return "Tuttle"
        if nm.startswith("GSR "):
            return "GSR"
        if nm.startswith("HEDGEYE"):
            return "Hedgeye"
        if nm.startswith("DEFIANCE"):
            return "Defiance"
        # Fall back to the trust name (best available attribution).
        return (p.trust or "").split()[0] if p.trust else "Issuer"

    for p in rex_rows:
        actor = _actor_for(p)
        if p.initial_filing_date:
            events.append({
                "date": p.initial_filing_date,
                "kind": "filed",
                "actor": actor,
                "ticker": p.ticker or p.name[:20],
                "label": f"{actor} filed {p.ticker or p.name[:30]}",
            })
        if p.official_listed_date:
            events.append({
                "date": p.official_listed_date,
                "kind": "listed",
                "actor": actor,
                "ticker": p.ticker or p.name[:20],
                "label": f"{actor} listed {p.ticker or p.name[:30]}",
            })
    # Inception dates from mkt_master_data are ISO-ish strings.
    for c in comp_rows:
        if c["is_rex"]:
            continue  # REX events already captured from rex_products
        raw = (c.get("inception_date") or "").strip()
        if not raw:
            continue
        try:
            dt = date.fromisoformat(raw[:10])
        except (ValueError, TypeError):
            continue
        events.append({
            "date": dt,
            "kind": "listed",
            "actor": c["issuer"] or "Competitor",
            "ticker": c["ticker"],
            "label": f"{c['issuer'] or 'Competitor'} listed {c['ticker']}",
        })
    events.sort(key=lambda e: e["date"])
    return events


# ---------------------------------------------------------------------------
# Route
# ---------------------------------------------------------------------------

@router.get(
    "/operations/pipeline/underlier/{underlier}",
    response_class=HTMLResponse,
)
def underlier_race(
    request: Request,
    underlier: str,
    modal: int = Query(default=0),
    db: Session = Depends(get_db),
):
    """Lifecycle-race view for a single underlier.

    ``modal=1`` strips the page chrome so the template can be loaded into
    a side panel via fetch() from pipeline_products.html.
    """
    key = _normalize(underlier)
    if not key:
        return HTMLResponse("Underlier required", status_code=400)

    rex_rows = _rex_products_for_underlier(db, key)
    comp_rows = _competitor_rows(db, key)
    rex_buckets = _bucket_rex(rex_rows)

    # REX vs competitor split of the Bloomberg-listed rows.
    rex_listed = [c for c in comp_rows if c["is_rex"]]
    comp_listed = [c for c in comp_rows if not c["is_rex"]]
    rex_listed.sort(key=lambda r: r["aum"], reverse=True)
    comp_listed.sort(key=lambda r: r["aum"], reverse=True)

    # Headline: who filed/launched first.
    first_filed = None
    first_listed = None
    timeline = _race_timeline(rex_rows, comp_rows)
    for e in timeline:
        if first_filed is None and e["kind"] == "filed":
            first_filed = e
        if first_listed is None and e["kind"] == "listed":
            first_listed = e

    total_aum = sum(c["aum"] for c in comp_rows)
    rex_aum = sum(c["aum"] for c in rex_listed)
    rex_share_pct = (rex_aum / total_aum * 100) if total_aum > 0 else 0.0

    # Audit timeline: last 10 capm_audit_log entries for any REX product on this underlier.
    audit_timeline: list[dict] = []
    try:
        if rex_rows:
            names = [r.name for r in rex_rows if r.name]
            if names:
                ph = ",".join("?" for _ in names)
                raw = db.execute(
                    f"""SELECT changed_at, action, field_name, old_value, new_value, row_label, changed_by
                        FROM capm_audit_log
                        WHERE row_label IN ({ph})
                        ORDER BY changed_at DESC LIMIT 10""",
                    names,
                ).fetchall() if hasattr(db, "execute") else []
                # When db is a SQLAlchemy session, raw SQL needs text() wrapping
                if not raw:
                    from sqlalchemy import text as _text
                    raw = db.execute(
                        _text(
                            f"SELECT changed_at, action, field_name, old_value, new_value, row_label, changed_by "
                            f"FROM capm_audit_log "
                            f"WHERE row_label IN ({ph}) "
                            f"ORDER BY changed_at DESC LIMIT 10"
                        ),
                        names,
                    ).fetchall()
                for r in raw:
                    audit_timeline.append({
                        "changed_at": r[0],
                        "action": r[1],
                        "field": r[2],
                        "old": r[3],
                        "new": r[4],
                        "label": r[5],
                        "by": r[6],
                    })
    except Exception:
        audit_timeline = []

    sister_count = len(rex_rows)

    ctx = {
        "request": request,
        "underlier": key,
        "underlier_url": key,  # URL token (already normalized)
        "rex_buckets": rex_buckets,
        "audit_timeline": audit_timeline,
        "sister_count": sister_count,
        "rex_listed": rex_listed,
        "comp_listed": comp_listed,
        "total_funds": len(comp_rows),
        "total_aum": total_aum,
        "rex_aum": rex_aum,
        "rex_share_pct": round(rex_share_pct, 1),
        "rex_count": len(rex_listed),
        "comp_count": len(comp_listed),
        "first_filed": first_filed,
        "first_listed": first_listed,
        "timeline": timeline[-20:],  # most recent 20 events
        "modal": bool(modal),
        # Related surfaces — the panel links back out to richer views.
        # Per Ryu 2026-05-13: drop the +US variant button — the canonical
        # form has the suffix already stripped, and the route now normalizes
        # both forms via webapp.services.ticker_normalize. Keeping two
        # buttons just confuses the click target.
        "market_underlier_url": f"/market/underlier?type=li&underlier={key}",
        "stocks_url": f"/stocks/{key}",
        "head_to_head_url": f"/intel/head-to-head?underlying={key}",
    }
    # In modal mode return the bare body partial (no base.html chrome).
    # In page mode return the wrapper that extends base.html. This split
    # is required because Jinja can't conditionally `extends` a parent.
    tmpl = (
        "operations/_underlier_race_body.html"
        if ctx["modal"]
        else "operations/underlier_race.html"
    )
    return templates.TemplateResponse(tmpl, ctx)


# ---------------------------------------------------------------------------
# JSON API — useful for the inline panel and future programmatic callers.
# ---------------------------------------------------------------------------

@router.get("/api/operations/underlier/{underlier}.json")
def underlier_race_json(
    underlier: str,
    db: Session = Depends(get_db),
):
    from fastapi.responses import JSONResponse

    key = _normalize(underlier)
    if not key:
        return JSONResponse({"error": "underlier required"}, status_code=400)

    rex_rows = _rex_products_for_underlier(db, key)
    comp_rows = _competitor_rows(db, key)
    buckets = _bucket_rex(rex_rows)

    def _ser_rex(p: RexProduct) -> dict:
        return {
            "id": p.id,
            "ticker": p.ticker,
            "name": p.name,
            "suite": p.product_suite,
            "status": p.status,
            "underlier": p.underlier,
            "filed": p.initial_filing_date.isoformat() if p.initial_filing_date else None,
            "effective": p.estimated_effective_date.isoformat() if p.estimated_effective_date else None,
            "listed": p.official_listed_date.isoformat() if p.official_listed_date else None,
        }

    return {
        "underlier": key,
        "rex": {k: [_ser_rex(p) for p in v] for k, v in buckets.items()},
        "competitors": [c for c in comp_rows if not c["is_rex"]],
        "rex_listed": [c for c in comp_rows if c["is_rex"]],
    }
