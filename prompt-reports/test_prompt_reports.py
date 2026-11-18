from compression import zstd
from contextlib import redirect_stdout
from datetime import datetime, timezone
import io
import json
import os
import re
from pathlib import Path
import tempfile
import time
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

import prompt_reports

NOW = round(time.time())
START, END = NOW - 3600, NOW + 3600
UTC = ZoneInfo('UTC')


def iso(offset):
    """An ISO 8601 timestamp `offset` seconds after the start of the window."""
    return datetime.fromtimestamp(START + offset, timezone.utc).isoformat().replace('+00:00', 'Z')


def write_jsonl(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(''.join(json.dumps(row) + '\n' for row in rows))


def user(offset, content, origin='human', **extra):
    row = {'type': 'user', 'timestamp': iso(offset), 'version': '2.1.317',
           'message': {'role': 'user', 'content': content}, **extra}
    if origin:
        row['origin'] = {'kind': origin}
    return row


def queued(offset, prompt, origin='human'):
    return {'type': 'attachment', 'timestamp': iso(offset),
            'attachment': {'type': 'queued_command', 'prompt': prompt, 'origin': {'kind': origin}}}


def started(offset, tool='mcp__ccd_session__start_session', **brief):
    return {'type': 'assistant', 'timestamp': iso(offset), 'message': {'content': [
        {'type': 'text', 'text': 'Starting a session.'},
        {'type': 'tool_use', 'name': 'mcp__ccd_session_mgmt__set_session_title', 'input': {'title': 'x'}},
        {'type': 'tool_use', 'name': tool, 'input': brief}]}}


def age(path, seconds):
    """Make `path` look last written `seconds` before the window starts."""
    os.utime(path, (START - seconds, START - seconds))


def meta(offset, thread, thread_source='user', source='vscode', **extra):
    return {'type': 'session_meta', 'timestamp': iso(offset), 'payload': {
        'id': thread, 'timestamp': iso(offset), 'source': source, 'thread_source': thread_source, **extra}}


def codex_message(offset, text, client_id=None):
    item = {'type': 'UserMessage', 'id': f'item-{offset}', 'content': [{'type': 'text', 'text': text}]}
    if client_id:
        item['client_id'] = client_id
    return {'type': 'event_msg', 'timestamp': iso(offset), 'payload': {'type': 'item_completed', 'item': item}}


V4 = '3f9b2c1d-7e4a-4b6c-9d8e-1a2b3c4d5e6f'
V4_OTHER = '8c0de4a1-1f2b-4c3d-9e8f-0a1b2c3d4e5f'
V7 = '01890a5d-ac96-774b-bcce-b302099a8057'


class PromptReportTest(unittest.TestCase):
    maxDiff = None
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.projects = self.home / '.claude/projects/-work'
        self.desktop = self.home / 'Library/Application Support/Claude/claude-code-sessions/account/org'
        self.codex = self.home / '.codex'

    def tearDown(self):
        self.tmp.cleanup()

    def desktop_session(self, sid, cli, title, created, prior=(), folder=None):
        folder = folder or self.desktop
        folder.mkdir(parents=True, exist_ok=True)
        (folder / f'{sid}.json').write_text(json.dumps({
            'sessionId': sid, 'cliSessionId': cli, 'priorCliSessionIds': list(prior),
            'title': title, 'createdAt': (START + created) * 1000}))

    def prompts(self):
        sessions = prompt_reports.analyse(self.home, START, END)
        return {(s.service, s.title or s.id): [p.text for p in s.prompts] for s in sessions}

    def test_claude_counts_only_what_the_person_typed(self):
        self.desktop_session('local_a', 'cli-a', 'Fix the thing', 0)
        write_jsonl(self.projects / 'cli-a.jsonl', [
            user(10, 'please fix the thing'),
            user(20, '<system-reminder>\nLinked sessions now\n</system-reminder>\nand test it'),
            user(30, '<system-reminder>\nThe user forked this\n</system-reminder>'),
            user(40, 'Another Claude session sent a message:\n<cross-session-message from-name="send-pr">\n'
                     'babysit https://example.com/pull/1\n</cross-session-message>', origin='peer'),
            user(50, '<task-notification>\n<task-id>b1</task-id>\n</task-notification>',
                 origin='task-notification'),
            user(60, 'squash', origin='plugin'),
            user(70, 'Stop hook feedback:\nUpdate your session title.', origin=None, isMeta=True),
            user(90, [{'type': 'tool_result', 'tool_use_id': 't1', 'content': 'ok'}], origin=None),
            user(100, [{'type': 'text', 'text': 'look at this'}, {'type': 'image', 'source': {}}]),
            user(110, '<bash-input>git status</bash-input><bash-stdout>clean</bash-stdout>'),
            queued(120, 'also rebase'),
            queued(130, '<agent-message from="a1">report</agent-message>', origin='peer'),
            {'type': 'assistant', 'timestamp': iso(140), 'message': {'content': [{'type': 'text', 'text': 'Done.'}]}},
        ])
        self.assertEqual(self.prompts(), {('claude', 'Fix the thing'): [
            'please fix the thing', 'and test it', 'look at this\n[image]', '! git status', 'also rebase']})

    def test_claude_briefs_written_by_sessions_are_not_counted(self):
        brief = 'Use project A1\n\nStop the tests leaving files in /tmp.'

        def opened(name, rows, created=0, prior=()):
            self.desktop_session(f'local_{name}', name, name, created, prior)
            write_jsonl(self.projects / f'{name}.jsonl', rows)

        write_jsonl(self.projects / 'parent.jsonl', [
            user(10, 'spawn a session to clean up the temporary files'),
            started(20, background='On Monday the `disk` was slow because of `/tmp/test-*` directories.',
                    prompt='Use project A1\n\nStop the tests leaving files in `/tmp`.'),
            started(25, prompt='rebase'),
            started(25, prompt='🚧?'),
            user(26, 'rebase'),
            user(27, 'load mcp__ccd_session__start_session first'),
        ])
        write_jsonl(self.projects / 'parent/subagents/agent-y.jsonl', [started(27, prompt='Review the patch.')])
        write_jsonl(self.projects / 'old-proposer.jsonl', [started(0, prompt='Make the build faster.')])
        age(self.projects / 'old-proposer.jsonl', 3 * 86400)
        write_jsonl(self.projects / 'expired-proposer.jsonl', [started(0, prompt='Make the tests faster.')])
        age(self.projects / 'expired-proposer.jsonl', 8 * 86400)
        write_jsonl(self.projects / 'handed-off.jsonl', [
            user(5, 'hand this off'),
            started(10, tool='mcp__ccd_session__hand_off_to_session', remaining_plan='Finish the docs.')])
        opened('child', [
            user(30, 'Background:\nOn Monday the disk was slow because of /tmp/test-* directories.\n\n' + brief),
            user(40, brief),
            user(50, 'is this done?'),
        ], created=29)
        opened('emoji', [user(30, '🚧?')])
        opened('from-proposal', [user(30, 'Make the build faster.')])
        opened('from-subagent', [user(30, 'Review the patch.')])
        opened('handed-to', [user(30, 'Finish the docs.')])
        opened('late-proposal', [user(30, 'Make the tests faster.')])
        opened('forked', [user(20, 'Finish the docs.'), user(32, 'Make the build faster.')], created=25)
        opened('resumed', [user(31, 'Make the build faster.')], prior=['resumed-old'])
        write_jsonl(self.projects / 'resumed-old.jsonl', [user(-100, 'the first prompt')])
        age(self.projects / 'resumed-old.jsonl', 1)
        write_jsonl(self.projects / 'terminal.jsonl', [user(30, brief)])
        self.assertEqual(self.prompts(), {
            ('claude', 'parent'): ['spawn a session to clean up the temporary files', 'rebase',
                                   'load mcp__ccd_session__start_session first'],
            ('claude', 'child'): [brief, 'is this done?'],
            ('claude', 'emoji'): ['🚧?'],
            ('claude', 'from-proposal'): [],
            ('claude', 'from-subagent'): [],
            ('claude', 'handed-off'): ['hand this off'],
            ('claude', 'handed-to'): [],
            ('claude', 'late-proposal'): ['Make the tests faster.'],
            ('claude', 'forked'): ['Finish the docs.'],
            ('claude', 'resumed'): ['Make the build faster.'],
            ('claude', 'terminal'): [brief],
        })

    def test_claude_forks_and_resumed_transcripts(self):
        self.desktop_session('local_parent', 'cli-p', 'Parent', 0)
        self.desktop_session('local_fork', 'cli-f2', 'Fork', 25, prior=['cli-f1'])
        self.desktop_session('local_gone', 'cli-gone', 'Set-aside session', 0,
                             folder=self.desktop.with_name('org.superseded.abc'))
        self.desktop_session('local_late', 'cli-late', 'Forked after the window', 7300)
        copied = user(10, 'first prompt')
        write_jsonl(self.projects / 'cli-p.jsonl', [copied, user(20, 'second prompt')])
        write_jsonl(self.projects / 'cli-f1.jsonl', [copied, user(30, 'asked in the fork')])
        write_jsonl(self.projects / 'cli-f2.jsonl', [user(40, 'asked after resuming')])
        write_jsonl(self.projects / 'cli-gone.jsonl', [user(45, 'in a session the app set aside')])
        write_jsonl(self.projects / 'cli-late.jsonl', [{**user(20, 'second prompt'), 'sessionId': 'cli-late'}])
        write_jsonl(self.projects / 'cli-p/subagents/agent-x.jsonl', [user(50, 'a subagent brief')])
        typed = user(60, 'typed in the terminal', sessionId='z-terminal')
        write_jsonl(self.projects / 'z-terminal.jsonl', [typed])
        write_jsonl(self.projects / 'a-terminal-fork.jsonl', [typed, user(70, 'typed in the terminal fork')])
        self.assertEqual(self.prompts(), {
            ('claude', 'Parent'): ['first prompt', 'second prompt'],
            ('claude', 'Fork'): ['asked in the fork', 'asked after resuming'],
            ('claude', 'cli-gone'): ['in a session the app set aside'],
            ('claude', 'z-terminal'): ['typed in the terminal'],
            ('claude', 'a-terminal-fork'): ['typed in the terminal fork'],
        })

    def test_claude_titles_from_transcripts(self):
        write_jsonl(self.projects / 'terminal.jsonl', [
            {'type': 'ai-title', 'aiTitle': 'First guess'},
            {'type': 'custom-title', 'customTitle': 'First choice'},
            {'type': 'custom-title', 'customTitle': 'Chosen title'},
            user(10, 'hello'),
            {'type': 'ai-title', 'aiTitle': 'Later guess'},
        ])
        write_jsonl(self.projects / 'guessed.jsonl', [{'type': 'ai-title', 'aiTitle': 'Guessed title'},
                                                      user(10, 'hi')])
        self.desktop_session('local_blank', 'cli-blank', '', 0)
        write_jsonl(self.projects / 'cli-blank.jsonl', [{'type': 'ai-title', 'aiTitle': 'Blank in the app'},
                                                        user(10, 'hey')])
        self.desktop_session('local_named', 'cli-named', 'Named in the app', 0)
        write_jsonl(self.projects / 'cli-named.jsonl', [{'type': 'custom-title', 'customTitle': 'Older name'},
                                                        user(10, 'yo')])
        self.assertEqual(self.prompts(), {('claude', 'Chosen title'): ['hello'], ('claude', 'Guessed title'): ['hi'],
                                          ('claude', 'Blank in the app'): ['hey'],
                                          ('claude', 'Named in the app'): ['yo']})

    def test_window_bounds(self):
        write_jsonl(self.projects / 'old.jsonl', [user(-7200, 'before the window'), user(-10, 'just before')])
        write_jsonl(self.projects / 'untouched.jsonl', [user(10, 'in a file last written before the window')])
        age(self.projects / 'untouched.jsonl', 1)
        self.desktop_session('local_copy', 'cli-copy', 'Copy', 100)
        write_jsonl(self.projects / 'cli-copy.jsonl', [user(50, 'copied from a transcript that is gone')])
        write_jsonl(self.projects / 'active.jsonl', [{**user(-10, 'before'), 'version': '2.1.181'},
                                                     user(0, 'at the start'),
                                                     user(7199, 'just before the end'),
                                                     user(7200, 'at the end')])
        write_jsonl(self.projects / 'later.jsonl', [user(7200, 'at the end'), user(7300, 'after')])
        write_jsonl(self.projects / 'dormant.jsonl', [user(-100, 'before'), {'type': 'assistant', 'timestamp': iso(7300)}])
        write_jsonl(self.projects / 'quiet.jsonl', [user(-10, 'before'),
                                                    {'type': 'assistant', 'timestamp': iso(5)}])
        self.assertEqual(self.prompts(), {('claude', 'active'): ['at the start', 'just before the end'], ('claude', 'quiet'): [],
                                          ('claude', 'Copy'): ['copied from a transcript that is gone']})
        counts = prompt_reports.render_markdown(prompt_reports.analyse(self.home, START, END), START, END, UTC,
                                                raw=False)['counts']
        self.assertIn(f'| 1 | 33.3% | Claude | Copy | `local_copy` | {prompt_reports.local(START + 50, UTC)} |', counts)

    def test_codex_counts_only_the_app_prompt_box(self):
        (self.codex).mkdir(parents=True)
        (self.codex / 'session_index.jsonl').write_text(
            json.dumps({'id': 'thread-1', 'thread_name': 'Old name'}) + '\n' +
            json.dumps({'id': 'thread-1', 'thread_name': 'Codex thread'}) + '\n')
        day = self.codex / 'sessions/2026/11/17'
        write_jsonl(day / 'rollout-1.jsonl', [
            meta(0, 'thread-1'),
            {'type': 'response_item', 'timestamp': iso(1), 'payload': {
                'type': 'message', 'role': 'user', 'content': [{'type': 'input_text', 'text': '# AGENTS.md'}]}},
            codex_message(10, 'Investigate the failing build'),
            codex_message(20, 'babysit https://example.com/pull/2', V7),
            codex_message(30, 'is this ready?\n', V4),
            codex_message(31, '<send_user_message_question_reply>\n[{"answer": "Codex only"}]\n'
                              '</send_user_message_question_reply>', '5e3f4a6b-7c8d-4e9f-a0b1-2c3d4e5f6a7b'),
        ])
        write_jsonl(day / 'rollout-1-fork-thread-2.jsonl', [
            meta(35, 'thread-2', forked_from_id='thread-1'),
            codex_message(35, 'is this ready?\n', V4),
            codex_message(40, 'in the fork', V4_OTHER),
        ])
        old = '6f1e2d3c-4b5a-4968-8776-655443322110'
        (self.codex / 'archived_sessions').mkdir(parents=True)
        (self.codex / 'archived_sessions/rollout-thread-7.jsonl.zst').write_bytes(zstd.compress(
            ''.join(json.dumps(row) + '\n' for row in [meta(-9000, 'thread-7'), codex_message(-8000, 'long ago', old)]).encode()))
        age(self.codex / 'archived_sessions/rollout-thread-7.jsonl.zst', 1000)
        write_jsonl(day / 'rollout-thread-8.jsonl', [
            meta(10, 'thread-8', forked_from_id='thread-7'),
            codex_message(10, 'long ago', old),
            codex_message(20, 'typed in the late fork', '7a2b3c4d-5e6f-4a7b-8c9d-0e1f2a3b4c5d'),
        ])
        write_jsonl(self.codex / 'archived_sessions/rollout-exec.jsonl', [
            meta(0, 'thread-4', source='exec'),
            codex_message(10, 'Fix the failing build'),
            codex_message(20, 'how is it going?', '2b7e4f1c-9a3d-4e2f-8c1b-7d6e5f4a3b2c'),
        ])
        archived = self.codex / 'archived_sessions/rollout-5.jsonl.zst'
        archived.write_bytes(zstd.compress(''.join(json.dumps(row) + '\n' for row in [
            meta(0, 'thread-5'),
            {**codex_message(10, 'from a compressed transcript', '3c1d2e4f-5a6b-4c7d-8e9f-0a1b2c3d4e5f'),
             }]).encode()))
        image = codex_message(20, '', '4d2e3f5a-6b7c-4d8e-9fa0-1b2c3d4e5f6a')
        image['payload']['item']['content'] = [{'type': 'text', 'text': 'see this'}, {'type': 'local_image', 'path': 'x.png'}]
        write_jsonl(self.codex / 'archived_sessions/rollout-6.jsonl', [meta(0, 'thread-6'), image])
        helper = 0
        for kind, source in [('subagent', {'subagent': {'thread_spawn': {'parent_thread_id': 'thread-1'}}}),
                             ('subagent', 'vscode'), ('guardian_review', {'subagent': 'guardian_review'})]:
            helper += 1
            write_jsonl(self.codex / f'archived_sessions/rollout-helper-{helper}.jsonl', [
                meta(0, f'helper-{helper}', kind, source),
                codex_message(10, '<codex_delegation>do this</codex_delegation>',
                              f'9e8d7c6b-5a49-4382-9716-0504030201{helper:02}'),
            ])
        write_jsonl(self.codex / 'archived_sessions/rollout-created.jsonl', [
            meta(0, 'thread-9', 'agent_created_thread'),
            codex_message(1, 'Fix the build, written by an agent'),
            codex_message(30, 'squash', '1f2e3d4c-5b6a-4798-8a9b-0c1d2e3f4a5b'),
        ])
        self.assertEqual(self.prompts(), {
            ('codex', 'Codex thread'): ['is this ready?'],
            ('codex', 'thread-2'): ['in the fork'],
            ('codex', 'thread-8'): ['typed in the late fork'],
            ('codex', 'thread-4'): ['how is it going?'],
            ('codex', 'thread-5'): ['from a compressed transcript'],
            ('codex', 'thread-6'): ['see this\n[local_image]'],
            ('codex', 'thread-9'): ['squash'],
        })

    def test_codex_thread_resumed_in_a_second_file(self):
        write_jsonl(self.codex / 'archived_sessions/rollout-r1.jsonl', [
            meta(5, 'thread-r'), {'type': 'event_msg', 'timestamp': iso(8), 'payload': {'type': 'task_started'}}])
        write_jsonl(self.codex / 'sessions/rollout-r2.jsonl', [
            meta(20, 'thread-r'), codex_message(25, 'after resuming', V4)])
        [thread] = prompt_reports.analyse(self.home, START, END)
        self.assertEqual((thread.first, thread.last), (START + 5, START + 25))
        self.assertEqual([p.text for p in thread.prompts], ['after resuming'])

    def test_a_line_still_being_written_is_skipped(self):
        write_jsonl(self.projects / 'live.jsonl', [
            user(10, [{'type': 'text', 'text': 'kept'}]),
            {'type': 'last-prompt', 'lastPrompt': 'kept'},
        ])
        with (self.projects / 'live.jsonl').open('a') as f:
            f.write('{"type": "user", "timestamp": "')
        self.assertEqual(self.prompts(), {('claude', 'live'): ['kept']})

    def test_malformed_transcripts_fail_loudly(self):
        rows = lambda *rows: ''.join(json.dumps(row) + '\n' for row in rows)
        unversioned = user(10, 'x')
        del unversioned['version']
        cases = {
            '.claude/projects/-work/broken.jsonl': ('not JSON\n' + rows(user(10, 'after')), 'Expecting value'),
            '.claude/projects/-work/array.jsonl': ('[1, 2]\n', 'a line holds a JSON list, not an object'),
            '.claude/projects/-work/clock.jsonl': (rows({**user(10, 'x'), 'timestamp': 'yesterday'}),
                                                  'Invalid isoformat'),
            '.claude/projects/-work/timeless.jsonl': (rows(queued(10, 'x') | {'timestamp': None}),
                                                     'attachment record has no timestamp'),
            '.claude/projects/-work/old.jsonl': (rows({**user(10, 'x', origin=None), 'version': '2.1.181'}),
                                                'Claude Code 2.1.181 did not record'),
            '.claude/projects/-work/unversioned.jsonl': (rows(unversioned), "'version'"),
            '.codex/sessions/rollout-kind.jsonl': (rows(meta(0, 'thread-y', 'new_kind')), "'new_kind'"),
            '.codex/sessions/rollout-headless.jsonl': (rows(codex_message(10, 'no meta', V4)),
                                                       'the first record is not session_meta'),
            '.codex/sessions/rollout-client.jsonl': (rows(meta(0, 'thread-x'),
                                                          codex_message(10, 'odd identifier', 'not-a-uuid')),
                                                     'badly formed hexadecimal UUID string'),
        }
        for relative, (text, message) in cases.items():
            with self.subTest(relative):
                path = self.home / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
                try:
                    with self.assertRaisesRegex(Exception, message):
                        prompt_reports.analyse(self.home, START, END)
                finally:
                    path.unlink()

    def test_times_follow_daylight_saving(self):
        pacific = ZoneInfo('America/Los_Angeles')
        before, after = '2026-10-31T12:00', '2026-11-02T12:00'
        self.assertEqual(prompt_reports.parse_time(after, pacific) - prompt_reports.parse_time(before, pacific),
                         2 * 86400 + 3600)
        self.assertEqual(prompt_reports.local(prompt_reports.parse_time(before, pacific), pacific),
                         '2026-10-31 12:00')
        try:
            with mock.patch.dict(os.environ, {'TZ': 'America/Los_Angeles'}):
                time.tzset()
                for moment in (before, after):
                    self.assertEqual(prompt_reports.local(prompt_reports.parse_time(moment, None), None),
                                     moment.replace('T', ' '))
        finally:
            time.tzset()

    def test_histogram_bins(self):
        sessions = [prompt_reports.Session('claude', str(n), prompts=[None] * n) for n in (0, 0, 1, 3, 4, 5, 8, 9, 29)]
        [(_, headings, each), (_, _, cumulative)] = prompt_reports.histograms(sessions)
        self.assertEqual(headings, ('Prompts', 'Sessions'))
        self.assertEqual([(label, n, part) for label, n, part, _ in each], [
            ('0', 2, '22.2%'), ('1', 1, '11.1%'), ('2', 0, '0.0%'), ('3–4', 2, '22.2%'), ('5–8', 2, '22.2%'),
            ('9–16', 1, '11.1%'), ('17–32', 1, '11.1%')])
        self.assertEqual([row[3] for row in each[:2]], ['2 sessions with 0 prompts', '1 session with 1 prompt'])
        self.assertEqual([label for label, _, _, _ in cumulative], ['0', *(f'0–{n}' for n in range(1, 30))])
        self.assertEqual([n for _, n, _, _ in cumulative],
                         [2, 3, 3, 4, 5, 6, 6, 6, 7, 8, *[8] * 19, 9])
        self.assertEqual(cumulative[1][2:], ('33.3%', '3 sessions with at most 1 prompt'))
        self.assertEqual(cumulative[-1][2], '100.0%')
        self.assertEqual([rows for _, _, rows in prompt_reports.histograms([])], [[], []])

    def test_chronological_heading(self):
        self.assertEqual([prompt_reports.chronological(0, seconds).split(' period')[0] for seconds in
                          (14 * 86400, 7.5 * 86400, 86400, 7200, 3456, 30)],
                         ['All prompts typed over a 14-day', 'All prompts typed over a 7.5-day',
                          'All prompts typed over a 1-day', 'All prompts typed over a 2-hour',
                          'All prompts typed over a 57.6-minute', 'All prompts typed over a 0.5-minute'])

    def test_share(self):
        self.assertEqual([prompt_reports.share(n, 3000) for n in (0, 1, 3, 1500, 2997, 2999, 3000)],
                         ['0.0%', '<0.1%', '0.1%', '50.0%', '99.9%', '>99.9%', '100.0%'])
        self.assertEqual(prompt_reports.share(0, 0), '–')

    def test_length_histogram(self):
        prompts = [prompt_reports.Prompt(0, 'x' * n, n, None) for n in (1, 2, 3, 3, 5, 8, 9)]
        caption, headings, rows = prompt_reports.length_histogram(prompts)
        self.assertEqual((caption, headings),
                         ('Prompts typed with at most each number of characters', ('Characters', 'Prompts')))
        self.assertEqual([(label, n, part) for label, n, part, _ in rows], [
            ('1', 1, '14.3%'), ('1–2', 2, '28.6%'), ('1–4', 4, '57.1%'), ('1–8', 6, '85.7%'), ('1–16', 7, '100.0%')])
        self.assertEqual(rows[0][3], '1 prompt with at most 1 character')
        self.assertEqual([label for label, _, _, _ in prompt_reports.length_histogram(prompts[:1])[2]], ['1'])
        self.assertEqual(prompt_reports.length_histogram([])[2], [])

    def test_empty_window(self):
        for render in (prompt_reports.render_markdown, prompt_reports.render_html):
            reports = render([], START, END, UTC, raw=False)
            self.assertIn('0 prompts in 0 sessions (0 Claude, 0 Codex). 0 sessions had no typed prompts.',
                          reports['counts'])
        idle = prompt_reports.Session('claude', 'idle', first=START, last=START)
        self.assertIn('| 0 | – | Claude | (untitled) | `idle` |',
                      prompt_reports.render_markdown([idle], START, END, UTC, raw=False)['counts'])

    def test_reports(self):
        self.desktop_session('local_a', 'cli-a', 'Title | with a "bar" & a \\', 0)
        write_jsonl(self.projects / 'cli-a.jsonl', [user(10, 'run ```python``` here'), user(20, 'second'),
                                                    user(30, 'squash'), user(40, 'squash')])
        write_jsonl(self.projects / 'idle.jsonl', [{'type': 'assistant', 'timestamp': iso(5)}])
        sessions = prompt_reports.analyse(self.home, START, END)
        reports = prompt_reports.render_markdown(sessions, START, END, UTC, raw=False)
        counts, listing, lengths = reports['counts'], reports['prompts'], reports['by-length']
        self.assertIn('4 prompts in 2 sessions (2 Claude, 0 Codex). 1 session had no typed prompts.', counts)
        self.assertIn('| 0 | 1 | 50.0% | ' + '█' * 40 + ' |\n| 1 | 0 | 0.0% |  |\n| 2 | 0 | 0.0% |  |\n'
                      '| 3–4 | 1 | 50.0% | ' + '█' * 40 + ' |\n', counts)
        self.assertIn('## Sessions with at most each number of typed prompts\n\n| Prompts | Sessions | Share | |\n'
                      '|--:|--:|--:|--|\n| 0 | 1 | 50.0% | ' + '█' * 20 + ' |\n| 0–1 | 1 | 50.0% | ' + '█' * 20 +
                      ' |\n| 0–2 | 1 | 50.0% | ' + '█' * 20 + ' |\n| 0–3 | 1 | 50.0% | ' + '█' * 20 +
                      ' |\n| 0–4 | 2 | 100.0% | ' + '█' * 40 + ' |\n', counts)
        self.assertIn('| 4 | 100.0% | Claude | Title \\| with a "bar" & a \\ | `local_a` |', counts)
        self.assertIn('| 0 | 0.0% | Claude | (untitled) | `idle` |', counts)
        self.assertLess(counts.index('`local_a`'), counts.index('`idle`'))
        when = re.sub(r'([-:])', r'\\\1', prompt_reports.local(START + 10, UTC))
        self.assertIn(f'[ℹ️](counts.md "{when}\\, Claude\\: Title \\| with a \\"bar\\" \\& a \\\\")'
                      '\n\n````\nrun ```python``` here\n````', listing)
        self.assertLess(listing.index('run ```python'), listing.index('second'))
        self.assertIn('3 distinct prompts, of 4 prompts.\n\n## Prompts typed with at most each number of characters\n\n'
                      '| Characters | Prompts | Share | |\n|--:|--:|--:|--|\n', lengths)
        self.assertIn('| 1–32 | 4 | 100.0% | ' + '█' * 40 + ' |\n\n## Distinct prompts\n\n', lengths)
        self.assertIn('×2\n\n```\nsquash\n```\n\n```\nsecond\n```\n\n````\nrun ```python``` here\n````', lengths)
        raw = prompt_reports.render_markdown(sessions, START, END, UTC, raw=True)
        self.assertEqual(raw['prompts'], '# All prompts typed over a 2-hour period, in chronological order\n\n'
                         '````\nrun ```python``` here\n````\n\n```\nsecond\n```\n\n```\nsquash\n```\n\n```\nsquash\n```\n')
        self.assertTrue(raw['counts'].startswith('## Sessions by number of typed prompts\n\n| Prompts | Sessions |'))
        self.assertIn('## Sessions with at most each number of typed prompts', raw['counts'])
        self.assertTrue(raw['by-length'].startswith('## Prompts typed with at most each number of characters\n'))
        self.assertTrue(raw['by-length'].endswith(
            ' |\n\n×2\n\n```\nsquash\n```\n\n```\nsecond\n```\n\n````\nrun ```python``` here\n````\n'))
        for text in raw.values():
            for metadata in ('# Prompts typed,', '# Prompts typed into', 'ℹ️', 'Title', 'local_a', 'distinct',
                             prompt_reports.local(START, UTC)[:10]):
                self.assertNotIn(metadata, text)

    def test_html_reports(self):
        self.desktop_session('local_a', 'cli-a', 'Fix <b>bold</b> & more', 0)
        write_jsonl(self.projects / 'cli-a.jsonl', [user(10, 'is <script>alert(1)</script> safe?\nsecond line'),
                                                    user(20, 'next'), user(30, 'next')])
        write_jsonl(self.projects / 'idle.jsonl', [{'type': 'assistant', 'timestamp': iso(5)}])
        sessions = prompt_reports.analyse(self.home, START, END)
        reports = prompt_reports.render_html(sessions, START, END, UTC, raw=False)
        counts, listing, lengths = reports['counts'], reports['prompts'], reports['by-length']
        for page in reports.values():
            self.assertTrue(page.startswith('<!doctype html>'))
            self.assertNotIn('<script>', page)
            self.assertNotIn('<b>', page)
        self.assertIn('<p>3 prompts in 2 sessions (2 Claude, 0 Codex). 1 session had no typed prompts.', counts)
        self.assertIn('<tr id="local_a"><td class="number">3<td class="number">100.0%<td>Claude'
                      '<td>Fix &lt;b&gt;bold&lt;/b&gt; &amp; more<td><code>local_a</code>', counts)
        self.assertLess(counts.index('id="local_a"'), counts.index('id="idle"'))
        self.assertIn('<tr title="1 session with 0 prompts"><td class="number">0<td class="number">1'
                      '<td class="number">50.0%<td class="bar" aria-hidden="true"><span style="width: 100.0%"></span>',
                      counts)
        self.assertIn('<tr title="0 sessions with 1 prompt"><td class="number">1<td class="number">0'
                      '<td class="number">0.0%<td class="bar" aria-hidden="true"><span style="width: 0.0%"></span>',
                      counts)
        self.assertIn('<caption>Sessions with at most each number of typed prompts</caption>', counts)
        self.assertIn('<tr title="1 session with at most 2 prompts"><td class="number">0–2<td class="number">1'
                      '<td class="number">50.0%<td class="bar" aria-hidden="true"><span style="width: 50.0%"></span>',
                      counts)
        self.assertIn('<div class="entry"><pre>is &lt;script&gt;alert(1)&lt;/script&gt; safe?\nsecond line</pre>'
                      '<a class="info" href="counts.html#local_a">ℹ️<span class="tip">'
                      f'{prompt_reports.local(START + 10, UTC)}, Claude: Fix &lt;b&gt;bold&lt;/b&gt; &amp; more</span></a>',
                      listing)
        self.assertLess(listing.index('safe?'), listing.index('<pre>next</pre>'))
        self.assertIn('2 distinct prompts, of 3 prompts.', lengths)
        self.assertIn('<thead><tr><th>Characters<th>Prompts<th>Share<td></thead>', lengths)
        self.assertIn('<tr title="2 prompts with at most 4 characters"><td class="number">1–4<td class="number">2'
                      '<td class="number">66.7%', lengths)
        self.assertIn('<pre>next</pre><span class="info">×2</span></div>\n<div class="entry"><pre>is &lt;script', lengths)
        self.assertIn('second line</pre><span class="info"></span>', lengths)
        raw = prompt_reports.render_html(sessions, START, END, UTC, raw=True)
        self.assertEqual(sorted(raw), ['by-length', 'counts', 'prompts'])
        when = prompt_reports.local(START, UTC)[:10]
        self.assertEqual([page.split('<title>')[1].split('</title>')[0] for page in raw.values()],
                         ['Prompts typed into each session',
                          'All prompts typed over a 2-hour period, in chronological order',
                          'Prompts typed, shortest first'])
        for page in raw.values():
            for metadata in ('<p>', 'ℹ️', 'Claude', 'local_a', 'idle', 'bold', when):
                self.assertNotIn(metadata, page)
        self.assertIn('<h1>All prompts typed over a 2-hour period, in chronological order</h1>', raw['prompts'])
        self.assertNotIn('<h1>', raw['counts'] + raw['by-length'])
        self.assertNotIn('×', raw['counts'] + raw['prompts'])
        self.assertIn('</h1>\n<pre>is &lt;script&gt;alert(1)&lt;/script&gt; safe?\nsecond line</pre>\n'
                      '<pre>next</pre>\n<pre>next</pre>\n\n</html>', raw['prompts'])
        self.assertIn('</style>\n\n<table class="histogram">\n<caption>Sessions by number of typed prompts</caption>',
                      raw['counts'])
        self.assertIn('<caption>Sessions with at most each number of typed prompts</caption>', raw['counts'])
        self.assertIn('</style>\n\n<table class="histogram">\n<caption>Prompts typed with at most each number of '
                      'characters</caption>', raw['by-length'])
        self.assertIn('</table>\n<div class="entry"><pre>next</pre><span class="info">×2</span></div>\n'
                      '<div class="entry"><pre>is &lt;script', raw['by-length'])

    def test_main_writes_the_reports(self):
        write_jsonl(self.projects / 'cli-a.jsonl', [user(10, 'hello')])
        output = self.home / 'out'
        start = datetime.fromtimestamp(START, UTC).strftime('%Y-%m-%dT%H:%M:%S')
        end = datetime.fromtimestamp(END, UTC).strftime('%Y-%m-%dT%H:%M:%S')
        with redirect_stdout(io.StringIO()) as printed:
            prompt_reports.main(['--start', start, '--end', end, '--timezone', 'UTC',
                                 '--home', str(self.home), '--output', str(output)])
        [report] = output.iterdir()
        self.assertEqual(sorted(p.name for p in report.iterdir()), ['by-length.md', 'counts.md', 'prompts.md'])
        self.assertIn('hello', (report / 'prompts.md').read_text())
        self.assertTrue(printed.getvalue().startswith('1 prompt in 1 session'))
        with redirect_stdout(io.StringIO()):
            prompt_reports.main(['--days', '1', '--home', str(self.home), '--output', str(output)])
        with redirect_stdout(io.StringIO()):
            prompt_reports.main(['--days', '1', '--format', 'html', '--home', str(self.home),
                                 '--output', str(self.home / 'html')])
        [report] = (self.home / 'html').iterdir()
        self.assertEqual(sorted(p.name for p in report.iterdir()), ['by-length.html', 'counts.html', 'prompts.html'])
        with redirect_stdout(io.StringIO()):
            prompt_reports.main(['--days', '1', '--raw', '--home', str(self.home), '--output', str(self.home / 'raw')])
        [report] = (self.home / 'raw').iterdir()
        self.assertEqual(sorted(p.name for p in report.iterdir()), ['by-length.md', 'counts.md', 'prompts.md'])
        self.assertEqual((report / 'prompts.md').read_text(),
                         '# All prompts typed over a 1-day period, in chronological order\n\n```\nhello\n```\n')
        self.assertEqual(len(list(output.iterdir())), 2)
        with self.assertRaises(SystemExit), redirect_stdout(io.StringIO()), \
                mock.patch('sys.stderr', io.StringIO()):
            prompt_reports.main(['--start', end, '--end', start, '--home', str(self.home)])


if __name__ == '__main__':
    unittest.main()
