from __future__ import annotations

import copy
import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from repowise_workspace_metrics.census import collect, count_staged
from repowise_workspace_metrics.history import collect_history, validate_history
from repowise_workspace_metrics.purposes import LEGACY_PURPOSES, PURPOSE_IDS, VERSION


class HistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = Path.cwd() / ".history-test-workspace"
        cls.workspace.mkdir()
        cls.addClassCleanup(shutil.rmtree, cls.workspace)
        cls.repo = cls.workspace / "repo"
        cls.repo.mkdir()
        cls.git("init", "-q")
        cls.git("config", "user.name", "Fixture")
        cls.git("config", "user.email", "fixture@example.invalid")
        cls.write("src/a.py", "a = 1\n# original\n")
        cls.write("src/tests/test_a.py", "assert True\n")
        cls.write("src/deleted.py", "old = 1\n")
        cls.write("src/pathrole.py", "print('moves into tests')\n")
        cls.write("src/blob.bin", b"\0original")
        cls.write("outside/no.py", "ignored = 1\n")
        cls.baseline = cls.commit("baseline", "2025-11-01T10:00:00+00:00")
        cls.write("src/a.py", "a = 1\nb = 2\n# changed\n")
        cls.december = cls.commit("grow implementation", "2025-12-12T10:00:00+00:00")
        cls.git("mv", "src/a.py", "src/renamed.py")
        cls.rename = cls.commit("pure rename", "2026-02-10T10:00:00+00:00")
        # Side-branch edits enter once through the merge's first-parent patch.
        cls.git("checkout", "-qb", "side")
        cls.write("src/tests/test_side.py", "assert 1\nassert 2\n")
        cls.side = cls.commit("side test", "2026-03-10T10:00:00+00:00")
        cls.git("checkout", "-q", "master")
        cls.write("src/deleted.py", "old = 1\nold += 2\n")
        cls.main = cls.commit("main edit", "2026-03-11T10:00:00+00:00")
        cls.merge = cls.git("merge", "--no-ff", "-qm", "merge side", "side",
                            date="2026-03-12T10:00:00+00:00").decode().strip()
        cls.merge = cls.git("rev-parse", "HEAD").decode().strip()
        cls.git("mv", "src/pathrole.py", "src/tests/pathrole.py")
        cls.role_rename = cls.commit("rename changes role", "2026-04-12T10:00:00+00:00")
        (cls.repo / "src/deleted.py").unlink()
        cls.write("src/blob.bin", b"\0changed")
        cls.latest = cls.commit("delete and binary", "2026-11-09T10:00:00+00:00")
        cls.write(".repowise/config.yaml", "analysis_paths: [src]\nexclude_patterns: []\n")
        (cls.workspace / ".repowise-workspace.yaml").write_text(
            "version: 1\nrepos:\n- {alias: one, path: repo}\n- {alias: overlap, path: repo}\n"
        )
        cls.census = collect(cls.workspace)
        cls.census["generated_at"] = "2026-11-15T10:00:00+00:00"
        cls.counted_versions = []

        def measured(rows, stage, scratch):
            cls.counted_versions.extend((row["path"], row["blob"], row["mode"]) for row in rows)
            return count_staged(rows, stage, scratch)

        with patch("repowise_workspace_metrics.history.count_staged", side_effect=measured):
            cls.history = collect_history(cls.census)

    @classmethod
    def git(cls, *args, date=None):
        env = os.environ.copy()
        if date:
            env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
        return subprocess.run(["git", *args], cwd=cls.repo, env=env, check=True,
                              capture_output=True).stdout

    @classmethod
    def write(cls, path, data):
        target = cls.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data if isinstance(data, bytes) else data.encode())

    @classmethod
    def commit(cls, message, date):
        cls.git("add", ".")
        cls.git("commit", "-qm", message, date=date)
        return cls.git("rev-parse", "HEAD").decode().strip()

    def test_historical_content_counts_ratios_and_monthly_carry(self):
        points = self.history["points"]
        self.assertEqual(len(points), 12)
        self.assertEqual(points[0]["period"], "2025-12")
        self.assertEqual(points[-1]["period"], "2026-11")
        self.assertTrue(points[-1]["partial"])
        baseline = self.history["coverage"]["baseline"]["workspace"]
        self.assertEqual(baseline["by_role"]["production_source"]["cloc_code"], 3)
        self.assertEqual(points[0]["workspace"]["by_role"]["production_source"]["cloc_code"], 4)
        self.assertEqual(points[-1]["workspace"]["by_role"]["production_source"]["cloc_code"], 2)
        self.assertEqual(points[-1]["workspace"]["by_role"]["test_source"]["cloc_code"], 4)
        self.assertEqual(points[-1]["workspace"]["by_role"]["agent_guidance"]["files"], 0)
        self.assertEqual(points[-1]["workspace"]["by_role"]["product_intent_spec"]["physical_lines"], 0)
        # January's unchanged stock is measured carry-forward, not fabricated absence.
        self.assertEqual(points[0]["commits"], points[1]["commits"])
        self.assertEqual(points[1]["workspace"]["flow"]["changed_lines"], 0)
        self.assertEqual(points[1]["workspace"]["flow"]["commits"], 0)
        self.assertEqual(points[-1]["workspace"]["union"], self.census["union"])
        self.assertGreater(self.history["coverage"]["cached_file_version_hits"], 0)
        self.assertNotIn("history", self.census)

    def test_rename_merge_deletion_binary_and_workspace_deduplication(self):
        points = self.history["points"]
        rename = points[2]["workspace"]["flow"]
        self.assertEqual(rename["changed_lines"], 0)
        self.assertEqual(rename["files"][0]["change_type"], "pure_rename")
        self.assertEqual(rename["files"][0]["previous_path"], "src/a.py")
        march = points[3]["workspace"]["flow"]
        self.assertEqual(march["commits"], 2)
        self.assertEqual(march["additions"], 3)
        self.assertEqual(march["deletions"], 0)
        self.assertEqual({f["commit"] for f in march["files"]}, {self.main, self.merge})
        self.assertNotIn(self.side, {f["commit"] for f in march["files"]})
        april = points[4]["workspace"]["flow"]["files"][0]
        self.assertEqual(april["change_type"], "pure_rename")
        self.assertEqual(april["role"], "test_source")
        self.assertEqual(april["previous_classification"]["role"], "production_source")
        self.assertTrue(april["purpose_changed"])
        latest = points[-1]["workspace"]["flow"]
        self.assertEqual(latest["binary_files"], 1)
        deleted = next(f for f in latest["files"] if f["change_type"] == "deleted")
        self.assertEqual(deleted["role"], "production_source")
        self.assertEqual(deleted["deletions"], 2)
        for point in points:
            workspace = point["workspace"]["flow"]
            alias = point["aliases"]["one"]["flow"]
            self.assertEqual(workspace, alias)
            self.assertEqual(workspace["net"], workspace["additions"] - workspace["deletions"])
            self.assertTrue(all(f["aliases"] == ["one", "overlap"] for f in workspace["files"]))
        baseline_lines = self.history["coverage"]["baseline"]["workspace"]["union"]["physical_lines"]
        self.assertEqual(
            points[-1]["workspace"]["union"]["physical_lines"] - baseline_lines,
            sum(point["workspace"]["flow"]["net"] for point in points),
        )

    def test_immutable_counter_cache_avoids_latest_and_unchanged_recounts(self):
        self.assertTrue(self.counted_versions)
        self.assertEqual(len(self.counted_versions), len(set(self.counted_versions)))
        latest = {(row["path"], row["blob"], row["mode"]) for row in self.census["files"]}
        self.assertFalse(latest.intersection(self.counted_versions))

    def test_validation_and_changed_configuration_failures(self):
        validate_history(self.census)
        document = {**self.census, "history": self.history}
        validate_history(document)
        bad = copy.deepcopy(document)
        bad["history"]["points"][0]["workspace"]["flow"]["additions"] += 1
        with self.assertRaisesRegex(ValueError, "flow evidence"):
            validate_history(bad)
        config = self.repo / ".repowise/config.yaml"
        original = config.read_bytes()
        try:
            config.write_bytes(original + b"# changed\n")
            with self.assertRaisesRegex(ValueError, "configuration changed"):
                collect_history(self.census)
        finally:
            config.write_bytes(original)
        with self.assertRaisesRegex(ValueError, "positive integer"):
            collect_history(self.census, 0)

    def test_shallow_and_missing_history_fail_explicitly(self):
        from repowise_workspace_metrics import history
        original_run = history.run

        def shallow(args, cwd, **kwargs):
            if args == ["git", "rev-parse", "--is-shallow-repository"]:
                return b"true\n"
            return original_run(args, cwd, **kwargs)

        with patch.object(history, "run", side_effect=shallow):
            with self.assertRaisesRegex(ValueError, "shallow history"):
                collect_history(self.census)
        def missing(args, cwd, **kwargs):
            if args[:3] == ["git", "rev-list", "--objects"]:
                return b"?missing-object\n"
            return original_run(args, cwd, **kwargs)

        with patch.object(history, "run", side_effect=missing):
            with self.assertRaisesRegex(ValueError, "Missing reachable Git objects"):
                collect_history(self.census)
        with self.assertRaisesRegex(ValueError, "No reachable baseline"):
            collect_history(self.census, 24)


class PurposeHistoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workspace = Path.cwd() / ".purpose-history-test-workspace"
        cls.workspace.mkdir()
        cls.addClassCleanup(shutil.rmtree, cls.workspace)
        repo = cls.workspace / "repo"
        repo.mkdir()

        def git(*args, date=None):
            env = os.environ.copy()
            if date:
                env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
            return subprocess.run(["git", *args], cwd=repo, env=env, check=True,
                                  capture_output=True).stdout

        def write(path, text):
            target = repo / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)

        def commit(date):
            git("add", ".")
            git("commit", "-qm", date, date=date)

        git("init", "-q")
        git("config", "user.name", "Fixture")
        git("config", "user.email", "fixture@example.invalid")
        write("outside/README.md", "# Earlier reachable empty scope\n")
        commit("2025-12-10T10:00:00+00:00")
        files = {
            "src/app.py": "a = 1\nb = 2\n",
            "src/core.py": "core = 1\n",
            "src/tests/test_core.py": "assert True\n",
            "src/intent.md": "# Intent\nShip feature\n",
            "src/AGENTS.md": "# Agents\nFollow rules\n",
            "src/build.sh": "echo build\necho finished\n",
            "src/tool.py": "print('developer tooling')\n",
            "src/excluded/agents.md": "# Agents\nExcluded\n",
            "outside/intent.md": "# Intent\nOutside\n",
        }
        for path, text in files.items():
            write(path, text)
        commit("2026-01-10T10:00:00+00:00")
        git("mv", "src/app.py", "src/build.py")
        for path in ("src/intent.md", "src/AGENTS.md", "src/excluded/agents.md", "outside/intent.md"):
            write(path, files[path] + "Additional evidence\n")
        write("src/tool.py", files["src/tool.py"] + "print('more tooling')\n")
        write("src/build.sh", files["src/build.sh"] + "echo updated\n")
        commit("2026-02-10T10:00:00+00:00")
        write(".repowise/config.yaml",
              "analysis_paths: [src]\nexclude_patterns: [/src/excluded/]\n"
              "metrics_purpose_overrides:\n"
              "- {pattern: src/tool.py, purpose: development_enablement, reason: developer tooling, enablement: rejected}\n")
        (cls.workspace / ".repowise-workspace.yaml").write_text(
            "version: 1\nrepos:\n- {alias: one, path: repo}\n- {alias: overlap, path: repo}\n")
        cls.census = collect(cls.workspace)
        cls.census["generated_at"] = "2026-03-15T10:00:00+00:00"
        cls.history = collect_history(cls.census, 2)

    def test_frozen_purpose_stocks_agentic_evidence_and_source_operands(self):
        baseline = self.history["coverage"]["baseline"]["workspace"]
        latest = self.history["points"][-1]["workspace"]
        self.assertEqual(self.history["method"]["purpose_classification"], VERSION)
        self.assertEqual(baseline["source_stock"]["implementation"]["cloc_code"], 3)
        self.assertEqual(latest["source_stock"]["implementation"]["cloc_code"], 1)
        self.assertEqual(latest["source_stock"]["testing"]["cloc_code"], 1)
        # A SOURCE-extension tooling override remains a source role, but not an I:T operand.
        self.assertEqual(latest["by_role"]["production_source"]["cloc_code"], 3)
        self.assertEqual(latest["counter_coverage"]["source_files"], 2)
        self.assertEqual(latest["by_purpose"]["product_definition"]["files"], 1)
        self.assertEqual(latest["by_purpose"]["development_enablement"]["files"], 4)
        self.assertEqual(latest["agentic"]["documentation"]["candidate"]["files"], 1)
        self.assertEqual(latest["agentic"]["enablement"]["confirmed"]["files"], 1)
        self.assertEqual(latest["agentic"]["non_agentic_enablement"]["files"], 2)
        self.assertEqual(set(latest["by_purpose"]), set(PURPOSE_IDS))
        self.assertEqual(self.history["points"][1]["workspace"]["flow"]["changed_lines"], 0)
        validate_history({**self.census, "history": self.history})

    def test_changed_file_purpose_agentic_flow_rename_and_scope_invariance(self):
        flow = self.history["points"][0]["workspace"]["flow"]
        rows = {row["path"]: row for row in flow["files"]}
        self.assertEqual(set(rows), {"src/build.py", "src/build.sh", "src/intent.md", "src/AGENTS.md", "src/tool.py"})
        self.assertEqual(rows["src/build.py"]["change_type"], "pure_rename")
        self.assertEqual(rows["src/build.py"]["purpose"], "development_enablement")
        self.assertEqual(rows["src/build.py"]["previous_classification"]["purpose"], "product_implementation")
        self.assertTrue(rows["src/build.py"]["purpose_changed"])
        self.assertEqual(rows["src/tool.py"]["purpose_rule"], "repository_override")
        self.assertEqual(rows["src/tool.py"]["agentic"]["enablement"]["status"], "rejected")
        self.assertEqual(rows["src/AGENTS.md"]["harness_load_status"], "unknown")
        self.assertEqual(rows["src/AGENTS.md"]["origin_traits"], [])
        self.assertEqual(flow["by_purpose"]["product_definition"]["additions"], 1)
        self.assertEqual(flow["by_agentic"]["documentation"]["candidate"]["additions"], 1)
        self.assertEqual(flow["by_agentic"]["enablement"]["confirmed"]["additions"], 1)
        self.assertEqual(flow["by_agentic"]["non_agentic_enablement"]["additions"], 1)
        self.assertEqual(flow, self.history["points"][0]["aliases"]["overlap"]["flow"])
        bad = copy.deepcopy({**self.census, "history": self.history})
        bad["history"]["points"][0]["workspace"]["flow"]["by_agentic"]["enablement"]["confirmed"]["additions"] += 1
        with self.assertRaisesRegex(ValueError, "agentic flow"):
            validate_history(bad)

    def test_reachable_empty_scope_has_measured_zero_agentic_stocks(self):
        history = collect_history(self.census, 3)
        baseline = history["coverage"]["baseline"]["workspace"]
        self.assertEqual(baseline["union"]["files"], 0)
        self.assertEqual(baseline["agentic"]["enablement"]["unknown"]["files"], 0)
        self.assertEqual(baseline["agentic"]["non_agentic_enablement"]["files"], 0)
        self.assertEqual(baseline["source_stock"]["implementation"]["cloc_code"], 0)
        self.assertEqual(history["points"][0]["workspace"]["by_purpose"]["product_definition"]["files"], 1)

    def test_legacy_saved_history_validates_but_cannot_be_recollected(self):
        legacy = copy.deepcopy({**self.census, "history": self.history})
        legacy["method"]["classification"] = "roles-v1"
        legacy["method"].pop("purpose_classification")
        for row in legacy["files"]:
            for key in ("purpose", "purpose_rule", "purpose_confidence", "purpose_override_reason",
                        "agentic", "enablement_kind", "harness_load_status"):
                row.pop(key, None)
        method = legacy["history"]["method"]
        method["classification"] = "roles-v1"
        method.pop("purpose_classification")
        method.pop("purpose_overrides")
        views = [legacy["history"]["coverage"]["baseline"]["workspace"]]
        for point in legacy["history"]["points"]:
            views.extend([point["workspace"], *point["aliases"].values()])
        for view in views:
            flow = view["flow"]
            for row in flow["files"]:
                row["purpose"] = LEGACY_PURPOSES[row["role"]]
            flow["by_purpose"] = {
                purpose: {
                    key: sum(row[key] for row in flow["files"] if row["purpose"] == purpose)
                    for key in ("additions", "deletions")
                } for purpose in set(LEGACY_PURPOSES.values())
            }
            for counts in flow["by_purpose"].values():
                counts.update(net=counts["additions"] - counts["deletions"],
                              changed_lines=counts["additions"] + counts["deletions"])
        validate_history(legacy)
        with self.assertRaisesRegex(ValueError, "legacy role classifiers"):
            collect_history(legacy, 2)
        old_purpose = copy.deepcopy(self.census)
        old_purpose["method"].pop("purpose_classification")
        with self.assertRaisesRegex(ValueError, "legacy purpose classifiers"):
            collect_history(old_purpose, 2)


if __name__ == "__main__":
    unittest.main()
