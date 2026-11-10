#!/usr/bin/env python3
"""Unit tests for t3g.py against a fake ghq.

ACCEPTED and REJECTED are the <owner>/<repo> cases that bridge/test_bridge.py
also runs through bridge.py's validator, so the two cannot drift apart.
"""
import contextlib
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

T3G = Path(__file__).with_name("t3g.py")
sys.path.insert(0, str(T3G.parent))
import t3g  # noqa: E402

ACCEPTED = [
    "octocat/Hello-World",
    "a/b",
    "0/9",
    "my-org/my_repo",
    "user/user.github.io",
    "user/repo-git",
    "user/a.b.c",
    "Org123/Repo_Name-2",
    "o/" + "r" * 98,  # 100 characters in all
]

REJECTED = [
    # not exactly two parts
    "", "owner", "owner/", "/repo", "/", "owner/repo/extra", "owner//repo",
    "github.com/owner/repo", "https://github.com/owner/repo",
    "git@github.com:owner/repo",
    # a part that does not start with a letter or a digit
    ".owner/repo", "owner/.repo", "-owner/repo", "owner/-repo",
    "_owner/repo", "owner/_repo", "~/repo", "owner/~",
    # .. anywhere
    "../repo", "owner/..", "owner/a..b", "a..b/repo",
    # the .git suffix that ghq would drop
    "owner/repo.git", "owner/repo.GIT", "owner/.git",
    # whitespace, control characters and the trailing newline $ would let in
    "owner/repo\n", "owner/repo\r", "owner/repo ", " owner/repo",
    "own er/repo", "owner/re po", "owner/repo\x00", "owner/repo\t",
    # shell and URL metacharacters, other separators, non-ASCII
    "owner/repo;ls", "owner/$(id)", "owner/repo`id`", "owner\\repo",
    "owner/repo#main", "owner/repo@v1", "owner/repo:x", "owner/repo?x",
    "owner/répo", "owner/repo*",
    # over the length cap
    "o/" + "r" * 99,
]

# The shebang is added by write_fake: the real interpreter, because a version
# manager's python3 shim stops working under the tests' own HOME.
FAKE_GHQ = r"""
import json, os, subprocess, sys, time

state = os.environ["FAKE_GHQ_STATE"]
mode = os.environ.get("FAKE_GHQ_MODE", "clone")
with open(os.path.join(state, "calls"), "a") as f:
    f.write(json.dumps({
        "argv": sys.argv[1:],
        "prompt": os.environ.get("GIT_TERMINAL_PROMPT"),
        "path0": os.environ["PATH"].split(os.pathsep)[0],
        "stdin_devnull": os.path.samestat(os.fstat(0), os.stat(os.devnull)),
    }) + "\n")
root = os.environ.get("FAKE_GHQ_ROOT", "")
if mode == "rootfail":
    root = os.path.expanduser("~/ghq")
if sys.argv[1:] == ["root"]:
    if mode == "rootfail":
        sys.exit(1)
    print(root)
    sys.exit(0)
if len(sys.argv) != 3 or sys.argv[1] != "get":
    sys.exit(9)
url = sys.argv[2]
print("clone " + url, file=sys.stderr)
if mode == "fail":
    print("remote: Repository not found.", file=sys.stderr)
    sys.exit(1)
if mode == "hang":
    # Stands in for git: a child in the same process group that, if it is
    # left running, leaves a mark.
    mark = os.path.join(state, "git")
    subprocess.Popen([sys.executable, "-c",
                      "import sys, time; open(sys.argv[1] + '-started', 'w'); "
                      "time.sleep(3); open(sys.argv[1] + '-survived', 'w')",
                      mark])
    deadline = time.time() + 10
    while not os.path.exists(mark + "-started") and time.time() < deadline:
        time.sleep(0.05)
    time.sleep(60)
if mode == "noclone":
    sys.exit(0)
os.makedirs(os.path.join(root, url.split("://", 1)[1]))
print(json.dumps({"stdout": "noise that must not reach t3g's stdout"}))
"""


def write_fake(folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    fake = folder / "ghq"
    fake.write_text(f"#!{sys.executable}" + FAKE_GHQ)
    fake.chmod(fake.stat().st_mode | stat.S_IEXEC)
    return fake


class SpecTest(unittest.TestCase):
    def test_accepted(self):
        for spec in ACCEPTED:
            self.assertIsNone(t3g.spec_error(spec), spec)

    def test_rejected(self):
        for spec in REJECTED:
            self.assertIsNotNone(t3g.spec_error(spec), repr(spec))


class T3gTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t3g-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.state = self.tmp / "state"
        self.state.mkdir()
        self.root = self.tmp / "ghq-root"
        self.home = self.tmp / "home"
        self.home.mkdir()
        self.fake = write_fake(self.tmp / "bin")

    def env(self, **extra):
        env = dict(os.environ, HOME=str(self.home), T3M_GHQ=str(self.fake),
                   FAKE_GHQ_STATE=str(self.state),
                   FAKE_GHQ_ROOT=str(self.root))
        env.update(extra)
        return env

    def run_t3g(self, *args, **extra):
        return subprocess.run(
            [sys.executable, str(T3G), *args], env=self.env(**extra),
            stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=30)

    def calls(self):
        log = self.state / "calls"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def test_clone(self):
        proc = self.run_t3g("get", "octo-org/web-app")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        path = self.root / "github.com" / "octo-org" / "web-app"
        self.assertEqual(json.loads(proc.stdout), {
            "repo": "octo-org/web-app",
            "url": "https://github.com/octo-org/web-app",
            "path": str(path),
            "status": "cloned",
        })
        self.assertTrue(path.is_dir())
        self.assertIn("clone https://github.com/octo-org/web-app",
                      proc.stderr)
        self.assertIn("noise", proc.stderr)
        calls = self.calls()
        self.assertEqual([c["argv"] for c in calls],
                         [["root"],
                          ["get", "https://github.com/octo-org/web-app"]])
        for call in calls:
            self.assertEqual(call["prompt"], "0")
            self.assertEqual(call["path0"], str(self.fake.parent))
            self.assertTrue(call["stdin_devnull"])

    def test_already_present(self):
        path = self.root / "github.com" / "octo-org" / "web-app"
        path.mkdir(parents=True)
        (path / "keep").write_text("local work")
        proc = self.run_t3g("get", "octo-org/web-app")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout), {
            "repo": "octo-org/web-app",
            "url": "https://github.com/octo-org/web-app",
            "path": str(path),
            "status": "already_present",
        })
        self.assertEqual([c["argv"] for c in self.calls()], [["root"]])
        self.assertEqual((path / "keep").read_text(), "local work")

    def test_second_get_is_already_present(self):
        first = self.run_t3g("get", "octo-org/web-app")
        second = self.run_t3g("get", "octo-org/web-app")
        self.assertEqual(json.loads(first.stdout)["status"], "cloned")
        self.assertEqual(json.loads(second.stdout)["status"],
                         "already_present")
        self.assertEqual(json.loads(first.stdout)["path"],
                         json.loads(second.stdout)["path"])
        gets = [c for c in self.calls() if c["argv"][0] == "get"]
        self.assertEqual(len(gets), 1)

    def test_root_fallback(self):
        proc = self.run_t3g("get", "octo-org/web-app",
                            FAKE_GHQ_MODE="rootfail")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        out = json.loads(proc.stdout)
        self.assertEqual(out["path"], str(self.home / "ghq" / "github.com"
                                          / "octo-org" / "web-app"))
        self.assertEqual(out["status"], "cloned")

    def test_ghq_fails(self):
        proc = self.run_t3g("get", "octo-org/private", FAKE_GHQ_MODE="fail")
        self.assertEqual(proc.returncode, 5)
        out = json.loads(proc.stdout)
        self.assertIn("exited 1", out["error"])
        self.assertIn("Repository not found", proc.stderr)

    def test_missing_after_clone(self):
        proc = self.run_t3g("get", "octo-org/web-app",
                            FAKE_GHQ_MODE="noclone")
        self.assertEqual(proc.returncode, 5)
        self.assertIn("is not there", json.loads(proc.stdout)["error"])

    def test_path_taken_by_a_file(self):
        owner = self.root / "github.com" / "octo-org"
        owner.mkdir(parents=True)
        (owner / "web-app").write_text("not a checkout")
        proc = self.run_t3g("get", "octo-org/web-app")
        self.assertEqual(proc.returncode, 5)
        self.assertIn("not a folder", json.loads(proc.stdout)["error"])
        self.assertEqual([c["argv"] for c in self.calls()], [["root"]])

    def test_invalid_spec_runs_nothing(self):
        for spec in ("owner/repo.git", "../x", "owner/repo\n", "a/b/c"):
            proc = self.run_t3g("get", spec)
            self.assertEqual(proc.returncode, 2, repr(spec))
            self.assertIn("invalid repository spec",
                          json.loads(proc.stdout)["error"])
        self.assertEqual(self.calls(), [])

    def test_usage(self):
        for args in ((), ("get",), ("get", "a/b", "extra"), ("clone", "a/b"),
                     ("--help",), ("get", "--", "a/b")):
            proc = self.run_t3g(*args)
            self.assertEqual(proc.returncode, 2, args)
            self.assertEqual(json.loads(proc.stdout)["error"], t3g.USAGE)
        self.assertEqual(self.calls(), [])

    def test_missing_ghq(self):
        proc = self.run_t3g("get", "a/b", T3M_GHQ=str(self.tmp / "nope"))
        self.assertEqual(proc.returncode, 3)
        self.assertIn("ghq not found", json.loads(proc.stdout)["error"])


class LookupTest(unittest.TestCase):
    """find_ghq without T3M_GHQ: PATH first, then the fallback folders."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t3g-lookup-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.on_path = write_fake(self.tmp / "on-path")
        self.fallback = write_fake(self.tmp / "fallback")
        self.empty = self.tmp / "empty"
        self.empty.mkdir()

    def find(self, path: str, fallback_dirs: tuple):
        env = {k: v for k, v in os.environ.items() if k != "T3M_GHQ"}
        env["PATH"] = path
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(t3g, "FALLBACK_DIRS", fallback_dirs):
            return t3g.find_ghq()

    def test_path_comes_first(self):
        found, _ = self.find(str(self.on_path.parent),
                             (str(self.fallback.parent),))
        self.assertEqual(found, str(self.on_path))

    def test_fallback_when_path_lacks_it(self):
        # launchd's PATH for the bridge daemon has no Homebrew folder.
        found, looked = self.find(str(self.empty),
                                  (str(self.fallback.parent),))
        self.assertEqual(found, str(self.fallback))
        self.assertEqual(looked, [str(self.empty / "ghq"),
                                  str(self.fallback)])

    def test_not_executable_is_skipped(self):
        self.on_path.chmod(0o644)
        found, _ = self.find(str(self.on_path.parent),
                             (str(self.fallback.parent),))
        self.assertEqual(found, str(self.fallback))

    def test_nowhere(self):
        found, looked = self.find(str(self.empty), (str(self.empty),))
        self.assertIsNone(found)
        self.assertEqual(looked, [str(self.empty / "ghq")] * 2)


class TimeoutTest(unittest.TestCase):
    def test_stops_the_whole_group(self):
        tmp = Path(tempfile.mkdtemp(prefix="t3g-timeout-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        state = tmp / "state"
        state.mkdir()
        fake = write_fake(tmp / "bin")
        env = dict(os.environ, T3M_GHQ=str(fake), FAKE_GHQ_STATE=str(state),
                   FAKE_GHQ_ROOT=str(tmp / "root"), FAKE_GHQ_MODE="hang")
        out, err = io.StringIO(), io.StringIO()
        started = time.monotonic()
        with mock.patch.dict(os.environ, env, clear=True), \
                mock.patch.object(t3g, "GHQ_TIMEOUT", 2), \
                contextlib.redirect_stdout(out), \
                contextlib.redirect_stderr(err):
            code = t3g.main(["t3g.py", "get", "octo-org/big"])
        self.assertEqual(code, 4)
        self.assertIn("timed out", json.loads(out.getvalue())["error"])
        self.assertLess(time.monotonic() - started, 15)
        self.assertTrue((state / "git-started").exists())
        # Left running, the stand-in for git would leave its mark 3s after
        # it started, which was before the 2s timeout.
        time.sleep(max(0, started + 5.5 - time.monotonic()))
        self.assertFalse((state / "git-survived").exists())


if __name__ == "__main__":
    unittest.main()
