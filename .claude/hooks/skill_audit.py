#!/usr/bin/env python3
"""Audit whether skills/t3-muse-starter/SKILL.md kept up with this branch.

The skill describes the bridge, the tools, the installer, the docs and the
setup steps, but nothing forces a change to one of those to touch the skill as
well, so the skill and what it describes drift apart silently. This compares
the branch with origin/main and reports drift when a path the skill describes
has changed and the skill has not.

  --check                    report for people; exit 0 clean, 1 drift, 2 usage or git error
  --hook post-tool [FAMILY]  Claude Code PostToolUse hook for a git commit or gh pr create
  --hook stop                Claude Code Stop hook

Hook modes fail open: any unexpected error prints nothing and exits 0.
"""
from __future__ import annotations

import fnmatch
import json
import subprocess
import sys
from pathlib import Path

# This file lives at .claude/hooks/skill_audit.py, three levels below the root.
REPO_ROOT = Path(__file__).resolve().parents[2]
BASE_REF = "origin/main"
SKILL_PATH = "skills/t3-muse-starter/SKILL.md"
GIT_TIMEOUT_SECONDS = 10

# The commands worth an audit once they succeed, by family. settings.json gives
# each family its own PostToolUse handler, whose "if" is Bash(<pattern>) and
# whose command passes the family name; a test keeps the two in step. Each
# pattern starts with "*" so that a wrapper in front, such as "agent-auth gh",
# still matches, and "*git *commit*" also covers "git -C <dir> commit".
AUDITED_COMMANDS = {
    "commit": "*git *commit*",
    "pr-create": "*gh pr create*",
}

# The areas the skill describes. An entry ending in "/" covers that directory
# and any other entry is one exact file. A path outside this table never counts
# as drift, which keeps these out on purpose:
#   skills/   the skill itself
#   .claude/  this audit and its wiring
SKILL_SURFACE = (
    "bridge/",  # mailbox daemon, restarter, LaunchAgent plists
    "tools/",  # t3r, t3w, t3a
    "installer/",  # the npx install command
    "docs/",  # architecture diagrams
    "README.md",  # setup and operation, English
    "README.ja.md",  # setup and operation, Japanese
    "setup.md",  # setup steps, English
    "setup.ja.md",  # setup steps, Japanese
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


def is_audited_command(command: str, family: str | None = None) -> bool:
    """Return whether command belongs to family, or to any family if None.

    The handler's "if" has already narrowed the call, but Claude Code runs every
    handler whose pattern names more than a program on any command holding $()
    or backticks, and the usual heredoc commit message holds $(). Matching the
    whole command text against this handler's own pattern keeps the other
    handlers quiet, so one commit gets one audit.
    """
    if family is None:
        patterns = AUDITED_COMMANDS.values()
    elif family in AUDITED_COMMANDS:
        patterns = (AUDITED_COMMANDS[family],)
    else:
        return False
    return any(fnmatch.fnmatchcase(command, pattern) for pattern in patterns)


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
    # Hooks hand this to Claude as context, which reads best as plain facts
    # rather than commands. The file list goes last, so the facts survive if a
    # long list is ever cut short.
    files = "\n".join(f"- {path}" for path in drift)
    return (
        f"skill audit: {BASE_REF} からの変更に skill の対象領域が含まれていますが、"
        f"{SKILL_PATH} は変更されていません。\n"
        "このリポジトリでは、skill が記述する面を変えたら同じ変更で "
        f"{SKILL_PATH} も更新します。"
        "本当に影響がない場合は、その旨を PR 説明に書きます。\n"
        f"ドリフトしたファイル:\n{files}"
    )


def hook_response(mode: str, payload: dict, family: str | None = None) -> dict | None:
    """Return the JSON a hook should print, or None to stay silent."""
    if mode == "post-tool":
        command = payload.get("tool_input", {}).get("command")
        if not isinstance(command, str) or not is_audited_command(command, family):
            return None
    elif mode == "stop" and family is None:
        # Set while Claude is already continuing because of a Stop hook. Asking
        # again would hold the turn open until Claude Code's own cap ends it.
        if payload.get("stop_hook_active"):
            return None
    else:
        return None

    drift = find_drift(changed_paths())
    if not drift:
        return None
    event = "PostToolUse" if mode == "post-tool" else "Stop"
    # On Stop, additionalContext keeps the turn going once, as decision "block"
    # would, but Claude Code shows it as hook feedback instead of a hook error.
    return {"hookSpecificOutput": {"hookEventName": event, "additionalContext": drift_message(drift)}}


def run_hook(args: list[str]) -> int:
    try:
        if 1 <= len(args) <= 2:
            mode, family = args[0], (args[1] if len(args) == 2 else None)
            response = hook_response(mode, json.load(sys.stdin), family)
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
    # Hook arguments are handled before anything can reject them. Exit 2 would
    # put the usage text in front of Claude after the tool call, or ask a Stop
    # hook to keep going, so a malformed hook call stays silent instead.
    if argv[:1] == ["--hook"]:
        return run_hook(argv[1:])
    if argv == ["--check"]:
        return run_check()
    print(
        "usage: skill_audit.py --check | --hook post-tool [commit|pr-create] | --hook stop",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
