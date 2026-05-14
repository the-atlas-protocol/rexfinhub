"""Canonical URL registry — single source of truth for v3 URLs.

Templates and tests should reference URLs by name through this registry
to prevent drift after future renames. Use `url(name, **params)` to resolve.

Example:
    {{ url('funds.detail', ticker=row.ticker) }}    # in Jinja2 template
    URL.funds.detail.format(ticker='NVDX')           # in Python
"""
from __future__ import annotations
from typing import Any

ROUTES: dict[str, str] = {
    # Top-level
    "home": "/",
    "data": "/downloads/",

    # REX Operations pillar
    "operations.products": "/operations/products",
    "operations.pipeline": "/operations/pipeline",
    "operations.calendar": "/operations/calendar",

    # Market Intelligence pillar
    "market.rex": "/market/rex",
    "market.category": "/market/category",
    "market.issuer": "/market/issuer",
    "market.underlier": "/market/underlier",
    "market.stocks": "/market/stocks/",

    # SEC Intelligence pillar
    "sec.etp.dashboard": "/sec/etp/",
    "sec.etp.filings": "/sec/etp/filings",
    "sec.etp.leverageandinverse": "/sec/etp/leverageandinverse",
    "sec.notes.dashboard": "/sec/notes/",
    "sec.notes.filings": "/sec/notes/filings",

    # 13F Intel — admin-only, internal product + sales intelligence
    "intel.home": "/intel/",
    "intel.rex": "/intel/rex",
    "intel.rex_filers": "/intel/rex/filers",
    "intel.rex_performance": "/intel/rex/performance",
    "intel.rex_sales": "/intel/rex/sales",
    "intel.competitors": "/intel/competitors",
    "intel.competitors_new_filers": "/intel/competitors/new-filers",
    "intel.products": "/intel/products",
    "intel.head_to_head": "/intel/head-to-head",
    "intel.country": "/intel/country",
    "intel.asia": "/intel/asia",
    "intel.trends": "/intel/trends",
    "holdings.index": "/holdings/",
    "holdings.fund": "/holdings/fund/{ticker}",
    "holdings.institution": "/holdings/{cik}",
    "holdings.institution_history": "/holdings/{cik}/history",
    "holdings.crossover": "/holdings/crossover",

    # Tools pillar
    "tools.compare.etps": "/tools/compare/etps",
    "tools.compare.filings": "/tools/compare/filings",
    "tools.compare.notes": "/tools/compare/notes",
    "tools.li.candidates": "/tools/li/candidates",
    "tools.simulators.autocall": "/tools/simulators/autocall",
    "tools.tickers": "/tools/tickers",
    "tools.calendar": "/tools/calendar",

    # Detail surfaces (at root)
    "funds.index": "/funds/",
    "funds.detail": "/funds/{ticker}",
    "funds.series": "/funds/series/{series_id}",
    "issuers.index": "/issuers/",
    "issuers.detail": "/issuers/{name}",
    "stocks.detail": "/stocks/{ticker}",
    "trusts.index": "/trusts/",
    "trusts.detail": "/trusts/{slug}",
    "filings.detail": "/filings/{filing_id}",
}


def url(_route: str, **kwargs: Any) -> str:
    """Resolve a named route to its URL with parameters substituted.

    First arg is `_route` (underscore prefix) so it doesn't collide with
    path parameters like `/issuers/{name}` when called as
    url('issuers.detail', name='BlackRock').
    """
    if _route not in ROUTES:
        raise KeyError(f"Unknown route name: {_route!r}. Add it to ROUTES in webapp/routes.py")
    template = ROUTES[_route]
    return template.format(**kwargs) if kwargs else template
