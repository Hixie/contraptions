#!/usr/bin/env python3
# Tests for sidebar-steps.py, run against a made-up app support directory.
#
#   ./test-steps.py

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


def section(kind, order, name="", **fields):
    record = {"id": kind if not name else "id-" + name, "kind": kind, "v": 2, "name": name,
              "order": order, "sortBy": "recency", "ascending": False, "collapsed": False,
              "shown": True, "show": {"metadata": False, "emptyGroups": False, "sideChats": False}}
    record.update(fields)
    return record


def write_config(support, sections, filings):
    with open(os.path.join(support, "claude_desktop_config.json"), "w") as f:
        json.dump({"preferences": {"epitaxyPrefs": {"dframe-code-sections": sections,
                                                    "dframe-group-scopes": filings}}}, f)


def session(support, sid, **fields):
    directory = os.path.join(support, "claude-code-sessions", *B.split("/"))
    os.makedirs(directory, exist_ok=True)
    with open(os.path.join(directory, sid + ".json"), "w") as f:
        json.dump(fields, f)


def run(support, *args):
    out = subprocess.run([sys.executable, os.path.join(HERE, "sidebar-steps.py"), support, *args],
                         capture_output=True, text=True)
    return out.returncode, out.stdout, out.stderr


def step(out, number):
    """The lines of step `number`, without its number."""
    lines = out.split("\n\n%d. " % number, 1)[1].split("\n\n", 1)[0].splitlines()
    return [line.strip() for line in lines]


def rows(out):
    """The sections the steps list, as (section, settings)."""
    block = out.split("as list_sections reports them:\n\n", 1)[1].split("\n\n", 1)[0]
    return [(line.split("  {")[0].strip(), json.loads("{" + line.split("  {", 1)[1]))
            for line in block.splitlines()]


def main():
    with tempfile.TemporaryDirectory() as support:
        session(support, "local_s1", title="Pinned work", isStarred=True)
        session(support, "local_s2", title="Old work", isArchived=True)
        session(support, "local_s3", title="Later work")
        layouts = {
            A: {"sections": [section("pinned", 0), section("routines", 1, shown=False),
                             section("manual", 2, "Blocked", groupBy="date", collapsed=True,
                                     show={"metadata": True}),
                             section("manual", 3, "Later"),
                             section("future", 4, "From a later version"),
                             section("sessions", 5, sortBy="created")]},
            B: {"sections": [section("pinned", 0), section("routines", 1, shown=False),
                             section("sessions", 2), section("manual", 3, "Later", groupBy="date"),
                             section("manual", 4, "Mine")]},
        }
        filings = {
            A: {"groups": [{"id": "g1", "name": "Blocked"}, {"id": "g2", "name": "Later"},
                           {"id": "g3", "name": "Unlisted"}],
                "assignments": {"code:local_s1": "g1", "code:local_s2": "g1", "chat:c9": "g1",
                                "code:local_s3": "g3"}},
            B: {"groups": [{"id": "h1", "name": "Mine"}], "assignments": {"code:local_s3": "h1"}},
        }
        write_config(support, layouts, filings)

        print("== steps to copy one account's sidebar to another")
        code, out, _ = run(support, A, B, "a@example.com")
        check(code, 0, "succeeds")
        check(out.splitlines()[3:5], ["  Signed in as: " + B, "  Copy from:    a@example.com (%s)" % A],
              "names both accounts")
        default = {"sort_by": "recency", "ascending": False, "collapsed": False, "shown": True}
        check(rows(out), [
            ("pinned", dict(default, group_by="none")),
            ("routines", dict(default, group_by="none", shown=False)),
            ('"Blocked"', dict(default, group_by="date", collapsed=True)),
            ('"Later"', dict(default, group_by="none")),
            ("sessions", dict(default, sort_by="created")),
            ('"Unlisted"', {}),
        ], "lists the sections in order with their settings, taking no recorded grouping as "
           "none except for Sessions, and ignoring sections of kinds it does not know")
        check(step(out, 1), ["Call list_sections."], "starts by listing the sections")
        check(step(out, 2)[0].startswith("For each section above written in quotes"), True,
              "then creates the sections that are missing")
        check(step(out, 3)[2:], [
            'move_sessions {"section_id": <id of "Blocked">, "session_ids": ["local_s1"]}',
            "local_s1  Pinned work",
            'move_sessions {"section_id": <id of "Unlisted">, "session_ids": ["local_s3"]}',
            'local_s3  Later work  (leaving "Mine")',
            "move_sessions unpins each pinned session it moves.  Pin these again:",
            'set_pinned {"session_id": "local_s1", "pinned": true}',
        ], "files the sessions, leaving out archived ones, naming the group a session leaves, "
           "and pins pinned ones again")
        check("set_view" in step(out, 4)[1], True, "then sets what set_view can set")
        check(step(out, 5)[-2:], [
            '- File this in the section "Blocked": chat item  [c9]',
            '- Make the section "Blocked": shows metadata yes (this account: no)',
        ], "asks the user for what the tools cannot do, taking a new section to show no details")
        check(step(out, 6), ["Tell the user what changed, and what is left for them to do."],
              "ends by reporting to the user")
        check('"Mine"' in dict(rows(out)), False, "lists no section that only the signed-in account has")

        print("== an account named only by its directory")
        code, out, _ = run(support, B, A)
        check((code, "Copy from:    %s\n" % B in out), (0, True), "names it by its directory")
        check([line for line in step(out, 5) if "Make the section" in line], [],
              "asks for no details that already match, nor for a new section's that show none")

        print("== accounts it cannot copy from")
        code, out, err = run(support, A + "x", B)
        check((code, "nothing is recorded about the sidebar of" in err), (1, True),
              "an account with nothing recorded fails")
        layouts[A]["sections"].append(section("manual", 6, "Later"))
        write_config(support, layouts, filings)
        code, out, err = run(support, A, B)
        check((code, 'more than one section named "Later"' in err), (1, True),
              "two sections with the same name fail")
        layouts[A]["sections"].pop()
        layouts[A]["sections"].append(section("manual", 6, "Sessions"))
        write_config(support, layouts, filings)
        code, out, _ = run(support, A, B)
        check(('"Sessions"' in dict(rows(out)), "sessions" in dict(rows(out))), (True, True),
              "a section named like a built-in one is told apart from it")
        layouts[A]["sections"].pop()

        print("== many sessions to file")
        for i in range(101):
            session(support, "local_m%03d" % i, title="Many")
        filings[A] = {"groups": [{"id": "g1", "name": "Mine"}],
                      "assignments": {"code:local_m%03d" % i: "g1" for i in range(101)}}
        write_config(support, layouts, filings)
        code, out, _ = run(support, A, B)
        calls = [line for line in out.splitlines() if line.strip().startswith("move_sessions {")]
        check([len(json.loads(c.split('"session_ids": ')[1][:-1])) for c in calls], [100, 1],
              "splits them into calls of at most 100")

        print("== records it cannot read")
        with open(os.path.join(support, "claude_desktop_config.json"), "w") as f:
            f.write("{ not json")
        code, out, err = run(support, A, B)
        check((code, "could not read" in err), (1, True), "an unreadable config fails")

    print()
    if FAILURES:
        print("%d failed." % len(FAILURES))
        sys.exit(1)
    print("All tests passed.")


if __name__ == "__main__":
    main()
