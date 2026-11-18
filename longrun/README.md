# longrun

Runs a long command, such as a test suite, a build or a gate, for an
agent that has to wait for it. Waiting costs one tool call. If the
command stops printing for a while, longrun says so, with what each of
its processes is doing, and leaves it running. If it stays silent,
longrun says so again less and less often. The command ends
when whoever started it is gone.

It works for any command. It knows nothing about any one test suite,
apart from recognising the summaries that some test runners print, and
the lines in which some say that a test has been running for a while.

## Running it

```sh
longrun run [--quiet MINUTES] [--owner PID] [--keep] -- COMMAND ARG...
longrun run [--keep] 'COMMAND LINE'
longrun wait RUN
longrun stop RUN
longrun list
```

With one argument, `run` gives it to `/bin/sh -c`, so it can hold pipes,
`&&` and `cd`. With more than one, it runs them as the program and its
arguments. The command runs in the current directory, with its standard
input at `/dev/null`, its output and errors written straight to the
log, and the standard handling of SIGINT, SIGQUIT, SIGTERM and SIGHUP
even when the caller ignores them. Unless the environment says
otherwise, it runs with `PYTHONUNBUFFERED=1`, so that what Python
prints reaches the log at once and in the order it was printed.

Give longrun the command itself. Do not pipe it through `tail` or `grep`
and do not redirect its output: longrun keeps the whole output in a log
and prints the part that matters at the end. A command whose output goes
to `tail` prints nothing until it ends, so it looks stalled.

`run` prints `longrun: started DIR` once the command is running, then
blocks until the command ends or stalls.

When the command ends, `run` prints what happened and how long it took,
the path of the log and its length, and a part of the log with each line
numbered as in the log file. It then exits with the command's exit
status, or 128 plus the signal number if a signal ended it. For example:

```
longrun: exit 1 after 4m12s: deno task test
log: /…/scratchpad/longrun/1026-102900-test-z8ubjjle/log (71601 lines)
longest silence: 2m40s after its output in line 5 (from 1m02s to 3m42s after the start)
 6|  ERRORS
 …
27| FAILED | 1 passed | 1 failed (11ms)
```

The `longest silence` line is there when the command once went a minute
or more without output other than hang warnings (see "Hang warnings"
below). It names the line that holds the last output before that
silence, or says that the silence came before any output, and says when
the silence started and ended. A command whose longest silence comes
close to the `--quiet` time is one that should print more progress.

The part of the log shown is the test runner's own summary when longrun
recognises it, and otherwise the last 5 lines of a run that succeeded or
the last 40 of one that failed. For a run that failed, it also shows up
to 20 earlier lines that report a failure: lines that start with
`FAIL`, `FAILED` or `--- FAIL`, lines that start with `error:` or
hold `: error: `,
`Test Case '…' failed` and `** BUILD FAILED **`. It recognises:

- a summary block that a project's test runner or other script prints
  at the end of a run, from its header line: `=` signs and a space,
  then an optional name, then either `test summary` and more `=` signs,
  or `summary:` and the outcome, as in `==== test summary ====`,
  `=== unit test summary: FAILED: 2 failed ===` or
  `=== bench summary: 'U0' is live ===`. A name holds only letters,
  digits, spaces, hyphens and underscores, and case matters. Any other
  header that ends in `summary` with no colon after it, such as pytest's
  `==== warnings summary ====`, is a section of the output rather than
  its summary. longrun also takes the last line
  that matches the regular expression in `$LONGRUN_SUMMARY`, for a
  project whose header looks different;
- pytest: the short test summary, or else the final `=== … in 1.2s ===`
  line;
- Python unittest: from the first `FAIL:` or `ERROR:` block;
- deno test: the `ERRORS` and `FAILURES` sections, or else the final
  `ok | … passed` line;
- xcodebuild and XCTest: the first 40 lines that report a failure, as
  above, and the final `Test Suite '…' passed` or `failed` line, or
  `** BUILD FAILED **` and the like, with what follows it.

At most 60 lines are shown. Where lines are left out, a line says how
many. Only the last 4 MB of the log are searched, so a huge log costs
no more than a small one.

For the authors of scripts: a script whose result matters should end
its output with a summary block, since longrun, a person reading a CI
log and a person at a terminal all look there first. The block's header
gives the outcome, such as
`=== verify-builds summary: FAILED, 99 row(s) in 10m45s, 2 failed ===`.
The lines after it are the ones the outcome rests on: each failure and
its log file, and what the run did not check. Whatever the script
prints after the header is shown as part of the block, so the form is
for the final header only, not for the headers of a run's stages.

## Stalls

A run has stalled when it has printed nothing for `--quiet` minutes (10
by default, and more than 0), other than hang warnings. longrun then
writes a report, and `run` prints it and exits with status 75 while the
command keeps running. A command that itself exits with status 75 is
told apart by the first line of the output, which says either `STALLED`
or how the command ended. The report says how much the command printed
and when, so that a command that was always slow can be told apart from
one that has stopped:

- how much the log holds, in bytes and lines, and how many bytes were
  written to it in each of at most 12 equal parts of the run so far, each
  part a round length of time. These count everything the command
  printed, hang warnings included;
- the longest earlier silence, with the line that holds the last output
  before it, or that it came before any output; or that the command
  never went a minute without output before; or that it has printed no
  output other than hang warnings since the run started;
- for each of the last lines of the log, how long after the start it
  was complete, to within about a second: when longrun first recorded
  its line break, or for a last line that has not ended, its last byte.

It then lists the command's processes with their CPU use, and for each
one what it is doing:

- For a Python process, the innermost two frames of each thread and the
  innermost frame outside the standard library and installed packages,
  which is usually the line of the project's code that is waiting.
- For any other process, the busiest frames that a one-second macOS
  `sample` found. Up to 12 processes are sampled, the command's own
  process first, then the busiest.

Processes that run the same command and are doing the same thing are
listed once. The report ends with the last lines of the log and the
commands to continue:

```
longrun: STALLED, no output for 10m00s. The command is still running (6 processes, 0% CPU between them).
The log so far: 48KB in 1204 lines over 25m10s. Bytes written to it in each 5m00s since the run started: 12K, 1.2K, 30K, 5.0K, 0, 0 (the last part covers only 10s).
Before this silence, the longest silence was 6m02s after its output in line 40 (from 1m08s to 7m10s after the start).
Each line of the log below starts with how long after the start it was complete.
4 processes (51202, 51203, 51204, 51205), 0% CPU: …/.venv/bin/python -u -c import sys;exec(eval(sys.stdin.readline()))
    1 thread:
      File ".../site-packages/execnet/gateway_base.py", line 534 in read
      …
pid 51206, 0% CPU: …/.venv/bin/python -u -c import sys;exec(eval(sys.stdin.readline()))
    1 thread (current):
      File ".../lib/python3.12/selectors.py", line 416 in select
      File ".../lib/python3.12/subprocess.py", line 2115 in _communicate
      File "tests/test_cleanup.py", line 88 in test_interrupted_run_removes_its_files
run: /…/scratchpad/longrun/0412-093015-pytest-k3v8a2qd (samples in stall-1/; the log has 1204 lines, ending:)
15m09s 1195| tests/test_cleanup.py::test_partial_files PASSED
…
15m10s 1204| tests/test_cleanup.py::test_interrupted_run_removes_its_files
If it stays silent, waiting reports it again after another 20m00s.
To keep waiting: longrun wait 0412-093015-pytest-k3v8a2qd
To end it:       longrun stop 0412-093015-pytest-k3v8a2qd
```

`wait` blocks again, until the command ends or stalls again. If the
command prints something other than hang warnings, the next stall is
reported after `--quiet` minutes of silence, as the first was. If it
prints nothing else, the next report comes after twice as long as the
time before, counted from when `wait` starts: with the default of 10
minutes, `wait` reports the stall again after another 20 minutes, then
40, then 80, and so on. Each report says how long that is for the next
`wait`, if the command stays silent. If a new stall happened while
nothing was waiting, `wait` reports it at once. `stop` ends the command
and prints the same result as `run` would. A run that was ended this
way, or in any of the ways listed below, exits with the command's
status, or 143 (128 plus SIGTERM) if the command exited with 0, since it
did not finish.

Python stacks come from `pystacks/sitecustomize.py`, which longrun puts
first on the command's `PYTHONPATH`. In each Python process it starts,
that module registers a handler that writes every thread's stack to a
file in the run directory on SIGINFO, then runs the `sitecustomize`
module that it hides, if there is one (Homebrew's Python has one). The
module it hides is the one that Python finds in the entries of the path
after the entry it was loaded from. Another `sitecustomize` earlier on
the path that runs the module it hides in the same way can therefore run
this one. The modules then run in the order of their entries on the
path, the module of each entry once. SIGINFO is ignored by default, so a
process without the handler is unaffected by it, and longrun sends it
only to processes that are still running Python. The stacks are read
once the `sample` runs have finished, which takes a second or more, and
a process whose stacks have not arrived by then is described by its
`sample` frames. Reading a Python stack from outside the process needs
root on macOS, which is why longrun asks the process instead. The
handler is registered without chaining to an earlier one, so that a
system call it interrupts is restarted rather than failing.

### Hang warnings

Some test runners print a warning when a test has been running for a
while, and some print every thread's stack with it. Such a warning says
that the run is not moving, so it does not count as output: the stall
is reported `--quiet` minutes after the last line that was not one,
however many warnings followed it. The warnings longrun recognises are:

- deno test's `'NAME' has been running for over (1m0s)`, which it
  prints 1, 2, 4, 9, 21 and more minutes into a test, at the start of a
  line or after the `NAME ...` it printed when the test started;
- cargo test's `test NAME has been running for over 60 seconds`;
- cargo-nextest's `SLOW [> 60.000s] TEST`;
- `stuck-test: ` and what follows it in its line, which is how a plugin
  in one of the projects that use longrun reports a stuck test;
- Python's faulthandler: `Timeout (0:05:00)!`, which
  `faulthandler.dump_traceback_later()` and pytest's
  `faulthandler_timeout` print, and the stack dumps it prints, which
  are its thread headers, such as
  `Thread 0x000000016c573000 (most recent call first):`, its frames,
  such as `  File "a.py", line 3 in f` (a traceback has a comma before
  `in`), its `  <no Python frame>` lines, and the `...` lines with
  which it cuts a stack or a list of threads short.

longrun also takes as a warning any line that matches the regular
expression in `$LONGRUN_HANG`, which `run` checks before it starts the
command. Blank lines count as neither output nor warnings, and colour
codes are ignored.

A warning can follow output on the same line, as when deno test has
printed `NAME ...` as the test started, or pytest has printed its
progress dots without a line break. The text from the start of the
warning to the end of the line is the warning. What comes before it is
output, counted from when it was written, not from when the warning
was. For this reason each recognised warning is matched from its first
character, and a pattern in `$LONGRUN_HANG` should be too.

Lines that report a test failed, killed or timed out are output, since
the run moves on after them: pytest-timeout's, Go's, XCTest's and
those of the JavaScript test runners.

longrun reads the log as the command writes it. A line is judged when
it ends. A line that has not ended yet is judged when a report falls
due, or `--quiet` minutes after its latest part was written, whichever
comes first. Only the first 4096 bytes of a line, without its colour
codes, are searched for a warning. A report that follows warnings says
so, and its excerpt of the log starts with the last 5 lines up to the
last output, however far back that is:

```
longrun: STALLED, no output for 10m00s other than hang warnings after its output in line 40. The command is still running (2 processes, 0% CPU between them).
…
run: … (samples in stall-1/; the log has 152 lines, ending, after its last output:)
 9m40s  36| ..............                                       [ 98%]
…
10m02s  40| ....
[102 lines left out]
15m02s 143|   File ".../execnet/gateway_base.py", line 1806 in serve
…
```

## How a run ends

longrun starts a supervisor process, in a session of its own, that runs
the command, watches the log for what the command writes, and tells
`run` and `wait` when the command stalls or ends. The supervisor ends
the command with SIGTERM, then SIGKILL 10 seconds later if anything is
left, sent to the command's process group and to every process
descended from it. It does this when:

- the owner exits. The owner is the process that started the job that
  ran longrun: the parent of the outermost process that shares
  longrun's process group. Claude Code, Codex and interactive shells
  each start a command in a process group of its own, so the owner is
  the agent, or the terminal's shell, and not the shell or wrappers in
  between. `--owner PID` names another process instead;
- the `run` or `wait` call that was waiting for the command goes away
  without being told that the command stalled or ended, for instance
  because the agent's call was interrupted, and no other call is
  waiting;
- `longrun stop RUN` asks it to;
- the supervisor receives SIGTERM, SIGHUP, SIGINT or SIGQUIT;
- the subagent that started the run finishes while nothing waits for
  the run, if the hooks below are installed.

Descendants are found through their parent processes when the run is
ended, so a daemon, a process that has left the process group and whose
parent has exited, is not found. If the supervisor itself is killed
with SIGKILL, nothing ends the command; `longrun list` shows such a run
with the command's process id.

A run whose stall was reported and that nothing waits for lives on
until one of these happens. In a subagent, the hooks below end it when
the subagent finishes. In the main session, they keep the agent from
finishing its turn until it has either waited for the run or stopped
it. A `run` or `wait` call that reports a stall returns only after the
supervisor has either recorded that the call no longer waits or
exited, so the hooks never count a call that has returned as one that
is still waiting for a live run.
`longrun list` shows each run and whether it is still running.

When the command exits by itself, processes it left running in its
process group get SIGTERM, and SIGKILL 10 seconds later. The result
lists them, and says to give `--keep` if the command's job is to start
them. Processes that left the command's process group are not
signalled. What any process left running prints still goes to the
log.

## Commands that start servers

Some commands exist to start a process that outlives them, such as a
development server, a local service or a copy of an app that later
commands drive. Most such scripts start it with `&`, which leaves it in
the script's process group, so longrun ends it when the script exits.

`--keep` declares that the command starts such processes. When the
command exits by itself, longrun then leaves running what it left in
its process group, and the result lists them under `kept`. Nothing in
longrun watches or ends them after that. What they print goes on being
added to the run's log for as long as they run, so a server that prints
much should have its output redirected. A run that is ended before the
command exits, in any of the ways listed above, still ends everything
the command started.

A script can instead start its server in a process group of its own,
and redirect its output. In bash and `/bin/sh`, `set -m` before the `&`
does this, by turning on job control; zsh refuses `set -m` without a
terminal.
longrun then leaves the server alone without `--keep`, as do terminals
and other tools that end a command's process group.

## Claude Code hooks

Claude Code moves a Bash call to the background when it outlives the
call's timeout, which is 2 minutes unless `BASH_DEFAULT_TIMEOUT_MS` or
the call says otherwise. Agents then poll. `longrun hook` handles three
hook events so that a call that runs longrun blocks instead, and so
that no stalled run is forgotten:

- Before a Bash call that runs longrun (as a command, not in quoted text
  or a here-document), it raises the call's timeout to
  the largest Claude Code allows. It leaves calls with
  `run_in_background` alone, since an agent that asks for the background
  means to work while it waits. It changes nothing else and grants no
  permission: Claude Code 2.1.308 applies a hook's `updatedInput`
  given without a permission decision and then asks for permission as
  it would have. In a subagent, it also puts `LONGRUN_AGENT=ID` before
  `longrun run`, with the subagent's id, which the run records. Claude
  Code ignores leading assignments when it matches a command against
  permission rules. It also starts a subagent's call with `sleep 0;`,
  for the reason given under "Calls from subagents" below.
- When a subagent finishes, it ends the runs that subagent started that
  nothing is waiting for, such as one whose stall the subagent was told
  about.
- When the main agent is about to finish its turn, it sends the agent
  back once if a run the main agent started has reported a stall and
  nothing is waiting for it, naming the runs and the commands that
  continue or end them.

Claude Code clamps the timeout to `BASH_MAX_TIMEOUT_MS`, so that has to
be large as well. The settings:

```json
{
  "env": {"BASH_MAX_TIMEOUT_MS": "2147483647"},
  "hooks": {
    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command",
      "if": "Bash(longrun *)", "command": "longrun hook"}]}],
    "SubagentStop": [{"hooks": [{"type": "command",
      "command": "longrun hook"}]}],
    "Stop": [{"hooks": [{"type": "command", "command": "longrun hook"}]}]
  }
}
```

The `if` condition means the hook runs only for calls that run a
command named `longrun`, so `longrun` has to be on `PATH` under that
name:

```sh
ln -s ~/dev/contraptions/longrun/longrun.py ~/.local/bin/longrun
```

If agents are told to run longrun by its full path instead, name that
path in the `if` condition and in each `command`, as in
`"if": "Bash(/Users/me/dev/contraptions/longrun/longrun.py *)"`.

### Calls from subagents

When a subagent runs in the background, as the subagents that the Claude
desktop app starts do, Claude Code limits how long each of its
foreground Bash calls may block. Once a call has run for as many
milliseconds as the server-side feature flag `tengu_maple_ferry` says,
Claude Code moves the call to the background, whatever the call's
timeout. The server set the flag to 180000 when this was written, with
Claude Code 2.1.317. The subagent then gets
something like this in place of longrun's report:

```
Command was moved to the background (ID: bey80em1e) after running for
180s. Output is being written to: .../tasks/bey80em1e.output.
```

No setting changes the limit. The Bash tool uses the smaller of the
call's timeout and the flag. `CLAUDE_CODE_AUTO_BACKGROUND_TIMEOUT_MS`
applies only to the main agent. Claude Code ignores local overrides of
its feature flags. `CLAUDE_CODE_DISABLE_BACKGROUND_TASKS` lifts the
limit, but it also turns off background tasks and background subagents
everywhere.

Claude Code applies the limit only to a call that it could move to the
background, and it never moves a call whose first command is `sleep`.
In Claude Code 2.1.317, the check compares the first word of the first
command in the call with a list that holds only `sleep`. The hook
therefore puts `sleep 0;` at the start of each foreground call to
longrun that a subagent makes. `sleep 0` returns at once, so the call
then runs as the subagent wrote it, and blocks until longrun returns.
Claude Code refuses a call that starts with a `sleep` of 25 seconds or
more followed by other commands, which a `sleep` of 0 seconds is not. A
call that already starts with `sleep` is exempt already, and is left as
it is, so that the hook neither adds a second `sleep` nor gets such a
call past that refusal.

This was found by reading the code bundled in Claude Code 2.1.317, and
confirmed with subagents that the desktop app ran in the background. A
foreground call of `sleep 0; sleep 220; echo finished` ran for 220
seconds. A foreground call of `longrun run 'sleep 240'` was moved to
the background after 180 seconds without the hook, and blocked for the
whole run once the hook had added `sleep 0;`. The exemption is not
documented, so a Claude Code release could remove it. If that happens,
a subagent's longrun calls get the message above again. To check, have
a subagent that runs in the background run
`sleep 0; sleep 220; echo finished` in the foreground with a `timeout`
of 600000, and see whether it prints `finished`. A subagent that does
not run in the background has no limit, and so proves nothing.

If the call no longer prints `finished`, the hook needs another way past
the limit. One way works without the exemption, at the cost of a call to
the model every few minutes. The hook reads the flag's value, which
Claude Code caches as `cachedGrowthBookFeatures.tengu_maple_ferry` in
`.claude.json`, and gives each `longrun run` and `longrun wait` in the
call a time a little before the limit. At that time the call tells the
supervisor that it is leaving, so that the supervisor does not end the
run as it would for an interrupted call. The call then exits with status
75 and a report that names the run, and the subagent runs `longrun wait`
again.

## Instructions for agents

The hooks make a call that runs longrun block, but agents still have to
choose to run their long commands through it, and they need to know how
to read what it says. This text, added to `~/.claude/CLAUDE.md` or to a
project's `CLAUDE.md` or `AGENTS.md`, tells them:

````markdown
## Long commands

Run any command that can take more than a couple of minutes, such as a
test suite, a build, a gate or a job that waits in a queue, through
longrun, in one call:

    longrun run 'COMMAND'

The call blocks until the command ends. A hook gives it the longest
timeout Claude Code allows, so do not set `timeout` yourself, and do not
poll, sleep or read the log while it runs.

When the command ends, longrun prints its exit status, how long it took,
the log's path, and the part of the log that matters, numbered as in the
log: the test runner's summary when longrun recognises it, and otherwise
the last lines. Read the log only for what that part leaves out, by line
number.

If the command prints nothing but hang warnings for 10 minutes, the
call returns with exit status 75 and a report that starts with
`STALLED`. The command is still running. The report says when the
command printed what, and what each of its processes is doing. Decide
at once what to do:

- If a process is waiting for something that will come, such as a free
  slot in a queue, or is busy in a long silent step, run
  `longrun wait RUN`. It blocks until the command ends or stalls again.
  If the command prints something, it stalls again after another 10
  minutes of silence. If it prints nothing, the next report comes after
  twice as long as the last, counted from when `wait` starts: 20
  minutes, then 40, then 80. Each report says how long the next `wait`
  will be if the command stays silent.
- If a process is waiting for something that will never happen, run
  `longrun stop RUN`, then fix the cause.

A report that follows a `wait` with no output in between means the step
you expected to finish has not finished. Wait again only if you can
name what the command is waiting for and why it has not arrived yet.
Otherwise, stop it. Each report is kept as `stall-N.txt` in the run
directory, so you can compare it with the earlier ones. If a run is
still stalled an hour after its first report, tell whoever you are
working for exactly what is happening.

A script you write for yourself should print a line as each step starts
or finishes, even when its results go to files. Print only real
progress, never on a timer. The result names the run's longest silence
when it lasted a minute or more; a script whose longest silence comes
close to 10 minutes needs more progress output. Give `--quiet MINUTES`
only for a command that cannot print progress and is known to stay
silent for more than 10 minutes, set to about how long it stays silent.
The intervals then start at that many minutes instead of 10, and double
in the same way.

When the command's job is to start a process that outlives it, such as
a development server, a local service or a copy of an app that later
commands drive, give `--keep`. Without it, longrun ends whatever the
command leaves running when it exits, and the result lists what it
ended. Do not give `--keep` to a test suite or a build to make that list
go away: a test or build that leaves processes running has a bug to fix.

Give longrun the bare command, and run each longrun call, `run` and
`wait` alike, bare too. Do not pipe either through `tail` or `grep`, do
not redirect their output, and do not wrap a longrun call in `$(...)`.
longrun keeps the command's whole output in a log in your scratchpad,
and a command whose output goes to `tail` prints nothing until it ends,
so it looks stalled. longrun's exit status is the result: the command's
own status, or 75 for a stall. A pipeline reports `tail`'s status
instead.

In a main session, use `run_in_background: true` on a `longrun run` call
only when you have other work to do while the command runs. If you would
end your turn right after starting it, block instead. If you do run it
in the background, you are notified when it ends or stalls, and its
output is the same short report. In a subagent, always block, and do not
end your turn, read the log or sleep while a run is going.
````

Without the hooks, the text should also tell agents to give each
`longrun` call `timeout: 2147483647`, since otherwise Claude Code moves a
call that outlives its timeout to the background. In a subagent, the
call should then also start with `sleep 0;`, for the reason given under
"Calls from subagents". That `sleep` returns at once, so it is not the
kind of waiting that the text tells agents not to do.

## Where runs live

Each run gets a directory in `$LONGRUN_DIR`. When that is not set, it
is `longrun/` in the Claude Code session's scratchpad (found through
`CLAUDE_CODE_SESSION_ID`, or given to the hook), and otherwise
`longrun-UID/` in the temporary directory. longrun refuses to use a
directory that belongs to another user. `wait` and `stop` take either the directory or its name
within that directory. A run directory holds:

- `spec.json`: the command, its directory, the owner, the stall
  threshold;
- `state.json`: the supervisor's view of the run, rewritten as it
  changes;
- `log`: everything the command printed, as bytes;
- `times`: when the command's output arrived, described below;
- `README.md`: a note that points to this file, for anyone who finds the
  directory;
- `result.txt`: what `run` prints when the command ends;
- `stall-N.txt` and `stall-N/`: the Nth stall report and its `sample`
  output;
- `py/PID.txt`: Python stacks written by each Python process;
- `supervisor.log`: the supervisor's own errors;
- `lock`: locked by the supervisor while it runs, which is how `list`
  and the hooks tell whether it is still running;
- `sock`: the socket that `run`, `wait` and `stop` talk to the
  supervisor through.

## The timing file

`times`, in each run directory, records when the command's output
arrived. The stall reports and the result use it, and it is there for
anyone who wants to measure how commands behave. The log stays exactly
what the command printed. The supervisor creates `times` when the run
starts, adds records to its end, and never changes what it has written.
If it cannot write a whole record, it deletes the file and records no
more, so that the file never says something false. Runs made by versions
of longrun from before it was added have no such file.

### Layout

All numbers are little-endian, with no padding between them. "u16" and
"u64" are unsigned integers of 16 and 64 bits, and "i64" is a signed
integer of 64 bits in two's complement.

The file starts with a header:

| Offset | Type | Field |
| --- | --- | --- |
| 0 | u16 | H, the length of the header in bytes, these two included (4) |
| 2 | u16 | R, the length of each record in bytes (72) |
| 4 | H − 4 bytes | fields that later versions may add |

Records follow the header, from offset H, one after another, each R
bytes long:

| Offset | Type | Field |
| --- | --- | --- |
| 0 | u64 | the size of the log, in bytes |
| 8 | u64 | how many line breaks (bytes 0x0A) the log holds, up to that size |
| 16 | i64 | when the record was written, in nanoseconds on the monotonic clock |
| 24 | i64 | the same moment, in nanoseconds since 1970-01-01 00:00:00 UTC |
| 32 | i64 | when the last output before then arrived, in nanoseconds on the monotonic clock |
| 40 | u64 | the number of the line that output is in, counting from 1, or 0 if there was no output yet |
| 48 | i64 | when the longest silence that had ended by then started, in nanoseconds on the monotonic clock |
| 56 | i64 | when that silence ended, in nanoseconds on the monotonic clock |
| 64 | u64 | the number of the line that holds the last output before that silence, or 0 if it came before any output |
| 72 | R − 72 bytes | fields that later versions may add |

The numbers in parentheses are the values that this version writes. A
later version adds fields only at the end of the header or of a record,
and makes H or R larger to match, so that what each field means never
changes. A reader takes the lengths from the header, uses the fields it
knows, and skips the rest.

The monotonic clock is the one that macOS calls `mach_absolute_time`,
in nanoseconds, which is what Python's `time.monotonic_ns()` returns;
longrun's values are correct to within a microsecond.
It is the clock that longrun measures silence with. It counts from when
the machine started, and it stops while the machine sleeps, so the
difference between two records' monotonic times is how long the command
ran in between. The difference between their wall-clock times also
counts any time the machine slept, and any change to the system clock.
Monotonic times can be compared only with others from the same machine
since it last started.

### When records are written

The supervisor writes a record:

- when the run starts, before the command prints anything: the size, the
  line breaks and the lines are 0, and the last output and both ends of
  the longest silence are the start, which they stay until there is
  output;
- when the log grows or the last output changes, but at most one record
  each second: at once if the last record is a second old or more, and
  otherwise one second after the last record, covering everything that
  arrived in between;
- just before each stall report;
- when the command has ended, or could not be started, and again when
  the run ends if anything has changed since, as when processes that
  the command left running print more.

The sizes, the line breaks, the times of the records and the times of
the last output never decrease from one record to the next. A byte of
the log that lies at or after the size in one record and before the
size in the next was read by the supervisor after the first record was
written, and before the second was. The
supervisor reads what the command writes as soon as the system tells it
there is more, and writes a record at most a second after that, so each
byte's time is known to within about a second.

### Reading it

To find when line N of the log was printed, counting from 1, take the
first record whose line break count is N or more: the line's line break
had arrived by that record's time. The log's last line may have no line
break yet; it was printed by the time of the first record whose size is
the size of the log. To find the line that holds the byte at offset B,
count the line breaks in the log before offset B and add 1. A record's
line break count is the number of lines that were complete when it was
written.

The fields from offset 32 are about output: what the command wrote other
than hang warnings, blank lines and colour codes, as described under
"Hang warnings". A silence is a time without such output, between two
pieces of output, or between the start and the first.

longrun judges a line when it ends, when the command ends, and earlier
in the cases that "Hang warnings" describes, so the last output's time
and line can change some time after the output arrived. When it judges a
line that arrived in pieces, such as pytest's dots, each piece of output
in it counts, so the silences between the pieces are counted as well.
That is why the longest silence is a field of its own: it cannot always
be worked out from the last output's times in the records.

The silence that is going on when a record is written runs from the
last output's time to the record's time, and is not counted in the
record's longest silence until it ends. In a run's last record, the
run's longest silence is the longer of the two.

A reader must expect:

- no file at all, for a run from an older version of longrun, or one
  whose timing file could not be written. Its output's timing is
  unknown, which is not the same as no output.
- a file that is shorter than 4 bytes, or than H bytes. It has no
  records yet.
- fewer than R bytes after the last whole record. That is a record that
  is being written, or whose writing was cut short, and the reader
  ignores those bytes.
- H less than 4, or R less than 72. Such a file is not one that this
  section describes, and the reader ignores it.

In Python:

```python
import struct

def records(path):
    """(size, line_breaks, monotonic_ns, wall_ns, last_output_ns,
    last_output_line, longest_start_ns, longest_end_ns, longest_line)
    for each record in a timing file."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) < 4:
        return []
    header, length = struct.unpack_from("<HH", data)
    if header < 4 or length < 72:
        return []
    return [struct.unpack_from("<QQqqqQqqQ", data, offset)
            for offset in range(header, len(data) - length + 1, length)]
```

Each run directory also holds a `README.md` that links to this section,
so that someone who finds a timing file can find out how to read it.
longrun writes it when it creates the directory.

## Requirements and tests

longrun needs macOS, for kqueue, `sample` and SIGINFO, and Python 3.9 or
later with nothing beyond the standard library. The tests run real
commands and take two to three minutes on a busy machine:

```sh
python3 -m unittest test_longrun
```
