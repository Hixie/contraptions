"""Tests for longrun. They run real commands and need macOS (kqueue,
`sample`, SIGINFO). Commands that have to wait for the test block on a
FIFO that the test opens when it is ready."""

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
import unittest
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


def alive(pid):
    return pid in longrun.processes()


class Runs(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="longrun-test-\U0001f989-")
        self.env = dict(os.environ, LONGRUN_DIR=self.base)
        self.env.pop("PYTHONPATH", None)
        self.env.pop("LONGRUN_AGENT", None)
        self.env["CLAUDE_CODE_SESSION_ID"] = "test-session"
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

    def test_exit_status_output_and_log(self):
        result = self.longrun("run", "--", "sh", "-c",
                              "echo one; echo two >&2; exit 3")
        self.assertEqual(result.returncode, 3)
        self.assertIn("exit 3 after", result.stdout)
        self.assertIn("1| one", result.stdout)
        self.assertIn("2| two", result.stdout)
        log = result.stdout.split("log: ", 1)[1].split(" (", 1)[0]
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

    def test_stall_hands_back_once_and_run_continues(self):
        gate = self.fifo("gate")
        proc, rundir = self.start("--quiet", "0.04",
                                  f"echo before; read x < {gate}; echo after")
        report, code = self.finish(proc)
        self.assertEqual(code, longrun.STALLED)
        self.assertIn("STALLED, no output for ", report)
        self.assertIn("1| before", report)
        self.assertIn("longrun wait " + os.path.basename(rundir), report)
        self.assertTrue(alive(self.state(rundir)["child"]))
        # The stall was reported, so wait blocks until the command ends.
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
        rundir = os.path.dirname(
            result.stdout.split("log: ", 1)[1].split(" (", 1)[0])
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

    def test_leftover_group_members_are_ended_and_reported(self):
        result = self.longrun("run", "sleep 300 & echo started")
        self.assertEqual(result.returncode, 0)
        self.assertIn("ended processes the command left behind", result.stdout)
        self.assertIn("sleep 300", result.stdout)
        leftover = int(result.stdout.split("left behind: ", 1)[1].split()[0])
        self.assertFalse(alive(leftover))
        clean = self.longrun("run", "sleep 0 & wait; echo started")
        self.assertNotIn("left behind", clean.stdout)

    def test_leftover_that_ignores_sigterm_is_killed(self):
        result = self.longrun(
            "run", "trap '' TERM; (while :; do sleep 1; done) >/dev/null 2>&1 &"
                   " echo started")
        self.assertEqual(result.returncode, 0)
        leftover = int(result.stdout.split("left behind: ", 1)[1].split()[0])
        self.assertFalse(alive(leftover))

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
        proc, watched = self.start("--quiet", "0.04", "sleep 300")
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
            self.assertEqual(rewritten, expected)
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
            "git commit -m 'a; longrun run b' && LONGRUN_AGENT=a1 longrun run c")

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
                expected)

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


if __name__ == "__main__":
    unittest.main()
