"""Tests for iterm-run.

Each shell test starts an interactive zsh under a pseudo-terminal with a
real window size (without one, zsh redraws the prompt after every job
because it thinks the window changed size), sources iterm-run.zsh in it,
and drives it with keystrokes and with the iterm-run sender, which
ITERM_RUN_PID points at the shell. The FIFOs live in a temporary
ITERM_RUN_DIR.

A thread reads the terminal all the time, because zsh's line editor
waits for its output to be read before it changes terminal settings.

Keys typed before a request is sent reach the line editor first: when
both are waiting, the line editor reads keys before it calls the handler
for requests."""

import fcntl
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "iterm-run.zsh")
SENDER = os.path.join(HERE, "iterm-run")
sys.path.insert(0, HERE)
from vt import screen  # noqa: E402

# Seconds to wait for output that should come before the test fails.
LIMIT = 30

ESCAPES = re.compile(rb"\x1b\[[0-9;?]*[A-Za-z]")

# Pretends to be a listening shell: holds the lock and the FIFO open,
# prints "ready", prints the command of the first request it reads, and
# then waits until it is killed.
FAKE_LISTENER = """
import fcntl, os, sys
fifo = os.path.join(sys.argv[1], str(os.getpid()))
lock = open(fifo + ".lock", "a")
fcntl.lockf(lock, fcntl.LOCK_EX)
os.mkfifo(fifo)
requests = os.fdopen(os.open(fifo, os.O_RDWR))
print("ready", flush=True)
print(requests.readline().split("\\t", 1)[1], end="", flush=True)
sys.stdin.read()
"""


def send(directory, command, target):
    env = dict(os.environ, ITERM_RUN_DIR=directory, ITERM_RUN_PID=str(target))
    return subprocess.Popen([SENDER, command], env=env, text=True,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def answer(process):
    out, _ = process.communicate(timeout=LIMIT)
    return out.strip(), process.returncode


class Shell:
    def __init__(self, directory):
        self.directory = directory
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            os.environ.update(PS1="READY> ", TERM="xterm-256color",
                              ITERM_RUN_DIR=directory)
            os.execvp("zsh", ["zsh", "-f", "-i"])
        fcntl.ioctl(self.fd, termios.TIOCSWINSZ,
                    struct.pack("HHHH", 24, 80, 0, 0))
        self.output = bytearray()
        self.consumed = 0
        self.ended = False
        self.reaped = None
        self.changed = threading.Condition()
        threading.Thread(target=self._read, daemon=True).start()
        self.prompt()
        self.run(f"setopt noclobber; source {SCRIPT}")

    def _read(self):
        while True:
            try:
                data = os.read(self.fd, 4096)
            except OSError:
                data = b""
            with self.changed:
                if data:
                    self.output += data
                else:
                    self.ended = True
                self.changed.notify_all()
            if not data:
                return

    def expect(self, text):
        """Waits for text in the output after what earlier calls consumed,
        consumes the output up to the end of it, and returns that part."""
        needle = text.encode()
        with self.changed:
            self.changed.wait_for(
                lambda: needle in self.output[self.consumed:] or self.ended,
                LIMIT)
            i = self.output.find(needle, self.consumed)
            if i < 0:
                raise AssertionError(
                    f"no {needle!r} in {bytes(self.output[-600:])!r}")
            part = bytes(self.output[self.consumed:i + len(needle)])
            self.consumed = i + len(needle)
            return part

    def type(self, keys):
        os.write(self.fd, keys.encode())

    def prompt(self):
        return self.expect("READY> ")

    def run(self, line):
        self.type(line + "\n")
        return self.prompt()

    def ask(self, command):
        return answer(send(self.directory, command, self.pid))

    def printed(self, text):
        with self.changed:
            return text.encode() in self.output

    def clear_screen(self):
        self.run(r"print -n '\e[H\e[2J'")
        with self.changed:
            return self.output.rindex(b"\x1b[2J")

    def screen_since(self, mark):
        with self.changed:
            return screen(bytes(self.output[mark:]))

    def wait_exit(self):
        with self.changed:
            self.changed.wait_for(lambda: self.ended, LIMIT)
        _, self.reaped = os.waitpid(self.pid, 0)
        return self.reaped

    def close(self):
        if self.reaped is None:
            os.kill(self.pid, signal.SIGKILL)
            os.waitpid(self.pid, 0)
        os.close(self.fd)
        for suffix in ("", ".lock"):
            path = os.path.join(self.directory, f"{self.pid}{suffix}")
            if os.path.lexists(path):
                os.unlink(path)


class TemporaryDirectoryTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.directory)

    def fifo(self, name):
        path = os.path.join(self.directory, name)
        os.mkfifo(path)
        return path


class ShellTest(TemporaryDirectoryTest):
    def setUp(self):
        super().setUp()
        self.shell = Shell(self.directory)
        self.addCleanup(self.shell.close)

    def open_requests(self):
        fd = os.open(os.path.join(self.directory, str(self.shell.pid)),
                     os.O_WRONLY | os.O_NONBLOCK)
        self.addCleanup(os.close, fd)
        return fd

    def test_runs_command_at_idle_prompt_as_if_typed(self):
        sh = self.shell
        command = "echo out-$((40+2)); stty -a | grep -o -E -- ' -?icanon'"
        self.assertEqual(sh.ask(command), ("ran", 0))
        seen = sh.expect("out-42")
        self.assertTrue(ESCAPES.sub(b"", seen).startswith(command.encode()),
                        "the command is shown on the prompt line")
        self.assertIn(b"\x1b[?2004l", seen, "bracketed paste is turned off")
        self.assertNotIn(b"-icanon", sh.expect("icanon"),
                         "the terminal is in its normal line mode")
        sh.prompt()
        self.assertIn(command.encode(), sh.run("fc -ln -1"))
        self.assertIn(b"typed-5", sh.run("echo typed-$((2+3))"))

    def test_screen_and_hooks_match_typed_command(self):
        sh = self.shell
        sh.run("precmd() { print -r hook=$? }")
        mark = sh.clear_screen()
        self.assertEqual(sh.ask("false"), ("ran", 0))
        sh.expect("hook=1")
        sh.prompt()
        sh.run("echo after")
        self.assertEqual(sh.screen_since(mark)[-6:], [
            "READY> false", "hook=1", "READY> echo after", "after",
            "hook=0", "READY>"])

    def test_preexec_hooks_see_command(self):
        sh = self.shell
        sh.run("preexec() { print -r pre=$1 }")
        self.assertEqual(sh.ask("echo x-$((1+1))"), ("ran", 0))
        sh.expect("pre=echo x-$((1+1))\r\n")
        sh.expect("x-2\r\n")

    def test_hook_error_leaves_empty_prompt(self):
        sh = self.shell
        sh.run("preexec() { unfunction preexec; : ${undefined_var?boom} }")
        self.assertEqual(sh.ask("echo stopped-$((2+3))"), ("ran", 0))
        sh.expect("boom")
        sh.prompt()
        self.assertEqual(sh.ask("echo after-$((2+2))"), ("ran", 0))
        sh.expect("after-4")
        self.assertFalse(sh.printed("stopped-5"))

    def test_marks_output_without_final_newline(self):
        sh = self.shell
        mark = sh.clear_screen()
        self.assertEqual(sh.ask("printf no-newline"), ("ran", 0))
        sh.expect("no-newline")
        sh.prompt()
        self.assertEqual(sh.screen_since(mark)[-2:], ["no-newline%", "READY>"])

    def test_keeps_command_text_exactly(self):
        sh = self.shell
        self.assertEqual(sh.ask("  print -r -- 'a\\b\t'x"), ("ran", 0))
        sh.expect("a\\b\tx\r\n")
        sh.prompt()
        self.assertEqual(sh.ask("print -r -- x-a\\ "), ("ran", 0))
        sh.expect("x-a \r\n")

    def test_respects_history_options(self):
        sh = self.shell
        sh.run("setopt hist_ignore_space")
        self.assertEqual(sh.ask(" echo hidden-$((2*3))"), ("ran", 0))
        sh.expect("hidden-6")
        sh.prompt()
        self.assertNotIn(b"hidden", sh.run("fc -ln -1"))

    def test_refuses_when_text_is_typed(self):
        sh = self.shell
        sh.type("half-typed")
        sh.expect("half-typed")
        self.assertEqual(sh.ask("echo refused-1"), ("busy", 1))
        sh.type("\x15echo cleared\n")
        sh.expect("cleared\r\n")
        self.assertFalse(sh.printed("refused-1"))

    def test_refuses_at_continuation_prompt(self):
        sh = self.shell
        sh.type("for x in 1; do\n")
        sh.expect("for> ")
        self.assertEqual(sh.ask("echo refused-2"), ("busy", 1))
        sh.type("\x03")
        sh.prompt()
        self.assertFalse(sh.printed("refused-2"))

    def test_refuses_during_search_and_key_sequence(self):
        sh = self.shell
        sh.type("\x12")
        sh.expect("bck-i-search")
        self.assertEqual(sh.ask("echo refused-3"), ("busy", 1))
        sh.type("\x07\x18")
        self.assertEqual(sh.ask("echo refused-4"), ("busy", 1))
        sh.type("\x07echo idle-again\n")
        sh.expect("idle-again\r\n")
        self.assertFalse(sh.printed("refused-3"))
        self.assertFalse(sh.printed("refused-4"))

    def test_refuses_with_numeric_argument_or_quoted_insert(self):
        sh = self.shell
        sh.type("\x1b3")
        self.assertEqual(sh.ask("echo refused-9"), ("busy", 1))
        sh.type("\x07\x16")
        self.assertEqual(sh.ask("echo refused-10"), ("busy", 1))
        sh.type("a\x15echo idle-again\n")
        sh.expect("idle-again\r\n")
        self.assertFalse(sh.printed("refused-9"))
        self.assertFalse(sh.printed("refused-10"))

    def test_accepts_directory_spelled_differently(self):
        sh = self.shell
        link = os.path.join(tempfile.mkdtemp(), "link")
        self.addCleanup(shutil.rmtree, os.path.dirname(link))
        os.symlink(self.directory, link)
        self.assertEqual(answer(send(link, "echo linked-$((3+3))", sh.pid)),
                         ("ran", 0))
        sh.expect("linked-6")

    def test_files_are_private(self):
        fifo = os.path.join(self.directory, str(self.shell.pid))
        self.assertEqual(os.stat(fifo).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(fifo + ".lock").st_mode & 0o777, 0o600)

    def test_refuses_at_once_while_program_runs(self):
        sh = self.shell
        sh.type("/bin/sh -c 'echo started; exec cat'\n")
        sh.expect("started\r\n")
        self.assertEqual(sh.ask("echo refused-5"), ("busy", 1))
        sh.type("\x04")
        sh.prompt()
        self.assertFalse(sh.printed("refused-5"))

    def test_refuses_requests_queued_during_program(self):
        sh = self.shell
        reply = self.fifo(f"reply-{os.getpid()}")
        replies = os.open(reply, os.O_RDWR)
        self.addCleanup(os.close, replies)
        requests = self.open_requests()
        sh.type("/bin/sh -c 'echo started; exec cat'\n")
        sh.expect("started\r\n")
        os.write(requests, (f"{self.directory}/reply-1\techo refused-6\n"
                            f"{reply}\techo refused-7\n").encode())
        sh.type("\x04")
        sh.prompt()
        ready, _, _ = select.select([replies], [], [], LIMIT)
        self.assertEqual(ready and os.read(replies, 100), b"busy\n")
        self.assertFalse(sh.printed("refused-6"))
        self.assertFalse(sh.printed("refused-7"))

    def test_skips_request_whose_sender_has_gone(self):
        sh = self.shell
        gone = self.fifo(f"reply-{os.getpid()}")
        os.write(self.open_requests(), f"{gone}\techo refused-8\n".encode())
        self.assertEqual(sh.ask("echo next-$((5+6))"), ("ran", 0))
        sh.expect("next-11")
        self.assertFalse(sh.printed("refused-8"))

    def test_recovers_from_ctrl_c(self):
        sh = self.shell
        self.assertEqual(sh.ask("/bin/sh -c 'echo started; exec sleep 30'"),
                         ("ran", 0))
        sh.expect("started\r\n")
        sh.type("\x03")
        sh.prompt()
        self.assertIn(b"key-9", sh.run("echo key-$((1+8))"),
                      "no key is lost")
        self.assertEqual(sh.ask("echo after-$((3+4))"), ("ran", 0))
        sh.expect("after-7")

    def test_recovers_from_shell_error(self):
        sh = self.shell
        self.assertEqual(sh.ask("echo /nonexistent-*-glob"), ("ran", 0))
        sh.expect("no matches found")
        sh.prompt()
        self.assertEqual(sh.ask("echo after-$((4+4))"), ("ran", 0))
        sh.expect("after-8")

    def test_suspended_command_finishes_once(self):
        sh = self.shell
        self.assertEqual(
            sh.ask("/bin/sh -c 'echo started; read x; echo resumed-$((6+6))'"),
            ("ran", 0))
        sh.expect("started\r\n")
        sh.type("\x1a")
        sh.expect("suspended")
        sh.prompt()
        sh.type("fg\n")
        sh.expect("continued")
        sh.type("\n")
        sh.expect("resumed-12\r\n")
        sh.prompt()
        self.assertEqual(sh.ask("echo after-fg"), ("ran", 0))
        sh.expect("after-fg")
        with sh.changed:
            self.assertEqual(sh.output.count(b"resumed-12"), 1)

    def test_accepts_in_vi_modes(self):
        sh = self.shell
        sh.run("bindkey -v; bindkey -M viins '^B' vi-cmd-mode")
        self.assertEqual(sh.ask("echo vi-ins-$((1+1))"), ("ran", 0))
        sh.expect("vi-ins-2")
        sh.prompt()
        sh.type("\x02")
        self.assertEqual(sh.ask("print -r km-$KEYMAP"), ("ran", 0))
        sh.expect("km-vicmd")

    def test_declined_exit_keeps_listening(self):
        sh = self.shell
        sh.run("sleep 100 &")
        self.assertEqual(sh.ask("exit"), ("ran", 0))
        sh.expect("running jobs")
        sh.prompt()
        self.assertEqual(sh.ask("echo still-here"), ("ran", 0))
        sh.expect("still-here")
        sh.prompt()
        sh.run("kill %1")

    def test_exit_ends_shell_at_once(self):
        self.check_ends_shell("exit")

    def test_bye_ends_shell_at_once(self):
        self.check_ends_shell("bye")

    def check_ends_shell(self, command):
        sh = self.shell
        self.assertEqual(sh.ask(command), ("ran", 0))
        status = sh.wait_exit()
        self.assertTrue(os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0)
        fifo = os.path.join(self.directory, str(sh.pid))
        self.assertFalse(os.path.lexists(fifo))
        self.assertFalse(os.path.lexists(fifo + ".lock"))


class SenderTest(TemporaryDirectoryTest):
    def start_fake_listener(self):
        fake = subprocess.Popen(
            [sys.executable, "-I", "-c", FAKE_LISTENER, self.directory],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)

        def stop():
            fake.kill()
            fake.wait()
            fake.stdin.close()
            fake.stdout.close()
        self.addCleanup(stop)
        self.assertEqual(fake.stdout.readline(), "ready\n")
        return fake

    def test_rejects_requests_it_cannot_send(self):
        self.assertEqual(answer(send(self.directory, "x" * 600, 1))[1], 2)
        self.assertEqual(answer(send(self.directory, "echo a\necho b", 1))[1], 2)
        self.assertEqual(answer(send(self.directory, "", 1))[1], 2)

    def test_busy_when_nothing_listens(self):
        self.assertEqual(answer(send(self.directory, "echo x", 1)),
                         ("busy", 1))
        self.fifo("999999")
        self.assertEqual(answer(send(self.directory, "echo x", 999999)),
                         ("busy", 1))

    def test_busy_when_listener_dies(self):
        fake = self.start_fake_listener()
        sender = send(self.directory, "echo x", fake.pid)
        self.assertEqual(fake.stdout.readline(), "echo x\n")
        self.assertIsNone(sender.poll(), "the sender waits for a reply")
        os.kill(fake.pid, signal.SIGKILL)
        self.assertEqual(answer(sender), ("busy", 1))

    def test_killed_sender_cleans_up(self):
        fake = self.start_fake_listener()
        sender = send(self.directory, "echo x", fake.pid)
        self.assertEqual(fake.stdout.readline(), "echo x\n")
        sender.terminate()
        answer(sender)
        self.assertFalse(os.path.lexists(
            os.path.join(self.directory, f"reply-{sender.pid}")))


if __name__ == "__main__":
    unittest.main()
