from __future__ import annotations
import re, io
from typing import Tuple, List
try:
    from bs4 import BeautifulSoup
except Exception:
    BeautifulSoup = None
try:
    from lxml import html as lxml_html
except Exception:
    lxml_html = None
try:
    from pdfminer.high_level import extract_text as pdf_extract_text
except Exception:
    pdf_extract_text = None

from .utils import normalize_spacing

# 2026-05-11 audit-fix-R3: tighten ticker validation in body extractors.
# Stage 1 audit found 1,498 rows polluted with ticker "SYM" and 657 with
# "SYMBO" -- truncated "SYMBOL" column headers being captured by the loose
# `[A-Z0-9]{1,6}` regex. The deny-list below covers the SYMBOL truncations
# plus other obvious column-header / English fragments observed in fund_status.
# Forward-only fix: prevents new pollution. Existing pollution is a separate
# Stage-2 dedup task.
_BODY_BAD_TICKERS = {
    # SYMBOL truncations (the original bleed)
    "SYM", "SYMB", "SYMBO", "SYMBOL", "SYMBOLS",
    # TICKER truncations
    "TIC", "TICK", "TICKE", "TICKER", "TICKERS",
    # Other column headers
    "COL", "ROW", "ITEM", "PAGE", "NUM", "TOTAL", "NULL",
    "N/A", "NA", "NONE", "NAN", "TBD", "TBA",
    # English connectives / determiners commonly found in tabular cells
    "ALL", "AND", "OR", "OF", "THE", "FOR", "WITH", "BY",
    # Currency / generic finance abbreviations
    "USD", "EUR", "GBP", "JPY", "CAD", "AUD",
    # Generic fund-related words
    "ETF", "ETN", "FUND", "FUNDS", "TRUST", "CLASS", "SHARE", "SHARES",
    "RISK", "MEMBER", "DAILY", "TARGET", "INC", "LLC", "COM",
    # Header role labels
    "NAME", "NAMES", "FUND",
}

# 2026-05-11: tighter ticker shape. Real US ETF tickers are 2-5 chars and
# overwhelmingly all-letter. We keep numeric-mixed support (length 2-5) but
# disallow the 1- and 6-char extremes that produced most false positives.
# Single-letter "tickers" are share-class labels (Class A/B/I); 6-char strings
# in body text are nearly always SYMBOL-truncations or sub-ticker fragments.
_BODY_TICKER_RX = re.compile(r"^[A-Z][A-Z0-9]{1,4}$")


def _is_valid_body_ticker(tok: str) -> bool:
    """Validate a candidate ticker pulled from body text or table cells.

    Rules (forward-only, conservative):
    - Must match `^[A-Z][A-Z0-9]{1,4}$` (length 2-5, leading letter)
    - Must not appear in `_BODY_BAD_TICKERS`
    - Must contain at least one letter (rejected by the leading-letter rule
      already, but kept explicit for safety)
    """
    if not tok:
        return False
    t = tok.strip().upper()
    if not _BODY_TICKER_RX.fullmatch(t):
        return False
    if t in _BODY_BAD_TICKERS:
        return False
    return True

def textify_html(html_text: str) -> str:
    if BeautifulSoup:
        try:
            soup = BeautifulSoup(html_text, "html.parser")
            return soup.get_text(" ", strip=True)
        except Exception:
            pass
    if lxml_html:
        try:
            doc = lxml_html.fromstring(html_text)
            return doc.text_content()
        except Exception:
            pass
    return html_text or ""

def iter_txt_documents(txt: str):
    """Yield (doctype, filename, body_html) for each <DOCUMENT> with HTML-ish content."""
    for m in re.finditer(r"(?is)<DOCUMENT>(.*?)</DOCUMENT>", txt or ""):
        block = m.group(1)
        def _tag(tag: str) -> str:
            mm = re.search(fr"(?is)<{tag}>\s*(.*?)\s*</{tag}>", block or "")
            return normalize_spacing(mm.group(1)) if mm else ""
        doctype = _tag("TYPE").upper()
        fname   = _tag("FILENAME")
        text    = re.search(r"(?is)<TEXT>(.*?)</TEXT>", block or "")
        if not text: continue
        body = text.group(1)
        if "<html" in body.lower() or "<table" in body.lower() or "<div" in body.lower():
            yield doctype, fname, body

def extract_from_html_string(html_text: str) -> tuple[list[dict], str]:
    rows: list[dict] = []
    plain = textify_html(html_text)
    # Look for tables with 'fund/name' and 'ticker' in the header
    if BeautifulSoup:
        try:
            soup = BeautifulSoup(html_text, "html.parser")
            for tbl in soup.find_all("table"):
                header_text = " ".join(th.get_text(" ", strip=True) for th in tbl.find_all(["th","td"]))
                if re.search(r"(fund|series|name)", header_text, re.I) and re.search(r"ticker", header_text, re.I):
                    for row in tbl.find_all("tr"):
                        cells = [td.get_text(" ", strip=True) for td in row.find_all(["td","th"])]
                        if len(cells) >= 2:
                            tkr = cells[-1].strip().upper()
                            if _is_valid_body_ticker(tkr):
                                rows.append({
                                    "Series ID": "", "Series Name": "",
                                    "Class-Contract ID": "", "Class Contract Name": " ".join(cells[:-1]).strip(),
                                    "Class Symbol": tkr, "Extracted From": "PRIMARY-HTML",
                                })
        except Exception:
            pass
    if not rows:
        for ln in plain.splitlines():
            parts = re.split(r"\s{2,}", ln.strip())
            if len(parts) >= 2:
                tkr = parts[-1].strip().upper()
                if _is_valid_body_ticker(tkr):
                    rows.append({
                        "Series ID": "", "Series Name": "",
                        "Class-Contract ID": "", "Class Contract Name": " ".join(parts[:-1]).strip(),
                        "Class Symbol": tkr, "Extracted From": "PRIMARY-HTML",
                    })
    return rows, plain

def extract_from_primary_html(client, url: str) -> tuple[list[dict], str]:
    rows: list[dict] = []
    if not url: return rows, ""
    try:
        html_text = client.fetch_text(url)
    except Exception:
        return rows, ""
    return extract_from_html_string(html_text)

def extract_from_primary_pdf(client, url: str) -> tuple[list[dict], str]:
    rows: list[dict] = []
    if not url or not pdf_extract_text: return rows, ""
    try:
        data = client.fetch_bytes(url)
        text = pdf_extract_text(io.BytesIO(data))  # type: ignore
    except Exception:
        return rows, ""
    for ln in text.splitlines():
        parts = re.split(r"\s{2,}", ln.strip())
        if len(parts) >= 2:
            tkr = parts[-1].strip().upper()
            if _is_valid_body_ticker(tkr):
                rows.append({
                    "Series ID": "", "Series Name": "",
                    "Class-Contract ID": "", "Class Contract Name": " ".join(parts[:-1]).strip(),
                    "Class Symbol": tkr, "Extracted From": "PRIMARY-PDF",
                })
    return rows, text
