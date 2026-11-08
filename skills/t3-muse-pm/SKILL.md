# T3 Muse PM

Use this skill to run t3-muse day to day once it is set up, with Muse as PM and T3 Code as the implementer: dispatch work, relay approvals and questions, watch progress, merge with the user's approval and keep the setup healthy.

## Stay current

- Treat this skill as a map rather than the territory. The repository code and its docs define the bridge side, and the live server discovery documents, the server tool list and your connector doc define the MCP side.

- Follow those sources over this skill whenever they disagree, and say so in chat when a step here no longer matches reality.

- Read the numbers and behaviors named here as observations from the build they were verified against. T3 Code ships as a fast moving Nightly, so re-check the cheap facts before relying on them, such as the tool schemas in the server tool list, the expiry printed when a token is issued, and how long a held request survives before the relay cuts it.

- Expect the relay to cut a long held request at around two minutes with an HTTP 524, and design every wait to be re-armed rather than held once for a long stretch.

- Update this skill in the same change whenever you fix a behavior it describes, and keep the copy in the repository in step with the copy Muse loads.

## Work as PM

- Treat t3-muse as a companion that lets Muse manage work in T3 Code on the user's Mac while T3 Code does the implementation.

- Act as PM rather than implementer. Break work into briefs, dispatch them to T3, track progress, verify results and report back to the user.

- Split the channels by job. Use the official T3 MCP over T3 Connect for operations such as launching, messaging, reading and waiting on threads.

- Use the file mailbox bridge for what MCP cannot do. It reads the T3 database, relays approval and question answers, restarts the daemon and acts as a fallback when Connect is down.

- Run the setup in the T3 Muse Starter skill (skills/t3-muse-starter/, entered from setup.md) first when the bridge or the connector is not in place yet.

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

- Answer approvals through t3a. An approve or deny answer requires the flag --user-approved at both the bridge validator and the tool, and it records that the human decided in chat.

- Check the current tool description before relying on what the MCP pending request respond action can answer. In the build this skill was written against it covers user questions only and cannot approve a permission request, so approvals go through t3a.

- When t3a pending or answer returns TOKEN_REJECTED, the approval token has expired or been revoked. Renew it as Keep the credentials current describes, then relay the user's answer again.

## Monitor, report and merge

- Keep a watcher cron owned by the project goal with a two minute interval while dispatched work is in flight.

- Be the watcher's guardian yourself. No resident process outside this chat watches the watcher, and the watcher switches itself off when idle, so monitoring lasts only as long as you keep it alive. A skipped check lets it vanish silently between tasks.

- Run the liveness check before each of these: a new dispatch, a follow up to any thread including one that has already ended, and a report to the user on the state of T3. First check that the watcher cron is enabled and re-enable it when it is not. Then seed the state entry of each target thread in the watcher's state file as running with reported false, re-seeding an entry that records the thread as ended. Leave an entry alone when it already records the thread as in flight, so a pending request it has reported is not reported again. A new thread has no id until its launch returns, so seed it from the main chat right then, before the watcher's next run.

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

- Renew the token once 14 days or fewer remain before the recorded expires_at, rather than waiting for t3a to answer TOKEN_REJECTED. Run t3a renew through the bridge with a 300 second timeout while T3 Code is running. This is the normal route, and it also works once the token has expired. It issues a new token into a 0600 temp file, proves it with a pending list, swaps it in with an atomic rename and only then revokes the old session. A failure before the swap leaves the token file untouched. Report the facts it prints to the user, which are expires_at, old_session and, when one older session was found, previous_expires_at, and write the new expires_at into the record.

- Treat the manual steps as the fallback. When old_session comes back left_active nothing was revoked, because renew revokes only what the session lists prove is the old session. Follow the note it prints: on the Mac, use the bundled T3 CLI session list and revoke commands and keep the t3-muse session issued last, because it belongs to the new token. When renew cannot run or the token is no longer wanted, revoke the session with those commands and delete the token file, then run issue-token again only to reissue.

- Tell the user once the grant is about five days from its due day, and offer to run the sign-in again at a time that suits them, so it does not lapse in the middle of a task. Start it only when they agree, and record the new approval day.

- When an MCP call through the connector fails with an authentication error, such as a 401, a token reported invalid or expired, or a prompt to sign in again, suspect the grant first, above all when the record shows it near or past its due day. A 403 with a Cloudflare 1010 body is the relay refusing the client, not the grant.

- Restore the grant on the connector registration you made at setup. Keep its MCP URL, OAuth settings and scopes, and do not register a second connector. Start the sign-in again on that connector the way setup did, and ask the user for one thing only: to approve it in the T3 app, picking the same access mode as before.

- Once they approve, run the cheap MCP read again, and report the grant as restored only after it passes. Then record the new approval day and due day, and retry the call that failed.

- Keep working through the bridge while the grant is down. The t3r status read keeps the watcher going, and t3w is the fallback for dispatch.

- Go back to Connect the official MCP in the T3 Muse Starter skill when the new sign-in fails or the environment's MCP URL has changed, because then the registration itself needs redoing.

## Maintain and troubleshoot

- Replace bridge or tool files through the device file channel, verify them by reading back, and restart the daemon through the restart trigger instead of asking the user to run launchctl.

- Check the quarantine attribute first when a LaunchAgent load fails with error 5, and check for a Cloudflare 1010 body when the relay returns 403. When a request returns 401, check that a hand written client attached the credential, and for a call through the connector, follow the grant recovery in Keep the credentials current.

- Pass a project id or path to t3w list. A bare list fails from the tools folder because no project matches the current directory.

- Expect t3r to fall back to an immutable read when T3 Code is stopped and the database refuses a plain read only open. It reports the open mode it used.

- Read t3r status from the orchestration_v2 projection tables, and check its source field before trusting it. It says legacy when those tables are missing and status has fallen back to the old projection tables, which stopped updating in early October 2026 on the build this was verified against.

- Read session_status in v2 output as the latest run status, or idle with no runs, the same values the MCP thread list reports. Expect subagent threads only when they have a pending request, even though the waiting counts always include them.

- Never read the authentication tables or the scheduled task webhook secrets in the T3 database.

## Change this repository

- Update docs/architecture.svg, docs/architecture.ja.svg and both README files whenever the architecture changes. README.md and docs/architecture.svg are English, and README.ja.md and docs/architecture.ja.svg are Japanese.

- Keep this skill truthful as the surfaces move. When a T3 Code or Muse update changes a behavior named here, ship the skill correction with the same pull request that adapts to it.

- Keep personal paths and identifiers out of the repository. The plists keep the __HOME__ placeholder and expansion happens only at install time on the Mac.

- Write original code only. Do not port or closely copy any other companion implementation, even where a license would allow it.

## Available connectors

- Use the T3 Code MCP connector for thread operations and waits, and create it with the T3 Muse Starter skill when it does not exist yet.

- Use the GitHub connector to read pull requests and to merge with approval. Use a contents scoped token client only for files under .github/workflows, which the GitHub App cannot push.

- Use the paired Mac device channel to place files and mailbox requests. It has no shell, so every Mac side action goes through the bridge allowlist or through a plist the user loads once.
