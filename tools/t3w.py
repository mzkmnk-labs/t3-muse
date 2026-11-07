#!/usr/bin/env python3
"""t3w: ask T3 Code to do work, via the `t3 thread` CLI bundled with it.

Subcommands (all print the CLI's JSON output):
    python3 t3w.py start <project> <message> [--worktree <ref>
                       [--branch <name>]] [--wait]
    python3 t3w.py send <thread-id> <message> [--wait]
    python3 t3w.py show <thread-id>
    python3 t3w.py list [project]
    python3 t3w.py add-project <path> [--title <title>]

<project> is a project id or any path inside the project's folder.
`start` prints {"threadId", "projectId", "runId"}. With --wait the tool
waits for the run to finish and passes the CLI's exit code through
(0 = completed, 1 = run failed, 2 = the agent is waiting for a human
answer, which only the T3 Code app can give).

start/send are write operations: they create threads and send prompts.
The bridge only runs this tool for requests the user asked Muse to
make. The CLI itself needs no token on the host machine; it uses the
running T3 Code server's local session. The CLI path defaults to
~/.t3/bin/t3 and can be overridden with T3M_T3CLI (used by tests).
"""
import json
import os
import subprocess
import sys

DEFAULT_CLI = os.path.expanduser("~/.t3/bin/t3")
CLI_TIMEOUT = 560  # the bridge caps requests at 600s; stay inside it

USAGE = "usage: t3w.py start|send|show|list ..."


def fail(message: str, code: int) -> int:
    print(json.dumps({"error": message}))
    return code


def _parse_start(rest: list):
    """Parse start arguments into a CLI call, or None when malformed."""
    if len(rest) < 2:
        return None
    call = ["thread", "start", rest[0], rest[1]]
    tail = rest[2:]
    wait = False
    i = 0
    while i < len(tail):
        token = tail[i]
        if token == "--wait":
            wait = True
            i += 1
        elif token in ("--worktree", "--branch") and i + 1 < len(tail):
            call += [token, tail[i + 1]]
            i += 2
        else:
            return None
    if wait:
        call.append("--wait")
    call.append("--json")
    return call


def build_call(argv: list):
    """Translate our argv into the t3 CLI argv, or return None."""
    cmd, rest = argv[1], argv[2:]
    wait = bool(rest) and rest[-1] == "--wait"
    if wait:
        rest = rest[:-1]
    if cmd == "start":
        return _parse_start(argv[2:])
    if cmd == "add-project" and not wait:
        # No --json here: `t3 project add` predates the JSON output
        # convention; pass its plain output through.
        if len(rest) == 1:
            return ["project", "add", rest[0]]
        if len(rest) == 3 and rest[1] == "--title":
            return ["project", "add", rest[0], "--title", rest[2]]
        return None
    if cmd == "send" and len(rest) == 2:
        call = ["thread", "send", rest[0], rest[1]]
    elif cmd == "show" and len(rest) == 1 and not wait:
        call = ["thread", "show", rest[0]]
    elif cmd == "list" and len(rest) <= 1 and not wait:
        call = ["thread", "list"]
        if rest:
            call += ["--project", rest[0]]
    else:
        return None
    if wait:
        call.append("--wait")
    call.append("--json")
    return call


def main(argv: list) -> int:
    if len(argv) < 2:
        return fail(USAGE, 2)
    call = build_call(argv)
    if call is None:
        return fail(USAGE, 2)
    cli = os.environ.get("T3M_T3CLI", DEFAULT_CLI)
    if not os.path.isfile(cli) or not os.access(cli, os.X_OK):
        return fail(f"t3 CLI not found or not executable: {cli}", 3)
    try:
        proc = subprocess.run(
            [cli] + call,
            stdin=subprocess.DEVNULL,
            capture_output=True, text=True, timeout=CLI_TIMEOUT)
    except subprocess.TimeoutExpired:
        return fail(f"t3 CLI timed out after {CLI_TIMEOUT}s", 4)
    sys.stdout.write(proc.stdout)
    sys.stderr.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
