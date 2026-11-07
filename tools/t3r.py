#!/usr/bin/env python3
"""t3r: read-only queries against T3 Code's local state database.

Opens the database through SQLite's read-only URI mode and sets
PRAGMA query_only, so it cannot modify T3 Code's data. Prints one JSON
object to stdout.

Usage:
    python3 t3r.py schema    table and column names, no row data
    python3 t3r.py health    database presence, size and table count

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


def connect(path: str) -> sqlite3.Connection:
    uri = "file:" + pathname2url(path) + "?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=10)
    con.execute("PRAGMA query_only = ON")
    return con


def table_names(con: sqlite3.Connection) -> list:
    rows = con.execute(
        "SELECT name FROM sqlite_master"
        " WHERE type = 'table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    )
    return [r[0] for r in rows]


def cmd_schema(con: sqlite3.Connection, path: str) -> dict:
    tables = []
    for name in table_names(con):
        quoted = '"' + name.replace('"', '""') + '"'
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({quoted})")]
        tables.append({"name": name, "columns": cols})
    return {"db": path, "tables": tables}


def cmd_health(con: sqlite3.Connection, path: str) -> dict:
    page_count = con.execute("PRAGMA page_count").fetchone()[0]
    page_size = con.execute("PRAGMA page_size").fetchone()[0]
    journal_mode = con.execute("PRAGMA journal_mode").fetchone()[0]
    return {
        "db": path,
        "exists": True,
        "size_bytes": os.path.getsize(path),
        "page_count": page_count,
        "page_size": page_size,
        "approx_size_bytes": page_count * page_size,
        "journal_mode": journal_mode,
        "table_count": len(table_names(con)),
    }


def main(argv: list) -> int:
    if len(argv) != 2 or argv[1] not in ("schema", "health"):
        print(json.dumps({"error": "usage: t3r.py schema|health"}))
        return 2
    path = db_path()
    if not os.path.isfile(path):
        print(json.dumps({"error": f"database not found: {path}"}))
        return 3
    try:
        con = connect(path)
        try:
            result = (cmd_schema if argv[1] == "schema" else cmd_health)(con, path)
        finally:
            con.close()
    except sqlite3.Error as e:
        print(json.dumps({"error": f"sqlite error: {e}"}))
        return 4
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
