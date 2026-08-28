"""T-REX Recommendation Brief — scoped summary of the T-REX Recommendation System
for a requested review list. One canonical score (li_engine_daily v1.0.1); verdict
changes. Review list leads. See docs/LI_ADHOC_PLAYBOOK.md §4.

Usage: python recommendation_brief.py OUT.html 'Title' REVIEW=TK,TK,...
"""
import sys, sqlite3, json, re
import pandas as pd
from datetime import date

sys.path.insert(0, "/home/jarvis/rexfinhub")
from screener.li_engine.analysis.trex_combined_v9 import (
    load_rex_position, _canon, _comp_issuer_of, _comp_underlier_of, _LI_NAME_RE, _REX_NAME_RE,
)
from screener.li_engine.persistence import DB

NAVY="#1a1a2e"; BLUE="#0984e3"; GREEN="#27ae60"; RED="#e74c3c"; TEAL="#16a085"; GRAY="#7f8c8d"; INK="#1a1a2e"; ORANGE="#e67e22"; HILITE="#fff7e0"

out, title = sys.argv[1], sys.argv[2]
review = []
for arg in sys.argv[3:]:
    if arg.startswith("REVIEW="):
        review = [_canon(t) for t in arg.split("=",1)[1].split(",") if t.strip()]
reviewset = set(review)

conn = sqlite3.connect(str(DB))
latest = conn.execute("SELECT MAX(run_date) FROM li_engine_daily").fetchone()[0]
uni = pd.read_sql_query("SELECT ticker, final_score FROM li_engine_daily WHERE run_date=?", conn, params=[latest])
uni["t"] = uni["ticker"].map(_canon)
uni = uni.dropna(subset=["final_score"]).drop_duplicates("t").sort_values("final_score", ascending=False).reset_index(drop=True)
uni["pct"] = uni["final_score"].rank(pct=True) * 100
n_univ = len(uni); smap = uni.set_index("t")["final_score"].to_dict()   # raw li_engine score (real spread, sorts cleanly)

def collapse_status(s):
    """Status is shown as Filed or Effective only (Ryu pref). Pending/Delayed/Filed -> Filed; Active/Listed -> Effective."""
    s = str(s).strip().lower()
    return "Effective" if s in ("effective", "active", "listed", "live") else "Filed"

# company names + single-stock universe from mkt_stock_data (report's _single_stock_set)
# The Bloomberg stock pull carries 29 numeric fields and NO name, so rec[Name] is empty
# for all 6,594 tickers and this column rendered blank for every row. stock_names is a
# local cache (scripts/backfill_stock_names.py) consulted as a fallback.
namemap = {}; single_stock = set()
_name_cache = {}
try:
    for _t, _n in conn.execute("SELECT ticker, company_name FROM stock_names WHERE company_name IS NOT NULL"):
        _name_cache[_canon(_t)] = _n
except Exception:
    pass
for tk, dj in conn.execute("SELECT ticker, data_json FROM mkt_stock_data"):
    u = _canon(tk)
    nme = ""
    try:
        rec = json.loads(dj); rec = rec[0] if isinstance(rec, list) else rec
        nme = str(rec.get("Name") or "") or _name_cache.get(u, "")
    except Exception: pass
    if any(x in nme for x in ("ETF","ETN")) or re.search(r"(USD|Curncy|Index)$", str(tk)): continue
    namemap[u] = nme[:30]; single_stock.add(u)
EXTRA = {"FLNC":"Fluence Energy","HPE":"HP Enterprise","UMAC":"Unusual Machines","RXT":"Rackspace",
    "LPTH":"LightPath","RADI":"Radius (delisted?)","DOCN":"DigitalOcean","CIEN":"Ciena","AKAM":"Akamai","JBL":"Jabil"}
for k,v in EXTRA.items(): namemap.setdefault(k, v); single_stock.add(k)
def nm(u): return namemap.get(u, "")

# live products per underlier
lv = pd.read_sql_query("""SELECT map_li_underlier u, map_li_direction d, COALESCE(issuer_nickname,issuer) iss,
    ticker, aum, is_rex, inception_date FROM mkt_master_data
    WHERE primary_category='LI' AND market_status='ACTV' AND map_li_underlier IS NOT NULL AND map_li_underlier!=''""", conn)
lv["u"] = lv["u"].map(_canon); lv["aum"] = pd.to_numeric(lv["aum"], errors="coerce").fillna(0)
lv["long"] = lv["d"].astype(str).str.lower().str.contains("long")
lv["inv"] = lv["d"].astype(str).str.lower().str.contains("short|inv")

comp_long = lv[(lv["long"]) & (lv["is_rex"] == 0)]
tk_list = tuple(comp_long["ticker"].dropna().unique().tolist()) or ("",)
ts = pd.read_sql_query(f"SELECT ticker, months_ago, aum_value FROM mkt_time_series WHERE months_ago IN (0,1,3) AND ticker IN ({','.join(['?']*len(tk_list))})", conn, params=list(tk_list))
ts["av"] = pd.to_numeric(ts["aum_value"], errors="coerce").fillna(0)
ts["u"] = ts["ticker"].map(dict(zip(comp_long["ticker"], comp_long["u"])))
trk = ts.dropna(subset=["u"]).groupby(["u","months_ago"])["av"].sum().unstack(fill_value=0)

# competitor filers: status + effective_date + latest_filing_date
fs = pd.read_sql_query("SELECT fund_name,status,effective_date,latest_filing_date FROM fund_status WHERE fund_name IS NOT NULL", conn)
filers = {}
for nmx, st, eff, filed in fs.itertuples(index=False):
    if not _LI_NAME_RE.search(nmx) or _REX_NAME_RE.search(nmx): continue
    iss = _comp_issuer_of(nmx); u = _comp_underlier_of(nmx)
    if not iss or not u: continue
    e = str(eff)[:10] if pd.notna(eff) and str(eff)[:3] not in ("Non","nan","NaT") else ""
    f = str(filed)[:10] if pd.notna(filed) and str(filed)[:3] not in ("Non","nan","NaT") else ""
    filers.setdefault(u, {}).setdefault(iss, {"issuer":iss,"status":collapse_status(st),"eff":e,"filed":f})

# inverse filings (competitor) per underlier — direction parsed from the name
INV_RE = re.compile(r"inverse|short|bear|-\dx|daily.*bear", re.I)
inv_filers = {}
for nmx, st, eff, filed in fs.itertuples(index=False):
    if not _LI_NAME_RE.search(nmx) or _REX_NAME_RE.search(nmx) or not INV_RE.search(nmx): continue
    iss = _comp_issuer_of(nmx); u = _comp_underlier_of(nmx)
    if not iss or not u: continue
    e = str(eff)[:10] if pd.notna(eff) and str(eff)[:3] not in ("Non","nan","NaT") else ""
    f = str(filed)[:10] if pd.notna(filed) and str(filed)[:3] not in ("Non","nan","NaT") else ""
    inv_filers.setdefault(u, {}).setdefault(iss, {"issuer":iss,"status":collapse_status(st),"eff":e,"filed":f})
conn.close()

# REX inverse filings from rex_products (name carries INVERSE/SHORT)
import sqlite3 as _sq
_c = _sq.connect(str(DB))
rex_inv = {}
for name, status in _c.execute("SELECT name, status FROM rex_products WHERE product_suite='T-REX' AND (name LIKE '%INVERSE%' OR name LIKE '%Inverse%' OR name LIKE '%SHORT%')"):
    m = re.search(r"\d(?:\.\d)?X (?:INVERSE|Inverse|SHORT|Short) ([A-Z]{1,6}) (?:DAILY|Daily)", name)
    if m: rex_inv[_canon(m.group(1))] = collapse_status(status)
_c.close()
rex_pos = load_rex_position()

def roll(u):
    g = lv[lv["u"] == u]
    longs = g[(g["long"]) & (g["is_rex"] == 0)]
    comp_inv = g[(g["inv"]) & (g["is_rex"] == 0)]
    rex_inv_live = g[(g["inv"]) & (g["is_rex"] == 1)]
    # has_inv must count REX inverse products too. Filtering to is_rex==0 made the
    # Inverse Opportunities section blind to our OWN live inverses and recommend
    # "launch it" for products already trading (CRCD/CRCL, CORD/CRWV). Ryu 2026-07-29.
    invs = g[g["inv"]]
    rex_long = g[(g["long"]) & (g["is_rex"] == 1)]
    tr = trk.loc[u] if u in trk.index else None
    now = float(tr.get(0,0)) if tr is not None else float(longs["aum"].sum())
    mo1 = float(tr.get(1,0)) if tr is not None else 0.0; mo3 = float(tr.get(3,0)) if tr is not None else 0.0
    return {"n_long":len(longs),"total_aum":float(longs["aum"].sum()),"now":now,"mo1":mo1,"mo3":mo3,"flow1":now-mo1,
        "has_inv":len(invs)>0,"rex_inv_live":len(rex_inv_live)>0,"comp_inv":len(comp_inv),
        "rex_live_long":len(rex_long)>0,"longs":longs.sort_values("aum",ascending=False),
        "n_filers":len(filers.get(u,{})),"rexpos":rex_pos.get(u,("—",))[0]}

def bucket(u):
    r = roll(u)
    if r["rex_live_long"]: return "have"
    if r["n_long"] >= 2: return "crowded"     # 2+ live competitors = too contested for launch-anyway
    if r["n_long"] == 1: return "live1"        # the cleanest proven-demand case
    if r["rexpos"] == "Filed" and r["n_filers"] == 0: return "sole"
    return "file"

def verdict(u):
    r = roll(u); b = bucket(u)
    if u not in smap and b == "file": return ("No signal — delisted / pass.", GRAY)
    if b == "have": return ("REX already trades it — distribution review, not a launch.", GRAY)
    if b == "crowded": return (f"Crowded — {r['n_long']} live competitors. Facts only.", GRAY)
    if b == "live1":
        proven = r["flow1"] >= 25 or r["now"] >= 75
        if r["rexpos"] == "Filed" and proven: return ("Launch anyway — proven demand.", GREEN)
        if r["rexpos"] == "Filed": return ("Filed; demand not yet proven — watch.", ORANGE)
        return ("Facts only — REX not filed, no call.", GRAY)
    if b == "sole": return ("Launch on your timing — sole filer, no race pressure.", BLUE)
    if r["rexpos"] == "Filed":
        return (f"Filed, nothing live{', race on' if r['n_filers'] else ''} — launch fast.", BLUE)
    if r["n_filers"] == 0: return ("Clean whitespace — file first.", GREEN)
    return (f"Whitespace, {r['n_filers']} racing — file fast.", INK)

def comp_sub(u, span):
    out = ""; seen = set()
    for _, r in lv[(lv["u"]==u)&(lv["long"])&(lv["is_rex"]==0)].sort_values("aum",ascending=False).iterrows():
        iss = str(r["iss"]); seen.add(iss.lower().replace(" ",""))
        inc = str(r["inception_date"])[:10] if pd.notna(r["inception_date"]) else ""
        out += f'<tr style="background:#fafbfc;"><td colspan="{span}" style="padding:3px 8px 3px 30px;font-size:10.5px;border-bottom:1px solid #eef1f4;color:{INK};">↳ <b>{iss}</b> · <span style="color:{GREEN};font-weight:700;">LIVE</span> ${r["aum"]:.0f}M{(" · since "+inc) if inc else ""}</td></tr>'
    for c in filers.get(u, {}).values():
        if c["issuer"].lower().replace(" ","") in seen: continue
        col = {"Effective":GREEN}.get(c["status"], BLUE)
        dt = f"eff {c['eff']}" if c["eff"] else (f"filed {c['filed']}" if c["filed"] else "no date")
        out += f'<tr style="background:#fafbfc;"><td colspan="{span}" style="padding:3px 8px 3px 30px;font-size:10.5px;border-bottom:1px solid #eef1f4;color:{INK};">↳ <b>{c["issuer"]}</b> · <span style="color:{col};font-weight:600;">{c["status"]}</span> · {dt}</td></tr>'
    return out

def pos_badge(u):
    lab = rex_pos.get(u,("—",))[0]; c = {"Live":GREEN,"Filed":BLUE}.get(lab,RED)
    return f'<span style="color:{c};font-weight:700;">{ {"Live":"Live","Filed":"Filed"}.get(lab,"Not in") }</span>'
def sc_cell(u): return f'<b style="color:{INK};">{smap[u]:.1f}</b>' if u in smap else f'<span style="color:{GRAY};">—</span>'
def tcell(u, star=True):
    rev = u in reviewset and star
    return f'<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;{("border-left:3px solid "+ORANGE+";background:"+HILITE+";") if rev else ""}font-family:Courier New,monospace;font-weight:700;color:{BLUE};font-size:12px;white-space:nowrap;">{u}{" ★" if rev else ""}</td>'

def header(cols):  # cols = [(name,width,align)]
    cg = "".join(f'<col style="width:{w}">' for _,w,_ in cols)
    th = "".join(f'<th style="padding:7px 8px;color:white;font-size:9.5px;text-transform:uppercase;letter-spacing:0.5px;text-align:{a};">{c}</th>' for c,_,a in cols)
    return f'<colgroup>{cg}</colgroup><tr style="background:{NAVY};">{th}</tr>'

def section(title, blurb, header_html, rows):
    return f"""<tr><td style="padding:16px 28px 2px;">
  <div style="font-size:13px;font-weight:700;color:{NAVY};text-transform:uppercase;letter-spacing:0.7px;border-bottom:2px solid {NAVY};padding-bottom:5px;">{title}</div>
  <div style="font-size:10.5px;color:{GRAY};margin:3px 0 4px;">{blurb}</div></td></tr>
<tr><td style="padding:2px 28px 6px;"><table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;table-layout:fixed;">{header_html}{rows}</table></td></tr>"""

# main verdict-table row (Ticker/Company/Score/REX/Verdict) + sub-rows
def vrow(u, sub=True):
    vd, vc = verdict(u)
    r = f'<tr>{tcell(u)}<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{nm(u)}</td>' \
        f'<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;text-align:right;">{sc_cell(u)}</td>' \
        f'<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;text-align:center;white-space:nowrap;">{pos_badge(u)}</td>' \
        f'<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;font-size:12px;color:{vc};">{vd}</td></tr>'
    return r + (comp_sub(u, 5) if sub else "")

VHDR = header([("Ticker","9%","left"),("Company","26%","left"),("Score","9%","right"),("REX","10%","center"),("Verdict · competitors below","46%","left")])

# ---- Section 0: REVIEW LIST (top) ----
sec0 = section("★ Your Review List", "The names you asked us to review — scored and verdicted, competitor filing timeline under each.",
    VHDR, "".join(vrow(u) for u in sorted(review, key=lambda u: -smap.get(u, -1))))

# ---- Section 1: Recommend to File (file + sole filer folded in) ----
file_sys = sorted([u for u in single_stock if bucket(u) in ("file","sole") and u in smap], key=lambda u:-smap[u])[:10]
file_us = [u for u in file_sys if u not in reviewset]
sec1 = section("Recommend to File", "Top system-surfaced names with nothing live — ranked by score. Filed + sole-filer shows a 'launch on timing' verdict.",
    VHDR, "".join(vrow(u) for u in file_us))

# ---- Section 2: Live, exactly 1 competitor — TOP 5 ----
live2 = sorted([u for u in single_stock if bucket(u)=="live1"], key=lambda u:-roll(u)["total_aum"])[:5]
LHDR = header([("Ticker","8%","left"),("Company","19%","left"),("Score","8%","right"),("REX","9%","center"),("Total Live AUM","15%","right"),("1-mo Flow","10%","right"),("Verdict","31%","left")])
rows = ""
for u in live2:
    r = roll(u); vd, vc = verdict(u)
    trkc = f'${r["now"]:.0f}M <span style="color:{GRAY};font-size:10px;">(1mo ${r["mo1"]:.0f}·3mo ${r["mo3"]:.0f})</span>'
    flow = f'<span style="color:{GREEN if r["flow1"]>=25 else GRAY};font-weight:{700 if r["flow1"]>=25 else 400};">{"+" if r["flow1"]>=0 else ""}{r["flow1"]:.0f}M</span>'
    rows += (f'<tr>{tcell(u)}<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{nm(u)}</td>'
        f'<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;text-align:right;">{sc_cell(u)}</td>'
        f'<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;text-align:center;white-space:nowrap;">{pos_badge(u)}</td>'
        f'<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;text-align:right;font-size:11px;white-space:nowrap;">{trkc}</td>'
        f'<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;text-align:right;font-size:12px;">{flow}</td>'
        f'<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;font-size:11.5px;color:{vc};">{vd}</td></tr>') + comp_sub(u, 7)
sec2 = section("Live — Top 5 (Exactly 1 Competitor)", "The cleanest proven-demand lanes: one live competitor, ordered by its AUM. Demand bar: 1-mo flow ≥ +$25M or total ≥ $75M. 2+ competitors are too contested and excluded.", LHDR, rows)

# ---- Section 3: Inverse Opportunities ----
inv_us = sorted([u for u in single_stock if (roll(u)["n_long"]>=1 and roll(u)["total_aum"]>=50 and not roll(u)["has_inv"])], key=lambda u:-roll(u)["total_aum"])[:10]
IHDR = header([("Ticker","9%","left"),("Company","30%","left"),("Score","9%","right"),("Long Live (no inverse)","20%","right"),("Verdict","32%","left")])
def inv_sub(u):
    out = ""
    if u in rex_inv:
        out += f'<tr style="background:#fafbfc;"><td colspan="5" style="padding:3px 8px 3px 30px;font-size:10.5px;border-bottom:1px solid #eef1f4;color:{INK};">↳ <b>T-REX</b> · <span style="color:{BLUE};font-weight:700;">inverse {rex_inv[u]}</span></td></tr>'
    for c in inv_filers.get(u, {}).values():
        col = {"Effective":GREEN}.get(c["status"], BLUE)
        dt = f"eff {c['eff']}" if c["eff"] else (f"filed {c['filed']}" if c["filed"] else "no date")
        out += f'<tr style="background:#fafbfc;"><td colspan="5" style="padding:3px 8px 3px 30px;font-size:10.5px;border-bottom:1px solid #eef1f4;color:{INK};">↳ <b>{c["issuer"]}</b> · <span style="color:{col};font-weight:600;">inverse {c["status"]}</span> · {dt}</td></tr>'
    if not out:
        out = f'<tr style="background:#fafbfc;"><td colspan="5" style="padding:3px 8px 3px 30px;font-size:10.5px;color:{GRAY};border-bottom:1px solid #eef1f4;">↳ no inverse filed anywhere — open lane</td></tr>'
    return out
rows = ""
for u in inv_us:
    r = roll(u)
    if u in rex_inv:
        vd, vc = (f"T-REX inverse {rex_inv[u].lower()} — launch it.", BLUE)
    elif u in inv_filers:
        vd, vc = (f"{len(inv_filers[u])} competitor(s) racing the inverse — file/launch now.", ORANGE)
    else:
        vd, vc = ("Open lane — file the inverse first.", TEAL)
    rows += (f'<tr>{tcell(u)}<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{nm(u)}</td>'
        f'<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;text-align:right;">{sc_cell(u)}</td>'
        f'<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;text-align:right;font-size:12px;white-space:nowrap;">${r["total_aum"]:.0f}M long, no inverse</td>'
        f'<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;font-size:12px;color:{vc};font-weight:600;">{vd}</td></tr>') + inv_sub(u)
sec3 = section("Inverse Opportunities", "Live long ≥$50M, no inverse live. Sub-rows show who has filed an inverse (T-REX + competitors) so you can see the race for the inverse slot.", IHDR, rows)

today = date.today().strftime("%B %d, %Y")
html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>{title} · {today}</title></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1a1a2e;line-height:1.45;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f8f9fa;"><tr><td align="center" style="padding:24px 12px;">
<table width="1000" cellpadding="0" cellspacing="0" border="0" style="background:#ffffff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,0.08);max-width:1000px;">
<tr><td style="background:{NAVY};padding:20px 28px;">
  <div style="color:#ffffff;font-size:20px;font-weight:700;letter-spacing:-0.3px;">{title}</div>
  <div style="color:#9bb1cc;font-size:11px;font-weight:500;letter-spacing:1px;text-transform:uppercase;margin-top:5px;">
    {today} · scoped from the T-REX Recommendation System · li_engine_daily v1.0.1 · ★ = your review list</div></td></tr>
{sec0}{sec1}{sec2}{sec3}
</table></td></tr></table></body></html>"""
open(out, "w", encoding="utf-8").write(html)
print(f"WROTE {out} | review={len(review)} file={len(file_us)} live={len(live2)} inverse={len(inv_us)}")
