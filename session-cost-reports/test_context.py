"""Attribution of Claude request costs to context content and actions."""
import json
from pathlib import Path
import tempfile
import unittest

import claude_context
import claude_reader
from reporting import aggregate, claude_unit_prices, price, save_report
from zoneinfo import ZoneInfo

HERE = Path(__file__).parent
RATES = json.loads((HERE / 'rates.json').read_text())
SID = 'd' * 36
# claude-opus-5 rates, in USD per token.
WRITE, READ, INPUT = 6.25e-6, .5e-6, 5e-6


def assistant(ts, rid, content, stop, usage):
    return {'type': 'assistant', 'timestamp': ts, 'sessionId': SID, 'uuid': rid + '-line',
            'message': {'id': rid, 'model': 'claude-opus-5', 'content': content,
                        'stop_reason': stop, 'usage': usage}}


def user(content, **extra):
    return {'type': 'user', 'timestamp': 1, 'sessionId': SID, 'message': {'content': content}, **extra}


def attachment(body, rendered=None):
    line = {'type': 'attachment', 'timestamp': 1, 'sessionId': SID, 'attachment': body}
    if rendered is not None:
        line['rendered'] = rendered
    return line


class ContextAttribution(unittest.TestCase):
    def home(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name).resolve()
        (home / '.claude' / 'projects' / 'project').mkdir(parents=True)
        (home / 'desktop').mkdir()
        return home, home / '.claude'

    def run_lines(self, home, lines):
        claude = home / '.claude'
        (claude / 'projects' / 'project' / (SID + '.jsonl')).write_text(''.join(json.dumps(x) + '\n' for x in lines))
        records, names, _ = claude_reader.read(claude / 'projects', home / 'desktop', 1, 100, 1000, [])
        return records, names, claude_context.attribute(records, RATES, home, claude)[SID]

    def run_fixture(self):
        home, claude = self.home()
        rules = str(claude / 'rules.md')
        # Character counts are four times the intended token counts, and each
        # request's recorded input equals the sum of those counts, so no block
        # needs rescaling.
        lines = [
            attachment({'type': 'prompt_snapshot', 'systemPrompt': 'S' * 400, 'tools': 'T' * 400}),
            attachment({'type': 'environment'}, [{'type': 'text', 'text': 'E' * 40}]),
            attachment({'type': 'hook_success', 'content': 'H' * 4000}),
            attachment({'type': 'instructions', 'files': [{'path': str(claude / 'CLAUDE.md'), 'content': 'C' * 800}]},
                       [{'type': 'text', 'text': 'C' * 800}]),
            user('U' * 400),
            assistant(10, 'r1', [{'type': 'thinking', 'thinking': ''},
                                 {'type': 'tool_use', 'id': 't1', 'name': 'Read', 'input': {'file_path': rules}}],
                      'tool_use', {'cache_creation_input_tokens': 510, 'output_tokens': 50}),
            user([{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'R' * 4000}]),
            # A line from another conversation recorded in the same file.
            user('X' * 4000, agentId='elsewhere'),
            assistant(20, 'r2', [{'type': 'thinking', 'thinking': ''},
                                 {'type': 'tool_use', 'id': 't2', 'name': 'Bash', 'input': {'command': 'git push'}}],
                      'tool_use', {'cache_read_input_tokens': 510, 'cache_creation_input_tokens': 1050, 'output_tokens': 30}),
            user([{'type': 'tool_result', 'tool_use_id': 't2', 'is_error': True, 'content': 'Exit code 1\n' + 'r' * 20}]),
            assistant(30, 'r3', [{'type': 'thinking', 'thinking': ''}, {'type': 'text', 'text': 'Done'}],
                      'end_turn', {'cache_read_input_tokens': 1560, 'cache_creation_input_tokens': 33,
                                  'input_tokens': 5, 'output_tokens': 5}),
            {'type': 'system', 'subtype': 'compact_boundary', 'timestamp': 31, 'sessionId': SID},
            user('Z' * 400, isCompactSummary=True),
            assistant(40, 'r4', [{'type': 'text', 'text': 'Done'}], 'end_turn',
                      {'cache_read_input_tokens': 200, 'cache_creation_input_tokens': 100, 'output_tokens': 1}),
            user('W' * 40),
            assistant(200, 'r5', [{'type': 'text', 'text': 'Late'}], 'end_turn',
                      {'cache_read_input_tokens': 300, 'cache_creation_input_tokens': 11, 'output_tokens': 1}),
        ]
        return self.run_lines(home, lines)

    def test_content_is_charged_at_the_rate_each_request_paid_for_it(self):
        _, _, parts = self.run_fixture()
        context = parts['context']
        # Written by the first request, read by the next two, then compacted away.
        self.assertAlmostEqual(context[('Instruction files', '~/.claude/CLAUDE.md')], 200 * WRITE + 2 * 200 * READ)
        # A file read through a tool is charged like an automatically loaded one.
        self.assertAlmostEqual(context[('Instruction files', '~/.claude/rules.md')], 1000 * WRITE + 1000 * READ)
        # The system prompt stays through compaction; the late request is outside the window.
        self.assertAlmostEqual(context[('System prompt and tools', 'System prompt')], 100 * WRITE + 3 * 100 * READ)
        self.assertAlmostEqual(context[('Compaction summaries', 'Summary')], 100 * WRITE)
        # The failed result's 8 tokens close the context: uncached input
        # covers its last 5 and the cache write covers the rest.
        self.assertAlmostEqual(context[('Failed tool calls', 'Bash: git push: exit code 1')], 5 * INPUT + 3 * WRITE)
        self.assertNotIn(('Attachments', 'hook_success'), context)
        # The prompt after the window closes is never charged.
        self.assertAlmostEqual(context[('User prompts', 'Typed text')], 100 * WRITE + 2 * 100 * READ)

    def test_totals_match_the_priced_requests(self):
        records, _, parts = self.run_fixture()
        self.assertEqual(sorted(r['response_id'] for r in records), ['r1', 'r2', 'r3', 'r4'])
        inputs = 0
        for r in records:
            unit = claude_unit_prices(r, RATES)
            u = r['usage']
            inputs += (u.get('cache_read_input_tokens', 0) * unit['read']
                       + u.get('cache_creation_input_tokens', 0) * unit['write']
                       + u.get('input_tokens', 0) * unit['input'])
        self.assertAlmostEqual(sum(parts['context'].values()), inputs)
        self.assertAlmostEqual(sum(parts['actions'].values()), sum(price('claude', r, RATES) for r in records))

    def test_each_request_is_assigned_to_its_action_or_the_failure_it_follows(self):
        records, _, parts = self.run_fixture()
        cost = {r['response_id']: price('claude', r, RATES) for r in records}
        self.assertEqual(parts['actions'], {
            ('Read', 'Read ~/.claude/rules.md'): cost['r1'],
            ('Bash', 'Bash: git push'): cost['r2'],
            ('After a failed tool call', 'Bash: git push: exit code 1'): cost['r3'],
            ('Replies', 'Final reply'): cost['r4'],
        })

    def test_report_summarizes_categories_and_sessions(self):
        records, names, parts = self.run_fixture()
        report = aggregate('claude', records, names, {'start': 1, 'end': 100}, RATES, {}, {SID: parts})
        context = report['summary']['breakdown']['context']
        categories = {c['category']: c for c in context['categories']}
        instructions = categories['Instruction files']
        self.assertEqual([i['label'] for i in instructions['items']], ['~/.claude/rules.md', '~/.claude/CLAUDE.md'])
        self.assertEqual(instructions['sessions'], 1)
        self.assertAlmostEqual(report['sessions'][0]['breakdown']['context']['Instruction files'], instructions['cost'])
        self.assertAlmostEqual(sum(c['percent_of_total'] for c in report['summary']['breakdown']['actions']['categories']), 100)

    def test_recorded_thinking_tokens_replace_the_estimate(self):
        home, _ = self.home()
        records, _, parts = self.run_lines(home, [
            attachment({'type': 'prompt_snapshot', 'systemPrompt': 'S' * 400, 'tools': 'T' * 400}),
            user('U' * 400),
            assistant(10, 'r1', [{'type': 'thinking', 'thinking': ''}, {'type': 'text', 'text': 'x' * 40}], 'end_turn',
                      {'cache_creation_input_tokens': 300, 'output_tokens': 1000,
                       'output_tokens_details': {'thinking_tokens': 90}}),
            user('Q' * 400),
            assistant(20, 'r2', [{'type': 'text', 'text': 'y'}], 'end_turn',
                      {'cache_read_input_tokens': 300, 'cache_creation_input_tokens': 200, 'output_tokens': 1}),
        ])
        # Output minus the visible text would have estimated 990 thinking tokens.
        self.assertAlmostEqual(parts['context'][('Thinking', 'Thinking')], 90 * WRITE)
        self.assertAlmostEqual(parts['context'][('User prompts', 'Typed text')], 100 * WRITE + 100 * READ + 100 * WRITE)

    def test_replayed_lines_are_counted_once(self):
        home, _ = self.home()
        failure = user([{'type': 'tool_result', 'tool_use_id': 't', 'is_error': True, 'content': 'Exit code 2'}], uuid='f')
        lines = [
            attachment({'type': 'prompt_snapshot', 'systemPrompt': 'S' * 400, 'tools': 'T' * 400}),
            assistant(10, 'r1', [{'type': 'tool_use', 'id': 't', 'name': 'Bash', 'input': {'command': 'make'}}],
                      'tool_use', {'cache_creation_input_tokens': 200, 'output_tokens': 5}),
            failure,
            assistant(20, 'r2', [{'type': 'text', 'text': 'ok'}], 'end_turn',
                      {'cache_read_input_tokens': 200, 'cache_creation_input_tokens': 10, 'output_tokens': 1}),
            failure,
            user('U' * 40),
            assistant(30, 'r3', [{'type': 'text', 'text': 'ok'}], 'end_turn',
                      {'cache_read_input_tokens': 210, 'cache_creation_input_tokens': 11, 'output_tokens': 1}),
        ]
        records, _, parts = self.run_lines(home, lines)
        cost = {r['response_id']: price('claude', r, RATES) for r in records}
        self.assertEqual(parts['actions'][('Replies', 'Final reply')], cost['r3'])
        self.assertEqual(parts['actions'][('After a failed tool call', 'Bash: make: exit code 2')], cost['r2'])

    def test_actions_distinguish_requests_made_only_for_them(self):
        home, _ = self.home()
        title = {'type': 'tool_use', 'id': 't1', 'name': 'mcp__s__set_session_title', 'input': {'title': 'x'}}
        make = {'type': 'tool_use', 'id': 't2', 'name': 'Bash', 'input': {'command': 'make'}}
        records, names, parts = self.run_lines(home, [
            attachment({'type': 'prompt_snapshot', 'systemPrompt': 'S' * 400, 'tools': 'T' * 400}),
            assistant(10, 'r1', [title, make], 'tool_use', {'cache_creation_input_tokens': 200, 'output_tokens': 9}),
            user([{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'ok'},
                  {'type': 'tool_result', 'tool_use_id': 't2', 'content': 'ok'}]),
            assistant(20, 'r2', [dict(title, id='t3')], 'tool_use',
                      {'cache_read_input_tokens': 200, 'cache_creation_input_tokens': 9, 'output_tokens': 5}),
        ])
        cost = {r['response_id']: price('claude', r, RATES) for r in records}
        key = ('set_session_title', 'set_session_title')
        self.assertAlmostEqual(parts['actions'][key], cost['r1'] / 2 + cost['r2'])
        self.assertEqual((parts['requests'][key], parts['sole_requests'][key]), (2, 1))
        self.assertEqual(parts['sole_cost'][key], cost['r2'])
        self.assertEqual(parts['sole_requests'][('Bash', 'Bash: make')], 0)
        report = aggregate('claude', records, names, {'start': 1, 'end': 100}, RATES, {}, {SID: parts})
        item = next(c for c in report['summary']['breakdown']['actions']['categories']
                    if c['category'] == 'set_session_title')['items'][0]
        self.assertEqual((item['requests'], item['sole_requests'], item['sole_cost']), (2, 1, cost['r2']))
        with tempfile.TemporaryDirectory() as tmp:
            rows = (save_report(Path(tmp), report, records, ZoneInfo('UTC')) / 'breakdown.csv').read_text().splitlines()
        self.assertTrue(rows[0].endswith(',requests,sole_requests,sole_cost'))
        self.assertIn(f"actions,set_session_title,set_session_title,{item['cost']},", '\n'.join(rows))
        self.assertTrue(any(r.startswith('context,') and r.endswith(',,,') for r in rows))

    def test_moved_and_removed_transcripts(self):
        records, _, expected = self.run_fixture()
        claude = Path(records[0]['source']).parents[2]
        moved = claude / 'projects' / 'moved'
        moved.mkdir()
        Path(records[0]['source']).rename(moved / (SID + '.jsonl'))
        home = claude.parent
        self.assertEqual(claude_context.attribute(records, RATES, home, claude)[SID], expected)
        (moved / (SID + '.jsonl')).unlink()
        parts = claude_context.attribute(records, RATES, home, claude)[SID]
        key = ('Other', 'Transcript removed during the run')
        self.assertEqual(list(parts['context']), [key])
        self.assertAlmostEqual(parts['context'][key], sum(expected['context'].values()))
        self.assertAlmostEqual(parts['actions'][key], sum(expected['actions'].values()))

    def test_parallel_worktrees_can_share_a_label(self):
        labels = claude_context.Labels(Path('/h'), Path('/h/.claude'), merge_worktrees=True)
        for path in ('~/dev/commontools/commontoolsinc.labs.Z/AGENTS.md',
                     '~/dev/commontools/commontoolsinc.labs.B0/AGENTS.md'):
            self.assertEqual(labels.key(('Instruction files', path)),
                             ('Instruction files', '~/dev/commontools/commontoolsinc.labs/.../AGENTS.md'))
        self.assertEqual(labels.key(('Tool results', 'Read ~/dev/labs/B0/X3/a.ts')),
                         ('Tool results', 'Read ~/dev/labs/.../.../a.ts'))
        # Lowercase names, longer names, and the final component are not checkouts.
        for label in ('Read ~/dev/labs/v1/a.ts', 'Read ~/dev/labs/ABC/a.ts', 'Read ~/dev/labs/root/B0'):
            self.assertEqual(labels.key(('Tool results', label)), ('Tool results', label))
        self.assertEqual(claude_context.Labels(Path('/h'), Path('/h/.claude')).key(('x', '/B0/')), ('x', '/B0/'))

    def test_merged_worktrees_count_one_request_once(self):
        home, claude = self.home()
        reads = [{'type': 'tool_use', 'id': f't{n}', 'name': 'Read', 'input': {'file_path': f'{home}/labs/{n}/AGENTS.md'}}
                 for n in ('B0', 'Z')]
        lines = [
            attachment({'type': 'prompt_snapshot', 'systemPrompt': 'S' * 400, 'tools': 'T' * 400}),
            assistant(10, 'r1', [{'type': 'thinking', 'thinking': ''}, *reads], 'tool_use',
                      {'cache_creation_input_tokens': 200, 'output_tokens': 100}),
            user([{'type': 'tool_result', 'tool_use_id': f't{n}', 'content': 'A' * 400} for n in ('B0', 'Z')]),
            assistant(20, 'r2', [{'type': 'text', 'text': 'ok'}], 'end_turn',
                      {'cache_read_input_tokens': 200, 'cache_creation_input_tokens': 300, 'output_tokens': 1}),
        ]
        records, _, _ = self.run_lines(home, lines)
        merged = claude_context.attribute(records, RATES, home, claude, merge_worktrees=True)[SID]
        key = ('Read', 'Read ~/labs/.../AGENTS.md')
        self.assertEqual((merged['requests'][key], merged['sole_requests'][key]), (1, 1))
        self.assertAlmostEqual(merged['context'][('Instruction files', '~/labs/.../AGENTS.md')], 200 * WRITE)
        separate = claude_context.attribute(records, RATES, home, claude)[SID]
        self.assertEqual(separate['sole_requests'][('Read', 'Read ~/labs/B0/AGENTS.md')], 0)
        self.assertAlmostEqual(separate['context'][('Instruction files', '~/labs/Z/AGENTS.md')], 100 * WRITE)

    def test_empty_breakdown_adds_no_section(self):
        report = aggregate('claude', [], {}, {'start': 1, 'end': 2}, RATES, {}, {})
        self.assertNotIn('breakdown', report['summary'])

    def test_later_snapshot_divides_the_unrecorded_remainder(self):
        stream = claude_context.Stream()
        stream.context.append(claude_context.Block(('User prompts', 'Typed text'), 100))
        stream.calibrate(500)
        self.assertEqual([b.tokens for b in stream.fixed], [400])
        stream.record_snapshot(100, 300)
        self.assertEqual([(b.key[1], b.tokens) for b in stream.fixed],
                         [('Tool definitions', 300), ('System prompt', 100)])
        self.assertEqual(stream.known, 500)

    def stream(self):
        stream = claude_context.Stream()
        stream.snapshot = (100, 100)
        stream.context.append(claude_context.Block(('User prompts', 'Typed text'), 100))
        stream.calibrate(300)
        return stream

    def test_growth_with_only_empty_blocks_is_unidentified(self):
        stream = self.stream()
        stream.context.append(claude_context.Block(('Tool results', 'Bash: true'), 0))
        stream.calibrate(350)
        # Every block has a size, so charging the whole context succeeds.
        stream.charge({'cache_read_input_tokens': 250, 'input_tokens': 100}, {'read': 1, 'write': 2, 'input': 3})
        costs = stream.finish()
        self.assertEqual(costs[('Other', 'Unidentified context')], 50 * 3)
        self.assertEqual(costs[('Tool results', 'Bash: true')], 0)
        self.assertEqual(costs[('User prompts', 'Typed text')], 50 * 1 + 50 * 3)

    def test_shrinking_context_scales_messages_but_not_the_system_prompt(self):
        stream = self.stream()
        stream.context.append(claude_context.Block(('Tool results', 'Bash: cat'), 100))
        stream.calibrate(250)
        self.assertEqual(stream.known, 250)
        self.assertEqual([b.tokens for b in stream.fixed], [100, 100])
        self.assertEqual([b.tokens for b in stream.context], [25, 25])

    def test_csv_escapes_labels_that_spreadsheets_would_evaluate(self):
        records, names, parts = self.run_fixture()
        parts['context'][('Tool results', '=HYPERLINK("x")')] = 1
        report = aggregate('claude', records, names, {'start': 1, 'end': 100}, RATES, {}, {SID: parts})
        with tempfile.TemporaryDirectory() as tmp:
            directory = save_report(Path(tmp), report, records, ZoneInfo('UTC'))
            rows = (directory / 'breakdown.csv').read_text().splitlines()
        self.assertIn('context,Tool results,"\'=HYPERLINK(""x"")",1,', '\n'.join(rows))
        self.assertTrue(any(r.startswith('actions,After a failed tool call,Bash: git push: exit code 1,') for r in rows))

    def test_labels(self):
        labels = claude_context.Labels(Path('/home/u'), Path('/home/u/.claude'))
        self.assertEqual(labels.tool('Bash', {'command': 'cd /x && FOO=1 git status -s'}, '/'), ('Bash', 'Bash: git status', []))
        self.assertEqual(labels.tool('Bash', {'command': 'cd ~/.claude && cat style.md other.txt'}, '/'),
                         ('Bash', 'Bash: cat', ['/home/u/.claude/style.md']))
        self.assertEqual(labels.tool('Bash', {'command': 'python3 - <<EOF\nprint(1)\nEOF'}, '/')[1], 'Bash: python3')
        self.assertEqual(labels.tool('Bash', {'command': 'export P=1; deno test'}, '/')[1], 'Bash: deno test')
        self.assertEqual(labels.tool('Bash', {'command': 'for f in a b; do sed -n 1p ~/.claude/$f.md; done'}, '/'),
                         ('Bash', 'Bash: sed', ['/home/u/.claude/$f.md']))
        # Commands that do not print a file do not read it.
        self.assertEqual(labels.tool('Bash', {'command': 'rm AGENTS.md; git add CLAUDE.md'}, '/r')[2], [])
        self.assertEqual(labels.tool('Edit', {'file_path': '/r/AGENTS.md'}, '/')[2], [])
        self.assertEqual(labels.tool('Read', {'file_path': 'src/AGENTS.md'}, '/repo'),
                         ('Read', 'Read /repo/src/AGENTS.md', ['/repo/src/AGENTS.md']))
        self.assertEqual(labels.tool('Read', {'file_path': '/home/u/.claude/projects/p/s/tool-results/x.txt'}, '/')[2], [])
        self.assertEqual(labels.tool('mcp__abc__set_session_title', {}, '/'), ('set_session_title', 'set_session_title', []))


if __name__ == '__main__':
    unittest.main()
