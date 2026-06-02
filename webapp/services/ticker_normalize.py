"""Single source of truth for underlier / ticker normalization.

Canonical underlier form (Sprint 1, 2026-06-02):
    ROOT + EXCHANGE — drop only the trailing asset-class word.

The trailing asset-class word is one of Bloomberg's yellow-key suffixes:
``Equity``, ``Curncy``, ``Comdty``, ``Index``, ``Govt``, ``Corp``, ``Mtge``.
Everything else — including the 2-letter exchange code (``US``, ``LN``,
``KS``, ``CN``, ``UN``, etc.) — is PRESERVED.

This preserves the meaningful distinction between cross-listed names::

    FEPI US != FEPI LN

Examples::

    NVDA              -> NVDA
    NVDA US           -> NVDA US
    NVDA US Equity    -> NVDA US
    FEPI LN           -> FEPI LN
    FEPI US           -> FEPI US
    005930 KS         -> 005930 KS
    BTC Curncy        -> BTC

Idempotent: ``normalize_underlier(normalize_underlier(x)) == normalize_underlier(x)``.

Notes
-----
A previous version of this module also collapsed the exchange code
(``NVDA US`` -> ``NVDA``) and mapped crypto shorthand (``BTC`` ->
``XBTUSD``). Those behaviors were removed in Sprint 1 because they
silently merged distinct securities. Callers that still need the old
``strip-everything`` behavior should use :func:`normalize_ticker`.
"""
from __future__ import annotations

# Bloomberg yellow-key asset-class suffixes. These are the ONLY tokens
# that ``normalize_underlier`` strips. Order: longest first to avoid
# partial matches.
_ASSET_CLASS_SUFFIXES = (
    "EQUITY",
    "CURNCY",
    "COMDTY",
    "INDEX",
    "GOVT",
    "CORP",
    "MTGE",
)


def normalize_underlier(raw: str | None) -> str:
    """Return the canonical underlier ticker.

    Drops only the trailing asset-class word (Equity/Curncy/Comdty/
    Index/Govt/Corp/Mtge); preserves the exchange code. See module
    docstring for examples.
    """
    if not raw:
        return ""
    s = str(raw).strip()
    if not s:
        return ""

    # Tokenize on whitespace, uppercase each token, then check whether
    # the last token is an asset-class suffix and drop it if so.
    tokens = s.split()
    if not tokens:
        return ""
    tokens = [t.upper() for t in tokens]
    if len(tokens) > 1 and tokens[-1] in _ASSET_CLASS_SUFFIXES:
        tokens = tokens[:-1]
    return " ".join(tokens)


def normalize_ticker(raw: str | None) -> str:
    """Return the bare ticker root (no exchange, no asset class).

    Strips ALL whitespace-delimited trailing tokens and returns the
    first token uppercased. Useful where you want the raw symbol
    without any Bloomberg qualifiers — e.g. ``NVDA US Equity`` ->
    ``NVDA``.
    """
    if not raw:
        return ""
    s = str(raw).strip()
    if not s:
        return ""
    return s.split()[0].upper()
