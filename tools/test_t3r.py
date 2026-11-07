#!/usr/bin/env python3
"""Unit tests for t3r.py against a small fixture database."""
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

T3R = Path(__file__).with_name("t3r.py")


def build_fixture(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE projection_projects (
            project_id TEXT, title TEXT, workspace_root TEXT,
            scripts_json TEXT, created_at TEXT, updated_at TEXT,
            deleted_at TEXT, default_model_selection_json TEXT,
            default_thread_env_mode TEXT, favicon_path TEXT,
            auto_pull INTEGER, project_icon_json TEXT);
        CREATE TABLE projection_threads (
            thread_id TEXT, project_id TEXT, title TEXT, branch TEXT,
            worktree_path TEXT, latest_turn_id TEXT, created_at TEXT,
            updated_at TEXT, deleted_at TEXT, runtime_mode TEXT,
            interaction_mode TEXT, model_selection_json TEXT,
            archived_at TEXT, latest_user_message_at TEXT,
            pending_approval_count INTEGER, pending_user_input_count INTEGER,
            has_actionable_proposed_plan INTEGER, settled_override INTEGER,
            settled_at TEXT, snoozed_until TEXT, snoozed_at TEXT,
            title_regeneration_request_id TEXT,
            title_regeneration_started_at TEXT, pinned_at TEXT,
            pin_order_key TEXT, linked_pull_request_json TEXT,
            unsettled_at TEXT, branch_pull_request_json TEXT,
            active_order_key TEXT, title_state_json TEXT,
            auto_settle_disabled_at TEXT);
        CREATE TABLE projection_thread_sessions (
            thread_id TEXT, status TEXT, provider_name TEXT,
            provider_session_id TEXT, provider_thread_id TEXT,
            active_turn_id TEXT, last_error TEXT, updated_at TEXT,
            runtime_mode TEXT, provider_instance_id TEXT);
        INSERT INTO projection_projects (project_id, title)
            VALUES ('p1', 'Demo Project');
        INSERT INTO projection_threads
            (thread_id, project_id, title, branch, updated_at,
             latest_user_message_at, pending_approval_count,
             pending_user_input_count)
            VALUES ('t1', 'p1', 'Fix login bug', 'main',
                    '2026-11-07T10:00:00Z', '2026-11-07T09:00:00Z', 2, 0);
        INSERT INTO projection_threads
            (thread_id, project_id, title, branch, updated_at,
             pending_approval_count, pending_user_input_count)
            VALUES ('t2', 'p1', 'Write docs', 'docs',
                    '2026-11-06T10:00:00Z', 0, 1);
        INSERT INTO projection_threads
            (thread_id, project_id, title, updated_at, archived_at)
            VALUES ('t3', 'p1', 'Old task', '2026-11-01T10:00:00Z',
                    '2026-11-02T10:00:00Z');
        INSERT INTO projection_threads
            (thread_id, project_id, title, updated_at, deleted_at)
            VALUES ('t4', 'p1', 'Deleted task', '2026-11-01T10:00:00Z',
                    '2026-11-03T10:00:00Z');
        INSERT INTO projection_thread_sessions
            (thread_id, status, provider_name, last_error)
            VALUES ('t1', 'running', 'claude-code', NULL);
        """
    )
    con.commit()
    con.close()


class T3rTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t3r-test-")
        cls.db = Path(cls.tmp) / "fixture.sqlite"
        build_fixture(cls.db)

    def run_t3r(self, *args, db=None):
        env = dict(os.environ, T3M_DB=str(db or self.db))
        proc = subprocess.run(
            [sys.executable, str(T3R), *args],
            env=env, capture_output=True, text=True, timeout=30)
        return proc.returncode, json.loads(proc.stdout)

    def test_health(self):
        code, out = self.run_t3r("health")
        self.assertEqual(code, 0)
        self.assertTrue(out["exists"])
        self.assertEqual(out["table_count"], 3)
        self.assertEqual(out["open_mode"], "ro")

    def test_schema(self):
        code, out = self.run_t3r("schema")
        self.assertEqual(code, 0)
        names = [t["name"] for t in out["tables"]]
        self.assertIn("projection_threads", names)

    def test_status(self):
        code, out = self.run_t3r("status")
        self.assertEqual(code, 0)
        self.assertEqual(out["counts"], {
            "visible_threads": 2,
            "archived_threads": 1,
            "threads_waiting_approval": 1,
            "threads_waiting_input": 1,
        })
        ids = [t["thread_id"] for t in out["threads"]]
        self.assertEqual(ids, ["t1", "t2"])  # newest first, no archived
        first = out["threads"][0]
        self.assertEqual(first["project_title"], "Demo Project")
        self.assertEqual(first["pending_approval_count"], 2)
        self.assertEqual(first["session_status"], "running")
        self.assertEqual(first["provider"], "claude-code")

    def test_missing_db(self):
        code, out = self.run_t3r("health", db=Path(self.tmp) / "nope.sqlite")
        self.assertEqual(code, 3)
        self.assertIn("not found", out["error"])

    def test_usage(self):
        code, out = self.run_t3r("bogus")
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
