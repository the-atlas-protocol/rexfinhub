p = r'C:\Projects\rex-asia\report_v15.html'
with open(p, 'r', encoding='utf-8') as f: c = f.read()
old = "el('p1-headline').textContent = `Asia AUM: ${$b(h.total_asia_aum)} \\u2014 inflows (${signed$m(fl)}) offset market decline (${signed$m(mm)})`;"
new = """const flLabel = fl >= 0 ? 'inflows' : 'outflows';
    const mmLabel = mm >= 0 ? 'gains' : 'decline';
    const connector = (fl >= 0 && mm < 0) || (fl < 0 && mm >= 0) ? 'offset' : 'compounded by';
    el('p1-headline').textContent = `Asia AUM: ${$b(h.total_asia_aum)} \\u2014 ${flLabel} (${signed$m(fl)}) ${connector} market ${mmLabel} (${signed$m(mm)})`;"""
if old not in c:
    print("OLD not found; search pattern needs adjustment")
    # Try without escaped character
    import re
    pat = re.search(r"el\('p1-headline'\).textContent = `Asia AUM.*?`;", c)
    if pat:
        print(f"Found at pos {pat.start()}: {pat.group()[:200]}")
else:
    c = c.replace(old, new)
    with open(p, 'w', encoding='utf-8') as f: f.write(c)
    print("Patched.")
