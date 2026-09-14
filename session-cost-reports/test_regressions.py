"""Regression coverage for session history and accounting."""
import json
from pathlib import Path
import tempfile
import unittest

import claude_reader
import codex_reader
from reporting import WEEK, previous_window


A = "a" * 36
B = "b" * 36


def event(kind, ts, payload):
    return {"k": kind, "t": ts, "o": ts, "p": payload}


def file(sid, events):
    return {
        "path": sid + ".jsonl",
        "meta": {"id": sid, "session_id": sid, "timestamp": 1},
        "events": events,
    }


def read(files):
    metadata = [{"id": f["meta"]["id"], "thread_source": "user"}
                for f in files]
    return codex_reader.read({"metadata": metadata, "files": files},
                             1, 100, {}, {})[0]


def tokens(scale=1):
    return {"input_tokens": 100 * scale, "output_tokens": 10 * scale,
            "total_tokens": 110 * scale}


def modern(ts=20):
    return event("token_usage_record", ts, {
        "response_id": "response-1", "thread_id": A, "session_id": A,
        "turn_id": "turn-1", "usage": tokens(),
    })


def count(ts, total_scale=1):
    return event("token_count", ts, {
        "total_token_usage": tokens(total_scale), "last_token_usage": tokens(),
    })


class QuotaCounterexamples(unittest.TestCase):
    def test_cluster_keeps_earliest_anchor_even_when_observed_second(self):
        start = 1_000_000
        observations = [
            {"service": "codex", "observed": start + 100,
             "reset": start + WEEK + 30, "duration": WEEK},
            {"service": "codex", "observed": start + 200,
             "reset": start + WEEK, "duration": WEEK},
        ]
        window = previous_window(observations, "codex", start + WEEK + 60)
        self.assertEqual((window["start"], window["end"]),
                         (start, start + WEEK))


class RequestIdentityCounterexamples(unittest.TestCase):
    def test_independent_legacy_sessions_without_turn_ids(self):
        files = [file(sid, [
            event("turn_context", 10, {"model": "gpt-6-astra"}),
            count(20),
        ]) for sid in (A, B)]
        self.assertEqual(len(read(files)), 2)

    def test_one_modern_record_does_not_match_two_distinct_requests(self):
        records = read([file(A, [
            event("turn_context", 10,
                  {"turn_id": "turn-1", "model": "gpt-6-astra"}),
            modern(), count(21), count(30, total_scale=2),
        ])])
        self.assertEqual(len(records), 2)

class ExplicitTitleEvidenceCounterexamples(unittest.TestCase):
    def test_last_auto_title_is_chronological_across_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            projects, desktop = root / 'projects', root / 'desktop'
            projects.mkdir()
            desktop.mkdir()
            events = [
                {'timestamp': 10, 'type': 'assistant', 'message': {
                    'id': 'r', 'usage': {'input_tokens': 1}, 'model': 'claude-opus-5'}},
                {'type': 'ai-title', 'timestamp': 30, 'aiTitle': 'Later automatic'},
            ]
            (projects / (A + '.jsonl')).write_text(''.join(json.dumps(e) + '\n' for e in events))
            _, names, _ = claude_reader.read(projects, desktop, 1, 100, 100,
                [(5, A, 'Earlier automatic', 'auto')])
            self.assertEqual(names[A][0], 'Later automatic')

    def test_known_tool_title_is_not_a_user_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            projects, desktop = root / 'projects', root / 'desktop'
            projects.mkdir()
            desktop.mkdir()
            events = [
                {'timestamp': 10, 'type': 'assistant', 'message': {
                    'id': 'r', 'usage': {'input_tokens': 1}, 'model': 'claude-opus-5'}},
                {'type': 'custom-title', 'timestamp': 11, 'customTitle': 'Agent assigned'},
                {'type': 'custom-title', 'timestamp': 12, 'customTitle': 'User chose this'},
            ]
            (projects / (A + '.jsonl')).write_text(''.join(json.dumps(e) + '\n' for e in events))
            _, names, _ = claude_reader.read(projects, desktop, 1, 100, 100, [
                (11, A, 'Agent assigned', 'tool'), (12, A, 'User chose this', 'user')])
            self.assertEqual(names[A][0], 'User chose this')

    def test_first_user_title_is_available_only_in_desktop_log(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp)
            projects, desktop = root / "projects", root / "desktop"
            projects.mkdir()
            desktop.mkdir()
            (projects / (A + ".jsonl")).write_text(json.dumps({
                "timestamp": 50, "type": "assistant", "sessionId": A,
                "message": {"id": "response-1", "model": "claude-opus-5",
                            "usage": {"input_tokens": 1},
                            "content": [{"type": "text", "text": "Done"}],
                            "stop_reason": "end_turn"},
            }) + "\n")
            (desktop / "session.json").write_text(json.dumps({
                "sessionId": A, "createdAt": 1, "lastActivityAt": 50,
                "title": "Later user title", "titleSource": "user",
            }))
            _, names, _ = claude_reader.read(projects, desktop, 1, 100, 100, [
                (10, A, "First user title", "user"),
                (20, A, "Later user title", "user"),
            ])
            self.assertEqual(names[A][0], "First user title")


class ResumedSubagentCounterexamples(unittest.TestCase):
    def test_each_subagent_invocation_inherits_its_enclosing_parent_turn(self):
        def assistant(ts, rid, content, stop):
            return {"type": "assistant", "timestamp": ts, "sessionId": A,
                    "message": {"id": rid, "model": "claude-opus-5",
                                "usage": {"input_tokens": 100},
                                "content": content, "stop_reason": stop}}

        def tool(ts, tool_id):
            return assistant(ts, "request-" + tool_id, [
                {"type": "tool_use", "id": tool_id, "name": "Agent", "input": {}}
            ], "tool_use")

        def result(ts, tool_id):
            return {"type": "user", "timestamp": ts, "sessionId": A,
                    "toolUseResult": {"agentId": "child"},
                    "message": {"content": [
                        {"type": "tool_result", "tool_use_id": tool_id}
                    ]}}

        def final(ts, rid, text):
            return assistant(ts, rid, [{"type": "text", "text": text}], "end_turn")

        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as tmp:
            root = Path(tmp)
            projects, desktop = root / "projects", root / "desktop"
            child = projects / A / "subagents"
            child.mkdir(parents=True)
            desktop.mkdir()
            parent_events = [
                tool(20, "spawn-1"), result(40, "spawn-1"),
                final(50, "parent-final-1", "Done\n🧱 0x5E"),
                tool(70, "resume-2"), result(90, "resume-2"),
                final(100, "parent-final-2", "Done\n🪻 0x5E"),
            ]
            child_events = [final(30, "child-1", "Reviewed."),
                            final(80, "child-2", "Reviewed again.")]
            (projects / (A + ".jsonl")).write_text("".join(
                json.dumps(e) + "\n" for e in parent_events))
            (child / "agent-child.jsonl").write_text("".join(
                json.dumps(e) + "\n" for e in child_events))
            records, _, _ = claude_reader.read(projects, desktop, 1, 200, 200, [])
            child_states = {r["response_id"]: r["footer"] for r in records
                            if r["subagent"]}
            self.assertEqual(child_states, {"child-1": "🧱", "child-2": "🪻"})


if __name__ == "__main__":
    unittest.main()
