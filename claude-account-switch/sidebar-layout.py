#!/usr/bin/env python3
# List how each account lays out the Claude desktop app's Code sidebar: the
# order of its sections, how each is sorted, which are shown or collapsed,
# and which details their rows show.
#
# The layout is kept per account and organization, not in the session
# directory, so moving the session list does not carry it, and the sidebar of
# an account the list moves to keeps that account's own layout.  The page that
# draws the sidebar keeps it in its own store, keyed by "<account>/<org>", and
# the app copies it into claude_desktop_config.json, under
# preferences.epitaxyPrefs["dframe-code-sections"].  The page reads that copy
# only to lay out an account it has not laid out before, so editing it does not
# change the sidebar of an account already used here; the settings are made
# again in the sidebar itself, or with the app's sidebar tools.  This reads the
# copy and changes nothing.  Custom groups appear as sections too, and are left
# to sidebar-groups.py.
#
#   sidebar-layout.py <support-dir>
#       every account's layout
#   sidebar-layout.py <support-dir> <from account/org> <to account/org>
#       the settings of the first account that the second does not share;
#       nothing if there are none

import json
import os
import sys

BUILT_IN = {"pinned": "Pinned", "routines": "Routines", "sessions": "Sessions"}
SORTS = {"recency": "last activity", "created": "date created", "alpha": "name"}
DETAILS = {"metadata": "metadata", "emptyGroups": "empty groups", "sideChats": "side chats"}


def layouts(support):
    """For each account/org, the built-in sections in sidebar order, each as
    (name, [(setting, value), ...])."""
    path = os.path.join(support, "claude_desktop_config.json")
    try:
        with open(path) as f:
            config = json.load(f)
    except (OSError, ValueError) as e:
        sys.exit("could not read %s: %s" % (path, e))
    # No record means the sidebar has not been laid out on this machine.
    scopes = config
    for key in ("preferences", "epitaxyPrefs", "dframe-code-sections"):
        scopes = scopes.get(key, {}) if isinstance(scopes, dict) else None
    if not isinstance(scopes, dict):
        sys.exit("%s does not record the sidebar layout in the form this script expects" % path)
    result = {}
    for scope, stored in scopes.items():
        try:
            sections = sorted((s for s in stored["sections"] if s["kind"] in BUILT_IN),
                              key=lambda s: s["order"])
            result[scope] = [(BUILT_IN[s["kind"]], settings(s)) for s in sections]
        except (KeyError, TypeError):
            sys.exit("%s does not record the sidebar layout of %s in the form this "
                     "script expects" % (path, scope))
    return result


def settings(section):
    """A section's settings in the words its menu uses.  A setting the
    section's record does not hold is left out rather than guessed."""
    result = []
    if "shown" in section:
        result.append(("shown", "yes" if section["shown"] else "no"))
    if "collapsed" in section:
        result.append(("collapsed", "yes" if section["collapsed"] else "no"))
    if "sortBy" in section:
        result.append(("sorted by", SORTS.get(section["sortBy"], section["sortBy"])))
    if "ascending" in section:
        result.append(("order", "ascending" if section["ascending"] else "descending"))
    for key, name in DETAILS.items():
        if key in section.get("show", {}):
            result.append(("shows " + name, "yes" if section["show"][key] else "no"))
    return result


def main():
    if len(sys.argv) not in (2, 4):
        sys.exit("usage: sidebar-layout.py <support-dir> [<from account/org> <to account/org>]")
    scopes = layouts(sys.argv[1])
    if len(sys.argv) == 4:
        source, target = scopes.get(sys.argv[2], []), scopes.get(sys.argv[3])
        if target is None:
            for name, values in source:
                print("%s: %s" % (name, ", ".join("%s %s" % v for v in values)))
            return
        order = [name for name, _ in source]
        if order != [name for name, _ in target][:len(order)]:
            print("Section order: %s" % ", ".join(order))
        have = dict(target)
        for name, values in source:
            theirs = dict(have.get(name, []))
            for setting, value in values:
                if theirs.get(setting) != value:
                    print("%s: %s %s (this account: %s)" % (
                        name, setting, value, theirs.get(setting, "not set")))
        return
    for scope, sections in scopes.items():
        print("%s:" % scope)
        for name, values in sections:
            print("  %s: %s" % (name, ", ".join("%s %s" % v for v in values)))


if __name__ == "__main__":
    main()
