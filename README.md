# t3-muse

English | [日本語](README.ja.md)

A companion for working with T3 Code from Meta Muse. Built
incrementally and from scratch: each piece is added only when it is
actually needed.

## Architecture

![Architecture diagram. Outside the Mac Studio, the user sends requests and approvals to Claude, and Muse writes request JSON to the file mailbox via the device channel. Inside the Mac Studio, the mailbox returns result JSON to Claude. bridge.py polls the inbox and runs allowlisted commands only, on t3r.py, t3w.py and t3a.py. t3r.py opens the T3 Code state DB read-only. t3w.py creates threads and sends prompts through the t3 CLI, which reaches T3 Code over a local session. t3a.py relays the user's answers to T3 Code's pending approvals and questions over a loopback WebSocket, using a token it issues once through the t3 CLI. T3 Code reads and writes the DB. The existing t3-tots setup for Grok points at the DB but is out of scope (do not touch).](docs/architecture.svg)

Key points:

- There is no path for the Mac to actively connect to Muse. Muse
  places and fetches files through its device channel — a file mailbox.
- The bridge listens on no network port and runs nothing outside its
  allowlist (`bridge-ping`, the read-only `t3r.py` commands, the
  task-request `t3w.py` commands, and the answer-relay `t3a.py` commands).
- Direct database access is read-only. Task requests to T3 go only
  through T3 Code's own CLI (`t3w.py`), and only for requests the user
  actually asked Muse to make. Answers to T3's pending approvals and
  questions go only through `t3a.py`, and only when the user decided them.
- The existing Grok-side t3-tots setup (`~/t3-tots`) runs on the same
  Mac but is out of scope for this project.

Development flow: branch → pull request → CI (GitHub Actions) →
squash merge. Files under `.github/workflows/` are the one exception:
the Muse GitHub App cannot push them, so they go through a dedicated
PAT instead.

## Layout

| Path | Contents |
|---|---|
| `bridge/` | The always-on Mac bridge. Receives requests through the file mailbox over Muse's device channel and runs allowlisted commands only (design notes in `bridge/README.md`) |
| `tools/` | Our own Mac-side tools. `t3r.py` is a minimal read-only reader for T3 Code's state database (`schema` / `health` / `status`). `t3w.py` sends task requests through T3's bundled `t3 thread` CLI (`start` / `send` / `show` / `list` / `add-project`). `t3a.py` relays the user's answers to T3's pending approvals and questions (`issue-token` / `pending` / `answer`) |

## Answering approvals and questions

T3 Code's public MCP and CLI surface cannot answer runtime requests, so
`t3a.py` uses the channel the app's own UI uses: the local server's
orchestration WebSocket API, with a session token limited to
`orchestration:read` and `orchestration:operate`.

```sh
python3 t3a.py issue-token                        # once; prints metadata only
python3 t3a.py pending                            # every request waiting on a human
python3 t3a.py answer <request-id> approve --user-approved
python3 t3a.py answer <request-id> deny --user-approved
python3 t3a.py answer <request-id> "Use the blue one"
```

Results are JSON on stdout. Errors are one JSON line with a stable code
on stderr (`NO_TOKEN`, `T3_NOT_RUNNING`, `USER_APPROVAL_REQUIRED`, ...;
the full list is at the top of `tools/t3a.py`). Usage errors exit 2.

**The `--user-approved` rule.** An approval's response must be exactly
`approve` or `deny`, and `--user-approved` must be present. Both the
bridge and the tool refuse it otherwise. The flag records that the human
decided; Muse must never pass it on its own judgment. It is a recorded
promise, not a proof: the tool cannot see who decided, so the request
stays in the bridge's `done/` folder for audit. `approve` grants that one
request (T3's `accept`), never a session-wide or permanent permission.

**Limits.** A request with several questions cannot be answered with one
text and is refused with `MULTI_QUESTION`; answer those in the app. For a
multi-select question, `answer` picks one option. Only `pending`'s
`answerable: true` entries can be answered.

**Token lifecycle.** The token lasts 90 days, and T3 Code then rejects it
(`TOKEN_REJECTED`). `issue-token` refuses to overwrite an existing file.
To reissue or revoke:

```sh
~/.t3/bin/t3 auth session list                    # find the t3-muse session id
~/.t3/bin/t3 auth session revoke <session-id>
rm ~/t3-muse-tools/.t3a-token
python3 t3a.py issue-token                        # only when reissuing
```

**Security.**

- The token never leaves the Mac. `t3a.py` only connects to the loopback
  address and refuses a non-local host in `server-runtime.json`
  (`T3_NOT_LOCAL`).
- The token is never printed. The CLI's stdout goes straight into a 0600
  file, the CLI's stderr is discarded, and the token is sent only in the
  `Authorization` header, never on a command line or in a URL. It does not
  appear in the mailbox or its `done/` archive.
- Muse only relays decisions the user made. It does not approve anything
  on its own.
- Holding the token is real authority: it can do anything those two scopes
  allow, including starting threads. Revoke it if the file may have been
  copied.

## How development proceeds

- First, get the bridge running on the Mac Studio and verify
  connectivity (bridge-ping)
- Then open the minimal read-only path
- Write skills one at a time, as they become necessary, matched to
  how the system actually behaves
