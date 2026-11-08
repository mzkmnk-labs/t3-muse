# Setting up t3-muse

English | [日本語](setup.ja.md)

This file is a setup instruction for Muse to read. To set up t3-muse,
give this file to your Muse and ask, for example, "Read setup.md and set
up t3-muse." Muse runs the setup from there and tells you in chat
whenever it needs you.

The rest of this file is addressed to Muse.

## Prerequisites

Confirm these with the user before you start:

- T3 Code is running on the user's Mac.
- The user has a Muse account.
- Node.js 24 or later is installed on the Mac.

## Steps

### 1. Place the starter skill

Fetch the repository https://github.com/mzkmnk-labs/t3-muse and place its
`skills/t3-muse-starter/` folder (`SKILL.md` and `skill.yaml`) in the
user's custom skills folder at `~/workspace/custom-skills/t3-muse-starter/`.

### 2. Follow the skill

Run the setup by following the skill you placed, T3 Muse Starter.

### 3. Hand the user the steps only they can take

At each of these steps, stop and tell the user in chat exactly what to
do, then wait for them.

- **Run the installer.** Ask the user to run
  `npx @mzkmnk-labs/t3-muse install` in a terminal on the Mac. It places
  the bridge and its tools, loads two LaunchAgents, and checks
  `bridge-ping`, `t3r health` and `t3a pending` through the mailbox.
- **Answer the token prompt.** A first install asks
  `No t3a token found. Issue one now? [y/N]`. Tell the user that `y`
  issues the t3a token, which relays their answers to T3 Code's pending
  approvals and questions, and that its value is never printed. The answer
  is theirs. Any other answer leaves the install unfinished, so once they
  run `python3 ~/t3-muse-tools/t3a.py issue-token`, have them run
  `install` again.
- **Enable T3 Connect and pass you the MCP URL.** Ask the user to enable
  T3 Connect in the T3 Code environment settings, then copy the
  environment's MCP URL and give it to you. They must copy it from a
  client that is itself connected through T3 Connect, such as the web
  app. Warn them that a URL copied on the host Mac can be a LAN address,
  which you cannot reach and which cannot complete sign-in.
- **Approve the connection and pick an access mode.** Once you have
  registered the URL as a custom connector and started the sign-in, ask
  the user to approve it in the T3 Code app and pick an access mode. Tell
  them the mode caps everything you can do through the connector, so it
  has to allow the thread launches the setup will run.

Never take these steps for the user, and never decide approvals or
answers to T3 Code's questions yourself.

## Done when

- `bridge-ping` through the mailbox answers with a pong.
- `t3r health` succeeds.
- `t3a pending` returns an empty list.
- A read through the MCP, such as listing projects, succeeds.

Report the results to the user.

## Also tell the user

**Stopping it.** Unloading the bridge's LaunchAgent plists, or deleting
the bridge folder `~/t3-muse-bridge`, stops everything. That is the kill
switch:

```sh
launchctl unload ~/Library/LaunchAgents/com.muse.t3bridge.plist ~/Library/LaunchAgents/com.muse.t3bridge-restarter.plist
```

**Uninstalling.** `npx @mzkmnk-labs/t3-muse uninstall` unloads both
LaunchAgents and deletes their plists, and keeps the data folders.
`npx @mzkmnk-labs/t3-muse uninstall --purge` also deletes
`~/t3-muse-bridge` and `~/t3-muse-tools` once the user confirms. Deleting
the token file does not end its T3 session, which stays valid until it
expires. The installer prints how to revoke it.

**Credential lifetimes.**

| Credential | Lifetime | To renew |
|---|---|---|
| MCP connector grant | About 30 days | Have the user approve the connection again |
| t3a approval token | 90 days | Run `t3a renew` through the bridge |
