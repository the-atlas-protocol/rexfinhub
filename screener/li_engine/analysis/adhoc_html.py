"""Ad-hoc L&I analysis -> HTML in the T-REX report style.
Canonical score = li_engine_daily.final_score v1.0.1 for EVERY ticker (filing
status is not an input). Leads with the system's Top Recommendations to File.
Under each underlier, one sub-row per competitor filer (issuer · status · eff
date · live AUM). See docs/LI_ADHOC_PLAYBOOK.md.

Usage: python adhoc_html.py OUT.html 'Title' 'Section=TK,TK' ...
"""
import sys, sqlite3, re
import pandas as pd
from datetime import date

sys.path.insert(0, "/home/jarvis/rexfinhub")
from screener.li_engine.analysis.trex_combined_v9 import (
    load_rex_position, _canon, _comp_issuer_of, _comp_underlier_of, _LI_NAME_RE, _REX_NAME_RE, ANALYSIS,
)
from screener.li_engine.persistence import DB

NAVY="#1a1a2e"; BLUE="#0984e3"; GREEN="#27ae60"; RED="#e74c3c"; TEAL="#16a085"; GRAY="#7f8c8d"; INK="#1a1a2e"
ORANGE="#e67e22"

out, title = sys.argv[1], sys.argv[2]
user_sections = []
for arg in sys.argv[3:]:
    label, csv = arg.split("=", 1)
    user_sections.append((label, [_canon(t) for t in csv.split(",") if t.strip()]))

NAMES = {"FLNC":"Fluence Energy","HPE":"Hewlett Packard Enterprise","UMAC":"Unusual Machines",
    "RXT":"Rackspace Technology","LPTH":"LightPath Technologies","RADI":"Radius (delisted?)",
    "DOCN":"DigitalOcean","CIEN":"Ciena","AKAM":"Akamai","JBL":"Jabil","ONDS":"Ondas Holdings",
    "LUNR":"Intuitive Machines","FCEL":"FuelCell Energy","WTI":"W&T Offshore","RR":"Richtech Robotics",
    "INOD":"Innodata","VICR":"Vicor","HIMX":"Himax","HUT":"Hut 8","NRGV":"Energy Vault","DELL":"Dell"}

conn = sqlite3.connect(str(DB))

# ---- canonical score: li_engine_daily v1.0.1 (latest run) + 0-100 percentile
latest = conn.execute("SELECT MAX(run_date) FROM li_engine_daily").fetchone()[0]
uni = pd.read_sql_query("SELECT ticker, final_score FROM li_engine_daily WHERE run_date=?", conn, params=[latest])
uni["t"] = uni["ticker"].map(_canon)
uni = uni.dropna(subset=["final_score"]).drop_duplicates("t").sort_values("final_score", ascending=False).reset_index(drop=True)
uni["pct"] = uni["final_score"].rank(pct=True) * 100
n_univ = len(uni)
smap = uni.set_index("t")["pct"].to_dict()

# single-stock no-live-product membership (used only to scope "top to file" to clean names)
ws = pd.read_parquet(ANALYSIS / "whitespace_v4.parquet")
if ws.index.name == "ticker": ws = ws.reset_index()
single_pool = set(ws["ticker"].map(_canon))

rex_pos = load_rex_position()

# ---- per-competitor filing timeline: underlier -> [ {issuer,status,eff,aum,live} ]
fs = pd.read_sql_query("SELECT fund_name,status,effective_date FROM fund_status WHERE fund_name IS NOT NULL", conn)
filed = {}
for nm, st, eff in fs.itertuples(index=False):
    if not _LI_NAME_RE.search(nm) or _REX_NAME_RE.search(nm): continue
    iss = _comp_issuer_of(nm); u = _comp_underlier_of(nm)
    if not iss or not u: continue
    e = str(eff)[:10] if pd.notna(eff) and str(eff)[:3] not in ("Non","nan","NaT") else ""
    filed.setdefault(u, {}).setdefault(iss, {"issuer":iss,"status":str(st).title(),"eff":e,"aum":None,"live":False})
    if e and not filed[u][iss]["eff"]: filed[u][iss]["eff"] = e
lv = pd.read_sql_query("""SELECT map_li_underlier u, COALESCE(issuer_nickname,issuer) issuer, ticker, aum, inception_date
    FROM mkt_master_data WHERE primary_category='LI' AND market_status='ACTV' AND is_rex=0
    AND map_li_underlier IS NOT NULL AND map_li_underlier!=''
    AND (map_li_direction LIKE '%ong%' OR fund_name LIKE '%LONG%' OR fund_name LIKE '%BULL%')""", conn)
lv["u"] = lv["u"].map(_canon); lv["aum"] = pd.to_numeric(lv["aum"], errors="coerce").fillna(0)
norm = {"leverageshares":"Leverage Shares"}
for u, iss, tk, aum, inc in lv[["u","issuer","ticker","aum","inception_date"]].itertuples(index=False):
    issn = norm.get(str(iss).lower().replace(" ",""), str(iss))
    d = filed.setdefault(u, {})
    key = next((k for k in d if k.lower().replace(" ","") == issn.lower().replace(" ","")), issn)
    row = d.setdefault(key, {"issuer":issn,"status":"Live","eff":str(inc)[:10] if pd.notna(inc) else "","aum":0.0,"live":True})
    row["live"] = True; row["aum"] = max(row.get("aum") or 0, float(aum))
    if pd.notna(inc) and not row["eff"]: row["eff"] = str(inc)[:10]
conn.close()

def competitors(u):
    rows = list(filed.get(u, {}).values())
    rows.sort(key=lambda r: (not r["live"], -(r["aum"] or 0), r["eff"] or "9999"))
    return rows

def position(t):
    lab = rex_pos.get(t, ("—",))[0]
    c = {"Live":GREEN, "Filed":BLUE}.get(lab, RED)
    txt = {"Live":"Live", "Filed":"Filed"}.get(lab, "Not in")
    return f'<span style="color:{c};font-weight:700;">{txt}</span>'

def pct_cell(t):
    if t not in smap: return f'<span style="color:{GRAY};">—</span>'
    p = smap[t]
    if p >= 90:
        import math
        return f'<span style="color:{TEAL};font-weight:700;">top {max(1,math.ceil(100-p))}%</span>'
    return f'<span style="color:{GRAY};">{p:.0f}%</span>'

def verdict(t):
    lab = rex_pos.get(t, ("—",))[0]
    comps = competitors(t)
    live = [c for c in comps if c["live"]]
    nfiled = len(comps)
    if t not in smap: return "Below scoring floor / no signal — pass."
    if lab == "Live": return "REX already trades this — distribution review, not a new launch."
    if live:
        top = max(live, key=lambda c: c["aum"] or 0)
        tail = f"{top['issuer']} live at ${top['aum']:.0f}M" + (f", {len(live)} live" if len(live) > 1 else "")
        if lab == "Filed": return f"REX filed; {tail} — launch to compete."
        if len(live) <= 2 and (top['aum'] or 0) > 100: return f"<b>Launch-anyway.</b> Proven demand ({tail}), REX missing."
        return f"Saturated — {tail}."
    if lab == "Filed":
        return f"REX filed; {nfiled} filer(s) racing, nothing live yet — launch fast." if nfiled else "REX filed, zero competitors — we're ahead."
    return ("<b>Clean whitespace.</b> No live product, no REX filing — file first." if not nfiled
            else f"<b>Whitespace</b> — {nfiled} filer(s) racing, nothing live. File fast.")

def comp_subrows(t):
    rows = competitors(t)
    if not rows:
        return f'<tr style="background:#fafbfc;"><td colspan="6" style="padding:5px 10px 5px 34px;font-size:11px;color:{GRAY};border-bottom:1px solid #eef1f4;">↳ no competitor filings on record</td></tr>'
    out = ""
    for c in rows:
        if c["live"]:
            tag = f'<span style="color:{GREEN};font-weight:700;">LIVE</span> · ${c["aum"]:.0f}M' + (f' · since {c["eff"]}' if c["eff"] else "")
        else:
            st_col = {"Effective":BLUE,"Pending":ORANGE,"Delayed":GRAY}.get(c["status"], GRAY)
            tag = f'<span style="color:{st_col};font-weight:600;">{c["status"]}</span>' + (f' · eff {c["eff"]}' if c["eff"] else " · eff —")
        out += (f'<tr style="background:#fafbfc;"><td colspan="6" style="padding:5px 10px 5px 34px;font-size:11px;color:{INK};border-bottom:1px solid #eef1f4;">'
                f'↳ <b>{c["issuer"]}</b> · {tag}</td></tr>')
    return out

def underlier_block(t, alt):
    bg = "#ffffff" if not alt else "#f6f8fa"
    sc = f'<b style="color:{INK};">{smap[t]:.1f}</b>' if t in smap else f'<span style="color:{GRAY};">—</span>'
    head = (f'<tr style="background:{bg};">'
        f'<td style="padding:9px 10px;border-bottom:1px solid #e3e8ee;font-family:Courier New,monospace;font-weight:700;color:{BLUE};font-size:13px;">{t}</td>'
        f'<td style="padding:9px 10px;border-bottom:1px solid #e3e8ee;font-size:12px;">{NAMES.get(t,"")}</td>'
        f'<td style="padding:9px 10px;border-bottom:1px solid #e3e8ee;font-size:13px;text-align:right;">{sc}</td>'
        f'<td style="padding:9px 10px;border-bottom:1px solid #e3e8ee;font-size:12px;text-align:right;">{pct_cell(t)}</td>'
        f'<td style="padding:9px 10px;border-bottom:1px solid #e3e8ee;font-size:12px;text-align:center;">{position(t)}</td>'
        f'<td style="padding:9px 10px;border-bottom:1px solid #e3e8ee;font-size:12px;color:{INK};">{verdict(t)}</td></tr>')
    return head + comp_subrows(t)

def section(label, tks, blurb=""):
    tks = [t for t in tks if t]
    tks.sort(key=lambda t: -(smap.get(t, -1)))
    body = "".join(underlier_block(t, i % 2 == 1) for i, t in enumerate(tks))
    sub = f'<div style="font-size:11px;color:{GRAY};margin:2px 0 8px;">{blurb}</div>' if blurb else ""
    return f"""<tr><td style="padding:18px 30px 2px;">
  <div style="font-size:13px;font-weight:700;color:{NAVY};text-transform:uppercase;letter-spacing:0.8px;border-bottom:2px solid {NAVY};padding-bottom:6px;">{label}</div>{sub}</td></tr>
<tr><td style="padding:4px 30px 8px;">
<table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;">
<tr style="background:{NAVY};">
  <th style="padding:8px 10px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.6px;text-align:left;">Ticker</th>
  <th style="padding:8px 10px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.6px;text-align:left;">Company</th>
  <th style="padding:8px 10px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.6px;text-align:right;">Score</th>
  <th style="padding:8px 10px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.6px;text-align:right;">Pctile</th>
  <th style="padding:8px 10px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.6px;text-align:center;">REX</th>
  <th style="padding:8px 10px;color:white;font-size:10px;text-transform:uppercase;letter-spacing:0.6px;text-align:left;">Verdict · competitors listed below each name</th>
</tr>
{body}
</table></td></tr>"""

# Top Recommendations to File = highest li_engine score, REX Not in, single-stock pool
top_file = [r["t"] for _, r in uni.iterrows()
            if rex_pos.get(r["t"], ("—",))[0] == "—" and r["t"] in single_pool][:12]

blocks = section("Top Recommendations to File — system-surfaced", top_file,
                 "Highest li_engine_daily v1.0.1 scorers where REX has no position yet (single-stock, no live product).")
for lbl, tks in user_sections:
    blocks += section(lbl, tks)

today = date.today().strftime("%B %d, %Y")
html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><title>{title} · {today}</title></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1a1a2e;line-height:1.5;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f8f9fa;">
<tr><td align="center" style="padding:24px 12px;">
<table width="960" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:960px;">
<tr><td style="background:{NAVY};padding:22px 30px;">
  <div style="color:#ffffff;font-size:20px;font-weight:700;letter-spacing:-0.3px;">{title}</div>
  <div style="color:#9bb1cc;font-size:11px;font-weight:500;letter-spacing:1px;text-transform:uppercase;margin-top:6px;">
    {today} · li_engine_daily v1.0.1 · same score for filed &amp; unfiled names · competitor filing timeline under each
  </div></td></tr>
{blocks}
</table></td></tr></table></body></html>"""
open(out, "w", encoding="utf-8").write(html)
print(f"WROTE {out} (top_file={len(top_file)}, universe {n_univ})")
