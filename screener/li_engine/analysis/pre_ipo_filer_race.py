"""Pre-IPO filer race — for each pre-IPO target, who's filed leveraged products on it.

Mirrors filing_race.py but matches by company-name LIKE patterns in series_name
since pre-IPO companies have no ticker yet.

Output: dict keyed by company name -> list of unique filer issuers (with REX flag).
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"


# Targets to track. `match_terms` are SQL LIKE patterns (wrapped in %...%).
# `display` is the canonical name surfaced in the report.
PRE_IPO_TARGETS = [
    {"display": "OpenAI",         "match_terms": ["OpenAI"]},
    {"display": "SpaceX",         "match_terms": ["SpaceX", "Space X"]},
    {"display": "Anthropic",      "match_terms": ["Anthropic"]},
    {"display": "Anduril",        "match_terms": ["Anduril"]},
    {"display": "Scale AI",       "match_terms": ["Scale AI"]},
    {"display": "Stripe",         "match_terms": ["Stripe"]},
    {"display": "Databricks",     "match_terms": ["Databricks"]},
    {"display": "xAI",            "match_terms": ["xAI", " xAI "]},
    {"display": "Cerebras",       "match_terms": ["Cerebras"]},
    {"display": "Klarna",         "match_terms": ["Klarna"]},
    {"display": "Discord",        "match_terms": ["Discord"]},
    {"display": "Viva Republica", "match_terms": ["Viva Republica", "Toss"]},
]


# Issuer label normalization — collapse trust-name variants to the brand.
# Matched as case-insensitive substring against `registrant`.
_ISSUER_NORMALIZE = [
    (r"ETF Opportunities|T-?REX",                "REX"),
    (r"GraniteShares",                           "GraniteShares"),
    (r"Direxion",                                "Direxion"),
    (r"Tidal Trust|Defiance",                    "Defiance"),
    (r"Investment Managers Series Trust.*Tradr|Tradr", "Tradr"),
    (r"Kurv",                                    "Kurv"),
    (r"Tuttle Capital|Tuttle",                   "Tuttle"),
    (r"YieldMax|Tidal ETF",                      "YieldMax"),
    (r"Innovator",                               "Innovator"),
    (r"First Trust",                             "First Trust"),
    (r"ProShares",                               "ProShares"),
    (r"Calamos",                                 "Calamos"),
    (r"Roundhill",                               "Roundhill"),
    (r"Bitwise",                                 "Bitwise"),
    (r"Simplify",                                "Simplify"),
]


def _normalize_issuer(registrant: str) -> str:
    if not isinstance(registrant, str):
        return "Unknown"
    for pattern, brand in _ISSUER_NORMALIZE:
        if re.search(pattern, registrant, flags=re.IGNORECASE):
            return brand
    return registrant.split(" Trust")[0].strip() or registrant


def _is_rex_row(registrant: str, series_name: str) -> bool:
    """REX detection: registrant pattern OR series_name starts with T-REX/REX brand."""
    rs = (registrant or "")
    sn = (series_name or "")
    if re.search(r"REX|ETF Opportunities", rs, re.IGNORECASE):
        return True
    if re.match(r"^\s*(T-REX|REX\s)", sn, re.IGNORECASE):
        return True
    return False


def load_pre_ipo_filer_race(targets=None) -> dict[str, dict]:
    """For each pre-IPO target, return per-issuer filer list with REX flag.

    Returns dict shape:
        {
          "OpenAI": {
            "filers": [{"issuer": "REX", "is_rex": True, "n_filings": 1, "latest_date": "2026-03-26"}, ...],
            "total_filings": 15,
            "rex_filed": True,
          },
          ...
        }
    """
    if targets is None:
        targets = PRE_IPO_TARGETS

    if not DB.exists():
        log.warning("etp_tracker.db not found at %s — returning empty filer race", DB)
        return {t["display"]: {"filers": [], "total_filings": 0, "rex_filed": False} for t in targets}

    conn = sqlite3.connect(str(DB))
    out: dict[str, dict] = {}
    try:
        for target in targets:
            terms = target["match_terms"]
            clauses = " OR ".join(["fe.series_name LIKE ?"] * len(terms))
            params = [f"%{t}%" for t in terms]
            df = pd.read_sql_query(
                f"""
                SELECT f.registrant, fe.series_name, f.form, f.filing_date
                FROM fund_extractions fe
                JOIN filings f ON f.id = fe.filing_id
                WHERE {clauses}
                ORDER BY f.filing_date DESC
                """,
                conn,
                params=params,
            )

            if df.empty:
                out[target["display"]] = {"filers": [], "total_filings": 0, "rex_filed": False}
                continue

            df["issuer"] = df["registrant"].apply(_normalize_issuer)
            df["is_rex"] = df.apply(
                lambda r: _is_rex_row(r["registrant"], r["series_name"]), axis=1
            )
            # Override issuer to REX when row is REX-tagged
            df.loc[df["is_rex"], "issuer"] = "REX"

            grp = (
                df.groupby("issuer")
                .agg(
                    n_filings=("form", "count"),
                    latest_date=("filing_date", "max"),
                    is_rex=("is_rex", "max"),
                )
                .reset_index()
                .sort_values(["is_rex", "n_filings"], ascending=[False, False])
            )

            out[target["display"]] = {
                "filers": grp.to_dict("records"),
                "total_filings": int(len(df)),
                "rex_filed": bool(grp["is_rex"].any()),
            }
    finally:
        conn.close()
    return out


def render_filers_pills(filers: list[dict], max_visible: int = 3) -> str:
    """Render an HTML cell of issuer pills for the IPO Watchlist 'Filed By' column.

    REX in green, competitors in grey, '+N more' overflow pill if needed.
    """
    if not filers:
        return '<span style="font-size:10px;color:#7f8c8d;font-style:italic;">open lane</span>'

    pills = []
    rex_pill = '<span style="background:#27ae60;color:white;padding:2px 6px;border-radius:3px;font-size:9px;font-weight:700;margin-right:3px;display:inline-block;">REX</span>'
    grey_pill = (
        '<span style="background:#ecf0f1;color:#2c3e50;padding:2px 6px;'
        'border-radius:3px;font-size:9px;font-weight:600;margin-right:3px;'
        'display:inline-block;">{label}</span>'
    )

    visible_count = 0
    for f in filers:
        if visible_count >= max_visible:
            break
        if f["is_rex"]:
            pills.append(rex_pill)
        else:
            pills.append(grey_pill.format(label=f["issuer"]))
        visible_count += 1

    overflow = len(filers) - visible_count
    if overflow > 0:
        pills.append(grey_pill.format(label=f"+{overflow}"))

    return "".join(pills)


def main():
    """CLI sanity-check: print the filer race for all pre-IPO targets."""
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    race = load_pre_ipo_filer_race()
    print(f"{'Target':<20} {'Filings':>8} {'REX?':>6}  Issuers")
    print("-" * 100)
    for company, data in race.items():
        rex_flag = "YES" if data["rex_filed"] else "no"
        issuers = ", ".join(
            f"{f['issuer']}({f['n_filings']})" for f in data["filers"][:6]
        ) or "—"
        print(f"{company:<20} {data['total_filings']:>8}   {rex_flag:>4}   {issuers}")


if __name__ == "__main__":
    main()
