"""Foreign & Pre-IPO L&I Competition — data-driven from what's actually been filed
(rex_products for OUR filings + fund_status for competitors). Universe is the set of
foreign-listed / pre-IPO names REX or a competitor has filed on — NOT a curated seed.
See docs/LI_ADHOC_PLAYBOOK.md §4.

Usage: python foreign_ipo_brief.py OUT.html
"""
import sys, sqlite3, re, yaml
from datetime import date, datetime, timedelta

def eff_date(*vals):
    """ACTUAL effective date from the data only — no projection. '' if genuinely blank (a gap to surface)."""
    for v in vals:
        s = str(v)[:10] if v is not None else ""
        if s and s[:3] not in ("Non","nan","NaT","") and s != "0.0":
            return s
    return ""
sys.path.insert(0, "/home/jarvis/rexfinhub")
from screener.li_engine.analysis.trex_combined_v9 import _comp_issuer_of, _LI_NAME_RE, _REX_NAME_RE
from screener.li_engine.persistence import DB

NAVY="#1a1a2e"; BLUE="#0984e3"; GREEN="#27ae60"; RED="#e74c3c"; TEAL="#16a085"; GRAY="#7f8c8d"; INK="#1a1a2e"; ORANGE="#e67e22"
out = sys.argv[1]
INV = re.compile(r"inverse|short|bear|-\dx", re.I)
def collapse(s):
    s = str(s).strip().lower()
    return "Effective" if s in ("effective","active","listed","live") else "Filed"
def strip(s): return re.sub(r"\(.*?\)", "", str(s)).upper().replace(" ", "")

# ---- pull all filings: rex_products (ours, authoritative) + fund_status (competitors) ----
conn = sqlite3.connect(str(DB))
REXP = conn.execute("SELECT name,status,initial_filing_date,estimated_effective_date,official_listed_date FROM rex_products WHERE product_suite='T-REX'").fetchall()
FS = conn.execute("SELECT fund_name,status,effective_date,latest_filing_date FROM fund_status WHERE fund_name IS NOT NULL").fetchall()
conn.close()

def race(keywords):
    """Return filer rows [{issuer,status,dir,date,dated_as,rex}] for any keyword match."""
    keys = [strip(k) for k in keywords]
    rows = {}
    # OUR filings — from rex_products (authoritative for REX status)
    for nm, st, fdate, est_eff, listed in REXP:
        if not any(k in strip(nm) for k in keys): continue
        d = "inverse" if INV.search(nm) else "long"
        cur = {"issuer":"T-REX","status":collapse(st),"dir":d,"date":eff_date(listed, est_eff),"rex":True}
        key = ("T-REX", d)
        if key not in rows or _rank(cur["status"]) > _rank(rows[key]["status"]): rows[key] = cur
    # competitors — from fund_status
    for nm, st, eff, filed in FS:
        if not _LI_NAME_RE.search(nm) or _REX_NAME_RE.search(nm): continue
        if not any(k in strip(nm) for k in keys): continue
        iss = _comp_issuer_of(nm)
        if not iss: continue
        d = "inverse" if INV.search(nm) else "long"
        cur = {"issuer":iss,"status":collapse(st),"dir":d,"date":eff_date(eff),"rex":False}
        key = (iss, d)
        if key not in rows or _rank(cur["status"]) > _rank(rows[key]["status"]): rows[key] = cur
    r = list(rows.values())
    r.sort(key=lambda x: (not x["rex"], -_rank(x["status"]), x["date"] or "9999"))
    return r
def _rank(s): return {"Effective":2,"Filed":1}.get(s,0)

# ---- explicit universe from the filing investigation (foreign-listed + genuine pre-IPO) ----
# (name, market, sector, ~mktcap_$B, [keywords])  — foreign-LISTED, no liquid US ADR
FOREIGN = [
    ("Saudi Aramco","Tadawul","Energy",1800,["ARAMCO"]),
    ("Tencent Holdings","HKEX","Communication",500,["TENCENT"]),
    ("Samsung Electronics","KRX","Semiconductors",420,["SAMSUNG"]),
    ("SK Hynix","KRX","Memory",130,["SKHYNIX","HYNIX"]),
    ("BYD Co","HKEX","Consumer Disc.",100,["BYD"]),
    ("Tokyo Electron","TSE","Semiconductors",90,["TOKYOELECTRON"]),
    ("SoftBank Group","TSE","Communication",90,["SOFTBANK"]),
    ("Nintendo","TSE","Communication",80,["NINTENDO"]),
    ("MediaTek","TWSE","Semiconductors",70,["MEDIATEK"]),
    ("Advantest","TSE","Semiconductors",60,["ADVANTEST"]),
    ("Hyundai","KRX","Consumer Disc.",50,["HYUNDAI"]),
    ("Hanwha Aerospace","KRX","Defense",30,["HANWHA"]),
    ("Viva Republica (Toss)","KRX (pre-IPO)","Fintech",30,["VIVAREPUBLICA"]),
    ("Kioxia Holdings","TSE","Memory",10,["KIOXIA"]),
    ("Metaplanet","TSE","Crypto-treasury",5,["METAPLANET"]),
]
# (name, [keywords]) — genuinely private pre-IPO
PRE_IPO = [
    ("OpenAI",["OPENAI"]), ("Anthropic",["ANTHROPIC"]), ("SpaceX",["SPACEX"]),
    ("Anduril",["ANDURIL"]), ("Discord",["DISCORD"]), ("Quantinuum",["QUANTINUUM"]),
    ("Figure AI",["FIGUREAI","FIGURE"]), ("Databricks",["DATABRICKS"]),
    ("Stripe",["STRIPE"]), ("Scale AI",["SCALEAI"]),
]
# pre-IPO valuations (sourced, dated) from the yaml
ipo = yaml.safe_load(open("/home/jarvis/rexfinhub/config/ipo_watchlist.yaml"))
VAL = {}
for e in (ipo.get("high_profile_pre_ipo",[]) + ipo.get("recently_priced",[])):
    VAL[strip(e.get("company") or e.get("ticker") or "")] = (e.get("valuation_usd"), e.get("as_of_date") or e.get("last_round_date") or "", e.get("s1_filed"), e.get("s1_filed_date",""))

# ---- render helpers ----
def header(cols):
    cg="".join(f'<col style="width:{w}">' for _,w,_ in cols)
    th="".join(f'<th style="padding:7px 8px;color:white;font-size:9.5px;text-transform:uppercase;letter-spacing:.5px;text-align:{a};">{c}</th>' for c,_,a in cols)
    return f'<colgroup>{cg}</colgroup><tr style="background:{NAVY};">{th}</tr>'
def section(title, blurb, hdr, rows):
    return f"""<tr><td style="padding:16px 28px 2px;"><div style="font-size:13px;font-weight:700;color:{NAVY};text-transform:uppercase;letter-spacing:.7px;border-bottom:2px solid {NAVY};padding-bottom:5px;">{title}</div><div style="font-size:10.5px;color:{GRAY};margin:3px 0 4px;">{blurb}</div></td></tr>
<tr><td style="padding:2px 28px 6px;"><table width="100%" cellpadding="0" cellspacing="0" style="border-collapse:collapse;table-layout:fixed;">{hdr}{rows}</table></td></tr>"""
def td(v,align="left",sz="12px",color=INK,bold=False,nowrap=True):
    return f'<td style="padding:7px 8px;border-bottom:1px solid #e3e8ee;font-size:{sz};text-align:{align};color:{color};{"white-space:nowrap;overflow:hidden;text-overflow:ellipsis;" if nowrap else ""}{"font-weight:700;" if bold else ""}">{v}</td>'
def rex_badge(rows):
    rx=[r for r in rows if r["rex"]]
    if not rx: return f'<span style="color:{RED};font-weight:700;">Not filed</span>'
    best=max(rx,key=lambda r:_rank(r["status"])); c=GREEN if best["status"]=="Effective" else BLUE
    dirs="+".join("Long" if d=="long" else "Inv" for d in sorted({r["dir"] for r in rx}))
    return f'<span style="color:{c};font-weight:700;">{best["status"]} · {dirs}</span>'
def race_sub(rows, span):
    if not rows: return f'<tr style="background:#fafbfc;"><td colspan="{span}" style="padding:3px 8px 3px 30px;font-size:10.5px;color:{GRAY};border-bottom:1px solid #eef1f4;">↳ no L&I filings on record</td></tr>'
    out=""
    for r in rows:
        c=GREEN if r["status"]=="Effective" else BLUE
        who=f'<b style="color:{BLUE if r["rex"] else INK};">{r["issuer"]}</b>'
        dt=f' · eff {r["date"]}' if r["date"] else f' · <span style="color:{RED};font-weight:700;">EFF DATE MISSING</span>'
        out+=f'<tr style="background:#fafbfc;"><td colspan="{span}" style="padding:3px 8px 3px 30px;font-size:10.5px;border-bottom:1px solid #eef1f4;color:{INK};">↳ {who} · {r["dir"]} · <span style="color:{c};font-weight:600;">{r["status"]}</span>{dt}</td></tr>'
    return out

# Foreign section
FHDR=header([("Company","27%","left"),("Market","10%","left"),("Sector","16%","left"),("~Mkt Cap","11%","right"),("REX","14%","center"),("Filers (below)","22%","left")])
frows=""
for name,market,sector,cap,kws in FOREIGN:
    rows=race(kws); ncomp=len({r["issuer"] for r in rows if not r["rex"]})
    frows+=(f'<tr>{td(name,bold=True)}{td(market,sz="11px",color=GRAY)}{td(sector,sz="11px",color=GRAY)}'
        f'{td(f"~${cap:.0f}B",align="right",sz="11px",color=GRAY)}{td(rex_badge(rows),align="center")}'
        f'{td(f"{ncomp} competitor(s)" if ncomp else ("competitors: none" if rows else ""),sz="11px")}</tr>')+race_sub(rows,6)
secF=section("Foreign-Listed Stocks — L&I Filer Race", f"Foreign-listed names ({len(FOREIGN)}) REX or a competitor has filed on — driven from rex_products + fund_status, not a curated list. US-ADR names excluded (already US-tradeable). Caps approximate. (us)=T-REX.", FHDR, frows)

# Pre-IPO section
IHDR=header([("Company","20%","left"),("Valuation (as of)","24%","left"),("S-1","14%","center"),("REX","14%","center"),("Filers (below)","28%","left")])
prows=""
for name,kws in PRE_IPO:
    rows=race(kws); ncomp=len({r["issuer"] for r in rows if not r["rex"]})
    val,asof,s1,s1d = VAL.get(strip(name),(None,"",None,""))
    vtxt=(f"${val:,.0f}B" if isinstance(val,(int,float)) else '<span style="color:%s;">—</span>'%GRAY)+(f' <span style="color:{GRAY};font-size:10px;">(as of {asof})</span>' if asof else "")
    s1txt=f'<span style="color:{BLUE};font-weight:700;">filed {s1d}</span>' if s1 else f'<span style="color:{GRAY};">no S-1</span>'
    prows+=(f'<tr>{td(name,bold=True)}{td(vtxt,sz="11px",nowrap=False)}{td(s1txt,align="center",sz="11px")}'
        f'{td(rex_badge(rows),align="center")}{td(f"{ncomp} competitor(s)",sz="11px")}</tr>')+race_sub(rows,5)
secP=section("Pre-IPO — L&I Filer Race (private only)", f"Genuinely private targets ({len(PRE_IPO)}). Valuations from ipo_watchlist.yaml with as-of date — not modeled. (us)=T-REX.", IHDR, prows)

today=date.today().strftime("%B %d, %Y")
html=f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><title>Foreign &amp; Pre-IPO Competition · {today}</title></head>
<body style="margin:0;padding:0;background:#f8f9fa;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:#1a1a2e;line-height:1.45;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" style="background:#f8f9fa;"><tr><td align="center" style="padding:24px 12px;">
<table width="1000" cellpadding="0" cellspacing="0" border="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 12px rgba(0,0,0,.08);max-width:1000px;">
<tr><td style="background:{NAVY};padding:20px 28px;"><div style="color:#fff;font-size:20px;font-weight:700;letter-spacing:-.3px;">Foreign &amp; Pre-IPO — L&amp;I Competition</div>
<div style="color:#9bb1cc;font-size:11px;font-weight:500;letter-spacing:1px;text-transform:uppercase;margin-top:5px;">{today} · what WE filed + what OTHERS filed · driven from rex_products + fund_status</div></td></tr>
{secF}{secP}
</table></td></tr></table></body></html>"""
open(out,"w",encoding="utf-8").write(html)
print(f"WROTE {out} | foreign={len(FOREIGN)} pre_ipo={len(PRE_IPO)}")
