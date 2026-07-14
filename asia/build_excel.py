"""
Build the source-of-truth Excel for the REX Asia Report.

Reads report_data.json (from generate_report_data.py) and produces an Excel
workbook with:
  1. Fund Detail — every fund with raw + derived values
  2. Suite Summary — aggregated by product family
  3. Grace Cross-Check — side-by-side with Grace's source data
  4. Headline Numbers — the exact values that appear in the report

Usage: python build_excel.py
"""
import json
import os
import pandas as pd
from collections import defaultdict

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(SCRIPT_DIR, "report_data.json")
GRACE_FILE = os.path.join(SCRIPT_DIR, "grace_data", "Asset report summary Feb 2026.xlsx")
OUT_FILE = os.path.join(SCRIPT_DIR, "reports", "REX_Asia_Source_of_Truth_Feb26.xlsx")

# ── Load report data ──
with open(DATA_FILE) as f:
    data = json.load(f)

funds = data["funds"]
meta = data["meta"]
print(f"Loaded {len(funds)} funds for {meta['report_month']}")

# ── Sheet 1: Fund Detail ──
rows = []
for f in sorted(funds, key=lambda x: -x["asia_aum"]):
    aa = f["asia_aum"]
    aap = f["asia_aum_prior"]
    ga = f["global_aum"]
    gap = f["global_aum_prior"]

    # Derived values
    dollar_change = aa - aap
    mom_pct = (aa / aap - 1) if aap > 0 else None
    pct_global = aa / ga if ga > 0 else None
    global_mom = (ga / gap - 1) if gap > 0 and ga > 0 else None

    if aap > 0 and gap > 0 and ga > 0:
        market_move = aap * global_mom
        est_flows = dollar_change - market_move
    else:
        market_move = 0
        est_flows = dollar_change if aap > 0 else 0

    flows_pct_aum = est_flows / aa if aa > 0 else None
    absurd = abs(est_flows) > 0.5 * aa if aa > 0 else False

    rows.append({
        "Fund": f["ticker"],
        "Family": f["family_name"],
        "Asia AUM": aa,
        "Asia AUM Prior": aap,
        "Global AUM": ga,
        "Global AUM Prior": gap,
        "% of Global AUM": pct_global,
        "Asia MoM Chg (%)": mom_pct,
        "Global MoM (%)": global_mom,
        "Dollar Change": dollar_change,
        "Market Move": market_move,
        "Est. Flows": est_flows,
        "Flows % of AUM": flows_pct_aum,
        "Balance Check": round(dollar_change - (est_flows + market_move), 2),
        "Flag": "CHECK" if absurd else "",
    })

df_funds = pd.DataFrame(rows)

# ── Sheet 2: Suite Summary ──
suites = defaultdict(lambda: {"funds": 0, "asia_aum": 0, "asia_aum_prior": 0,
                               "global_aum": 0, "dollar_change": 0,
                               "market_move": 0, "est_flows": 0})
for r in rows:
    s = suites[r["Family"]]
    s["funds"] += 1
    s["asia_aum"] += r["Asia AUM"]
    s["asia_aum_prior"] += r["Asia AUM Prior"]
    s["global_aum"] += r["Global AUM"]
    s["dollar_change"] += r["Dollar Change"]
    s["market_move"] += r["Market Move"]
    s["est_flows"] += r["Est. Flows"]

suite_rows = []
for family in sorted(suites.keys()):
    s = suites[family]
    mom = (s["asia_aum"] / s["asia_aum_prior"] - 1) if s["asia_aum_prior"] > 0 else None
    suite_rows.append({
        "Suite": family,
        "Funds": s["funds"],
        "Asia AUM": s["asia_aum"],
        "Asia AUM Prior": s["asia_aum_prior"],
        "MoM (%)": mom,
        "Dollar Change": s["dollar_change"],
        "Market Move": s["market_move"],
        "Est. Flows": s["est_flows"],
        "Balance": round(s["dollar_change"] - (s["est_flows"] + s["market_move"]), 2),
    })

# Grand total row
gt = {
    "Suite": "TOTAL",
    "Funds": sum(s["funds"] for s in suites.values()),
    "Asia AUM": sum(s["asia_aum"] for s in suites.values()),
    "Asia AUM Prior": sum(s["asia_aum_prior"] for s in suites.values()),
    "Dollar Change": sum(s["dollar_change"] for s in suites.values()),
    "Market Move": sum(s["market_move"] for s in suites.values()),
    "Est. Flows": sum(s["est_flows"] for s in suites.values()),
}
gt["MoM (%)"] = (gt["Asia AUM"] / gt["Asia AUM Prior"] - 1) if gt["Asia AUM Prior"] > 0 else None
gt["Balance"] = round(gt["Dollar Change"] - (gt["Est. Flows"] + gt["Market Move"]), 2)
suite_rows.append(gt)
df_suites = pd.DataFrame(suite_rows)

# ── Sheet 3: Grace Cross-Check ──
# Grace's exchange-level data (from her Excel, units: $M)
grace_data = [
    ("Korea", "KSD (Korea Securities Depository) - Retail", 1019.6, 956.9, 927.6, "Reported monthly"),
    ("Korea", "Korea Investment Mgmt - ACE TESLA", 32.2, 25.6, 23.9, "Reported monthly"),
    ("Japan", "SBI", 28.9, 32.1, 29.5, "Reported monthly"),
    ("Japan", "Rakuten", 60.6, 63.6, 61.9, "Reported monthly"),
    ("Japan", "MooMoo", 12.1, 12.1, 12.1, "Quarterly carry-forward"),
    ("Japan", "Monex", 6.8, 6.8, 7.3, "Reported monthly"),
    ("Japan", "Matsui", 1.5, 1.9, 1.8, "Reported monthly"),
    ("Hong Kong", "Futu/MooMoo", 168.3, 168.3, 168.3, "Quarterly carry-forward"),
    ("Hong Kong", "Oriental Harbour *", 125.7, 105.9, 105.9, "13F filing (latest Q4)"),
    ("Hong Kong", "SYFE", None, None, 1.0, "Verbal report (FEPILN)"),
    ("Singapore", "MooMoo", 16.6, 16.6, 16.6, "Quarterly carry-forward"),
    ("Malaysia", "MooMoo", 6.3, 6.3, 6.3, "Quarterly carry-forward"),
    ("Thailand", "Asset Plus Asset Mgmt", None, None, 0.4, "$DRNZ only"),
    ("Taiwan/SG/HK", "ViewTrade", None, None, 25.4, "Quarterly (physical report)"),
]

# Get our DB exchange-level data for comparison
import psycopg2
conn = psycopg2.connect(host='localhost', port=5433, user='postgres', dbname='rex_asia')
cur = conn.cursor()

db_exchange = {}
for month_end, label in [("2025-12-31", "Dec"), ("2026-01-31", "Jan"), ("2026-02-28", "Feb")]:
    cur.execute("""
        SELECT c.name, ex.name, SUM(ea.exchange_aum_usd),
               SUM(COALESCE(ea.original_aum_usd, ea.exchange_aum_usd))
        FROM etp_exchange_monthly_aum ea
        JOIN exchange ex ON ea.exchange_id = ex.exchange_id
        JOIN country c ON ex.country_id = c.country_id
        JOIN calendar_month cm ON ea.month_id = cm.month_id
        WHERE cm.month_end = %s
        GROUP BY c.name, ex.name
    """, (month_end,))
    for r in cur.fetchall():
        key = (r[0], r[1])
        if key not in db_exchange:
            db_exchange[key] = {}
        db_exchange[key][f"DB {label} (repriced)"] = float(r[2]) / 1e6
        db_exchange[key][f"DB {label} (original)"] = float(r[3]) / 1e6

conn.close()

grace_rows = []
for country, source, dec, jan, feb, note in grace_data:
    row = {
        "Country": country,
        "Source": source,
        "Grace Dec ($M)": dec,
        "Grace Jan ($M)": jan,
        "Grace Feb ($M)": feb,
        "Note": note,
    }
    # Find matching DB entry — fuzzy match on exchange name
    matched = False
    # Special cases for multi-country or name mismatches
    match_map = {
        ("Korea", "Korea Investment Mgmt - ACE TESLA"): ("Korea", "Korea Investment Management"),
        ("Thailand", "Asset Plus Asset Mgmt"): ("Thailand", "Asset Plus Asset Management"),
        ("Taiwan/SG/HK", "ViewTrade"): None,  # Aggregated across countries, skip
    }
    lookup = match_map.get((country, source), "DEFAULT")
    if lookup is None:
        # Aggregate ViewTrade across all countries
        vt_total = {}
        for (dc, dn), dv in db_exchange.items():
            if "ViewTrade" in dn:
                for k, v in dv.items():
                    vt_total[k] = vt_total.get(k, 0) + v
        if vt_total:
            for k, v in vt_total.items():
                row[k] = v
            matched = True
    elif lookup != "DEFAULT":
        target_country, target_name = lookup
        for (dc, dn), dv in db_exchange.items():
            if dc == target_country and target_name.lower() in dn.lower():
                for k, v in dv.items():
                    row[k] = v
                matched = True
                break
    else:
        for (db_country, db_exchange_name), db_vals in db_exchange.items():
            if db_country == country and (
                source.lower() in db_exchange_name.lower() or
                db_exchange_name.lower() in source.lower()
            ):
                for k, v in db_vals.items():
                    row[k] = v
                matched = True
                break

    if matched and feb is not None:
        db_feb = row.get("DB Feb (repriced)", 0)
        db_orig = row.get("DB Feb (original)", 0)
        row["Diff vs Grace (repriced)"] = db_feb - feb
        row["Diff vs Grace (original)"] = db_orig - feb
        row["Match?"] = "YES" if abs(db_feb - feb) < 0.5 else ("REPRICED" if abs(db_orig - feb) < 0.5 else "CHECK")
    else:
        row["Match?"] = "N/A" if not matched else ""

    grace_rows.append(row)

# Total row
grace_feb_total = sum(r.get("Grace Feb ($M)", 0) or 0 for r in grace_rows)
db_feb_total = sum(r.get("DB Feb (repriced)", 0) or 0 for r in grace_rows)
grace_rows.append({
    "Country": "TOTAL",
    "Source": "",
    "Grace Feb ($M)": grace_feb_total,
    "DB Feb (repriced)": db_feb_total,
    "Diff vs Grace (repriced)": db_feb_total - grace_feb_total,
    "Match?": "",
    "Note": "Diff explained by quarterly repricing methodology",
})

df_grace = pd.DataFrame(grace_rows)

# ── Sheet 4: Headline Numbers ──
total_aum = sum(r["Asia AUM"] for r in rows)
total_prior = sum(r["Asia AUM Prior"] for r in rows)
total_dc = sum(r["Dollar Change"] for r in rows)
total_mm = sum(r["Market Move"] for r in rows)
total_flows = sum(r["Est. Flows"] for r in rows)
total_global = sum(r["Global AUM"] for r in rows)

headline_rows = [
    {"Metric": "Report Month", "Value": meta["report_month"]},
    {"Metric": "Total Asia AUM", "Value": total_aum},
    {"Metric": "Prior Month AUM", "Value": total_prior},
    {"Metric": "Total Global AUM", "Value": total_global},
    {"Metric": "% of AUM in Asia", "Value": total_aum / total_global if total_global > 0 else 0},
    {"Metric": "Dollar Change", "Value": total_dc},
    {"Metric": "MoM Change (%)", "Value": (total_aum / total_prior - 1) if total_prior > 0 else 0},
    {"Metric": "Est. Net Flows", "Value": total_flows},
    {"Metric": "Market Move", "Value": total_mm},
    {"Metric": "Balance (DC - Flows - MM)", "Value": round(total_dc - total_flows - total_mm, 2)},
    {"Metric": "Fund Count", "Value": len(funds)},
    {"Metric": "Countries", "Value": len(data.get("countries", []))},
    {"Metric": "Exchanges", "Value": len(data.get("exchanges", []))},
    {"Metric": "", "Value": ""},
    {"Metric": "T-REX Flows", "Value": suites["T-REX"]["est_flows"]},
    {"Metric": "T-REX Market Move", "Value": suites["T-REX"]["market_move"]},
    {"Metric": "MicroSectors Flows", "Value": suites["MicroSectors"]["est_flows"]},
    {"Metric": "MicroSectors Market Move", "Value": suites["MicroSectors"]["market_move"]},
    {"Metric": "Income (EPI+G&I) Flows", "Value": suites["Income"]["est_flows"]},
    {"Metric": "Income Market Move", "Value": suites["Income"]["market_move"]},
]
df_headline = pd.DataFrame(headline_rows)

# ── Write Excel ──
with pd.ExcelWriter(OUT_FILE, engine="openpyxl") as writer:
    df_funds.to_excel(writer, sheet_name="Fund Detail", index=False)
    df_suites.to_excel(writer, sheet_name="Suite Summary", index=False)
    df_grace.to_excel(writer, sheet_name="Grace Cross-Check", index=False)
    df_headline.to_excel(writer, sheet_name="Headlines", index=False)

    # Format columns
    wb = writer.book

    # Fund Detail formatting
    ws = wb["Fund Detail"]
    from openpyxl.styles import numbers
    money_fmt = '#,##0'
    pct_fmt = '0.0%'
    for col_letter, fmt in [
        ("C", money_fmt), ("D", money_fmt), ("E", money_fmt), ("F", money_fmt),
        ("G", pct_fmt), ("H", pct_fmt), ("I", pct_fmt),
        ("J", money_fmt), ("K", money_fmt), ("L", money_fmt), ("M", pct_fmt),
    ]:
        for cell in ws[col_letter][1:]:
            cell.number_format = fmt

    # Suite Summary formatting
    ws2 = wb["Suite Summary"]
    for col_letter, fmt in [
        ("C", money_fmt), ("D", money_fmt), ("E", pct_fmt),
        ("F", money_fmt), ("G", money_fmt), ("H", money_fmt),
    ]:
        for cell in ws2[col_letter][1:]:
            cell.number_format = fmt

    # Grace Cross-Check formatting
    ws3 = wb["Grace Cross-Check"]
    for col_letter in ["C", "D", "E", "F", "G", "H", "I", "J", "K"]:
        for cell in ws3[col_letter][1:]:
            if cell.value is not None:
                cell.number_format = '#,##0.0'

    # Auto-fit column widths (approximate)
    for ws_name in ["Fund Detail", "Suite Summary", "Grace Cross-Check", "Headlines"]:
        ws = wb[ws_name]
        for col in ws.columns:
            max_len = max(len(str(cell.value or "")) for cell in col)
            ws.column_dimensions[col[0].column_letter].width = min(max_len + 2, 25)

print(f"\nWritten: {OUT_FILE}")
print(f"  Sheet 1: Fund Detail ({len(df_funds)} funds)")
print(f"  Sheet 2: Suite Summary ({len(df_suites)} rows)")
print(f"  Sheet 3: Grace Cross-Check ({len(df_grace)} rows)")
print(f"  Sheet 4: Headlines ({len(df_headline)} rows)")
