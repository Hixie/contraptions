#!/usr/bin/env python3
# List how each account lays out the Claude desktop app's Code sidebar: the
# order of its sections, how each is grouped and sorted, which are shown or
# collapsed, and which details their rows show.
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
GROUPINGS = {"date": "date", "project": "folder", "state": "state", "none": "nothing"}
SORTS = {"recency": "last activity", "created": "date created", "alpha": "name"}
DETAILS = {"metadata": "metadata", "emptyGroups": "empty groups", "sideChats": "side chats"}
# A section's settings, by the key its record holds each under and the name
# the app's list_sections tool gives it.
FIELDS = (("shown", "shown"), ("collapsed", "collapsed"), ("groupBy", "group_by"),
          ("sortBy", "sort_by"), ("ascending", "ascending"))


def sections(support):
    """For each account/org, the records of its built-in sections and custom
    groups in sidebar order."""
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
            result[scope] = sorted((s for s in stored["sections"]
                                    if s["kind"] in BUILT_IN or s["kind"] == "manual"),
                                   key=lambda s: s["order"])
            # A custom group's record names it.
            for section in result[scope]:
                if section["kind"] == "manual":
                    section["name"]
        except (KeyError, TypeError):
            sys.exit("%s does not record the sidebar layout of %s in the form this "
                     "script expects" % (path, scope))
    return result


def layouts(support):
    """For each account/org, the built-in sections in sidebar order, each as
    (name, [(setting, value), ...])."""
    return {scope: [(BUILT_IN[s["kind"]], settings(s)) for s in records if s["kind"] in BUILT_IN]
            for scope, records in sections(support).items()}


def fields(section):
    """A section's settings, under the names list_sections gives them.  A
    setting the record does not hold is left out, except grouping:
    list_sections reports a section with no recorded grouping as grouped by
    nothing, unless it is Sessions, whose grouping is then the whole list's."""
    result = {name: section[key] for key, name in FIELDS if key in section}
    if section["kind"] != "sessions":
        result.setdefault("group_by", "none")
    return result


def settings(section):
    """A section's settings in the words its menu uses."""
    values = fields(section)
    result = []
    if "shown" in values:
        result.append(("shown", "yes" if values["shown"] else "no"))
    if "collapsed" in values:
        result.append(("collapsed", "yes" if values["collapsed"] else "no"))
    if "group_by" in values:
        result.append(("grouped by", GROUPINGS.get(values["group_by"], values["group_by"])))
    if "sort_by" in values:
        result.append(("sorted by", SORTS.get(values["sort_by"], values["sort_by"])))
    if "ascending" in values:
        result.append(("order", "ascending" if values["ascending"] else "descending"))
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
