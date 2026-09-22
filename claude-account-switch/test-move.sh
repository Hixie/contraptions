#!/bin/bash
# Tests for "account-switch.sh move", run against a scratch home directory.
# The app-running check is stubbed out, so these run while the app is open.
#
#   ./test-move.sh

set -u
here="$(cd "$(dirname "$0")" && pwd)"
T="$(mktemp -d)"
trap 'rm -rf "$T"' EXIT
export HOME="$T/home"
sed 's|^app_is_running() {|app_is_running() { return 1; }\nreal_app_is_running() {|' \
  "$here/account-switch.sh" > "$T/as.sh"
chmod +x "$T/as.sh"
cp "$here/sidebar-groups.py" "$T/"
S="$HOME/Library/Application Support/Claude"
CODE="$S/claude-code-sessions"
AGENT="$S/local-agent-mode-sessions"
A=aaaaaaaa-0000-0000-0000-000000000001 AO=aaaaaaaa-0000-0000-0000-0000000000a0
B=bbbbbbbb-0000-0000-0000-000000000002 BO=bbbbbbbb-0000-0000-0000-0000000000b0
C=cccccccc-0000-0000-0000-000000000003 CO=cccccccc-0000-0000-0000-0000000000c0

failures=0
check() {
  if [ "$1" = "$2" ]; then echo "  ok: $3"; else echo "  FAIL: $3 (got '$1', want '$2')"; failures=$((failures + 1)); fi
}
reset() { rm -rf "$HOME"; mkdir -p "$S"; store_group; }
# Sign in as account $1 in organization $2, as the app does: note the account
# in config.json and create the directories with a scheduled-tasks file.
login() {
  mkdir -p "$CODE/$1/$2" "$AGENT/$1/$2"
  echo '[]' > "$CODE/$1/$2/scheduled-tasks.json"
  printf '{"lastKnownAccountUuid":"%s"}' "$1" > "$S/config.json"
}
session() { echo '{}' > "$CODE/$1/$2/local_$3.json"; }
sessions() { ls -A "$CODE/$1/$2" 2>/dev/null | grep -c '^local_'; }
exists() { if [ -L "$1" ]; then echo link; elif [ -d "$1" ]; then echo dir; else echo none; fi; }
nlinks() { find "$CODE" "$AGENT" -maxdepth 2 -type l | wc -l | tr -d ' '; }
move() { "$T/as.sh" move > "$T/out" 2>&1; }
recorded() { cut -d' ' -f1-2 "$HOME/.claude-account-switch-state"; }
# Record sidebar filings as the app does: for each account/org, group name
# and session given, that session is filed in that group.
store_group() {
  python3 - "$S" "$@" <<'PY'
import json, sys
support, rest = sys.argv[1], sys.argv[2:]
scopes = {}
for i in range(0, len(rest), 3):
    scope, name, session = rest[i:i + 3]
    stored = scopes.setdefault(scope, {"groups": [], "assignments": {}})
    stored["groups"].append({"id": "g%d" % i, "name": name})
    stored["assignments"]["code:local_" + session] = "g%d" % i
with open(support + "/claude_desktop_config.json", "w") as f:
    json.dump({"preferences": {"epitaxyPrefs": {"dframe-group-scopes": scopes}}}, f)
PY
}

echo "== first switch, to an account with a history of its own"
reset
login $A $AO; session $A $AO a1; session $A $AO a2
echo '{"v":1,"archived":["local_a1"]}' > "$CODE/$A/$AO/archived-sessions.idx"
mkdir -p "$AGENT/$A/$AO/agent"; echo '{}' > "$AGENT/$A/$AO/agent/local_x1.json"
"$T/as.sh" record > /dev/null
login $B $BO; session $B $BO b1
echo '{"v":1,"archived":["local_b1"]}' > "$CODE/$B/$BO/archived-sessions.idx"
mkdir -p "$AGENT/$B/$BO/agent"; echo '{}' > "$AGENT/$B/$BO/agent/local_y1.json"
echo 'b' > "$AGENT/$B/$BO/agent/manifest.json"; echo 'a' > "$AGENT/$A/$AO/agent/manifest.json"
move; check $? 0 "move succeeds"
check "$(sessions $B $BO)" 3 "the signed-in account has all three sessions"
check "$(exists "$CODE/$A/$AO")" none "the recorded account's directory is gone"
check "$(exists "$CODE/$B/$BO")" dir "the signed-in account's directory is a real directory"
check "$(grep -c local_a1 "$CODE/$B/$BO/archived-sessions.idx")" 1 "the list keeps its own archive index"
check "$(ls "$CODE/$B/$BO".superseded.*/archived-sessions.idx | wc -l | tr -d ' ')" 1 "the signed-in account's index is set aside"
check "$(ls "$AGENT/$B/$BO/agent" | grep -c '^local_')" 2 "nested agent sessions are merged"
check "$(cat "$AGENT/$B/$BO/agent/manifest.json")" a "a nested collision keeps the list's copy"
check "$(cat "$AGENT/$B/$BO".superseded.*/agent/manifest.json)" b "and sets the other aside at the same path"
check "$(recorded)" "$B $BO" "the signed-in account is recorded"
move; check $? 0 "a second move succeeds"
check "$(grep -c 'already with the signed-in account' "$T/out")" 2 "and reports the list already in place"

check "$(grep -c 'had filed' "$T/out")" 0 "nothing is said about sidebar groups when there were none"

echo "== switching back"
login $A $AO; session $A $AO a3
move; check $? 0 "move succeeds"
check "$(sessions $A $AO)" 4 "all four sessions are back"
check "$(exists "$CODE/$B/$BO")" none "the other account's directory is gone"

echo "== the previous account's sidebar groups are listed"
reset
login $A $AO; session $A $AO a1; "$T/as.sh" record > /dev/null
store_group "$A/$AO" "Blocked" a1
login $B $BO
move; check $? 0 "move succeeds"
check "$(grep -c "$A had filed" "$T/out")" 1 "move says the groups stay behind"
check "$(grep -A3 'had filed' "$T/out" | tail -2 | tr -s ' ')" " Blocked
 (untitled) [local_a1]" "and lists them with their sessions"

echo "== filings the signed-in account already has are not listed"
reset
login $A $AO; session $A $AO a1; session $A $AO a2; "$T/as.sh" record > /dev/null
store_group "$A/$AO" "Blocked" a1 "$A/$AO" "Blocked" a2 "$B/$BO" "Blocked" a1
login $B $BO
move; check $? 0 "move succeeds"
check "$(grep -A3 'had filed' "$T/out" | tail -2 | tr -s ' ')" " Blocked
 (untitled) [local_a2]" "only the missing filing is listed"
store_group "$A/$AO" "Blocked" a1 "$B/$BO" "Blocked" a1
login $A $AO
move; check $? 0 "switching back succeeds"
check "$(grep -c 'had filed' "$T/out")" 0 "and lists nothing when nothing is missing"

echo "== sidebar groups that cannot be read"
reset
login $A $AO; session $A $AO a1; "$T/as.sh" record > /dev/null
echo '{ not json' > "$S/claude_desktop_config.json"
login $B $BO
move; check $? 0 "move succeeds"
check "$(grep -c 'Could not list the sidebar groups' "$T/out")" 1 "and says it could not list the groups"

echo "== three accounts, forgetting one move"
reset
login $A $AO; session $A $AO a1; "$T/as.sh" record > /dev/null
login $B $BO; move; session $B $BO b1
login $C $CO; session $C $CO c1
login $A $AO; move; check $? 0 "move from the second account to the first"
check "$(sessions $A $AO)" 2 "the first account has a1 and b1"
check "$(sessions $C $CO)" 1 "c1 waits in the third account's directory"
login $C $CO; move; check $? 0 "move to the third account"
check "$(sessions $C $CO)" 3 "the third account has all three"

echo "== links left by the earlier version, when the list is already in place"
reset
login $A $AO; session $A $AO a1; "$T/as.sh" record > /dev/null
for tree in "$CODE" "$AGENT"; do mkdir -p "$tree/$B"; ln -s "$tree/$A/$AO" "$tree/$B/$BO"; done
check "$("$T/as.sh" status | grep -c 'cannot save through it')" 2 "status reports both links"
move; check $? 0 "move succeeds"
check "$(nlinks)" 0 "both links are removed"
check "$(sessions $A $AO)" 1 "the list is untouched"

echo "== links left by the earlier version, signed in as the linked account"
reset
login $A $AO; session $A $AO a1; "$T/as.sh" record > /dev/null
for tree in "$CODE" "$AGENT"; do mkdir -p "$tree/$B"; ln -s "$tree/$A/$AO" "$tree/$B/$BO"; done
printf '{"lastKnownAccountUuid":"%s"}' $B > "$S/config.json"
move; check $? 0 "move succeeds"
check "$(exists "$CODE/$B/$BO")" dir "the link became the real directory"
check "$(sessions $B $BO)" 1 "holding the list"
check "$(nlinks)" 0 "no links remain"

echo "== the recorded directory is itself a link"
reset
login $A $AO; session $A $AO a1
for tree in "$CODE" "$AGENT"; do mkdir -p "$tree/$B"; ln -s "$tree/$A/$AO" "$tree/$B/$BO"; done
printf '{"lastKnownAccountUuid":"%s"}' $B > "$S/config.json"
"$T/as.sh" record > /dev/null
store_group "$A/$AO" "Blocked" a1
move; check $? 0 "move succeeds"
check "$(grep -c "$A had filed" "$T/out")" 1 "the groups listed are those of the account the list came from"
check "$(exists "$CODE/$B/$BO")" dir "the signed-in account has a real directory"
check "$(sessions $B $BO)" 1 "holding the list"
check "$(nlinks)" 0 "no links remain"

echo "== a link to something else is left alone"
reset
login $A $AO; session $A $AO a1; "$T/as.sh" record > /dev/null
mkdir -p "$T/elsewhere"
for tree in "$CODE" "$AGENT"; do mkdir -p "$tree/$B"; ln -s "$T/elsewhere" "$tree/$B/$BO"; done
printf '{"lastKnownAccountUuid":"%s"}' $B > "$S/config.json"
move; check $? 1 "move fails"
check "$(sessions $A $AO)" 1 "the list is untouched"
check "$(nlinks)" 2 "the links are untouched"
check "$(recorded)" "$A $AO" "the recorded account is unchanged"

echo "== a link in one tree stops the whole move, which can then be rerun"
reset
login $A $AO; session $A $AO a1; echo '{}' > "$AGENT/$A/$AO/local_x1.json"
"$T/as.sh" record > /dev/null
mkdir -p "$T/elsewhere2" "$AGENT/$B"
login $B $BO; rm -r "$AGENT/$B/$BO"; ln -s "$T/elsewhere2" "$AGENT/$B/$BO"
move; check $? 1 "move fails"
check "$(sessions $A $AO)" 1 "the session list has not moved"
check "$(recorded)" "$A $AO" "the recorded account is unchanged"
rm "$AGENT/$B/$BO"
move; check $? 0 "after removing the link, move succeeds"
check "$(sessions $B $BO)" 1 "the session list has moved"
check "$([ -f "$AGENT/$B/$BO/local_x1.json" ] && echo yes)" yes "the agent-mode list has moved"
check "$(recorded)" "$B $BO" "the signed-in account is recorded"

echo "== a move that stops partway lists the groups when it is rerun"
reset
login $A $AO; session $A $AO a1; "$T/as.sh" record > /dev/null
store_group "$A/$AO" "Blocked" a1
login $B $BO
# The signed-in account has an agent-mode entry that cannot be moved, so
# the session list moves and the agent-mode list does not.
mkdir -p "$AGENT/$B/$BO/stuck"; chmod 555 "$AGENT/$B/$BO/stuck"
move; check $? 1 "move fails"
check "$(sessions $B $BO)" 1 "after moving the session list"
check "$(recorded)" "$A $AO" "the recorded account is unchanged"
chmod 755 "$AGENT/$B/$BO/stuck"
move; check $? 0 "the rerun succeeds"
check "$(grep -c "$A had filed" "$T/out")" 1 "and lists the groups of the account the list came from"

echo
if [ "$failures" -eq 0 ]; then echo "All tests passed."; else echo "$failures failed."; exit 1; fi
