#!/usr/bin/env python3
"""Unit tests for t3w.py against a fake t3 CLI."""
import json
import os
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

T3W = Path(__file__).with_name("t3w.py")

FAKE_CLI = """#!/usr/bin/env python3
import json, sys
print(json.dumps({"argv": sys.argv[1:], "threadId": "tid-123"}))
"""


class T3wTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t3w-test-")
        cls.fake = Path(cls.tmp) / "fake-t3"
        cls.fake.write_text(FAKE_CLI)
        cls.fake.chmod(cls.fake.stat().st_mode | stat.S_IEXEC)

    def run_t3w(self, *args, cli=None):
        env = dict(os.environ, T3M_T3CLI=str(cli or self.fake))
        proc = subprocess.run(
            [sys.executable, str(T3W), *args],
            env=env, capture_output=True, text=True, timeout=30)
        return proc

    def test_start(self):
        proc = self.run_t3w("start", "/path/to/project", "Fix the tests")
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual(out["argv"],
                         ["thread", "start", "/path/to/project",
                          "Fix the tests", "--json"])
        self.assertEqual(out["threadId"], "tid-123")

    def test_send_with_wait(self):
        proc = self.run_t3w("send", "tid-123", "Now fix it", "--wait")
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual(out["argv"],
                         ["thread", "send", "tid-123", "Now fix it",
                          "--wait", "--json"])

    def test_start_with_worktree(self):
        proc = self.run_t3w("start", "/path/to/project", "Fix the tests",
                            "--worktree", "main",
                            "--branch", "docs/rich-diagram")
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual(out["argv"],
                         ["thread", "start", "/path/to/project",
                          "Fix the tests", "--worktree", "main",
                          "--branch", "docs/rich-diagram", "--json"])

    def test_start_flag_without_value(self):
        proc = self.run_t3w("start", "/path/to/project", "Fix",
                            "--worktree")
        self.assertEqual(proc.returncode, 2)

    def test_show_and_list(self):
        proc = self.run_t3w("show", "tid-123")
        self.assertEqual(json.loads(proc.stdout)["argv"],
                         ["thread", "show", "tid-123", "--json"])
        proc = self.run_t3w("list", "/path/to/project")
        self.assertEqual(json.loads(proc.stdout)["argv"],
                         ["thread", "list",
                          "--project", "/path/to/project", "--json"])

    def test_add_project(self):
        proc = self.run_t3w("add-project", "/path/to/project",
                            "--title", "Demo")
        self.assertEqual(proc.returncode, 0)
        out = json.loads(proc.stdout)
        self.assertEqual(out["argv"],
                         ["project", "add", "/path/to/project",
                          "--title", "Demo"])

    def test_missing_cli(self):
        proc = self.run_t3w("list", cli=Path(self.tmp) / "nope")
        self.assertEqual(proc.returncode, 3)
        self.assertIn("not found", json.loads(proc.stdout)["error"])

    def test_usage(self):
        proc = self.run_t3w("bogus")
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
