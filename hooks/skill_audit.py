#!/usr/bin/env python3
"""Audit whether skills/t3-muse-starter/SKILL.md kept up with this branch.

The skill describes how bridge/ and tools/ behave, but nothing forces a change
there to touch the skill as well, so it goes stale silently. This compares the
branch with origin/main and reports drift when a path the skill describes has
changed and the skill has not.

  --check            report for people; exit 0 clean, 1 drift, 2 usage or git error
  --hook post-tool   Claude Code PostToolUse hook, audits git commit and gh pr create
  --hook stop        Claude Code Stop hook

Hook modes fail open: any unexpected error prints nothing and exits 0.
"""
from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BASE_REF = "origin/main"
SKILL_PATH = "skills/t3-muse-starter/SKILL.md"
GIT_TIMEOUT_SECONDS = 10

# Commands whose completion is worth an audit. Matched as substrings, so a
# wrapped call such as "agent-auth gh pr create" counts too.
AUDITED_COMMANDS = ("git commit", "gh pr create")

# The areas the skill describes. An entry ending in "/" covers that directory
# and any other entry is one exact file. A path outside this table never counts
# as drift, which keeps these out on purpose:
#   skills/   the skill itself
#   .claude/  the wiring of this audit
#   hooks/    this audit
SKILL_SURFACE = (
    "bridge/",  # mailbox daemon, restarter, LaunchAgent plists
    "tools/",  # t3r, t3w, t3a
    "docs/",  # architecture diagram
    "README.md",  # setup and operation, English
    "README.ja.md",  # setup and operation, Japanese
)

# Tests sit beside the code they cover inside the areas above, but the skill
# does not describe them, so they are checked for and excluded first.
TEST_FILE_GLOBS = ("test_*", "*_test.*")
TEST_DIRECTORY = "tests"


class GitError(Exception):
    """A git command failed, for example because origin/main is missing."""


def is_test(path: str) -> bool:
    *directories, name = path.split("/")
    return TEST_DIRECTORY in directories or any(
        fnmatch.fnmatchcase(name, glob) for glob in TEST_FILE_GLOBS
    )


def surface_area(path: str) -> str | None:
    """Return the SKILL_SURFACE entry that covers path, or None."""
    if is_test(path):
        return None
    for area in SKILL_SURFACE:
        if path.startswith(area) if area.endswith("/") else path == area:
            return area
    return None


def find_drift(paths) -> list[str]:
    """Return the changed surface paths, or nothing if the skill changed too."""
    if SKILL_PATH in paths:
        return []
    return sorted(path for path in paths if surface_area(path))


def git(root: Path, *args: str) -> str:
    # A hook runs beside the agent's own git commands, so it must not take the
    # index lock that a plain "git diff" would grab to refresh the index.
    result = subprocess.run(
        ["git", "--no-optional-locks", *args],
        cwd=root,
        capture_output=True,
        timeout=GIT_TIMEOUT_SECONDS,
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args)}: {detail}")
    return result.stdout.decode("utf-8", "surrogateescape")


def merge_base(root: Path = REPO_ROOT) -> str:
    return git(root, "merge-base", "HEAD", BASE_REF).strip()


def changed_paths(root: Path = REPO_ROOT, base: str | None = None) -> set[str]:
    """Return tracked paths changed since the merge base with origin/main.

    Comparing the working tree with the base covers commits, staged edits and
    unstaged edits at once, except for a staged edit later undone on disk, which
    only the --cached side sees. --no-renames lists both ends of a rename, so
    moving a file out of a surface area still counts as changing that area.
    """
    base = base or merge_base(root)
    paths: set[str] = set()
    for extra in ((), ("--cached",)):
        output = git(root, "diff", "--name-only", "--no-renames", "-z", *extra, base)
        paths.update(path for path in output.split("\0") if path)
    return paths


def drift_message(drift: list[str]) -> str:
    files = "\n".join(f"- {path}" for path in drift)
    return (
        f"skill audit: {BASE_REF} からの変更に skill の対象領域が含まれていますが、"
        f"{SKILL_PATH} は変更されていません。\n"
        f"ドリフトしたファイル:\n{files}\n"
        f"同じ変更で {SKILL_PATH} を更新すること。"
        "本当に影響がない場合はその旨を PR 説明に書くこと。"
    )


def hook_response(mode: str, payload: dict) -> dict | None:
    """Return the JSON a hook should print, or None to stay silent."""
    if mode == "post-tool":
        command = payload.get("tool_input", {}).get("command", "")
        if not any(audited in command for audited in AUDITED_COMMANDS):
            return None
    elif mode == "stop":
        # Set while Claude is already continuing because of a Stop hook. Blocking
        # again here would never let the session end.
        if payload.get("stop_hook_active"):
            return None
    else:
        return None

    drift = find_drift(changed_paths())
    if not drift:
        return None
    message = drift_message(drift)
    if mode == "stop":
        return {"decision": "block", "reason": message}
    return {
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": message,
        }
    }


def run_hook(mode: str | None) -> int:
    try:
        response = hook_response(mode, json.load(sys.stdin))
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    except Exception:
        # An audit must never break the session it is advising.
        pass
    return 0


def run_check() -> int:
    # Paths are decoded with surrogateescape, so keep them printable in any locale.
    sys.stdout.reconfigure(errors="backslashreplace")
    try:
        base = merge_base()
        paths = changed_paths(base=base)
    except (GitError, OSError, subprocess.SubprocessError) as error:
        print(f"skill audit: 監査できませんでした: {error}", file=sys.stderr)
        return 2

    surface = sum(1 for path in paths if surface_area(path))
    print(f"基点: {BASE_REF} との merge-base ({base[:7]})")
    print(f"変更パス: {len(paths)} 件 / うち対象領域: {surface} 件")
    drift = find_drift(paths)
    if drift:
        print(drift_message(drift))
        return 1
    print("skill audit: ドリフトなし")
    return 0


def main(argv: list[str]) -> int:
    # Hook arguments are handled before anything can reject them: exit 2 tells
    # Claude Code to block, and a Stop hook that always blocks never lets go.
    if argv[:1] == ["--hook"]:
        return run_hook(argv[1] if len(argv) == 2 else None)
    if argv == ["--check"]:
        return run_check()
    print("usage: skill_audit.py --check | --hook {post-tool,stop}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
