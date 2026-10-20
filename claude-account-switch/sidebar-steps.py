#!/usr/bin/env python3
# Print steps for Claude, running in a Code session in the Claude desktop app,
# to make one account's Code sidebar match another's: its sections, the
# sessions filed in each, and each section's settings.
#
# The first account's sidebar is read from the copy the app keeps in
# claude_desktop_config.json, as sidebar-groups.py and sidebar-layout.py read
# it.  The steps list its sections and their settings, for Claude to compare
# with what the app's sidebar tools report and to set with those tools.
# Settings those tools cannot make are listed for Claude to ask the user to
# make.  Like "move", the steps add what the second account lacks and leave
# alone what only it has.
#
#   sidebar-steps.py <support-dir> <from account/org> <to account/org> [<name of from>]

import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"),
                                                  os.path.join(HERE, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


groups = load("sidebar-groups")
layout = load("sidebar-layout")

BATCH = 100


def ref(section):
    """How the steps write a section: a built-in one by its id, and a custom
    one by its name in quotes."""
    return section["kind"] if section["kind"] in layout.BUILT_IN else json.dumps(section["name"])


def section_id(name):
    return "<id of %s>" % json.dumps(name)


def call(tool, **args):
    return "%s {%s}" % (tool, ", ".join("%s: %s" % (json.dumps(k), v) for k, v in args.items()))


def main():
    if len(sys.argv) not in (4, 5):
        sys.exit("usage: sidebar-steps.py <support-dir> <from account/org> <to account/org> "
                 "[<name of from>]")
    support, source, target = sys.argv[1:4]
    who = sys.argv[4] if len(sys.argv) == 5 else source
    sections = layout.sections(support)
    scopes = groups.filings(support)
    known = groups.sessions(support)
    if source not in sections and source not in scopes:
        sys.exit("nothing is recorded about the sidebar of %s" % source)
    want = sections.get(source, [])
    refs = [ref(s) for s in want]
    named = [json.dumps(name) for name, _ in scopes.get(source, [])]
    for names in (refs, named):
        twice = sorted({r for r in names if names.count(r) > 1})
        if twice:
            sys.exit("%s has more than one section named %s; rename all but one so the "
                     "steps can tell them apart" % (who, ", ".join(twice)))
    rows = [(r, layout.fields(s)) for r, s in zip(refs, want)]
    # Every group with sessions to file is a section too, whether or not the
    # layout's copy lists it.
    filings = groups.missing(scopes, known, source, target)
    rows += [(json.dumps(name), {}) for name, _ in filings if json.dumps(name) not in refs]

    filed = {m: name for name, members in scopes.get(target, []) for m in members}
    moves, pins, by_hand = [], [], []
    for name, members in filings:
        code = [m[len("code:"):] for m in members if m.startswith("code:")]
        for i in range(0, len(code), BATCH):
            moves.append((call("move_sessions", section_id=section_id(name),
                               session_ids=json.dumps(code[i:i + BATCH])),
                          ["%s  %s%s" % (sid, known["code:" + sid].get("title") or "(untitled)",
                                          "  (leaving %s)" % json.dumps(filed["code:" + sid])
                                          if "code:" + sid in filed else "")
                           for sid in code[i:i + BATCH]]))
        pins += [call("set_pinned", session_id=json.dumps(sid), pinned="true")
                 for sid in code if known["code:" + sid].get("isStarred")]
        by_hand += ["File this in the section %s: %s" % (json.dumps(name), groups.describe(m, known))
                    for m in members if not m.startswith("code:")]

    # list_sections does not report which details a section's rows show, so
    # these come from the signed-in account's record.  A section it has no
    # record of is taken to show none.
    have = {}
    for s in sections.get(target, []):
        have.setdefault(ref(s), s)
    for r, s in zip(refs, want):
        mine = dict(layout.settings(have.get(r, {"kind": "manual",
                                                 "show": {k: False for k in layout.DETAILS}})))
        by_hand += ["Make the section %s: %s %s (this account: %s)" % (
                        r, setting, value, mine.get(setting, "not set"))
                    for setting, value in layout.settings(s)
                    if setting.startswith("shows ") and mine.get(setting) != value]

    print("Steps for Claude: make the Code sidebar of the account the Claude app is")
    print("signed in as match the layout and grouping of another account.")
    print()
    print("  Signed in as: %s" % target)
    print("  Copy from:    %s" % (source if who == source else "%s (%s)" % (who, source)))
    print()
    print("Use the app's sidebar tools, whose names start with mcp__ccd_sidebar__.")
    print("Below, a built-in section is written as its id: pinned, routines or")
    print("sessions.  Any other section is written as its name in quotes.  Its id,")
    print('written <id of "NAME">, is the id of the first section of kind "manual"')
    print("named NAME that list_sections reports.")
    print()
    print("These are the sections the sidebar should have, in sidebar order, with")
    print("their settings as list_sections reports them:")
    print()
    width = max(len(r) for r, _ in rows) if rows else 0
    for r, settings in rows:
        print("  %-*s  %s" % (width, r, json.dumps(settings)))
    steps = [["Call list_sections."]]
    steps.append(["For each section above written in quotes that list_sections does not",
                  "   report, in the order above, call create_section with its name."])
    if moves:
        step = ["File these sessions.  In the default permission mode, the app asks the",
                "   user to approve each move."]
        for line, titles in moves:
            step += ["   " + line] + ["       " + t for t in titles]
        if pins:
            step += ["   move_sessions unpins each pinned session it moves.  Pin these again:"]
            step += ["   " + line for line in pins]
        steps.append(step)
    steps.append(["Call list_sections again.  For each section above whose group_by or",
                  "   sort_by differs from what list_sections reports, call set_view with",
                  "   its section_id and the values above that differ."])
    steps.append(["Call list_sections again.  The sidebar tools cannot make the changes",
                  "   below.  Ask the user to make them in the sidebar, by dragging sections",
                  "   and from each section's menu:",
                  "   - Put the sections in the order above, if list_sections reports them",
                  "     in a different order relative to each other.",
                  "   - Make ascending, collapsed and shown what they are above, for each",
                  "     section where list_sections reports a different value."] +
                 ["   - " + line for line in by_hand])
    steps.append(["Tell the user what changed, and what is left for them to do."])
    for i, step in enumerate(steps, 1):
        print()
        print("%d. %s" % (i, step[0]))
        for line in step[1:]:
            print(line)


if __name__ == "__main__":
    main()
