"""Read configured scopes and immutable Git blobs, never Repowise databases."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import tempfile
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import pathspec
import yaml

from . import __version__
from .compat import helpers
from .purposes import VERSION as PURPOSE_VERSION, SUPPORTED_VERSIONS
from .purposes import FAMILIES, PURPOSE_IDS, STATUSES, build_kind, discovery, evidence, summarize, validate_overrides

SOURCE = frozenset(
    ".py .pm .pl .plx .t .tcl .il .ils .rb .js .jsx .ts .tsx .c .cc .cpp .cxx .h "
    ".hpp .hh .hxx .java .cs .go .rs .sh .bash .ksh .csh .v .sv .vhd .vhdl .sql .i".split()
)
DATA = frozenset(
    ".def .lef .lib .db .json .xml .csv .tsv .rpt .log .dat .data .sdf .spef "
    ".sdc .f .lst .list".split()
)
CONFIG = frozenset(".yml .yaml .toml .ini .cfg .conf .config .properties .cmake .mk".split())
DOC = frozenset(".md .rst .tex .pod .lyx .html .htm .dox".split())
CLASSIFIER_VERSION = "roles-v2"


def run(args: list[str], cwd: Path, *, timeout: int = 120) -> bytes:
    env = {**os.environ, "GIT_NO_LAZY_FETCH": "1", "GIT_NO_REPLACE_OBJECTS": "1"}
    result = subprocess.run(args, cwd=cwd, env=env, capture_output=True, timeout=timeout, check=False)
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        raise RuntimeError(f"{args[0]} failed ({result.returncode}): {detail}")
    return result.stdout


def read_mapping(path: Path) -> tuple[bytes, dict[str, Any]]:
    raw = path.read_bytes()
    value = yaml.safe_load(raw)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: expected a YAML mapping")
    return raw, value


def scope_reason(path: str, roots: tuple[str, ...], patterns: pathspec.PathSpec) -> str | None:
    if roots and not any(path == root or path.startswith(root + "/") for root in roots):
        return "outside_analysis_paths"
    parts = path.split("/")
    ancestors = ["/".join(parts[:i]) + "/" for i in range(1, len(parts))]
    if any(patterns.match_file(parent) for parent in ancestors) or patterns.match_file(path):
        return "configured_exclusion"
    return None


def line_counts(data: bytes) -> tuple[int, int]:
    lines = data.split(b"\n")
    if lines[-1] == b"":
        lines.pop()
    return len(lines), sum(not line.strip() for line in lines)


def artifact_kind(path: str) -> str | None:
    return discovery(path)["kind"]


def classify(path: str, mode: str, data: bytes, is_test: Callable[[str], bool]) -> tuple[str, str, str]:
    p = PurePosixPath(path)
    name, ext = p.name.lower(), p.suffix.lower()
    parts = {part.lower() for part in p.parts[:-1]}
    artifact = artifact_kind(path)
    if mode == "120000":
        return "unknown", "git_symlink_no_follow", "high"
    if b"\0" in data[:8192]:
        return "asset_binary", "nul_in_first_8192_bytes", "medium"
    if parts & {"vendor", "third_party", "third-party", "node_modules"}:
        return "generated_vendor", "vendor_directory", "medium"
    if parts & {"fixtures", "fixture", "testcases", "pilot_data", "expected", "golden"}:
        return "test_fixture", "fixture_directory", "medium"
    if "templates" in parts and (artifact or name in {"makefile", "cmakelists.txt", "build.sh", "build.py"}):
        return "unknown", "template_purpose_requires_review", "low"
    if artifact in {"harness_instructions", "agent_skill", "agent_profile", "agent_prompt", "harness_configuration"}:
        return "agent_guidance", "agent_artifact_filename", "medium"
    if artifact == "product_or_work_intent_candidate":
        return "product_intent_spec", "intent_document_filename", "low"
    if artifact == "workflow_playbook_candidate":
        return "unknown", "playbook_purpose_requires_review", "low"
    if build_kind(path):
        return "configuration_build_ci", "build_or_packaging_convention", "medium"
    if ext in DOC or name.startswith(("readme", "license", "changelog")):
        return "documentation", "document_extension_or_filename", "medium"
    if ext in CONFIG or name in {"makefile", "gnumakefile", "cmakelists.txt", "jenkinsfile"} or name.startswith(("makefile.", "makefile_")):
        return "configuration_build_ci", "configuration_extension_or_filename", "medium"
    source = ext in SOURCE or (
        not ext and data.startswith(b"#!") and any(
            token in data.split(b"\n", 1)[0] for token in (b"perl", b"python", b"sh", b"ruby", b"tcl")
        )
    )
    if source:
        return (
            ("test_source", "repowise_test_path_and_source_type", "medium") if is_test(path)
            else ("production_source", "source_extension_or_shebang", "medium")
        )
    if is_test(path):
        return "test_fixture", "repowise_test_path_non_source", "low"
    if ext in DATA:
        return "data_schema_design", "data_extension", "low"
    return "unknown", "no_role_rule", "low"


def aggregate(rows: list[dict[str, Any]]) -> dict[str, int]:
    totals = dict.fromkeys((
        "files", "bytes", "text_files", "binary_files", "symlinks", "physical_lines",
        "blank_lines", "cloc_files", "cloc_code", "cloc_comment", "cloc_blank",
    ), 0)
    totals["files"] = len(rows)
    for row in rows:
        totals["bytes"] += row["bytes"]
        if row["mode"] == "120000":
            totals["symlinks"] += 1
        elif row["binary"]:
            totals["binary_files"] += 1
        else:
            totals["text_files"] += 1
            totals["physical_lines"] += row["physical_lines"]
            totals["blank_lines"] += row["blank_lines"]
        if row["cloc"] is not None:
            totals["cloc_files"] += 1
            for key in ("code", "comment", "blank"):
                totals[f"cloc_{key}"] += row["cloc"][key]
    return totals


def count_staged(rows: list[dict[str, Any]], stage: Path, scratch: Path) -> str:
    staged = {row["stage_path"]: row for row in rows if "stage_path" in row}
    omissions: dict[str, str] = {}
    stderr = ""
    if staged:
        listing, ignored = scratch / "files.txt", scratch / "ignored.json"
        listing.write_text("".join(path + "\n" for path in staged), encoding="utf-8")
        result = subprocess.run(
            ["cloc", f"--list-file={listing}", "--by-file", "--json", "--quiet",
             "--skip-uniqueness", "--timeout=5", f"--ignored={ignored}"],
            cwd=stage, capture_output=True, text=True, timeout=300, check=False,
        )
        if result.returncode:
            raise RuntimeError(f"cloc failed ({result.returncode}): {result.stderr.strip()}")
        stderr = result.stderr
        counted = json.loads(result.stdout)
        for path, counts in counted.items():
            if path not in {"header", "SUM"}:
                staged[path.removeprefix("./")]["cloc"] = counts
        if ignored.exists():
            omissions = {
                item["file"].removeprefix("./"): item["reason"].strip()
                for item in json.loads(ignored.read_text(encoding="utf-8"))
                if item  # cloc 1.98 emits [{}] when no files were omitted.
            }
    for row in rows:
        if row["mode"] == "120000":
            row["counter_status"] = "symlink_not_followed"
        elif row["binary"]:
            row["counter_status"] = "binary_not_counted"
        elif row["cloc"] is not None:
            row["counter_status"] = "counted"
            if sum(row["cloc"][key] for key in ("code", "comment", "blank")) != row["physical_lines"]:
                raise ValueError(f"Counter/physical line discrepancy: {row['path']}")
        else:
            path = row["stage_path"]
            if path not in omissions:
                raise ValueError(f"cloc omitted a file without an explanation: {row['path']}")
            row["counter_status"] = omissions[path]
        row.pop("stage_path", None)
    return stderr


def collect(workspace: Path) -> dict[str, Any]:
    analysis_paths, is_test = helpers()
    workspace = workspace.resolve()
    membership_file = workspace / ".repowise-workspace.yaml"
    membership_raw, membership = read_mapping(membership_file)
    if membership.get("version") != 1:
        raise ValueError("Only Repowise workspace configuration version 1 is supported")
    members = membership.get("repos")
    if not isinstance(members, list) or not members:
        raise ValueError("Workspace must declare a nonempty repos list")
    aliases: list[dict[str, Any]] = []
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    seen_aliases: set[str] = set()
    with tempfile.TemporaryDirectory(prefix=".repowise-metrics-", dir=workspace) as temp:
        scratch = Path(temp)
        stage = scratch / "source"
        stage.mkdir()
        cloc_version = run(["cloc", "--version"], workspace).decode().strip()
        for member in members:
            if not isinstance(member, dict) or not isinstance(member.get("alias"), str) or not member["alias"]:
                raise ValueError("Each workspace repo must declare a nonempty alias")
            alias = member["alias"]
            if alias in seen_aliases:
                raise ValueError(f"Duplicate workspace alias: {alias}")
            seen_aliases.add(alias)
            if not isinstance(member.get("path"), str) or not member["path"]:
                raise ValueError(f"{alias}: expected a repository path")
            repo = (workspace / member["path"]).resolve()
            config_file = repo / ".repowise/config.yaml"
            config_raw, config = read_mapping(config_file) if config_file.exists() else (b"", {})
            roots = analysis_paths(config)
            exclusions = config.get("exclude_patterns", [])
            if not isinstance(exclusions, list) or any(not isinstance(p, str) for p in exclusions):
                raise ValueError(f"{alias}: exclude_patterns must be a list of strings")
            patterns = pathspec.PathSpec.from_lines("gitwildmatch", exclusions)
            purpose_overrides = validate_overrides(config.get("metrics_purpose_overrides", []))
            identity = run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], repo).decode().strip()
            head = run(["git", "rev-parse", "HEAD"], repo).decode().strip()
            tree = run(["git", "rev-parse", f"{head}^{{tree}}"], repo).decode().strip()
            records = run(["git", "ls-tree", "-rz", "--full-tree", head], repo)
            entries = []
            for record in records.split(b"\0"):
                if record:
                    meta, raw_path = record.split(b"\t", 1)
                    mode, kind, oid = meta.decode().split()
                    path = raw_path.decode("utf-8")
                    if any(char in path for char in "\n\r"):
                        raise ValueError(f"{alias}: cloc list-file cannot represent newline paths")
                    if PurePosixPath(path).name == ".repowiseIgnore":
                        raise ValueError(f"{alias}: tracked .repowiseIgnore requires scope support")
                    entries.append((path, mode, kind, oid))
            if any(repo.rglob(".repowiseIgnore")):
                raise ValueError(f"{alias}: working-tree .repowiseIgnore requires scope support")
            scoped, excluded = [], []
            outside = 0
            for path, mode, kind, oid in entries:
                reason = scope_reason(path, roots, patterns)
                if reason == "outside_analysis_paths":
                    outside += 1
                elif reason:
                    excluded.append(path)
                else:
                    if kind != "blob":
                        raise ValueError(f"{alias}: in-scope {kind} {path} is unsupported")
                    scoped.append((path, mode, oid))
            for path, mode, oid in scoped:
                key = identity, head, path, oid
                if key in unique:
                    candidate = evidence(path, unique[key]["role"], mode, purpose_overrides)
                    if candidate != {field: unique[key][field] for field in candidate}:
                        raise ValueError(f"Overlapping aliases disagree on purpose evidence: {path}")
                    unique[key]["aliases"].append(alias)
                    continue
                data = run(["git", "cat-file", "blob", oid], repo)
                role, rule, confidence = classify(path, mode, data, is_test)
                binary = b"\0" in data[:8192]
                text = mode != "120000" and not binary
                physical, blank = line_counts(data) if text else (None, None)
                row = {
                    "repository": identity, "head": head, "path": path, "blob": oid,
                    "mode": mode, "aliases": [alias], "bytes": len(data), "binary": binary,
                    "physical_lines": physical, "blank_lines": blank,
                    "extension": PurePosixPath(path).suffix.lower() or "[none]",
                    "role": role, "classification_rule": rule, "confidence": confidence,
                    "artifact_kind": artifact_kind(path),
                    "harness_load_status": "not_observed" if artifact_kind(path) else None,
                    "cloc": None,
                    "origin_traits": ["vendored"] if role == "generated_vendor" else [],
                    **evidence(path, role, mode, purpose_overrides),
                }
                unique[key] = row
                if text:
                    target = stage / str(len(unique)) / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    row["stage_path"] = target.relative_to(stage).as_posix()
            aliases.append({
                "alias": alias, "path": str(repo), "repository": identity, "head": head, "tree": tree,
                "config_sha256": hashlib.sha256(config_raw).hexdigest(),
                "analysis_paths": list(roots), "exclude_patterns": list(dict.fromkeys(exclusions)),
                "purpose_overrides": purpose_overrides,
                "git_tree_entries": len(entries), "outside_analysis_paths": outside,
                "configured_exclusions": len(excluded), "excluded_paths": excluded,
                "scope_files": len(scoped),
                "dirty_tracked": run(["git", "status", "--porcelain", "--untracked-files=no"], repo).decode().splitlines(),
            })
            current_config = config_file.read_bytes() if config_file.exists() else b""
            if current_config != config_raw or run(["git", "rev-parse", "HEAD"], repo).decode().strip() != head:
                raise ValueError(f"{alias}: configuration or HEAD changed during collection")
        if membership_file.read_bytes() != membership_raw:
            raise ValueError("Workspace membership changed during collection")
        rows = sorted(unique.values(), key=lambda r: (r["repository"], r["head"], r["path"]))
        stderr = count_staged(rows, stage, scratch)
        for alias_row in aliases:
            alias_row["composition"] = aggregate([r for r in rows if alias_row["alias"] in r["aliases"]])
        result = {
            "schema_version": 1, "generator": {"name": "repowise-workspace-metrics", "version": __version__},
            "generated_at": datetime.now(timezone.utc).isoformat(), "workspace": str(workspace),
            "membership_sha256": hashlib.sha256(membership_raw).hexdigest(),
            "default_repo": membership.get("default_repo"),
            "method": {
                "scope": "committed Git paths under Repowise analysis_paths minus configured exclusions",
                "classification": CLASSIFIER_VERSION, "repowise_helper_version": "0.61.0",
                "purpose_classification": PURPOSE_VERSION,
                "binary": "NUL in first 8192 bytes; symlinks not followed",
                "physical_lines": "LF-delimited bytes; final unterminated line counted; ASCII whitespace blank",
                "cloc_version": cloc_version, "cloc_skip_uniqueness": True,
                "cloc_timeout_per_filter_seconds": 5, "mixed_code_comment": None,
            },
            "unmeasured": ["ownership", "history_trends", "provenance", "observed_harness_loading"],
            "aliases": aliases, "union": aggregate(rows),
            "overlapping_alias_paths": sum(len(r["aliases"]) > 1 for r in rows),
            "by_role": {key: aggregate([r for r in rows if r["role"] == key]) for key in sorted({r["role"] for r in rows})},
            "by_extension": {key: aggregate([r for r in rows if r["extension"] == key]) for key in sorted({r["extension"] for r in rows})},
            "extension_role": [
                {"extension": ext, "role": role, **aggregate([r for r in rows if r["extension"] == ext and r["role"] == role])}
                for ext, role in sorted({(r["extension"], r["role"]) for r in rows})
            ],
            "cloc_stderr": stderr,
            "cloc_omission_reasons": dict(Counter(
                r["counter_status"] for r in rows if r["mode"] != "120000" and not r["binary"] and r["cloc"] is None
            )),
            "files": rows,
            **summarize(rows, measured=True),
        }
        validate(result)
        return result


def validate(value: Mapping[str, Any]) -> None:
    if value.get("schema_version") != 1:
        raise ValueError("Unsupported census schema version")
    rows = value["files"]
    if value.get("method", {}).get("purpose_classification") is not None:
        if value["method"]["purpose_classification"] not in SUPPORTED_VERSIONS:
            raise ValueError("Unsupported purpose classifier version")
        for row in rows:
            if row.get("purpose") not in PURPOSE_IDS or any(
                row.get("agentic", {}).get(family, {}).get("status") not in STATUSES for family in FAMILIES
            ):
                raise ValueError(f"Invalid purpose/agentic evidence: {row['path']}")
            for family, primary in (("documentation", "product_definition"), ("enablement", "development_enablement")):
                if row["agentic"][family]["status"] == "confirmed" and row["purpose"] != primary:
                    raise ValueError(f"Confirmed agentic evidence disagrees with purpose: {row['path']}")
        for key, expected in summarize(rows, measured=True).items():
            if value.get(key) != expected:
                raise ValueError(f"Census {key} evidence does not reconcile")
    for row in rows:
        if row["bytes"] < 0 or (
            row["mode"] != "120000" and not row["binary"]
            and (row["physical_lines"] < 0 or not 0 <= row["blank_lines"] <= row["physical_lines"])
        ):
            raise ValueError(f"Invalid file counts: {row['path']}")
        if row["cloc"] is not None and any(
            not isinstance(row["cloc"][k], int) or row["cloc"][k] < 0 for k in ("code", "comment", "blank")
        ):
            raise ValueError(f"Invalid counter counts: {row['path']}")
    identities = {(r["repository"], r["head"], r["path"], r["blob"]) for r in rows}
    if len(identities) != len(rows) or aggregate(rows) != value["union"]:
        raise ValueError("Census population or totals do not reconcile")
    for field in ("role", "extension"):
        expected = {key: aggregate([r for r in rows if r[field] == key]) for key in sorted({r[field] for r in rows})}
        if expected != value[f"by_{field}"]:
            raise ValueError(f"Census {field} breakdown does not reconcile")
    expected_cross = [
        {"extension": ext, "role": role, **aggregate([r for r in rows if r["extension"] == ext and r["role"] == role])}
        for ext, role in sorted({(r["extension"], r["role"]) for r in rows})
    ]
    if expected_cross != value["extension_role"]:
        raise ValueError("Census extension/role cross-tab does not reconcile")
    names = [a["alias"] for a in value["aliases"]]
    if len(set(names)) != len(names) or any(not r["aliases"] or len(set(r["aliases"])) != len(r["aliases"]) or not set(r["aliases"]) <= set(names) for r in rows):
        raise ValueError("Census aliases are invalid")
    for alias in value["aliases"]:
        composed = aggregate([r for r in rows if alias["alias"] in r["aliases"]])
        if composed != alias["composition"] or alias["scope_files"] != composed["files"] or alias["git_tree_entries"] != alias["outside_analysis_paths"] + alias["configured_exclusions"] + alias["scope_files"]:
            raise ValueError(f"Alias scope/totals do not reconcile: {alias['alias']}")
    if value["overlapping_alias_paths"] != sum(len(r["aliases"]) > 1 for r in rows):
        raise ValueError("Census overlap count does not reconcile")
    for row in rows:
        if row["cloc"] is not None and sum(row["cloc"][k] for k in ("code", "comment", "blank")) != row["physical_lines"]:
            raise ValueError(f"Counter/physical line discrepancy: {row['path']}")
    omissions = dict(Counter(
        r["counter_status"] for r in rows if r["mode"] != "120000" and not r["binary"] and r["cloc"] is None
    ))
    if omissions != value["cloc_omission_reasons"]:
        raise ValueError("Census omission reasons do not reconcile")
