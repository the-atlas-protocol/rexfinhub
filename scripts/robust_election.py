"""Robust 485 cover-page effectiveness-election parser (entity-aware).

step3._parse_485a_election does a lossy glyph->[X] substitution that, on filings
whose font styling injects a Wingdings PUA char between every character, marks the
ENTIRE cover page as checked. This parser reads the document's RAW checkbox entities
and only accepts a clause whose IMMEDIATELY-PRECEDING box is genuinely CHECKED:
    &#9744; / U+2610 (empty ballot)            -> UNCHECKED
    &#9745; / &#9746; / U+2611 / U+2612 / ' X '-> CHECKED
Returns ('YYYY-MM-DD', source) or ('', reason). The 60/75-day rules ARE the document's
own elected designation (filing_date + N), not an outside estimate.
"""
from __future__ import annotations
import re
import pandas as pd

CHK = "\x01CHK\x01"
UNC = "\x01UNC\x01"

_CLAUSES = [
    (re.compile(r"on\s+(?:or\s+about\s+)?([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4})\s+pursuant\s+to\s+paragraph\s*\(b\)", re.I), "date", None),
    (re.compile(r"on\s+(?:or\s+about\s+)?([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4})\s+pursuant\s+to\s+paragraph\s*\(a\)\s*\(2\)", re.I), "date", None),
    (re.compile(r"on\s+(?:or\s+about\s+)?([A-Za-z]+\.?\s+\d{1,2},?\s+\d{4})\s+pursuant\s+to\s+paragraph\s*\(a\)\s*\(1\)", re.I), "date", None),
    (re.compile(r"immediately\s+upon\s+filing", re.I), "days", 0),
    (re.compile(r"60\s*days?\s+after\s+filing", re.I), "days", 60),
    (re.compile(r"75\s*days?\s+after\s+filing", re.I), "days", 75),
]


def _parse_date(s: str):
    s = s.strip().replace(",", "").replace(".", "")
    for f in ("%B %d %Y", "%b %d %Y"):
        try:
            dt = pd.to_datetime(s, format=f)
            if not pd.isna(dt):
                return dt.strftime("%Y-%m-%d")
        except Exception:
            continue
    try:
        dt = pd.to_datetime(s, errors="coerce")
        return None if pd.isna(dt) else dt.strftime("%Y-%m-%d")
    except Exception:
        return None


def parse_election_robust(txt: str, filing_date) -> tuple[str, str]:
    if not isinstance(txt, str) or not txt.strip() or filing_date is None:
        return "", "no-input"
    try:
        fdt = pd.to_datetime(str(filing_date))
    except Exception:
        return "", "bad-filing-date"

    low = txt.lower()
    a = low.find("will become effective")
    if a < 0:
        a = low.find("become effective")
    if a < 0:
        return "", "no-anchor"
    win = txt[a:a + 6000]

    s = re.sub(r"<[^>]+>", " ", win)
    s = s.replace("&nbsp;", " ").replace("&#160;", " ")
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"&#9745;|&#9746;|☑|☒|✓|✔", " " + CHK + " ", s)
    s = re.sub(r"&#9744;|☐", " " + UNC + " ", s)

    best = None  # (clause_start, kind, value)
    for rx, kind, days in _CLAUSES:
        for m in rx.finditer(s):
            # Marker is whatever sits in the SHORT gap immediately before the clause
            # (after the previous clause's text). Keeps a prior [UNC] from bleeding in.
            gap = s[max(0, m.start() - 14):m.start()]
            has_chk = (CHK in gap) or (re.search(r"(?:^|\s)X\s*$", gap) is not None)
            has_unc = UNC in gap
            checked = has_chk and not (has_unc and CHK not in gap)
            if not checked:
                continue
            if kind == "date":
                d = _parse_date(m.group(1))
                if d and (best is None or m.start() < best[0]):
                    best = (m.start(), "date", d)
            else:
                d = (fdt + pd.Timedelta(days=days)).strftime("%Y-%m-%d")
                if best is None or m.start() < best[0]:
                    best = (m.start(), "days", d)
    if best:
        return best[2], ("ELECTION-DATE" if best[1] == "date" else "ELECTION")
    return "", "no-checked-election"
