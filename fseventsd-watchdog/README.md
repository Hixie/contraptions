# fseventsd-watchdog

Works around a memory leak in macOS's file event daemon, fseventsd. After
days or weeks of uptime on a machine with a lot of file activity,
fseventsd can grow without limit, at more than a megabyte per second,
until it holds tens of gigabytes and uses about two cores. On one Mac it
reached 103 GB on 2026-10-22 and 62 GB on 2026-10-27, under macOS 26.6.2.
Restarting fseventsd fixes it until next time.

This directory has:

- a launchd daemon that restarts fseventsd as soon as it logs the
  warning that comes before the leak (`fseventsd-watchdog.sh`, which
  `make install` puts in a plist built from
  `local.fseventsd-watchdog.plist.template`);
- a script that checks whether this Mac's fseventsd still has the bug
  (`check-fseventsd.py`), to look at after macOS updates;
- an optional launchd agent that keeps a long-term copy of fseventsd's
  log messages (`local.fseventsd-log-recorder.plist.template`);
- a draft bug report for Apple (`feedback-assistant-report.md`);
- a blog post about the bug and how it was found (`blogpost.html`).

## The bug

fseventsd is closed source. The following comes from disassembling the
arm64e slice of fseventsd 1413.160.2 (macOS 26.6.2). `feedback-assistant-report.md`
gives the addresses.

fseventsd keeps a record for each path with recent changes: 192 bytes,
plus the path itself in a separate table of strings. The records live in
a chained hash table that starts at 4,096 buckets. It doubles whenever
three quarters of its buckets are in use, up to a maximum of 262,144.

Releasing a record does not free it. It only decrements the record's
reference count. Records are freed by an incremental sweeper, which
fseventsd's notify thread runs after each batch of events it handles,
and on a timer when it is idle. Each run checks 512 buckets, starting at
a position kept in a 32-bit counter that only ever goes up. The loop
amounts to:

    end = cursor + 512;
    while (cursor < end) { sweep bucket (cursor & mask); cursor++; }

Once the counter reaches 0xFFFFFE00, after about 8.4 million runs,
`cursor + 512` wraps round to a small number. The loop never runs again,
so the counter never moves again, and no record is ever freed.

The only thing that resets the counter is the table doubling in size. So
there are two cases:

- **The table can still grow.** Records pile up until the table fills,
  the table doubles, the counter resets, and sweeping starts again. Each
  stall leaves the table one size bigger.
- **The table is already at its maximum size.** fseventsd logs this the
  first time the full-size table fills up:

      event table has grown too large. (filled 196608 / mask 0x3ffff)

  From then on, if the counter wraps, the stall is permanent. Every new
  path that changes leaks a record and its string, about 350 bytes. The
  hash chains grow without limit, so each lookup compares more and more
  strings. That accounts for both the memory and the CPU.

This explains why the leak needs days or weeks of uptime, and why it
comes sooner on a busier machine: more activity means more batches, so
the counter climbs faster. On 2026-10-26, fseventsd had been running for
four days. It used 12 MB at 15:24, logged the warning above at 17:30,
and used 57 GB by 02:37. In five days of recorded log messages, that was
the only time fseventsd logged that warning.

The same analysis, down to the addresses, was posted three weeks
earlier in [a thread on Apple's developer
forums](https://developer.apple.com/forums/thread/849819) and filed as
FB25090184. Using a temporary SIP exception, its author watched the
counter in a running fseventsd stop at 0xFFFFFE00, and saw it reset when
the table next doubled. Other people in that thread report similar
symptoms on macOS 15 and macOS 27.0.1. Apple's replies there agree that
FSEvents has bugs, but do not discuss the counter.

Bursts of 50,000 and 250,000 new files did not make fseventsd overflow
or keep memory afterwards. That is what the analysis predicts while the
sweeper is still working.

## The workaround

A permanent leak can only start after fseventsd has logged "event table
has grown too large". The table has to be at its maximum size, and the
first time it fills up at that size, fseventsd logs that warning. The
watchdog waits for the warning with `log stream`. When it sees the
warning from the fseventsd that is running, it sends that process
SIGTERM, and launchd starts a fresh one. A fresh fseventsd starts with a
small table, where a stall clears itself.

fseventsd logs the warning only once per process, so the watchdog makes
sure it does not miss it:

- The stream only accepts messages whose sending executable is the
  system's fseventsd, as logd records it. The watchdog takes the process
  ID from the line's header, which logd also writes, and acts only if
  that is the fseventsd launchd is running now, as `launchctl print`
  reports it. So no other program, not even another copy of the
  fseventsd executable, can make it stop anything. It notes any warning
  it ignores, so if a macOS update changed what `launchctl print` says,
  the log would show it.
- `log stream` drops messages when the machine is busy, which is exactly
  when the table fills up, and it reports each drop. On a drop, the
  watchdog searches the last ten minutes of the stored log with
  `log show`.
- When the watchdog starts, it searches the stored log back to the last
  boot. That covers a warning logged before the watchdog was installed or
  while launchd was restarting it, unless the stored log no longer
  reaches back to it: on a busy machine it keeps less than a day of
  fseventsd's messages. The search runs alongside the stream. The stream
  connects within milliseconds, while the search takes seconds, so a
  warning logged before the stream connects is normally still found by
  the search. Nothing guarantees that ordering, but the gap only exists
  for those few milliseconds each time the watchdog starts.

A warning that the watchdog sees twice before the old fseventsd has
exited, for example once from the stream and once from a search, makes
it send SIGTERM twice and record two restarts. That is harmless.

If `log stream` ever stops, the watchdog exits with an error and launchd
starts it again, which repeats the search back to boot. If fseventsd is
not where the watchdog expects it, the watchdog says so and stops, and
launchd leaves it stopped.

fseventsd was restarted by hand on 2026-10-22 and 2026-10-27 with no
visible ill effects. Each restart the watchdog makes, and each warning
it ignores, is recorded in `/var/log/fseventsd-watchdog.log`, along with
any errors.

## Installing

The watchdog runs as root, so installing it needs `sudo`:

```sh
make install     # install and start the daemon
make uninstall   # stop the daemon and remove its plist,
                 # leaving /var/log/fseventsd-watchdog.log
```

`make install` builds `local.fseventsd-watchdog.plist`, in which launchd
runs `/bin/sh -c` with the whole text of `fseventsd-watchdog.sh`, and
copies it to `/Library/LaunchDaemons`, owned by root. The script lives
only in that file, so no other user can change what runs as root.
Run `make install` again after changing the script.

The log recorder is a per-user agent and needs no `sudo`. It appends
everything fseventsd logs at the default level and above to
`~/Library/Logs/fseventsd/stream.log`, about 200 KB a day on a busy
machine. The stored system log on a busy machine keeps less than a day
of fseventsd's messages, which is not long enough to see what happened
when a leak started.

```sh
make install-recorder
make uninstall-recorder
```

## Checking whether macOS still has the bug

```sh
make check
```

This runs `check-fseventsd.py`, which disassembles fseventsd, finds the
sweeper, and looks for the wrapping counter check at its start. If it
finds the check, it reports "affected" and exits with status 3, so
`make check` fails. Otherwise it reports "not found" and exits with
status 0. Not finding the check does not prove the bug is fixed, because
a different compiler could produce different instructions for the same
code. After a macOS update that reports "not found", look at the new
sweeper's disassembly before uninstalling the watchdog.

The checker reads the arm64e part of fseventsd, which is what Apple
silicon Macs run, so its answer is not about Intel Macs. It needs `otool`
from the Xcode command line tools.

## Tests

```sh
make test
```

Most of the watchdog tests run `fseventsd-watchdog.sh` with fake `log`
and `launchctl` commands. The fake `launchctl` names a `sleep` process as
the running fseventsd, so the tests can see which processes the script
really signals. The tests check that the script:
- stops that process when it logs the warning, whether the warning
  arrives through the stream, the search at startup, or the search after
  a drop;
- leaves everything alone for other messages, including a warning from
  another process whose text names the running fseventsd;
- reports a warning it cannot act on because `launchctl` does not name
  the running fseventsd;
- starts the search back to boot without waiting for anything else, so
  that it runs alongside the stream (the fake `log` makes the search wait
  until the stream has started, so a script that searched first would
  never finish);
- exits with an error when the stream ends, and stops quietly if
  fseventsd is missing.

One test uses the real `launchctl` and a real line from fseventsd's log
with the warning in place of the message. Run as an ordinary user, the
script then tries to stop the real fseventsd and is refused, which shows
that it understands both real formats. That test is skipped when run as
root, and when fseventsd has logged nothing in the last hour. Other
tests check that the real `log` accepts both searches, and that the
plist `make install` builds runs exactly the script and restarts it only
after an error.

The checker tests run its analysis on prepared disassembly, with and
without the bug, and on this Mac's fseventsd if it is the build
described above.
