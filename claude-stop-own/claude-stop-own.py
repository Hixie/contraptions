#!/usr/bin/env python3
"""Stops processes that belong to the calling Claude Code session, and
refuses to stop any other process.

Usage: claude-stop-own.py [--tree] [-s|--signal NAME] PID ...

A process belongs to the caller when any of these holds:

- walking parent PIDs from it reaches $CLAUDE_PID;
- its working directory is inside the caller's own
  ~/dev/commonfabric/REPO/XX worktree, the one holding the caller's
  working directory (REPO/root and ~/dev/commonfabric never count);
- its command line or working directory contains the caller's
  scratchpad path, which ends in /$CLAUDE_CODE_SESSION_ID/scratchpad.

The command that runs this script, and every process above it, is never
signalled. With --tree, every descendant of each PID is judged the same
way.

$CLAUDE_PID, when set, must be the parent of the nearest process-group
leader among this command and its ancestors, and must not be PID 1, or
the command fails with status 2. Otherwise the exit status is 1 if any PID
was refused or could not be signalled, and 0 otherwise.
"""

import argparse
import os
import re
import signal
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple, Optional

WORKTREE_CODE = re.compile(r"[0-9A-Z]{1,2}")


class Process(NamedTuple):
    ppid: int
    pgid: int
    command: str


def snapshot():
    """Returns {pid: Process} for every process."""
    out = subprocess.run(["ps", "-Aww", "-o", "pid=,ppid=,pgid=,command="],
                         capture_output=True, text=True, check=True).stdout
    table = {}
    for line in out.splitlines():
        pid, ppid, pgid, command = (line.split(None, 3) + [""])[:4]
        table[int(pid)] = Process(int(ppid), int(pgid), command)
    return table


def working_directories(pids):
    """Returns {pid: cwd} for those of pids whose cwd lsof can report."""
    # lsof exits 1 when any PID has gone, and still reports the others.
    out = subprocess.run(
        ["lsof", "-a", "-d", "cwd", "-Fn", "-p", ",".join(map(str, pids))],
        stdout=subprocess.PIPE, text=True).stdout
    cwds = {}
    pid = None
    for line in out.splitlines():
        if line.startswith("p"):
            pid = int(line[1:])
        elif line.startswith("n") and pid is not None:
            cwds[pid] = line[1:]
    return cwds


def worktree_of(path):
    """Returns the ~/dev/commonfabric/REPO/XX directory holding path, or
    None when path is not inside one."""
    root = Path(os.path.realpath(Path.home() / "dev" / "commonfabric"))
    try:
        parts = Path(os.path.realpath(path)).relative_to(root).parts
    except ValueError:
        return None
    if len(parts) < 2 or not WORKTREE_CODE.fullmatch(parts[1]):
        return None
    return root / parts[0] / parts[1]


def ancestors(pid, table):
    """Yields the PIDs above pid, nearest first."""
    seen = {pid}
    while pid in table:
        pid = table[pid].ppid
        if pid in seen:
            return
        seen.add(pid)
        yield pid


def descendants(pid, children):
    stack = list(reversed(children.get(pid, [])))
    while stack:
        child = stack.pop()
        yield child
        stack.extend(reversed(children.get(child, [])))


class Caller(NamedTuple):
    claude_pid: Optional[int]
    worktree: Optional[Path]
    scratchpad_marker: Optional[str]


def ownership(pid, table, cwds, caller):
    """Returns the reason pid belongs to the caller, or None."""
    if caller.claude_pid is not None \
            and caller.claude_pid in ancestors(pid, table):
        return f"descends from CLAUDE_PID {caller.claude_pid}"
    cwd = cwds.get(pid)
    if cwd is not None and caller.worktree is not None \
            and worktree_of(cwd) == caller.worktree:
        return f"runs in {caller.worktree}"
    marker = caller.scratchpad_marker
    if marker is not None and marker in table[pid].command:
        return "command line names this session's scratchpad"
    if marker is not None and cwd is not None and marker in cwd:
        return "runs in this session's scratchpad"
    return None


def parse_signal(name):
    try:
        return signal.Signals["SIG" + name.upper().removeprefix("SIG")]
    except KeyError:
        raise argparse.ArgumentTypeError(f"unknown signal {name}")


def main(argv):
    parser = argparse.ArgumentParser(
        description="Stop processes that belong to this Claude Code "
                    "session; refuse all others.")
    parser.add_argument("--tree", action="store_true",
                        help="also stop every descendant of each PID")
    parser.add_argument("-s", "--signal", type=parse_signal,
                        default=signal.SIGTERM,
                        help="signal to send (default TERM)")
    parser.add_argument("pids", metavar="PID", type=int, nargs="+")
    args = parser.parse_args(argv)

    sys.stdout.reconfigure(line_buffering=True)
    table = snapshot()
    chain = [os.getpid(), *ancestors(os.getpid(), table)]
    protected = set(chain)
    # Claude Code starts each command's shell as the leader of a new
    # process group, so its own process is that leader's parent.
    owner = table[next(pid for pid in chain
                       if pid in table and table[pid].pgid == pid)].ppid
    claude_pid = os.environ.get("CLAUDE_PID")
    if claude_pid and (owner == 1 or claude_pid != str(owner)):
        parser.error(f"CLAUDE_PID {claude_pid} is not this command's "
                     "Claude Code process, which is the parent of the "
                     "nearest process-group leader among this command and "
                     "its ancestors, and is never PID 1")
    session_id = os.environ.get("CLAUDE_CODE_SESSION_ID")
    caller = Caller(
        claude_pid=int(claude_pid) if claude_pid else None,
        worktree=worktree_of(os.getcwd()),
        scratchpad_marker=f"/{session_id}/scratchpad" if session_id else None,
    )
    children = {}
    for child, process in table.items():
        children.setdefault(process.ppid, []).append(child)

    failed = False
    targets = []
    for pid in args.pids:
        if pid not in table:
            print(f"claude-stop-own: {pid}: no such process", file=sys.stderr)
            failed = True
            continue
        targets.append(pid)
        if args.tree:
            targets.extend(descendants(pid, children))
    targets = list(dict.fromkeys(targets))
    cwds = working_directories(targets) if targets else {}

    for pid in targets:
        reason = None if pid in protected \
            else ownership(pid, table, cwds, caller)
        if reason is None:
            failed = True
            whose = "is running this command" if pid in protected \
                else "is not this session's"
            print(f"claude-stop-own: refused {pid}, which {whose}\n"
                  f"  cwd: {cwds.get(pid, '(unknown)')}\n"
                  f"  command: {table[pid].command}", file=sys.stderr)
            continue
        try:
            os.kill(pid, args.signal)
        except ProcessLookupError:
            print(f"claude-stop-own: {pid} had already exited")
            continue
        except OSError as error:
            failed = True
            print(f"claude-stop-own: could not signal {pid}: {error.strerror}",
                  file=sys.stderr)
            continue
        print(f"claude-stop-own: sent {args.signal.name} to {pid} ({reason})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
