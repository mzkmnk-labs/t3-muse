#!/usr/bin/env python3
"""t3a: relay the user's answers to T3 Code's pending runtime requests.

Subcommands (a JSON result on stdout; errors are one JSON line on stderr):
    python3 t3a.py issue-token
    python3 t3a.py renew
    python3 t3a.py pending
    python3 t3a.py answer <request-id> <response> [--user-approved]

issue-token runs the bundled t3 CLI with its stdout redirected straight
into a 0600 token file next to this script, so the token never passes
through this process, and refuses to overwrite an existing file. The
session lasts 90 days. pending lists every request waiting on a human.
answer resolves exactly one of them.

renew replaces an existing token. It issues the same session into a 0600
temp file beside the token file, runs pending with the new token, renames
the temp file over the token file, and only then revokes the old session.
A failure before the rename leaves the token file as it was. Once the
rename is done it exits 0 even if the old session could not be revoked:
old_session in the result says revoked, none_active or left_active, and
left_active comes with a note on revoking by hand. It revokes only what
the session lists prove is the old session, so nothing is revoked when
they do not.

For approvals <response> is exactly `approve` or `deny`, and
--user-approved records that the human decided; approve maps to T3's
single-use `accept`, never to a session-wide or permanent grant. Since
the flag is only required for those two words, a question whose answer
really is "approve" needs it as well. For questions <response> is the
answer text. A request with several questions cannot be answered with
one text and is refused (MULTI_QUESTION); use the T3 Code app.

Protocol (T3 Code's orchestration protocol 2, as used by its own app):
one WebSocket to GET /ws?orchestrationProtocol=2 with a bearer token in
the Authorization header, one JSON RPC message per text frame:
    -> {"_tag": "Request", "id", "tag", "payload", "headers"}
    <- {"_tag": "Chunk", "requestId", "values"}   (stream items)
    <- {"_tag": "Exit", "requestId", "exit"}      (Success or Failure)
No single call returns pending requests with their text, and the
respond command needs the thread and question ids, so one connection
makes several calls: subscribeShell for the threads that have a pending
request, getThreadProjection for each of them, then at most one
dispatchCommand. Requests the app does not put to the user
(dynamic_tool_call, auth_refresh) are not listed.

The token is only ever sent to the loopback address: a wildcard bind
address in server-runtime.json means loopback, anything else is refused
(T3_NOT_LOCAL). Paths default to ~/.t3/bin/t3 and ~/.t3, overridable
with T3M_T3CLI and T3M_T3_HOME.

Error codes and exit status:
    2  USAGE INVALID_REQUEST_ID INVALID_ANSWER USER_APPROVAL_REQUIRED
       INVALID_APPROVAL_RESPONSE
    3  NO_TOKEN TOKEN_EXISTS TOKEN_INSECURE TOKEN_REJECTED TOKEN_SCOPE
    4  T3_NOT_RUNNING T3_NOT_LOCAL T3_PROTOCOL
    5  T3CLI_NOT_FOUND ISSUE_FAILED RENEW_FAILED
    6  REQUEST_NOT_FOUND NOT_ANSWERABLE DETAILS_UNAVAILABLE MULTI_QUESTION
       ANSWER_NOT_A_CHOICE DECISION_NOT_OFFERED
    7  SERVER_REJECTED
"""
import base64
import datetime
import hashlib
import itertools
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import uuid

DEFAULT_CLI = os.path.expanduser("~/.t3/bin/t3")
DEFAULT_T3_HOME = os.path.expanduser("~/.t3")
TOKEN_FILE = ".t3a-token"
RENEW_TEMP_PREFIX = TOKEN_FILE + ".renew-"
TOKEN_TTL_DAYS = 90
SESSION_NAME = "t3-muse"
ISSUE_ARGS = [
    "auth", "session", "issue",
    "--scope", "orchestration:read",
    "--scope", "orchestration:operate",
    "--ttl", f"{TOKEN_TTL_DAYS}d",
    "--label", SESSION_NAME,
    "--subject", SESSION_NAME,
    "--token-only",
]
LIST_ARGS = ["auth", "session", "list", "--json"]
REVOKE_ARGS = ["auth", "session", "revoke"]
CLI_TIMEOUT = 60
CONNECT_TIMEOUT = 5
IO_TIMEOUT = 30
MAX_MESSAGE_BYTES = 128 * 1024 * 1024

REQUEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9:%_.~-]{0,799}")
TOKEN_SHAPE = re.compile(r"[\x21-\x7e]+")
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
MAX_ANSWER = 4000
MAX_SUMMARY = 2000
USER_APPROVED = "--user-approved"
DECISIONS = {"approve": "accept", "deny": "decline"}
APPROVAL_KINDS = {"command", "file-read", "file-change", "mcp-elicitation",
                  "permission"}
QUESTION_KIND = "user_input"
LOOPBACK = {"0.0.0.0": "127.0.0.1", "127.0.0.1": "127.0.0.1",
            "localhost": "127.0.0.1", "::": "::1", "::1": "::1"}
WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONTINUATION, OP_TEXT, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x8, 0x9, 0xA

USAGE = ("usage: t3a.py issue-token | renew | pending | "
         "answer <request-id> <response> [--user-approved]")
EXIT_CODES = {
    "USAGE": 2, "INVALID_REQUEST_ID": 2, "INVALID_ANSWER": 2,
    "USER_APPROVAL_REQUIRED": 2, "INVALID_APPROVAL_RESPONSE": 2,
    "NO_TOKEN": 3, "TOKEN_EXISTS": 3, "TOKEN_INSECURE": 3,
    "TOKEN_REJECTED": 3, "TOKEN_SCOPE": 3,
    "T3_NOT_RUNNING": 4, "T3_NOT_LOCAL": 4, "T3_PROTOCOL": 4,
    "T3CLI_NOT_FOUND": 5, "ISSUE_FAILED": 5, "RENEW_FAILED": 5,
    "REQUEST_NOT_FOUND": 6, "NOT_ANSWERABLE": 6, "DETAILS_UNAVAILABLE": 6,
    "MULTI_QUESTION": 6, "ANSWER_NOT_A_CHOICE": 6, "DECISION_NOT_OFFERED": 6,
    "SERVER_REJECTED": 7,
}


class T3aError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def token_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), TOKEN_FILE)


# --- issue-token ---------------------------------------------------------

def t3_cli() -> str:
    cli = os.environ.get("T3M_T3CLI", DEFAULT_CLI)
    if not os.path.isfile(cli) or not os.access(cli, os.X_OK):
        raise T3aError("T3CLI_NOT_FOUND",
                       f"t3 CLI not found or not executable: {cli}")
    return cli


def write_issued_token(cli: str, fd: int) -> None:
    """Issue a session with the CLI's stdout going straight into fd."""
    try:
        # The file is the CLI's stdout. Its stderr is discarded rather than
        # relayed: nothing it prints may reach the bridge's captured output.
        proc = subprocess.run([cli] + ISSUE_ARGS, stdin=subprocess.DEVNULL,
                              stdout=fd, stderr=subprocess.DEVNULL,
                              timeout=CLI_TIMEOUT)
        written = os.fstat(fd).st_size
    except (OSError, subprocess.TimeoutExpired) as e:
        raise T3aError("ISSUE_FAILED",
                       f"t3 CLI did not finish ({type(e).__name__}).")
    if proc.returncode != 0 or written == 0:
        raise T3aError("ISSUE_FAILED",
                       f"t3 CLI exited {proc.returncode} and wrote {written} "
                       "bytes.")


def issue_token() -> dict:
    cli = t3_cli()
    path = token_path()
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise T3aError("TOKEN_EXISTS",
                       f"{path} already exists. To replace it, run t3a.py "
                       "renew.")
    try:
        write_issued_token(cli, fd)
    except T3aError as e:
        os.unlink(path)
        raise T3aError(e.code,
                       f"{e.message} No token was saved. Run t3 auth session "
                       "issue by hand to see its error, and if a t3-muse "
                       "session now shows in t3 auth session list, revoke it.")
    finally:
        os.close(fd)
    expires = (datetime.datetime.now(datetime.timezone.utc)
               + datetime.timedelta(days=TOKEN_TTL_DAYS))
    return {
        "issued": True,
        "token_path": path,
        "expires_at": expires.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "note": (f"The token expires after {TOKEN_TTL_DAYS} days. To replace "
                 "it: t3a.py renew. To revoke it: t3 auth session list, then "
                 "t3 auth session revoke <id>."),
    }


def read_token(path: str = None) -> str:
    path = path or token_path()
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        raise T3aError("NO_TOKEN",
                       f"no token file at {path}; run t3a.py issue-token")
    except OSError as e:
        raise T3aError("TOKEN_INSECURE",
                       f"cannot open {path} safely ({e.strerror})")
    with os.fdopen(fd, encoding="ascii", errors="replace") as f:
        if os.fstat(f.fileno()).st_mode & 0o077:
            raise T3aError("TOKEN_INSECURE",
                           f"{path} is readable by others; it must be 0600")
        token = f.read().strip()
    if not TOKEN_SHAPE.fullmatch(token):
        raise T3aError("NO_TOKEN",
                       f"{path} does not hold a usable token; delete it and "
                       "run t3a.py issue-token")
    return token


# --- server address ------------------------------------------------------

def server_address() -> tuple:
    """Return the loopback (host, port) of the running T3 Code server."""
    home = os.environ.get("T3M_T3_HOME", DEFAULT_T3_HOME)
    path = os.path.join(home, "userdata", "server-runtime.json")
    try:
        with open(path, encoding="utf-8") as f:
            runtime = json.load(f)
    except (OSError, ValueError):
        raise T3aError("T3_NOT_RUNNING",
                       f"no readable server runtime file at {path}; "
                       "is T3 Code running?")
    host = runtime.get("host") if isinstance(runtime, dict) else None
    port = runtime.get("port") if isinstance(runtime, dict) else None
    if not isinstance(port, int) or not 1 <= port <= 65535:
        raise T3aError("T3_NOT_RUNNING", f"no usable port in {path}")
    target = LOOPBACK.get(host) if isinstance(host, str) else None
    if target is None:
        raise T3aError("T3_NOT_LOCAL",
                       f"{path} names a non-local host; refusing to send the "
                       "token anywhere but this Mac")
    return target, port


# --- minimal RFC 6455 client ---------------------------------------------

class WebSocket:
    """Just enough of a WebSocket client: text frames, ping, close."""

    def __init__(self, sock: socket.socket, leftover: bytes = b""):
        self._sock = sock
        self._buf = bytearray(leftover)

    @classmethod
    def connect(cls, host: str, port: int, token: str) -> "WebSocket":
        try:
            sock = socket.create_connection((host, port),
                                            timeout=CONNECT_TIMEOUT)
        except OSError as e:
            raise T3aError("T3_NOT_RUNNING",
                           f"T3 Code is not reachable on {host}:{port} "
                           f"({type(e).__name__})")
        try:
            sock.settimeout(IO_TIMEOUT)
            leftover = cls._handshake(sock, host, port, token)
        except BaseException:
            sock.close()
            raise
        return cls(sock, leftover)

    @staticmethod
    def _handshake(sock, host, port, token) -> bytes:
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        authority = f"[{host}]:{port}" if ":" in host else f"{host}:{port}"
        sock.sendall((
            "GET /ws?orchestrationProtocol=2 HTTP/1.1\r\n"
            f"Host: {authority}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"Authorization: Bearer {token}\r\n"
            "\r\n").encode("ascii"))
        data = b""
        while b"\r\n\r\n" not in data:
            chunk = sock.recv(4096)
            if not chunk or len(data) > 65536:
                raise T3aError("T3_PROTOCOL", "no valid WebSocket handshake")
            data += chunk
        head, leftover = data.split(b"\r\n\r\n", 1)
        lines = head.decode("latin-1").split("\r\n")
        parts = lines[0].split(" ")
        status = parts[1] if len(parts) > 1 else ""
        if status in ("401", "403"):
            raise T3aError("TOKEN_REJECTED",
                           f"T3 Code refused the token (HTTP {status}); it "
                           "may be expired or revoked. Run t3a.py renew")
        if status == "426":
            raise T3aError("T3_PROTOCOL",
                           "this T3 Code no longer speaks orchestration "
                           "protocol 2 (HTTP 426)")
        if status != "101":
            raise T3aError("T3_PROTOCOL",
                           f"WebSocket upgrade failed (HTTP {status})")
        headers = {}
        for line in lines[1:]:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(hashlib.sha1(
            (key + WS_GUID).encode("ascii")).digest()).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            raise T3aError("T3_PROTOCOL", "bad Sec-WebSocket-Accept")
        return leftover

    def _read(self, count: int) -> bytes:
        while len(self._buf) < count:
            chunk = self._sock.recv(65536)
            if not chunk:
                raise T3aError("T3_PROTOCOL", "connection closed by T3 Code")
            self._buf += chunk
        data = bytes(self._buf[:count])
        del self._buf[:count]
        return data

    def _send_frame(self, opcode: int, payload: bytes) -> None:
        size = len(payload)
        head = bytes([0x80 | opcode])
        if size < 126:
            head += bytes([0x80 | size])
        elif size < 65536:
            head += bytes([0x80 | 126]) + size.to_bytes(2, "big")
        else:
            head += bytes([0x80 | 127]) + size.to_bytes(8, "big")
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self._sock.sendall(head + mask + masked)

    def send_text(self, text: str) -> None:
        self._send_frame(OP_TEXT, text.encode("utf-8"))

    def recv_text(self) -> str:
        parts = []
        total = 0
        while True:
            byte0, byte1 = self._read(2)
            if byte0 & 0x70 or byte1 & 0x80:
                raise T3aError("T3_PROTOCOL", "unexpected WebSocket frame bits")
            size = byte1 & 0x7F
            if size == 126:
                size = int.from_bytes(self._read(2), "big")
            elif size == 127:
                size = int.from_bytes(self._read(8), "big")
            total += size
            if total > MAX_MESSAGE_BYTES:
                raise T3aError("T3_PROTOCOL", "WebSocket message too large")
            payload = self._read(size)
            opcode = byte0 & 0x0F
            if opcode == OP_PING:
                total -= size
                self._send_frame(OP_PONG, payload)
            elif opcode == OP_PONG:
                total -= size
            elif opcode == OP_CLOSE:
                raise T3aError("T3_PROTOCOL", "connection closed by T3 Code")
            elif opcode in (OP_TEXT, OP_CONTINUATION):
                parts.append(payload)
                if byte0 & 0x80:
                    return b"".join(parts).decode("utf-8")
            else:
                raise T3aError("T3_PROTOCOL",
                               f"unexpected WebSocket opcode {opcode}")

    def close(self) -> None:
        try:
            self._send_frame(OP_CLOSE, (1000).to_bytes(2, "big"))
        except OSError:
            pass
        self._sock.close()


# --- orchestration RPC envelope ------------------------------------------

def _exit_value(exit_message: dict):
    """Return a Success value, or raise for a Failure."""
    if exit_message.get("_tag") == "Success":
        return exit_message.get("value")
    for cause in exit_message.get("cause") or []:
        error = cause.get("error")
        if isinstance(error, dict):
            if error.get("_tag") == "EnvironmentAuthorizationError":
                raise T3aError("TOKEN_SCOPE", str(error.get("message"))[:500])
            text = error.get("detail") or error.get("message") or error.get("_tag")
            raise T3aError("SERVER_REJECTED", str(text)[:500])
        if "defect" in cause:
            raise T3aError("SERVER_REJECTED", str(cause["defect"])[:500])
    raise T3aError("SERVER_REJECTED", "the request ended without a result")


class Rpc:
    def __init__(self, ws):
        self._ws = ws
        self._ids = itertools.count(1)

    def _send(self, message: dict) -> None:
        self._ws.send_text(json.dumps(message))

    def _request(self, tag: str, payload: dict) -> str:
        request_id = str(next(self._ids))
        self._send({"_tag": "Request", "id": request_id, "tag": tag,
                    "payload": payload, "headers": []})
        return request_id

    def _messages(self):
        while True:
            decoded = json.loads(self._ws.recv_text())
            for message in decoded if isinstance(decoded, list) else [decoded]:
                if message.get("_tag") == "Defect":
                    raise T3aError("T3_PROTOCOL",
                                   str(message.get("defect"))[:500])
                yield message

    def call(self, tag: str, payload: dict):
        request_id = self._request(tag, payload)
        for message in self._messages():
            if (message.get("_tag") == "Exit"
                    and message.get("requestId") == request_id):
                return _exit_value(message["exit"])

    def shell_snapshot(self) -> dict:
        """First snapshot of the shell stream; the stream is then stopped."""
        request_id = self._request("orchestration.subscribeShell", {})
        for message in self._messages():
            if message.get("requestId") != request_id:
                continue
            if message.get("_tag") == "Exit":
                _exit_value(message["exit"])
                raise T3aError("T3_PROTOCOL", "shell stream had no snapshot")
            if message.get("_tag") == "Chunk":
                for item in message["values"]:
                    if item.get("kind") == "snapshot":
                        self._send({"_tag": "Interrupt",
                                    "requestId": request_id})
                        return item["snapshot"]
                self._send({"_tag": "Ack", "requestId": request_id})


def iter_pending(rpc: Rpc):
    """Yield (thread, request, turn item or None) for each pending request."""
    for thread in rpc.shell_snapshot()["threads"]:
        if not thread.get("pendingRuntimeRequest"):
            continue
        projection = rpc.call("orchestration.getThreadProjection",
                              {"threadId": thread["id"]})
        items = {item["requestId"]: item for item in projection["turnItems"]
                 if item.get("type") in ("approval_request",
                                         "user_input_request")}
        for request in projection["runtimeRequests"]:
            if request.get("status") == "pending" and (
                    request["kind"] in APPROVAL_KINDS
                    or request["kind"] == QUESTION_KIND):
                yield thread, request, items.get(request["id"])


# --- pending -------------------------------------------------------------

def _option_answer(option: dict) -> str:
    """The app sends an option's value when it has one, else its label."""
    return option["value"] if option.get("value") is not None else option["label"]


def _question_choices(question: dict) -> list:
    return [{"answer": _option_answer(option),
             "label": option.get("label"),
             "description": option.get("description")}
            for option in question.get("options") or []]


def _short(text):
    if text is None or len(text) <= MAX_SUMMARY:
        return text
    return text[:MAX_SUMMARY] + "…"


def _not_resumable_reason(request: dict):
    capability = request.get("responseCapability") or {}
    if capability.get("type") == "not_resumable":
        return capability.get("reason") or "T3 Code can no longer deliver this"
    return None


def _describe_approval(item: dict) -> dict:
    fields = {"summary": _short(item.get("prompt") or item.get("title"))}
    if item.get("reason"):
        fields["reason"] = _short(item["reason"])
    offered = {o["decision"]: o for o in item.get("options") or []}
    choices = []
    for answer, decision in DECISIONS.items():
        if offered and decision not in offered:
            continue
        choice = {"answer": answer}
        if offered.get(decision, {}).get("warning"):
            choice["warning"] = offered[decision]["warning"]
        choices.append(choice)
    fields["choices"] = choices
    return fields


def _describe_questions(item: dict) -> dict:
    questions = item.get("questions") or []
    if len(questions) == 1:
        fields = {"summary": _short(questions[0].get("question"))}
        choices = _question_choices(questions[0])
        if choices:
            fields["choices"] = choices
        return fields
    return {
        "summary": _short(" / ".join(str(q.get("question"))
                                     for q in questions)),
        "questions": [{"id": q.get("id"), "question": q.get("question"),
                       "choices": _question_choices(q)} for q in questions],
    }


def describe(thread: dict, request: dict, item) -> dict:
    is_question = request["kind"] == QUESTION_KIND
    entry = {
        "request_id": request["id"],
        "thread_id": thread["id"],
        "thread_title": thread.get("title"),
        "kind": "user-input" if is_question else "approval",
        "provider_kind": request["kind"],
        "created_at": request.get("createdAt"),
        "answerable": True,
    }
    if item is None:
        entry["summary"] = None
        entry["answerable"] = False
        entry["unanswerable_reason"] = "T3 Code returned no details for it"
        return entry
    if is_question:
        entry.update(_describe_questions(item))
        if len(item.get("questions") or []) != 1:
            entry["answerable"] = False
            entry["unanswerable_reason"] = (
                "it has several questions; answer it in the T3 Code app")
    else:
        entry.update(_describe_approval(item))
    reason = _not_resumable_reason(request)
    if reason:
        entry["answerable"] = False
        entry["unanswerable_reason"] = reason
    return entry


def list_pending(token: str) -> list:
    host, port = server_address()
    ws = WebSocket.connect(host, port, token)
    try:
        rpc = Rpc(ws)
        return [describe(*found) for found in iter_pending(rpc)]
    finally:
        ws.close()


def pending() -> list:
    return list_pending(read_token())


# --- answer --------------------------------------------------------------

def parse_answer(args: list) -> tuple:
    """Validate answer's arguments before anything else is touched."""
    user_approved = args[-1:] == [USER_APPROVED]
    rest = args[:-1] if user_approved else args
    if len(rest) != 2:
        raise T3aError("USAGE", USAGE)
    request_id, response = rest
    if not REQUEST_ID.fullmatch(request_id):
        raise T3aError("INVALID_REQUEST_ID", "not a valid request id")
    if response in DECISIONS:
        if not user_approved:
            raise T3aError("USER_APPROVAL_REQUIRED",
                           f"{response} needs {USER_APPROVED}, which records "
                           "that the human made this decision")
        return request_id, response, True
    if user_approved:
        raise T3aError("USAGE",
                       f"{USER_APPROVED} only goes with approve or deny")
    if not response.strip():
        raise T3aError("INVALID_ANSWER", "the answer text is empty")
    if len(response) > MAX_ANSWER:
        raise T3aError("INVALID_ANSWER",
                       f"the answer text is over {MAX_ANSWER} characters")
    if response.lstrip().startswith("-"):
        raise T3aError("INVALID_ANSWER",
                       "the answer text must not start with a dash")
    return request_id, response, False


def _question_answer(question: dict, text: str):
    resolved = next((_option_answer(option)
                     for option in question.get("options") or []
                     if text in (option.get("value"), option.get("label"))),
                    None)
    if resolved is None:
        if question.get("allowCustomAnswer") is False:
            raise T3aError("ANSWER_NOT_A_CHOICE",
                           "this question only accepts one of its choices")
        return text
    return [resolved] if question.get("multiSelect") else resolved


def respond_command(thread: dict, request: dict, item, response: str) -> dict:
    """Build the one runtime-request.respond command, or refuse."""
    reason = _not_resumable_reason(request)
    if reason:
        raise T3aError("NOT_ANSWERABLE", str(reason)[:500])
    if item is None:
        raise T3aError("DETAILS_UNAVAILABLE",
                       "T3 Code returned no details for this request, so it "
                       "cannot have been shown to the user; use the app")
    command = {
        "type": "runtime-request.respond",
        "commandId": f"t3a:{uuid.uuid4()}",
        "threadId": thread["id"],
        "requestId": request["id"],
    }
    if request["kind"] == QUESTION_KIND:
        questions = item.get("questions") or []
        if len(questions) != 1:
            raise T3aError("MULTI_QUESTION",
                           f"this request has {len(questions)} questions and "
                           "one text cannot answer them; use the app")
        question = questions[0]
        command["answers"] = {
            question["id"]: _question_answer(question, response.strip())}
        return command
    decision = DECISIONS.get(response)
    if decision is None:
        raise T3aError("INVALID_APPROVAL_RESPONSE",
                       "this request is an approval; the response must be "
                       f"exactly approve or deny, with {USER_APPROVED}")
    offered = [option["decision"] for option in item.get("options") or []]
    if offered and decision not in offered:
        raise T3aError("DECISION_NOT_OFFERED",
                       f"{response} is not offered for this request; use "
                       "the app")
    command["decision"] = decision
    return command


def answer(args: list) -> dict:
    request_id, response, user_approved = parse_answer(args)
    token = read_token()
    host, port = server_address()
    ws = WebSocket.connect(host, port, token)
    try:
        rpc = Rpc(ws)
        for thread, request, item in iter_pending(rpc):
            if request["id"] == request_id:
                break
        else:
            raise T3aError("REQUEST_NOT_FOUND",
                           "no pending request has that id; it may already "
                           "be answered")
        command = respond_command(thread, request, item, response)
        ack = rpc.call("orchestration.dispatchCommand", command)
    finally:
        ws.close()
    result = {
        "answered": True,
        "request_id": request_id,
        "thread_id": thread["id"],
        "kind": "user-input" if request["kind"] == QUESTION_KIND else "approval",
        "user_approved": user_approved,
        "ack": ack,
    }
    if "decision" in command:
        result["decision"] = command["decision"]
    return result


# --- renew ---------------------------------------------------------------

def muse_sessions(cli: str):
    """Return {session id: expiry} for the sessions named t3-muse.

    None means the list could not be read, and so proves nothing. Nothing the
    CLI prints here is relayed.
    """
    try:
        proc = subprocess.run([cli] + LIST_ARGS, stdin=subprocess.DEVNULL,
                              capture_output=True, text=True,
                              timeout=CLI_TIMEOUT)
        rows = json.loads(proc.stdout)
        if proc.returncode != 0 or not isinstance(rows, list):
            return None
        sessions = {}
        for row in rows:
            label = (row.get("client") or {}).get("label")
            if row.get("subject") == SESSION_NAME and label == SESSION_NAME:
                if not SESSION_ID.fullmatch(row["sessionId"]):
                    return None
                sessions[row["sessionId"]] = row.get("expiresAt")
    except (OSError, subprocess.TimeoutExpired, AttributeError, KeyError,
            TypeError, ValueError):
        return None
    return sessions


def new_session_id(before, after):
    """The one session that appeared since before, or None if not just one."""
    if before is None or after is None:
        return None
    new = [session_id for session_id in after if session_id not in before]
    return new[0] if len(new) == 1 else None


def token_session_id(token: str):
    """The session id a token names, or None when it cannot be read.

    T3 does not publish this format, so it is only used to refuse a revoke,
    never to choose what to revoke.
    """
    try:
        payload = token.split(".")[0]
        padded = payload + "=" * (-len(payload) % 4)
        session_id = json.loads(base64.urlsafe_b64decode(padded))["sid"]
    except (KeyError, TypeError, ValueError):
        return None
    return session_id if isinstance(session_id, str) else None


def revoke_session(cli: str, session_id: str) -> bool:
    """Revoke a session and say whether it has left the session list.

    The list decides, because the CLI also exits 0 when it found nothing to
    revoke.
    """
    try:
        subprocess.run([cli] + REVOKE_ARGS + [session_id],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=CLI_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        pass
    remaining = muse_sessions(cli)
    return remaining is not None and session_id not in remaining


def remove_stale_temp_files(path: str) -> None:
    """Clear what a run killed by the bridge's timeout could not clean up."""
    folder = os.path.dirname(path)
    for name in os.listdir(folder):
        if name.startswith(RENEW_TEMP_PREFIX):
            try:
                os.unlink(os.path.join(folder, name))
            except FileNotFoundError:
                pass


def check_new_token(temp: str) -> None:
    """Prove the new token works. Listing never answers anything."""
    try:
        token = read_token(temp)
    except T3aError:
        raise T3aError("ISSUE_FAILED",
                       "the t3 CLI did not print a usable token.")
    try:
        list_pending(token)
    except T3aError as e:
        raise T3aError(e.code,
                       f"the new token failed the pending check ({e.code}).")
    except (OSError, AttributeError, KeyError, TypeError, ValueError) as e:
        raise T3aError("T3_PROTOCOL",
                       "the new token failed the pending check "
                       f"({type(e).__name__}).")


def sync_folder(folder: str) -> None:
    """Make a rename durable; a filesystem that cannot do this is no failure."""
    try:
        fd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    except OSError:
        pass


def replace_token(cli: str, path: str) -> None:
    """Issue, check, then rename. The rename is all that ever touches path."""
    folder = os.path.dirname(path)
    try:
        fd, temp = tempfile.mkstemp(prefix=RENEW_TEMP_PREFIX, dir=folder)
    except OSError as e:
        raise T3aError("RENEW_FAILED",
                       f"could not create a temp file ({type(e).__name__}).")
    try:
        try:
            write_issued_token(cli, fd)
            os.fsync(fd)
        finally:
            os.close(fd)
        check_new_token(temp)
        os.replace(temp, path)
    except OSError as e:
        raise T3aError("RENEW_FAILED",
                       "could not put the new token in place "
                       f"({type(e).__name__}).")
    finally:
        try:
            os.unlink(temp)
        except OSError:
            pass
    # The revoke that follows cannot be undone, so the rename must last first.
    sync_folder(folder)


def discard_new_session(cli: str, before) -> str:
    """Revoke the session of a token that was thrown away.

    Left active, it would also count as a second old session in the next renew
    and stop that run from revoking anything.
    """
    after = muse_sessions(cli)
    if before is not None and after is not None and set(after) <= set(before):
        return "No new session was left behind."
    new = new_session_id(before, after)
    if new is not None and revoke_session(cli, new):
        return "The new session was revoked."
    return ("A new t3-muse session may be left behind with its token already "
            "discarded. To revoke it by hand on the Mac: t3 auth session "
            "list, then t3 auth session revoke <id> for the t3-muse session "
            "issued last; an older one belongs to the token file.")


def retire_old_session(cli: str, before, old_token_session) -> dict:
    """Revoke the old session if the lists prove which one it is.

    Only a session listed before this run issued anything can be revoked here,
    so the new session is never a candidate.
    """
    after = muse_sessions(cli)
    new = new_session_id(before, after)
    if new is None:
        result = {"expires_at": None}
        reason = "the session lists do not single out the new session"
    else:
        result = {"expires_at": after[new]}
        old = [session_id for session_id in before if session_id in after]
        if not old:
            return dict(result, old_session="none_active")
        if len(old) == 1:
            result["previous_expires_at"] = before[old[0]]
        if len(old) > 1:
            reason = f"{len(old)} older t3-muse sessions are active"
        elif old_token_session not in (None, old[0]):
            reason = "the older t3-muse session is not the old token's"
        elif revoke_session(cli, old[0]):
            return dict(result, old_session="revoked")
        else:
            reason = "the session list did not confirm the revoke"
    return dict(
        result, old_session="left_active",
        note=(f"The old t3-muse session was not revoked ({reason}); treat it "
              "as still active. To revoke it by hand on the Mac: t3 auth "
              "session list, then t3 auth session revoke <id> for each "
              "t3-muse session except the one issued last, which belongs to "
              "the new token."))


def renew() -> dict:
    # A token that has expired still passes this; it only proves the file is
    # there and safe. Checking the server first keeps a run that cannot verify
    # anything from issuing a session.
    old_token = read_token()
    cli = t3_cli()
    server_address()
    path = token_path()
    remove_stale_temp_files(path)
    before = muse_sessions(cli)
    try:
        replace_token(cli, path)
    except T3aError as e:
        raise T3aError(e.code,
                       f"{e.message} The token file was not changed. "
                       f"{discard_new_session(cli, before)}")
    return dict({"renewed": True},
                **retire_old_session(cli, before, token_session_id(old_token)))


# --- entry point ---------------------------------------------------------

def run(args: list):
    command, rest = (args[0], args[1:]) if args else (None, [])
    if command == "answer":
        return answer(rest)
    commands = {"issue-token": issue_token, "renew": renew, "pending": pending}
    if rest or command not in commands:
        raise T3aError("USAGE", USAGE)
    return commands[command]()


def main(argv: list) -> int:
    try:
        print(json.dumps(run(argv[1:]), indent=2))
        return 0
    except T3aError as e:
        code, message = e.code, e.message
    except socket.timeout:
        code, message = "T3_PROTOCOL", "T3 Code did not answer in time"
    except OSError as e:
        code, message = "T3_PROTOCOL", f"connection failed ({type(e).__name__})"
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        code, message = "T3_PROTOCOL", ("unexpected response from T3 Code "
                                        f"({type(e).__name__})")
    print(json.dumps({"error": code, "message": message}), file=sys.stderr)
    return EXIT_CODES[code]


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
