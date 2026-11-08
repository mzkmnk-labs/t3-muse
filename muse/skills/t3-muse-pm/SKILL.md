# T3 Muse PM

Use this skill to run t3-muse day to day once it is set up, with Muse as PM and T3 Code as the implementer: dispatch work, relay approvals and questions, watch progress, merge with the user's approval and keep the setup healthy.

## Stay current

- Use the T3 Muse Bridge skill (muse/skills/t3-muse-bridge/) for how to send bridge requests, what each allowlisted command takes and how to read its reply.

- Check the MCP side against the live server discovery documents, the server tool list and your connector doc. Follow them over this skill whenever they disagree, and say so in chat when a step here no longer matches reality.

- Read the numbers and behaviors named here as observations from the build they were verified against. T3 Code ships as a fast moving Nightly, so re-check the cheap facts before relying on them, such as the tool schemas in the server tool list, the expiry printed when a token is issued, and how long a held request survives before the relay cuts it.

- Expect the relay to cut a long held request at around two minutes with an HTTP 524, and design every wait to be re-armed rather than held once for a long stretch.

## Work as PM

- Treat t3-muse as a companion that lets Muse manage work in T3 Code on the user's Mac while T3 Code does the implementation.

- Act as PM rather than implementer. Break work into briefs, dispatch them to T3, track progress, verify results and report back to the user.

- Split the channels by job. Use the official T3 MCP over T3 Connect for operations such as launching, messaging, reading and waiting on threads.

- Use the file mailbox bridge for what MCP cannot do. It reads the T3 database, relays approval and question answers, restarts the daemon and acts as a fallback when Connect is down.

- Run the setup in the T3 Muse Starter skill (muse/skills/t3-muse-starter/, entered from setup.md) first when the bridge or the connector is not in place yet.

- Keep another companion's existing install on the user's machine untouched if one exists, and never open its token file.

## Dispatch work

- Launch threads through the MCP connector with a project id, a worktree workspace strategy from the base ref and a unique client request id that you reuse if a reply is lost.

- Write each brief so a fresh agent can finish without this chat. Include the background, the exact scope, the checks to run, the instruction to open a pull request, and the rule that T3 never merges its own pull requests.

- Pick the thread by what the work is. When the work continues the task a thread is already on, send the follow up to that same thread instead of starting a new one.

- When the task is a different piece of work, start a new thread for it, even inside the same project, rather than adding it to a thread that is on something else.

- Register a repository as a T3 project first when it is missing, using t3w add-project through the bridge or an MCP read to confirm the project id.

- Run the watcher liveness check in Monitor, report and merge before every dispatch and every follow up, so completions are reported without the user asking.

## Relay approvals and questions

- Never decide an approval or an answer yourself. Detect the pending request, tell the user what is being asked in this chat, and relay only the decision the user makes.

- Answer approvals with t3a answer through the bridge, in the form the T3 Muse Bridge skill gives. Add --user-approved only to relay a decision the user made in this chat, because the flag records that the human decided.

- Check the current tool description before relying on what the MCP pending request respond action can answer. In the build this skill was written against it covers user questions only and cannot approve a permission request, so approvals go through t3a.

- When t3a pending or answer returns TOKEN_REJECTED, the approval token has expired or been revoked. Renew it as Keep the credentials current describes, then relay the user's answer again.

## Build the watcher

- Build one watcher for each project goal, the first time a liveness check finds that the goal has none. When the goal has one that is disabled, re-enable it rather than building a second.

- The watcher is a cron owned by the project goal that runs every two minutes. Each run reads t3r status once through the bridge, keeps only the threads of the goal's T3 project, the seeded ones and any other of the project's threads that is waiting, compares them with the watcher's state file, and posts new completions, new failures and newly pending approvals and questions to the chat where you work with the user. Then it saves what it saw. A run with nothing new posts nothing. It only reads and reports. Starting threads, relaying answers and merging stay in this chat, where the user decides.

- The rules it follows are the ones in Monitor, report and merge. The prompt below spells them out, so a run needs nothing from this chat.

- Keep the state file where every run and every later chat can find it, such as a t3-muse folder in the project goal's workspace, with one file per project. Write its path and the cron's name and id into the record this environment keeps across chats, next to the two clocks in Keep the credentials current.

- Write the state file before you create the cron, starting from an empty threads object. With one thread seeded it looks like this:

```json
{
  "idle_runs": 0,
  "threads": {
    "<thread id>": {"status": "running", "reported": false, "latest_user_message_at": null}
  }
}
```

| Field | Meaning |
|---|---|
| `idle_runs` | How many idle runs have come in a row. A run that is not idle sets it to 0, and the watcher disables its cron when it reaches 15. Set it back to 0 whenever you re-enable the cron |
| `threads` | One entry per watched thread, keyed by its thread_id from t3r status |
| `status` | What the entry last recorded: running for a thread in flight, waiting for one with a pending approval or question, or the status its run ended with, such as completed or failed |
| `reported` | true once the waiting or ended status in the entry has been posted to the chat, and false while the thread runs |
| `latest_user_message_at` | null, or the thread's latest_user_message_at from the last status read, recorded when you re-seed an ended entry before a follow up |

- Fill these placeholders in the prompt with this environment's values:
  - `<project title>` is the T3 project's title, as the project_title field in t3r status gives it. That field is the only project field status has, and the watcher uses it only for threads that have no entry yet, so a renamed project does not hide the seeded ones.
  - `<project id>` is the T3 project id, which the MCP thread list takes for the fallback.
  - `<state file>` is the state file's path.
  - `<repository>` is the GitHub owner and repository of the project, for the pull request lookup.
  - `<chat>` is the chat where you and the user work on this project.
  - `<watcher name>` is the name you give the cron, such as t3-muse watcher followed by the project title, so a run and a later chat can find it.

- Create the cron with this environment's scheduling tool. Set it to run every two minutes, `*/2 * * * *` in cron syntax, and make the project goal its owner, so it belongs to the work it watches rather than to one chat. Deliver its results to `<chat>`, so a pending approval reaches the person who decides it. Give it the filled prompt.

- A run needs the paired Mac device channel for the bridge, the T3 MCP connector for the fallback and the GitHub connector for the pull request lookup. When the scheduling tool asks which tools a run may use, grant those three.

- Find out how the scheduling tool lets a run end without posting, and word the prompt's no-report step to match. A watcher that posts every two minutes buries the reports that matter.

- Re-check the status values in the prompt against the status values in the server tool list's thread list schema when you build. They come from the build this skill was verified against.

- Seed the target threads once the cron exists, as the liveness check describes. Check the scheduling tool's run history after the first run, and fix the prompt if that run failed or posted with nothing new to report.

- When the state file has gone missing, write it again as above and seed it, because the watcher disables itself when it cannot read the file.

- Give the cron this prompt, with the placeholders filled:

```text
You are <watcher name>, the t3-muse watcher for the T3 project <project title>. Each run reads T3 Code's state once, reports what is new to <chat> and saves what it saw. Only read and report. Never start or message a thread, never answer, approve or deny a request, and never merge.

1. Read the state file at <state file>. If it is missing or is not valid JSON, post one line saying so, disable this cron, <watcher name>, and stop.

2. Read t3r status through the t3-muse bridge on the Mac, as the T3 Muse Bridge skill describes. Make a request with argv ["python3", "t3r.py", "status"], timeout 120 and an id no request has used, made of letters, digits and dashes, such as watch, a short tag for the project, the date and the time. Write it into ~/t3-muse-bridge/inbox under a name that starts with a dot, then rename it to the id followed by .json. Wait up to 60 seconds for the reply of the same name in ~/t3-muse-bridge/outbox. Use the reply only when ok is true, exit_code is 0 and stdout parses as JSON whose source is orchestration_v2.

3. If the bridge gave no usable reply, list the threads of the T3 project <project id> with the T3 MCP connector instead, subagent threads included. Read a waiting status there as a pending request. If that fails too, change nothing, post nothing and stop.

4. Take every thread that has an entry in the state file, wherever status lists it, because the main chat seeds only this project's threads. Take a thread with no entry only when its project_title is <project title>. A run status of preparing, queued, starting, running or waiting means the thread is in flight. Completed, failed, interrupted, cancelled and rolled_back mean its run has ended. Idle means no run is recorded yet. A thread with a pending approval or question counts as waiting, whatever its run status.

5. Update each thread entry in the state file:
   - Waiting: unless the entry is waiting with reported true, report the pending approval or question and save waiting with reported true.
   - In flight: save running with reported false. This also covers a thread whose entry was ended and reported and that runs again after a follow up. Its next end or request is reported as usual.
   - Ended: if the entry has a latest_user_message_at and this read does not show a later one for the thread, the follow up has not been recorded yet, so leave the entry as it is. Otherwise, unless the entry already records this status with reported true, report it and save the status with reported true. Report completed as a completion, and every other ended status as a failure that names the status and gives last_error.
   - Idle: leave the entry as it is. A new thread reads idle until its first run is recorded.
   - Missing from a t3r status read: drop the entry if it is waiting, because status lists every waiting thread, and a subagent thread leaves the list once its request is answered. Otherwise leave it as it is. Drop nothing on an MCP read.

6. Give a thread of the project that has no entry one only when it is waiting, as a subagent thread is when it asks for approval. Report its request and save it as waiting with reported true. Ignore the project's other threads that have no entry.

7. For each completed thread with a branch, look up the pull request whose head is that branch in <repository> on GitHub, and give its number and URL when there is one.

8. Post one line per report to <chat>, with the thread's title and thread_id: completed with its pull request, a failure with its status and last_error, or waiting for an approval or an answer, which the user decides in <chat>. When there is nothing to report, post nothing.

9. The run is idle when no entry is running or waiting now and it reported nothing. Add 1 to idle_runs on an idle run, and set it to 0 on any other run. When idle_runs reaches 15, disable this cron, <watcher name>, and post nothing more.

10. Save the state file. Read it again first, keep the version of any entry that changed or was added since step 1, because the main chat seeds entries while runs go on, and write the rest of this run's result over it in one write.
```

## Monitor, report and merge

- Keep a watcher cron owned by the project goal with a two minute interval while dispatched work is in flight. Build it as Build the watcher describes when the goal has none.

- Be the watcher's guardian yourself. No resident process outside this chat watches the watcher, and the watcher switches itself off when idle, so monitoring lasts only as long as you keep it alive. A skipped check lets it vanish silently between tasks.

- Run the liveness check before each of these: a new dispatch, a follow up to any thread including one that has already ended, and a report to the user on the state of T3. First check that the watcher cron is enabled and re-enable it when it is not, setting idle_runs in its state file back to 0, or build it when the goal has none. Then seed the state entry of each target thread in the watcher's state file as running with reported false, re-seeding an entry that records the thread as ended. Leave an entry alone when it already records the thread as in flight, so a pending request it has reported is not reported again. A new thread has no id until its launch returns, so seed it from the main chat right then, before the watcher's next run.

- Run the upkeep check from Keep the credentials current as part of every liveness check, so the two credential clocks get looked at whenever work moves.

- Take as the targets of a status report the threads the report shows as still in flight, so a thread that has already ended is not seeded and reported a second time.

- Expect status to read idle for a thread right after its launch, because no run has been recorded yet. The seed keeps the next watcher run from taking it for idle. Treat a seeded thread as active until status shows its run has ended.

- When the entry you re-seed had already ended, also record in it the thread's latest_user_message_at from the last status read. Right after a follow up, status can still show the earlier run as ended because the new message is not recorded yet. Have the watcher treat that entry as still active until latest_user_message_at in status moves past the recorded value, and only then read an ended status as the new run's. When a read lacks that field, as the MCP fallback may, keep the entry active until a read that has it. Put the entry back as it was if the follow up fails to send.

- Have each run fetch t3r status through the bridge, compare it with the saved state file, report only new completions, new failures and newly pending requests, and then save what it saw. The watcher is a plain poll and never holds t3_thread_wait.

- Treat a thread as a new incarnation when its entry records it as ended and reported and status shows it running again, as happens after a follow up. Reset reported to false, save it as running, and report its next completion, failure or pending request as usual. A run that starts and ends between two polls never reads running, so this rule is the watcher's safety net and your re-seed before a follow up is the main guard.

- Find the pull request for a finished thread from the branch field in status. Look up the pull request for that head branch on GitHub, and put its number and URL in the report when one exists.

- Fall back to the MCP thread list when the bridge read fails, or when status reports the legacy source, and stay silent when both fail so a transient error never becomes a false report.

- Count a run as idle only when nothing is active and nothing was reported, and disable the watcher after fifteen consecutive idle runs, about thirty minutes at the two minute interval, so the gap between tasks does not switch it off. Nothing turns it back on except your next liveness check.

- Verify the continuous integration check runs for the pull request head commit before proposing a merge. The connector pull request status view can miss Actions check runs, and the check-runs API is the reliable source.

- Merge only with the user's approval and use a squash merge. The repository rejects direct pushes to main.

## Keep the credentials current

- Keep track of two clocks. The two credentials run out on their own schedules, and they renew in different ways.

- The MCP connector grant lasts about 30 days from the day the user approved the sign-in in the T3 app. Renewing it needs the user's consent every time, because only they can approve a sign-in. No tool prints when the grant ends, so the approval day is the anchor you count from.

- The t3a approval token lasts 90 days. You renew it yourself with t3a renew through the bridge, and the user only hears the result. Its expiry is the expires_at that issue-token and renew print. When the installer issued the token, that result stays in the mailbox outbox, and the installer also prints the expiry in the user's terminal.

- Write both clocks into the record this environment keeps across chats, such as your memory. Write it at setup and again after every renew and every re-approval: the day the grant was approved, the day about 30 days later when it comes due, the token's expires_at, and the last day an MCP read passed.

- Never leave a clock unknown. The expires_at that issue-token prints is counted on the Mac from the moment it issued the 90 day session, so record it as it is. The one renew prints comes from the T3 session list and is null when the lists do not single out the new session. Then record the day of the renew plus 90 days and mark it as estimated. When the grant's approval day is missing, ask the user when they approved it, and if nobody knows, offer to run the sign-in again so the clock starts from a known day.

- Start the upkeep check by comparing today with the recorded dates. It costs only a read of the record and tells you which of the steps below are due.

- Test the grant with one cheap MCP read, such as the project list, at the first liveness check of each day you work, and record the day it passed. An authentication error sends you to the grant recovery below.

- Renew the token once 14 days or fewer remain before the recorded expires_at, rather than waiting for t3a to answer TOKEN_REJECTED. Run t3a renew through the bridge while T3 Code is running, in the form and with the timeout the T3 Muse Bridge skill gives. This is the normal route, and it also works once the token has expired. A renew that fails before it swaps in the new token leaves the token file untouched. Report the facts it prints to the user, which are expires_at, old_session and, when one older session was found, previous_expires_at, and write the new expires_at into the record.

- Treat the manual steps as the fallback. When old_session comes back left_active nothing was revoked, because renew revokes only what the session lists prove is the old session. Follow the note it prints: on the Mac, use the bundled T3 CLI session list and revoke commands and keep the t3-muse session issued last, because it belongs to the new token. When renew cannot run or the token is no longer wanted, revoke the session with those commands and delete the token file, then run issue-token again only to reissue.

- Tell the user once the grant is about five days from its due day, and offer to run the sign-in again at a time that suits them, so it does not lapse in the middle of a task. Start it only when they agree, and record the new approval day.

- When an MCP call through the connector fails with an authentication error, such as a 401, a token reported invalid or expired, or a prompt to sign in again, suspect the grant first, above all when the record shows it near or past its due day. A 403 with a Cloudflare 1010 body is the relay refusing the client, not the grant.

- Restore the grant on the connector registration you made at setup. Keep its MCP URL, OAuth settings and scopes, and do not register a second connector. Start the sign-in again on that connector the way setup did, and ask the user for one thing only: to approve it in the T3 app, picking the same access mode as before.

- Once they approve, run the cheap MCP read again, and report the grant as restored only after it passes. Then record the new approval day and due day, and retry the call that failed.

- Keep working through the bridge while the grant is down. The t3r status read keeps the watcher going, and t3w is the fallback for dispatch.

- Go back to Connect the official MCP in the T3 Muse Starter skill when the new sign-in fails or the environment's MCP URL has changed, because then the registration itself needs redoing.

## Maintain and troubleshoot

- Replace bridge or tool files and restart the daemon as the T3 Muse Bridge skill describes, instead of asking the user to run launchctl.

- Check the quarantine attribute first when a LaunchAgent load fails with error 5, and check for a Cloudflare 1010 body when the relay returns 403. When a request returns 401, check that a hand written client attached the credential, and for a call through the connector, follow the grant recovery in Keep the credentials current.

- Look up the meaning of a t3r, t3w or t3a reply in the T3 Muse Bridge skill, from the t3r status fields, open mode and source to each error code.

- Never read the authentication tables or the scheduled task webhook secrets in the T3 database.

## Available connectors

- Use the T3 Code MCP connector for thread operations and waits, and create it with the T3 Muse Starter skill when it does not exist yet.

- Use the GitHub connector to read pull requests and to merge with approval. Use a contents scoped token client only for files under .github/workflows, which the GitHub App cannot push.

- Use the paired Mac device channel to place files and mailbox requests. It has no shell, so every Mac side action goes through the bridge allowlist, as the T3 Muse Bridge skill describes, or through a plist the user loads once.
