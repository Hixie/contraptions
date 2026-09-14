import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import claude_reader
import codex_reader
import session_cost_reports
from reporting import (WEEK, aggregate, claude_reset, codex_titles, footer,
                       jsonlines, previous_window, price, save_report,
                       scan_codex, stamp, title_call)

HERE = Path(__file__).parent
RATES = json.loads((HERE / 'rates.json').read_text())
ROOT = 'a' * 36
CHILD = 'b' * 36
FORK = 'c' * 36


def q(start, observed, service='codex', drift=0):
    return {'service': service, 'observed': start + observed,
            'reset': start + WEEK + drift, 'duration': WEEK}


def event(kind, time, payload):
    return {'k': kind, 't': time, 'o': time, 'p': payload}


def usage(rid, time, owner=ROOT, turn='t', root_turn='t', tokens=100):
    return event('token_usage_record', time, {
        'response_id': rid, 'thread_id': owner, 'session_id': ROOT,
        'turn_id': turn, 'root_turn_id': root_turn,
        'usage': {'input_tokens': tokens, 'cached_input_tokens': 50,
                  'output_tokens': 10, 'total_tokens': tokens + 10}})


def file(sid, events, parent=None, fork=None):
    return {'path': '/fixtures/' + sid + '.jsonl',
            'meta': {'id': sid, 'session_id': ROOT, 'timestamp': 1,
                     'parent_thread_id': parent, 'forked_from_id': fork},
            'events': events}


def metadata(sid, source='user'):
    return {'id': sid, 'thread_source': source, 'model': 'gpt-6-astra'}


class Windows(unittest.TestCase):
    def test_superseding_reset_and_opening_request_drift(self):
        first, second, third = 1_000_000, 1_010_000, 1_600_000
        observations = [q(first, 10), q(second, 15), q(second, 30, drift=5), q(third, 20), q(third, 21, drift=15), q(third, 300, drift=22)]
        w = previous_window(observations, 'codex', third + 100)
        self.assertEqual((w['start'], w['end']), (second, third))

    def test_expired_window_and_idle_gap(self):
        first = 1_000_000
        w = previous_window([q(first, 30)], 'codex', first + WEEK + 400)
        self.assertEqual((w['start'], w['end']), (first, first + WEEK))

    def test_current_window_is_not_reported_as_complete(self):
        with self.assertRaisesRegex(ValueError, 'no completed'):
            previous_window([q(1_000_000, 30)], 'codex', 1_000_100)

    def test_stale_copied_deadline_and_other_service_do_not_replace_cycle(self):
        obs = [q(1_000_000, 10), q(1_000_000, WEEK + 10), q(2_000_000, 20, 'claude')]
        self.assertEqual(previous_window(obs, 'codex', 3_000_000)['start'], 1_000_000)

    def test_claude_local_and_year_boundary(self):
        zone = ZoneInfo('America/Los_Angeles')
        observed = stamp('2001-07-07T23:00:00-07:00')
        self.assertEqual(claude_reset("You've hit your weekly limit · resets 10am (America/Los_Angeles)", observed, zone), stamp('2001-07-08T10:00:00-07:00'))
        observed = stamp('2001-12-31T23:00:00-08:00')
        self.assertEqual(claude_reset('weekly limit · resets Jan 2 at 10am (America/Los_Angeles)', observed, zone), stamp('2002-01-02T10:00:00-08:00'))
        self.assertIsNone(claude_reset('five-hour limit resets 10am', observed, zone))

    def test_claude_previous_completed_week(self):
        start = stamp('2001-07-01T10:00:00-07:00')
        w = previous_window([q(start, WEEK - 100, 'claude'), q(start + WEEK, 30, 'claude')], 'claude', start + WEEK + 200)
        self.assertEqual((w['start'], w['end']), (start, start + WEEK))


class Accounting(unittest.TestCase):
    def test_claude_synthetic_zero_usage_and_search_only_usage(self):
        self.assertFalse(claude_reader.has_usage({'input_tokens': 0, 'server_tool_use': {'web_search_requests': 0}}))
        self.assertTrue(claude_reader.has_usage({'server_tool_use': {'web_search_requests': 1}}))

    def test_cache_and_reasoning_are_not_counted_twice(self):
        r = {'response_id': 'r', 'model': 'gpt-6-astra', 'usage': {'input_tokens': 1_000_000, 'cached_input_tokens': 900_000, 'output_tokens': 100_000, 'reasoning_output_tokens': 99_000}, 'tier': 'default'}
        self.assertAlmostEqual(price('codex', r, RATES), 3.8 + 7.5)
        r['usage']['input_tokens'] = 200_000
        r['usage']['cached_input_tokens'] = 100_000
        r['tier'] = 'fast'
        self.assertAlmostEqual(price('codex', r, RATES), 12.2)

    def test_claude_cache_ttls_and_search(self):
        r = {'response_id': 'r', 'model': 'claude-opus-5', 'usage': {'input_tokens': 1000, 'output_tokens': 100, 'cache_read_input_tokens': 1000, 'cache_creation_input_tokens': 2000, 'cache_creation': {'ephemeral_1h_input_tokens': 1000, 'ephemeral_5m_input_tokens': 1000}, 'server_tool_use': {'web_search_requests': 1}}}
        self.assertAlmostEqual(price('claude', r, RATES), .03425)
        r['usage']['cache_creation_input_tokens'] = 1999
        with self.assertRaisesRegex(ValueError, 'inconsistent'):
            price('claude', r, RATES)

    def test_unpriced_remains_null(self):
        r = {'response_id': 'r', 'session_id': ROOT, 'model': 'future-model', 'usage': {'input_tokens': 42}, 'footer': '🧱', 'subagent': False}
        report = aggregate('codex', [r], {ROOT: ('Name', 'first_user')}, {'start': 1, 'end': 2}, RATES, {})
        self.assertIsNone(r['cost_usd'])
        self.assertEqual(report['summary']['unpriced_requests'], 1)
        self.assertEqual(report['sessions'][0]['unpriced_tokens']['input_tokens'], 42)

    def test_fork_dedup_and_parent_footer(self):
        parent = file(ROOT, [event('task_started', 10, {'turn_id': 't'}), event('turn_context', 11, {'turn_id': 't', 'model': 'gpt-6-astra'}), usage('r', 20), event('task_complete', 90, {'turn_id': 't', 'last_agent_message': 'Done\n🧱 0x5E¹'})])
        copied = file(FORK, [usage('r', 40)], fork=ROOT)
        child = file(CHILD, [event('turn_context', 12, {'turn_id': 'child', 'model': 'gpt-6-astra'}), usage('child-r', 30, CHILD, 'child'), event('task_complete', 60, {'turn_id': 'child', 'last_agent_message': 'Reviewed.'})], parent=ROOT)
        x = {'metadata': [metadata(ROOT), metadata(FORK), metadata(CHILD, 'subagent')], 'files': [parent, copied, child]}
        records, _, diagnostics = codex_reader.read(x, 15, 80, {}, {})
        self.assertEqual(len(records), 2)
        self.assertEqual(diagnostics['modern_copies'], 1)
        self.assertEqual([(r['footer'], r['footer_source']) for r in records], [('🧱', 'own'), ('🧱', 'parent')])
        self.assertEqual({r['session_id'] for r in records}, {ROOT})

    def test_repeated_notification_is_not_a_new_request(self):
        modern = usage('r', 20)
        u = modern['p']['usage']
        count = {'total_token_usage': u, 'last_token_usage': u}
        events = [event('turn_context', 10, {'turn_id': 't', 'model': 'gpt-6-astra'}), modern, event('token_count', 21, count), event('token_count', 22, count), event('token_count', 23, {'total_token_usage': {'total_tokens': 999}, 'last_token_usage': {'total_tokens': 999}})]
        records, _, _ = codex_reader.read({'metadata': [metadata(ROOT)], 'files': [file(ROOT, events)]}, 1, 100, {}, {})
        self.assertEqual(len(records), 1)

    def test_new_cycle_turn_started_one_second_before_cutoff_is_excluded(self):
        f = file(ROOT, [event('task_started', 99, {'turn_id': 't'}), usage('r', 105)])
        records, names, _ = codex_reader.read({'metadata': [metadata(ROOT)], 'files': [f]}, 1, 100, {}, {})
        self.assertEqual((records, names), ([], {}))

    def test_fractional_seconds_obey_inclusive_start_exclusive_end(self):
        start = stamp('2001-07-08T00:00:00Z')
        end = start + 100
        responses = [usage('before', start - .001), usage('start', start),
                     usage('fractional', stamp('2001-07-08T00:00:00.125Z')),
                     usage('end', end)]
        x = {'metadata': [metadata(ROOT)], 'files': [file(ROOT, responses)]}
        records, _, _ = codex_reader.read(x, start, end, {}, {})
        self.assertEqual({r['response_id'] for r in records}, {'start', 'fractional'})


class TitlesAndFiles(unittest.TestCase):
    def test_footer_ignores_prose_and_closing_heartbeat_tag(self):
        self.assertEqual(footer('Done\n🪻 0x5E¹\n</heartbeat>'), '🪻')
        self.assertIsNone(footer('The title contains 🧱 and this is prose.'))
        self.assertIsNone(footer('The 🧱 state was introduced in 0x5E.'))
        self.assertIsNone(footer('🎬 0x5E'))
        self.assertEqual(footer('🟠 [#101](https://github.com/example/repo/pull/101) 0x47 ΑΒΘ'), '🟠')
        self.assertEqual(footer('🦚 → ✅ 0x54 [#102](https://github.com/example/repo/pull/102) ΑΒΚ'), '✅')

    def test_first_gear_wins(self):
        names = codex_titles({ROOT}, {ROOT: [(1, 'Automatic'), (2, '⚙️ First'), (3, '⚙️ Later')]}, {}, {}, {}, {})
        self.assertEqual(names[ROOT], ('⚙️ First', 'first_gear'))

    def test_tool_names_are_excluded_from_user_fallback(self):
        history = {ROOT: [(1, 'Automatic'), (2, 'Agent'), (3, 'User'), (4, 'Later user')]}
        names = codex_titles({ROOT}, history, {}, {}, {ROOT: [(2, 'Agent')]}, {})
        self.assertEqual(names[ROOT], ('User', 'first_user_inferred'))

    def test_title_call_parses_literals_without_executing(self):
        self.assertEqual(title_call('exec', 'await tools.mcp__codex_app__set_thread_title({title:"⚙️ Hello",threadId:"abc"})', ROOT), [('abc', '⚙️ Hello')])
        self.assertEqual(title_call('exec', 'set_thread_title({title: dangerous()})', ROOT), [])
        self.assertEqual(title_call('exec', 'set_thread_title({title: "⚙️ Repair {cache}"})', ROOT), [(ROOT, '⚙️ Repair {cache}')])

    def test_incomplete_tail_is_ignored_but_invalid_completed_line_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / 'records.jsonl'
            p.write_text('{"ok":1}\n{"unfinished":')
            self.assertEqual(list(jsonlines(p)), [(1, {'ok': 1})])
            p.write_text('{invalid}\n')
            with self.assertRaisesRegex(ValueError, 'invalid JSON'):
                list(jsonlines(p))

    def test_reports_escape_embedded_session_text(self):
        report = aggregate('codex', [], {ROOT: ('</script><img src=x onerror=alert(1)>', 'first_user')}, {'start': 1, 'end': 2, 'evidence': []}, RATES, {})
        with tempfile.TemporaryDirectory() as tmp:
            dest = save_report(Path(tmp), report, [], ZoneInfo('UTC'))
            self.assertNotIn('</script><img', (dest / 'index.html').read_text())
            self.assertIn('\\u003c/script>', (dest / 'index.html').read_text())

    def test_database_failure_replaces_stale_success_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'state_0.sqlite').write_text('damaged database\n')
            output = root / 'reports'
            output.mkdir()
            (output / 'latest.json').write_text('{"reports":["old success"]}')
            with patch('builtins.print'):
                result = session_cost_reports.main(['--service', 'codex', '--codex-home', tmp,
                    '--output', str(output), '--codex-window', '2001-07-01', '2001-07-08',
                    '--as-of', '2001-07-09', '--timezone', 'UTC'])
            self.assertEqual(result, 1)
            manifest = json.loads((output / 'latest.json').read_text())
            self.assertEqual(manifest['reports'], [])
            self.assertIn('not a database', manifest['errors'][0])

    def test_display_timezone_does_not_reinterpret_local_logs(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('session_cost_reports.local_zone', return_value=ZoneInfo('America/Los_Angeles')), \
                 patch('session_cost_reports.claude_logs', return_value=([], [])) as logs, \
                 patch('session_cost_reports.claude_reader.read', return_value=([], {}, {})) as read, \
                 patch.dict('os.environ', {'CLAUDE_CONFIG_DIR': '/wrong-home'}), \
                 patch('builtins.print'):
                result = session_cost_reports.main(['--service', 'claude', '--home', tmp,
                    '--output', str(Path(tmp) / 'reports'), '--claude-window', '2001-07-01', '2001-07-08',
                    '--as-of', '2001-07-09', '--timezone', 'UTC'])
            self.assertEqual(result, 0)
            self.assertEqual(logs.call_args.args[1].key, 'America/Los_Angeles')
            self.assertEqual(read.call_args.args[0], Path(tmp) / '.claude/projects')


if __name__ == '__main__':
    unittest.main()
