"""Window selection and percentage behavior across report outputs."""
import csv
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import session_cost_reports
from reporting import CONVENTION_SOURCE, WEEK, aggregate, save_report, select_window
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


if __name__ == '__main__':
    unittest.main()
