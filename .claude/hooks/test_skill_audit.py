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
INSTRUCTION = f"同じ変更で {SKILL} を更新すること。本当に影響がない場合はその旨を PR 説明に書くこと"

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

    def run_post_tool(self, command, **kwargs):
        payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
        return self.run_script("--hook", "post-tool", stdin=payload, **kwargs)

    def run_stop(self, active=False):
        payload = json.dumps({"stop_hook_active": active})
        return self.run_script("--hook", "stop", stdin=payload)

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
        self.assertIn(INSTRUCTION, result.stdout)

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

    def test_post_tool_reports_drift_on_commit(self):
        self.make_drift()
        result = self.run_post_tool('git commit -m "x"')
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)["hookSpecificOutput"]
        self.assertEqual(output["hookEventName"], "PostToolUse")
        self.assertIn("tools/t3w.py", output["additionalContext"])
        self.assertIn(INSTRUCTION, output["additionalContext"])

    def test_post_tool_matches_pr_create_and_wrapped_calls(self):
        self.make_drift()
        for command in ("gh pr create --fill", "wrapper gh pr create --fill", "cd x && git commit -m y"):
            with self.subTest(command=command):
                self.assertIn("hookSpecificOutput", json.loads(self.run_post_tool(command).stdout))

    def test_post_tool_ignores_other_commands(self):
        self.make_drift()
        for command in ("git status", "git push", "gh pr view 1", "ls"):
            with self.subTest(command=command):
                self.assert_silent_success(self.run_post_tool(command))

    def test_post_tool_silent_without_drift(self):
        self.assert_silent_success(self.run_post_tool("git commit -m x"))

    def test_post_tool_audits_this_repo_from_any_directory(self):
        self.make_drift()
        with tempfile.TemporaryDirectory() as elsewhere:
            result = self.run_post_tool("git commit -m x", cwd=elsewhere)
        self.assertIn("tools/t3w.py", json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"])

    def test_stop_blocks_on_drift(self):
        self.make_drift()
        result = self.run_stop()
        self.assertEqual(result.returncode, 0, result.stderr)
        output = json.loads(result.stdout)
        self.assertEqual(output["decision"], "block")
        self.assertIn("tools/t3w.py", output["reason"])
        self.assertIn(INSTRUCTION, output["reason"])

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
            "unknown mode": ("bogus", "{}"),
        }
        for name, (mode, stdin) in cases.items():
            with self.subTest(name):
                self.assert_silent_success(self.run_script("--hook", mode, stdin=stdin))

    def test_hook_without_a_mode_fails_open(self):
        self.assert_silent_success(self.run_script("--hook", stdin="{}"))

    def test_hooks_fail_open_when_git_cannot_answer(self):
        self.make_drift()
        self.git("update-ref", "-d", "refs/remotes/origin/main")
        self.assert_silent_success(self.run_post_tool("git commit -m x"))
        self.assert_silent_success(self.run_stop())


class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.hooks = json.loads(SETTINGS.read_text(encoding="utf-8"))["hooks"]

    def command(self, event):
        return self.hooks[event][0]["hooks"][0]["command"]

    def test_post_tool_is_registered_for_bash(self):
        self.assertEqual(self.hooks["PostToolUse"][0]["matcher"], "Bash")
        self.assertIn('python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/skill_audit.py" --hook post-tool', self.command("PostToolUse"))

    def test_stop_is_registered(self):
        self.assertIn('python3 "$CLAUDE_PROJECT_DIR/.claude/hooks/skill_audit.py" --hook stop', self.command("Stop"))

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
            for event in ("PostToolUse", "Stop"):
                with self.subTest(event):
                    result = subprocess.run(
                        self.command(event), shell=True, env=env, input="{}",
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0)
                    self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
