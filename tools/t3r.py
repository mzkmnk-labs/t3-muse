#!/usr/bin/env python3
"""t3r: read-only queries against T3 Code's local state database.

Opens the database through SQLite's read-only URI mode and sets
PRAGMA query_only, so it cannot modify T3 Code's data. Prints one JSON
object to stdout.

Usage:
    python3 t3r.py schema    table and column names, no row data
    python3 t3r.py health    database presence, size and table count
    python3 t3r.py status    thread overview, see below

The database path defaults to ~/.t3/userdata/statev2.sqlite and can be
overridden with the T3M_DB environment variable.

status reads the orchestration_v2_projection_* tables. A database without
them falls back to the older projection_* tables, and the source field says
which answered ("orchestration_v2" or "legacy"). In v2 output:

- session_status is the thread's latest run status, or "idle" with no runs,
  the same values the T3 MCP thread list reports. Legacy output carries the
  old session status instead.
- pending_user_input_count counts pending user_input requests and
  pending_approval_count counts every other pending kind.
- Subagent threads are not counted in visible_threads or archived_threads and
  are listed only while they have a pending request. The waiting counts always
  include them, so an approval cannot go unseen.
- The list holds the 30 newest threads plus any older thread still waiting.
"""
import json
import os
import sqlite3
import sys
from urllib.request import pathname2url

DEFAULT_DB = os.path.expanduser("~/.t3/userdata/statev2.sqlite")

V2_THREADS = "orchestration_v2_projection_threads"
V2_RUNS = "orchestration_v2_projection_runs"
V2_REQUESTS = "orchestration_v2_projection_runtime_requests"
V2_MESSAGES = "orchestration_v2_projection_messages"
V2_TURN_ITEMS = "orchestration_v2_projection_turn_items"
V2_STATUS_TABLES = {V2_THREADS, V2_RUNS, V2_REQUESTS, V2_MESSAGES,
                    V2_TURN_ITEMS}
V2_QUESTION_KIND = "user_input"

MAX_LISTED_THREADS = 30
MAX_ERROR_CHARS = 300


def db_path() -> str:
    return os.environ.get("T3M_DB", DEFAULT_DB)


def _open(path: str, suffix: str) -> sqlite3.Connection:
    con = sqlite3.connect("file:" + pathname2url(path) + suffix,
                          uri=True, timeout=10)
    con.execute("PRAGMA query_only = ON")
    # sqlite3 connects lazily; force a real read so open errors surface here.
    con.execute("SELECT count(*) FROM sqlite_master").fetchone()
    return con


def connect(path: str):
    """Try each read strategy and collect the errors.

    Returns (connection, open_mode, errors). connection is None when every
    strategy failed. WAL databases without live -shm/-wal files can refuse
    a plain read-only open; immutable mode bypasses the WAL machinery but
    may lag the writer's newest uncheckpointed changes.
    """
    errors = []
    for mode, suffix in (("ro", "?mode=ro"),
                         ("ro+immutable", "?mode=ro&immutable=1")):
        try:
            return _open(path, suffix), mode, errors
        except sqlite3.Error as e:
            errors.append({"mode": mode, "error": str(e)})
    return None, None, errors


def header_info(path: str) -> dict:
    """OS-level open check plus the SQLite file header, for diagnostics."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError as e:
        return {"os_open_errno": e.errno, "os_open_error": e.strerror}
    with os.fdopen(fd, "rb") as f:
        head = f.read(100)
    if len(head) < 100 or head[:16] != b"SQLite format 3\x00":
        return {"os_open_errno": None, "magic_ok": False,
                "head_hex": head[:16].hex()}
    return {
        "os_open_errno": None,
        "magic_ok": True,
        "page_size": int.from_bytes(head[16:18], "big"),
        "file_format_write_version": head[18],
        "file_format_read_version": head[19],
        "database_size_pages": int.from_bytes(head[28:32], "big"),
    }


def table_names(con: sqlite3.Connection) -> list:
    rows = con.execute(
        "SELECT name FROM sqlite_master"
        " WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [r[0] for r in rows]


def cmd_schema(con: sqlite3.Connection, path: str, open_mode: str) -> dict:
    tables = []
    for name in table_names(con):
        quoted = '"' + name.replace('"', '""') + '"'
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({quoted})")]
        tables.append({"name": name, "columns": cols})
    return {"db": path, "open_mode": open_mode, "tables": tables}


def cmd_health(con: sqlite3.Connection, path: str, open_mode: str) -> dict:
    page_count = con.execute("PRAGMA page_count").fetchone()[0]
    page_size = con.execute("PRAGMA page_size").fetchone()[0]
    journal_mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    return {
        "db": path,
        "open_mode": open_mode,
        "exists": True,
        "size_bytes": os.path.getsize(path),
        "page_count": page_count,
        "page_size": page_size,
        "approx_size_bytes": page_count * page_size,
        "journal_mode": journal_mode,
        "table_count": len(table_names(con)),
    }


def cmd_status(con: sqlite3.Connection, path: str, open_mode: str) -> dict:
    tables = set(table_names(con))
    if V2_STATUS_TABLES <= tables:
        return _status_v2(con, path, open_mode, tables)
    if "projection_threads" not in tables:
        raise sqlite3.Error("table projection_threads not found")
    return _status_legacy(con, path, open_mode)


def _short_error(text):
    return (text or "")[:MAX_ERROR_CHARS] or None


def _json_field(column: str, field: str) -> str:
    """SQL for one payload field. A malformed payload reads as NULL."""
    return (f"CASE WHEN json_valid({column})"
            f" THEN json_extract({column}, '{field}') END")


def _status_v2(con: sqlite3.Connection, path: str, open_mode: str,
               tables: set) -> dict:
    # T3 Code keeps projects in the older table; v2 has none of its own.
    project_title = ("(SELECT p.title FROM projection_projects p"
                     " WHERE p.project_id = t.project_id)"
                     if "projection_projects" in tables else "NULL")
    # One statement, so the counts and the list share a single snapshot.
    rows = con.execute(f"""
        WITH pending AS (
            SELECT thread_id,
                   SUM(kind <> '{V2_QUESTION_KIND}') AS approvals,
                   SUM(kind = '{V2_QUESTION_KIND}') AS questions
            FROM {V2_REQUESTS}
            WHERE status = 'pending'
            GROUP BY thread_id
        )
        SELECT t.thread_id, t.title, {project_title},
               {_json_field('t.payload_json', '$.branch')},
               COALESCE(p.approvals, 0), COALESCE(p.questions, 0),
               t.updated_at,
               (SELECT MAX(m.created_at) FROM {V2_MESSAGES} m
                 WHERE m.thread_id = t.thread_id AND m.role = 'user'),
               COALESCE(r.status, 'idle'),
               COALESCE(r.provider, t.default_provider),
               (SELECT {_json_field('i.payload_json', '$.failure.message')}
                  FROM {V2_TURN_ITEMS} i
                 WHERE r.status = 'failed'
                   AND i.thread_id = t.thread_id AND i.run_id = r.run_id
                   AND i.type = 'error' AND i.status = 'failed'
                 ORDER BY i.updated_at DESC, i.ordinal DESC LIMIT 1),
               t.archived_at IS NOT NULL,
               {_json_field('t.payload_json',
                            '$.lineage.relationshipToParent')} = 'subagent'
        FROM {V2_THREADS} t
        LEFT JOIN pending p ON p.thread_id = t.thread_id
        LEFT JOIN {V2_RUNS} r ON r.run_id = (
            SELECT x.run_id FROM {V2_RUNS} x
             WHERE x.thread_id = t.thread_id
             ORDER BY x.ordinal DESC LIMIT 1)
        WHERE t.deleted_at IS NULL
        ORDER BY t.updated_at DESC, t.thread_id
    """)
    counts = {"visible_threads": 0, "archived_threads": 0,
              "threads_waiting_approval": 0, "threads_waiting_input": 0}
    threads = []
    for (thread_id, title, project, branch, approvals, questions, updated_at,
         latest_user_message_at, status, provider, last_error, archived,
         subagent) in rows:
        if archived:
            counts["archived_threads"] += not subagent
            continue
        counts["visible_threads"] += not subagent
        counts["threads_waiting_approval"] += approvals > 0
        counts["threads_waiting_input"] += questions > 0
        waiting = approvals > 0 or questions > 0
        if not waiting and (subagent or len(threads) >= MAX_LISTED_THREADS):
            continue
        threads.append({
            "thread_id": thread_id,
            "title": title,
            "project_title": project,
            "branch": branch,
            "pending_approval_count": approvals,
            "pending_user_input_count": questions,
            "updated_at": updated_at,
            "latest_user_message_at": latest_user_message_at,
            "session_status": status,
            "provider": provider,
            "last_error": _short_error(last_error),
        })
    return {
        "db": path,
        "open_mode": open_mode,
        "source": "orchestration_v2",
        "counts": counts,
        "threads": threads,
    }


def _status_legacy(con: sqlite3.Connection, path: str,
                   open_mode: str) -> dict:
    counts = con.execute(
        "SELECT"
        " SUM(CASE WHEN deleted_at IS NULL AND archived_at IS NULL"
        "          THEN 1 ELSE 0 END),"
        " SUM(CASE WHEN deleted_at IS NULL AND archived_at IS NOT NULL"
        "          THEN 1 ELSE 0 END),"
        " SUM(CASE WHEN deleted_at IS NULL AND archived_at IS NULL"
        "          AND pending_approval_count > 0 THEN 1 ELSE 0 END),"
        " SUM(CASE WHEN deleted_at IS NULL AND archived_at IS NULL"
        "          AND pending_user_input_count > 0 THEN 1 ELSE 0 END)"
        " FROM projection_threads"
    ).fetchone()
    rows = con.execute(
        "SELECT t.thread_id, t.title, p.title, t.branch,"
        " t.pending_approval_count, t.pending_user_input_count,"
        " t.updated_at, t.latest_user_message_at,"
        " s.status, s.provider_name, s.last_error"
        " FROM projection_threads t"
        " LEFT JOIN projection_projects p ON p.project_id = t.project_id"
        " LEFT JOIN projection_thread_sessions s"
        "   ON s.thread_id = t.thread_id"
        " WHERE t.deleted_at IS NULL AND t.archived_at IS NULL"
        f" ORDER BY t.updated_at DESC LIMIT {MAX_LISTED_THREADS}"
    )
    threads = []
    for r in rows:
        threads.append({
            "thread_id": r[0],
            "title": r[1],
            "project_title": r[2],
            "branch": r[3],
            "pending_approval_count": r[4],
            "pending_user_input_count": r[5],
            "updated_at": r[6],
            "latest_user_message_at": r[7],
            "session_status": r[8],
            "provider": r[9],
            "last_error": _short_error(r[10]),
        })
    return {
        "db": path,
        "open_mode": open_mode,
        "source": "legacy",
        "counts": {
            "visible_threads": counts[0] or 0,
            "archived_threads": counts[1] or 0,
            "threads_waiting_approval": counts[2] or 0,
            "threads_waiting_input": counts[3] or 0,
        },
        "threads": threads,
    }


def main(argv: list) -> int:
    commands = {"schema": cmd_schema, "health": cmd_health,
                "status": cmd_status}
    if len(argv) != 2 or argv[1] not in commands:
        print(json.dumps(
            {"error": "usage: t3r.py schema|health|status"}))
        return 2
    path = db_path()
    if not os.path.isfile(path):
        print(json.dumps({"error": f"database not found: {path}"}))
        return 3
    con, open_mode, errors = connect(path)
    if con is None:
        print(json.dumps({
            "error": "cannot open database",
            "attempts": errors,
            "header": header_info(path),
        }))
        return 4
    try:
        result = commands[argv[1]](con, path, open_mode)
    except sqlite3.Error as e:
        print(json.dumps({"error": f"sqlite error: {e}"}))
        return 4
    finally:
        con.close()
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
