"""Join Repowise's existing human knowledge ownership to census dimensions."""

from __future__ import annotations

import copy
import hashlib
import os
import posixpath
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .census import validate
from .compat import ownership_helpers, verify_version
from .purposes import LEGACY_PURPOSES as PURPOSES
from .purposes import purpose_of, source_file

METHOD = (
    "Repowise owner_data/person_owner; normalized human knowledge owners only. "
    "Contributors are top-five human commit shares, not authored LOC. "
    "Stable identity keys are fingerprints of Repowise's normalized keys; "
    "display labels follow the explicitly selected export mode. "
    "Purpose is a provisional role-derived lens, not content-reviewed intent."
)


def identity_label(key: str) -> dict[str, str]:
    fingerprint = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return {"key": fingerprint, "name": f"Owner {fingerprint[:12]}"}


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str
    ) -> None:
        raise ValueError("Ownership API redirects are not allowed")


def collect_api(census: dict[str, Any], api_url: str, *, include_names: bool = False) -> dict[str, Any]:
    """Export the existing local Contributors API; never select index files."""
    import json

    from repowise.server.schemas.ownership import KnowledgeMatrixResponse

    validate(census)
    verify_version()
    url = urlsplit(api_url)
    if (
        url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}
        or url.username or url.password or url.path not in {"", "/"} or url.query or url.fragment
    ):
        raise ValueError("Ownership API must be a loopback HTTP origin without credentials")
    opener = build_opener(_NoRedirects())
    headers = {"Accept": "application/json"}
    if os.environ.get("REPOWISE_API_KEY"):
        headers["X-API-Key"] = os.environ["REPOWISE_API_KEY"]

    def read(endpoint: str) -> Any:
        with opener.open(Request(api_url.rstrip("/") + endpoint, headers=headers), timeout=60) as response:
            return json.load(response)

    health = read("/health")
    if health.get("status") != "healthy" or health.get("version") != "0.61.0":
        raise ValueError("Ownership API requires a healthy Repowise 0.61.0 instance")

    def targets(repos: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        selected = {}
        for alias in census["aliases"]:
            matches = [repo for repo in repos if Path(repo["local_path"]).resolve() == Path(alias["path"]).resolve()]
            if len(matches) != 1 or matches[0]["head_commit"] != alias["head"]:
                raise ValueError(f"Ownership API repository/snapshot does not match census: {alias['alias']}")
            selected[alias["alias"]] = matches[0]
        return selected

    before = targets(read("/api/repos"))
    sections = []
    for alias, repo in before.items():
        matrix = KnowledgeMatrixResponse.model_validate(
            read(f"/api/repos/{repo['id']}/owners/knowledge-matrix")
        )
        identities = []
        for entry in matrix.authors:
            identity = identity_label(entry.key)
            if include_names:
                if not entry.name.strip():
                    raise ValueError(f"Repowise has no mapped display name for an identity in {alias}")
                identity["name"] = entry.name
            identities.append(identity)

        def author(index: int) -> dict[str, str]:
            if not 0 <= index < len(identities):
                raise ValueError(f"Ownership API author index out of range: {alias}")
            return identities[index]

        files = []
        for directory, name, _, _, _, _, primary, shares, _ in matrix.files:
            if not 0 <= directory < len(matrix.dirs):
                raise ValueError(f"Ownership API directory index out of range: {alias}")
            total = sum(share for _, share in shares)
            files.append({
                "path": posixpath.join(matrix.dirs[directory], name),
                "primary_owner": author(primary) if primary is not None else None,
                "contributors": [{**author(index), "share": share} for index, share in shares],
                "contributor_residual_share": max(0.0, 1 - total) if shares else None,
            })
        sections.append({
            "id": "workspace-ownership", "repo": alias,
            "checks": [{
                "basis": "measured", "method": METHOD,
                "data": {
                    "schema_version": 1, "alias": alias, "head_commit": repo["head_commit"],
                    "files": files, "source": "Repowise public Contributors knowledge-matrix API",
                    "contributor_share_precision": 0.001,
                    "identity_labels": "mapped_display_names" if include_names else "opaque_fingerprints",
                },
            }],
        })
    if targets(read("/api/repos")) != before:
        raise ValueError("Repowise repository metadata changed during ownership export")
    evidence = {
        "schema_version": 1, "kind": "assessment",
        "generated_at": datetime.now(timezone.utc).isoformat(), "sections": sections,
    }
    _exports(evidence)
    return evidence


async def collect_section(ctx: Any) -> list[dict[str, Any]] | None:
    if os.environ.get("REPOWISE_WORKSPACE_METRICS_OWNERSHIP") != "1":
        return None
    from repowise.core.report.model import check

    read, person_owner, file_author_shares = ownership_helpers()
    data = await read(ctx.session, ctx.repository_id)
    files = []
    for row in data.git_rows:
        key = person_owner(row, data.resolve)
        shares = file_author_shares(row, data.resolve)
        files.append({
            "path": row["file_path"],
            "primary_owner": identity_label(key) if key else None,
            "contributors": [
                {**identity_label(contributor), "share": share}
                for contributor, share in shares
            ],
            "contributor_residual_share": max(0.0, 1 - sum(share for _, share in shares)) if shares else None,
        })
    return [check(
        "workspace-ownership",
        reason=f"{len(files)} indexed code-file ownership rows; not all census paths",
        basis="measured",
        method=METHOD,
        data={
            "schema_version": 1, "alias": ctx.alias, "head_commit": ctx.head_commit,
            "files": files, "declared_accountability": "not_collected",
            "identity_labels": "opaque fingerprints of normalized Repowise keys; no person names",
        },
    )]


def _exports(evidence: dict[str, Any]) -> dict[str, dict[str, Any]]:
    if evidence.get("schema_version") != 1 or evidence.get("kind") != "assessment":
        raise ValueError("Ownership evidence must be a Repowise assessment report schema 1")
    exports = {}
    for section in evidence["sections"]:
        if section["id"] != "workspace-ownership":
            continue
        if section.get("failed"):
            raise ValueError(f"Ownership export failed for {section.get('repo')}")
        checks = section["checks"]
        if len(checks) != 1 or checks[0]["basis"] != "measured":
            raise ValueError("Ownership evidence is not a measured per-file export")
        data = checks[0]["data"]
        alias = data["alias"]
        if not isinstance(alias, str) or data["schema_version"] != 1 or alias in exports or section["repo"] != alias:
            raise ValueError("Ownership evidence requires unique configured aliases")
        paths = [row["path"] for row in data["files"]]
        if len(set(paths)) != len(paths):
            raise ValueError(f"Duplicate ownership paths: {alias}")
        precision = data.get("contributor_share_precision", 0)
        if precision not in {0, 0.001}:
            raise ValueError("Unsupported contributor share precision")
        for row in data["files"]:
            owner = row["primary_owner"]
            if owner is not None and (not isinstance(owner["key"], str) or not owner["key"] or not isinstance(owner["name"], str)):
                raise ValueError("Invalid normalized owner identity")
            shares = row["contributors"]
            if len(shares) > 5 or len({entry["key"] for entry in shares}) != len(shares):
                raise ValueError("Invalid top-five contributor evidence")
            for entry in shares:
                if not isinstance(entry["key"], str) or not entry["key"] or not isinstance(entry["name"], str) or not isinstance(entry["share"], (int, float)) or not (0 <= entry["share"] <= 1 and (precision or entry["share"] > 0)):
                    raise ValueError("Invalid contributor identity/share")
            total = sum(entry["share"] for entry in shares)
            residual = row["contributor_residual_share"]
            rounding = precision * len(shares) / 2
            if shares and (
                total > 1 + rounding + 1e-9 or not isinstance(residual, (int, float))
                or not 0 <= residual <= 1 or abs(total + residual - 1) > rounding + 1e-9
            ):
                raise ValueError("Contributor shares/residual do not reconcile")
            if not shares and residual is not None:
                raise ValueError("No human contributors must remain unknown, not a zero-share human population")
        exports[alias] = data
    if not exports:
        raise ValueError("Report has no workspace-ownership section; export requires REPOWISE_WORKSPACE_METRICS_OWNERSHIP=1")
    return exports


def aggregate_ownership(files: list[dict[str, Any]], dimension: str) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in files:
        owner = row["primary_owner"]
        status = row["status"]
        key = (row[dimension], "owned" if owner else status, owner["key"] if owner else "")
        if key not in groups:
            groups[key] = {
                dimension: row[dimension], "status": key[1], "owner_key": key[2],
                "owner_name": owner["name"] if owner else "", "files": 0,
                "nonblank_text_lines": 0, "source_code_lines": 0, "source_counter_files": 0,
            }
        group = groups[key]
        group["files"] += 1
        group["nonblank_text_lines"] += row["nonblank_text_lines"]
        group["source_code_lines"] += row["source_code_lines"]
        group["source_counter_files"] += row["source_counter_files"]
    return [groups[key] for key in sorted(groups)]


def enrich(census: dict[str, Any], evidence: dict[str, Any]) -> dict[str, Any]:
    validate(census)
    exports = _exports(evidence)
    expected = {alias["alias"]: alias for alias in census["aliases"]}
    unknown = set(exports) - set(expected)
    if unknown:
        raise ValueError(f"Ownership evidence contains aliases outside the census: {', '.join(sorted(unknown))}")
    for alias, export in exports.items():
        if export["head_commit"] != expected[alias]["head"]:
            raise ValueError(f"Ownership snapshot does not match census: {alias}")
    indexed = {alias: {row["path"]: row for row in export["files"]} for alias, export in exports.items()}
    result = copy.deepcopy(census)
    rows = []
    for file in census["files"]:
        candidates = [
            indexed[alias][file["path"]] for alias in file["aliases"]
            if alias in indexed and file["path"] in indexed[alias]
        ]
        if candidates and any(row != candidates[0] for row in candidates[1:]):
            raise ValueError(f"Overlapping aliases disagree on normalized ownership: {file['path']}")
        record = candidates[0] if candidates else None
        owner = record["primary_owner"] if record else None
        status = "owned" if owner else (
            "no_primary_human_owner" if record else
            "no_ownership_metadata" if all(alias in exports for alias in file["aliases"]) else "missing_alias_export"
        )
        source = source_file(file)
        rows.append({
            "repository": file["repository"], "head": file["head"], "path": file["path"],
            "blob": file["blob"], "aliases": file["aliases"], "role": file["role"],
            "purpose": purpose_of(file), "extension": file["extension"], "status": status,
            "agentic_documentation": file.get("agentic", {}).get("documentation", {}).get("status", "not_measured"),
            "agentic_enablement": file.get("agentic", {}).get("enablement", {}).get("status", "not_measured"),
            "primary_owner": owner, "contributors": record["contributors"] if record else [],
            "contributor_residual_share": record["contributor_residual_share"] if record else None,
            "contributor_share_precision": max(
                (exports[alias].get("contributor_share_precision", 0) for alias in file["aliases"] if alias in exports),
                default=0,
            ),
            "nonblank_text_lines": file["physical_lines"] - file["blank_lines"] if file["physical_lines"] is not None else 0,
            "source_code_lines": file["cloc"]["code"] if source and file["cloc"] is not None else 0,
            "source_counter_files": int(source and file["cloc"] is not None),
        })
    result["ownership"] = {
        "schema_version": 1, "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "Repowise workspace-ownership report section", "method": METHOD,
        "evidence_generated_at": evidence["generated_at"],
        "evidence_sources": sorted({export.get("source", "Repowise workspace-ownership report section") for export in exports.values()}),
        "identity_label_modes": sorted({export.get("identity_labels", "unspecified_legacy") for export in exports.values()}),
        "contributor_share_precision": max(export.get("contributor_share_precision", 0) for export in exports.values()),
        "missing_alias_exports": sorted(set(expected) - set(exports)),
        "declared_accountability": "not_collected",
        "coverage": {status: sum(row["status"] == status for row in rows) for status in (
            "owned", "no_primary_human_owner", "no_ownership_metadata", "missing_alias_export",
        )},
        "files": rows,
        "by_role": aggregate_ownership(rows, "role"),
        "by_purpose": aggregate_ownership(rows, "purpose"),
        "by_extension": aggregate_ownership(rows, "extension"),
        "by_agentic_documentation": aggregate_ownership(rows, "agentic_documentation"),
        "by_agentic_enablement": aggregate_ownership(rows, "agentic_enablement"),
    }
    # A partial export is still a measured subset; coverage retains every missing path.
    result["unmeasured"] = [key for key in result.get("unmeasured", []) if key != "ownership"]
    from .artefacts import join_ownership

    join_ownership(result)
    validate_ownership(result)
    return result


def validate_ownership(document: dict[str, Any]) -> None:
    ownership = document.get("ownership")
    if ownership is None:
        return
    if ownership["schema_version"] != 1:
        raise ValueError("Unsupported ownership schema")
    rows = ownership["files"]
    identities = [(row["repository"], row["head"], row["path"], row["blob"]) for row in rows]
    census_identities = [(row["repository"], row["head"], row["path"], row["blob"]) for row in document["files"]]
    if identities != census_identities:
        raise ValueError("Ownership population does not match census")
    for row, file in zip(rows, document["files"]):
        if row["role"] != file["role"] or row["extension"] != file["extension"] or row["purpose"] != purpose_of(file):
            raise ValueError("Ownership dimensions do not match census")
        if (row["status"] == "owned") != (row["primary_owner"] is not None):
            raise ValueError("Ownership status and identity disagree")
        if row["status"] not in {"owned", "no_primary_human_owner", "no_ownership_metadata", "missing_alias_export"}:
            raise ValueError("Unknown ownership coverage status")
        nonblank = file["physical_lines"] - file["blank_lines"] if file["physical_lines"] is not None else 0
        source = source_file(file) and file["cloc"] is not None
        if row["nonblank_text_lines"] != nonblank or row["source_counter_files"] != int(source) or row["source_code_lines"] != (file["cloc"]["code"] if source else 0):
            raise ValueError("Ownership exposure weights do not match census")
    if sum(ownership["coverage"].values()) != len(rows) or any(
        ownership["coverage"][status] != sum(row["status"] == status for row in rows)
        for status in ownership["coverage"]
    ):
        raise ValueError("Ownership coverage does not reconcile")
    for dimension in ("role", "purpose", "extension"):
        if ownership[f"by_{dimension}"] != aggregate_ownership(rows, dimension):
            raise ValueError(f"Ownership {dimension} aggregates do not reconcile")
    for dimension in ("agentic_documentation", "agentic_enablement"):
        if f"by_{dimension}" in ownership and ownership[f"by_{dimension}"] != aggregate_ownership(rows, dimension):
            raise ValueError(f"Ownership {dimension} aggregates do not reconcile")
