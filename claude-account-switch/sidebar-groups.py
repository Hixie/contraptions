#!/usr/bin/env python3
# List which sessions each account has filed in the Claude desktop app's
# sidebar groups.
#
# The Code tab's sidebar groups are not in the session directory, so moving
# the session list does not carry them.  The page that draws the sidebar keeps
# them, keyed by "<account>/<org>", and syncs them to the account on
# claude.ai.  It also keeps a record of which session is filed in which
# group, which the app copies into claude_desktop_config.json, under
# preferences.epitaxyPrefs["dframe-group-scopes"].  This reads that record and
# changes nothing.  A group with nothing filed in it is not in the record.
#
#   sidebar-groups.py <support-dir>
#       every account's filings
#   sidebar-groups.py <support-dir> <from account/org> <to account/org>
#       the filings of the first account that the second lacks, leaving out
#       sessions that no longer exist here and archived sessions, which the
#       app's sidebar does not show in groups or let anyone file there;
#       nothing if there are none

import glob
import json
import os
import re
import sys

UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def filings(support):
    """For each account/org, a list of (group name, [member, ...]) in the
    order the sidebar shows them.  A member is "code:<session id>" for a Code
    session, or another kind of item, such as "chat:<id>"."""
    path = os.path.join(support, "claude_desktop_config.json")
    try:
        with open(path) as f:
            config = json.load(f)
    except (OSError, ValueError) as e:
        sys.exit("could not read %s: %s" % (path, e))
    # No record means nothing has been filed in a group on this machine.
    scopes = config
    for key in ("preferences", "epitaxyPrefs", "dframe-group-scopes"):
        scopes = scopes.get(key, {}) if isinstance(scopes, dict) else None
    if not isinstance(scopes, dict):
        sys.exit("%s does not record sidebar groups in the form this script expects" % path)
    result = {}
    for scope, stored in scopes.items():
        try:
            groups = []
            for group in stored.get("groups", []):
                members = [m for m, g in stored.get("assignments", {}).items() if g == group["id"]]
                order = stored.get("order", {}).get(group["id"], [])
                members.sort(key=lambda m: order.index(m) if m in order else len(order))
                if members:
                    groups.append((group["name"], members))
        except (AttributeError, KeyError, TypeError):
            sys.exit("%s does not record the sidebar groups of %s in the form this "
                     "script expects" % (path, scope))
        result[scope] = groups
    return result


def sessions(support):
    """The contents of each Code session's file, from the session directories
    themselves, not the backups next to them."""
    result = {}
    for path in glob.glob(os.path.join(support, "claude-code-sessions", "*", "*", "local_*.json")):
        org = os.path.dirname(path)
        if not (UUID.fullmatch(os.path.basename(org))
                and UUID.fullmatch(os.path.basename(os.path.dirname(org)))):
            continue
        try:
            with open(path) as f:
                session = json.load(f)
        except ValueError:
            continue
        result["code:" + os.path.basename(path)[:-5]] = session
    return result


def missing(scopes, known, source, target):
    """The filings of account/org source that target lacks, as (group name,
    [member, ...]) in sidebar order.  Sessions that no longer exist here are
    left out, and so are archived sessions, which the app's sidebar does not
    show in groups or let anyone file there."""
    have = {(name, m) for name, members in scopes.get(target, []) for m in members}
    result = []
    for name, members in scopes.get(source, []):
        members = [m for m in members if (name, m) not in have
                   and (not m.startswith("code:") or (m in known and not known[m].get("isArchived")))]
        if members:
            result.append((name, members))
    return result


def describe(member, known):
    kind, _, ident = member.partition(":")
    if kind == "code":
        if member not in known:
            return "(no session file)  [%s]" % ident
        session = known[member]
        return "%s%s  [%s]" % (session.get("title") or "(untitled)",
                               " (archived)" if session.get("isArchived") else "", ident)
    return "%s item  [%s]" % (kind, ident)


def main():
    if len(sys.argv) not in (2, 4):
        sys.exit("usage: sidebar-groups.py <support-dir> [<from account/org> <to account/org>]")
    support = sys.argv[1]
    scopes = filings(support)
    known = sessions(support)
    if len(sys.argv) == 4:
        for name, members in missing(scopes, known, sys.argv[2], sys.argv[3]):
            print(name)
            for member in members:
                print("  " + describe(member, known))
        return
    for scope, groups in scopes.items():
        print("%s:" % scope)
        if not groups:
            print("  nothing filed in groups")
        for name, members in groups:
            print("  %s" % name)
            for member in members:
                print("    " + describe(member, known))


if __name__ == "__main__":
    main()
