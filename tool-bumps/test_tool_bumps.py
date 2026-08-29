import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "tool_bumps.py"


def git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def write_manifest(cwd, body):
    (Path(cwd) / "mise.toml").write_text(body)


def commit(cwd, message):
    git(cwd, "add", "-A")
    git(cwd, "commit", "-m", message)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def run(cwd, base, *extra):
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), base, *extra],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(proc.stdout)


class ToolBumpsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = tmp.name
        git(self.repo, "init", "-q")
        git(self.repo, "config", "user.email", "t@example.com")
        git(self.repo, "config", "user.name", "t")

    def test_changed_removed_added_and_unchanged(self):
        write_manifest(
            self.repo,
            '[tools]\ncopier = "9.4.1"\nshellcheck = "0.10.0"\n',
        )
        base = commit(self.repo, "base")
        write_manifest(
            self.repo,
            '[tools]\ncopier = "9.5.0"\nhk = "1.0.0"\n',
        )
        commit(self.repo, "bump")

        self.assertEqual(
            run(self.repo, base),
            {"copier": True, "shellcheck": True, "hk": True},
        )

    def test_no_changes(self):
        body = '[tools]\ncopier = "9.4.1"\n'
        write_manifest(self.repo, body)
        base = commit(self.repo, "base")
        (Path(self.repo) / "other.txt").write_text("x")
        commit(self.repo, "unrelated")

        self.assertEqual(run(self.repo, base), {"copier": False})

    def test_manifest_absent_at_base(self):
        (Path(self.repo) / "other.txt").write_text("x")
        base = commit(self.repo, "base")
        write_manifest(self.repo, '[tools]\ncopier = "9.4.1"\n')
        commit(self.repo, "add manifest")

        self.assertEqual(run(self.repo, base), {"copier": True})

    def test_custom_manifest_name(self):
        (Path(self.repo) / "custom.toml").write_text('[tools]\nfoo = "1"\n')
        base = commit(self.repo, "base")
        (Path(self.repo) / "custom.toml").write_text('[tools]\nfoo = "2"\n')
        commit(self.repo, "bump")

        self.assertEqual(run(self.repo, base, "custom.toml"), {"foo": True})


if __name__ == "__main__":
    unittest.main()
