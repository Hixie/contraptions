from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone

from discord_activity_bot.discord_api import DiscordApiError
from discord_activity_bot.raw_snapshot import (
    RecordingDiscordClient,
    ReplayDiscordClient,
    load_raw_snapshot,
    save_raw_snapshot,
)
from discord_activity_bot.scanner import ActivityScanner, ScanOptions, discord_timestamp


class RawSnapshotTests(unittest.TestCase):
    def test_recorded_success_response_replays_without_client(self) -> None:
        class FakeClient:
            def request(self, method, path, *, params=None, json_body=None):
                return [{"id": "message"}]

        options = ScanOptions(
            guild_id="guild",
            start=datetime(2026, 7, 1, tzinfo=timezone.utc),
            end=datetime(2026, 7, 2, tzinfo=timezone.utc),
            bucket_seconds=86400,
        )
        recorder = RecordingDiscordClient(FakeClient())

        self.assertEqual(
            recorder.request("GET", "/channels/channel/messages", params={"limit": 100}),
            [{"id": "message"}],
        )
        replay = ReplayDiscordClient(recorder.snapshot(options))

        self.assertEqual(
            replay.request("GET", "/channels/channel/messages", params={"limit": 100}),
            [{"id": "message"}],
        )

    def test_recorded_error_replays_as_api_error(self) -> None:
        class ErrorClient:
            def request(self, method, path, *, params=None, json_body=None):
                raise DiscordApiError(403, "missing access", path)

        options = ScanOptions(
            guild_id="guild",
            start=datetime(2026, 7, 1, tzinfo=timezone.utc),
            end=datetime(2026, 7, 2, tzinfo=timezone.utc),
            bucket_seconds=86400,
        )
        recorder = RecordingDiscordClient(ErrorClient())
        with self.assertRaises(DiscordApiError):
            recorder.request("GET", "/channels/channel/messages", params={"limit": 100})
        replay = ReplayDiscordClient(recorder.snapshot(options))

        with self.assertRaises(DiscordApiError):
            replay.request("GET", "/channels/channel/messages", params={"limit": 100})

    def test_snapshot_round_trips_through_json_file(self) -> None:
        class FakeClient:
            def request(self, method, path, *, params=None, json_body=None):
                return {"ok": True}

        options = ScanOptions(
            guild_id="guild",
            start=datetime(2026, 7, 1, tzinfo=timezone.utc),
            end=datetime(2026, 7, 2, tzinfo=timezone.utc),
            bucket_seconds=86400,
        )
        recorder = RecordingDiscordClient(FakeClient())
        recorder.request("GET", "/guilds/guild/channels")

        with tempfile.TemporaryDirectory() as directory:
            path = f"{directory}/raw.json"
            save_raw_snapshot(recorder.snapshot(options), path)
            loaded = load_raw_snapshot(path)

        self.assertEqual(loaded.guild_id, "guild")
        self.assertEqual(len(loaded.downloads), 1)

    def test_recorded_archived_thread_scan_replays_with_stable_params(self) -> None:
        channel_id = "1232452732963655720"
        options = ScanOptions(
            guild_id="guild",
            start=datetime(2026, 7, 1, tzinfo=timezone.utc),
            end=datetime(2026, 7, 2, tzinfo=timezone.utc),
            bucket_seconds=86400,
        )
        expected_before = discord_timestamp(options.end)

        class ScanClient:
            def request(self, method, path, *, params=None, json_body=None):
                if path == "/guilds/guild/channels":
                    return [
                        {
                            "id": channel_id,
                            "type": 0,
                            "name": "general",
                            "position": 0,
                        }
                    ]
                if path == f"/channels/{channel_id}/messages":
                    return []
                if path == "/guilds/guild/threads/active":
                    return {"threads": []}
                if path == f"/channels/{channel_id}/threads/archived/public":
                    self.assert_archived_params(params)
                    return {"threads": [], "has_more": False}
                raise AssertionError((method, path, params, json_body))

            @staticmethod
            def assert_archived_params(params):
                if params != {"limit": 100, "before": expected_before}:
                    raise AssertionError(params)

        recorder = RecordingDiscordClient(ScanClient())
        first_result = ActivityScanner(recorder, options).scan()
        replay = ReplayDiscordClient(recorder.snapshot(options))
        second_result = ActivityScanner(replay, options).scan()

        self.assertEqual(first_result.channels[0].path, "#general")
        self.assertEqual(second_result.channels[0].path, "#general")


if __name__ == "__main__":
    unittest.main()
