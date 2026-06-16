"""Build the full-system architecture visual → docs/architecture/index.html.

Companion to scripts/build_lineage_map.py (which traces a single number Excel→report).
This one shows the SYSTEM in its entirety: sources → schedulers (systemd timers) →
pipeline stages → data stores (per device) → outputs (10 reports + website). Every node
carries a health badge (green=healthy / amber=degraded / red=broken|disabled) grounded in
the 2026-06-16 automation audit (docs/audits/2026-06-16-automation-and-reports.md).

Self-contained (opens in Chrome, no network). Click a node to highlight its flow.
Regenerate:  python scripts/build_architecture_map.py
"""
from __future__ import annotations
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "architecture" / "index.html"

LANES = ["Sources", "Schedulers (systemd)", "Pipeline stages", "Data stores", "Outputs"]

# id, lane, label, sub (schedule/device/detail), health: ok|warn|bad
NODES = [
    # Sources
    ("s_bbg",   0, "Bloomberg daily xlsm", "SharePoint pull · ~21:00 ET", "ok"),
    ("s_edgar", 0, "SEC EDGAR", "485 filings + daily index", "ok"),
    ("s_cboe",  0, "CBOE", "symbol reservation", "bad"),
    ("s_rules", 0, "Rules CSVs (config/rules)", "fund_master · attributes · rex_funds", "ok"),
    # Schedulers (timers)
    ("t_chain", 1, "bloomberg-chain", "Mon–Fri 17:15 + 21:00 · sync + 12 post-steps", "ok"),
    ("t_poller",1, "fresh-poller", "Mon–Fri 08–20 /15min", "ok"),
    ("t_atom",  1, "atom-watcher + worker", "continuous daemons", "ok"),
    ("t_sec",   1, "sec-scrape", "08/12/16/20:00 · runs run_daily", "warn"),
    ("t_intra", 1, "intraday-refresh", "DISABLED (never enabled)", "bad"),
    ("t_recon", 1, "reconciler", "daily 08:00", "ok"),
    ("t_sweep", 1, "classification-sweep", "Mon–Fri 09:00", "ok"),
    ("t_triage",1, "morning-triage", "08:00 · assertions+email", "bad"),
    ("t_pre",   1, "preflight", "18:30 · DISABLED", "bad"),
    ("t_gate",  1, "gate open/close", "open 19:00 DISABLED · close 20:00", "warn"),
    ("t_backup",1, "db-backup", "daily 23:00", "ok"),
    # Pipeline stages (the bloomberg-chain / run_daily order)
    ("p_sync",  2, "sync_market_data", "full refresh + (ticker,etp_category) dedup", "ok"),
    ("p_class", 2, "classify_daily", "rules→LLM→critic→queue", "ok"),
    ("p_fm",    2, "apply_fund_master", "curated 3-axis restamp", "ok"),
    ("p_iss",   2, "derive+apply_issuer_brands", "issuer_display", "ok"),
    ("p_sweep", 2, "apply_classification_sweep", "HIGH/MED auto-fill", "ok"),
    ("p_recon", 2, "status_reconciler", "status_history → status_cached", "ok"),
    ("p_snap",  2, "snapshot_daily", "append-only EOD history", "ok"),
    ("p_commit",2, "commit_rules_delta", "rules → git → Render", "ok"),
    ("p_assert",2, "run_assertions", "31 checks (08:00 tripwire)", "warn"),
    ("p_qc",    2, "qc_all_reports", "input→output report QC", "ok"),
    # Data stores (with device)
    ("d_etp",   3, "etp_tracker.db", "VPS · authority (64 tables)", "ok"),
    ("d_mkt",   3, "mkt_master_data", "full-snapshot per sync", "ok"),
    ("d_ts",    3, "mkt_time_series", "36-mo AUM/flow", "ok"),
    ("d_snap",  3, "mkt_daily_snapshot", "VPS · per-day field history", "ok"),
    ("d_rex",   3, "rex_products / status_history", "REX lifecycle authority", "ok"),
    ("d_live",  3, "live_feed.db", "VPS · SEC live feed", "ok"),
    ("d_render",3, "Render replica", "rexfinhub.com read DB", "ok"),
    ("d_darch", 3, "D: archive", "nightly tarballs + cache", "ok"),
    # Outputs
    ("o_daily", 4, "Daily filing report", "filings + upcoming launches", "ok"),
    ("o_weekly",4, "Weekly digest", "5-category landscape", "ok"),
    ("o_li",    4, "L&I report", "REX position + chart", "ok"),
    ("o_income",4, "Income report", "covered-call + chart", "ok"),
    ("o_flow",  4, "Flow report", "per-suite CEO flows", "ok"),
    ("o_blue",  4, "Blue Ocean", "monthly 1st bus. day", "ok"),
    ("o_port",  4, "Portfolio suite", "20-fund suite flows", "ok"),
    ("o_trex",  4, "T-REX Stock recs", "Monday pipeline", "ok"),
    ("o_brief", 4, "T-REX Brief", "on-demand (names→chat)", "ok"),
    ("o_ms",    4, "MicroSectors", "ETN overlay (dtype wart)", "warn"),
    ("o_web",   4, "rexfinhub.com", "Render webapp", "ok"),
]

EDGES = [
    ("s_bbg", "t_chain"), ("s_edgar", "t_poller"), ("s_edgar", "t_atom"),
    ("s_edgar", "t_sec"), ("s_cboe", "t_chain"), ("s_rules", "p_fm"),
    ("t_chain", "p_sync"), ("t_sec", "p_sync"), ("t_intra", "p_sync"),
    ("p_sync", "p_class"), ("p_class", "p_fm"), ("p_fm", "p_iss"),
    ("p_iss", "p_sweep"), ("p_sweep", "p_recon"), ("p_recon", "p_snap"),
    ("p_snap", "p_commit"),
    ("t_recon", "p_recon"), ("t_sweep", "p_fm"), ("t_triage", "p_assert"),
    ("t_pre", "p_qc"),
    ("p_sync", "d_mkt"), ("p_sync", "d_ts"), ("d_mkt", "d_etp"),
    ("p_snap", "d_snap"), ("p_recon", "d_rex"), ("t_atom", "d_live"),
    ("p_commit", "d_render"), ("t_backup", "d_darch"), ("d_etp", "d_darch"),
    ("d_mkt", "o_daily"), ("d_mkt", "o_weekly"), ("d_mkt", "o_li"),
    ("d_mkt", "o_income"), ("d_mkt", "o_flow"), ("d_ts", "o_li"),
    ("d_mkt", "o_blue"), ("d_mkt", "o_port"), ("d_rex", "o_trex"),
    ("d_rex", "o_brief"), ("d_mkt", "o_ms"), ("d_render", "o_web"),
    ("p_qc", "o_li"), ("p_qc", "o_income"), ("p_assert", "d_etp"),
]

LANE_X = {0: 40, 1: 320, 2: 660, 3: 1010, 4: 1340}
LANE_W = 250
HEALTH = {"ok": "#14532d", "warn": "#78350f", "bad": "#7f1d1d"}
HEALTH_STROKE = {"ok": "#22c55e", "warn": "#f59e0b", "bad": "#ef4444"}


def build_html() -> str:
    by_lane: dict[int, list] = {i: [] for i in range(len(LANES))}
    for n in NODES:
        by_lane[n[1]].append(n)
    pos, row_h, top = {}, 40, 90
    height = top + max(len(v) for v in by_lane.values()) * row_h + 60
    for lane, ns in by_lane.items():
        for i, n in enumerate(ns):
            pos[n[0]] = (LANE_X[lane], top + i * row_h)
    nodes_js = [{"id": n[0], "lane": n[1], "label": n[2], "sub": n[3], "h": n[4],
                 "x": pos[n[0]][0], "y": pos[n[0]][1]} for n in NODES]
    edges_js = [{"from": e[0], "to": e[1]} for e in EDGES]
    data = json.dumps({"nodes": nodes_js, "edges": edges_js, "lanes": LANES,
                       "laneX": LANE_X, "laneW": LANE_W, "height": height,
                       "fill": HEALTH, "stroke": HEALTH_STROKE})
    return """<!DOCTYPE html><html><head><meta charset="utf-8"><title>rexfinhub system architecture</title>
<style>
 body{margin:0;font-family:-apple-system,Segoe UI,sans-serif;background:#0f172a;color:#e2e8f0}
 #top{padding:10px 16px;background:#1e293b;border-bottom:1px solid #334155}
 #top h1{margin:0 0 4px;font-size:16px} #top p{margin:0;font-size:12px;color:#94a3b8}
 .legend span{display:inline-block;margin-right:14px;font-size:12px}
 .dot{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:middle}
 #canvas{overflow:auto} svg{display:block}
 .lane-h{fill:#64748b;font-size:13px;font-weight:700}
 .node rect{rx:5;cursor:pointer;stroke-width:2}
 .node .lbl{font-size:11px;fill:#f1f5f9;font-weight:600;pointer-events:none}
 .node .sub{font-size:9px;fill:#94a3b8;pointer-events:none}
 .edge{stroke:#475569;fill:none;stroke-width:1}.edge.hot{stroke:#f59e0b;stroke-width:2.5}
 .dim{opacity:.13}
</style></head><body>
<div id="top"><h1>rexfinhub &mdash; system architecture (sources &rarr; schedulers &rarr; pipeline &rarr; data &rarr; outputs)</h1>
<p>Health from the 2026-06-16 automation audit. Click any node to highlight its flow.</p>
<div class="legend"><span><i class="dot" style="background:#22c55e"></i>healthy</span>
<span><i class="dot" style="background:#f59e0b"></i>degraded</span>
<span><i class="dot" style="background:#ef4444"></i>broken / disabled</span></div></div>
<div id="canvas"><svg id="svg"></svg></div>
<script>
const D = """ + data + """;
const svg=document.getElementById('svg'), W=1620;
svg.setAttribute('width',W); svg.setAttribute('height',D.height);
const NS='http://www.w3.org/2000/svg', byId={};
D.nodes.forEach(n=>byId[n.id]=n);
const adjOut={}, adjIn={};
D.edges.forEach(e=>{(adjOut[e.from]=adjOut[e.from]||[]).push(e);(adjIn[e.to]=adjIn[e.to]||[]).push(e);});
D.lanes.forEach((l,i)=>{const t=document.createElementNS(NS,'text');t.setAttribute('x',D.laneX[i]);t.setAttribute('y',60);t.setAttribute('class','lane-h');t.textContent=l;svg.appendChild(t);});
const edgeEls=[];
D.edges.forEach(e=>{const a=byId[e.from],b=byId[e.to];if(!a||!b)return;const p=document.createElementNS(NS,'path');
 const x1=a.x+D.laneW,y1=a.y+17,x2=b.x,y2=b.y+17,mx=(x1+x2)/2;
 p.setAttribute('d',`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);p.setAttribute('class','edge');p._e=e;svg.appendChild(p);edgeEls.push(p);});
const nodeEls=[];
D.nodes.forEach(n=>{const g=document.createElementNS(NS,'g');g.setAttribute('class','node');
 const r=document.createElementNS(NS,'rect');r.setAttribute('x',n.x);r.setAttribute('y',n.y);r.setAttribute('width',D.laneW);r.setAttribute('height',34);
 r.setAttribute('fill',D.fill[n.h]);r.setAttribute('stroke',D.stroke[n.h]);
 const t1=document.createElementNS(NS,'text');t1.setAttribute('x',n.x+8);t1.setAttribute('y',n.y+15);t1.setAttribute('class','lbl');t1.textContent=n.label;
 const t2=document.createElementNS(NS,'text');t2.setAttribute('x',n.x+8);t2.setAttribute('y',n.y+27);t2.setAttribute('class','sub');t2.textContent=n.sub.length>40?n.sub.slice(0,39)+'…':n.sub;
 g.appendChild(r);g.appendChild(t1);g.appendChild(t2);g._n=n;g.onclick=()=>trace(n.id);svg.appendChild(g);nodeEls.push(g);});
function walk(id,adj,acc){(adj[id]||[]).forEach(e=>{const nxt=adj===adjOut?e.to:e.from;if(!acc.nodes.has(nxt)){acc.nodes.add(nxt);acc.edges.add(e);walk(nxt,adj,acc);}else acc.edges.add(e);});}
function trace(id){const acc={nodes:new Set([id]),edges:new Set()};walk(id,adjOut,acc);walk(id,adjIn,acc);
 nodeEls.forEach(g=>g.classList.toggle('dim',!acc.nodes.has(g._n.id)));
 edgeEls.forEach(p=>{const on=acc.edges.has(p._e);p.classList.toggle('hot',on);p.classList.toggle('dim',!on);});}
</script></body></html>"""


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_html(), encoding="utf-8")
    print(f"wrote {OUT}  ({len(NODES)} nodes, {len(EDGES)} edges)")


if __name__ == "__main__":
    main()
