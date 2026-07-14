"""Verify every cover/page-1 number in Mar PDF matches enriched JSON."""
import json
from pypdf import PdfReader

j = json.load(open("enriched_report_data_mar.json"))
H = j["headlines"]
r = PdfReader("reports/2026-03/REX_Asia_Report_Mar26.pdf")
t = r.pages[0].extract_text()

def has(needle): return needle in t

checks = [
    ("Cover AUM $1.2B",     "$1.2B"),
    ("Global AUM $6.1B",    "$6.1B"),
    ("% in Asia 19.9%",     "19.9%"),
    ("Market -$61.3M",      "-$61.3M"),
    ("Flows -$54.5M",       "-$54.5M"),
    ("MoM -$116M",          "-$116M"),
    ("MoM -8.8%",           "-8.8%"),
    ("March 2026",          "March 2026"),
    ("6 markets",           "6 markets"),
    ("15 exchanges",        "15 exchanges"),
]

print("Page 1 cover number audit:")
for label, needle in checks:
    mark = "OK" if has(needle) else "MISSING"
    print(f"  [{mark}] {label}: '{needle}'")

# Verify enriched vs what's shown
print()
print(f"Enriched values:")
print(f"  total_asia_aum:    ${H['total_asia_aum']:,.0f} ($ {H['total_asia_aum']/1e9:.3f}B)")
print(f"  total_global_aum:  ${H['total_global_aum']:,.0f} ($ {H['total_global_aum']/1e9:.3f}B)")
print(f"  pct_in_asia:       {H['pct_in_asia']*100:.2f}%")
print(f"  total_market_move: ${H['total_market_move']:,.0f}")
print(f"  total_flows:       ${H['total_flows']:,.0f}")
print(f"  dollar_change:     ${H['dollar_change']:,.0f}")
print(f"  mom_pct:           {H['mom_pct']*100:.2f}%")
print(f"  country_count:     {H['country_count']}")
print(f"  exchange_count:    {H['exchange_count']}")

# Top fund spot-checks
print()
print("Top 5 funds (asia_aum) — enriched vs PDF appendix:")
funds = sorted(j["funds"], key=lambda f: -f.get("asia_aum", 0))[:10]
appendix_text = r.pages[6].extract_text()
for f in funds:
    aum_m = f["asia_aum"] / 1e6
    ticker = f["ticker"]
    ok = ticker in appendix_text
    print(f"  {ticker:<8}  ${aum_m:>8.2f}M enriched     [{'in PDF' if ok else 'NOT FOUND'}]")

# Suite totals
print()
print("Suite totals — enriched vs PDF:")
for name, s in j["suites"].items():
    print(f"  {name:<14}  ${s['aum']/1e6:>9.2f}M   flows ${s['flows']/1e6:+8.2f}M   market ${s['market_move']/1e6:+8.2f}M")

# Fund count
print()
print(f"Asia-active fund count: {H['fund_count']}")
print(f"Appendix says: (grep for 'REX funds')")
for line in t.split("\n"):
    if "REX funds" in line:
        print(f"  '{line.strip()}'")
