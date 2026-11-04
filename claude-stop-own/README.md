# claude-stop-own

Stops processes that belong to the Claude Code session that runs it,
and refuses to stop any other process.

Many agent sessions run on the same machine at once, often running the
same commands. A session that has lost track of a process it started,
for instance after its context was summarised, can find the PID again
with `ps`, but a bare `kill` cannot tell that PID from one belonging to
another session's test run. claude-stop-own decides that from facts
about the process, so the decision depends neither on what the agent
remembers nor on a permission checker that sees only the command.

The rules below rely on the working directory and the `CLAUDE_PID`,
`CLAUDE_CODE_SESSION_ID` and `HOME` variables that Claude Code gives
the session's shell. They stop a session from signalling another
session's process by mistake. A caller that changes directory into
another session's worktree, or overrides `CLAUDE_CODE_SESSION_ID` or
`HOME`, can get past them. So can a caller that overrides `CLAUDE_PID`
from a process group that Claude Code did not start, such as a shell
in a tmux pane, whose parent is the tmux server. In each case the
command it runs shows that it did.

```
claude-stop-own.py [--tree] [-s|--signal NAME] PID ...
```

A process belongs to the calling session when any of these holds:

- Walking parent PIDs up from the process reaches `$CLAUDE_PID`. Claude
  Code sets this variable to the session's own process, which is an
  ancestor of every shell the session runs commands in. If the variable
  is set, it must name the parent of the nearest process-group leader
  among claude-stop-own and its ancestors, and must not be PID 1;
  otherwise claude-stop-own signals nothing and exits with status 2.
  Claude Code starts each command's shell as the leader of a new
  process group, so the Claude process is that shell's parent. Setting
  `CLAUDE_PID` to a process higher up, such as PID 1 or the Claude
  desktop app, which are ancestors of every session's processes,
  therefore fails. So does running claude-stop-own with `CLAUDE_PID`
  set under anything that starts a new process group, such as longrun,
  GNU `timeout` or `script`; such a call has to leave `CLAUDE_PID`
  unset.
- The process's working directory is inside the caller's
  `~/dev/commonfabric/REPO/XX` worktree, where `XX` is a one or two
  character worktree code, and the caller's working directory is inside
  the same worktree. `REPO/root` and `~/dev/commonfabric` itself never
  count, since many sessions share them.
- The process's command line or working directory contains
  `/$CLAUDE_CODE_SESSION_ID/scratchpad`, the end of the session's
  scratchpad path. This finds processes that have been orphaned (so
  that their parent is now PID 1) but that still use the session's
  scratchpad.

claude-stop-own never signals itself or any process above it, such as
the shell that runs it or the Claude Code process.

`--tree` also stops every descendant of each PID. Each descendant must
meet one of the conditions on its own; being the child of an accepted
process is not enough.

`-s` or `--signal` picks the signal, by name, such as `KILL` or
`SIGINT`. The default is `TERM`.

For each PID it signals, claude-stop-own prints the signal and the
condition that accepted the PID. For each PID it refuses, it prints the
process's working directory and command line, so the caller can see
whose process it is. It exits with status 1 if it refused any PID or
could not signal one, and 0 otherwise. A process that exits between the
moment claude-stop-own lists the processes and the moment it sends the
signal is reported but does not count as a failure.

## Tests

```
python3 -m unittest test_claude_stop_own
```

The tests start real processes, under a temporary `HOME` that holds a
fake `~/dev/commonfabric`, and need macOS (`ps` and `lsof` output).
