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
# missing.  This script moves the list into the directory of the account the
# app is logged in as, after each switch.
#
# The app opens that directory with O_NOFOLLOW, so it cannot be a symbolic link
# to another account's directory.  An earlier version of this script made one;
# the app then read the list through the link but failed every save with
# ENOTDIR, and sessions started under the new account were lost when the app
# quit.  "move" removes such links, and "recover", run after "move", rebuilds
# the lost sessions from the app's log and the transcripts.
#
#   ./account-switch.sh status    # which account is recorded, which is live
#   ./account-switch.sh record    # once, logged in as the account with the list
#   ./account-switch.sh move      # after each switch to another account
#   ./account-switch.sh groups    # each account's sidebar groups
#   ./account-switch.sh recover [--apply|--verify]
#
# Sidebar groups are kept per account by the app and on claude.ai, not in the
# session directory, so "move" does not carry them; it lists the sessions the
# previous account had filed in groups, so they can be filed again in the app.
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
# logged in, and the app writes to it at every login.  While it switches
# accounts, the app can also make a directory pairing the account it is leaving
# with the organization it is going to, so take the newest.  A symbolic link
# counts by what it leads to, if anything.
def modified(path):
    try:
        return os.stat(path).st_mtime
    except OSError:
        return os.lstat(path).st_mtime

parent = os.path.join(support, "claude-code-sessions", account)
try:
    orgs = [d for d in os.listdir(parent) if uuid.match(d)]
except OSError:
    orgs = []
if not orgs:
    sys.exit("no organization directory under %s; open the app once first" % parent)
orgs.sort(key=lambda d: modified(os.path.join(parent, d)), reverse=True)

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
      printf '  %s: symlink to %s; the app cannot save through it\n' \
        "$d" "$(readlink "$p")"
    elif [ -d "$p" ]; then
      printf '  %s: %s entries, %s\n' "$d" \
        "$(find "$p" -mindepth 1 -maxdepth 1 | wc -l | tr -d ' ')" \
        "$(du -sh "$p" | cut -f1)"
    else
      printf '  %s: not present\n' "$d"
    fi
  done
}

# Every symbolic link at an account's directory in tree $1, and where it
# leads, one per line.  An earlier version of this script made them.
links() {
  local link
  for link in "$SUPPORT/$1"/*/*; do
    [ -L "$link" ] && printf '%s\t%s\n' "$link" "$(cd "$link" 2>/dev/null && pwd -P)"
  done
  return 0
}

# Move each entry of directory $1 into directory $2, merging directories that
# both have.  An entry whose name is already taken is set aside instead, at
# the same relative path ($3) under a directory made from the template in
# $aside_template on first use and named in $aside.  Counts go in $moved and
# the set-aside paths in $kept.  Returns non-zero if anything fails to move.
merge_into() {
  local src="$1" dst="$2" rel="$3" entry name
  for entry in "$src"/* "$src"/.[!.]*; do
    [ -e "$entry" ] || [ -L "$entry" ] || continue
    name="${entry##*/}"
    if [ -d "$entry" ] && [ ! -L "$entry" ] && [ -d "$dst/$name" ] && [ ! -L "$dst/$name" ]; then
      merge_into "$entry" "$dst/$name" "$rel$name/" && rmdir "$entry" || return 1
    elif [ -e "$dst/$name" ] || [ -L "$dst/$name" ]; then
      if [ -z "$aside" ]; then
        aside="$(mktemp -d "$aside_template")" || return 1
      fi
      mkdir -p "$aside/$rel" && mv "$entry" "$aside/$rel$name" || return 1
      kept="$kept $rel$name"
    else
      mv "$entry" "$dst/$name" || return 1
      moved=$((moved + 1))
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
    for d in "${DIRS[@]}"; do
      links "$d" | while IFS=$'\t' read -r link target; do
        echo
        echo "Symbolic link at $link"
        echo "  leads to ${target:-nothing}; the app cannot save through it."
        echo "  Run 'move' with the app quit to remove it."
      done
    done
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

  move)
    [ -f "$STATE" ] || { echo "No recorded account; run 'record' first." >&2; exit 1; }
    if app_is_running; then
      echo "The Claude app is running.  Quit it completely first: it rewrites" >&2
      echo "these files as it goes, and this step moves them." >&2
      exit 1
    fi
    read -r old_acct old_org old_email < "$STATE"
    pair="$(live_pair)" || exit 1
    read -r acct org <<< "$pair"
    email="$(cached_email "$acct")"
    echo "From: $old_email ($old_acct)"
    echo "To:   $email ($acct)"
    # The account the list comes from, for listing its sidebar groups: the
    # recorded one, unless the recorded directory is a link to another.
    holding=0 moves=0 failed=0 from="$old_acct/$old_org"
    # A symbolic link at the signed-in account's directory that leads anywhere
    # but the list cannot be replaced.  Every tree is checked before any is
    # changed, so that a switch is not left half done.
    for d in "${DIRS[@]}"; do
      to="$SUPPORT/$d/$acct/$org"
      list="$(cd "$SUPPORT/$d/$old_acct/$old_org" 2>/dev/null && pwd -P)"
      if [ -L "$to" ] && { [ -z "$list" ] || [ "$(cd "$to" 2>/dev/null && pwd -P)" != "$list" ]; }; then
        echo "  $d: $to is a symlink that does not lead to the list" >&2
        failed=1
      fi
    done
    if [ "$failed" -ne 0 ]; then
      echo "Nothing was moved.  Remove the link, then rerun this." >&2
      exit 1
    fi
    for d in "${DIRS[@]}"; do
      to="$SUPPORT/$d/$acct/$org"
      # The list is wherever the recorded directory leads.
      list="$(cd "$SUPPORT/$d/$old_acct/$old_org" 2>/dev/null && pwd -P)"
      if [ "$d" = claude-code-sessions ] && [ -n "$list" ]; then
        from="$(basename "$(dirname "$list")")/$(basename "$list")"
      fi
      # A symbolic link to the list, made by an earlier version of this
      # script, holds no data, and the app cannot save through it.
      while IFS=$'\t' read -r link target; do
        if [ -n "$list" ] && [ "$target" = "$list" ]; then
          rm "$link" && echo "  $d: removed the symlink at $link"
        fi
      done < <(links "$d")
      if [ -z "$list" ]; then
        echo "  $d: nothing recorded, skipping"
        continue
      fi
      if [ "$list" = "$(cd "$to" 2>/dev/null && pwd -P)" ]; then
        echo "  $d: already with the signed-in account"
        holding=$((holding + 1))
        continue
      fi
      # By the time this runs, the signed-in account normally has a file or
      # two of its own here, and a whole history if it has been used before.
      # Each entry moves into the list.  Session files are named after their
      # session, so they do not collide.  An entry whose name is already taken
      # is set aside instead, which keeps the list's copy.  The directory it is
      # set aside in is made fresh for each run, so a later switch does not
      # write over what this one set aside.
      if [ -d "$to" ]; then
        moved=0 kept="" aside="" aside_template="$to.superseded.XXXXXX"
        if ! merge_into "$to" "$list" ""; then
          echo "  $d: could not move everything out of $to; the list is unchanged" >&2
          failed=1
          continue
        fi
        [ "$moved" -gt 0 ] && echo "  $d: merged $moved of the signed-in account's entries"
        [ -n "$kept" ] && echo "  $d: set entries aside in $aside:$kept"
        if ! rmdir "$to"; then
          echo "  $d: $to is not empty, refusing to replace it" >&2
          failed=1
          continue
        fi
      fi
      if ! { mkdir -p "$(dirname "$to")" && mv "$list" "$to"; }; then
        echo "  $d: could not move $list to $to" >&2
        failed=1
        continue
      fi
      echo "  $d: moved"
      holding=$((holding + 1)) moves=$((moves + 1))
    done
    if [ "$failed" -ne 0 ]; then
      echo "Not everything was moved; see the messages above.  The recorded" >&2
      echo "account is unchanged, so rerunning this carries on from here." >&2
      exit 1
    fi
    if [ "$holding" -eq 0 ]; then
      echo "No session list was found to move." >&2
      exit 1
    fi
    printf '%s %s %s\n' "$acct" "$org" "$email" > "$STATE"
    if [ "$moves" -eq 0 ]; then
      echo "The session list is already with the account the app is logged in"
      echo "as.  After a switch, open the app once so it creates its directory,"
      echo "then quit the app and rerun this."
    else
      echo "Recorded $email as the account holding the list."
      who="${from%%/*}"
      if [ "$from" = "$old_acct/$old_org" ] && [ "$old_email" != unknown ]; then
        who="$old_email"
      fi
      if ! groups="$(python3 "$(dirname "$0")/sidebar-groups.py" "$SUPPORT" "$from" "$acct/$org")"; then
        echo "Could not list the sidebar groups $who had; see the message above."
      elif [ -n "$groups" ]; then
        echo
        echo "Sidebar groups stay with each account.  $who had filed these sessions"
        echo "in groups, and this account has not:"
        sed 's/^/  /' <<< "$groups"
        echo "File them in the app, making any group that is missing, or ask Claude to."
      fi
      echo "Now open the Claude app again."
    fi
    ;;

  groups)
    python3 "$(dirname "$0")/sidebar-groups.py" "$SUPPORT"
    ;;

  recover)
    [ -f "$STATE" ] || { echo "No recorded account; run 'record' first." >&2; exit 1; }
    read -r old_acct old_org old_email < "$STATE"
    pair="$(live_pair)" || exit 1
    read -r acct org <<< "$pair"
    dir="$SUPPORT/claude-code-sessions/$acct/$org"
    if [ "$acct $org" != "$old_acct $old_org" ] || [ -L "$dir" ] || [ ! -d "$dir" ] ||
       [ -n "$(links claude-code-sessions)" ]; then
      echo "The session list is not in place with the account the app is logged" >&2
      echo "in as.  Quit the app and run 'move' first." >&2
      exit 1
    fi
    if [ "${2-}" = "--apply" ] && app_is_running; then
      echo "The Claude app is running.  Quit it completely first: it would" >&2
      echo "write its own copies of these sessions over the recovered ones." >&2
      exit 1
    fi
    python3 "$(dirname "$0")/recover-sessions.py" "$dir" ${2+"$2"}
    ;;

  *)
    echo "usage: $0 status|record|move|groups|recover [--apply|--verify]" >&2
    exit 1
    ;;
esac
