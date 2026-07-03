from __future__ import annotations

import json
import unittest
from collections import Counter
from datetime import datetime, timezone

from discord_activity_bot.formatting import (
    display_channel_name,
    format_html_report,
    format_text_report,
    make_sparkline,
    relative_age,
    result_to_json,
    split_discord_messages,
)
from discord_activity_bot.scanner import ChannelReport, ScanResult


class SparklineTests(unittest.TestCase):
    def test_zero_buckets_are_visible(self) -> None:
        self.assertEqual(make_sparkline([0, 0, 0]), "···")

    def test_nonzero_buckets_scale_to_the_peak(self) -> None:
        self.assertEqual(make_sparkline([0, 1, 2, 4]), "·▄▆█")

    def test_ascii_mode_uses_plain_characters(self) -> None:
        self.assertEqual(make_sparkline([0, 1, 2, 4], ascii_only=True), ".468")

    def test_peak_can_be_shared_across_sparklines(self) -> None:
        self.assertEqual(make_sparkline([0, 1, 2], peak=4), "·▄▆")


class DiscordMessageSplitTests(unittest.TestCase):
    def test_split_keeps_lines_under_limit(self) -> None:
        chunks = split_discord_messages("one\ntwo\nthree", limit=8)
        self.assertEqual(chunks, ["one\ntwo", "three"])

    def test_split_long_line(self) -> None:
        chunks = split_discord_messages("abcdefghij", limit=4)
        self.assertEqual(chunks, ["abcd", "efgh", "ij"])


class ChannelNameTests(unittest.TestCase):
    def test_display_channel_name_removes_only_category_prefix(self) -> None:
        self.assertEqual(display_channel_name("Category/#general"), "#general")
        self.assertEqual(
            display_channel_name("Category/voice:Team / Standup"),
            "voice:Team / Standup",
        )
        self.assertEqual(
            display_channel_name("voice:Team / Standup"),
            "voice:Team / Standup",
        )


class RelativeAgeTests(unittest.TestCase):
    def test_relative_age_uses_short_units(self) -> None:
        now = datetime(2026, 7, 4, tzinfo=timezone.utc)

        self.assertEqual(relative_age(datetime(2026, 7, 4, tzinfo=timezone.utc), now), "now")
        self.assertEqual(
            relative_age(datetime(2026, 7, 3, tzinfo=timezone.utc), now),
            "1d",
        )
        self.assertEqual(
            relative_age(datetime(2024, 7, 4, tzinfo=timezone.utc), now),
            "2y",
        )


class TextReportTests(unittest.TestCase):
    def test_text_report_includes_channel_sparkline(self) -> None:
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, tzinfo=timezone.utc)
        result = ScanResult(
            guild_id="guild",
            start=start,
            end=end,
            bucket_seconds=86400,
            include_threads=True,
            include_archived_threads=True,
            include_private_archived_threads=False,
            include_all_private_archived_threads=False,
            exclude_bots=False,
            channels=[
                ChannelReport(
                    channel_id="channel",
                    path="#general",
                    kind="text",
                    position=0,
                    counts=[0, 2, 4],
                    message_count=6,
                )
            ],
            warnings=[],
        )

        report = format_text_report(result)

        self.assertIn("#general", report)
        self.assertIn("·▆█", report)


class HtmlReportTests(unittest.TestCase):
    def test_html_report_uses_shared_bar_scale_and_requested_columns(self) -> None:
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, tzinfo=timezone.utc)
        result = ScanResult(
            guild_id="guild",
            start=start,
            end=end,
            bucket_seconds=86400,
            include_threads=True,
            include_archived_threads=True,
            include_private_archived_threads=False,
            include_all_private_archived_threads=False,
            exclude_bots=False,
            channels=[
                ChannelReport(
                    channel_id="channel-a",
                    path="Category/#general",
                    kind="text",
                    position=0,
                    counts=[0, 2, 4],
                    created_at=start,
                    first_message_at=datetime(2026, 7, 2, tzinfo=timezone.utc),
                    last_message_at=datetime(2026, 7, 3, tzinfo=timezone.utc),
                    message_count=6,
                    participant_counts=Counter({"user-a": 4, "user-b": 2}),
                    participant_labels={"user-a": "Ada", "user-b": "Grace"},
                ),
                ChannelReport(
                    channel_id="channel-b",
                    path="#quiet",
                    kind="text",
                    position=1,
                    counts=[0, 1, 2],
                    message_count=3,
                    participant_counts=Counter({"user-c": 3}),
                    participant_labels={"user-c": "Lin"},
                ),
            ],
            warnings=[],
        )

        report = format_html_report(result)

        self.assertIn("data-sort-key=\"channel\"", report)
        self.assertIn("data-sort-key=\"prolific\"", report)
        self.assertIn("data-sort-key=\"message_rate\"", report)
        self.assertIn("<th scope=\"col\" aria-sort=\"none\"><button", report)
        self.assertIn("aria-sort=\"none\"", report)
        self.assertIn("focus-visible", report)
        self.assertIn("Channel</button>", report)
        self.assertIn("Activity (log)", report)
        self.assertIn("Msgs", report)
        self.assertIn("Rate", report)
        self.assertIn("People", report)
        self.assertIn("Top Sender", report)
        self.assertNotIn("Message Rate", report)
        self.assertNotIn("Activity Sparkline (Log)", report)
        self.assertIn("Log Scale Peak", report)
        self.assertIn("content: \"↑\"", report)
        self.assertIn("content: \"↓\"", report)
        self.assertNotIn("content: \"sort\"", report)
        self.assertIn("return type === \"number\" ? -1 : 1", report)
        self.assertIn("th {\n      padding: 8px 12px", report)
        self.assertIn("td {\n      padding: 0 12px", report)
        self.assertIn("border-right: 0", report)
        self.assertIn("border-left: 0", report)
        self.assertIn("border-radius: 0", report)
        self.assertIn("--bar-height:100.0%", report)
        self.assertIn("--bar-height:68.26%", report)
        self.assertIn('class="spark-bar" style="--bar-height:0.0%', report)
        self.assertIn('role="img" tabindex="0"', report)
        self.assertIn("Shared logarithmic vertical scale", report)
        self.assertIn("Counts by bucket", report)
        self.assertIn(
            '<span class="channel-name" title="Category/#general">#general</span>',
            report,
        )
        self.assertNotIn("channel-kind", report)
        self.assertIn(">3d</time>", report)
        self.assertIn(">2d</time>", report)
        self.assertIn(">1d</time>", report)
        self.assertNotIn("days ago", report)
        self.assertIn("Ada", report)
        self.assertIn("Message totals and participants use this same window.", report)


class JsonReportTests(unittest.TestCase):
    def test_json_sparklines_use_shared_peak(self) -> None:
        start = datetime(2026, 7, 1, tzinfo=timezone.utc)
        end = datetime(2026, 7, 4, tzinfo=timezone.utc)
        result = ScanResult(
            guild_id="guild",
            start=start,
            end=end,
            bucket_seconds=86400,
            include_threads=False,
            include_archived_threads=False,
            include_private_archived_threads=False,
            include_all_private_archived_threads=False,
            exclude_bots=False,
            channels=[
                ChannelReport(
                    channel_id="channel-a",
                    path="#busy",
                    kind="text",
                    position=0,
                    counts=[0, 2, 4],
                    created_at=start,
                    message_count=6,
                ),
                ChannelReport(
                    channel_id="channel-b",
                    path="#quiet",
                    kind="text",
                    position=1,
                    counts=[0, 1, 2],
                    message_count=3,
                ),
            ],
            warnings=[],
        )

        payload = json.loads(result_to_json(result))

        self.assertEqual(payload["sparkline_scale_peak"], 4)
        self.assertEqual(payload["sparkline_scale"], "logarithmic")
        self.assertEqual(payload["channels"][1]["sparkline"], "·▄▆")
        self.assertIn("message_rate_per_day", payload["channels"][0])


if __name__ == "__main__":
    unittest.main()
