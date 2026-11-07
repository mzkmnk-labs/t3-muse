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

import t3r

T3R = Path(__file__).with_name("t3r.py")

V2_THREADS_TABLE = """
    CREATE TABLE orchestration_v2_projection_threads (
        thread_id TEXT, project_id TEXT, title TEXT, default_provider TEXT,
        runtime_mode TEXT, interaction_mode TEXT,
        active_provider_thread_id TEXT, created_at TEXT, updated_at TEXT,
        archived_at TEXT, deleted_at TEXT, payload_json TEXT,
        provider_instance_id TEXT);
"""

V2_OTHER_TABLES = """
    CREATE TABLE orchestration_v2_projection_runs (
        run_id TEXT, thread_id TEXT, ordinal INTEGER, provider TEXT,
        provider_thread_id TEXT, status TEXT, requested_at TEXT,
        completed_at TEXT, payload_json TEXT, provider_instance_id TEXT);
    CREATE TABLE orchestration_v2_projection_runtime_requests (
        runtime_request_id TEXT, thread_id TEXT, node_id TEXT,
        provider_turn_id TEXT, kind TEXT, status TEXT, created_at TEXT,
        resolved_at TEXT, payload_json TEXT);
    CREATE TABLE orchestration_v2_projection_messages (
        message_id TEXT, thread_id TEXT, run_id TEXT, node_id TEXT,
        role TEXT, streaming INTEGER, created_at TEXT, updated_at TEXT,
        payload_json TEXT);
    CREATE TABLE orchestration_v2_projection_turn_items (
        turn_item_id TEXT, thread_id TEXT, run_id TEXT, node_id TEXT,
        provider_thread_id TEXT, provider_turn_id TEXT, parent_item_id TEXT,
        ordinal INTEGER, type TEXT, status TEXT, updated_at TEXT,
        payload_json TEXT);
"""

SECRET_TABLES = """
    CREATE TABLE auth_sessions (token TEXT);
    INSERT INTO auth_sessions VALUES ('SENTINEL-AUTH');
    CREATE TABLE scheduled_tasks (webhook_secret TEXT);
    INSERT INTO scheduled_tasks VALUES ('SENTINEL-WEBHOOK');
"""

LONG_ERROR = "boom " + "x" * 400


def v2_thread(thread_id: str, updated_at: str, branch: str = None,
              relationship: str = None, archived_at: str = None,
              deleted_at: str = None, provider: str = "claudeAgent") -> tuple:
    payload = {"branch": branch,
               "lineage": {"relationshipToParent": relationship}}
    return (thread_id, "p1", f"Title of {thread_id}", provider, updated_at,
            archived_at, deleted_at, json.dumps(payload))


def insert_v2_threads(con: sqlite3.Connection, rows: list) -> None:
    con.executemany(
        "INSERT INTO orchestration_v2_projection_threads"
        " (thread_id, project_id, title, default_provider, updated_at,"
        "  archived_at, deleted_at, payload_json)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)


def insert_v2_request(con: sqlite3.Connection, request_id: str,
                      thread_id: str, kind: str, status: str) -> None:
    con.execute(
        "INSERT INTO orchestration_v2_projection_runtime_requests"
        " (runtime_request_id, thread_id, kind, status, payload_json)"
        " VALUES (?, ?, ?, ?, ?)",
        (request_id, thread_id, kind, status,
         json.dumps({"prompt": "SENTINEL-REQUEST"})))


def build_v2_fixture(path: Path) -> None:
    """The legacy fixture plus v2 tables, as on a migrated database."""
    build_fixture(path)
    con = sqlite3.connect(path)
    con.executescript(V2_THREADS_TABLE + V2_OTHER_TABLES + SECRET_TABLES)
    insert_v2_threads(con, [
        v2_thread("deleted", "2026-11-07T13:00:00.000Z",
                  deleted_at="2026-11-07T13:00:00.000Z"),
        v2_thread("archived-child", "2026-11-07T12:30:00.000Z",
                  relationship="subagent",
                  archived_at="2026-11-07T12:30:00.000Z"),
        v2_thread("archived", "2026-11-07T12:00:00.000Z",
                  archived_at="2026-11-07T12:00:00.000Z"),
        v2_thread("child-quiet", "2026-11-07T11:00:00.000Z",
                  relationship="subagent"),
        v2_thread("running", "2026-11-07T10:00:00.000Z", branch="main",
                  provider="codex"),
        v2_thread("child-waiting", "2026-11-07T09:00:00.000Z",
                  relationship="subagent"),
        v2_thread("failed", "2026-11-07T08:00:00.000Z"),
        v2_thread("recovered", "2026-11-07T07:00:00.000Z"),
        v2_thread("idle-fork", "2026-11-07T06:00:00.000Z",
                  relationship="fork", provider="opencode"),
    ])
    con.execute(
        "INSERT INTO orchestration_v2_projection_threads"
        " (thread_id, project_id, title, default_provider, updated_at,"
        "  payload_json) VALUES ('malformed', 'p1', 'Bad payload',"
        "  'claudeAgent', '2026-11-07T05:00:00.000Z', 'not json')")
    con.executemany(
        "INSERT INTO orchestration_v2_projection_runs"
        " (run_id, thread_id, ordinal, provider, status)"
        " VALUES (?, ?, ?, ?, ?)", [
            ("running:1", "running", 1, "codex", "completed"),
            ("running:2", "running", 2, "claudeAgent", "running"),
            ("failed:1", "failed", 1, "claudeAgent", "completed"),
            ("failed:2", "failed", 2, "claudeAgent", "failed"),
            ("recovered:1", "recovered", 1, "claudeAgent", "failed"),
            ("recovered:2", "recovered", 2, "claudeAgent", "completed"),
            ("child-quiet:1", "child-quiet", 1, "claudeAgent", "running"),
            ("child-waiting:1", "child-waiting", 1, "claudeAgent", "running"),
        ])
    for request_id, thread_id, kind, status in (
            ("r1", "running", "command", "pending"),
            ("r2", "running", "file-change", "pending"),
            ("r3", "running", "user_input", "pending"),
            ("r4", "running", "command", "resolved"),
            ("r5", "running", "user_input", "cancelled"),
            ("r6", "child-waiting", "command", "pending"),
            ("r7", "archived", "command", "pending"),
            ("r8", "deleted", "user_input", "pending")):
        insert_v2_request(con, request_id, thread_id, kind, status)
    con.executemany(
        "INSERT INTO orchestration_v2_projection_messages"
        " (message_id, thread_id, role, streaming, created_at, updated_at,"
        "  payload_json) VALUES (?, ?, ?, 0, ?, ?, ?)", [
            ("m1", "running", "user", "2026-11-07T08:00:00.000Z",
             "2026-11-07T08:00:00.000Z", '{"text": "SENTINEL-MESSAGE"}'),
            ("m2", "running", "user", "2026-11-07T09:00:00.000Z",
             "2026-11-07T09:00:00.000Z", '{"text": "SENTINEL-MESSAGE"}'),
            ("m3", "running", "assistant", "2026-11-07T09:30:00.000Z",
             "2026-11-07T09:30:00.000Z", '{"text": "SENTINEL-MESSAGE"}'),
        ])
    con.executemany(
        "INSERT INTO orchestration_v2_projection_turn_items"
        " (turn_item_id, thread_id, run_id, ordinal, type, status, updated_at,"
        "  payload_json) VALUES (?, ?, ?, ?, 'error', ?, ?, ?)", [
            ("e1", "failed", "failed:2", 4, "completed",
             "2026-11-07T07:50:00.000Z",
             json.dumps({"failure": {"message": "retried and recovered"}})),
            ("e2", "failed", "failed:2", 2, "failed",
             "2026-11-07T07:30:00.000Z",
             json.dumps({"failure": {"message": "an earlier failure"}})),
            ("e3", "failed", "failed:2", 3, "failed",
             "2026-11-07T07:40:00.000Z",
             json.dumps({"failure": {"message": LONG_ERROR}})),
            ("e4", "recovered", "recovered:1", 1, "failed",
             "2026-11-07T06:00:00.000Z",
             json.dumps({"failure": {"message": "fixed by the next run"}})),
            ("e5", "recovered", "recovered:2", 2, "completed",
             "2026-11-07T06:30:00.000Z",
             json.dumps({"failure": {"message": "retried and recovered"}})),
            ("e6", "running", "running:2", 3, "failed",
             "2026-11-07T09:45:00.000Z",
             json.dumps({"failure": {"message": "retrying"}})),
        ])
    con.commit()
    con.close()


def run_t3r_on(db: Path, *args) -> tuple:
    env = dict(os.environ, T3M_DB=str(db))
    proc = subprocess.run(
        [sys.executable, str(T3R), *args],
        env=env, capture_output=True, text=True, timeout=30)
    return proc.returncode, json.loads(proc.stdout), proc.stdout


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
        code, out, _ = run_t3r_on(db or self.db, *args)
        return code, out

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
        self.assertEqual(out["source"], "legacy")

    def test_missing_db(self):
        code, out = self.run_t3r("health", db=Path(self.tmp) / "nope.sqlite")
        self.assertEqual(code, 3)
        self.assertIn("not found", out["error"])

    def test_usage(self):
        code, out = self.run_t3r("bogus")
        self.assertEqual(code, 2)

    def test_status_without_projection_tables(self):
        db = Path(self.tmp) / "empty.sqlite"
        con = sqlite3.connect(db)
        con.execute("CREATE TABLE unrelated (id TEXT)")
        con.commit()
        con.close()
        code, out = self.run_t3r("status", db=db)
        self.assertEqual(code, 4)
        self.assertIn("projection_threads", out["error"])

    def test_status_falls_back_when_v2_is_incomplete(self):
        db = Path(self.tmp) / "partial.sqlite"
        build_fixture(db)
        con = sqlite3.connect(db)
        con.executescript(V2_THREADS_TABLE)
        con.commit()
        con.close()
        code, out = self.run_t3r("status", db=db)
        self.assertEqual(code, 0)
        self.assertEqual(out["source"], "legacy")
        self.assertEqual([t["thread_id"] for t in out["threads"]],
                         ["t1", "t2"])


class T3rStatusV2Test(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="t3r-v2-test-")
        cls.db = Path(cls.tmp) / "fixture.sqlite"
        build_v2_fixture(cls.db)
        cls.code, cls.out, cls.stdout = run_t3r_on(cls.db, "status")
        cls.by_id = {t["thread_id"]: t for t in cls.out["threads"]}

    def test_v2_wins_over_the_stale_legacy_tables(self):
        self.assertEqual(self.code, 0)
        self.assertEqual(self.out["source"], "orchestration_v2")
        self.assertNotIn("t1", self.by_id)

    def test_counts(self):
        self.assertEqual(self.out["counts"], {
            "visible_threads": 5,  # subagents, archived and deleted left out
            "archived_threads": 1,  # the archived subagent is left out
            "threads_waiting_approval": 2,  # includes the waiting subagent
            "threads_waiting_input": 1,
        })

    def test_list_is_newest_first_and_hides_quiet_subagents(self):
        self.assertEqual(
            [t["thread_id"] for t in self.out["threads"]],
            ["running", "child-waiting", "failed", "recovered", "idle-fork",
             "malformed"])

    def test_pending_counts_by_kind(self):
        thread = self.by_id["running"]
        self.assertEqual(thread["pending_approval_count"], 2)
        self.assertEqual(thread["pending_user_input_count"], 1)
        self.assertEqual(self.by_id["child-waiting"]["pending_approval_count"],
                         1)
        self.assertEqual(self.by_id["failed"]["pending_approval_count"], 0)
        self.assertEqual(self.by_id["failed"]["pending_user_input_count"], 0)

    def test_session_status_is_the_latest_run(self):
        statuses = {t["thread_id"]: t["session_status"]
                    for t in self.out["threads"]}
        self.assertEqual(statuses["running"], "running")
        self.assertEqual(statuses["failed"], "failed")
        self.assertEqual(statuses["recovered"], "completed")
        self.assertEqual(statuses["idle-fork"], "idle")

    def test_provider_prefers_the_latest_run(self):
        self.assertEqual(self.by_id["running"]["provider"], "claudeAgent")
        self.assertEqual(self.by_id["idle-fork"]["provider"], "opencode")

    def test_thread_fields(self):
        thread = self.by_id["running"]
        self.assertEqual(thread["title"], "Title of running")
        self.assertEqual(thread["project_title"], "Demo Project")
        self.assertEqual(thread["branch"], "main")
        self.assertEqual(thread["updated_at"], "2026-11-07T10:00:00.000Z")
        self.assertEqual(thread["latest_user_message_at"],
                         "2026-11-07T09:00:00.000Z")
        self.assertIsNone(self.by_id["idle-fork"]["latest_user_message_at"])

    def test_last_error_comes_from_a_failed_latest_run(self):
        self.assertEqual(self.by_id["failed"]["last_error"], LONG_ERROR[:300])
        self.assertIsNone(self.by_id["recovered"]["last_error"])
        self.assertIsNone(self.by_id["running"]["last_error"])

    def test_malformed_payload_reads_as_null(self):
        thread = self.by_id["malformed"]
        self.assertEqual(thread["title"], "Bad payload")
        self.assertIsNone(thread["branch"])

    def test_same_shape_as_legacy(self):
        legacy_db = Path(self.tmp) / "legacy.sqlite"
        build_fixture(legacy_db)
        _, legacy, _ = run_t3r_on(legacy_db, "status")
        self.assertEqual(list(self.out), list(legacy))
        self.assertEqual(list(self.out["counts"]), list(legacy["counts"]))
        self.assertEqual(list(self.out["threads"][0]),
                         list(legacy["threads"][0]))

    def test_no_project_table_leaves_the_title_empty(self):
        db = Path(self.tmp) / "no-projects.sqlite"
        build_v2_fixture(db)
        con = sqlite3.connect(db)
        con.execute("DROP TABLE projection_projects")
        con.commit()
        con.close()
        code, out, _ = run_t3r_on(db, "status")
        self.assertEqual(code, 0)
        self.assertIsNone(out["threads"][0]["project_title"])

    def test_list_keeps_an_old_waiting_thread_past_the_limit(self):
        db = Path(self.tmp) / "many.sqlite"
        con = sqlite3.connect(db)
        con.executescript(V2_THREADS_TABLE + V2_OTHER_TABLES)
        insert_v2_threads(con, [
            v2_thread(f"t{i:02}", f"2026-11-07T10:{59 - i:02}:00.000Z")
            for i in range(35)])
        insert_v2_request(con, "r1", "t34", "command", "pending")
        con.commit()
        con.close()
        code, out, _ = run_t3r_on(db, "status")
        self.assertEqual(code, 0)
        self.assertEqual(out["counts"]["visible_threads"], 35)
        self.assertEqual(out["counts"]["threads_waiting_approval"], 1)
        self.assertEqual([t["thread_id"] for t in out["threads"]],
                         [f"t{i:02}" for i in range(30)] + ["t34"])

    def test_status_reads_only_the_documented_tables(self):
        con, mode, _ = t3r.connect(str(self.db))
        read = set()

        def record(action, table, _column, _db, _trigger):
            if action == sqlite3.SQLITE_READ:
                read.add(table)
            return sqlite3.SQLITE_OK

        con.set_authorizer(record)
        t3r.cmd_status(con, str(self.db), mode)
        con.close()
        self.assertEqual(read, t3r.V2_STATUS_TABLES | {
            "projection_projects", "sqlite_master"})

    def test_output_has_no_secrets_or_message_bodies(self):
        self.assertNotIn("SENTINEL", self.stdout)


if __name__ == "__main__":
    unittest.main()
