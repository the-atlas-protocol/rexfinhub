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
    # 13F placeholders (Coming Soon)
    "sec.13f.rex_report": "/sec/13f/rex-report",
    "sec.13f.market_report": "/sec/13f/market-report",
    "sec.13f.institutions": "/sec/13f/institutions",
    "sec.13f.country": "/sec/13f/country",

    # Tools pillar
    "tools.compare.etps": "/tools/compare/etps",
    "tools.compare.filings": "/tools/compare/filings",
    "tools.compare.notes": "/tools/compare/notes",
    "tools.compare.13f_inst": "/tools/compare/13f-inst",
    "tools.compare.13f_products": "/tools/compare/13f-products",
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


def url(name: str, **kwargs: Any) -> str:
    """Resolve a named route to its URL with parameters substituted."""
    if name not in ROUTES:
        raise KeyError(f"Unknown route name: {name!r}. Add it to ROUTES in webapp/routes.py")
    template = ROUTES[name]
    return template.format(**kwargs) if kwargs else template
