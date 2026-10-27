# longrun

Runs a long command, such as a test suite, a build or a gate, for an
agent that has to wait for it. Waiting costs one tool call. If the
command stops printing for a while, longrun says so once, with what
each of its processes is doing, and leaves it running. The command ends
when whoever started it is gone.

It works for any command. It knows nothing about any one test suite,
apart from recognising the summaries that some test runners print.

## Running it

```sh
longrun run [--quiet MINUTES] [--owner PID] -- COMMAND ARG...
longrun run 'COMMAND LINE'
longrun wait RUN
longrun stop RUN
longrun list
```

With one argument, `run` gives it to `/bin/sh -c`, so it can hold pipes,
`&&` and `cd`. With more than one, it runs them as the program and its
arguments. The command runs in the current directory, with its standard
input at `/dev/null`, and with the standard handling of SIGINT, SIGQUIT,
SIGTERM and SIGHUP even when the caller ignores them.

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
 6|  ERRORS
 …
27| FAILED | 1 passed | 1 failed (11ms)
```

The part of the log shown is the test runner's own summary when longrun
recognises it, and otherwise the last 5 lines of a run that succeeded or
the last 40 of one that failed. It recognises:

- a summary block that a project's test runner prints at the end of a
  run, from its header line: a line such as `==== test summary ====` or
  `=== unit test summary: FAILED: 2 failed ===`, or the last line that
  matches the regular expression in `$LONGRUN_SUMMARY`, for a project
  whose header looks different;
- pytest: the short test summary, or else the final `=== … in 1.2s ===`
  line;
- Python unittest: from the first `FAIL:` or `ERROR:` block;
- deno test: the `ERRORS` and `FAILURES` sections, or else the final
  `ok | … passed` line;
- xcodebuild and XCTest: each `error:` line and `Test Case '…' failed`
  line, and the final `Test Suite '…' passed` or `failed` line, or
  `** BUILD FAILED **` and the like, with what follows it.

At most 60 lines are shown. Where lines are left out, a line says how
many. Only the last 4 MB of the log are searched, so a huge log costs
no more than a small one.

## Stalls

A run has stalled when it has printed nothing for `--quiet` minutes (10
by default, and more than 0). longrun then writes a report, and `run` prints it and exits
with status 75 while the command keeps running. A command that itself
exits with status 75 is told apart by the first line of the output,
which says either `STALLED` or how the command ended. The report lists the
command's processes with their CPU use, and for each one what it is
doing:

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
4 processes (51202, 51203, 51204, 51205), 0% CPU: …/.venv/bin/python -u -c import sys;exec(eval(sys.stdin.readline()))
    1 thread:
      File ".../site-packages/execnet/gateway_base.py", line 534 in read
      …
pid 51206, 0% CPU: …/.venv/bin/python -u -c import sys;exec(eval(sys.stdin.readline()))
    1 thread (current):
      File ".../lib/python3.12/selectors.py", line 416 in select
      File ".../lib/python3.12/subprocess.py", line 2115 in _communicate
      File "tests/test_cleanup.py", line 88 in test_interrupted_run_removes_its_files
…
To keep waiting: longrun wait 0412-093015-pytest-k3v8a2qd
To end it:       longrun stop 0412-093015-pytest-k3v8a2qd
```

`wait` blocks again, until the command ends or stalls again. A stall is
reported once: `wait` does not return for the stall it was told about,
only for a new one, which starts when the command prints something and
then falls silent again. If a new stall happened while nothing was
waiting, `wait` reports it at once. `stop` ends the command and prints
the same result as `run` would. A run that was ended this way, or in
any of the ways listed below, exits with the command's status, or 143
(128 plus SIGTERM) if the command exited with 0, since it did not
finish.

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

## How a run ends

longrun starts a supervisor process, in a session of its own, that runs
the command, copies its output to the log and tells `run` and `wait`
when the command stalls or ends. The supervisor ends the command with
SIGTERM, then SIGKILL 10 seconds later if anything is left, sent to the
command's process group and to every process descended from it. It does
this when:

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
it. `longrun list` shows each run and whether it is still running.

When the command exits by itself, processes it left running in its
process group get SIGTERM, and SIGKILL 10 seconds later. The result
lists them. Processes that left the command's process group are not
signalled, but if they still write to the command's output, they get
SIGPIPE once longrun has exited. A server that a command starts and
means to outlive it should therefore leave the process group and
redirect its output, as daemons do.

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
  permission rules.
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

## Instructions for agents

The hooks make a call that runs longrun block, but agents still have to
choose to run their long commands through it. This text, added to
`~/.claude/CLAUDE.md` or to a project's `CLAUDE.md` or `AGENTS.md`, tells
them to:

````markdown
## Long commands

Run any command that can take more than a couple of minutes, such as a
test suite, a build, a gate or a job that waits in a queue, through
longrun, in one call:

    longrun run 'COMMAND'

Give it the bare command. Do not pipe it through `tail` or `grep`, and do
not redirect its output: longrun keeps the whole output in a log in your
scratchpad, and a command whose output goes to `tail` prints nothing
until it ends, so it looks stalled. When the command ends, longrun
prints its exit status, how long it took, the log's path, and the part
of the log that matters, numbered as in the log: the test runner's
summary when longrun recognises it, and otherwise the last lines. Read
the log only for what that part leaves out, by line number.

The call blocks until the command ends. A hook gives it the longest
timeout Claude Code allows, so do not set `timeout` yourself, and do not
poll, sleep or read the log while it runs.

If the command prints nothing for 10 minutes, the call returns with exit
status 75 and a report that starts with `STALLED`. The command is still
running. The report says what each of its processes is doing. Decide at
once what to do:

- If a process is waiting for something that will come, such as a free
  slot in a queue, or is busy in a long silent step, run
  `longrun wait RUN`. It blocks until the command ends or stalls again,
  and never reports the same stall twice.
- If a process is waiting for something that will never happen, run
  `longrun stop RUN`, then fix the cause.

For a command that is known to stay silent for longer, give
`--quiet MINUTES`.

In a main session, `run_in_background: true` on a `longrun run` call
lets you work while it runs. You are notified when it ends or stalls,
and its output is the same short report. In a subagent, block instead.
````

Without the hooks, the text should also tell agents to give each
`longrun` call `timeout: 2147483647`, since otherwise Claude Code moves a
call that outlives its timeout to the background.

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
- `result.txt`: what `run` prints when the command ends;
- `stall-N.txt` and `stall-N/`: the Nth stall report and its `sample`
  output;
- `py/PID.txt`: Python stacks written by each Python process;
- `supervisor.log`: the supervisor's own errors;
- `lock`: locked by the supervisor while it runs, which is how `list`
  and the hooks tell whether it is still running;
- `sock`: the socket that `run`, `wait` and `stop` talk to the
  supervisor through.

## Requirements and tests

longrun needs macOS, for kqueue, `sample` and SIGINFO, and Python 3.9 or
later with nothing beyond the standard library. The tests run real
commands and take two to three minutes on a busy machine:

```sh
python3 -m unittest test_longrun
```
