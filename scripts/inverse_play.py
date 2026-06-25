"""Inverse launch board — report-styled HTML tables.
A) Inverse on a proven long: proven longs ($100M+) with NO live trading inverse,
   ranked by long AUM. Shows REX long filing status + whether anyone has FILED
   (not launched) an inverse yet.
B) Foreign pairs: foreign-listed names (high mcap OR a competitor has filed),
   ordered by market cap — file long+inverse together.
C) Pre-IPO pairs: pre-IPO underliers — file long+inverse together.
"""
import re, sqlite3
from datetime import date
from html import escape
from screener.li_engine.analysis import trex_combined_v9 as t

BLUE, NAVY, GREEN, RED, ORANGE, GRAY, PURPLE = "#2563eb", "#1e293b", "#16a34a", "#dc2626", "#ea580c", "#64748b", "#7c3aed"
BORDER = "#e2e8f0"
con = sqlite3.connect("/home/jarvis/rexfinhub/data/etp_tracker.db")
ss = t._single_stock_set()

def th(cols):
    return ("<tr>" + "".join(
        f'<th style="text-align:left;padding:7px 10px;background:{NAVY};color:#fff;'
        f'font-size:11px;text-transform:uppercase;letter-spacing:.4px;">{c}</th>' for c in cols) + "</tr>")

def tr(cells, i):
    bg = "#ffffff" if i % 2 == 0 else "#f8fafc"
    return f'<tr style="background:{bg};">' + "".join(
        f'<td style="padding:6px 10px;border-bottom:1px solid {BORDER};font-size:12px;">{c}</td>' for c in cells) + "</tr>"

def section(title, blurb, color):
    return (f'<h2 style="font-size:16px;color:{color};margin:26px 0 4px;">{title}</h2>'
            f'<div style="font-size:11px;color:{GRAY};margin-bottom:8px;max-width:980px;">{blurb}</div>')

def fmt_cap(c):
    c = float(c or 0)
    if c >= 1e9:
        return f"${c/1e9:,.1f}B"
    if c >= 1e6:
        return f"${c/1e6:,.0f}M"
    return "—"

# ---- REX coverage (status + dates) parsed from product names ----------------
NRE = re.compile(r"T-REX\s+[\d.]+X\s+(LONG|INVERSE)\s+(.+?)\s+DAILY", re.I)
cov = {}
for nm, st, ee, ld in con.execute("""SELECT name, COALESCE(status_cached,status),
        estimated_effective_date, official_listed_date
        FROM rex_products WHERE product_suite='T-REX'""").fetchall():
    m = NRE.search(str(nm))
    if not m:
        continue
    d = "inverse" if m.group(1).upper() == "INVERSE" else "long"
    u = re.sub(r"\s+", " ", m.group(2).upper().strip())
    cov.setdefault(u, {}).setdefault(d, (str(st).lower(), ee, ld))

def rex_long_html(u):
    rec = cov.get(u, {}).get("long")
    if not rec:
        return f'<span style="color:{GRAY};">—</span>'
    st, ee, ld = rec
    if st in ("effective", "listed", "active"):
        dt = (str(ld) or str(ee) or "")[:10]
        return f'<span style="color:{GREEN};font-weight:700;">Effective</span> {escape(dt)}'
    dt = (str(ee) or "")[:10]
    return f'<span style="color:{BLUE};font-weight:700;">Filed</span> <span style="color:{GRAY};">eff {escape(dt)}</span>'

# ---- live longs + live trading inverses from the Bloomberg trading feed -------
longs, trading_inv = {}, set()
for u, d, iss, tk, aum in con.execute("""SELECT map_li_underlier, map_li_direction,
        COALESCE(issuer_nickname,issuer), ticker, aum
        FROM mkt_master_data WHERE primary_category='LI' AND market_status='ACTV'
          AND aum IS NOT NULL AND map_li_underlier IS NOT NULL
          AND fund_name NOT LIKE '%MICROSECTORS%'""").fetchall():
    cu = t._canon(u)
    if cu not in ss:
        continue
    dl = str(d).lower()
    if "long" in dl and (aum or 0) >= 100:
        if cu not in longs or aum > longs[cu]["aum"]:
            longs[cu] = {"aum": float(aum), "issuer": iss, "ticker": tk}
    if re.search("short|inv", dl):
        trading_inv.add(cu)

# ---- inverse FILINGS (registered, not yet trading) from fund_status ----------
# direction-adjacent ticker extraction (collision-safe) filtered to single stocks
_adj = re.compile(r"(?:SHORT|INVERSE|ULTRASHORT)\s+([A-Z]{1,6})\b|\b([A-Z]{1,6})\s+(?:BEAR|SHORT)\b")
inv_filing = {}  # underlier -> (issuer, status, eff_date)
for fn, st, eff in con.execute("SELECT fund_name,status,effective_date FROM fund_status WHERE fund_name IS NOT NULL").fetchall():
    s = str(fn)
    if not t._INV_RE.search(s) or t._REX_NAME_RE.search(s):
        continue
    for m in _adj.finditer(s.upper()):
        cu = t._canon(m.group(1) or m.group(2))
        if cu not in ss:
            continue
        rec = (t._comp_issuer_of(s) or s.split()[0], t._race_collapse(st), t._race_eff(eff))
        prev = inv_filing.get(cu)
        if prev is None or (t._race_rank(rec[1]) > t._race_rank(prev[1])):
            inv_filing[cu] = rec

# ---- Table A: proven longs with NO live trading inverse, ranked by AUM --------
arows = sorted(((u, L) for u, L in longs.items() if u not in trading_inv),
               key=lambda x: -x[1]["aum"])
rowsA = ""
for i, (u, L) in enumerate(arows[:30]):
    f = inv_filing.get(u)
    if f:
        inv_html = f'<span style="color:{ORANGE};font-weight:700;">{escape(f[1])}</span> · {escape(str(f[0]))[:16]} {escape(str(f[2])[:10])}'
        act, acol = f"File inverse — race vs {escape(str(f[0]))[:14]}", ORANGE
    else:
        inv_html = f'<span style="color:{GREEN};font-weight:700;">OPEN — none filed</span>'
        act, acol = "File inverse — open seat", GREEN
    rowsA += tr([
        f'<b>{escape(u)}</b>',
        f'{escape(str(L["ticker"]))} · {escape(str(L["issuer"]))[:16]}',
        f'<b style="color:{NAVY};">${L["aum"]:,.0f}M</b>',
        rex_long_html(u),
        inv_html,
        f'<b style="color:{acol};">{act}</b>',
    ], i)
nopen = sum(1 for u, _ in arows if u not in inv_filing)
tableA = f'<table style="border-collapse:collapse;width:100%;">{th(["Underlier","Top Live Long","Long AUM","REX Long","Inverse Filed?","Action"])}{rowsA}</table>'

# ---- B/C helpers -------------------------------------------------------------
def rex_legs(race):
    rx = [r for r in race if r["rex"]]
    best = lambda dirn: next((r["status"] for r in sorted(rx, key=lambda x: -t._race_rank(x["status"])) if r["dir"] == dirn), None)
    return best("long"), best("inverse")

def cov_badge(ls, iv):
    if ls and iv: return f'<span style="color:{GREEN};font-weight:700;">BOTH filed</span>'
    if ls:        return f'<span style="color:{ORANGE};font-weight:700;">LONG only</span>'
    if iv:        return f'<span style="color:{ORANGE};font-weight:700;">INVERSE only</span>'
    return f'<span style="color:{RED};font-weight:700;">NEITHER</span>'

def pair_action(ls, iv):
    if ls and iv: return ("Filed — monitor", GRAY)
    if ls:        return ("File inverse", ORANGE)
    if iv:        return ("File long", ORANGE)
    return ("File both — long + inverse", RED)

# ---- Table B: foreign — high mcap OR competitor filed, ordered by mcap --------
HIGH = 10e9
fc = t.load_foreign_competition()
fb = []
for x in fc:
    if x["cap"] >= HIGH or x["ncomp"] > 0 or any(r["rex"] for r in x["race"]):
        fb.append(x)
fb.sort(key=lambda x: -x["cap"])
rowsB = ""
for i, x in enumerate(fb):
    ls, iv = rex_legs(x["race"])
    comps = sorted({r["issuer"] for r in x["race"] if not r["rex"]})
    act, acol = pair_action(ls, iv)
    comp_html = (f'<b>{x["ncomp"]}</b> · {escape(", ".join(comps))[:40]}' if comps else f'<span style="color:{GRAY};">none</span>')
    rowsB += tr([
        f'<b>{escape(str(x["name"]))[:30]}</b>', escape(str(x["market"])),
        f'<b style="color:{NAVY};">{fmt_cap(x["cap"])}</b>',
        comp_html, cov_badge(ls, iv),
        f'<b style="color:{acol};">{act}</b>',
    ], i)
tableB = f'<table style="border-collapse:collapse;width:100%;">{th(["Foreign Underlier","Market","Mkt Cap","Competitors Filed","REX Coverage","Action"])}{rowsB}</table>'

# ---- Table C: pre-IPO — ordered by valuation ---------------------------------
pc = sorted(t.load_preipo_competition(), key=lambda x: -(x["valuation_usd"] or 0))
rowsC = ""
for i, x in enumerate(pc):
    ls, iv = rex_legs(x["race"])
    comps = sorted({r["issuer"] for r in x["race"] if not r["rex"]})
    act, acol = pair_action(ls, iv)
    comp_html = (f'<b>{x["ncomp"]}</b> · {escape(", ".join(comps))[:40]}' if comps else f'<span style="color:{GRAY};">none</span>')
    val = x["valuation_usd"] or 0
    rowsC += tr([
        f'<b>{escape(str(x["company"]))[:26]}</b>',
        f'<b style="color:{NAVY};">${val:,.0f}B</b>' if val else "—",
        comp_html, cov_badge(ls, iv),
        f'<b style="color:{acol};">{act}</b>',
    ], i)
tableC = f'<table style="border-collapse:collapse;width:100%;">{th(["Pre-IPO Underlier","Valuation","Competitors Filed","REX Coverage","Action"])}{rowsC}</table>'

html = f"""<!doctype html><html><head><meta charset="utf-8"><title>T-REX Inverse Launch Board</title></head>
<body style="font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#0f172a;max-width:1120px;margin:24px auto;padding:0 20px;">
<h1 style="font-size:22px;margin-bottom:2px;">T-REX Inverse &amp; Pairs Launch Board</h1>
<div style="color:{GRAY};font-size:12px;margin-bottom:6px;">{date.today().strftime('%B %d, %Y')} · two ways to play inverse</div>
{section("A · Inverse on a Proven Long", "A 2x LONG is already a hit (live, $100M+ AUM) and has NO live trading inverse yet — the long proved the demand, so file the inverse. Ranked by long AUM. <b>Inverse Filed?</b> shows whether anyone has REGISTERED an inverse but not launched it: <b style='color:#16a34a;'>OPEN</b> = nobody (clean seat) · <b style='color:#ea580c;'>Filed/Effective</b> = a competitor registered one and we are racing them to launch. Names with an already-trading inverse are excluded.", BLUE)}
{tableA}
{section("B · Foreign Pairs — File Long + Inverse Together", "Foreign-listed underliers, ordered by market cap. Included when market cap is large (≥$10B) OR a competitor has already filed an L&amp;I product on the name (small-cap foreign only earns a slot once someone else files). REX Coverage shows our current legs; file whatever is missing.", "#34495e")}
{tableB}
{section("C · Pre-IPO Pairs — File Long + Inverse Together", "Pre-IPO underliers from the watchlist, ordered by valuation. File both legs now; they launch when the name lists.", PURPLE)}
{tableC}
</body></html>"""

out = "/home/jarvis/rexfinhub/outputs/inverse_launch_board.html"
open(out, "w", encoding="utf-8").write(html)
print("WROTE", out, len(html), "chars")
print(f"TableA: {rowsA.count('<tr')} proven-longs-no-live-inverse ({nopen} OPEN) | TableB: {rowsB.count('<tr')} foreign | TableC: {rowsC.count('<tr')} pre-IPO")
con.close()
