#!/usr/bin/env python3
"""Run a long command so that waiting for it takes one call, a stall is
reported once, and the command ends when whoever started it is gone.
See README.md."""

from __future__ import annotations

import argparse
import bisect
import contextlib
import ctypes
import fcntl
import glob
import json
import math
import os
import re
import select
import shlex
import signal
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Sequence
from typing import NamedTuple

HERE = os.path.dirname(os.path.realpath(__file__))
STALLED = 75  # EX_TEMPFAIL: the command is still running; wait again.
GRACE = 10.0  # Seconds between SIGTERM and SIGKILL when ending processes.
SAMPLED = 12  # At most this many processes are sampled for a stall report.
WIDTH = 300  # Longer lines are cut in summaries.
# Lines that report a failure, from xcodebuild, XCTest, Go, unittest and
# tools that print FAIL or FAILED before each failed check.
FAILURE = re.compile(r"^(--- )?FAIL(ED)?\b|^error(\[\w+\])?: |: error: |"
                     r"^Test Case '.*' failed|^\*\* (BUILD|TEST) FAILED \*\*")
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b\n]*(\x07|\x1b\\)")
ANSI_BYTES = re.compile(ANSI.pattern.encode())
# Lines in which a test runner says that a test is still running, and the
# stack dumps printed with them: the warnings of deno test, cargo test,
# cargo-nextest and loom's stuck-test plugin, and the output of Python's
# faulthandler. Each alternative matches from where the warning starts,
# since the text before it in the line is the command's real output. deno
# test's starts the line, or follows the `NAME ...` that it writes when
# the test starts.
HANG = (r"(^|(?<=\.\.\.))'.*' has been running for over \((\d+m)?\d+s\)$"
        r"|^test .+ has been running for over \d+ seconds$"
        r"|^ *(SETUP |TRY \d+ )?SLOW \[> *[\d.]+s\] "
        r"|stuck-test: "
        r"|Timeout \(\d+:\d\d:\d\d(\.\d+)?\)!$"
        r"|^((Current thread|Thread) 0x[0-9a-f]+( \[.*\])?|Stack)"
        r" \(most recent call first\):$"
        r'|  File ".*", line (\d+|\?\?\?) in '
        r"|^  <no Python frame>$|^(  )?\.\.\.$")
LINE = 4096  # Bytes at the start of a line that are searched for a warning.
FREEZE = "freeze-on-battery"  # Present in a run that is frozen on battery.


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


RUN_README = """\
This directory holds one run of longrun, which runs a long command for
an agent. longrun's README says what each file here holds, and how to
read `times`, the record of when the command's output arrived:

https://github.com/Hixie/contraptions/blob/main/longrun/README.md#the-timing-file
{readme} (section "The timing file")
"""


# The timing file -------------------------------------------------------------

HEADER = struct.Struct("<HH")  # The header's length and each record's.
RECORD = struct.Struct("<QQqqqQqqQ")


class Silence(NamedTuple):
    """A time without output, in seconds of the monotonic clock, and the
    line that holds the output before it, or 0 if there was none."""
    start: float
    end: float
    line: int


class Record(NamedTuple):
    """What the supervisor knew of the log at one moment. Times are in
    seconds of the monotonic clock, except wall."""
    size: int  # Bytes in the log.
    lines: int  # Line breaks in the log.
    at: float  # When the record was written.
    wall: float  # The same moment, in seconds since the Unix epoch.
    output: float  # When the last output before then arrived.
    line: int  # The line that output is in, or 0 before any output.
    longest: Silence  # The longest silence that had ended by then.


def records(rundir: str, limit: int | None = None) -> list[Record]:
    """The records in the run's timing file, or the first limit of them,
    without a last record that was cut short. A missing file, one without
    a whole header, and one whose header gives lengths too short for this
    version's fields have none."""
    try:
        with open(os.path.join(rundir, "times"), "rb") as f:
            data = f.read()
    except FileNotFoundError:
        return []
    if len(data) < HEADER.size:
        return []
    start, step = HEADER.unpack_from(data)
    if start < HEADER.size or step < RECORD.size:
        return []
    found = []
    for offset in range(start, len(data) - step + 1, step)[:limit]:
        (size, lines, at, wall, output, line, quiet, heard,
         before) = RECORD.unpack_from(data, offset)
        found.append(Record(size, lines, at / 1e9, wall / 1e9, output / 1e9,
                            line, Silence(quiet / 1e9, heard / 1e9, before)))
    return found


def output_times(pieces: Sequence[tuple[float, bytes]]) -> list[float]:
    """When each piece of a line that holds output arrived: a byte that
    is neither white space nor part of a colour code."""
    line = b"".join(piece for _, piece in pieces)
    hidden = {i for code in ANSI_BYTES.finditer(line)
              for i in range(*code.span())}
    times, offset = [], 0
    for arrived, piece in pieces:
        if any(i not in hidden and not line[i:i + 1].isspace()
               for i in range(offset, offset + len(piece))):
            times.append(arrived)
        offset += len(piece)
    return times


def length(silence: Silence) -> float:
    return silence.end - silence.start


def current(record: Record) -> Silence:
    """The silence that was going on when the record was written."""
    return Silence(record.output, record.at, record.line)


def describe_silence(silence: Silence, start: float) -> str:
    where = (f"after its output in line {silence.line}" if silence.line
             else "before any output")
    began, ended = int(silence.start - start), int(silence.end - start)
    return (f"{duration(ended - began)} {where} (from {duration(began)} to "
            f"{duration(ended)} after the start)")


MINUTE = 60.0  # Shorter silences are not worth naming.
SLICES = (10, 30, 60, 120, 300, 600, 900, 1800, 3600, 7200, 10800, 21600,
          43200, 86400)


def rhythm(found: list[Record], now: float) -> str:
    """How much the command printed in each of at most 12 equal parts of
    the run so far."""
    start = found[0].at
    elapsed = now - start
    # A last part shorter than a second is counted in the one before.
    width = next((s for s in SLICES if 12 * s >= elapsed - 1),
                 SLICES[-1] * math.ceil((elapsed - 1) / (12 * SLICES[-1])))
    times = [record.at for record in found]

    def printed(moment: float) -> int:
        index = bisect.bisect_right(times, moment) - 1
        return found[index].size if index >= 0 else 0

    parts = max(1, math.ceil((elapsed - 1) / width))
    edges = [start + i * width for i in range(parts)] + [now]
    amounts = [amount(printed(b) - printed(a))
               for a, b in zip(edges, edges[1:])]
    text = (f"Bytes written to it in each {duration(width)} since the run "
            f"started: "
            f"{', '.join(amounts)}")
    if now - edges[-2] < width:
        text += f" (the last part covers only {duration(now - edges[-2])})"
    return text + "."


def amount(n: float) -> str:
    """A number of bytes, shortened with K, M or G."""
    for unit in ("", "K", "M", "G"):
        text = f"{n:.{int(n < 9.95 and unit != '')}f}"
        if float(text) < 1000 or unit == "G":
            break
        n /= 1000
    return text + unit


def analysis(found: list[Record], lines: int) -> str:
    """For a stall report written at the time of the last record, about a
    log of the given number of lines: how much the command printed and
    when, and how the silence now compares with the earlier ones."""
    start, last = found[0].at, found[-1]
    text = [f"The log so far: {amount(last.size)}B in {count(lines, 'line')} "
            f"over {duration(last.at - start)}. " + rhythm(found, last.at)]
    if not last.line:
        text.append("This silence started when the run did.")
    elif length(last.longest) < MINUTE:
        text.append("Before this silence, the command never went a minute "
                    "without output.")
    else:
        text.append("Before this silence, the longest silence was "
                    + describe_silence(last.longest, start) + ".")
    text.append("Each line of the log below starts with how long after the "
                "start it was complete.")
    return "\n".join(text)


def stamps(found: list[Record], first: int, lines: list[str],
           extent: tuple[int, int]) -> list[str]:
    """For each line, how long after the start its last byte was read:
    the time of the first record whose size includes it. The records
    reach as far as extent. Without records, nothing."""
    if not found:
        return []
    breaks, sizes = [r.lines for r in found], [r.size for r in found]
    out = []
    for i in range(len(lines)):
        number = first + i + 1
        index = (bisect.bisect_left(breaks, number) if number <= extent[0]
                 else bisect.bisect_left(sizes, extent[1]))
        out.append(duration(found[index].at - found[0].at))
    return out


# Processes -------------------------------------------------------------------

class Process(NamedTuple):
    ppid: int
    pgid: int
    cpu: float  # Percent of one core, as ps reports it.
    state: str  # As ps reports it; it starts with T while stopped.
    command: str


def processes() -> dict[int, Process]:
    """Every process, by pid."""
    out = subprocess.run(
        ["ps", "-Aww", "-o", "pid=,ppid=,pgid=,pcpu=,stat=,args="],
        capture_output=True, text=True, errors="replace", check=True,
        env={**{k: v for k, v in os.environ.items() if k != "LC_ALL"},
             "LC_CTYPE": "UTF-8", "LC_NUMERIC": "C"}).stdout
    table = {}
    for line in filter(None, out.split("\n")):
        pid, ppid, pgid, cpu, state, command = (line + " ").split(None, 5)
        table[int(pid)] = Process(int(ppid), int(pgid), float(cpu), state,
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


# Power -------------------------------------------------------------------------

# What macOS posts when the computer switches between wall power and its
# battery, and what IOKit says of the time left on wall power.
POWER_NOTIFICATION = "com.apple.system.powersources.source"
TIME_REMAINING_UNLIMITED = -2.0


def power_events() -> int:
    """A file descriptor that becomes readable, holding four bytes for
    each notification, when the computer may have switched between wall
    power and battery. With $LONGRUN_POWER set, the notification is the
    one named by its value instead."""
    name = os.environ.get("LONGRUN_POWER") or POWER_NOTIFICATION
    libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    fd, token = ctypes.c_int(-1), ctypes.c_int()
    error = libc.notify_register_file_descriptor(
        name.encode(), ctypes.byref(fd), 0, ctypes.byref(token))
    if error:
        raise OSError(f"cannot register for {name} notifications: "
                      f"notify status {error}")
    return fd.value


def on_battery() -> bool:
    """Whether the computer runs on battery power. With $LONGRUN_POWER
    set, which the tests do, it does while the file that it names holds
    `battery`."""
    stand_in = os.environ.get("LONGRUN_POWER")
    if stand_in:
        with contextlib.suppress(FileNotFoundError):
            return read(stand_in).strip() == "battery"
        return False
    iokit = ctypes.CDLL("/System/Library/Frameworks/IOKit.framework/IOKit")
    iokit.IOPSGetTimeRemainingEstimate.restype = ctypes.c_double
    return iokit.IOPSGetTimeRemainingEstimate() != TIME_REMAINING_UNLIMITED


def still_running(table: dict[int, Process], pids: list[int]) -> str:
    number = count(len(pids), "process", "processes")
    return (f"The command is still running ({number}, "
            f"{sum(table[p].cpu for p in pids):.0f}% CPU between them).")


def battery_report(rundir: str, child: int, elapsed: float,
                   extent: tuple[int, int]) -> str:
    """Says that the computer has switched to battery power, elapsed
    seconds into the run, how much the command is doing and has printed,
    and how to continue the run."""
    table = processes()
    pids = members(table, child, child)
    first, log, total = log_tail(rundir, extent, 1 << 16)
    run = os.path.basename(rundir)
    lines = [f"longrun: ON BATTERY, the computer switched to battery power "
             f"{duration(elapsed)} into the run. "
             + still_running(table, pids),
             f"run: {rundir} (the log has {count(total, 'line')}"
             f"{', ending:' * bool(log)})"]
    if log:
        lines.append(section(log, list(range(max(0, len(log) - 5),
                                             len(log))), first))
    lines += ["Freezing stops its processes until the computer is on wall "
              "power again, so a timeout that counts real time can expire "
              "while they are stopped.",
              f"To freeze it and wait:  longrun wait --freeze-on-battery {run}",
              f"To let it run and wait: longrun wait {run}",
              f"To end it:              longrun stop {run}"]
    return "\n".join(lines) + "\n"


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
            limit: int = 60, times: list[str] | None = None) -> str:
    """The chosen lines, numbered as in the log, where lines[0] is the
    line after the first ones, saying how many lines are left out between
    them, and each after its entry in times if given. When more than
    limit are chosen, the middle ones are left out too."""
    if len(chosen) > limit:
        chosen = chosen[:limit // 2] + chosen[-(limit // 2):]
    width = len(str(first + len(lines)))
    wide = max((len(times[i]) for i in chosen), default=0) if times else 0
    out, previous = [], -1
    for i in chosen:
        if previous >= 0 and i > previous + 1:
            out.append(f"[{count(i - previous - 1, 'line')} left out]")
        when = f"{times[i]:>{wide}} " if times else ""
        out.append(f"{when}{first + i + 1:>{width}}| {lines[i]}")
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
    """The lines that hold a test runner's or a script's own summary, for
    output this recognises: a block from the last line that matches
    $LONGRUN_SUMMARY or that reads `=== ... test summary ===` or
    `=== NAME summary: OUTCOME ...`; pytest's short summary or final line;
    deno test's ERRORS and FAILURES sections or final line; Python
    unittest's failures from the first one; and the errors, failed test
    cases and final result of xcodebuild and XCTest."""
    rest = lambda start: list(range(start, len(lines)))
    start = last_match(lines, "|".join(filter(None, [
        os.environ.get("LONGRUN_SUMMARY"),
        r"^=+ [\w -]*\b(test summary =+$|summary: )"])))
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
        return failures(lines, start, 40) + rest(start)
    return None


def failures(lines: list[str], end: int, limit: int) -> list[int]:
    """The first limit lines before end that report a failure."""
    return [i for i in range(end) if FAILURE.search(lines[i])][:limit]


def summary(rundir: str, spec: dict, state: dict,
            extent: tuple[int, int]) -> str:
    first, lines, total = log_tail(rundir, extent)
    code = state.get("exit")
    chosen = extract(lines)
    if chosen is None:
        start = max(0, len(lines) - (5 if code == 0 else 40))
        chosen = (failures(lines, start, 20) if code else []) + list(
            range(start, len(lines)))
    parts = [f"longrun: {describe(state)} after "
             f"{duration(state['ended'] - state['started'])}: "
             f"{spec['display']}",
             f"log: {os.path.join(rundir, 'log')} "
             f"({count(total, 'line')})"]
    found = records(rundir)
    if found:
        longest = max(found[-1].longest, current(found[-1]), key=length)
        if length(longest) >= MINUTE:
            parts.append("longest silence: "
                         + describe_silence(longest, found[0].at))
    if state.get("frozen"):
        parts.append(f"frozen for {duration(state['frozen'])} while the "
                     f"computer was on battery")
    left = state.get("leftovers", [])
    if left:
        number = count(len(left), "process", "processes")
        if spec["keep"]:
            parts.append(f"kept {number} that the command left running, "
                         f"as --keep asked:")
        else:
            parts.append(f"ended {number} that the command left running. "
                         f"If the command's job is to start them, run it "
                         f"with `longrun run --keep`:")
        parts.extend(f"  {process}" for process in left[:10])
        if len(left) > 10:
            parts.append(f"  [{len(left) - 10} more]")
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


# Hang warnings -------------------------------------------------------------------

def hang_patterns() -> list[re.Pattern]:
    """HANG, and the regular expression in $LONGRUN_HANG."""
    return [re.compile(pattern)
            for pattern in (HANG, os.environ.get("LONGRUN_HANG")) if pattern]


def judge(pieces: list[tuple[float, bytes]], hang: list[re.Pattern],
          ended: bool = True) -> tuple[float | None, bool]:
    """When the latest real output in a line arrived, or None if it has
    none, and whether the line holds a hang warning, given the pieces in
    which the line was read with their arrival times, and whether the
    line has ended. Without its colour codes, a line holds a warning from
    the first match of a hang pattern in its first LINE bytes to its end;
    a pattern can match the end of the line only once the line has ended.
    Real output is what comes before that, other than white space and
    colour codes."""
    line = b"".join(piece for _, piece in pieces)
    text = ANSI_BYTES.sub(b"", line)
    head = text[:LINE].rstrip().decode(errors="surrogateescape")
    if not ended:
        head += "\0"
    matches = [next((m.start() for m in p.finditer(head)
                     if m.end() > m.start()), None) for p in hang]
    matches = [len(head[:start].encode(errors="surrogateescape"))
               for start in matches if start is not None]
    last = len(text[:min(matches, default=len(text))].rstrip()) - 1
    for code in ANSI_BYTES.finditer(line):
        if code.start() > last:
            break
        last += code.end() - code.start()
    seen = 0
    for at, piece in pieces:
        seen += len(piece)
        if last >= 0 and seen > last:
            return at, bool(matches)
    return None, bool(matches)


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


def continuing(rundir: str, again: float) -> str:
    """When waiting for a run that stalled reports it again if the
    command stays silent, and how to continue the run."""
    run = os.path.basename(rundir)
    return (f"If it stays silent, waiting reports it again after another "
            f"{duration(again)}.\n"
            f"To keep waiting: longrun wait {run}\n"
            f"To end it:       longrun stop {run}\n")


def diagnose(rundir: str, number: int, child: int, quiet: float,
             again: float, extent: tuple[int, int], heard: int,
             end: tuple[int, int], warned: bool, recorded: int) -> str:
    """Describes a run that has stalled: when it printed what, from the
    first `recorded` records of its timing file, the last of which was
    written for the stall; its processes and what each is doing; and how
    to continue. Processes that run the same command and are
    doing the same thing are listed once. The run's last output was in
    line heard, which ends at end (line breaks and bytes in the log up to
    there), and warned says whether it has printed hang warnings
    since."""
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
    first, log, total = log_tail(rundir, extent, 1 << 16)
    since = f" after its output in line {heard}" * (heard > 0)
    lines = [f"longrun: STALLED, no output for {duration(quiet)}"
             f"{f' other than hang warnings{since}' * warned}. "
             + still_running(table, pids)]
    found = records(rundir, recorded)
    lines.append(analysis(found, total) if found else
                 "longrun could not record when the output arrived.")
    for (command, detail), group in alike.items():
        share = sum(table[p].cpu for p in group)
        who = (f"pid {group[0]}" if len(group) == 1 else
               f"{len(group)} processes ({', '.join(map(str, group[:4]))}"
               f"{', ...' * (len(group) > 4)})")
        lines.append(f"{who}, {share:.0f}% CPU: {command}")
        lines.extend("    " + line for line in detail)
    lines.append(f"run: {rundir} (samples in stall-{number}/; the log "
                 f"has {count(total, 'line')}, ending"
                 f"{', after its last output' * (warned and heard > 0)}:)")
    chosen = set(range(max(0, len(log) - 10), len(log)))
    if warned and heard > first:
        chosen.update(range(max(0, heard - first - 5),
                            min(heard - first, len(log))))
    elif warned:
        # The last output, from further back than the end of the log that
        # was read.
        start, before, _ = log_tail(rundir, end, 1 << 16)
        if before:
            lines.append(section(
                before, list(range(max(0, len(before) - 5), len(before))),
                start, times=stamps(found, start, before, end)))
        left = first + min(chosen, default=len(log)) - heard
        if left > 0:
            lines.append(f"[{count(left, 'line')} left out]")
    if chosen:
        lines.append(section(log, sorted(chosen), first,
                             times=stamps(found, first, log, extent)))
    return "\n".join(lines) + "\n" + continuing(rundir, again)


# The supervisor ------------------------------------------------------------------

class Supervisor:
    """Runs the command, watches the log it writes, and tells the
    connected clients when it stalls, when the computer switches to
    battery power, and when it ends. It outlives the client that started
    it, so that a run can continue after a report, and it ends the
    command when the owner exits, when a waiting client goes away without
    being told anything, or when told to. On battery, it freezes the
    command if the run asks for that."""

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
        # How long a waiting call hears nothing before the next stall
        # report, which doubles with each report and is reset by output,
        # and when that report is due, which is None from a report until
        # a call next waits.
        self.interval = self.spec["quiet"]
        self.due: float | None = None
        # The processes that the run froze, in the order it stopped them,
        # or None while it is not frozen, when it froze them, and how
        # long it has been frozen since the command's latest output.
        self.frozen: list[int] | None = None
        self.frozen_at = 0.0
        self.frozen_quiet = 0.0
        self.ending: set[int] = set()  # Processes sent SIGTERM.
        self.kill_at: float | None = None  # When to send them SIGKILL.
        self.killed = False
        self.extent = (0, 0)  # Line breaks and bytes in the log.
        self.hang = hang_patterns()
        # The pieces of the line the command is writing, with when each
        # arrived, which are judged when the line ends or a stall report
        # falls due.
        self.line: list[tuple[float, bytes]] = []
        # When the command's latest real output arrived, the line that
        # holds it, and the line breaks and bytes in the log up to the end
        # of that line.
        self.last, self.last_line, self.last_end = time.monotonic(), 0, (0, 0)
        self.last_warned = False
        self.judged = 0.0  # When the last piece of self.line judged came.
        # The longest time between two pieces of real output, or between
        # the start and the first, which is none until the run starts.
        self.longest = Silence(self.last, self.last, 0)
        # The timing file, what its last record says, when that was
        # written, when the next is due, which is None unless something
        # has changed since, and how many records it holds.
        self.timing_fd: int | None = None
        self.recorded: tuple[int, int, float, int, Silence] | None = None
        self.recorded_at = -1.0
        self.record_at: float | None = None
        self.records_written = 0

    def watch(self, ident: int, kind: int, flags: int = select.KQ_EV_ADD,
              fflags: int = 0) -> None:
        self.kq.control([select.kevent(ident, kind, flags, fflags)], 0)

    def run(self) -> None:
        self.lock = open(os.path.join(self.rundir, "lock"), "wb")
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        os.makedirs(os.path.join(self.rundir, "py"), exist_ok=True)
        env = {"PYTHONUNBUFFERED": "1", **os.environ,
               "LONGRUN_RUN": self.rundir,
               "LONGRUN_PYSTACKS": os.path.join(self.rundir, "py")}
        env["PYTHONPATH"] = os.pathsep.join(
            filter(None, [os.path.join(HERE, "pystacks"),
                          env.get("PYTHONPATH")]))
        for sig in SIGNALS:
            signal.signal(sig, signal.SIG_IGN)
            self.watch(sig, select.KQ_FILTER_SIGNAL)
        output = open(os.path.join(self.rundir, "log"), "ab", buffering=0)
        self.log = open(os.path.join(self.rundir, "log"), "rb")
        self.watch(self.log.fileno(), select.KQ_FILTER_VNODE,
                   select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                   select.KQ_NOTE_WRITE | select.KQ_NOTE_EXTEND)
        with contextlib.suppress(OSError):
            self.timing_fd = os.open(os.path.join(self.rundir, "times"),
                                     os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                                     0o644)
        self.append(HEADER.pack(HEADER.size, RECORD.size))
        self.heard(time.monotonic(), 0, (0, 0), False)
        self.longest = Silence(self.last, self.last, 0)
        self.write_record()
        self.power = power_events()
        self.battery = on_battery()
        self.state["power"] = "battery" if self.battery else "wall"
        try:
            self.child = subprocess.Popen(
                self.spec["argv"], cwd=self.spec["cwd"], env=env,
                stdin=subprocess.DEVNULL, stdout=output, stderr=output,
                start_new_session=True, preexec_fn=default_signals)
        except OSError as error:
            output.write(f"longrun: cannot run the command: {error}\n"
                         .encode())
            self.finish(127)
            return
        finally:
            output.close()
        self.state.update(child=self.child.pid, owner=self.spec["owner"])
        self.save()
        self.watch(self.listener.fileno(), select.KQ_FILTER_READ)
        self.watch(self.reports[0], select.KQ_FILTER_READ)
        self.watch(self.power, select.KQ_FILTER_READ)
        if self.battery:
            self.freeze()
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
        while self.child.returncode is None or (self.ending and
                                                not self.killed):
            deadlines = [t for t in (self.kill_at, self.stall_at(),
                                     self.record_at) if t is not None]
            wait = (max(0.0, min(deadlines) - time.monotonic())
                    if deadlines else None)
            for event in self.kq.control(None, 64, wait):
                self.handle(event)
            now = time.monotonic()
            if self.kill_at is not None and now >= self.kill_at:
                self.kill()
            elif self.stall_at() is not None and now >= self.stall_at():
                self.listen()
                self.judge_line(False)
                if now >= self.stall_at():
                    self.stall(now - self.last - self.frozen_quiet)
            self.record()
        self.finish(self.child.returncode)
        self.log.close()

    def stall_at(self) -> float | None:
        """When to report a stall, or to judge the line the command is
        writing, if it would be due by then were it real output."""
        if (self.due is None or "reason" in self.state
                or self.child.returncode is not None
                or self.frozen is not None):
            return None
        if self.line and self.line[-1][0] > self.judged:
            return min(self.due, self.line[-1][0] + self.spec["quiet"])
        return self.due

    def heard(self, at: float, line: int, end: tuple[int, int],
              warned: bool,
              pieces: Sequence[tuple[float, bytes]] = ()) -> None:
        """Counts the command's silence from its real output at time at,
        and starts the intervals between stall reports again. The output
        is in the given line of the log, which ends at end (line breaks
        and bytes in the log up to there), and which holds a hang warning
        after the output if warned. The pieces of the line that hold
        output and arrived before at count too, so that the silences
        between them are counted. For output counted already, this only records
        where its line ends now and whether it holds a warning."""
        for moment in [arrived for arrived in output_times(pieces)
                       if arrived < at] + [at]:
            if (line, moment) > (self.last_line, self.last):
                if line:
                    self.longest = max(
                        self.longest, Silence(self.last, moment,
                                              self.last_line), key=length)
                self.last, self.last_line = moment, line
                self.frozen_quiet = 0.0
                self.interval = self.spec["quiet"]
                self.due = self.last + self.interval
        if line == self.last_line:
            self.last_end, self.last_warned = end, warned

    def handle(self, event: select.kevent) -> None:
        if event.filter == select.KQ_FILTER_VNODE:
            self.listen()
        elif event.filter == select.KQ_FILTER_READ:
            if event.ident == self.listener.fileno():
                self.accept()
            elif event.ident == self.reports[0]:
                self.reported()
            elif event.ident == self.power:
                self.power_changed()
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
        """Reaps the command and ends what it left in its process group,
        unless the command finished by itself and the run keeps what it
        leaves."""
        self.child.wait()
        self.thaw()
        self.ending.discard(self.child.pid)
        self.listen()
        self.judge_line(True)
        self.write_record()
        table = processes()
        left = [pid for pid, v in table.items() if v.pgid == self.child.pid]
        if "reason" in self.state:
            self.terminate(left)
        elif left:
            self.state["leftovers"] = [f"{pid} {table[pid].command[:WIDTH]}"
                                       for pid in left]
            if not self.spec["keep"]:
                self.terminate(left)

    def listen(self) -> tuple[int, int]:
        """Reads what the command has added to the log, up to the log's
        size now, counts the silence from the latest real output in the
        lines that it ends, and returns the line breaks and bytes in the
        log up to there. The line the command is still writing is judged
        when it ends or when a stall report falls due."""
        now = time.monotonic()
        newlines, size = self.extent
        end = os.fstat(self.log.fileno()).st_size
        while size < end and (data := self.log.read(min(1 << 20,
                                                         end - size))):
            ended = data.rfind(b"\n")
            if ended >= 0:
                self.ended(now, data[:ended], newlines, size)
                self.line = []
                newlines += data.count(b"\n")
            rest = data[ended + 1:]
            size += len(data)
            if rest and len(ANSI_BYTES.sub(b"", b"".join(
                    piece for _, piece in self.line))) < LINE:
                self.line.append((now, rest))
            elif rest:
                # Past where a warning can start, only the start is kept.
                pieces = self.line + [(now, rest)]
                at, warned = judge(pieces, self.hang, False)
                if at == now:
                    self.heard(now, newlines + 1, (newlines, size), warned,
                               pieces)
        self.extent = (newlines, size)
        return self.extent

    def ended(self, now: float, data: bytes, newlines: int,
              size: int) -> None:
        """Counts the silence from the latest real output in data, whose
        lines arrived now and follow the first newlines line breaks and
        size bytes in the log, the first of them continuing self.line, by
        looking at the lines from the last, and notes the hang warnings
        after that output. The first line is counted whatever follows it,
        so that the silences between its pieces are counted."""
        pieces = self.line + [(now, data.split(b"\n", 1)[0])]
        judged = judge(pieces, self.hang)
        if judged[0] is not None:
            self.heard(judged[0], newlines + 1,
                       (newlines + 1, size + len(pieces[-1][1]) + 1),
                       judged[1], pieces)
        line, end, after = newlines + data.count(b"\n") + 1, len(data), False
        while True:
            start = data.rfind(b"\n", 0, end) + 1
            piece, at, warned = data[start:end], None, False
            if start == 0:
                at, warned = judged
            elif piece.strip():
                at, warned = judge([(now, piece)], self.hang)
                if at is not None:
                    self.heard(at, line, (line, size + end + 1), warned)
            if at is not None or start == 0:
                self.last_warned |= after or (at is None and warned)
                return
            line, end, after = line - 1, start - 1, after or warned

    def judge_line(self, ended: bool) -> None:
        """Judges the line the command is writing, as far as it has been
        read, as a line that has ended if ended."""
        newlines, size = self.extent
        at, warned = judge(self.line, self.hang, ended)
        self.judged = self.line[-1][0] if self.line else 0.0
        if at is not None:
            self.heard(at, newlines + 1, (newlines, size), warned, self.line)
        else:
            self.last_warned |= warned

    def record(self) -> None:
        """Adds a record to the timing file if the log or its last output
        has changed since the last record, at once if that was a second
        ago or more, and otherwise when it will be."""
        if self.timing_fd is None or self.timing() == self.recorded:
            return
        if time.monotonic() >= self.recorded_at + 1:
            self.write_record()
        else:
            self.record_at = self.recorded_at + 1

    def timing(self) -> tuple[int, int, float, int, Silence]:
        """The bytes and line breaks in the log, the time and line of the
        last output, and the longest silence."""
        newlines, size = self.extent
        return size, newlines, self.last, self.last_line, self.longest

    def write_record(self) -> None:
        """Adds a record of the log and its last output, as they are now,
        to the timing file."""
        self.recorded = self.timing()
        self.recorded_at, self.record_at = time.monotonic(), None
        size, newlines, last, line, longest = self.recorded
        if self.append(RECORD.pack(
                size, newlines, round(self.recorded_at * 1e9),
                time.time_ns(), round(last * 1e9), line,
                round(longest.start * 1e9), round(longest.end * 1e9),
                longest.line)):
            self.records_written += 1

    def append(self, data: bytes) -> bool:
        """Adds data to the timing file, and returns whether it did. If
        it cannot, it deletes the file, which then says nothing rather
        than something false, and stops recording."""
        if self.timing_fd is None:
            return False
        try:
            if os.write(self.timing_fd, data) == len(data):
                return True
        except OSError:
            pass
        os.close(self.timing_fd)
        self.timing_fd = None
        with contextlib.suppress(OSError):
            os.remove(os.path.join(self.rundir, "times"))
        return False

    def save(self) -> None:
        save(self.rundir, "state.json", self.state)

    def accept(self) -> None:
        conn, _ = self.listener.accept()
        self.clients[conn.fileno()] = [conn, False]
        self.watch(conn.fileno(), select.KQ_FILTER_READ)
        self.state["waiting"] = len(self.clients)
        self.save()
        if not self.send(conn.fileno(), f"running {self.child.pid}"):
            return
        if self.written > self.told:
            self.tell_stall()
        elif self.due is None and self.state["stalls"] == self.told:
            self.due = time.monotonic() + self.interval

    def hear(self, fd: int) -> None:
        try:
            data = self.clients[fd][0].recv(64)
        except OSError:
            data = b""
        if data.startswith(b"stop"):
            # It waits for the end, and is told nothing else.
            self.clients[fd][1] = True
            self.end("stopped by longrun stop")
        elif data.startswith(b"abandon") and len(self.clients) == 1:
            self.end("the subagent that started it finished")
        elif data.startswith(b"freeze") and self.battery:
            self.freeze()
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
        self.state["waiting"] = len(self.clients)
        self.save()
        conn.close()
        if not told and not self.clients:
            self.end("the call waiting for it was interrupted")

    def tell(self, message: str, everyone: bool = False) -> bool:
        """Sends message to the clients that have not been told anything,
        or to every client if everyone, and returns whether any got it."""
        told = False
        for fd, client in list(self.clients.items()):
            if (everyone or not client[1]) and self.send(fd, message):
                client[1] = told = True
        return told

    def tell_stall(self) -> None:
        if self.tell(f"stall {self.written}"):
            self.told = self.written

    def stall(self, quiet: float) -> None:
        """Writes a stall report on a thread of its own, since sampling
        takes a while, and tells the clients when it is written."""
        self.due, again = None, self.interval * 2
        self.interval = again
        self.state["stalls"] += 1
        number, extent = self.state["stalls"], self.extent
        heard, end, warned = self.last_line, self.last_end, self.last_warned
        self.write_record()
        recorded = self.records_written

        def report() -> None:
            try:
                text = diagnose(self.rundir, number, self.child.pid, quiet,
                                again, extent, heard, end, warned, recorded)
            except Exception as error:
                text = (f"longrun: STALLED, no output for {duration(quiet)}"
                        f"{' other than hang warnings' * warned}. "
                        f"The command is still running. Describing its "
                        f"processes failed: {error!r}\n"
                        + continuing(self.rundir, again))
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
        self.tell_stall()

    def power_changed(self) -> None:
        """Freezes the run, or tells the clients, when the computer
        switches to battery power, and thaws the run when it switches
        back."""
        os.read(self.power, 4096)
        was, self.battery = self.battery, on_battery()
        self.state["power"] = "battery" if self.battery else "wall"
        if not self.battery:
            self.thaw()
        elif not was and not self.freeze():
            self.report_battery()
        self.save()

    def report_battery(self) -> None:
        """Tells the clients that are waiting for a running command that
        the computer has switched to battery power."""
        if (self.child.returncode is None and "reason" not in self.state
                and not all(told for _, told in self.clients.values())):
            number = self.state["battery"] = self.state.get("battery", 0) + 1
            with open(os.path.join(self.rundir, f"battery-{number}.txt"),
                      "w") as f:
                f.write(battery_report(
                    self.rundir, self.child.pid,
                    time.time() - self.state["started"], self.listen()))
            self.save()
            if self.tell(f"battery {number}"):
                # As after a stall, no stall falls due until a call waits.
                self.due = None

    def freeze(self) -> bool:
        """Stops the command's processes with SIGSTOP, other than those
        that are stopped already, if the run asks to be frozen on battery,
        and returns whether it asks. Processes are listed again after
        each round, until a round finds none that a process started
        before it was stopped."""
        if not os.path.exists(os.path.join(self.rundir, FREEZE)):
            return False
        if (self.frozen is None and self.child.returncode is None
                and "reason" not in self.state):
            # What the command printed before the freeze counts from when
            # it arrived, even in a line that it has not finished.
            self.listen()
            self.judge_line(False)
            self.frozen, self.frozen_at = [], time.monotonic()
            self.state["frozen_since"] = time.time()
            while True:
                table = processes()
                found = [p for p in members(table, self.child.pid,
                                            self.child.pid)
                         if p not in self.frozen
                         and not table[p].state.startswith("T")]
                if not found:
                    break
                signal_all(found, None, signal.SIGSTOP)
                self.frozen.extend(found)
            self.save()
        return True

    def thaw(self) -> None:
        """Continues the processes that freeze() stopped, the last first,
        and moves the next stall report later by as much of the freeze as
        fell within its countdown, so that the freeze does not count as
        silence."""
        if self.frozen is None:
            return
        signal_all(reversed(self.frozen), None, signal.SIGCONT)
        now = time.monotonic()
        self.frozen_quiet += now - max(self.frozen_at, self.last)
        if self.due is not None:
            # The countdown to it started an interval before it.
            self.due += now - max(self.frozen_at, self.due - self.interval)
        self.frozen = None
        self.state["frozen"] = (self.state.get("frozen", 0.0)
                                + now - self.frozen_at)
        del self.state["frozen_since"]
        self.save()

    def end(self, reason: str) -> None:
        """Ends the command and everything it started."""
        if self.child.returncode is None and "reason" not in self.state:
            self.state["reason"] = reason
            self.terminate(members(processes(), self.child.pid,
                                   self.child.pid))
            # A stopped process acts on SIGTERM once it continues.
            self.thaw()

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
        extent = self.listen()
        self.judge_line(True)
        if self.timing() != self.recorded:
            self.write_record()
        if self.timing_fd is not None:
            os.close(self.timing_fd)
            self.timing_fd = None
        with open(os.path.join(self.rundir, "result.txt"), "w") as f:
            f.write(summary(self.rundir, self.spec, self.state, extent))
        self.save()
        self.tell("done", everyone=True)


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
    """Waits for the run to stall, to switch to battery power or to end,
    prints the report, and returns the exit status to use. After a stall
    or a switch to battery, it returns once the supervisor closes the
    connection, which the supervisor does after it has stopped counting
    the call as waiting, or when it exits."""
    if conn is not None:
        if request:
            conn.sendall(request)
        messages = conn.makefile()
        for message in messages:
            kind, _, arg = message.strip().partition(" ")
            if kind == "running" and announce:
                print(f"longrun: started {rundir}", flush=True)
            elif kind in ("stall", "battery") and request != b"stop\n":
                sys.stdout.write(read(os.path.join(rundir,
                                                   f"{kind}-{arg}.txt")))
                with contextlib.suppress(OSError):  # The supervisor is gone.
                    conn.shutdown(socket.SHUT_WR)
                messages.read()
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


def start(argv: list[str], quiet: float, owner: int | None,
          keep: bool, freeze: bool) -> int:
    if argv and argv[0] == "--":
        argv = argv[1:]
    if not argv:
        sys.exit("longrun: no command given")
    try:
        hang_patterns()
    except re.error as error:
        sys.exit(f"longrun: $LONGRUN_HANG is not a regular expression: "
                 f"{error}")
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
                               "quiet": quiet * 60, "keep": keep,
                               "agent": os.environ.get("LONGRUN_AGENT"),
                               "session": os.environ.get(
                                   "CLAUDE_CODE_SESSION_ID")})
    with open(os.path.join(rundir, "README.md"), "w") as f:
        f.write(RUN_README.format(readme=os.path.join(HERE, "README.md")))
    if freeze:
        open(os.path.join(rundir, FREEZE), "w").close()
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
                  f"{', frozen' * ('frozen_since' in state)}"
                  if live else f"its supervisor is gone; the command was "
                  f"pid {state.get('child')}")
        print(f"{os.path.basename(rundir)}  {status}  "
              f"{spec['display'][:100]}")


def hook(event: dict) -> dict | None:
    """Claude Code hooks. Before a Bash call that runs longrun, raise its
    timeout to the largest Claude Code allows, so that the call blocks
    instead of moving to the background, and tell runs which subagent
    started them. In a subagent, also start the call with `sleep 0;`, which
    Claude Code exempts from the time limit it puts on a subagent's calls
    (see README.md). When a subagent finishes, end the runs it started
    that nothing is waiting for. When the main agent is about to finish,
    send it back to any run that stalled, or said the computer switched to
    battery power, and that nothing is waiting for."""
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
        if agent and not tool.get("run_in_background") and not re.match(
                r"\s*sleep\s", command):
            command = f"sleep 0; {command}"
        if command != tool["command"]:
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
                     if not spec.get("agent")
                     and (state.get("stalls") or state.get("battery"))
                     and not state.get("waiting")]
        if forgotten:
            return {"decision": "block", "reason": (
                "These runs reported a stall, or that the computer switched "
                "to battery power, and nothing is waiting for them: "
                + "; ".join(forgotten) + ". Before you finish, run "
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
    run.add_argument("--keep", action="store_true",
                     help="leave running what the command leaves running "
                          "when it exits by itself")
    wait = commands.add_parser("wait", help="wait for a run that stalled")
    for waits in (run, wait):
        waits.add_argument("--freeze-on-battery", action="store_true",
                           help="stop the command's processes while the "
                                "computer runs on battery power")
    run.add_argument("command", nargs=argparse.REMAINDER)
    wait.add_argument("run")
    commands.add_parser("stop", help="end a run and wait for it to "
                                     "finish").add_argument("run")
    commands.add_parser("list", help="list this session's runs")
    commands.add_parser("hook", help="handle a Claude Code hook event")
    supervise = commands.add_parser("supervise")
    supervise.add_argument("rundir")
    supervise.add_argument("fd", type=int)
    args = parser.parse_args()
    if args.action == "run":
        return start(args.command, args.quiet, args.owner, args.keep,
                     args.freeze_on_battery)
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
    request = b"stop\n" if args.action == "stop" else b""
    if args.action == "wait" and args.freeze_on_battery:
        # The file makes a later switch to battery freeze the run, and
        # the request freezes it now if the computer is on battery.
        open(os.path.join(rundir, FREEZE), "w").close()
        request = b"freeze\n"
    return attach(rundir, connect(rundir), request)


if __name__ == "__main__":
    sys.exit(main())
