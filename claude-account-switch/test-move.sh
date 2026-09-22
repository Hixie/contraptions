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
reset() { rm -rf "$HOME"; mkdir -p "$HOME"; }
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

echo "== switching back"
login $A $AO; session $A $AO a3
move; check $? 0 "move succeeds"
check "$(sessions $A $AO)" 4 "all four sessions are back"
check "$(exists "$CODE/$B/$BO")" none "the other account's directory is gone"

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
move; check $? 0 "move succeeds"
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

echo
if [ "$failures" -eq 0 ]; then echo "All tests passed."; else echo "$failures failed."; exit 1; fi
