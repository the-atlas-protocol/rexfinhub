"""Patch page 1 bullets to be sign-aware and drop stale 'Taiwan entered' / 'gold miner' text."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

p = r'C:\Projects\rex-asia\report_v15.html'
with open(p, 'r', encoding='utf-8') as f: content = f.read()

# Identify the full block by two distinctive anchor lines
anchor_start = "    el('p1-overview').innerHTML = ["
anchor_end = "].join('\\n');"

i = content.find(anchor_start)
if i < 0:
    print("Start anchor not found"); sys.exit(1)
j = content.find(anchor_end, i)
if j < 0:
    print("End anchor not found"); sys.exit(1)
j += len(anchor_end)

old = content[i:j]
print(f"Found block, length {j-i}")
print("First 200 chars of old block:")
print(old[:200])

new = """    // Overview bullets — tone/direction adapts to data sign
    const mmDir = mm >= 0
      ? '<strong>' + signed$m(mm) + '</strong> in market gains'
      : '<strong>' + signed$m(mm) + '</strong> in adverse movements';
    const flDir = fl >= 0
      ? ', partially offset by <strong>' + signed$m(fl) + '</strong> in est. net inflows'
      : ', compounded by <strong>' + signed$m(fl) + '</strong> in est. net outflows';
    const momDir = momPct >= 0 ? 'up' : 'down';
    const microTrend = microMoM >= 0
      ? 'gained <strong>' + sign(microMoM) + pct1(Math.abs(microMoM)) + ' MoM</strong>'
      : 'declined <strong>' + pct1(Math.abs(microMoM)) + ' MoM</strong>';
    const topFlowFund = APPENDIX.slice().sort((a, b) => Math.abs(b.flows) - Math.abs(a.flows))[0];
    const notableLine = topFlowFund
      ? '<strong>' + topFlowFund.fund + '</strong> ' + (topFlowFund.flows >= 0 ? 'attracted' : 'saw') + ' <strong>' + signed$m(topFlowFund.flows) + '</strong> in est. ' + (topFlowFund.flows >= 0 ? 'net inflows' : 'net outflows') + ' \\u2014 the single largest Asia flow this month'
      : '';
    el('p1-overview').innerHTML = [
      '<li>Asia AUM: <strong>' + $b(h.total_asia_aum) + '</strong> across ' + h.country_count + ' markets and ' + h.exchange_count + ' exchanges \\u2014 ' + momDir + ' <strong>' + pct1(Math.abs(momPct)) + ' MoM</strong> (' + signed$m(dc) + ')</li>',
      '<li>REX Shares (Leveraged): <strong>' + $b(leveragedAum) + '</strong> \\u00b7 REX Financial (Non-Leveraged): <strong>' + $m(nonLeveragedAum) + '</strong></li>',
      '<li>Broad market ' + (mm >= 0 ? 'strength' : 'weakness') + ' drove ' + mmDir + flDir + '</li>',
      '<li><strong>T-REX</strong> represents <strong>' + pct1(trexPctAsia) + '</strong> of its global AUM in Asia; <strong>MicroSectors</strong> ' + microTrend + '</li>',
      notableLine ? '<li>' + notableLine + '</li>' : ''
    ].filter(Boolean).join('\\n');"""

content2 = content[:i] + new + content[j:]
with open(p, 'w', encoding='utf-8') as f: f.write(content2)
print(f"Patched. New block length: {len(new)}")
