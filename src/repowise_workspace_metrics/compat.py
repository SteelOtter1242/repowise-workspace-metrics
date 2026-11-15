"""Guard the prototype's narrow dependency on non-plugin Repowise helpers."""

from __future__ import annotations

from collections.abc import Callable
from importlib.metadata import version
from typing import Any


def verify_version() -> None:
    installed = version("repowise")
    if installed.split("+", 1)[0] != "0.61.0":
        raise RuntimeError(
            f"workspace-metrics requires the verified Repowise 0.61.0 helper contract; "
            f"found {installed}. No substitute scope/test classifier was applied."
        )


def helpers() -> tuple[Callable[[dict[str, Any]], tuple[str, ...]], Callable[[str], bool]]:
    verify_version()
    try:
        from repowise.core.repo_config import analysis_paths
        from repowise.core.test_paths import is_test_related_path
    except ImportError as exc:
        raise RuntimeError(
            "Repowise scope/test helpers are unavailable; collection cannot proceed."
        ) from exc
    return analysis_paths, is_test_related_path


def ownership_helpers() -> tuple[Any, Any, Any]:
    verify_version()
    try:
        from repowise.server.services.owner_profile import owner_data
        from repowise.core.analysis.ownership_concentration import person_owner
        from repowise.core.analysis.knowledge_matrix import file_author_shares
    except ImportError as exc:
        raise RuntimeError("Repowise ownership helpers unavailable; no replacement model was applied") from exc
    return owner_data, person_owner, file_author_shares
