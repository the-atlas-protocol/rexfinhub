"""Build the interactive system-architecture EXPLORER -> docs/architecture/explorer.html.

A real dependency-graph tool (vis-network), not a static diagram: drag-anywhere pan,
wheel zoom, click any node to see its purpose + upstream/downstream dependencies in a
side panel, search, and "fit". Nodes = every load-bearing component (sources, Excel
sheets, scripts, DB tables, reports, website routes, docs); edges = real data
dependencies. Table row counts are pulled live from the DB so it reflects actual data.

Regenerate:  python scripts/build_system_explorer.py [--db data/etp_tracker.db]
"""
from __future__ import annotations
import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "architecture" / "explorer.html"

# group -> (color, hierarchical level). Flow runs left->right by level.
GROUPS = {
    "source":  ("#3b82f6", 0),
    "sheet":   ("#0ea5e9", 0),
    "rules":   ("#6366f1", 0),
    "script":  ("#f59e0b", 1),
    "table":   ("#10b981", 2),
    "report":  ("#a855f7", 3),
    "web":     ("#ec4899", 4),
    "device":  ("#64748b", 0),
    "doc":     ("#94a3b8", 4),
}

# id, label, group, purpose, location/device, [health: ok|warn|bad]
NODES = [
    # ---- devices (context) ----
    ("dev_vps",   "VPS (production)", "device", "Authoritative SQLite DB + every daemon/timer. jarvis@46.224.126.196.", "VPS", "ok"),
    ("dev_local", "Local C:", "device", "Dev only. Syncthing laptop↔desktop. Never authoritative for data.", "C:/Foundry/Rexfinhub", "ok"),
    ("dev_d",     "D: archive", "device", "Cold archive — nightly tarballs + cache snapshots. Not queried live.", "D:/sec-data", "ok"),
    ("dev_render","Render (rexfinhub.com)", "device", "Read-only replica of the VPS DB; webapp. Auto-deploys on push to main.", "render.com", "warn"),
    # ---- sources ----
    ("src_bbg",   "Bloomberg daily xlsm", "source", "The market data source: AUM, flows, prices, status, leverage, single-stock flag.", "data/DASHBOARD/bloomberg_daily_file.xlsm", "ok"),
    ("src_edgar", "SEC EDGAR", "source", "485 filings + daily index → new funds, effective dates, lifecycle.", "etp_tracker/sec_client.py", "ok"),
    ("src_cboe",  "CBOE", "source", "Symbol-reservation sweep (cookie-gated; currently expired).", "webapp/services/cboe/", "bad"),
    # ---- key Excel sheets ----
    ("sh_w4",     "sheet: w1–w5 (metadata)", "sheet", "Per-ticker fund metadata: name, issuer, fund_type, is_singlestock, uses_leverage.", "xlsm w1..w5", "ok"),
    ("sh_aum",    "sheet: data_aum/flow", "sheet", "Per-ticker AUM + flow time series (Bloomberg, raw — ETN AUM = issuance, inflated).", "xlsm data_aum/data_flow", "warn"),
    ("sh_ms",     "sheet: microsector_aum", "sheet", "TRUE MicroSectors ETN AUM/flow (overrides the inflated Bloomberg issuance).", "xlsm microsector_aum/flow/sh", "ok"),
    ("sh_status", "sheet: mkt_status", "sheet", "Per-ticker market_status (ACTV/LIQU/PEND/DLST).", "xlsm mkt_status", "ok"),
    # ---- rules ----
    ("r_fm",      "fund_master.csv", "rules", "Curated 3-axis taxonomy (asset_class × primary_strategy × sub_strategy). Overrides auto.", "config/rules/fund_master.csv", "ok"),
    ("r_attr",    "attributes_LI/CC/…", "rules", "Per-category attribute maps (map_li_subcategory, cc_category, underlier, …).", "config/rules/attributes_*.csv", "warn"),
    ("r_rex",     "rex_funds.csv", "rules", "is_rex membership — which tickers are REX products.", "config/rules/rex_funds.csv", "ok"),
    ("r_iss",     "issuer_brand_overrides.csv", "rules", "ticker → issuer_display brand map (regex-derived + manual).", "config/rules/issuer_brand_overrides.csv", "warn"),
    # ---- scripts (ingestion + enrichment) ----
    ("s_sync",    "sync_market_data", "script", "Full-replace mkt_master_data from the xlsm (dedup on ticker,etp_category).", "webapp/services/market_sync.py", "ok"),
    ("s_classify","classify_daily", "script", "Autonomous classifier (rules→LLM→critic→queue) → etp_category + map_li_*/cc_*.", "scripts/classify_daily.py", "warn"),
    ("s_fm",      "apply_fund_master", "script", "Restamp the curated 3-axis taxonomy onto mkt_master_data (curated tickers only).", "scripts/apply_fund_master.py", "warn"),
    ("s_iss",     "derive+apply_issuer_brands", "script", "Regenerate brand map and stamp issuer_display.", "scripts/derive_issuer_brands.py", "warn"),
    ("s_ms",      "microsectors override", "script", "Replace inflated Bloomberg ETN AUM with true AUM at report-build time.", "market/microsectors.py", "ok"),
    ("s_recon",   "status_reconciler", "script", "3-source rule → status_history → rex_products.status_cached.", "webapp/services/status_reconciler.py", "ok"),
    ("s_snap",    "snapshot_daily", "script", "Append the fully-enriched end-of-day state to mkt_daily_snapshot.", "scripts/snapshot_daily.py", "ok"),
    ("s_commit",  "commit_rules_delta", "script", "Commit rules CSVs to git + push (→ Render replica).", "scripts/commit_rules_delta.py", "ok"),
    ("s_sec",     "SEC pipeline", "script", "step3 485 parser + sync_rex_products_from_filings → REX lifecycle.", "etp_tracker/step3.py", "ok"),
    # ---- DB tables ----
    ("t_mkt",     "mkt_master_data", "table", "THE hub: one row per fund per sync, all market + classification columns. Full-replaced each sync.", "etp_tracker.db", "ok"),
    ("t_ts",      "mkt_time_series", "table", "36-month AUM/flow per ticker — feeds charts.", "etp_tracker.db", "ok"),
    ("t_snap",    "mkt_daily_snapshot", "table", "Append-only per-day history of every tracked field (WS-C1).", "etp_tracker.db", "ok"),
    ("t_rex",     "rex_products", "table", "REX product lifecycle authority (Filed→Effective→Listed→Delisted).", "etp_tracker.db", "ok"),
    ("t_hist",    "status_history", "table", "Bi-temporal status record — THE status authority (ADR 0008/0012).", "etp_tracker.db", "ok"),
    ("t_filings", "filings / fund_extractions / fund_status", "table", "SEC filing hierarchy + per-series lifecycle feed.", "etp_tracker.db", "ok"),
    ("t_auto",    "autocall_* / fund_distributions", "table", "Autocallable product data + distribution history.", "etp_tracker.db", "ok"),
    ("t_li",      "li_engine_daily", "table", "Canonical L&I score history (final_score v1.0.1).", "etp_tracker.db", "ok"),
    ("t_cache",   "mkt_report_cache / assertion_run", "table", "Baked report JSON + daily assertion results.", "etp_tracker.db", "ok"),
    ("t_live",    "live_feed.db", "table", "SEC live-feed store (atom-watcher).", "data/live_feed.db", "ok"),
    # ---- reports ----
    ("rp_daily",  "Daily filing report", "report", "Intraday filings + Upcoming Launches (grouped by primary_strategy — NULL→'Other' bug).", "etp_tracker/email_alerts.py", "bad"),
    ("rp_weekly", "Weekly digest", "report", "5-category landscape, issuer rankings, winners/losers.", "etp_tracker/weekly_digest.py", "ok"),
    ("rp_li",     "L&I report", "report", "REX position in Leveraged & Inverse + 3 charts (incl. market-share).", "webapp/services/report_emails.py", "warn"),
    ("rp_income", "Income report", "report", "Covered-call / income ETPs + 3 charts.", "webapp/services/report_emails.py", "warn"),
    ("rp_flow",   "Flow report", "report", "Per-suite competitive flow (T-REX, MicroSectors, autocall, …).", "webapp/services/report_data.py", "warn"),
    ("rp_auto",   "Autocallable update", "report", "Autocallable weekly (now 22 funds / $2.9B after fix).", "webapp/services/report_emails.py", "ok"),
    ("rp_trex",   "T-REX Stock Recs", "report", "trex_combined_v9 — the recommendation system report.", "screener/li_engine/analysis/trex_combined_v9.py", "warn"),
    ("rp_brief",  "T-REX Brief", "report", "recommendation_brief — on-demand, you supply names.", "screener/li_engine/analysis/recommendation_brief.py", "ok"),
    ("rp_blue",   "Blue Ocean", "report", "Overnight L&I trading (monthly).", "screener/li_engine/analysis/blue_ocean_report.py", "ok"),
    ("rp_port",   "Portfolio suite", "report", "REX 20-fund portfolio suite flows.", "webapp/services/portfolio_suite_flow.py", "ok"),
    ("rp_ms",     "MicroSectors L&I Industry Report", "report", "Competitive L&I industry report from MicroSectors' lens (5-page PDF). NOT yet wired.", "TO BUILD", "bad"),
    # ---- website ----
    ("w_screen",  "/screener · /market", "web", "L&I + ETP screening pages.", "webapp/routers/", "ok"),
    ("w_ops",     "/operations/pipeline", "web", "REX lifecycle pipeline view.", "webapp/routers/", "ok"),
    ("w_fil",     "/filings · /filings/symbols", "web", "Filing tracker + CBOE symbol reservation.", "webapp/routers/filings.py", "warn"),
    # ---- docs ----
    ("d_ledger",  "SYSTEM_LEDGER + six-doc framework", "doc", "Consolidated purpose map + ARCHITECTURE/SYSTEM/TARGET/RUNBOOK/CLASSIFICATION/GLOSSARY + ADRs.", "docs/", "ok"),
]

EDGES = [
    ("src_bbg","sh_w4"),("src_bbg","sh_aum"),("src_bbg","sh_ms"),("src_bbg","sh_status"),
    ("sh_w4","s_sync"),("sh_aum","s_sync"),("sh_status","s_sync"),
    ("s_sync","t_mkt"),("s_sync","t_ts"),
    ("r_rex","s_sync"),("r_attr","s_classify"),
    ("s_sync","s_classify"),("s_classify","t_mkt"),("s_classify","s_fm"),
    ("r_fm","s_fm"),("s_fm","t_mkt"),
    ("r_iss","s_iss"),("s_iss","t_mkt"),
    ("s_classify","s_iss"),
    ("s_fm","s_recon"),("s_recon","t_hist"),("t_hist","t_rex"),
    ("s_recon","s_snap"),("t_mkt","t_snap"),("s_snap","s_commit"),
    ("src_edgar","s_sec"),("s_sec","t_filings"),("s_sec","t_rex"),
    ("src_cboe","w_fil"),
    ("sh_ms","s_ms"),("s_ms","rp_li"),("s_ms","rp_flow"),("s_ms","rp_ms"),
    # reports read mkt_master_data (+ others)
    ("t_mkt","rp_daily"),("t_mkt","rp_weekly"),("t_mkt","rp_li"),("t_mkt","rp_income"),
    ("t_mkt","rp_flow"),("t_mkt","rp_auto"),("t_mkt","rp_port"),("t_mkt","rp_ms"),
    ("t_ts","rp_li"),("t_ts","rp_income"),("t_ts","rp_ms"),
    ("t_rex","rp_trex"),("t_rex","rp_brief"),("t_rex","rp_daily"),
    ("t_li","rp_trex"),("t_auto","rp_auto"),
    ("t_mkt","rp_blue"),
    # publish
    ("s_commit","dev_render"),("t_mkt","dev_render"),
    ("t_mkt","w_screen"),("t_mkt","w_ops"),("t_rex","w_ops"),("t_filings","w_fil"),
    ("dev_render","w_screen"),("dev_render","w_ops"),("dev_render","w_fil"),
    # devices / archive
    ("t_mkt","t_live"),("dev_vps","dev_d"),
    ("d_ledger","t_mkt"),
]


def _row_counts(db_path: Path) -> dict:
    counts = {}
    if not db_path.exists():
        return counts
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        for tbl in ("mkt_master_data", "mkt_time_series", "mkt_daily_snapshot",
                    "rex_products", "status_history", "filings", "fund_extractions",
                    "li_engine_daily"):
            try:
                counts[tbl] = con.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
            except sqlite3.Error:
                pass
        con.close()
    except sqlite3.Error:
        pass
    return counts


def build_html(counts: dict) -> str:
    nodes = []
    HB = {"ok": "#22c55e", "warn": "#f59e0b", "bad": "#ef4444"}
    for nid, label, grp, desc, loc, health in NODES:
        color = GROUPS[grp][0]
        nodes.append({
            "id": nid, "label": label, "group": grp, "level": GROUPS[grp][1],
            "desc": desc, "loc": loc, "health": health,
            "color": {"background": color, "border": HB.get(health, "#475569"),
                      "highlight": {"background": color, "border": "#fbbf24"}},
            "borderWidth": 3 if health != "ok" else 1,
        })
    edges = [{"from": a, "to": b, "arrows": "to"} for a, b in EDGES]
    data = json.dumps({"nodes": nodes, "edges": edges, "counts": counts,
                       "groups": {g: GROUPS[g][0] for g in GROUPS}})
    return """<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">
<title>rexfinhub — system explorer</title>
<script src="https://cdn.jsdelivr.net/npm/vis-network@9.1.9/standalone/umd/vis-network.min.js"></script>
<style>
 *{box-sizing:border-box} html,body{margin:0;height:100%;font-family:-apple-system,Segoe UI,sans-serif;background:#0b1220;color:#e2e8f0}
 #bar{height:52px;display:flex;align-items:center;gap:12px;padding:0 16px;background:#111a2e;border-bottom:1px solid #233}
 #bar h1{font-size:15px;margin:0;font-weight:700} #bar .sub{font-size:12px;color:#8aa}
 #search{margin-left:auto;padding:6px 10px;width:300px;border-radius:6px;border:1px solid #345;background:#0b1220;color:#e2e8f0}
 button{background:#233;color:#e2e8f0;border:1px solid #456;border-radius:6px;padding:6px 12px;cursor:pointer}
 #main{position:absolute;top:52px;left:0;right:330px;bottom:0}
 #net{width:100%;height:100%}
 #panel{position:absolute;top:52px;right:0;bottom:0;width:330px;background:#111a2e;border-left:1px solid #233;padding:16px;overflow:auto}
 #panel h2{margin:0 0 2px;font-size:16px} #panel .grp{display:inline-block;font-size:11px;padding:2px 8px;border-radius:10px;margin-bottom:8px}
 #panel .desc{font-size:13px;line-height:1.5;color:#cbd5e1;margin:8px 0} #panel .loc{font-size:11px;color:#7dd3fc;word-break:break-all;margin-bottom:12px}
 #panel h3{font-size:12px;color:#8aa;text-transform:uppercase;letter-spacing:.5px;margin:14px 0 4px}
 .dep{font-size:12px;padding:5px 8px;background:#0b1220;border-left:3px solid #456;border-radius:4px;margin:3px 0;cursor:pointer}
 .dep:hover{border-left-color:#fbbf24}
 #legend{position:absolute;left:14px;bottom:14px;background:rgba(17,26,46,.92);border:1px solid #233;border-radius:8px;padding:8px 10px;font-size:11px}
 #legend span{display:inline-block;margin:2px 8px 2px 0} .sw{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:4px;vertical-align:middle}
</style></head><body>
<div id="bar"><h1>rexfinhub — system explorer</h1><span class="sub">drag to pan · scroll to zoom · click a node for its purpose + dependencies</span>
 <input id="search" placeholder="search a component…"><button onclick="NET.fit({animation:true})">Fit</button></div>
<div id="main"><div id="net"></div>
 <div id="legend"></div></div>
<div id="panel"><h2>Click any node</h2><div class="desc">Every box is a real component. Click it to see what it's for and exactly which components feed it (upstream) and which it feeds (downstream). Click a dependency to jump to it.</div></div>
<script>
const D = """ + data + """;
const LBL = {source:"Source",sheet:"Excel sheet",rules:"Rules CSV",script:"Script",table:"DB table",report:"Report",web:"Website",device:"Device",doc:"Docs"};
// annotate table labels with live row counts
D.nodes.forEach(n=>{ for(const [t,c] of Object.entries(D.counts)){ if(n.label.includes(t)){ n.label += "  ("+c.toLocaleString()+" rows)"; } } });
const byId={}; D.nodes.forEach(n=>byId[n.id]=n);
const outAdj={}, inAdj={};
D.edges.forEach(e=>{(outAdj[e.from]=outAdj[e.from]||[]).push(e.to);(inAdj[e.to]=inAdj[e.to]||[]).push(e.from);});
const nodes=new vis.DataSet(D.nodes.map(n=>({id:n.id,label:n.label,color:n.color,borderWidth:n.borderWidth,level:n.level,
   shape:"box",font:{color:"#e8eefc",size:14,face:"Segoe UI",multi:false},margin:8,widthConstraint:{maximum:230}})));
const edges=new vis.DataSet(D.edges.map((e,i)=>({id:i,from:e.from,to:e.to,arrows:"to",color:{color:"#3a4a63",highlight:"#fbbf24"},smooth:{type:"cubicBezier",forceDirection:"horizontal",roundness:.4}})));
const NET=new vis.Network(document.getElementById("net"),{nodes,edges},{
  layout:{hierarchical:{enabled:true,direction:"LR",sortMethod:"directed",levelSeparation:260,nodeSpacing:70,treeSpacing:60}},
  physics:false, interaction:{dragNodes:true,dragView:true,zoomView:true,hover:true,navigationButtons:false},
  nodes:{shapeProperties:{borderRadius:6}}
});
function showPanel(id){
  const n=byId[id]; if(!n)return;
  const up=(inAdj[id]||[]), dn=(outAdj[id]||[]);
  const dep=(arr,dir)=>arr.length?arr.map(x=>`<div class="dep" onclick="jump('${x}')">${dir} ${byId[x].label.replace(/\\s+\\(.*/,'')}</div>`).join(""):'<div style="color:#667;font-size:12px">none</div>';
  document.getElementById("panel").innerHTML=
    `<h2>${n.label.replace(/\\s+\\(.*/,'')}</h2>`+
    `<span class="grp" style="background:${D.groups[n.group]}33;color:${D.groups[n.group]}">${LBL[n.group]||n.group}</span>`+
    `<div class="desc">${n.desc}</div><div class="loc">📍 ${n.loc}</div>`+
    `<h3>⬆ Upstream (feeds this)</h3>${dep(up,'←')}`+
    `<h3>⬇ Downstream (this feeds)</h3>${dep(dn,'→')}`;
  // highlight neighbourhood
  const keep=new Set([id,...up,...dn]);
  nodes.update(D.nodes.map(x=>({id:x.id,opacity:keep.has(x.id)?1:0.25})));
}
function jump(id){NET.selectNodes([id]);NET.focus(id,{scale:1.1,animation:true});showPanel(id);}
NET.on("click",p=>{ if(p.nodes.length){showPanel(p.nodes[0]);} else {nodes.update(D.nodes.map(x=>({id:x.id,opacity:1})));} });
document.getElementById("search").addEventListener("input",function(){const q=this.value.toLowerCase().trim();
  if(!q){nodes.update(D.nodes.map(x=>({id:x.id,opacity:1})));return;}
  const hit=D.nodes.filter(n=>(n.label+' '+n.desc).toLowerCase().includes(q)).map(n=>n.id);
  const s=new Set(hit); nodes.update(D.nodes.map(x=>({id:x.id,opacity:s.has(x.id)?1:0.18})));
  if(hit.length===1)jump(hit[0]);});
// legend
document.getElementById("legend").innerHTML=Object.entries(LBL).map(([g,l])=>`<span><span class="sw" style="background:${D.groups[g]}"></span>${l}</span>`).join("");
NET.once("afterDrawing",()=>NET.fit());
</script></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "etp_tracker.db"))
    args = ap.parse_args()
    counts = _row_counts(Path(args.db))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build_html(counts), encoding="utf-8")
    print(f"wrote {OUT}  ({len(NODES)} nodes, {len(EDGES)} edges, {len(counts)} live counts)")


if __name__ == "__main__":
    main()
