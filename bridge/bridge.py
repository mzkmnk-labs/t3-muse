#!/usr/bin/env python3
"""T3 Bridge daemon for Muse (Mac side).

Watches ~/t3-muse-bridge/inbox/ for request JSON files placed there through
the user's Muse device channel, runs allowlisted T3 tool commands, and
writes results to ~/t3-muse-bridge/outbox/<id>.json.

Design rules:
- No network listener. The only input is local files in the inbox folder.
- Allowlist only: the built-in bridge-ping, our read-only reader
  t3r.py (schema / health / status), our task writer t3w.py
  (start / send / show / list — the only write path, run solely for
  requests the user asked Muse to make), and our answer relay t3a.py
  (issue-token / renew / pending / answer — only relays decisions the user
  made; approvals need --user-approved). No arbitrary shell, no other
  programs.
- Every request and result is archived under done/ for audit.
"""
import json
import os
import re
import subprocess
import sys
import time
import traceback

BASE = os.path.expanduser("~/t3-muse-bridge")
INBOX = os.path.join(BASE, "inbox")
OUTBOX = os.path.join(BASE, "outbox")
DONE = os.path.join(BASE, "done")
TOOLS = os.path.expanduser("~/t3-muse-tools")

POLL_SECONDS = 0.5
DEFAULT_TIMEOUT = 120
MAX_OUTPUT = 200_000  # bytes per stream

READER_SUBCOMMANDS = {"schema", "health", "status"}
WRITER_SUBCOMMANDS = {"start", "send", "show", "list", "add-project"}
ENV_WHITELIST = {"T3M_DB", "T3M_T3CLI"}
THREAD_ID = re.compile(r"^(?:mcp:)?[0-9a-fA-F-]{32,40}\Z")
SAFE_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
MAX_MESSAGE = 20_000
T3A_SUBCOMMANDS_NO_ARGS = {"issue-token", "pending", "renew"}
T3A_ENV_WHITELIST = {"T3M_T3CLI", "T3M_T3_HOME"}
REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:%_.~-]{0,799}")
APPROVAL_RESPONSES = {"approve", "deny"}
USER_APPROVED = "--user-approved"
MAX_ANSWER = 4000


def _validate_start(rest):
    if len(rest) < 2:
        return "t3w start needs a project and a message"
    project, message = rest[0], rest[1]
    if project.startswith("-") or message.startswith("-"):
        return "t3w arguments must not start with -"
    if len(project) > 500:
        return "t3w start project argument too long"
    if len(message) > MAX_MESSAGE:
        return "t3w message too long"
    tail = rest[2:]
    i = 0
    while i < len(tail):
        token = tail[i]
        if token == "--wait":
            i += 1
        elif token in ("--worktree", "--branch"):
            if i + 1 >= len(tail) or not SAFE_REF.match(tail[i + 1]):
                return f"t3w start {token} needs a safe ref value"
            i += 2
        else:
            return f"t3w start argument not allowed: {token}"
    return None


def _validate_writer(rest, sub):
    if sub == "start":
        return _validate_start(rest)
    if sub == "send":
        if rest and rest[-1] == "--wait":
            rest = rest[:-1]
        if len(rest) != 2:
            return "t3w send needs exactly two arguments"
        target, message = rest
        if target.startswith("-") or message.startswith("-"):
            return "t3w arguments must not start with -"
        if not THREAD_ID.match(target):
            return "t3w send needs a valid thread id"
        if len(message) > MAX_MESSAGE:
            return "t3w message too long"
        return None
    if sub == "add-project":
        if len(rest) == 1:
            path, title = rest[0], None
        elif len(rest) == 3 and rest[1] == "--title":
            path, title = rest[0], rest[2]
        else:
            return "t3w add-project needs a path and optional --title"
        if not path.startswith("/") or len(path) > 500 or "\n" in path:
            return "t3w add-project needs an absolute path"
        if title is not None and (not title or len(title) > 200
                                  or title.startswith("-")):
            return "t3w add-project title not allowed"
        return None
    if sub == "show":
        if len(rest) != 1 or not THREAD_ID.match(rest[0]):
            return "t3w show needs a valid thread id"
        return None
    if sub == "list":
        if len(rest) > 1:
            return "t3w list takes at most one project argument"
        if rest and rest[0].startswith("-"):
            return "t3w list project must not start with -"
        return None
    return f"t3w.py subcommand not allowed: {sub}"


def _validate_t3a(rest, sub):
    if sub in T3A_SUBCOMMANDS_NO_ARGS:
        return f"t3a {sub} takes no arguments" if rest else None
    if sub != "answer":
        return f"t3a.py subcommand not allowed: {sub}"
    user_approved = rest[-1:] == [USER_APPROVED]
    if user_approved:
        rest = rest[:-1]
    if len(rest) != 2:
        return "t3a answer needs a request id and a response"
    request_id, response = rest
    if not REQUEST_ID.fullmatch(request_id):
        return "t3a answer needs a valid request id"
    if response in APPROVAL_RESPONSES:
        if not user_approved:
            return f"t3a answer {response} needs {USER_APPROVED}"
        return None
    if user_approved:
        return f"t3a answer {USER_APPROVED} only goes with approve or deny"
    if not response.strip() or "\x00" in response:
        return "t3a answer text is empty or invalid"
    if len(response) > MAX_ANSWER:
        return "t3a answer text too long"
    if response.lstrip().startswith("-"):
        return "t3a answer text must not start with -"
    return None


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
    if argv[:2] == ["python3", "t3w.py"] and len(argv) >= 3:
        return _validate_writer(argv[3:], argv[2])
    if argv[:2] == ["python3", "t3a.py"] and len(argv) >= 3:
        return _validate_t3a(argv[3:], argv[2])
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
    allowed_env = T3A_ENV_WHITELIST if argv[1] == "t3a.py" else ENV_WHITELIST
    if isinstance(req_env, dict):
        for key, value in req_env.items():
            if key in allowed_env and isinstance(value, str):
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
