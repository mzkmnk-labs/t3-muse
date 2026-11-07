#!/usr/bin/env node
'use strict';
// Installs the Mac side of t3-muse: the mailbox bridge daemon and its tools.
// Nothing runs on `npm install`; every change comes from an explicit subcommand.
//
// launchctl and xattr are looked up on PATH, and the home folder comes from
// os.homedir(), which honors $HOME. installer/test_t3_muse.py relies on both.

const { spawnSync } = require('node:child_process');
const crypto = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const readline = require('node:readline');

const USAGE = `usage: t3-muse <command>

commands:
  install            place the bridge and tools, load both LaunchAgents and verify
                     them through the mailbox; safe to run again
  status             report the state of the install; for the t3a token it only
                     says whether the file exists, never what is in it
  uninstall          unload both LaunchAgents and delete their plists; keeps the
                     data folders
  uninstall --purge  the same, and delete ~/t3-muse-bridge and ~/t3-muse-tools
                     once you confirm
`;

const PACKAGE_ROOT = path.resolve(__dirname, '..');
const PYTHON = '/usr/bin/python3';
const QUARANTINE = 'com.apple.quarantine';
const BRIDGE_LABEL = 'com.muse.t3bridge';
const RESTARTER_LABEL = 'com.muse.t3bridge-restarter';
const PURGE_WORD = 'purge';

const POLL_MS = 100;
const COMMAND_TIMEOUT_MS = 30_000;
const JOB_STATE_WAIT_MS = 5_000;
const INBOX_DRAIN_WAIT_MS = 15_000;
const PING_WAIT_MS = 30_000;
const BRIDGE_TIMEOUT_SECONDS = 60; // how long the bridge lets a tool run
const TOOL_WAIT_MS = 75_000;

const PROBES = [
  {
    name: 'bridge-ping',
    argv: ['bridge-ping'],
    waitMs: PING_WAIT_MS,
    accept: (out) => (out && out.pong === true && out.tools_folder_exists === true ? 'pong' : null),
  },
  {
    name: 't3r health',
    argv: ['python3', 't3r.py', 'health'],
    waitMs: TOOL_WAIT_MS,
    accept: (out) => (out && out.exists === true ? 'database readable' : null),
  },
  {
    name: 't3a pending',
    argv: ['python3', 't3a.py', 'pending'],
    waitMs: TOOL_WAIT_MS,
    accept: (out) => (Array.isArray(out) ? `${out.length} pending` : null),
  },
];
const PING = PROBES[0];

const NEXT_STEPS = {
  NO_TOKEN: 'Issue the t3a token once: python3 ~/t3-muse-tools/t3a.py issue-token',
  TOKEN_REJECTED: 'Replace the t3a token: python3 ~/t3-muse-tools/t3a.py renew',
  T3_NOT_RUNNING: 'Start T3 Code.',
};

// An expected failure: its message is all the user needs to see.
class Failure extends Error {}

function pathsFor(home) {
  const bridge = path.join(home, 't3-muse-bridge');
  const tools = path.join(home, 't3-muse-tools');
  const agents = path.join(home, 'Library', 'LaunchAgents');
  const inbox = path.join(bridge, 'inbox');
  const outbox = path.join(bridge, 'outbox');
  const job = (label) => ({
    label,
    template: `bridge/${label}.plist`,
    plist: path.join(agents, `${label}.plist`),
  });
  return {
    home,
    bridge,
    tools,
    inbox,
    outbox,
    token: path.join(tools, '.t3a-token'),
    log: path.join(bridge, 'bridge.log'),
    folders: [inbox, outbox, path.join(bridge, 'done'), path.join(bridge, 'restart-trigger'), tools, agents],
    files: [
      { source: 'bridge/bridge.py', target: path.join(bridge, 'bridge.py'), mode: 0o644 },
      { source: 'bridge/restart-bridge.sh', target: path.join(bridge, 'restart-bridge.sh'), mode: 0o755 },
      { source: 'tools/t3r.py', target: path.join(tools, 't3r.py'), mode: 0o644 },
      { source: 'tools/t3w.py', target: path.join(tools, 't3w.py'), mode: 0o644 },
      { source: 'tools/t3a.py', target: path.join(tools, 't3a.py'), mode: 0o644 },
    ],
    // In load order. Unloading goes the other way, so the restarter cannot
    // kick the bridge while the bridge is on its way down.
    jobs: [job(BRIDGE_LABEL), job(RESTARTER_LABEL)],
  };
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function waitUntil(condition, budgetMs) {
  const deadline = Date.now() + budgetMs;
  for (;;) {
    if (condition()) return true;
    if (Date.now() >= deadline) return false;
    await sleep(POLL_MS);
  }
}

function run(program, args) {
  const result = spawnSync(program, args, {
    encoding: 'utf8',
    stdio: ['ignore', 'pipe', 'pipe'],
    timeout: COMMAND_TIMEOUT_MS,
  });
  if (result.error) throw new Failure(`could not run ${program}: ${result.error.message}`);
  return result;
}

function parseJson(text) {
  try {
    return JSON.parse(text);
  } catch {
    return undefined;
  }
}

function exists(file) {
  try {
    fs.lstatSync(file);
    return true;
  } catch {
    return false;
  }
}

// --- preflight -----------------------------------------------------------

function preflight() {
  if (process.platform !== 'darwin') {
    throw new Failure(`t3-muse only runs on macOS, and this is ${process.platform}.`);
  }
  // As root, launchctl looks at a different domain than the one these jobs live
  // in, so nothing it reported or changed would be about them.
  if (process.getuid() === 0) {
    throw new Failure('LaunchAgents belong to your own user. Run t3-muse without sudo.');
  }
}

function requirePython() {
  // On a Mac without the Command Line Tools this path is a stub that exists
  // but cannot run anything, so running it is the only real check.
  const result = spawnSync(PYTHON, ['--version'], { stdio: 'ignore', timeout: COMMAND_TIMEOUT_MS });
  if (result.error || result.status !== 0) {
    throw new Failure(
      `${PYTHON} is missing or does not run, and the bridge needs it. ` +
        'Install the Command Line Tools with xcode-select --install, then try again.',
    );
  }
}

function queuedRequests(paths) {
  try {
    return fs.readdirSync(paths.inbox).filter((name) => name.endsWith('.json'));
  } catch {
    return [];
  }
}

// A running request stays in the inbox until it finishes, and restarting the
// bridge makes it run again from the start. A t3w send must not happen twice.
async function requireIdleInbox(paths) {
  const running = isLoaded(BRIDGE_LABEL);
  if (running) await waitUntil(() => queuedRequests(paths).length === 0, INBOX_DRAIN_WAIT_MS);
  const queued = queuedRequests(paths).length;
  if (queued === 0) return;
  throw new Failure(
    running
      ? `${queued} request(s) are still in ${paths.inbox}, and restarting the bridge now would run them twice. ` +
          'Nothing was changed. Wait for them to finish and run install again.'
      : `${queued} request(s) are waiting in ${paths.inbox} and would run as soon as the bridge starts. ` +
          'Nothing was changed. Review or remove them and run install again.',
  );
}

// --- placing files -------------------------------------------------------

// A rename never leaves a half-written file behind, and a new file carries no
// attributes over from whatever it replaces.
function writeAtomically(target, data, mode) {
  const draft = `${target}.${process.pid}.${crypto.randomBytes(4).toString('hex')}.tmp`;
  try {
    fs.writeFileSync(draft, data, { mode, flag: 'wx' });
    fs.chmodSync(draft, mode);
    fs.renameSync(draft, target);
  } catch (error) {
    fs.rmSync(draft, { force: true });
    throw error;
  }
}

function escapeXml(text) {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;');
}

function renderPlist(template, home) {
  // split/join, because a replace() string would treat $& in a path as a pattern.
  return template.split('__HOME__').join(escapeXml(home));
}

function clearQuarantine(file) {
  // Listing first keeps a missing attribute, the normal case, from reading as
  // an error, without having to recognize xattr's message for it.
  const listed = run('xattr', [file]);
  if (listed.status !== 0) {
    throw new Failure(`xattr could not read ${file}: ${listed.stderr.trim()}`);
  }
  if (!listed.stdout.split('\n').includes(QUARANTINE)) return;
  const removed = run('xattr', ['-d', QUARANTINE, file]);
  if (removed.status !== 0) {
    throw new Failure(`launchd refuses a quarantined plist, and xattr could not clear ${file}: ${removed.stderr.trim()}`);
  }
}

// --- launchd -------------------------------------------------------------

// launchctl load and unload can exit 0 after failing, printing something like
// "Load failed: 5: Input/output error". Only launchctl list is taken as fact.
function isLoaded(label) {
  return run('launchctl', ['list', label]).status === 0;
}

async function unloadJob(job) {
  if (!isLoaded(job.label)) return false;
  const gone = () => !isLoaded(job.label);
  let output = '';
  if (exists(job.plist)) {
    output = run('launchctl', ['unload', job.plist]).stderr;
    if (await waitUntil(gone, JOB_STATE_WAIT_MS)) return true;
  }
  // Loaded with its plist deleted or damaged, which unload cannot act on.
  output += run('launchctl', ['remove', job.label]).stderr;
  if (await waitUntil(gone, JOB_STATE_WAIT_MS)) return true;
  throw new Failure(`could not unload ${job.label}: ${output.trim() || 'it is still loaded'}`);
}

async function loadJob(job) {
  const result = run('launchctl', ['load', job.plist]);
  if (await waitUntil(() => isLoaded(job.label), JOB_STATE_WAIT_MS)) return;
  throw new Failure(`could not load ${job.plist}: ${result.stderr.trim() || 'launchctl does not list it'}`);
}

// --- mailbox -------------------------------------------------------------

// Returns the bridge's result, or null when it did not answer in time.
async function askBridge(paths, argv, waitMs) {
  const id = `t3-muse-${Date.now()}-${crypto.randomBytes(4).toString('hex')}`;
  const request = path.join(paths.inbox, `${id}.json`);
  const answer = path.join(paths.outbox, `${id}.json`);
  // The bridge takes any *.json on its next poll, so that name appears only
  // once the content is complete.
  const draft = path.join(paths.inbox, `.${id}.tmp`);
  fs.writeFileSync(draft, JSON.stringify({ id, argv, timeout: BRIDGE_TIMEOUT_SECONDS }), { flag: 'wx' });
  fs.renameSync(draft, request);
  if (await waitUntil(() => exists(answer), waitMs)) {
    return JSON.parse(fs.readFileSync(answer, 'utf8'));
  }
  // Left there, it would run later with nobody waiting, and block the next install.
  fs.rmSync(request, { force: true });
  return null;
}

// The tools report through the error key: t3a as JSON on stderr, t3r on stdout.
function toolFailure(result) {
  const report = [result.stderr, result.stdout].map(parseJson).find((parsed) => parsed && parsed.error);
  if (!report) return { message: `exited ${result.exit_code}` };
  return report.message
    ? { code: report.error, message: `${report.error}: ${report.message}` }
    : { message: String(report.error) };
}

async function runProbe(paths, probe) {
  const result = await askBridge(paths, probe.argv, probe.waitMs);
  if (result === null) {
    return { ok: false, message: `no answer in ${probe.waitMs / 1000}s; look in ${paths.log}` };
  }
  // The bridge sets ok whenever the program ran, so a tool that failed still
  // has ok: true, and only exit_code tells the two apart.
  if (result.ok !== true) return { ok: false, message: String(result.error) };
  if (result.exit_code !== 0) return { ok: false, ...toolFailure(result) };
  const detail = probe.accept(parseJson(result.stdout));
  return detail ? { ok: true, detail } : { ok: false, message: 'unexpected output' };
}

// --- subcommands ---------------------------------------------------------

async function install(paths) {
  preflight();
  requirePython();
  // Read everything first, so a damaged package fails before anything changes.
  const files = paths.files.map((file) => ({
    ...file,
    data: fs.readFileSync(path.join(PACKAGE_ROOT, file.source)),
  }));
  const plists = paths.jobs.map((job) => ({
    ...job,
    data: renderPlist(fs.readFileSync(path.join(PACKAGE_ROOT, job.template), 'utf8'), paths.home),
  }));
  await requireIdleInbox(paths);

  for (const folder of paths.folders) fs.mkdirSync(folder, { recursive: true });
  for (const file of files) writeAtomically(file.target, file.data, file.mode);
  console.log(`placed the bridge in ${paths.bridge} and the tools in ${paths.tools}`);
  for (const job of plists) {
    writeAtomically(job.plist, job.data, 0o644);
    clearQuarantine(job.plist);
  }
  console.log(`placed the LaunchAgents in ${path.dirname(plists[0].plist)}`);

  for (const job of [...paths.jobs].reverse()) await unloadJob(job);
  for (const job of paths.jobs) await loadJob(job);
  console.log(`loaded ${paths.jobs.map((job) => job.label).join(' and ')}`);

  for (const probe of PROBES) {
    const outcome = await runProbe(paths, probe);
    if (!outcome.ok) {
      console.error(`${probe.name}: FAILED: ${outcome.message}`);
      console.error(`\ninstall is not complete: ${probe.name} did not pass.`);
      if (NEXT_STEPS[outcome.code]) console.error(NEXT_STEPS[outcome.code]);
      console.error('The files and LaunchAgents stay in place. Fix this and run install again.');
      return 1;
    }
    console.log(`${probe.name}: ok (${outcome.detail})`);
  }
  console.log('\ninstall complete.');
  return 0;
}

async function status(paths) {
  preflight();
  let healthy = true;
  const report = (name, ok, text) => {
    healthy = healthy && ok;
    console.log(`${name.padEnd(29)} ${text}`);
  };

  for (const job of paths.jobs) {
    const loaded = isLoaded(job.label);
    report(job.label, loaded, loaded ? 'loaded' : 'not loaded');
  }
  // The ping is the one file status writes. With no daemon to answer it, it
  // would only sit in the inbox, so it is not sent.
  if (isLoaded(BRIDGE_LABEL) && exists(paths.inbox)) {
    const outcome = await runProbe(paths, PING);
    report(PING.name, outcome.ok, outcome.ok ? outcome.detail : outcome.message);
  } else {
    report(PING.name, false, 'skipped: the bridge is not running');
  }
  for (const file of paths.files) {
    const present = exists(file.target);
    report(path.basename(file.target), present, present ? 'present' : 'missing');
  }
  const hasToken = exists(paths.token);
  report('t3a token file', hasToken, hasToken ? 'present' : 'missing');
  return healthy ? 0 : 1;
}

// Resolves to '' at end of input, where readline would otherwise never answer.
function ask(question) {
  return new Promise((resolve) => {
    const prompt = readline.createInterface({ input: process.stdin, output: process.stdout });
    let answered = false;
    prompt.question(question, (answer) => {
      answered = true;
      prompt.close();
      resolve(answer);
    });
    prompt.once('close', () => {
      if (!answered) resolve('');
    });
  });
}

async function confirmPurge(paths) {
  console.log('This permanently deletes:');
  console.log(`  ${paths.bridge}  (the mailbox, its audit history in done/ and outbox/, the logs)`);
  console.log(`  ${paths.tools}  (the tools and the t3a token file)`);
  const answer = await ask(`Type "${PURGE_WORD}" to confirm: `);
  return answer.trim() === PURGE_WORD;
}

async function uninstall(paths, purge) {
  preflight();
  // Asked first, so that answering no leaves everything exactly as it was.
  if (purge && !(await confirmPurge(paths))) {
    console.error('Not confirmed. Nothing was changed.');
    return 1;
  }
  for (const job of [...paths.jobs].reverse()) {
    const unloaded = await unloadJob(job);
    const hadPlist = exists(job.plist);
    fs.rmSync(job.plist, { force: true });
    console.log(`${job.label}: ${unloaded || hadPlist ? 'removed' : 'was not installed'}`);
  }
  if (!purge) {
    console.log(`Kept ${paths.bridge} and ${paths.tools}. To delete them too: t3-muse uninstall --purge`);
    return 0;
  }
  const hadToken = exists(paths.token);
  fs.rmSync(paths.bridge, { recursive: true, force: true });
  fs.rmSync(paths.tools, { recursive: true, force: true });
  console.log(`Deleted ${paths.bridge} and ${paths.tools}.`);
  if (hadToken) {
    console.log(
      'The T3 session behind the deleted token stays valid until it expires. To revoke it: ' +
        '~/.t3/bin/t3 auth session list, then ~/.t3/bin/t3 auth session revoke <session-id>',
    );
  }
  return 0;
}

async function main(args) {
  const paths = pathsFor(path.resolve(os.homedir()));
  const command = args.join(' ');
  if (command === 'install') return install(paths);
  if (command === 'status') return status(paths);
  if (command === 'uninstall') return uninstall(paths, false);
  if (command === 'uninstall --purge') return uninstall(paths, true);
  if (command === '--help' || command === '-h') {
    process.stdout.write(USAGE);
    return 0;
  }
  process.stderr.write(USAGE);
  return 2;
}

main(process.argv.slice(2)).then(
  (code) => {
    process.exitCode = code;
  },
  (error) => {
    console.error(`t3-muse: ${error.message}`);
    process.exitCode = 1;
  },
);
