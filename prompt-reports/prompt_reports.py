#!/usr/bin/env python3
"""List the prompts a person typed into Claude and Codex sessions.

Reads the local transcripts of Claude Code (including the Claude desktop
app's session records) and of Codex, finds every session with activity in a
time window, and writes reports of how many prompts the person typed into
each session, of every such prompt in chronological order, and of every
distinct prompt, shortest first. Messages that
another program wrote into a session are not counted; see README.md.
"""

import argparse
from collections import Counter
import html
from compression import zstd
from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
import re
import sys
import time
import uuid
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
REMINDER = re.compile(r'<system-reminder>.*?</system-reminder>', re.S)
SHELL = re.compile(r'<bash-input>(.*?)</bash-input>', re.S)
# The desktop app's tools for starting sessions and proposing them.
BRIEF_TOOLS = 'mcp__ccd_session'
PROPOSAL_LIFETIME = 7 * 86400
ANSWER = '<send_user_message_question_reply>'
# Whether a Codex thread with each `thread_source` is a session of its own.
THREAD_SOURCES = {'user': True, 'agent_created_thread': True, 'subagent': False, 'guardian_review': False}
# The oldest Claude Code version known to record each message's origin.
ORIGIN_VERSION = (2, 1, 197)


@dataclass(eq=False)
class Session:
    service: str
    id: str
    title: str = ''
    created: float = 0.0
    first: float = float('inf')
    last: float = float('-inf')
    prompts: list = field(default_factory=list)


@dataclass(eq=False)
class Prompt:
    time: float
    text: str
    key: object
    session: Session


def stamp(value):
    """Seconds since the epoch for an ISO 8601 timestamp, or None if there
    is no timestamp."""
    return None if value is None else datetime.fromisoformat(value).timestamp()


def records(path, containing=''):
    """The JSON objects on the lines of `path`, which may be compressed with
    Zstandard, that contain `containing`. The last line is skipped while it is
    still being written."""
    opener = zstd.open if path.suffix == '.zst' else open
    with opener(path, 'rt', encoding='utf-8') as lines:
        for line in lines:
            if containing not in line or not line.endswith('\n'):
                continue
            record = json.loads(line)
            if type(record) is not dict:
                raise ValueError(f'{path}: a line holds a JSON {type(record).__name__}, not an object')
            yield record


def recent(paths, start):
    """The paths last written at or after `start`, since an older file can
    hold no record from the window."""
    return [p for p in paths if p.stat().st_mtime >= start]


def message_text(content):
    """The text of a message, with a line such as [image] for each block that
    is not text."""
    if isinstance(content, str):
        return content
    return '\n'.join(block['text'] if block['type'] == 'text' else f"[{block['type']}]"
                     for block in content)


def typed_text(content):
    """What the person typed into a Claude prompt, without the reminders the
    app attaches to it. A shell command typed after `!` is shown that way."""
    text = REMINDER.sub('', message_text(content)).strip()
    shell = SHELL.match(text)
    return f'! {shell.group(1).strip()}' if shell else text


def version(text):
    return tuple(int(part) for part in text.split('.'))


def is_human(origin):
    return (origin or {}).get('kind') == 'human'


def unique(prompts):
    """Each prompt once. A forked session repeats the prompts that came before
    the fork, so a prompt belongs to the earliest-created session that holds
    it."""
    owners = {}
    for prompt in sorted(prompts, key=lambda p: (p.session.created, p.session.id)):
        owners.setdefault(prompt.key, prompt)
    return list(owners.values())


def extend(session, ts, window):
    """Count `ts` as a time when `session` was active, if it is in the
    window."""
    if window[0] <= ts < window[1]:
        session.first, session.last = min(session.first, ts), max(session.last, ts)


def brief_key(text):
    """The letters and digits of `text`, for matching a brief with the prompt
    that delivered it, from which the app may have dropped punctuation."""
    return re.sub(r'[^a-z0-9]', '', text.lower())


def claude_briefs(paths):
    """Keys for every brief that a Claude session wrote for a session it
    started, or offered for the person to start."""
    keys = set()
    for path in paths:
        for record in records(path, BRIEF_TOOLS):
            if record.get('type') != 'assistant':
                continue
            for block in record['message']['content']:
                brief = block.get('input') or {}
                prompt = brief.get('prompt') or brief.get('remaining_plan')
                if block.get('name', '').startswith(BRIEF_TOOLS) and prompt:
                    # The background, when there is one, comes first under
                    # a heading.
                    keys |= {brief_key(prompt),
                             brief_key(f"Background: {brief.get('background', '')} {prompt}")}
    # A prompt with no letters or digits matches no brief.
    return keys - {''}


def read_claude(home, window):
    start = window[0]
    desktop = home / 'Library/Application Support/Claude/claude-code-sessions'
    sessions, aliases = {}, {}
    # Folders whose names have a dot hold copies the app set aside.
    for path in desktop.glob('*/*/local_*.json'):
        if '.' in path.parent.name:
            continue
        meta = json.loads(path.read_text())
        session = Session('claude', meta['sessionId'], meta.get('title') or '', meta['createdAt'] / 1000)
        sessions[session.id] = session
        for cli in [meta.get('cliSessionId'), *(meta.get('priorCliSessionIds') or [])]:
            if cli:
                aliases[cli] = session.id

    # The app's title wins, then the last title the person chose, then the
    # last title the app guessed.
    titled = {sid for sid, session in sessions.items() if session.title}
    chosen = set()
    # Only the desktop app starts sessions from briefs.
    started = set(sessions)
    projects = home / '.claude/projects'
    transcripts = list(projects.glob('*/*.jsonl'))
    fresh = recent(transcripts, start)

    def session_for(cli):
        sid = aliases.get(cli, cli)
        return sessions.setdefault(sid, Session('claude', sid))

    # A proposed session can be started until its proposal expires. Subagents
    # can propose sessions too.
    briefs = claude_briefs(recent(projects.rglob('*.jsonl'), start - PROPOSAL_LIFETIME))
    prompts = []
    for path in fresh:
        for record in records(path):
            # A forked transcript copies records with their timestamps,
            # content, and the identifier of the session that wrote them.
            # Records of file history have no identifier, and belong to the
            # transcript's own session.
            session = session_for(record.get('sessionId', path.stem))
            kind = record.get('type')
            if session.id not in titled:
                if kind == 'custom-title':
                    session.title = record['customTitle']
                    chosen.add(session.id)
                elif kind == 'ai-title' and session.id not in chosen:
                    session.title = record['aiTitle']
            ts = stamp(record.get('timestamp'))
            if ts is None:
                if kind in ('user', 'attachment'):
                    raise ValueError(f'{path}: a {kind} record has no timestamp')
                continue
            # Records a fork copies from its parent predate the fork.
            if ts >= session.created:
                extend(session, ts, window)
            if kind == 'user' and ts >= start and version(record['version']) < ORIGIN_VERSION:
                raise ValueError(f'{path}: Claude Code {record["version"]} did not record '
                                 'which messages a person typed')
            content = None
            if kind == 'user' and is_human(record.get('origin')):
                content = record['message']['content']
            elif kind == 'attachment':
                # A prompt typed while the session is working is recorded only
                # as a queued command.
                queued = record['attachment']
                if queued['type'] == 'queued_command' and is_human(queued.get('origin')):
                    content = queued['prompt']
            text = typed_text(content) if content is not None else ''
            if text:
                prompts.append(Prompt(ts, text, (record['timestamp'], json.dumps(content)), session))
    prompts = unique(prompts)
    # A session with a transcript from before the window opened before it.
    started -= {aliases.get(path.stem, path.stem) for path in set(transcripts).difference(fresh)}
    # A session that another session started, or that the person started
    # from another session's proposal, can open with that session's brief.
    opening = {}
    for prompt in sorted(prompts, key=lambda p: p.time):
        if prompt.session.id in started and prompt.time >= prompt.session.created:
            opening.setdefault(prompt.session.id, prompt)
    delivered = {p for p in opening.values() if brief_key(p.text) in briefs}
    return list(sessions.values()), [p for p in prompts if p not in delivered]


def codex_prompt(payload):
    """The client identifier and text of a message typed into the Codex
    app's prompt box, or None for any other record."""
    item = payload.get('item') or {}
    if payload.get('type') != 'item_completed' or item.get('type') != 'UserMessage':
        return None
    # The prompt box gives each message a random (version 4) UUID. Messages
    # from `codex queue`, from other threads, and from programs that start
    # threads have a time-ordered (version 7) UUID or none.
    client = item.get('client_id')
    if not client or uuid.UUID(client).version != 4:
        return None
    text = message_text(item['content']).strip()
    # An answer to a question the agent asked is a choice made in a form,
    # not a prompt. Claude records it as a tool result.
    return (client, text) if text and not text.startswith(ANSWER) else None


def read_codex(home, window):
    start = window[0]
    root = home / '.codex'
    titles = {}
    index = root / 'session_index.jsonl'
    if index.exists():
        for record in records(index):
            if record.get('id') and record.get('thread_name'):
                titles[record['id']] = record['thread_name']
    sessions, prompts, parents = {}, [], {}
    paths = [path for folder in ('sessions', 'archived_sessions')
             for pattern in ('*.jsonl', '*.jsonl.zst') for path in (root / folder).rglob(pattern)]
    for path in recent(paths, start):
        session = None
        for record in records(path):
            payload = record.get('payload') or {}
            if session is None:
                if record['type'] != 'session_meta':
                    raise ValueError(f'{path}: the first record is not session_meta')
                # A thread that another thread spawns to help it, such as a
                # subagent or a reviewer, is not a session of its own. A
                # thread resumed later continues in a file of its own.
                if not THREAD_SOURCES[payload['thread_source']]:
                    break
                sid = payload['id']
                session = sessions.setdefault(sid, Session('codex', sid, titles.get(sid, '')))
                if 'forked_from_id' in payload:
                    parents[sid] = payload['forked_from_id']
            ts = stamp(record['timestamp'])
            extend(session, ts, window)
            if typed := codex_prompt(payload):
                prompts.append(Prompt(ts, typed[1], typed[0], session))
    # A fork opens with copies of its parent's messages, made when it is
    # forked, so the parent's transcripts, whose names hold its identifier,
    # tell them apart.
    inherited = {parent: {typed[0] for path in paths if parent in path.name
                          for record in records(path, 'UserMessage')
                          if (typed := codex_prompt(record.get('payload') or {}))}
                 for parent in set(parents.values())}
    return list(sessions.values()), unique([p for p in prompts
                                            if p.key not in inherited.get(parents.get(p.session.id), ())])


def analyse(home, start, end):
    """The sessions with activity in [start, end), each with the prompts the
    person typed into it in that interval."""
    sessions, prompts = [], []
    for reader in (read_claude, read_codex):
        found, typed = reader(home, (start, end))
        sessions += found
        prompts += typed
    for prompt in prompts:
        session = prompt.session
        # A prompt is activity of the session it is credited to, even when
        # it predates that session, as one that a fork copied from a parent
        # whose transcript is gone does.
        extend(session, prompt.time, (start, end))
        if start <= prompt.time < end:
            session.prompts.append(prompt)
    listed = [s for s in sessions if s.first <= s.last]
    for session in listed:
        session.prompts.sort(key=lambda p: p.time)
    return listed


def count(n, noun):
    return f'{n} {noun}' if n == 1 else f'{n} {noun}s'


def local(ts, zone):
    return datetime.fromtimestamp(ts, zone).strftime('%Y-%m-%d %H:%M')


def name(session):
    return session.title or (session.prompts[0].text.splitlines()[0][:80] if session.prompts else '(untitled)')


def cell(text):
    return ' '.join(text.split()).replace('|', '\\|')


def fenced(text):
    longest = max((len(run) for run in re.findall(r'`+', text)), default=0)
    fence = '`' * max(3, longest + 1)
    return f'{fence}\n{text}\n{fence}'


def overview(sessions, start, end, zone):
    """The window, the prompts in chronological order, and a sentence about
    both."""
    window = f"{local(start, zone)} to {local(end, zone)} ({zone or 'local time'})"
    prompts = sorted((p for s in sessions for p in s.prompts), key=lambda p: p.time)
    services = {svc: sum(1 for s in sessions if s.service == svc) for svc in ('claude', 'codex')}
    summary = (f"{count(len(prompts), 'prompt')} in {count(len(sessions), 'session')} "
               f'({services["claude"]} Claude, {services["codex"]} Codex). '
               f"{count(sum(1 for s in sessions if not s.prompts), 'session')} had no typed prompts.")
    return window, prompts, summary


def share(n, total):
    """`n` as a percentage of `total`, never rounded to 0% or 100% unless it
    is exactly that."""
    if not total:
        return '–'
    if 0 < n < total / 1000:
        return '<0.1%'
    if total * 0.999 < n < total:
        return '>99.9%'
    return f'{100 * n / total:.1f}%'


def cumulative(values, highs, low, unit, things):
    """Chart rows of how many of `values` are at most each of `highs`."""
    rows = []
    for high in highs:
        n = sum(1 for v in values if v <= high)
        rows.append((f'{low}–{high}' if high > low else str(low), n, share(n, len(values)),
                     f'{count(n, things)} with at most {count(high, unit)}'))
    return rows


def histograms(sessions):
    """Two charts of how many sessions had how many typed prompts: one of the
    sessions in each bin, and one of the sessions with at most each number.
    A chart is a caption, its column headings, and (label, number, share,
    description) rows. The first chart's bins double in width after 2
    prompts, since a few sessions have many more prompts than the rest."""
    counts = [len(s.prompts) for s in sessions]
    most = max(counts, default=-1)
    each, low = [], 0
    while low <= most:
        high = low if low < 3 else 2 * low - 2
        n = sum(1 for c in counts if low <= c <= high)
        label = str(low) if low == high else f'{low}–{high}'
        each.append((label, n, share(n, len(counts)),
                     f"{count(n, 'session')} with {label} {'prompt' if label == '1' else 'prompts'}"))
        low = high + 1
    return [('Sessions by number of typed prompts', ('Prompts', 'Sessions'), each),
            ('Sessions with at most each number of typed prompts', ('Prompts', 'Sessions'),
             cumulative(counts, range(most + 1), 0, 'prompt', 'session'))]


def length_histogram(prompts):
    """A chart of how many typed prompts were at most each length, in
    lengths that double, since a few prompts are far longer than the rest."""
    lengths = [len(p.text) for p in prompts]
    highs = [2 ** k for k in range((max(lengths, default=0) - 1).bit_length() + 1)] if lengths else []
    return ('Prompts typed with at most each number of characters', ('Characters', 'Prompts'),
            cumulative(lengths, highs, 1, 'character', 'prompt'))


def markdown_chart(caption, headings, rows):
    widest = max((n for _, n, _, _ in rows), default=0)
    return [f'## {caption}', '', f'| {headings[0]} | {headings[1]} | Share | |', '|--:|--:|--:|--|',
            *(f"| {label} | {n} | {part} | {'█' * -(-40 * n // widest)} |" for label, n, part, _ in rows), '']


def html_chart(caption, headings, rows):
    widest = max((n for _, n, _, _ in rows), default=0)
    return (f'<table class="histogram">\n<caption>{caption}</caption>\n'
            f'<thead><tr><th>{headings[0]}<th>{headings[1]}<th>Share<td></thead>\n<tbody>\n' + ''.join(
                f'<tr title="{title}"><td class="number">{label}<td class="number">{n}'
                f'<td class="number">{part}<td class="bar" aria-hidden="true">'
                f'<span style="width: {100 * n / widest:.1f}%"></span>\n'
                for label, n, part, title in rows) + '</tbody>\n</table>\n')


def by_count(sessions):
    return sorted(sessions, key=lambda s: (-len(s.prompts), s.first))


def distinct(prompts):
    """Each distinct prompt text and how many times it was typed, shortest
    first, and the most often typed first among prompts of one length."""
    return sorted(Counter(p.text for p in prompts).items(), key=lambda item: (len(item[0]), -item[1], item[0]))


def about(prompt, zone):
    return f'{local(prompt.time, zone)}, {prompt.session.service.capitalize()}: {name(prompt.session)}'


def chronological(start, end):
    """A heading for a raw list of prompts, which gives the window's length
    but not its dates."""
    seconds = end - start
    unit, size = next((unit, size) for unit, size in (('day', 86400), ('hour', 3600), ('minute', 60))
                      if seconds >= size or unit == 'minute')
    return f'All prompts typed over a {round(seconds / size, 1):g}-{unit} period, in chronological order'


def times(n):
    return f'×{n}' if n > 1 else ''


def render_markdown(sessions, start, end, zone, raw):
    """The reports as Markdown, by file name stem. A raw report holds only
    the charts and the prompts."""
    window, prompts, summary = overview(sessions, start, end, zone)
    texts = distinct(prompts)
    charts = [line for chart in histograms(sessions) for line in markdown_chart(*chart)]
    lengths_chart = markdown_chart(*length_histogram(prompts))
    repeated = [line for text, n in texts for line in [*([times(n), ''] if n > 1 else []), fenced(text), '']]
    if raw:
        return {'counts': '\n'.join(charts),
                'prompts': f'# {chronological(start, end)}\n\n' + '\n\n'.join(fenced(p.text) for p in prompts) + '\n',
                'by-length': '\n'.join(lengths_chart + repeated)}
    counts = [f'# Prompts typed into each session, {window}', '', summary, '', *charts]
    counts += [
        '## Sessions', '',
        '| Prompts | Share | Service | Session | ID | First activity | Last activity |',
        '|--:|--:|--|--|--|--|--|',
        *(f'| {len(s.prompts)} | {share(len(s.prompts), len(prompts))} | {s.service.capitalize()} | '
          f'{cell(name(s))} | `{s.id}` | {local(s.first, zone)} | {local(s.last, zone)} |' for s in by_count(sessions)),
    ]
    listing = [f'# Prompts typed, in chronological order, {window}', '', f"{count(len(prompts), 'prompt')}.", '']
    for p in prompts:
        # A link title can escape any ASCII punctuation with a backslash.
        title = re.sub(r'([!-/:-@[-`{-~])', r'\\\1', ' '.join(about(p, zone).split()))
        listing += [f'[ℹ️](counts.md "{title}")', '', fenced(p.text), '']
    lengths = [f'# Prompts typed, shortest first, {window}', '',
               f"{count(len(texts), 'distinct prompt')}, of {count(len(prompts), 'prompt')}.", '',
               *lengths_chart, '## Distinct prompts', '', *repeated]
    return {'counts': '\n'.join(counts) + '\n', 'prompts': '\n'.join(listing), 'by-length': '\n'.join(lengths)}


def page(title, body, heading=True):
    return f"""<!doctype html>
<html lang="en">
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root {{ color-scheme: light dark; --series-1: #2a78d6; }}
@media (prefers-color-scheme: dark) {{ :root {{ --series-1: #3987e5; }} }}
body {{ font: 15px/1.5 system-ui, sans-serif; max-width: 72rem; margin: 2rem auto; padding: 0 1rem; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ text-align: left; vertical-align: top; padding: 0.25rem 0.5rem; border-bottom: 1px solid #8884; }}
td.number {{ text-align: right; }}
tr:target {{ background: #fd04; }}
table.histogram {{ width: auto; margin: 1rem 0 2rem; }}
table.histogram td.bar {{ width: 30rem; }}
table.histogram tbody tr:hover {{ background: #8882; }}
td.bar span {{ display: block; height: 16px; border-radius: 0 4px 4px 0; background: var(--series-1); }}
pre {{ white-space: pre-wrap; overflow-wrap: anywhere; margin: 0.5rem 0; padding: 0.5rem 0.75rem;
       border-radius: 4px; background: #8882; }}
.entry {{ display: flex; gap: 0.5rem; align-items: flex-start; }}
.entry pre {{ flex: 1; }}
.entry .info {{ flex: none; position: relative; width: 2.5rem; margin-top: 0.75rem; text-decoration: none; }}
.tip {{ position: absolute; right: 100%; top: 0; white-space: nowrap; padding: 0.25rem 0.5rem; border-radius: 4px;
        font-size: 0.85rem; background: Canvas; color: CanvasText; border: 1px solid #8884;
        opacity: 0; pointer-events: none; }}
.info:hover .tip, .info:focus-visible .tip {{ opacity: 1; }}
</style>
{f"<h1>{html.escape(title)}</h1>" if heading else ""}
{body}
</html>
"""


def render_html(sessions, start, end, zone, raw):
    """The reports as HTML pages, by file name stem. A raw report holds only
    the charts and the prompts."""
    window, prompts, summary = overview(sessions, start, end, zone)
    texts = distinct(prompts)
    e = html.escape
    # A raw page's title, which a browser shows, gives no dates; the raw list
    # of prompts has a title of its own.
    when = '' if raw else f', {window}'
    counts_title = f'Prompts typed into each session{when}'
    listing_title = f'Prompts typed, in chronological order, {window}'
    lengths_title = f'Prompts typed, shortest first{when}'
    charts = ''.join(html_chart(*chart) for chart in histograms(sessions))
    lengths_chart = html_chart(*length_histogram(prompts))
    distinct_entries = ''.join(
        f'<div class="entry"><pre>{e(text)}</pre><span class="info">{times(n)}</span></div>\n'
        for text, n in texts)
    if raw:
        return {'counts': page(counts_title, charts, False),
                'prompts': page(chronological(start, end), ''.join(f'<pre>{e(p.text)}</pre>\n' for p in prompts)),
                'by-length': page(lengths_title, lengths_chart + distinct_entries, False)}
    rows = ''.join(
        f'<tr id="{e(s.id)}"><td class="number">{len(s.prompts)}<td class="number">{share(len(s.prompts), len(prompts))}'
        f'<td>{s.service.capitalize()}<td>{e(name(s))}<td><code>{e(s.id)}</code>'
        f'<td>{local(s.first, zone)}<td>{local(s.last, zone)}\n'
        for s in by_count(sessions))
    counts = page(counts_title, f"""<p>{e(summary)} <a href="prompts.html">Every prompt</a>.
 <a href="by-length.html">Distinct prompts, shortest first</a>.
{charts}<table>
<thead><tr><th>Prompts<th>Share<th>Service<th>Session<th>ID<th>First activity<th>Last activity</thead>
<tbody>
{rows}</tbody>
</table>""")
    entries = ''.join(
        f'<div class="entry"><pre>{e(p.text)}</pre><a class="info" href="counts.html#{e(p.session.id)}">ℹ️'
        f'<span class="tip">{e(about(p, zone))}</span></a></div>\n' for p in prompts)
    listing = page(listing_title, f"<p>{count(len(prompts), 'prompt')}. Point at or focus ℹ️ for a prompt's "
                                  f'time and session. <a href="counts.html">Counts by session</a>.\n{entries}')
    lengths = page(lengths_title, f"<p>{count(len(texts), 'distinct prompt')}, of "
                                  f"{count(len(prompts), 'prompt')}. <a href=\"counts.html\">Counts by session</a>."
                                  f'\n{lengths_chart}{distinct_entries}')
    return {'counts': counts, 'prompts': listing, 'by-length': lengths}


# The file extension and renderer for each output format.
FORMATS = {'markdown': ('md', render_markdown), 'html': ('html', render_html)}


def parse_time(value, zone):
    """Seconds since the epoch for an ISO 8601 date or time, which is in
    `zone` (local time when None) unless it has an offset."""
    moment = datetime.fromisoformat(value)
    return (moment if moment.tzinfo or zone is None else moment.replace(tzinfo=zone)).timestamp()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    window = parser.add_mutually_exclusive_group(required=True)
    window.add_argument('--days', type=float, help='the window is the DAYS days before --end')
    window.add_argument('--start', help='the start of the window, as an ISO 8601 date or time')
    parser.add_argument('--end', help='the end of the window (default: now)')
    parser.add_argument('--timezone', help='timezone for times without an offset and for '
                        'the reports (default: the local timezone)')
    parser.add_argument('--home', type=Path, default=Path.home(),
                        help='the home directory whose transcripts to read')
    parser.add_argument('--output', type=Path, default=HERE / 'reports',
                        help='the directory to write reports into')
    parser.add_argument('--format', choices=FORMATS, default='markdown',
                        help='the format of the reports (default: markdown)')
    parser.add_argument('--raw', action='store_true',
                        help='write only the charts, the prompts and how often each was repeated, '
                        'with no dates, times, sessions or summaries')
    args = parser.parse_args(argv)
    zone = ZoneInfo(args.timezone) if args.timezone else None
    end = parse_time(args.end, zone) if args.end else time.time()
    start = end - args.days * 86400 if args.days is not None else parse_time(args.start, zone)
    if start >= end:
        parser.error('the window must start before it ends')
    sessions = analyse(args.home, start, end)
    extension, render = FORMATS[args.format]
    out = args.output / '_to_'.join(datetime.fromtimestamp(ts, zone).strftime('%Y-%m-%dT%H%M')
                                    for ts in (start, end))
    out.mkdir(parents=True, exist_ok=True)
    total = sum(len(s.prompts) for s in sessions)
    print(f"{count(total, 'prompt')} in {count(len(sessions), 'session')}")
    for stem, text in render(sessions, start, end, zone, args.raw).items():
        (out / f'{stem}.{extension}').write_text(text)
        print(out / f'{stem}.{extension}')


if __name__ == '__main__':
    sys.exit(main())
