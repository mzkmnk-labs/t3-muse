#!/bin/sh
# Restarts the bridge LaunchAgent. launchd runs this (job
# com.muse.t3bridge-restarter) when anything lands in restart-trigger. It takes
# no arguments and never reads the trigger files, so a trigger can only ever
# cause one restart of that one job.

BASE="$HOME/t3-muse-bridge"
TRIGGER_DIR="$BASE/restart-trigger"

# launchd can re-run this job about 10s later with the folder already empty. Do
# nothing then, or one trigger would restart the bridge twice.
[ -n "$(ls -A "$TRIGGER_DIR")" ] || exit 0

# launchd keeps re-running this job while the folder is non-empty, so clear every
# entry (.DS_Store and subfolders too), whatever the kickstart does. Clearing
# first means a trigger dropped during the kickstart gets a run of its own.
cd "$TRIGGER_DIR" && rm -rf ./* ./.[!.]* ./..?*

launchctl kickstart -k "gui/$(id -u)/com.muse.t3bridge"
status=$?

printf '%s kickstart exit=%s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$status" >> "$BASE/restart.log"
exit "$status"
