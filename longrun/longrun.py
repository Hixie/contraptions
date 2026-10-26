#!/usr/bin/env python3
"""Run a long command so that waiting for it takes one call, a stall is
reported once, and the command ends when whoever started it is gone.
See README.md."""

from __future__ import annotations

import argparse
import fcntl
import glob
import json
import os
import re
import select
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from typing import NamedTuple

HERE = os.path.dirname(os.path.realpath(__file__))
STALLED = 75  # EX_TEMPFAIL: the command is still running; wait again.
GRACE = 10.0  # Seconds between SIGTERM and SIGKILL when ending processes.
SAMPLED = 12  # At most this many processes are sampled for a stall report.
WIDTH = 300  # Longer lines are cut in summaries.
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b\n]*(\x07|\x1b\\)")


# Files and directories ------------------------------------------------------

def base_dir(scratchpad: str | None = None, create: bool = True) -> str:
    """The directory that holds runs: $LONGRUN_DIR, else the Claude Code
    session's scratchpad, else the temporary directory."""
    path = os.environ.get("LONGRUN_DIR")
    if not path and scratchpad:
        path = os.path.join(scratchpad, "longrun")
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    if not path and session:
        found = sorted(glob.glob(
            f"/private/tmp/claude-{os.getuid()}/*/{session}/scratchpad"))
        if found:
            path = os.path.join(found[0], "longrun")
    path = path or os.path.join(tempfile.gettempdir(),
                                f"longrun-{os.getuid()}")
    if create:
        os.makedirs(path, mode=0o700, exist_ok=True)
    if os.path.exists(path) and os.stat(path).st_uid != os.getuid():
        sys.exit(f"longrun: {path} belongs to another user")
    return path


def resolve(run: str) -> str:
    path = run if os.sep in run else os.path.join(base_dir(), run)
    if not os.path.isfile(os.path.join(path, "spec.json")):
        sys.exit(f"longrun: no run at {path}")
    return os.path.abspath(path)


def load(rundir: str, name: str) -> dict:
    try:
        with open(os.path.join(rundir, name)) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}


def save(rundir: str, name: str, value: dict) -> None:
    path = os.path.join(rundir, name)
    with open(path + ".new", "w") as f:
        json.dump(value, f, indent=1)
    os.replace(path + ".new", path)


def read(path: str) -> str:
    with open(path, errors="replace") as f:
        return f.read()


# Processes -------------------------------------------------------------------

class Process(NamedTuple):
    ppid: int
    pgid: int
    cpu: float  # Percent of one core, as ps reports it.
    command: str


def processes() -> dict[int, Process]:
    """Every process, by pid."""
    out = subprocess.run(
        ["ps", "-Aww", "-o", "pid=,ppid=,pgid=,pcpu=,args="],
        capture_output=True, text=True, errors="replace", check=True,
        env={**{k: v for k, v in os.environ.items() if k != "LC_ALL"},
             "LC_CTYPE": "UTF-8", "LC_NUMERIC": "C"}).stdout
    table = {}
    for line in filter(None, out.split("\n")):
        pid, ppid, pgid, cpu, command = (line + " ").split(None, 4)
        table[int(pid)] = Process(int(ppid), int(pgid), float(cpu),
                                  command.strip())
    return table


def find_owner() -> int:
    """The process that started this command: the parent of the outermost
    ancestor that shares this process's process group. Agents and
    interactive shells start each command in a process group of its own,
    so this is the agent or the shell, not the wrappers in between."""
    table, pid, group = processes(), os.getpid(), os.getpgrp()
    while pid in table and table[pid].pgid == group:
        pid = table[pid].ppid
    return pid


def members(table: dict[int, Process], root: int, group: int) -> list[int]:
    """The processes in the group, and every descendant of the root or of
    a group member, including ones that moved to a group of their own."""
    children: dict[int, list[int]] = {}
    for pid, process in table.items():
        children.setdefault(process.ppid, []).append(pid)
    found, todo = set(), [root] + [p for p, v in table.items()
                                   if v.pgid == group]
    while todo:
        pid = todo.pop()
        if pid in table and pid not in found:
            found.add(pid)
            todo.extend(children.get(pid, []))
    return sorted(found)


def signal_all(pids, group: int | None, sig: int) -> None:
    """Sends sig to each of pids, and to the process group unless it is
    None."""
    targets = [(p, os.kill) for p in pids]
    if group is not None:
        targets.append((group, os.killpg))
    for target, kill in targets:
        try:
            kill(target, sig)
        except (ProcessLookupError, PermissionError):
            pass


SIGNALS = (signal.SIGINT, signal.SIGQUIT, signal.SIGTERM, signal.SIGHUP)


def default_signals() -> None:
    """Runs in the command's process before it starts, so that it gets
    the standard handling of SIGNALS whatever its parent did with them."""
    for sig in SIGNALS:
        signal.signal(sig, signal.SIG_DFL)


# Summaries ---------------------------------------------------------------------

def clean(raw: str) -> list[str]:
    """Log lines without colour codes, keeping what a terminal would show
    after carriage returns, and cut to a readable width."""
    lines = []
    for line in ANSI.sub("", raw).split("\n"):
        line = line.rstrip("\r").rsplit("\r", 1)[-1].rstrip()
        lines.append(line if len(line) <= WIDTH else line[:WIDTH] + " …")
    while lines and not lines[-1]:
        lines.pop()
    return lines


def section(lines: list[str], chosen: list[int], first: int = 0,
            limit: int = 60) -> str:
    """The chosen lines, numbered as in the log, where lines[0] is the
    line after the first ones, saying how many lines are left out between
    them. When more than limit are chosen, the middle ones are left out
    too."""
    if len(chosen) > limit:
        chosen = chosen[:limit // 2] + chosen[-(limit // 2):]
    width = len(str(first + len(lines)))
    out, previous = [], -1
    for i in chosen:
        if previous >= 0 and i > previous + 1:
            out.append(f"[{count(i - previous - 1, 'line')} left out]")
        out.append(f"{first + i + 1:>{width}}| {lines[i]}")
        previous = i
    return "\n".join(out)


def log_tail(rundir: str, extent: tuple[int, int],
             limit: int = 4 << 20) -> tuple[int, list[str], int]:
    """The lines in the last limit bytes of the log's first extent[1]
    bytes, which hold extent[0] line breaks, how many lines come before
    them, and how many lines there are in all."""
    newlines, size = extent
    with open(os.path.join(rundir, "log"), "rb") as f:
        f.seek(max(0, size - limit))
        data = f.read(min(size, limit))
    total = newlines + (data[-1:] not in (b"", b"\n"))
    if size > limit:
        data = data.partition(b"\n")[2]
    return (newlines - data.count(b"\n"), clean(data.decode(errors="replace")),
            total)


def last_match(lines: list[str], pattern: str) -> int | None:
    regex = re.compile(pattern)
    for i in range(len(lines) - 1, -1, -1):
        if regex.search(lines[i]):
            return i
    return None


def first_match(lines: list[str], pattern: str, after: int = 0) -> int | None:
    regex = re.compile(pattern)
    return next((i for i in range(after, len(lines))
                 if regex.search(lines[i])), None)


def extract(lines: list[str]) -> list[int] | None:
    """The lines that hold a test runner's own summary, for runners whose
    output this recognises: a block from the last line that matches
    $LONGRUN_SUMMARY or that reads `=== ... test summary ===` or
    `=== ... test summary: ...`; pytest's short summary or final line;
    deno test's ERRORS and FAILURES sections or final line; Python
    unittest's failures from the first one; and the errors, failed test
    cases and final result of xcodebuild and XCTest."""
    rest = lambda start: list(range(start, len(lines)))
    start = last_match(lines, "|".join(filter(None, [
        os.environ.get("LONGRUN_SUMMARY"),
        r"^=+ [\w -]*test summary( =+$|: )"])))
    if start is None:
        start = last_match(lines, r"^=+ short test summary info =+$")
    if start is None:
        start = last_match(lines, r"^=+ .*\b(passed|failed|errors?|no tests "
                                  r"ran)\b.* in [\d.]+s.* =+$")
    if start is None:
        ran = last_match(lines, r"^running \d+ tests? from ") or 0
        start = first_match(lines, r"^ (ERRORS|FAILURES)$", ran)
    if start is None:
        start = last_match(lines, r"^(ok|FAILED) \| \d+ passed")
    if start is None and last_match(lines, r"^Ran \d+ tests? in "):
        start = first_match(lines, r"^(FAIL|ERROR): ")
        start = start and start - lines[start - 1].startswith("=====")
    if start is not None:
        return rest(start)
    start = last_match(lines, r"^(Test Suite '.*' (passed|failed) at |"
                              r"\*\* (BUILD|TEST) (SUCCEEDED|FAILED) \*\*)")
    if start is not None:
        failures = [i for i in range(start) if re.search(
            r"^Test Case '.*' failed|: error: ", lines[i])]
        return failures[:40] + rest(start)
    return None


def summary(rundir: str, spec: dict, state: dict,
            extent: tuple[int, int]) -> str:
    first, lines, total = log_tail(rundir, extent)
    code = state.get("exit")
    chosen = extract(lines)
    if chosen is None:
        chosen = list(range(max(0, len(lines) - (5 if code == 0 else 40)),
                            len(lines)))
    parts = [f"longrun: {describe(state)} after "
             f"{duration(state['ended'] - state['started'])}: "
             f"{spec['display']}",
             f"log: {os.path.join(rundir, 'log')} "
             f"({count(total, 'line')})"]
    if state.get("leftovers"):
        parts.append("ended processes the command left behind: "
                     + "; ".join(state["leftovers"]))
    if lines:
        parts.append(section(lines, chosen, first))
    return "\n".join(parts) + "\n"


def describe(state: dict) -> str:
    code = state.get("exit")
    text = (f"killed by {signal.Signals(-code).name}" if code < 0
            else f"exit {code}")
    return f"{text}, {state['reason']}" if state.get("reason") else text


def count(n: int, noun: str, plural: str = "") -> str:
    return f"{n} {noun if n == 1 else plural or noun + 's'}"


def duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    hours, rest = divmod(seconds, 3600)
    return (f"{hours}h{rest // 60:02}m" if hours
            else f"{rest // 60}m{rest % 60:02}s")


# Stall reports -------------------------------------------------------------------

def top_of_stack(text: str) -> list[str]:
    """The busiest frames from the end of a macOS `sample` report."""
    _, _, tail = text.partition("Sort by top of stack")
    frames = []
    for line in tail.splitlines()[1:]:
        if not line.strip() or line.startswith("Binary Images"):
            break
        frames.append("top of stack: " + re.sub(r"\s+\d+$", "",
                                                 " ".join(line.split())))
    return frames[:3]


def library(frame: str) -> bool:
    return bool(re.search(r"/(site-packages|lib/python[\d.]+)/", frame))


def python_stack(path: str, offset: int, cwd: str) -> list[str]:
    """Each distinct thread stack in a faulthandler dump: its two
    innermost frames, and the innermost frame outside the standard
    library and installed packages."""
    with open(path, errors="replace") as f:
        f.seek(offset)
        dump = f.read()
    stacks: dict[tuple[str, ...], list[bool]] = {}
    for thread in re.split(r"\n(?=(?:Current thread|Thread) 0x)", dump):
        header, *frames = thread.strip().splitlines() or [""]
        if not header:
            continue
        frames = [re.sub(r'File "[^"]*/((site-packages|lib/python[\d.]+)/)',
                         r'File ".../\1',
                         frame.strip().replace(f'"{cwd}/', '"'))
                  for frame in frames]
        chosen = frames[:2] + next(
            ([frame] for frame in frames[2:] if not library(frame)), [])
        stacks.setdefault(tuple(chosen), []).append(
            header.startswith("Current"))
    out = []
    for frames, current in stacks.items():
        out.append(f"{len(current)} thread{'s' * (len(current) > 1)}"
                   f"{' (current)' * any(current)}:")
        out.extend("  " + frame for frame in frames)
    return out


def diagnose(rundir: str, number: int, child: int, quiet: float,
             extent: tuple[int, int]) -> str:
    """Describes a run that has printed nothing for a while: its
    processes, what each is doing, and how to continue. Processes that
    run the same command and are doing the same thing are listed once."""
    table = processes()
    pids = members(table, child, child)
    stacks = os.path.join(rundir, f"stall-{number}")
    os.makedirs(stacks, exist_ok=True)
    dumps = {}
    for pid in pids:
        path = os.path.join(rundir, "py", f"{pid}.txt")
        if os.path.exists(path) and re.search(
                r"(?i)python", (table[pid].command.split() or [""])[0]):
            dumps[pid] = (path, os.path.getsize(path))
            try:
                os.kill(pid, signal.SIGINFO)
            except (ProcessLookupError, PermissionError):
                pass
    busiest = sorted(pids, key=lambda p: (p != child, -table[p].cpu))
    samplers = [subprocess.Popen(
        ["sample", str(pid), "1", "-mayDie", "-file",
         os.path.join(stacks, f"{pid}.txt")],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for pid in busiest[:SAMPLED]]
    for sampler in samplers:
        sampler.wait()
    cwd = load(rundir, "spec.json")["cwd"]
    alike: dict[tuple, list[int]] = {}
    for pid in pids:
        sampled = os.path.join(stacks, f"{pid}.txt")
        detail = python_stack(*dumps[pid], cwd) if pid in dumps else []
        if not detail and os.path.exists(sampled):
            detail = top_of_stack(read(sampled))
        alike.setdefault((table[pid].command[:160], tuple(detail)),
                         []).append(pid)
    cpu = sum(table[p].cpu for p in pids)
    lines = [f"longrun: STALLED, no output for {duration(quiet)}. The command "
             f"is still running ({count(len(pids), 'process', 'processes')}, {cpu:.0f}% CPU "
             f"between them)."]
    for (command, detail), group in alike.items():
        share = sum(table[p].cpu for p in group)
        who = (f"pid {group[0]}" if len(group) == 1 else
               f"{len(group)} processes ({', '.join(map(str, group[:4]))}"
               f"{', ...' * (len(group) > 4)})")
        lines.append(f"{who}, {share:.0f}% CPU: {command}")
        lines.extend("    " + line for line in detail)
    first, log, total = log_tail(rundir, extent, 1 << 16)
    lines.append(f"run: {rundir} (samples in stall-{number}/; the log "
                 f"has {count(total, 'line')}, ending:)")
    if log:
        lines.append(section(log, list(range(max(0, len(log) - 10),
                                             len(log))), first))
    run = os.path.basename(rundir)
    lines.append(f"To keep waiting: longrun wait {run}")
    lines.append(f"To end it:       longrun stop {run}")
    return "\n".join(lines) + "\n"


# The supervisor ------------------------------------------------------------------

class Supervisor:
    """Runs the command, copies its output to the log, and tells the
    connected clients when it stalls and when it ends. It outlives the
    client that started it, so that a run can continue after a stall
    report, and it ends the command when the owner exits, when a waiting
    client goes away without being told anything, or when told to."""

    def __init__(self, rundir: str, listener: socket.socket):
        self.rundir, self.listener = rundir, listener
        self.spec = load(rundir, "spec.json")
        self.state = {"status": "running", "supervisor": os.getpid(),
                      "started": time.time(), "stalls": 0}
        self.kq = select.kqueue()
        self.clients: dict[int, list] = {}  # fd: [socket, told]
        self.told = 0  # The last stall that a client was told about.
        self.written = 0  # The last stall whose report is written.
        self.reports = os.pipe()  # The number of each report written.
        self.stalled = False
        self.ending: set[int] = set()  # Processes sent SIGTERM.
        self.kill_at: float | None = None  # When to send them SIGKILL.
        self.killed = False
        self.extent = (0, 0)  # Line breaks and bytes in the log.

    def watch(self, ident: int, kind: int, flags: int = select.KQ_EV_ADD,
              fflags: int = 0) -> None:
        self.kq.control([select.kevent(ident, kind, flags, fflags)], 0)

    def run(self) -> None:
        self.lock = open(os.path.join(self.rundir, "lock"), "wb")
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        os.makedirs(os.path.join(self.rundir, "py"), exist_ok=True)
        env = dict(os.environ, LONGRUN_RUN=self.rundir,
                   LONGRUN_PYSTACKS=os.path.join(self.rundir, "py"))
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [os.path.join(HERE, "pystacks"),
                          env.get("PYTHONPATH")]))
        for sig in SIGNALS:
            signal.signal(sig, signal.SIG_IGN)
            self.watch(sig, select.KQ_FILTER_SIGNAL)
        self.log = open(os.path.join(self.rundir, "log"), "wb")
        self.reader, writer = os.pipe()
        try:
            self.child = subprocess.Popen(
                self.spec["argv"], cwd=self.spec["cwd"], env=env,
                stdin=subprocess.DEVNULL, stdout=writer, stderr=writer,
                start_new_session=True, preexec_fn=default_signals)
        except OSError as error:
            message = f"longrun: cannot run the command: {error}\n".encode()
            self.log.write(message)
            self.log.close()
            self.extent = (1, len(message))
            self.finish(127)
            return
        finally:
            os.close(writer)
        self.state.update(child=self.child.pid, owner=self.spec["owner"])
        self.save()
        os.set_blocking(self.reader, False)
        self.watch(self.reader, select.KQ_FILTER_READ)
        self.watch(self.listener.fileno(), select.KQ_FILTER_READ)
        self.watch(self.reports[0], select.KQ_FILTER_READ)
        try:
            self.watch(self.child.pid, select.KQ_FILTER_PROC,
                       fflags=select.KQ_NOTE_EXIT)
        except ProcessLookupError:
            self.child_exited()
        try:
            self.watch(self.spec["owner"], select.KQ_FILTER_PROC,
                       fflags=select.KQ_NOTE_EXIT)
        except ProcessLookupError:
            self.end("its owner had already exited")
        self.last = time.monotonic()
        while self.child.returncode is None or (self.ending and
                                                not self.killed):
            deadlines = [t for t in (self.kill_at, self.stall_at())
                         if t is not None]
            wait = (max(0.0, min(deadlines) - time.monotonic())
                    if deadlines else None)
            for event in self.kq.control(None, 64, wait):
                self.handle(event)
            now = time.monotonic()
            if self.kill_at is not None and now >= self.kill_at:
                self.kill()
            elif self.stall_at() is not None and now >= self.stall_at():
                self.stall(now - self.last)
        self.drain()
        self.log.close()
        self.finish(self.child.returncode)

    def stall_at(self) -> float | None:
        if (self.stalled or "reason" in self.state
                or self.child.returncode is not None):
            return None
        return self.last + self.spec["quiet"]

    def handle(self, event: select.kevent) -> None:
        if event.filter == select.KQ_FILTER_READ:
            if event.ident == self.reader:
                if self.drain():
                    self.last, self.stalled = time.monotonic(), False
            elif event.ident == self.listener.fileno():
                self.accept()
            elif event.ident == self.reports[0]:
                self.reported()
            elif event.ident in self.clients:
                self.hear(event.ident)
        elif event.filter == select.KQ_FILTER_SIGNAL:
            self.end(f"longrun got {signal.Signals(event.ident).name}")
        elif event.ident == self.child.pid:
            self.child_exited()
        elif event.ident == self.spec["owner"]:
            self.end(f"its owner (pid {event.ident}) exited")
        else:
            self.ending.discard(event.ident)

    def child_exited(self) -> None:
        """Reaps the command and ends what it left in its process group."""
        self.child.wait()
        self.ending.discard(self.child.pid)
        self.drain()
        table = processes()
        left = [pid for pid, v in table.items() if v.pgid == self.child.pid]
        if left and "reason" not in self.state:
            self.state["leftovers"] = [f"{pid} {table[pid].command[:80]}"
                                       for pid in left]
        self.terminate(left)

    def drain(self) -> bool:
        """Copies what the command printed to the log. Returns whether
        there was anything, and stops watching the pipe at its end."""
        got = False
        while self.reader is not None:
            try:
                data = os.read(self.reader, 1 << 16)
            except BlockingIOError:
                break
            if not data:
                os.close(self.reader)
                self.reader = None
                break
            self.log.write(data)
            self.extent = (self.extent[0] + data.count(b"\n"),
                           self.extent[1] + len(data))
            got = True
        self.log.flush()
        return got

    def save(self) -> None:
        save(self.rundir, "state.json", self.state)

    def accept(self) -> None:
        conn, _ = self.listener.accept()
        self.clients[conn.fileno()] = [conn, False]
        self.watch(conn.fileno(), select.KQ_FILTER_READ)
        self.state["waiting"] = len(self.clients)
        self.save()
        if (self.send(conn.fileno(), f"running {self.child.pid}")
                and self.written > self.told):
            self.tell(f"stall {self.written}")

    def hear(self, fd: int) -> None:
        try:
            data = self.clients[fd][0].recv(64)
        except OSError:
            data = b""
        if data.startswith(b"stop"):
            self.end("stopped by longrun stop")
        elif data.startswith(b"abandon") and len(self.clients) == 1:
            self.end("the subagent that started it finished")
        elif not data:
            self.drop(fd)

    def send(self, fd: int, message: str) -> bool:
        try:
            self.clients[fd][0].sendall(message.encode() + b"\n")
            return True
        except OSError:
            self.drop(fd)
            return False

    def drop(self, fd: int) -> None:
        """Forgets a client. A client that leaves before it was told
        anything was interrupted, and if it was the last one, nothing is
        waiting for the run any more."""
        conn, told = self.clients.pop(fd)
        conn.close()
        self.state["waiting"] = len(self.clients)
        self.save()
        if not told and not self.clients:
            self.end("the call waiting for it was interrupted")

    def tell(self, message: str) -> None:
        for fd in list(self.clients):
            if self.send(fd, message):
                self.clients[fd][1] = True
        if self.clients:
            self.told = self.written

    def stall(self, quiet: float) -> None:
        """Writes a stall report on a thread of its own, since sampling
        takes a while, and tells the clients when it is written."""
        self.stalled = True
        self.state["stalls"] += 1
        number = self.state["stalls"]

        def report() -> None:
            try:
                text = diagnose(self.rundir, number, self.child.pid, quiet,
                                self.extent)
            except Exception as error:
                run = os.path.basename(self.rundir)
                text = (f"longrun: STALLED, no output for {duration(quiet)}. "
                        f"The command is still running. Describing its "
                        f"processes failed: {error!r}\n"
                        f"To keep waiting: longrun wait {run}\n"
                        f"To end it:       longrun stop {run}\n")
            try:
                with open(os.path.join(self.rundir, f"stall-{number}.txt"),
                          "w") as f:
                    f.write(text)
            finally:
                os.write(self.reports[1], b"%d\n" % number)

        threading.Thread(target=report, daemon=True).start()

    def reported(self) -> None:
        latest = max(map(int, os.read(self.reports[0], 512).split()))
        if latest <= self.written:
            return
        self.written = latest
        self.save()
        self.tell(f"stall {self.written}")

    def end(self, reason: str) -> None:
        """Ends the command and everything it started."""
        if self.child.returncode is None and "reason" not in self.state:
            self.state["reason"] = reason
            self.terminate(members(processes(), self.child.pid,
                                   self.child.pid))

    def terminate(self, pids: list[int]) -> None:
        """Sends SIGTERM to pids now, and SIGKILL GRACE seconds later to
        those that are still running."""
        for pid in pids:
            try:
                self.watch(pid, select.KQ_FILTER_PROC,
                           select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                           select.KQ_NOTE_EXIT)
                self.ending.add(pid)
            except ProcessLookupError:
                pass
        if self.ending:
            signal_all(self.ending, self.group(), signal.SIGTERM)
            if self.kill_at is None:
                self.kill_at = time.monotonic() + GRACE

    def kill(self) -> None:
        self.kill_at, self.killed = None, True
        pids = set(self.ending)
        if self.child.returncode is None:
            pids.update(members(processes(), self.child.pid, self.child.pid))
        signal_all(pids, self.group(), signal.SIGKILL)

    def group(self) -> int | None:
        """The command's process group, while its leader's pid cannot have
        been reused: until longrun reaps the command, and afterwards
        while processes remain in it."""
        if self.child.returncode is None or any(
                v.pgid == self.child.pid for v in processes().values()):
            return self.child.pid
        return None

    def finish(self, code: int) -> None:
        self.state.update(status="exited", exit=code, ended=time.time())
        for name in glob.glob(os.path.join(self.rundir, "py", "*.txt")):
            if not os.path.getsize(name):
                os.remove(name)
        with open(os.path.join(self.rundir, "result.txt"), "w") as f:
            f.write(summary(self.rundir, self.spec, self.state, self.extent))
        self.save()
        self.tell("done")


# The client ----------------------------------------------------------------------

def connect(rundir: str) -> socket.socket | None:
    """Connects to the run's supervisor. The socket's path is relative
    because socket paths are limited to 104 bytes."""
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    here = os.getcwd()
    os.chdir(rundir)
    try:
        conn.connect("sock")
        return conn
    except (ConnectionRefusedError, FileNotFoundError):
        return None
    finally:
        os.chdir(here)


def attach(rundir: str, conn: socket.socket | None, request: bytes = b"",
           announce: bool = False) -> int:
    """Waits for the run to stall or end, prints the report, and returns
    the exit status to use."""
    if conn is not None:
        if request:
            conn.sendall(request)
        for message in conn.makefile():
            kind, _, arg = message.strip().partition(" ")
            if kind == "running" and announce:
                print(f"longrun: started {rundir}", flush=True)
            elif kind == "stall" and not request:
                sys.stdout.write(read(os.path.join(rundir,
                                                   f"stall-{arg}.txt")))
                return STALLED
            if kind == "done":
                break
    state = load(rundir, "state.json")
    if state.get("status") != "exited":
        print(f"longrun: the supervisor of {rundir} is gone without a "
              f"result; see {os.path.join(rundir, 'supervisor.log')}")
        return 1
    sys.stdout.write(read(os.path.join(rundir, "result.txt")))
    code = state["exit"]
    if code == 0 and state.get("reason"):
        return 128 + signal.SIGTERM
    return 128 - code if code < 0 else code


def start(argv: list[str], quiet: float, owner: int | None) -> int:
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        sys.exit("longrun: no command given")
    display = argv[0] if len(argv) == 1 else shlex.join(argv)
    if len(argv) == 1:
        argv = ["/bin/sh", "-c", argv[0]]
    word = re.sub(r"[^A-Za-z0-9]+", "-",
                  " ".join(os.path.basename(a) for a in argv[-1].split()[:2]))
    rundir = tempfile.mkdtemp(dir=base_dir(), prefix=time.strftime(
        "%m%d-%H%M%S-") + word.strip("-")[:24] + "-")
    save(rundir, "spec.json", {"argv": argv, "display": display,
                               "cwd": os.getcwd(),
                               "owner": owner or find_owner(),
                               "quiet": quiet * 60,
                               "agent": os.environ.get("LONGRUN_AGENT"),
                               "session": os.environ.get(
                                   "CLAUDE_CODE_SESSION_ID")})
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    here = os.getcwd()
    os.chdir(rundir)
    try:
        listener.bind("sock")
    finally:
        os.chdir(here)
    listener.listen()
    with open(os.path.join(rundir, "supervisor.log"), "wb") as errors:
        subprocess.Popen(
            [sys.executable, os.path.realpath(__file__), "supervise", rundir,
             str(listener.fileno())], pass_fds=[listener.fileno()],
            stdin=subprocess.DEVNULL, stdout=errors, stderr=errors,
            start_new_session=True)
    conn = connect(rundir)
    listener.close()
    return attach(rundir, conn, announce=True)


def runs(base: str) -> list[tuple[str, dict, dict, bool]]:
    """Each run in base: its directory, spec and state, and whether its
    supervisor is running, which it is while it holds the run's lock.
    Nothing connects to the supervisor, since a client that leaves without
    being told anything ends the run."""
    found = []
    for spec in sorted(glob.glob(os.path.join(base, "*", "spec.json"))):
        rundir = os.path.dirname(spec)
        found.append((rundir, load(rundir, "spec.json"),
                      load(rundir, "state.json"), locked(rundir)))
    return found


def locked(rundir: str) -> bool:
    try:
        with open(os.path.join(rundir, "lock"), "rb") as f:
            fcntl.flock(f, fcntl.LOCK_SH | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except FileNotFoundError:
        return False
    return False


def show(base: str) -> None:
    for rundir, spec, state, live in runs(base):
        status = (describe(state) if state.get("status") == "exited" else
                  f"running, {count(state.get('stalls', 0), 'stall')}"
                  if live else f"its supervisor is gone; the command was "
                  f"pid {state.get('child')}")
        print(f"{os.path.basename(rundir)}  {status}  "
              f"{spec['display'][:100]}")


def hook(event: dict) -> dict | None:
    """Claude Code hooks. Before a Bash call that runs longrun, raise its
    timeout to the largest Claude Code allows, so that the call blocks
    instead of moving to the background, and tell runs which subagent
    started them. When a subagent finishes, end the runs it started that
    nothing is waiting for. When the main agent is about to finish, send
    it back to any run that stalled and that nothing is waiting for."""
    name, agent = event["hook_event_name"], event.get("agent_id")
    if name == "PreToolUse":
        tool = dict(event["tool_input"])
        command = tool["command"]
        found = [call for call in re.finditer(
            r"(^|[;&|({\n]|\bthen|\bdo)\s*(time\s+)?(?P<at>)"
            r"(?P<wrappers>(\w+=\S*\s+|(env|nice|nohup|caffeinate)"
            r"(\s+-\w+(\s+\d+)?)*\s+)*)(\S*/)?longrun(\.py)?"
            r"\s+(?P<action>run|wait|stop)\b", command)
            if shell_code(command, call.start("at"))]
        if not found:
            return None
        if not tool.get("run_in_background"):
            tool["timeout"] = 2**31 - 1
        if agent and re.fullmatch(r"[\w-]+", agent):
            for call in reversed(found):
                if call["action"] == "run" and not re.search(
                        r"\benv\s+(-\w*\s+)*-\w*i", call["wrappers"]):
                    at = call.start("at")
                    command = (f"{command[:at]}LONGRUN_AGENT={agent} "
                               f"{command[at:]}")
            tool["command"] = command
        if tool == event["tool_input"]:
            return None
        return {"hookSpecificOutput": {"hookEventName": name,
                                       "updatedInput": tool}}
    base = base_dir(event.get("scratchpad_dir"), create=False)
    mine = [(rundir, spec, state) for rundir, spec, state, live in runs(base)
            if live and spec.get("session") == event.get("session_id")]
    if name == "SubagentStop" and agent:
        for rundir, spec, state in mine:
            conn = spec.get("agent") == agent and connect(rundir)
            if conn:
                try:
                    with conn, conn.makefile() as messages:
                        messages.readline()
                        conn.sendall(b"abandon\n")
                except OSError:
                    pass
    if name == "Stop" and not event.get("stop_hook_active"):
        forgotten = [f"{os.path.basename(rundir)} ({spec['display'][:80]})"
                     for rundir, spec, state in mine
                     if not spec.get("agent") and state.get("stalls")
                     and not state.get("waiting")]
        if forgotten:
            return {"decision": "block", "reason": (
                "These runs reported a stall and nothing is waiting for "
                "them: " + "; ".join(forgotten) + ". Before you finish, run "
                "`longrun wait RUN` to keep waiting for each, or "
                "`longrun stop RUN` to end it.")}
    return None


def positive(text: str) -> float:
    value = float(text)
    if not value > 0:
        raise argparse.ArgumentTypeError(f"{text} is not more than 0")
    return value


def shell_code(command: str, end: int) -> bool:
    """Whether command[end] is outside quotes, outside any here-document,
    and so could start a command."""
    quote, i = "", 0
    while i < end:
        c = command[i]
        if c == "\\" and quote != "'":
            i += 1
        elif quote and c == quote:
            quote = ""
        elif not quote and c in "'\"":
            quote = c
        elif not quote and command.startswith("<<", i):
            return False
        i += 1
    return not quote


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="longrun", description="Run a long command; see README.md.")
    commands = parser.add_subparsers(dest="action", required=True)
    run = commands.add_parser("run", help="run a command and wait for it")
    run.add_argument("--quiet", type=positive, default=10.0, metavar="MINUTES",
                     help="report a stall after this long without output "
                          "(default 10)")
    run.add_argument("--owner", type=int, metavar="PID",
                     help="end the command when this process exits")
    run.add_argument("command", nargs=argparse.REMAINDER)
    for name, text in [("wait", "wait for a run that stalled"),
                       ("stop", "end a run and wait for it to finish")]:
        commands.add_parser(name, help=text).add_argument("run")
    commands.add_parser("list", help="list this session's runs")
    commands.add_parser("hook", help="handle a Claude Code hook event")
    supervise = commands.add_parser("supervise")
    supervise.add_argument("rundir")
    supervise.add_argument("fd", type=int)
    args = parser.parse_args()
    if args.action == "run":
        return start(args.command, args.quiet, args.owner)
    if args.action == "supervise":
        supervisor = Supervisor(args.rundir, socket.socket(fileno=args.fd))
        try:
            supervisor.run()
        except BaseException:
            child = getattr(supervisor, "child", None)
            if child is not None and child.returncode is None:
                signal_all([], child.pid, signal.SIGKILL)
                signal_all(members(processes(), child.pid, child.pid),
                           None, signal.SIGKILL)
            raise
        return 0
    if args.action == "list":
        show(base_dir())
        return 0
    if args.action == "hook":
        reply = hook(json.load(sys.stdin))
        if reply:
            json.dump(reply, sys.stdout)
        return 0
    rundir = resolve(args.run)
    return attach(rundir, connect(rundir),
                  b"stop\n" if args.action == "stop" else b"")


if __name__ == "__main__":
    sys.exit(main())
