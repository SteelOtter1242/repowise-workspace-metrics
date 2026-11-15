"""Versioned purpose evidence; discovery never establishes agent use."""

from __future__ import annotations

import fnmatch
import re
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
from typing import Any

VERSION = "purposes-v3"
SUPPORTED_VERSIONS = frozenset({"purposes-v2", VERSION})
LEGACY_PURPOSES = {
    "production_source": "product_implementation", "test_source": "product_verification",
    "test_fixture": "fixtures_data", "configuration_build_ci": "configuration_operations",
    "data_schema_design": "fixtures_data", "documentation": "general_documentation",
    "agent_guidance": "development_enablement_candidate",
    "product_intent_spec": "product_definition_candidate",
    "generated_vendor": "generated_vendor", "asset_binary": "binary_asset", "unknown": "unknown",
}
PURPOSE_IDS = frozenset({
    "product_implementation", "product_verification", "product_definition",
    "development_enablement", "general_documentation", "runtime_configuration_operations",
    "data_fixtures_design", "assets", "mixed_unknown",
})
FAMILIES = {
    "documentation": ("vision", "intent", "feature", "spec", "plan", "tasks"),
    "enablement": ("agent", "skill", "prompt"),
}
STATUSES = ("candidate", "confirmed", "rejected", "unknown")
FIXTURE_PARTS = {"fixtures", "fixture", "testcases", "pilot_data", "expected", "golden", "templates"}


def discovery(path: str, *, classifier_version: str = VERSION) -> dict[str, Any]:
    p = PurePosixPath(path)
    name = p.name.lower()
    words = set(re.sub(r"([a-z])([A-Z])", r"\1_\2", p.stem).lower().replace("-", "_").split("_"))
    matches = {
        family: [f"*{term}*.md" for term in terms if fnmatch.fnmatchcase(name, f"*{term}*.md")]
        for family, terms in FAMILIES.items()
    }
    kind = None
    if name in {"agents.md", "agent.md", "claude.md", "copilot-instructions.md"} or name.endswith(".instructions.md"):
        kind = "harness_instructions"
    elif name in {"skill.md", "skills.md"}:
        kind = "agent_skill"
    elif name.endswith(".agent.md"):
        kind = "agent_profile"
    elif name.endswith(".prompt.md"):
        kind = "agent_prompt"
    elif name.endswith(".md") and "playbook" in name:
        kind = "workflow_playbook_candidate"
    elif name.endswith(".md") and words & {
        "vision", "intent", "feature", "features", "spec", "specification",
        "plan", "plans", "tasks", "requirements",
    }:
        kind = "product_or_work_intent_candidate"
    harness_config = (
        name in {"settings.json", "settings.local.json"} and ".claude" in p.parts
        or p.suffix.lower() == ".mdc" and ".cursor" in p.parts and "rules" in p.parts
    )
    if harness_config:
        kind = "harness_configuration"
    skill_support = any(
        p.parts[i] in {".agents", ".claude", ".github", ".copilot"}
        and p.parts[i + 1] == "skills" and len(p.parts[i + 2:]) >= 2
        for i in range(len(p.parts) - 2)
    )
    if classifier_version == VERSION and skill_support and kind in {
        None, "product_or_work_intent_candidate", "workflow_playbook_candidate",
    }:
        kind = "agent_skill_support"
    signals = {}
    for family, rules in matches.items():
        status = "candidate" if rules else "unknown"
        reason = "basename_glob_only" if rules else "no_discovery_evidence"
        if family == "enablement" and kind in {
            "harness_instructions", "agent_skill", "agent_profile", "agent_prompt", "harness_configuration", "agent_skill_support",
        }:
            status, reason = "confirmed", (
                "recognized_skill_support_path_not_observed_use" if kind == "agent_skill_support"
                else "recognized_instruction_format_not_observed_use"
            )
        elif rules and not words & set(FAMILIES[family]) and kind is None:
            status, reason = "rejected", "substring_without_purpose_keyword"
        signals[family] = {"status": status, "matches": rules, "reason": reason}
    return {"kind": kind, "signals": signals}


def build_kind(path: str, *, discovery_only: bool = False) -> str | None:
    p = PurePosixPath(path)
    name = p.name.lower()
    parts = {part.lower() for part in p.parts[:-1]}
    if not discovery_only and parts & (FIXTURE_PARTS | {"vendor", "third_party", "third-party", "node_modules"}):
        return None
    if (
        name in {".gitlab.yml", ".gitlab-ci.yml", "jenkinsfile"}
        or p.suffix.lower() in {".yml", ".yaml"} and (
            ".github/workflows/" in path.lower() or ".gitlab/ci/" in path.lower()
        )
    ):
        return "build_ci"
    if (
        name in {"makefile", "gnumakefile", "cmakelists.txt", "build.sh", "build.py"}
        or name.startswith(("makefile.", "makefile_"))
    ):
        return "build_ci"
    if name in {"package.json", "pyproject.toml", "setup.py", "setup.cfg", "tox.ini", "cargo.toml"}:
        return "packaging_tooling"
    return None


def validate_overrides(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise ValueError("metrics_purpose_overrides must be a list")
    allowed = {"pattern", "purpose", "reason", "documentation", "enablement"}
    for override in value:
        if not isinstance(override, dict) or set(override) - allowed:
            raise ValueError("Invalid purpose override fields")
        if any(not isinstance(override.get(key), str) or not override[key].strip() for key in ("pattern", "purpose", "reason")):
            raise ValueError("Purpose overrides require nonempty pattern, purpose and reason")
        if override["purpose"] not in PURPOSE_IDS:
            raise ValueError("Unknown override purpose")
        for family, expected in (("documentation", "product_definition"), ("enablement", "development_enablement")):
            if family in override and (
                override[family] not in STATUSES
                or override[family] == "confirmed" and override["purpose"] != expected
            ):
                raise ValueError("Agentic override status disagrees with primary purpose")
    return value


def evidence(path: str, role: str, mode: str = "100644", overrides: Sequence[dict[str, str]] = (), *, classifier_version: str = VERSION) -> dict[str, Any]:
    if classifier_version not in SUPPORTED_VERSIONS:
        raise ValueError(f"Unsupported purpose classifier: {classifier_version}")
    found = discovery(path, classifier_version=classifier_version)
    signals = found["signals"]
    purpose = {
        "product_definition_candidate": "product_definition",
        "development_enablement_candidate": "development_enablement",
        "configuration_operations": "runtime_configuration_operations",
        "fixtures_data": "data_fixtures_design", "binary_asset": "assets",
        "generated_vendor": "mixed_unknown", "unknown": "mixed_unknown",
    }.get(LEGACY_PURPOSES[role], LEGACY_PURPOSES[role])
    rule, confidence = "role_fallback", "medium"
    kind = build_kind(path)
    template = "templates" in {part.lower() for part in PurePosixPath(path).parts[:-1]}
    if mode == "120000" or role in {"asset_binary", "test_fixture", "generated_vendor"} or template:
        for signal in signals.values():
            if signal["status"] == "confirmed":
                signal.update(status="candidate", reason="instruction_format_in_non_instruction_population")
    elif found["kind"] == "agent_skill_support":
        purpose, rule, kind = "development_enablement", "agent_skill_support_path", "agent_skill_support"
    elif kind:
        purpose, rule = "development_enablement", "build_or_packaging_convention"
    elif found["kind"] in {"harness_instructions", "agent_skill", "agent_profile", "agent_prompt", "harness_configuration"}:
        purpose, rule = "development_enablement", "instruction_format"
        kind = "agent_guidance"
    elif found["kind"] == "product_or_work_intent_candidate":
        if signals["enablement"]["status"] == "candidate":
            purpose, rule, confidence = "mixed_unknown", "conflicting_agent_and_work_intent", "low"
        else:
            purpose, rule, confidence = "product_definition", "work_intent_filename_candidate", "low"
    elif any(signal["status"] == "candidate" for signal in signals.values()):
        purpose, rule, confidence = "mixed_unknown", "agentic_subject_requires_review", "low"
    matching = [override for override in overrides if fnmatch.fnmatchcase(path, override["pattern"])]
    if len(matching) > 1:
        raise ValueError(f"Multiple purpose overrides match {path}")
    if matching:
        override = matching[0]
        purpose, rule, confidence = override["purpose"], "repository_override", "high"
        for family in FAMILIES:
            if family in override:
                signals[family].update(status=override[family], reason=override["reason"])
            elif classifier_version == VERSION and signals[family]["status"] == "confirmed" and purpose != {
                "documentation": "product_definition", "enablement": "development_enablement",
            }[family]:
                signals[family].update(status="rejected", reason=override["reason"])
    if purpose in {"product_implementation", "product_verification"} and role not in {"production_source", "test_source"}:
        raise ValueError(f"Source purpose requires a recognized source role: {path}")
    return {
        "purpose": purpose, "purpose_rule": rule, "purpose_confidence": confidence,
        "purpose_override_reason": matching[0]["reason"] if matching else None,
        "agentic": signals, "enablement_kind": kind if purpose == "development_enablement" else None,
        "harness_load_status": "unknown",
    }


def purpose_of(row: Mapping[str, Any]) -> str:
    return row["purpose"] if "purpose" in row else LEGACY_PURPOSES[row["role"]]


def source_file(row: Mapping[str, Any]) -> bool:
    return row["role"] in {"production_source", "test_source"} and purpose_of(row) in {
        "product_implementation", "product_verification",
    }


def summarize(rows: list[dict[str, Any]], *, measured: bool = False) -> dict[str, Any]:
    from .census import aggregate

    result: dict[str, Any] = {
        "source_stock": {
            key: aggregate([row for row in rows if source_file(row) and purpose_of(row) == purpose])
            for key, purpose in (("implementation", "product_implementation"), ("testing", "product_verification"))
        },
        "by_purpose": {key: aggregate([row for row in rows if purpose_of(row) == key]) for key in sorted({purpose_of(row) for row in rows})},
        "extension_purpose": [
            {"extension": ext, "purpose": purpose, **aggregate([
                row for row in rows if row["extension"] == ext and purpose_of(row) == purpose
            ])}
            for ext, purpose in sorted({(row["extension"], purpose_of(row)) for row in rows})
        ],
    }
    if (rows or measured) and all("agentic" in row for row in rows):
        result["agentic"] = {
            family: {status: aggregate([row for row in rows if row["agentic"][family]["status"] == status]) for status in STATUSES}
            for family in FAMILIES
        }
        result["agentic"]["non_agentic_enablement"] = aggregate([
            row for row in rows if purpose_of(row) == "development_enablement"
            and row.get("enablement_kind") in {"build_ci", "packaging_tooling"}
            and row["agentic"]["enablement"]["status"] != "confirmed"
        ])
    return result
