#!/bin/bash
# Carry the Claude desktop app's session list across an account switch.
#
# The app keeps the list it shows in the sidebar -- one file per session, with
# the title, working directory, model, effort, permission mode and so on -- in a
# directory named after the account and organization it is logged in as:
#
#   ~/Library/Application Support/Claude/claude-code-sessions/<account>/<org>/
#
# Logging in as a different account makes the app compute a different directory
# name, find nothing there, and show an empty sidebar.  The transcripts are not
# account-scoped -- they are in ~/.claude/projects/<directory>/<session>.jsonl
# and are untouched by a switch -- so nothing is lost; only the index is
# missing.  This script points the new account's directory at the old one, so
# both accounts read one shared list and switching back needs no further work.
#
#   ./account-switch.sh status   # which account is recorded, which is live
#   ./account-switch.sh record   # while still logged in as the old account
#   ./account-switch.sh link     # after logging in as the new account
#
# The account this script acts on comes from the app's own config.json, in the
# key "lastKnownAccountUuid", together with the <account>/<org> directory the
# app creates when it logs in.  The "oauthAccount" block in ~/.claude.json is a
# cached profile snapshot, updated only when a surface fetches the profile, and
# it can name an account the app is no longer logged in as.  This script reads
# it only for an email address to print.
#
# Layout: local-agent-mode-sessions holds account directories alongside a
# "skills-plugin" directory, and the nesting under skills-plugin is
# <org>/<account>, the reverse of the session directories.  The paths below are
# built from an account and an organization directly.  skills-plugin holds a
# cache the app downloads again, and this script leaves it alone.
#
# README.md alongside this script covers the layout and the procedure in full.

set -u

SUPPORT="$HOME/Library/Application Support/Claude"
STATE="$HOME/.claude-account-switch-state"
DIRS=(claude-code-sessions local-agent-mode-sessions)

# The account and organization the app is logged in as right now, printed as
# "<account> <org>".  Exits with a message on standard error when either cannot
# be determined.
live_pair() {
  SUPPORT="$SUPPORT" python3 -c '
import json, os, re, sys

support = os.environ["SUPPORT"]
uuid = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

try:
    config = json.load(open(os.path.join(support, "config.json")))
except OSError as e:
    sys.exit("cannot read the app config: %s" % e)

account = config.get("lastKnownAccountUuid")
if not account:
    sys.exit("the app config has no lastKnownAccountUuid; is the app logged in?")

# The organization is the subdirectory the app made under the account when it
# logged in.  Normally there is exactly one; take the newest if there are more.
parent = os.path.join(support, "claude-code-sessions", account)
try:
    orgs = [d for d in os.listdir(parent) if uuid.match(d)]
except OSError:
    orgs = []
if not orgs:
    sys.exit("no organization directory under %s; open the app once first" % parent)
orgs.sort(key=lambda d: os.stat(os.path.join(parent, d)).st_mtime, reverse=True)

print(account, orgs[0])
'
}

# The email ~/.claude.json has cached for an account, or "unknown" if its
# snapshot is about some other account.
cached_email() {
  ACCOUNT="$1" python3 -c '
import json, os
try:
    a = json.load(open(os.path.expanduser("~/.claude.json")))["oauthAccount"]
except (OSError, KeyError, ValueError):
    print("unknown")
else:
    print(a.get("emailAddress", "unknown")
          if a.get("accountUuid") == os.environ["ACCOUNT"] else "unknown")
'
}

# One line per session directory describing what is at <account>/<org>.
describe() {
  local acct="$1" org="$2" d p
  for d in "${DIRS[@]}"; do
    p="$SUPPORT/$d/$acct/$org"
    if [ -L "$p" ]; then
      printf '  %s: symlink to %s\n' "$d" "$(readlink "$p")"
    elif [ -d "$p" ]; then
      printf '  %s: %s entries, %s\n' "$d" \
        "$(find "$p" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')" \
        "$(du -sh "$p" | cut -f1)"
    else
      printf '  %s: not present\n' "$d"
    fi
  done
}

# Whether the desktop app is running.  This matches the app's executable path
# in a captured process listing.  "pgrep -f" does not reliably match that path,
# and has reported the app as absent while it was running.
app_is_running() {
  local listing
  listing="$(ps -Ao args=)" || return 1
  case "$listing" in
    */Applications/Claude.app/Contents/MacOS/Claude*) return 0 ;;
  esac
  return 1
}

case "${1-}" in
  status)
    if [ -f "$STATE" ]; then
      read -r old_acct old_org old_email < "$STATE"
      echo "Recorded account: $old_email"
      echo "  account uuid: $old_acct"
      echo "  org uuid:     $old_org"
      describe "$old_acct" "$old_org"
    else
      echo "Recorded account: none; run 'record' before switching."
    fi
    echo
    if pair="$(live_pair)"; then
      read -r acct org <<< "$pair"
      echo "Live account (from the app's own config): $(cached_email "$acct")"
      echo "  account uuid: $acct"
      echo "  org uuid:     $org"
      describe "$acct" "$org"
    else
      echo "Live account: could not be determined." >&2
      exit 1
    fi
    ;;

  record)
    pair="$(live_pair)" || exit 1
    read -r acct org <<< "$pair"
    email="$(cached_email "$acct")"
    printf '%s %s %s\n' "$acct" "$org" "$email" > "$STATE"
    echo "Recorded old account: $email"
    echo "  account uuid: $acct"
    echo "  org uuid:     $org"
    describe "$acct" "$org"
    ;;

  link)
    [ -f "$STATE" ] || { echo "No recorded old account; run 'record' first." >&2; exit 1; }
    if app_is_running; then
      echo "The Claude app is running.  Quit it completely first: it rewrites" >&2
      echo "these files as it goes, and this step moves some of them." >&2
      exit 1
    fi
    read -r old_acct old_org old_email < "$STATE"
    pair="$(live_pair)" || exit 1
    read -r acct org <<< "$pair"
    if [ "$acct" = "$old_acct" ] && [ "$org" = "$old_org" ]; then
      echo "The app is still logged in as the recorded account ($old_email)."
      echo "Log in as the other account first, open the app once so it creates"
      echo "its directory, then quit the app and rerun this."
      exit 0
    fi
    echo "Old account: $old_email ($old_acct)"
    echo "New account: $(cached_email "$acct") ($acct)"
    ready=0
    for d in "${DIRS[@]}"; do
      tgt="$SUPPORT/$d/$old_acct/$old_org"
      src="$SUPPORT/$d/$acct/$org"
      if [ ! -d "$tgt" ]; then
        echo "  $d: old directory missing, skipping"
        continue
      fi
      if [ -L "$src" ]; then
        echo "  $d: already a symlink to $(readlink "$src")"
        ready=$((ready + 1))
        continue
      fi
      # Both paths reaching the same directory means an earlier run linked this
      # pair of accounts the other way round.  Every entry below would then be
      # moved onto itself and set aside as a duplicate, emptying the directory
      # that holds the sessions.
      if [ -d "$src" ] &&
         [ "$(cd "$src" && pwd -P)" = "$(cd "$tgt" && pwd -P)" ]; then
        echo "  $d: the recorded and the live directory are one directory;" >&2
        echo "      these two accounts are already sharing a session list." >&2
        continue
      fi
      # By the time this runs, the new account normally has a session or two of
      # its own here.  Each entry moves into the shared directory.  An entry
      # whose name is already taken there is set aside instead, which keeps the
      # copy the old account built up.  The directory it is set aside in is
      # made fresh for each run, so a second switch between the same two
      # accounts does not write over what the first one set aside.
      if [ -d "$src" ]; then
        moved=0 kept=0 aside=""
        for entry in "$src"/* "$src"/.[!.]*; do
          [ -e "$entry" ] || continue
          name="$(basename "$entry")"
          if [ -e "$tgt/$name" ]; then
            if [ -z "$aside" ]; then
              aside="$(mktemp -d "$src.superseded.XXXXXX")" || exit 1
            fi
            mv "$entry" "$aside/$name"
            kept=$((kept + 1))
          else
            mv "$entry" "$tgt/$name"
            moved=$((moved + 1))
          fi
        done
        [ "$moved" -gt 0 ] && echo "  $d: moved $moved entries into the shared directory"
        [ "$kept" -gt 0 ] && echo "  $d: set $kept already-present entries aside in $aside"
        if ! rmdir "$src" 2>/dev/null; then
          echo "  $d: $src is not empty, refusing to replace it" >&2
          continue
        fi
      fi
      mkdir -p "$(dirname "$src")"
      ln -s "$tgt" "$src"
      echo "  $d: linked"
      ready=$((ready + 1))
    done
    if [ "$ready" -eq 0 ]; then
      echo "No session list was linked; the session list has not moved." >&2
      exit 1
    fi
    echo "Now open the Claude app again."
    ;;

  *)
    echo "usage: $0 status|record|link" >&2
    exit 1
    ;;
esac
