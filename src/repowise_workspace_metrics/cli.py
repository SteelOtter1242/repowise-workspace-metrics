"""Explicit collection and read-only rendering commands."""

from __future__ import annotations

import json
import subprocess
from urllib.error import URLError
from pathlib import Path

import click
import yaml

from .census import collect, counter_warning, validate
from .render import render_html
from .ownership import collect_api, enrich, validate_ownership
from .artefacts import collect_artefacts, validate_artefacts


def write_output(path: Path, content: str) -> None:
    path = path.resolve()
    if any(part in {".repowise", ".repowise-workspace", ".git"} for part in path.parts) or path.name in {".repowise-workspace.yaml", ".repowiseIgnore"}:
        raise ValueError("Metrics output cannot target Repowise configuration/index storage")
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(content)


@click.group("workspace-metrics")
def workspace_metrics() -> None:
    """Committed-file census and offline reports; no index rebuilds."""


@workspace_metrics.command("collect")
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path), default=".")
@click.option("--workspace", is_flag=True, required=True, help="Use this directory's configured Repowise membership.")
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), required=True, help="New census JSON file; existing files are never overwritten.")
def collect_command(path: Path, workspace: bool, out: Path) -> None:
    """Read the recorded commit, not dirty or untracked working files."""
    try:
        if out.exists() or out.is_symlink():
            raise FileExistsError(f"Output already exists: {out}")
        document = collect(path)
        write_output(out, json.dumps(document, indent=2) + "\n")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, yaml.YAMLError, subprocess.TimeoutExpired) as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Wrote census: {out}; {document['union']['files']} unique paths", err=True)
    if warning := counter_warning(document):
        click.echo(f"Warning: {warning}", err=True)
    if document["cloc_stderr"]:
        click.echo(f"cloc diagnostics: {document['cloc_stderr']}", err=True)


@workspace_metrics.command("report")
@click.option("--input", "input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--format", "fmt", type=click.Choice(["metrics-html", "json"]), default="metrics-html", show_default=True)
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), required=True, help="New report file.")
def report_command(input_path: Path, fmt: str, out: Path) -> None:
    """Render saved census evidence only; no collection, network or index reads."""
    try:
        document = json.loads(input_path.read_text(encoding="utf-8"))
        validate(document)
        validate_ownership(document)
        validate_artefacts(document)
        if "history" in document:
            from .history import validate_history

            validate_history(document)
        content = render_html(document) if fmt == "metrics-html" else json.dumps(document, indent=2) + "\n"
        write_output(out, content)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise click.ClickException(f"Cannot render census: {exc}") from exc
    click.echo(f"Wrote {fmt} report: {out}", err=True)
    if warning := counter_warning(document):
        click.echo(f"Warning: {warning}", err=True)


@workspace_metrics.command("ownership")
@click.option("--input", "input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--evidence", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True, help="Authorized Repowise workspace-ownership assessment export.")
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), required=True)
def ownership_command(input_path: Path, evidence: Path, out: Path) -> None:
    """Join existing Repowise ownership to saved census roles, purposes and extensions."""
    try:
        document = enrich(
            json.loads(input_path.read_text(encoding="utf-8")),
            json.loads(evidence.read_text(encoding="utf-8")),
        )
        write_output(out, json.dumps(document, indent=2) + "\n")
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise click.ClickException(f"Cannot join ownership: {exc}") from exc
    coverage = document["ownership"]["coverage"]
    click.echo(f"Wrote ownership census: {out}; coverage {coverage}", err=True)


@workspace_metrics.command("ownership-api")
@click.option("--input", "input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--api-url", default="http://127.0.0.1:9932", show_default=True, help="Authorized local Repowise API origin.")
@click.option("--include-names", is_flag=True, help="Include Repowise's mapped person names in this authorized local report; never emails.")
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), required=True, help="New ownership evidence JSON; opaque labels by default.")
def ownership_api_command(input_path: Path, api_url: str, include_names: bool, out: Path) -> None:
    """Export existing Contributors evidence over loopback; no direct index access."""
    try:
        if out.exists() or out.is_symlink():
            raise FileExistsError(f"Output already exists: {out}")
        evidence = collect_api(json.loads(input_path.read_text(encoding="utf-8")), api_url, include_names=include_names)
        write_output(out, json.dumps(evidence, indent=2) + "\n")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, URLError) as exc:
        raise click.ClickException(f"Cannot export API ownership: {exc}") from exc
    click.echo(f"Wrote ownership evidence: {out}; {len(evidence['sections'])} aliases", err=True)
    if include_names:
        click.echo("Mapped person names are included; keep this evidence and its report under the Contributors view's access policy.", err=True)


@workspace_metrics.command("history")
@click.option("--input", "input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--months", type=click.IntRange(min=2), default=12, show_default=True)
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), required=True, help="New census with measured historical evidence.")
def history_command(input_path: Path, months: int, out: Path) -> None:
    """Recount immutable monthly Git snapshots under the census's frozen scopes."""
    from .history import collect_history, validate_history

    try:
        if out.exists() or out.is_symlink():
            raise FileExistsError(f"Output already exists: {out}")
        document = json.loads(input_path.read_text(encoding="utf-8"))
        document["history"] = collect_history(document, months)
        validate_history(document)
        document["unmeasured"] = [key for key in document.get("unmeasured", []) if key != "history_trends"]
        write_output(out, json.dumps(document, indent=2) + "\n")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, yaml.YAMLError, subprocess.TimeoutExpired) as exc:
        raise click.ClickException(f"Cannot measure history: {exc}") from exc
    click.echo(f"Wrote historical census: {out}; {len(document['history']['points'])} periods", err=True)
    if warning := counter_warning(document):
        click.echo(f"Warning: {warning}", err=True)


@workspace_metrics.command("artefacts")
@click.option("--input", "input_path", type=click.Path(exists=True, dir_okay=False, path_type=Path), required=True)
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), required=True)
def artefacts_command(input_path: Path, out: Path) -> None:
    """Add an opt-in whole-repository inventory, without widening the baseline."""
    try:
        if out.exists() or out.is_symlink():
            raise FileExistsError(f"Output already exists: {out}")
        document = collect_artefacts(json.loads(input_path.read_text(encoding="utf-8")))
        write_output(out, json.dumps(document, indent=2) + "\n")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        raise click.ClickException(f"Cannot inventory artefacts: {exc}") from exc
    click.echo(f"Wrote supplementary inventory: {out}; {len(document['artefacts']['files'])} unique paths", err=True)


@workspace_metrics.command("generate")
@click.argument("path", type=click.Path(exists=True, file_okay=False, path_type=Path), default=".")
@click.option("--workspace", is_flag=True, required=True)
@click.option("--months", type=click.IntRange(min=0), default=12, show_default=True, help="Measured monthly history; 0 for snapshot only, otherwise at least 2.")
@click.option("--include-artefacts", is_flag=True, help="Include separately scoped repository-wide artefacts.")
@click.option("--api-url", help="Opt in to existing ownership from an authorized loopback Repowise instance.")
@click.option("--include-names", is_flag=True, help="Requires --api-url; mapped names, never emails.")
@click.option("--evidence-out", type=click.Path(dir_okay=False, path_type=Path), required=True)
@click.option("--out", type=click.Path(dir_okay=False, path_type=Path), required=True)
def generate_command(path: Path, workspace: bool, months: int, include_artefacts: bool, api_url: str | None, include_names: bool, evidence_out: Path, out: Path) -> None:
    """Generate persistent JSON/HTML through the same extension report interface."""
    try:
        if months == 1:
            raise ValueError("Use --months 0 for snapshot only, or at least 2 for history")
        if include_names and not api_url:
            raise ValueError("--include-names requires explicit --api-url ownership access")
        if evidence_out.resolve() == out.resolve():
            raise ValueError("JSON evidence and HTML output must have distinct paths")
        for target in (evidence_out, out):
            if target.exists() or target.is_symlink():
                raise FileExistsError(f"Output already exists: {target}")
        document = collect(path)
        if months:
            from .history import collect_history, validate_history

            document["history"] = collect_history(document, months)
            validate_history(document)
            document["unmeasured"] = [key for key in document["unmeasured"] if key != "history_trends"]
        if api_url:
            document = enrich(document, collect_api(document, api_url, include_names=include_names))
        if include_artefacts:
            document = collect_artefacts(document)
        content = render_html(document)
        write_output(evidence_out, json.dumps(document, indent=2) + "\n")
        write_output(out, content)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, URLError, yaml.YAMLError, subprocess.TimeoutExpired) as exc:
        raise click.ClickException(f"Cannot generate metrics report: {exc}") from exc
    click.echo(f"Wrote metrics report: {out}; saved evidence: {evidence_out}", err=True)
    if warning := counter_warning(document):
        click.echo(f"Warning: {warning}", err=True)
