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

- Re-enable the watcher cron on every dispatch so completions are reported without the user asking.

## Relay approvals and questions

- Never decide an approval or an answer yourself. Detect the pending request, tell the user what is being asked in this chat, and relay only the decision the user makes.

- Answer approvals through t3a. An approve or deny answer requires the flag --user-approved at both the bridge validator and the tool, and it records that the human decided in chat.

- Check the current tool description before relying on what the MCP pending request respond action can answer. In the build this skill was written against it covers user questions only and cannot approve a permission request, so approvals go through t3a.

- Update the approval token by running t3a renew through the bridge with a 300 second timeout. This is the normal route, and it also works once the token has expired. The token lifetime is 90 days. It issues a new token into a 0600 temp file, proves it with a pending list, swaps it in with an atomic rename and only then revokes the old session. A failure before the swap leaves the token file untouched. Report the facts it prints to the user, which are expires_at, old_session and, when one older session was found, previous_expires_at.

- Treat the manual steps as the fallback. When old_session comes back left_active nothing was revoked, because renew revokes only what the session lists prove is the old session. Follow the note it prints: on the Mac, use the bundled T3 CLI session list and revoke commands and keep the t3-muse session issued last, because it belongs to the new token. When renew cannot run or the token is no longer wanted, revoke the session with those commands and delete the token file, then run issue-token again only to reissue.

## Monitor, report and merge

- Keep a watcher cron owned by the project goal with a two minute interval while dispatched work is in flight.

- Have each run list the project threads through the MCP helper, compare them with a saved state file, and report only new completions, new failures and newly pending requests, with the pull request number and URL when present.

- Have the run hold t3_thread_wait on one running thread with a timeout of about 90000 ms after the comparison, fold a settled result into the same report, and treat a timeout or a relay error as a normal end because the next run re-arms the wait.

- Fall back to the bridge thread list when the MCP read fails, and stay silent when both fail so a transient error never becomes a false report.

- Count a run as idle only when nothing is active and nothing was reported, and disable the watcher after two consecutive idle runs. It restarts on the next dispatch.

- Verify the continuous integration check runs for the pull request head commit before proposing a merge. The connector pull request status view can miss Actions check runs, and the check-runs API is the reliable source.

- Merge only with the user's approval and use a squash merge. The repository rejects direct pushes to main.

## Maintain and troubleshoot

- Replace bridge or tool files through the device file channel, verify them by reading back, and restart the daemon through the restart trigger instead of asking the user to run launchctl.

- Re-approve the MCP connector grant when it expires at about 30 days and renew the approval token with t3a renew before its 90 days run out.

- Check the quarantine attribute first when a LaunchAgent load fails with error 5, check for a Cloudflare 1010 body when the relay returns 403, and check that the credential was attached when it returns 401.

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
