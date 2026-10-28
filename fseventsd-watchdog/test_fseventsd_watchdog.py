"""Tests for fseventsd-watchdog.sh and check-fseventsd.py.

Most watchdog tests run the real script with fake `log` and `launchctl`
commands first on the PATH. The fake `log` prints prepared lines, and the
fake `launchctl` names a /bin/sleep process as the running fseventsd, so
the tests can see which processes the script really signals. One test
uses the real `launchctl` and a real log line, as an ordinary user, so
that its attempt to stop the real fseventsd is refused."""

import importlib.util
import os
import platform
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))

spec = importlib.util.spec_from_file_location("check", os.path.join(HERE, "check-fseventsd.py"))
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

WARNING = "event table has grown too large. (filled 196608 / mask 0x3ffff)"
DROPPED = "=== Messages dropped during live streaming (use `log show` to see what they were)"

# The build that README.md and feedback-assistant-report.md describe.
AFFECTED_UUID = "519A5391-0608-346B-A333-D34CC76E7EC4"


def log_line(pid, message):
    return f"2026-10-26 17:30:38.123 E  fseventsd[{pid}:15b9ee3e] [com.apple.fsevents:daemon] {message}"


class Watchdog(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.fseventsd = self.sleeper()
        # The search back to boot cannot open the rendezvous FIFO until the
        # stream has opened it too, so a script that searched before
        # starting the stream would never finish.
        os.mkfifo(os.path.join(self.dir, "rendezvous"))
        self.fake("log", """\
            echo "$*" >> "$FAKE/log-calls"
            case "$1 $5" in
                "stream "*) : > "$FAKE/rendezvous"; cat "$FAKE/stream" ;;
                "show boot") : < "$FAKE/rendezvous"; cat "$FAKE/show-boot" ;;
                "show 10m") cat "$FAKE/show-10m" ;;
            esac
            """)
        self.fake("launchctl", f"""\
            [ "$*" = "print system/com.apple.fseventsd" ] || exit 1
            printf 'system/com.apple.fseventsd = {{\\n\\tstate = running\\n\\tpid = {self.fseventsd.pid}\\n\\tendpoints = {{\\n\\t\\tpid = 1\\n\\t}}\\n}}\\n'
            """)

    def fake(self, name, body):
        path = os.path.join(self.dir, "bin", name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write("#!/bin/sh\n" + textwrap.dedent(body))
        os.chmod(path, 0o755)

    def sleeper(self):
        process = subprocess.Popen(["/bin/sleep", "60"])
        self.addCleanup(process.wait)
        self.addCleanup(process.kill)
        return process

    def run_script(self, script):
        env = {"PATH": os.path.join(self.dir, "bin") + ":/usr/bin:/bin:/usr/sbin:/sbin", "FAKE": self.dir}
        return subprocess.run(["/bin/sh", "-c", script], env=env, capture_output=True, text=True)

    def run_watchdog(self, stream=(), show_boot=(), show_10m=()):
        """Returns the calls to `log` and what the script wrote to stderr,
        apart from the line saying that the stream ended. Here the stream
        always ends, because the fake one does."""
        for name, lines in (("stream", stream), ("show-boot", show_boot), ("show-10m", show_10m)):
            with open(os.path.join(self.dir, name), "w") as f:
                f.write("".join(line + "\n" for line in lines))
        with open(os.path.join(HERE, "fseventsd-watchdog.sh")) as f:
            result = self.run_script(f.read())
        self.assertEqual(result.returncode, 1, result.stderr)
        *stderr, ended = result.stderr.splitlines(keepends=True)
        self.assertRegex(ended, r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d log stream ended\n$")
        with open(os.path.join(self.dir, "log-calls")) as f:
            return f.read().splitlines(), "".join(stderr)

    def predicate(self, calls):
        streams = [call for call in calls if call.startswith("stream ")]
        self.assertEqual(len(streams), 1)
        return streams[0].split(" --predicate ", 1)[1]

    def test_restarts_fseventsd_on_warning(self):
        line = log_line(self.fseventsd.pid, WARNING)
        _, stderr = self.run_watchdog(stream=[
            'Filtering the log data using "eventMessage BEGINSWITH "event table has grown too large""',
            line,
        ])
        self.assertEqual(self.fseventsd.wait(), -15)
        self.assertRegex(stderr, rf"^\d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d restarting fseventsd \(pid {self.fseventsd.pid}\): {re.escape(line)}\n$")

    def test_catches_up_on_start(self):
        calls, _ = self.run_watchdog(show_boot=[log_line(self.fseventsd.pid, WARNING)])
        self.assertEqual(self.fseventsd.wait(), -15)
        predicate = self.predicate(calls)
        self.assertEqual(sorted(calls), [
            f"show --style compact --last boot --predicate {predicate}",
            f"stream --style compact --predicate {predicate}",
        ])

    def test_catches_up_after_dropped_messages(self):
        calls, _ = self.run_watchdog(stream=[DROPPED], show_10m=[log_line(self.fseventsd.pid, WARNING)])
        self.assertEqual(self.fseventsd.wait(), -15)
        self.assertIn(f"show --style compact --last 10m --predicate {self.predicate(calls)}", calls)

    def test_ignores_other_lines(self):
        bystander = self.sleeper()
        lines = [
            # A warning from a process other than the running fseventsd,
            # whose text names the running fseventsd.
            log_line(bystander.pid, f"{WARNING} fseventsd[{self.fseventsd.pid}:1] {WARNING}"),
            log_line(self.fseventsd.pid, "fname table has grown too large. (filled 196608 / mask 0x3ffff)"),
            log_line(self.fseventsd.pid, "fsevent_add_client: no bundle id available for pid 123"),
            WARNING,
        ]
        _, stderr = self.run_watchdog(stream=lines)
        self.assertIsNone(self.fseventsd.poll())
        self.assertIsNone(bystander.poll())
        self.assertRegex(
            stderr,
            rf"^\d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d ignoring a warning from pid {bystander.pid}; "
            rf"the running fseventsd is pid {self.fseventsd.pid}: {re.escape(lines[0])}\n$",
        )

    def test_reports_warning_when_fseventsd_cannot_be_found(self):
        self.fake("launchctl", "exit 113\n")
        line = log_line(self.fseventsd.pid, WARNING)
        _, stderr = self.run_watchdog(stream=[line])
        self.assertIsNone(self.fseventsd.poll())
        self.assertRegex(
            stderr,
            rf"^\d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d ignoring a warning from pid {self.fseventsd.pid}; "
            rf"the running fseventsd is pid : {re.escape(line)}\n$",
        )

    @unittest.skipIf(os.geteuid() == 0, "would really restart fseventsd")
    def test_real_launchctl_and_log_format(self):
        os.remove(os.path.join(self.dir, "bin", "launchctl"))
        real = subprocess.run(
            ["/usr/bin/log", "show", "--style", "compact", "--last", "1h", "--predicate", f'processImagePath == "{check.FSEVENTSD}"'],
            capture_output=True, text=True, check=True,
        ).stdout.splitlines()
        running = [pid for pid, comm in (line.split(None, 1) for line in subprocess.run(
            ["/bin/ps", "-Ax", "-o", "pid=,comm="], capture_output=True, text=True, check=True
        ).stdout.splitlines()) if comm == check.FSEVENTSD]
        self.assertEqual(len(running), 1)
        lines = [line for line in real if f" fseventsd[{running[0]}:" in line]
        if not lines:
            self.skipTest("the running fseventsd logged nothing in the last hour")
        # A real line from the running fseventsd, with the warning in place
        # of everything after its header.
        line = re.sub(r"(fseventsd\[[0-9]+:[^ ]+ ).*", lambda m: m[1] + WARNING, lines[-1], count=1)
        _, stderr = self.run_watchdog(stream=[line])
        self.assertIn(f"restarting fseventsd (pid {running[0]}): {line}\n", stderr)
        self.assertIn("Operation not permitted", stderr)

    def test_plist_runs_the_script(self):
        subprocess.run(["make", "-s", "-B", "-C", HERE, "local.fseventsd-watchdog.plist"], check=True)
        with open(os.path.join(HERE, "local.fseventsd-watchdog.plist"), "rb") as f:
            plist = plistlib.load(f)
        with open(os.path.join(HERE, "fseventsd-watchdog.sh")) as f:
            self.assertEqual(plist["ProgramArguments"], ["/bin/sh", "-c", f.read().rstrip("\n")])
        # launchd restarts the script when it fails, but not when it stops
        # because fseventsd is missing.
        self.assertEqual(plist["KeepAlive"], {"SuccessfulExit": False})

    def test_stops_when_fseventsd_is_missing(self):
        with open(os.path.join(HERE, "fseventsd-watchdog.sh")) as f:
            script = f.read()
        missing = os.path.join(self.dir, "missing", "fseventsd")
        self.assertIn(f"FSEVENTSD={check.FSEVENTSD}\n", script)
        result = self.run_script(script.replace(f"FSEVENTSD={check.FSEVENTSD}\n", f"FSEVENTSD={missing}\n"))
        self.assertEqual(result.returncode, 0)
        self.assertRegex(
            result.stderr, rf"^\d{{4}}-\d\d-\d\d \d\d:\d\d:\d\d stopping: {re.escape(missing)} does not exist, so this watchdog cannot work\n$"
        )
        self.assertFalse(os.path.exists(os.path.join(self.dir, "log-calls")))

    def test_real_log_accepts_the_searches(self):
        predicate = self.predicate(self.run_watchdog()[0])
        self.assertIn(f'processImagePath == "{check.FSEVENTSD}"', predicate)
        for last in ("boot", "10m"):
            subprocess.run(["/usr/bin/log", "show", "--style", "compact", "--last", last, "--predicate", predicate], capture_output=True, check=True)


SWEEPER = """\
0000000100004604	pacibsp
0000000100004648	ldr	w8, [x19, #0x14]
000000010000464c	cmn	w8, #0x201
0000000100004650	b.hi	0x100004758
0000000100004674	add	x21, x21, #0x9e1 ; literal pool for: "event table is screwed up! filled buckets %u / table_mask %u)"
0000000100004738	bl	0x100018f60 ; symbol stub for: _malloc_zone_free
0000000100004798	retab
"""

INSERT = """\
0000000100005800	pacibsp
0000000100005a90	add	x3, x3, #0x9e1 ; literal pool for: "event table is screwed up! filled buckets %u / table_mask %u)"
0000000100005bc8	bl	0x100018f50 ; symbol stub for: _malloc_type_zone_malloc
0000000100005d00	retab
"""


def padded(lines):
    return SWEEPER.replace("0000000100004648", "0000000100004640\tnop\n" * lines + "0000000100004648", 1)


class Check(unittest.TestCase):
    def test_affected(self):
        self.assertEqual(check.find_wrapping_check(INSERT + SWEEPER), "0x100004604")

    def test_no_wrapping_check(self):
        fixed = SWEEPER.replace("cmn\tw8, #0x201", "cmp\tw8, w24")
        self.assertIsNone(check.find_wrapping_check(INSERT + fixed))

    def test_check_must_be_followed_by_b_hi(self):
        self.assertIsNone(check.find_wrapping_check(SWEEPER.replace("b.hi", "b.lo")))

    def test_check_must_be_in_prologue(self):
        # The branch is the last line of the prologue when two lines come before the padding.
        self.assertEqual(check.find_wrapping_check(padded(check.PROLOGUE - 4)), "0x100004604")
        self.assertIsNone(check.find_wrapping_check(padded(check.PROLOGUE - 3)))

    def test_no_sweeper(self):
        self.assertIsNone(check.find_wrapping_check(INSERT))

    @unittest.skipUnless(platform.machine() == "arm64", "needs an Apple silicon Mac")
    def test_this_mac(self):
        load_commands = subprocess.run(
            ["otool", "-arch", "arm64e", "-l", check.FSEVENTSD], capture_output=True, text=True, check=True
        ).stdout
        if f"uuid {AFFECTED_UUID}" not in load_commands:
            self.skipTest("this Mac's fseventsd is not the build that was analysed")
        result = subprocess.run(
            [sys.executable, "-I", os.path.join(HERE, "check-fseventsd.py")], capture_output=True, text=True
        )
        self.assertEqual(result.returncode, check.AFFECTED, result.stderr)
        self.assertEqual(result.stdout, "affected: the event table sweeper at 0x100004604 stops for good once its cursor reaches 0xFFFFFE00\n")


if __name__ == "__main__":
    unittest.main()
