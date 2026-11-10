#!/usr/bin/env python3
"""Dry run of the installer CLI: a fixture HOME, and launchctl and xattr stubs on PATH.

The stubbed launchctl acts like launchd where it matters: it parses the rendered
plists, starts RunAtLoad jobs from their ProgramArguments, and refuses a
quarantined plist. So the probes go through the real bridge.py and the real
tools, placed by the CLI, against a stub T3 server. The real launchd and the
real home folder are never reached, and setUp checks that before anything runs.

The token prompt depends on stdin being a terminal, so those tests give the CLI
a real one through a pty instead of faking isTTY. That also covers what a fake
cannot: readline's terminal mode, Ctrl-D and Ctrl-C, and the CLI exiting afterwards.
"""
import json
import os
import plistlib
import pty
import select
import shutil
import signal
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import time
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "installer" / "t3-muse.js"
# The real binaries, because a version manager's shims stop working under a
# different HOME.
NODE = subprocess.run(["node", "-p", "process.execPath"],
                      capture_output=True, text=True, check=True).stdout.strip()
sys.path.insert(0, str(REPO / "tools"))
import t3a  # noqa: E402  (what issue-token really asks for)
import test_t3a  # noqa: E402  (for its stub T3 server)

TOKEN = test_t3a.TOKEN
TOKEN_PROMPT = "No t3a token found. Issue one now? [y/N]"
PURGE_PROMPT = 'Type "purge" to confirm:'
BRIDGE = "com.muse.t3bridge"
RESTARTER = "com.muse.t3bridge-restarter"
PACKAGED = {
    "installer/t3-muse.js",
    "bridge/bridge.py",
    "bridge/restart-bridge.sh",
    "bridge/com.muse.t3bridge.plist",
    "bridge/com.muse.t3bridge-restarter.plist",
    "tools/t3r.py",
    "tools/t3w.py",
    "tools/t3a.py",
    "tools/t3g.py",
}
LIFECYCLE_SCRIPTS = {"preinstall", "install", "postinstall", "prepare", "prepack", "postpack"}

FAKE_LAUNCHCTL = r"""#!/usr/bin/env python3
import json, os, plistlib, signal, subprocess, sys, time
from pathlib import Path

state = Path(os.environ["STUB_STATE"])
with open(state / "launchctl-calls", "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
verb, target = sys.argv[1:3]


def marker(label):
    return state / f"loaded-{label}"


def stop(label):
    pid = int(marker(label).read_text())
    marker(label).unlink()
    if pid:
        os.kill(pid, signal.SIGTERM)
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except OSError:
                break
            time.sleep(0.05)


def read_plist(path):
    with open(path, "rb") as f:
        return plistlib.load(f)


if verb == "list":
    if not marker(target).exists():
        sys.exit("Could not find service")
elif verb == "remove":
    stop(target)
elif verb == "unload":
    if not os.path.exists(target):
        print("Unload failed: 5: Input/output error", file=sys.stderr)
    else:
        stop(read_plist(target)["Label"])
elif verb == "load":
    # The real launchctl exits 0 here too, whatever went wrong.
    quarantined = state / "quarantined"
    refused = quarantined.exists() and target in quarantined.read_text().splitlines()
    plist = read_plist(target)
    if refused or os.environ.get("STUB_LOAD") == "silent-fail" or marker(plist["Label"]).exists():
        print("Load failed: 5: Input/output error", file=sys.stderr)
        sys.exit(0)
    pid = 0
    if plist.get("RunAtLoad") and os.environ.get("STUB_LOAD") != "no-daemon":
        log = open(plist["StandardOutPath"], "a")
        pid = subprocess.Popen(
            plist["ProgramArguments"], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
            start_new_session=True).pid
    marker(plist["Label"]).write_text(str(pid))
else:
    sys.exit(f"stub launchctl: unexpected verb {verb}")
"""

FAKE_XATTR = r"""#!/usr/bin/env python3
import os, sys
from pathlib import Path

state = Path(os.environ["STUB_STATE"]) / "quarantined"
paths = state.read_text().splitlines() if state.exists() else []
if sys.argv[1] == "-d":
    name, path = sys.argv[2:]
    if name != "com.apple.quarantine" or path not in paths:
        sys.exit(f"xattr: {path}: No such xattr: {name}")
    paths.remove(path)
    state.write_text("".join(p + "\n" for p in paths))
else:
    print("com.apple.provenance")
    if sys.argv[1] in paths:
        print("com.apple.quarantine")
"""

FAKE_T3_CLI = f"""#!/bin/sh
echo "$*" >> "$STUB_STATE/t3-calls"
echo '{TOKEN}'
"""

# Lets the CLI run on Ubuntu with no test seam in its own code.
PRELOAD = r"""
Object.defineProperty(process, 'platform', { value: process.env.SHIM_PLATFORM || 'darwin' });
if (process.env.SHIM_ROOT) process.getuid = () => 0;
if (process.env.SHIM_NO_PYTHON) {
  const childProcess = require('node:child_process');
  const real = childProcess.spawnSync;
  childProcess.spawnSync = (program, ...rest) =>
    program === '/usr/bin/python3' ? { status: null, error: new Error('ENOENT') } : real(program, ...rest);
}
if (process.env.SHIM_FAST_CLOCK) {
  // Every wait gives up 100 times sooner, for the tests of waits that time out.
  const realNow = Date.now;
  const start = realNow();
  Date.now = () => start + (realNow() - start) * 100;
}
"""


class InstallerCase(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="t3-muse-test-"))
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.addCleanup(self.stop_daemons)

        # Both & and $& would break a careless __HOME__ expansion.
        self.home = self.root / "home & $& co"
        self.home.mkdir()
        self.bridge = self.home / "t3-muse-bridge"
        self.tools = self.home / "t3-muse-tools"
        self.agents = self.home / "Library" / "LaunchAgents"
        self.token_file = self.tools / ".t3a-token"

        self.state = self.root / "stub-state"
        self.state.mkdir()
        stubs = self.root / "stubs"
        stubs.mkdir()
        for name, body in (("launchctl", FAKE_LAUNCHCTL), ("xattr", FAKE_XATTR)):
            (stubs / name).write_text(body)
            (stubs / name).chmod(0o755)
        (stubs / "python3").symlink_to(sys.executable)
        self.preload = self.root / "preload.cjs"
        self.preload.write_text(PRELOAD)
        # The stubs are Python, and the macOS system Python writes its bytecode
        # cache under $HOME/Library/Caches/com.apple.python, which here is the
        # fixture HOME the tests compare.
        self.env = dict(os.environ, HOME=str(self.home), STUB_STATE=str(self.state),
                        PATH=f"{stubs}{os.pathsep}{os.environ['PATH']}",
                        PYTHONDONTWRITEBYTECODE="1")

        # The safety catch: stop here rather than touch a real install.
        self.assertEqual(shutil.which("launchctl", path=self.env["PATH"]),
                         str(stubs / "launchctl"))
        self.assertEqual(shutil.which("xattr", path=self.env["PATH"]), str(stubs / "xattr"))

        # Another companion's install on the same Mac, which the CLI must never touch.
        self.other_install = self.home / "other-companion" / "token"
        self.other_install.parent.mkdir()
        self.other_install.write_text("other-companion-secret")
        self.addCleanup(self.assert_other_install_untouched)

    def assert_other_install_untouched(self):
        self.assertEqual(self.other_install.read_text(), "other-companion-secret")

    def stop_daemons(self):
        for marker in self.state.glob("loaded-*"):
            pid = int(marker.read_text())
            if pid:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass

    def plist(self, label):
        return self.agents / f"{label}.plist"

    def seed_t3(self, *, db=True, server=True, token=True, server_status=101):
        """What T3 Code leaves in the home folder, with a stub of its server."""
        userdata = self.home / ".t3" / "userdata"
        userdata.mkdir(parents=True)
        if db:
            con = sqlite3.connect(userdata / "statev2.sqlite")
            con.execute("CREATE TABLE threads (id TEXT)")
            con.commit()
            con.close()
        self.server = None
        if server:
            self.server = test_t3a.StubT3Server(status=server_status)
            self.addCleanup(self.server.stop)
            (userdata / "server-runtime.json").write_text(
                json.dumps({"host": "127.0.0.1", "port": self.server.port}))
        if token:
            self.tools.mkdir(exist_ok=True)
            self.token_file.write_text(TOKEN + "\n")
            self.token_file.chmod(0o600)
        cli = self.home / ".t3" / "bin" / "t3"
        cli.parent.mkdir()
        cli.write_text(FAKE_T3_CLI)
        cli.chmod(0o755)

    def no_pending(self):
        self.addCleanup(setattr, test_t3a, "THREADS", test_t3a.THREADS)
        test_t3a.THREADS = []

    def run_cli(self, *args, input="", cli=CLI, **env):
        proc = subprocess.run(
            [NODE, "--require", str(self.preload), str(cli), *args],
            env={**self.env, **env}, input=input, capture_output=True, text=True, timeout=180)
        proc.output = proc.stdout + proc.stderr
        self.assertNotIn(TOKEN, proc.output)
        return proc

    def run_on_terminal(self, *args, answer=None, prompt=TOKEN_PROMPT):
        """Run the CLI with a real terminal as stdin, stdout and stderr.

        answer is typed once the prompt shows. A CLI that never exits, as one
        left holding the terminal would, fails the test.
        """
        master, slave = pty.openpty()
        self.addCleanup(os.close, master)
        proc = subprocess.Popen([NODE, "--require", str(self.preload), str(CLI), *args],
                                env=self.env, stdin=slave, stdout=slave, stderr=slave)
        os.close(slave)
        output = b""
        deadline = time.monotonic() + 120
        while True:
            if time.monotonic() > deadline:
                proc.kill()
                proc.wait()
                self.fail(f"the CLI did not exit: {output!r}")
            if select.select([master], [], [], 0.2)[0]:
                try:
                    chunk = os.read(master, 4096)
                except OSError:  # Linux, once the CLI has closed the terminal
                    break
                if not chunk:  # macOS, likewise
                    break
                output += chunk
                if answer is not None and prompt.encode() in output:
                    os.write(master, answer)
                    answer = None
            elif proc.poll() is not None:
                break
        proc.wait(timeout=30)
        text = output.decode(errors="replace").replace("\r\n", "\n")
        self.assertNotIn(TOKEN, text)
        return types.SimpleNamespace(returncode=proc.returncode, output=text)

    def t3_calls(self):
        path = self.state / "t3-calls"
        return path.read_text().splitlines() if path.exists() else []

    def install_ok(self, **kwargs):
        proc = self.run_cli("install", **kwargs)
        self.assertEqual(proc.returncode, 0, proc.output)
        self.assertIn("install complete", proc.stdout)
        return proc

    def launchctl_changes(self):
        """launchctl calls other than list, then forget them."""
        path = self.state / "launchctl-calls"
        calls = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        path.write_text("")
        return [call for call in calls if call[0] != "list"]

    def daemon_pid(self):
        return int((self.state / f"loaded-{BRIDGE}").read_text())

    def mark_loaded(self, *labels):
        for label in labels:
            (self.state / f"loaded-{label}").write_text("0")

    def inbox(self):
        return sorted(path.name for path in (self.bridge / "inbox").iterdir())


class Refusals(InstallerCase):
    def test_not_a_mac(self):
        proc = self.run_cli("install", SHIM_PLATFORM="linux")
        self.assertEqual(proc.returncode, 1, proc.output)
        self.assertIn("only runs on macOS", proc.stderr)
        self.assertFalse(self.bridge.exists())

    def test_no_python(self):
        proc = self.run_cli("install", SHIM_NO_PYTHON="1")
        self.assertEqual(proc.returncode, 1, proc.output)
        self.assertIn("/usr/bin/python3", proc.stderr)
        self.assertFalse(self.bridge.exists())

    def test_root(self):
        self.agents.mkdir(parents=True)
        self.plist(BRIDGE).write_text("placed by the user")
        for args in (("install",), ("status",), ("uninstall",)):
            proc = self.run_cli(*args, SHIM_ROOT="1")
            self.assertEqual(proc.returncode, 1, args)
            self.assertIn("without sudo", proc.stderr)
        self.assertFalse(self.bridge.exists())
        self.assertTrue(self.plist(BRIDGE).exists())
        self.assertEqual(self.launchctl_changes(), [])

    def test_usage(self):
        for args in ((), ("bogus",), ("install", "--purge"), ("status", "x"), ("uninstall", "--force")):
            proc = self.run_cli(*args)
            self.assertEqual(proc.returncode, 2, args)
            self.assertIn("usage: t3-muse", proc.stderr)
        self.assertEqual(self.launchctl_changes(), [])
        proc = self.run_cli("--help")
        self.assertEqual(proc.returncode, 0)
        self.assertIn("usage: t3-muse", proc.stdout)


class Install(InstallerCase):
    def assert_placed(self):
        for source, target in (("bridge/bridge.py", self.bridge / "bridge.py"),
                               ("bridge/restart-bridge.sh", self.bridge / "restart-bridge.sh"),
                               ("tools/t3r.py", self.tools / "t3r.py"),
                               ("tools/t3w.py", self.tools / "t3w.py"),
                               ("tools/t3a.py", self.tools / "t3a.py"),
                               ("tools/t3g.py", self.tools / "t3g.py")):
            self.assertEqual(target.read_bytes(), (REPO / source).read_bytes(), target)
        self.assertEqual((self.bridge / "restart-bridge.sh").stat().st_mode & 0o777, 0o755)
        for name in ("inbox", "outbox", "done", "restart-trigger"):
            self.assertTrue((self.bridge / name).is_dir(), name)

    def assert_plists_expanded(self):
        base = f"{self.home}/t3-muse-bridge"
        for label in (BRIDGE, RESTARTER):
            self.assertNotIn("__HOME__", self.plist(label).read_text())
        with open(self.plist(BRIDGE), "rb") as f:
            bridge = plistlib.load(f)
        self.assertEqual(bridge["Label"], BRIDGE)
        self.assertEqual(bridge["ProgramArguments"], ["/usr/bin/python3", f"{base}/bridge.py"])
        self.assertEqual(bridge["StandardOutPath"], f"{base}/bridge.log")
        with open(self.plist(RESTARTER), "rb") as f:
            restarter = plistlib.load(f)
        self.assertEqual(restarter["Label"], RESTARTER)
        self.assertEqual(restarter["ProgramArguments"], ["/bin/sh", f"{base}/restart-bridge.sh"])
        self.assertEqual(restarter["QueueDirectories"], [f"{base}/restart-trigger"])

    def assert_stopped_at_the_token(self, output):
        self.assertIn("t3a pending: FAILED: NO_TOKEN", output)
        self.assertIn("python3 ~/t3-muse-tools/t3a.py issue-token", output)
        self.assertNotIn("install complete", output)
        self.assertNotIn("Issued a t3a token", output)
        self.assertFalse(self.token_file.exists())
        self.assertEqual(self.t3_calls(), [])

    def test_no_terminal_is_not_asked_and_the_named_step_works(self):
        self.seed_t3(token=False)
        self.no_pending()
        self.agents.mkdir(parents=True)
        # Quarantined plists from an earlier transfer. launchd refuses them, so a
        # load that happened before the attribute was cleared would fail here.
        self.plist(BRIDGE).write_text("stale")
        self.plist(RESTARTER).write_text("stale")
        (self.state / "quarantined").write_text(f"{self.plist(BRIDGE)}\n{self.plist(RESTARTER)}\n")

        # A piped y is not a person at a terminal: no prompt, and no token.
        proc = self.run_cli("install", input="y\n")
        self.assertEqual(proc.returncode, 1, proc.output)
        self.assertIn("bridge-ping: ok (pong)", proc.stdout)
        self.assertIn("t3r health: ok", proc.stdout)
        self.assertNotIn(TOKEN_PROMPT, proc.output)
        self.assert_stopped_at_the_token(proc.stderr)

        self.assert_placed()
        self.assert_plists_expanded()
        self.assertEqual((self.state / "quarantined").read_text(), "")
        self.assertEqual(self.launchctl_changes(), [
            ["load", str(self.plist(BRIDGE))], ["load", str(self.plist(RESTARTER))]])
        self.assertEqual(self.inbox(), [])

        # The step the message names, then the same command again.
        issued = subprocess.run(
            [sys.executable, str(self.tools / "t3a.py"), "issue-token"],
            env=self.env, capture_output=True, text=True)
        self.assertEqual(issued.returncode, 0, issued.stderr)
        self.assertEqual(self.token_file.stat().st_mode & 0o777, 0o600)
        proc = self.install_ok()
        self.assertIn("t3a pending: ok (0 pending)", proc.stdout)
        self.assertEqual(self.launchctl_changes(), [
            ["unload", str(self.plist(RESTARTER))], ["unload", str(self.plist(BRIDGE))],
            ["load", str(self.plist(BRIDGE))], ["load", str(self.plist(RESTARTER))]])

        # The token reached the server, and nowhere else.
        self.assertTrue(any(f"Bearer {TOKEN}" in shake for shake in self.server.handshakes))
        for folder in ("outbox", "done"):
            for path in (self.bridge / folder).iterdir():
                self.assertNotIn(TOKEN, path.read_text(), path)

    def test_terminal_yes_issues_the_token_and_install_passes(self):
        self.seed_t3(token=False)
        self.no_pending()

        proc = self.run_on_terminal("install", answer=b"y\r")

        self.assertEqual(proc.returncode, 0, proc.output)
        self.assertIn(TOKEN_PROMPT, proc.output)
        self.assertIn("t3a pending: ok (0 pending)", proc.output)
        self.assertIn("install complete", proc.output)
        self.assert_placed()

        # The report matches what the tool really asked T3 for.
        self.assertEqual(len(self.t3_calls()), 1)
        issue_args = " ".join(t3a.ISSUE_ARGS)
        self.assertEqual(self.t3_calls()[0], issue_args)
        scopes = [t3a.ISSUE_ARGS[i + 1] for i, arg in enumerate(t3a.ISSUE_ARGS) if arg == "--scope"]
        self.assertTrue(scopes)
        for scope in scopes:
            self.assertIn(scope, proc.output)
        self.assertIn(f"--ttl {t3a.TOKEN_TTL_DAYS}d", issue_args)
        self.assertIn(f"{t3a.TOKEN_TTL_DAYS} days", proc.output)
        self.assertIn(str(self.token_file), proc.output)
        self.assertIn("auth session revoke <session-id>", proc.output)

        self.assertEqual(self.token_file.read_text().strip(), TOKEN)
        self.assertEqual(self.token_file.stat().st_mode & 0o777, 0o600)
        self.assertTrue(any(f"Bearer {TOKEN}" in shake for shake in self.server.handshakes))

        # It went through the mailbox, with room for t3a to finish on its own,
        # and only the file holds the token.
        requests = [json.loads(path.read_text()) for path in (self.bridge / "done").iterdir()]
        issuing = [r for r in requests if r.get("argv") == ["python3", "t3a.py", "issue-token"]]
        self.assertEqual(len(issuing), 1)
        self.assertGreater(issuing[0]["timeout"], t3a.CLI_TIMEOUT)
        for folder in ("outbox", "done"):
            for path in (self.bridge / folder).iterdir():
                self.assertNotIn(TOKEN, path.read_text(), path)

        # Now the file exists: nothing to ask, and nothing issued twice.
        again = self.run_on_terminal("install")
        self.assertEqual(again.returncode, 0, again.output)
        self.assertNotIn(TOKEN_PROMPT, again.output)
        self.assertEqual(len(self.t3_calls()), 1)

    def test_terminal_capital_y_also_issues(self):
        self.seed_t3(token=False)
        self.no_pending()
        proc = self.run_on_terminal("install", answer=b"Y\r")
        self.assertEqual(proc.returncode, 0, proc.output)
        self.assertEqual(len(self.t3_calls()), 1)

    def test_terminal_anything_but_y_changes_nothing(self):
        self.seed_t3(token=False)
        self.no_pending()
        for name, answer in (("n", b"n\r"), ("empty", b"\r"), ("yes", b"yes\r"),
                             ("Ctrl-D", b"\x04"), ("Ctrl-C", b"\x03")):
            with self.subTest(name):
                proc = self.run_on_terminal("install", answer=answer)
                self.assertEqual(proc.returncode, 1, proc.output)
                self.assertIn(TOKEN_PROMPT, proc.output)
                self.assert_stopped_at_the_token(proc.output)

    def test_issued_token_is_still_reported_when_pending_then_fails(self):
        self.seed_t3(token=False, server=False)
        proc = self.run_on_terminal("install", answer=b"y\r")
        self.assertEqual(proc.returncode, 1, proc.output)
        self.assertIn("Issued a t3a token", proc.output)
        self.assertIn("t3a pending: FAILED: T3_NOT_RUNNING", proc.output)
        self.assertIn("Start T3 Code.", proc.output)
        self.assertNotIn("install complete", proc.output)
        self.assertTrue(self.token_file.exists())

    def test_issue_that_fails_is_not_reported_as_issued(self):
        self.seed_t3(token=False)
        (self.home / ".t3" / "bin" / "t3").unlink()
        proc = self.run_on_terminal("install", answer=b"y\r")
        self.assertEqual(proc.returncode, 1, proc.output)
        self.assertIn("t3a issue-token: FAILED: T3CLI_NOT_FOUND", proc.output)
        self.assertNotIn("Issued a t3a token", proc.output)
        self.assertNotIn("install complete", proc.output)
        self.assertFalse(self.token_file.exists())

    def test_unusable_token_file_is_not_asked_about(self):
        # NO_TOKEN again, but there is a file, and issue-token will not replace it.
        self.seed_t3(token=False)
        self.tools.mkdir()
        self.token_file.write_text("")
        self.token_file.chmod(0o600)
        proc = self.run_on_terminal("install")
        self.assertEqual(proc.returncode, 1, proc.output)
        self.assertNotIn(TOKEN_PROMPT, proc.output)
        self.assertIn("does not hold a usable token", proc.output)
        self.assertNotIn("Issue the t3a token once", proc.output)
        self.assertEqual(self.token_file.read_text(), "")
        self.assertEqual(self.t3_calls(), [])

    def test_install_again_restores_files_and_keeps_data(self):
        self.seed_t3()
        self.no_pending()
        self.install_ok()
        first_daemon = self.daemon_pid()
        token_before = self.token_file.read_bytes()
        token_time = self.token_file.stat().st_mtime_ns
        audit = self.bridge / "done" / "earlier-request.json"
        audit.write_text("{}")
        extra = self.tools / "something-else"
        extra.write_text("kept")
        (self.tools / "t3r.py").write_text("damaged")
        self.launchctl_changes()

        self.install_ok()

        self.assert_placed()
        self.assertNotEqual(self.daemon_pid(), first_daemon)
        with self.assertRaises(OSError):
            os.kill(first_daemon, 0)
        self.assertEqual(self.token_file.read_bytes(), token_before)
        self.assertEqual(self.token_file.stat().st_mtime_ns, token_time)
        self.assertTrue(audit.exists())
        self.assertTrue(extra.exists())

    def test_pending_requests_are_counted_but_not_shown(self):
        self.seed_t3()
        proc = self.install_ok()
        self.assertRegex(proc.stdout, r"t3a pending: ok \([1-9]\d* pending\)")
        self.assertNotIn("Approvals", proc.output)
        self.assertNotIn("rm -rf build", proc.output)

    def test_a_tool_that_fails_fails_the_install(self):
        # The bridge answers ok: true with exit_code 3 here, which must not pass.
        self.seed_t3(db=False)
        self.no_pending()
        proc = self.run_cli("install")
        self.assertEqual(proc.returncode, 1, proc.output)
        self.assertIn("t3r health: FAILED: database not found", proc.stderr)
        self.assertNotIn("install complete", proc.stdout)
        self.assertEqual(self.inbox(), [])

    def test_t3_code_not_running_fails_the_install(self):
        self.seed_t3(server=False)
        proc = self.run_cli("install")
        self.assertEqual(proc.returncode, 1, proc.output)
        self.assertIn("t3a pending: FAILED: T3_NOT_RUNNING", proc.stderr)
        self.assertIn("Start T3 Code.", proc.stderr)

    def test_rejected_token_is_reported_and_left_alone(self):
        self.seed_t3(server_status=401)
        before = self.token_file.read_bytes()
        # Replacing a credential is heavier than one prompt: never asked, even
        # at a terminal.
        for proc in (self.run_cli("install"), self.run_on_terminal("install")):
            self.assertEqual(proc.returncode, 1, proc.output)
            self.assertNotIn(TOKEN_PROMPT, proc.output)
            self.assertIn("t3a pending: FAILED: TOKEN_REJECTED", proc.output)
            self.assertIn("python3 ~/t3-muse-tools/t3a.py renew", proc.output)
            self.assertEqual(self.token_file.read_bytes(), before)
            self.assertEqual(sorted(p.name for p in self.tools.glob(".t3a-token*")), [".t3a-token"])
            self.assertEqual(self.t3_calls(), [])

    def test_load_that_fails_but_exits_zero(self):
        self.seed_t3()
        proc = self.run_cli("install", STUB_LOAD="silent-fail", SHIM_FAST_CLOCK="1")
        self.assertEqual(proc.returncode, 1, proc.output)
        self.assertIn("Load failed: 5", proc.stderr)
        self.assertNotIn("bridge-ping", proc.output)
        self.assertEqual(self.inbox(), [])

    def test_daemon_that_never_answers(self):
        self.seed_t3()
        proc = self.run_cli("install", STUB_LOAD="no-daemon", SHIM_FAST_CLOCK="1")
        self.assertEqual(proc.returncode, 1, proc.output)
        self.assertIn("bridge-ping: FAILED: no answer", proc.stderr)
        self.assertNotIn("t3r health", proc.output)
        # Its own request is gone, so it cannot block or surprise the next run.
        self.assertEqual(self.inbox(), [])

    def test_refuses_while_a_request_is_running(self):
        (self.bridge / "inbox").mkdir(parents=True)
        (self.bridge / "inbox" / "busy.json").write_text("{}")
        (self.bridge / "bridge.py").write_text("running version")
        self.mark_loaded(BRIDGE, RESTARTER)
        proc = self.run_cli("install", SHIM_FAST_CLOCK="1")
        self.assertEqual(proc.returncode, 1, proc.output)
        self.assertIn("would run them twice", proc.stderr)
        self.assertEqual(self.launchctl_changes(), [])
        self.assertEqual((self.bridge / "bridge.py").read_text(), "running version")
        self.assertEqual(self.inbox(), ["busy.json"])

    def test_install_waits_for_a_running_request_to_finish(self):
        self.seed_t3()
        self.no_pending()
        self.install_ok()
        request = {"id": "held", "argv": ["bridge-ping"]}
        (self.bridge / "inbox" / "held.json").write_text(json.dumps(request))
        self.install_ok()
        self.assertTrue((self.bridge / "outbox" / "held.json").exists())

    def test_refuses_to_start_a_bridge_onto_a_queued_request(self):
        (self.bridge / "inbox").mkdir(parents=True)
        (self.bridge / "inbox" / "queued.json").write_text("{}")
        proc = self.run_cli("install")
        self.assertEqual(proc.returncode, 1, proc.output)
        self.assertIn("would run as soon as the bridge starts", proc.stderr)
        self.assertEqual(self.launchctl_changes(), [])


class Status(InstallerCase):
    def tree(self):
        return sorted(str(path) for path in self.home.rglob("*"))

    def test_healthy(self):
        self.seed_t3()
        self.no_pending()
        self.install_ok()
        self.launchctl_changes()
        files = {path: path.read_bytes() for path in (self.tools / "t3a.py", self.plist(BRIDGE))}
        pid = self.daemon_pid()

        proc = self.run_cli("status")

        self.assertEqual(proc.returncode, 0, proc.output)
        self.assertRegex(proc.stdout, rf"{BRIDGE} +loaded")
        self.assertRegex(proc.stdout, rf"{RESTARTER} +loaded")
        self.assertRegex(proc.stdout, r"bridge-ping +pong")
        self.assertRegex(proc.stdout, r"t3a\.py +present")
        self.assertRegex(proc.stdout, r"t3g\.py +present")
        self.assertRegex(proc.stdout, r"t3a token file +present")
        self.assertEqual(self.launchctl_changes(), [])
        self.assertEqual(self.daemon_pid(), pid)
        for path, data in files.items():
            self.assertEqual(path.read_bytes(), data, path)
        self.assertEqual(self.inbox(), [])

    def test_nothing_installed_changes_nothing(self):
        before = self.tree()
        proc = self.run_cli("status")
        self.assertEqual(proc.returncode, 1, proc.output)
        self.assertRegex(proc.stdout, rf"{BRIDGE} +not loaded")
        self.assertIn("skipped", proc.stdout)
        self.assertRegex(proc.stdout, r"t3r\.py +missing")
        self.assertRegex(proc.stdout, r"t3a token file +missing")
        self.assertEqual(self.tree(), before)
        self.assertEqual(self.launchctl_changes(), [])

    def test_loaded_but_silent(self):
        self.seed_t3(token=False)
        self.run_cli("install", STUB_LOAD="no-daemon", SHIM_FAST_CLOCK="1")
        proc = self.run_cli("status", SHIM_FAST_CLOCK="1")
        self.assertEqual(proc.returncode, 1, proc.output)
        self.assertRegex(proc.stdout, r"bridge-ping +no answer")
        self.assertEqual(self.inbox(), [])


class Uninstall(InstallerCase):
    def setUp(self):
        super().setUp()
        self.seed_t3()
        self.no_pending()
        self.install_ok()
        self.launchctl_changes()

    def test_default_keeps_the_data(self):
        pid = self.daemon_pid()
        proc = self.run_cli("uninstall")
        self.assertEqual(proc.returncode, 0, proc.output)
        self.assertEqual(self.launchctl_changes(), [
            ["unload", str(self.plist(RESTARTER))], ["unload", str(self.plist(BRIDGE))]])
        self.assertFalse(self.plist(BRIDGE).exists())
        self.assertFalse(self.plist(RESTARTER).exists())
        with self.assertRaises(OSError):
            os.kill(pid, 0)
        self.assertEqual(self.token_file.read_text().strip(), TOKEN)
        self.assertTrue((self.tools / "t3r.py").exists())
        self.assertTrue((self.bridge / "bridge.py").exists())
        self.assertTrue(any((self.bridge / "done").iterdir()))

        # Running it again finds nothing to do and still succeeds.
        proc = self.run_cli("uninstall")
        self.assertEqual(proc.returncode, 0, proc.output)
        self.assertIn("was not installed", proc.stdout)
        self.assertEqual(self.launchctl_changes(), [])

    def test_loaded_job_whose_plist_is_gone(self):
        self.plist(BRIDGE).unlink()
        proc = self.run_cli("uninstall", SHIM_FAST_CLOCK="1")
        self.assertEqual(proc.returncode, 0, proc.output)
        self.assertEqual(self.launchctl_changes(), [
            ["unload", str(self.plist(RESTARTER))], ["remove", BRIDGE]])
        self.assertFalse((self.state / f"loaded-{BRIDGE}").exists())

    def test_purge_needs_the_word(self):
        for answer in ("", "no\n", "y\n", "yes\n"):
            proc = self.run_cli("uninstall", "--purge", input=answer)
            self.assertEqual(proc.returncode, 1, (answer, proc.output))
            self.assertIn("Nothing was changed", proc.stderr)
            self.assertTrue(self.plist(BRIDGE).exists(), answer)
            self.assertTrue(self.token_file.exists(), answer)
            self.assertTrue(self.bridge.is_dir(), answer)
            self.assertEqual(self.launchctl_changes(), [], answer)

    def test_purge_at_a_terminal(self):
        proc = self.run_on_terminal("uninstall", "--purge", answer=b"\x04", prompt=PURGE_PROMPT)
        self.assertEqual(proc.returncode, 1, proc.output)
        self.assertTrue(self.bridge.is_dir())
        proc = self.run_on_terminal("uninstall", "--purge", answer=b"purge\r", prompt=PURGE_PROMPT)
        self.assertEqual(proc.returncode, 0, proc.output)
        self.assertFalse(self.bridge.exists())

    def test_purge_confirmed(self):
        proc = self.run_cli("uninstall", "--purge", input="purge\n")
        self.assertEqual(proc.returncode, 0, proc.output)
        self.assertFalse(self.plist(BRIDGE).exists())
        self.assertFalse(self.plist(RESTARTER).exists())
        self.assertFalse(self.bridge.exists())
        self.assertFalse(self.tools.exists())
        self.assertIn("auth session revoke", proc.stdout)
        # Only those two folders: T3 Code's own data stays.
        self.assertTrue((self.home / ".t3" / "userdata" / "statev2.sqlite").exists())


class Package(InstallerCase):
    def test_manifest(self):
        manifest = json.loads((REPO / "package.json").read_text())
        self.assertEqual(manifest["name"], "@mzkmnk-labs/t3-muse")
        self.assertEqual(manifest["license"], "MIT")
        self.assertEqual(set(manifest["files"]), PACKAGED)
        self.assertEqual(manifest["bin"], {"t3-muse": "installer/t3-muse.js"})
        self.assertEqual(LIFECYCLE_SCRIPTS & set(manifest.get("scripts", {})), set())
        for key in ("dependencies", "optionalDependencies", "peerDependencies", "bundleDependencies"):
            self.assertNotIn(key, manifest)
        self.assertTrue(os.access(CLI, os.X_OK))

    def test_packed_tarball_works_on_its_own(self):
        packed = self.root / "packed"
        packed.mkdir()
        # Packing only: never publish from a test.
        output = subprocess.run(
            ["npm", "pack", "--json", "--pack-destination", str(packed)],
            cwd=REPO, capture_output=True, text=True, check=True).stdout
        contents = {entry["path"] for entry in json.loads(output)[0]["files"]}
        # npm adds LICENSE to every tarball, whatever "files" lists.
        self.assertEqual({path for path in contents if not path.upper().startswith("README")},
                         PACKAGED | {"package.json", "LICENSE"})

        tarball = next(packed.glob("*.tgz"))
        with tarfile.open(tarball) as tar:
            # Python 3.9.6, the macOS system Python, predates extraction filters.
            if hasattr(tarfile, "data_filter"):
                tar.extractall(packed, filter="data")
            else:
                tar.extractall(packed)

        self.seed_t3()
        self.no_pending()
        self.install_ok(cli=packed / "package" / "installer" / "t3-muse.js")
        self.assertEqual((self.bridge / "restart-bridge.sh").stat().st_mode & 0o777, 0o755)


if __name__ == "__main__":
    unittest.main()
