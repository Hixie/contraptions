"""Tests for claude-stop-own. They start real processes and run the
script on them, under a temporary HOME that holds a fake
~/dev/commonfabric.

Each test process prints its PID, then blocks reading its standard
input. When the test closes that input, the process prints "exited" and
exits; a process that was signalled first dies without printing it."""

import os
import re
import signal
import subprocess
import sys
import tempfile
import unittest
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "claude-stop-own.py")

SLEEPER = ("import os, sys; print(os.getpid(), flush=True); "
           "sys.stdin.read(); print('exited', flush=True)")

# Starts argv[3:] in directory argv[2], then waits for it if argv[1] is
# "wait" or exits at once if it is "orphan".
LAUNCHER = ("import subprocess, sys; "
            "p = subprocess.Popen(sys.argv[3:], cwd=sys.argv[2]); "
            "sys.argv[1] == 'wait' and p.wait()")


class Process:
    """A test process, started directly, as the child of a launcher
    process (parent="wait"), or as an orphan (parent="orphan")."""

    def __init__(self, test, cwd, args=(), parent=None, child_cwd=None):
        command = [sys.executable, "-c", SLEEPER, *args]
        if parent is not None:
            command = [sys.executable, "-c", LAUNCHER, parent,
                       child_cwd or cwd, *command]
        self.proc = subprocess.Popen(command, cwd=cwd, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE)
        self.output = None
        test.addCleanup(self.finish)
        self.pid = int(self.proc.stdout.readline())
        if parent == "orphan":
            self.proc.wait()
        self.launcher_pid = self.proc.pid if parent == "wait" else None

    def finish(self):
        """Lets the process exit, and returns whether it had been stopped
        by a signal before then."""
        if self.output is None:
            self.proc.stdin.close()
            self.output = self.proc.stdout.read()
            self.proc.stdout.close()
            self.proc.wait()
        return self.output == b""

    def parent_pid(self):
        return int(subprocess.run(["ps", "-o", "ppid=", "-p", str(self.pid)],
                                  capture_output=True, text=True,
                                  check=True).stdout)


class StopOwnTest(unittest.TestCase):

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = os.path.realpath(temp.name)
        self.fabric = os.path.join(self.home, "dev", "commonfabric")
        self.mine = os.path.join(self.fabric, "labs", "E3")
        self.theirs = os.path.join(self.fabric, "labs", "E4")
        self.root = os.path.join(self.fabric, "labs", "root")
        self.elsewhere = os.path.join(self.home, "elsewhere")
        for path in (os.path.join(self.mine, "packages"), self.theirs,
                     self.root, self.elsewhere):
            os.makedirs(path)
        self.session_id = str(uuid.uuid4())

    def scratchpad(self, session_id):
        path = os.path.join(self.home, "claude-501", "-proj", session_id,
                            "scratchpad")
        os.makedirs(path, exist_ok=True)
        return path

    def stop(self, *args, cwd=None, claude_pid=None, home=None):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PID"}
        env.update(HOME=home or self.home,
                   CLAUDE_CODE_SESSION_ID=self.session_id)
        if claude_pid is not None:
            env["CLAUDE_PID"] = str(claude_pid)
        return subprocess.run([sys.executable, SCRIPT, *map(str, args)],
                              cwd=cwd or self.elsewhere, env=env,
                              capture_output=True, text=True)

    def assertRefused(self, result, process, cwd):
        self.assertIn(f"refused {process.pid}", result.stderr)
        self.assertIn(f"cwd: {cwd}", result.stderr)
        self.assertRegex(result.stderr, "command: .* -c " + re.escape(SLEEPER))
        self.assertFalse(process.finish())

    def test_descendant_of_claude_pid_is_stopped(self):
        # This test process stands in for the Claude process: it is an
        # ancestor of the script and of the child, but not of the orphan.
        child = Process(self, self.elsewhere)
        stranger = Process(self, self.elsewhere, parent="orphan")
        result = self.stop(child.pid, stranger.pid, claude_pid=os.getpid())
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"sent SIGTERM to {child.pid} (descends from "
                      f"CLAUDE_PID {os.getpid()})", result.stdout)
        self.assertTrue(child.finish())
        self.assertRefused(result, stranger, self.elsewhere)

    def test_claude_pid_must_be_an_ancestor(self):
        process = Process(self, self.mine)
        for claude_pid in (process.pid, "x"):
            with self.subTest(claude_pid=claude_pid):
                result = self.stop(process.pid, cwd=self.mine,
                                   claude_pid=claude_pid)
                self.assertEqual(result.returncode, 2)
                self.assertIn(f"CLAUDE_PID {claude_pid} is not an ancestor",
                              result.stderr)
        self.assertFalse(process.finish())

    def test_orphan_naming_own_scratchpad_is_stopped(self):
        mine = Process(self, self.elsewhere,
                       [os.path.join(self.scratchpad(self.session_id), "log")],
                       parent="orphan")
        theirs = Process(self, self.elsewhere,
                         [os.path.join(self.scratchpad(str(uuid.uuid4())),
                                       "log")],
                         parent="orphan")
        self.assertEqual(mine.parent_pid(), 1)
        self.assertEqual(theirs.parent_pid(), 1)
        result = self.stop(mine.pid, theirs.pid)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"sent SIGTERM to {mine.pid} (command line names this "
                      "session's scratchpad)", result.stdout)
        self.assertTrue(mine.finish())
        self.assertRefused(result, theirs, self.elsewhere)

    def test_orphan_in_own_scratchpad_is_stopped(self):
        mine = Process(self, self.scratchpad(self.session_id), parent="orphan")
        other = self.scratchpad(str(uuid.uuid4()))
        theirs = Process(self, other, parent="orphan")
        result = self.stop(mine.pid, theirs.pid)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"sent SIGTERM to {mine.pid} (runs in this session's "
                      "scratchpad)", result.stdout)
        self.assertTrue(mine.finish())
        self.assertRefused(result, theirs, other)

    def test_home_reached_through_a_symbolic_link(self):
        link = os.path.join(self.elsewhere, "home")
        os.symlink(self.home, link)
        process = Process(self, self.mine)
        result = self.stop(process.pid, cwd=self.mine, home=link)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(process.finish())

    def test_process_in_own_worktree_is_stopped(self):
        process = Process(self, self.mine)
        result = self.stop(process.pid,
                           cwd=os.path.join(self.mine, "packages"))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"sent SIGTERM to {process.pid} (runs in {self.mine})",
                      result.stdout)
        self.assertTrue(process.finish())

    def test_process_in_another_worktree_is_refused(self):
        same_code_other_repo = os.path.join(self.fabric, "loom", "E3")
        os.makedirs(same_code_other_repo)
        prefix = os.path.join(self.fabric, "labs", "E")
        os.makedirs(prefix)
        for caller_cwd, process_cwd in ((self.mine, self.theirs),
                                        (self.mine, same_code_other_repo),
                                        (prefix, self.mine)):
            with self.subTest(caller_cwd=caller_cwd, process_cwd=process_cwd):
                process = Process(self, process_cwd)
                result = self.stop(process.pid, cwd=caller_cwd)
                self.assertEqual(result.returncode, 1)
                self.assertEqual(result.stdout, "")
                self.assertRefused(result, process, process_cwd)

    def test_worktree_counts_only_for_a_caller_inside_it(self):
        process = Process(self, self.mine)
        result = self.stop(process.pid, cwd=self.elsewhere)
        self.assertEqual(result.returncode, 1)
        self.assertRefused(result, process, self.mine)

    def test_main_worktree_and_fabric_directory_never_count(self):
        for directory in (self.root, self.fabric):
            with self.subTest(directory=directory):
                process = Process(self, directory)
                result = self.stop(process.pid, cwd=directory)
                self.assertEqual(result.returncode, 1)
                self.assertRefused(result, process, directory)

    def test_tree_stops_descendants(self):
        process = Process(self, self.mine, parent="wait")
        result = self.stop("--tree", process.launcher_pid, cwd=self.mine)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"sent SIGTERM to {process.launcher_pid}", result.stdout)
        self.assertIn(f"sent SIGTERM to {process.pid}", result.stdout)
        self.assertTrue(process.finish())

    def test_without_tree_descendants_are_left(self):
        process = Process(self, self.mine, parent="wait")
        result = self.stop(process.launcher_pid, cwd=self.mine)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(str(process.pid), result.stdout)
        self.assertFalse(process.finish())

    def test_tree_judges_each_descendant(self):
        process = Process(self, self.mine, parent="wait",
                          child_cwd=self.theirs)
        result = self.stop("--tree", process.launcher_pid, cwd=self.mine)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"sent SIGTERM to {process.launcher_pid}", result.stdout)
        self.assertRefused(result, process, self.theirs)

    def test_never_signals_its_own_ancestors(self):
        # The shell runs in the caller's worktree, so only the rule
        # protecting the script's ancestors refuses it.
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PID"}
        env.update(HOME=self.home, CLAUDE_CODE_SESSION_ID=self.session_id)
        result = subprocess.run(
            ["sh", "-c", '"$0" "$1" $$; echo "rc=$?"', sys.executable, SCRIPT],
            cwd=self.mine, env=env, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "rc=1\n")
        self.assertRegex(result.stderr,
                         r"refused \d+, which is running this command\n"
                         r"  cwd: .*\n  command: sh -c ")

    def test_unknown_pid_fails(self):
        gone = subprocess.Popen(["true"])
        gone.wait()
        result = self.stop(gone.pid)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{gone.pid}: no such process", result.stderr)

    @unittest.skipIf(os.geteuid() == 0, "root can signal any process")
    def test_unsignallable_pid_fails(self):
        # Every process descends from PID 1, and this user cannot signal
        # processes that root owns.
        listing = subprocess.run(["ps", "-Ao", "pid=,ppid=,ruser=,user="],
                                 capture_output=True, text=True,
                                 check=True).stdout
        pid = next(int(p) for p, ppid, ruser, user
                   in map(str.split, listing.splitlines())
                   if ruser == user == "root" and ppid == "1")
        result = self.stop(pid, claude_pid=1)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"could not signal {pid}: Operation not permitted",
                      result.stderr)

    def test_signal_option(self):
        process = Process(self, self.mine)
        result = self.stop("--signal", "KILL", process.pid, cwd=self.mine)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"sent SIGKILL to {process.pid}", result.stdout)
        self.assertTrue(process.finish())
        self.assertEqual(process.proc.returncode, -signal.SIGKILL)

    def test_unknown_signal_is_a_usage_error(self):
        result = self.stop("--signal", "BOGUS", os.getpid())
        self.assertEqual(result.returncode, 2)
        self.assertIn("unknown signal BOGUS", result.stderr)


if __name__ == "__main__":
    unittest.main()
