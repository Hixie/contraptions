#!/usr/bin/env python3
# Tests for sidebar-groups.py, run against a made-up app support directory.
#
#   ./test-groups.py

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
A = "aaaaaaaa-0000-0000-0000-000000000001/aaaaaaaa-0000-0000-0000-0000000000a0"
B = "bbbbbbbb-0000-0000-0000-000000000002/bbbbbbbb-0000-0000-0000-0000000000b0"
FAILURES = []


def check(got, want, what):
    if got == want:
        print("  ok: %s" % what)
    else:
        print("  FAIL: %s\n    got:  %r\n    want: %r" % (what, got, want))
        FAILURES.append(what)


def write_config(support, scopes):
    with open(os.path.join(support, "claude_desktop_config.json"), "w") as f:
        json.dump({"preferences": {"epitaxyPrefs": {"dframe-group-scopes": scopes}}}, f)


def session(directory, sid, **fields):
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, sid + ".json"), "w") as f:
        json.dump(fields, f)


def run(support, *args):
    out = subprocess.run([sys.executable, os.path.join(HERE, "sidebar-groups.py"), support, *args],
                         capture_output=True, text=True)
    return out.returncode, out.stdout, out.stderr


def main():
    with tempfile.TemporaryDirectory() as support:
        live = os.path.join(support, "claude-code-sessions", *A.split("/"))
        session(live, "local_s1", title="First session")
        session(live, "local_s2", title="Second", isArchived=True)
        session(live, "local_s3", title="Third")
        session(live + ".before-recover.abc", "local_s1", title="Stale backup title")
        write_config(support, {
            A: {"groups": [{"id": "g1", "name": "Blocked 🚧"}, {"id": "g2", "name": "Empty"},
                           {"id": "g3", "name": "no groups"}],
                "assignments": {"code:local_s2": "g1", "code:local_s1": "g1",
                                "chat:c9": "g1", "code:local_s3": "g3", "code:local_gone": "g3"},
                "order": {"g1": ["code:local_s1", "code:local_s2", "chat:c9"]}},
            B: {"groups": [{"id": "h1", "name": "Blocked 🚧"}],
                "assignments": {"code:local_s1": "h1"}},
            "other/org": {"groups": []},
        })

        print("== every account")
        code, out, _ = run(support)
        check(code, 0, "succeeds")
        check(out, A + ":\n"
                   "  Blocked 🚧\n"
                   "    First session  [local_s1]\n"
                   "    Second (archived)  [local_s2]\n"
                   "    chat item  [c9]\n"
                   "  no groups\n"
                   "    Third  [local_s3]\n"
                   "    (no session file)  [local_gone]\n" +
                   B + ":\n"
                   "  Blocked 🚧\n"
                   "    First session  [local_s1]\n"
                   "other/org:\n"
                   "  nothing filed in groups\n",
              "lists filings in the stored order, with titles from the session "
              "directory and not its backup, and leaves out empty groups")

        print("== what one account has filed and another has not")
        code, out, _ = run(support, A, B)
        check((code, out), (0, "Blocked 🚧\n"
                               "  Second (archived)  [local_s2]\n"
                               "  chat item  [c9]\n"
                               "no groups\n"
                               "  Third  [local_s3]\n"),
              "lists only what the second account lacks, leaving out a session "
              "that no longer exists")
        code, out, _ = run(support, B, A)
        check((code, out), (0, ""), "prints nothing when nothing is missing")
        code, out, _ = run(support, "missing/scope", B)
        check((code, out), (0, ""), "an account with nothing recorded has nothing to list")

        print("== records it does not recognize")
        write_config(support, {A: {"groups": [{"name": "no id"}]}})
        code, out, err = run(support)
        check((code, "form this script expects" in err), (1, True), "a malformed group fails")
        write_config(support, ["not", "a", "map"])
        code, out, err = run(support)
        check((code, "form this script expects" in err), (1, True), "a malformed record fails")
        with open(os.path.join(support, "claude_desktop_config.json"), "w") as f:
            json.dump({"preferences": None}, f)
        code, out, err = run(support)
        check((code, "form this script expects" in err), (1, True),
              "a config whose preferences are not a map fails")
        with open(os.path.join(support, "claude_desktop_config.json"), "w") as f:
            f.write("{ not json")
        code, out, err = run(support)
        check((code, "could not read" in err), (1, True), "an unreadable config fails")
        with open(os.path.join(support, "claude_desktop_config.json"), "w") as f:
            json.dump({"preferences": {}}, f)
        code, out, _ = run(support)
        check((code, out), (0, ""), "a config with no record of groups lists nothing")

    print()
    if FAILURES:
        print("%d failed." % len(FAILURES))
        sys.exit(1)
    print("All tests passed.")


if __name__ == "__main__":
    main()
