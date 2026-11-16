"""Chart-first presentation of saved measurements, without network resources."""

from __future__ import annotations

import json
from typing import Any

from .census import aggregate
from .ownership import PURPOSES
from .purposes import SUPPORTED_VERSIONS, summarize


def _view(rows: list[dict[str, Any]], *, measured: bool = False) -> dict[str, Any]:
    return {
        **summarize(rows, measured=measured),
        "union": aggregate(rows),
        "by_role": {
            role: aggregate([row for row in rows if row["role"] == role])
            for role in sorted({row["role"] for row in rows})
        },
        "extension_role": [
            {"extension": extension, "role": role, **aggregate([
                row for row in rows if row["extension"] == extension and row["role"] == role
            ])}
            for extension, role in sorted({(row["extension"], row["role"]) for row in rows})
        ],
    }


def _history_view(view: dict[str, Any]) -> dict[str, Any]:
    result = {key: view[key] for key in (
        "union", "by_role", "by_purpose", "source_stock", "extension_role",
        "extension_purpose", "agentic",
    ) if key in view}
    if "flow" in view:
        flow = view["flow"]
        result["flow"] = {key: flow[key] for key in (
            "additions", "deletions", "net", "changed_lines", "by_purpose",
        )}
        result["flow"]["files"] = [
            {key: row[key] for key in (
                "path", "purpose", "extension", "additions", "deletions", "change_type", "commit",
            ) if key in row}
            for row in flow["files"]
        ]
    return result


def _artefact_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row[key] for key in (
        "repository", "head", "path", "blob", "aliases", "alias_scopes",
        "scope_status", "purpose", "purpose_rule", "agentic", "enablement_kind",
        "build_discovery_kind", "harness_load_status", "primary_owner", "ownership_status",
    ) if key in row}


def sections(document: dict[str, Any]) -> str:
    history = document.get("history")
    if history:
        from .history import validate_history

        validate_history(document)
        history = {"points": [
            {
                **{key: point[key] for key in ("period", "partial", "cutoff", "commits")},
                "workspace": _history_view(point["workspace"]),
                "aliases": {alias: _history_view(view) for alias, view in point["aliases"].items()},
            }
            for point in history["points"]
        ]}
    ownership = document.get("ownership", {}).get("files")
    if ownership is not None:
        ownership = [
            {key: row[key] for key in (
                "repository", "head", "path", "blob", "aliases", "role", "purpose",
                "extension", "status", "primary_owner", "nonblank_text_lines",
                "source_code_lines", "agentic_documentation", "agentic_enablement",
            ) if key in row}
            for row in ownership
        ]
    artefacts = document.get("artefacts")
    if artefacts is not None:
        artefacts = {
            "workspace": artefacts["workspace"], "aliases": artefacts["aliases"],
            "files": [_artefact_row(row) for row in artefacts["files"]],
        }
    payload = {
        "current": {
            "workspace": _view(document["files"], measured=document["method"].get("purpose_classification") in SUPPORTED_VERSIONS),
            "aliases": {
                alias["alias"]: _view([row for row in document["files"] if alias["alias"] in row["aliases"]], measured=document["method"].get("purpose_classification") in SUPPORTED_VERSIONS)
                for alias in document["aliases"]
            },
        },
        "ownership": ownership,
        "history": history,
        "generated_at": document["generated_at"],
        "purposes": PURPOSES,
        "artefacts": artefacts,
        "files": [_artefact_row(row) for row in document["files"] if row.get("enablement_kind") or any(
            signal["status"] != "unknown" for signal in row.get("agentic", {}).values()
        )],
    }
    encoded = json.dumps(payload, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
    return f"""
<script type="application/json" id="chart-evidence">{encoded}</script>
<section class="chart-controls">
<label>Report scope <select id="chart-repo"><option value="">Workspace union</option></select></label>
<span id="chart-scope-note" class="muted"></span>
<p id="history-method" class="muted"></p>
</section>
<section id="timeline-section">
<h2>1. Five line-volume trends — not stacked</h2>
<div id="timeline-controls" class="legend"></div>
<div class="chart-scroll" id="combined-chart"></div>
<p id="timeline-summary" class="reading"></p>
<p>Blue implementation and teal testing are separate, absolute source-LOC series on the same left axis.
Documentation, product definition and development enablement use nonblank text lines on the purple right axis.
Nothing is stacked or added to another curve. Axis ranges stay fixed when hiding series; compare values against their labeled axis, not screen heights. Click a point to inspect that period.</p>
<h3>Separate diagnostic: implementation/testing ratio</h3>
<div class="chart-scroll" id="ratio-chart"></div>
<p>The dotted orange curve is a ratio, not a line count. Its dotted style highlights the different axis unit. It has its own plotting area so it cannot visually merge with implementation or testing. Zero counted test LOC makes this ratio undefined; it is not test coverage.</p>
</section>
<section id="agentic-section">
<h2>Agentic documentation and development enablement</h2>
<label>Lens <select id="agentic-lens"><option value="baseline">Configured baseline scope</option><option value="artefacts">Repository-wide artefact inventory (opt-in; latest only)</option></select></label>
<label>Measure <select id="agentic-metric"><option value="files">Files</option><option value="nonblank">Nonblank text lines</option></select></label>
<div id="agentic-legend" class="legend"></div>
<div class="chart-scroll" id="agentic-chart"></div>
<p id="agentic-summary" class="reading"></p>
<p>Filename candidates can include unresolved/mixed purposes; confirmed instruction formats establish presence, not loading or AI authorship.
Build/packaging enablement is shown separately without an agentic claim. Candidate and confirmed series are disjoint; do not add them to parent-purpose totals.</p>
<details class="evidence" id="agentic-details"><summary>Agentic artefact evidence and scope</summary>
<div class="scroll"><table><thead><tr><th>Path</th><th>Scope</th><th>Purpose / rule</th><th>Documentation signal</th><th>Enablement signal</th><th>Loading</th><th>Primary owner</th></tr></thead><tbody id="agentic-rows"></tbody></table></div>
</details>
</section>
<section>
<h2>2. What changed? Monthly purpose focus</h2>
<label>Display <select id="focus-mode"><option value="share">Share of text edits (%)</option><option value="absolute">Added + deleted physical lines</option><option value="net">Net physical lines</option></select></label>
<div id="focus-legend" class="legend"></div>
<div class="chart-scroll" id="focus-chart"></div>
<p id="focus-summary" class="reading"></p>
<p>Flow, not accumulated stock: physical additions/deletions include comments. This is not effort, productivity or agent attribution.
Fixtures, configuration, documentation and unknown purpose stay separate. Click a bar to inspect its changed files.</p>
</section>
<section>
<h2>3. Extension trends within purpose</h2>
<label>Purpose <select id="extension-purpose"><option value="source">Implementation + testing</option></select></label>
<label>Measure <select id="extension-metric"><option value="cloc_code">Counted source LOC</option><option value="cloc_comment">Source comment lines</option><option value="cloc_blank">Source blank lines</option><option value="nonblank">Nonblank text lines</option><option value="files">File count</option></select></label>
<div id="extension-legend" class="legend"></div>
<div class="chart-scroll" id="extension-chart"></div>
<p>Largest eight extensions plus an exact “Other extensions” remainder. Source LOC counts only production/test source, never fixture-native “code”.
Click a point for period/extension evidence. Purpose is a provisional role-derived lens.</p>
</section>
<div class="chart-grid">
<section>
<h2>4. Source code, comments and blanks</h2>
<label>View <select id="line-kind-mode"><option value="latest">Latest comparison</option><option value="history">Monthly history</option></select></label>
<label>Source <select id="line-kind-role"><option value="source">Implementation + testing</option><option value="production_source">Implementation</option><option value="test_source">Testing</option></select></label>
<div class="legend"><span><i style="background:#326bb4"></i>Code</span><span><i style="background:#238477"></i>Comments</span><span><i style="background:#bbc5d1"></i>Blank</span></div>
<div class="chart-scroll" id="line-kind-chart"></div>
<p id="line-kind-note"></p>
<p>Native cloc categories, only classified source. Mixed code/comment lines are unavailable. Fixtures are excluded.</p>
</section>
<section>
<h2>5. Latest purpose composition</h2>
<label>Weight <select id="purpose-weight"><option value="files">File count</option><option value="nonblank">Nonblank text lines</option></select></label>
<div class="chart-scroll" id="purpose-chart"></div>
<p>Includes unknown purpose. Text is not programming LOC; large fixtures can dominate text volume.</p>
</section>
</div>
<section>
<h2>6. Who owns each purpose or extension?</h2>
<label>Group <select id="chart-ownership-group"><option value="purpose">Purpose</option><option value="role">Role</option><option value="extension">Extension</option><option value="agentic_documentation">Agentic documentation status</option><option value="agentic_enablement">Agentic enablement status</option></select></label>
<label>Weight <select id="chart-ownership-weight"><option value="files">Files</option><option value="nonblank_text_lines">Nonblank text exposure</option><option value="source_code_lines">Counted source LOC exposure</option></select></label>
<div id="owner-legend" class="legend"></div>
<div class="chart-scroll" id="owner-chart"></div>
<p id="owner-summary" class="reading"></p>
<p>Latest snapshot only: no historical ownership is inferred. Primary-owned volume is not authored LOC.
Top eight owners plus all other known owners and separate metadata/no-human-owner/missing-export gaps; percentages include gaps.
Click a category to open ownership detail. Source-LOC weighting is N/A for purposes without counted source.</p>
</section>
<details class="evidence" id="history-details"><summary>Drill down into a period’s measured changes</summary>
<label>Period <select id="chart-period"></select></label>
<label>Purpose <select id="change-purpose"><option value="">All</option></select></label>
<label>Extension <select id="change-extension"><option value="">All</option></select></label>
<p id="change-summary"></p>
<div class="scroll"><table><thead><tr><th>Path</th><th>Purpose</th><th>Extension</th><th>Added</th><th>Deleted</th><th>Change</th><th>Commit</th></tr></thead><tbody id="change-rows"></tbody></table></div>
<button id="more-changes" type="button">Show next 200 changes</button>
</details>
"""


STYLE = """
.chart-grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}
.chart-grid section{min-width:0;margin:0}
.chart-scroll{overflow-x:auto;max-width:100%}
.chart-scroll svg{display:block;width:100%;min-width:720px;height:auto}
.chart-grid .chart-scroll svg{min-width:480px}
.legend{display:flex;flex-wrap:wrap;gap:10px 18px;margin:12px 0;font-size:13px}
.legend span,.legend label{display:inline-flex;align-items:center;gap:6px}
.legend i{display:inline-block;width:13px;height:13px;border-radius:3px;flex-shrink:0}
.legend input{accent-color:#326bb4}
#agentic-section select{max-width:100%}
.reading{background:#edf3fa;padding:12px;border-radius:6px}
.evidence{background:white;border:1px solid #dce3ed;border-radius:10px;padding:16px;margin:18px 0}
.evidence summary{font-weight:600;cursor:pointer}
.evidence section{border:0;padding:4px;margin:10px 0}
svg .interactive{cursor:pointer}
svg .interactive:hover{opacity:.72;stroke:#18283d;stroke-width:2}
button{border:1px solid #bbc5d1;border-radius:5px;background:#edf3fa;padding:8px 12px;cursor:pointer}
@media(max-width:900px){.chart-grid{grid-template-columns:1fr}}
"""


SCRIPT = r"""
const charts = JSON.parse(document.getElementById("chart-evidence").textContent);
const $ = id => document.getElementById(id);
const esc = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const fmt = value => Number(value).toLocaleString("en-US", {maximumFractionDigits:2});
const labels = {
  product_implementation:"Implementation", product_verification:"Testing",
  product_definition_candidate:"Product definition (candidate)",
  development_enablement_candidate:"Development enablement (candidate)",
  configuration_operations:"Configuration / operations", fixtures_data:"Fixtures / data",
  general_documentation:"General documentation", generated_vendor:"Generated / vendor",
  binary_asset:"Binary assets", unknown:"Unknown / mixed",
  product_definition:"Product definition",development_enablement:"Development enablement",
  runtime_configuration_operations:"Runtime configuration / operations",
  data_fixtures_design:"Data / fixtures / design",assets:"Assets",mixed_unknown:"Mixed / unknown"
};
const colors = {
  product_implementation:"#326bb4",product_verification:"#238477",
  product_definition_candidate:"#8055b5",development_enablement_candidate:"#b54b88",
  configuration_operations:"#546c86",fixtures_data:"#a68a25",
  general_documentation:"#96768b",generated_vendor:"#795548",binary_asset:"#4d6870",unknown:"#87929f",
  product_definition:"#8055b5",development_enablement:"#b54b88",
  runtime_configuration_operations:"#546c86",data_fixtures_design:"#a68a25",assets:"#4d6870",mixed_unknown:"#87929f"
};
const purposeOf = role => charts.purposes[role] || "unknown";
const scopeView = point => $("chart-repo").value ? point.aliases[$("chart-repo").value] : point.workspace;
const current = () => scopeView(charts.current);
const points = charts.history?.points || [{
  period:charts.generated_at.slice(0,7),partial:true,commits:{},workspace:charts.current.workspace,
  aliases:charts.current.aliases
}];
const stock = (view, role, field="cloc_code") => {
  const key=role==="production_source"?"implementation":role==="test_source"?"testing":null;
  return (key&&view?.source_stock?view.source_stock[key]?.[field]:view?.by_role?.[role]?.[field]) ?? 0;
};
const nonblank = total => (total?.physical_lines || 0) - (total?.blank_lines || 0);
const sumPurpose = (view,purpose,metric="nonblank") => view?.by_purpose ?
  (metric==="nonblank"?nonblank(view.by_purpose[purpose]):view.by_purpose[purpose]?.[metric]??0) : Object.entries(view?.by_role || {})
  .filter(([role])=>purposeOf(role)===purpose)
  .reduce((sum,[,total])=>sum+(metric==="nonblank"?nonblank(total):total[metric] || 0),0);
function option(select,value,text){const node=document.createElement("option");node.value=value;node.textContent=text;select.append(node);}
for(const alias of Object.keys(charts.current.aliases))option($("chart-repo"),alias,alias);
option($("extension-purpose"),"all","All purposes");
for(const key of Object.keys(labels)){
  option($("extension-purpose"),key,labels[key]);option($("change-purpose"),key,labels[key]);
}
option($("change-purpose"),"source","Implementation + testing");
option($("change-extension"),"__other","Other extensions in selected chart");
for(const point of points)option($("chart-period"),point.period,point.period+(point.partial?" (partial)":""));
for(const ext of [...new Set(points.flatMap(p=>Object.values(p.aliases).flatMap(v=>v.extension_role.map(r=>r.extension))))].sort())
  option($("change-extension"),ext,ext);
$("chart-period").value=points.at(-1).period;
const seriesDefinitions=[
  {key:"implementation",label:"Implementation / left LOC",color:colors.product_implementation,axis:"source"},
  {key:"testing",label:"Testing / left LOC",color:colors.product_verification,axis:"source"},
  {key:"documentation",label:"Documentation / right text",color:colors.general_documentation,axis:"text"},
  {key:"definition",label:"Product definition / right text",color:colors.product_definition_candidate,axis:"text"},
  {key:"enablement",label:"Development enablement / right text",color:colors.development_enablement_candidate,axis:"text"}
];
for(const s of seriesDefinitions){
  const label=document.createElement("label");const input=document.createElement("input");
  input.type="checkbox";input.checked=true;input.value=s.key;input.addEventListener("change",renderTimeline);
  label.append(input);const swatch=document.createElement("i");swatch.style.background=s.color;
  const caption=document.createElement("span");caption.dataset.series=s.key;caption.textContent=s.label;label.append(swatch,caption);
  $("timeline-controls").append(label);
}
function maximum(values){return Math.max(0,...values.filter(v=>v!==null && Number.isFinite(v)));}
function nice(value){
  if(value<=0)return 5;
  const power=10**Math.floor(Math.log10(value/5)),n=value/(5*power);
  return (n<=1?1:n<=2?2:n<=5?5:10)*power*5;
}
function svgStart(title,width=1180,height=400){
  return `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="${esc(title)}"><title>${esc(title)}</title>`;
}
function legend(id,series){
  $(id).innerHTML=series.map(s=>`<span><i style="background:${esc(s.color)}"></i>${esc(s.label)}</span>`).join("");
}
const xAt = (i,length,left=90,width=780) => left+(length<=1?width/2:i*width/(length-1));
function pointMark(x,y,color,period,values,series){
  return `<circle class="interactive" tabindex="0" role="button" aria-label="${esc(series)} ${esc(period)}: ${fmt(values)}" cx="${x}" cy="${y}" r="4" fill="${color}" data-period="${esc(period)}" data-series="${esc(series)}" data-value="${values}"><title>${esc(period)} · ${esc(series)}: ${fmt(values)}</title></circle>`;
}
function wirePeriods(id,callback){
  for(const mark of $(id).querySelectorAll("[data-period]")){
    mark.addEventListener("click",()=>callback(mark));
    mark.addEventListener("keydown",e=>{if(e.key==="Enter"||e.key===" "){e.preventDefault();callback(mark);}});
  }
}
function openPeriod(period,purpose="",extension=""){
  $("chart-period").value=period;$("change-purpose").value=purpose;$("change-extension").value=extension;
  $("history-details").open=true;renderChanges();
  $("history-details").scrollIntoView({block:"start"});
}
function renderTimeline(){
  const data=points.map(p=>{
    const v=scopeView(p),implementation=stock(v,"production_source"),testing=stock(v,"test_source");
    return {implementation,testing,documentation:sumPurpose(v,"general_documentation"),definition:sumPurpose(v,"product_definition_candidate")+sumPurpose(v,"product_definition"),
      enablement:sumPurpose(v,"development_enablement_candidate")+sumPurpose(v,"development_enablement"),ratio:testing?implementation/testing:null};
  });
  const axes={
    source:{max:nice(maximum(data.flatMap(v=>[v.implementation,v.testing]))),x:90,color:"#326bb4",label:"Source LOC",side:"left"},
    text:{max:nice(maximum(data.flatMap(v=>[v.documentation,v.definition,v.enablement]))),x:970,color:"#8055b5",label:"Nonblank text lines",side:"right"}
  };
  let svg=svgStart("Five unstacked line-volume series: implementation, testing, documentation, product definition and development enablement",1180,420);
  for(let tick=0;tick<=5;tick++){
    const y=65+tick*52;svg+=`<line x1="90" y1="${y}" x2="870" y2="${y}" stroke="#e0e6ee"/>`;
  }
  for(const [key,axis] of Object.entries(axes)){
    svg+=`<g data-axis="${key}" data-max="${axis.max}" fill="${axis.color}"><line x1="${axis.x}" y1="65" x2="${axis.x}" y2="325" stroke="${axis.color}"/><text x="${axis.x}" y="30" text-anchor="${axis.side==="left"?"start":"middle"}" font-size="14" font-weight="600">${axis.label}</text>`;
    for(let tick=0;tick<=5;tick++)svg+=`<text x="${axis.x+(axis.side==="left"?-8:8)}" y="${329-tick*52}" text-anchor="${axis.side==="left"?"end":"start"}" font-size="12">${fmt(axis.max*tick/5)}</text>`;
    svg+="</g>";
  }
  const selected=new Set([...$("timeline-controls").querySelectorAll("input:checked")].map(n=>n.value));
  for(const series of seriesDefinitions){
    if(!selected.has(series.key))continue;
    let path="",drawing=false;
    for(let i=0;i<data.length;i++){
      const value=data[i][series.key];
      if(value===null){drawing=false;continue;}
      const x=xAt(i,data.length),y=325-value/axes[series.axis].max*260;
      path+=`${drawing?"L":"M"}${x},${y} `;drawing=true;
    }
    const dash=series.key==="definition"?' stroke-dasharray="7 5"':series.key==="enablement"?' stroke-dasharray="2 6"':"";
    svg+=`<path data-series="${series.key}" data-axis-ref="${series.axis}" d="${path}" fill="none" stroke="${series.color}" stroke-width="2.5"${dash}/>`;
    data.forEach((v,i)=>{
      if(v[series.key]!==null)svg+=pointMark(xAt(i,data.length),325-v[series.key]/axes[series.axis].max*260,series.color,points[i].period,v[series.key],series.key);
    });
  }
  points.forEach((p,i)=>svg+=`<text x="${xAt(i,points.length)}" y="357" text-anchor="middle" font-size="11">${esc(p.period)}${p.partial?"*":""}</text>`);
  $("combined-chart").innerHTML=svg+"</svg>";
  wirePeriods("combined-chart",mark=>openPeriod(mark.dataset.period));
  const first=data[0],last=data.at(-1);
  for(const series of seriesDefinitions)$("timeline-controls").querySelector(`span[data-series="${series.key}"]`).textContent=
    `${series.label} · latest ${fmt(last[series.key])} ${series.axis==="source"?"LOC":"lines"}`;
  $("timeline-summary").textContent=`${points[0].period} → ${points.at(-1).period}: implementation ${fmt(first.implementation)} → ${fmt(last.implementation)} LOC; testing ${fmt(first.testing)} → ${fmt(last.testing)} LOC; documentation ${fmt(first.documentation)} → ${fmt(last.documentation)} nonblank lines. `+
    (data.every(v=>!v.definition&&!v.enablement)?"No classified definition/enablement candidates were counted inside these scopes. This does NOT establish their absence: excluded documents and unknown-purpose files are not assessed. ":"")+
    (charts.history?"* Partial latest month.":"Only one snapshot; no measured history is attached.");
  renderRatio(data);
}
function renderRatio(data){
  const max=nice(maximum(data.map(point=>point.ratio)));
  let svg=svgStart("Implementation/testing ratio only; separate from line-volume curves",1180,230);
  for(let tick=0;tick<=5;tick++){
    const y=180-tick*28;
    svg+=`<line x1="90" y1="${y}" x2="1060" y2="${y}" stroke="#e0e6ee"/><text x="82" y="${y+4}" text-anchor="end" font-size="11" fill="#a85219">${fmt(max*tick/5)} : 1</text>`;
  }
  let path="",markers="",drawing=false;
  data.forEach((point,i)=>{
    if(point.ratio===null){drawing=false;return;}
    const x=xAt(i,data.length,90,970),y=180-point.ratio/max*140;
    path+=`${drawing?"L":"M"}${x},${y} `;drawing=true;
    markers+=pointMark(x,y,"#a85219",points[i].period,point.ratio,"ratio");
  });
  svg+=`<path data-series="ratio" d="${path}" fill="none" stroke="#a85219" stroke-width="2.5" stroke-dasharray="1 6" stroke-linecap="round"/>`+markers;
  points.forEach((point,i)=>svg+=`<text x="${xAt(i,points.length,90,970)}" y="210" text-anchor="middle" font-size="11">${esc(point.period)}</text>`);
  $("ratio-chart").innerHTML=svg+"</svg>";wirePeriods("ratio-chart",mark=>openPeriod(mark.dataset.period));
}
function renderAgentic(){
  const inventory=$("agentic-lens").value==="artefacts",metric=$("agentic-metric").value;
  const selected=$("chart-repo").value;
  const latest=inventory?(selected?charts.artefacts?.aliases[selected]:charts.artefacts?.workspace):current();
  const views=inventory?[latest]:points.map(scopeView);
  if(!latest?.agentic || views.some(view=>!view?.agentic)){
    $("agentic-chart").innerHTML="<p>Not measured for this lens. Collect a new purpose-classified census; opt in to --include-artefacts for the separately scoped repository inventory. Old saved evidence is not reclassified.</p>";
    $("agentic-summary").textContent="No synthetic zeroes or agent-use claims are substituted for missing evidence.";
    $("agentic-legend").innerHTML="";$("agentic-rows").innerHTML="";return;
  }
  const definitions=[
    {key:"doc_candidate",family:"documentation",status:"candidate",label:"Documentation filename candidates (including unresolved)",color:"#8055b5",dash:"7 5"},
    {key:"doc_confirmed",family:"documentation",status:"confirmed",label:"Confirmed agent-oriented product definition",color:"#8055b5"},
    {key:"enable_candidate",family:"enablement",status:"candidate",label:"Enablement filename candidates (including unresolved)",color:"#b54b88",dash:"7 5"},
    {key:"enable_confirmed",family:"enablement",status:"confirmed",label:"Confirmed agent instruction formats / declarations",color:"#b54b88"},
    {key:"build",label:"Other build / packaging enablement",color:"#546c86",dash:"2 6"}
  ];
  const read=(view,d)=>{const total=d.key==="build"?view.agentic.non_agentic_enablement:view.agentic[d.family][d.status];return metric==="nonblank"?nonblank(total):total.files;};
  legend("agentic-legend",definitions.map(d=>({label:`${d.label} · latest ${fmt(read(latest,d))}`,color:d.color})));
  if(inventory){
    const shortLabels=["Documentation: candidate","Documentation: confirmed","Agent guidance: candidate","Agent guidance: confirmed","Build / packaging (other)"];
    horizontalBars("agentic-chart","Latest repository-wide agentic artefact discovery",definitions.map((d,i)=>({label:shortLabels[i],value:read(latest,d),color:d.color})),metric);
    $("agentic-summary").textContent=`Latest whole-repository lens, not historical trends: ${fmt(latest.union.files)} unique candidates; ${fmt(latest.by_scope.included.files)} included, ${fmt(latest.by_scope.configured_exclusion.files)} explicitly excluded, ${fmt(latest.by_scope.outside_analysis_paths.files)} outside configured modules. Native source LOC and ownership outside the census are not collected.`;
  }else{
    const max=nice(maximum(views.flatMap(view=>definitions.map(d=>read(view,d)))));
    let svg=svgStart(`Agentic presence trends: ${metric}; no agent provenance inferred`,1180,400);
    for(let tick=0;tick<=5;tick++){const y=325-tick*52;svg+=`<line x1="90" y1="${y}" x2="1060" y2="${y}" stroke="#e0e6ee"/><text x="82" y="${y+4}" text-anchor="end" font-size="11">${fmt(max*tick/5)}</text>`;}
    for(const d of definitions){
      const values=views.map(view=>read(view,d));
      svg+=`<path data-agentic-series="${d.key}" d="${values.map((value,i)=>`${i?"L":"M"}${xAt(i,views.length,90,970)},${325-value/max*260}`).join(" ")}" fill="none" stroke="${d.color}" stroke-width="2.5"${d.dash?` stroke-dasharray="${d.dash}"`:""}/>`;
      values.forEach((value,i)=>svg+=pointMark(xAt(i,views.length,90,970),325-value/max*260,d.color,points[i].period,value,d.key));
    }
    points.forEach((point,i)=>svg+=`<text x="${xAt(i,points.length,90,970)}" y="355" text-anchor="middle" font-size="11">${esc(point.period)}</text>`);
    $("agentic-chart").innerHTML=svg+"</svg>";wirePeriods("agentic-chart",mark=>openPeriod(mark.dataset.period));
    $("agentic-summary").textContent=(charts.history?"Measured frozen-classifier baseline history. Points open period change evidence; the table below is latest only. ":"Latest baseline snapshot only. ")+
      `Rejected substring matches: documentation ${fmt(latest.agentic.documentation.rejected.files)}, enablement ${fmt(latest.agentic.enablement.rejected.files)}. Files without discovery evidence remain unknown, not confirmed non-agentic.`;
  }
  const rows=(inventory?charts.artefacts.files:charts.files).filter(row=>(!selected||row.aliases.includes(selected))&&row.agentic&&
    (row.enablement_kind||row.build_discovery_kind||Object.values(row.agentic).some(signal=>signal.status!=="unknown")));
  const owners=new Map((charts.ownership||[]).map(row=>[JSON.stringify([row.repository,row.head,row.path,row.blob]),row]));
  $("agentic-rows").innerHTML=rows.map(row=>{
    const owner=inventory?row:owners.get(JSON.stringify([row.repository,row.head,row.path,row.blob]));
    const status=family=>`${row.agentic[family].status}: ${row.agentic[family].reason}; ${row.agentic[family].matches.join(", ")}`;
    const scope=inventory?(selected?row.alias_scopes[selected]:row.scope_status):"included baseline";
    return `<tr><td>${esc(row.path)}</td><td>${esc(scope)}</td><td>${esc(row.purpose)} / ${esc(row.purpose_rule)}</td><td>${esc(status("documentation"))}</td><td>${esc(status("enablement"))}</td><td>${esc(row.harness_load_status)}</td><td>${esc(owner?.primary_owner?.name||owner?.ownership_status||owner?.status||"not collected")}</td></tr>`;
  }).join("");
}
function renderFocus(){
  legend("focus-legend",Object.entries(labels).map(([key,label])=>({label,color:colors[key]})));
  const flows=points.map(p=>scopeView(p)?.flow),mode=$("focus-mode").value;
  if(!charts.history){$("focus-chart").innerHTML="<p>Historical edits are not measured in a single census.</p>";return;}
  const max=mode==="share"?100:nice(maximum(flows.map(f=>mode==="net"?
    Object.values(f?.by_purpose||{}).reduce((s,v)=>s+Math.abs(v.net),0):f?.changed_lines||0)));
  const baseline=mode==="net"?205:325,height=mode==="net"?135:260;
  let svg=svgStart("Monthly text change focus by purpose; additions and deletions, not productivity",1180,420);
  for(let i=0;i<=5;i++){const y=baseline-i*height/5;svg+=`<line x1="90" y1="${y}" x2="1080" y2="${y}" stroke="#e0e6ee"/><text x="82" y="${y+4}" text-anchor="end" font-size="12">${fmt(max*i/5)}${mode==="share"?"%":""}</text>`;}
  if(mode==="net")svg+=`<text x="82" y="343" text-anchor="end" font-size="12">−${fmt(max)}</text>`;
  flows.forEach((flow,i)=>{
    const x=95+i*980/points.length,width=780/points.length;let positive=0,negative=0;
    for(const [purpose,total] of Object.entries(flow?.by_purpose||{})){
      const value=mode==="share"?(flow.changed_lines?total.changed_lines/flow.changed_lines*100:0):mode==="net"?total.net:total.changed_lines;
      const magnitude=Math.abs(value)*height/max;
      const y=value<0?baseline+negative:baseline-positive-magnitude;
      if(value<0)negative+=magnitude;else positive+=magnitude;
      if(magnitude)svg+=`<rect class="interactive" tabindex="0" role="button" x="${x}" y="${y}" width="${width}" height="${magnitude}" fill="${colors[purpose]||colors.unknown}" data-period="${esc(points[i].period)}" data-purpose="${esc(purpose)}" data-value="${value}"><title>${esc(points[i].period)} · ${esc(labels[purpose]||purpose)}: +${fmt(total.additions)} / −${fmt(total.deletions)}; net ${fmt(total.net)}; ${fmt(flow.changed_lines?total.changed_lines/flow.changed_lines*100:0)}% of edits</title></rect>`;
    }
    svg+=`<text x="${x+width/2}" y="372" text-anchor="middle" font-size="11">${esc(points[i].period)}</text>`;
    if(!flow?.changed_lines)svg+=`<text x="${x+width/2}" y="${baseline-8}" text-anchor="middle" font-size="11">${flow?"0 edits":"Missing"}</text>`;
  });
  $("focus-chart").innerHTML=svg+"</svg>";wirePeriods("focus-chart",mark=>openPeriod(mark.dataset.period,mark.dataset.purpose));
  const added=flows.reduce((s,f)=>s+(f?.additions||0),0),deleted=flows.reduce((s,f)=>s+(f?.deletions||0),0);
  $("focus-summary").textContent=`In these windows: +${fmt(added)} / −${fmt(deleted)} physical text lines; net ${fmt(added-deleted)}. No-edit months have no percentage denominator. Purpose follows recorded classification, not a productivity inference.`;
}
let extensionOthers=[];
function renderExtensions(){
  const purpose=$("extension-purpose").value,metric=$("extension-metric").value;
  const sourceCounter=["cloc_code","cloc_comment","cloc_blank"].includes(metric);
  const values=points.map(point=>{
    const result={};
    for(const row of scopeView(point)?.extension_purpose||scopeView(point)?.extension_role||[]){
      const rowPurpose=row.purpose||purposeOf(row.role);
      const source=["product_implementation","product_verification"].includes(rowPurpose);
      if(purpose==="source"&&!source || !["all","source"].includes(purpose)&&rowPurpose!==purpose)continue;
      const value=sourceCounter?(source?row[metric]:0):metric==="nonblank"?nonblank(row):row.files;
      result[row.extension]=(result[row.extension]||0)+value;
    }return result;
  });
  const extensions=[...new Set(values.flatMap(v=>Object.keys(v)))].sort((a,b)=>values.reduce((s,v)=>s+(v[b]||0)-(v[a]||0),0));
  const top=extensions.slice(0,8),other=extensions.slice(8);
  extensionOthers=other;
  const palette=["#326bb4","#238477","#8055b5","#b54b88","#a68a25","#546c86","#cc6633","#4d6870","#87929f"];
  const series=[...top.map((extension,i)=>({label:extension,key:extension,color:palette[i],values:values.map(v=>v[extension]||0)}))];
  if(other.length)series.push({label:`Other extensions (${other.length})`,key:"__other",color:palette[8],values:values.map(v=>other.reduce((s,e)=>s+(v[e]||0),0))});
  legend("extension-legend",series);
  const max=nice(maximum(series.flatMap(s=>s.values)));let svg=svgStart(`Extension trends: ${metric}`,1180,400);
  for(let i=0;i<=5;i++){const y=325-i*52;svg+=`<line x1="90" y1="${y}" x2="1080" y2="${y}" stroke="#e0e6ee"/><text x="82" y="${y+4}" text-anchor="end" font-size="12">${fmt(max*i/5)}</text>`;}
  for(const s of series){
    svg+=`<path d="${s.values.map((v,i)=>`${i?"L":"M"}${xAt(i,points.length,90,970)},${325-v/max*260}`).join(" ")}" fill="none" stroke="${s.color}" stroke-width="2"/>`;
    s.values.forEach((v,i)=>svg+=`<g data-extension="${esc(s.key)}">${pointMark(xAt(i,points.length,90,970),325-v/max*260,s.color,points[i].period,v,s.label)}</g>`);
  }
  points.forEach((p,i)=>svg+=`<text x="${xAt(i,points.length,90,970)}" y="355" text-anchor="middle" font-size="11">${esc(p.period)}</text>`);
  const applicable=!sourceCounter || ["all","source","product_implementation","product_verification"].includes(purpose);
  $("extension-chart").innerHTML=applicable?
    svg+"</svg>"+(series.every(s=>s.values.every(v=>v===0))?"<p>No classified files in this purpose within the configured scope; this is not evidence that excluded artefacts are absent.</p>":""):
    "<p>N/A: source counter lines do not apply to this non-source purpose. Select nonblank text or file count.</p>";
  wirePeriods("extension-chart",mark=>{
    const extension=mark.parentElement.dataset.extension;
    openPeriod(mark.dataset.period,purpose==="source"||(sourceCounter&&purpose==="all")?"source":purpose==="all"?"":purpose,extension);
  });
}
function horizontalBars(id,title,rows,unit){
  const max=maximum(rows.map(r=>r.value)),height=60+rows.length*36;
  let svg=svgStart(title,760,height);
  rows.forEach((r,i)=>svg+=`<text x="8" y="${42+i*36}" font-size="12">${esc(r.label)}</text><rect x="235" y="${25+i*36}" width="${max?r.value/max*350:0}" height="23" fill="${r.color}" data-value="${r.value}"><title>${esc(r.label)}: ${fmt(r.value)} ${esc(unit)}</title></rect><text x="595" y="${42+i*36}" font-size="12">${fmt(r.value)}</text>`);
  $(id).innerHTML=svg+"</svg>";
}
function renderSnapshot(){
  const v=current(),roles=["production_source","test_source"],kinds=[["cloc_code","#326bb4"],["cloc_comment","#238477"],["cloc_blank","#bbc5d1"]];
  const selectedRoles=$("line-kind-role").value==="source"?roles:[$("line-kind-role").value];
  const max=maximum(roles.map(role=>kinds.reduce((s,[key])=>s+stock(v,role,key),0)));let svg=svgStart("Latest implementation and test source code/comment/blank line composition",760,170);
  roles.forEach((role,i)=>{
    let x=170;svg+=`<text x="8" y="${48+i*52}" font-size="13">${role==="production_source"?"Implementation":"Testing"}</text>`;
    for(const [key,color] of kinds){
      const value=stock(v,role,key),width=max?value/max*450:0;
      svg+=`<rect x="${x}" y="${30+i*52}" width="${width}" height="26" fill="${color}" data-kind="${key}" data-role="${role}" data-value="${value}"><title>${esc(role)} ${key}: ${fmt(value)}</title></rect>`;x+=width;
    }
    svg+=`<text x="${x+8}" y="${48+i*52}" font-size="12">${fmt(kinds.reduce((s,[key])=>s+stock(v,role,key),0))}</text>`;
  });
  if($("line-kind-mode").value==="history" && charts.history){
    const totals=points.map(p=>kinds.map(([key])=>selectedRoles.reduce((sum,role)=>sum+stock(scopeView(p),role,key),0)));
    const limit=nice(maximum(totals.map(values=>values.reduce((a,b)=>a+b,0))));
    svg=svgStart("Monthly source code/comment/blank line composition",760,360);
    for(let tick=0;tick<=5;tick++){const y=295-tick*48;svg+=`<line x1="65" y1="${y}" x2="745" y2="${y}" stroke="#e0e6ee"/><text x="58" y="${y+4}" text-anchor="end" font-size="10">${fmt(limit*tick/5)}</text>`;}
    totals.forEach((values,i)=>{
      const x=70+i*670/points.length,width=520/points.length;let y=295;
      values.forEach((value,k)=>{
        const height=value/limit*240;y-=height;
        svg+=`<rect class="interactive" tabindex="0" role="button" x="${x}" y="${y}" width="${width}" height="${height}" fill="${kinds[k][1]}" data-kind="${kinds[k][0]}" data-value="${value}" data-period="${esc(points[i].period)}"><title>${esc(points[i].period)} ${kinds[k][0]}: ${fmt(value)}</title></rect>`;
      });svg+=`<text x="${x+width/2}" y="320" text-anchor="middle" font-size="9">${esc(points[i].period)}</text>`;
    });
  }else if($("line-kind-mode").value==="latest" && selectedRoles.length===1){
    svg=svgStart("Latest selected source code/comment/blank line composition",760,160);
    const totals=kinds.map(([key])=>stock(v,selectedRoles[0],key)),total=totals.reduce((a,b)=>a+b,0);let x=20;
    totals.forEach((value,i)=>{
      const width=total?value/total*680:0;
      svg+=`<rect x="${x}" y="35" width="${width}" height="35" fill="${kinds[i][1]}" data-kind="${kinds[i][0]}" data-value="${value}"><title>${kinds[i][0]}: ${fmt(value)}</title></rect>`;x+=width;
    });svg+=`<text x="20" y="105" font-size="13">${fmt(total)} counted physical source lines</text>`;
  }
  $("line-kind-chart").innerHTML=svg+"</svg>";
  wirePeriods("line-kind-chart",mark=>openPeriod(mark.dataset.period));
  const files=roles.reduce((s,r)=>s+stock(v,r,"files"),0),counted=roles.reduce((s,r)=>s+stock(v,r,"cloc_files"),0);
  const selectedFiles=selectedRoles.reduce((s,r)=>s+stock(v,r,"files"),0),selectedCounted=selectedRoles.reduce((s,r)=>s+stock(v,r,"cloc_files"),0);
  $("line-kind-note").textContent=`Latest selected-source coverage: ${fmt(selectedCounted)} / ${fmt(selectedFiles)} files counted. Per-period counter coverage is retained in JSON; unsupported source is not included in observed LOC.`;
  $("card-files").textContent=fmt(v.union.files);$("card-text").textContent=fmt(v.union.physical_lines);
  const implementation=sumPurpose(v,"product_implementation"),testing=sumPurpose(v,"product_verification");
  const definition=sumPurpose(v,"product_definition")+sumPurpose(v,"product_definition_candidate");
  $("card-impl").textContent=fmt(implementation);$("card-tests").textContent=fmt(testing);$("card-definition").textContent=fmt(definition);
  const ratioPart=value=>value>0&&value<0.01?"<0.01":value.toLocaleString("en-US",{minimumFractionDigits:2,maximumFractionDigits:2});
  $("card-ratio").textContent=testing?`${ratioPart(implementation/testing)} : 1 : ${ratioPart(definition/testing)}`:"Undefined (no testing nonblank lines)";
  $("source-coverage").textContent=`${fmt(counted)} / ${fmt(files)} classified source files counted by cloc for code-only charts.`;
  $("census-coverage").textContent=`${fmt(v.union.cloc_files)} / ${fmt(v.union.text_files)} text files counted by cloc. ${fmt(v.union.binary_files)} binary candidates and ${fmt(v.union.symlinks)} symlinks have no line denominator. ${fmt(v.union.blank_lines)} physical blank lines; ${fmt(v.union.bytes)} bytes.`;
  const metric=$("purpose-weight").value;
  horizontalBars("purpose-chart","Latest purpose composition",Object.entries(labels).map(([key,label])=>({
    label,value:sumPurpose(v,key,metric),color:colors[key]
  })),metric==="files"?"files":"nonblank text lines");
}
const gapLabels={no_ownership_metadata:"No ownership metadata",no_primary_human_owner:"Indexed: no primary human owner",missing_alias_export:"Missing alias export"};
const gapColors={no_ownership_metadata:"#d5dbe3",no_primary_human_owner:"#d6a44c",missing_alias_export:"#ce7182"};
function ownerRows(){
  return (charts.ownership||[]).filter(row=>!$("chart-repo").value || row.aliases.includes($("chart-repo").value)).map(row=>({
    ...row,agentic_documentation:row.agentic_documentation||"not_measured",agentic_enablement:row.agentic_enablement||"not_measured"
  }));
}
function ownerColor(key){return `hsl(${parseInt(key.slice(0,6),16)%360},48%,43%)`;}
let ownerOtherCategories=[];
function renderOwners(){
  if(!charts.ownership){$("owner-chart").innerHTML="<p>Ownership evidence is not joined; no owners are inferred.</p>";return;}
  const dimension=$("chart-ownership-group").value,weight=$("chart-ownership-weight").value,rows=ownerRows(),rank={};
  for(const row of rows)if(row.primary_owner)rank[row.primary_owner.key]=(rank[row.primary_owner.key]||0)+(weight==="files"?1:row[weight]);
  const top=Object.keys(rank).sort((a,b)=>rank[b]-rank[a]||a.localeCompare(b)).slice(0,8);
  const known=new Map(rows.filter(r=>r.primary_owner).map(r=>[r.primary_owner.key,r.primary_owner.name]));
  const series=[...top.map(key=>({key,label:known.get(key),color:ownerColor(key)})),
    {key:"other",label:"Other known owners",color:"#8ba2b3"},
    ...Object.entries(gapLabels).map(([key,label])=>({key,label,color:gapColors[key]}))];
  legend("owner-legend",series);
  const groups={};
  for(const row of rows){
    const category=row[dimension],value=weight==="files"?1:row[weight];
    groups[category] ||= {total:0,parts:{}};const key=row.primary_owner?(top.includes(row.primary_owner.key)?row.primary_owner.key:"other"):row.status;
    groups[category].total+=value;groups[category].parts[key]=(groups[category].parts[key]||0)+value;
  }
  const categories=Object.keys(groups).sort((a,b)=>groups[b].total-groups[a].total||a.localeCompare(b));
  const shown=categories.slice(0,12),remaining=categories.slice(12);
  ownerOtherCategories=remaining;
  if(remaining.length){
    const other={total:0,parts:{}};
    for(const c of remaining){other.total+=groups[c].total;for(const [key,n] of Object.entries(groups[c].parts))other.parts[key]=(other.parts[key]||0)+n;}
    groups.__other=other;shown.push("__other");
  }
  let svg=svgStart("Latest primary ownership shares by purpose or extension, including coverage gaps",1180,70+shown.length*38);
  shown.forEach((category,i)=>{
    const group=groups[category],label=category==="__other"?`Other categories (${remaining.length})`:labels[category]||category;
    svg+=`<text x="8" y="${45+i*38}" font-size="12">${esc(label)}</text>`;
    let x=300;
    for(const s of series){
      const value=group.parts[s.key]||0,width=group.total?value/group.total*640:0;
      if(width)svg+=`<rect class="interactive" tabindex="0" role="button" x="${x}" y="${27+i*38}" width="${width}" height="25" fill="${s.color}" data-category="${esc(category)}" data-owner="${esc(s.key)}" data-value="${value}"><title>${esc(label)} · ${esc(s.label)}: ${fmt(value)} (${fmt(group.total?value/group.total*100:0)}%)</title></rect>`;
      x+=width;
    }
    svg+=`<text x="950" y="${45+i*38}" font-size="12">${group.total?fmt(group.total):"N/A — no source denominator"}</text>`;
  });
  $("owner-chart").innerHTML=svg+"</svg>";
  for(const mark of $("owner-chart").querySelectorAll("[data-category]")){
    const open=()=>{
      $("ownership-dimension").value=dimension;$("ownership-weight").value=weight;
      $("owner-details").open=true;renderOwnerDetail(mark.dataset.category);
      $("owner-details").scrollIntoView({block:"start"});
    };
    mark.addEventListener("click",open);mark.addEventListener("keydown",e=>{if(e.key==="Enter"){e.preventDefault();open();}});
  }
  const coverage={};for(const row of rows)coverage[row.status]=(coverage[row.status]||0)+1;
  $("owner-summary").textContent=`${fmt(rows.length)} included paths: ${fmt(coverage.owned||0)} primary-owned; ${fmt(coverage.no_primary_human_owner||0)} indexed without a primary human owner; ${fmt(coverage.no_ownership_metadata||0)} without metadata; ${fmt(coverage.missing_alias_export||0)} with missing alias exports.`;
  renderOwnerDetail();
}
function renderOwnerDetail(category=""){
  if(!$("ownership-table"))return;
  const dimension=$("ownership-dimension").value,rows=ownerRows(),groups={},totals={};
  for(const row of rows){
    const c=row[dimension],key=JSON.stringify([c,row.primary_owner?.key||row.status]);
    groups[key] ||= {category:c,name:row.primary_owner?.name||row.status,status:row.status,files:0,nonblank_text_lines:0,source_code_lines:0};
    totals[c] ||= {files:0,nonblank_text_lines:0,source_code_lines:0};
    for(const weight of ["files","nonblank_text_lines","source_code_lines"]){const n=weight==="files"?1:row[weight];groups[key][weight]+=n;totals[c][weight]+=n;}
  }
  $("ownership-table").querySelector("tbody").innerHTML=Object.values(groups).filter(g=>!category||(category==="__other"?ownerOtherCategories.includes(g.category):g.category===category)).sort((a,b)=>a.category.localeCompare(b.category)||b.files-a.files).map(g=>
    `<tr data-dimension="${dimension}"><td>${esc(g.category)}</td><td>${esc(g.name)}</td><td>${esc(g.status)}</td>`+
    ["files","nonblank_text_lines","source_code_lines"].map(weight=>`<td data-weight="${weight}">${fmt(g[weight])}</td><td data-weight="${weight}">${totals[g.category][weight]?fmt(g[weight]/totals[g.category][weight]*100)+"%":"N/A"}</td>`).join("")+"</tr>").join("");
  $("ownership-weight").dispatchEvent(new Event("change"));
}
let changeLimit=200;
function renderChanges(reset=true){
  if(reset)changeLimit=200;
  const point=points.find(p=>p.period===$("chart-period").value),view=point?scopeView(point):null,flow=view?.flow;
  const purpose=$("change-purpose").value,extension=$("change-extension").value;
  const rows=(flow?.files||[]).filter(row=>(!purpose||(purpose==="source"?["product_implementation","product_verification"].includes(row.purpose):row.purpose===purpose))&&
    (!extension||(extension==="__other"?extensionOthers.includes(row.extension):row.extension===extension))).sort((a,b)=>(b.additions+b.deletions)-(a.additions+a.deletions));
  $("change-summary").textContent=flow?`${point.period}: +${fmt(flow.additions)} / −${fmt(flow.deletions)}; net ${fmt(flow.net)} physical lines. ${fmt(rows.length)} matching changes; displaying ${Math.min(changeLimit,rows.length)}. Cutoff ${point.cutoff}; commits ${JSON.stringify(point.commits)}.`:"No measured change history is attached.";
  $("change-rows").innerHTML=rows.slice(0,changeLimit).map(row=>`<tr><td>${esc(row.path)}</td><td>${esc(labels[row.purpose]||row.purpose)}</td><td>${esc(row.extension)}</td><td>${fmt(row.additions)}</td><td>${fmt(row.deletions)}</td><td>${esc(row.change_type||"")}</td><td><code title="${esc(row.commit)}">${esc(row.commit?.slice(0,12)||"")}</code></td></tr>`).join("");
  $("more-changes").hidden=rows.length<=changeLimit;
}
function renderAll(){
  const alias=$("chart-repo").value;
  $("chart-scope-note").textContent=(alias?alias:"Workspace union")+" · current ownership, frozen-scope historical composition";
  $("history-method").textContent=charts.history?
    `${points.length} measured monthly snapshots: ${points[0].period} → ${points.at(-1).period}. UTC cutoffs and first-parent Git history; frozen configured scopes/exclusions and classifier. Full method and immutable evidence remain in the saved JSON.`:
    "Snapshot charts only; historical evidence has not been collected.";
  renderTimeline();renderAgentic();renderFocus();renderExtensions();renderSnapshot();renderOwners();renderChanges();
}
$("chart-repo").addEventListener("change",renderAll);
$("focus-mode").addEventListener("change",renderFocus);
for(const id of ["agentic-lens","agentic-metric"])$(id).addEventListener("change",renderAgentic);
for(const id of ["extension-purpose","extension-metric"])$(id).addEventListener("change",renderExtensions);
$("purpose-weight").addEventListener("change",renderSnapshot);
for(const id of ["line-kind-mode","line-kind-role"])$(id).addEventListener("change",renderSnapshot);
for(const id of ["chart-ownership-group","chart-ownership-weight"])$(id).addEventListener("change",renderOwners);
if($("ownership-dimension"))$("ownership-dimension").addEventListener("change",()=>renderOwnerDetail());
for(const id of ["chart-period","change-purpose","change-extension"])$(id).addEventListener("change",()=>renderChanges());
$("more-changes").addEventListener("click",()=>{changeLimit+=200;renderChanges(false);});
$("timeline-section").after($("snapshot-summary"));
renderAll();
"""
