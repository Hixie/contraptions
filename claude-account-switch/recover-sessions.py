#!/usr/bin/env python3
# Rebuild the Claude desktop app's session entries that the app failed to
# save.
#
# The app keeps the sidebar's list of sessions as one JSON file per session
# in a directory named after the account and organization it is logged in as,
# and rewrites a session's file whenever the session changes.  It refuses to
# write through a symbolic link at that directory: every save then fails, the
# app logs
#
#   Failed to save session local_<id>: ENOTDIR: not a directory, open '<dir>'
#
# and the change lives only in memory until the app quits.  A session started
# in that state never reaches the disk, and a change to an older session, such
# as archiving it, is lost.
#
# The transcripts in ~/.claude/projects/<directory>/<id>.jsonl are written by
# the command line tool, not by the app, and are complete.  This script
# replays the app's log from the launch that made the first failed save,
# reads the transcripts, and writes what the app would have written: a file
# for each session that has none, and the lost fields of each session whose
# file is out of date.  A field that the app changed and saved after the last
# failed save of that session keeps its saved value.  Linked pull requests
# are read from GitHub with the gh command.
#
#   recover-sessions.py <dir>            report what would be written
#   recover-sessions.py <dir> --apply    write it
#   recover-sessions.py <dir> --verify   rebuild sessions whose files were
#                                        saved, and compare with those files
#
# <dir> is the session directory the app is using now.  account-switch.sh
# works it out and runs this script as "account-switch.sh recover", and
# checks that the app is not running before passing --apply.

import argparse
import datetime
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time

LOGS = os.path.expanduser("~/Library/Logs/Claude")
PROJECTS = os.path.expanduser("~/.claude/projects")

L = r"(local_[0-9a-f-]{36})"
U = r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
LINE = re.compile(r"(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d) \[\w+\] (.*)")
LAUNCH = "] Starting app {"
PATTERNS = [
    ("failed", re.compile(
        rf"Failed to save session {L}: ENOTDIR: not a directory, open '[^']*/claude-code-sessions/")),
    ("modeFailed", re.compile(rf"Failed to set permission mode for session {L}")),
    ("start", re.compile(rf"Starting local session {L} in (.+)$")),
    ("forkParent", re.compile(rf"LocalSessions\.forkSession: parentSessionId={L}")),
    ("chdirQueued", re.compile(rf"change_directory queued for {L}: (.+)$")),
    ("chdir", re.compile(rf"pending cwd apply for {L}: ok")),
    ("changeCwd", re.compile(rf"LocalSessions\.changeCwd: sessionId={L}, cwd=(.+), trustAccepted=")),
    ("fork", re.compile(rf"\[CCD fork-timing\] {L} ")),
    ("spawn", re.compile(
        rf"Spawned-task started → parent {L}: (task_\w+) \(local, child {L}\)")),
    ("spawnEnd", re.compile(
        rf"Spawned-task ended → parent {L}: (task_\w+) \(local, child {L}\)")),
    ("cli", re.compile(rf"Mapping internal session {L} to CLI session {U}")),
    ("clear", re.compile(
        rf"clearStaleResumeHandle session={L} reason=clearSession "
        rf"dropping cliSessionId={U}")),
    ("archive", re.compile(rf"LocalSessions\.archive: sessionId={L}")),
    ("unarchive", re.compile(rf"LocalSessions\.unarchive: sessionId={L}")),
    ("delete", re.compile(rf"(?:LocalSessions\.delete: sessionId=|Deleted session ){L}")),
    ("update", re.compile(rf"Updated session {L}: \{{ (.*) \}}")),
    ("mode", re.compile(rf"LocalSessions\.setPermissionMode: sessionId={L}, mode=(\w+)")),
    ("focus", re.compile(rf"LocalSessions\.setFocusedSession: sessionId={L}")),
    ("bind", re.compile(rf"\[GitHubPrManager\] Bound (\w+) #(\d+) to session {L}")),
    ("dismissPr", re.compile(
        rf"LocalSessions\.dismissBoundPr: sessionId={L}, repo=(\S+), prNumber=(\d+)")),
]
PR_URL = re.compile(r"https://github\.com/[\w.-]+/[\w.-]+/pull/\d+")
# One "key: value" pair of an "Updated session" line, as Node prints it.
UPDATE_FIELD = re.compile(r"(\w+): ('(?:[^'\\]|\\.)*'|true|false|-?\d+)")

# Fields a new session file takes from the newest existing one; they describe
# the account's connectors and settings rather than the session.
TEMPLATE_FIELDS = ("remoteMcpServersConfig", "enabledMcpTools",
                   "chromePermissionMode", "classifierSummaryEnabled")
FIELD_ORDER = ("sessionId", "cliSessionId", "priorCliSessionIds", "cwd",
               "originCwd", "lastFocusedAt", "createdAt", "lastActivityAt",
               "model", "effort", "isArchived", "isStarred", "title",
               "titleSource", "permissionMode", "forkedFromSessionId",
               "spawnedFrom", "spawnedFromEndNotified", "prs")
COMPARED = FIELD_ORDER[1:]
DELETED = "(deleted)"


def ms(stamp):
    return int(time.mktime(time.strptime(stamp, "%Y-%m-%d %H:%M:%S")) * 1000)


def iso_ms(stamp):
    return int(datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
               .timestamp() * 1000)


def log_files():
    # main.log is the newest; mainN.log grows older as N grows.
    ages = {}
    for path in glob.glob(os.path.join(LOGS, "main*.log")):
        m = re.fullmatch(r"main(\d*)\.log", os.path.basename(path))
        if m:
            ages[path] = int(m.group(1) or 0)
    return sorted(ages, key=ages.get, reverse=True)


class Log:
    """What the app's log records about sessions: for each session id, its
    events oldest first as (time, kind, data), and the time the session is
    first mentioned; the time of each launch of the app; and the time of the
    oldest line the log still holds."""

    def __init__(self):
        self.events = {}
        self.first_seen = {}
        self.launches = []
        self.start = None
        self.fork_parent = None
        self.moving = {}
        for path in log_files():
            with open(path, errors="replace") as f:
                for raw in f:
                    self.read(raw)

    def read(self, raw):
        m = LINE.match(raw)
        if not m:
            return
        t, text = ms(m.group(1)), m.group(2).rstrip("\n")
        self.start = self.start or t
        if LAUNCH in raw:
            self.launches.append(t)
        if "local_" not in text:
            return
        for sid in re.findall(L, text):
            self.first_seen.setdefault(sid, t)
        for kind, pattern in PATTERNS:
            p = pattern.search(text)
            if p:
                self.record(t, kind, p)
                return

    def record(self, t, kind, p):
        if kind == "forkParent":
            self.fork_parent = p.group(1)
            return
        if kind == "chdirQueued":
            self.moving[p.group(1)] = p.group(2)
            return
        if kind == "modeFailed":
            # The app logs a request to change the mode, then whether it failed.
            requests = self.events.get(p.group(1), [])
            for i in range(len(requests) - 1, -1, -1):
                if requests[i][1] == "mode":
                    del requests[i]
                    break
            return
        if kind in ("spawn", "spawnEnd", "bind"):
            sid, data = p.group(3), (p.group(1), p.group(2))
        elif kind == "dismissPr":
            sid, data = p.group(1), (p.group(2), p.group(3))
        elif kind == "fork":
            sid, data = p.group(1), self.fork_parent
            self.fork_parent = None
        elif kind == "chdir":
            sid, data = p.group(1), self.moving.pop(p.group(1), None)
            if data is None:
                return
        elif kind == "update":
            sid = p.group(1)
            data = [(k, json.loads('"%s"' % v[1:-1].replace('"', '\\"'))
                     if v.startswith("'") else json.loads(v))
                    for k, v in UPDATE_FIELD.findall(p.group(2))]
        else:
            sid = p.group(1)
            data = p.group(2) if p.re.groups > 1 else None
        self.events.setdefault(sid, []).append((t, kind, data))


class Transcript:
    """What a command line transcript records about its session."""

    def __init__(self, path):
        self.path = path
        self.dir = os.path.basename(os.path.dirname(path))
        self.first_at = self.last_at = None
        # The latest model, effort and permission mode, each as (time, value).
        self.model = self.effort = self.mode = (0, None)
        self.cwds = []
        # Each pull request URL the transcript mentions, in order.
        self.pr_urls = []
        # Titles the command line tool recorded, which after a restart can
        # repeat an older title, and the titles the session gave itself, with
        # the time of each.  A transcript that continues another one starts
        # with a copy of it.
        self.custom_titles = []
        self.tool_titles = []
        self.spawn_titles = {}
        title_uses = {}
        spawn_uses = {}
        with open(path, errors="replace") as f:
            for line in f:
                self.pr_urls.extend(PR_URL.findall(line))
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(e, dict):
                    continue
                kind = e.get("type")
                at = iso_ms(e["timestamp"]) if e.get("timestamp") else 0
                if e.get("cwd") and e["cwd"] not in self.cwds[-1:]:
                    self.cwds.append(e["cwd"])
                if e.get("permissionMode"):
                    self.mode = (at, e["permissionMode"])
                if kind == "custom-title" and e.get("customTitle"):
                    self.custom_titles.append(e["customTitle"])
                if kind in ("user", "assistant") and at:
                    self.first_at = self.first_at or at
                    self.last_at = at
                message = e.get("message") if isinstance(e.get("message"), dict) else {}
                # The command line tool writes its own notices, such as a
                # usage limit, as replies from the model "<synthetic>".
                if kind == "assistant" and message.get("model") not in (None, "<synthetic>"):
                    self.model = (at, message["model"])
                    if e.get("effort"):
                        self.effort = (at, e["effort"])
                content = message.get("content")
                for c in content if isinstance(content, list) else []:
                    if not isinstance(c, dict):
                        continue
                    if c.get("type") == "tool_use" and isinstance(c.get("input"), dict):
                        name = c.get("name", "")
                        if name.endswith("__set_session_title") and \
                                c["input"].get("session_id", "self") == "self":
                            title_uses[c.get("id")] = c["input"].get("title")
                        elif name.endswith("__spawn_task"):
                            spawn_uses[c.get("id")] = c["input"].get("title")
                    elif c.get("type") == "tool_result" and not c.get("is_error"):
                        use = c.get("tool_use_id")
                        if title_uses.get(use) and e.get("timestamp"):
                            self.tool_titles.append((iso_ms(e["timestamp"]), title_uses[use]))
                        elif use in spawn_uses:
                            m = re.search(r"task_id: (task_\w+)", json.dumps(c.get("content")))
                            if m:
                                self.spawn_titles[m.group(1)] = spawn_uses[use]


def mangle(cwd):
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


class Recovery:
    def __init__(self, directory):
        self.dir = directory
        self.log = Log()
        self.events, self.first_seen = self.log.events, self.log.first_seen
        self.paths = {os.path.basename(p)[:-6]: p
                      for p in glob.glob(os.path.join(PROJECTS, "*", "*.jsonl"))}
        self.scanned = {}
        self.pulls = {}
        self.warnings = []
        self.files = {}
        for name in os.listdir(directory):
            if name.startswith("local_") and name.endswith(".json"):
                try:
                    with open(os.path.join(directory, name)) as f:
                        self.files[name[:-5]] = json.load(f)
                except ValueError as e:
                    # The app skips such files too.
                    self.warnings.append("skipping unreadable %s: %s" % (name, e))
        newest = max(self.files.values(), key=lambda d: d.get("lastActivityAt", 0),
                     default={})
        self.template = {k: newest[k] for k in TEMPLATE_FIELDS if k in newest}
        self.template.update(isArchived=False, alwaysAllowedReasons=[],
                             sessionPermissionUpdates=[], spawnSeed={})
        self.built = {}

    def transcript(self, cli):
        if cli not in self.scanned:
            self.scanned[cli] = Transcript(self.paths[cli]) if cli in self.paths else None
        return self.scanned[cli]

    def transcripts(self, entry):
        """The session's transcripts, newest first."""
        ids = [entry.get("cliSessionId")] + list(reversed(entry.get("priorCliSessionIds", [])))
        return [t for t in map(self.transcript, filter(None, ids)) if t]

    def changes(self, sid, event):
        """The (field, value) pairs one logged event sets."""
        t, kind, data = event
        if kind == "start":
            return [("cwd", data)]
        if kind in ("chdir", "changeCwd"):
            return [("cwd", data), ("originCwd", data)]
        if kind == "fork":
            return [("forkedFromSessionId", data)] if data else []
        if kind == "spawn":
            parent, task = data
            spawned = {"sessionId": parent, "taskId": task}
            title = self.spawn_title(parent, task)
            if title:
                spawned["title"] = title
            return [("spawnedFrom", spawned)]
        if kind == "spawnEnd":
            return [("spawnedFromEndNotified", True)]
        if kind == "cli":
            return [("cliSessionId", data)]
        if kind == "clear":
            return [("priorCliSessionIds", data)]
        if kind in ("archive", "unarchive"):
            return [("isArchived", kind == "archive")]
        if kind == "delete":
            return [(DELETED, True)]
        if kind == "update":
            # The log shows the session's title source next to every change,
            # but only a change to the title sets it.
            titled = any(field == "title" for field, _ in data)
            return [(f, v) for f, v in data if titled or f != "titleSource"]
        if kind == "mode":
            return [("permissionMode", data)]
        if kind == "focus":
            return [("lastFocusedAt", t)]
        if kind == "bind":
            return [("prs", ("bind", data[0], int(data[1])))]
        if kind == "dismissPr":
            return [("prs", ("dismiss", data[0], int(data[1])))]
        return []

    def spawn_title(self, parent, task):
        entry = self.built.get(parent) or self.files.get(parent) or {}
        clis = [c for _, k, c in self.events.get(parent, []) if k in ("cli", "clear")]
        for cli in [entry.get("cliSessionId")] + entry.get("priorCliSessionIds", []) + clis:
            tr = self.transcript(cli) if cli else None
            if tr and task in tr.spawn_titles:
                return tr.spawn_titles[task]
        return None

    def title(self, entry, source):
        """The session's title, given the source of its latest title change,
        or None when the log records no change."""
        transcripts = self.transcripts(entry)
        if source == "tool":
            titles = [t for tr in transcripts for t in tr.tool_titles]
            if titles:
                return max(titles)[1]
        latest = next((tr.custom_titles[-1] for tr in transcripts if tr.custom_titles), None)
        if source is None and "forkedFromSessionId" in entry:
            # The app names a fork after its parent until it is renamed.
            forks = [t for tr in transcripts for t in tr.custom_titles if t.endswith(" (fork)")]
            return forks[0] if forks else latest and latest + " (fork)"
        return latest

    def pull(self, entry, provider, number, problems):
        """The app's record of pull request #number, bound to the session:
        the URL the session's transcripts last mention for it, or failing
        that, the repository of the session's working directory, and the
        pull request's branches and state from GitHub."""
        url = next((u for tr in self.transcripts(entry) for u in reversed(tr.pr_urls)
                    if u.endswith("/pull/%d" % number)), None)
        key = url or (entry.get("cwd"), number)
        if key not in self.pulls:
            cwd = entry.get("cwd") if url is None and os.path.isdir(entry.get("cwd") or "") else None
            try:
                out = subprocess.run(
                    ["gh", "pr", "view", url or str(number), "--json",
                     "url,headRefName,baseRefName,state"],
                    cwd=cwd, capture_output=True, text=True)
            except OSError as e:
                out = subprocess.CompletedProcess([], 1, "", str(e))
            if out.returncode:
                problems.append("could not read pull request #%d: %s"
                                % (number, out.stderr.strip() or "gh failed"))
                self.pulls[key] = None
            else:
                pr = json.loads(out.stdout)
                self.pulls[key] = {
                    "prNumber": number, "repo": "/".join(pr["url"].split("/")[3:5]),
                    "host": "github.com", "provider": provider, "url": pr["url"],
                    "branch": pr["headRefName"], "baseRef": pr["baseRefName"],
                    "state": pr["state"]}
        return self.pulls[key]

    def apply_pull(self, entry, change, problems):
        action, where, number = change
        prs = entry.setdefault("prs", [])
        bound = next((pr for pr in prs if pr.get("prNumber") == number), None)
        if action == "dismiss":
            if bound:
                bound["dismissed"] = True
        elif bound is None:
            if where != "github":
                problems.append("cannot read pull request #%d from %s" % (number, where))
                return
            pr = self.pull(entry, where, number, problems)
            if pr:
                prs.append(dict(pr))

    @staticmethod
    def add_prior(entry, cli):
        entry["priorCliSessionIds"] = [
            c for c in entry.get("priorCliSessionIds", []) if c != cli] + [cli]

    def rebuild(self, sid, base, lost, saved, problems):
        """The entry for a session: base, with the lost events applied except
        where a saved event changed the same field, then brought up to date
        with the session's transcripts."""
        entry = json.loads(json.dumps(base))
        entry["sessionId"] = sid
        kept = {field for event in saved for field, _ in self.changes(sid, event)}
        if DELETED in kept:
            entry[DELETED] = True
            return entry
        titled = False
        source = None
        set_at = {}
        pulls = []
        for event in lost:
            for field, value in self.changes(sid, event):
                if field == "prs":
                    # Bindings made later were saved alongside these.
                    pulls.append(value)
                    continue
                if field in kept:
                    continue
                set_at[field] = event[0]
                if field == "cliSessionId" and entry.get(field) not in (None, value):
                    self.add_prior(entry, entry[field])
                if field == "priorCliSessionIds":
                    self.add_prior(entry, value)
                elif field == "lastFocusedAt":
                    entry[field] = max(entry.get(field, 0), value)
                elif field == "title":
                    titled = True
                elif field == "titleSource":
                    source = value
                    entry[field] = value
                elif field == "spawnedFrom" and \
                        entry.get(field, {}).get("taskId") == value["taskId"]:
                    entry[field] = {**entry[field], **value}
                else:
                    entry[field] = value
        if DELETED in entry:
            return entry
        if "priorCliSessionIds" in entry:
            entry["priorCliSessionIds"] = [c for c in entry["priorCliSessionIds"]
                                           if c != entry.get("cliSessionId")]
            if not entry["priorCliSessionIds"]:
                del entry["priorCliSessionIds"]
        if titled or "title" not in entry:
            title = self.title(entry, source if titled else None)
            if title:
                entry["title"] = title
        # The transcript's latest model, effort and mode stand, unless a
        # logged change to the same field is newer.
        current = self.transcript(entry.get("cliSessionId"))
        if current:
            for field, (at, value) in (("model", current.model), ("effort", current.effort),
                                       ("permissionMode", current.mode)):
                if value and field not in kept and at > set_at.get(field, 0):
                    entry[field] = value
            if current.last_at:
                entry["lastActivityAt"] = max(entry.get("lastActivityAt", 0), current.last_at)
        for change in pulls:
            self.apply_pull(entry, change, problems)
        if not entry.get("prs"):
            entry.pop("prs", None)
        return entry

    def new_entry(self, sid, events):
        """The fields a new session starts with, before any event is applied."""
        entry = dict(self.template)
        entry["createdAt"] = self.first_seen[sid]
        starts = [data for _, kind, data in events if kind == "start"]
        if starts:
            entry["originCwd"] = entry["cwd"] = starts[0]
        return entry

    def settle_cwd(self, entry, problems):
        """Make sure the session names the working directory that holds its
        transcript, which is where the command line tool looks for it."""
        current = self.transcript(entry.get("cliSessionId"))
        if not current:
            problems.append("no transcript for CLI session %s" % entry.get("cliSessionId"))
        elif entry.get("cwd") is None or mangle(entry["cwd"]) != current.dir:
            matching = [c for c in current.cwds if mangle(c) == current.dir]
            if matching:
                entry["cwd"] = entry["originCwd"] = matching[-1]
            else:
                problems.append("transcript is not under the directory for %s"
                                % entry.get("cwd"))
        entry.setdefault("originCwd", entry.get("cwd"))

    def plan(self):
        """(sid, old entry or None, new entry, problems) for every session
        with a failed save."""
        failed = {sid: [t for t, k, _ in evs if k == "failed"]
                  for sid, evs in self.events.items()}
        failed = {sid: ts for sid, ts in failed.items() if ts}
        if not failed:
            return []
        # The app checks its directory once per launch, so every change from
        # the launch that made the first failed save onwards was lost.
        first = min(min(ts) for ts in failed.values())
        launches = [t for t in self.log.launches if t <= first]
        if launches:
            start = launches[-1]
        else:
            start = self.log.start
            self.warnings.append(
                "the log no longer reaches back to the launch before the first "
                "failed save; changes made before %s may be missing"
                % time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(start / 1000)))
        # Parents first, so that a fork or spawned session can use its
        # parent's rebuilt entry.
        order = sorted(failed, key=lambda sid: self.events[sid][0][0])
        result = []
        for sid in order:
            evs = self.events[sid]
            last = max(failed[sid])
            lost = [e for e in evs if start <= e[0] <= last]
            saved = [e for e in evs if e[0] > last]
            old = self.files.get(sid)
            base = old if old is not None else self.new_entry(sid, lost)
            problems = []
            entry = self.rebuild(sid, base, lost, saved, problems)
            if DELETED in entry:
                problems.append("deleted in the app")
            else:
                self.settle_cwd(entry, problems)
                entry = {**{k: entry[k] for k in FIELD_ORDER if k in entry}, **entry}
            self.built[sid] = entry
            result.append((sid, old, entry, problems))
        return result

    def verify(self):
        """Check the method on sessions whose files were saved.  First,
        rebuild each one whose whole life is in the log as if none of it had
        been saved, and count the fields that match its file.  Second, replay
        each one's logged history onto its own file, which should change
        nothing."""
        created = ("start", "fork", "spawn")
        tally = {}
        examples = {}
        count = 0
        changed = {}
        for sid, evs in sorted(self.events.items(), key=lambda kv: kv[1][0][0]):
            if sid not in self.files or any(k == "failed" for _, k, _ in evs):
                continue
            actual = self.files[sid]
            replayed = self.rebuild(sid, actual, evs, [], [])
            self.settle_cwd(replayed, [])
            for field in sorted(set(actual) | set(replayed)):
                if field != DELETED and actual.get(field) != replayed.get(field):
                    changed.setdefault(field, []).append((sid, actual.get(field),
                                                          replayed.get(field)))
            if evs[0][1] not in created or evs[0][0] > self.first_seen[sid]:
                continue
            count += 1
            entry = self.rebuild(sid, self.new_entry(sid, evs), evs, [], [])
            self.settle_cwd(entry, [])
            self.built[sid] = entry
            for field in COMPARED:
                if field == "prs":
                    continue
                a, b = actual.get(field), entry.get(field)
                if field == "spawnedFrom" and a:
                    a = {k: v for k, v in a.items() if k != "pendingPop"}
                if field in ("createdAt", "lastFocusedAt") and a and b:
                    same = abs(a - b) <= 3000
                elif field == "lastActivityAt" and a and b:
                    same = abs(a - b) <= 60000
                else:
                    same = a == b
                tally.setdefault(field, [0, 0])[0 if same else 1] += 1
                if not same:
                    examples.setdefault(field, []).append((sid, a, b))
        print("Rebuilt %d sessions whose files were saved, as if they had not been." % count)
        print("%-24s %6s %6s" % ("field", "match", "differ"))
        for field in COMPARED:
            if field in tally:
                same, differ = tally[field]
                print("%-24s %6d %6d" % (field, same, differ))
        show("%s differs", examples)
        replays = sum(1 for sid, evs in self.events.items()
                      if sid in self.files and not any(k == "failed" for _, k, _ in evs))
        print("\nReplayed the logged history of %d saved sessions onto their files;" % replays)
        print("%d fields changed in %d sessions." % (
            sum(len(v) for v in changed.values()),
            len({sid for rows in changed.values() for sid, _, _ in rows})))
        show("replaying changed %s", changed)


def show(heading, examples):
    for field, rows in examples.items():
        print("\n%s, for example:" % (heading % field))
        for sid, a, b in rows[:4]:
            print("  %s\n    file:    %s\n    rebuilt: %s" % (
                sid, json.dumps(a, ensure_ascii=False)[:150],
                json.dumps(b, ensure_ascii=False)[:150]))


def write_json(directory, name, value):
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".recover-")
    with os.fdopen(fd, "w") as f:
        json.dump(value, f, ensure_ascii=False, separators=(",", ":"))
    os.chmod(tmp, 0o600)
    os.replace(tmp, os.path.join(directory, name))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true")
    mode.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if os.path.islink(args.directory) or not os.path.isdir(args.directory):
        sys.exit("%s is not a directory the app can write to" % args.directory)
    recovery = Recovery(args.directory)
    if args.verify:
        recovery.verify()
        return

    plan = recovery.plan()
    for warning in recovery.warnings:
        print("warning: %s" % warning)
    creates = updates = 0
    backup = None
    for sid, old, entry, problems in plan:
        if DELETED in entry:
            print("skip    %s  (%s)" % (sid, "; ".join(problems)))
            continue
        changed = [k for k in entry if old is None or old.get(k) != entry[k]]
        if old is not None and not changed:
            continue
        state = "archived" if entry.get("isArchived") else "active"
        verb = "create" if old is None else "update"
        print("%s  %s  %-8s %s" % (verb, sid, state, entry.get("title", "(untitled)")))
        if old is not None:
            for k in changed:
                print("          %s: %s -> %s" % (
                    k, json.dumps(old.get(k), ensure_ascii=False)[:70],
                    json.dumps(entry[k], ensure_ascii=False)[:70]))
        for problem in problems:
            print("          warning: %s" % problem)
        if old is None:
            creates += 1
        else:
            updates += 1
        if args.apply:
            if old is not None:
                if backup is None:
                    backup = tempfile.mkdtemp(dir=os.path.dirname(args.directory),
                                              prefix=os.path.basename(args.directory)
                                              + ".before-recover.")
                shutil.copy2(os.path.join(args.directory, sid + ".json"), backup)
            write_json(args.directory, sid + ".json", entry)
    print("\n%s %d new session files and %d updated ones." % (
        "Wrote" if args.apply else "Would write", creates, updates))
    if backup:
        print("The files as they were before are in %s." % backup)
    if not args.apply and (creates or updates):
        print("Quit the app completely, then run this again with --apply.")


if __name__ == "__main__":
    main()
