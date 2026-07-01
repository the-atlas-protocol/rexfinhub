"""Inverse launch board — two plays, report-styled HTML tables.
A) Inverse on a proven long (long is a hit, no inverse exists).
B) New Foreign pairs (launch Long+Inverse together).
C) New Pre-IPO pairs (file Long+Inverse together).
"""
from datetime import date
from html import escape
from screener.li_engine.analysis import trex_combined_v9 as t

BLUE, NAVY, GREEN, RED, ORANGE, GRAY = "#2563eb", "#1e293b", "#16a34a", "#dc2626", "#ea580c", "#64748b"
BORDER = "#e2e8f0"

def rex_legs(race):
    """REX long/inverse status from a filer race (rex rows)."""
    rx = [r for r in race if r["rex"]]
    lo = [r for r in rx if r["dir"] == "long"]
    iv = [r for r in rx if r["dir"] == "inverse"]
    best = lambda rows: max((r["status"] for r in rows), key=lambda s: {"Effective": 2, "Filed": 1}.get(s, 0)) if rows else None
    return best(lo), best(iv)

def coverage_badge(long_s, inv_s):
    if long_s and inv_s:
        return f'<span style="color:{GREEN};font-weight:700;">BOTH</span>'
    if long_s and not inv_s:
        return f'<span style="color:{ORANGE};font-weight:700;">LONG only</span>'
    if inv_s and not long_s:
        return f'<span style="color:{ORANGE};font-weight:700;">INVERSE only</span>'
    return f'<span style="color:{RED};font-weight:700;">NEITHER</span>'

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
            f'<div style="font-size:11px;color:{GRAY};margin-bottom:8px;max-width:920px;">{blurb}</div>')

# ---- A: inverse on a proven long -------------------------------------------
# Built directly (not via load_inverse_gap, which now strictly drops any name with
# a registered inverse) so we can show the THREE-TIER inverse status: OPEN (no
# inverse anywhere) / RACING (competitor inverse filed but PENDING) / TAKEN
# (competitor inverse Effective/live). Mechanism 1 ranks proven longs by AUM.
import re, sqlite3
con = sqlite3.connect("/home/jarvis/rexfinhub/data/etp_tracker.db")
ss = t._single_stock_set()
cov = {}
rex = con.execute("SELECT name, COALESCE(status_cached,status) FROM rex_products WHERE product_suite='T-REX'").fetchall()
NRE = re.compile(r"T-REX\s+[\d.]+X\s+(LONG|INVERSE)\s+(.+?)\s+DAILY", re.I)
for nm, st in rex:
    m = NRE.search(str(nm))
    if not m:
        continue
    d = "inverse" if m.group(1).upper() == "INVERSE" else "long"
    u = re.sub(r"\s+", " ", m.group(2).upper().strip())
    cov.setdefault(u, {"long": None, "inverse": None})
    cov[u][d] = st

# top live long per single-stock underlier ($100M+), from the trading feed
ml = con.execute("""SELECT map_li_underlier, map_li_direction, COALESCE(issuer_nickname,issuer),
                           ticker, fund_name, aum
                    FROM mkt_master_data
                    WHERE primary_category='LI' AND market_status='ACTV' AND aum IS NOT NULL
                      AND map_li_underlier IS NOT NULL AND fund_name NOT LIKE '%MICROSECTORS%'""").fetchall()
longs, trading_inv = {}, set()
for u, d, iss, tk, fn, aum in ml:
    cu = t._canon(u)
    if cu not in ss:
        continue
    dl = str(d).lower()
    if "long" in dl and (aum or 0) >= 100:
        if cu not in longs or aum > longs[cu]["aum"]:
            longs[cu] = {"aum": float(aum), "issuer": iss, "ticker": tk, "fund": fn}
    if re.search("short|inv", dl):
        trading_inv.add(cu)
# inverse registrations from fund_status -> {underlier: 'Effective'|'PENDING'|...}
_u_re = re.compile(r"(?:SHORT|INVERSE|ULTRASHORT)\s+([A-Z]{1,6})\b|\b([A-Z]{1,6})\s+(?:BEAR|SHORT)\b")
reg_inv = {}
for fn, fst in con.execute("SELECT fund_name,status FROM fund_status WHERE fund_name IS NOT NULL").fetchall():
    s = str(fn)
    if not t._INV_RE.search(s) or t._REX_NAME_RE.search(s):
        continue
    for m in _u_re.finditer(s.upper()):
        cu = t._canon(m.group(1) or m.group(2))
        if cu in ss:
            prev = reg_inv.get(cu, ("", ""))
            # prefer an Effective record over a pending one
            if "effective" in str(fst).lower() or "effective" not in str(prev[1]).lower():
                reg_inv[cu] = (s, str(fst))

def inv_tier(u):
    if u in trading_inv:
        return "TAKEN", "trading", ""
    if u in reg_inv:
        fn, st = reg_inv[u]
        return ("TAKEN", st, fn) if "effective" in st.lower() else ("RACING", st, fn)
    return "OPEN", "", ""

TIER_RANK = {"OPEN": 0, "RACING": 1, "TAKEN": 2}
arows = []
for u, L in longs.items():
    tier, st, fn = inv_tier(u)
    arows.append((u, L, tier, st, fn))
arows.sort(key=lambda x: (TIER_RANK[x[2]], -x[1]["aum"]))

rowsA = ""
for i, (u, L, tier, st, fn) in enumerate(arows[:30]):
    rex_long = cov.get(u, {}).get("long")
    tier_html = {
        "OPEN":   f'<span style="color:{GREEN};font-weight:700;">OPEN — no inverse</span>',
        "RACING": f'<span style="color:{ORANGE};font-weight:700;">RACING — {escape(fn[:30])} ({escape(st)})</span>',
        "TAKEN":  f'<span style="color:{GRAY};">TAKEN — {escape((fn or "trading inverse")[:30])}</span>',
    }[tier]
    if tier == "OPEN":
        act = "Launch our inverse" if rex_long else "File inverse — open seat on a proven name"
        acol = GREEN
    elif tier == "RACING":
        act = "Race — file inverse now (competitor pending)"
        acol = ORANGE
    else:
        act = "Seat taken — monitor only"
        acol = GRAY
    rowsA += tr([
        f'<b>{escape(u)}</b>',
        escape(str(L["ticker"])), escape(str(L["issuer"]))[:18],
        f'<b style="color:{NAVY};">${L["aum"]:,.0f}M</b>',
        (f'<span style="color:{BLUE};font-weight:700;">{rex_long}</span>' if rex_long else f'<span style="color:{GRAY};">—</span>'),
        tier_html,
        f'<b style="color:{acol};">{act}</b>',
    ], i)
nopen = sum(1 for a in arows if a[2] == "OPEN")
nrace = sum(1 for a in arows if a[2] == "RACING")
tableA = f'<table style="border-collapse:collapse;width:100%;">{th(["Underlier","Top Long","Long Issuer","Long AUM","REX Long","Inverse Status","Action"])}{rowsA}</table>'

# ---- B: new foreign pairs ---------------------------------------------------
fc = t.load_foreign_competition()
fb = [x for x in fc if x["race"]]            # only names with an active filer race
fb.sort(key=lambda x: (-x["ncomp"], -x["cap"]))
rowsB = ""
for i, x in enumerate(fb[:20]):
    ls, isv = rex_legs(x["race"])
    comps = sorted({r["issuer"] for r in x["race"] if not r["rex"]})
    miss = "Launch L+I pair" if not (ls or isv) else ("Add inverse" if ls and not isv else ("Add long" if isv and not ls else "Have both"))
    rowsB += tr([
        f'<b>{escape(x["name"])[:30]}</b>', escape(x["ticker"]),
        f'${x["cap"]/1e9:,.0f}B', f'<b>{x["ncomp"]}</b> ({escape(", ".join(comps)[:36])})',
        coverage_badge(ls, isv),
        f'<b style="color:{RED if not (ls or isv) else ORANGE};">{miss}</b>',
    ], i)
tableB = f'<table style="border-collapse:collapse;width:100%;">{th(["Foreign Underlier","Ticker","Mkt Cap","Competitors Filing","REX Coverage","Action"])}{rowsB}</table>'

# ---- C: new pre-IPO pairs ---------------------------------------------------
pc = t.load_preipo_competition()
pc = [x for x in pc if x["ncomp"] > 0 or [r for r in x["race"] if r["rex"]]]
pc.sort(key=lambda x: -(x["valuation_usd"] or 0))
rowsC = ""
for i, x in enumerate(pc):
    ls, isv = rex_legs(x["race"])
    comps = sorted({r["issuer"] for r in x["race"] if not r["rex"]})
    miss = "File L+I pair" if not (ls or isv) else ("Add inverse" if ls and not isv else ("Add long" if isv and not ls else "Have both"))
    rowsC += tr([
        f'<b>{escape(x["company"])[:26]}</b>',
        f'${(x["valuation_usd"] or 0):,.0f}B',
        f'<b>{x["ncomp"]}</b> ({escape(", ".join(comps)[:36])})',
        coverage_badge(ls, isv),
        f'<b style="color:{RED if not (ls or isv) else ORANGE};">{miss}</b>',
    ], i)
tableC = f'<table style="border-collapse:collapse;width:100%;">{th(["Pre-IPO Underlier","Valuation","Competitors Filing","REX Coverage","Action"])}{rowsC}</table>'

html = f"""<!doctype html><html><head><meta charset="utf-8"><title>T-REX Inverse Launch Board</title></head>
<body style="font-family:-apple-system,Segoe UI,Arial,sans-serif;color:#0f172a;max-width:1100px;margin:24px auto;padding:0 20px;">
<h1 style="font-size:22px;margin-bottom:2px;">T-REX Inverse Launch Board</h1>
<div style="color:{GRAY};font-size:12px;margin-bottom:6px;">{date.today().strftime('%B %d, %Y')} · two ways to play inverse</div>
{section("A · Inverse on a Proven Long", "A 2x LONG is already a hit (big AUM) — the long proved the demand, so add the inverse. Ranked OPEN → RACING → TAKEN, then by long AUM. <b style='color:#16a34a;'>OPEN</b> = no inverse exists anywhere (file/launch now). <b style='color:#ea580c;'>RACING</b> = a competitor inverse is FILED but still PENDING (we can still beat it to launch). <b style='color:#64748b;'>TAKEN</b> = a competitor inverse is already Effective/trading (seat gone — monitor only). Inverse-existence now checks the SEC registration tracker, not just the Bloomberg trading feed.", BLUE)}
{tableA}
{section("B · New Foreign Pairs — Launch Long + Inverse Together", "Newest foreign-listed underliers where competitors are racing. Thesis is BOTH directions win, so ship as a pair ASAP. REX Coverage shows what legs we already have — NEITHER (red) = we are behind the race.", "#34495e")}
{tableB}
{section("C · New Pre-IPO Pairs — File Long + Inverse Together", "Newest pre-IPO underliers in the filer race. File both legs now; they launch when the name lists. NEITHER = competitors are filing and REX is absent.", "#7c3aed")}
{tableC}
</body></html>"""

out = "/home/jarvis/rexfinhub/outputs/inverse_launch_board.html"
open(out, "w", encoding="utf-8").write(html)
print("WROTE", out, len(html), "chars")
print(f"TableA rows: {rowsA.count('<tr')} (OPEN={nopen} RACING={nrace}) | TableB: {rowsB.count('<tr')} | TableC: {rowsC.count('<tr')}")
con.close()
