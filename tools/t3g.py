#!/usr/bin/env python3
"""t3g: fetch a GitHub repository onto this Mac with ghq.

Usage:
    python3 t3g.py get <owner>/<repo>

get runs `ghq get https://github.com/<owner>/<repo>`, so the repository
lands in <ghq root>/github.com/<owner>/<repo>, ready for
`t3w.py add-project`. Passing the full URL keeps the host and the
destination fixed, whatever ghq would make of a shorter form. The root is
what `ghq root` prints, or ~/ghq when it prints nothing usable. When that
folder already exists, get clones nothing and reports it as already
present; it never updates an existing checkout.

<owner>/<repo> is exactly two parts joined by one /, at most 100
characters in all. Each part starts with a letter or a digit, followed
by letters, digits, ".", "_" or "-". Neither may contain "..", and the
repository part must not end in .git, because ghq drops that suffix and
the checkout would land somewhere else. bridge.py checks the same rules.

Prints one JSON object to stdout:
    {"repo", "url", "path", "status"}
with status "cloned" or "already_present" and path the absolute checkout
folder. A failure is {"error": "..."} with exit 2 for a usage error or a
malformed spec, 3 when ghq is not found, 4 when ghq ran past 560 seconds,
and 5 when the fetch failed: ghq exited nonzero, the folder is missing
afterwards, or the path is taken by something that is not a folder.
ghq's own output goes to stderr.

The bridge daemon runs under launchd, whose PATH leaves out Homebrew, so
ghq is looked up on PATH and then in /opt/homebrew/bin and /usr/local/bin.
T3M_GHQ overrides the lookup (used by tests). ghq's folder goes first on
its PATH, so it finds the git installed beside it. git never prompts
(GIT_TERMINAL_PROMPT=0): a clone that needs credentials it cannot get
without asking fails instead of holding up the bridge.
"""
import json
import os
import re
import signal
import subprocess
import sys

GITHUB = "github.com"
FALLBACK_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")
DEFAULT_ROOT = os.path.expanduser("~/ghq")
SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")
MAX_SPEC = 100
ROOT_TIMEOUT = 30
GHQ_TIMEOUT = 560  # the bridge caps requests at 600s; stay inside it
STOP_GRACE = 10

USAGE = "usage: t3g.py get <owner>/<repo>"


def fail(message: str, code: int, **extra) -> int:
    print(json.dumps(dict({"error": message}, **extra)))
    return code


def spec_error(spec: str):
    """Return why spec is not a usable <owner>/<repo>, or None."""
    if len(spec) > MAX_SPEC:
        return f"longer than {MAX_SPEC} characters"
    parts = spec.split("/")
    if len(parts) != 2 or not all(SEGMENT.fullmatch(p) for p in parts):
        return ("not <owner>/<repo>, two parts of letters, digits, "
                "., _ and - that each start with a letter or a digit")
    if ".." in spec:
        return "contains .."
    if parts[1].lower().endswith(".git"):
        return "ends in .git; leave the suffix off"
    return None


def find_ghq():
    """Return the ghq to run and where it was looked for."""
    override = os.environ.get("T3M_GHQ")
    if override:
        candidates = [override]
    else:
        dirs = os.environ.get("PATH", "").split(os.pathsep)
        candidates = [os.path.join(d, "ghq")
                      for d in dirs + list(FALLBACK_DIRS) if d]
    for path in candidates:
        if os.path.isfile(path) and os.access(path, os.X_OK):
            return os.path.abspath(path), candidates
    return None, candidates


def child_env(ghq: str) -> dict:
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(
        [os.path.dirname(ghq)] + [d for d in env.get("PATH", "").split(
            os.pathsep) if d])
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def ghq_root(ghq: str, env: dict) -> str:
    try:
        proc = subprocess.run([ghq, "root"], env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True,
                              timeout=ROOT_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return DEFAULT_ROOT
    lines = proc.stdout.strip().splitlines()
    root = os.path.expanduser(lines[0].strip()) if lines else ""
    if proc.returncode != 0 or not os.path.isabs(root):
        return DEFAULT_ROOT
    return os.path.normpath(root)


def run_get(ghq: str, url: str, env: dict):
    """Run ghq get in its own process group. Returns (exit code, output),
    with None for the code when it ran out of time and was stopped."""
    proc = subprocess.Popen([ghq, "get", url], env=env,
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True,
                            start_new_session=True)
    try:
        output, _ = proc.communicate(timeout=GHQ_TIMEOUT)
        return proc.returncode, output
    except subprocess.TimeoutExpired:
        pass
    # SIGTERM reaches git as well, which then removes its partial clone.
    output = ""
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass
        try:
            output, _ = proc.communicate(timeout=STOP_GRACE)
            break
        except subprocess.TimeoutExpired:
            continue
    return None, output


def cmd_get(spec: str) -> int:
    problem = spec_error(spec)
    if problem:
        return fail(f"invalid repository spec: {problem}", 2)
    ghq, looked = find_ghq()
    if ghq is None:
        return fail("ghq not found or not executable; looked for "
                    + ", ".join(looked), 3)
    owner, repo = spec.split("/")
    url = f"https://{GITHUB}/{owner}/{repo}"
    env = child_env(ghq)
    path = os.path.join(ghq_root(ghq, env), GITHUB, owner, repo)
    result = {"repo": spec, "url": url, "path": path}
    if os.path.isdir(path):
        print(json.dumps(dict(result, status="already_present")))
        return 0
    if os.path.lexists(path):
        return fail(f"{path} exists but is not a folder", 5, path=path)
    code, output = run_get(ghq, url, env)
    sys.stderr.write(output or "")
    if code is None:
        return fail(f"ghq get timed out after {GHQ_TIMEOUT}s and was "
                    "stopped", 4, path=path)
    if code != 0:
        return fail(f"ghq get exited {code}; its output is in stderr", 5,
                    path=path)
    if not os.path.isdir(path):
        return fail(f"ghq get finished, but {path} is not there; check "
                    "ghq root", 5, path=path)
    print(json.dumps(dict(result, status="cloned")))
    return 0


def main(argv: list) -> int:
    if len(argv) != 3 or argv[1] != "get":
        return fail(USAGE, 2)
    return cmd_get(argv[2])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
