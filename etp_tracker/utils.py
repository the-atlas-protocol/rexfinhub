from __future__ import annotations
import re
import pandas as pd

_SPACE_RE  = re.compile(r"\s+")
_PARENS_RE = re.compile(r"[()]")
_TRADEMARKS = {"\u2122": "TM"}

def safe_str(x) -> str:
    return x if isinstance(x, str) else ""

def is_html_doc(url: str) -> bool:
    u = (url or "").split("?", 1)[0].strip().lower()
    return u.endswith((".htm", ".html"))

def is_pdf_doc(url: str) -> bool:
    u = (url or "").split("?", 1)[0].strip().lower()
    return u.endswith(".pdf")

def now_ts() -> str:
    return pd.Timestamp.utcnow().strftime("%Y-%m-%d %H:%M:%S")

def slugify_name(s: str) -> str:
    s2 = re.sub(r"[^\w\-\s&]", " ", s or "")
    s2 = re.sub(r"\s+", " ", s2).strip()
    return s2

def normalize_spacing(s: str) -> str:
    return _SPACE_RE.sub(" ", s or "").strip()

def clean_fund_name_for_rollup(raw: str) -> str:
    if not isinstance(raw, str):
        return ""
    s = raw
    for k, v in _TRADEMARKS.items():
        s = s.replace(k, v)
    s = s.replace("- Osprey", "-Osprey")
    s = s.replace("+Staking", "+ Staking")
    s = _PARENS_RE.sub("", s)
    s = normalize_spacing(s)
    return s

def titlecase_safe(s: str) -> str:
    return s.title() if isinstance(s, str) else ""

def date_plus_days(iso: str, days: int) -> str:
    try:
        dt = pd.to_datetime(iso, errors="coerce")
        if pd.isna(dt):
            return ""
        return (dt + pd.Timedelta(days=days)).strftime("%Y-%m-%d")
    except Exception:
        return ""

def is_prospectus_form(form: str) -> bool:
    from .config import PROSPECTUS_EXACT, PROSPECTUS_PREFIXES
    f = (form or "").upper().strip()
    return (f in PROSPECTUS_EXACT) or f.startswith(PROSPECTUS_PREFIXES)

def norm_key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().casefold().replace("\u2019","'").replace("\u2013","-").replace("\u2014","-")
