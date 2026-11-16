"""Chart-first presentation of measured census, history and ownership evidence."""

from __future__ import annotations

import base64
import hashlib
import html
from typing import Any

from .census import counter_warning, validate
from .ownership import validate_ownership
from .purposes import source_file
from .artefacts import validate_artefacts
from . import charts

_SCRIPT = """
const role = document.getElementById("role-filter");
const extension = document.getElementById("extension-filter");
function filterRows() {
  let shown = 0;
  for (const row of document.querySelectorAll("#cross-tab tbody tr")) {
    row.hidden = (role.value && row.dataset.role !== role.value) ||
      (extension.value && row.dataset.extension !== extension.value);
    if (!row.hidden) shown++;
  }
  document.getElementById("filter-count").textContent = shown + " combinations shown";
}
role.addEventListener("change", filterRows);
extension.addEventListener("change", filterRows);
filterRows();
const ownershipDimension = document.getElementById("ownership-dimension");
const ownershipWeight = document.getElementById("ownership-weight");
if (ownershipDimension) {
  function filterOwnership() {
    for (const row of document.querySelectorAll("#ownership-table tbody tr")) {
      row.hidden = row.dataset.dimension !== ownershipDimension.value;
    }
    for (const cell of document.querySelectorAll("#ownership-table [data-weight]")) {
      cell.hidden = cell.dataset.weight !== ownershipWeight.value;
    }
  }
  ownershipDimension.addEventListener("change", filterOwnership);
  ownershipWeight.addEventListener("change", filterOwnership);
  filterOwnership();
}
"""
_SCRIPT += charts.SCRIPT


def table(headers: list[str], rows: list[list[Any]]) -> str:
    return (
        "<div class='scroll'><table><thead><tr>"
        + "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
        + "</tr></thead><tbody>"
        + "".join("<tr>" + "".join(f"<td>{html.escape(str(c))}</td>" for c in row) + "</tr>" for row in rows)
        + "</tbody></table></div>"
    )


def render_html(document: dict[str, Any]) -> str:
    if "files" not in document:
        raise ValueError("metrics-html requires a census; use `repowise workspace-metrics report --input`")
    validate(document)
    validate_ownership(document)
    validate_artefacts(document)
    union = document["union"]
    number = lambda n: f"{n:,}"
    source = document.get("source_stock", {}).get("implementation", document["by_role"].get("production_source", {"cloc_code": 0}))["cloc_code"]
    tests = document.get("source_stock", {}).get("testing", document["by_role"].get("test_source", {"cloc_code": 0}))["cloc_code"]
    ratio = f"{source / tests:.2f} : 1" if tests else "Undefined (no counted test LOC)"
    source_rows = [r for r in document["files"] if source_file(r)]
    counted_sources = sum(r["cloc"] is not None for r in source_rows)
    alias_rows = [
        [a["alias"], ", ".join(a["analysis_paths"]) or "(entire tree)", a["head"][:12],
         a["git_tree_entries"] - a["outside_analysis_paths"], a["configured_exclusions"],
         a["scope_files"], number(a["composition"]["physical_lines"])]
        for a in document["aliases"]
    ]
    role_rows = [
        [role, number(t["files"]), number(t["bytes"]), number(t["physical_lines"]),
         number(t["blank_lines"]), number(t["cloc_files"]), number(t["cloc_code"]),
         number(t["cloc_comment"]), number(t["cloc_blank"])]
        for role, t in document["by_role"].items()
    ]
    bars = []
    physical = union["physical_lines"]
    for role, totals in document["by_role"].items():
        width = totals["physical_lines"] / physical * 100 if physical else 0
        bars.append(
            f"<div class='bar-row'><span>{html.escape(role)}</span>"
            f"<div class='track'><div class='bar' style='width:{width:.4f}%'></div></div>"
            f"<strong>{number(totals['physical_lines'])}</strong></div>"
        )
    combinations = []
    for row in document["extension_role"]:
        combinations.append(
            f"<tr data-role='{html.escape(row['role'], quote=True)}' "
            f"data-extension='{html.escape(row['extension'], quote=True)}'>"
            + "".join(
                f"<td>{html.escape(str(cell))}</td>" for cell in (
                    row["extension"], row["role"], number(row["files"]), number(row["bytes"]),
                    number(row["physical_lines"]), number(row["cloc_files"]), number(row["cloc_code"]),
                    number(row["cloc_comment"]), number(row["cloc_blank"]),
                )
            ) + "</tr>"
        )
    def options(values: list[str]) -> str:
        return "<option value=''>All</option>" + "".join(
            f"<option value='{html.escape(v, quote=True)}'>{html.escape(v)}</option>" for v in values
        )
    digest = base64.b64encode(hashlib.sha256(_SCRIPT.encode()).digest()).decode()
    warning = html.escape(document.get("cloc_stderr", ""))
    coverage_warning = counter_warning(document)
    counter_notice = (
        f'<p class="notice" id="counter-warning" role="alert">{html.escape(coverage_warning)} '
        '<a href="#coverage-details">Counter omission evidence</a></p>'
        if coverage_warning else ""
    )
    omitted_rows = [
        [row["path"], row["counter_status"], number(row["physical_lines"]), number(row["blank_lines"])]
        for row in document["files"]
        if row["mode"] != "120000" and not row["binary"] and row["cloc"] is None
    ]
    chart_sections = charts.sections(document)
    ownership_section = ""
    ownership_notice = "Ownership, agent provenance and observed harness loading are unmeasured."
    if "ownership" in document:
        ownership_notice = "Primary ownership is joined from Repowise exports; coverage gaps are shown. Declared accountability, provenance and observed harness loading remain unmeasured."
        ownership = document["ownership"]
        if "mapped_display_names" in ownership.get("identity_label_modes", []):
            ownership_notice += " This report includes Repowise-mapped person names; restrict sharing to the authorized audience."
        owner_rows = []
        weights = ("files", "nonblank_text_lines", "source_code_lines")
        for dimension in ("purpose", "role", "extension"):
            groups = ownership[f"by_{dimension}"]
            for group in groups:
                cells = ""
                for weight in weights:
                    total = sum(item[weight] for item in groups if item[dimension] == group[dimension])
                    share = f"{group[weight] / total * 100:.1f}%" if total else "N/A"
                    cells += f"<td data-weight='{weight}'>{number(group[weight])}</td><td data-weight='{weight}'>{share}</td>"
                owner_rows.append(
                    f"<tr data-dimension='{dimension}'><td>{html.escape(group[dimension])}</td>"
                    f"<td>{html.escape(group['owner_name'] or group['status'])}</td>"
                    f"<td>{html.escape(group['status'])}</td>{cells}</tr>"
                )
        coverage = ownership["coverage"]
        head_cells = "".join(
            f"<th data-weight='{weight}'>{label}</th><th data-weight='{weight}'>Share of category</th>"
            for weight, label in zip(weights, ("Files", "Nonblank text in associated files", "Counted source LOC in associated files"))
        )
        ownership_section = f"""<section><h2>Repowise primary human ownership</h2>
<p>Coverage: {coverage['owned']} primary-owned paths; {coverage['no_primary_human_owner']} indexed paths without a primary human owner;
{coverage['no_ownership_metadata']} paths without ownership metadata; {coverage['missing_alias_export']} paths with missing alias exports.</p>
<label>Group by <select id="ownership-dimension"><option value="purpose">Purpose (recorded evidence)</option><option value="role">Role</option><option value="extension">Extension</option><option value="agentic_documentation">Agentic documentation status</option><option value="agentic_enablement">Agentic enablement status</option></select></label>
<label>Weight <select id="ownership-weight"><option value="files">File count</option><option value="nonblank_text_lines">Nonblank text exposure</option><option value="source_code_lines">Counted source LOC exposure</option></select></label>
<div class="scroll"><table id="ownership-table"><thead><tr><th>Category</th><th>Owner or gap</th><th>Status</th>{head_cells}</tr></thead><tbody>{''.join(owner_rows)}</tbody></table></div>
<p>Shares include uncovered paths in each category denominator. Text/LOC weighting means volume in primary-owned files, not lines authored by that person.
Source LOC applies only to production/test source with counter evidence; other purposes have no source-LOC denominator.
Purpose labels are provisional role mappings, not content-reviewed intent. Declared accountability is not collected.</p>
<p>Same-snapshot exports are required; overlapping aliases must agree. Contributor top-five human commit shares and their residual are retained per file in JSON; these are not blame line shares or exact author churn.
Empty contributor evidence is unknown, not human. Excluded files are still outside this census population.</p></section>"""
        precision = ownership.get("contributor_share_precision", 0)
        if precision:
            ownership_section = ownership_section.replace(
                "</section>",
                f"<p>API contributor shares are rounded to {precision:g}. They are retained unchanged, not normalized to 100%. "
                "Residual is approximate; rounding uncertainty is half that precision per retained contributor. "
                "Primary-owner assignment and file/text/LOC weights are unaffected.</p></section>",
            )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'sha256-{digest}'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>Repowise workspace metrics — chart-first report</title>
<style>
body{{font:15px/1.5 system-ui,sans-serif;background:#f3f6fa;color:#18283d;margin:0}}
main{{max-width:1200px;margin:auto;padding:24px;overflow-wrap:anywhere}}section{{background:white;border:1px solid #dce3ed;border-radius:10px;padding:20px;margin:18px 0}}
h1{{font-size:28px}}h2{{font-size:20px}}.notice{{background:#fff4d6;padding:14px}}.cards{{display:flex;flex-wrap:wrap;gap:28px}}.cards strong{{font-size:24px;display:block}}
table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{padding:8px;text-align:right;border-bottom:1px solid #e0e6ee}}td:first-child,th:first-child{{text-align:left}}th{{background:#edf2f8}}
.scroll{{overflow:auto}}select{{padding:7px;margin:8px}}.bar-row{{display:grid;grid-template-columns:230px 1fr 110px;align-items:center;gap:12px;font-size:13px;margin:9px 0}}.track{{background:#edf2f8;height:14px}}.bar{{height:100%;background:#326bb4}}.muted{{color:#53647b}}pre{{white-space:pre-wrap}}
@media(max-width:650px){{main{{padding:12px}}.bar-row{{grid-template-columns:1fr 90px}}.track{{grid-column:1/-1;grid-row:auto}}}}@media print{{section{{break-inside:avoid}}}}
{charts.STYLE}
</style></head><body><main>
<h1>Repowise workspace metrics</h1>
<p class="muted">Chart-first report · census captured {html.escape(document['generated_at'])}</p>
<p class="notice">Real census evidence only. {ownership_notice} No synthetic timelines or owner identities are included.</p>
{counter_notice}
<section id="snapshot-summary"><div class="cards">
<div><strong id="card-files">{number(union['files'])}</strong>Included paths</div>
<div><strong id="card-text">{number(union['physical_lines'])}</strong>Physical text lines</div>
<div><strong id="card-impl">{number(source)}</strong>Counted implementation code</div>
<div><strong id="card-tests">{number(tests)}</strong>Counted test-source code</div>
<div><strong id="card-ratio">{ratio}</strong>Observed implementation/testing ratio</div>
</div><p id="source-coverage">{counted_sources}/{len(source_rows)} classified source files counted. Ratio excludes fixtures; unsupported and role-unknown files may contain more source. This is not test coverage.</p>
<p id="census-coverage">{union['cloc_files']}/{union['text_files']} text files counted by cloc. {union['binary_files']} binary candidates and {union['symlinks']} symlinks have no text-line denominator.
Physical blank lines: {number(union['blank_lines'])}. Bytes: {number(union['bytes'])}. Overlapping alias paths: {document['overlapping_alias_paths']}.</p></section>
{chart_sections}
<details class="evidence" id="scope-details"><summary>Scope and full-workspace inventory evidence</summary>
<section><h2>Configured membership and scope reconciliation</h2>
<p>Workspace: {html.escape(document['workspace'])}<br>Membership SHA-256: {html.escape(document['membership_sha256'])}</p>
{table(['Alias','analysis_paths','Commit','Candidates','Excluded','Included','Physical lines'], alias_rows)}
<p>Population = committed paths under analysis_paths minus explicit exclusions, not parser-indexed nodes. Shared repository snapshots are deduplicated by repository + commit + path + blob. Same-content files at different paths count separately.</p></section></details>
<details class="evidence" id="role-details"><summary>Full-workspace role and line-count evidence</summary>
<section><h2>Physical text volume by role</h2>{''.join(bars)}
<p>These are physical lines, not programming LOC. Large EDA test fixtures must not inflate implementation LOC. Roles are heuristic; see per-file rules/confidence in the JSON.</p>
{table(['Role','Paths','Bytes','Physical lines','Physical blank','Counted files','Native code','Comments','Native blank'],role_rows)}
<p>Native “code” in fixture/data/configuration/documentation rows is a counter label, not source LOC.</p></section></details>
<details class="evidence" id="extension-details"><summary>Full-workspace extension × role evidence</summary>
<section><h2>Extension × role</h2>
<label>Role <select id="role-filter">{options(list(document['by_role']))}</select></label>
<label>Extension <select id="extension-filter">{options(list(document['by_extension']))}</select></label>
<span id="filter-count" aria-live="polite"></span>
<div class="scroll"><table id="cross-tab"><thead><tr><th>Extension</th><th>Role</th><th>Paths</th><th>Bytes</th><th>Physical lines</th><th>Counted files</th><th>Native code</th><th>Comments</th><th>Native blank</th></tr></thead><tbody>{''.join(combinations)}</tbody></table></div></section></details>
<details class="evidence" id="owner-details"><summary>Selected-scope ownership evidence</summary>
{ownership_section}
 </details>
<details class="evidence" id="coverage-details"><summary>Coverage and interpretation evidence</summary>
<section><h2>Coverage and interpretation limits</h2>
{table(['Counter omission reason','Text files'],[[key,number(n)] for key,n in document['cloc_omission_reasons'].items()])}
{table(['Uncounted path','Counter status','Physical lines','Physical blank'], omitted_rows)}
<p>unreported_cloc_omission means cloc returned neither counts nor an omission reason.
The code/comment split is unmeasured, not zero; independently measured physical and blank lines are retained.</p>
<p>Cloc {html.escape(document['method']['cloc_version'])}; content deduplication disabled. Five-second filter timeout. Mixed code/comment lines are not separately measured.
Physical lines use LF-delimited bytes and count an unterminated final line. Binary detection uses NUL in the first 8,192 bytes; symlink targets are not followed. Vendored/generated classification is not complete. Playbook filenames require purpose review.</p>
<p>Working changes and untracked files are excluded. Explicit exclusions may omit documentation and product/agent artefacts. Census collection and saved-evidence rendering do not open Repowise indexes. Ownership exports use Repowise's report lifecycle or public Contributors API; they do not rebuild indexes.</p>
<pre>{warning}</pre></section></details></main><script>{_SCRIPT}</script></body></html>
"""
