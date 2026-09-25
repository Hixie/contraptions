# Session cost reports

Generate Claude and Codex cost reports for the current weekly quota window, the
last completed window, or both, using local reset history. The default is the
last completed window.

Run from the `session-cost-reports` directory:

```sh
python3 session_cost_reports.py
```

Python 3.10 or newer is sufficient. There are no third-party dependencies. The
command reads local files, takes no credentials, and makes no network requests.
It prints the dates, totals, and HTML report paths. Open those HTML files in a
browser; click a session to expand its footer breakdown.

## Output

By default, output goes into `reports/` beside the script. Each service gets a
directory named for its window's closing time, containing:

- `index.html`: a self-contained chart, with one bar per session, segmented by
  the footer emoji of the turns that incurred the cost. Each session shows its
  percentage of the report's priced total. Expanding it shows each footer's
  percentage of that session.
- `sessions.csv`: session totals, percentages, and cost and percentage columns
  for each footer emoji.
- `report.json`: session data, title provenance, totals, and reset evidence.
- `requests.jsonl`: the individual usage records behind the totals, each with
  the path of the transcript it came from.
- `breakdown.csv` (Claude only): the cost attributed to each kind of context
  content and each kind of action, described under
  [Where the cost went](#where-the-cost-went).

`reports/latest.json` points to the reports produced by the latest invocation
and identifies each as `last`, `current`, or `custom`. It also records any
errors. `reports/reset-history.json` retains observed reset deadlines, so log
rotation need not erase previously collected evidence. Keep using the same
output directory to preserve that history. Repeating a report replaces the
output for that window. Current reports use a `-current` suffix and the recorded
reset time in the directory name, so rerunning the command refreshes the same
report as usage accumulates. Explicit intervals use a `-custom` suffix. The
reports contain local session titles and identifiers.

Keep generated reports, request ledgers, and local validation records in the
ignored `reports/` directory. For a custom `--output`, use a directory outside
the checkout or add that directory to Git's ignore rules. Tracked tests use
synthetic session data; local measurements belong with the ignored reports.

## Options

The explicit dates below are synthetic examples. Replace them with the dates to
analyze.

```sh
# Report usage so far in the current quota window.
python3 session_cost_reports.py --window current

# Report the last completed quota window (also the default).
python3 session_cost_reports.py --window last

# Generate separate reports for both windows and both services.
python3 session_cost_reports.py --window both

# Generate just one service's report.
python3 session_cost_reports.py --service codex --window current

# Choose the output location or reproduce an earlier observation date.
python3 session_cost_reports.py --output /tmp/session-reports
python3 session_cost_reports.py --as-of 2001-07-14T12:00:00-07:00

# Analyze a copy of another home directory.
python3 session_cost_reports.py --home /path/to/copied-home

# Supply known bounds if local reset evidence is unavailable.
python3 session_cost_reports.py --service claude \
  --claude-window 2001-07-01T10:00:00-07:00 2001-07-08T10:00:00-07:00
```

Use `--help` for all options. `--codex-home` and `--claude-home` select app data
directories directly. They take precedence over `--home`, which takes precedence
over `CODEX_HOME` and `CLAUDE_CONFIG_DIR`. Claude Desktop metadata and logs are
read from `Library/` beneath the selected home directory.

Use either `--window` or explicit `--claude-window` / `--codex-window` bounds.
Combining the two forms is an error. With `--window both`, each requested window
is handled independently. If one lacks reset evidence, the other report can
still be generated. The command records the missing window in `latest.json` and
exits with a nonzero status.

`--timezone` controls displayed dates and timestamps supplied without an offset.
The default is the machine's local timezone. Claude Desktop's log timestamps are
also local but carry no offset; use `--claude-log-timezone` when those logs were
produced in another timezone. An explicit timestamp offset is preferable near
daylight-saving transitions.

## Window selection

Codex records weekly quota deadlines in its session logs. The report groups
small variations in the same deadline, subtracts the recorded weekly duration to
recover its opening time, and recognizes a later quota reset that supersedes an
earlier deadline. This handles a manually redeemed reset as well as a scheduled
reset. Deadline variations within one minute are grouped because the source
window duration is recorded in whole minutes.

Claude Desktop records a weekly reset deadline when it reports a weekly limit.
The command reads those messages in `Library/Logs/Claude/main*.log`, including
their named timezone, and infers the opening time seven days before the
deadline. Claude CLI transcripts alone do not necessarily contain this reset
evidence. Neither service's boundaries are assumed to be calendar weeks.

`--window last` selects the most recent completed window supported by retained
evidence. `--window current` selects the recorded window containing the current
time, or `--as-of` when supplied. Its costs stop at that observation time. The
future reset deadline is shown separately. `--window both` generates these two
reports separately, with a separate percentage denominator for each report.

The exact selected dates are printed and included in every report. If no window
matches the requested selection, the command explains how to pass explicit
`--claude-window` or `--codex-window` bounds. It does not manufacture reset
dates from the current day. An old retained window can remain the latest
completed one when newer reset evidence is missing. Expired evidence cannot
establish a current window.

The interval includes its start and excludes its end. Requests are assigned
using their recorded response timestamps; a turn spanning a boundary contributes
only the requests inside the interval. Fractional seconds are preserved.
Sessions with user activity but no priced requests can appear with zero cost.

## Accounting and titles

The dollar amounts are **API-equivalent estimates**, not subscription invoices
or quota percentages. `rates.json` records the pricing snapshot and source
links. Update it, or pass `--rates /path/to/rates.json`, when model prices
change. A model with no catalog entry remains explicitly unpriced; its request
count and tokens are retained rather than treated as free.

The `codex-auto-review` alias uses GPT-5.6 Luna rates, based on
[OpenAI's July 30, 2026 announcement](https://x.com/OpenAI/status/2082878180478910571).
The mapping is a pricing assumption; local usage records retain the alias as the
model name.

Percentages describe priced usage within one service and one report interval:

- `percent_of_total` is the session's cost divided by the report's total cost,
  multiplied by 100. Session percentages sum to 100 before display rounding.
- `segment_percent_of_session` divides each footer's cost by its session's cost.
  CSV columns for these values end in `_percent_of_session`.
- The HTML legend and JSON summary show each footer's share of the report total.
  Each legend entry includes its cost, percentage, and a bar scaled from 0% to
  100% of the report's priced total. Subagent shares are also included in the
  HTML, JSON, CSV, and command output.

Unpriced requests are excluded from percentage denominators. A zero denominator
produces `null` in JSON, an empty CSV field, and an unavailable-share label in
HTML. These are percentages of API-equivalent cost, not percentages of account
quota.

The readers deduplicate streamed records and copied fork history. Codex uses
response IDs when present, with support for older cumulative token
notifications. Cached input and reasoning output are subsets of Codex's input
and output totals; they are not added twice. The catalog supports long-context
and service-tier multipliers. Claude accounting includes cache writes by TTL,
cache reads, output, and recorded server-side web searches. When an older Claude
record omits the cache-write TTL breakdown, it uses the standard five-minute
write rate.

The **FOOTER LINE and status emoji convention** comes from the
[common-fabric system prompts](https://github.com/Hixie/settings/tree/main/system-prompts/common-fabric).
Those prompts define the status emoji and the Agent Instructions Version (AIV)
included in the footer. The report groups recorded emoji; it does not infer a
turn's status from the session's current title. Each generated HTML and JSON
report also includes this source link.

Real subagent usage is included under its parent session. A subagent's own turn
footer wins when available; otherwise it inherits its enclosing parent turn's
footer. Each invocation of a resumed agent uses its corresponding parent turn.
Missing footers appear as “No footer.” User-created Codex forks remain separate
sessions after copied usage is deduplicated.

Session naming follows this priority:

1. The first recorded title containing ⚙️.
2. Otherwise, the first title set by the user.
3. Otherwise, the last automatically generated title.

The local stores do not always retain a title's author. The readers combine
title history, recorded title-tool calls, and Claude Desktop title-source
events. When source information is missing, the report labels an inferred choice
rather than claiming verified user authorship. Original assigned or fork titles
are retained when the requested history cannot be recovered.

## Where the cost went

Claude reports include a section that attributes cost to what each request
carried and what it did. It is useful for finding recurring overhead, such as
instruction files that every session loads or reads, tool calls that
instructions require on every turn, and repeated failures.

Every request sends the whole conversation so far, so content added early is
paid for again by every later request until the conversation is compacted.
The **context** view divides each request's input cost among the content in
that request's context:

- Recorded cache reads cover the beginning of the context, cache writes cover
  what follows, and uncached input covers the end. Each block is charged the
  rate of the part that covered it.
- The increase in recorded input between consecutive requests is the number of
  tokens added between them. That increase is divided among the added blocks in
  proportion to their length. The first request's input is divided between the
  recorded system prompt, the blocks before it, and a remainder for the tool
  definitions. Current Claude Code versions record the tool definitions only
  after the first request; when they do, the system prompt and the remainder
  are divided again in proportion to the recorded sizes.
- Thinking blocks show little or no text in transcripts. Their size is the
  request's recorded thinking tokens or, in transcripts without that count, its
  output tokens minus the other output. Local transcripts show that the next
  request's input grows by the full output, thinking included.
- Lines that Claude Code writes to a transcript again are counted once.
- Transcripts are read a second time for this section. Claude Code moves a
  transcript to another project directory when its session's working directory
  changes; a moved transcript is found in its new directory. The cost of a
  transcript that disappears during the run is shown as "Transcript removed
  during the run".
- A recorded compaction starts a new context. If the context shrinks without
  one, the blocks are scaled down proportionally.
- Attachments count only when the transcript records that they were rendered
  to the model. Transcripts from Claude Code versions that do not record this
  count every attachment except the prompt snapshot and deferred tool records.

Categories include instruction files (automatically loaded `CLAUDE.md` and
similar files, skill bodies, and Markdown files in the Claude configuration
directory or named `CLAUDE.md`, `AGENTS.md`, or `SKILL.md` that the Read tool
or a command such as `cat`, `sed`, or `head` printed), tool results and tool
calls by tool and file or command name, failed tool calls by tool and exit code
or first line, thinking, attachments by type, and the system prompt and tool
definitions. The context view sums to the report's input cost.

The **actions** view assigns each request's whole cost, output included, to
the tool calls it made, divided equally among them. A request that follows a
failed tool call is assigned to that failure instead, so the cost of recovering
from each kind of failure is visible. Requests that made no tool call are
replies. The actions view sums to the report total. For each action, it also
counts the requests that took it and the requests that took no other action,
with the full cost of the latter. Those requests were made only for that
action; the others also made other tool calls.

Many repositories are checked out several times in parallel, so the same file
appears under several paths. `--normalize-parallel-worktrees` replaces each path
component of one or two digits or capital letters, such as `/B0/`, and each such
dotted suffix, such as the `.Z` of `labs.Z/`, with `/.../` in the breakdown's
labels. `~/dev/labs.Z/AGENTS.md` and `~/dev/labs.B0/AGENTS.md` then share the
label `~/dev/labs/.../AGENTS.md`.

Both views show each item's share of the report total and the number of
sessions that incurred it. The HTML shows the largest items in each category;
`breakdown.csv` lists all of them. Codex reports do not include this section.

## Verification

```sh
python3 -m unittest -v
```

The tests cover current and completed windows, reset boundaries and deadline
jitter, fractional timestamps, percentage denominators and empty reports,
pricing, streaming/fork deduplication, older Codex records, title provenance,
resumed subagents, footer parsing, malformed files, report escaping, and the
attribution of cost to context content and actions.
