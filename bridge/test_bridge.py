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

        print("bridge smoke test: OK")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
