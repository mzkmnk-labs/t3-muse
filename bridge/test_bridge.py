#!/usr/bin/env python3
"""Smoke test for bridge.py: ping round-trip and allowlist rejection."""
import json
import os
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

        print("bridge smoke test: OK")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    main()
