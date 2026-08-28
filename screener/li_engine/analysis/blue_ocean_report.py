"""Blue Ocean — L&I Overnight Trading report (v6 final shape).

Three tables built off the market data BlueOcean sheet, joined to mkt_master_data:
  1. Blue Ocean by Issuer (filtered to max-of-windows >= $100K)
  2. Top 15 Underliers by Blue Ocean turnover
  3. Appendix — REX & MicroSectors product detail

Source script was bo_v5.py (filename v5, content v6 — signed off 2026-06-08).
Data logic and visuals are NOT to be modified here without explicit direction.

Run as: ``python -m screener.li_engine.analysis.blue_ocean_report``
Default output: ``reports/blue_ocean_YYYY-MM-DD.html`` relative to repo root.
"""
from __future__ import annotations

import argparse
import sqlite3
from datetime import date
from html import escape
from pathlib import Path

import pandas as pd

_ROOT = Path(__file__).resolve().parent.parent.parent.parent
DB = _ROOT / "data" / "etp_tracker.db"
XLSM = _ROOT / "data" / "DASHBOARD" / "bloomberg_daily_file.xlsm"


def _money(v):
    if v >= 1e9:
        return f"${v/1e9:.2f}B"
    if v >= 1e6:
        return f"${v/1e6:.1f}M"
    if v >= 1e3:
        return f"${v/1e3:.0f}K"
    if v == 0:
        return "—"
    return f"${v:.0f}"


def _pct(v):
    return "—" if (v == 0 or pd.isna(v)) else f"{v:.2f}%"


def _row_issuer(r, alt=False):
    is_rex = r["issuer"] in ("REX", "MicroSectors")
    bg = "#eef3f8" if is_rex else ("#f7f8f9" if alt else "#ffffff")
    bold = "font-weight:700;" if is_rex else ""
    return f"""<tr style="background:{bg};">
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;{bold}">{escape(r['issuer'])}</td>
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;text-align:right;{bold}">{int(r['n'])}</td>
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;text-align:right;{bold}">{_money(r['oc_1m'])}</td>
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;text-align:right;{bold}">{_money(r['oc_3m'])}</td>
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;text-align:right;{bold}">{_money(r['us_1m'])}</td>
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;text-align:right;{bold}">{_money(r['us_3m'])}</td>
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;text-align:right;{bold}">{_pct(r['pct_bo_1m'])}</td>
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;text-align:right;{bold}">{_pct(r['pct_bo_3m'])}</td>
</tr>"""


def _row_underlier(r, alt=False):
    bg = "#f7f8f9" if alt else "#ffffff"
    return f"""<tr style="background:{bg};">
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;font-family:Consolas,'Courier New',monospace;font-weight:700;">{escape(r['u_clean'])}</td>
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;text-align:right;">{int(r['n'])}</td>
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;text-align:right;">{_money(r['oc_1m'])}</td>
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;text-align:right;">{_money(r['oc_3m'])}</td>
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;text-align:right;">{_money(r['us_1m'])}</td>
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;text-align:right;">{_money(r['us_3m'])}</td>
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;text-align:right;">{_pct(r['pct_bo_1m'])}</td>
<td style="padding:8px 11px;border-bottom:1px solid #e0e3e6;text-align:right;">{_pct(r['pct_bo_3m'])}</td>
</tr>"""


def _row_appx(r, alt=False):
    bg = "#f7f8f9" if alt else "#ffffff"
    return f"""<tr style="background:{bg};">
<td style="padding:7px 11px;border-bottom:1px solid #e0e3e6;font-family:Consolas,'Courier New',monospace;font-weight:700;">{escape(r['ticker_clean'])}</td>
<td style="padding:7px 11px;border-bottom:1px solid #e0e3e6;font-size:10pt;">{escape(str(r['fund_name'])[:60])}</td>
<td style="padding:7px 11px;border-bottom:1px solid #e0e3e6;text-align:right;font-size:10pt;">{escape(r['issuer'])}</td>
<td style="padding:7px 11px;border-bottom:1px solid #e0e3e6;text-align:right;">{_money(r['oc_1m'])}</td>
<td style="padding:7px 11px;border-bottom:1px solid #e0e3e6;text-align:right;">{_money(r['oc_3m'])}</td>
<td style="padding:7px 11px;border-bottom:1px solid #e0e3e6;text-align:right;">{_money(r['us_1m'])}</td>
<td style="padding:7px 11px;border-bottom:1px solid #e0e3e6;text-align:right;">{_money(r['us_3m'])}</td>
<td style="padding:7px 11px;border-bottom:1px solid #e0e3e6;text-align:right;">{_pct(r['pct_bo_1m'])}</td>
<td style="padding:7px 11px;border-bottom:1px solid #e0e3e6;text-align:right;">{_pct(r['pct_bo_3m'])}</td>
</tr>"""


def build_html(xlsm_path: Path | str = XLSM, db_path: Path | str = DB) -> str:
    """Build the Blue Ocean report HTML and return it as a string.

    Reads the BlueOcean sheet from the daily data file and joins to
    mkt_master_data (primary_category='LI' AND market_status='ACTV').
    """
    bo = pd.read_excel(str(xlsm_path), sheet_name="BlueOcean")
    bo = bo.rename(columns={
        "Ticker": "us_ticker", "1M Traded Value": "us_1m", "3M Traded Value": "us_3m",
        "Ticker.1": "oc_ticker", "1M Traded Value.1": "oc_1m", "3M Traded Value.1": "oc_3m",
    })
    bo["us_clean"] = bo["us_ticker"].astype(str).str.upper().str.replace(" US", "").str.strip()
    # The sheet's US half and OC half are two INDEPENDENT ticker lists that happen to sit
    # side by side; they are NOT guaranteed to align row-for-row. When a ticker is added to
    # one half only (RAM added 2026-08-04: 'RAM US' landed on row 7884 but 'RAM OC' on 7885)
    # a positional read hands that fund its NEIGHBOUR's overnight values — wrong numbers,
    # not blanks. Key the OC half by its own ticker and join on that instead.
    bo["oc_clean"] = bo["oc_ticker"].astype(str).str.upper().str.replace(" OC", "").str.strip()
    _oc = (bo[["oc_clean", "oc_1m", "oc_3m"]]
           .dropna(subset=["oc_clean"])
           .query("oc_clean != '' and oc_clean != 'NAN' and oc_clean != '#ERROR'")
           .drop_duplicates(subset="oc_clean", keep="first"))
    bo = bo.drop(columns=["oc_1m", "oc_3m"]).merge(
        _oc, left_on="us_clean", right_on="oc_clean", how="left")

    conn = sqlite3.connect(str(db_path))
    try:
        li = pd.read_sql_query("""
            SELECT ticker, fund_name, CASE WHEN is_rex=1 AND COALESCE(issuer_nickname,issuer)<>'MicroSectors' THEN 'REX' ELSE COALESCE(issuer_nickname, issuer) END AS issuer, map_li_underlier
            FROM mkt_master_data
            WHERE primary_category='LI' AND market_status='ACTV'
        """, conn)
    finally:
        conn.close()

    li["t_clean"] = li["ticker"].astype(str).str.upper().str.replace(" US", "").str.strip()
    merged = li.merge(
        bo[["us_clean", "us_1m", "us_3m", "oc_1m", "oc_3m"]],
        left_on="t_clean", right_on="us_clean", how="left",
    )
    for c in ["us_1m", "us_3m", "oc_1m", "oc_3m"]:
        merged[c] = merged[c].fillna(0)

    # Table 1 — By-issuer
    agg = merged.groupby("issuer").agg(
        n=("ticker", "count"),
        us_1m=("us_1m", "sum"), us_3m=("us_3m", "sum"),
        oc_1m=("oc_1m", "sum"), oc_3m=("oc_3m", "sum"),
    ).reset_index()
    agg["pct_bo_1m"] = (agg["oc_1m"] / agg["us_1m"] * 100).fillna(0).round(2)
    agg["pct_bo_3m"] = (agg["oc_3m"] / agg["us_3m"] * 100).fillna(0).round(2)
    agg["_max_bo"] = agg[["oc_1m", "oc_3m"]].max(axis=1)
    agg = agg[agg["_max_bo"] >= 100_000].drop(columns=["_max_bo"]).sort_values("oc_1m", ascending=False)

    # Table 2 — Top underliers
    und = merged[merged["map_li_underlier"].notna() & (merged["map_li_underlier"] != "")].copy()
    und["u_clean"] = und["map_li_underlier"].astype(str).str.upper().str.replace(" US", "").str.replace(" EQUITY", "").str.strip()
    und_agg = und.groupby("u_clean").agg(
        n=("ticker", "count"),
        us_1m=("us_1m", "sum"), us_3m=("us_3m", "sum"),
        oc_1m=("oc_1m", "sum"), oc_3m=("oc_3m", "sum"),
    ).reset_index()
    und_agg["pct_bo_1m"] = (und_agg["oc_1m"] / und_agg["us_1m"] * 100).fillna(0).round(2)
    und_agg["pct_bo_3m"] = (und_agg["oc_3m"] / und_agg["us_3m"] * 100).fillna(0).round(2)
    und_agg = und_agg[und_agg["oc_1m"] > 0].sort_values("oc_1m", ascending=False).head(15)

    # Table 3 — Appendix: REX + MicroSectors detail
    appx = merged[merged["issuer"].isin(["REX", "MicroSectors"])].copy()
    appx["pct_bo_1m"] = (appx["oc_1m"] / appx["us_1m"] * 100).fillna(0).round(2)
    appx["pct_bo_3m"] = (appx["oc_3m"] / appx["us_3m"] * 100).fillna(0).round(2)
    appx["ticker_clean"] = appx["ticker"].astype(str).str.replace(" US", "", regex=False).str.strip()
    appx = appx.sort_values(["issuer", "oc_1m"], ascending=[True, False])
    rex_n = (appx["issuer"] == "REX").sum()
    ms_n = (appx["issuer"] == "MicroSectors").sum()

    issuer_rows = "".join(_row_issuer(r, i % 2 == 1) for i, (_, r) in enumerate(agg.iterrows()))
    under_rows = "".join(_row_underlier(r, i % 2 == 1) for i, (_, r) in enumerate(und_agg.iterrows()))
    appx_rows = "".join(_row_appx(r, i % 2 == 1) for i, (_, r) in enumerate(appx.iterrows()))
    today_str = date.today().strftime("%B %d, %Y")

    HEAD = """<th style="padding:9px 11px;color:#1a1a2e;font-size:10pt;text-align:right;font-weight:700;border-bottom:2px solid #1a1a2e;">"""
    HEAD_L = """<th style="padding:9px 11px;color:#1a1a2e;font-size:10pt;text-align:left;font-weight:700;border-bottom:2px solid #1a1a2e;">"""

    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8">
<title>Blue Ocean &mdash; L&amp;I Overnight Trading &middot; {today_str}</title></head>
<body style="margin:0;padding:0;background:#ffffff;font-family:Aptos,Aptos_MSFontService,-apple-system,Roboto,Arial,Helvetica,sans-serif;font-size:12pt;color:#1a1a2e;line-height:1.55;">
<div style="padding:24px 28px;max-width:980px;margin:0 auto;">

<div style="font-size:18pt;font-weight:700;color:#1a1a2e;letter-spacing:-0.3px;">Blue Ocean &mdash; L&amp;I Overnight Trading</div>
<div style="font-size:10pt;color:#566573;margin-top:4px;letter-spacing:0.3px;text-transform:uppercase;">{today_str}</div>

<div style="font-size:11pt;font-weight:700;color:#1a1a2e;margin-top:22px;margin-bottom:8px;">Blue Ocean by Issuer</div>
<table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;font-family:Aptos,Aptos_MSFontService,-apple-system,Roboto,Arial,Helvetica,sans-serif;font-size:11pt;color:#1a1a2e;border:1px solid #c8ccd0;width:100%;">
<thead><tr style="background:#e8eaed;">
{HEAD_L}Issuer</th>
{HEAD}# Products</th>
{HEAD}Blue Ocean 1M</th>
{HEAD}Blue Ocean 3M</th>
{HEAD}US 1M</th>
{HEAD}US 3M</th>
{HEAD}BO % of US (1M)</th>
{HEAD}BO % of US (3M)</th>
</tr></thead>
<tbody>{issuer_rows}</tbody></table>

<div style="font-size:11pt;font-weight:700;color:#1a1a2e;margin-top:28px;margin-bottom:8px;">Top Underliers by Blue Ocean Turnover</div>
<table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;font-family:Aptos,Aptos_MSFontService,-apple-system,Roboto,Arial,Helvetica,sans-serif;font-size:11pt;color:#1a1a2e;border:1px solid #c8ccd0;width:100%;">
<thead><tr style="background:#e8eaed;">
{HEAD_L}Underlier</th>
{HEAD}# Products</th>
{HEAD}Blue Ocean 1M</th>
{HEAD}Blue Ocean 3M</th>
{HEAD}US 1M</th>
{HEAD}US 3M</th>
{HEAD}BO % of US (1M)</th>
{HEAD}BO % of US (3M)</th>
</tr></thead>
<tbody>{under_rows}</tbody></table>

<div style="font-size:11pt;font-weight:700;color:#1a1a2e;margin-top:32px;margin-bottom:4px;">Appendix &mdash; REX &amp; MicroSectors Product Detail</div>
<div style="font-size:9.5pt;color:#566573;margin-bottom:8px;">{rex_n} REX-brand + {ms_n} MicroSectors active L&amp;I products. Sorted by issuer, then Blue Ocean 1M descending.</div>
<table cellpadding="0" cellspacing="0" border="0" style="border-collapse:collapse;font-family:Aptos,Aptos_MSFontService,-apple-system,Roboto,Arial,Helvetica,sans-serif;font-size:10.5pt;color:#1a1a2e;border:1px solid #c8ccd0;width:100%;">
<thead><tr style="background:#e8eaed;">
{HEAD_L}Ticker</th>
{HEAD_L}Fund Name</th>
{HEAD}Issuer</th>
{HEAD}Blue Ocean 1M</th>
{HEAD}Blue Ocean 3M</th>
{HEAD}US 1M</th>
{HEAD}US 3M</th>
{HEAD}BO % of US (1M)</th>
{HEAD}BO % of US (3M)</th>
</tr></thead>
<tbody>{appx_rows}</tbody></table>

<div style="font-size:9.5pt;color:#566573;margin-top:22px;border-top:1px solid #e0e3e6;padding-top:10px;">

</div>

</div></body></html>"""


def _default_output_path() -> Path:
    return _ROOT / "reports" / f"blue_ocean_{date.today().isoformat()}.html"


def main():
    ap = argparse.ArgumentParser(description="Build the Blue Ocean L&I Overnight Trading report")
    ap.add_argument("--out", type=Path, default=None,
                    help="Output HTML path. Default: reports/blue_ocean_YYYY-MM-DD.html")
    ap.add_argument("--xlsm", type=Path, default=XLSM,
                    help=f"Path to market data daily xlsm. Default: {XLSM}")
    ap.add_argument("--db", type=Path, default=DB,
                    help=f"Path to etp_tracker.db. Default: {DB}")
    args = ap.parse_args()

    out_path = args.out if args.out is not None else _default_output_path()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    html = build_html(xlsm_path=args.xlsm, db_path=args.db)
    out_path.write_text(html, encoding="utf-8")
    print(f"Blue Ocean report: {len(html):,} bytes -> {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
