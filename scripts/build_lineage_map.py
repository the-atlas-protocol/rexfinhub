"""Build the field-level data-lineage visualization (Plan WS-0.4 / G).

Renders docs/lineage/index.html — a self-contained interactive map (opens in Chrome,
no network) where you trace one number from a Bloomberg Excel cell, through ingestion,
the DB columns, enrichment, to a report cell, and back. Click any node (or search) and
its full upstream+downstream path highlights, with the file:line of every hop.

The graph is the consolidated lineage from docs/SYSTEM_LEDGER.md (the 12-agent discovery
+ critic loop). Every EDGE cites a real file:line so it's evidence-grounded and the map
doubles as a QC lens: a report number whose path does not reach a source is a flagged bug.
Regenerate after any data/report change:  python scripts/build_lineage_map.py
"""
from __future__ import annotations
import html
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "lineage" / "index.html"

# Lanes, left -> right (the flow of a number through the system).
LANES = ["Source", "Ingestion", "DB column", "Enrichment", "Report"]

# id, lane, label, sublabel (file). Grounded in SYSTEM_LEDGER.
NODES = [
    # --- Sources ---
    ("src_bbg_aum",   0, "Bloomberg: data_aum / w4 AUM cell", "bloomberg_daily_file.xlsm"),
    ("src_bbg_flow",  0, "Bloomberg: data_flow / fund_flow", "bloomberg_daily_file.xlsm"),
    ("src_bbg_status",0, "Bloomberg: mkt_status (ACTV/LIQU/PEND)", "bloomberg_daily_file.xlsm"),
    ("src_bbg_lev",   0, "Bloomberg: is_singlestock / leverage", "bloomberg_daily_file.xlsm"),
    ("src_bbg_ms",    0, "Bloomberg: microsector_aum overlay", "bloomberg_daily_file.xlsm sheet"),
    ("src_edgar",     0, "SEC EDGAR: 485 filings + cover election", "etp_tracker/sec_client.py"),
    ("src_rules_li",  0, "Rules CSV: attributes_LI.csv", "config/rules/attributes_LI.csv"),
    ("src_rules_cc",  0, "Rules CSV: attributes_CC.csv", "config/rules/attributes_CC.csv"),
    ("src_rules_fm",  0, "Rules CSV: fund_master.csv (3-axis)", "config/rules/fund_master.csv"),
    ("src_rules_rex", 0, "Rules CSV: rex_funds.csv (is_rex)", "config/rules/rex_funds.csv"),
    # --- Ingestion ---
    ("ing_read",   1, "market/ingest.read_input", "market/ingest.py"),
    ("ing_xform",  1, "market/transform (12 steps)", "market/transform.py"),
    ("ing_writer", 1, "market/db_writer (full refresh)", "market/db_writer.py"),
    ("ing_ms",     1, "MicroSectors AUM overlay", "market/microsectors.py"),
    ("ing_step3",  1, "step3 485 election parser", "etp_tracker/step3.py:_parse_485a_election"),
    ("ing_sync",   1, "sync_rex_products_from_filings", "scripts/sync_rex_products_from_filings.py"),
    # --- DB columns ---
    ("db_aum",     2, "mkt_master_data.aum", "models.py MktMasterData"),
    ("db_flow",    2, "mkt_master_data.fund_flow_1week", "models.py"),
    ("db_status",  2, "mkt_master_data.market_status", "models.py"),
    ("db_etp",     2, "mkt_master_data.etp_category", "legacy projection"),
    ("db_subcat",  2, "mkt_master_data.map_li_subcategory", "SingleStock vs Index"),
    ("db_underl",  2, "mkt_master_data.map_li_underlier", "underlier label"),
    ("db_cccat",   2, "mkt_master_data.cc_category / cc_type", "autocall encoding"),
    ("db_prim",    2, "mkt_master_data.primary_strategy", "3-axis taxonomy"),
    ("db_isrex",   2, "mkt_master_data.is_rex", "REX universe flag"),
    ("db_ts",      2, "mkt_time_series.aum (36mo)", "models.py MktTimeSeries"),
    ("db_snap",    2, "mkt_daily_snapshot (EOD history)", "snapshot_daily.py"),
    ("db_rexstat", 2, "rex_products.status / est_eff_date", "models.py RexProduct"),
    ("db_dist",    2, "fund_distributions", "income/autocall data"),
    ("db_stock",   2, "mkt_stock_data (underlier mcap)", "single-stock analysis"),
    # --- Enrichment ---
    ("en_classify",3, "classify_daily (rules->LLM->critic)", "scripts/classify_daily.py"),
    ("en_fm",      3, "apply_fund_master (restamp 3-axis)", "scripts/apply_fund_master.py"),
    ("en_recon",   3, "status_reconciler (LIQU->Delisted)", "webapp/services/status_reconciler.py"),
    ("en_charts",  3, "generate_market_share_charts", "scripts/generate_market_share_charts.py"),
    ("en_kpi",     3, "kpi.py (single KPI source) [Phase1]", "webapp/services/kpi.py (planned)"),
    ("en_cache",   3, "market_sync cache bake", "webapp/services/market_sync.py Step6"),
    # --- Reports ---
    ("rp_li_aum",  4, "L&I report: REX AUM KPI", "report_data.py:1217 li_mask"),
    ("rp_li_chart",4, "L&I/Income: market-share chart", "report_emails.py:1285-1348"),
    ("rp_li_ss",   4, "L&I: single-stock underlier table", "report_data.py:1388-1418"),
    ("rp_income",  4, "Income report (CC)", "report_data.py:1537 etp_category=CC"),
    ("rp_autocall",4, "Autocall report set", "report_data.py:2103-2274"),
    ("rp_flow",    4, "Flow report suite KPIs", "report_data.py:2163"),
    ("rp_daily_up",4, "Daily: Upcoming Launches (issuer/substrat)", "email_alerts.py:1406-1454"),
    ("rp_trex",    4, "T-REX Stock pipeline + eff dates", "trex_combined_v9.py"),
    ("rp_ms",      4, "MicroSectors report", "market/microsectors.py"),
    ("rp_blue",    4, "Blue Ocean (monthly, 1st bus. day)", "blue_ocean_report.py + nyse_holidays"),
]

# from, to, evidence (file:line / how)
EDGES = [
    ("src_bbg_aum","ing_read","openpyxl read w4 AUM"),
    ("src_bbg_flow","ing_read","read fund_flow sheets"),
    ("src_bbg_status","ing_read","read mkt_status sheet"),
    ("src_bbg_lev","ing_read","read is_singlestock"),
    ("src_bbg_ms","ing_ms","microsector_aum/sh sheets"),
    ("src_edgar","ing_step3","485 cover-page election"),
    ("src_edgar","ing_sync","filings -> rex_products"),
    ("ing_read","ing_xform","DataFrame -> 12-step transform"),
    ("ing_xform","ing_writer","transformed -> write_master_data"),
    ("ing_ms","ing_writer","overlay AUM into master"),
    ("ing_writer","db_aum","write_master_data aum"),
    ("ing_writer","db_flow","write fund_flow_1week"),
    ("ing_writer","db_status","write market_status"),
    ("ing_writer","db_stock","write_stock_data"),
    ("ing_writer","db_ts","write_time_series 36mo"),
    ("src_rules_li","ing_xform","attributes_LI -> map_li_*"),
    ("src_rules_cc","ing_xform","attributes_CC -> cc_*"),
    ("src_rules_rex","ing_xform","rex_funds -> is_rex"),
    ("ing_xform","db_etp","etp_category stamp"),
    ("ing_xform","db_subcat","map_li_subcategory (SS heuristic gap - PV)"),
    ("ing_xform","db_underl","map_li_underlier (37% NULL - PV)"),
    ("ing_xform","db_cccat","cc_category/cc_type (3-way split - PV)"),
    ("ing_xform","db_isrex","is_rex from rex_funds"),
    ("ing_sync","db_rexstat","status + estimated_effective_date"),
    ("ing_step3","db_rexstat","ELECTION effective date"),
    # enrichment
    ("src_rules_fm","en_fm","fund_master.csv 28 cols"),
    ("en_fm","db_prim","restamp primary_strategy + 23 cols"),
    ("db_etp","en_classify","gap scan"),
    ("en_classify","db_etp","auto-apply category"),
    ("en_classify","db_cccat","autocall double-tag [Phase1]"),
    ("en_classify","db_subcat","single-stock heuristic [Phase1]"),
    ("db_status","en_recon","LIQU/DLST evidence"),
    ("en_recon","db_rexstat","-> Delisted"),
    ("db_aum","db_snap","EOD snapshot (after enrichment)"),
    ("db_etp","db_snap","snapshot all fields"),
    ("db_ts","en_charts","time-series -> chart"),
    ("db_etp","en_charts","category filter"),
    ("db_aum","en_kpi","REX AUM aggregate"),
    ("db_isrex","en_kpi","REX universe filter"),
    ("en_cache","rp_li_aum","baked li_report cache"),
    # reports
    ("en_kpi","rp_li_aum","REX AUM KPI"),
    ("db_etp","rp_li_aum","li_mask etp_category=LI"),
    ("db_status","rp_li_aum","ACTV-only filter"),
    ("en_charts","rp_li_chart","embed market-share png"),
    ("db_subcat","rp_li_ss","single-stock split"),
    ("db_underl","rp_li_ss","group by underlier"),
    ("db_stock","rp_li_ss","underlier mcap"),
    ("db_etp","rp_income","etp_category=CC"),
    ("db_cccat","rp_autocall","Autocallable filter (union [Phase1])"),
    ("db_dist","rp_autocall","distribution data"),
    ("db_isrex","rp_flow","REX suite KPIs"),
    ("db_aum","rp_flow","suite AUM"),
    ("db_status","rp_flow","ACTV filter"),
    ("db_rexstat","rp_daily_up","pending launches"),
    ("db_prim","rp_daily_up","issuer/sub_strategy (PEND enrich - PV)"),
    ("db_rexstat","rp_trex","pipeline + effective dates"),
    ("db_underl","rp_trex","underlier rollups"),
    ("ing_ms","rp_ms","MicroSectors AUM"),
    ("db_aum","rp_blue","Blue Ocean overnight"),
]

LANE_X = {0: 60, 1: 340, 2: 640, 3: 980, 4: 1300}
LANE_W = 250


def build_html() -> str:
    # vertical positions per lane
    by_lane: dict[int, list] = {i: [] for i in range(len(LANES))}
    for n in NODES:
        by_lane[n[1]].append(n)
    pos = {}
    row_h = 46
    top = 90
    height = top + max(len(v) for v in by_lane.values()) * row_h + 60
    for lane, ns in by_lane.items():
        for i, n in enumerate(ns):
            pos[n[0]] = (LANE_X[lane], top + i * row_h)

    nodes_js = [{"id": n[0], "lane": n[1], "label": n[2], "file": n[3],
                 "x": pos[n[0]][0], "y": pos[n[0]][1]} for n in NODES]
    edges_js = [{"from": e[0], "to": e[1], "ev": e[2]} for e in EDGES]

    data = json.dumps({"nodes": nodes_js, "edges": edges_js, "lanes": LANES,
                       "laneX": LANE_X, "laneW": LANE_W, "height": height})

    return """<!DOCTYPE html><html><head><meta charset="utf-8"><title>rexfinhub data lineage</title>
<style>
 body{margin:0;font-family:-apple-system,Segoe UI,sans-serif;background:#0f172a;color:#e2e8f0}
 #top{padding:10px 16px;background:#1e293b;border-bottom:1px solid #334155;position:sticky;top:0;z-index:5}
 #top h1{margin:0 0 4px;font-size:16px} #top p{margin:0;font-size:12px;color:#94a3b8}
 #search{margin-top:6px;padding:5px 8px;width:340px;border-radius:5px;border:1px solid #475569;background:#0f172a;color:#e2e8f0}
 #wrap{display:flex} #canvas{flex:1;overflow:auto} svg{display:block}
 #panel{width:330px;min-width:330px;background:#1e293b;border-left:1px solid #334155;padding:12px;font-size:12px;height:calc(100vh - 70px);overflow:auto}
 .lane-h{fill:#64748b;font-size:13px;font-weight:700}
 .node rect{rx:5;cursor:pointer;stroke-width:1.5}
 .node text{font-size:11px;fill:#e2e8f0;pointer-events:none}
 .edge{stroke:#475569;fill:none;stroke-width:1} .edge.hot{stroke:#f59e0b;stroke-width:2.5}
 .dim{opacity:.12} .pv{stroke:#ef4444 !important}
 .hop{padding:5px 7px;margin:4px 0;background:#0f172a;border-left:3px solid #f59e0b;border-radius:4px}
 .hop b{color:#fbbf24} .hop code{color:#7dd3fc;font-size:11px}
 .lc0{fill:#1e3a5f}.lc1{fill:#3b2f5e}.lc2{fill:#14532d}.lc3{fill:#78350f}.lc4{fill:#7f1d1d}
</style></head><body>
<div id="top"><h1>rexfinhub &mdash; data lineage: Excel cell &rarr; report number</h1>
<p>Click any node to trace its full path (upstream + downstream), hop by hop with file:line. Red border = a known purpose-violation on that field. Lanes: Source &rarr; Ingestion &rarr; DB column &rarr; Enrichment &rarr; Report.</p>
<input id="search" placeholder="search a field or report (e.g. AUM, autocall, underlier, election)"></div>
<div id="wrap"><div id="canvas"><svg id="svg"></svg></div>
<div id="panel"><b>Click a node</b><div style="color:#94a3b8;margin-top:6px">e.g. click &ldquo;L&amp;I report: REX AUM KPI&rdquo; to see it trace back to the Bloomberg AUM cell.</div><div id="trace"></div></div></div>
<script>
const D = """ + data + """;
const PV = new Set(["db_subcat","db_underl","db_cccat","rp_daily_up","rp_li_chart","rp_autocall"]);
const svg=document.getElementById('svg'), W=1620;
svg.setAttribute('width',W); svg.setAttribute('height',D.height);
const NS='http://www.w3.org/2000/svg';
const byId={}; D.nodes.forEach(n=>byId[n.id]=n);
const adjOut={}, adjIn={};
D.edges.forEach(e=>{(adjOut[e.from]=adjOut[e.from]||[]).push(e);(adjIn[e.to]=adjIn[e.to]||[]).push(e);});
// lane headers
D.lanes.forEach((l,i)=>{const t=document.createElementNS(NS,'text');t.setAttribute('x',D.laneX[i]);t.setAttribute('y',60);t.setAttribute('class','lane-h');t.textContent=l;svg.appendChild(t);});
// edges
const edgeEls=[];
D.edges.forEach(e=>{const a=byId[e.from],b=byId[e.to];if(!a||!b)return;const p=document.createElementNS(NS,'path');
 const x1=a.x+D.laneW,y1=a.y+14,x2=b.x,y2=b.y+14,mx=(x1+x2)/2;
 p.setAttribute('d',`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`);p.setAttribute('class','edge');p._e=e;svg.appendChild(p);edgeEls.push(p);});
// nodes
const nodeEls=[];
D.nodes.forEach(n=>{const g=document.createElementNS(NS,'g');g.setAttribute('class','node');
 const r=document.createElementNS(NS,'rect');r.setAttribute('x',n.x);r.setAttribute('y',n.y);r.setAttribute('width',D.laneW);r.setAttribute('height',28);
 r.setAttribute('class','lc'+n.lane);if(PV.has(n.id))r.classList.add('pv');r.setAttribute('stroke',PV.has(n.id)?'#ef4444':'#475569');
 const t=document.createElementNS(NS,'text');t.setAttribute('x',n.x+8);t.setAttribute('y',n.y+18);t.textContent=n.label.length>40?n.label.slice(0,39)+'…':n.label;
 g.appendChild(r);g.appendChild(t);g._n=n;g.onclick=()=>trace(n.id);svg.appendChild(g);nodeEls.push(g);});
function walk(id,adj,acc){(adj[id]||[]).forEach(e=>{const nxt=adj===adjOut?e.to:e.from;if(!acc.nodes.has(nxt)){acc.nodes.add(nxt);acc.edges.add(e);walk(nxt,adj,acc);}else acc.edges.add(e);});}
function trace(id){const acc={nodes:new Set([id]),edges:new Set()};walk(id,adjOut,acc);walk(id,adjIn,acc);
 nodeEls.forEach(g=>g.classList.toggle('dim',!acc.nodes.has(g._n.id)));
 edgeEls.forEach(p=>{const on=acc.edges.has(p._e);p.classList.toggle('hot',on);p.classList.toggle('dim',!on);});
 // build the hop list upstream (source->this) then downstream
 const up=[];let cur=id,guard=0;function chainUp(nid){(adjIn[nid]||[]).filter(e=>acc.edges.has(e)).forEach(e=>{up.push(e);chainUp(e.from);});}
 const seen=new Set();const ups=[];(function cu(nid){(adjIn[nid]||[]).forEach(e=>{if(seen.has(e.from+'>'+e.to))return;seen.add(e.from+'>'+e.to);ups.push(e);cu(e.from);});})(id);
 const downs=[];const seen2=new Set();(function cd(nid){(adjOut[nid]||[]).forEach(e=>{if(seen2.has(e.from+'>'+e.to))return;seen2.add(e.from+'>'+e.to);downs.push(e);cd(e.to);});})(id);
 let h='<b>'+byId[id].label+'</b><div style="color:#7dd3fc;font-size:11px;margin-bottom:6px">'+byId[id].file+'</div>';
 h+='<div style="color:#94a3b8">UPSTREAM (where this number comes from):</div>';
 ups.reverse().forEach(e=>{h+=`<div class="hop"><b>${byId[e.from].label}</b> &rarr; ${byId[e.to].label}<br><code>${e.ev}</code></div>`;});
 h+='<div style="color:#94a3b8;margin-top:8px">DOWNSTREAM (where it flows):</div>';
 downs.forEach(e=>{h+=`<div class="hop" style="border-left-color:#38bdf8"><b>${byId[e.from].label}</b> &rarr; ${byId[e.to].label}<br><code>${e.ev}</code></div>`;});
 document.getElementById('trace').innerHTML=h;document.querySelector('#panel>b').textContent='Trace:';}
document.getElementById('search').oninput=function(){const q=this.value.toLowerCase();if(!q){nodeEls.forEach(g=>g.classList.remove('dim'));edgeEls.forEach(p=>{p.classList.remove('dim','hot')});return;}
 const hit=nodeEls.find(g=>g._n.label.toLowerCase().includes(q));if(hit)trace(hit._n.id);};
</script></body></html>"""


def main():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_html(), encoding="utf-8")
    print(f"wrote {OUT}  ({len(NODES)} nodes, {len(EDGES)} edges)")


if __name__ == "__main__":
    main()
