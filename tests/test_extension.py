from __future__ import annotations

import copy
import asyncio
import hashlib
import json
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace

import pathspec
from click.testing import CliRunner

from repowise_workspace_metrics.census import classify, collect, count_staged, line_counts, scope_reason, validate
from repowise_workspace_metrics.cli import workspace_metrics, write_output
from repowise_workspace_metrics.compat import helpers
from repowise_workspace_metrics.render import render_html
from repowise_workspace_metrics.ownership import collect_api, collect_section, enrich, identity_label, validate_ownership
from repowise_workspace_metrics.charts import sections
from repowise_workspace_metrics.purposes import evidence, validate_overrides
from repowise_workspace_metrics.artefacts import collect_artefacts, validate_artefacts


class CensusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.workspace = Path(cls.temp.name)
        cls.repo = cls.workspace / "repo"
        cls.repo.mkdir()

        def git(*args: str) -> bytes:
            return subprocess.run(["git", *args], cwd=cls.repo, check=True, capture_output=True).stdout

        git("init", "-q")
        files = {
            "src/a.py": b'# standalone\ns = "# not a comment"  # inline\n\n',
            "src/duplicate.py": b'# standalone\ns = "# not a comment"  # inline\n\n',
            "src/tests/test_a.py": b"assert True\n",
            "src/tests/fixtures/design.def": b"VERSION 5.8 ;\nDESIGN example ;\n",
            "src/unknown.custom": b"unclassified\n",
            "src/empty.py": b"",
            "src/blob.bin": b"\x00binary",
            "src/excluded/hidden.py": b"print('excluded')\n",
            "src-long/not-in-scope.py": b"print('outside')\n",
        }
        for name, data in files.items():
            path = cls.repo / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        os.symlink("/does/not/exist/outside", cls.repo / "src/link.py")
        git("add", ".")
        git("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Fixture")
        # All sources are tracked, but the configuration is deliberately local.
        config = cls.repo / ".repowise/config.yaml"
        config.parent.mkdir()
        config.write_text("analysis_paths: [src]\nexclude_patterns: [/src/excluded/]\n")
        cls.index_sentinel = config.parent / "repowise.db"
        cls.index_sentinel.write_bytes(b"existing index must not be opened or changed")
        cls.membership = cls.workspace / ".repowise-workspace.yaml"
        cls.membership.write_text(
            "version: 1\ndefault_repo: first\nrepos:\n"
            "  - {alias: first, path: repo}\n  - {alias: overlap, path: repo}\n"
        )
        cls.head = git("rev-parse", "HEAD")
        cls.config_hash = hashlib.sha256(config.read_bytes()).hexdigest()
        cls.document = collect(cls.workspace)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    def test_committed_scope_overlap_and_line_semantics(self) -> None:
        doc = self.document
        self.assertEqual(doc["union"]["files"], 8)
        self.assertEqual(doc["overlapping_alias_paths"], 8)
        self.assertEqual([a["scope_files"] for a in doc["aliases"]], [8, 8])
        self.assertEqual(doc["union"]["symlinks"], 1)
        self.assertEqual(doc["union"]["binary_files"], 1)
        rows = {r["path"]: r for r in doc["files"]}
        self.assertEqual(rows["src/a.py"]["cloc"]["code"], 1)
        self.assertEqual(rows["src/a.py"]["cloc"]["comment"], 1)
        self.assertEqual(rows["src/a.py"]["cloc"]["blank"], 1)
        self.assertEqual(rows["src/a.py"]["cloc"], rows["src/duplicate.py"]["cloc"])
        self.assertEqual(rows["src/tests/fixtures/design.def"]["role"], "test_fixture")
        self.assertEqual(rows["src/tests/test_a.py"]["role"], "test_source")
        self.assertEqual(rows["src/link.py"]["counter_status"], "symlink_not_followed")
        self.assertEqual(rows["src/unknown.custom"]["physical_lines"], 1)
        self.assertNotEqual(rows["src/unknown.custom"]["counter_status"], "counted")
        self.assertIsNone(rows["src/empty.py"]["cloc"])
        config = self.repo / ".repowise/config.yaml"
        self.assertEqual(hashlib.sha256(config.read_bytes()).hexdigest(), self.config_hash)
        self.assertEqual(subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=self.repo), self.head)
        self.assertEqual(self.index_sentinel.read_bytes(), b"existing index must not be opened or changed")

    def test_dirty_content_is_not_collected_and_reports_only_read_input(self) -> None:
        source = self.repo / "src/a.py"
        original = source.read_bytes()
        try:
            source.write_bytes(b"print('dirty working file')\n")
            document = collect(self.workspace)
            self.assertEqual(document["union"], self.document["union"])
            self.assertTrue(document["aliases"][0]["dirty_tracked"])
        finally:
            source.write_bytes(original)
        with tempfile.TemporaryDirectory() as folder:
            input_path, out = Path(folder) / "census.json", Path(folder) / "report.html"
            input_path.write_text(json.dumps(self.document))
            with patch("repowise_workspace_metrics.cli.collect", side_effect=AssertionError("Report must not collect")):
                result = CliRunner().invoke(workspace_metrics, [
                    "report", "--input", str(input_path), "--out", str(out),
                ])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("Real census evidence only", out.read_text())
            again = CliRunner().invoke(workspace_metrics, [
                "report", "--input", str(input_path), "--out", str(out),
            ])
            self.assertNotEqual(again.exit_code, 0)

    def test_matching_ambiguous_playbooks_and_physical_lines(self) -> None:
        _, is_test = helpers()
        self.assertEqual(line_counts(b""), (0, 0))
        self.assertEqual(line_counts(b"a\n \t\nlast"), (3, 1))
        patterns = pathspec.PathSpec.from_lines("gitwildmatch", ["/src/excluded/"])
        self.assertEqual(scope_reason("src-long/a.py", ("src",), patterns), "outside_analysis_paths")
        self.assertEqual(scope_reason("src/excluded/a.py", ("src",), patterns), "configured_exclusion")
        for name in ("Makefile.test", "Makefile_lef", "Jenkinsfile"):
            self.assertEqual(classify(name, "100644", b"content", is_test)[0], "configuration_build_ci")
        self.assertEqual(classify("docs/playbook.md", "100644", b"workflow", is_test)[0], "unknown")
        self.assertEqual(classify("lib/skill/code.il", "100644", b"procedure()", is_test)[0], "production_source")
        with patch("repowise_workspace_metrics.compat.version", return_value="0.62.0"):
            with self.assertRaisesRegex(RuntimeError, "No substitute"):
                helpers()

    def test_unreported_counter_gap_retains_physical_lines_and_warns(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            repo = workspace / "repo"
            repo.mkdir()
            (repo / "route.inc").write_text("# Forward a route\nproxy_set_header X-Prefix /sermons;\n\n")
            (repo / "app.py").write_text("print('hello')\n")
            for args in (("init", "-q"), ("add", "."), (
                "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                "commit", "-qm", "Counter gap fixture",
            )):
                subprocess.run(
                    ["git", *args], cwd=repo, check=True, capture_output=True,
                    env={**os.environ, "GIT_AUTHOR_DATE": "2025-11-01T10:00:00+00:00",
                         "GIT_COMMITTER_DATE": "2025-11-01T10:00:00+00:00"},
                )
            (workspace / ".repowise-workspace.yaml").write_text(json.dumps({
                "version": 1, "repos": [{"alias": "pilot", "path": str(repo)}],
            }))
            run = subprocess.run

            def silent_omission(args, **kwargs):
                if args[0] == "cloc" and "--by-file" in args:
                    listing = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--list-file=")))
                    ignored = Path(next(arg.split("=", 1)[1] for arg in args if arg.startswith("--ignored=")))
                    ignored.write_text("[{}]")
                    counted = {
                        path: {"code": 1, "comment": 0, "blank": 0, "language": "Python"}
                        for path in listing.read_text().splitlines() if path.endswith("/app.py")
                    }
                    return subprocess.CompletedProcess(args, 0, json.dumps(counted), "")
                return run(args, **kwargs)

            # Reproduce cloc 1.98's response even when a newer cloc recognizes .inc.
            with patch("repowise_workspace_metrics.census.subprocess.run", side_effect=silent_omission):
                result = CliRunner().invoke(workspace_metrics, [
                    "generate", str(workspace), "--workspace", "--months", "2",
                    "--evidence-out", str(workspace / "snapshot.json"),
                    "--out", str(workspace / "snapshot.html"),
                ])
            self.assertEqual(result.exit_code, 0, result.output)
            self.assertIn("Warning: Counter coverage incomplete", result.output)
            document = json.loads((workspace / "snapshot.json").read_text())
            validate(document)
            row = next(row for row in document["files"] if row["path"] == "route.inc")
            self.assertEqual((row["physical_lines"], row["blank_lines"]), (3, 1))
            self.assertIsNone(row["cloc"])
            self.assertEqual(row["counter_status"], "unreported_cloc_omission")
            self.assertEqual(document["cloc_omission_reasons"]["unreported_cloc_omission"], 1)
            self.assertEqual(document["union"]["cloc_code"], 1)
            for point in document["history"]["points"]:
                self.assertEqual(
                    point["workspace"]["counter_coverage"]["omission_reasons"]["unreported_cloc_omission"], 1,
                )
            rendered = (workspace / "snapshot.html").read_text()
            self.assertIn("UNMEASURED", rendered)
            self.assertIn("2 historical periods also have counter gaps", rendered)
            self.assertLess(rendered.index('id="counter-warning"'), rendered.index('<details'))
            self.assertIn('type="button" id="show-coverage"', rendered)
            self.assertNotIn('href="#coverage-details"', rendered)

    def test_counter_failures_and_inconsistent_totals_still_abort(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            scratch = Path(folder)
            row = {
                "stage_path": "app.py", "path": "app.py", "mode": "100644",
                "binary": False, "cloc": None, "physical_lines": 1,
            }
            failed = SimpleNamespace(returncode=1, stderr="counter failure", stdout="")
            inconsistent = SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
                "app.py": {"code": 2, "comment": 0, "blank": 0},
            }))
            for output, error, message in (
                (failed, RuntimeError, "cloc failed"),
                (inconsistent, ValueError, "Counter/physical line discrepancy"),
            ):
                with self.subTest(message=message):
                    with patch("repowise_workspace_metrics.census.subprocess.run", return_value=output):
                        with self.assertRaisesRegex(error, message):
                            count_staged([copy.deepcopy(row)], scratch, scratch)

    def test_corrupt_evidence_and_html_injection_are_not_success_shaped(self) -> None:
        bad = copy.deepcopy(self.document)
        bad["union"]["cloc_code"] += 1
        with self.assertRaisesRegex(ValueError, "reconcile"):
            validate(bad)
        bad = copy.deepcopy(self.document)
        bad["cloc_omission_reasons"] = {}
        with self.assertRaisesRegex(ValueError, "omission"):
            validate(bad)
        malicious = copy.deepcopy(self.document)
        malicious["workspace"] = "</p><script>alert(1)</script>"
        report = render_html(malicious)
        self.assertNotIn("<script>alert(1)", report)
        self.assertIn("&lt;script&gt;alert(1)", report)
        self.assertIn("script-src 'sha256-", report)
        with self.assertRaisesRegex(ValueError, "requires a census"):
            render_html({"sections": []})
        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder) / ".repowise"
            target.mkdir()
            with self.assertRaises(ValueError):
                write_output(target / "config.yaml", "bad")

    def test_extra_ignores_fail_explicitly(self) -> None:
        ignored = self.repo / "src/.repowiseIgnore"
        try:
            ignored.write_text("a.py\n")
            with self.assertRaisesRegex(ValueError, "repowiseIgnore requires scope support"):
                collect(self.workspace)
        finally:
            ignored.unlink()

    def test_chart_first_report_keeps_evidence_collapsed_and_uses_real_source_counts(self) -> None:
        from html.parser import HTMLParser

        class EvidenceParser(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.details = []
                self.chart_containers = set()
                self.payload = ""
                self.reading_payload = False

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                values = dict(attrs)
                if tag == "details":
                    self.details.append(values)
                if tag == "div" and values.get("class") == "chart-scroll":
                    self.chart_containers.add(values.get("id"))
                if tag == "script" and values.get("id") == "chart-evidence":
                    self.reading_payload = True

            def handle_endtag(self, tag: str) -> None:
                if tag == "script":
                    self.reading_payload = False

            def handle_data(self, data: str) -> None:
                if self.reading_payload:
                    self.payload += data

        parser = EvidenceParser()
        report = render_html(self.document)
        parser.feed(report)
        payload = json.loads(parser.payload)
        workspace = payload["current"]["workspace"]
        self.assertEqual(workspace["by_role"]["production_source"]["cloc_code"], 2)
        self.assertEqual(workspace["by_role"]["test_source"]["cloc_code"], 1)
        self.assertEqual(workspace["by_role"]["test_fixture"]["cloc_code"], 2)
        self.assertEqual(payload["current"]["aliases"]["first"], workspace)
        self.assertIsNone(payload["history"])
        self.assertEqual(len(parser.details), 7)
        self.assertTrue(all("open" not in detail for detail in parser.details))
        self.assertTrue({"combined-chart", "ratio-chart", "agentic-chart"} <= parser.chart_containers)
        self.assertLess(report.index('id="combined-chart"'), report.index('id="cross-tab"'))
        self.assertLess(report.index('id="owner-chart"'), report.index('id="ownership-table"') if 'id="ownership-table"' in report else len(report))
        malicious = copy.deepcopy(self.document)
        malicious["aliases"][0]["alias"] = "</script><img src=x>"
        for row in malicious["files"]:
            row["aliases"] = ["</script><img src=x>" if alias == "first" else alias for alias in row["aliases"]]
        encoded = sections(malicious)
        self.assertNotIn("</script><img", encoded)
        self.assertIn("\\u003c/script\\u003e", encoded)

    def test_summary_ratio_uses_nonblank_lines_for_three_purposes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            for name, files in {
                "first": {
                    "src/app.py": "# comment\none = 1\n \nother = 2\n",
                    "tests/test_app.py": "# comment\nassert True\n\n",
                    "docs/vision.md": "# Vision\n\nProduct intent\n",
                    "tests/fixtures/example.py": "fixture\nfixture\n",
                },
                "second": {"src/other.py": "value = 1\n"},
            }.items():
                repo = workspace / name
                repo.mkdir()
                subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
                for path, content in files.items():
                    target = repo / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(content)
                subprocess.run(["git", "add", "."], cwd=repo, check=True)
                subprocess.run(
                    ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                     "commit", "-qm", "Fixture"], cwd=repo, check=True,
                )
            (workspace / ".repowise-workspace.yaml").write_text(
                "version: 1\ndefault_repo: first\nrepos:\n"
                "  - {alias: first, path: first}\n  - {alias: second, path: second}\n"
            )
            report = render_html(collect(workspace))
            self.assertIn('<strong id="card-impl">4</strong>', report)
            self.assertIn('<strong id="card-tests">2</strong>', report)
            self.assertIn('<strong id="card-definition">2</strong>', report)
            self.assertIn('<strong id="card-ratio">2.00 : 1 : 1.00</strong>', report)

            repo = workspace / "first"
            (repo / "tests/test_app.py").write_text("assert True\n" * 300)
            subprocess.run(["git", "add", "."], cwd=repo, check=True)
            subprocess.run(
                ["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", "More tests"], cwd=repo, check=True,
            )
            report = render_html(collect(workspace))
            self.assertIn('<strong id="card-ratio">0.01 : 1 : &lt;0.01</strong>', report)

            (workspace / ".repowise-workspace.yaml").write_text(
                "version: 1\ndefault_repo: second\nrepos:\n  - {alias: second, path: second}\n"
            )
            report = render_html(collect(workspace))
            self.assertIn('<strong id="card-ratio">Undefined (no testing nonblank lines)</strong>', report)

    def test_agentic_purposes_distinguish_instructions_candidates_and_false_positives(self) -> None:
        _, is_test = helpers()
        cases = [
            ("docs/vision.md", "product_definition", "candidate", "unknown"),
            ("docs/feature-login.md", "product_definition", "candidate", "unknown"),
            ("docs/agent-plan.md", "mixed_unknown", "candidate", "candidate"),
            ("docs/spectre.md", "general_documentation", "rejected", "unknown"),
            (".github/prompts/review.prompt.md", "development_enablement", "unknown", "confirmed"),
            ("scripts/build.sh", "development_enablement", "unknown", "unknown"),
            (".gitlab-ci.yml", "development_enablement", "unknown", "unknown"),
            (".gitlab.yml", "development_enablement", "unknown", "unknown"),
            ("runtime/config.yml", "runtime_configuration_operations", "unknown", "unknown"),
            ("lib/skill/code.il", "product_implementation", "unknown", "unknown"),
            ("tests/fixtures/AGENTS.md", "data_fixtures_design", "unknown", "candidate"),
            ("templates/reviewer.agent.md", "mixed_unknown", "unknown", "candidate"),
            (".agents/skills/reviewer/scripts/check.py", "development_enablement", "unknown", "confirmed"),
            (".claude/skills/reviewer/engine/check.py", "development_enablement", "unknown", "confirmed"),
            (".agents/skills/reviewer/tests/test_check.py", "development_enablement", "unknown", "confirmed"),
            (".agents/skills/reviewer/vision.md", "development_enablement", "candidate", "confirmed"),
            ("tests/fixtures/.agents/skills/reviewer/check.py", "data_fixtures_design", "unknown", "candidate"),
            ("src/skills/reviewer/check.py", "product_implementation", "unknown", "unknown"),
        ]
        for path, purpose, doc_status, enable_status in cases:
            with self.subTest(path=path):
                role, _, _ = classify(path, "100644", b"content\n", is_test)
                found = evidence(path, role)
                self.assertEqual(found["purpose"], purpose)
                self.assertEqual(found["agentic"]["documentation"]["status"], doc_status)
                self.assertEqual(found["agentic"]["enablement"]["status"], enable_status)
                self.assertEqual(found["harness_load_status"], "unknown")
        with self.assertRaisesRegex(ValueError, "disagrees"):
            validate_overrides([{"pattern": "*.md", "purpose": "general_documentation", "reason": "review", "enablement": "confirmed"}])
        overrides = validate_overrides([{"pattern": "plan.md", "purpose": "development_enablement", "reason": "Reviewed tooling plan"}])
        self.assertEqual(evidence("plan.md", "product_intent_spec", overrides=overrides)["purpose"], "development_enablement")
        previous = evidence(".agents/skills/reviewer/check.py", "production_source", classifier_version="purposes-v2")
        self.assertEqual(previous["purpose"], "product_implementation")
        self.assertEqual(previous["agentic"]["enablement"]["status"], "unknown")

    def test_agentic_inventory_and_generate_preserve_baseline_scope_and_ratio(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder)
            repo = workspace / "repo"
            repo.mkdir()
            files = {
                "src/product.py": "print('product')\n",
                "src/tests/test_product.py": "assert True\n",
                "src/helper.py": "print('developer helper')\n",
                "src/.agents/skills/reviewer/check.py": "print('skill helper')\n",
                "src/build.sh": "#!/bin/sh\necho build\n",
                "src/AGENTS.md": "Review repository changes.\n",
                "src/feature-login.md": "Desired login behavior.\n",
                "src/agent-plan.md": "Ambiguous agent/product plan.\n",
                "src/spectre.md": "A spectre reference.\n",
                "src/tests/fixtures/build.sh": "#!/bin/sh\necho fixture\n",
                "src/excluded/vision.md": "Excluded product vision.\n",
                "docs/vision.md": "Outside-module product vision.\n",
                ".agents/skills/reviewer/check.py": "print('skill support')\n",
            }
            for path, text in files.items():
                target = repo / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text)
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "Agentic fixture"], cwd=repo, check=True, capture_output=True)
            config = repo / ".repowise/config.yaml"
            config.parent.mkdir()
            config.write_text(
                "analysis_paths: [src]\nexclude_patterns: [/src/excluded/]\n"
                "metrics_purpose_overrides:\n"
                "  - {pattern: src/helper.py, purpose: development_enablement, reason: Reviewed developer utility}\n"
            )
            (workspace / ".repowise-workspace.yaml").write_text(
                "version: 1\nrepos:\n  - {alias: first, path: repo}\n  - {alias: overlap, path: repo}\n"
            )
            baseline = collect(workspace)
            self.assertEqual(baseline["union"]["files"], 10)
            self.assertEqual(baseline["by_role"]["production_source"]["cloc_code"], 3)
            self.assertEqual(baseline["source_stock"]["implementation"]["cloc_code"], 1)
            self.assertEqual(baseline["source_stock"]["testing"]["cloc_code"], 1)
            self.assertEqual(baseline["agentic"]["documentation"]["candidate"]["files"], 2)
            self.assertEqual(baseline["agentic"]["enablement"]["confirmed"]["files"], 2)
            inventory = collect_artefacts(baseline)
            self.assertEqual(inventory["union"], baseline["union"])
            self.assertEqual(inventory["files"], baseline["files"])
            self.assertEqual(inventory["artefacts"]["workspace"]["by_scope"]["outside_analysis_paths"]["files"], 2)
            self.assertEqual(inventory["artefacts"]["workspace"]["by_scope"]["configured_exclusion"]["files"], 1)
            self.assertEqual(inventory["artefacts"]["workspace"]["agentic"]["documentation"]["candidate"]["files"], 4)
            self.assertEqual(len(inventory["artefacts"]["files"]), 11)
            skill = next(row for row in inventory["artefacts"]["files"] if row["path"] == ".agents/skills/reviewer/check.py")
            self.assertEqual(skill["purpose"], "development_enablement")
            self.assertEqual(skill["agentic"]["enablement"]["status"], "confirmed")
            fixture = next(row for row in inventory["artefacts"]["files"] if row["path"] == "src/tests/fixtures/build.sh")
            self.assertEqual(fixture["purpose"], "data_fixtures_design")
            self.assertIsNone(fixture["enablement_kind"])
            self.assertEqual(fixture["build_discovery_kind"], "build_ci")
            self.assertTrue(all(row["aliases"] == ["first", "overlap"] for row in inventory["artefacts"]["files"]))
            broken = copy.deepcopy(inventory)
            broken["artefacts"]["files"][0]["scope_status"] = "wrong"
            with self.assertRaisesRegex(ValueError, "reconcile"):
                validate_artefacts(broken)
            out, saved = workspace / "report.html", workspace / "report.json"
            result = CliRunner().invoke(workspace_metrics, [
                "generate", str(workspace), "--workspace", "--months", "0",
                "--include-artefacts", "--evidence-out", str(saved), "--out", str(out),
            ])
            self.assertEqual(result.exit_code, 0, result.output)
            measured = json.loads(saved.read_text())
            self.assertEqual(measured["source_stock"]["implementation"]["cloc_code"], 1)
            self.assertIn("agentic-chart", out.read_text())
            self.assertEqual(config.read_text().splitlines()[0], "analysis_paths: [src]")
            bad = CliRunner().invoke(workspace_metrics, [
                "generate", str(workspace), "--workspace", "--include-names",
                "--evidence-out", str(workspace / "bad.json"), "--out", str(workspace / "bad.html"),
            ])
            self.assertNotEqual(bad.exit_code, 0)
            self.assertFalse((workspace / "bad.json").exists())

    def ownership_evidence(self) -> dict:
        head = self.document["aliases"][0]["head"]
        data = {
            "schema_version": 1, "alias": "first", "head_commit": head,
            "files": [
                {"path": "src/a.py", "primary_owner": {"key": "alice", "name": "Alice"},
                 "contributors": [{"key": "alice", "name": "Alice", "share": 0.8}],
                 "contributor_residual_share": 0.2},
                {"path": "src/tests/test_a.py", "primary_owner": None,
                 "contributors": [], "contributor_residual_share": None},
                {"path": "src/tests/fixtures/design.def", "primary_owner": {"key": "bob", "name": "Bob"},
                 "contributors": [{"key": "bob", "name": "Bob", "share": 1.0}],
                 "contributor_residual_share": 0.0},
            ],
        }
        return {
            "schema_version": 1, "kind": "assessment", "generated_at": "2026-11-15T00:00:00Z",
            "sections": [{"id": "workspace-ownership", "repo": "first",
                          "checks": [{"basis": "measured", "data": data}]}],
        }

    def test_public_api_export_preserves_rounded_shares_and_opaque_identities(self) -> None:
        repos = [
            {"id": alias["alias"], "local_path": alias["path"], "head_commit": alias["head"]}
            for alias in self.document["aliases"][:1]
        ]
        matrix = {
            "authors": [
                {"key": name, "name": name, "email": None, "last_commit_at": None, "active": True}
                for name in ("fixture-alice", "fixture-bob", "fixture-carol")
            ],
            "dirs": ["src"],
            "files": [[0, "a.py", 1, None, False, 3, 0, [[0, 0.334], [1, 0.334], [2, 0.333]], True]],
        }
        responses = [{"status": "healthy", "version": "0.61.0"}, repos, matrix, matrix, repos]
        with patch("repowise_workspace_metrics.ownership.build_opener") as factory:
            factory.return_value.open.side_effect = [io.BytesIO(json.dumps(data).encode()) for data in responses]
            evidence = collect_api(self.document, "http://127.0.0.1:9932")
        self.assertNotIn("fixture-alice", json.dumps(evidence))
        joined = enrich(self.document, evidence)
        row = next(row for row in joined["ownership"]["files"] if row["path"] == "src/a.py")
        self.assertEqual([author["share"] for author in row["contributors"]], [0.334, 0.334, 0.333])
        self.assertEqual(row["contributor_residual_share"], 0)
        self.assertEqual(row["contributor_share_precision"], 0.001)
        self.assertEqual(joined["ownership"]["coverage"]["owned"], 1)
        self.assertEqual(joined["union"], self.document["union"])
        self.assertIn("not normalized to 100%", render_html(joined))
        raw = copy.deepcopy(evidence)
        for section in raw["sections"]:
            del section["checks"][0]["data"]["contributor_share_precision"]
        with self.assertRaisesRegex(ValueError, "reconcile"):
            enrich(self.document, raw)
        excessive = copy.deepcopy(evidence)
        excessive["sections"][0]["checks"][0]["data"]["files"][0]["contributors"][0]["share"] = 0.4
        with self.assertRaisesRegex(ValueError, "reconcile"):
            enrich(self.document, excessive)

    def test_api_export_rejects_wrong_snapshots_and_nonlocal_origins(self) -> None:
        repos = [
            {"id": alias["alias"], "local_path": alias["path"], "head_commit": alias["head"]}
            for alias in self.document["aliases"][:1]
        ]
        changed = copy.deepcopy(repos)
        changed[0]["head_commit"] = "different"
        health = {"status": "healthy", "version": "0.61.0"}
        matrix = {"authors": [], "dirs": [], "files": []}
        for responses in ([health, changed], [health, repos, matrix, matrix, changed]):
            with patch("repowise_workspace_metrics.ownership.build_opener") as factory:
                factory.return_value.open.side_effect = [io.BytesIO(json.dumps(data).encode()) for data in responses]
                with self.assertRaisesRegex(ValueError, "snapshot"):
                    collect_api(self.document, "http://127.0.0.1:9932")
        with patch("repowise_workspace_metrics.ownership.build_opener") as factory:
            for origin in ("https://example.invalid", "http://user:secret@localhost:9932", "http://localhost:9932/redirect"):
                with self.assertRaisesRegex(ValueError, "loopback"):
                    collect_api(self.document, origin)
            factory.assert_not_called()

    def test_named_api_export_reuses_mapped_names_without_changing_identity_or_shares(self) -> None:
        repos = [
            {"id": alias["alias"], "local_path": alias["path"], "head_commit": alias["head"]}
            for alias in self.document["aliases"][:1]
        ]
        matrix = {
            "authors": [{
                "key": "fixture@example.invalid", "email": "fixture@example.invalid",
                "name": "Correctly Mapped Name", "last_commit_at": None, "active": True,
            }],
            "dirs": ["src"],
            "files": [[0, "a.py", 1, None, False, 1, 0, [[0, 1.0]], True]],
        }

        def export(include_names: bool) -> dict:
            responses = [{"status": "healthy", "version": "0.61.0"}, repos, matrix, matrix, repos]
            with patch("repowise_workspace_metrics.ownership.build_opener") as factory:
                factory.return_value.open.side_effect = [io.BytesIO(json.dumps(data).encode()) for data in responses]
                return collect_api(self.document, "http://127.0.0.1:9932", include_names=include_names)

        opaque, named = export(False), export(True)
        before = opaque["sections"][0]["checks"][0]["data"]["files"][0]
        after = named["sections"][0]["checks"][0]["data"]["files"][0]
        self.assertEqual(after["primary_owner"]["key"], before["primary_owner"]["key"])
        self.assertEqual(after["primary_owner"]["name"], matrix["authors"][0]["name"])
        self.assertEqual(after["contributors"][0]["share"], before["contributors"][0]["share"])
        self.assertEqual(after["contributors"][0]["key"], before["contributors"][0]["key"])
        self.assertNotIn("Correctly Mapped Name", json.dumps(opaque))
        self.assertNotIn("fixture@example.invalid", json.dumps(named))
        joined = enrich(self.document, named)
        self.assertEqual(joined["union"], self.document["union"])
        self.assertEqual(joined["ownership"]["coverage"], enrich(self.document, opaque)["ownership"]["coverage"])
        self.assertIn("Correctly Mapped Name", render_html(joined))
        self.assertIn("mapped_display_names", joined["ownership"]["identity_label_modes"])
        matrix["authors"][0]["name"] = " "
        with self.assertRaisesRegex(ValueError, "no mapped display name"):
            export(True)

    def test_ownership_join_preserves_population_gaps_and_fixture_ratio(self) -> None:
        evidence = self.ownership_evidence()
        partial = enrich(self.document, evidence)
        self.assertEqual(partial["union"], self.document["union"])
        self.assertEqual(partial["by_role"], self.document["by_role"])
        self.assertEqual(partial["ownership"]["coverage"], {
            "owned": 2, "no_primary_human_owner": 1, "no_ownership_metadata": 0, "missing_alias_export": 5,
        })
        self.assertEqual(partial["ownership"]["missing_alias_exports"], ["overlap"])
        duplicate = copy.deepcopy(evidence["sections"][0])
        duplicate["repo"] = duplicate["checks"][0]["data"]["alias"] = "overlap"
        evidence["sections"].append(duplicate)
        joined = enrich(self.document, evidence)
        self.assertEqual(len(joined["ownership"]["files"]), 8)
        self.assertEqual(joined["ownership"]["coverage"]["no_ownership_metadata"], 5)
        fixture = next(row for row in joined["ownership"]["files"] if row["path"].endswith("design.def"))
        self.assertEqual(fixture["purpose"], "data_fixtures_design")
        self.assertEqual(fixture["source_code_lines"], 0)
        owned = next(row for row in joined["ownership"]["files"] if row["path"] == "src/a.py")
        self.assertEqual(owned["contributor_residual_share"], 0.2)
        self.assertEqual(owned["source_code_lines"], 1)
        for dimension in ("role", "purpose", "extension"):
            self.assertEqual(sum(row["files"] for row in joined["ownership"][f"by_{dimension}"]), 8)
        self.assertIn("Repowise primary human ownership", render_html(joined))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            census_path, evidence_path = root / "census.json", root / "evidence.json"
            joined_path, report_path = root / "joined.json", root / "joined.html"
            census_path.write_text(json.dumps(self.document))
            evidence_path.write_text(json.dumps(evidence))
            command = CliRunner().invoke(workspace_metrics, [
                "ownership", "--input", str(census_path), "--evidence", str(evidence_path),
                "--out", str(joined_path),
            ])
            self.assertEqual(command.exit_code, 0, command.output)
            command = CliRunner().invoke(workspace_metrics, [
                "report", "--input", str(joined_path), "--out", str(report_path),
            ])
            self.assertEqual(command.exit_code, 0, command.output)
            self.assertIn('id="ownership-dimension"', report_path.read_text())
            self.assertEqual(json.loads(joined_path.read_text())["union"], self.document["union"])
        bad = copy.deepcopy(joined)
        bad["ownership"]["files"][0]["source_code_lines"] += 1
        with self.assertRaisesRegex(ValueError, "weights"):
            validate_ownership(bad)

    def test_ownership_export_requires_compatible_measured_consistent_evidence(self) -> None:
        evidence = self.ownership_evidence()
        bad = copy.deepcopy(evidence)
        bad["sections"][0]["checks"][0]["data"]["head_commit"] = "stale"
        with self.assertRaisesRegex(ValueError, "snapshot"):
            enrich(self.document, bad)
        bad = copy.deepcopy(evidence)
        bad["sections"][0]["failed"] = True
        with self.assertRaisesRegex(ValueError, "failed"):
            enrich(self.document, bad)
        bad = copy.deepcopy(evidence)
        bad["sections"][0]["checks"][0]["data"]["files"][0]["contributor_residual_share"] = 0.5
        with self.assertRaisesRegex(ValueError, "residual"):
            enrich(self.document, bad)
        duplicate = copy.deepcopy(evidence["sections"][0])
        duplicate["repo"] = duplicate["checks"][0]["data"]["alias"] = "overlap"
        duplicate["checks"][0]["data"]["files"][0]["primary_owner"] = {"key": "other", "name": "Other"}
        evidence["sections"].append(duplicate)
        with self.assertRaisesRegex(ValueError, "disagree"):
            enrich(self.document, evidence)

    def test_report_section_reuses_repowise_normalization_and_human_filter(self) -> None:
        from repowise.core.author_identity import build_identity_resolver
        from repowise.core.analysis.ownership_concentration import person_owner
        from repowise.core.analysis.knowledge_matrix import file_author_shares

        resolve = build_identity_resolver([
            ("Alice", "alice@example.invalid"), ("A. Alias", "alice@example.invalid"),
            ("dependabot[bot]", "bot@example.invalid"),
            *[(f"Person {i}", f"p{i}@example.invalid") for i in range(6)],
        ])
        data = SimpleNamespace(resolve=resolve, git_rows=[
            {"file_path": "src/a.py", "primary_owner_name": "Alice", "primary_owner_email": "alice@example.invalid",
             "top_authors_json": [
                 {"name": "Alice", "email": "alice@example.invalid", "commit_count": 3},
                 {"name": "A. Alias", "email": "alice@example.invalid", "commit_count": 2},
                 {"name": "dependabot[bot]", "email": "bot@example.invalid", "commit_count": 20},
             ]},
            {"file_path": "src/b.py", "primary_owner_name": "dependabot[bot]", "primary_owner_email": "bot@example.invalid",
             "top_authors_json": []},
            {"file_path": "src/c.py", "primary_owner_name": None, "primary_owner_email": None,
             "top_authors_json": [
                 {"name": f"Person {i}", "email": f"p{i}@example.invalid", "commit_count": 1}
                 for i in range(6)
             ]},
        ])

        async def read(session: object, repository_id: str) -> object:
            self.assertEqual(repository_id, "fixture-repo")
            return data

        with patch.dict(os.environ, {"REPOWISE_WORKSPACE_METRICS_OWNERSHIP": "1"}), patch(
            "repowise_workspace_metrics.ownership.ownership_helpers", return_value=(read, person_owner, file_author_shares),
        ):
            checks = asyncio.run(collect_section(SimpleNamespace(
                session=object(), repository_id="fixture-repo", alias="first", head_commit="captured",
            )))
        rows = checks[0]["data"]["files"]
        self.assertEqual(len(rows[0]["contributors"]), 1)
        self.assertEqual(rows[0]["contributors"][0]["share"], 1)
        self.assertEqual(rows[0]["primary_owner"], identity_label(resolve("Alice", "alice@example.invalid")))
        self.assertNotIn("alice@example.invalid", json.dumps(checks))
        self.assertNotIn("Alice", json.dumps(checks))
        self.assertIsNone(rows[1]["primary_owner"])
        self.assertIsNone(rows[1]["contributor_residual_share"])
        self.assertEqual(len(rows[2]["contributors"]), 5)
        self.assertAlmostEqual(rows[2]["contributor_residual_share"], 1 / 6)
        with patch.dict(os.environ, {"REPOWISE_WORKSPACE_METRICS_OWNERSHIP": ""}), patch(
            "repowise_workspace_metrics.ownership.ownership_helpers", side_effect=AssertionError("Default reports must not read ownership"),
        ):
            self.assertIsNone(asyncio.run(collect_section(SimpleNamespace())))


if __name__ == "__main__":
    unittest.main()
