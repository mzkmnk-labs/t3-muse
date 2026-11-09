# T3 Muse Bridge

Use this skill whenever you send a request to the t3-muse bridge on the user's Mac: to check the bridge with bridge-ping, read T3 Code's state with t3r, start and message threads with t3w, list and answer pending approvals and questions with t3a, or restart the bridge daemon.

## Know the layout

- The bridge is a launchd daemon on the user's Mac that watches a folder mailbox. It listens on no network port and runs a fixed allowlist of commands and nothing else.

- You reach the Mac through the paired Mac device channel, which reads and writes files and has no shell. Every action on the Mac goes through a request in this mailbox, a file in the restart trigger folder, or a step you ask the user to run in a terminal.

- These paths sit under ~/t3-muse-bridge on the Mac:

| Path | Role |
|---|---|
| `inbox/` | You write one request file here per command |
| `outbox/` | The bridge writes each reply here as `<id>.json` |
| `done/` | The bridge moves each finished request here as `<id>.json` and keeps it as the audit record |
| `restart-trigger/` | Any file placed here restarts the daemon |
| `bridge.py` | The daemon |
| `restart-bridge.sh` | The restarter, which launchd runs when the trigger folder fills |
| `bridge.log` | The daemon's own output |
| `restart.log` | One line per restart |

- The tools the bridge runs sit in ~/t3-muse-tools: t3r.py, t3w.py and t3a.py. The approval token file .t3a-token sits there too, and you never open it.

## Send a request

- Write one JSON object per request:

```json
{"id": "20261108-001", "argv": ["python3", "t3r.py", "status"], "timeout": 120}
```

| Field | Rules |
|---|---|
| `id` | Always set it. The reply and the archive copy are named after it. Make it unique and use only letters, digits, `-` and `_`, such as the date and a counter. A reused id overwrites the earlier reply and archive copy |
| `argv` | Required. A non-empty list of strings that matches one allowlist entry exactly, as the sections under Use the allowlist give it |
| `env` | Optional. An object of string values. Only the variables that entry allows are passed on, and anything else is dropped without an error. Leave it out in normal use |
| `timeout` | Optional. Seconds, a number from 1 to 600, and 120 when left out. A value that is not a number or is out of range silently becomes 120, not an error |

- Name the file `inbox/<id>.json` with the same id as inside it. Without an id, the bridge names the reply after the file name instead.

- The bridge picks up every file in inbox whose name ends in .json, about twice a second, so a file must be complete before it gets that name. Write it first as `inbox/.<id>.tmp`, then rename it to `inbox/<id>.json`. If the device channel cannot rename, write the whole file in one write. A file read half written comes back as bad request json, so send it again under a new id.

- The bridge runs one request at a time, in file name order. A slow request holds back every request behind it, the watcher's t3r status poll included, so keep requests short.

- Each tool request starts a fresh process in ~/t3-muse-tools, with the daemon's environment plus the env values the entry allows.

- Put nothing secret in a request. Every request and reply stays on the Mac in done/ and outbox/.

## Read the reply

- Poll `outbox/<id>.json` until it appears. The bridge writes it under a temporary dot name and renames it into place, so once the file is there it is complete. Ignore dot files in outbox.

- Most requests answer within a few seconds. One takes at most its own timeout plus the time the requests ahead of it take.

- Once the reply is there, the request has moved to `done/<id>.json`. While the request file is still in inbox, it is waiting or running.

- A command that ran comes back like this:

```json
{"id": "20261108-001", "ok": true, "exit_code": 0, "stdout": "{\"db\": \"...\", \"open_mode\": \"ro\", \"source\": \"orchestration_v2\", ...}", "stderr": "", "finished_at": "2026-11-08T10:15:02+0900"}
```

- A request the bridge refused or could not finish comes back like this:

```json
{"id": "20261108-002", "ok": false, "exit_code": 2, "error": "t3w send needs a valid thread id", "finished_at": "2026-11-08T10:15:03+0900"}
```

- Read ok first. ok true means the bridge ran the command, and the command's own result is in exit_code, stdout and stderr. A tool that failed still comes back with ok true and a nonzero exit_code. ok false means the command did not run to the end: error says why, exit_code is always 2, and there is no stdout or stderr. A tool's own usage error also exits 2, so never judge a reply by exit_code alone.

- stdout and stderr are strings. The tools print JSON, except t3w add-project, so parse stdout as JSON, and stderr too for t3a errors. Each stream is cut to its first 200,000 characters with no marker.

- finished_at is the Mac's local time with its UTC offset.

- When a reply disagrees with this skill, go by the reply and say so in chat.

- A tool file missing from ~/t3-muse-tools shows as ok true with exit_code 2 and python's can't open file message in stderr.

## Handle a refusal

| `error` | Cause | Next step |
|---|---|---|
| `command not on the allowlist` | argv matches no entry | Fix argv to match an entry exactly |
| `argv must be a non-empty list of strings` | argv is missing, empty or holds a non-string | Fix argv |
| A rule message, such as `t3w message too long` or `t3a answer approve needs --user-approved` | argv matched an entry but broke one of its argument rules | Fix that argument; the message names the rule |
| `timeout after <n>s` | The command ran past its timeout and was killed | Check what it already did, as below |
| `not found (tools folder or program): ...` | ~/t3-muse-tools or python3 is missing | Ask the user to run `npx @mzkmnk-labs/t3-muse install` again |
| `bad request json: ...` | The file was not valid JSON | Send it again under a new id. This reply holds only ok and error, and the request moves to done/ under its file name |
| Any other text, such as a Python traceback | The bridge hit an unexpected error while running the request | Tell the user what it says. Errors the daemon hits outside a request go to ~/t3-muse-bridge/bridge.log |

- A timeout kills the tool but does not undo what it already did. A t3w start or send that timed out may still have created the thread or sent the message, so check with t3r status or t3w show before you send it again.

- When no reply comes, send bridge-ping. If that gets no reply either, the daemon is down or stuck behind a long request. Delete any request you no longer want from inbox, because it runs as soon as the daemon is free again, with nobody waiting for it. Then restart the bridge as Restart the bridge describes.

## Use the allowlist

| Entry | argv | env passed on | timeout |
|---|---|---|---|
| bridge-ping | `["bridge-ping"]` | None | Not used |
| t3r | `["python3", "t3r.py", "schema" \| "health" \| "status"]` | `T3M_DB`, `T3M_T3CLI` | 120 |
| t3w | `["python3", "t3w.py", <command>, ...]`, commands start, send, show, list and add-project | `T3M_DB`, `T3M_T3CLI` | 120, or 600 with --wait |
| t3a | `["python3", "t3a.py", <command>, ...]`, commands issue-token, renew, pending and answer | `T3M_T3CLI`, `T3M_T3_HOME` | 120, or 300 for renew |

- The first two strings are always exactly "python3" and the tool's file name. Anything else, such as a path to the tool or another program, is refused.

- The env variables point a tool at non-default paths: `T3M_DB` at the state database (default ~/.t3/userdata/statev2.sqlite), `T3M_T3CLI` at the T3 CLI (default ~/.t3/bin/t3), and `T3M_T3_HOME` at the T3 home folder (default ~/.t3). The defaults match a normal install, so leave env out.

### bridge-ping

- argv is exactly `["bridge-ping"]`. The bridge answers it itself without starting a tool.

- stdout holds `{"pong": true, "hostname": "...", "tools_folder_exists": true, "python": "3.9.6"}`. pong true proves the daemon is running. tools_folder_exists false means ~/t3-muse-tools is missing.

### t3r: read T3 Code's state

- argv is exactly `["python3", "t3r.py", <command>]` with schema, health or status, and nothing after it.

- t3r opens T3 Code's state database read-only and cannot change it. It works while T3 Code is stopped too.

- stdout is one JSON object. A failure is `{"error": "..."}` with exit 2 for a usage error, 3 when the database is not found and 4 when it cannot be opened or a query fails.

- schema gives `{"db", "open_mode", "tables": [{"name", "columns"}]}`, names only and no rows.

- health gives `{"db", "open_mode", "exists", "size_bytes", "page_count", "page_size", "approx_size_bytes", "journal_mode", "table_count"}`.

- status gives `{"db", "open_mode", "source", "counts", "threads"}`, an overview of the threads:

| Field | Meaning |
|---|---|
| `open_mode` | `ro`, or `ro+immutable` when the database refuses a plain read-only open, as can happen while T3 Code is stopped. An immutable read can miss the newest changes |
| `source` | `orchestration_v2` normally. `legacy` means the current tables are missing and the reply comes from the older ones, which stopped updating in early October 2026 on the build this was verified against, so do not trust a legacy status for what is running now |
| `counts` | `visible_threads`, `archived_threads`, `threads_waiting_approval` and `threads_waiting_input` |
| `threads` | The 30 most recently updated threads, plus any older thread still waiting on a request. Archived threads are left out. Subagent threads appear only while they have a pending request, and the waiting counts always include them |

- Each entry in threads holds these fields:

| Field | Meaning |
|---|---|
| `thread_id` | The thread id that t3w send and show take |
| `title`, `project_title` | The thread's and its project's titles |
| `branch` | The thread's git branch, used to find its pull request |
| `pending_approval_count` | Pending approvals, every pending kind except questions |
| `pending_user_input_count` | Pending questions |
| `updated_at` | When the thread last changed |
| `latest_user_message_at` | When the latest user message was recorded |
| `session_status` | The status of the latest run, such as running, completed or failed, or idle when the thread has no run yet. These are the values the T3 MCP thread list reports |
| `provider` | The agent provider of the latest run |
| `last_error` | The failure message of a failed latest run, up to 300 characters, or null |

### t3w: start and message threads

- t3w runs the thread and project commands of the T3 CLI bundled with T3 Code, so T3 Code must be running. start and send create threads and send prompts, so run them only for work the user asked for.

| Command after `"python3", "t3w.py"` | Does |
|---|---|
| `"start", <project>, <message>` and options | Starts a new thread and sends it the message |
| `"send", <thread-id>, <message>`, optionally `"--wait"` | Sends a follow up message to a thread |
| `"show", <thread-id>` | Shows one thread |
| `"list", <project>` | Lists a project's threads |
| `"add-project", <path>`, optionally `"--title", <title>` | Registers a folder as a T3 project |

- `<project>` is a project id or any path inside the project's folder, up to 500 characters.

- `<message>` is up to 20,000 characters. Neither a project nor a message may start with -, so begin a message that would start with a dash with a word instead.

- start takes its options after the project and the message, in any order:
  - `"--worktree", <ref>` runs the thread in a new git worktree based on that branch, tag or commit instead of the project folder, such as main or origin/main.
  - `"--branch", <name>` names the branch of that new worktree, so use it together with --worktree, such as t3/fix-login.
  - `"--wait"` waits for the run to finish, as described below.
  - A ref or a branch name is 1 to 100 characters. The first is a letter or a digit, and the rest are letters, digits, `.`, `_`, `/` or `-`.

- `<thread-id>` is a T3 thread id: 32 to 40 hex digits and dashes, a UUID, optionally prefixed with `mcp:`. Take it from the threadId that start prints or the thread_id in t3r status.

- send takes exactly a thread id and a message, with --wait only as the last argument. show takes exactly one thread id and no --wait.

- list takes at most one argument, which must not start with -. Always pass a project id or path. A bare list fails, because the tool runs in the tools folder, which belongs to no project.

- add-project takes an absolute path, starting with /, up to 500 characters and on one line. A title goes after the path as `"--title", <title>`, 1 to 200 characters and not starting with -.

- start, send, show and list print the CLI's JSON. start prints `{"threadId", "projectId", "runId"}`. add-project prints the CLI's plain text, not JSON.

- With --wait the tool returns only when the run ends, and exit_code is 0 when it completed, 1 when it failed and 2 when the agent is waiting for an answer from a human. t3w gives the CLI at most 560 seconds, so a --wait request needs timeout 600, and it holds back every other bridge request while it waits. Leave --wait off and follow the thread with t3r status instead.

- t3w's own failures are `{"error": "..."}` on stdout, with exit 2 for a usage error, 3 when the CLI is missing or not executable and 4 when the CLI ran past 560 seconds. Any other nonzero exit_code is the CLI's own, with its message in stderr or stdout.

### t3a: answer approvals and questions

- t3a relays answers to the approvals and questions T3 Code is waiting on, through T3 Code's local server on the Mac's loopback address, with the approval token in ~/t3-muse-tools/.t3a-token. T3 Code must be running.

| Command after `"python3", "t3a.py"` | Does | timeout |
|---|---|---|
| `"issue-token"` | Issues the approval token, once, and only on the user's explicit yes, as NO_TOKEN below describes | 120 |
| `"renew"` | Replaces the token with a new one | 300 |
| `"pending"` | Lists every request waiting on a human | 120 |
| `"answer", <request-id>, "approve", "--user-approved"` | Approves one approval request | 120 |
| `"answer", <request-id>, "deny", "--user-approved"` | Denies one approval request | 120 |
| `"answer", <request-id>, <text>` | Answers one question | 120 |

- issue-token, renew and pending take no arguments.

- `<request-id>` is the request_id from pending: 1 to 800 characters, the first a letter or a digit and the rest letters, digits or `:`, `%`, `_`, `.`, `~`, `-`.

- approve and deny must be followed by `--user-approved` as the very last argument, and both the bridge and the tool refuse them without it. Add the flag only when the user decided that request in chat. It records the user's decision.

- The flag goes with the words approve and deny themselves, so a question whose answer really is approve or deny needs it as well.

- An answer text is 1 to 4,000 characters, must not start with - even after leading spaces, must hold no NUL character, and never carries --user-approved.

- Every result is JSON on stdout with exit_code 0.

- pending gives a list. Each entry holds:

| Field | Meaning |
|---|---|
| `request_id` | The id that answer takes |
| `thread_id`, `thread_title` | The thread that is waiting |
| `kind` | `approval` or `user-input` |
| `provider_kind` | T3's own kind, such as command, file-read, file-change, mcp-elicitation, permission or user_input |
| `created_at` | When the request was made |
| `summary` | What is being asked, up to 2,000 characters |
| `reason` | Why an approval is asked, when T3 gives one |
| `choices` | For an approval, the decisions T3 offers, as `{"answer": "approve" \| "deny"}` with a `warning` when T3 gives one. For a single question with options, `{"answer", "label", "description"}` |
| `questions` | Only when one request holds several questions: `{"id", "question", "choices"}` for each |
| `answerable` | Whether t3a can answer it. When false, `unanswerable_reason` says why, and the user answers it in the T3 Code app |

- Requests the app does not put to the user are not listed.

- answer gives `{"answered": true, "request_id", "thread_id", "kind", "user_approved", "ack"}`, plus `"decision"` for an approval, which is accept or decline. approve grants that one request only, never a session-wide or permanent permission.

- To answer a question with options, send an option's answer or its label. A question that takes no custom answer refuses any other text. A multi-select question gets the one option you send.

- issue-token gives `{"issued": true, "token_path", "expires_at", "note"}`. It never prints the token itself, and it refuses with TOKEN_EXISTS when a token file is already there.

- renew gives `{"renewed": true, "expires_at", "old_session"}`, plus `"previous_expires_at"` when one older session was found and `"note"` when the old session was left active. It issues a new token into a temp file, proves it with a pending list, renames it over the token file and only then revokes the old session. A failure before the rename leaves the token file as it was. It also works after the token has expired.
  - expires_at is null when the session lists do not single out the new session.
  - old_session is `revoked` when the old session was revoked and is gone from the session list, `none_active` when no older t3-muse session was active, and `left_active` when nothing was revoked.
  - After left_active, the note gives the manual steps for the user on the Mac: `~/.t3/bin/t3 auth session list`, then `~/.t3/bin/t3 auth session revoke <id>` for each t3-muse session except the one issued last, which belongs to the new token.
  - A renew cut off by a restart or a timeout can leave a temp file and an extra t3-muse session behind. The next renew removes the temp file, but returns left_active because of the extra session.

- An error is one JSON line on stderr, `{"error": "<CODE>", "message": "..."}`, and exit_code tells the group:

| exit_code | Codes |
|---|---|
| 2 | USAGE, INVALID_REQUEST_ID, INVALID_ANSWER, USER_APPROVAL_REQUIRED, INVALID_APPROVAL_RESPONSE |
| 3 | NO_TOKEN, TOKEN_EXISTS, TOKEN_INSECURE, TOKEN_REJECTED, TOKEN_SCOPE |
| 4 | T3_NOT_RUNNING, T3_NOT_LOCAL, T3_PROTOCOL |
| 5 | T3CLI_NOT_FOUND, ISSUE_FAILED, RENEW_FAILED |
| 6 | REQUEST_NOT_FOUND, NOT_ANSWERABLE, DETAILS_UNAVAILABLE, MULTI_QUESTION, ANSWER_NOT_A_CHOICE, DECISION_NOT_OFFERED |
| 7 | SERVER_REJECTED |

- Act on the common codes like this:
  - NO_TOKEN: there is no token file, or it holds no usable token. Issuing one is the user's decision, and at setup the installer's prompt puts it to them in their own terminal first. Run issue-token only when the setup stopped without a token, because the prompt never appeared or the user said no to it, and only once the user has agreed explicitly in chat. Any answer but y at the prompt is a no, so never issue the token right after it. If the token is needed, explain again what it is for and ask once more. After setup, as when the file has gone missing, ask the same way before you issue one. When a file is there but unusable, issue-token refuses with TOKEN_EXISTS, so the user deletes the file on the Mac first.
  - TOKEN_REJECTED: the token has expired or been revoked. Run renew, then relay the answer again.
  - TOKEN_INSECURE: the token file is readable by others or cannot be opened safely, as with a symlink. Tell the user, who fixes it to 0600 on the Mac, or revokes and reissues it if it may have been copied.
  - T3_NOT_RUNNING: ask the user to start T3 Code.
  - REQUEST_NOT_FOUND: the request is gone or already answered. Run pending again.
  - MULTI_QUESTION, NOT_ANSWERABLE, DETAILS_UNAVAILABLE, DECISION_NOT_OFFERED: t3a cannot answer this one, so the user answers it in the T3 Code app.
  - ANSWER_NOT_A_CHOICE: send one of the question's options.

## Replace bridge and tool files

- Write the new file over the old one through the device channel, as `~/t3-muse-bridge/bridge.py` or `~/t3-muse-tools/<tool>.py`, and read it back to verify it.

- A new tool file takes effect at the next request, because each request starts a fresh process. A new bridge.py takes effect only after a restart.

- The user can also update everything by running `npx @mzkmnk-labs/t3-muse install` again. It replaces the shipped files and leaves the token, done/ and outbox/ alone, and it stops without changing anything while requests are waiting in inbox.

## Restart the bridge

- Check that inbox holds no .json request first. A restart cuts off the running request, which stays in inbox and runs again after the restart, so a t3w start or send could run twice.

- Place any file in `~/t3-muse-bridge/restart-trigger/`, such as `restart-trigger/restart`. Its name and content do not matter, and any item counts, a Finder .DS_Store too.

- The restarter empties the folder, restarts the bridge job and appends one line to restart.log. It can restart only the bridge. The bridge itself has no launchctl rights.

- Wait 10 to 20 seconds. launchd delays a run that comes within 10 seconds of the previous one, but never drops it.

- Read the last line of `~/t3-muse-bridge/restart.log`. `<time> kickstart exit=0` means the bridge restarted. `exit=113` means the bridge job is not loaded, so nothing restarted, and the user has to load it again, most simply by running the installer again. Any other code comes with launchctl's error text in the same log.

- Send bridge-ping and require pong true before you go on.

## Stop the bridge

- Stopping the bridge is the user's kill switch for the bridge path only, run on the Mac. The MCP connector keeps working, as the last point here describes.

- Unload both LaunchAgents to stop it: `launchctl unload ~/Library/LaunchAgents/com.muse.t3bridge.plist ~/Library/LaunchAgents/com.muse.t3bridge-restarter.plist`. The plists stay in ~/Library/LaunchAgents, so launchd loads both again at the next login.

- To keep it stopped, the user runs `npx @mzkmnk-labs/t3-muse uninstall`, which unloads both and deletes their plists but keeps the data folders. `uninstall --purge` also deletes ~/t3-muse-bridge and ~/t3-muse-tools once the user confirms, after it has unloaded both.

- Deleting the folder ~/t3-muse-bridge alone does not stop the bridge. The running daemon keeps watching the inbox path, and runs any request written there once the folder exists again. Unload first, then delete the folder if it is no longer wanted.

- With only the bridge unloaded, a restart trigger fails with exit=113 and does not start it again.

- Requests left in inbox run as soon as the bridge is back, and the installer refuses to run while any are there. Delete the ones that are no longer wanted.

- Deleting the token file does not end its T3 session, which stays valid until it expires. The user revokes it with `~/.t3/bin/t3 auth session list` and `~/.t3/bin/t3 auth session revoke <id>`.

- The MCP connector is a separate path, and nothing above touches it. Its grant lives in T3 Code, so the user stops it in the T3 Code settings by revoking the connected client, or by turning T3 Connect off, which also cuts their other clients that come in through it. That is done in T3 Code itself, and neither the bridge nor anything else in t3-muse can do it for them.

## Keep within bounds

- Run only what the allowlist offers. Never place a bridge.py with a wider allowlist, or another program, to get past a refusal. When a task needs a command the allowlist does not offer, tell the user.

- Never open, read, copy or move ~/t3-muse-tools/.t3a-token or the .t3a-token.renew-* temp files beside it. t3a reads the token itself.

- Never add --user-approved on your own judgment. Relay only the decisions the user made in chat.

- Run t3w start and send only for work the user asked for.

- Leave done/ and outbox/ as they are. They are the audit record of every request.

- Leave another companion's install on the Mac, and its token, untouched.
