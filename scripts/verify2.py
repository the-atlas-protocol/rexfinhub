from screener.li_engine.analysis import trex_combined_v9 as t
fc = t.load_foreign_competition()
print("=== DISCO race after collision fix ===")
for x in fc:
    if "DISCO" in x["name"].upper():
        if not x["race"]: print(f"  {x['name']}: (no filings — clean)")
        for r in x["race"]:
            print(f"  {x['name']}: {r['issuer']:14} {r['dir']:7} {r['status']:10} rex={r['rex']}")
HIGH=10e9
fb=[x for x in fc if x['cap']>=HIGH or x['ncomp']>0 or any(r['rex'] for r in x['race'])]
fb.sort(key=lambda x:-x['cap'])
print(f"\n=== Foreign top 10 by cap (included={len(fb)}) ===")
for x in fb[:10]:
    print(f"  {x['name'][:26]:26} ${x['cap']/1e9:,.1f}B comp={x['ncomp']} {x['market']}")
print("\n=== Foreign WITH competitor filings (verify real) ===")
for x in sorted(fc, key=lambda x:-x['ncomp']):
    if x['ncomp']>0:
        print(f"  {x['name'][:24]:24} comp={x['ncomp']} -> {sorted({r['issuer'] for r in x['race'] if not r['rex']})}")
