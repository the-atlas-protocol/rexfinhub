"""Add pp-delta calculation to updateSuiteKpis."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
p = r'C:\Projects\rex-asia\report_v15.html'
with open(p, 'r', encoding='utf-8') as f: c = f.read()

# In calcFromFunds, include prior pctInAsia
old = """    return { aum, globalAum, flows, marketMove, momDollar, momPct, pctInAsia };
  }"""
new = """    // Prior month % in Asia (for pp-delta sub)
    let aumPrior = 0, globalAumPrior = 0;
    funds.forEach(f => {
      aumPrior += (f.aumPrior != null ? f.aumPrior : (f.aum - (f.flows || 0) - (f.marketMove || 0))) || 0;
      globalAumPrior += (f.globalPrior != null ? f.globalPrior : (f.global || 0)) || 0;
    });
    const pctInAsiaPrior = globalAumPrior > 0 ? aumPrior / globalAumPrior : 0;
    return { aum, globalAum, flows, marketMove, momDollar, momPct, pctInAsia, pctInAsiaPrior };
  }"""
if old in c:
    c = c.replace(old, new)
    print("Patched calcFromFunds.")
else:
    print("calcFromFunds old form not found.")

# In updateSuiteKpis, add sub text to KPI 1
old2 = """    // KPI 1: % in Asia
    kpis[1].querySelector('.kpi-val').textContent = (k.pctInAsia * 100).toFixed(1) + '%';"""
new2 = """    // KPI 1: % in Asia (+ pp delta sub)
    kpis[1].querySelector('.kpi-val').textContent = (k.pctInAsia * 100).toFixed(1) + '%';
    const pp = (k.pctInAsia - k.pctInAsiaPrior) * 100;
    const sub1 = kpis[1].querySelector('.kpi-sub');
    if (sub1) {
      const ppSign = pp >= 0 ? '+' : '';
      sub1.textContent = ppSign + pp.toFixed(1) + 'pp';
      sub1.style.color = pp >= 0 ? 'var(--positive)' : 'var(--negative)';
    }"""
if old2 in c:
    c = c.replace(old2, new2)
    print("Patched updateSuiteKpis.")
else:
    print("updateSuiteKpis old form not found.")

with open(p, 'w', encoding='utf-8') as f: f.write(c)
print("Done.")
