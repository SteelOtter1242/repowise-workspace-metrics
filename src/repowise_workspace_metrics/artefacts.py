"""Opt-in whole-repository artefact evidence, separate from baseline scope."""

from __future__ import annotations

import copy
import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

import pathspec

from .census import aggregate, classify, line_counts, run, scope_reason, validate
from .compat import helpers
from .purposes import VERSION, SUPPORTED_VERSIONS, build_kind, discovery, evidence, summarize


def view(rows: list[dict[str, Any]], alias: str | None = None) -> dict[str, Any]:
    return {
        "union": aggregate(rows), **summarize(rows, measured=True),
        "by_scope": {
            scope: aggregate([row for row in rows if (row["alias_scopes"][alias] if alias else row["scope_status"]) == scope])
            for scope in ("included", "configured_exclusion", "outside_analysis_paths")
        },
    }


def collect_artefacts(document: dict[str, Any]) -> dict[str, Any]:
    validate(document)
    if document["method"].get("purpose_classification") != VERSION:
        raise ValueError(f"Artefact inventory requires a fresh {VERSION} census; old evidence is not reclassified")
    _, is_test = helpers()
    unique: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    for alias in document["aliases"]:
        repo = Path(alias["path"])
        identity = run(["git", "rev-parse", "--path-format=absolute", "--git-common-dir"], repo).decode().strip()
        if identity != alias["repository"]:
            raise ValueError(f"Artefact repository identity changed: {alias['alias']}")
        patterns = pathspec.PathSpec.from_lines("gitwildmatch", alias["exclude_patterns"])
        overrides = alias.get("purpose_overrides", [])
        for record in run(["git", "ls-tree", "-rz", "--full-tree", alias["head"]], repo).split(b"\0"):
            if not record:
                continue
            meta, raw_path = record.split(b"\t", 1)
            mode, kind, blob = meta.decode().split()
            path = raw_path.decode("utf-8")
            found = discovery(path)
            if not (
                found["kind"] or any(signal["matches"] for signal in found["signals"].values())
                or build_kind(path, discovery_only=True) or any(fnmatch.fnmatchcase(path, override["pattern"]) for override in overrides)
            ):
                continue
            if kind != "blob":
                raise ValueError(f"Unsupported artefact Git entry: {kind} {path}")
            key = identity, alias["head"], path, blob
            scope = scope_reason(path, tuple(alias["analysis_paths"]), patterns) or "included"
            if key in unique:
                row = unique[key]
                proposed = evidence(path, row["role"], mode, overrides)
                if proposed != {field: row[field] for field in proposed}:
                    raise ValueError(f"Overlapping aliases disagree on artefact purpose: {path}")
                row["aliases"].append(alias["alias"])
                row["alias_scopes"][alias["alias"]] = scope
                continue
            data = run(["git", "cat-file", "blob", blob], repo)
            role, rule, confidence = classify(path, mode, data, is_test)
            binary = b"\0" in data[:8192]
            physical, blank = line_counts(data) if mode != "120000" and not binary else (None, None)
            unique[key] = {
                "repository": identity, "head": alias["head"], "path": path, "blob": blob,
                "mode": mode, "aliases": [alias["alias"]], "alias_scopes": {alias["alias"]: scope},
                "bytes": len(data), "binary": binary, "physical_lines": physical, "blank_lines": blank,
                "extension": PurePosixPath(path).suffix.lower() or "[none]", "role": role,
                "classification_rule": rule, "confidence": confidence, "cloc": None,
                "counter_status": "native_code_not_requested_in_supplementary_inventory",
                "build_discovery_kind": build_kind(path, discovery_only=True),
                **evidence(path, role, mode, overrides),
            }
    rows = sorted(unique.values(), key=lambda row: (row["repository"], row["head"], row["path"]))
    for row in rows:
        scopes = set(row["alias_scopes"].values())
        row["scope_status"] = (
            "included" if "included" in scopes else "configured_exclusion"
            if "configured_exclusion" in scopes else "outside_analysis_paths"
        )
    result = copy.deepcopy(document)
    result["artefacts"] = {
        "schema_version": 1, "classification": VERSION,
        "scope": "Whole tracked repository artefact candidates; baseline exclusions unchanged",
        "historical_trends": "not_measured",
        "native_code_counts": "not_measured",
        "files": rows, "workspace": view(rows),
        "aliases": {
            alias["alias"]: view([row for row in rows if alias["alias"] in row["aliases"]], alias["alias"])
            for alias in document["aliases"]
        },
    }
    join_ownership(result)
    validate_artefacts(result)
    return result


def join_ownership(document: dict[str, Any]) -> None:
    if "artefacts" not in document:
        return
    owners = {
        (row["repository"], row["head"], row["path"], row["blob"]): row
        for row in document.get("ownership", {}).get("files", [])
    }
    for row in document["artefacts"]["files"]:
        owner = owners.get((row["repository"], row["head"], row["path"], row["blob"]))
        row["ownership_status"] = owner["status"] if owner else "not_collected_for_this_path"
        row["primary_owner"] = owner["primary_owner"] if owner else None


def validate_artefacts(document: dict[str, Any]) -> None:
    artefacts = document.get("artefacts")
    if artefacts is None:
        return
    if artefacts["schema_version"] != 1 or artefacts["classification"] not in SUPPORTED_VERSIONS:
        raise ValueError("Unsupported artefact evidence schema/classifier")
    rows = artefacts["files"]
    identities = {(row["repository"], row["head"], row["path"], row["blob"]) for row in rows}
    if len(identities) != len(rows) or artefacts["workspace"] != view(rows):
        raise ValueError("Artefact population/totals do not reconcile")
    aliases = {alias["alias"]: alias for alias in document["aliases"]}
    for row in rows:
        if set(row["aliases"]) != set(row["alias_scopes"]) or not set(row["aliases"]) <= aliases.keys():
            raise ValueError("Invalid artefact alias coverage")
        scopes = set(row["alias_scopes"].values())
        expected_union = "included" if "included" in scopes else "configured_exclusion" if "configured_exclusion" in scopes else "outside_analysis_paths"
        if row["scope_status"] != expected_union:
            raise ValueError("Artefact union scope does not reconcile")
        if row["bytes"] < 0 or row["mode"] != "120000" and not row["binary"] and (
            row["physical_lines"] < 0 or not 0 <= row["blank_lines"] <= row["physical_lines"]
        ):
            raise ValueError("Invalid artefact counts")
        for name, scope in row["alias_scopes"].items():
            alias = aliases[name]
            patterns = pathspec.PathSpec.from_lines("gitwildmatch", alias["exclude_patterns"])
            expected = scope_reason(row["path"], tuple(alias["analysis_paths"]), patterns) or "included"
            if scope != expected or row["repository"] != alias["repository"] or row["head"] != alias["head"]:
                raise ValueError("Artefact scope/snapshot does not match census")
    for name in aliases:
        if artefacts["aliases"][name] != view([row for row in rows if name in row["aliases"]], name):
            raise ValueError("Artefact alias totals do not reconcile")
