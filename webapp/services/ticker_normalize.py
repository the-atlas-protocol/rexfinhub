"""Single source of truth for underlier / ticker normalization.

Per Ryu 2026-05-13: strip the suffix part. For stocks/ETPs the canonical
form is the bare ticker (up-to-5 alphanumerics). For FX/crypto the canonical
form is the Bloomberg ticker WITHOUT " Curncy" (e.g. ``XETUSD``).

Examples:
  ``SNDK US``     -> ``SNDK``
  ``SNDK``        -> ``SNDK``
  ``XETUSD Curncy`` -> ``XETUSD``
  ``XBTUSD Curncy`` -> ``XBTUSD``
  ``ETH``         -> ``XETUSD`` (crypto shorthand → BBG canonical)
  ``BTC``         -> ``XBTUSD``
  ``Ethereum``    -> ``XETUSD``
  ``Bitcoin``     -> ``XBTUSD``

The shorthand map handles the data drift where REX-Osprey crypto rows use
3-letter symbols (BTC, ETH, SOL) while mkt_master_data uses BBG Curncy form
(XBTUSD Curncy, XETUSD Curncy). Normalizing both to the BBG bare form makes
underlier joins work cleanly.

Add to ``_CRYPTO_SHORTHAND_TO_BBG`` when a new crypto fund appears with a
3-letter underlier that doesn't already resolve.
"""
from __future__ import annotations

# Exchange/quote suffixes Bloomberg appends. Order matters — longer
# prefixes first so " Equity" wins over " Eq" (defensive).
_BBG_SUFFIXES = (
    " Curncy", " Equity", " Index", " Comdty", " Govt", " Corp", " Mtge",
    " US", " UA", " LN", " UN", " CN", " UQ", " UR", " UP", " AU",
)

# Crypto shorthand → Bloomberg canonical ticker.
# Source of truth: mkt_master_data.underlier_name for known REX crypto
# products. Update when a new crypto product surfaces with an unmapped
# shorthand symbol.
_CRYPTO_SHORTHAND_TO_BBG = {
    "BTC": "XBTUSD",
    "BITCOIN": "XBTUSD",
    "ETH": "XETUSD",
    "ETHER": "XETUSD",
    "ETHEREUM": "XETUSD",
    "SOL": "XSOUSD",
    "SOLANA": "XSOUSD",
    "DOGE": "XDGUSD",
    "DOGECOIN": "XDGUSD",
    "XRP": "XRPUSD",
    "BNB": "XBNUSD",
    "ADA": "XADUSD",
    "CARDANO": "XADUSD",
    "AVAX": "XAVUSD",
    "ATOM": "XATUSD",
    "MATIC": "XMTUSD",
    "DOT": "XDTUSD",
    "LINK": "XLKUSD",
    "BCH": "XBCUSD",
    "LTC": "XLTUSD",
}


def normalize_underlier(raw: str | None) -> str:
    """Return the canonical underlier ticker for a raw input.

    Order of operations:
    1. None / empty -> empty string.
    2. Strip whitespace.
    3. Strip a single trailing Bloomberg suffix (` US`, ` Curncy`, etc.).
    4. Uppercase.
    5. Map crypto shorthand to BBG canonical (BTC -> XBTUSD).

    Idempotent: ``normalize_underlier(normalize_underlier(x)) == normalize_underlier(x)``.
    """
    if not raw:
        return ""
    s = str(raw).strip()
    if not s:
        return ""

    # Strip one trailing BBG suffix if present.
    for suf in _BBG_SUFFIXES:
        if s.upper().endswith(suf.upper()):
            s = s[: -len(suf)].rstrip()
            break

    s = s.upper()
    return _CRYPTO_SHORTHAND_TO_BBG.get(s, s)


def normalize_ticker(raw: str | None) -> str:
    """Same as ``normalize_underlier`` but skips the crypto-shorthand map.

    For raw stock/ETP ticker normalization where you want the bare symbol
    but don't want ``BTC`` rewritten to ``XBTUSD``.
    """
    if not raw:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    for suf in _BBG_SUFFIXES:
        if s.upper().endswith(suf.upper()):
            s = s[: -len(suf)].rstrip()
            break
    return s.upper()
