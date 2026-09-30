"""Window selection and percentage behavior across report outputs."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import session_cost_reports
from reporting import (CONVENTION_SOURCE, WEEK, aggregate, claude_logs,
                       compact_quotas, detected_windows, save_report,
                       select_window)
from test_reports import RATES, ROOT, FORK, q, stamp


class WindowSelection(unittest.TestCase):
    def test_current_stops_at_asof_and_preserves_reset(self):
        start = 1_000_000
        window = select_window([q(start, 10)], 'codex', start + 100, 'current')
        self.assertEqual((window['start'], window['end'], window['reset']),
                         (start, start + 100, start + WEEK))
        self.assertEqual(window['selection'], 'current')

    def test_manual_reset_closes_last_and_opens_current(self):
        start, reset = 1_000_000, 1_010_000
        obs = [q(start, 10), q(reset, 5)]
        last = select_window(obs, 'codex', reset + 20, 'last')
        current = select_window(obs, 'codex', reset + 20, 'current')
        self.assertEqual((last['start'], last['end']), (start, reset))
        self.assertEqual((current['start'], current['end']), (reset, reset + 20))
        self.assertEqual(last['evidence'], obs)
        self.assertEqual(current['evidence'], obs[1:])

    def test_exact_reset_requires_evidence_of_new_current_window(self):
        start = 1_000_000
        obs = [q(start, 10)]
        self.assertEqual(select_window(obs, 'codex', start + WEEK, 'last')['end'], start + WEEK)
        with self.assertRaisesRegex(ValueError, 'no current'):
            select_window(obs, 'codex', start + WEEK, 'current')
        obs.append(q(start + WEEK, 0))
        current = select_window(obs, 'codex', start + WEEK, 'current')
        self.assertEqual(current['start'], current['end'])

    def test_future_observation_does_not_change_past_current_window(self):
        start = 1_000_000
        obs = [q(start, 10), q(start + 100, 20)]
        current = select_window(obs, 'codex', start + 50, 'current')
        self.assertEqual(current['reset'], start + WEEK)

    def test_account_resets_partition_effective_windows(self):
        start = 1_000_000
        first = q(start, 10, 'claude')
        first['account'] = 'account-a'
        second = q(start + 2 * 24 * 3600, 10, 'claude')
        second['account'] = 'account-b'
        observations = [first, second]

        early_last = select_window(observations, 'claude', start + 3 * 24 * 3600, 'last')
        early_current = select_window(observations, 'claude', start + 3 * 24 * 3600, 'current')
        self.assertEqual((early_last['start'], early_last['end']),
                         (start, start + 2 * 24 * 3600))
        self.assertEqual({w['account'] for w in early_last['detected']},
                         {'account-a', 'account-b'})
        self.assertEqual((early_current['start'], early_current['reset']),
                         (start + 2 * 24 * 3600, start + WEEK))
        self.assertEqual({w['account'] for w in early_current['detected']},
                         {'account-a', 'account-b'})

        late_last = select_window(observations, 'claude', start + WEEK + 100, 'last')
        late_current = select_window(observations, 'claude', start + WEEK + 100, 'current')
        self.assertEqual((late_last['start'], late_last['end']),
                         (start + 2 * 24 * 3600, start + WEEK))
        self.assertEqual((late_current['start'], late_current['reset']),
                         (start + WEEK, start + WEEK + 2 * 24 * 3600))
        self.assertEqual({w['account'] for w in late_current['detected']},
                         {'account-a', 'account-b'})

    def test_nearby_account_resets_remain_separate_boundaries(self):
        start = 1_000_000
        first = {**q(start, 10, 'claude'), 'account': 'account-a'}
        second = {**q(start + 60, 0, 'claude'), 'account': 'account-b'}
        observations = [first, second]

        early = select_window(observations, 'claude', start + 61, 'current')
        late = select_window(observations, 'claude', start + WEEK + 1, 'current')
        self.assertEqual((early['start'], early['reset']),
                         (start + 60, start + WEEK))
        self.assertEqual((late['start'], late['reset']),
                         (start + WEEK, start + WEEK + 60))

    def test_accounted_history_masks_old_unattributed_copy(self):
        start = 1_000_000
        old = q(start, 20, 'claude')
        first = {**q(start, 10, 'claude'), 'account': 'account-a'}
        second = {**q(start, 30, 'claude'), 'account': 'account-b'}
        compacted = compact_quotas([old, first, second])
        self.assertEqual(len(compacted), 3)
        selected = select_window(compacted, 'claude', start + 40, 'current')
        self.assertEqual({window['account'] for window in selected['detected']},
                         {'account-a', 'account-b'})

    def test_historical_query_keeps_legacy_evidence(self):
        start = 1_000_000
        old = q(start, 10, 'claude')
        attributed = {**q(start, 20, 'claude'), 'account': 'account-a'}
        selected = select_window(compact_quotas([old, attributed]), 'claude',
                                 start + 15, 'current')
        self.assertEqual((selected['start'], selected['reset']),
                         (start, start + WEEK))
        self.assertEqual(selected['detected'][0]['account'], None)

    def test_claude_reset_evidence_is_assigned_to_logged_in_account(self):
        account_a = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        account_b = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            (logs / 'main.log').write_text(
                f'2001-07-07 09:00:00 cacheKey=acct:{account_a}|profile\n'
                f'2001-07-07 09:00:30 cacheKey=acct:{account_a}|profile\n'
                "2001-07-07 09:01:00 weekly limit resets Jul 8 at 10am (UTC)\n"
                f'2001-07-09 09:00:00 cacheKey=acct:{account_b}|profile\n'
                f'2001-07-09 09:00:30 cacheKey=acct:{account_b}|profile\n'
                "2001-07-09 09:01:00 weekly limit resets Jul 10 at 10am (UTC)\n")
            quotas, _, activity = claude_logs(logs, ZoneInfo('UTC'),
                                              stamp('2001-07-11T00:00:00Z'))
        self.assertEqual([quota['account'] for quota in quotas],
                         [account_a, account_b])
        self.assertEqual(activity, [
            {'account': account_a, 'start': stamp('2001-07-07T09:00:00Z'),
             'end': stamp('2001-07-09T09:00:00Z')},
            {'account': account_b, 'start': stamp('2001-07-09T09:00:00Z'),
             'end': None},
        ])

    def test_claude_reset_uses_global_account_timeline(self):
        account_a = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        account_b = 'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb'
        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp)
            (logs / 'main.log').write_text(
                f'2001-07-07 09:00:00 cacheKey=acct:{account_a}|profile\n'
                "2001-07-07 11:00:00 weekly limit resets Jul 8 at 10am (UTC)\n")
            (logs / 'main1.log').write_text(
                f'2001-07-07 10:00:00 cacheKey=acct:{account_b}|profile\n')
            quotas, _, _ = claude_logs(logs, ZoneInfo('UTC'),
                                       stamp('2001-07-08T00:00:00Z'))
        self.assertEqual(quotas[0]['account'], account_b)

    def test_codex_primary_and_secondary_are_distinct_windows(self):
        start = 1_000_000
        observed = start + 2 * 24 * 3600
        primary = {'service': 'codex', 'observed': observed,
                   'reset': start + WEEK, 'duration': WEEK,
                   'kind': 'codex primary'}
        secondary = {**primary, 'reset': start + WEEK + 24 * 3600,
                     'kind': 'codex secondary'}
        windows = detected_windows([primary, secondary], 'codex', observed)
        self.assertEqual({window['reset'] for window in windows},
                         {primary['reset'], secondary['reset']})

    def test_current_snapshots_share_a_path_and_do_not_overwrite_last(self):
        start = 1_000_000
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            paths = []
            for asof in (start + 20, start + 40, start + WEEK):
                selection = 'last' if asof == start + WEEK else 'current'
                window = select_window([q(start, 10)], 'codex', asof, selection)
                report = aggregate('codex', [], {}, window, RATES, {})
                paths.append(save_report(output, report, [], ZoneInfo('UTC')))
            self.assertEqual(paths[0], paths[1])
            self.assertNotEqual(paths[1], paths[2])
            current = json.loads((paths[1] / 'report.json').read_text())
            self.assertEqual(current['window']['end'], start + 40)
            self.assertIn('local_reset', current['window'])


class Percentages(unittest.TestCase):
    def report(self):
        records = []
        for rid, sid, tokens, state, child, model in [
            ('a', ROOT, 400_000, '🧱', False, 'claude-opus-5'),
            ('b', ROOT, 200_000, '🐤', True, 'claude-opus-5'),
            ('c', FORK, 200_000, '🧱', False, 'claude-opus-5'),
            ('d', FORK, 99_000_000, '🧱', False, 'unknown-model'),
        ]:
            records.append({'response_id': rid, 'session_id': sid,
                            'model': model, 'usage': {'input_tokens': tokens},
                            'footer': state, 'subagent': child})
        names = {ROOT: ('A', 'first_gear'), FORK: ('B', 'first_user')}
        return aggregate('claude', records, names, {'start': 1, 'end': 2}, RATES, {}), records

    def test_denominators_exclude_unpriced_requests(self):
        report, _ = self.report()
        first, second = report['sessions']
        self.assertEqual(report['summary']['window_total'], 4)
        self.assertEqual((first['percent_of_total'], second['percent_of_total']), (75, 25))
        self.assertAlmostEqual(first['segment_percent_of_session']['🧱'], 200 / 3)
        self.assertAlmostEqual(first['subagent_percent_of_session'], 100 / 3)
        self.assertEqual(report['summary']['segment_percent_of_total'], {'🧱': 75, '🐤': 25})
        self.assertEqual(report['summary']['subagent_percent_of_total'], 25)
        self.assertEqual(sum(s['percent_of_total'] for s in report['sessions']), 100)
        self.assertEqual(report['convention_source'], CONVENTION_SOURCE)

    def test_csv_preserves_percentages_and_zero_denominators_are_blank(self):
        report, records = self.report()
        with tempfile.TemporaryDirectory() as tmp:
            output = save_report(Path(tmp), report, records, ZoneInfo('UTC'))
            with (output / 'sessions.csv').open() as fp:
                rows = list(csv.DictReader(fp))
            self.assertEqual(float(rows[0]['percent_of_total']), 75)
            self.assertAlmostEqual(float(rows[0]['🧱_percent_of_session']), 200 / 3)
            empty = aggregate('codex', [], {ROOT: ('Empty', 'last_auto')}, {'start': 1, 'end': 2}, RATES, {})
            output = save_report(Path(tmp), empty, [], ZoneInfo('UTC'))
            self.assertIsNone(empty['sessions'][0]['percent_of_total'])
            self.assertIsNone(empty['summary']['subagent_percent_of_total'])
            with (output / 'sessions.csv').open() as fp:
                self.assertEqual(next(csv.DictReader(fp))['percent_of_total'], '')


class CommandWindows(unittest.TestCase):
    def run_report(self, selection=None, only_current=False):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            sessions = base / 'codex/sessions'
            sessions.mkdir(parents=True)
            start = stamp('2001-07-01T00:00:00Z')
            end = start + WEEK
            asof = end + 100
            events = []
            for ts, text in [(start + 10, 'last'), (end + 10, 'current')]:
                if only_current and text == 'last':
                    continue
                events += [
                    {'type': 'turn_context', 'timestamp': ts, 'payload': {'turn_id': text, 'model': 'gpt-6-astra'}},
                    {'type': 'token_usage_record', 'timestamp': ts + 1, 'payload': {
                        'response_id': text, 'thread_id': ROOT, 'session_id': ROOT,
                        'turn_id': text, 'usage': {'input_tokens': 100, 'output_tokens': 10}}},
                    {'type': 'event_msg', 'timestamp': ts + 2, 'payload': {
                        'type': 'token_count', 'rate_limits': {'limit_id': 'codex',
                            'secondary': {'window_minutes': 10080, 'resets_at': start + WEEK if text == 'last' else end + WEEK}}}},
                ]
            (sessions / (ROOT + '.jsonl')).write_text(''.join(json.dumps(e) + '\n' for e in events))
            args = ['--service', 'codex', '--codex-home', str(base / 'codex'), '--output', str(base / 'reports'),
                    '--as-of', '2001-07-08T00:01:40Z', '--timezone', 'UTC']
            if selection:
                args += ['--window', selection]
            with patch('builtins.print'):
                status = session_cost_reports.main(args)
            manifest = json.loads((base / 'reports/latest.json').read_text())
            requests = {r['window']: [json.loads(line)['response_id']
                        for line in (Path(r['path']) / 'requests.jsonl').read_text().splitlines()]
                        for r in manifest['reports']}
            return status, manifest, requests

    def test_both_windows_have_separate_requests_and_manifest_entries(self):
        status, manifest, requests = self.run_report('both')
        self.assertEqual(status, 0)
        self.assertEqual(manifest['errors'], [])
        self.assertEqual(requests, {'last': ['last'], 'current': ['current']})

    def test_default_and_explicit_last_agree_and_current_is_independent(self):
        self.assertEqual(self.run_report()[2], {'last': ['last']})
        self.assertEqual(self.run_report('last')[2], {'last': ['last']})
        self.assertEqual(self.run_report('current')[2], {'current': ['current']})

    def test_both_keeps_current_report_when_last_is_unavailable(self):
        status, manifest, requests = self.run_report('both', only_current=True)
        self.assertEqual(status, 1)
        self.assertEqual(len(manifest['errors']), 1)
        self.assertIn('no completed', manifest['errors'][0])
        self.assertEqual(requests, {'current': ['current']})

    def test_explicit_bounds_reject_ambiguous_window_option(self):
        with patch('sys.stderr'), self.assertRaises(SystemExit) as error:
            session_cost_reports.main(['--window', 'current', '--codex-window', '2001-07-01', '2001-07-08'])
        self.assertEqual(error.exception.code, 2)

    def test_print_windows_prints_specified_bounds_without_writing_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'reports'
            missing_rates = Path(tmp) / 'missing-rates.json'
            with patch('builtins.print') as output_lines:
                status = session_cost_reports.main([
                    '--service', 'claude', '--print-windows',
                    '--claude-window', '2001-07-01', '2001-07-08',
                    '--as-of', '2001-07-09', '--timezone', 'UTC',
                    '--rates', str(missing_rates), '--output', str(output),
                ])
            rendered = '\n'.join(str(call.args[0]) for call in output_lines.call_args_list)
            self.assertEqual(status, 0)
            self.assertIn('claude custom:', rendered)
            self.assertIn('specified:', rendered)
            self.assertFalse(output.exists())

    def test_specified_window_print_does_not_read_existing_report_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'reports'
            output.mkdir()
            state = output / 'reset-history.json'
            state.write_text('{invalid history}\n')
            with patch('builtins.print') as output_lines:
                status = session_cost_reports.main([
                    '--service', 'claude', '--print-windows',
                    '--claude-window', '2001-07-01', '2001-07-08',
                    '--as-of', '2001-07-09', '--timezone', 'UTC',
                    '--output', str(output),
                ])
            rendered = '\n'.join(str(call.args[0]) for call in output_lines.call_args_list)
            self.assertEqual(status, 0)
            self.assertIn('specified:', rendered)
            self.assertEqual(state.read_text(), '{invalid history}\n')
            self.assertEqual(list(output.iterdir()), [state])

    def test_print_windows_shows_detected_and_effective_bounds(self):
        account = 'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa'
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            logs = base / 'Library/Logs/Claude'
            logs.mkdir(parents=True)
            (logs / 'main.log').write_text(
                f'2001-07-07 09:00:00 cacheKey=acct:{account}|profile\n'
                "2001-07-07 09:01:00 weekly limit resets Jul 8 at 10am (UTC)\n")
            output = base / 'reports'
            with patch('builtins.print') as output_lines:
                status = session_cost_reports.main([
                    '--service', 'claude', '--window', 'current',
                    '--print-windows', '--home', str(base),
                    '--as-of', '2001-07-07T12:00:00Z', '--timezone', 'UTC',
                    '--claude-log-timezone', 'UTC', '--output', str(output),
                ])
            rendered = '\n'.join(str(call.args[0]) for call in output_lines.call_args_list)
            self.assertEqual(status, 0)
            self.assertIn('claude observed account activity:', rendered)
            self.assertIn('account aaaaaaaa (current):', rendered)
            self.assertIn('end unknown', rendered)
            self.assertIn('claude detected quota windows:', rendered)
            self.assertIn('weekly limit deadline, account aaaaaaaa:', rendered)
            self.assertIn('effective:', rendered)
            self.assertIn('recorded reset:', rendered)
            self.assertNotIn('specified:', rendered)
            self.assertFalse(output.exists())

    def test_print_windows_lists_codex_resets(self):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            sessions = base / 'codex/sessions'
            sessions.mkdir(parents=True)
            start = stamp('2001-07-01T00:00:00Z')
            event = {'type': 'event_msg', 'timestamp': start + 2 * 24 * 3600, 'payload': {
                'type': 'token_count', 'rate_limits': {'limit_id': 'codex',
                    'primary': {'window_minutes': 10080, 'resets_at': start + WEEK},
                    'secondary': {'window_minutes': 10080,
                                  'resets_at': start + WEEK + 24 * 3600}}}}
            (sessions / (ROOT + '.jsonl')).write_text(json.dumps(event) + '\n')
            with patch('builtins.print') as output_lines:
                status = session_cost_reports.main([
                    '--service', 'codex', '--window', 'current', '--print-windows',
                    '--codex-home', str(base / 'codex'),
                    '--as-of', '2001-07-04T00:00:00Z', '--timezone', 'UTC',
                    '--output', str(base / 'reports'),
                ])
            rendered = '\n'.join(str(call.args[0]) for call in output_lines.call_args_list)
            self.assertEqual(status, 0)
            self.assertIn('codex detected quota windows:', rendered)
            self.assertIn('codex primary:', rendered)
            self.assertIn('codex secondary:', rendered)
            self.assertIn('recorded reset:', rendered)
            self.assertFalse((base / 'reports').exists())


if __name__ == '__main__':
    unittest.main()
