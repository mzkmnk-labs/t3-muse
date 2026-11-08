#!/usr/bin/env python3
"""Unit tests for skill_audit.py, using throwaway git repositories."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).parent))
import skill_audit  # noqa: E402

SCRIPT = Path(__file__).with_name("skill_audit.py")
SETTINGS = Path(__file__).resolve().parent.parent / "settings.json"
SKILL = skill_audit.SKILL_PATH
RULE = f"このリポジトリでは、skill が記述する面を変えたら同じ変更で {SKILL} も更新します。本当に影響がない場合は、その旨を PR 説明に書きます。"
HEREDOC_COMMIT = """git commit -m "$(cat <<'EOF'
Title

Body with 'quotes' and \"git status\"
EOF
)\""""

# Keeps the machine's git config from changing what the tests see.
GIT_ENV = {
    **os.environ,
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.invalid",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.invalid",
}


class SurfaceAreaTests(unittest.TestCase):
    def test_paths_the_skill_describes(self):
        for path, area in (
            ("bridge/bridge.py", "bridge/"),
            ("bridge/README.md", "bridge/"),
            ("bridge/com.muse.t3bridge.plist", "bridge/"),
            ("tools/t3w.py", "tools/"),
            ("installer/t3-muse.js", "installer/"),
            ("docs/architecture.svg", "docs/"),
            ("README.md", "README.md"),
            ("README.ja.md", "README.ja.md"),
            ("setup.md", "setup.md"),
            ("setup.ja.md", "setup.ja.md"),
        ):
            with self.subTest(path=path):
                self.assertEqual(skill_audit.surface_area(path), area)

    def test_paths_left_out(self):
        for path in (
            SKILL,
            "skills/t3-muse-starter/skill.yaml",
            ".claude/settings.json",
            ".claude/hooks/skill_audit.py",
            ".claude/hooks/README.md",
            ".github/workflows/ci.yml",
            ".gitignore",
        ):
            with self.subTest(path=path):
                self.assertIsNone(skill_audit.surface_area(path))

    def test_tests_inside_an_area_are_left_out(self):
        for path in (
            "bridge/test_bridge.py",
            "tools/test_t3r.py",
            "tools/t3r_test.py",
            "tools/tests/fixture.json",
        ):
            with self.subTest(path=path):
                self.assertIsNone(skill_audit.surface_area(path))

    def test_names_that_only_resemble_a_test_stay_in(self):
        for path in ("tools/testing.py", "tools/contest.py", "bridge/test-plan.md"):
            with self.subTest(path=path):
                self.assertIsNotNone(skill_audit.surface_area(path))

    def test_matching_stops_at_path_boundaries(self):
        for path in (
            "bridge-old/bridge.py",
            "tools.py",
            "docs",
            "README.md.bak",
            "README.txt",
            "sub/README.md",
            "sub/tools/t3r.py",
            "setup.md.bak",
            "setup.txt",
            "sub/setup.md",
            "sub/setup.ja.md",
        ):
            with self.subTest(path=path):
                self.assertIsNone(skill_audit.surface_area(path))


class FindDriftTests(unittest.TestCase):
    def test_surface_change_without_skill_is_drift(self):
        drift = skill_audit.find_drift({"tools/t3w.py", "bridge/bridge.py", ".gitignore"})
        self.assertEqual(drift, ["bridge/bridge.py", "tools/t3w.py"])

    def test_skill_in_the_change_clears_drift(self):
        self.assertEqual(skill_audit.find_drift({"tools/t3w.py", SKILL}), [])

    def test_sibling_of_the_skill_does_not_clear_drift(self):
        drift = skill_audit.find_drift({"tools/t3w.py", "skills/t3-muse-starter/skill.yaml"})
        self.assertEqual(drift, ["tools/t3w.py"])

    def test_changes_outside_the_surface_are_not_drift(self):
        self.assertEqual(
            skill_audit.find_drift({".claude/hooks/skill_audit.py", "tools/test_t3w.py", ".gitignore"}),
            [],
        )

    def test_empty_change_is_not_drift(self):
        self.assertEqual(skill_audit.find_drift(set()), [])


class AuditedCommandTests(unittest.TestCase):
    def test_commit_family(self):
        for command in (
            "git commit -m x",
            "git commit",
            HEREDOC_COMMIT,
            "git -C /some/dir commit -m x",
            "git -C '/dir with space' commit -m x",
            "git -c user.name=x commit -m x",
            "git --no-pager commit -m x",
            "cd x && git commit -m y",
            "git add -A && git commit -m y && git push",
            "FOO=bar git commit -m x",
            "timeout 30 git commit -m x",
            "/usr/bin/git commit -m x",
            "sh -c 'git commit -m x'",
        ):
            with self.subTest(command=command):
                self.assertTrue(skill_audit.is_audited_command(command, "commit"))

    def test_pr_create_family(self):
        for command in (
            "gh pr create --fill",
            "wrapper gh pr create --title x --body-file body.md",
            "git push -u origin x && gh pr create --fill",
        ):
            with self.subTest(command=command):
                self.assertTrue(skill_audit.is_audited_command(command, "pr-create"))

    def test_commands_outside_a_family(self):
        for family, command in (
            ("commit", "git status"),
            ("commit", "git push"),
            ("commit", "echo $(date)"),
            ("commit", "gh pr create --fill"),
            ("pr-create", "gh pr view 1"),
            ("pr-create", "gh pr list"),
            ("pr-create", HEREDOC_COMMIT),
            ("pr-create", "echo `date`"),
        ):
            with self.subTest(family=family, command=command):
                self.assertFalse(skill_audit.is_audited_command(command, family))

    def test_no_family_means_any_family(self):
        self.assertTrue(skill_audit.is_audited_command("git commit -m x"))
        self.assertTrue(skill_audit.is_audited_command("gh pr create --fill"))
        self.assertFalse(skill_audit.is_audited_command("git status"))

    def test_unknown_family_matches_nothing(self):
        self.assertFalse(skill_audit.is_audited_command("git commit -m x", "bogus"))


class RepoTestCase(unittest.TestCase):
    """A repository whose origin/main holds the skill and one file per area."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.git("init", "-q", "-b", "main")
        for path in (SKILL, "tools/t3w.py", "bridge/bridge.py", "README.md", ".claude/hooks/keep.txt"):
            self.write(path)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("checkout", "-q", "-b", "feature")

    def git(self, *args):
        subprocess.run(["git", *args], cwd=self.root, env=GIT_ENV, check=True, capture_output=True)

    def write(self, path, text="original\n"):
        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    def commit_all(self, message="change"):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)


class ChangedPathsTests(RepoTestCase):
    def changed(self):
        return skill_audit.changed_paths(self.root)

    def test_clean_branch_has_no_changes(self):
        self.assertEqual(self.changed(), set())

    def test_committed_change(self):
        self.write("tools/t3w.py", "edited\n")
        self.commit_all()
        self.assertEqual(self.changed(), {"tools/t3w.py"})

    def test_staged_change(self):
        self.write("tools/t3w.py", "edited\n")
        self.git("add", "tools/t3w.py")
        self.assertEqual(self.changed(), {"tools/t3w.py"})

    def test_unstaged_change(self):
        self.write("tools/t3w.py", "edited\n")
        self.assertEqual(self.changed(), {"tools/t3w.py"})

    def test_staged_change_undone_on_disk(self):
        self.write("tools/t3w.py", "edited\n")
        self.git("add", "tools/t3w.py")
        self.write("tools/t3w.py")
        self.assertEqual(self.changed(), {"tools/t3w.py"})

    def test_untracked_file_is_ignored(self):
        self.write("tools/new.py")
        self.assertEqual(self.changed(), set())

    def test_rename_lists_both_ends(self):
        self.git("mv", "tools/t3w.py", "moved.py")
        self.commit_all()
        self.assertEqual(self.changed(), {"tools/t3w.py", "moved.py"})

    def test_deleted_file(self):
        self.git("rm", "-q", "tools/t3w.py")
        self.commit_all()
        self.assertEqual(self.changed(), {"tools/t3w.py"})

    def test_non_ascii_path_is_not_quoted(self):
        self.write("tools/日本語.py")
        self.commit_all()
        self.assertEqual(self.changed(), {"tools/日本語.py"})

    def test_commits_on_main_after_the_fork_do_not_count(self):
        self.git("checkout", "-q", "main")
        self.write("bridge/bridge.py", "landed on main\n")
        self.commit_all()
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")
        self.git("checkout", "-q", "feature")
        self.assertEqual(self.changed(), set())

    def test_missing_origin_main_raises(self):
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        with self.assertRaises(skill_audit.GitError):
            self.changed()


class CommandLineTests(RepoTestCase):
    """Runs a copy of the script from inside the repository it audits.

    The copy sits where the real one does, because the script finds the
    repository root from its own location.
    """

    def setUp(self):
        super().setUp()
        self.script = self.root / ".claude" / "hooks" / "skill_audit.py"
        self.script.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(SCRIPT, self.script)
        self.git("add", ".claude/hooks/skill_audit.py")
        self.git("commit", "-q", "-m", "add audit")
        self.git("update-ref", "refs/remotes/origin/main", "HEAD")

    def run_script(self, *args, stdin="", cwd=None):
        return subprocess.run(
            [sys.executable, str(self.script), *args],
            cwd=cwd or self.root,
            env=GIT_ENV,
            input=stdin,
            capture_output=True,
            text=True,
        )

    def run_post_tool(self, command, family="commit", **kwargs):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        args = ("--hook", "post-tool") + ((family,) if family else ())
        return self.run_script(*args, stdin=payload, **kwargs)

    def run_stop(self, active=False):
        payload = json.dumps({"hook_event_name": "Stop", "stop_hook_active": active})
        return self.run_script("--hook", "stop", stdin=payload)

    def context(self, result, event):
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(set(output), {"hookSpecificOutput"})
        self.assertEqual(output["hookSpecificOutput"]["hookEventName"], event)
        return output["hookSpecificOutput"]["additionalContext"]

    def make_drift(self):
        self.write("tools/t3w.py", "# comment\n")
        self.commit_all()

    def assert_silent_success(self, result):
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_check_clean(self):
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ドリフトなし", result.stdout)

    def test_check_drift(self):
        self.make_drift()
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("tools/t3w.py", result.stdout)
        self.assertIn(RULE, result.stdout)

    def test_check_clean_when_skill_changed_too(self):
        self.make_drift()
        self.write(SKILL, "updated\n")
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_check_without_origin_main_is_exit_2(self):
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("ドリフトなし", result.stdout)

    def test_usage_errors_are_exit_2(self):
        for args in ((), ("--bogus",), ("--check", "extra")):
            with self.subTest(args=args):
                self.assertEqual(self.run_script(*args).returncode, 2)

    def test_check_reports_a_new_setup_file(self):
        # The setup guides are part of the surface before they exist; adding
        # one counts like any other change to it.
        self.write("setup.md", "# Setup\n")
        self.commit_all()
        result = self.run_script("--check")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("- setup.md", result.stdout)

    def test_post_tool_reports_drift_on_commit(self):
        self.make_drift()
        context = self.context(self.run_post_tool('git commit -m "x"'), "PostToolUse")
        self.assertIn("tools/t3w.py", context)
        self.assertIn(RULE, context)
        # The facts come before the file list, so a cut-short list keeps them.
        self.assertLess(context.index(RULE), context.index("- tools/t3w.py"))

    def test_post_tool_matches_each_family(self):
        self.make_drift()
        for family, command in (
            ("commit", HEREDOC_COMMIT),
            ("commit", "cd x && git commit -m y"),
            ("commit", "git -C /some/dir commit -m y"),
            ("pr-create", "gh pr create --fill"),
            ("pr-create", "wrapper gh pr create --fill"),
        ):
            with self.subTest(family=family, command=command):
                self.assertIn("tools/t3w.py", self.context(self.run_post_tool(command, family), "PostToolUse"))

    def test_post_tool_stays_quiet_for_the_other_family(self):
        # Claude Code runs both handlers for a command holding $(), so the
        # handler for the other family must not report the same drift again.
        self.make_drift()
        self.assert_silent_success(self.run_post_tool(HEREDOC_COMMIT, "pr-create"))
        self.assert_silent_success(self.run_post_tool("gh pr create --fill", "commit"))

    def test_post_tool_without_a_family_checks_every_family(self):
        self.make_drift()
        for command in ("git commit -m x", "gh pr create --fill"):
            with self.subTest(command=command):
                self.assertIn("tools/t3w.py", self.context(self.run_post_tool(command, None), "PostToolUse"))

    def test_post_tool_ignores_other_commands(self):
        self.make_drift()
        for family in ("commit", "pr-create", None):
            for command in ("git status", "git push", "gh pr view 1", "ls", "echo $(date)"):
                with self.subTest(family=family, command=command):
                    self.assert_silent_success(self.run_post_tool(command, family))

    def test_post_tool_silent_without_drift(self):
        self.assert_silent_success(self.run_post_tool("git commit -m x"))

    def test_post_tool_audits_this_repo_from_any_directory(self):
        self.make_drift()
        with tempfile.TemporaryDirectory() as elsewhere:
            result = self.run_post_tool("git commit -m x", cwd=elsewhere)
        self.assertIn("tools/t3w.py", json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_stop_continues_the_turn_on_drift(self):
        # additionalContext keeps the turn going once without being labelled a
        # hook error; context() also checks that no "decision" rides along.
        self.make_drift()
        context = self.context(self.run_stop(), "Stop")
        self.assertIn("tools/t3w.py", context)
        self.assertIn(RULE, context)

    def test_stop_lets_go_when_already_active(self):
        self.make_drift()
        self.assert_silent_success(self.run_stop(active=True))

    def test_stop_silent_without_drift(self):
        self.assert_silent_success(self.run_stop())

    def test_hooks_fail_open(self):
        self.make_drift()
        cases = {
            "garbage stdin": ("post-tool", "not json"),
            "empty stdin": ("stop", ""),
            "non-object stdin": ("stop", "[]"),
            "non-string command": ("post-tool", '{"tool_input": {"command": 5}}'),
            "non-object tool_input": ("post-tool", '{"tool_input": 5}'),
            "missing command": ("post-tool", '{"tool_input": {}}'),
            "unknown mode": ("bogus", "{}"),
        }
        for name, (mode, stdin) in cases.items():
            with self.subTest(name):
                self.assert_silent_success(self.run_script("--hook", mode, stdin=stdin))

    def test_malformed_hook_arguments_fail_open(self):
        self.make_drift()
        commit = json.dumps({"tool_input": {"command": "git commit -m x"}})
        for args in (
            ("post-tool", "bogus"),
            ("post-tool", "commit", "extra"),
            ("stop", "commit"),
        ):
            with self.subTest(args=args):
                self.assert_silent_success(self.run_script("--hook", *args, stdin=commit))

    def test_hook_without_a_mode_fails_open(self):
        self.assert_silent_success(self.run_script("--hook", stdin="{}"))

    def test_registered_commands_audit_through_the_shell(self):
        # Runs each command exactly as settings.json writes it, with the
        # project dir pointing at this repository and its copy of the script.
        self.make_drift()
        env = {**GIT_ENV, "CLAUDE_PROJECT_DIR": str(self.root)}
        commands = {"commit": HEREDOC_COMMIT, "pr-create": "wrapper gh pr create --fill"}
        for event, handler in registered_handlers():
            if event == "PostToolUse":
                payload = {"tool_input": {"command": commands[handler_family(handler)]}}
            else:
                payload = {"stop_hook_active": False}
            with self.subTest(event=event, command=handler["command"]):
                result = subprocess.run(
                    handler["command"], shell=True, cwd=self.root, env=env,
                    input=json.dumps(payload), capture_output=True, text=True,
                )
                self.assertIn("tools/t3w.py", self.context(result, event))

    def test_hooks_fail_open_when_git_cannot_answer(self):
        self.make_drift()
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        self.assert_silent_success(self.run_post_tool("git commit -m x"))
        self.assert_silent_success(self.run_stop())


def registered_handlers():
    """Return (event, handler) for every hook handler in settings.json."""
    hooks = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]
    return [
        (event, handler)
        for event, groups in hooks.items()
        for group in groups
        for handler in group["hooks"]
    ]


def handler_family(handler):
    """Return the family a PostToolUse handler passes to --hook post-tool."""
    words = handler["command"].split()
    return words[words.index("post-tool") + 1]


class SettingsTests(unittest.TestCase):
    PREFIX = 'python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/skill_audit.py" --hook '

    def setUp(self):
        self.hooks = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]
        self.handlers = registered_handlers()

    def test_only_post_tool_and_stop_are_registered(self):
        self.assertEqual(set(self.hooks), {"PostToolUse", "Stop"})
        self.assertEqual([group.get("matcher") for group in self.hooks["PostToolUse"]], ["Bash"])

    def test_each_family_has_one_handler_whose_if_matches_the_script(self):
        # The "if" narrows the handler before python starts, and the script
        # checks the same pattern again, so the two must not drift apart.
        post_tool = [handler for event, handler in self.handlers if event == "PostToolUse"]
        families = [handler_family(handler) for handler in post_tool]
        self.assertEqual(sorted(families), sorted(skill_audit.AUDITED_COMMANDS))
        for handler, family in zip(post_tool, families):
            with self.subTest(family=family):
                self.assertEqual(handler["if"], f"Bash({skill_audit.AUDITED_COMMANDS[family]})")
                self.assertEqual(handler["command"], f"{self.PREFIX}post-tool {family} || true")

    def test_stop_handler_has_no_if(self):
        # Claude Code never runs a handler with "if" on a non-tool event.
        stop = [handler for event, handler in self.handlers if event == "Stop"]
        self.assertEqual(len(stop), 1)
        self.assertNotIn("if", stop[0])
        self.assertEqual(stop[0]["command"], f"{self.PREFIX}stop || true")

    def test_commands_hold_no_shell_glob(self):
        # Shell form hands the command to sh, which would expand a bare "*".
        for event, handler in self.handlers:
            with self.subTest(event=event, command=handler["command"]):
                self.assertNotIn("*", handler["command"])

    def test_timeout_covers_the_git_calls(self):
        # Three git calls at most, one merge-base and two diffs.
        for event, handler in self.handlers:
            with self.subTest(event=event):
                self.assertGreater(handler["timeout"], 3 * skill_audit.GIT_TIMEOUT_SECONDS)

    def test_script_finds_the_root_the_settings_assume(self):
        # The commands above start from the project root, and the script works
        # out that same root from where it sits in this checkout.
        self.assertTrue((skill_audit.REPO_ROOT / SKILL).is_file())
        self.assertEqual(skill_audit.REPO_ROOT / ".claude" / "hooks" / "skill_audit.py", SCRIPT.resolve())

    def test_a_checkout_without_the_script_does_not_block(self):
        # A session keeps its hooks after switching to a branch that has no
        # .claude/hooks/skill_audit.py, and python exits 2 for a missing
        # script, which Claude Code would treat as a Stop that never lets go.
        with tempfile.TemporaryDirectory() as empty:
            env = {**os.environ, "CLAUDE_PROJECT_DIR": empty}
            for event, handler in self.handlers:
                with self.subTest(event=event, command=handler["command"]):
                    result = subprocess.run(
                        handler["command"], shell=True, env=env, input="{}",
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
