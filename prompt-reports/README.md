# Prompt reports

Lists the prompts a person typed into Claude and Codex sessions during a time
window. It finds every session with activity in the window and writes three
reports:

- `counts.md`: two histograms and a table. The first histogram shows how many
  sessions had each number of typed prompts, in bins that double in width after
  2 prompts (0, 1, 2, 3–4, 5–8, and so on), since a few sessions have many more
  prompts than the rest. The second is cumulative: how many sessions had at
  most 0 prompts, at most 1, at most 2, and so on, one row for each number up
  to the largest. Each row of both gives its share of all sessions. The table
  has one row per session, giving the number of prompts the person typed into
  it and its share of all typed prompts, its title and identifier, and its
  first and last activity in the window. Sessions with no typed prompts are
  listed too.
- `prompts.md`: every typed prompt, in chronological order. An ℹ️ link before
  each prompt shows its time and session when the pointer rests on it.
- `by-length.md`: a cumulative histogram of how many typed prompts had at most
  1, 2, 4, 8, and so on characters, doubling until it covers the longest
  prompt, with each row's share of all typed prompts; then every distinct
  prompt, shortest first, with the number of times it was typed (`×2`) when
  that is more than once. Prompts are the same when their text is identical.

With `--format html`, the same reports are written as HTML pages. There, each
prompt's ℹ️ sits to its right and links to its session's row in the table.

With `--raw`, the reports hold only the charts and the prompts: `counts` has
just the two histograms; `prompts` has the prompts under a heading that gives
the window's length but not its dates, such as "All prompts typed over a
14-day period, in chronological order"; and `by-length` has the length
histogram and the distinct prompts with their repeat counts. There are no
other titles, no summaries, dates, times or sessions, and no ℹ️ links; the
charts keep their captions.

Run from this directory:

```sh
# The last 14 days.
python3 prompt_reports.py --days 14

# An explicit window. Times without an offset are in --timezone, which
# defaults to the machine's local timezone.
python3 prompt_reports.py --start 2001-07-01 --end 2001-07-08T12:00

# The last 14 days, as HTML pages.
python3 prompt_reports.py --days 14 --format html

# Only the charts and the prompts.
python3 prompt_reports.py --days 14 --raw
```

It needs Python 3.14 or newer, for the standard library's Zstandard module, and
nothing else. The command reads local files and makes no network requests.
Reports go into `reports/START_to_END/` beside the script, which Git ignores;
`--output` chooses another directory, and `--home` reads another home
directory's transcripts. A report contains the full text of every prompt. Times
follow the timezone's daylight saving rules.

A transcript line that is not a JSON object, a timestamp that is not ISO 8601,
a message with no timestamp, or a record without a field the script reads stops
the command with an error rather than being skipped, so a change in either
program's format cannot silently change the counts. The one exception is an
unterminated last line, which a running session is still writing. A Claude
message in the window from a Claude Code version older than 2.1.197, the oldest
seen to record which messages a person typed, also stops the command.

A two-week window of heavy use takes about a minute of processor time, most of
it spent parsing Claude transcripts, and longer when the disk is busy.

A session's activity is the span of the records in the window that it wrote
itself, together with the prompts in the window credited to it. A session is
listed when it has any activity in the window. A record that a Claude fork
copied from its parent keeps its original timestamp. In a desktop session,
which has a known creation time, a copied record predates it and is not
counted, so a session forked after the window is not listed. A command-line
session has no known creation time, and its copied records usually name the
parent session, which they are credited to.

## What counts as a typed prompt

Sessions receive many messages that a person did not type: reports from
subagents and background tasks, messages from other sessions, prompts sent by
scripts, buttons that a mod adds to the app, briefs that one session writes for
another, scheduled prompts, hook output, and reminders the app attaches. The
transcripts record enough to tell these apart.

### Claude

The transcripts are in `~/.claude/projects/*/*.jsonl`, one per command-line
session. The desktop app's records in `~/Library/Application
Support/Claude/claude-code-sessions/ACCOUNT/ORGANIZATION/` give each desktop
session's title, its creation time, and every command-line session that belongs
to it, so a desktop session that was resumed in a new transcript is reported
once. Organization folders whose names contain a dot hold older copies that the
app set aside, and are not read. Transcripts of subagents, in `subagents/`
folders, are not read. A session with no title in the app takes the last title
the person gave it in its transcript, or else the last title the app guessed.

A prompt is counted when its record has `origin.kind` set to `human`. Other
records have another kind (`peer` for messages from other sessions and from
`send-pr`, `task-notification` for background tasks, `plugin` for messages a
mod sends, such as the worktree buttons' "squash") or no origin (hook feedback,
interruption notices, scheduled prompts, and the brief that usually opens a
session another session started). A slash command is also recorded with no
origin, so whether a person typed it cannot be told, and it is not counted; the
app sends some itself, such as `/auto-mode-setup` with a request identifier
from its setup wizard. Answers to an agent's questions are recorded as tool
results and are not counted either. A prompt typed while the session is working
appears only as a `queued_command` attachment, with the same origin field, and
is counted the same way.

The app sometimes attaches `<system-reminder>` blocks to a typed prompt. They
are removed from the text, and a prompt that held nothing else is not counted.
A shell command typed after `!` is shown as `! COMMAND`, without its output.
Each pasted image is shown as `[image]`, and any other block that is not text
as its type in brackets.

A desktop session that the person started from another session's proposal can
instead open with the proposing session's brief, recorded as if the person had
typed it. The script collects every brief passed to the desktop app's session
tools (`start_session`, `spawn_task`, `hand_off_to_session`, and the others
whose names start with `mcp__ccd_session`) in transcripts, including those of
subagents, written up to a week before the window, which is how long a proposal
lasts. A desktop session's first prompt after its creation is not counted when
its letters and digits are exactly those of a brief, with or without the
brief's background section. A session with a transcript older than the window
opened before the window, and is not checked. Other prompts are never compared
with briefs, so typing the same words as a short brief elsewhere is still
counted. A brief that the person edited before starting the session no longer
matches, and is counted.

A forked session's transcript repeats the records before the fork, with their
timestamps, their content, and usually the identifier of the session that wrote
them. A prompt is credited to the session its record names, is identified by
its timestamp and content, and is counted once, for the earliest-created
session that holds it.

### Codex

The transcripts are in `~/.codex/sessions/` and `~/.codex/archived_sessions/`,
and Codex compresses older ones with Zstandard (`.jsonl.zst`). Each starts with
a `session_meta` record, and a thread resumed later continues in a new file
with the same thread identifier. A thread that another thread spawned to help
it has a `thread_source` of `subagent` or `guardian_review`, and is not
reported; a thread the person or an agent started for the person has `user` or
`agent_created_thread`. Any other value stops the command with an error. Titles
come from `~/.codex/session_index.jsonl`.

Every message a thread receives is recorded as an `item_completed` event with a
`UserMessage` item, whoever sent it. The app's prompt box gives each message a
random `client_id`, a version 4 UUID. Messages sent by `codex queue` (which
`send-pr` uses) and by other threads carry a time-ordered version 7 UUID, and
the first message of a thread that a program created has none. Only messages
with a version 4 `client_id` are counted. A forked thread can open with copies
of its parent's messages, timestamped when it was forked and keeping their
`client_id`. The fork's `session_meta` names the parent in `forked_from_id`,
and the parent's transcripts, whose file names hold its identifier, are read
whatever their age to tell the copies apart.

An answer to a question that Codex asks through the app is recorded as a typed
message wrapped in `<send_user_message_question_reply>`. It is a choice made in
a form rather than a prompt, as with Claude, and is not counted.

## Tests

```sh
python3 -m unittest -v
```

The tests build synthetic transcripts in a temporary home directory.
