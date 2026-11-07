#!/usr/bin/env python3
"""T3 Bridge daemon for Muse (Mac side).

Watches ~/t3-tots-bridge/inbox/ for request JSON files placed there through
the user's Muse device channel, runs allowlisted T3 tool commands, and
writes results to ~/t3-tots-bridge/outbox/<id>.json.

Design rules:
- No network listener. The only input is local files in the inbox folder.
- Allowlist only: the built-in bridge-ping and our own read-only
  reader t3r.py (schema / health / status). No arbitrary shell,
  no other programs.
- Every request and result is archived under done/ for audit.
"""
import json
import os
import subprocess
import sys
import time
import traceback

BASE = os.path.expanduser("~/t3-tots-bridge")
INBOX = os.path.join(BASE, "inbox")
OUTBOX = os.path.join(BASE, "outbox")
DONE = os.path.join(BASE, "done")
TOOLS = os.path.expanduser("~/t3-muse-tools")

POLL_SECONDS = 0.5
DEFAULT_TIMEOUT = 120
MAX_OUTPUT = 200_000  # bytes per stream

READER_SUBCOMMANDS = {"schema", "health", "status"}
ENV_WHITELIST = {"T3M_DB"}


def validate(argv):
    """Return an error string, or None when the argv is allowed."""
    if not isinstance(argv, list) or not argv or not all(isinstance(a, str) for a in argv):
        return "argv must be a non-empty list of strings"
    if argv == ["bridge-ping"]:
        return None
    if argv[:2] == ["python3", "t3r.py"] and len(argv) == 3:
        if argv[2] not in READER_SUBCOMMANDS:
            return f"t3r.py subcommand not allowed: {argv[2]}"
        return None
    return "command not on the allowlist"


def run_request(req):
    argv = req.get("argv")
    err = validate(argv)
    if err:
        return {"ok": False, "error": err}
    if argv == ["bridge-ping"]:
        return {
            "ok": True,
            "exit_code": 0,
            "stdout": json.dumps(
                {
                    "pong": True,
                    "hostname": os.uname().nodename,
                    "tools_folder_exists": os.path.isdir(TOOLS),
                    "python": sys.version.split()[0],
                }
            ),
            "stderr": "",
        }
    env = dict(os.environ)
    req_env = req.get("env") or {}
    if isinstance(req_env, dict):
        for key, value in req_env.items():
            if key in ENV_WHITELIST and isinstance(value, str):
                env[key] = value
    timeout = req.get("timeout", DEFAULT_TIMEOUT)
    if not isinstance(timeout, (int, float)) or not (1 <= timeout <= 600):
        timeout = DEFAULT_TIMEOUT
    try:
        proc = subprocess.run(
            argv,
            cwd=TOOLS,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "ok": True,
            "exit_code": proc.returncode,
            "stdout": proc.stdout[:MAX_OUTPUT],
            "stderr": proc.stderr[:MAX_OUTPUT],
        }
    except FileNotFoundError as e:
        return {"ok": False, "error": f"not found (tools folder or program): {e}"}
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"timeout after {timeout}s"}
    except Exception:
        return {"ok": False, "error": traceback.format_exc(limit=3)}


def write_result(req_id, result):
    tmp = os.path.join(OUTBOX, f".{req_id}.tmp")
    final = os.path.join(OUTBOX, f"{req_id}.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False)
    os.replace(tmp, final)


def process(path):
    name = os.path.basename(path)
    req_id = name[:-5] if name.endswith(".json") else name
    try:
        with open(path, encoding="utf-8") as f:
            req = json.load(f)
    except Exception as e:
        write_result(req_id, {"ok": False, "error": f"bad request json: {e}"})
        os.replace(path, os.path.join(DONE, name))
        return
    if isinstance(req, dict) and req.get("id"):
        req_id = str(req["id"])
    result = run_request(req if isinstance(req, dict) else {})
    result.setdefault("exit_code", 0 if result.get("ok") else 2)
    result["id"] = req_id
    result["finished_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    write_result(req_id, result)
    os.replace(path, os.path.join(DONE, f"{req_id}.json"))


def main():
    for d in (INBOX, OUTBOX, DONE):
        os.makedirs(d, exist_ok=True)
    print(f"t3-bridge watching {INBOX}", flush=True)
    while True:
        try:
            for name in sorted(os.listdir(INBOX)):
                if name.endswith(".json"):
                    process(os.path.join(INBOX, name))
        except Exception:
            traceback.print_exc()
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
