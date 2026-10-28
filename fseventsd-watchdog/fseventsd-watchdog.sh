#!/bin/sh
# Restarts fseventsd when it logs "event table has grown too large", the
# warning that comes before its permanent memory leak. README.md explains
# why. Runs as root under launchd, which sends stderr to
# /var/log/fseventsd-watchdog.log and restarts the script whenever it
# exits with a non-zero status.

FSEVENTSD=/System/Library/Frameworks/CoreServices.framework/Versions/A/Frameworks/FSEvents.framework/Versions/A/Support/fseventsd
WARNING="event table has grown too large"
PREDICATE="processImagePath == \"$FSEVENTSD\" AND eventMessage BEGINSWITH \"$WARNING\""

if [ ! -x "$FSEVENTSD" ]; then
	printf '%s stopping: %s does not exist, so this watchdog cannot work\n' "$(date '+%F %T')" "$FSEVENTSD" >&2
	exit 0
fi

# Restarts fseventsd if the line is the warning from the fseventsd that
# launchd is running now. logd writes the line's header, so the pid in it
# is the sender's. A warning from any other process, including an earlier
# fseventsd, is only noted, so once launchd has started a new fseventsd,
# seeing the same warning again does nothing.
handle() {
	pid=$(printf '%s\n' "$1" | sed -nE "s/^[^ ]+ [^ ]+ [^ ]+ +fseventsd\\[([0-9]+):[^ ]+ .*$WARNING.*/\\1/p")
	[ -n "$pid" ] || return
	running=$(launchctl print system/com.apple.fseventsd | sed -nE 's/^[[:blank:]]pid = ([0-9]+)$/\1/p')
	if [ "$pid" = "$running" ]; then
		printf '%s restarting fseventsd (pid %s): %s\n' "$(date '+%F %T')" "$pid" "$1" >&2
		kill -TERM "$pid"
	else
		printf '%s ignoring a warning from pid %s; the running fseventsd is pid %s: %s\n' "$(date '+%F %T')" "$pid" "$running" "$1" >&2
	fi
}

# Handles warnings in the stored log that the stream did not deliver.
# Takes the argument for log show's --last.
catch_up() {
	log show --style compact --last "$1" --predicate "$PREDICATE" | while IFS= read -r line; do
		handle "$line"
	done
}

# The search back to boot runs alongside the stream. The stream connects
# within milliseconds, while the search takes seconds, so a warning logged
# before the stream connects is normally still found by the search.
log stream --style compact --predicate "$PREDICATE" | {
	catch_up boot
	while IFS= read -r line; do
		case "$line" in
			"=== Messages dropped"*) catch_up 10m ;;
			*) handle "$line" ;;
		esac
	done
}
printf '%s log stream ended\n' "$(date '+%F %T')" >&2
exit 1
