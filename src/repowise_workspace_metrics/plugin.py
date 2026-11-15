"""Repowise plugin registration; no registration-time collection or writes."""

from __future__ import annotations


def register() -> None:
    from repowise.core.registry import PLUGIN_API_VERSION, register_lazy_command
    from repowise.core.report.render import report_renderers

    if PLUGIN_API_VERSION != 1:
        raise RuntimeError(f"workspace-metrics supports plugin API 1; found {PLUGIN_API_VERSION}")
    from .render import render_html
    from .ownership import collect_section
    from repowise.core.report.model import Section, report_sections

    register_lazy_command("workspace-metrics", "repowise_workspace_metrics.cli:workspace_metrics")
    report_renderers.register("metrics-html", render_html)
    report_sections.register("workspace-ownership", Section(
        title="Workspace metrics ownership evidence",
        summary="Existing Repowise primary human owners and top-five human commit shares per indexed code file.",
        collect=collect_section,
    ))
