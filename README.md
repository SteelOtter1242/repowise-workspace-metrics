# repowise-workspace-metrics

Chart-first workspace composition, historical changes and ownership reporting.
Version 0.3 adds measured monthly history and interactive charts ahead of collapsed
evidence lists. It retains the 0.2 ownership integration and local public-API export.
Version 0.3.1 adds an explicit local-report option for Repowise's mapped person names.
Version 0.3.2 shows the five requested volume series without a ratio overlay.
Version 0.4.0 adds versioned purpose evidence, explicit agentic stock/flow,
repository-wide artefact discovery, and one-action persistent report generation.
Version 0.4.1 classifies executable agent-skill support as enablement.
Version 0.4.2 preserves unexplained cloc omissions as explicit unmeasured gaps,
with prominent report and CLI warnings instead of guessed counts.
Version 0.4.3 opens counter-gap evidence directly without fragment navigation,
so the control also works inside an opaque-origin sandboxed native viewer.

## Beta integration and VM installation

The scope prerequisite was originally merged in [beta PR #3](https://github.com/SteelOtter1242/repowise-ifx/pull/3).
Beta later diverged from that merge; [repair PR #4](https://github.com/SteelOtter1242/repowise-ifx/pull/4)
restored the helper. The verified beta installation below is pinned to repaired
merge `86caf6a53b4c39aba5579be4229b4cb442c6038f`, not a moving branch name.

The required interface is `repowise.core.repo_config.analysis_paths`.
Package version 0.61.0 or working command help alone does not establish that it
exists. The prerequisite is read-only and does not change indexing/traversal
or rebuild indexes.

For a managed beta checkout, update it through your normal deployment procedure,
then install it and the metrics wheel into the **same** environment. The
prerequisite patch is no longer needed on current beta:

```sh
uv pip install --python /path/to/beta-env/bin/python /path/to/repowise-ifx
uv pip install --python /path/to/beta-env/bin/python \
  /path/to/repowise_workspace_metrics-0.4.3-py3-none-any.whl
/path/to/beta-env/bin/repowise workspace-metrics --help
```

If beta already includes the prerequisite, do not apply it again. Git and
`cloc` must be installed on the VM. The locally verified counter is cloc 1.98.
The extension does not replace the fork with upstream Repowise.

### Workshop installers

Either installer can be downloaded and run by itself; it fetches the pinned
workspace-metrics source into `REPOWISE_HOME`, so no extension-repository
checkout is needed. The first install uses the verified beta snapshot above.
Re-running reuses the checkouts and environment; pass `--update` / `-Update`
only when you want to fast-forward Repowise to the latest `beta`. The update is
rejected before changing Repowise source if that checkout has local changes.
After an update, the script verifies Repowise's version and required helpers;
it stops with an error if the new beta is incompatible. Existing indexes are
not deleted.

On Linux or macOS:

```sh
export REPOWISE_HOME="$HOME/.local/share/repowise-workshop"
bash scripts/install-workspace-metrics.sh

# Later, only when you want the latest beta:
bash scripts/install-workspace-metrics.sh --update
```

On Windows, from PowerShell without WSL:

```powershell
$env:REPOWISE_HOME = Join-Path $env:LOCALAPPDATA 'RepowiseWorkshop'
.\scripts\install-workspace-metrics.ps1

# Later, only when you want the latest beta:
.\scripts\install-workspace-metrics.ps1 -Update
```

`REPOWISE_HOME` is optional. Its defaults are
`$HOME/.local/share/repowise-workshop` on Unix and
`%LOCALAPPDATA%\RepowiseWorkshop` on Windows. Git, `uv`, and network access to
GitHub and the Python package index are required; `uv` installs Python 3.11.
`cloc` is required when collecting metrics. If PowerShell's execution policy
blocks the script, invoke it for this process with
`powershell -NoProfile -ExecutionPolicy Bypass -File .\scripts\install-workspace-metrics.ps1`
rather than changing the machine-wide policy.

### One-action persistent report

```sh
repowise workspace-metrics generate /path/to/agentic-workspace --workspace \
  --months 12 --include-artefacts \
  --evidence-out agentic-metrics.json --out agentic-metrics.html
```

The workspace must contain `.repowise-workspace.yaml`. All outputs must be new
files. Use `--months 0` for a snapshot-only initial pilot; measured history needs
complete local, non-shallow reachable history and at least two periods.
No history is fetched or fabricated.

For a single indexed repository without workspace membership, create a separate
report-workspace directory and place a `.repowise-workspace.yaml` there:

```json
{"version":1,"default_repo":"pilot","repos":[{"alias":"pilot","path":"/absolute/path/to/existing/repository"}]}
```

JSON syntax is valid YAML and avoids indentation damage when pasted.
This declares metrics membership only; it does not initialize/rebuild an index.
The repository's own `.repowise/config.yaml` still defines its analysis paths and
exclusions. An indexed repository alone is not proof of complete Git history.

Optionally add `--api-url http://127.0.0.1:9932` to reuse an existing, healthy
local Contributors interface at the same repository snapshots. Add
`--include-names` only for explicitly authorized sharing; emails are never
exported. Without this opt-in, ownership remains unmeasured.

JSON and HTML persist independently of a temporary report server. `generate`
is the extension's report-generation action; a native Repowise web Metrics
entry point and remote-VM acceptance are still pending. No GitHub/GitLab
publication is implied by a local installation or commit.

### Isolated VM pilot from GitHub

Do not install into a running production environment as the first test.
The environment below is separate; an existing GUI/server need not be stopped.
Use a new environment path and install Python 3.11+ and cloc first if absent.

```sh
uv venv --python 3.13 /path/to/new/metrics-env
uv pip install --python /path/to/new/metrics-env/bin/python \
  'repowise @ git+https://github.com/SteelOtter1242/repowise-ifx.git@86caf6a53b4c39aba5579be4229b4cb442c6038f' \
  'https://github.com/SteelOtter1242/repowise-workspace-metrics/releases/download/v0.4.3/repowise_workspace_metrics-0.4.3-py3-none-any.whl'
/path/to/new/metrics-env/bin/repowise workspace-metrics generate \
  /path/to/report-workspace --workspace --months 0 --include-artefacts \
  --evidence-out /path/to/new/pilot.json --out /path/to/new/pilot.html
```

Keep the verified full commit pin unless validating a newer beta revision;
do not silently substitute upstream Repowise. Start with a snapshot,
then generate new output filenames with `--months 12` after confirming complete
history. Ownership is optional and requires an authorized loopback interface
reachable from the same execution environment.

Reports are sensitive even though this extension is public. Do not upload pilot
JSON/HTML to GitHub issues or releases. Serve them only through an authenticated
internal report location, or download them privately for inspection.

### Updating an existing VM deployment

Use the actual Python executable of the environment that runs Repowise, not an
unrelated shell interpreter:

```sh
uv pip install --python /path/to/running-env/bin/python --upgrade \
  'repowise @ git+https://github.com/SteelOtter1242/repowise-ifx.git@86caf6a53b4c39aba5579be4229b4cb442c6038f' \
  'https://github.com/SteelOtter1242/repowise-workspace-metrics/releases/download/v0.4.3/repowise_workspace_metrics-0.4.3-py3-none-any.whl'
/path/to/running-env/bin/repowise workspace-metrics --help
```

For a `uv tool` installation, upgrade the tool from the fork and include the
extension in that same tool environment:

```sh
uv tool install --upgrade \
  --with 'https://github.com/SteelOtter1242/repowise-workspace-metrics/releases/download/v0.4.3/repowise_workspace_metrics-0.4.3-py3-none-any.whl' \
  'repowise @ git+https://github.com/SteelOtter1242/repowise-ifx.git@86caf6a53b4c39aba5579be4229b4cb442c6038f'
repowise workspace-metrics --help
```

Schedule the update and restart through the deployment's existing service
manager. Do not start a second server or guess its service name. Existing
processes do not pick up installed Python changes until restarted. Confirm
health and plugin command discovery afterward; the version string alone is not
sufficient because these beta commits all report Repowise 0.61.0.

### Purpose and agentic contract

New collection freezes `roles-v2` and `purposes-v3`. Primary purposes are product
implementation, product verification, product definition, general documentation,
development enablement, runtime configuration/operations, data/fixtures/design,
assets and mixed/unknown.

Agentic documentation uses the requested vision/intent/feature/spec/plan/tasks
Markdown basename globs; agentic enablement uses agent/skill/prompt globs and
recognized instruction formats/configuration. Every match keeps its rule and
candidate/confirmed/rejected/unknown status. Spectre, planning-tool names and
Cadence SKILL ambiguities are not proof of agent instructions or product intent.
Confirmed instruction formats/declarations establish presence, not loading,
authorship, effort or productivity.

Repository build/CI/packaging tooling is enablement, not implementation.
Build-like fixtures/templates remain discovery candidates without becoming
developer tooling by filename alone. Native source operands exclude tooling
overrides even when their extension is programming. Enablement text-volume
counts include build/CI source and comments, not just prose.

Repository overrides live in `.repowise/config.yaml`, with a required reason:

```yaml
metrics_purpose_overrides:
  - pattern: docs/plans/tooling-plan.md
    purpose: development_enablement
    reason: Reviewed repository tooling plan, not product behavior
  - pattern: docs/specs/login.md
    purpose: product_definition
    documentation: confirmed
    reason: Declared agent-oriented product work contract
```

Patterns match repository-relative paths. Multiple matching overrides, unknown
purposes/statuses and contradictory confirmed signals fail explicitly.
Overlapping aliases must agree. Configuration hashes and override declarations
are frozen for historical reconstruction.

The opt-in repository inventory keeps included, explicitly excluded and
outside-module paths distinct, deduplicates shared snapshots, and never widens
the baseline or index. It currently measures **latest artefact stock only**;
its historical trends and native source LOC are unmeasured. Ownership is reused
only where the baseline export already supplies that exact path/snapshot.
Its unresolved/rejected matches remain available in collapsed evidence.

Baseline agentic stock and physical edit flows are measured historically under
the frozen classifier. Older saved evidence still renders without invented
agentic zeroes; recollect before requesting new-classifier history.
Untracked files and harness-private artefacts are outside the committed lens.
Skill support beneath `.agents/skills/<skill>/`, `.claude/skills/<skill>/`,
`.github/skills/<skill>/` and `.copilot/skills/<skill>/` is agentic development
enablement, including executable helpers and their tests. Fixture, vendor,
binary, symlink and template exceptions still apply; a generic `src/skills/`
directory is not enough. Repository overrides remain authoritative. Presence
does not imply loading or agent authorship. Older `purposes-v2` reports retain
their saved classifications; a fresh collection is required for v3 history.
Tracked/local `.repowiseIgnore` and in-scope Git submodules currently fail with
explicit scope-support errors rather than being silently ignored.
Purpose labels remain provisional role mappings; no synthetic timelines or owners.

## Run without installing

Run in the Repowise 0.61.0 Python environment, with Git and cloc 1.98 available.
PyYAML, pathspec and Click are declared dependencies. From this package directory:

```bash
export PYTHONPATH="$PWD/src"
export REPOWISE_PLUGINS=repowise_workspace_metrics.plugin:register
repowise plugins
repowise workspace-metrics collect /path/to/workspace --workspace --out census.json
repowise workspace-metrics report --input census.json --format metrics-html --out census.html
```

The collect path must contain the configured `.repowise-workspace.yaml`. Membership
comes only from that file; individual members' `analysis_paths` and explicit
exclusions define composition scopes. Parent output directories must exist.
Output files must be new; no implicit overwriting. Reports can also emit `--format json`.

For an editable install into Repowise's own environment, use
`uv pip install --python /path/to/repowise/python -e .`, then `repowise plugins`.
This does not change Repowise source.

## Chart-first historical report

```bash
repowise workspace-metrics history --input census-with-ownership.json \
  --months 12 --out census-with-history.json
repowise workspace-metrics report --input census-with-history.json --out charts.html
```

The report opens on charts, not lists:

1. Five **unstacked** volume curves: separate implementation and testing source
   LOC on the left axis; documentation, product definition and development
   enablement nonblank text on the purple right axis. Latest values are shown in
   the legend. The implementation/testing ratio has its own separate plotting
   area and a dotted orange curve to distinguish its axis unit; it is not a
   volume curve. Hiding a series does not change axis ranges.
2. Monthly purpose change focus: shares, absolute additions+deletions, or net text.
3. Extension trends filtered by purpose, with source code/comment/blank, nonblank text or file count.
4. Latest and historical code/comment/blank composition for implementation and tests.
5. Latest purpose composition with file/text weighting.
6. Latest primary ownership splits by purpose, role or extension, including gaps.

The scope selector supports the workspace union and every configured member.
Chart points/categories open measured period or ownership drill-downs; detailed
lists stay collapsed until requested. Top-extension and top-owner groupings retain
an exact remainder rather than dropping the tail. A census without history still
shows snapshot charts but does not invent a historical line or edit series.

History uses immutable local Git objects under frozen census scopes, not working
files or database discovery. It records UTC month cutoffs, a first-parent/mainline
merge policy, the current partial month, coverage and per-change commit evidence.
It neither fetches history nor rebuilds indexes. Incomplete history must be exposed
or rejected, never filled with fabricated zero edits. Reclassification/pure-renames
must not be interpreted as new source; generated refreshes and formatting detection
remain unmeasured. Historical ownership is not extrapolated from today's owners.
Definition/enablement zeroes mean no **classified candidates inside these scopes**,
not absence of excluded documentation or proof about harness loading.

## Integration and compatibility

- Plugin API 1: lazy CLI group `workspace-metrics`, renderer `metrics-html`,
  and opt-in assessment section `workspace-ownership`.
- Registration does no collection or storage writes.
- The renderer consumes the census schema; no dashboard page is registered.
  Use the command above, not
  `repowise report assessment --format metrics-html`: an ordinary assessment has
  no census and will fail explicitly.
- The prototype isolates scope/test helpers and existing server owner-data/core
  ownership helpers behind a strict Repowise 0.61.0 compatibility guard.
  It fails on other versions rather than applying different scope semantics.
  A stable extension-facing scope/test read contract is the next core contribution.
- The report implements the preview's chart shape with measured evidence.
  One snapshot alone still cannot support trend lines. The ownership section runs inside Repowise's
  report lifecycle, reusing the owner fold behind its Contributors view; the
  extension never selects a database path or implements identity resolution.

## Ownership: reuse the existing Repowise view

Repowise's Contributors/Owners view already uses `owner_data`, `person_owner`
and normalized per-file human commit shares. The section delegates to those
same functions, retaining the top-five contributors and residual share. Bots
and coding agents do not become human knowledge owners.

In an **authorized indexed workspace**, export the existing evidence explicitly:

```bash
REPOWISE_WORKSPACE_METRICS_OWNERSHIP=1 \
  repowise report assessment /path/to/authorized/workspace --workspace \
  --sections workspace-ownership --format json --out ownership-evidence.json
repowise workspace-metrics ownership --input census.json \
  --evidence ownership-evidence.json --out census-with-ownership.json
repowise workspace-metrics report --input census-with-ownership.json --out ownership.html
```

The environment opt-in prevents ordinary assessments from suddenly exporting
person-level identity information. A default assessment does not run this
section's ownership reads. To preserve the assessment pack's existing
no-person-names contract, the section uses stable SHA-256 fingerprints of
Repowise's normalized keys and opaque owner labels, not names/emails. It does not
invent a second identity model. These linkable identities remain sensitive
repository information: only share them under the same access policy as the existing view.
Do not use this feature to access content-excluded databases; obtain permitted
evidence from an authorized Repowise environment.

The saved-evidence join/report opens no index. It requires matching alias+commit,
rejects inconsistent overlapping views and retains uncovered paths. It never
sums shared aliases twice. Files without metadata, indexed files without a
primary human owner and missing alias exports are distinct.

Alternatively, with the authorized local Repowise service running, export through
the **existing public Contributors API**, without direct database-file access:

```bash
repowise workspace-metrics ownership-api --input census.json \
  --api-url http://127.0.0.1:9932 --out ownership-evidence.json
```

Then use the same saved-evidence ownership/report commands above. This command
requires a healthy 0.61.0 API, matches each configured member by repository path
and commit, and checks that repository metadata stayed unchanged during export.
It permits only a loopback HTTP origin, rejects redirects, and uses the existing
`REPOWISE_API_KEY` environment variable if authentication is configured.
By default, only opaque identity fingerprints are saved, not returned names/emails.
This is an authorized service interface, not a workaround for a denied resource:
if its access is restricted, stop rather than using another route.

For an authorized **named local report**, use `--include-names`:

```bash
repowise workspace-metrics ownership-api --input census-with-history.json \
  --include-names --out named-ownership-evidence.json
repowise workspace-metrics ownership --input census-with-history.json \
  --evidence named-ownership-evidence.json --out named-census-with-history.json
repowise workspace-metrics report --input named-census-with-history.json --out named-charts.html
```

Names come directly from the existing Contributors API's resolved display-name
field: `OwnerAccumulator.name`, populated by Repowise's `IdentityResolver.display_name`.
The extension does not infer names from emails or build a second mapping. Stable
fingerprint keys, contributor shares and history remain unchanged. Email fields
are never exported. An empty mapped name fails explicitly rather than inventing one.
Ordinary assessment exports remain opaque; this flag affects only the explicit
public-API export. Named evidence and HTML contain personal information and must
stay under the same access policy as the Contributors view. Keep the default
pseudonymized report for audiences not authorized to view names.

API contributor shares are rounded to 0.001 by Repowise. The export retains them
unchanged, including rounded zeroes and totals slightly above one. Their residual
is an approximate nonnegative remainder; uncertainty is ±0.0005 per retained
contributor. Validation permits only that rounding budget, not arbitrary errors.
Primary-owner assignments and census exposure weights are unaffected.

The HTML can group by role, provisional role-derived purpose, or extension and
weight by file count, nonblank text or counted production/test LOC. Volume in
primary-owned files is **not authored LOC**, and source-LOC shares have no
denominator for documentation/fixtures. Contributors' commit shares are retained
in JSON, not converted into blame. Declared team accountability is not collected.

## Evidence and failure contract

Collection reads Git blobs at each captured commit, not working changes or
untracked files. It records dirty state without incorporating it. It never opens
Repowise indexes, runs init/update, installs dependencies or calls a network
provider. Only the requested output and automatically cleaned temporary source
staging are written; no storage migrations or pipeline hooks are registered.

Shared repository+commit+path+blob entries count once across aliases. Equal blobs
at different paths count separately (`cloc --skip-uniqueness`). Per-alias views
still report their scopes, candidates, excluded paths and composition. Physical
text lines are independent of cloc support; binary candidates and symlinks have
no line denominator. Symbolic links are never followed for source collection.

The role/extension cross-tab, omission reasons, rule/confidence and immutable
identities are retained per file. Binary detection is the NUL-in-first-8192-bytes
heuristic, not encoding-aware certification. Cloc filter timeout is five seconds;
process timeout is 300 seconds, with diagnostics exposed. Mixed code/comment
lines are unavailable. Invalid counter totals fail rather than publishing success.

If cloc successfully exits but returns neither counts nor an omission reason
(for example, cloc 1.98 with a non-PHP/non-Pascal `.inc` file), the file remains
in the population with `cloc: null` and `counter_status: unreported_cloc_omission`.
Its physical/blank lines remain independently measured; its code/comment split
is **unmeasured**, never inferred or converted into measured zeros. CLI output
and an expanded notice above the charts warn about incomplete counter coverage,
including historical periods. Collapsed evidence lists affected latest paths,
statuses and physical lines. Code/comment totals and source ratios describe only
the counted subset. Counter subprocess failures, malformed output and
counter/physical-line discrepancies still fail explicitly.

Role rules are provisional. Fixture data is not source LOC. A playbook filename
is role-unknown pending purpose review, and Cadence SKILL source is not agent
guidance. Unknown files may contain source; the implementation/testing ratio is
an observed-counted ratio, not coverage or effectiveness. Ownership is absent
until joined from an authorized export; trends require the explicit history command.
Provenance, declared accountability and actual harness loading remain unmeasured.

Tracked or materialized `.repowiseIgnore` files cause an explicit failure until
their semantics are supported. Newline-containing paths and in-scope submodules
also fail explicitly. Collection checks for configuration/HEAD changes during
measurement. Multiple captured commits remain distinct snapshots; their volume
must not be interpreted as a single version of the source.

## Verify

```bash
PYTHONPATH=src /path/to/repowise/python -m unittest discover -s tests
uv build
```

Tests exercise committed versus dirty content, scope pruning, overlapping aliases,
duplicate blobs at different paths, fixtures, symlinks, unsupported/empty text,
counter reconciliation, invalid evidence, and offline report rendering.
Ownership tests exercise real Repowise normalization/human filtering, explicit
opt-in, truncated contributor residuals, snapshot mismatch, alias disagreement,
deduplication, API snapshot fences/rounding/privacy and unchanged fixture/source ratios.
