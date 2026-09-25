"""Attribute Claude request costs to the content in each request's context and
to the actions each request took."""
from collections import Counter, defaultdict
from itertools import chain
from pathlib import Path
import os
import re
from claude_reader import request_key
from reporting import claude_unit_prices, jsonlines, price

INSTRUCTION_NAMES = {'CLAUDE.md', 'CLAUDE.local.md', 'AGENTS.md', 'SKILL.md'}
# Attachments recorded for the application's own use rather than sent to the model.
UNSENT = {'prompt_snapshot', 'deferred_tools_record'}
CHARACTERS_PER_TOKEN = 4
IMAGE_TOKENS = 1600
SYSTEM = 'System prompt and tools'
# Cost by context content; cost by action; for each action, the number of
# requests that took it, the number that took no other action, and their cost.
PARTS = ('context', 'actions', 'requests', 'sole_requests', 'sole_cost')
SEGMENT = re.compile(r'&&|\|\||[;|\n]')
# Words that introduce a shell command without naming it.
PREFIXES = {'do', 'then', 'else', 'time', 'sudo', 'exec', '!', '{', '('}
# Shell syntax whose segment names no command worth reporting.
SYNTAX = {'for', 'while', 'until', 'if', 'elif', 'case', 'export', 'set', 'unset',
          'local', 'done', 'fi', 'esac', '}', ')'}
# Commands that print the files they are given.
READERS = {'cat', 'head', 'tail', 'sed', 'awk', 'less', 'more', 'nl', 'bat'}
# A path component, or a dotted suffix of one, naming one of several parallel
# checkouts of a repository, such as `/B0/` or the `.Z` of `labs.Z/`.
WORKTREE = re.compile(r'[./][0-9A-Z]{1,2}(?=/)')


def characters(value):
    if isinstance(value, str):
        return len(value)
    if isinstance(value, dict):
        if value.get('type') == 'image':
            return IMAGE_TOKENS * CHARACTERS_PER_TOKEN
        return sum(characters(v) for k, v in value.items() if k != 'type')
    if isinstance(value, list):
        return sum(characters(v) for v in value)
    return 0


def estimate(value):
    return characters(value) / CHARACTERS_PER_TOKEN


def text_of(value):
    return value if isinstance(value, str) else '\n'.join(
        b.get('text', '') for b in value or [] if isinstance(b, dict))


def first_line(value):
    for line in text_of(value).splitlines():
        line = re.sub(r'</?[\w-]+>', '', line).strip()
        if line and not re.fullmatch(r'Exit code \d+', line):
            return re.sub(r'\d+', 'N', line)[:100]
    return 'no message'


class Labels:
    def __init__(self, home, claude_home, merge_worktrees=False):
        self.home = str(home)
        self.claude_home = str(claude_home)
        self.merge_worktrees = merge_worktrees

    def key(self, key):
        """The (category, label) under which a cost is reported."""
        return (key[0], WORKTREE.sub('/...', key[1])) if self.merge_worktrees else key

    def path(self, value):
        return '~' + value[len(self.home):] if value.startswith(self.home + '/') else value

    def instruction(self, value):
        return value.endswith('.md') and (value.startswith(self.claude_home + '/')
                                          or os.path.basename(value) in INSTRUCTION_NAMES)

    def resolve(self, value, cwd):
        value = value.strip('\'"')
        if value == '~' or value.startswith('~/'):
            value = self.home + value[1:]
        return os.path.normpath(os.path.join(cwd, value))

    def commands(self, command, cwd):
        """Each simple command in a shell command line, with its directory."""
        for segment in SEGMENT.split(command):
            words = segment.split()
            while words and (words[0] in PREFIXES or re.fullmatch(r'[A-Za-z_]\w*=\S*', words[0])):
                words.pop(0)
            if not words or words[0] in SYNTAX or words[0].startswith('#'):
                continue
            if words[0] == 'cd':
                cwd = self.resolve(words[1], cwd) if len(words) > 1 else self.home
                continue
            words[0] = words[0].rsplit('/', 1)[-1]
            yield words, cwd

    def shell(self, command, cwd):
        """A shell command's label and the files its reading commands print."""
        label, paths = 'cd', []
        for words, directory in self.commands(command, cwd):
            if label == 'cd':
                label = words[0]
                if len(words) > 1 and re.fullmatch(r'[a-z][\w-]*', words[1]):
                    label += ' ' + words[1]
            if words[0] in READERS:
                paths += [self.resolve(w, directory) for w in words[1:]
                          if not w.startswith('-') and ('/' in w or re.fullmatch(r'[\'"]?[\w.-]+\.md[\'"]?', w))]
        return label, paths

    def tool(self, name, arguments, cwd):
        """A tool call's label and the instruction files it reads."""
        short = name.rsplit('__', 1)[-1] if name.startswith('mcp__') else name
        arguments = arguments if isinstance(arguments, dict) else {}
        target = arguments.get('file_path') or arguments.get('notebook_path')
        if isinstance(target, str):
            target = self.resolve(target, cwd)
            read = name == 'Read' and self.instruction(target)
            return short, f'{short} {self.path(target)}', [target] if read else []
        if name == 'Bash':
            label, paths = self.shell(arguments.get('command') or '', cwd)
            return short, f'Bash: {label}', list(dict.fromkeys(p for p in paths if self.instruction(p)))
        if name in ('Agent', 'Task'):
            return short, f"{short}: {arguments.get('subagent_type') or 'general-purpose'}", []
        if name == 'Skill':
            return short, f"Skill: {arguments.get('skill')}", []
        return short, short, []


class Block:
    __slots__ = ('key', 'estimate', 'tokens', 'request')

    def __init__(self, key, estimate, request=None):
        self.key, self.estimate, self.tokens, self.request = key, estimate, None, request


class Stream:
    """One conversation's context, from its start or its latest compaction.

    Each request's recorded input is the whole context: cache reads cover its
    beginning, cache writes follow, and uncached input covers the end. The
    growth between consecutive requests measures the tokens added between them.
    Character counts divide that growth among the added blocks, so each block
    receives a token count that makes the context match the recorded total.
    Every request then charges each block for its tokens, at the rate of the
    part of the request that covered it.
    """

    def __init__(self):
        self.context, self.calibrated, self.fixed, self.snapshot = [], 0, None, None
        self.failures = []
        self.requests = {}
        self.tokens, self.marks, self.costs = Counter(), {}, Counter()
        self.known = 0
        self.read_rates = 0.0

    def settle(self, key):
        # Read charges accrue lazily: every token in context pays each request's
        # read rate, and read_rates is the running sum of those rates.
        self.costs[key] += self.tokens[key] * (self.read_rates - self.marks.get(key, self.read_rates))
        self.marks[key] = self.read_rates

    def assign(self, block, tokens):
        self.settle(block.key)
        change = tokens - (block.tokens or 0)
        self.tokens[block.key] += change
        self.known += change
        block.tokens = tokens

    def estimate(self, block):
        if block.estimate is not None:
            return block.estimate
        r = self.requests[block.request]
        thinking = r['thinking_tokens'] if r['thinking_tokens'] is not None else max(r['output'] - r['visible'], 0)
        return thinking / len(r['thinking'])

    def scale(self, blocks, estimates, total):
        whole = sum(estimates)
        for block, value in zip(blocks, estimates):
            self.assign(block, value * total / whole if whole else 0)

    def record_snapshot(self, system, tools):
        self.snapshot = (system, tools)
        if self.fixed and system and tools and any(b.estimate is None for b in self.fixed):
            # A snapshot recorded after the first request divides that
            # request's unrecorded remainder between the two parts.
            total = sum(b.tokens for b in self.fixed)
            for b in self.fixed:
                self.assign(b, 0)
            self.fixed = [Block((SYSTEM, 'Tool definitions'), tools), Block((SYSTEM, 'System prompt'), system)]
            self.scale(self.fixed, [tools, system], total)

    def compact(self):
        for block in self.context:
            if block.tokens:
                self.assign(block, 0)
        self.context, self.calibrated = [], 0

    def calibrate(self, total):
        fresh = self.context[self.calibrated:]
        estimates = [self.estimate(b) for b in fresh]
        if self.fixed is None:
            system, tools = self.snapshot or (None, None)
            self.fixed = [Block((SYSTEM, 'Tool definitions'), tools or None),
                          Block((SYSTEM, 'System prompt'), system)]
            if not system:
                self.fixed = [Block((SYSTEM, 'System prompt and tool definitions'), None)]
            known = [b for b in self.fixed if b.estimate is not None]
            # Whatever the recorded parts do not account for is the unrecorded part.
            for b in self.fixed:
                if b.estimate is None:
                    self.assign(b, max(total - sum(k.estimate for k in known) - sum(estimates), 0))
            remainder = total - sum(b.tokens or 0 for b in self.fixed)
            self.scale(known + fresh, [b.estimate for b in known] + estimates, remainder)
        elif total >= self.known:
            growth = total - self.known
            self.scale(fresh, estimates, growth)
            if growth and not sum(estimates):
                block = Block(('Other', 'Unidentified context'), growth)
                self.context.append(block)
                self.assign(block, block.estimate)
        else:
            # The context shrank without a recorded compaction.
            fixed = sum(b.tokens for b in self.fixed)
            if total < fixed:
                self.scale(self.fixed, [b.tokens for b in self.fixed], total)
            messages = self.context[:self.calibrated]
            self.scale(messages + fresh, [b.tokens for b in messages] + estimates, max(total - fixed, 0))
        self.calibrated = len(self.context)

    def charge(self, usage, unit):
        read = usage.get('cache_read_input_tokens', 0) or 0
        written = read + (usage.get('cache_creation_input_tokens', 0) or 0)
        position = written + (usage.get('input_tokens', 0) or 0)
        self.read_rates += unit['read']
        for block in chain(reversed(self.context), reversed(self.fixed)):
            if position <= read:
                break
            start = position - block.tokens
            uncached = max(0, position - max(start, written))
            write = max(0, min(position, written) - max(start, read))
            self.costs[block.key] += uncached * unit['input'] + write * unit['write'] - (uncached + write) * unit['read']
            position = start

    def finish(self):
        for key in list(self.tokens):
            self.settle(key)
        return self.costs


def attribute_file(path, owned, labels, catalog):
    """The PARTS for the requests from `owned` in this file, keyed by (category, label)."""
    agent_file = 'subagents' in Path(path).parts
    streams = defaultdict(Stream)
    tools = {}
    seen = set()
    strict = False
    parts = {name: Counter() for name in PARTS}
    for line_number, x in jsonlines(Path(path)):
        typ = x.get('type')
        if x.get('uuid') in seen:
            # Claude Code sometimes writes earlier lines again.
            continue
        if x.get('uuid'):
            seen.add(x['uuid'])
        stream = streams[x.get('agentId') if not agent_file else None]
        message = x.get('message') or {}
        content = message.get('content')
        cwd = x.get('cwd') or labels.home
        if 'rendered' in x:
            # This version records exactly which attachments reached the model.
            strict = True
        if typ == 'system' and x.get('subtype') == 'compact_boundary':
            stream.compact()
        elif typ == 'attachment':
            a = x.get('attachment') or {}
            kind = a.get('type')
            if kind == 'prompt_snapshot':
                stream.record_snapshot(estimate(a.get('systemPrompt')), estimate(a.get('tools')))
            elif (x.get('rendered') if strict else kind not in UNSENT):
                if kind == 'instructions':
                    for f in a.get('files') or []:
                        stream.context.append(Block(('Instruction files', labels.path(f.get('path') or '')), estimate(f.get('content'))))
                elif kind == 'nested_memory':
                    stream.context.append(Block(('Instruction files', labels.path(a.get('path') or '')), estimate(x.get('rendered') or a)))
                else:
                    stream.context.append(Block(('Attachments', kind), estimate(x.get('rendered') or a)))
        elif typ == 'user':
            blocks = content if isinstance(content, list) else [{'type': 'text', 'text': content or ''}]
            for b in blocks:
                if not isinstance(b, dict):
                    continue
                if b.get('type') == 'tool_result':
                    name, label, paths = tools.get(b.get('tool_use_id'), ('unknown', 'Unknown tool', []))
                    size = estimate(b.get('content'))
                    if b.get('is_error'):
                        code = re.match(r'\s*Exit code (\d+)', text_of(b.get('content')))
                        failure = f'{label}: exit code {code[1]}' if code else f"{label}: {first_line(b.get('content'))}"
                        stream.failures.append(failure)
                        stream.context.append(Block(('Failed tool calls', failure), size))
                    elif paths:
                        stream.context.extend(Block(('Instruction files', labels.path(p)), size / len(paths)) for p in paths)
                    else:
                        stream.context.append(Block(('Tool results', label), size))
                    continue
                text = b.get('text', '') if b.get('type') == 'text' else ''
                if x.get('isCompactSummary'):
                    key = ('Compaction summaries', 'Summary')
                elif x.get('isMeta'):
                    skill = re.match(r'Base directory for this skill: (\S+)', text)
                    key = (('Instruction files', labels.path(skill[1].rstrip('/') + '/SKILL.md')) if skill
                           else ('Injected messages', first_line(text)))
                else:
                    tag = re.match(r'\s*<([\w-]+)>', text)
                    kind = 'Subagent prompts' if agent_file else 'User prompts'
                    key = (kind, f'<{tag[1]}>' if tag else 'Images' if b.get('type') == 'image' else 'Typed text')
                stream.context.append(Block(key, estimate(b)))
        elif typ == 'assistant' and x.get('timestamp'):
            key = request_key(x, path, line_number)
            usage = message.get('usage') or {}
            r = stream.requests.get(key)
            if r is None:
                r = stream.requests[key] = {'output': 0, 'visible': 0, 'thinking': [], 'thinking_tokens': None,
                                            'actions': [], 'stop': None,
                                            'failures': stream.failures}
                stream.failures = []
                recorded = owned[key]['usage'] if key in owned else usage
                total = sum(recorded.get(k, 0) or 0 for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'))
                if total:
                    stream.calibrate(total)
                    unit = claude_unit_prices(owned[key], catalog) if key in owned else None
                    if unit:
                        stream.charge(recorded, unit)
            r['output'] = max(r['output'], usage.get('output_tokens', 0) or 0)
            recorded = (usage.get('output_tokens_details') or {}).get('thinking_tokens')
            if recorded is not None:
                r['thinking_tokens'] = max(r['thinking_tokens'] or 0, recorded)
            r['stop'] = message.get('stop_reason') or r['stop']
            for b in content if isinstance(content, list) else []:
                if not isinstance(b, dict):
                    continue
                kind = b.get('type')
                if kind in ('thinking', 'redacted_thinking'):
                    block = Block(('Thinking', 'Thinking'), None, key)
                    r['thinking'].append(block)
                elif kind == 'text':
                    block = Block(('Assistant text', 'Assistant text'), estimate(b.get('text')))
                    r['visible'] += block.estimate
                elif kind == 'tool_use':
                    name, label, paths = labels.tool(b.get('name', ''), b.get('input'), cwd)
                    tools[b.get('id')] = (name, label, paths)
                    r['actions'].append((name, label))
                    block = Block(('Tool calls', label), estimate(b.get('input')) + estimate(b.get('name')))
                    r['visible'] += block.estimate
                else:
                    block = Block(('Other', kind or 'unknown'), estimate(b))
                stream.context.append(block)
    for stream in streams.values():
        for key, value in stream.finish().items():
            parts['context'][labels.key(key)] += value
        for key, r in stream.requests.items():
            value = price('claude', owned[key], catalog) if key in owned else None
            if value is None:
                continue
            steps = [labels.key(step) for step in [('After a failed tool call', f) for f in r['failures']] or r['actions']
                     or [('Replies', 'Final reply' if r['stop'] in ('end_turn', 'stop_sequence') else 'No tool call')]]
            for step in steps:
                parts['actions'][step] += value / len(steps)
            for step in set(steps):
                parts['requests'][step] += 1
                if len(set(steps)) == 1:
                    parts['sole_requests'][step] += 1
                    parts['sole_cost'][step] += value
    return parts


def locate(source, projects):
    path = Path(source)
    if path.exists():
        return path
    # Claude Code moves a transcript to another project directory when its
    # session's working directory changes.
    return next(projects.glob('*/' + '/'.join(path.relative_to(projects).parts[1:])), None)


def unattributed(owned, catalog):
    """The PARTS for requests whose transcript is no longer available."""
    parts = {name: Counter() for name in PARTS}
    key = ('Other', 'Transcript removed during the run')
    for r in owned.values():
        unit, usage = claude_unit_prices(r, catalog), r['usage']
        value = price('claude', r, catalog)
        if value is None:
            continue
        parts['context'][key] += sum((usage.get(k, 0) or 0) * unit[rate] for k, rate in (
            ('input_tokens', 'input'), ('cache_read_input_tokens', 'read'), ('cache_creation_input_tokens', 'write')))
        parts['actions'][key] += value
    return parts


def attribute(records, catalog, home, claude_home, merge_worktrees=False):
    """Per-session context and action costs for Claude usage records."""
    labels = Labels(Path(home).expanduser().resolve(), Path(claude_home).expanduser().resolve(), merge_worktrees)
    projects = Path(claude_home) / 'projects'
    by_source = defaultdict(dict)
    for r in records:
        by_source[r['source']][r['response_id']] = r
    result = defaultdict(lambda: {name: Counter() for name in PARTS})
    for source, owned in sorted(by_source.items()):
        sid = next(iter(owned.values()))['session_id']
        path = locate(source, projects)
        try:
            parts = attribute_file(path, owned, labels, catalog) if path else unattributed(owned, catalog)
        except FileNotFoundError:
            parts = unattributed(owned, catalog)
        for name, values in parts.items():
            result[sid][name].update(values)
    for parts in result.values():
        for lens in parts.values():
            for key in [k for k, v in lens.items() if abs(v) < 1e-12]:
                del lens[key]
    return dict(result)
