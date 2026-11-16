from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name == "posix" and shutil.which("git"), "requires POSIX and git")
class InstallerUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.home = root / "home"
        self.home.mkdir()
        self.env = os.environ.copy()
        self.env["REPOWISE_HOME"] = str(self.home)
        self.env["GIT_CONFIG_COUNT"] = "2"
        self.checkouts = []
        self.remotes = []
        for i, (name, branch, url) in enumerate((
            ("repowise-ifx", "beta", "https://github.com/SteelOtter1242/repowise-ifx.git"),
            ("repowise-workspace-metrics", "main", "https://github.com/SteelOtter1242/repowise-workspace-metrics.git"),
        )):
            remote = root / f"remote-{i}"
            remote.mkdir()
            self.git(remote, "init", "-q", "-b", branch)
            (remote / "pyproject.toml").write_text("initial\n")
            self.commit(remote, "initial")
            checkout = self.home / name
            self.git(root, "clone", "-q", str(remote), str(checkout))
            self.git(checkout, "remote", "set-url", "origin", url)
            self.git(checkout, "switch", "--detach")
            self.env[f"GIT_CONFIG_KEY_{i}"] = f"url.{remote.as_uri()}.insteadOf"
            self.env[f"GIT_CONFIG_VALUE_{i}"] = url
            self.checkouts.append(checkout)
            self.remotes.append(remote)

        tools = root / "tools"
        tools.mkdir()
        self.env["PATH"] = f"{tools}{os.pathsep}{self.env['PATH']}"
        self.stub(tools / "uv")
        git = shutil.which("git")
        wrapper = tools / "git"
        wrapper.write_text(
            "#!/bin/sh\n"
            'if [ "$1" = "-C" ] && [ "$3" = "remote" ] && [ "$4" = "get-url" ]; then\n'
            f'  exec "{git}" -C "$2" config --get remote.origin.url\n'
            "fi\n"
            f'exec "{git}" "$@"\n'
        )
        wrapper.chmod(0o755)
        self.stub_environment(self.home)

    def stub_environment(self, home: Path) -> None:
        for directory in ("bin", "Scripts"):
            path = home / ".venv" / directory
            path.mkdir(parents=True)
            for name in (("python", "repowise") if directory == "bin" else ("python.exe", "repowise.exe")):
                self.stub(path / name)

    @staticmethod
    def stub(path: Path) -> None:
        path.write_text("#!/bin/sh\nexit 0\n")
        path.chmod(0o755)

    @staticmethod
    def git(cwd: Path, *args: str) -> str:
        return subprocess.run(
            ["git", *args], cwd=cwd, check=True, text=True, capture_output=True,
        ).stdout.strip()

    def commit(self, repo: Path, message: str) -> None:
        self.git(repo, "add", ".")
        self.git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                 "commit", "-qm", message)

    def advance_remotes(self) -> list[str]:
        for remote in self.remotes:
            (remote / "pyproject.toml").write_text("updated\n")
            self.commit(remote, "update")
        return [self.git(remote, "rev-parse", "HEAD") for remote in self.remotes]

    def run_installer(self, command: list[str], *args: str, error: str | None = None) -> None:
        result = subprocess.run(command + list(args), env=self.env, text=True, capture_output=True)
        if error is None:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn(error, result.stdout + result.stderr)

    def test_fresh_install_uses_latest_branches_and_rerun_reuses_them(self) -> None:
        commands = [["bash", str(ROOT / "scripts/install-workspace-metrics.sh")]]
        if shutil.which("pwsh"):
            commands.append(["pwsh", "-NoProfile", "-File", str(ROOT / "scripts/install-workspace-metrics.ps1")])
        latest = [self.git(repo, "rev-parse", "HEAD") for repo in self.remotes]
        homes = []
        for i, command in enumerate(commands):
            home = Path(self.temp.name) / f"fresh-{i}"
            home.mkdir()
            self.stub_environment(home)
            self.env["REPOWISE_HOME"] = str(home)
            with self.subTest(command=command[0], phase="first install"):
                self.run_installer(command)
                self.assertEqual(
                    [self.git(home / name, "rev-parse", "HEAD")
                     for name in ("repowise-ifx", "repowise-workspace-metrics")], latest,
                )
            homes.append(home)

        self.advance_remotes()
        for home, command in zip(homes, commands):
            self.env["REPOWISE_HOME"] = str(home)
            with self.subTest(command=command[0], phase="rerun"):
                self.run_installer(command)
                self.assertEqual(
                    [self.git(home / name, "rev-parse", "HEAD")
                     for name in ("repowise-ifx", "repowise-workspace-metrics")], latest,
                )

    def test_explicit_update_and_plain_rerun(self) -> None:
        commands = [(["bash", str(ROOT / "scripts/install-workspace-metrics.sh")], "--update", "--upgrade")]
        if shutil.which("pwsh"):
            commands.append((["pwsh", "-NoProfile", "-File", str(ROOT / "scripts/install-workspace-metrics.ps1")],
                             "-Update", "-Upgrade"))
        original = [self.git(repo, "rev-parse", "HEAD") for repo in self.checkouts]
        expected = self.advance_remotes()
        for command, update, upgrade in commands:
            with self.subTest(command=command[0]):
                for checkout, commit in zip(self.checkouts, original):
                    self.git(checkout, "switch", "--detach", commit)
                self.run_installer(command, update)
                self.assertEqual([self.git(repo, "rev-parse", "HEAD") for repo in self.checkouts], expected)
                self.run_installer(command)
                self.assertEqual([self.git(repo, "rev-parse", "HEAD") for repo in self.checkouts], expected)
                for checkout, commit in zip(self.checkouts, original):
                    self.git(checkout, "switch", "--detach", commit)
                self.run_installer(command, upgrade)
                self.assertEqual([self.git(repo, "rev-parse", "HEAD") for repo in self.checkouts], expected)

    def test_divergence_and_dirty_extension_stop_before_updating_repowise(self) -> None:
        expected_repowise = self.git(self.checkouts[0], "rev-parse", "HEAD")
        self.advance_remotes()
        commands = [(["bash", str(ROOT / "scripts/install-workspace-metrics.sh")], "--update",
                     "Extension checkout cannot fast-forward")]
        if shutil.which("pwsh"):
            commands.append((["pwsh", "-NoProfile", "-File", str(ROOT / "scripts/install-workspace-metrics.ps1")],
                             "-Update", "git failed with exit code 1"))
        (self.checkouts[1] / "pyproject.toml").write_text("local edit\n")
        for command, update, _ in commands:
            self.run_installer(command, update, error="Extension checkout has local changes")
            self.assertEqual(self.git(self.checkouts[0], "rev-parse", "HEAD"), expected_repowise)
        self.commit(self.checkouts[1], "local divergent commit")
        for command, update, error in commands:
            self.run_installer(command, update, error=error)
            self.assertEqual(self.git(self.checkouts[0], "rev-parse", "HEAD"), expected_repowise)


if __name__ == "__main__":
    unittest.main()
