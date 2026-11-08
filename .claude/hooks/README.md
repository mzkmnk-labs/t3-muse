# Skill audit hook

English | [日本語](README.ja.md)

An internal tool that keeps the three Muse skills, `muse/skills/t3-muse-starter/SKILL.md` (setup), `muse/skills/t3-muse-pm/SKILL.md` (day-to-day operation) and `muse/skills/t3-muse-bridge/SKILL.md` (using the bridge), and the docs and code they describe from drifting apart silently. The skills describe how the bridge, the tools, the installer, the diagrams, the READMEs and the setup steps behave. Changing one of those doesn't force anyone to fix a skill too. A surface that has changed while none of the skills has is called drift, and this hook tells Claude about it at commit time, at PR creation, and at the end of a turn.

## What it does

It computes the change set since the merge base of the current branch and `origin/main`. The set covers tracked changes that are committed, staged or unstaged. When a surface area has changed and none of the three `SKILL.md` files is in the set, it reports drift. A change to at least one of the three counts as updating the skills. The surface is shared, so the audit doesn't decide which skill a path belongs to; that is left to whoever makes the change.

- Surface: `bridge/`, `tools/`, `installer/`, `docs/`, `README.md`, `README.ja.md`, `setup.md`, `setup.ja.md`
- Left out: `muse/skills/`, `.claude/` (this audit and its wiring), and tests (`test_*`, `*_test.*`, anything under `tests/`)

The surface table lives in one place, `SKILL_SURFACE` in `skill_audit.py`, and the three guarded files are `SKILL_PATHS` beside it. A `skill.yaml` change alone doesn't count.

## When it fires

Following Claude Code's conventions, the script sits in `.claude/hooks/`, and `.claude/settings.json` registers it as `$CLAUDE_PROJECT_DIR/.claude/hooks/skill_audit.py`. When you edit the settings file during a session, Claude Code's file watcher picks up the change and applies it. If it doesn't, restart the session.

| Event | Narrowed by | What happens |
| --- | --- | --- |
| PostToolUse (Bash) | `if: "Bash(*git *commit*)"`, `--hook post-tool commit` | On drift, adds the file list and the repo rule to Claude's context |
| PostToolUse (Bash) | `if: "Bash(*gh pr create*)"`, `--hook post-tool pr-create` | Same as above |
| Stop | Nothing | When `stop_hook_active` is false and there is drift, hands over the same text and keeps the turn going once |

### Narrowing the commands

The previous version started python on every Bash call and matched substrings of the command inside the script. Now each handler's `if` narrows the call before Claude Code starts python. An `if` holds one rule per handler, so there are two handlers, one for commits and one for PR creation.

- The leading `*` in each pattern is for wrappers in front of the command. Measured: `Bash(gh pr create *)` did not match a wrapped form such as `wrapper gh pr create`, and `Bash(git commit *)` did not match `timeout 30 git commit` or `nohup git commit`. The wrapper stripping that the docs describe for permission rules doesn't apply to `if`.
- `*git *commit*` also catches `git -C <dir> commit`, `git -c k=v commit` and `git --no-pager commit`, which the old substring match missed. `/usr/bin/git commit`, `sh -c 'git commit ...'` and `nohup git commit` match too. All of these were measured on 2.1.319.
- The script checks the whole command against the same pattern. As the docs say, `if` is best effort: on a command holding `$()` or backticks, every handler whose pattern names more than a program starts. A command holding a variable such as `$PWD` can start them too. Claude's usual commit, `git commit -m "$(cat <<'EOF' ... )"`, is one of these. Each handler passes its family name, and the script looks only at that family's pattern, so one commit gets one audit.
- The pattern table is `AUDITED_COMMANDS` in the script. A test checks that it matches the `if` in `settings.json`. `--hook post-tool` without a family name checks every family.

Behavior measured on Claude Code 2.1.319:

| Command | Handlers started | Output |
| --- | --- | --- |
| `git add -A && git commit -m "$(cat <<'MSG' ... )"` | commit | One audit |
| `git -C "$PWD" commit ...` | commit, pr-create | Only commit reports |
| `gh pr create --fill`, `wrapper gh pr create --fill` | pr-create | One audit |
| `echo $(date)` | commit, pr-create | Both silent |
| `echo "$HOME"`, `git status`, `ls` | None | Nothing |
| `git status; echo commit` | None (matched per subcommand) | Nothing |

### Holes that remain

PostToolUse can't catch these, so the Stop hook is what notices them.

- Git aliases (such as `git ci`), PRs created with `gh api`, and commits made inside another program such as `make`.
- Failed Bash calls. PostToolUse fires only for tool calls that succeed, and a failure fires PostToolUseFailure instead. If `git commit ... && git push` fails at the push, the commit is made but no audit appears.
- In the other direction, unrelated commands such as `git log --grep commit` or `echo "git commit"` can run the audit. It only reports drift that is really there, and it stops nothing.
- A single command that both commits and creates a PR gets the note twice.

## Advisory and fail-open

- Both events only advise. PostToolUse runs after the tool, so it never stops the commit or the PR creation itself.
- Stop returns `hookSpecificOutput.additionalContext` rather than `decision: "block"`. The docs recommend this form for a hook that is working as designed and giving guidance. It keeps the turn going, with the same loop protection as `block`. It reaches Claude as added hook context, not as a hook error (in a T3 session it showed as "Stop hook additional context", and the transcript's `hookErrors` was empty). The Stop at the end of the continued turn carries `stop_hook_active: true`, and the hook stays silent there, so it keeps a turn going at most once.
- On an unexpected error it prints nothing and exits 0. Malformed `--hook` arguments are treated the same way.
- The `|| true` at the end of each registered command serves the same purpose. On a branch without the script, python exits 2. On PostToolUse, the stderr of an exit 2 would reach Claude. On Stop, current Claude Code treats an exit 2 with empty stdout and a missing-file stderr as a non-blocking error, but `|| true` makes both exit 0 regardless.
- Because of `|| true`, the commands are registered in shell form (no `args`). The path is quoted as `"$CLAUDE_PROJECT_DIR/..."`. The handlers pass a family name rather than the pattern because sh would expand a `*` in a shell-form command.
- `timeout` is 60 seconds. Each git call takes at most 10 seconds, and there are at most 3. That is shorter than the 600-second default, so a stuck run never holds up the end of a turn for long. Output from a run that times out is discarded, and nothing happens.
- `onFailure` is not set. The default lets the action go ahead, and the setting has no effect on Stop.

## Claude Code version

`if` is available from 2.1.85, and Stop's `additionalContext` from 2.1.163. The `if` matching has been fixed several times since (2.1.163, 2.1.243 and others), so read the tables in this README as measurements on 2.1.319.

Firing is confirmed in T3's claudeAgent runtime and with `claude -p`. When you open a new worktree interactively, Claude Code shows its trust dialog first.

## Running it by hand

```sh
python3 .claude/hooks/skill_audit.py --check
```

Exit 0 means no drift, and exit 1 means drift. Exit 2 means a usage error, or that the audit couldn't run, for example because `origin/main` is missing.

CI runs the same check on pull requests, as the last step of the Ubuntu job. It only advises there too: drift (exit 1), or an audit that couldn't run (exit 2), shows as a warning annotation, and the job still passes.

## Things to know

- It doesn't run `git fetch`. It uses the local `origin/main` as the base, so when that is stale it can count changes that already landed on main as changes on the branch.
- It audits the checkout the script sits in, which is the project the session started in. If the session enters a worktree partway through, `$CLAUDE_PROJECT_DIR` still points at the original place, so the audit doesn't follow. T3 starts its sessions inside the worktree, so this gap doesn't arise there.
- A change that really doesn't affect any skill still counts as drift on its branch until one of the three `SKILL.md` files changes, and Stop keeps one turn going each time. In that case, say in the PR description that the skills aren't affected, and finish as usual.
