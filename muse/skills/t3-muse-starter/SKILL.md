# T3 Muse Starter

Use this skill to set up t3-muse from scratch on a fresh Mac: install the bridge, connect the official T3 MCP, verify the whole path, and place the T3 Muse PM and T3 Muse Bridge skills that run it day to day.

## Stay current

- Send every bridge request in this setup as the T3 Muse Bridge skill describes. It is in muse/skills/t3-muse-bridge/ of the t3-muse repository, and the last step here places it; until then, read it there.

- Check the MCP side against the live server discovery documents, the server tool list and your connector doc. Follow them over this skill whenever they disagree, and say so in chat when a step here no longer matches reality.

- Read the numbers and behaviors named here as observations from the build they were verified against. T3 Code ships as a fast moving Nightly, so re-check the cheap facts before relying on them, such as the tool schemas in the server tool list, the expiry printed when a token is issued, and how long a held request survives before the relay cuts it.

- Expect the relay to cut a long held request at around two minutes with an HTTP 524, and design every wait to be re-armed rather than held once for a long stretch.

## Understand the shape

- t3-muse lets Muse manage work in T3 Code on the user's Mac. Muse is the PM and T3 Code does the implementation.

- Setup starts in chat. The user sends you one sentence that points at setup.md, or at setup.ja.md in Japanese, on raw.githubusercontent.com, and you lead from there. The installer is not the entry point but a step you hand the user in chat when you reach it, like the token prompt, copying the MCP URL and approving the sign-in.

- Setup builds two channels. The official T3 MCP over T3 Connect carries thread operations. The file mailbox bridge covers what MCP cannot do: it reads the T3 database, relays approval and question answers, fetches repositories onto the Mac, restarts the daemon and stands in when Connect is down.

- Require the Mac to be paired with Muse as a device before you set up the bridge. You place every mailbox request and read every reply through that pairing, so without it the bridge cannot be verified or used. The MCP channel does not need it. If the Mac is not paired, ask the user to open the Muse app on that Mac and pair it, and confirm you can reach it as a device before you go on. The installer's own checks run locally on the Mac, so a passing install says nothing about the pairing.

- This skill ends once setup is verified. Day-to-day work belongs to two companion skills, which the last step here places: T3 Muse PM in muse/skills/t3-muse-pm/, from dispatching briefs to relaying approvals and merging, and T3 Muse Bridge in muse/skills/t3-muse-bridge/, for sending bridge requests and reading their replies.

- Keep another companion's existing install on the user's machine untouched if one exists, and never open its token file.

## Set up the bridge on the Mac

- Prefer the npx installer to the manual route. When you reach this step, ask the user in chat to run npx @mzkmnk-labs/t3-muse install in a terminal on the Mac. It places the bridge and its tools, loads both LaunchAgents and checks bridge-ping, t3r health and t3a pending through the mailbox.

- Expect the token prompt on a machine with no token file. The installer asks No t3a token found. Issue one now? [y/N] in the user's terminal. This prompt is the normal way the token is issued, and the answer is the user's to give. Only y issues the token. You cannot see the terminal, so ask the user whether the prompt appeared and what they answered.

- Treat any answer but y as a no. The install then stops at t3a pending with NO_TOKEN. Do not issue the token yourself at that point. If the setup needs it, explain again what the token is for and ask once more.

- Expect no prompt when the installer runs without a terminal, or when a token file is there but unusable. The install then stops at t3a pending with NO_TOKEN as well.

- A TOKEN_REJECTED is never asked about, so replace that token with t3a renew.

- Fetching a repository through the bridge with t3g get needs ghq and git on the Mac. The installer does not check for them, and nothing else in setup needs them. If the user wants you to fetch repositories, ask whether ghq is installed, and if not, ask them to install it, for example with Homebrew.

- Fall back to the manual steps below only when the installer cannot be used. Place files through the paired Mac device channel, which has no shell, so every Mac side action goes through the bridge allowlist or through a plist the user loads once.

- Clone the t3-muse repository and read its README and bridge README before placing any files.

- Create the folder ~/t3-muse-bridge with inbox, outbox, done and restart-trigger inside it, plus the folder ~/t3-muse-tools.

- Place bridge.py and restart-bridge.sh in ~/t3-muse-bridge and place t3r.py, t3w.py, t3a.py and t3g.py in ~/t3-muse-tools.

- Expand the __HOME__ placeholder in both LaunchAgent plists to the user's home directory and place them in ~/Library/LaunchAgents.

- Remove the com.apple.quarantine attribute from any plist that arrived through a file transfer channel before loading it. launchd refuses a quarantined plist with the generic error Load failed 5 Input/output error even when the file itself is valid. The installer clears the attribute itself, so this lesson matters on the manual route.

- Load com.muse.t3bridge with launchctl and confirm the daemon answers before continuing.

- Restart the bridge later by placing any file into restart-trigger. The restarter job kickstarts the daemon and appends the result to restart.log, so the user never needs a terminal for restarts.

- Stop the bridge by unloading both LaunchAgent plists, and keep it stopped across logins with npx @mzkmnk-labs/t3-muse uninstall. Deleting the bridge folder alone does not stop a running daemon, so unload first and delete the folder after, if at all. This is the kill switch for the bridge only. The MCP connector is stopped in T3 Code, as Stop the bridge in the T3 Muse Bridge skill describes. Mention both to the user during setup.

## Connect the official MCP

- Ask the user to enable T3 Connect in the T3 Code environment settings and to copy the environment MCP URL from a client that is itself connected over T3 Connect, such as the web app. The address copied on the host can be a LAN address, which Muse cannot reach and which cannot complete sign-in.

- Register the environment as a custom connector with the custom_connector_setup skill. Use OAuth authorization code with dynamic client registration, no client secret, PKCE S256, the scopes orchestration:read and orchestration:operate, and the MCP URL as the resource.

- Have the user approve the sign-in inside the T3 app and pick an access mode that permits the launches the setup should run. The grant lasts about 30 days and the chosen mode caps everything the connector can do.

- Send a browser user agent header on every request from any hand written client. The relay sits behind Cloudflare and bans the default Python user agent signature with error 1010.

- Give a hand written client a socket timeout comfortably above the longest single wait you plan to hold, so a held wait is not cut short locally.

- Add tools to a client allowlist one at a time and only after reading the tool schema from the server tool list.

## Verify the whole path

- Send a bridge-ping request through the mailbox and require a pong before trusting the bridge. When you cannot reach the Mac or cannot place the request in inbox, check the pairing first, as Understand the shape describes. When the request is in place but no pong comes, the daemon is the suspect, so follow the T3 Muse Bridge skill for a bridge that does not reply.

- Run t3r health and t3w list for a known project through the bridge and check that both succeed. Pass a project id or path to t3w list, because a bare list fails from the tools folder when no project matches the current directory.

- Issue the approval token through the bridge with t3a issue-token only when the setup stopped without a token, because the prompt never appeared or the user said no to it, and only after the user agrees explicitly in chat. After a no at the prompt, that means asking again as described above, never issuing on the spot. The user can instead run python3 ~/t3-muse-tools/t3a.py issue-token in the terminal and then install again.

- Expect issue-token to write the bundled T3 CLI output straight into a token file with 0600 permissions, so the token value is never printed, logged or sent to Muse. It refuses with TOKEN_EXISTS when a token file is there but unusable, so the user deletes the file on the Mac first.

- Run t3a pending and accept an empty list as proof that the local WebSocket round trip works.

- Finish verification with one harmless MCP read such as the project list before dispatching real work.

- Once that read passes, write the day the user approved the sign-in and the token's expires_at into the record you keep across chats, as Keep the credentials current in T3 Muse PM describes, so day-to-day work starts with both clocks known.

## Place the day-to-day skills

- As the last setup step, place the two companion skills from the same t3-muse repository, the same way setup.md had you place this skill. Put the muse/skills/t3-muse-pm/ folder (SKILL.md and skill.yaml) in the user's custom skills folder at ~/workspace/custom-skills/t3-muse-pm/, and the muse/skills/t3-muse-bridge/ folder (SKILL.md and skill.yaml) at ~/workspace/custom-skills/t3-muse-bridge/.

- Report the verification results to the user and tell them that day-to-day work now runs through T3 Muse PM, with T3 Muse Bridge for the bridge.
