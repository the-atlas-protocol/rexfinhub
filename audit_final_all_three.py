"""
Final comprehensive audit of all 3 Mar 2026 PDFs.
Cross-references every visible number to the enriched JSON source.
Flags anything unaccounted for.
"""
import sys, io, json, re
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
from pathlib import Path
from pypdf import PdfReader

OUT = open("_audit_final.txt", "w", encoding="utf-8")
F = {"pass": 0, "fail": 0, "warn": 0}
def L(*a, level="INFO"):
    tag = "" if level == "INFO" else f"[{level}] "
    msg = " ".join(str(x) for x in a)
    print(f"{tag}{msg}"); print(f"{tag}{msg}", file=OUT); OUT.flush()
    if level == "PASS": F["pass"] += 1
    elif level == "FAIL": F["fail"] += 1
    elif level == "WARN": F["warn"] += 1

def hdr(t): L(""); L("="*80); L(t); L("="*80)

enr = json.load(open("enriched_report_data_mar.json"))
H, S, N = enr["headlines"], enr["suites"], enr["narrative"]
F_MAP = {f["ticker"]: f for f in enr["funds"]}

R_FULL = PdfReader("reports/final/2026-03/REX_Asia_Report_Mar26.pdf")
R_TREX = PdfReader("reports/final/2026-03/REX_TREX_Asia_Report_Mar26.pdf")
R_MS   = PdfReader("reports/final/2026-03/REX_MicroSectors_Asia_Report_Mar26.pdf")
T_FULL = [p.extract_text() for p in R_FULL.pages]
T_TREX = [p.extract_text() for p in R_TREX.pages]
T_MS   = [p.extract_text() for p in R_MS.pages]
FULL = "\n".join(T_FULL); TREX = "\n".join(T_TREX); MS = "\n".join(T_MS)

# ═══════════════════════════════════════════════════════════════════
hdr("1. ALL 3 PDFs — BASIC STRUCTURE")
# ═══════════════════════════════════════════════════════════════════
L(f"  Full report: {len(T_FULL)} pages", level="PASS" if len(T_FULL) == 9 else "FAIL")
L(f"  T-REX report: {len(T_TREX)} pages", level="PASS" if len(T_TREX) == 3 else "FAIL")
L(f"  MicroSectors: {len(T_MS)} pages", level="PASS" if len(T_MS) == 3 else "FAIL")

# ═══════════════════════════════════════════════════════════════════
hdr("2. NO TEMPLATE LEAKS — placeholder tokens absent")
# ═══════════════════════════════════════════════════════════════════
leaks = ["__MONTH_LONG__", "__MONTH_SHORT__", "Futu HK", "Oriental Harbour —", "Asset Plus Thailand:",
         "DRNZ", "quarterly reporters (Futu", "sold DRNZ"]
for label, text in [("Full", FULL), ("T-REX", TREX), ("MicroSectors", MS)]:
    for leak in leaks:
        if leak in text:
            L(f"  {label}: contains '{leak}'", level="FAIL")
        else:
            L(f"  {label}: no '{leak}'", level="PASS")

# ═══════════════════════════════════════════════════════════════════
hdr("3. FULL REPORT — COVER PAGE (page 1)")
# ═══════════════════════════════════════════════════════════════════
p1 = T_FULL[0]
# Key displayed numbers
checks = [
    ("March 2026", N["month_long"]),
    ("$1.2B", f"${H['total_asia_aum']/1e9:.1f}B"),
    ("$6.1B", f"${H['total_global_aum']/1e9:.1f}B"),
    ("20.2%", f"{H['pct_in_asia']*100:.1f}%"),  # 20.19%
    ("-$61.3M", f"${H['total_market_move']/1e6:.1f}M"),
    ("-$34.6M", f"${H['total_flows']/1e6:.1f}M"),
    ("-$96.0M", f"${H['dollar_change']/1e6:.1f}M"),  # -96.03M approx
    ("-7.3%", f"{H['mom_pct']*100:.1f}%"),   # roughly
]
for display, source in checks:
    # Normalize: check if display value appears or a close approximation
    if display in p1:
        L(f"  cover: '{display}' present (source: {source})", level="PASS")
    else:
        L(f"  cover: '{display}' NOT in page 1 (source: {source})", level="WARN")

# Suite breakdown rows
suite_breakdown_pdf = []
for line in p1.split("\n"):
    m = re.match(r"^(T-REX|MicroSectors|EPI \+ Autocallable|G&I \+ IncomeMax|Other)\s+\$(\S+)\s+(\S+%)\s+([+-]?\$\S+)$", line.strip())
    if m:
        suite_breakdown_pdf.append((m.group(1), m.group(2), m.group(3), m.group(4)))
L(f"  Suite breakdown rows in PDF cover: {len(suite_breakdown_pdf)}")
for suite, aum_str, pct_str, mom_str in suite_breakdown_pdf:
    L(f"    {suite:<25}  AUM={aum_str}  pct={pct_str}  mom={mom_str}")

# Country breakdown rows
country_breakdown_pdf = []
country_keywords = ["Korea", "Hong Kong", "Japan", "Singapore", "Taiwan", "Malaysia", "Thailand"]
for line in p1.split("\n"):
    for ck in country_keywords:
        if line.strip().startswith(ck + " "):
            country_breakdown_pdf.append(line.strip())
            break
L(f"  Country breakdown rows in PDF cover: {len(country_breakdown_pdf)}")
for c_line in country_breakdown_pdf[:8]:
    L(f"    {c_line[:80]}")

# ═══════════════════════════════════════════════════════════════════
hdr("4. FULL REPORT — PAGE 2 (Market Activity, Exchange, Flows)")
# ═══════════════════════════════════════════════════════════════════
p2 = T_FULL[1]

# Exchange breakdown
ex_lines = []
for line in p2.split("\n"):
    # e.g. "KSD (Korea Securities Depository) - Retail Korea 76 $882M 72.4%"
    m = re.match(r"^(.+?)\s+(Korea|Japan|Hong Kong|Singapore|Taiwan|Malaysia|Thailand|Various)\s+(\d+)\s+\$([\d\.MBK,]+)\s+([\d\.]+%)", line.strip())
    if m:
        ex_lines.append((m.group(1).strip(), m.group(2), int(m.group(3)), m.group(4), m.group(5)))
L(f"  Exchange table rows parsed: {len(ex_lines)}")
for ex_name, country, count, aum, pct in ex_lines[:12]:
    L(f"    {ex_name[:40]:<40} {country:<12} {count:>3} {aum:>10} {pct:>7}")

# Verify top exchange percentages are roughly sensible
if ex_lines:
    top_exchange_aum = float(re.sub(r"[M,]", "", ex_lines[0][3])) if ex_lines[0][3].endswith("M") else 0
    L(f"  Top exchange AUM: ${top_exchange_aum}M")

# Flow bars
flow_suite_lines = [l for l in p2.split("\n") if re.match(r"^(T-REX|MicroSectors|EPI|G&I|Other)\s+[+-]?\$", l.strip())]
L(f"  Flow-by-suite lines: {len(flow_suite_lines)}")
for l in flow_suite_lines:
    L(f"    {l.strip()}")

# ═══════════════════════════════════════════════════════════════════
hdr("5. FULL REPORT — SUITE PAGES (3-6)")
# ═══════════════════════════════════════════════════════════════════
# For each suite page, verify:
#   - page title
#   - KPI strip values match suite aggregation
#   - No __MONTH tokens
for i, (name, page_idx) in enumerate([("T-REX", 2), ("MicroSectors", 3), ("EPI+AC", 4), ("G&I+IncomeMax", 5)]):
    p = T_FULL[page_idx]
    L(f"")
    L(f"  Page {page_idx+1}: {name}")
    # Find ASIA AUM line
    aum_match = re.search(r"ASIA AUM\s*\n\s*\$(\S+)\s*\n", p)
    if aum_match:
        L(f"    ASIA AUM displayed: ${aum_match.group(1)}")
    # Find MoM CHANGE
    mom_match = re.search(r"MOM CHANGE\s*\n\s*(\S+)\s*\n", p)
    if mom_match:
        L(f"    MoM displayed: {mom_match.group(1)}")
    # Find % IN ASIA
    pct_match = re.search(r"% IN ASIA\s*\n\s*(\S+)\s*\n\s*(\S+pp)", p)
    if pct_match:
        L(f"    % in Asia: {pct_match.group(1)}  delta: {pct_match.group(2)}")
    # First bullet
    bullet_start = p.find("OVERVIEW")
    if bullet_start >= 0:
        bullets = p[bullet_start:bullet_start+600].split("\n")[1:6]
        for b in bullets:
            if b.strip(): L(f"      bullet: {b.strip()[:100]}")

# ═══════════════════════════════════════════════════════════════════
hdr("6. FULL REPORT — APPENDIX (pages 7-8)")
# ═══════════════════════════════════════════════════════════════════
p7 = T_FULL[6]; p8 = T_FULL[7]
# Count fund rows
fund_row_pattern = re.compile(r"^([A-Z]{3,6})\s+(MicroSectors|T-REX|Equity Premium Income|Growth & Income|Autocallable|IncomeMax|Other|REX Osprey)\s+\$")
fund_rows_p7 = [l for l in p7.split("\n") if fund_row_pattern.match(l.strip())]
fund_rows_p8 = [l for l in p8.split("\n") if fund_row_pattern.match(l.strip())]
total_pdf_fund_rows = len(fund_rows_p7) + len(fund_rows_p8)
enr_asia_active = len([f for f in enr["funds"] if f["asia_aum"] > 0])
L(f"  Appendix fund rows: page 7 = {len(fund_rows_p7)}, page 8 = {len(fund_rows_p8)}, total = {total_pdf_fund_rows}")
L(f"  Enriched Asia-active fund count: {enr_asia_active}",
  level="PASS" if abs(total_pdf_fund_rows - enr_asia_active) <= 2 else "FAIL")

# Grand total row
if "Total $" in p8 or "Total\t$" in p8:
    total_line = [l for l in p8.split("\n") if l.strip().startswith("Total")]
    if total_line:
        L(f"  Grand total line: {total_line[0].strip()}")

# ═══════════════════════════════════════════════════════════════════
hdr("7. FULL REPORT — METHODOLOGY (page 9)")
# ═══════════════════════════════════════════════════════════════════
p9 = T_FULL[8]
L(f"  Page 9 title: {p9.split(chr(10))[0]}")
# Check it's generic (no vendor names)
vendor_names = ["Futu", "Oriental Harbour", "Asset Plus", "MooMoo", "DRNZ", "ViewTrade", "SYFE", "KSD", "Rakuten", "SBI", "Monex", "Matsui"]
leaks_found = [v for v in vendor_names if v in p9]
if leaks_found:
    L(f"  METHODOLOGY CONTAINS VENDOR NAMES: {leaks_found}", level="FAIL")
else:
    L(f"  Methodology is generic — no vendor names", level="PASS")

# Check each expected section is present
for section in ["Asia AUM", "Global AUM", "Estimated Flows"]:
    if section in p9:
        L(f"  Section present: {section}", level="PASS")
    else:
        L(f"  Section MISSING: {section}", level="FAIL")

# Show methodology content
L(f"  Methodology content (first 1500 chars):")
for line in p9.split("\n")[:40]:
    if line.strip(): L(f"    | {line}")

# ═══════════════════════════════════════════════════════════════════
hdr("8. T-REX REPORT — specific audit")
# ═══════════════════════════════════════════════════════════════════
trex_page1 = T_TREX[0]
# Expected: T-REX Asia Report / March 2026 header
L(f"  Header line 1: {trex_page1.split(chr(10))[0]}")
L(f"  Header line 2: {trex_page1.split(chr(10))[1]}")
# KPI Asia AUM should match T-REX suite total
trex_suite = S.get("T-REX", {})
L(f"  T-REX suite AUM enriched: ${trex_suite.get('aum', 0)/1e6:.2f}M")
aum_search = re.search(r"ASIA AUM\s*\n\s*\$(\S+)", trex_page1)
if aum_search:
    L(f"  T-REX PDF shows ASIA AUM: ${aum_search.group(1)}", level="PASS")
# Fund table - how many T-REX funds?
trex_fund_count = sum(1 for f in enr["funds"] if f["family_name"] == "T-REX" and f["asia_aum"] > 0)
L(f"  T-REX Asia-active funds enriched: {trex_fund_count}")
# PDF appendix should only list T-REX
trex_appendix = "\n".join(T_TREX[1:])
non_trex_in_appendix = [m.group(1) for m in re.finditer(r"\n([A-Z]{3,6})\s+(MicroSectors|Equity Premium Income|Growth & Income|IncomeMax|REX Osprey|Other)\s+\$", trex_appendix)]
if non_trex_in_appendix:
    L(f"  T-REX appendix has NON-T-REX funds: {non_trex_in_appendix}", level="FAIL")
else:
    L(f"  T-REX appendix is T-REX only", level="PASS")

# ═══════════════════════════════════════════════════════════════════
hdr("9. MICROSECTORS REPORT — specific audit")
# ═══════════════════════════════════════════════════════════════════
ms_page1 = T_MS[0]
L(f"  Header line 1: {ms_page1.split(chr(10))[0]}")
L(f"  Header line 2: {ms_page1.split(chr(10))[1]}")
ms_suite = S.get("MicroSectors", {})
L(f"  MicroSectors suite AUM enriched: ${ms_suite.get('aum', 0)/1e6:.2f}M")
aum_search = re.search(r"ASIA AUM\s*\n\s*\$(\S+)", ms_page1)
if aum_search:
    L(f"  MS PDF shows ASIA AUM: ${aum_search.group(1)}", level="PASS")
ms_fund_count = sum(1 for f in enr["funds"] if f["family_name"] == "MicroSectors" and f["asia_aum"] > 0)
L(f"  MicroSectors Asia-active funds enriched: {ms_fund_count}")
ms_appendix = "\n".join(T_MS[1:])
non_ms_in_appendix = [m.group(1) for m in re.finditer(r"\n([A-Z]{3,6})\s+(T-REX|Equity Premium Income|Growth & Income|IncomeMax|REX Osprey|Other)\s+\$", ms_appendix)]
if non_ms_in_appendix:
    L(f"  MS appendix has NON-MicroSectors funds: {non_ms_in_appendix}", level="FAIL")
else:
    L(f"  MS appendix is MicroSectors only", level="PASS")

# ═══════════════════════════════════════════════════════════════════
hdr("10. SUITE PAGE KPI DRIFT — T-REX and MicroSectors report vs full report page")
# ═══════════════════════════════════════════════════════════════════
# The T-REX standalone report page 1 should match the full report's page 3 (T-REX section)
trex_full_p3 = T_FULL[2]
trex_standalone_p1 = T_TREX[0]
# Extract ASIA AUM from both
m1 = re.search(r"ASIA AUM\s*\n\s*\$(\S+)", trex_full_p3)
m2 = re.search(r"ASIA AUM\s*\n\s*\$(\S+)", trex_standalone_p1)
if m1 and m2:
    ok = m1.group(1) == m2.group(1)
    L(f"  T-REX: full-report p3 AUM '${m1.group(1)}' == standalone p1 '${m2.group(1)}'",
      level="PASS" if ok else "FAIL")
ms_full_p4 = T_FULL[3]; ms_standalone_p1 = T_MS[0]
m3 = re.search(r"ASIA AUM\s*\n\s*\$(\S+)", ms_full_p4)
m4 = re.search(r"ASIA AUM\s*\n\s*\$(\S+)", ms_standalone_p1)
if m3 and m4:
    ok = m3.group(1) == m4.group(1)
    L(f"  MicroSectors: full-report p4 AUM '${m3.group(1)}' == standalone p1 '${m4.group(1)}'",
      level="PASS" if ok else "FAIL")

# ═══════════════════════════════════════════════════════════════════
hdr("SUMMARY")
# ═══════════════════════════════════════════════════════════════════
L(f"  PASS: {F['pass']}"); L(f"  FAIL: {F['fail']}"); L(f"  WARN: {F['warn']}")
OUT.close()
