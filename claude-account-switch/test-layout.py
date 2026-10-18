#!/usr/bin/env python3
# Tests for sidebar-layout.py, run against a made-up app support directory.
#
#   ./test-layout.py

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


def section(kind, order, sort="recency", **fields):
    show = {"metadata": False, "emptyGroups": False, "sideChats": False}
    show.update(fields.pop("show", {}))
    record = {"id": kind, "kind": kind, "v": 2, "name": "", "order": order, "sortBy": sort,
              "ascending": False, "show": show, "collapsed": False, "shown": True}
    record.update(fields)
    return record


def write_config(support, scopes):
    with open(os.path.join(support, "claude_desktop_config.json"), "w") as f:
        json.dump({"preferences": {"epitaxyPrefs": {"dframe-code-sections": scopes}}}, f)


def run(support, *args):
    out = subprocess.run([sys.executable, os.path.join(HERE, "sidebar-layout.py"), support, *args],
                         capture_output=True, text=True)
    return out.returncode, out.stdout, out.stderr


def main():
    with tempfile.TemporaryDirectory() as support:
        write_config(support, {
            A: {"sections": [section("sessions", 2, "created", show={"sideChats": True}),
                             section("pinned", 0),
                             section("routines", 1, collapsed=True, shown=False),
                             section("manual", 3, id="g1", name="Blocked")],
                "migratedFrom": 1, "prefsSeeded": True},
            B: {"sections": [section("pinned", 0), section("routines", 1, collapsed=True, shown=False),
                             section("sessions", 2)],
                "migratedFrom": 1, "prefsSeeded": True},
        })

        print("== every account")
        code, out, _ = run(support)
        check(code, 0, "succeeds")
        check(out.splitlines()[:4], [
            A + ":",
            "  Pinned: shown yes, collapsed no, sorted by last activity, order descending, "
            "shows metadata no, shows empty groups no, shows side chats no",
            "  Routines: shown no, collapsed yes, sorted by last activity, order descending, "
            "shows metadata no, shows empty groups no, shows side chats no",
            "  Sessions: shown yes, collapsed no, sorted by date created, order descending, "
            "shows metadata no, shows empty groups no, shows side chats yes",
        ], "lists built-in sections in sidebar order, leaving custom groups to sidebar-groups.py")

        print("== what one account has set and another has not")
        code, out, _ = run(support, A, B)
        check((code, out), (0, "Sessions: sorted by date created (this account: last activity)\n"
                               "Sessions: shows side chats yes (this account: no)\n"),
              "lists only the settings the second account does not share")
        code, out, _ = run(support, B, B)
        check((code, out), (0, ""), "prints nothing when the layouts match")
        code, out, _ = run(support, "missing/scope", B)
        check((code, out), (0, ""), "an account with nothing recorded has nothing to list")
        code, out, _ = run(support, B, "missing/scope")
        check((code, out.splitlines()[-1]),
              (0, "Sessions: shown yes, collapsed no, sorted by last activity, order descending, "
                  "shows metadata no, shows empty groups no, shows side chats no"),
              "an account the app has not laid out yet gets the whole layout")

        print("== a reordered sidebar")
        write_config(support, {
            A: {"sections": [section("sessions", 0), section("pinned", 1)]},
            B: {"sections": [section("pinned", 0), section("sessions", 1)]},
        })
        code, out, _ = run(support, A, B)
        check((code, out), (0, "Section order: Sessions, Pinned\n"), "names the order to set")

        print("== records it does not recognize")
        write_config(support, {A: {"sections": [{"kind": "sessions"}]}})
        code, out, err = run(support)
        check((code, "form this script expects" in err), (1, True), "a malformed section fails")
        write_config(support, ["not", "a", "map"])
        code, out, err = run(support)
        check((code, "form this script expects" in err), (1, True), "a malformed record fails")
        with open(os.path.join(support, "claude_desktop_config.json"), "w") as f:
            f.write("{ not json")
        code, out, err = run(support)
        check((code, "could not read" in err), (1, True), "an unreadable config fails")
        with open(os.path.join(support, "claude_desktop_config.json"), "w") as f:
            json.dump({"preferences": {}}, f)
        code, out, _ = run(support)
        check((code, out), (0, ""), "a config with no record of the layout lists nothing")

    print()
    if FAILURES:
        print("%d failed." % len(FAILURES))
        sys.exit(1)
    print("All tests passed.")


if __name__ == "__main__":
    main()
