# Setting up t3-muse

English | [日本語](setup.ja.md)

## Prerequisites

Confirm these with the user before you start:

- T3 Code is running on the user's Mac.
- The user has a Muse account.
- Node.js 24 or later is installed on the Mac.

## Steps

### 1. Place the starter skill

Fetch the repository https://github.com/mzkmnk-labs/t3-muse and place its
`muse/skills/t3-muse-starter/` folder (`SKILL.md` and `skill.yaml`) in the
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
  approvals and questions, and that its value is never printed. This
  prompt is the normal way the token is issued, and the answer is the
  user's, given in their own terminal. You cannot see that terminal, so
  ask them whether the prompt appeared and what they answered.
  - **They answered `y`.** The installer issues the token and checks
    `t3a pending` again.
  - **They answered anything else.** That is a no, and the install stops
    at `t3a pending` with `NO_TOKEN`. Do not issue the token yourself
    then. If the setup needs it, explain again why and ask once more.
  - **The prompt never appeared**, as when the installer ran without a
    terminal, and the install stopped at `t3a pending` with `NO_TOKEN`.
    Ask the user in chat whether to issue the token.

  In the last two cases, go on only once the user says yes in chat. Then
  either they run `python3 ~/t3-muse-tools/t3a.py issue-token` in the
  terminal and run `install` again, or you run `t3a issue-token` through
  the bridge and check `t3a pending`, as T3 Muse Starter describes.
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
answers to T3 Code's questions yourself. The bridge route for the token
is the one step you may run, and only on the user's yes in chat.

## Done when

- `bridge-ping` through the mailbox answers with a pong.
- `t3r health` succeeds.
- `t3a pending` returns an empty list.
- A read through the MCP, such as listing projects, succeeds.
- The T3 Muse PM and T3 Muse Bridge skills are in the user's custom
  skills folder, at `~/workspace/custom-skills/t3-muse-pm/` and
  `~/workspace/custom-skills/t3-muse-bridge/`.

Report the results to the user.

## Also tell the user

**Stopping it.** t3-muse reaches T3 Code by two paths, and each is
stopped on its own. Stopping one leaves the other working.

- **The bridge.** Unloading both LaunchAgents stops the bridge daemon and
  its restarter. That is the bridge's kill switch:

  ```sh
  launchctl unload ~/Library/LaunchAgents/com.muse.t3bridge.plist ~/Library/LaunchAgents/com.muse.t3bridge-restarter.plist
  ```

  The plists stay in `~/Library/LaunchAgents`, so launchd loads them
  again at the next login. To keep the bridge stopped, uninstall it as
  below. Deleting the bridge folder `~/t3-muse-bridge` alone does not
  stop it: the running daemon keeps watching its inbox, and runs any
  request written there once the folder exists again. Unload first, then
  delete the folder if it is no longer wanted.
- **The MCP connector.** Its grant lives in T3 Code, and nothing on the
  bridge side touches it. The user stops it in the T3 Code settings by
  revoking the connected client, or by turning T3 Connect off, which
  also cuts their other clients that come in through it, such as the web
  app. That is done in T3 Code itself, not through anything in t3-muse.

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
