# Setting up t3-muse

English | [日本語](setup.ja.md)

You give Muse one prompt, and Muse sets up t3-muse on your Mac by
following its starter skill. It does most of the work itself and stops
to tell you what to do at the few steps only you can take.

## Before you start

- T3 Code is running on your Mac.
- You have a Muse account.
- Node.js 18 or later is installed on the Mac.

## The prompt to give Muse

Paste this into a chat with Muse:

```text
Please set up t3-muse for me.

Fetch the repository https://github.com/mzkmnk-labs/t3-muse and place its skills/t3-muse-starter/ folder (SKILL.md and skill.yaml) into my custom skills folder at ~/workspace/custom-skills/t3-muse-starter/.

Then follow that skill (T3 Muse Starter) to run the setup. Whenever a step needs me — running npx @mzkmnk-labs/t3-muse install in a terminal, answering the token prompt, enabling T3 Connect in T3 Code and giving you the MCP URL, approving the connection — tell me exactly what to do. Decisions on approvals and questions are mine to make.
```

## What happens next

Muse places the skill and works through it. These are the steps where
it needs you, in order.

### 1. Run the installer and answer the token prompt

Muse asks you to run this in a terminal on the Mac:

```sh
npx @mzkmnk-labs/t3-muse install
```

It places the bridge and its tools, loads two LaunchAgents and checks
the path end to end. A first install has no approval token yet, so it
asks:

```text
No t3a token found. Issue one now? [y/N]
```

Answer `y`. The token lets t3-muse relay your answers to T3 Code's
pending approvals and questions. The installer prints the token's scopes,
its 90-day lifetime and where the file is, but never the token itself.

Any other answer leaves the install unfinished. When you're ready, run
`python3 ~/t3-muse-tools/t3a.py issue-token` and then `install` again.

### 2. Enable T3 Connect and copy the MCP URL

In T3 Code, enable T3 Connect in the environment settings. Copy the
environment's MCP URL and give it to Muse.

Copy the URL from a client that is itself connected through T3 Connect,
such as the web app. On the host Mac the URL can be a LAN address, which
Muse cannot reach and which cannot complete sign-in.

### 3. Approve the connection and pick an access mode

Muse registers the URL as a custom connector and starts the sign-in.
Approve it in the T3 Code app and pick an access mode. The mode caps
everything Muse can do through the connector, so pick one that allows
the thread launches you want Muse to make.

## How you know it's done

- `bridge-ping` through the mailbox answers with a pong.
- `t3r health` succeeds.
- `t3a pending` returns an empty list.
- A read through the MCP, such as listing projects, succeeds.

The installer prints `install complete.` once the first three pass, and
Muse runs all four and reports the results.

## Good to know

**Stopping it.** Unloading the bridge's LaunchAgent plists, or deleting
the bridge folder `~/t3-muse-bridge`, stops everything. That is the kill
switch:

```sh
launchctl unload ~/Library/LaunchAgents/com.muse.t3bridge.plist ~/Library/LaunchAgents/com.muse.t3bridge-restarter.plist
```

**Uninstalling.** `npx @mzkmnk-labs/t3-muse uninstall` unloads both
LaunchAgents and deletes their plists, and keeps the data folders.
`npx @mzkmnk-labs/t3-muse uninstall --purge` also deletes
`~/t3-muse-bridge` and `~/t3-muse-tools` once you confirm. Deleting the
token file does not end its T3 session, which stays valid until it
expires. The installer prints how to revoke it.

**Credential lifetimes.**

| Credential | Lifetime | To renew |
|---|---|---|
| MCP connector grant | About 30 days | Approve the connection again |
| t3a approval token | 90 days | Run `t3a renew`, which Muse does through the bridge |
