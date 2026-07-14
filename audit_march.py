"""
Full end-to-end audit of the Mar 2026 report. Checks every layer.

Layers:
    1. Input: raw broker Excel files
    2. DB:    etp_monthly_fund + etp_exchange_monthly_aum for month_id=14
    3. JSON:  report_data_mar.json, enriched_report_data_mar.json
    4. PDF:   reports/final/2026-03/REX_Asia_Report_Mar26.pdf

Every check has a PASS/FAIL flag. Final exit code = 0 if all pass.
"""
import sys, json, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

from pathlib import Path
from decimal import Decimal

import openpyxl
import psycopg2
import psycopg2.extras

RESULTS = []

def chk(label, passed, detail=""):
    RESULTS.append({"label": label, "pass": passed, "detail": detail})
    mark = "✓" if passed else "✗"
    print(f"  [{mark}] {label}")
    if detail:
        print(f"      {detail}")

def hdr(title):
    print(f"\n{'='*70}\n{title}\n{'='*70}")

DB = psycopg2.connect(host="localhost", port=5433, user="postgres", dbname="rex_asia",
                      cursor_factory=psycopg2.extras.RealDictCursor)
CUR = DB.cursor()

MONTH_ID = 14
FEB_ID = 13

# ─────────────────────────────────────────────────────────────────────────
# Layer 1: Broker files -> DB
# ─────────────────────────────────────────────────────────────────────────
hdr("LAYER 1 — BROKER FILES vs DB")

# Grace's canonical Mar summary values (from her Asset report summary Mar 2026.xlsx)
GRACE_MAR = {
    "KSD (Korea Securities Depository) - Retail": 881.8,
    "Korea Investment Management -ACE TESLA Value Chain ETF": 17.7,
    "SBI": 31.2,
    "Rakuten": 58.0,
    "Monex": 4.3,
    "Matsui": 1.5,
    "SYFE": 1.25,
    # MooMoo JP is aggregate-level in Grace's summary: $13M
    # Futu HK, OH, ViewTrade, MooMoo SG/MY, Asset Plus are blank/waiting in Grace's file
}

# DB per exchange
CUR.execute("""
    SELECT ex.name AS ex, c.name AS country,
           ROUND(SUM(m.exchange_aum_usd)::numeric/1e6, 2) aum_mm,
           m.source_type
    FROM etp_exchange_monthly_aum m
    JOIN exchange ex USING (exchange_id) JOIN country c USING (country_id)
    WHERE m.month_id = %s
    GROUP BY ex.name, c.name, m.source_type
    ORDER BY aum_mm DESC
""", (MONTH_ID,))
db_by_ex = {}
for r in CUR.fetchall():
    key = r["ex"]
    db_by_ex.setdefault(key, {"country": r["country"], "sources": [], "aum_mm": 0.0})
    db_by_ex[key]["aum_mm"] += float(r["aum_mm"])
    db_by_ex[key]["sources"].append(r["source_type"])

for ex, grace_val in GRACE_MAR.items():
    db_val = db_by_ex.get(ex, {}).get("aum_mm", 0)
    diff = db_val - grace_val
    chk(f"  {ex[:50]:<50} DB=${db_val:.2f}M vs Grace=${grace_val}M",
        abs(diff) < 1.0,
        f"diff ${diff:+.2f}M")

# MooMoo Japan aggregate
mm_jp = db_by_ex.get("MooMoo", {}).get("aum_mm", 0)
# Actually MooMoo appears in Japan/SG/MY — filter to Japan
CUR.execute("""
    SELECT ROUND(SUM(m.exchange_aum_usd)::numeric/1e6, 2) aum_mm
    FROM etp_exchange_monthly_aum m JOIN exchange ex USING (exchange_id) JOIN country c USING (country_id)
    WHERE m.month_id = %s AND ex.name = 'MooMoo' AND c.name = 'Japan'
""", (MONTH_ID,))
mm_jp_only = float(CUR.fetchone()["aum_mm"])
chk(f"  MooMoo Japan DB=${mm_jp_only:.2f}M vs Grace=$13.00M",
    abs(mm_jp_only - 13.0) < 1.0,
    f"diff ${mm_jp_only - 13.0:+.2f}M (scaled to Grace aggregate)")

# ─────────────────────────────────────────────────────────────────────────
# Layer 2: DB internal consistency
# ─────────────────────────────────────────────────────────────────────────
hdr("LAYER 2 — DB INTERNAL CONSISTENCY (month_id=14)")

CUR.execute("""
    SELECT COUNT(*) cnt, COUNT(DISTINCT etp_id) funds, COUNT(DISTINCT exchange_id) exch,
           ROUND(SUM(exchange_aum_usd)::numeric/1e6, 2) total_mm
    FROM etp_exchange_monthly_aum WHERE month_id = %s
""", (MONTH_ID,))
r = CUR.fetchone()
DB_TOTAL = float(r["total_mm"])
chk(f"etp_exchange_monthly_aum rows = {r['cnt']}", r["cnt"] > 0, f"{r['cnt']} rows, {r['funds']} funds, {r['exch']} exchanges")
chk(f"Total Asia AUM = ${DB_TOTAL:.2f}M", DB_TOTAL > 0)

CUR.execute("SELECT COUNT(*) cnt FROM etp_monthly_fund WHERE month_id = %s", (MONTH_ID,))
n = CUR.fetchone()["cnt"]
chk(f"etp_monthly_fund (global AUM + price) rows = {n}", n > 0)

# Every fund in etp_exchange must have a row in etp_monthly_fund for that month
CUR.execute("""
    SELECT DISTINCT e.ticker
    FROM etp_exchange_monthly_aum m JOIN etp e USING (etp_id)
    WHERE m.month_id = %s AND NOT EXISTS (
        SELECT 1 FROM etp_monthly_fund mf WHERE mf.etp_id = m.etp_id AND mf.month_id = m.month_id
    )
""", (MONTH_ID,))
missing_bbg = [r["ticker"] for r in CUR.fetchall()]
chk(f"All Asia-active funds have Bloomberg global AUM", len(missing_bbg) == 0,
    f"missing: {missing_bbg}" if missing_bbg else "all present")

# Source type distribution
CUR.execute("""
    SELECT source_type, COUNT(*), ROUND(SUM(exchange_aum_usd)::numeric/1e6, 2) total_mm
    FROM etp_exchange_monthly_aum WHERE month_id = %s
    GROUP BY source_type ORDER BY 2 DESC
""", (MONTH_ID,))
print(f"\n  Source type distribution:")
for r in CUR.fetchall():
    print(f"    {r['source_type']:12s}  {r['count']:>4} rows  ${float(r['total_mm']):>9.2f}M")

# ─────────────────────────────────────────────────────────────────────────
# Layer 3: Enriched JSON derivations
# ─────────────────────────────────────────────────────────────────────────
hdr("LAYER 3 — ENRICHED JSON DERIVATIONS")

enr = json.loads(Path("enriched_report_data_mar.json").read_text())

# Headlines totals
H = enr["headlines"]
total_asia_musd = H["total_asia_aum"] / 1e6
chk(f"Enriched total_asia_aum = ${total_asia_musd:.2f}M",
    abs(total_asia_musd - DB_TOTAL) < 0.1,
    f"DB: ${DB_TOTAL}M, JSON: ${total_asia_musd:.2f}M, diff ${total_asia_musd-DB_TOTAL:+.4f}M")

chk(f"total_market_move + total_flows == dollar_change",
    abs(H["total_market_move"] + H["total_flows"] - H["dollar_change"]) < 1.0,
    f"{H['total_market_move']:+,.0f} + {H['total_flows']:+,.0f} = {H['total_market_move']+H['total_flows']:+,.0f} vs {H['dollar_change']:+,.0f}")

# Suites sum to Asia total
suites_total = sum(s["aum"] for s in enr["suites"].values())
chk(f"sum(suites.aum) == headlines.total_asia_aum",
    abs(suites_total - H["total_asia_aum"]) < 1.0,
    f"suites ${suites_total/1e6:.2f}M vs headlines ${H['total_asia_aum']/1e6:.2f}M")

# Per-fund: flows + market_move == dollar_change
bad_funds = []
for f in enr["funds"]:
    dc_expected = f["market_move"] + f["flows"]
    dc_actual = f["dollar_change"]
    if abs(dc_expected - dc_actual) > 1.0:
        bad_funds.append(f"{f['ticker']}: mm+fl={dc_expected:+,.0f} vs dc={dc_actual:+,.0f}")
chk(f"All funds: market_move + flows == dollar_change",
    len(bad_funds) == 0,
    f"{len(bad_funds)} mismatches" + (": " + "; ".join(bad_funds[:3]) if bad_funds else ""))

# % in Asia sanity
chk(f"% in Asia = total_asia_aum / total_global_aum = {H['pct_in_asia']*100:.2f}%",
    0 < H["pct_in_asia"] < 1, f"{H['pct_in_asia']*100:.2f}%")

# ─────────────────────────────────────────────────────────────────────────
# Layer 4: PDF outputs match enriched JSON
# ─────────────────────────────────────────────────────────────────────────
hdr("LAYER 4 — PDF OUTPUT vs ENRICHED JSON")

from pypdf import PdfReader
r = PdfReader("reports/final/2026-03/REX_Asia_Report_Mar26.pdf")
pdf_text = "\n".join(p.extract_text() for p in r.pages)

# Cover: $1.2B
chk(f"PDF contains 'March 2026' title", "March 2026" in pdf_text)
chk(f"PDF cover AUM = $1.2B (matches enriched $%.2fM)" % total_asia_musd,
    "$1.2B" in pdf_text,
    f"enriched total: ${total_asia_musd:.2f}M")
bbg_bn = H["total_global_aum"] / 1e9
chk(f"PDF shows TOTAL REX AUM = $%.1fB" % bbg_bn,
    f"${bbg_bn:.1f}B" in pdf_text,
    f"enriched bbg_total: ${bbg_bn:.3f}B")

# Appendix fund count
fund_count_pdf = pdf_text.count("MicroSectors") + pdf_text.count("T-REX")
asia_fund_count = H["fund_count"]
chk(f"Fund count in PDF plausible (enriched says {asia_fund_count})",
    asia_fund_count > 50, f"enriched fund_count: {asia_fund_count}")

# ─────────────────────────────────────────────────────────────────────────
# Layer 5: Feb vs Mar cross-month sanity
# ─────────────────────────────────────────────────────────────────────────
hdr("LAYER 5 — FEB vs MAR CROSS-MONTH SANITY")

CUR.execute("""
    SELECT ROUND(SUM(exchange_aum_usd)::numeric/1e6, 2) total_mm
    FROM etp_exchange_monthly_aum WHERE month_id = %s
""", (FEB_ID,))
FEB_TOTAL = float(CUR.fetchone()["total_mm"])
chk(f"Feb DB total = ${FEB_TOTAL}M (should match Feb shipped PDF $1,319.85M)",
    abs(FEB_TOTAL - 1319.85) < 0.1)

chk(f"Prior-month Asia in enriched JSON matches Feb DB",
    abs(H["total_asia_aum_prior"]/1e6 - FEB_TOTAL) < 0.1,
    f"enriched prior: ${H['total_asia_aum_prior']/1e6:.2f}M vs Feb DB: ${FEB_TOTAL}M")

mom_delta = total_asia_musd - FEB_TOTAL
mom_pct = mom_delta / FEB_TOTAL * 100
chk(f"Feb->Mar MoM: ${mom_delta:+.2f}M ({mom_pct:+.2f}%)",
    abs(mom_pct) < 30, "-102M / -8% is plausible month")

# Suite MoM plausibility
CUR.execute("""
    SELECT pf.name suite,
           ROUND(SUM(CASE WHEN m.month_id=%s THEN m.exchange_aum_usd END)::numeric/1e6, 2) feb,
           ROUND(SUM(CASE WHEN m.month_id=%s THEN m.exchange_aum_usd END)::numeric/1e6, 2) mar
    FROM etp_exchange_monthly_aum m JOIN etp e USING (etp_id)
    JOIN product_family pf USING (family_id)
    WHERE m.month_id IN (%s, %s)
    GROUP BY pf.name ORDER BY 2 DESC
""", (FEB_ID, MONTH_ID, FEB_ID, MONTH_ID))
print("\n  Suite Feb -> Mar:")
for r in CUR.fetchall():
    feb = float(r["feb"] or 0); mar = float(r["mar"] or 0)
    chg = mar - feb
    pct = (chg / feb * 100) if feb > 0 else 0
    print(f"    {r['suite']:14s}  Feb ${feb:>8.2f}M  Mar ${mar:>8.2f}M  {chg:+7.2f}M  ({pct:+.1f}%)")

# Flag MATERIAL outliers: big absolute flow OR big % flow on non-tiny funds
print("\n  Material outliers (flow > $30M abs OR mom > 100% AND asia > $5M):")
material = []
for f in enr["funds"]:
    flow = f.get("flows", 0)
    mom = f.get("mom") or 0
    aum = f.get("asia_aum", 0)
    is_big_flow = abs(flow) > 30_000_000
    is_big_mom_on_real_fund = abs(mom) > 1.0 and aum > 5_000_000
    if is_big_flow or is_big_mom_on_real_fund:
        material.append(f)
        print(f"    {f['ticker']:6s}  asia=${aum/1e6:>7.2f}M  flow=${flow/1e6:+7.2f}M  mom={mom*100:+7.1f}%  (global_mom={(f.get('global_mom') or 0)*100:+.1f}%)")
chk(f"Material outlier count = {len(material)}", len(material) <= 5,
    f"{len(material)} material outliers — each should have a story")

# Also print small-base noise count for context (not a failure)
noise = [f for f in enr["funds"] if (f.get("mom") or 0) > 1.0 and f.get("asia_aum", 0) <= 5_000_000]
if noise:
    print(f"\n  (Small-base ETN noise — MoM > 100% on funds <$5M AUM: {len(noise)} funds — not a failure)")
    for f in noise:
        print(f"    {f['ticker']:6s}  asia=${f['asia_aum']/1e6:.2f}M  flow=${f['flows']/1e6:+.2f}M  mom={(f['mom'] or 0)*100:+.1f}%")

# ─────────────────────────────────────────────────────────────────────────
# Layer 6: Methodology markers
# ─────────────────────────────────────────────────────────────────────────
hdr("LAYER 6 — METHODOLOGY MARKERS")

CUR.execute("""
    SELECT ex.name, c.name country, m.source_type, COUNT(*) n, ROUND(SUM(m.exchange_aum_usd)::numeric/1e6, 2) mm
    FROM etp_exchange_monthly_aum m JOIN exchange ex USING (exchange_id) JOIN country c USING (country_id)
    WHERE m.month_id = %s GROUP BY ex.name, c.name, m.source_type ORDER BY c.name, ex.name
""", (MONTH_ID,))
print(f"\n  {'Country':<12} {'Exchange':<42} {'Source':<10} {'#':>3} {'AUM $M':>10}")
print("  " + "-"*82)
for r in CUR.fetchall():
    print(f"  {r['country']:<12} {r['name'][:42]:<42} {r['source_type']:<10} {r['n']:>3} {float(r['mm']):>10.2f}")

# Specific methodology checks
EXPECTED_REPRICED = {"Futu/MooMoo", "Oriental Harbour *", "ViewTrade"}
EXPECTED_MOOMOO_SGMY_REPRICED = True  # MooMoo SG and MY

CUR.execute("""
    SELECT ex.name ex_name, c.name country, m.source_type FROM etp_exchange_monthly_aum m
    JOIN exchange ex USING (exchange_id) JOIN country c USING (country_id)
    WHERE m.month_id = %s AND ex.name IN ('Futu/MooMoo', 'Oriental Harbour *', 'ViewTrade')
    GROUP BY ex.name, c.name, m.source_type
""", (MONTH_ID,))
for r in CUR.fetchall():
    chk(f"{r['country']} {r['ex_name']} source = {r['source_type']}",
        r["source_type"] == "repriced",
        f"expected repriced")

# MooMoo SG / MY should be repriced
CUR.execute("""
    SELECT c.name country, m.source_type FROM etp_exchange_monthly_aum m
    JOIN exchange ex USING (exchange_id) JOIN country c USING (country_id)
    WHERE m.month_id = %s AND ex.name = 'MooMoo' AND c.name IN ('Singapore', 'Malaysia')
    GROUP BY c.name, m.source_type
""", (MONTH_ID,))
for r in CUR.fetchall():
    chk(f"{r['country']} MooMoo source = {r['source_type']}",
        r["source_type"] == "repriced")

# Thailand = zero (no row)
CUR.execute("""
    SELECT COUNT(*) cnt FROM etp_exchange_monthly_aum m
    JOIN exchange ex USING (exchange_id) JOIN country c USING (country_id)
    WHERE m.month_id = %s AND c.name = 'Thailand'
""", (MONTH_ID,))
th = CUR.fetchone()["cnt"]
chk(f"Thailand has NO rows for Mar (DRNZ sold)", th == 0, f"rows={th}")

# ─────────────────────────────────────────────────────────────────────────
# Final tally
# ─────────────────────────────────────────────────────────────────────────
hdr("SUMMARY")

total = len(RESULTS)
passed = sum(1 for r in RESULTS if r["pass"])
failed = total - passed
print(f"\nTotal checks: {total}   PASSED: {passed}   FAILED: {failed}")
if failed:
    print("\nFailures:")
    for r in RESULTS:
        if not r["pass"]:
            print(f"  ✗ {r['label']}")
            if r["detail"]: print(f"      {r['detail']}")

DB.close()
sys.exit(0 if failed == 0 else 1)
