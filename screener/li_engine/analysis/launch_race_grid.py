"""Launch-race grid — REX pre-launch pipeline vs. competitor filings on the same underlier.

One row per REX product in status IN ('Filed','Under Consideration','Target List').
For each underlier, find non-REX L&I filings (last 365d) on the same underlier and
project their effective dates using the canonical 75-day SEC 485(a) review window.

Tags each row with the race-winner-so-far:
    REX_FIRST   — green   — REX effective date is earlier than any competitor's
    TIED        — yellow  — same date (within 7 days) or both undated
    COMP_FIRST  — red     — a competitor effective date precedes REX's

CLI:
    python -m screener.li_engine.analysis.launch_race_grid
    -> writes reports/launch_race_grid_<today>.html
"""
from __future__ import annotations

import logging
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

_HERE = Path(__file__).resolve()
_CANDIDATE_ROOTS = [
    _HERE.parent.parent.parent.parent,
    Path("/home/jarvis/rexfinhub"),
    Path("C:/Foundry/Rexfinhub"),
]
_ROOT = next(
    (r for r in _CANDIDATE_ROOTS if (r / "data" / "etp_tracker.db").exists()),
    _CANDIDATE_ROOTS[0],
)
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

DB = _ROOT / "data" / "etp_tracker.db"
OUT_DIR = _ROOT / "reports"

# Canonical SEC 485(a) automatic-effectiveness window. Mirror of
# scripts/sync_rex_products_from_filings.RULE_485A_DAYS so any future change
# stays in one place if we ever centralize it.
RULE_485A_DAYS = 75

# Pre-launch statuses to include (per Scott's spec).
PRE_LAUNCH_STATUSES = ("Filed", "Under Consideration", "Target List")

# Competitor-detection patterns. Source of truth lives in
# scripts/sync_rex_products_from_filings.COMPETITOR_NAME_PATTERNS; the regex
# below covers both registrant strings (filings.registrant) and series names.
COMPETITOR_REGISTRANT_RE = re.compile(
    r"\b(DIREXION|PROSHARES|DEFIANCE|INNOVATOR|ROUNDHILL|TRADR|GRANITESHARES|"
    r"AMPLIFY|KODEX|CORGI|TUTTLE|VOLATILITY\s+SHARES|GLOBAL\s+X|FIRST\s+TRUST|"
    r"THEMES|GSR|TIDAL|HEDGEYE|LEVERAGE\s+SHARES|YIELDMAX|BATTLESHARES)\b",
    re.IGNORECASE,
)
# Word-bounded so "diREXion" (Direxion) does not falsely look like REX.
REX_REGISTRANT_RE = re.compile(r"\bREX\b|ETF\s+Opportunities\s+Trust", re.IGNORECASE)

# Per-row issuer extraction from registrant — short-form display.
_ISSUER_SHORT = [
    (re.compile(r"DIREXION", re.I), "Direxion"),
    (re.compile(r"PROSHARES", re.I), "ProShares"),
    (re.compile(r"DEFIANCE", re.I), "Defiance"),
    (re.compile(r"INNOVATOR", re.I), "Innovator"),
    (re.compile(r"ROUNDHILL", re.I), "Roundhill"),
    (re.compile(r"TRADR", re.I), "Tradr"),
    (re.compile(r"GRANITESHARES", re.I), "GraniteShares"),
    (re.compile(r"AMPLIFY", re.I), "Amplify"),
    (re.compile(r"KODEX", re.I), "Kodex"),
    (re.compile(r"VOLATILITY\s+SHARES", re.I), "Volatility Shares"),
    (re.compile(r"GLOBAL\s+X", re.I), "Global X"),
    (re.compile(r"FIRST\s+TRUST", re.I), "First Trust"),
    (re.compile(r"YIELDMAX", re.I), "YieldMax"),
    (re.compile(r"BATTLESHARES", re.I), "Battleshares"),
    (re.compile(r"LEVERAGE\s+SHARES", re.I), "Leverage Shares"),
    (re.compile(r"TUTTLE", re.I), "Tuttle"),
]

NAVY = "#1a1a2e"
BLUE = "#0984e3"
GREEN = "#27ae60"
ORANGE = "#f39c12"
RED = "#e74c3c"
GRAY = "#7f8c8d"
LIGHT = "#f4f5f6"
BORDER = "#ecf0f1"
YELLOW_BG = "#fff8d6"
GREEN_BG = "#e6f5ec"
RED_BG = "#fbe6e3"

# Underlier extraction from a REX/competitor fund name. Ordered most-specific
# first; first match wins. Always returns uppercase. We reject obvious
# non-ticker tokens via STOP_TOKENS.
STOP_TOKENS = {
    "ETF", "ETN", "DAILY", "TARGET", "LONG", "SHORT", "BULL", "BEAR",
    "INVERSE", "ULTRA", "ULTRAPRO", "SHARES", "DIREXION", "PROSHARES",
    "DEFIANCE", "INNOVATOR", "ROUNDHILL", "TRADR", "GRANITESHARES",
    "AMPLIFY", "KODEX", "TIDAL", "HEDGEYE", "FUND", "TRUST",
}
_UNDERLIER_PATTERNS = [
    re.compile(
        r"\bT[-\s]?REX\s+\d(?:\.\d)?X\s+(?:LONG|SHORT|INVERSE)\s+([A-Z0-9]{2,12}(?:\s+[A-Z]{2})?)\s+DAILY",
        re.I,
    ),
    re.compile(
        r"\bDAILY\s+TARGET\s+\d(?:\.\d)?X\s+(?:LONG|SHORT|INVERSE)\s+([A-Z0-9]{2,12})\s+(?:ETF|ETN)",
        re.I,
    ),
    re.compile(
        r"\b\d(?:\.\d)?X\s+(?:LONG|SHORT|BULL|BEAR|INVERSE)\s+([A-Z0-9]{2,12})\s+(?:DAILY|ETF|ETN)",
        re.I,
    ),
    re.compile(r"\b\d(?:\.\d)?X\s+(?:LONG|SHORT)\s+([A-Z0-9]{2,12})\b", re.I),
    re.compile(r"\b(?:LONG|SHORT)\s+([A-Z0-9]{2,12})\s+(?:DAILY|ETF|ETN)", re.I),
    re.compile(r"\bULTRA(?:PRO|SHORT)?\s+([A-Z0-9]{2,12})\b", re.I),
    # Direxion-style: "Daily NVDA Bull 2X"
    re.compile(r"\bDAILY\s+([A-Z0-9]{2,12})\s+(?:BULL|BEAR)\s+\d(?:\.\d)?X\b", re.I),
    re.compile(r"\b(?:BULL|BEAR)\s+\d(?:\.\d)?X\s+([A-Z0-9]{2,12})\b", re.I),
]

# Pre-IPO targets — names whose underlier is the company itself, not a ticker.
# Keep this list short; expand only when a filing surfaces a new pre-IPO target.
_PRE_IPO_LABELS = {
    "ANTHROPIC": "ANTHROPIC",
    "SPACEX": "SPACEX",
    "OPENAI": "OPENAI",
    "FIGURE AI": "FIGURE AI",
    "ANDURIL": "ANDURIL",
    "QUANTINUUM": "QUANTINUUM",
    "KIOXIA": "KIOXIA",
    "VIVA REPUBLICA": "VIVA REPUBLICA",
    "SK HYNIX": "SK HYNIX",
}


def _short_issuer(registrant: str | None) -> str:
    if not registrant:
        return "—"
    for pat, label in _ISSUER_SHORT:
        if pat.search(registrant):
            return label
    return registrant.strip().split()[0].title()[:24] or "—"


def _extract_underlier_from_name(name: str | None) -> str:
    """Best-effort underlier extraction from a fund name.

    Returns uppercased ticker or pre-IPO label, or '' if nothing matched.
    Honors well-known pre-IPO labels so 'T-REX 2X Long Anthropic Daily' yields
    'ANTHROPIC' instead of garbage.
    """
    if not isinstance(name, str) or not name.strip():
        return ""
    upper = name.upper()
    for label in _PRE_IPO_LABELS:
        if label in upper:
            return _PRE_IPO_LABELS[label]
    for pat in _UNDERLIER_PATTERNS:
        m = pat.search(name)
        if m:
            token = m.group(1).strip().upper()
            if token and token not in STOP_TOKENS:
                return token
    return ""


def _normalize_underlier_key(raw: str | None) -> str:
    """Comparison key for joining REX rows to competitor filings.

    Strips a trailing ' US' (Bloomberg US-Equity convention) so that
    'NVDA US' and 'NVDA' collapse to the same key. Other exchange codes
    (KS, LN, JP, HK) are preserved so 'FEPI US' != 'FEPI LN'.

    Idempotent.
    """
    if not raw:
        return ""
    s = str(raw).strip().upper()
    # Drop Bloomberg yellow-key trailing words just in case.
    for suffix in (" EQUITY", " CURNCY", " COMDTY", " INDEX"):
        if s.endswith(suffix):
            s = s[: -len(suffix)].rstrip()
    # Drop a single trailing ' US' but keep other exchange codes.
    if s.endswith(" US"):
        s = s[:-3].rstrip()
    return s


def _row_underlier(row: dict) -> str:
    """Pick the best underlier value for a REX row. DB column first, then name."""
    val = row.get("underlier") or row.get("underlying_ticker")
    if val and str(val).strip():
        return _normalize_underlier_key(val)
    return _normalize_underlier_key(_extract_underlier_from_name(row.get("name")))


# ---------------------------------------------------------------------------
# Data loaders
# ---------------------------------------------------------------------------
def load_rex_pipeline(conn: sqlite3.Connection) -> pd.DataFrame:
    """Pre-launch REX products."""
    placeholders = ",".join("?" for _ in PRE_LAUNCH_STATUSES)
    df = pd.read_sql_query(
        f"""SELECT id, name, ticker, series_id, product_suite, status, direction,
                   underlier, underlying_ticker, initial_filing_date,
                   estimated_effective_date, latest_form
            FROM rex_products
            WHERE status IN ({placeholders})""",
        conn,
        params=PRE_LAUNCH_STATUSES,
    )
    if df.empty:
        return df
    df["underlier_key"] = df.apply(lambda r: _row_underlier(r.to_dict()), axis=1)
    df["initial_filing_date"] = pd.to_datetime(df["initial_filing_date"], errors="coerce")
    df["estimated_effective_date"] = pd.to_datetime(df["estimated_effective_date"], errors="coerce")
    return df


def load_competitor_filings(conn: sqlite3.Connection, lookback_days: int = 365) -> pd.DataFrame:
    """Non-REX 485APOS / 485BPOS / N-1A / S-1 filings in the lookback window.

    For each, extract the underlier from series_name. One row per
    (registrant, underlier, filing_date).
    """
    cutoff = (date.today() - timedelta(days=lookback_days)).isoformat()
    df = pd.read_sql_query(
        """SELECT f.filing_date, f.form, f.registrant,
                  fe.series_name, fe.class_symbol
           FROM filings f
           JOIN fund_extractions fe ON fe.filing_id = f.id
           WHERE f.form IN ('485APOS','485BPOS','485BXT','N-1A','S-1')
             AND f.filing_date >= ?""",
        conn,
        params=(cutoff,),
    )
    if df.empty:
        return df
    # REX out, competitor in.
    df = df[df["registrant"].fillna("").apply(
        lambda s: bool(COMPETITOR_REGISTRANT_RE.search(s)) and not REX_REGISTRANT_RE.search(s)
    )]
    df["underlier_key"] = df["series_name"].apply(
        lambda n: _normalize_underlier_key(_extract_underlier_from_name(n))
    )
    df = df[df["underlier_key"] != ""]
    df["filing_date"] = pd.to_datetime(df["filing_date"], errors="coerce")
    df["projected_effective"] = df["filing_date"] + pd.Timedelta(days=RULE_485A_DAYS)
    df["issuer_short"] = df["registrant"].apply(_short_issuer)
    df = df.sort_values("filing_date").drop_duplicates(
        subset=["underlier_key", "issuer_short", "filing_date"]
    )
    return df


# ---------------------------------------------------------------------------
# Grid build
# ---------------------------------------------------------------------------
def build_grid(rex: pd.DataFrame, comp: pd.DataFrame) -> pd.DataFrame:
    """Join pipeline + competitors. One row per REX product."""
    rows = []
    has_comp = (not comp.empty) and ("underlier_key" in comp.columns)
    for _, r in rex.iterrows():
        key = r["underlier_key"]
        if has_comp and key:
            cf = comp[comp["underlier_key"] == key]
        else:
            cf = comp.iloc[0:0] if has_comp else pd.DataFrame(
                columns=["issuer_short", "filing_date", "projected_effective"]
            )
        comp_issuers = sorted(cf["issuer_short"].dropna().unique().tolist())
        comp_dates = []
        first_comp_eff = None
        first_comp_filing = None
        first_comp_issuer = None
        for _, c in cf.iterrows():
            comp_dates.append(f"{c['issuer_short']}: {c['projected_effective'].date().isoformat()}")
            if first_comp_eff is None or c["projected_effective"] < first_comp_eff:
                first_comp_eff = c["projected_effective"]
                first_comp_issuer = c["issuer_short"]
            if first_comp_filing is None or c["filing_date"] < first_comp_filing:
                first_comp_filing = c["filing_date"]
        # Count distinct competitor filers (issuers racing on this underlier).
        n_filers = len(comp_issuers)
        rex_eff = r["estimated_effective_date"]
        rex_filing = r["initial_filing_date"]

        # Race-winner classification.
        if len(comp_issuers) == 0:
            winner = "NO_COMP"
        else:
            if pd.isna(rex_eff) and first_comp_eff is None:
                winner = "TIED"
            elif pd.isna(rex_eff):
                winner = "COMP_FIRST"
            elif first_comp_eff is None:
                winner = "REX_FIRST"
            else:
                delta = (rex_eff - first_comp_eff).days
                if abs(delta) <= 7:
                    winner = "TIED"
                elif delta < 0:
                    winner = "REX_FIRST"
                else:
                    winner = "COMP_FIRST"

        rows.append({
            "rex_name": r["name"],
            "series_id": r["series_id"],
            "underlier": key if key else "—",
            "rex_filing_date": rex_filing,
            "rex_estimated_effective": rex_eff,
            "rex_latest_form": r["latest_form"],
            "rex_status": r["status"],
            "competitor_count": n_filers,
            "competitor_issuers": ", ".join(comp_issuers) if comp_issuers else "—",
            "competitor_effective_dates": " · ".join(comp_dates) if comp_dates else "—",
            "first_competitor_filing_date": first_comp_filing,
            "first_competitor_effective": first_comp_eff,
            "first_competitor_issuer": first_comp_issuer or "—",
            "winner": winner,
        })

    df = pd.DataFrame(rows)
    if df.empty:
        return df
    # Sort: contested races first (more filers = more urgent), then soonest
    # REX estimated effective (NaT last) so imminent launches rise to the top.
    df = df.sort_values(
        by=["competitor_count", "rex_estimated_effective"],
        ascending=[False, True],
        na_position="last",
    ).reset_index(drop=True)
    return df


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------
def _fmt_date(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    if isinstance(v, (pd.Timestamp, datetime, date)):
        return pd.Timestamp(v).date().isoformat()
    return str(v)


def render_html(grid: pd.DataFrame, run_date: date | None = None) -> str:
    run_date = run_date or date.today()
    n_total = len(grid)
    n_with_comp = int((grid["competitor_count"] > 0).sum()) if not grid.empty else 0

    cols = [
        ("REX Product", "left"),
        ("Underlier", "center"),
        ("REX Filed", "center"),
        ("REX Est. Effective", "center"),
        ("Filers", "center"),
        ("Earliest Competitor", "left"),
    ]
    header = (
        "<tr>"
        + "".join(
            f'<th style="padding:8px 12px;color:{NAVY};font-size:11px;font-weight:600;'
            f"text-transform:uppercase;letter-spacing:0.3px;text-align:{a};"
            f'border-bottom:2px solid {NAVY};white-space:nowrap;">{escape(c)}</th>'
            for c, a in cols
        )
        + "</tr>"
    )

    body_rows = []
    if grid.empty:
        body_rows.append(
            f'<tr><td colspan="6" style="padding:18px;font-style:italic;color:{GRAY};">'
            "No pre-launch REX products in pipeline.</td></tr>"
        )
    else:
        for i, (_, r) in enumerate(grid.iterrows()):
            stripe = "white" if i % 2 == 0 else LIGHT
            n_filers = int(r["competitor_count"])
            if n_filers > 0:
                earliest = (
                    f'{escape(str(r["first_competitor_issuer"]))} '
                    f'<span style="color:{GRAY};">· {_fmt_date(r["first_competitor_effective"])}</span>'
                )
                filers_cell = f'<span style="font-weight:600;">{n_filers}</span>'
            else:
                earliest = f'<span style="color:{GRAY};">— none —</span>'
                filers_cell = f'<span style="color:{GRAY};">0</span>'
            body_rows.append(
                f'<tr style="background:{stripe};">'
                f'<td style="padding:7px 12px;border-bottom:1px solid {BORDER};font-size:12px;">{escape(str(r["rex_name"]))}</td>'
                f'<td style="padding:7px 12px;border-bottom:1px solid {BORDER};font-size:12px;text-align:center;'
                f'font-family:Courier New,monospace;font-weight:700;color:{NAVY};white-space:nowrap;">{escape(r["underlier"])}</td>'
                f'<td style="padding:7px 12px;border-bottom:1px solid {BORDER};font-size:12px;text-align:center;white-space:nowrap;color:{GRAY};">{_fmt_date(r["rex_filing_date"])}</td>'
                f'<td style="padding:7px 12px;border-bottom:1px solid {BORDER};font-size:12px;text-align:center;white-space:nowrap;font-weight:600;">{_fmt_date(r["rex_estimated_effective"])}</td>'
                f'<td style="padding:7px 12px;border-bottom:1px solid {BORDER};font-size:12px;text-align:center;">{filers_cell}</td>'
                f'<td style="padding:7px 12px;border-bottom:1px solid {BORDER};font-size:12px;white-space:nowrap;">{earliest}</td>'
                "</tr>"
            )

    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Launch Race Grid — {run_date.isoformat()}</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        background: white; margin: 0; padding: 24px 30px; color: {NAVY}; }}
.title {{ font-size: 18px; font-weight: 700; color: {NAVY}; margin: 0 0 2px; }}
.subtitle {{ font-size: 12px; color: {GRAY}; margin-bottom: 16px; }}
table.grid {{ width: 100%; border-collapse: collapse; }}
.footer {{ font-size: 11px; color: {GRAY}; margin-top: 16px; line-height: 1.5; }}
</style></head><body>
  <div class="title">REX Launch Race Grid</div>
  <div class="subtitle">{n_total} pre-launch REX products · {n_with_comp} with a competing filing · projected effective dates · {run_date.isoformat()}</div>
  <table class="grid">{header}{''.join(body_rows)}</table>
  <div class="footer">
    One row per pre-launch REX product (status: {", ".join(PRE_LAUNCH_STATUSES)}).
    <strong>Filers</strong> = distinct non-REX issuers with a leveraged/inverse filing on the same underlier in the last 365 days.
    <strong>Earliest Competitor</strong> = the filer whose projected effective date is soonest, using the SEC 485(a) {RULE_485A_DAYS}-day automatic-effectiveness window.
    REX estimated effective dates come from <code>rex_products.estimated_effective_date</code>.
    Internal pipeline view — not investment advice.
  </div>
</body></html>"""
    return html


# ---------------------------------------------------------------------------
# Public build entry
# ---------------------------------------------------------------------------
def build(db_path: Path | None = None) -> tuple[pd.DataFrame, str]:
    db_path = Path(db_path) if db_path else DB
    conn = sqlite3.connect(str(db_path))
    try:
        rex = load_rex_pipeline(conn)
        comp = load_competitor_filings(conn)
    finally:
        conn.close()
    log.info("Loaded %d pre-launch REX rows, %d competitor filings", len(rex), len(comp))
    grid = build_grid(rex, comp)
    html = render_html(grid, run_date=date.today())
    return grid, html


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    grid, html = build()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"launch_race_grid_{date.today().isoformat()}.html"
    out.write_text(html, encoding="utf-8")
    log.info("Wrote %s (%.1f KB, %d rows)", out, out.stat().st_size / 1024, len(grid))
    print(f"Report: {out}")
    if not grid.empty:
        cols = ["rex_name", "underlier", "rex_estimated_effective",
                "competitor_count", "competitor_issuers", "winner"]
        print("\nSample (first 10 rows):")
        print(grid[cols].head(10).to_string(index=False))


if __name__ == "__main__":
    main()
