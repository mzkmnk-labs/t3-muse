# t3-muse

English | [日本語](README.ja.md)

A companion for working with T3 Code from Meta Muse. Built
incrementally and from scratch: each piece is added only when it is
actually needed.

## Architecture

![Architecture diagram. The user talks to Muse in chat, and Muse acts as PM, dispatching tasks to T3 Code over two paths. Path 1, the default for operations: Muse goes through the custom connector custom.t3code to the T3 Connect relay (HTTPS, OAuth authorization code with dynamic registration, a 30-day session and a mode cap), and the relay reaches the environment's official MCP server at /mcp, which operates the T3 Code server. This path starts, sends to, reads and lists threads and waits with t3_thread_wait, which Muse holds until the run is done and re-arms after a timeout. Path 2, the file mailbox bridge: Muse writes request JSON to the file mailbox on the Mac Studio through the paired device, and the mailbox returns result JSON to Muse. bridge.py polls the inbox and runs allowlisted commands only, on t3r.py, t3w.py and t3a.py. t3r.py opens the T3 Code state DB read-only, which the MCP cannot do. t3a.py relays only the answers the user decided in chat to T3 Code's pending approvals and questions over a loopback WebSocket, using a token it issues once through the t3 CLI; approvals need --user-approved, and they use this path because the MCP's t3_pending_request answers questions only. t3w.py is the fallback when the MCP is unavailable: it creates threads and sends prompts through the t3 CLI, which reaches T3 Code over a local session. T3 Code reads and writes the DB. The existing t3-tots setup for Grok points at the DB but is out of scope (do not touch). Separately, a file in the mailbox's restart-trigger folder makes launchd run restart-bridge.sh, a watchman that restarts only the bridge job.](docs/architecture.svg)

The user talks to Muse, the product. Muse acts as PM and dispatches tasks
to T3 Code over two paths.

**Path 1: operations (default).** Muse → the custom connector
`custom.t3code` → the T3 Connect relay → the environment's official MCP
server (`/mcp`) → the T3 Code server. The relay uses HTTPS and OAuth
(authorization code with dynamic registration), with a 30-day session and
a mode cap. Starting, sending to, reading and listing threads go this way,
and so does `t3_thread_wait`: to watch for completion, Muse holds the wait
and re-arms it after a timeout.

**Path 2: file mailbox bridge.** Muse → the file mailbox
`~/t3-muse-bridge`, through the paired device → the bridge daemon (launchd,
allowlist only), which runs:

- `t3r.py`, which reads `statev2.sqlite` read-only.
- `t3a.py`, which relays answers to approvals and questions. It relays only
  what the user decided in chat, and approvals need `--user-approved`.
- `t3w.py`, the fallback when the MCP is unavailable.

A watchman, `restart-bridge.sh`, restarts the daemon when a file lands in
`restart-trigger`.

**Why two paths.** The official MCP's `t3_pending_request` only answers
questions and cannot answer permission approvals, so `t3a.py` handles
approvals. The MCP has no direct database access, so `t3r.py` handles that.

Key points:

- On the mailbox path, the Mac has no way to actively connect to Muse.
  Muse places and fetches the files through the paired device.
- The bridge listens on no network port and runs nothing outside its
  allowlist (`bridge-ping`, the read-only `t3r.py` commands, the
  task-request `t3w.py` commands, and the answer-relay `t3a.py` commands).
- Direct database access is read-only. Task requests to T3 go through the
  official MCP by default and through T3 Code's own CLI (`t3w.py`) only
  when the MCP is unavailable, in both cases only for requests the user
  actually asked Muse to make. Permission approvals go only through
  `t3a.py`. Answers to T3's pending approvals and questions are relayed
  only when the user decided them.
- The existing Grok-side t3-tots setup (`~/t3-tots`) runs on the same
  Mac but is out of scope for this project.
- The bridge cannot restart itself, so a separate launchd job does it:
  a file dropped into `~/t3-muse-bridge/restart-trigger` runs
  `restart-bridge.sh`, which restarts only the bridge job. The bridge
  gains no `launchctl` rights.

Development flow: branch → pull request → CI (GitHub Actions) →
squash merge. Files under `.github/workflows/` are the one exception:
the Muse GitHub App cannot push them, so they go through a dedicated
PAT instead.

## Layout

| Path | Contents |
|---|---|
| `bridge/` | The always-on Mac bridge (path 2). Receives requests through the file mailbox via the paired device and runs allowlisted commands only (design notes in `bridge/README.md`) |
| `tools/` | Our own Mac-side tools, run by the bridge (path 2). `t3r.py` is a minimal read-only reader for T3 Code's state database (`schema` / `health` / `status`). `t3w.py` sends task requests through T3's bundled `t3 thread` CLI when the MCP is unavailable (`start` / `send` / `show` / `list` / `add-project`). `t3a.py` relays the user's answers to T3's pending approvals and questions (`issue-token` / `pending` / `answer`) |
| `skills/t3-muse-starter/` | A custom Muse skill for setting up t3-muse from scratch and running it day to day, with Muse as PM and T3 Code as the implementer |

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
