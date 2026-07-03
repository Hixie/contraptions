from __future__ import annotations

import unittest
from datetime import datetime, timezone

from discord_activity_bot.__main__ import parse_args, scan_start


class ScanWindowTests(unittest.TestCase):
    def test_default_scan_starts_at_guild_creation(self) -> None:
        end = datetime(2026, 7, 2, tzinfo=timezone.utc)

        self.assertEqual(
            scan_start("1232452732963655720", end, None),
            datetime(2024, 4, 23, 22, 7, 9, 403000, tzinfo=timezone.utc),
        )

    def test_days_option_uses_recent_window(self) -> None:
        end = datetime(2026, 7, 2, tzinfo=timezone.utc)

        self.assertEqual(
            scan_start("1232452732963655720", end, 7),
            datetime(2026, 6, 25, tzinfo=timezone.utc),
        )

    def test_days_option_does_not_start_before_guild_creation(self) -> None:
        end = datetime(2026, 7, 2, tzinfo=timezone.utc)

        self.assertEqual(
            scan_start("1232452732963655720", end, 3650),
            datetime(2024, 4, 23, 22, 7, 9, 403000, tzinfo=timezone.utc),
        )

    def test_invalid_guild_id_has_no_lifetime_start(self) -> None:
        end = datetime(2026, 7, 2, tzinfo=timezone.utc)

        self.assertIsNone(scan_start("not-a-snowflake", end, None))


class ParseArgsTests(unittest.TestCase):
    def test_days_default_is_server_lifetime(self) -> None:
        self.assertIsNone(parse_args([]).days)

    def test_lifetime_report_defaults_to_weekly_buckets(self) -> None:
        self.assertEqual(parse_args([]).bucket, "week")

    def test_joined_private_archived_threads_are_included_by_default(self) -> None:
        self.assertTrue(parse_args([]).include_private_archived_threads)

    def test_joined_private_archived_threads_can_be_skipped(self) -> None:
        self.assertFalse(
            parse_args(["--no-private-archived-threads"]).include_private_archived_threads
        )

    def test_days_option_sets_recent_window(self) -> None:
        self.assertEqual(parse_args(["--days", "7"]).days, 7)

    def test_scan_only_and_raw_paths_parse(self) -> None:
        args = parse_args(["--save-raw", "raw.json", "--scan-only"])

        self.assertEqual(args.save_raw, "raw.json")
        self.assertTrue(args.scan_only)

    def test_from_raw_path_parses(self) -> None:
        self.assertEqual(parse_args(["--from-raw", "raw.json"]).from_raw, "raw.json")


if __name__ == "__main__":
    unittest.main()
