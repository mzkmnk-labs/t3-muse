# t3-muse

English | [日本語](README.ja.md)

A companion for working with T3 Code from Meta Muse.

## Getting started

Send your Muse this sentence:

```text
Read https://raw.githubusercontent.com/mzkmnk-labs/t3-muse/main/setup.md and set up t3-muse for me.
```

[setup.md](setup.md) is a setup instruction written for Muse. Muse reads
it, runs the setup, and stops to tell you in chat whenever a step needs
you. Before you send it, make sure T3 Code is running on your Mac and
Node.js 24 or later is installed there.

Only you can take these steps, and Muse asks for each one when it gets
there:

- **Run the installer.** Run the command Muse gives you,
  `npx @mzkmnk-labs/t3-muse install`, in a terminal on the Mac. A first
  install asks whether to issue the t3a token, which relays your answers
  to T3 Code's pending approvals and questions. The answer is yours.
- **Copy the MCP URL.** Enable T3 Connect in the T3 Code environment
  settings, then copy the environment's MCP URL from a client connected
  through T3 Connect, such as the web app, and give it to Muse. A URL
  copied on the host Mac can be a LAN address, which Muse cannot reach.
- **Approve the sign-in.** When Muse starts the OAuth sign-in for its
  connector, approve it in the T3 Code app and pick an access mode. The
  mode caps everything Muse can do through the connector.

Muse never takes these steps for you, and never decides approvals or
answers to T3 Code's questions on its own. To run the installer yourself,
see [Manual setup](#manual-setup).

## Architecture

![Architecture diagram. The user talks to Muse in chat, and Muse acts as PM, dispatching tasks to T3 Code over two paths. Path 1, the default for operations: Muse goes through the custom connector custom.t3code to the T3 Connect relay (HTTPS, OAuth authorization code with dynamic registration, a 30-day session and a mode cap), and the relay reaches the environment's official MCP server at /mcp, which operates the T3 Code server. This path starts, sends to, reads and lists threads, and the watcher falls back to the thread list when the bridge read fails. Path 2, the file mailbox bridge: Muse writes request JSON to the file mailbox on the Mac Studio through the paired device, and the mailbox returns result JSON to Muse. bridge.py polls the inbox and runs allowlisted commands only, on t3r.py, t3w.py and t3a.py. t3r.py opens the T3 Code state DB read-only, which the MCP cannot do, and the watcher fetches its status through the mailbox every two minutes. t3a.py relays only the answers the user decided in chat to T3 Code's pending approvals and questions over a loopback WebSocket, using a token it issues and renews through the t3 CLI; approvals need --user-approved, and they use this path because the MCP's t3_pending_request answers questions only. t3w.py is the fallback when the MCP is unavailable: it creates threads and sends prompts through the t3 CLI, which reaches T3 Code over a local session. T3 Code reads and writes the DB. Separately, a file in the mailbox's restart-trigger folder makes launchd run restart-bridge.sh, a watchman that restarts only the bridge job.](docs/architecture.svg)

The user talks to Muse, the product. Muse acts as PM and dispatches tasks
to T3 Code over two paths.

**Path 1: operations (default).** Muse → the custom connector
`custom.t3code` → the T3 Connect relay → the environment's official MCP
server (`/mcp`) → the T3 Code server. The relay uses HTTPS and OAuth
(authorization code with dynamic registration), with a 30-day session and
a mode cap. Starting, sending to, reading and listing threads go this way.
The watcher uses the MCP thread list only as a fallback (see "Watching for
completion" below).

**Path 2: file mailbox bridge.** Muse → the file mailbox
`~/t3-muse-bridge`, through the paired device → the bridge daemon (launchd,
allowlist only), which runs:

- `t3r.py`, which reads `statev2.sqlite` read-only. The watcher polls its
  `status`.
- `t3a.py`, which relays answers to approvals and questions. It relays only
  what the user decided in chat, and approvals need `--user-approved`.
- `t3w.py`, the fallback when the MCP is unavailable.

A watchman, `restart-bridge.sh`, restarts the daemon when a file lands in
`restart-trigger`.

**Why two paths.** The official MCP's `t3_pending_request` only answers
questions and cannot answer permission approvals, so `t3a.py` handles
approvals. The MCP has no direct database access, so `t3r.py` handles that.

**Watching for completion.** While dispatched work is in flight, Muse runs
a watcher cron every two minutes. Each run fetches `t3r status` through the
bridge, compares it with the watcher's state file, reports only new
completions, new failures and newly pending approvals and questions, and
then saves what it saw. The watcher is a plain poll and never holds
`t3_thread_wait`. The "Build the watcher" section of the T3 Muse PM skill
gives the cron's prompt template and the state file's format.

- Muse keeps the watcher alive itself, and no resident guardian runs
  outside the chat. Before a dispatch, a follow-up or a report on the
  state of T3, the main chat checks that the watcher is enabled, turns it
  back on if not, and marks each target thread that has no entry yet, or
  has ended, as running and not yet reported in the state file. Right after a launch, status can still read
  idle because no run has been recorded yet, and the mark keeps the
  watcher from taking the thread for idle.
- A thread that was reported as ended and runs again, for example after a
  follow-up, counts as a new incarnation. The watcher clears its reported
  mark and reports its next completion, failure or pending request as
  usual.
- For a finished thread, the watcher takes the `branch` field from status
  and looks up the pull request for that branch on GitHub.
- When the bridge read fails, or status reports the `legacy` source, the
  watcher falls back to the MCP thread list. When both fail it stays
  silent, so a transient error never becomes a false report.
- A run counts as idle when nothing is active and nothing was reported.
  After 15 consecutive idle runs, about 30 minutes, the watcher disables
  itself, and Muse's next check turns it back on.

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
| `tools/` | Our own Mac-side tools, run by the bridge (path 2). `t3r.py` is a minimal read-only reader for T3 Code's state database (`schema` / `health` / `status`). `t3w.py` sends task requests through T3's bundled `t3 thread` CLI when the MCP is unavailable (`start` / `send` / `show` / `list` / `add-project`). `t3a.py` relays the user's answers to T3's pending approvals and questions (`issue-token` / `renew` / `pending` / `answer`) |
| `installer/`, `package.json` | The npm package `@mzkmnk-labs/t3-muse`: a Node CLI with no dependencies that installs the bridge and tools on the Mac (`install` / `status` / `uninstall`) |
| `muse/skills/t3-muse-starter/` | The custom Muse skill for setting up t3-muse from scratch: the bridge, the MCP connector and verification. [setup.md](setup.md) has Muse place this one, and its last step places the PM and bridge skills |
| `muse/skills/t3-muse-pm/` | The custom Muse skill for running t3-muse day to day, with Muse as PM and T3 Code as the implementer: dispatching, relaying approvals and questions, monitoring, merging and maintenance |
| `muse/skills/t3-muse-bridge/` | The custom Muse skill for using the bridge: the request and reply format, each allowlisted command with its arguments and errors, and restarting and stopping the daemon |

## Manual setup

During setup, Muse asks you to run the installer when it gets to that
step. This section is for running it yourself: to install or update the
Mac side without Muse walking you through it, or to check on or remove
an install. The installer covers only the Mac side, the bridge and its
tools. Connecting the MCP and placing the skills still happen in Muse, so
send it the sentence in [Getting started](#getting-started) afterwards.

```sh
npx @mzkmnk-labs/t3-muse install            # place the files, load both LaunchAgents, verify
npx @mzkmnk-labs/t3-muse status             # report the state; never shows the token
npx @mzkmnk-labs/t3-muse uninstall          # unload and delete the plists; keeps the data
npx @mzkmnk-labs/t3-muse uninstall --purge  # also delete both data folders, once you confirm
```

Nothing runs on `npm install`; every change comes from one of these
subcommands. `install` succeeds only when `bridge-ping`, `t3r health` and
`t3a pending` all pass through the mailbox, so T3 Code must be running and
the approval token must exist.

A fresh machine has no token file yet. On a terminal, `install` asks
`No t3a token found. Issue one now? [y/N]`. Only `y` issues one, through
`t3a.py issue-token` in the mailbox, and `t3a pending` then runs again. `install`
prints what it issued (the scopes and the 90-day lifetime), where the file is
and how to revoke it, and never the token itself.

Any other answer changes nothing, and neither does a run without a terminal
on stdin, such as CI or a script, which is never asked. In both cases `install`
exits 1. Issue the token yourself and run `install` again:

```sh
python3 ~/t3-muse-tools/t3a.py issue-token
```

If the token has expired or been revoked, `install` stops with
`TOKEN_REJECTED` and leaves it alone. It does not ask, because replacing a
credential is a heavier step than one prompt. Replace it with `t3a.py renew`.

Running `install` again is safe, and it is also how to update. It replaces
the shipped files and leaves the token and the audit history alone. It
changes nothing while requests are waiting in the inbox, because restarting
the bridge would run them twice. To place the files without the installer,
see `bridge/README.md`.

## Answering approvals and questions

T3 Code's public MCP and CLI surface cannot answer runtime requests, so
`t3a.py` uses the channel the app's own UI uses: the local server's
orchestration WebSocket API, with a session token limited to
`orchestration:read` and `orchestration:operate`.

```sh
python3 t3a.py issue-token                        # once; prints metadata only
python3 t3a.py renew                              # replaces the token; prints metadata only
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
(`TOKEN_REJECTED`). `renew` replaces it through the bridge, and also works
once it has expired. T3 Code has to be running.

`renew` issues a new token into a 0600 temp file beside the token file,
lists pending requests with it, renames it over the token file, and only
then revokes the old session. A failure before the rename leaves the token
file as it was. Once the rename is done it exits 0, with the new
`expires_at`, `previous_expires_at` when one older session was found, and
one of these:

| `old_session` | Meaning |
| --- | --- |
| `revoked` | Revoked, and gone from a fresh `t3 auth session list` |
| `none_active` | No older `t3-muse` session was active, so nothing needed revoking |
| `left_active` | Nothing was revoked; `note` gives the reason and the manual steps |

A session is revoked only when its `subject` and `client.label` are both
`t3-muse`, it was listed before the new token was issued, exactly one new
session appeared, and exactly one older one is left. If the old token
names a session, it has to be that one too. `expires_at` is null when the
lists do not single out the new session.

`issue-token` refuses to overwrite an existing file. When `renew` cannot
run or the token is no longer wanted, revoke by hand:

```sh
~/.t3/bin/t3 auth session list                    # find the t3-muse session id
~/.t3/bin/t3 auth session revoke <session-id>
rm ~/t3-muse-tools/.t3a-token
python3 t3a.py issue-token                        # only when reissuing
```

After `left_active`, run only the first two lines, and keep the `t3-muse`
session issued last, because it belongs to the new token.

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

## License

t3-muse is released under the MIT License. See [LICENSE](LICENSE).
