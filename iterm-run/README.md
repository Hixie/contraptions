# iterm-run

Runs a command in a zsh shell in iTerm2 if, and only if, that shell is
idle at an empty prompt. The command is never typed into the terminal,
so it can never end up in a running program, such as an editor or a
password prompt, or on the end of a half-typed command line.

Typing a command into a terminal, for instance with iTerm2's
`write text` scripting command, sends it wherever keyboard input is
going at that moment. Asking iTerm2 first what the terminal is running
does not make that safe, because someone can start a program between the
question and the typing. Only the shell knows for certain whether it is
idle, so iterm-run has the shell decide. A small script sourced from
`~/.zshrc` makes each interactive zsh listen for commands, and the
`iterm-run` program sends a command to the shell in the frontmost iTerm2
session and reports whether the shell ran it.

## Installing

1. Add this line near the end of `~/.zshrc`:

   ```zsh
   source ~/dev/contraptions/iterm-run/iterm-run.zsh
   ```

   The script does nothing in shells that are not interactive. It binds
   one key sequence in the `emacs`, `viins` and `vicmd` keymaps, so it
   belongs after anything in `~/.zshrc` that resets keymaps, such as
   `bindkey -d`. Switching between `bindkey -e` and `bindkey -v` before
   or after it is fine.

2. A shell starts listening when it sources the script, so open new
   tabs, or run `exec zsh` in existing ones.

3. Optionally, put the sender on your `PATH`. If `~/bin` is on it already:

   ```bash
   ln -s ~/dev/contraptions/iterm-run/iterm-run ~/bin/iterm-run
   ```

The first time a program uses `iterm-run`, macOS asks whether that
program may control iTerm2. The sender needs this to ask iTerm2 which
session is frontmost. The question names the application that started
the sender, such as Terminal or Claude.

The first time a shell runs a sent command, iTerm2 asks "A control
sequence attempted to activate a session. Allow it?", because the shell
asks iTerm2 to bring its session forward (see below). Choose Allow, and
let iTerm2 remember the choice.

iterm-run needs macOS, zsh 5.9 (earlier versions are untested), and the
`perl` that ships with macOS. The sender also needs iTerm2 to be
running, unless the caller names the target shell itself (see
`ITERM_RUN_PID` below). The sender never launches iTerm2.

## Using it

```
iterm-run COMMAND
```

COMMAND is one line of shell code, passed as a single argument. The
target shell runs it as if someone had typed it at the prompt, so
aliases, functions and builtins such as `cd` work. The one exception is
history expansion: a `!` in COMMAND stays a `!`, so a program never has
to guard against it. The command has to fit in one request of at most
512 bytes, which leaves about 440 bytes for the command; the exact limit
depends on the directory and on the number of digits in the sender's
process ID, and the usage message gives it.

The sender prints one word and exits with a matching status:

| Output | Status | Meaning |
|--|--|--|
| `ran` | 0 | The shell was idle and has started running the command. |
| `busy` | 1 | The shell was not idle, or nothing was listening, or the shell exited before answering. Nothing ran. |
| (a message on standard error) | 2 | The request was not sent: the arguments were not exactly one non-empty line short enough to send, iTerm2 was not running or had no windows, or the sender could not create its reply pipe. |

`ran` means that the shell has started the command, not that the
command has finished. Its output goes to the terminal, not to the
sender. The shell runs the command's `preexec` hooks first, as it does
for a typed command, and an error in one of them stops the command, as
it would stop a typed one.

The sender has no timeout. It answers as soon as the shell does, which
is at once in almost every case, but it waits as long as the shell
cannot answer:

- while the shell itself is stopped, for instance with `kill -STOP`;
- while the shell is busy inside the line editor, for instance working
  out completions or running a key binding that starts a program;
- when the request reaches the shell in the moment between the shell
  starting a command and closing its pipe to new requests. The shell
  then answers `busy` when that command ends.

The target is the current session of iTerm2's frontmost window, that is,
the focused split pane of the selected tab, even when another
application is active. The sender asks iTerm2 for that session's
foreground process (its `jobPid` variable) and sends the command to that
process. If the foreground process is not a listening shell, for
instance because the shell is running a program, the answer is `busy`.
The sender never tries another session.

Two environment variables change what the sender does:

- `ITERM_RUN_PID` names the process ID of the target shell, and the
  sender then does not ask iTerm2. A caller that wants a session other
  than the frontmost one can find its `jobPid` with iTerm2's scripting
  interface and pass it here. The shell does not have to be in iTerm2.
- `ITERM_RUN_DIR` names the directory that holds the shells' named pipes
  (see "How it works"). A shell reads it when it sources the script, so
  set it in the environment before that. The shells and the sender must
  name the same directory, though they may spell it differently, for
  instance through a symbolic link. The tests use it.

A program that wants to run a command runs the sender with the command
as its argument and reads the one word it prints. The argument is
passed as it is, without another layer of shell quoting. For instance,
a Claude Code mod can use the `$.process.run` function of the mod
interface, with the path of the sender in your checkout:

```ts
const result = await $.process.run(
  ["/path/to/contraptions/iterm-run/iterm-run", "deno task test"]);
```

## When a shell counts as idle

A listening shell runs a command only when all of these are true:

- It is waiting at its primary prompt, not at a continuation prompt such
  as `for> `, not in `select` or `vared`, and not running anything.
- The command line is empty.
- The line editor has read no keys of an unfinished key sequence, such
  as Ctrl-X, which starts several bindings, and is not waiting for the
  key that a binding reads next, as Ctrl-V waits for the key to insert
  literally.
- No numeric argument, such as `ESC 3`, is waiting for the next key.
- It is not in an incremental history search (Ctrl-R) and not in a
  recursive edit.

While the shell runs a command, it refuses requests at once, apart from
the moment described above.

Two states look exactly like an idle prompt from inside zsh, and the
shell treats them as idle: an empty line after `ESC x`, which waits for
the name of a line editor widget to run, and vi command mode after an
operator such as `d` that waits for its motion. A command sent in either
state runs, and then the key sequence carries on.

## What a sent command looks like

Before it runs a sent command, a shell in iTerm2 prints iTerm2's
`StealFocus` control sequence (`ESC ] 1337 ; StealFocus BEL`), which
makes iTerm2 bring the shell's window to the front, select its tab and
split pane, and become the active application. iTerm2 does this only if
the user allowed it when it asked, and never if "Disable potentially
insecure escape sequences" is on in its advanced settings. The shell
checks `TERM_PROGRAM`, and does not print the sequence in any other
terminal.

A sent command behaves like a typed one in these ways:

- The command appears on the prompt line, its output appears below it,
  and a new prompt follows. The prompt is worked out again, so prompts
  that show the directory or a git branch stay current.
- The command is added to history, subject to `hist_ignore_space` and
  any `zshaddhistory` hooks.
- The `preexec` and `precmd` hooks run, and `precmd` hooks see the
  command's exit status in `$?`. iTerm2's shell integration therefore
  marks the command, its exit status and the new prompt as it does for
  a typed command.
- The command runs with the terminal in its normal line mode and with
  bracketed paste turned off, so programs that read the terminal, such
  as `read`, `vim`, `ssh` and `sudo`, work as usual.
- Output that does not end with a newline is followed by zsh's usual
  marker, a highlighted `%`, before the prompt.
- Ctrl-C stops the command and Ctrl-Z suspends it, after which `fg`
  resumes it. After either, or after a shell error, the shell keeps
  listening and no keystroke is lost.
- `exit`, `logout` and `bye` end the shell at once. If zsh declines to
  exit because jobs are running, the shell keeps listening.

## Differences from typing a command

The shell runs a sent command inside a line editor function, because
zsh's line editor only acts on a command line after it reads a key from
the terminal. That leaves these differences, which zsh gives no way to
remove:

- `$?` for the next typed command, and `%?` in the prompt, still show
  the status from before the sent command. zsh does not let line editor
  functions set it.
- `typeset`, `local`, `declare`, `integer`, `float` and `readonly` make
  variables local to the command, `set --` changes only the command's
  own positional parameters, and an `EXIT` trap that the command sets
  runs as soon as the command finishes. `$0` names a function of the
  script.
- The line editor's own variables are visible to the command: `$BUFFER`
  holds the command's text, and `$WIDGET`, `$KEYS` and `$HISTNO` are set.
- `vared` and anything else that needs the line editor fail with "ZLE
  cannot be used recursively".
- A `zshaddhistory` hook that returns 2, which asks zsh to keep a
  command in the shell's history but not in the history file, keeps it
  out of both.
- A sent `exit` does not run the shell's `zshexit` hooks, because zsh
  skips them for an exit that starts inside the line editor. The script
  removes its own files itself, and zsh still saves the history file.
- A suspended job shows no command text in `jobs`, so `fg %name` does
  not find it.
- A sent `exit` leaves an empty prompt on the screen just before the
  shell ends.

## How it works

Each listening shell creates two files, each readable and writable only
by its owner, in the directory named by `ITERM_RUN_DIR`, or by default
`$(getconf DARWIN_USER_TEMP_DIR)iterm-run`. The script creates the
directory, if it does not exist yet, so that only its owner can open
it.

- `PID`, a named pipe (FIFO), where `PID` is the shell's process ID. The
  shell keeps it open for both reading and writing, so that the shell
  always counts as a reader.
- `PID.lock`, an empty file. The shell holds a lock on it, taken with
  `fcntl`, for as long as it runs. The system releases the lock when
  the process ends, however it ends.

A request is one line: the path of the sender's reply pipe, a tab, the
command, and a newline. The sender writes it in a single write of at
most 512 bytes. The system writes a pipe write of up to 512 bytes all at
once, so requests from two senders never mix. The reply pipe is
`reply-SENDERPID` in the same directory, and the answer is one line,
`ran` or `busy`. The shell ignores a request whose reply pipe is not a
named pipe called `reply-` and a number in its own directory.

The shell asks its line editor to call a handler whenever the request
pipe has data while the editor waits for keys (`zle -F`). The handler
reads one request and checks that the shell is idle. If the reply pipe
has no reader, because the sender has gone, the handler does nothing
more. Otherwise it answers `busy`, or it answers `ran` and runs the
command itself.

While a command runs, from the `preexec` hook to the `precmd` hook, the
shell sets the request pipe's permissions to none, so a sender's attempt
to open it fails and the sender answers `busy` at once. Requests already
in the pipe are answered `busy` when the command starts and when it
ends. The script puts these two hooks before any others, because an
error in a hook makes zsh skip the hooks after it, which would leave the
shell refusing requests.

The sender does the following:

1. It asks iTerm2 for the frontmost session's foreground process with
   `osascript`, unless `ITERM_RUN_PID` is set.
2. It creates its reply pipe and opens it for reading and writing, so
   that the shell's attempt to answer finds a reader.
3. It opens the shell's request pipe without waiting. That fails if
   nothing has the pipe open for reading, or if the shell is running a
   command, and the sender then answers `busy`.
4. It starts a helper process that waits for the shell's lock. If the
   helper gets the lock, or finds the lock file gone, the shell has
   exited, and the helper writes `busy` into the reply pipe.
5. It writes the request and prints the first answer that arrives.

If the sender is killed with SIGINT, SIGTERM or SIGHUP, it removes its
reply pipe and stops the helper. The helper does not hold the sender's
output open, so a caller that waits for the sender's output to end is
not kept waiting.

zsh carries out a sent `exit` only after its line editor reads a key. So
after a sent `exit`, the shell puts the key sequence `ESC [ 2026 ~` into
its own terminal's input with the `TIOCSTI` call, with echo turned off.
The script binds that sequence to a widget that does nothing. This is
the only input the shell ever adds to the terminal, and it does so only
for `exit`. Because zsh stops running shell functions once an exit is
pending, the shell removes its two files with builtins first.

A shell that is killed with SIGKILL, or replaced with `exec`, leaves its
two files behind, and a sender killed with SIGKILL leaves its reply
pipe. They do no harm: nothing holds a leftover request pipe open, so
senders answer `busy`, and a new shell or sender that gets the same
process ID replaces them.

## Tests

```
cd ~/dev/contraptions/iterm-run
python3 -m unittest test_iterm_run
```

The tests start real interactive zsh shells under pseudo-terminals with
a temporary `ITERM_RUN_DIR`, and send them commands with `ITERM_RUN_PID`,
so they need macOS, zsh and `perl` but not iTerm2. They check the
terminal output by rendering it into screen lines with `vt.py`, a small
renderer for the output zsh's line editor produces.
