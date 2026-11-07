#!/usr/bin/env python3
"""t3r: read-only queries against T3 Code's local state database.

Opens the database through SQLite's read-only URI mode and sets
PRAGMA query_only, so it cannot modify T3 Code's data. Prints one JSON
object to stdout.

Usage:
    python3 t3r.py schema    table and column names, no row data
    python3 t3r.py health    database presence, size and table count
    python3 t3r.py status    thread overview from the projection tables

The database path defaults to ~/.t3/userdata/statev2.sqlite and can be
overridden with the T3M_DB environment variable.
"""
import json
import os
import sqlite3
import sys
from urllib.request import pathname2url

DEFAULT_DB = os.path.expanduser("~/.t3/userdata/statev2.sqlite")


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
    if "projection_threads" not in table_names(con):
        raise sqlite3.Error("table projection_threads not found")
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
        " ORDER BY t.updated_at DESC LIMIT 30"
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
            "last_error": (r[10] or "")[:300] or None,
        })
    return {
        "db": path,
        "open_mode": open_mode,
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
