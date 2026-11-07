#!/usr/bin/env python3
"""Smoke test for bridge.py: ping round-trip and allowlist rejection."""
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main() -> None:
    root = Path(tempfile.mkdtemp(prefix="bridge-test-"))
    home = root / "home"
    base = home / "t3-tots-bridge"
    for name in ("inbox", "outbox", "done"):
        (base / name).mkdir(parents=True)
    env = dict(os.environ, HOME=str(home))
    bridge = Path(__file__).with_name("bridge.py")
    proc = subprocess.Popen(
        [sys.executable, str(bridge)], env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        def roundtrip(req: dict) -> dict:
            rid = req["id"]
            (base / "inbox" / f"{rid}.json").write_text(json.dumps(req))
            out = base / "outbox" / f"{rid}.json"
            deadline = time.time() + 15
            while time.time() < deadline:
                if out.exists():
                    return json.loads(out.read_text())
                if proc.poll() is not None:
                    raise AssertionError(
                        "bridge exited early: " + (proc.stdout.read() or ""))
                time.sleep(0.1)
            raise AssertionError(f"no result for {rid}")

        r = roundtrip({"id": "t-ping", "argv": ["bridge-ping"]})
        assert r["ok"] and r["exit_code"] == 0, r
        assert "pong" in r["stdout"], r

        r = roundtrip({"id": "t-evil", "argv": ["rm", "-rf", "/"]})
        assert not r["ok"] and r["exit_code"] == 2, r

        # the tools of the earlier design are no longer allowlisted
        r = roundtrip({"id": "t-old", "argv": ["python3", "t3q.py", "health"]})
        assert not r["ok"] and r["exit_code"] == 2, r

        # our own reader, against a fixture database
        tools_dir = home / "t3-muse-tools"
        tools_dir.mkdir()
        repo_root = Path(__file__).resolve().parent.parent
        shutil.copy(repo_root / "tools" / "t3r.py", tools_dir / "t3r.py")
        fixture = root / "fixture.sqlite"
        con = sqlite3.connect(fixture)
        con.execute("CREATE TABLE threads (id TEXT, title TEXT, status TEXT)")
        con.execute("INSERT INTO threads VALUES ('1', 'hello', 'active')")
        con.commit()
        con.close()

        req = {"argv": ["python3", "t3r.py", "health"],
               "env": {"T3M_DB": str(fixture)}}
        r = roundtrip(dict(req, id="t-health"))
        assert r["ok"] and r["exit_code"] == 0, r
        health = json.loads(r["stdout"])
        assert health["table_count"] == 1 and health["exists"], health
        assert health["open_mode"] == "ro", health

        r = roundtrip(dict(req, id="t-schema",
                           argv=["python3", "t3r.py", "schema"]))
        assert r["ok"] and r["exit_code"] == 0, r
        schema = json.loads(r["stdout"])
        assert schema["tables"][0]["name"] == "threads", schema
        assert "title" in schema["tables"][0]["columns"], schema

        # status is allowlisted too; the fixture lacks projection tables,
        # so the reader itself reports that (exit 4) instead of running
        # something unintended
        r = roundtrip(dict(req, id="t-status",
                           argv=["python3", "t3r.py", "status"]))
        assert r["ok"] and r["exit_code"] == 4, r
        assert "projection_threads" in r["stdout"], r

        # our writer, against a fake t3 CLI
        shutil.copy(repo_root / "tools" / "t3w.py", tools_dir / "t3w.py")
        fake = root / "fake-t3"
        fake.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "print(json.dumps({'argv': sys.argv[1:],"
            " 'threadId': 'tid-123'}))\n")
        fake.chmod(0o755)
        wreq = {"env": {"T3M_T3CLI": str(fake)}}
        r = roundtrip(dict(wreq, id="t-wstart",
                           argv=["python3", "t3w.py", "start",
                                 "/some/project", "do the thing"]))
        assert r["ok"] and r["exit_code"] == 0, r
        started = json.loads(r["stdout"])
        assert started["threadId"] == "tid-123", started
        assert started["argv"][:2] == ["thread", "start"], started

        # writer validation: flag-like project, bad thread id, bad verb
        r = roundtrip(dict(wreq, id="t-wevil",
                           argv=["python3", "t3w.py", "start",
                                 "--evil", "x"]))
        assert not r["ok"] and r["exit_code"] == 2, r
        r = roundtrip(dict(wreq, id="t-wbadid",
                           argv=["python3", "t3w.py", "send",
                                 "not-a-thread-id", "hi"]))
        assert not r["ok"] and r["exit_code"] == 2, r
        r = roundtrip(dict(wreq, id="t-wbogus",
                           argv=["python3", "t3w.py", "bogus"]))
        assert not r["ok"] and r["exit_code"] == 2, r

        # worktree start passes the flags through; unsafe values rejected
        r = roundtrip(dict(wreq, id="t-wworktree",
                           argv=["python3", "t3w.py", "start",
                                 "/some/project", "do the thing",
                                 "--worktree", "main",
                                 "--branch", "docs/rich-diagram"]))
        assert r["ok"] and r["exit_code"] == 0, r
        started = json.loads(r["stdout"])
        assert "--worktree" in started["argv"], started
        assert "docs/rich-diagram" in started["argv"], started
        r = roundtrip(dict(wreq, id="t-wwtbad",
                           argv=["python3", "t3w.py", "start",
                                 "/some/project", "do the thing",
                                 "--worktree", "--evil"]))
        assert not r["ok"] and r["exit_code"] == 2, r
        r = roundtrip(dict(wreq, id="t-wwtnoval",
                           argv=["python3", "t3w.py", "start",
                                 "/some/project", "do the thing",
                                 "--branch"]))
        assert not r["ok"] and r["exit_code"] == 2, r

        # project registration passes through; relative paths rejected
        r = roundtrip(dict(wreq, id="t-waddproj",
                           argv=["python3", "t3w.py", "add-project",
                                 "/some/project", "--title", "Demo"]))
        assert r["ok"] and r["exit_code"] == 0, r
        added = json.loads(r["stdout"])
        assert added["argv"][:2] == ["project", "add"], added
        r = roundtrip(dict(wreq, id="t-waddrel",
                           argv=["python3", "t3w.py", "add-project",
                                 "relative/path"]))
        assert not r["ok"] and r["exit_code"] == 2, r

        # the answer relay: validators reject before anything runs
        shutil.copy(repo_root / "tools" / "t3a.py", tools_dir / "t3a.py")
        sentinel = "BRIDGE-TEST-TOKEN-sentinel"
        fake_issue = root / "fake-issue-t3"
        env_seen = root / "env-seen"
        fake_issue.write_text(
            "#!/usr/bin/env python3\n"
            "import os\n"
            f"open({str(env_seen)!r}, 'w').write(os.environ.get('T3M_DB', ''))\n"
            f"print({sentinel!r})\n")
        fake_issue.chmod(0o755)
        t3_home = root / "t3-home"
        t3_home.mkdir()
        aenv = {"T3M_T3CLI": str(fake_issue), "T3M_T3_HOME": str(t3_home)}
        request_id = ("runtime-request:provider:codex:provider-turn:"
                      "provider-turn%3Aabc:req_1")
        rejected = [
            ["answer", request_id, "approve"],
            ["answer", request_id, "deny"],
            ["answer", request_id, "--user-approved", "approve"],
            ["answer", "--user-approved", request_id, "approve"],
            ["answer", request_id, "plain text", "--user-approved"],
            ["answer", request_id, "approve", "--user-approved", "extra"],
            ["answer", request_id, "approve", "--force"],
            ["answer", request_id],
            ["answer"],
            ["answer", "-x", "hello"],
            ["answer", "bad id", "hello"],
            ["answer", "trailing-newline\n", "hello"],
            ["answer", request_id, ""],
            ["answer", request_id, "   "],
            ["answer", request_id, "-flag-like"],
            ["answer", request_id, "  -flag-like"],
            ["answer", request_id, "x" * 4001],
            ["answer", request_id, "nul\x00byte"],
            ["pending", "extra"],
            ["issue-token", "--ttl", "1d"],
            ["revoke-token"],
        ]
        for i, args in enumerate(rejected):
            r = roundtrip(dict(env=aenv, id=f"t-areject-{i}",
                               argv=["python3", "t3a.py", *args]))
            assert not r["ok"] and r["exit_code"] == 2, (args, r)
            assert "exit_code" in r and "stdout" not in r, (args, r)

        # allowed shapes reach the tool, which reports it has no token yet
        allowed = [
            ["pending"],
            ["answer", request_id, "approve", "--user-approved"],
            ["answer", request_id, "deny", "--user-approved"],
            ["answer", request_id, "use the blue one"],
            ["answer", request_id, "x" * 4000],
        ]
        for i, args in enumerate(allowed):
            r = roundtrip(dict(env=aenv, id=f"t-aallow-{i}",
                               argv=["python3", "t3a.py", *args]))
            assert r["ok"] and r["exit_code"] == 3, (args, r)
            assert json.loads(r["stderr"])["error"] == "NO_TOKEN", (args, r)

        # issue-token: the token reaches the file, never the mailbox
        r = roundtrip({"id": "t-aissue",
                       "env": dict(aenv, T3M_DB="not-for-t3a"),
                       "argv": ["python3", "t3a.py", "issue-token"]})
        assert r["ok"] and r["exit_code"] == 0, r
        issued = json.loads(r["stdout"])
        token_file = Path(issued["token_path"])
        assert issued["issued"] is True, issued
        assert token_file.resolve().parent == tools_dir.resolve(), issued
        assert token_file.read_text().strip() == sentinel, issued
        assert token_file.stat().st_mode & 0o777 == 0o600, issued
        assert env_seen.read_text() == "", "T3M_DB must not reach t3a"
        mailbox_files = [*base.joinpath("outbox").iterdir(),
                         *base.joinpath("done").iterdir()]
        assert mailbox_files, "the mailbox should hold results"
        for path in mailbox_files:
            assert sentinel not in path.read_text(), path
        r = roundtrip({"id": "t-areissue", "env": aenv,
                       "argv": ["python3", "t3a.py", "issue-token"]})
        assert r["ok"] and r["exit_code"] == 3, r
        assert json.loads(r["stderr"])["error"] == "TOKEN_EXISTS", r

        # T3M_T3_HOME reaches t3a, and only t3a
        r = roundtrip({"id": "t-ahome", "env": aenv,
                       "argv": ["python3", "t3a.py", "pending"]})
        assert r["ok"] and r["exit_code"] == 4, r
        failure = json.loads(r["stderr"])
        assert failure["error"] == "T3_NOT_RUNNING", r
        assert str(t3_home) in failure["message"], r
        fake_env = root / "fake-env-t3"
        fake_env.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os\n"
            "print(json.dumps({'home': os.environ.get('T3M_T3_HOME')}))\n")
        fake_env.chmod(0o755)
        r = roundtrip({"id": "t-wenv",
                       "env": {"T3M_T3CLI": str(fake_env),
                               "T3M_T3_HOME": "not-for-t3w"},
                       "argv": ["python3", "t3w.py", "list"]})
        assert r["ok"] and json.loads(r["stdout"])["home"] is None, r

        print("bridge smoke test: OK")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
