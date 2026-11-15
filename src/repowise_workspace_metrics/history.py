"""Reconstruct frozen-scope stocks and mainline text edits from immutable Git objects."""

from __future__ import annotations

import copy
import hashlib
import shutil
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any

import pathspec

from .census import (
    CLASSIFIER_VERSION, aggregate, artifact_kind, classify, count_staged,
    line_counts, read_mapping, run, scope_reason, validate,
)
from .compat import helpers
from .purposes import (
    FAMILIES, LEGACY_PURPOSES, PURPOSE_IDS, STATUSES, VERSION, SUPPORTED_VERSIONS,
    evidence as purpose_evidence, purpose_of, source_file, summarize, validate_overrides,
)

PURPOSE_FIELDS = (
    "purpose", "purpose_rule", "purpose_confidence", "purpose_override_reason",
    "agentic", "enablement_kind", "harness_load_status",
)


def _purpose_evidence(
    aliases: list[dict[str, Any]], path: str, role: str, mode: str,
    *, classifier_version: str = VERSION,
) -> dict[str, Any]:
    results = [
        purpose_evidence(path, role, mode, alias["purpose_overrides"], classifier_version=classifier_version)
        for alias in aliases
    ]
    if not results or any(result != results[0] for result in results[1:]):
        raise ValueError(f"Overlapping alias purpose evidence disagrees: {path}")
    return results[0]


def _month(date: datetime, offset: int) -> datetime:
    index = date.year * 12 + date.month - 1 + offset
    return datetime(index // 12, index % 12 + 1, 1, tzinfo=timezone.utc)


def _edits(rows: list[dict[str, Any]]) -> dict[str, int]:
    additions = sum(row["additions"] for row in rows)
    deletions = sum(row["deletions"] for row in rows)
    return {
        "additions": additions, "deletions": deletions,
        "net": additions - deletions, "changed_lines": additions + deletions,
    }


def _flow(rows: list[dict[str, Any]], commits: set[tuple[str, str]]) -> dict[str, Any]:
    result = {
        **_edits(rows), "commits": len(commits),
        "binary_files": sum(row["binary"] for row in rows),
        "by_purpose": {
            key: _edits([row for row in rows if row["purpose"] == key])
            for key in sorted(PURPOSE_IDS)
        },
        "by_extension": {
            key: _edits([row for row in rows if row["extension"] == key])
            for key in sorted({row["extension"] for row in rows})
        },
        "files": rows,
    }
    result["by_agentic"] = {
        family: {
            status: _edits([row for row in rows if row["agentic"][family]["status"] == status])
            for status in STATUSES
        } for family in FAMILIES
    }
    result["by_agentic"]["non_agentic_enablement"] = _edits([
        row for row in rows if purpose_of(row) == "development_enablement"
        and row.get("enablement_kind") in {"build_ci", "packaging_tooling"}
        and row["agentic"]["enablement"]["status"] != "confirmed"
    ])
    return result


def _view(rows: list[dict[str, Any]], flow: dict[str, Any]) -> dict[str, Any]:
    purpose_summary = summarize(rows, measured=True)
    purpose_summary["by_purpose"] = {
        key: purpose_summary["by_purpose"].get(key, aggregate([])) for key in sorted(PURPOSE_IDS)
    }
    return {
        **purpose_summary,
        "union": aggregate(rows),
        "by_role": {
            key: aggregate([row for row in rows if row["role"] == key])
            for key in sorted(LEGACY_PURPOSES)
        },
        "by_extension": {
            key: aggregate([row for row in rows if row["extension"] == key])
            for key in sorted({row["extension"] for row in rows})
        },
        "extension_role": [
            {"extension": ext, "role": role, **aggregate([
                row for row in rows if row["extension"] == ext and row["role"] == role
            ])}
            for ext, role in sorted({(row["extension"], row["role"]) for row in rows})
        ],
        "counter_coverage": {
            "omission_reasons": dict(Counter(
                row["counter_status"] for row in rows
                if row["mode"] != "120000" and not row["binary"] and row["cloc"] is None
            )),
            "source_files": sum(source_file(row) for row in rows),
            "counted_source_files": sum(
                source_file(row) and row["cloc"] is not None
                for row in rows
            ),
        },
        "source_stock": {
            key: aggregate([row for row in rows if source_file(row) and purpose_of(row) == purpose])
            for key, purpose in (
                ("implementation", "product_implementation"), ("testing", "product_verification")
            )
        },
        "flow": flow,
    }


class _Reader:
    def __init__(self, census: dict[str, Any], scratch: Path):
        self.census = census
        self.scratch = scratch
        self.analysis_paths, self.is_test = helpers()
        self.cache = {
            (row["repository"], row["path"], row["blob"], row["mode"]): copy.deepcopy(row)
            for row in census["files"]
        }
        self.complete_counts = set(self.cache)
        self.latest_cache_hits = 0
        self.counted_versions = 0
        self.aliases = census["aliases"]
        self.groups: dict[str, list[dict[str, Any]]] = {}
        self.patterns = {}
        for alias in self.aliases:
            validate_overrides(alias["purpose_overrides"])
            self.groups.setdefault(alias["repository"], []).append(alias)
            self.patterns[alias["alias"]] = pathspec.PathSpec.from_lines(
                "gitwildmatch", alias["exclude_patterns"]
            )
        for row in self.cache.values():
            expected = self.purpose(row["repository"], row["path"], row["role"], row["mode"])
            if any(row.get(key) != expected[key] for key in PURPOSE_FIELDS):
                raise ValueError(f"Cached purpose evidence does not match frozen classifier: {row['path']}")

    def purpose(self, repository: str, path: str, role: str, mode: str) -> dict[str, Any]:
        included = self.included(repository, path)
        return _purpose_evidence(
            [alias for alias in self.groups[repository] if alias["alias"] in included],
            path, role, mode,
        )

    def included(self, repository: str, path: str) -> list[str]:
        return [
            alias["alias"] for alias in self.groups[repository]
            if scope_reason(path, tuple(alias["analysis_paths"]), self.patterns[alias["alias"]]) is None
        ]

    def guard(self) -> None:
        workspace = Path(self.census["workspace"])
        membership_file = workspace / ".repowise-workspace.yaml"
        raw, membership = read_mapping(membership_file)
        if hashlib.sha256(raw).hexdigest() != self.census["membership_sha256"]:
            raise ValueError("Workspace membership changed since census")
        actual = {
            member["alias"]: str((workspace / member["path"]).resolve())
            for member in membership["repos"]
        }
        if actual != {alias["alias"]: str(Path(alias["path"]).resolve()) for alias in self.aliases}:
            raise ValueError("Workspace membership does not match census aliases")
        for alias in self.aliases:
            repo = Path(alias["path"])
            config_file = repo / ".repowise/config.yaml"
            raw, config = read_mapping(config_file) if config_file.exists() else (b"", {})
            if (
                hashlib.sha256(raw).hexdigest() != alias["config_sha256"]
                or list(self.analysis_paths(config)) != alias["analysis_paths"]
                or list(dict.fromkeys(config.get("exclude_patterns", []))) != alias["exclude_patterns"]
                or validate_overrides(config.get("metrics_purpose_overrides", [])) != alias["purpose_overrides"]
            ):
                raise ValueError(f"{alias['alias']}: configuration changed since census")
            for args, expected in (
                (["rev-parse", "HEAD"], alias["head"]),
                (["rev-parse", "--path-format=absolute", "--git-common-dir"], alias["repository"]),
                (["rev-parse", f"{alias['head']}^{{tree}}"], alias["tree"]),
            ):
                if run(["git", *args], repo).decode().strip() != expected:
                    raise ValueError(f"{alias['alias']}: Git identity or HEAD changed since census")
            if run(["git", "rev-parse", "--is-shallow-repository"], repo).strip() != b"false":
                raise ValueError(f"{alias['alias']}: shallow history cannot establish monthly coverage")

    def row(self, repository: str, path: str, blob: str, mode: str) -> dict[str, Any]:
        key = repository, path, blob, mode
        if key in self.cache:
            self.latest_cache_hits += 1
            return self.cache[key]
        repo = Path(self.groups[repository][0]["path"])
        data = run(["git", "cat-file", "blob", blob], repo)
        role, rule, confidence = classify(path, mode, data, self.is_test)
        binary = b"\0" in data[:8192]
        text = mode != "120000" and not binary
        physical, blank = line_counts(data) if text else (None, None)
        row = {
            "repository": repository, "path": path, "blob": blob, "mode": mode,
            "bytes": len(data), "binary": binary, "physical_lines": physical,
            "blank_lines": blank, "extension": PurePosixPath(path).suffix.lower() or "[none]",
            "role": role, "classification_rule": rule, "confidence": confidence,
            "artifact_kind": artifact_kind(path), "cloc": None,
            "origin_traits": ["vendored"] if role == "generated_vendor" else [],
            **self.purpose(repository, path, role, mode),
        }
        if not text:
            row["counter_status"] = "symlink_not_followed" if mode == "120000" else "binary_not_counted"
            self.complete_counts.add(key)
        self.cache[key] = row
        return row

    def snapshot(self, commits: dict[str, str]) -> list[dict[str, Any]]:
        rows = []
        pending = []
        stage = self.scratch / "source"
        stage.mkdir()
        try:
            for repository, aliases in self.groups.items():
                head = commits[repository]
                repo = Path(aliases[0]["path"])
                records = run(["git", "ls-tree", "-rz", "--full-tree", head], repo)
                for record in records.split(b"\0"):
                    if not record:
                        continue
                    meta, raw_path = record.split(b"\t", 1)
                    mode, kind, blob = meta.decode().split()
                    path = raw_path.decode("utf-8")
                    included = self.included(repository, path)
                    if not included:
                        continue
                    if kind != "blob" or any(char in path for char in "\n\r"):
                        raise ValueError(f"Unsupported historical path: {path}")
                    if PurePosixPath(path).name == ".repowiseIgnore":
                        raise ValueError("Historical .repowiseIgnore requires scope support")
                    row = self.row(repository, path, blob, mode)
                    key = repository, path, blob, mode
                    if key not in self.complete_counts:
                        target = stage / str(len(pending)) / path
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(run(["git", "cat-file", "blob", blob], repo))
                        row["stage_path"] = target.relative_to(stage).as_posix()
                        pending.append(row)
                    rows.append({**row, "head": head, "aliases": included})
            count_staged(pending, stage, self.scratch)
            self.counted_versions += len(pending)
            for row in pending:
                self.complete_counts.add((row["repository"], row["path"], row["blob"], row["mode"]))
            # Copies must receive newly measured counts, not the pre-count placeholder.
            return [
                {**self.cache[(row["repository"], row["path"], row["blob"], row["mode"])],
                 "head": row["head"], "aliases": row["aliases"]}
                for row in rows
            ]
        finally:
            shutil.rmtree(stage)

    def changes(self, repository: str, commit: str, parent: str) -> list[dict[str, Any]]:
        repo = Path(self.groups[repository][0]["path"])
        # The first pass reads metadata only. Never send excluded paths to a content diff.
        tokens = run([
            "git", "diff-tree", "-r", "--raw", "-z", "--no-renames",
            "--no-abbrev", parent, commit,
        ], repo).split(b"\0")
        paths = []
        for i in range(0, len(tokens) - 1, 2):
            path = tokens[i + 1].decode("utf-8")
            if self.included(repository, path):
                paths.append(path)
        if not paths:
            return []
        tokens = run([
            "git", "--literal-pathspecs", "diff", "--raw", "--numstat", "-z",
            "--no-abbrev", "--find-renames=50%", "--no-ext-diff", "--no-textconv",
            parent, commit, "--", *paths,
        ], repo, timeout=300).split(b"\0")
        records = []
        i = 0
        while i < len(tokens) and tokens[i].startswith(b":"):
            old_mode, mode, old_blob, blob, status = tokens[i][1:].decode().split()
            old_path = tokens[i + 1].decode("utf-8")
            i += 2
            path = old_path
            if status.startswith(("R", "C")):
                path = tokens[i].decode("utf-8")
                i += 1
            records.append((old_mode, mode, old_blob, blob, status, old_path, path))
        counts = {}
        while i < len(tokens) and tokens[i]:
            additions, deletions, raw_path = tokens[i].split(b"\t", 2)
            i += 1
            if not raw_path:
                old_path, path = tokens[i].decode(), tokens[i + 1].decode()
                i += 2
            else:
                path = old_path = raw_path.decode()
            counts[(old_path, path)] = (additions, deletions)
        evidence = []
        for old_mode, mode, old_blob, blob, status, old_path, path in records:
            additions, deletions = counts[(old_path, path)]
            binary = additions == b"-"
            deleted = status == "D"
            row = self.row(repository, old_path if deleted else path, old_blob if deleted else blob,
                           old_mode if deleted else mode)
            old_aliases = self.included(repository, old_path) if old_mode != "000000" else []
            new_aliases = self.included(repository, path) if mode != "000000" else []
            previous = None
            if old_mode != "000000":
                previous = self.row(repository, old_path, old_blob, old_mode)
            evidence.append({
                "repository": repository, "commit": commit, "parent": parent,
                "path": path, "previous_path": old_path if status.startswith("R") else None,
                "blob": old_blob if deleted else blob, "previous_blob": old_blob if previous else None,
                "aliases": sorted(set(old_aliases + new_aliases)),
                "previous_aliases": old_aliases, "current_aliases": new_aliases,
                "role": row["role"], **{key: copy.deepcopy(row[key]) for key in PURPOSE_FIELDS},
                "origin_traits": copy.deepcopy(row["origin_traits"]),
                "mode": old_mode if deleted else mode, "previous_mode": old_mode if previous else None,
                "extension": row["extension"],
                "additions": 0 if binary else int(additions),
                "deletions": 0 if binary else int(deletions), "binary": binary,
                "change_type": (
                    "pure_rename" if status.startswith("R") and old_blob == blob else
                    "rename_edit" if status.startswith("R") else
                    {"A": "added", "D": "deleted", "M": "modified", "T": "type_changed"}.get(status, status)
                ),
                "classification_rule": row["classification_rule"], "confidence": row["confidence"],
                "previous_classification": ({
                    "role": previous["role"],
                    "origin_traits": copy.deepcopy(previous["origin_traits"]),
                    **{key: copy.deepcopy(previous[key]) for key in PURPOSE_FIELDS},
                    "classification_rule": previous["classification_rule"], "confidence": previous["confidence"],
                } if previous else None),
                "purpose_changed": previous is not None and purpose_of(previous) != purpose_of(row),
            })
        return evidence


def collect_history(census: dict[str, Any], months: int = 12) -> dict[str, Any]:
    """Return history only; neither the census nor its ownership overlay is modified."""
    validate(census)
    if not isinstance(months, int) or isinstance(months, bool) or months < 1:
        raise ValueError("months must be a positive integer")
    if census["method"]["classification"] != CLASSIFIER_VERSION:
        raise ValueError(f"Historical collection requires {CLASSIFIER_VERSION}/{VERSION}; legacy role classifiers cannot be mixed")
    if census["method"].get("purpose_classification") != VERSION:
        raise ValueError(f"Historical collection requires {VERSION} census evidence; legacy purpose classifiers cannot be mixed")
    collected = datetime.fromisoformat(census["generated_at"].replace("Z", "+00:00"))
    if collected.tzinfo is None:
        raise ValueError("Census collection date requires a timezone")
    collected = collected.astimezone(timezone.utc)
    scratch = Path.cwd() / ".workspace-metrics-history-counts"
    scratch.mkdir()  # Refuse to overwrite another collector's staging area.
    try:
        reader = _Reader(census, scratch)
        reader.guard()
        if run(["cloc", "--version"], Path(census["workspace"])).decode().strip() != census["method"]["cloc_version"]:
            raise ValueError("Historical counter version does not match census")
        chains = {}
        selected = {}
        cutoffs = [_month(collected, -months + i + 1) - timedelta(microseconds=1) for i in range(months)]
        cutoffs.append(collected)
        coverage_repos = {}
        for repository, aliases in reader.groups.items():
            heads = {alias["head"] for alias in aliases}
            if len(heads) != 1:
                raise ValueError("Historical aliases sharing a repository must share a census HEAD")
            head = heads.pop()
            repo = Path(aliases[0]["path"])
            objects = run(["git", "rev-list", "--objects", "--missing=print", head], repo, timeout=600)
            if any(line.startswith(b"?") for line in objects.splitlines()):
                raise ValueError(f"Missing reachable Git objects: {repository}")
            chain = [
                (sha, int(timestamp)) for sha, timestamp in (
                    line.split() for line in run(
                        ["git", "log", "--first-parent", "--format=%H %ct", head], repo, timeout=600
                    ).decode().splitlines()
                )
            ]
            if not chain or chain[0][0] != head or chain[0][1] > collected.timestamp():
                raise ValueError("HEAD is absent or dated beyond census collection")
            indices = []
            for cutoff in cutoffs[:-1]:
                found = next((i for i, (_, stamp) in enumerate(chain) if stamp <= cutoff.timestamp()), None)
                if found is None:
                    raise ValueError(f"No reachable baseline/month-end snapshot at {cutoff.isoformat()}: {repository}")
                indices.append(found)
            indices.append(0)
            if any(left < right for left, right in zip(indices, indices[1:])):
                raise ValueError("Non-monotonic commit timestamps prevent comparable month-end snapshots")
            chains[repository] = chain
            selected[repository] = indices
            coverage_repos[repository] = {
                "head": head, "first_parent_commits": len(chain), "reachable_objects_complete": True,
                "shallow": False, "baseline_commit": chain[indices[0]][0],
                "baseline_cutoff": cutoffs[0].isoformat(),
                "head_committed_at": datetime.fromtimestamp(chain[0][1], timezone.utc).isoformat(),
                "nonmonotonic_timestamp_edges": sum(a[1] < b[1] for a, b in zip(chain, chain[1:])),
            }
        baseline_heads = {repo: chains[repo][selected[repo][0]][0] for repo in chains}
        baseline_rows = reader.snapshot(baseline_heads)
        baseline = {
            "cutoff": cutoffs[0].isoformat(),
            "commits": {alias["alias"]: baseline_heads[alias["repository"]] for alias in census["aliases"]},
            "workspace": _view(baseline_rows, _flow([], set())),
            "aliases": {
                alias["alias"]: _view([r for r in baseline_rows if alias["alias"] in r["aliases"]], _flow([], set()))
                for alias in census["aliases"]
            },
        }
        points = []
        for window in range(months):
            heads = {repo: chains[repo][selected[repo][window + 1]][0] for repo in chains}
            rows = reader.snapshot(heads)
            evidence = []
            commits: set[tuple[str, str]] = set()
            alias_commits = {alias["alias"]: set() for alias in census["aliases"]}
            for repository, chain in chains.items():
                before, after = selected[repository][window], selected[repository][window + 1]
                for index in range(before - 1, after - 1, -1):
                    commit, parent = chain[index][0], chain[index + 1][0]
                    changed = reader.changes(repository, commit, parent)
                    evidence.extend(changed)
                    # Activity is all first-parent commits, including out-of-scope-only commits.
                    commits.add((repository, commit))
                    for alias in reader.groups[repository]:
                        alias_commits[alias["alias"]].add((repository, commit))
            cutoff = cutoffs[window + 1]
            points.append({
                "period": _month(collected, -months + window + 1).strftime("%Y-%m"),
                "cutoff": cutoff.isoformat(), "partial": window == months - 1,
                "commits": {alias["alias"]: heads[alias["repository"]] for alias in census["aliases"]},
                "workspace": _view(rows, _flow(evidence, commits)),
                "aliases": {
                    alias["alias"]: _view(
                        [r for r in rows if alias["alias"] in r["aliases"]],
                        _flow([r for r in evidence if alias["alias"] in r["aliases"]], alias_commits[alias["alias"]]),
                    ) for alias in census["aliases"]
                },
            })
        reader.guard()
        history = {
            "schema_version": 1,
            "method": {
                "population": census["method"]["scope"], "scope_policy": "frozen current membership/configuration",
                "membership_sha256": census["membership_sha256"],
                "config_sha256": {a["alias"]: a["config_sha256"] for a in census["aliases"]},
                "classification": CLASSIFIER_VERSION, "counter": copy.deepcopy(census["method"]),
                "purpose_classification": VERSION,
                "purpose_overrides": {a["alias"]: copy.deepcopy(a["purpose_overrides"]) for a in census["aliases"]},
                "timestamp": "Git committer timestamp, UTC",
                "git_objects": "local reachable immutable objects only; lazy fetch and replacement objects disabled",
                "snapshot_policy": "latest first-parent reachable commit at or before UTC cutoff; latest point is census HEAD",
                "flow_policy": "topological first-parent commit-parent diffs between selected snapshots; merges mainline once",
                "flow_units": "physical text additions/deletions including comments, not programming LOC; Git numstat",
                "commit_count": "all first-parent commits in window, including commits without in-scope edits; workspace repository-deduplicated",
                "rename_policy": "Git 50% similarity within admitted changed paths; unchanged-content renames have zero text edits",
                "scope_transition_policy": "edits attributed to union of old/new admitted aliases; scope-crossing outside paths are additions/deletions",
                "purpose": f"frozen {VERSION} path/role/mode evidence and repository overrides; deletion uses old blob/path",
                "agentic": "independent documentation/enablement discovery statuses; not observed agent use or harness loading",
                "non_agentic_enablement": "recognized build/packaging tooling without confirmed agent guidance; not non-agent authorship",
                "count_cache": "immutable repository/path/blob/mode; latest census counts seeded; path-dependent classifications",
                "ratio": "observed counted source_stock implementation/testing cloc_code; zero tests undefined; build/CI/tooling, fixtures/vendor excluded",
                "ownership": "latest census only; historical ownership not collected",
                "unmeasured": ["generated_refresh_detection", "mass_formatting_detection", "historical_ownership", "agent_provenance"],
            },
            "coverage": {
                "complete": True, "gaps": [], "months": months,
                "start": _month(collected, -months + 1).isoformat(), "end": collected.isoformat(),
                "repositories": coverage_repos, "baseline": baseline,
                "new_counted_file_versions": reader.counted_versions,
                "cached_file_version_hits": reader.latest_cache_hits,
            },
            "points": points,
        }
        validate_history({**census, "history": history})
        return history
    finally:
        shutil.rmtree(scratch)


def validate_history(document: dict[str, Any]) -> None:
    """Validate optional historical evidence without re-reading Git or ownership."""
    validate(document)
    history = document.get("history")
    if history is None:
        return
    if history.get("schema_version") != 1 or not history.get("points"):
        raise ValueError("Unsupported or empty history")
    points = history["points"]
    aliases = {a["alias"]: a for a in document["aliases"]}
    if history["coverage"]["months"] != len(points) or not history["coverage"]["complete"] or history["coverage"]["gaps"]:
        raise ValueError("History month coverage does not reconcile")
    method = history["method"]
    purpose_version = method.get("purpose_classification")
    if purpose_version != document["method"].get("purpose_classification") or purpose_version not in {None, *SUPPORTED_VERSIONS}:
        raise ValueError("History purpose classifier does not match census")
    if purpose_version is not None and method.get("purpose_overrides") != {
        a["alias"]: a["purpose_overrides"] for a in document["aliases"]
    }:
        raise ValueError("History frozen purpose overrides do not match census")
    if (
        method["classification"] != document["method"]["classification"]
        or method["membership_sha256"] != document["membership_sha256"]
        or method["config_sha256"] != {a["alias"]: a["config_sha256"] for a in document["aliases"]}
    ):
        raise ValueError("History frozen scope/classifier does not match census")
    previous = None
    previous_period = None
    for index, point in enumerate(points):
        current = datetime.fromisoformat(point["cutoff"])
        if current.tzinfo is None or current.strftime("%Y-%m") != point["period"] or (previous and current <= previous):
            raise ValueError("History dates are not ordered monthly cutoffs")
        if previous_period and _month(current, -1).strftime("%Y-%m") != previous_period:
            raise ValueError("History has a missing monthly period")
        if point["partial"] != (index == len(points) - 1):
            raise ValueError("Only the latest historical period is partial")
        previous = current
        previous_period = point["period"]
        if set(point["aliases"]) != set(aliases) or set(point["commits"]) != set(aliases):
            raise ValueError("History aliases do not reconcile")
        for view in [point["workspace"], *point["aliases"].values()]:
            total = view["union"]
            if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in total.values()):
                raise ValueError("Invalid history stock counts")
            for grouping in ("by_role", "by_extension"):
                if any(sum(row[key] for row in view[grouping].values()) != value for key, value in total.items()):
                    raise ValueError("History stock breakdown does not reconcile")
            if any(sum(row[key] for row in view["extension_role"]) != value for key, value in total.items()):
                raise ValueError("History stock cross-tab does not reconcile")
            if purpose_version is not None:
                if set(view["by_purpose"]) != set(PURPOSE_IDS) or any(
                    sum(row[key] for row in view["by_purpose"].values()) != value
                    or sum(row[key] for row in view["extension_purpose"]) != value
                    for key, value in total.items()
                ):
                    raise ValueError("History purpose stocks do not reconcile")
                for family in FAMILIES:
                    if set(view["agentic"][family]) != set(STATUSES) or any(
                        sum(row[key] for row in view["agentic"][family].values()) != value
                        for key, value in total.items()
                    ):
                        raise ValueError("History agentic stocks do not reconcile")
            flow = view["flow"]
            if any(
                not isinstance(flow[key], int) or isinstance(flow[key], bool) or flow[key] < 0
                for key in ("additions", "deletions", "changed_lines", "commits", "binary_files")
            ):
                raise ValueError("Invalid history flow counts")
            if _edits(flow["files"]) != {key: flow[key] for key in _edits([])}:
                raise ValueError("History flow evidence does not reconcile")
            if flow["binary_files"] != sum(row["binary"] for row in flow["files"]):
                raise ValueError("History binary flow does not reconcile")
            for grouping in ("by_purpose", "by_extension"):
                dimension = "purpose" if grouping == "by_purpose" else "extension"
                expected = {
                    key: _edits([row for row in flow["files"] if row[dimension] == key])
                    for key in sorted(
                        (PURPOSE_IDS if purpose_version is not None else set(LEGACY_PURPOSES.values())) if dimension == "purpose"
                        else {row[dimension] for row in flow["files"]}
                    )
                }
                if expected != flow[grouping]:
                    raise ValueError("History flow breakdown does not reconcile")
            identities = {(r["repository"], r["commit"], r["parent"], r["path"]) for r in flow["files"]}
            if len(identities) != len(flow["files"]) or any(
                not isinstance(r["additions"], int) or not isinstance(r["deletions"], int)
                or r["additions"] < 0 or r["deletions"] < 0
                or not r["aliases"] or not set(r["aliases"]) <= set(aliases)
                or (r["purpose"] not in PURPOSE_IDS if purpose_version is not None
                    else r["purpose"] != LEGACY_PURPOSES.get(r["role"]))
                for r in flow["files"]
            ):
                raise ValueError("History flow identities/counts are invalid")
            if purpose_version is not None:
                for row in flow["files"]:
                    admitted = row["previous_aliases"] if row["change_type"] == "deleted" else row["current_aliases"]
                    expected = _purpose_evidence(
                        [aliases[alias] for alias in admitted], row["path"], row["role"], row["mode"],
                        classifier_version=purpose_version,
                    )
                    if any(row.get(key) != expected[key] for key in PURPOSE_FIELDS):
                        raise ValueError("History changed-file purpose evidence does not reconcile")
                    previous_classification = row["previous_classification"]
                    if previous_classification:
                        expected_previous = _purpose_evidence(
                            [aliases[alias] for alias in row["previous_aliases"]],
                            row["previous_path"] or row["path"], previous_classification["role"],
                            row["previous_mode"],
                            classifier_version=purpose_version,
                        )
                        if any(previous_classification.get(key) != expected_previous[key] for key in PURPOSE_FIELDS):
                            raise ValueError("History previous purpose evidence does not reconcile")
                    if row["purpose_changed"] != (
                        previous_classification is not None
                        and previous_classification["purpose"] != row["purpose"]
                    ):
                        raise ValueError("History purpose transition does not reconcile")
                for family in FAMILIES:
                    expected = {
                        status: _edits([row for row in flow["files"] if row["agentic"][family]["status"] == status])
                        for status in STATUSES
                    }
                    if flow["by_agentic"][family] != expected:
                        raise ValueError("History agentic flow does not reconcile")
                if flow["by_agentic"]["non_agentic_enablement"] != _edits([
                    row for row in flow["files"] if row["purpose"] == "development_enablement"
                    and row.get("enablement_kind") in {"build_ci", "packaging_tooling"}
                    and row["agentic"]["enablement"]["status"] != "confirmed"
                ]):
                    raise ValueError("History non-agentic enablement flow does not reconcile")
        for alias in aliases:
            expected = [row for row in point["workspace"]["flow"]["files"] if alias in row["aliases"]]
            if point["aliases"][alias]["flow"]["files"] != expected:
                raise ValueError("History alias flow population does not reconcile")
    latest = points[-1]
    if datetime.fromisoformat(latest["cutoff"]) != datetime.fromisoformat(document["generated_at"]):
        raise ValueError("Latest history cutoff does not match census collection")
    expected_roles = {role: document["by_role"].get(role, aggregate([])) for role in LEGACY_PURPOSES}
    if (
        latest["workspace"]["union"] != document["union"]
        or latest["workspace"]["by_role"] != expected_roles
        or any(latest["workspace"][key] != document[key] for key in ("by_extension", "extension_role"))
    ):
        raise ValueError("Latest historical stock does not match census")
    for alias, census_alias in aliases.items():
        if latest["aliases"][alias]["union"] != census_alias["composition"] or latest["commits"][alias] != census_alias["head"]:
            raise ValueError("Latest historical alias does not match census")
    if purpose_version is not None:
        for view, rows in [
            (latest["workspace"], document["files"]),
            *[(latest["aliases"][alias], [row for row in document["files"] if alias in row["aliases"]])
              for alias in aliases],
        ]:
            expected = _view(rows, view["flow"])
            for key in ("by_purpose", "extension_purpose", "agentic", "source_stock", "counter_coverage"):
                if view[key] != expected[key]:
                    raise ValueError("Latest historical purpose/source stock does not match census")
