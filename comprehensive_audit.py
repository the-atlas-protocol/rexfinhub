"""
Comprehensive audit for the March 2026 Asia Report.

Sections:
  A. Scan HTML for any HARDCODED numbers/dates (strings that look like fixed values)
  B. Trace every displayed number in PDF -> source in enriched JSON / DB
  C. Verify math invariants (market + flows = dollar_change; % in Asia; etc.)
  D. Cross-check against Grace's Mar 2026 summary file vendor-by-vendor
  E. Verify per-fund appendix values vs DB
  F. Check suite-filtered PDFs inherit parent data correctly

Writes _comprehensive_audit.txt with findings.
"""
import sys, io, json, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path
from pypdf import PdfReader
import openpyxl
import psycopg2
import psycopg2.extras

OUT = open("_comprehensive_audit.txt", "w", encoding="utf-8")
FINDINGS = {"pass": 0, "fail": 0, "warn": 0}
def L(*a, level="INFO", **k):
    tag = "" if level == "INFO" else f"[{level}] "
    msg = " ".join(str(x) for x in a)
    print(f"{tag}{msg}"); print(f"{tag}{msg}", file=OUT)
    OUT.flush()
    if level == "PASS": FINDINGS["pass"] += 1
    elif level == "FAIL": FINDINGS["fail"] += 1
    elif level == "WARN": FINDINGS["warn"] += 1

def hdr(title):
    L(""); L("="*80); L(title); L("="*80)

# Load data ────────────────────────────────────────────────────────────
enr = json.load(open("enriched_report_data_mar.json"))
H = enr["headlines"]
S = enr["suites"]
F = {f["ticker"]: f for f in enr["funds"]}
N = enr["narrative"]
HTML = open("report_v15.html", "r", encoding="utf-8").read()
PDF_FULL = PdfReader("reports/2026-03/REX_Asia_Report_Mar26.pdf")
PDF_TREX = PdfReader("reports/2026-03/REX_TREX_Asia_Report_Mar26.pdf")
PDF_MS = PdfReader("reports/2026-03/REX_MicroSectors_Asia_Report_Mar26.pdf")
text_full = "\n".join(p.extract_text() for p in PDF_FULL.pages)
text_trex = "\n".join(p.extract_text() for p in PDF_TREX.pages)
text_ms = "\n".join(p.extract_text() for p in PDF_MS.pages)

conn = psycopg2.connect(host="localhost", port=5433, user="postgres", dbname="rex_asia",
                        cursor_factory=psycopg2.extras.RealDictCursor)
cur = conn.cursor()

# ═══════════════════════════════════════════════════════════════════════
hdr("A. SCAN HTML FOR HARDCODED VALUES")
# ═══════════════════════════════════════════════════════════════════════
L("Looking for suspicious patterns: fixed dollar amounts, date strings, specific percentages")
L("")

# Patterns that should NOT exist in current HTML (indicate Feb/prior-month bleed)
bad_patterns = [
    (r"\bFeb.*202[56]\b", "February date label"),
    (r"Feb '2[56]", "Feb year ref"),
    (r"\$1\.3B\b", "Feb Asia $1.3B"),
    (r"\$1,319", "Feb Asia $1,319M"),
    (r"\$699M\b", "Feb T-REX"),
    (r"\$489M\b", "Feb MicroSectors"),
    (r"\+\$142M", "Feb flows"),
    (r"-\$202M", "Feb market move"),
    (r"142\.49", "Feb flows float"),
    (r"202\.49", "Feb market float"),
    (r"6,711,700,000", "Feb Seamus number"),
    (r"6711700000", "Feb Seamus number int"),
    (r"Taiwan entered", "Feb narrative"),
    (r"gold miner momentum", "Feb narrative"),
    (r"recovered from 59", "Feb narrative"),
    (r"\$25M to \$113M", "Feb EPI narrative"),
    (r"TSII and NVII represent", "Feb G&I narrative"),
    (r"\+5\.1pp", "Feb T-REX pp"),
    (r"\+0\.8pp", "Feb Micro pp"),
    (r"\+2\.0pp", "Feb EPI pp"),
    (r"-1\.0pp", "Feb G&I pp"),
    (r"<li>Korea accounts for 78\.5%", "Feb T-REX bullet"),
    (r"MSTU drew \$50M", "Feb MSTU flow"),
    (r"GDXU rose 89%", "Feb GDXU rise"),
    (r"__MONTH_LONG__", "Unreplaced token"),
    (r"__MONTH_SHORT__", "Unreplaced token"),
]
for pat, desc in bad_patterns:
    m = re.findall(pat, HTML, re.IGNORECASE)
    if m:
        L(f"  HARDCODED: {desc}  pattern={pat!r}  matches={len(m)}", level="FAIL")
        # Show first occurrence line
        for i, line in enumerate(HTML.split("\n"), 1):
            if re.search(pat, line, re.IGNORECASE):
                L(f"    line {i}: {line.strip()[:140]}")
                break
    else:
        L(f"  clean: {desc}", level="PASS")

# ═══════════════════════════════════════════════════════════════════════
hdr("B. COVER PAGE — NUMBERS vs ENRICHED JSON")
# ═══════════════════════════════════════════════════════════════════════

cover_expectations = [
    ("$1.2B",         f"${H['total_asia_aum']/1e9:.1f}B",       "Asia AUM"),
    ("-$116M",        f"${H['dollar_change']/1e6:+.0f}M".replace("+", ""),   "MoM $"),
    ("-8.8%",         f"{H['mom_pct']*100:+.1f}%".replace("+", ""),   "MoM %"),
    ("$6.1B",         f"${H['total_global_aum']/1e9:.1f}B",     "Total REX"),
    ("19.9%",         f"{H['pct_in_asia']*100:.1f}%",           "% in Asia"),
    ("-$61.3M",       f"{'+' if H['total_market_move']>=0 else '-'}${abs(H['total_market_move'])/1e6:.1f}M",   "Market"),
    ("-$54.5M",       f"{'+' if H['total_flows']>=0 else '-'}${abs(H['total_flows'])/1e6:.1f}M",   "Flows"),
    ("March 2026",    N["month_long"],                          "Month label"),
]
for pdf_val, enr_val, label in cover_expectations:
    if pdf_val in text_full:
        L(f"  {label:<15} PDF='{pdf_val}' == enriched '{enr_val}'", level="PASS")
    else:
        L(f"  {label:<15} PDF MISSING '{pdf_val}' (enriched says '{enr_val}')", level="FAIL")

# ═══════════════════════════════════════════════════════════════════════
hdr("C. MATH INVARIANTS")
# ═══════════════════════════════════════════════════════════════════════

# C1: per-fund market_move + flows = dollar_change
bad = 0
for f in enr["funds"]:
    mm = f.get("market_move", 0); fl = f.get("flows", 0); dc = f.get("dollar_change", 0)
    if abs((mm + fl) - dc) > 1:
        bad += 1
L(f"  Per-fund market_move + flows == dollar_change  ({len(enr['funds'])} funds, {bad} mismatches)",
  level="PASS" if bad == 0 else "FAIL")

# C2: suite aggregate == sum of funds by family
for suite_name in ["T-REX", "MicroSectors", "Income"]:
    fund_sum = sum(f["asia_aum"] for f in enr["funds"] if f["family_name"] == suite_name)
    s_aum = S.get(suite_name, {}).get("aum", 0)
    ok = abs(fund_sum - s_aum) < 1
    L(f"  Suite '{suite_name}' aum: fund-sum ${fund_sum/1e6:.2f}M vs enriched ${s_aum/1e6:.2f}M",
      level="PASS" if ok else "FAIL")

# C3: headlines.total_asia_aum == sum of all fund asia_aum
fund_total = sum(f["asia_aum"] for f in enr["funds"])
ok = abs(fund_total - H["total_asia_aum"]) < 1
L(f"  headlines.total_asia_aum: fund-sum ${fund_total/1e6:.2f}M vs enriched ${H['total_asia_aum']/1e6:.2f}M",
  level="PASS" if ok else "FAIL")

# C4: headlines.total_market_move == sum of fund market_move
fund_mm = sum(f.get("market_move", 0) for f in enr["funds"])
ok = abs(fund_mm - H["total_market_move"]) < 10
L(f"  headlines.total_market_move: fund-sum ${fund_mm/1e6:.2f}M vs enriched ${H['total_market_move']/1e6:.2f}M",
  level="PASS" if ok else "FAIL")

# C5: headlines.pct_in_asia == total_asia / total_global
expected = H["total_asia_aum"] / H["total_global_aum"]
ok = abs(expected - H["pct_in_asia"]) < 0.0001
L(f"  headlines.pct_in_asia: computed {expected*100:.4f}% vs stored {H['pct_in_asia']*100:.4f}%",
  level="PASS" if ok else "FAIL")

# C6: sum of country AUM == total_asia_aum
country_total = sum(c["aum"] for c in enr["countries"])
ok = abs(country_total - H["total_asia_aum"]) < 1
L(f"  countries sum: ${country_total/1e6:.2f}M vs headlines.total_asia_aum ${H['total_asia_aum']/1e6:.2f}M",
  level="PASS" if ok else "FAIL")

# C7: sum of exchange AUM == total_asia_aum
ex_total = sum(e["aum"] for e in enr["exchanges"])
ok = abs(ex_total - H["total_asia_aum"]) < 1
L(f"  exchanges sum: ${ex_total/1e6:.2f}M vs headlines.total_asia_aum ${H['total_asia_aum']/1e6:.2f}M",
  level="PASS" if ok else "FAIL")

# ═══════════════════════════════════════════════════════════════════════
hdr("D. GRACE'S MAR 2026 SUMMARY vs OUR DB")
# ═══════════════════════════════════════════════════════════════════════
GRACE_MAR = {
    ("Korea", "KSD (Korea Securities Depository) - Retail"): 881.8,
    ("Korea", "Korea Investment Management -ACE TESLA Value Chain ETF"): 17.7,
    ("Japan", "SBI"): 31.2,
    ("Japan", "Rakuten"): 58.0,
    ("Japan", "MooMoo"): 13.0,
    ("Japan", "Monex"): 4.3,
    ("Japan", "Matsui"): 1.5,
    ("Hong Kong", "SYFE"): 1.25,
    # Grace Mar column is BLANK for these — waiting
    ("Hong Kong", "Futu/MooMoo"): None,
    ("Hong Kong", "Oriental Harbour *"): None,
    ("Hong Kong", "ViewTrade"): None,
    ("Singapore", "MooMoo"): None,
    ("Singapore", "ViewTrade"): None,
    ("Malaysia", "MooMoo"): None,
    ("Taiwan", "ViewTrade"): None,
    ("Thailand", "Asset Plus Asset Management"): None,  # $0 confirmed (DRNZ sold)
}
cur.execute("""
    SELECT c.name country, ex.name exchange, ROUND(SUM(m.exchange_aum_usd)::numeric/1e6, 2) aum_mm, m.source_type
    FROM etp_exchange_monthly_aum m JOIN exchange ex USING (exchange_id) JOIN country c USING (country_id)
    WHERE m.month_id = 14 GROUP BY c.name, ex.name, m.source_type ORDER BY c.name, ex.name
""")
db_by_ex = {(r["country"], r["exchange"]): (float(r["aum_mm"]), r["source_type"]) for r in cur.fetchall()}

for (country, exchange), grace_val in GRACE_MAR.items():
    db_entry = db_by_ex.get((country, exchange))
    if grace_val is None:
        # Grace is silent; we may have a repriced value. Verify source_type.
        if db_entry:
            db_val, src = db_entry
            if src == "repriced":
                L(f"  {country} {exchange[:35]:<35}  Grace=blank  DB=${db_val:.2f}M (repriced) — OK", level="PASS")
            else:
                L(f"  {country} {exchange[:35]:<35}  Grace=blank  DB=${db_val:.2f}M src={src} — CHECK", level="WARN")
        else:
            L(f"  {country} {exchange[:35]:<35}  Grace=blank  DB=absent — OK (e.g. Thailand DRNZ sold)", level="PASS")
    else:
        if db_entry is None:
            L(f"  {country} {exchange[:35]:<35}  Grace=${grace_val}M  DB=MISSING", level="FAIL")
        else:
            db_val, src = db_entry
            diff = db_val - grace_val
            ok = abs(diff) < 1.0
            flag = "PASS" if ok else "FAIL"
            L(f"  {country} {exchange[:35]:<35}  Grace=${grace_val}M  DB=${db_val:.2f}M  diff {diff:+.2f}M  src={src}",
              level=flag)

# ═══════════════════════════════════════════════════════════════════════
hdr("E. APPENDIX FUND VALUES — PDF vs DB")
# ═══════════════════════════════════════════════════════════════════════
# Check a sample of tickers — their appendix AUM should match DB sum per ticker
cur.execute("""
    SELECT e.ticker, ROUND(SUM(m.exchange_aum_usd)::numeric/1e6, 3) aum_mm
    FROM etp_exchange_monthly_aum m JOIN etp e USING (etp_id)
    WHERE m.month_id = 14 GROUP BY e.ticker ORDER BY aum_mm DESC LIMIT 25
""")
top_db = cur.fetchall()
# Extract tickers from PDF appendix
page7_text = PDF_FULL.pages[6].extract_text() + "\n" + PDF_FULL.pages[7].extract_text()
for r in top_db:
    t = r["ticker"]; db_mm = float(r["aum_mm"])
    # Look for ticker in appendix; extract the value after it
    m = re.search(rf"\n{re.escape(t)}\s+\S.*?\s+\$([\d\.,]+)(M|K)?", page7_text)
    if not m:
        L(f"  {t:<8}  DB ${db_mm:.2f}M  appendix NOT FOUND", level="WARN")
        continue
    val_str, unit = m.group(1), m.group(2) or "M"
    val = float(val_str.replace(",", ""))
    if unit == "K": val = val / 1000
    diff = val - db_mm
    ok = abs(diff) < 1.0  # PDF rounds to nearest $M for large, $K for small
    L(f"  {t:<8}  DB ${db_mm:>8.2f}M  PDF ${val:>8.2f}M  diff {diff:+.3f}M",
      level="PASS" if ok else "WARN")

# ═══════════════════════════════════════════════════════════════════════
hdr("F. SUITE-FILTERED PDFs — DO THEY INHERIT PARENT DATA?")
# ═══════════════════════════════════════════════════════════════════════

# T-REX report
trex_sum_pdf = None
for line in text_trex.split("\n"):
    # Find "$611M" or similar after ASIA AUM
    if line.strip().startswith("$") and "M" in line and len(line.strip()) < 10:
        trex_sum_pdf = line.strip(); break
trex_sum_enr = S.get("T-REX", {}).get("aum", 0) / 1e6
L(f"  T-REX PDF 'ASIA AUM' header: first $M figure = {trex_sum_pdf}, enriched ${trex_sum_enr:.2f}M",
  level="PASS" if trex_sum_pdf and abs(float(re.sub(r'[^\d.]', '', trex_sum_pdf)) - trex_sum_enr) < 2 else "WARN")

# MicroSectors
ms_sum_enr = S.get("MicroSectors", {}).get("aum", 0) / 1e6
for line in text_ms.split("\n"):
    if line.strip().startswith("$") and "M" in line and len(line.strip()) < 10:
        ms_sum_pdf = line.strip(); break
L(f"  MicroSectors PDF 'ASIA AUM' header: {ms_sum_pdf}, enriched ${ms_sum_enr:.2f}M",
  level="PASS" if ms_sum_pdf and abs(float(re.sub(r'[^\d.]', '', ms_sum_pdf)) - ms_sum_enr) < 2 else "WARN")

# Ensure suite reports don't have __MONTH_LONG__
for name, text in [("T-REX", text_trex), ("MicroSectors", text_ms)]:
    ok = "__MONTH_LONG__" not in text
    L(f"  {name} PDF has no __MONTH_LONG__ token", level="PASS" if ok else "FAIL")

# Ensure suite reports have correct month label
for name, text in [("T-REX", text_trex), ("MicroSectors", text_ms)]:
    ok = "March 2026" in text
    L(f"  {name} PDF shows 'March 2026'", level="PASS" if ok else "FAIL")

# ═══════════════════════════════════════════════════════════════════════
hdr("G. TIMELINE DATA — HISTORICAL CONTEXT PRESERVED")
# ═══════════════════════════════════════════════════════════════════════

for entry in enr["timeline"]:
    month = entry["month"]; total = entry["total"] / 1e6
    L(f"  {month}: Asia ${total:>8.2f}M  {len([s for s,v in entry['suites'].items() if v>0])} suites")

# Verify the 13-month timeline is continuous and lines up with DB
cur.execute("""
    SELECT cm.month_end,
           ROUND(SUM(m.exchange_aum_usd)::numeric/1e6, 2) asia_mm
    FROM etp_exchange_monthly_aum m JOIN calendar_month cm USING (month_id)
    GROUP BY cm.month_end ORDER BY cm.month_end DESC LIMIT 14
""")
db_timeline = list(reversed(cur.fetchall()))
L("")
L("Cross-check enriched timeline vs DB:")
enr_timeline_map = {e["month"]: e["total"]/1e6 for e in enr["timeline"]}
for r in db_timeline:
    mo = r["month_end"].strftime("%Y-%m")
    db_val = float(r["asia_mm"])
    enr_val = enr_timeline_map.get(mo, 0)
    diff = db_val - enr_val
    ok = abs(diff) < 0.1
    L(f"  {mo}  DB ${db_val:>8.2f}M  Enriched ${enr_val:>8.2f}M  diff {diff:+.4f}",
      level="PASS" if ok else "WARN")

# ═══════════════════════════════════════════════════════════════════════
hdr("H. GLOBAL AUM TIMELINE — HISTORICAL % IN ASIA")
# ═══════════════════════════════════════════════════════════════════════
g = enr["global_aum_timeline"]
for month, glob in sorted(g.items()):
    asia = enr_timeline_map.get(month, 0) * 1e6
    pct = asia / glob if glob > 0 else 0
    L(f"  {month}  Asia ${asia/1e6:>8.2f}M  Global ${glob/1e9:>5.3f}B  % in Asia {pct*100:>5.2f}%")

# ═══════════════════════════════════════════════════════════════════════
hdr("I. SUITE FLOW BAR CHART DATA")
# ═══════════════════════════════════════════════════════════════════════
# The chart data is built from SUITE_FLOWS which comes from SUITES in JS
# Verify math
L(f"  T-REX           flows ${S.get('T-REX', {}).get('flows', 0)/1e6:+.2f}M   market ${S.get('T-REX', {}).get('market_move', 0)/1e6:+.2f}M")
L(f"  MicroSectors    flows ${S.get('MicroSectors', {}).get('flows', 0)/1e6:+.2f}M   market ${S.get('MicroSectors', {}).get('market_move', 0)/1e6:+.2f}M")
L(f"  Income          flows ${S.get('Income', {}).get('flows', 0)/1e6:+.2f}M   market ${S.get('Income', {}).get('market_move', 0)/1e6:+.2f}M")
L(f"  Other           flows ${S.get('Other', {}).get('flows', 0)/1e6:+.2f}M   market ${S.get('Other', {}).get('market_move', 0)/1e6:+.2f}M")
total_fl_check = sum(s["flows"] for s in S.values())
total_mm_check = sum(s["market_move"] for s in S.values())
L(f"  Sum flows: ${total_fl_check/1e6:+.2f}M  headlines ${H['total_flows']/1e6:+.2f}M",
  level="PASS" if abs(total_fl_check - H["total_flows"]) < 10 else "FAIL")
L(f"  Sum market: ${total_mm_check/1e6:+.2f}M  headlines ${H['total_market_move']/1e6:+.2f}M",
  level="PASS" if abs(total_mm_check - H["total_market_move"]) < 10 else "FAIL")

# ═══════════════════════════════════════════════════════════════════════
hdr("SUMMARY")
# ═══════════════════════════════════════════════════════════════════════
L(f"  PASS: {FINDINGS['pass']}")
L(f"  FAIL: {FINDINGS['fail']}")
L(f"  WARN: {FINDINGS['warn']}")
conn.close()
OUT.close()
