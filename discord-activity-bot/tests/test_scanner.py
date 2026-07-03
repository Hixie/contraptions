from __future__ import annotations

import unittest
from datetime import datetime, timezone

from discord_activity_bot.scanner import (
    ActivityScanner,
    ScanOptions,
    bucket_index,
    parse_discord_time,
    snowflake_time,
)


class TimeParsingTests(unittest.TestCase):
    def test_parse_discord_time_uses_utc(self) -> None:
        parsed = parse_discord_time("2026-07-02T12:34:56.000000+00:00")
        self.assertEqual(parsed, datetime(2026, 7, 2, 12, 34, 56, tzinfo=timezone.utc))


class BucketIndexTests(unittest.TestCase):
    def test_bucket_index_places_timestamps_in_range(self) -> None:
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, tzinfo=timezone.utc)
        timestamp = datetime(2026, 7, 2, 12, tzinfo=timezone.utc)
        self.assertEqual(bucket_index(timestamp, start, end, 86400), 1)

    def test_bucket_index_keeps_end_in_last_bucket(self) -> None:
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, tzinfo=timezone.utc)
        self.assertEqual(bucket_index(end, start, end, 86400), 2)


class SnowflakeTimeTests(unittest.TestCase):
    def test_snowflake_time_reads_creation_time(self) -> None:
        self.assertEqual(
            snowflake_time("1232452732963655720"),
            datetime(2024, 4, 23, 22, 7, 9, 403000, tzinfo=timezone.utc),
        )


class ScannerWarningTests(unittest.TestCase):
    def test_empty_history_with_last_message_adds_permission_warning(self) -> None:
        class EmptyHistoryClient:
            def request(self, method, path, *, params=None, json_body=None):
                if path == "/guilds/guild/channels":
                    return [
                        {
                            "id": "channel",
                            "type": 0,
                            "name": "general",
                            "position": 0,
                            "last_message_id": "message",
                        }
                    ]
                if path == "/channels/channel/messages":
                    return []
                raise AssertionError((method, path, params, json_body))

        options = ScanOptions(
            guild_id="guild",
            start=datetime(2026, 7, 1, tzinfo=timezone.utc),
            end=datetime(2026, 7, 2, tzinfo=timezone.utc),
            bucket_seconds=86400,
            include_threads=False,
        )

        result = ActivityScanner(EmptyHistoryClient(), options).scan()

        self.assertEqual(result.channels[0].message_count, 0)
        self.assertIn("Read Message History", result.warnings[0])

    def test_message_stats_include_participants_and_message_ages(self) -> None:
        class MessageClient:
            def request(self, method, path, *, params=None, json_body=None):
                if path == "/guilds/guild/channels":
                    return [
                        {
                            "id": "1232452732963655720",
                            "type": 0,
                            "name": "general",
                            "position": 0,
                            "last_message_id": "message-2",
                        }
                    ]
                if path == "/channels/1232452732963655720/messages":
                    if params and params.get("before"):
                        return []
                    return [
                        {
                            "id": "message-2",
                            "timestamp": "2026-07-02T12:00:00+00:00",
                            "author": {"id": "user-a", "username": "Ada"},
                        },
                        {
                            "id": "message-1",
                            "timestamp": "2026-07-02T06:00:00+00:00",
                            "author": {"id": "user-b", "username": "Grace"},
                        },
                        {
                            "id": "message-0",
                            "timestamp": "2026-07-02T01:00:00+00:00",
                            "author": {"id": "user-a", "username": "Ada"},
                        },
                    ]
                raise AssertionError((method, path, params, json_body))

        options = ScanOptions(
            guild_id="guild",
            start=datetime(2026, 7, 2, tzinfo=timezone.utc),
            end=datetime(2026, 7, 3, tzinfo=timezone.utc),
            bucket_seconds=86400,
            include_threads=False,
        )

        result = ActivityScanner(MessageClient(), options).scan()
        channel = result.channels[0]

        self.assertEqual(channel.message_count, 3)
        self.assertEqual(len(channel.participant_counts), 2)
        self.assertEqual(channel.participant_counts["user-a"], 2)
        self.assertEqual(channel.participant_labels["user-b"], "Grace")
        self.assertEqual(
            channel.first_message_at,
            datetime(2026, 7, 2, 1, tzinfo=timezone.utc),
        )
        self.assertEqual(
            channel.last_message_at,
            datetime(2026, 7, 2, 12, tzinfo=timezone.utc),
        )

    def test_threads_are_collapsed_into_parent_channels(self) -> None:
        class ThreadClient:
            def request(self, method, path, *, params=None, json_body=None):
                if path == "/guilds/guild/channels":
                    return [
                        {
                            "id": "1232452732963655720",
                            "type": 0,
                            "name": "general",
                            "position": 0,
                        }
                    ]
                if path == "/guilds/guild/threads/active":
                    return {
                        "threads": [
                            {
                                "id": "1232452732963655721",
                                "parent_id": "1232452732963655720",
                                "name": "topic",
                                "type": 11,
                            }
                        ]
                    }
                if path == "/channels/1232452732963655720/threads/archived/public":
                    return {"threads": [], "has_more": False}
                if path == "/channels/1232452732963655720/messages":
                    if params and params.get("before"):
                        return []
                    return [
                        {
                            "id": "parent-message",
                            "timestamp": "2026-07-02T01:00:00+00:00",
                            "author": {"id": "user-a", "username": "Ada"},
                        }
                    ]
                if path == "/channels/1232452732963655721/messages":
                    if params and params.get("before"):
                        return []
                    return [
                        {
                            "id": "thread-message-2",
                            "timestamp": "2026-07-02T03:00:00+00:00",
                            "author": {"id": "user-b", "username": "Grace"},
                        },
                        {
                            "id": "thread-message-1",
                            "timestamp": "2026-07-02T02:00:00+00:00",
                            "author": {"id": "user-b", "username": "Grace"},
                        },
                    ]
                raise AssertionError((method, path, params, json_body))

        options = ScanOptions(
            guild_id="guild",
            start=datetime(2026, 7, 2, tzinfo=timezone.utc),
            end=datetime(2026, 7, 3, tzinfo=timezone.utc),
            bucket_seconds=86400,
        )

        result = ActivityScanner(ThreadClient(), options).scan()

        self.assertEqual(len(result.channels), 1)
        self.assertEqual(result.channels[0].path, "#general")
        self.assertEqual(result.channels[0].message_count, 3)
        self.assertEqual(result.channels[0].thread_count, 1)
        self.assertEqual(result.channels[0].participant_counts["user-b"], 2)


if __name__ == "__main__":
    unittest.main()
