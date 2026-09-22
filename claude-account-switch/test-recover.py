#!/usr/bin/env python3
# Tests for recover-sessions.py, run against a scratch home directory that
# holds a made-up app log, transcripts and session files, with a stand-in for
# the gh command on PATH.
#
#   ./test-recover.py

import datetime
import glob
import json
import os
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
T0 = int(time.time()) // 60 * 60 - 86400
FAILURES = []


def check(got, want, what):
    if got == want:
        print("  ok: %s" % what)
    else:
        print("  FAIL: %s (got %r, want %r)" % (what, got, want))
        FAILURES.append(what)


def stamp(t):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(t))


def iso(t):
    return datetime.datetime.fromtimestamp(t, datetime.timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%S.000Z")


class Home:
    def __init__(self, root):
        self.root = root
        self.support = os.path.join(root, "Library/Application Support/Claude")
        self.dir = os.path.join(self.support, "claude-code-sessions/acct/org")
        os.makedirs(self.dir)
        os.makedirs(os.path.join(root, "Library/Logs/Claude"))
        self.bin = os.path.join(root, "bin")
        os.makedirs(self.bin)
        gh = os.path.join(self.bin, "gh")
        with open(gh, "w") as f:
            f.write("#!/bin/sh\n"
                    "# gh pr view <url or number> --json ...\n"
                    "n=\"${3##*/}\"\n"
                    "printf '{\"url\":\"https://github.com/org/repo/pull/%s\","
                    "\"headRefName\":\"branch-%s\",\"baseRefName\":\"main\","
                    "\"state\":\"OPEN\"}' \"$n\" \"$n\"\n")
        os.chmod(gh, 0o755)
        self.lines = []

    def log(self, t, text):
        self.lines.append("%s [info] %s\n" % (stamp(t), text))

    def fail(self, t, sid, tree="claude-code-sessions"):
        self.lines.append(
            "%s [error] Failed to save session %s: ENOTDIR: not a directory, open "
            "'/x/%s/acct/org' { errno: -20 }\n" % (stamp(t), sid, tree))

    def session(self, sid, **fields):
        path = os.path.join(self.dir, sid + ".json")
        with open(path, "w") as f:
            json.dump({"sessionId": sid, **fields}, f)

    def transcript(self, cwd, cli, entries):
        d = os.path.join(self.root, ".claude/projects",
                         "".join(c if c.isalnum() else "-" for c in cwd))
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, cli + ".jsonl"), "w") as f:
            for e in entries:
                f.write(json.dumps({"cwd": cwd, "sessionId": cli, **e}) + "\n")

    def run(self, *args):
        with open(os.path.join(self.root, "Library/Logs/Claude/main.log"), "w") as f:
            f.writelines(self.lines)
        env = dict(os.environ, HOME=self.root,
                   PATH=self.bin + os.pathsep + os.environ["PATH"])
        return subprocess.run([sys.executable, os.path.join(HERE, "recover-sessions.py"),
                               self.dir, *args], env=env, capture_output=True, text=True)

    def read(self, sid):
        path = os.path.join(self.dir, sid + ".json")
        return json.load(open(path)) if os.path.exists(path) else None


def user(t, mode=None):
    e = {"type": "user", "timestamp": iso(t), "message": {"role": "user", "content": "go"}}
    if mode:
        e["permissionMode"] = mode
    return e


def assistant(t, model, content="done", effort=None):
    e = {"type": "assistant", "timestamp": iso(t),
         "message": {"role": "assistant", "model": model,
                     "content": [{"type": "text", "text": content}]}}
    if effort:
        e["effort"] = effort
    return e


def main():
    with tempfile.TemporaryDirectory() as root:
        h = Home(root)
        old = dict(cliSessionId="c0000001-0000-0000-0000-000000000000", cwd="/tmp/old", originCwd="/tmp/old",
                   title="Old", titleSource="user", isArchived=False, isStarred=True,
                   model="m1", permissionMode="auto", lastActivityAt=(T0 - 1000) * 1000)
        h.session("local_00000001-0000-0000-0000-000000000000", **old)
        h.session("local_00000002-0000-0000-0000-000000000000", **{**old, "cliSessionId": "c0000002-0000-0000-0000-000000000000", "cwd": "/tmp/saved",
                                    "originCwd": "/tmp/saved", "isStarred": False,
                                    "lastActivityAt": (T0 - 500) * 1000})
        open(os.path.join(h.dir, "local_00000003-0000-0000-0000-000000000000.json"), "w").close()
        h.transcript("/tmp/old", "c0000001-0000-0000-0000-000000000000", [user(T0 - 1100, "auto"), assistant(T0 - 1000, "m1")])

        h.log(T0 - 5, "Starting app {")
        # Changed between the launch and the first failed save.
        h.log(T0 - 2, "LocalSessions.archive: sessionId=local_00000001-0000-0000-0000-000000000000")
        h.log(T0 - 2, "Updated session local_00000001-0000-0000-0000-000000000000: { isStarred: false, titleSource: 'user' }")
        h.fail(T0, "local_00000001-0000-0000-0000-000000000000")

        # A new session: a refused mode, a title from a tool, a linked pull
        # request, and a transcript that ends with a synthetic notice.
        h.log(T0 + 10, "Starting local session local_00000004-0000-0000-0000-000000000000 in /tmp/proj")
        h.log(T0 + 10, "Mapping internal session local_00000004-0000-0000-0000-000000000000 to CLI session c0000003-0000-0000-0000-000000000000")
        h.log(T0 + 13, "Updated session local_00000004-0000-0000-0000-000000000000: { title: '<redacted>', titleSource: 'tool' }")
        h.log(T0 + 16, "[GitHubPrManager] Bound github #42 to session local_00000004-0000-0000-0000-000000000000")
        h.log(T0 + 18, "[CCD] LocalSessions.setPermissionMode: sessionId=local_00000004-0000-0000-0000-000000000000, mode=bypassPermissions")
        h.lines.append("%s [error] [CCD] Failed to set permission mode for session local_00000004-0000-0000-0000-000000000000: "
                       "Cannot set permission mode\n" % stamp(T0 + 18))
        h.fail(T0 + 18, "local_00000004-0000-0000-0000-000000000000")
        h.transcript("/tmp/proj", "c0000003-0000-0000-0000-000000000000", [
            user(T0 + 11, "default"),
            assistant(T0 + 12, "claude-x", effort="high"),
            {"type": "assistant", "timestamp": iso(T0 + 13), "message": {
                "role": "assistant", "model": "claude-x", "content": [{
                    "type": "tool_use", "id": "t1", "name": "mcp__ccd_session_mgmt__set_session_title",
                    "input": {"session_id": "self", "title": "New title"}}]}},
            {"type": "user", "timestamp": iso(T0 + 13), "message": {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "Renamed"}]}},
            assistant(T0 + 15, "claude-x", "Opened https://github.com/org/repo/pull/42"),
            assistant(T0 + 17, "<synthetic>", "You've hit your limit"),
        ])

        # A mode set in the app after the transcript last recorded one.
        h.log(T0 + 20, "Starting local session local_00000005-0000-0000-0000-000000000000 in /tmp/mode")
        h.log(T0 + 20, "Mapping internal session local_00000005-0000-0000-0000-000000000000 to CLI session c0000004-0000-0000-0000-000000000000")
        h.log(T0 + 23, "[CCD] LocalSessions.setPermissionMode: sessionId=local_00000005-0000-0000-0000-000000000000, mode=acceptEdits")
        h.fail(T0 + 23, "local_00000005-0000-0000-0000-000000000000")
        h.transcript("/tmp/mode", "c0000004-0000-0000-0000-000000000000", [user(T0 + 21, "default"),
                                               assistant(T0 + 25, "claude-x")])

        # A saved session: a lost star is overridden by a later, saved one, and
        # a lost mode change is overridden by a newer mode in the transcript.
        h.log(T0 + 30, "Updated session local_00000002-0000-0000-0000-000000000000: { isStarred: true, titleSource: 'user' }")
        h.log(T0 + 31, "[CCD] LocalSessions.setPermissionMode: sessionId=local_00000002-0000-0000-0000-000000000000, mode=acceptEdits")
        h.fail(T0 + 31, "local_00000002-0000-0000-0000-000000000000")
        h.log(T0 + 60, "Updated session local_00000002-0000-0000-0000-000000000000: { isStarred: false, titleSource: 'user' }")
        h.transcript("/tmp/saved", "c0000002-0000-0000-0000-000000000000", [user(T0 + 35, "plan"),
                                                 assistant(T0 + 36, "claude-x")])

        # A session deleted after its last failed save, and a failure in the
        # agent-mode tree.
        h.log(T0 + 40, "Starting local session local_00000006-0000-0000-0000-000000000000 in /tmp/gone")
        h.log(T0 + 40, "Mapping internal session local_00000006-0000-0000-0000-000000000000 to CLI session c0000005-0000-0000-0000-000000000000")
        h.fail(T0 + 40, "local_00000006-0000-0000-0000-000000000000")
        h.log(T0 + 70, "LocalSessions.delete: sessionId=local_00000006-0000-0000-0000-000000000000")
        h.transcript("/tmp/gone", "c0000005-0000-0000-0000-000000000000", [user(T0 + 41, "default")])
        h.log(T0 + 45, "Starting local session local_00000007-0000-0000-0000-000000000000 in /tmp/agent")
        h.fail(T0 + 45, "local_00000007-0000-0000-0000-000000000000", "local-agent-mode-sessions")

        print("== report")
        out = h.run()
        check(out.returncode, 0, "the report succeeds")
        check("skipping unreadable local_00000003-0000-0000-0000-000000000000.json" in out.stdout, True, "an empty file is skipped")
        check("skip    local_00000006-0000-0000-0000-000000000000" in out.stdout, True, "a session deleted later is skipped")
        check("local_00000007-0000-0000-0000-000000000000" in out.stdout, False, "an agent-mode failure is ignored")
        check("Would write 2 new session files and 2 updated ones." in out.stdout, True,
              "two sessions to create and two to update")

        print("== apply")
        out = h.run("--apply")
        check(out.returncode, 0, "applying succeeds")
        new = h.read("local_00000004-0000-0000-0000-000000000000") or {}
        check(new.get("title"), "New title", "the title comes from the session's own tool call")
        check(new.get("model"), "claude-x", "a synthetic notice does not set the model")
        check(new.get("effort"), "high", "effort comes from the transcript")
        check(new.get("permissionMode"), "default", "a refused mode change is not applied")
        check(new.get("cwd"), "/tmp/proj", "the working directory is the one that started it")
        check([(p["prNumber"], p["url"], p["branch"], p["provider"]) for p in new.get("prs", [])],
              [(42, "https://github.com/org/repo/pull/42", "branch-42", "github")],
              "the linked pull request is restored with its branch")
        check((h.read("local_00000005-0000-0000-0000-000000000000") or {}).get("permissionMode"), "acceptEdits",
              "a mode set after the transcript's last one stands")
        old = h.read("local_00000001-0000-0000-0000-000000000000") or {}
        check((old.get("isArchived"), old.get("isStarred")), (True, False),
              "changes between the launch and the first failed save are applied")
        saved = h.read("local_00000002-0000-0000-0000-000000000000") or {}
        check(saved.get("isStarred"), False, "a field saved again later keeps its saved value")
        check(saved.get("permissionMode"), "plan", "a newer mode in the transcript stands")
        check(h.read("local_00000006-0000-0000-0000-000000000000"), None, "the deleted session is not recreated")
        backups = glob.glob(h.dir + ".before-recover.*")
        check(len(backups), 1, "one backup directory")
        check(sorted(os.listdir(backups[0])) if backups else [],
              ["local_00000001-0000-0000-0000-000000000000.json", "local_00000002-0000-0000-0000-000000000000.json"], "holding the two files it replaced")
        check(json.load(open(os.path.join(backups[0], "local_00000001-0000-0000-0000-000000000000.json")))["isArchived"]
              if backups else None, False, "as they were")
        out = h.run()
        check("Would write 0 new session files and 0 updated ones." in out.stdout, True,
              "a second run has nothing to write")

    print("== a log that has lost the launch before the first failure")
    with tempfile.TemporaryDirectory() as root:
        h = Home(root)
        h.log(T0 + 10, "Starting local session local_00000004-0000-0000-0000-000000000000 in /tmp/proj")
        h.fail(T0 + 10, "local_00000004-0000-0000-0000-000000000000")
        out = h.run()
        check("the log no longer reaches back" in out.stdout, True, "the report warns")

    print()
    if FAILURES:
        print("%d failed." % len(FAILURES))
        sys.exit(1)
    print("All tests passed.")


if __name__ == "__main__":
    main()
