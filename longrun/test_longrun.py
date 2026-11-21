"""Tests for longrun. They run real commands and need macOS (kqueue,
`sample`, SIGINFO). Commands that have to wait for the test block on a
FIFO that the test opens when it is ready."""

import contextlib
import ctypes
import io
import os
import re
import select
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
import unittest.mock
import zipfile

import longrun

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "longrun.py")


def until(directory, check):
    """Blocks until check() is true, checking again whenever a file in
    directory is added, removed or replaced."""
    fd = os.open(directory, os.O_RDONLY)
    queue = select.kqueue()
    queue.control([select.kevent(fd, select.KQ_FILTER_VNODE,
                                 select.KQ_EV_ADD | select.KQ_EV_CLEAR,
                                 select.KQ_NOTE_WRITE)], 0)
    while not check():
        queue.control(None, 1, None)
    os.close(fd)


def created(path):
    until(os.path.dirname(path), lambda: os.path.exists(path))


def log_path(result):
    """The path of the log that a finished `longrun run` names."""
    return result.stdout.split("log: ", 1)[1].split(" (", 1)[0]


def alive(pid):
    return pid in longrun.processes()


def exited(pid):
    """Blocks until pid has exited."""
    queue = select.kqueue()
    try:
        queue.control([select.kevent(pid, select.KQ_FILTER_PROC,
                                     select.KQ_EV_ADD, select.KQ_NOTE_EXIT)],
                      1, None)
    except ProcessLookupError:
        pass
    queue.close()


class Runs(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="longrun-test-\U0001f989-")
        self.env = dict(os.environ, LONGRUN_DIR=self.base)
        self.env.pop("PYTHONPATH", None)
        self.env.pop("LONGRUN_AGENT", None)
        self.env.pop("PYTHONUNBUFFERED", None)
        self.env["CLAUDE_CODE_SESSION_ID"] = "test-session"
        # As inside another run.
        self.env["LONGRUN_RUN"] = "/an/outer/run"
        self.env["LONGRUN_PYSTACKS"] = "/an/outer/run/py"
        # A stand-in for the power source, which starts on wall power.
        self.env["LONGRUN_POWER"] = os.path.join(self.base, "power")
        self.switch("wall")
        # The hooks run in this process and look here first.
        old = os.environ.pop("LONGRUN_DIR", None)
        if old is not None:
            self.addCleanup(os.environ.__setitem__, "LONGRUN_DIR", old)
        self.owner = subprocess.Popen(["sleep", "3600"])
        self.addCleanup(shutil.rmtree, self.base, True)
        self.addCleanup(self.end_orphans)
        self.addCleanup(self.owner.wait)
        self.addCleanup(self.owner.kill)

    def end_orphans(self):
        """Ends the commands of runs whose supervisor died, so that a
        failing test leaves nothing running."""
        for rundir, spec, state, live in longrun.runs(self.base):
            if not live and state.get("status") != "exited":
                longrun.signal_all([], state.get("child"), signal.SIGKILL)

    def longrun(self, *args, **kwargs):
        if args[0] == "run":
            args = ("run", "--owner", str(self.owner.pid)) + args[1:]
        return subprocess.run([sys.executable, SCRIPT, *args], env=self.env,
                              capture_output=True, text=True, **kwargs)

    def start(self, *args):
        """Starts `longrun run` without waiting for it, and returns the
        process and the run directory."""
        proc = self.client("run", "--owner", str(self.owner.pid), *args)
        first = proc.stdout.readline()
        self.assertTrue(first.startswith("longrun: started "), first)
        return proc, first.split("started ", 1)[1].strip()

    def client(self, *args):
        proc = subprocess.Popen([sys.executable, SCRIPT, *args], env=self.env,
                                stdout=subprocess.PIPE, text=True)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(proc.wait)
        return proc

    def finish(self, proc):
        """The output and exit status of a client started earlier."""
        output = proc.stdout.read()
        return output, proc.wait()

    def fifo(self, name):
        path = os.path.join(self.base, name)
        os.mkfifo(path)
        return path

    def release(self, fifo):
        """Lets a command that reads the FIFO continue."""
        with open(fifo, "w") as f:
            f.write("go\n")

    def state(self, rundir):
        return longrun.load(rundir, "state.json")

    def switch(self, source):
        """Switches the stand-in power source to "battery" or "wall", and
        says so as macOS does."""
        stand_in = self.env["LONGRUN_POWER"]
        with open(stand_in, "w") as f:
            f.write(source)
        ctypes.CDLL("/usr/lib/libSystem.B.dylib").notify_post(
            stand_in.encode())

    def frozen(self, pid):
        return longrun.processes()[pid].state.startswith("T")

    def until_frozen(self, rundir, frozen=True):
        until(rundir, lambda: ("frozen_since" in self.state(rundir)) == frozen)

    def test_exit_status_output_and_log(self):
        result = self.longrun("run", "--", "sh", "-c",
                              "echo one; echo two >&2; exit 3")
        self.assertEqual(result.returncode, 3)
        self.assertIn("exit 3 after", result.stdout)
        self.assertIn("1| one", result.stdout)
        self.assertIn("2| two", result.stdout)
        log = log_path(result)
        with open(log) as f:
            self.assertEqual(f.read(), "one\ntwo\n")
        success = self.longrun("run", "true")
        self.assertEqual(success.returncode, 0)
        self.assertIn("exit 0 after", success.stdout)

    def test_one_argument_is_a_shell_command(self):
        result = self.longrun("run", "echo a | tr a b; exit 4")
        self.assertEqual(result.returncode, 4)
        self.assertIn("1| b", result.stdout)
        self.assertIn(": echo a | tr a b; exit 4\n", result.stdout)

    def test_commands_that_end_at_once(self):
        for _ in range(20):
            result = self.longrun("run", "--", "/usr/bin/false")
            self.assertEqual(result.returncode, 1, result.stdout)

    def test_unusual_characters_in_other_processes(self):
        odd = subprocess.Popen([sys.executable, "-c",
                                "import time; time.sleep(300)",
                                "a\u2028b\x1cc\x0bd\x85e"])
        self.addCleanup(odd.wait)
        self.addCleanup(odd.kill)
        self.assertIn("a\u2028b", longrun.processes()[odd.pid].command)
        proc, rundir = self.start("sleep 300")
        result = self.longrun("stop", rundir)
        self.assertIn("stopped by longrun stop", result.stdout)
        self.finish(proc)

    def test_quiet_must_be_positive(self):
        for value in ("0", "-1"):
            result = self.longrun("run", "--quiet", value, "true")
            self.assertEqual(result.returncode, 2)
            self.assertIn("is not more than 0", result.stderr)

    def test_missing_program(self):
        result = self.longrun("run", "--", "/nonexistent/program", "x")
        self.assertEqual(result.returncode, 127)
        self.assertIn("cannot run the command", result.stdout)

    def test_signal_death(self):
        result = self.longrun("run", "kill -USR1 $$")
        self.assertEqual(result.returncode, 128 + signal.SIGUSR1)
        self.assertIn("killed by SIGUSR1", result.stdout)

    def test_command_gets_default_signal_handling(self):
        script = ("import signal; print(signal.getsignal(signal.SIGINT) is "
                  "signal.default_int_handler)")
        result = subprocess.run(
            [sys.executable, "-c",
             "import signal, subprocess, sys; "
             "signal.signal(signal.SIGINT, signal.SIG_IGN); "
             "sys.exit(subprocess.run(sys.argv[1:]).returncode)",
             sys.executable, SCRIPT, "run", "--owner", str(self.owner.pid),
             "--", sys.executable, "-c", script],
            env=self.env, capture_output=True, text=True)
        self.assertIn("1| True", result.stdout)

    def test_python_output_arrives_in_order(self):
        result = self.longrun(
            "run", "--", sys.executable, "-c",
            "import sys; print('out'); print('err', file=sys.stderr); "
            "print('out')")
        self.assertIn("1| out\n2| err\n3| out\n", result.stdout)

    def test_the_command_is_told_its_own_run(self):
        result = self.longrun("run",
                              "echo $LONGRUN_RUN; echo $LONGRUN_PYSTACKS")
        rundir = os.path.dirname(log_path(result))
        self.assertIn(f"1| {rundir}\n2| {rundir}/py\n", result.stdout)

    def test_runs_in_the_callers_directory(self):
        cwd = tempfile.mkdtemp(dir=self.base)
        result = self.longrun("run", "pwd", cwd=cwd)
        self.assertIn(os.path.realpath(cwd), result.stdout)

    def test_long_summaries_are_shortened_with_line_numbers(self):
        result = self.longrun("run", "seq 1 500; exit 1")
        lines = result.stdout.splitlines()
        self.assertIn("500| 500", lines)
        self.assertIn("461| 461", lines)
        self.assertNotIn("460| 460", lines)
        self.assertTrue(any(line.startswith("log: ") and
                            line.endswith("(500 lines)") for line in lines))
        passing = self.longrun("run", "seq 1 500").stdout.splitlines()
        self.assertIn("500| 500", passing)
        self.assertNotIn("495| 495", passing)

    def test_stall_hands_back_and_run_continues(self):
        gate = self.fifo("gate")
        proc, rundir = self.start("--quiet", "0.04",
                                  f"echo before; read x < {gate}; echo after")
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("STALLED, no output for ", report)
        self.assertIn("1| before", report)
        self.assertIn("longrun wait " + os.path.basename(rundir), report)
        self.assertTrue(alive(self.state(rundir)["child"]))
        # The stall was reported, so wait blocks until the command ends,
        # since that happens before twice the quiet time has passed.
        waiting = self.client("wait", os.path.basename(rundir))
        self.release(gate)
        output, code = self.finish(waiting)
        self.assertEqual(code, 0)
        self.assertIn("2| after", output)
        self.assertNotIn("STALLED", output)
        self.assertEqual(self.state(rundir)["stalls"], 1)

    def test_steady_output_is_not_a_stall(self):
        result = self.longrun("run", "--quiet", "0.04",
                              "for i in $(seq 30); do echo $i; sleep 0.2; done")
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("STALLED", result.stdout)

    def test_a_new_stall_after_output_is_reported(self):
        first, second = self.fifo("first"), self.fifo("second")
        proc, rundir = self.start(
            "--quiet", "0.04",
            f"read x < {first}; echo middle; read x < {second}; echo end")
        self.assertEqual(self.finish(proc)[1], longrun.STALLED)
        waiting = self.client("wait", rundir)
        self.release(first)
        output, code = self.finish(waiting)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("1| middle", output)
        waiting = self.client("wait", rundir)
        self.release(second)
        self.assertEqual(self.finish(waiting)[1], 0)
        self.assertEqual(self.state(rundir)["stalls"], 2)

    def test_silence_after_a_stall_is_reported_less_and_less_often(self):
        gate, marker = self.fifo("gate"), os.path.join(self.base, "marker")
        quiet = 0.02 * 60
        again = lambda n: f"after another {longrun.duration(n * quiet)}."
        # The marker appears more than twice the quiet time after the
        # first report, so the wait below would return as soon as it
        # started if the interval were counted from the report.
        proc, rundir = self.start(
            "--quiet", "0.02", f"sleep 5; touch {marker}; read x < {gate}; "
            f"echo middle; sleep 300")
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn(again(2), report)
        created(marker)
        began = time.monotonic()
        report, code = self.finish(self.client("wait", rundir))
        self.assertEqual(code, longrun.STALLED)
        self.assertGreaterEqual(time.monotonic() - began, 2 * quiet)
        self.assertIn(again(4), report)
        self.assertEqual(self.state(rundir)["stalls"], 2)
        # Output starts the intervals again.
        waiting = self.client("wait", rundir)
        self.release(gate)
        report, code = self.finish(waiting)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("1| middle", report)
        self.assertIn(again(2), report)
        self.longrun("stop", rundir)

    def test_hang_warnings_are_not_output(self):
        # A warning every half second keeps the command from being silent
        # for the quiet time, but it is not output.
        quiet = 0.04 * 60
        proc, rundir = self.start(
            "--quiet", "0.04",
            "printf \"before\\n't' has been running for over (1m0s)\\n\"; "
            "while :; do sleep 0.5; printf '\\033[0m\\033[1m"
            "\\033[33m'\\''t'\\'' has been running for over \\033[0m"
            "\\033[38;5;245m(1m0s)\\033[0m\\033[0m\\n'; done")
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertRegex(report, r"^longrun: STALLED, no output for \d+s "
                                 r"other than hang warnings after its output "
                                 r"in line 1\. ")
        self.assertIn("lines, ending, after its last output:)",
                      report)
        self.assertIn("1| before", report)
        # Nor do the warnings start the intervals between reports again.
        report, code = self.finish(self.client("wait", rundir))
        self.assertEqual(code, longrun.STALLED)
        self.assertIn(f"after another {longrun.duration(4 * quiet)}.", report)
        self.longrun("stop", rundir)

    def test_a_warning_after_output_in_its_line_is_not_output(self):
        proc, rundir = self.start(
            "--quiet", "0.04", "echo a; printf 'slow ...'; sleep 0.3; "
            "echo \"'slow' has been running for over (1m0s)\"; sleep 300")
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("other than hang warnings after its output in line 2.",
                      report)
        self.assertIn("2| slow ...'slow' has been running", report)
        self.longrun("stop", rundir)

    def test_long_output_before_warnings_is_shown(self):
        proc, rundir = self.start(
            "--quiet", "0.04", "echo first; printf '%5000s\\n' '' | tr ' ' x; "
            "for i in $(seq 30); do echo 'stuck-test: x'; done; sleep 300")
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertRegex(report, r"\n\d+s  1\| first\n\d+s  2\| x+ …\n"
                                 r"\[20 lines left out\]\n\d+s 23\| "
                                 r"stuck-test: x")
        self.longrun("stop", rundir)

    def test_reports_at_the_edges_of_what_they_read(self):
        for command, expected, unexpected in (
                # The line of output ends just inside the end of the log
                # that the report reads, and is left out of it.
                ("echo before; for i in $(seq 4681); do "
                 "echo 'stuck-test: x'; done; sleep 300",
                 r"\n\d+s 1\| before\n\[4671 lines left out\]\n\d+s 4673\| "
                 r"stuck", None),
                # Blank lines are not warnings.
                ("echo x; head -c 70000 /dev/zero | tr '\\0' '\\n'; sleep 300",
                 r"STALLED, no output for 2s\. ", "warnings"),
                # A warning longer than the end of the log that is read.
                ("echo before; printf 'stuck-test: '; "
                 "head -c 70000 /dev/zero | tr '\\0' x; echo; sleep 300",
                 r"\n\d+s 1\| before\n", "failed"),
                # Output that a terminal would show as blank.
                ("printf 'x stuck-test: y\\r   \\n'; sleep 300",
                 r"other than hang warnings after its output in line 1\.",
                 "failed"),
                # pytest's dots, while their line has not ended.
                ("echo a; sleep 1; printf ...; sleep 300",
                 r"no output for 2s\. ", "warnings"),
                # Many colour codes before a warning, which are not
                # output.
                ("for i in $(seq 500); do printf '\\033[32m.\\033[0m'; done; "
                 "printf .; echo 'stuck-test: x'; sleep 300",
                 r"other than hang warnings after its output in line 1\.",
                 None)):
            proc, rundir = self.start("--quiet", "0.04", command)
            report, code = self.finish(proc)
            self.assertEqual(code, longrun.STALLED, report)
            self.assertRegex(report, expected)
            if unexpected:
                self.assertNotIn(unexpected, report)
            self.longrun("stop", rundir)

    def test_a_warning_that_grows_past_where_warnings_are_looked_for(self):
        proc, rundir = self.start(
            "--quiet", "0.04", "printf 'stuck-test: x'; while :; do "
            "head -c 1000 /dev/zero | tr '\\0' x; sleep 0.5; done")
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("no output for 2s other than hang warnings.", report)
        self.longrun("stop", rundir)

    def test_a_warning_that_ends_a_line_judged_before(self):
        # The report that falls due for `a` judges the unfinished line of
        # dots, and the warning that ends that line comes after it.
        proc, rundir = self.start(
            "--quiet", "0.1", "echo a; sleep 2; printf ....; sleep 5; "
            "echo 'stuck-test: x has run for 600 seconds'; sleep 300")
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("other than hang warnings after its output in line 2.",
                      report)
        self.assertIn("2| ....stuck-test: x has run for 600 seconds", report)
        self.longrun("stop", rundir)

    def test_output_in_an_unfinished_line_after_a_stall(self):
        # After a report, the next one comes the quiet time after output,
        # even if the output does not end its line.
        gate = self.fifo("gate")
        proc, rundir = self.start("--quiet", "0.04",
                                  f"read x < {gate}; printf .; sleep 300")
        self.assertEqual(self.finish(proc)[1], longrun.STALLED)
        waiting = self.client("wait", rundir)
        self.release(gate)
        report, code = self.finish(waiting)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("STALLED, no output for 2s.", report)
        self.longrun("stop", rundir)

    def test_python_stack_dumps_are_not_output(self):
        proc, rundir = self.start(
            "--quiet", "0.04", "--", sys.executable, "-c",
            "import faulthandler, time; "
            "faulthandler.dump_traceback_later(0.5, repeat=True); "
            "time.sleep(300)")
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("no output for 2s other than hang warnings. ", report)
        self.assertRegex(report, r"\| Timeout \(0:00:00\.500000\)!")
        self.longrun("stop", rundir)

    def test_output_beside_hang_warnings_is_output(self):
        for start, command in (
                # Output and then a warning, in one write.
                ("", r"""printf "real\n'w' has been running for over """
                     r"""(1m0s)\n" """),
                # A line that has not ended yet.
                ("", "printf ."),
                # A line that has not ended yet, and is longer than
                # longrun looks in for a warning.
                (f"printf '%{longrun.LINE}s' '' | tr ' ' .; ", "printf ."),
                # Output before a warning in the same line.
                ("", r"printf '.stuck-test: x has run for 600 seconds\n'")):
            result = self.longrun(
                "run", "--quiet", "0.04",
                f"{start}for i in $(seq 10); do {command}; sleep 0.5; done; "
                f"echo")
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertNotIn("STALLED", result.stdout)

    def test_hang_warnings_from_the_environment(self):
        command = "for i in $(seq 10); do echo tick; sleep 0.5; done"
        result = self.longrun("run", "--quiet", "0.04", command)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("STALLED", result.stdout)
        self.env["LONGRUN_HANG"] = "^tick$"
        result = self.longrun("run", "--quiet", "0.04",
                              "while :; do echo tick; sleep 0.5; done")
        self.assertEqual(result.returncode, longrun.STALLED, result.stdout)
        self.assertIn("other than hang warnings. ", result.stdout)
        self.longrun("stop", re.search(r"longrun stop (\S+)",
                                       result.stdout)[1])
        runs = os.listdir(self.base)
        self.env["LONGRUN_HANG"] = "("
        result = self.longrun("run", "echo hi")
        self.assertEqual(result.returncode, 1)
        self.assertIn("$LONGRUN_HANG is not a regular expression",
                      result.stderr)
        self.assertEqual(os.listdir(self.base), runs)

    def test_output_before_many_warnings_is_shown(self):
        # More warnings than the report reads from the end of the log.
        proc, rundir = self.start(
            "--quiet", "0.04", "echo before; echo during; "
            "for i in $(seq 2000); do "
            "echo 'stuck-test: tests/a.py::test_x has run for 300 seconds'; "
            "done; sleep 300")
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("other than hang warnings after its output in line 2.",
                      report)
        self.assertRegex(report, r"\n\d+s 1\| before\n\d+s 2\| during\n"
                                 r"\[1990 lines left out\]\n\d+s 1993\| "
                                 r"stuck")
        self.longrun("stop", rundir)

    def test_times_say_when_output_arrived(self):
        gate = self.fifo("gate")
        before = time.time()
        proc, rundir = self.start(f"echo a; read x < {gate}; echo b")
        released = time.monotonic()
        self.release(gate)
        output, code = self.finish(proc)
        self.assertEqual(code, 0)
        with open(os.path.join(rundir, "times"), "rb") as f:
            self.assertEqual(f.read(4), b"\x04\x00\x48\x00")
        found = longrun.records(rundir)
        self.assertEqual((found[0].size, found[0].lines, found[0].line),
                         (0, 0, 0))
        self.assertEqual(longrun.length(found[0].longest), 0)
        self.assertEqual(found[-1].size,
                         os.path.getsize(os.path.join(rundir, "log")))
        self.assertEqual((found[-1].lines, found[-1].line), (2, 2))
        self.assertEqual([r.at for r in found], sorted(r.at for r in found))
        self.assertTrue(before <= found[0].wall <= found[-1].wall
                        <= time.time())
        # The second line was printed after the gate opened.
        self.assertLessEqual(released, found[-1].output)
        self.assertNotIn("longest silence", output)
        with open(os.path.join(rundir, "README.md")) as f:
            readme = f.read()
        self.assertIn("README.md#the-timing-file", readme)
        self.assertIn(os.path.join(HERE, "README.md"), readme)

    def test_times_are_recorded_at_most_once_a_second(self):
        result = self.longrun(
            "run", "for i in $(seq 150); do echo $i; sleep 0.01; done")
        found = longrun.records(os.path.dirname(log_path(result)))
        # The last record is written when the command ends; the others
        # when the run starts and when output arrives, a second apart or
        # more.
        gaps = [b.at - a.at for a, b in zip(found[:-1], found[1:-1])]
        self.assertGreater(len(gaps), 0)
        self.assertGreaterEqual(min(gaps), 0.99)
        self.assertEqual((found[-1].lines, found[-1].line), (150, 150))

    def test_a_line_that_never_ends_is_counted_when_the_command_does(self):
        result = self.longrun("run", "printf a; sleep 1.5; printf b")
        found = longrun.records(os.path.dirname(log_path(result)))
        self.assertEqual((found[-1].lines, found[-1].line), (0, 1))
        self.assertGreaterEqual(found[-1].output - found[0].at, 1.5)
        # The silence was measured within the line.
        self.assertEqual(found[-1].longest.line, 1)

    def test_a_stall_report_without_a_timing_file_says_so(self):
        gate = self.fifo("gate")
        proc, rundir = self.start("--quiet", "0.04",
                                  f"echo a; read x < {gate}")
        os.remove(os.path.join(rundir, "times"))
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("\nlongrun could not record when the output arrived.\n",
                      report)
        self.assertIn("\n1| a\n", report)
        self.release(gate)
        self.assertEqual(self.finish(self.client("wait", rundir))[1], 0)

    def test_silences_within_a_line_are_counted(self):
        # The end of the line comes in the same write as the next line.
        result = self.longrun("run", "printf a; sleep 2; printf b; sleep 2; "
                                     "printf 'c\\nnext\\n'")
        found = longrun.records(os.path.dirname(log_path(result)))
        # The silences in the first line are counted, not the whole line
        # as one.
        whole = found[-1].output - found[0].at
        self.assertLess(longrun.length(found[-1].longest), whole - 1.5)
        self.assertEqual((found[-1].line, found[-1].lines), (2, 2))

    def test_silences_within_a_long_line_are_counted(self):
        result = self.longrun(
            "run", "printf a; sleep 2; printf '%5000s' '' | tr ' ' .; "
                   "sleep 0.5; printf b; sleep 2; echo")
        found = longrun.records(os.path.dirname(log_path(result)))
        self.assertEqual(found[-1].longest.line, 1)

    def test_the_silence_before_the_command_exits_is_recorded(self):
        result = self.longrun("run", "echo a; sleep 1.5")
        found = longrun.records(os.path.dirname(log_path(result)))
        # The last record was written when the command exited, although
        # nothing had changed.
        self.assertEqual(found[-1][:2] + found[-1][4:],
                         found[-2][:2] + found[-2][4:])
        self.assertGreater(found[-1].at, found[-2].at)

    def test_the_last_record_covers_what_leftovers_print(self):
        result = self.longrun(
            "run", "sh -c 'trap \"echo bye; exit\" TERM; "
                   "while :; do sleep 0.1; done' & echo hi; sleep 0.3")
        self.assertIn("bye", result.stdout)
        found = longrun.records(os.path.dirname(log_path(result)))
        self.assertEqual(found[-1].size, os.path.getsize(log_path(result)))

    def test_stall_report_says_when_lines_were_printed(self):
        gate = self.fifo("gate")
        proc, rundir = self.start(
            "--quiet", "0.04",
            f"echo before; sleep 1.2; echo middle; read x < {gate}")
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("\nThe log so far: 14B in 2 lines over ", report)
        self.assertIn("the log has 2 lines", report)
        self.assertRegex(report, r"Bytes written to it in each \S+ since "
                                 r"the run started: 14[ ,]")
        self.assertIn("Before this silence, the command never went a minute "
                      "without output.", report)
        self.assertIn("\nEach line of the log below starts with how long "
                      "after the start it was complete.\n", report)
        self.assertRegex(report, r"\n *\d+s 1\| before\n *\d+s 2\| middle\n")
        self.release(gate)
        self.assertEqual(self.finish(self.client("wait", rundir))[1], 0)

    def test_stall_while_detached_is_reported_on_wait(self):
        gate = self.fifo("gate")
        proc, rundir = self.start("--quiet", "0.04",
                                  f"read x < {gate}; echo middle; sleep 300")
        self.assertEqual(self.finish(proc)[1], longrun.STALLED)
        self.release(gate)
        created(os.path.join(rundir, "stall-2.txt"))
        result = self.longrun("wait", rundir)
        self.assertEqual(result.returncode, longrun.STALLED)
        self.assertIn("1| middle", result.stdout)
        self.longrun("stop", rundir)

    def test_stop_after_a_stall_nothing_was_told_about(self):
        gate = self.fifo("gate")
        proc, rundir = self.start("--quiet", "0.04",
                                  f"read x < {gate}; echo middle; sleep 300")
        self.assertEqual(self.finish(proc)[1], longrun.STALLED)
        self.release(gate)
        created(os.path.join(rundir, "stall-2.txt"))
        result = self.longrun("stop", rundir)
        self.assertEqual(result.returncode, 128 + signal.SIGTERM)
        self.assertNotIn("STALLED", result.stdout)
        self.assertIn("stopped by longrun stop", result.stdout)

    def test_a_stopped_run_does_not_succeed(self):
        ready = self.fifo("ready")
        program = textwrap.dedent(f"""\
            import signal, sys
            signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
            open({ready!r}, "w").close()
            signal.pause()
            """)
        proc, rundir = self.start("--", sys.executable, "-c", program)
        open(ready).close()
        result = self.longrun("stop", rundir)
        self.assertIn("exit 0, stopped by longrun stop", result.stdout)
        self.assertEqual(result.returncode, 128 + signal.SIGTERM)
        self.finish(proc)

    def test_siginfo_reaches_only_python(self):
        program = ("import os; print('x', flush=True); "
                   "os.execvp('sleep', ['sleep', '300'])")
        proc, rundir = self.start("--quiet", "0.04", "--", sys.executable,
                                  "-c", program)
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("top of stack: __semwait_signal", report)
        with open(os.path.join(rundir, "log")) as f:
            self.assertEqual(f.read(), "x\n")
        self.longrun("stop", rundir)

    def test_a_command_that_closes_its_output_can_stall(self):
        gate = self.fifo("gate")
        proc, rundir = self.start("--quiet", "0.04",
                                  f"exec >/dev/null 2>&1; read x < {gate}")
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("the log has 0 lines", report)
        self.release(gate)
        self.assertEqual(self.longrun("wait", rundir).returncode, 0)

    def test_python_stacks_in_stall_report(self):
        gate = self.fifo("gate")
        program = textwrap.dedent(f"""\
            import threading
            def waiting_for_the_answer():
                print("ready", flush=True)
                open({gate!r}).read()
            done = threading.Event()
            for _ in range(30):
                threading.Thread(target=done.wait, daemon=True).start()
            waiting_for_the_answer()
            """)
        proc, rundir = self.start("--quiet", "0.04", "--", sys.executable,
                                  "-c", program)
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("1 thread (current):", report)
        self.assertIn("30 threads:", report)
        self.assertIn('line 4 in waiting_for_the_answer', report)
        self.assertNotIn("top of stack", report)
        self.release(gate)
        self.assertEqual(self.longrun("wait", rundir).returncode, 0)

    def test_python_that_does_not_answer_siginfo_is_sampled(self):
        program = ("import faulthandler, signal, time; "
                   "faulthandler.unregister(signal.SIGINFO); "
                   "print('x', flush=True); time.sleep(300)")
        proc, rundir = self.start("--quiet", "0.04", "--", sys.executable,
                                  "-c", program)
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("top of stack:", report)
        self.longrun("stop", rundir)

    def test_signals_to_the_supervisor_end_the_run(self):
        proc, rundir = self.start("sleep 300")
        os.kill(self.state(rundir)["supervisor"], signal.SIGQUIT)
        output, code = self.finish(proc)
        self.assertIn("longrun got SIGQUIT", output)
        self.assertEqual(code, 128 + signal.SIGTERM)

    def test_python_stack_hook_keeps_the_hidden_sitecustomize(self):
        other = tempfile.mkdtemp(dir=self.base)
        with open(os.path.join(other, "sitecustomize.py"), "w") as f:
            f.write("import builtins\nbuiltins.SEEN = 'other'\n")
        self.env["PYTHONPATH"] = other
        result = self.longrun("run", "--", sys.executable, "-c",
                              "import builtins; print(builtins.SEEN)")
        self.assertIn("1| other", result.stdout)
        self.env.pop("PYTHONPATH")
        result = self.longrun("run", "--", sys.executable, "-c",
                              "import builtins; print(hasattr(builtins, "
                              "'SEEN'))")
        self.assertIn("1| False", result.stdout)

    def test_python_stack_hook_leaves_no_file_to_warn_about(self):
        # Interpreter shutdown finalizes what the hook made in some
        # versions of Python, such as 3.12. The second program drops
        # every reference that the hook made, which finalizes it in all.
        drop = ("import faulthandler, gc, signal, sys; "
                "faulthandler.unregister(signal.SIGINFO); "
                "sys.modules.pop('sitecustomize').__dict__.clear(); "
                "gc.collect()")
        for program in ["pass", drop]:
            result = self.longrun("run", "--", sys.executable, "-X", "dev",
                                  "-c", program)
            self.assertEqual(result.returncode, 0, result.stdout)
            log = log_path(result)
            with open(log) as f:
                self.assertEqual(f.read(), "", program)

    def test_python_stack_hook_chains_with_one_that_chains_the_same_way(self):
        # Each directory's sitecustomize records its letter. A and C run
        # the one they hide with a copy of the hook's own code, which
        # registers no stack handler. P is the hook's directory and L is a
        # symbolic link to it. Z is a zip file.
        pystacks = os.path.join(HERE, "pystacks")
        with open(os.path.join(pystacks, "sitecustomize.py")) as f:
            chain = f.read()
        self.assertIn("LONGRUN_PYSTACKS", chain)
        chain = chain.replace("LONGRUN_PYSTACKS", "UNSET")
        stacks = os.path.join(self.base, "py")
        os.mkdir(stacks)
        dirs = {"P": pystacks, "L": os.path.join(self.base, "L")}
        os.symlink(pystacks, dirs["L"])
        dirs["Z"] = os.path.join(self.base, "Z.zip")
        with zipfile.ZipFile(dirs["Z"], "w") as z:
            z.writestr("sitecustomize.py", "import builtins\nbuiltins.SEEN "
                       "= getattr(builtins, 'SEEN', '') + 'Z'\n")
        for name in "ABC":
            dirs[name] = os.path.join(self.base, name)
            os.mkdir(dirs[name])
            with open(os.path.join(dirs[name], "sitecustomize.py"),
                      "w") as f:
                f.write("import builtins\nbuiltins.SEEN = getattr("
                        "builtins, 'SEEN', '') + %r\n" % name)
                if name != "B":
                    f.write(chain)
        for order, seen in [("APB", "AB"), ("PAB", "AB"), ("CAPB", "CAB"),
                            ("PALB", "AB"), ("LAPB", "AB"), ("APZ", "AZ"),
                            ("P", "")]:
            path = [dirs[name] for name in order]
            env = dict(self.env, PYTHONPATH=os.pathsep.join(path),
                       LONGRUN_PYSTACKS=stacks)
            result = subprocess.run(
                [sys.executable, "-c",
                 "import builtins, faulthandler, signal; "
                 "print(getattr(builtins, 'SEEN', ''), "
                 "faulthandler.unregister(signal.SIGINFO))"],
                env=env, capture_output=True, text=True)
            self.assertEqual((result.stderr, result.stdout),
                             ("", seen + " True\n"), order)

    def test_stop_ends_the_whole_tree(self):
        proc, rundir = self.start("--quiet", "0.04",
                                  "sh -c 'sleep 300' & sleep 300")
        self.assertEqual(self.finish(proc)[1], longrun.STALLED)
        state = self.state(rundir)
        pids = longrun.members(longrun.processes(), state["child"],
                               state["child"])
        self.assertGreaterEqual(len(pids), 3)
        result = self.longrun("stop", rundir)
        self.assertIn("killed by SIGTERM, stopped by longrun stop",
                      result.stdout)
        self.assertFalse([p for p in pids if alive(p)])

    def test_stop_ends_a_descendant_that_left_and_ignores_sigterm(self):
        ready = self.fifo("ready")
        program = textwrap.dedent(f"""\
            import subprocess
            loner = subprocess.Popen(
                ["sh", "-c", "trap '' TERM; while :; do sleep 1; done"],
                start_new_session=True)
            open({ready!r}, "w").write(str(loner.pid))
            loner.wait()
            """)
        proc, rundir = self.start("--", sys.executable, "-c", program)
        with open(ready) as f:
            loner = int(f.read())
        result = self.longrun("stop", rundir)
        self.assertIn("stopped by longrun stop", result.stdout)
        self.assertFalse(alive(loner))
        self.finish(proc)

    def test_term_ignored_is_followed_by_kill_without_a_stall(self):
        ready, gate = self.fifo("ready"), self.fifo("gate")
        proc, rundir = self.start(
            "--quiet", "0.1",
            f"trap '' TERM; echo > {ready}; read x < {gate}")
        with open(ready) as f:
            f.read()
        result = self.longrun("stop", rundir)
        self.assertEqual(result.returncode, 128 + signal.SIGKILL)
        self.assertIn("killed by SIGKILL, stopped by longrun stop",
                      result.stdout)
        self.assertNotIn("STALLED", result.stdout)
        self.assertEqual(self.state(rundir)["stalls"], 0)
        self.finish(proc)

    def test_owner_exit_ends_the_run(self):
        proc, rundir = self.start("sleep 300")
        child = self.state(rundir)["child"]
        self.owner.kill()
        output, code = self.finish(proc)
        self.assertEqual(code, 128 + signal.SIGTERM)
        self.assertIn(f"its owner (pid {self.owner.pid}) exited", output)
        self.assertFalse(alive(child))

    def test_owner_already_gone(self):
        gone = subprocess.Popen(["true"])
        gone.wait()
        result = subprocess.run(
            [sys.executable, SCRIPT, "run", "--owner", str(gone.pid),
             "sleep 300"], env=self.env, capture_output=True, text=True)
        self.assertIn("its owner had already exited", result.stdout)
        rundir = os.path.dirname(log_path(result))
        self.assertFalse(alive(self.state(rundir)["child"]))

    def test_interrupted_client_ends_the_run(self):
        proc, rundir = self.start("sleep 300")
        child = self.state(rundir)["child"]
        proc.kill()
        proc.wait()
        created(os.path.join(rundir, "result.txt"))
        result = self.longrun("wait", rundir)
        self.assertIn("the call waiting for it was interrupted",
                      result.stdout)
        self.assertFalse(alive(child))

    def test_detached_run_survives_without_a_client(self):
        gate = self.fifo("gate")
        proc, rundir = self.start("--quiet", "0.04",
                                  f"read x < {gate}; echo done")
        self.assertEqual(self.finish(proc)[1], longrun.STALLED)
        self.release(gate)
        created(os.path.join(rundir, "result.txt"))
        result = self.longrun("wait", rundir)
        self.assertEqual(result.returncode, 0)
        self.assertIn("1| done", result.stdout)

    def leftover(self, output, verb):
        """The pid of the first process that a result says was ended or
        kept."""
        found = re.search(rf"^{verb} \d+ process(es)? that the command "
                          rf"left running.*\n  (\d+) ", output, re.M)
        self.assertIsNotNone(found, output)
        return int(found[2])

    def test_leftover_group_members_are_ended_and_reported(self):
        result = self.longrun("run", "sleep 300 & echo started")
        self.assertEqual(result.returncode, 0)
        self.assertIn("run it with `longrun run --keep`", result.stdout)
        self.assertIn(" sleep 300\n", result.stdout)
        self.assertFalse(alive(self.leftover(result.stdout, "ended")))
        clean = self.longrun("run", "sleep 0 & wait; echo started")
        self.assertNotIn("left running", clean.stdout)

    def test_leftover_that_ignores_sigterm_is_killed(self):
        result = self.longrun(
            "run", "trap '' TERM; (while :; do sleep 1; done) >/dev/null 2>&1 &"
                   " echo started")
        self.assertEqual(result.returncode, 0)
        self.assertFalse(alive(self.leftover(result.stdout, "ended")))

    def test_keep_leaves_what_the_command_started(self):
        result = self.longrun("run", "--keep",
                              "sleep 300 >/dev/null 2>&1 & echo started")
        self.assertEqual(result.returncode, 0)
        self.assertIn("1| started", result.stdout)
        kept = self.leftover(result.stdout, "kept")
        self.addCleanup(os.kill, kept, signal.SIGKILL)
        self.assertTrue(alive(kept))

    def test_kept_processes_keep_writing_to_the_log(self):
        gate, marker = self.fifo("gate"), os.path.join(self.base, "marker")
        result = self.longrun(
            "run", "--keep",
            f"(read x < {gate}; echo later; touch {marker}) &"
            f" echo started")
        self.assertEqual(result.returncode, 0)
        self.addCleanup(longrun.signal_all,
                        [self.leftover(result.stdout, "kept")], None,
                        signal.SIGKILL)
        log = log_path(result)
        exited(self.state(os.path.dirname(log))["supervisor"])
        self.release(gate)
        created(marker)
        with open(log) as f:
            self.assertEqual(f.read(), "started\nlater\n")

    def test_keep_does_not_keep_what_a_stopped_run_started(self):
        pid = os.path.join(self.base, "pid")
        proc, rundir = self.start(
            "--keep", f"sleep 300 & echo $! > {pid}.new; mv {pid}.new {pid};"
                      f" sleep 300")
        created(pid)
        with open(pid) as f:
            background = int(f.read())
        self.owner.kill()
        output, code = self.finish(proc)
        self.assertEqual(code, 128 + signal.SIGTERM)
        self.assertNotIn("left running", output)
        self.assertFalse(alive(background))

    def test_supervisor_failure_ends_the_command(self):
        gate = self.fifo("gate")
        proc, rundir = self.start("--quiet", "0.04", f"read x < {gate}")
        child = self.state(rundir)["child"]
        os.chmod(rundir, 0o500)
        self.addCleanup(os.chmod, rundir, 0o700)
        output, code = self.finish(proc)
        self.assertEqual(code, 1)
        self.assertIn("is gone without a result", output)
        self.assertFalse(alive(child))

    def test_owner_is_the_process_that_started_the_job(self):
        code = (f"import sys; sys.path.insert(0, {HERE!r}); import longrun; "
                f"print(longrun.find_owner())")
        env = dict(os.environ, PY=sys.executable, CODE=code)
        out = subprocess.run(["sh", "-c", """sh -c '"$PY" -c "$CODE"'"""],
                             capture_output=True, text=True, env=env,
                             start_new_session=True)
        self.assertEqual(int(out.stdout), os.getpid())
        out = subprocess.run([sys.executable, "-c", code],
                             capture_output=True, text=True)
        self.assertNotEqual(int(out.stdout), os.getpid())

    def test_subagent_stop_ends_only_that_agents_detached_runs(self):
        project = tempfile.mkdtemp(prefix="longrun-test-",
                                   dir=f"/private/tmp/claude-{os.getuid()}")
        self.addCleanup(shutil.rmtree, project, True)
        session = os.path.basename(project)
        scratchpad = os.path.join(project, session, "scratchpad")
        os.makedirs(scratchpad)
        self.env.pop("LONGRUN_DIR")
        self.env["CLAUDE_CODE_SESSION_ID"] = session
        runs = {}
        for agent, args in [("mine", ["--quiet", "0.04"]),
                            ("other", ["--quiet", "0.04"]), ("waited", [])]:
            self.env["LONGRUN_AGENT"] = agent
            runs[agent] = self.start(*args, "echo started; sleep 300")
        for agent in ("mine", "other"):
            self.assertEqual(self.finish(runs[agent][0])[1], longrun.STALLED)
        for agent in ("mine", "waited"):
            longrun.hook({"hook_event_name": "SubagentStop",
                          "agent_id": agent, "scratchpad_dir": scratchpad,
                          "session_id": session})
        self.assertTrue(runs["mine"][1].startswith(scratchpad + "/longrun/"))
        result = self.longrun("wait", runs["mine"][1])
        self.assertIn("the subagent that started it finished", result.stdout)
        for agent in ("other", "waited"):
            state = self.state(runs[agent][1])
            self.assertNotEqual(state["status"], "exited")
            self.assertTrue(alive(state["child"]))
        runs["waited"][0].kill()
        self.longrun("stop", runs["other"][1])

    def test_list(self):
        self.longrun("run", "exit 2")
        detached, quiet = self.start("--quiet", "0.04", "sleep 300")
        self.assertEqual(self.finish(detached)[1], longrun.STALLED)
        proc, rundir = self.start("sleep 300")
        listing = self.longrun("list").stdout
        self.assertIn("exit 2  exit 2", listing)
        self.assertIn("running, 0 stalls  sleep 300", listing)
        self.assertIn("running, 1 stall  sleep 300", listing)
        # Listing does not count as a call that waited and went away.
        self.assertTrue(alive(self.state(quiet)["child"]))
        self.longrun("stop", quiet)
        child = self.state(rundir)["child"]
        os.kill(self.state(rundir)["supervisor"], signal.SIGKILL)
        self.finish(proc)
        listing = self.longrun("list").stdout
        self.assertIn(f"its supervisor is gone; the command was pid {child}",
                      listing)
        os.killpg(child, signal.SIGKILL)

    def test_stop_hook_sends_the_agent_back_to_forgotten_runs(self):
        scratchpad = tempfile.mkdtemp(dir=self.base)
        self.env["LONGRUN_DIR"] = os.path.join(scratchpad, "longrun")
        proc, forgotten = self.start("--quiet", "0.04", "sleep 300")
        self.assertEqual(self.finish(proc)[1], longrun.STALLED)
        # The wait on this run has to outlast the checks below.
        proc, watched = self.start("--quiet", "0.1", "sleep 300")
        self.assertEqual(self.finish(proc)[1], longrun.STALLED)
        waited = self.client("wait", watched)
        until(watched, lambda: self.state(watched).get("waiting") == 1)

        def stop(**event):
            return longrun.hook(dict({"hook_event_name": "Stop",
                                      "scratchpad_dir": scratchpad,
                                      "session_id": "test-session"}, **event))
        self.assertIsNone(stop(session_id="another-session"))
        reply = stop()
        self.assertEqual(reply["decision"], "block")
        self.assertIn(os.path.basename(forgotten), reply["reason"])
        self.assertNotIn(os.path.basename(watched), reply["reason"])
        self.assertIsNone(stop(stop_hook_active=True))
        self.longrun("stop", forgotten)
        self.assertIsNone(stop())
        self.longrun("stop", watched)
        self.finish(waited)

    def test_stop_hook_sees_a_stall_as_soon_as_the_call_returns(self):
        scratchpad = tempfile.mkdtemp(dir=self.base)
        self.env["LONGRUN_DIR"] = os.path.join(scratchpad, "longrun")
        gate = self.fifo("gate")
        proc, rundir = self.start("--quiet", "0.04",
                                  f"read x < {gate}; echo middle; sleep 300")
        self.assertEqual(self.finish(proc)[1], longrun.STALLED)
        # The connection stays open until the end of the with statement,
        # so only what attach does before it returns can tell the
        # supervisor that the call has returned.
        with longrun.connect(rundir) as conn:
            self.release(gate)
            with contextlib.redirect_stdout(io.StringIO()) as report:
                self.assertEqual(longrun.attach(rundir, conn),
                                 longrun.STALLED)
            self.assertIn("1| middle", report.getvalue())
            reply = longrun.hook({"hook_event_name": "Stop",
                                  "scratchpad_dir": scratchpad,
                                  "session_id": "test-session"})
            self.assertIsNotNone(reply)
            self.assertIn(os.path.basename(rundir), reply["reason"])
        self.longrun("stop", rundir)

    def test_a_switch_to_battery_is_reported_and_the_run_continues(self):
        ready, gate = self.fifo("ready"), self.fifo("gate")
        proc, rundir = self.start(f"echo before; echo > {ready}; "
                                  f"read x < {gate}; echo after")
        with open(ready) as f:
            f.read()
        self.switch("battery")
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertTrue(report.startswith("longrun: ON BATTERY, "), report)
        self.assertIn("1| before", report)
        self.assertIn("longrun wait --freeze-on-battery "
                      + os.path.basename(rundir), report)
        child = self.state(rundir)["child"]
        self.assertFalse(self.frozen(child))
        self.assertEqual(self.state(rundir)["battery"], 1)
        # A notification while the computer stays on battery, and a wait,
        # do not report it again.
        waiting = self.client("wait", rundir)
        self.switch("battery")
        self.release(gate)
        output, code = self.finish(waiting)
        self.assertEqual(code, 0)
        self.assertIn("2| after", output)
        self.assertNotIn("ON BATTERY", output)
        self.assertNotIn("frozen", output)
        self.assertEqual(self.state(rundir)["battery"], 1)

    def test_after_a_battery_report_only_a_waiting_call_counts(self):
        quiet = 0.02 * 60
        gate = self.fifo("gate")
        proc, rundir = self.start("--quiet", "0.02",
                                  f"read x < {gate}; echo after")
        self.switch("battery")
        self.assertEqual(self.finish(proc)[1], longrun.STALLED)
        # No stall falls due while nothing waits, as after a stall. One
        # that did would be written within a second or two of falling due.
        time.sleep(4 * quiet)
        self.assertEqual(self.state(rundir)["stalls"], 0)
        # Nor is a switch to battery reported while nothing waits.
        for source in ("wall", "battery"):
            self.switch(source)
            until(rundir, lambda: self.state(rundir)["power"] == source)
        waiting = self.client("wait", rundir)
        self.release(gate)
        output, code = self.finish(waiting)
        self.assertEqual(code, 0)
        self.assertIn("1| after", output)
        self.assertEqual(self.state(rundir)["battery"], 1)

    def test_a_wait_during_a_freeze_counts_from_when_it_started(self):
        quiet = 0.02 * 60
        proc, rundir = self.start("--quiet", "0.02", "--freeze-on-battery",
                                  "sleep 300")
        self.assertEqual(self.finish(proc)[1], longrun.STALLED)
        self.switch("battery")
        self.until_frozen(rundir)
        # Frozen for long before the wait starts. The wait's countdown,
        # twice the quiet time after a stall, starts when it does, and
        # only the freeze after that puts its report off.
        time.sleep(6 * quiet)
        waiting = self.client("wait", rundir)
        until(rundir, lambda: self.state(rundir).get("waiting") == 1)
        thawed = time.monotonic()
        self.switch("wall")
        report, code = self.finish(waiting)
        self.assertEqual(code, longrun.STALLED)
        elapsed = time.monotonic() - thawed
        self.assertGreaterEqual(elapsed, 2 * quiet)
        self.assertLess(elapsed, 5 * quiet)
        self.longrun("stop", rundir)

    def test_a_run_started_on_battery_is_not_reported(self):
        gate = self.fifo("gate")
        self.switch("battery")
        proc, rundir = self.start(f"read x < {gate}; echo after")
        self.switch("battery")
        self.release(gate)
        output, code = self.finish(proc)
        self.assertEqual(code, 0)
        self.assertNotIn("ON BATTERY", output)

    def test_a_run_can_be_frozen_on_battery(self):
        gate = self.fifo("gate")
        proc, rundir = self.start(
            "--freeze-on-battery", f"sh -c 'read x < {gate}; echo after'; "
            f"echo done")
        child = self.state(rundir)["child"]
        pids = longrun.members(longrun.processes(), child, child)
        self.assertGreaterEqual(len(pids), 2)
        self.assertFalse([p for p in pids if self.frozen(p)])
        self.switch("battery")
        self.until_frozen(rundir)
        self.assertEqual([p for p in pids if not self.frozen(p)], [])
        self.assertIn("running, 0 stalls, frozen", self.longrun("list").stdout)
        self.switch("wall")
        self.until_frozen(rundir, False)
        self.assertEqual([p for p in pids if self.frozen(p)], [])
        self.release(gate)
        output, code = self.finish(proc)
        self.assertEqual(code, 0)
        self.assertIn("1| after", output)
        self.assertRegex(output, r"\nfrozen for \ds while the computer was "
                                 r"on battery\n")
        self.assertNotIn("ON BATTERY", output)

    def test_wait_can_freeze_a_run_after_the_switch(self):
        gate = self.fifo("gate")
        proc, rundir = self.start(f"read x < {gate}; echo after")
        self.switch("battery")
        self.assertEqual(self.finish(proc)[1], longrun.STALLED)
        child = self.state(rundir)["child"]
        self.assertFalse(self.frozen(child))
        waiting = self.client("wait", "--freeze-on-battery",
                              os.path.basename(rundir))
        self.until_frozen(rundir)
        self.assertTrue(self.frozen(child))
        self.switch("wall")
        self.until_frozen(rundir, False)
        self.release(gate)
        output, code = self.finish(waiting)
        self.assertEqual(code, 0)
        self.assertIn("frozen for ", output)

    def test_time_frozen_is_not_silence(self):
        quiet = 0.02 * 60
        ready = self.fifo("ready")
        # The command's output is a line it has not finished, which is
        # judged only when it ends or a stall falls due.
        proc, rundir = self.start("--quiet", "0.02", "--freeze-on-battery",
                                  f"printf 'tests ...'; echo > {ready}; "
                                  f"sleep 300")
        with open(ready) as f:
            f.read()
        self.switch("battery")
        self.until_frozen(rundir)
        # Frozen for longer than a stall takes to fall due.
        time.sleep(3 * quiet)
        thawed = time.monotonic()
        self.switch("wall")
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertGreaterEqual(time.monotonic() - thawed, quiet)
        self.assertIn("STALLED, no output for 1s.", report)
        self.longrun("stop", rundir)

    def test_a_frozen_run_ends_without_waiting_to_be_killed(self):
        self.switch("battery")
        proc, rundir = self.start("--freeze-on-battery", "sleep 300")
        self.until_frozen(rundir)
        result = self.longrun("stop", rundir)
        self.assertIn("killed by SIGTERM, stopped by longrun stop",
                      result.stdout)
        self.finish(proc)

    def test_a_process_stopped_before_the_freeze_stays_stopped(self):
        pid = os.path.join(self.base, "pid")
        proc, rundir = self.start(
            "--freeze-on-battery", f"sleep 300 & kill -STOP $!; "
            f"echo $! > {pid}.new; mv {pid}.new {pid}; sleep 300")
        created(pid)
        with open(pid) as f:
            stopped = int(f.read())
        self.addCleanup(longrun.signal_all, [stopped], None, signal.SIGKILL)
        child = self.state(rundir)["child"]
        self.switch("battery")
        self.until_frozen(rundir)
        self.assertTrue(self.frozen(child))
        self.switch("wall")
        self.until_frozen(rundir, False)
        self.assertFalse(self.frozen(child))
        self.assertTrue(self.frozen(stopped))
        os.kill(stopped, signal.SIGKILL)
        self.longrun("stop", rundir)
        self.finish(proc)

    def test_stop_hook_sends_the_agent_back_to_a_run_on_battery(self):
        scratchpad = tempfile.mkdtemp(dir=self.base)
        self.env["LONGRUN_DIR"] = os.path.join(scratchpad, "longrun")
        proc, rundir = self.start("sleep 300")
        self.switch("battery")
        self.assertEqual(self.finish(proc)[1], longrun.STALLED)
        stop = {"hook_event_name": "Stop", "scratchpad_dir": scratchpad,
                "session_id": "test-session"}
        reply = longrun.hook(stop)
        self.assertEqual(reply["decision"], "block")
        self.assertIn(os.path.basename(rundir), reply["reason"])
        self.assertIn("switched to battery power", reply["reason"])
        self.assertEqual(self.state(rundir)["stalls"], 0)
        self.longrun("stop", rundir)
        self.assertIsNone(longrun.hook(stop))

    def test_run_directory_of_another_user_is_refused(self):
        self.env["LONGRUN_DIR"] = "/usr"
        result = self.longrun("list")
        self.assertIn("/usr belongs to another user", result.stderr)
        self.assertNotEqual(result.returncode, 0)


class Hooks(unittest.TestCase):
    def pre(self, command, **event):
        return longrun.hook(dict({"hook_event_name": "PreToolUse",
                                  "tool_name": "Bash",
                                  "tool_input": {"command": command}},
                                 **event))

    def test_pre_tool_use_makes_the_call_block(self):
        reply = longrun.hook({
            "hook_event_name": "PreToolUse", "tool_name": "Bash",
            "tool_input": {"command": "cd x && longrun run make",
                           "description": "Build", "timeout": 60000}})
        self.assertEqual(reply["hookSpecificOutput"], {
            "hookEventName": "PreToolUse",
            "updatedInput": {"command": "cd x && longrun run make",
                             "description": "Build",
                             "timeout": 2147483647}})

    def test_pre_tool_use_in_a_subagent_names_the_agent(self):
        for command, expected in [
                ("cd x && X=1 longrun run make",
                 "cd x && LONGRUN_AGENT=a1f3 X=1 longrun run make"),
                ("time ~/bin/longrun.py run make",
                 "time LONGRUN_AGENT=a1f3 ~/bin/longrun.py run make"),
                ("nohup nice -n 5 longrun run make",
                 "LONGRUN_AGENT=a1f3 nohup nice -n 5 longrun run make"),
                ("longrun wait r1", "longrun wait r1")]:
            reply = self.pre(command, agent_id="a1f3")
            rewritten = reply["hookSpecificOutput"]["updatedInput"]["command"]
            self.assertEqual(rewritten, "sleep 0; " + expected)
            # The shell agents use passes the assignment to the command.
            probe = re.sub(r"\S*longrun(\.py)? \w+ \w+$", "env", rewritten)
            out = subprocess.run(["/bin/zsh", "-c", probe.replace(
                "cd x && ", "")], capture_output=True, text=True).stdout
            self.assertEqual("LONGRUN_AGENT=a1f3" in out.split("\n"),
                             "wait" not in command, probe)

    def test_other_commands_are_left_alone(self):
        for command in ["make test", "echo longrun run x", "git log longrun",
                        "ls ~/dev/contraptions/longrun", "exec longrun run x"]:
            self.assertIsNone(self.pre(command), command)
        for command in ["~/dev/contraptions/longrun/longrun.py wait r1",
                        "cd x && longrun stop r1", "(longrun run make)",
                        "longrun wait --freeze-on-battery r1",
                        "X=1 longrun run make", "env A=b longrun run make",
                        "nohup longrun run make"]:
            self.assertIsNotNone(self.pre(command), command)

    def test_quoted_text_and_here_documents_are_left_alone(self):
        for command in ["git commit -m 'then longrun run tests'",
                        'echo "do; longrun run x"',
                        "cat <<EOF\nlongrun run x\nEOF"]:
            self.assertIsNone(self.pre(command, agent_id="a1"), command)
        reply = self.pre("git commit -m 'a; longrun run b' && longrun run c",
                         agent_id="a1")
        self.assertEqual(
            reply["hookSpecificOutput"]["updatedInput"]["command"],
            "sleep 0; git commit -m 'a; longrun run b' && "
            "LONGRUN_AGENT=a1 longrun run c")

    def test_background_calls_keep_their_timeout(self):
        self.assertIsNone(longrun.hook({
            "hook_event_name": "PreToolUse",
            "tool_input": {"command": "longrun run make",
                           "run_in_background": True}}))
        reply = longrun.hook({
            "hook_event_name": "PreToolUse", "agent_id": "a1",
            "tool_input": {"command": "longrun run make",
                           "run_in_background": True}})
        self.assertEqual(reply["hookSpecificOutput"]["updatedInput"], {
            "command": "LONGRUN_AGENT=a1 longrun run make",
            "run_in_background": True})

    def test_every_run_in_a_call_names_the_agent(self):
        for command, expected in [
                ("longrun wait r; longrun run x",
                 "longrun wait r; LONGRUN_AGENT=a1 longrun run x"),
                ("{ longrun run x; }; longrun run y",
                 "{ LONGRUN_AGENT=a1 longrun run x; }; "
                 "LONGRUN_AGENT=a1 longrun run y"),
                ("env -i PATH=/bin longrun run x",
                 "env -i PATH=/bin longrun run x")]:
            reply = self.pre(command, agent_id="a1")
            self.assertEqual(
                reply["hookSpecificOutput"]["updatedInput"]["command"],
                "sleep 0; " + expected)

    def test_subagent_calls_start_with_sleep(self):
        # Claude Code does not move a call whose first command is `sleep`
        # to the background, which it otherwise does to a subagent's call
        # after a few minutes, whatever the call's timeout.
        for command, expected in [
                ("longrun stop r1", "sleep 0; longrun stop r1"),
                ("  cd x\nlongrun wait r1", "sleep 0;   cd x\nlongrun wait r1"),
                ("sleep 1; longrun wait r1", "sleep 1; longrun wait r1"),
                ("sleeper; longrun wait r1",
                 "sleep 0; sleeper; longrun wait r1")]:
            reply = self.pre(command, agent_id="a1")
            self.assertEqual(
                reply["hookSpecificOutput"]["updatedInput"]["command"],
                expected, command)
        # Whatever the form of the subagent's id.
        reply = self.pre("longrun run x", agent_id="a1.f3")
        self.assertEqual(
            reply["hookSpecificOutput"]["updatedInput"]["command"],
            "sleep 0; longrun run x")
        # The main agent has no such limit.
        reply = self.pre("longrun wait r1")
        self.assertEqual(
            reply["hookSpecificOutput"]["updatedInput"]["command"],
            "longrun wait r1")

    def test_subagent_stop_without_a_scratchpad(self):
        base = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, base)
        old = os.environ.get("LONGRUN_DIR")
        os.environ["LONGRUN_DIR"] = base
        try:
            self.assertIsNone(longrun.hook({
                "hook_event_name": "SubagentStop", "agent_id": "a1"}))
        finally:
            if old is None:
                os.environ.pop("LONGRUN_DIR")
            else:
                os.environ["LONGRUN_DIR"] = old


class Reports(unittest.TestCase):
    def test_a_late_report_is_not_told_again(self):
        rundir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rundir)
        longrun.save(rundir, "spec.json", {"quiet": 60})
        listener = socket.socket(socket.AF_UNIX)
        self.addCleanup(listener.close)
        supervisor = longrun.Supervisor(rundir, listener)
        told = []
        supervisor.tell = told.append
        supervisor.written = 2
        os.write(supervisor.reports[1], b"1\n")
        supervisor.reported()
        self.assertEqual((told, supervisor.written), ([], 2))
        os.write(supervisor.reports[1], b"3\n")
        supervisor.reported()
        self.assertEqual((told, supervisor.written), (["stall 3"], 3))

    def test_a_report_is_told_once_to_each_waiting_call(self):
        rundir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rundir)
        longrun.save(rundir, "spec.json", {"quiet": 60})
        listener = socket.socket(socket.AF_UNIX)
        self.addCleanup(listener.close)
        supervisor = longrun.Supervisor(rundir, listener)
        pairs = {}
        for name, told in (("leaving", True), ("waiting", False)):
            pairs[name] = socket.socketpair()
            for end in pairs[name]:
                self.addCleanup(end.close)
            pairs[name][1].setblocking(False)
            supervisor.clients[pairs[name][0].fileno()] = [pairs[name][0],
                                                           told]

        def heard(name):
            try:
                return pairs[name][1].recv(64)
            except BlockingIOError:
                return b""
        os.write(supervisor.reports[1], b"1\n")
        supervisor.reported()
        self.assertEqual((heard("waiting"), heard("leaving"), supervisor.told),
                         (b"stall 1\n", b"", 1))
        # Every call has been told something now, so the next report is
        # kept for the next call that waits.
        os.write(supervisor.reports[1], b"2\n")
        supervisor.reported()
        self.assertEqual((heard("waiting"), heard("leaving"), supervisor.told),
                         (b"", b"", 1))
        supervisor.tell("done", everyone=True)
        self.assertEqual((heard("waiting"), heard("leaving")),
                         (b"done\n", b"done\n"))

    def test_a_timing_file_that_cannot_be_written_is_removed(self):
        rundir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rundir)
        longrun.save(rundir, "spec.json", {"quiet": 60})
        listener = socket.socket(socket.AF_UNIX)
        self.addCleanup(listener.close)
        supervisor = longrun.Supervisor(rundir, listener)
        path = os.path.join(rundir, "times")
        with open(path, "wb") as f:
            f.write(longrun.HEADER.pack(4, 48))
        supervisor.timing_fd = os.open(path, os.O_RDONLY)
        supervisor.write_record()
        self.assertEqual((supervisor.timing_fd, supervisor.records_written),
                         (None, 0))
        self.assertFalse(os.path.exists(path))
        supervisor.write_record()
        self.assertEqual(longrun.records(rundir), [])

    def test_a_stall_is_reported_when_the_supervisor_is_gone_after_it(self):
        rundir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rundir)
        with open(os.path.join(rundir, "stall-1.txt"), "w") as f:
            f.write("the report\n")
        conn, supervisor = socket.socketpair()
        self.addCleanup(conn.close)
        supervisor.sendall(b"running 1\nstall 1\n")
        supervisor.close()
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(longrun.attach(rundir, conn), longrun.STALLED)
        self.assertEqual(output.getvalue(), "the report\n")


class Power(unittest.TestCase):
    def test_the_power_source_comes_from_macos(self):
        with unittest.mock.patch.dict(os.environ):
            os.environ.pop("LONGRUN_POWER", None)
            fd = longrun.power_events()
            self.addCleanup(os.close, fd)
            battery = longrun.on_battery()
        source = subprocess.run(["pmset", "-g", "batt"], capture_output=True,
                                text=True, check=True).stdout
        self.assertEqual(battery, "'Battery Power'" in source, source)
        self.assertNotEqual(battery, "'AC Power'" in source, source)


class Timing(unittest.TestCase):
    """The timing file, and what reports say from it, from files written
    here."""

    def rundir(self, rows, header=(4, 72), pad=b"", log=b""):
        """A run directory whose timing file holds a record for each row
        of (size, lines, at, output, line) or (size, lines, at, output,
        line, longest start, longest end, longest line), times in seconds,
        each followed by pad."""
        rundir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rundir)
        with open(os.path.join(rundir, "times"), "wb") as f:
            f.write(longrun.HEADER.pack(*header)
                    + b"\xff" * (header[0] - 4))
            for size, lines, at, output, line, *longest in rows:
                quiet, heard, before = longest or (0, 0, 0)
                f.write(longrun.RECORD.pack(
                    size, lines, round(at * 1e9), round(at * 1e9) + 10**18,
                    round(output * 1e9), line, round(quiet * 1e9),
                    round(heard * 1e9), before) + pad)
        with open(os.path.join(rundir, "log"), "wb") as f:
            f.write(log)
        return rundir

    def test_records(self):
        rows = [(0, 0, 100, 100, 0), (6, 1, 101.5, 101.2, 1, 100, 101.2, 0),
                (12, 2, 130, 101.2, 1, 100, 101.2, 0)]
        found = longrun.records(self.rundir(rows))
        self.assertEqual(found[1], longrun.Record(
            6, 1, 101.5, 1e9 + 101.5, 101.2, 1,
            longrun.Silence(100, 101.2, 0)))
        self.assertEqual(len(found), 3)
        # Readers skip what later versions add to the header and to each
        # record.
        self.assertEqual(longrun.records(
            self.rundir(rows, (12, 80), b"\xee" * 8)), found)
        self.assertEqual(longrun.records(self.rundir(rows), 2), found[:2])

    def test_damaged_or_missing_times(self):
        rows = [(0, 0, 1, 1, 0), (2, 1, 2, 2, 1)]
        rundir = self.rundir(rows)
        path = os.path.join(rundir, "times")
        with open(path, "ab") as f:
            f.write(b"\x01" * 71)
        self.assertEqual(len(longrun.records(rundir)), 2)
        for data in (b"", b"\x04\x00\x48", b"\x04\x00\x48\x00",
                     b"\x02\x00\x48\x00" + b"\x00" * 72,
                     b"\x04\x00\x30\x00" + b"\x00" * 72):
            with open(path, "wb") as f:
                f.write(data)
            self.assertEqual(longrun.records(rundir), [], data)
        os.remove(path)
        self.assertEqual(longrun.records(rundir), [])

    def supervisor(self):
        """A supervisor that has not started a command, and the time from
        which it counts silence."""
        rundir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rundir)
        longrun.save(rundir, "spec.json", {"quiet": 600})
        listener = socket.socket(socket.AF_UNIX)
        self.addCleanup(listener.close)
        supervisor = longrun.Supervisor(rundir, listener)
        return supervisor, supervisor.last

    def test_the_longest_silence_is_counted_between_pieces(self):
        supervisor, t = self.supervisor()
        supervisor.heard(t + 3, 1, (1, 2), False)
        # A line of dots, judged when it ends: the silences between the
        # dots count, not the time since the line before.
        dots = [(t + 10, b"."), (t + 50, b"."), (t + 60, b".")]
        supervisor.heard(t + 70, 2, (2, 7), False, dots + [(t + 70, b"]")])
        self.assertEqual(supervisor.longest,
                         longrun.Silence(t + 10, t + 50, 2))
        self.assertEqual((supervisor.last, supervisor.last_line), (t + 70, 2))
        # Pieces counted already, and earlier output, change nothing.
        supervisor.heard(t + 70, 2, (2, 7), True, dots)
        supervisor.heard(t + 60, 1, (1, 2), False)
        self.assertEqual(supervisor.longest,
                         longrun.Silence(t + 10, t + 50, 2))
        self.assertEqual((supervisor.last, supervisor.last_line,
                          supervisor.last_warned), (t + 70, 2, True))
        # Pieces that hold only white space or colour codes are not
        # output.
        supervisor.heard(t + 200, 3, (3, 20), False,
                         [(t + 80, b"\x1b[32m"), (t + 90, b"  "),
                          (t + 100, b"x\x1b[0m"), (t + 200, b"y")])
        self.assertEqual(supervisor.longest,
                         longrun.Silence(t + 100, t + 200, 3))
        # Nor is a colour code split between what was read before and
        # what has just been read.
        supervisor.line = [(t + 210, b"a"), (t + 260, b"\x1b[3")]
        supervisor.ended(t + 400, b"2mb", 3, 20)
        self.assertEqual(supervisor.longest,
                         longrun.Silence(t + 210, t + 400, 4))
        # A colour code split across pieces is not output either.
        self.assertEqual(longrun.output_times(
            [(1, b"a\x1b[3"), (2, b"2m"), (3, b" \x1b[0m"), (4, b"z")]),
            [1, 4])
        # The first output ends the silence that the run started with.
        quiet, t = self.supervisor()
        quiet.heard(t + 200, 1, (1, 2), False)
        self.assertEqual(quiet.longest, longrun.Silence(t, t + 200, 0))

    def test_a_record_waits_a_second_after_the_last(self):
        supervisor, t = self.supervisor()
        path = os.path.join(supervisor.rundir, "times")
        supervisor.timing_fd = os.open(path, os.O_WRONLY | os.O_CREAT)
        self.addCleanup(lambda: supervisor.timing_fd is None or
                        os.close(supervisor.timing_fd))
        supervisor.write_record()
        supervisor.extent = (1, 2)
        supervisor.record()
        self.assertEqual(supervisor.record_at, supervisor.recorded_at + 1)
        self.assertEqual(os.path.getsize(path), longrun.RECORD.size)
        supervisor.recorded_at -= 1
        supervisor.record()
        self.assertIsNone(supervisor.record_at)
        self.assertEqual(os.path.getsize(path), 2 * longrun.RECORD.size)
        # Nothing has changed since, so no record is due.
        supervisor.recorded_at -= 1
        supervisor.record()
        self.assertEqual(os.path.getsize(path), 2 * longrun.RECORD.size)

    def test_stall_analysis_of_steady_output_that_stopped(self):
        rows = [(0, 0, 0, 0, 0)] + [(100 * i, 10 * i, 60 * i, 60 * i - 1,
                                     10 * i, 0, 59, 0) for i in range(1, 6)]
        found = longrun.records(self.rundir(
            rows + [(500, 50, 600, 299, 50, 59, 119, 10)]))
        text = longrun.analysis(found, 51)
        self.assertEqual(text, (
            "The log so far: 500B in 51 lines over 10m00s. Bytes written to "
            "it in each 1m00s since the run started: 100, 100, 100, 100, "
            "100, 0, 0, 0, 0, 0.\n"
            "Before this silence, the longest silence was 1m00s after its "
            "output in line 10 (from 59s to 1m59s after the start).\n"
            "Each line of the log below starts with how long after the start "
            "it was complete."))

    def test_stall_analysis_of_output_that_was_always_slow(self):
        rows = [(0, 0, 0, 0, 0), (10, 1, 480, 480, 1, 0, 480, 0),
                (20, 2, 1080, 1080, 2, 480, 1080, 1),
                (20, 2, 1500, 1080, 2, 480, 1080, 1)]
        found = longrun.records(self.rundir(rows))
        text = longrun.analysis(found, 2)
        self.assertIn("each 5m00s since the run started: 0, 10, 0, 10, 0.\n",
                      text)
        self.assertIn("the longest silence was 10m00s after its output in "
                      "line 1 (from 8m00s to 18m00s after the start).", text)
        quiet = longrun.records(self.rundir([(0, 0, 0, 0, 0),
                                             (0, 0, 700, 0, 0)]))
        self.assertIn("This silence started when the run did.",
                      longrun.analysis(quiet, 0))
        steady = longrun.records(self.rundir([(0, 0, 0, 0, 0),
                                              (5, 1, 1, 0.5, 1, 0, 0.5, 0),
                                              (9, 2, 50, 50, 2, 0.5, 50, 1),
                                              (9, 2, 700, 50, 2, 0.5, 50, 1)]))
        self.assertIn("\nBefore this silence, the command never went a "
                      "minute without output.",
                      longrun.analysis(steady, 2))

    def test_rhythm_parts(self):
        def text(now):
            found = longrun.records(self.rundir([(0, 0, 0, 0, 0),
                                                 (50, 1, 30, 30, 1)]))
            return longrun.rhythm(found, now)
        # A part shorter than a second is counted in the one before.
        self.assertEqual(text(600.4), "Bytes written to it in each 1m00s since "
                         "the run started: 50, 0, 0, 0, 0, 0, 0, 0, 0, 0.")
        self.assertEqual(text(605), "Bytes written to it in each 1m00s since the "
                         "run started: 50, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0 (the "
                         "last part covers only 5s).")
        self.assertEqual(text(5), "Bytes written to it in each 10s since the run "
                         "started: 0 (the last part covers only 5s).")
        # Runs longer than 12 days are shown in parts of whole days.
        self.assertTrue(text(12 * 86400 + 0.5).startswith(
            "Bytes written to it in each 24h00m since the run started: 50, 0, "))
        self.assertTrue(text(30 * 86400).startswith(
            "Bytes written to it in each 72h00m since the run started: 50, 0, "))

    def test_amounts(self):
        self.assertEqual([longrun.amount(n) for n in
                          (0, 999, 1000, 1500, 9_999, 12_345, 999_499,
                           999_500, 2_500_000, 2.5e9, 3e12)],
                         ["0", "999", "1.0K", "1.5K", "10K", "12K", "999K",
                          "1.0M", "2.5M", "2.5G", "3000G"])

    def test_line_stamps(self):
        found = longrun.records(self.rundir([
            (0, 0, 0, 0, 0), (4, 2, 1, 0.5, 2), (6, 2, 65, 65, 3),
            (8, 3, 70, 70, 3), (10, 3, 200, 200, 4)]))
        lines = ["a", "b", "cc", "d"]
        self.assertEqual(longrun.stamps(found, 0, lines, (3, 10)),
                         ["1s", "1s", "1m10s", "3m20s"])
        self.assertEqual(longrun.stamps(found, 2, lines[2:], (3, 10)),
                         ["1m10s", "3m20s"])
        # A line that had not ended at a size the records reach.
        self.assertEqual(longrun.stamps(found, 1, lines[1:3], (2, 6)),
                         ["1s", "1m05s"])
        shown = longrun.section(lines, [0, 3], 0,
                                times=["1s", "1s", "1m10s", "3m20s"])
        self.assertEqual(shown, "   1s 1| a\n[2 lines left out]\n"
                                "3m20s 4| d")

    def test_longest_silence_in_the_result(self):
        log = b"a\nb\nc\n"
        rows = [(0, 0, 0, 0, 0), (2, 1, 1, 0.5, 1, 0, 0.5, 0),
                (4, 2, 75, 75, 2, 0.5, 75, 1), (6, 3, 80, 79, 3, 0.5, 75, 1),
                (6, 3, 90, 79, 3, 0.5, 75, 1)]
        result = longrun.summary(self.rundir(rows, log=log),
                                 {"display": "x", "keep": False},
                                 {"exit": 0, "started": 0, "ended": 90},
                                 (3, 6))
        self.assertIn("\nlongest silence: 1m15s after its output in line 1 "
                      "(from 0s to 1m15s after the start)\n", result)
        # The silence that lasted until the end counts too.
        rows[-1] = (6, 3, 200, 79, 3, 0.5, 75, 1)
        result = longrun.summary(self.rundir(rows, log=log),
                                 {"display": "x", "keep": False},
                                 {"exit": 0, "started": 0, "ended": 200},
                                 (3, 6))
        self.assertIn("\nlongest silence: 2m01s after its output in line 3 "
                      "(from 1m19s to 3m20s after the start)\n", result)
        short = longrun.summary(self.rundir(rows[:2] + [(6, 3, 30, 29, 3,
                                                          0, 0.5, 0)],
                                            log=log),
                                {"display": "x", "keep": False},
                                {"exit": 0, "started": 0, "ended": 30},
                                (3, 6))
        self.assertNotIn("longest silence", short)


class Summaries(unittest.TestCase):
    def summary_start(self, text):
        lines = longrun.clean(text)
        chosen = longrun.extract(lines)
        return None if chosen is None else lines[chosen[0]]

    def test_pytest(self):
        text = textwrap.dedent("""\
            ....F.
            =================================== FAILURES ===================================
            ___________________________________ test_x ____________________________________
                def test_x():
            >       assert 1 == 2
            E       assert 1 == 2
            =========================== short test summary info ============================
            FAILED test_a.py::test_x - assert 1 == 2
            ========================= 1 failed, 5 passed in 0.12s ==========================
            """)
        self.assertEqual(self.summary_start(text),
                         "=========================== short test summary info "
                         "============================")
        passing = "......\n============ 6 passed in 0.10s ============\n"
        self.assertEqual(self.summary_start(passing),
                         "============ 6 passed in 0.10s ============")

    def test_summary_blocks(self):
        text = textwrap.dedent("""\
            ..F.
            ============ 1 failed, 3 passed in 0.20s ============
            === unit test summary: FAILED: 1 failed, 3 passed in 1m35s ===
            FAILED tests/test_a.py::test_x
              assert 1 == 2
            """)
        self.assertEqual(self.summary_start(text), "=== unit test summary: "
                         "FAILED: 1 failed, 3 passed in 1m35s ===")
        plain = ("ok | 3 passed\n" + "=" * 24 + " test summary " + "=" * 24
                 + "\nFAILED: 1 of 4 tasks\nLogs: /tmp/x\n")
        self.assertEqual(self.summary_start(plain),
                         "=" * 24 + " test summary " + "=" * 24)
        self.assertIsNone(self.summary_start("my test summary: fine\n"))

    def test_summary_blocks_of_scripts_that_are_not_tests(self):
        text = ("=== build ===\nbuilt\n=== bench summary: 'U0' is live ===\n"
                "    gateway: http://127.0.0.1:9966\n\n    Pinned.\n")
        self.assertEqual(self.summary_start(text),
                         "=== bench summary: 'U0' is live ===")
        for line in ("=== e2e test summary ===", "=== summary: 3 passed"):
            self.assertEqual(self.summary_start("x\n" + line + "\ny\n"), line)
        # A header without an outcome is a section of some tool's output,
        # such as a coverage table, not a report.
        for line in ("==== deploy summary ====", "=== Coverage summary ===",
                     "=== nosummary: x ===", "=== contest summary ===",
                     "=== a summary of what follows",
                     "summary: fine", "=== summary"):
            self.assertIsNone(self.summary_start(line + "\nmore\n"), line)

    def test_pytest_warnings_summary_is_not_the_summary(self):
        text = textwrap.dedent("""\
            ..F
            =================================== FAILURES ===================================
            E       assert 1 == 2
            =============================== warnings summary ===============================
            tests/test_a.py::test_y
              DeprecationWarning: old
            =========================== short test summary info ============================
            FAILED test_a.py::test_x - assert 1 == 2
            ===================== 1 failed, 2 passed, 1 warning in 0.12s ===================
            """)
        self.assertEqual(self.summary_start(text),
                         "=========================== short test summary info "
                         "============================")

    def test_summary_header_from_the_environment(self):
        gate = ("gate: row 3 OK\nbuilding\n"
                "gate: FAILED, 212 rows in 5m3s, 2 failed\nrow 7 FAILED\n")
        self.assertIsNone(self.summary_start(gate))
        old = os.environ.get("LONGRUN_SUMMARY")
        os.environ["LONGRUN_SUMMARY"] = r"^gate: (PASSED|FAILED)"
        try:
            self.assertEqual(self.summary_start(gate),
                             "gate: FAILED, 212 rows in 5m3s, 2 failed")
        finally:
            if old is None:
                os.environ.pop("LONGRUN_SUMMARY")
            else:
                os.environ["LONGRUN_SUMMARY"] = old

    def test_deno(self):
        text = textwrap.dedent("""\
            running 2 tests from ./a_test.ts
            adds ... ok (1ms)
            fails ... \x1b[31mFAILED\x1b[0m (2ms)

            \x1b[1m\x1b[37m\x1b[41m ERRORS \x1b[0m

            fails => ./a_test.ts:5:6
            error: AssertionError: Values are not equal.

            \x1b[1m\x1b[37m\x1b[41m FAILURES \x1b[0m

            fails => ./a_test.ts:5:6

            FAILED | 1 passed | 1 failed (10ms)
            """)
        self.assertEqual(self.summary_start(text), " ERRORS")
        passing = ("running 1 test from ./a_test.ts\nadds ... ok\n\n"
                   "ok | 1 passed | 0 failed (5ms)\n")
        self.assertEqual(self.summary_start(passing),
                         "ok | 1 passed | 0 failed (5ms)")

    def test_xctest(self):
        lines = longrun.clean(textwrap.dedent("""\
            Test Suite 'All tests' started at 2026-10-26 11:07:48.
            Test Case '-[PeopleTests testMerge]' started.
            Test Case '-[PeopleTests testMerge]' passed (0.001 seconds).
            Test Case '-[PeopleTests testSplit]' started.
            /src/PeopleTests.swift:41: error: -[PeopleTests testSplit] : XCTAssertEqual failed: ("1") is not equal to ("2")
            Test Case '-[PeopleTests testSplit]' failed (0.002 seconds).
            noise
            Test Suite 'All tests' failed at 2026-10-26 11:07:59.749.
            \t Executed 2 tests, with 1 failure (0 unexpected) in 0.003 (0.003) seconds
            """))
        shown = longrun.section(lines, longrun.extract(lines))
        self.assertIn("5| /src/PeopleTests.swift:41: error:", shown)
        self.assertIn("6| Test Case '-[PeopleTests testSplit]' failed", shown)
        self.assertIn("[1 line left out]", shown)
        self.assertIn("9| \t Executed 2 tests, with 1 failure", shown)
        self.assertNotIn("testMerge", shown)
        self.assertNotIn("noise", shown)

    def test_xcodebuild(self):
        lines = longrun.clean(textwrap.dedent("""\
            CompileSwift normal arm64 /src/A.swift
            /src/A.swift:3:9: error: cannot find 'x' in scope
            CompileSwift normal arm64 /src/B.swift
            ** BUILD FAILED **

            The following build commands failed:
            \tCompileSwift normal arm64 /src/A.swift
            """))
        shown = longrun.section(lines, longrun.extract(lines))
        self.assertIn("2| /src/A.swift:3:9: error: cannot find 'x'", shown)
        self.assertIn("4| ** BUILD FAILED **", shown)
        self.assertIn("7| \tCompileSwift normal arm64 /src/A.swift", shown)
        self.assertNotIn("B.swift", shown)

    def shown(self, text, code):
        """What a result shows of a log holding text, for a command that
        exited with code."""
        rundir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rundir)
        with open(os.path.join(rundir, "log"), "w") as f:
            f.write(text)
        extent = (text.count("\n"), len(text.encode()))
        return longrun.summary(rundir, {"display": "x", "keep": False},
                               {"exit": code, "started": 0, "ended": 1},
                               extent)

    def test_failures_before_the_last_lines_are_shown(self):
        text = "".join(f"PASS check {i}\n" for i in range(100))
        text = text.replace("PASS check 7\n", "FAIL  check 7\n")
        text += "1 FAILURE(S)\n"
        failed = self.shown(text, 1)
        self.assertIn(" 8| FAIL  check 7\n", failed)
        self.assertIn("[53 lines left out]", failed)
        self.assertIn("101| 1 FAILURE(S)", failed)
        self.assertNotIn("PASS check 7\n", failed)
        self.assertNotIn("FAIL", self.shown(text.replace("1 FAIL", "0 FAIL"),
                                            0).split("100|")[0])
        self.assertNotIn("longest silence", failed)

    def test_every_failed_build_is_shown(self):
        lines = longrun.clean("one\n** BUILD FAILED **\nx\nx\nx\n"
                              "** BUILD SUCCEEDED **\n")
        shown = longrun.section(lines, longrun.extract(lines))
        self.assertIn("2| ** BUILD FAILED **", shown)
        self.assertIn("6| ** BUILD SUCCEEDED **", shown)

    def test_unrecognised_output(self):
        self.assertIsNone(self.summary_start("compiling\nlinking\ndone\n"))

    def test_line_counts_include_blank_and_unfinished_lines(self):
        rundir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rundir)
        for data, total in [(b"a\n\n\n", 3), (b"a\nb", 2), (b"", 0)]:
            with open(os.path.join(rundir, "log"), "wb") as f:
                f.write(data)
            extent = (data.count(b"\n"), len(data))
            self.assertEqual(longrun.log_tail(rundir, extent)[2], total)

    def test_escape_codes_do_not_swallow_lines(self):
        link = "\x1b]8;;http://x\x1b\\link\x1b]8;;\x1b\\"
        self.assertEqual(longrun.clean(f"a\n{link}\nc\nd\x07\ne\n"),
                         ["a", "link", "c", "d\x07", "e"])
        self.assertEqual(longrun.clean("\x1b[31mred\x1b[0m\n"), ["red"])

    def test_progress_lines_keep_what_a_terminal_shows(self):
        self.assertEqual(longrun.clean("10%\r50%\r100%\ndone\n"),
                         ["100%", "done"])

    def test_the_end_of_a_large_log_keeps_its_line_numbers(self):
        rundir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, rundir)
        with open(os.path.join(rundir, "log"), "wb") as f:
            f.write(b"".join(b"line %d\n" % i for i in range(1, 200001)))
        extent = (200000, os.path.getsize(os.path.join(rundir, "log")))
        first, lines, total = longrun.log_tail(rundir, extent, 100000)
        self.assertEqual(total, 200000)
        self.assertEqual(first + len(lines), 200000)
        self.assertEqual(lines[0], f"line {first + 1}")
        self.assertEqual(lines[-1], "line 200000")
        self.assertLess(len(lines), 20000)
        shown = longrun.section(lines, [len(lines) - 1], first)
        self.assertEqual(shown, "200000| line 200000")
        whole = longrun.log_tail(rundir, extent)
        self.assertEqual(whole[:1] + (len(whole[1]),) + whole[2:],
                         (0, 200000, 200000))
        # Bytes written after the extent was taken are not read.
        with open(os.path.join(rundir, "log"), "ab") as f:
            f.write(b"later\n")
        self.assertEqual(longrun.log_tail(rundir, extent, 100)[1][-1],
                         "line 200000")


class HangWarnings(unittest.TestCase):
    def real(self, *pieces):
        """When the real output in a line made of pieces arrived, piece n
        arriving at time n, or None."""
        return self.judge(*pieces)[0]

    def judge(self, *pieces):
        return longrun.judge(
            [(n, piece.encode()) for n, piece in enumerate(pieces, 1)],
            longrun.hang_patterns())

    def test_warnings_and_stack_dumps(self):
        for line in (
                "'slow test' has been running for over (1m0s)",
                "\x1b[0m\x1b[1m\x1b[33m'slow' has been running for over "
                "\x1b[0m\x1b[38;5;245m(1m0s)\x1b[0m\x1b[0m",
                "'it's slow' has been running for over (64m0s)",
                "'a' has been running for over (1s)",
                "test tests::slow_one has been running for over 60 seconds",
                "        SLOW [> 60.000s] my-crate tests::slow_one",
                "  TRY 2 SLOW [>120.000s] my-crate tests::slow_one",
                "stuck-test: tests/a.py::test_x has run for 300 seconds; "
                "every thread's stack follows, and the test carries on.",
                "stuck-test: end of the stacks for tests/a.py::test_x.",
                "stuck-test: tests/a.py::test_x has run for 600 seconds; it "
                "started no process that is still running, so nothing is "
                "killed.",
                "Timeout (0:05:00)!",
                "Timeout (0:00:00.300000)!",
                "Timeout (0:05:00)!\r",
                "Thread 0x000000016c573000 (most recent call first):",
                "Thread 0x000000016de17000 [worker] (most recent call first):",
                "Current thread 0x00000001f1fae180 (most recent call first):",
                "Stack (most recent call first):",
                '  File "/x/lib/python3.12/threading.py", line 1012 in run',
                '  File "<string>", line 5 in f',
                "  <no Python frame>",
                "  ...",
                "",
                "   "):
            self.assertIsNone(self.real(line), line)

    def test_output(self):
        for line in (
                "....F...........                 [ 43%]",
                "adds ... ok (1m5s)",
                " ok (4s)",
                '  File "x.py", line 3, in f',
                "1 passed in 2.3s",
                "     TIMEOUT [ 60.001s] my-crate tests::slow_one",
                " TERMINATING [>120.000s] my-crate tests::slow_one",
                "panic: test timed out after 10m0s",
                "+" * 35 + " Timeout " + "+" * 35,
                "Test Case '-[A b]' exceeded execution time allowance of 10 "
                "minutes.",
                "the build has been running for a while"):
            self.assertEqual(self.judge(line), (1, False), line)
        # pytest's dot before faulthandler's frame, from another process.
        self.assertEqual(self.judge('.  File "x.py", line 3 in f'),
                         (1, True))

    def test_output_before_a_warning_counts_from_when_it_arrived(self):
        # Deno writes a test's name when it starts, and its warning on the
        # same line a minute later.
        self.assertEqual(
            self.real("slow ...", "'slow' has been running for over (1m0s)"),
            1)
        # pytest writes its dots without a line break.
        self.assertEqual(self.real(
            "..", "..", "stuck-test: x has run for 600 seconds; it started "
            "no process that is still running, so nothing is killed."), 2)
        # Text that arrives with the warning is part of it.
        self.assertIsNone(self.real(
            "test x has been running ", "for over 60 seconds"))
        self.assertEqual(self.real(
            "a ...", "'a' has been running", " for over (1m0s)"), 1)
        # A quote in the output before deno's warning starts nothing.
        self.assertEqual(self.real(
            "it's", " a ...", "'it's a' has been running for over (1m0s)"),
            2)
        # Nor does a colour code split between two reads.
        self.assertIsNone(self.real(
            "\x1b[3", "3m'slow' has been running for over (1m0s)\x1b[0m"))
        self.assertEqual(self.real(
            "a ...", "\x1b[0m\x1b[1m\x1b[33m'a' has been running for over "
            "\x1b[0m\x1b[38;5;245m(1m0s)\x1b[0m\x1b[0m"), 1)

    def test_lines_that_hold_warnings(self):
        self.assertEqual(self.judge("slow ...", "'slow' has been running "
                                    "for over (1m0s)"), (1, True))
        self.assertEqual(self.judge("stuck-test: x"), (None, True))
        self.assertEqual(self.judge("ok"), (1, False))
        self.assertEqual(self.judge(""), (None, False))
        # Colour codes are neither output nor warnings.
        self.assertEqual(self.judge("\x1b[2K\x1b[1G"), (None, False))
        self.assertEqual(self.judge("abc", "\x1b[0m"), (1, False))
        # faulthandler's line for more threads than it shows, which
        # pytest's dots are until their line ends.
        self.assertEqual(self.judge("..."), (None, True))
        self.assertEqual(self.judge("...."), (1, False))
        self.assertEqual(longrun.judge([(1, b"...")], longrun.hang_patterns(),
                                       False), (1, False))
        self.assertEqual(longrun.judge([(1, b"stuck-test: x")],
                                       longrun.hang_patterns(), False),
                         (None, True))
        # faulthandler starts its dump in the line pytest is writing.
        self.assertEqual(self.judge("tests/a.py::test_x ",
                                    "Timeout (0:00:01.500000)!"), (1, True))

    def test_long_lines(self):
        # A warning starts in the first LINE bytes of a line, and runs to
        # the end of it.
        self.assertEqual(self.real("." * longrun.LINE, "stuck-test: x"), 2)
        self.assertEqual(self.judge("stuck-test: x", "x" * longrun.LINE),
                         (None, True))
        self.assertEqual(self.judge("..", "stuck-test: " + "x" * longrun.LINE),
                         (1, True))
        self.assertIsNone(self.real(" " * longrun.LINE, "  \n"))
        # Only the start of a line is searched for a warning.
        self.assertEqual(self.judge("'" + "a" * longrun.LINE + "' has been "
                                    "running for over (1m0s)"), (1, False))
        # A long line full of places where deno's warning could start
        # takes as long to judge as any other.
        self.assertEqual(self.real("...'" * 250000), 1)

    def test_hang_warnings_from_the_environment(self):
        self.assertEqual(self.real("tick"), 1)
        with unittest.mock.patch.dict(os.environ, {"LONGRUN_HANG": "^tick$"}):
            self.assertIsNone(self.real("tick"))
            self.assertEqual(self.real("ticks"), 1)
            self.assertIsNone(self.real("Timeout (0:05:00)!"))
        with unittest.mock.patch.dict(os.environ, {"LONGRUN_HANG": "(?i)^t"}):
            self.assertIsNone(self.real("Tick"))
        # A pattern that matches nothing in a line does not make it a
        # warning, though it matches the empty string there.
        with unittest.mock.patch.dict(os.environ,
                                      {"LONGRUN_HANG": r"^\w+ hängt$"}):
            self.assertEqual(self.judge("Prüfung hängt"), (None, True))
            self.assertEqual(self.judge("Prüfung läuft"), (1, False))
        with unittest.mock.patch.dict(os.environ, {"LONGRUN_HANG": "stuck|"}):
            self.assertEqual(self.judge("hello"), (1, False))
            self.assertEqual(self.judge("a stuck"), (1, True))


if __name__ == "__main__":
    unittest.main()
