#!/usr/bin/env python3
"""Unit tests for t3a.py against a stub t3 CLI and a stub WebSocket server."""
import base64
import hashlib
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

T3A = Path(__file__).with_name("t3a.py")
TOKEN = "T3A-SENTINEL-TOKEN.abc_123-XYZ"
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# Prints the token where the tool must never relay it, and records its argv.
STUB_CLI = f"""#!/usr/bin/env python3
import json, os, sys
with open(os.environ["STUB_RECORD"], "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
sys.stderr.write("stderr chatter {TOKEN}\\n")
mode = os.environ.get("STUB_MODE", "ok")
if mode == "fail":
    print("{TOKEN}")
    raise SystemExit(7)
if mode != "empty":
    print("{TOKEN}")
"""

APPROVAL_ID = "runtime-request:provider:codex:provider-turn:provider-turn%3Aa:r_1"
SECOND_APPROVAL_ID = "runtime-request:provider:codex:provider-turn:provider-turn%3Aa:r_2"
RESOLVED_ID = "runtime-request:provider:codex:provider-turn:provider-turn%3Aa:r_0"
TOOL_CALL_ID = "runtime-request:provider:codex:provider-turn:provider-turn%3Aa:r_3"
QUESTION_ID = "runtime-request:provider:claudeAgent:provider-turn:b:q_1"
STRICT_ID = "runtime-request:provider:claudeAgent:provider-turn:b:q_2"
MULTI_ID = "runtime-request:provider:claudeAgent:provider-turn:c:q_1"
MULTISELECT_ID = "runtime-request:provider:claudeAgent:provider-turn:c:q_2"
DEAD_ID = "runtime-request:provider:opencode:provider-turn:d:r_1"
BLIND_ID = "runtime-request:provider:opencode:provider-turn:d:r_2"
LIMITED_ID = "runtime-request:provider:opencode:provider-turn:d:r_3"
WARNED_ID = "runtime-request:provider:opencode:provider-turn:d:r_4"


def rq(request_id, kind, status="pending", capability=None):
    return {"id": request_id, "kind": kind, "status": status,
            "createdAt": "2026-11-07T00:00:00.000Z",
            "responseCapability": capability or {
                "type": "live", "providerSessionId": "s"}}


def shell(thread_id, title, pending=None):
    return {"id": thread_id, "title": title, "pendingRuntimeRequest": pending}


def approval_item(request_id, prompt, **extra):
    return dict({"type": "approval_request", "requestId": request_id,
                 "requestKind": "command", "prompt": prompt, "title": None},
                **extra)


def question_item(request_id, *questions):
    return {"type": "user_input_request", "requestId": request_id,
            "questions": list(questions)}


def question(question_id, text, *options, **extra):
    return dict({"id": question_id, "header": "H", "question": text,
                 "options": list(options)}, **extra)


SUMMARY = {"id": "x", "kind": "command", "createdAt": "2026-11-07T00:00:00Z"}
# The padded idle thread pushes the snapshot past the 16-bit frame length.
THREADS = [
    shell("thread-idle", "idle " + "x" * 70000),
    shell("thread-a", "Approvals", SUMMARY),
    shell("thread-b", "Questions", SUMMARY),
    shell("thread-c", "Hard questions", SUMMARY),
    shell("thread-d", "Odd requests", SUMMARY),
]
PROJECTIONS = {
    "thread-a": {
        "runtimeRequests": [
            rq(RESOLVED_ID, "command", status="resolved"),
            rq(APPROVAL_ID, "command"),
            rq(SECOND_APPROVAL_ID, "file-change"),
            rq(TOOL_CALL_ID, "dynamic_tool_call"),
        ],
        "turnItems": [
            {"type": "command_execution", "input": "ls"},
            approval_item(APPROVAL_ID, "rm -rf build", reason="clean up"),
            approval_item(SECOND_APPROVAL_ID, "edit a.py"),
        ],
    },
    "thread-b": {
        "runtimeRequests": [rq(QUESTION_ID, "user_input"),
                            rq(STRICT_ID, "user_input")],
        "turnItems": [
            question_item(QUESTION_ID, question(
                "which", "Which database?",
                {"label": "Postgres", "description": "relational",
                 "value": "pg"},
                {"label": "SQLite", "description": "embedded"})),
            question_item(STRICT_ID, question(
                "strict", "Pick one",
                {"label": "A", "description": "first"},
                allowCustomAnswer=False)),
        ],
    },
    "thread-c": {
        "runtimeRequests": [rq(MULTI_ID, "user_input"),
                            rq(MULTISELECT_ID, "user_input")],
        "turnItems": [
            question_item(MULTI_ID,
                          question("q1", "First?"), question("q2", "Second?")),
            question_item(MULTISELECT_ID, question(
                "multi", "Which?",
                {"label": "One", "description": "1"}, multiSelect=True)),
        ],
    },
    "thread-d": {
        "runtimeRequests": [
            rq(DEAD_ID, "command", capability={
                "type": "not_resumable", "reason": "provider restarted"}),
            rq(BLIND_ID, "permission"),
            rq(LIMITED_ID, "mcp-elicitation"),
            rq(WARNED_ID, "permission"),
        ],
        "turnItems": [
            approval_item(DEAD_ID, "old command"),
            approval_item(LIMITED_ID, "use the tool", options=[
                {"decision": "decline", "label": "No"},
                {"decision": "acceptForSession", "label": "Yes"}]),
            approval_item(WARNED_ID, "full access", options=[
                {"decision": "decline", "label": "No"},
                {"decision": "accept", "label": "Yes", "warning": "careful"}]),
        ],
    },
}


class StubT3Server:
    """Just enough of T3's /ws endpoint, on stdlib sockets."""

    def __init__(self, status=101, dispatch_exit=None, close_after_upgrade=False):
        self.status = status
        self.dispatch_exit = dispatch_exit or {
            "_tag": "Success", "value": {"sequence": 42}}
        self.close_after_upgrade = close_after_upgrade
        self.handshakes = []
        self.requests = []
        self.pongs = []
        self.interrupted = []
        self.unmasked_frames = 0
        self.sock = socket.socket()
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        threading.Thread(target=self._accept, daemon=True).start()

    def stop(self):
        self.sock.close()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,),
                             daemon=True).start()

    def _serve(self, conn):
        try:
            self._handle(conn)
        except (OSError, EOFError):
            pass
        finally:
            conn.close()

    def _handle(self, conn):
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = conn.recv(4096)
            if not chunk:
                return
            data += chunk
        head = data.split(b"\r\n\r\n", 1)[0].decode()
        self.handshakes.append(head)
        if self.status != 101:
            conn.sendall(f"HTTP/1.1 {self.status} Nope\r\n"
                         "Content-Length: 0\r\n\r\n".encode())
            return
        key = next(line.split(":", 1)[1].strip()
                   for line in head.split("\r\n")
                   if line.lower().startswith("sec-websocket-key"))
        accept = base64.b64encode(hashlib.sha1(
            (key + WS_GUID).encode()).digest()).decode()
        conn.sendall(("HTTP/1.1 101 Switching Protocols\r\n"
                      "Upgrade: websocket\r\nConnection: Upgrade\r\n"
                      f"Sec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        if self.close_after_upgrade:
            return
        self._rpc_loop(conn)

    @staticmethod
    def _frame(opcode, payload, fin=True):
        size = len(payload)
        head = bytes([(0x80 if fin else 0) | opcode])
        if size < 126:
            head += bytes([size])
        elif size < 65536:
            head += bytes([126]) + size.to_bytes(2, "big")
        else:
            head += bytes([127]) + size.to_bytes(8, "big")
        return head + payload

    def _send_json(self, conn, message):
        conn.sendall(self._frame(0x1, json.dumps(message).encode()))

    def _read(self, conn, count):
        data = b""
        while len(data) < count:
            chunk = conn.recv(count - len(data))
            if not chunk:
                raise EOFError
            data += chunk
        return data

    def _rpc_loop(self, conn):
        while True:
            byte0, byte1 = self._read(conn, 2)
            size = byte1 & 0x7F
            if size == 126:
                size = int.from_bytes(self._read(conn, 2), "big")
            elif size == 127:
                size = int.from_bytes(self._read(conn, 8), "big")
            if byte1 & 0x80:
                mask = self._read(conn, 4)
                body = self._read(conn, size)
                payload = bytes(b ^ mask[i & 3] for i, b in enumerate(body))
            else:
                self.unmasked_frames += 1
                payload = self._read(conn, size)
            opcode = byte0 & 0x0F
            if opcode == 0xA:
                self.pongs.append(payload)
            elif opcode == 0x8:
                return
            else:
                self._dispatch(conn, json.loads(payload))

    def _dispatch(self, conn, message):
        if message["_tag"] == "Interrupt":
            self.interrupted.append(message["requestId"])
            return
        if message["_tag"] != "Request":
            return
        self.requests.append(message)
        request_id, tag = message["id"], message["tag"]
        if tag == "orchestration.subscribeShell":
            # A ping and a fragmented message, as a real server may send.
            conn.sendall(self._frame(0x9, b"ka"))
            body = json.dumps({
                "_tag": "Chunk", "requestId": request_id,
                "values": [{"kind": "snapshot", "snapshot": {
                    "threads": THREADS}}]}).encode()
            conn.sendall(self._frame(0x1, body[:1000], fin=False))
            conn.sendall(self._frame(0x0, body[1000:]))
        elif tag == "orchestration.getThreadProjection":
            # A late item of the shell stream must not be taken for a reply.
            self._send_json(conn, {"_tag": "Chunk", "requestId": "1",
                                   "values": [{"kind": "synchronized"}]})
            self._send_json(conn, {
                "_tag": "Exit", "requestId": request_id,
                "exit": {"_tag": "Success",
                         "value": PROJECTIONS[message["payload"]["threadId"]]}})
        elif tag == "orchestration.dispatchCommand":
            self._send_json(conn, {"_tag": "Exit", "requestId": request_id,
                                   "exit": self.dispatch_exit})


class T3aCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="t3a-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.tools = self.tmp / "tools"
        self.tools.mkdir()
        shutil.copy(T3A, self.tools / "t3a.py")
        self.token_file = self.tools / ".t3a-token"
        self.t3_home = self.tmp / "t3-home"
        (self.t3_home / "userdata").mkdir(parents=True)
        self.record = self.tmp / "cli-calls"
        self.cli = self.tmp / "stub-t3"
        self.cli.write_text(STUB_CLI)
        self.cli.chmod(self.cli.stat().st_mode | stat.S_IEXEC)

    def run_t3a(self, *args, **env):
        full_env = dict(os.environ)
        full_env.update(T3M_T3CLI=str(self.cli),
                        T3M_T3_HOME=str(self.t3_home),
                        STUB_RECORD=str(self.record))
        full_env.update(env)
        proc = subprocess.run(
            [sys.executable, str(self.tools / "t3a.py"), *args],
            env=full_env, capture_output=True, text=True, timeout=60)
        self.assertNotIn(TOKEN, proc.stdout)
        self.assertNotIn(TOKEN, proc.stderr)
        return proc

    def assert_error(self, proc, code, exit_code):
        self.assertEqual(proc.stdout, "")
        self.assertEqual(json.loads(proc.stderr)["error"], code, proc.stderr)
        self.assertEqual(proc.returncode, exit_code)

    def write_token(self, mode=0o600):
        self.token_file.write_text(TOKEN + "\n")
        self.token_file.chmod(mode)

    def write_runtime(self, port, host="0.0.0.0"):
        (self.t3_home / "userdata" / "server-runtime.json").write_text(
            json.dumps({"host": host, "port": port}))

    def start_server(self, **kwargs):
        server = StubT3Server(**kwargs)
        self.addCleanup(server.stop)
        self.write_token()
        self.write_runtime(server.port)
        return server


class IssueTokenTest(T3aCase):
    def test_writes_token_only_to_a_0600_file(self):
        proc = self.run_t3a("issue-token")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(self.token_file.read_text().strip(), TOKEN)
        self.assertEqual(stat.S_IMODE(self.token_file.stat().st_mode), 0o600)
        out = json.loads(proc.stdout)
        self.assertIs(out["issued"], True)
        self.assertEqual(out["token_path"], str(self.token_file))
        self.assertIn("expires_at", out)
        self.assertIn("revoke", out["note"])
        self.assertEqual(proc.stderr, "")

    def test_passes_the_agreed_cli_arguments(self):
        self.run_t3a("issue-token")
        self.assertEqual(json.loads(self.record.read_text()), [
            "auth", "session", "issue",
            "--scope", "orchestration:read",
            "--scope", "orchestration:operate",
            "--ttl", "90d", "--label", "t3-muse", "--subject", "t3-muse",
            "--token-only"])

    def test_mode_is_0600_even_with_a_permissive_umask(self):
        script = (f"import os, runpy, sys; os.umask(0); "
                  f"sys.argv = ['t3a.py', 'issue-token']; "
                  f"runpy.run_path({str(self.tools / 't3a.py')!r}, "
                  f"run_name='__main__')")
        env = dict(os.environ, T3M_T3CLI=str(self.cli),
                   STUB_RECORD=str(self.record))
        subprocess.run([sys.executable, "-c", script], env=env,
                       capture_output=True, text=True, timeout=60)
        self.assertEqual(stat.S_IMODE(self.token_file.stat().st_mode), 0o600)

    def test_refuses_to_overwrite(self):
        self.token_file.write_text("existing\n")
        proc = self.run_t3a("issue-token")
        self.assert_error(proc, "TOKEN_EXISTS", 3)
        self.assertEqual(self.token_file.read_text(), "existing\n")
        self.assertFalse(self.record.exists(), "the CLI must not have run")

    def test_refuses_a_symlink_in_the_way(self):
        target = self.tmp / "elsewhere"
        self.token_file.symlink_to(target)
        proc = self.run_t3a("issue-token")
        self.assert_error(proc, "TOKEN_EXISTS", 3)
        self.assertFalse(target.exists())

    def test_cli_failure_leaves_no_file_and_no_stderr_relay(self):
        proc = self.run_t3a("issue-token", STUB_MODE="fail")
        self.assert_error(proc, "ISSUE_FAILED", 5)
        self.assertFalse(self.token_file.exists())

    def test_empty_output_leaves_no_file(self):
        proc = self.run_t3a("issue-token", STUB_MODE="empty")
        self.assert_error(proc, "ISSUE_FAILED", 5)
        self.assertFalse(self.token_file.exists())

    def test_missing_cli(self):
        proc = self.run_t3a("issue-token", T3M_T3CLI=str(self.tmp / "nope"))
        self.assert_error(proc, "T3CLI_NOT_FOUND", 5)
        self.assertFalse(self.token_file.exists())


class AnswerArgumentsTest(T3aCase):
    """Refused before any file or socket is touched: no token, no server."""

    def test_approve_without_flag(self):
        self.assert_error(self.run_t3a("answer", APPROVAL_ID, "approve"),
                          "USER_APPROVAL_REQUIRED", 2)

    def test_deny_without_flag(self):
        self.assert_error(self.run_t3a("answer", APPROVAL_ID, "deny"),
                          "USER_APPROVAL_REQUIRED", 2)

    def test_flag_with_free_text(self):
        self.assert_error(
            self.run_t3a("answer", APPROVAL_ID, "yes", "--user-approved"),
            "USAGE", 2)

    def test_flag_only_counts_in_last_position(self):
        self.assert_error(
            self.run_t3a("answer", APPROVAL_ID, "--user-approved", "approve"),
            "USAGE", 2)
        self.assert_error(
            self.run_t3a("answer", "--user-approved", APPROVAL_ID, "approve"),
            "USAGE", 2)

    def test_empty_and_blank_text(self):
        for text in ("", "   ", "\n"):
            self.assert_error(self.run_t3a("answer", QUESTION_ID, text),
                              "INVALID_ANSWER", 2)

    def test_text_too_long(self):
        self.assert_error(self.run_t3a("answer", QUESTION_ID, "x" * 4001),
                          "INVALID_ANSWER", 2)

    def test_text_starting_with_a_dash(self):
        for text in ("-rf", "--user-approved-ish", "  -x"):
            self.assert_error(self.run_t3a("answer", QUESTION_ID, text),
                              "INVALID_ANSWER", 2)

    def test_bad_request_id(self):
        for bad in ("-x", "", "a b", "a\n", "a/b", "x" * 801):
            self.assert_error(self.run_t3a("answer", bad, "hello"),
                              "INVALID_REQUEST_ID", 2)

    def test_argument_counts(self):
        self.assert_error(self.run_t3a("answer"), "USAGE", 2)
        self.assert_error(self.run_t3a("answer", QUESTION_ID), "USAGE", 2)
        self.assert_error(self.run_t3a("answer", QUESTION_ID, "a", "b"),
                          "USAGE", 2)

    def test_top_level_usage(self):
        self.assert_error(self.run_t3a(), "USAGE", 2)
        self.assert_error(self.run_t3a("bogus"), "USAGE", 2)
        self.assert_error(self.run_t3a("pending", "extra"), "USAGE", 2)
        self.assert_error(self.run_t3a("issue-token", "--ttl", "1d"), "USAGE", 2)
        self.assertFalse(self.token_file.exists())


class ConnectionTest(T3aCase):
    def test_no_token(self):
        self.assert_error(self.run_t3a("pending"), "NO_TOKEN", 3)

    def test_token_readable_by_others(self):
        self.write_token(mode=0o644)
        self.assert_error(self.run_t3a("pending"), "TOKEN_INSECURE", 3)

    def test_token_behind_a_symlink(self):
        real = self.tmp / "real-token"
        real.write_text(TOKEN)
        real.chmod(0o600)
        self.token_file.symlink_to(real)
        self.assert_error(self.run_t3a("pending"), "TOKEN_INSECURE", 3)

    def test_no_runtime_file(self):
        self.write_token()
        self.assert_error(self.run_t3a("pending"), "T3_NOT_RUNNING", 4)

    def test_server_down(self):
        self.write_token()
        probe = socket.socket()
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
        probe.close()
        self.write_runtime(port)
        self.assert_error(self.run_t3a("pending"), "T3_NOT_RUNNING", 4)

    def test_token_is_never_sent_off_the_mac(self):
        self.write_token()
        for host in ("93.184.216.34", "100.64.0.1", "example.com", "", None):
            self.write_runtime(3773, host=host)
            self.assert_error(self.run_t3a("pending"), "T3_NOT_LOCAL", 4)

    def test_rejected_token(self):
        for status in (401, 403):
            self.start_server(status=status)
            self.assert_error(self.run_t3a("pending"), "TOKEN_REJECTED", 3)

    def test_incompatible_server(self):
        self.start_server(status=426)
        self.assert_error(self.run_t3a("pending"), "T3_PROTOCOL", 4)

    def test_connection_dropped(self):
        self.start_server(close_after_upgrade=True)
        self.assert_error(self.run_t3a("pending"), "T3_PROTOCOL", 4)


class PendingTest(T3aCase):
    def listed(self):
        proc = self.run_t3a("pending")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return {entry["request_id"]: entry for entry in json.loads(proc.stdout)}

    def test_lists_every_answerable_kind(self):
        self.start_server()
        listed = self.listed()
        self.assertEqual(set(listed), {
            APPROVAL_ID, SECOND_APPROVAL_ID, QUESTION_ID, STRICT_ID, MULTI_ID,
            MULTISELECT_ID, DEAD_ID, BLIND_ID, LIMITED_ID, WARNED_ID})

    def test_approval_entry(self):
        self.start_server()
        entry = self.listed()[APPROVAL_ID]
        self.assertEqual(entry["kind"], "approval")
        self.assertEqual(entry["provider_kind"], "command")
        self.assertEqual(entry["thread_id"], "thread-a")
        self.assertEqual(entry["thread_title"], "Approvals")
        self.assertEqual(entry["summary"], "rm -rf build")
        self.assertEqual(entry["reason"], "clean up")
        self.assertEqual(entry["choices"],
                         [{"answer": "approve"}, {"answer": "deny"}])
        self.assertIs(entry["answerable"], True)

    def test_question_entry_with_choices(self):
        self.start_server()
        entry = self.listed()[QUESTION_ID]
        self.assertEqual(entry["kind"], "user-input")
        self.assertEqual(entry["summary"], "Which database?")
        self.assertEqual(entry["choices"], [
            {"answer": "pg", "label": "Postgres", "description": "relational"},
            {"answer": "SQLite", "label": "SQLite", "description": "embedded"},
        ])

    def test_multi_question_entry_is_flagged(self):
        self.start_server()
        entry = self.listed()[MULTI_ID]
        self.assertIs(entry["answerable"], False)
        self.assertEqual([q["id"] for q in entry["questions"]], ["q1", "q2"])

    def test_unanswerable_entries_say_why(self):
        self.start_server()
        listed = self.listed()
        self.assertEqual(listed[DEAD_ID]["unanswerable_reason"],
                         "provider restarted")
        self.assertIsNone(listed[BLIND_ID]["summary"])
        self.assertIs(listed[BLIND_ID]["answerable"], False)

    def test_offered_options_limit_and_warn(self):
        self.start_server()
        listed = self.listed()
        self.assertEqual(listed[LIMITED_ID]["choices"], [{"answer": "deny"}])
        self.assertEqual(listed[WARNED_ID]["choices"],
                         [{"answer": "approve", "warning": "careful"},
                          {"answer": "deny"}])

    def test_wire_format(self):
        server = self.start_server()
        self.listed()
        handshake = server.handshakes[0]
        request_line = handshake.split("\r\n")[0]
        self.assertEqual(request_line,
                         "GET /ws?orchestrationProtocol=2 HTTP/1.1")
        self.assertIn(f"Authorization: Bearer {TOKEN}", handshake)
        self.assertEqual(server.unmasked_frames, 0)
        self.assertEqual(server.pongs, [b"ka"], "pings must be answered")
        self.assertEqual(server.interrupted, ["1"], "the stream must be stopped")
        first = server.requests[0]
        self.assertEqual(first, {"_tag": "Request", "id": "1",
                                 "tag": "orchestration.subscribeShell",
                                 "payload": {}, "headers": []})
        fetched = [r["payload"]["threadId"] for r in server.requests[1:]]
        self.assertEqual(fetched, ["thread-a", "thread-b", "thread-c", "thread-d"])

    def test_pending_never_dispatches(self):
        server = self.start_server()
        self.listed()
        self.assertFalse([r for r in server.requests
                          if r["tag"] == "orchestration.dispatchCommand"])


class AnswerTest(T3aCase):
    def dispatched(self, server):
        return [r["payload"] for r in server.requests
                if r["tag"] == "orchestration.dispatchCommand"]

    def answered(self, *args, **server_kwargs):
        server = self.start_server(**server_kwargs)
        proc = self.run_t3a("answer", *args)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        commands = self.dispatched(server)
        self.assertEqual(len(commands), 1, "exactly one dispatch")
        self.assertTrue(commands[0].pop("commandId").startswith("t3a:"))
        return json.loads(proc.stdout), commands[0]

    def refused(self, code, exit_code, *args):
        server = self.start_server()
        self.assert_error(self.run_t3a("answer", *args), code, exit_code)
        self.assertEqual(self.dispatched(server), [], "nothing may be sent")

    def test_approve_is_single_use_accept(self):
        out, command = self.answered(APPROVAL_ID, "approve", "--user-approved")
        self.assertEqual(command, {
            "type": "runtime-request.respond", "threadId": "thread-a",
            "requestId": APPROVAL_ID, "decision": "accept"})
        self.assertIs(out["answered"], True)
        self.assertIs(out["user_approved"], True)
        self.assertEqual(out["decision"], "accept")
        self.assertEqual(out["ack"], {"sequence": 42})

    def test_deny_is_decline(self):
        _, command = self.answered(SECOND_APPROVAL_ID, "deny", "--user-approved")
        self.assertEqual(command["decision"], "decline")
        self.assertEqual(command["requestId"], SECOND_APPROVAL_ID)

    def test_free_text_on_an_approval_is_refused(self):
        self.refused("INVALID_APPROVAL_RESPONSE", 2, APPROVAL_ID, "yes")
        self.refused("INVALID_APPROVAL_RESPONSE", 2, APPROVAL_ID, "Approve")
        self.refused("INVALID_APPROVAL_RESPONSE", 2, APPROVAL_ID, "approve ")

    def test_question_custom_text(self):
        out, command = self.answered(QUESTION_ID, "  MySQL please  ")
        self.assertEqual(command["answers"], {"which": "MySQL please"})
        self.assertNotIn("decision", command)
        self.assertIs(out["user_approved"], False)

    def test_question_option_sends_its_value(self):
        _, by_value = self.answered(QUESTION_ID, "pg")
        self.assertEqual(by_value["answers"], {"which": "pg"})
        _, by_label = self.answered(QUESTION_ID, "Postgres")
        self.assertEqual(by_label["answers"], {"which": "pg"})
        _, labelled = self.answered(QUESTION_ID, "SQLite")
        self.assertEqual(labelled["answers"], {"which": "SQLite"})

    def test_question_answered_with_a_literal_approve(self):
        _, command = self.answered(QUESTION_ID, "approve", "--user-approved")
        self.assertEqual(command["answers"], {"which": "approve"})
        self.assertNotIn("decision", command)

    def test_multi_select_sends_a_list(self):
        _, command = self.answered(MULTISELECT_ID, "One")
        self.assertEqual(command["answers"], {"multi": ["One"]})

    def test_custom_text_refused_where_not_allowed(self):
        self.refused("ANSWER_NOT_A_CHOICE", 6, STRICT_ID, "Something else")

    def test_multi_question_is_refused(self):
        self.refused("MULTI_QUESTION", 6, MULTI_ID, "one text for both")

    def test_unknown_or_resolved_request(self):
        self.refused("REQUEST_NOT_FOUND", 6, "runtime-request:nope", "hello")
        self.refused("REQUEST_NOT_FOUND", 6, RESOLVED_ID, "approve",
                     "--user-approved")
        self.refused("REQUEST_NOT_FOUND", 6, TOOL_CALL_ID, "approve",
                     "--user-approved")

    def test_not_resumable_is_refused(self):
        self.refused("NOT_ANSWERABLE", 6, DEAD_ID, "approve", "--user-approved")

    def test_request_without_details_is_refused(self):
        self.refused("DETAILS_UNAVAILABLE", 6, BLIND_ID, "approve",
                     "--user-approved")

    def test_decision_the_request_does_not_offer(self):
        self.refused("DECISION_NOT_OFFERED", 6, LIMITED_ID, "approve",
                     "--user-approved")
        _, command = self.answered(LIMITED_ID, "deny", "--user-approved")
        self.assertEqual(command["decision"], "decline")

    def test_server_rejection_is_reported(self):
        server = self.start_server(dispatch_exit={
            "_tag": "Failure", "cause": [{"_tag": "Fail", "error": {
                "_tag": "OrchestrationV2DispatchCommandError",
                "message": "Failed", "detail": "already answered"}}]})
        proc = self.run_t3a("answer", APPROVAL_ID, "approve", "--user-approved")
        self.assert_error(proc, "SERVER_REJECTED", 7)
        self.assertIn("already answered", proc.stderr)
        self.assertEqual(len(self.dispatched(server)), 1)

    def test_missing_scope_is_reported(self):
        self.start_server(dispatch_exit={
            "_tag": "Failure", "cause": [{"_tag": "Fail", "error": {
                "_tag": "EnvironmentAuthorizationError",
                "message": "missing required scope",
                "requiredScope": "orchestration:operate"}}]})
        proc = self.run_t3a("answer", APPROVAL_ID, "deny", "--user-approved")
        self.assert_error(proc, "TOKEN_SCOPE", 3)

    def test_token_comes_from_the_file_not_the_arguments(self):
        server = self.start_server()
        self.run_t3a("answer", APPROVAL_ID, "deny", "--user-approved")
        for request in server.requests:
            self.assertNotIn(TOKEN, json.dumps(request))


if __name__ == "__main__":
    unittest.main()
