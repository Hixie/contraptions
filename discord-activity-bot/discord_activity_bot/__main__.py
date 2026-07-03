from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta, timezone

from .discord_api import DiscordClient
from .formatting import (
    format_html_report,
    format_text_report,
    result_to_json,
    split_discord_messages,
)
from .raw_snapshot import (
    RecordingDiscordClient,
    ReplayDiscordClient,
    load_raw_snapshot,
    save_raw_snapshot,
)
from .scanner import ActivityScanner, ScanOptions, snowflake_time


BUCKET_SECONDS = {
    "hour": 3600,
    "day": 86400,
    "week": 604800,
}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.scan_only and not args.save_raw:
        print("Pass --save-raw when using --scan-only.", file=sys.stderr)
        return 2
    if args.from_raw and args.save_raw:
        print("Use either --from-raw or --save-raw, not both.", file=sys.stderr)
        return 2

    token = os.environ.get("DISCORD_BOT_TOKEN", "").strip()
    recording_client: RecordingDiscordClient | None = None
    if args.from_raw:
        snapshot = load_raw_snapshot(args.from_raw)
        guild_id = snapshot.guild_id
        end = snapshot.scan_end
        start = snapshot_scan_start(snapshot.scan_start, snapshot.scan_end, args.days)
        client = ReplayDiscordClient(snapshot)
        options = ScanOptions(
            guild_id=guild_id,
            start=start,
            end=end,
            bucket_seconds=BUCKET_SECONDS[args.bucket],
            include_threads=args.include_threads and snapshot.include_threads,
            include_archived_threads=(
                args.include_archived_threads and snapshot.include_archived_threads
            ),
            include_private_archived_threads=(
                args.include_private_archived_threads
                and snapshot.include_private_archived_threads
            ),
            include_all_private_archived_threads=(
                snapshot.include_all_private_archived_threads
            ),
            exclude_bots=args.exclude_bots,
        )
    else:
        if not token:
            print("Set DISCORD_BOT_TOKEN before running the scanner.", file=sys.stderr)
            return 2

        guild_id = args.guild_id or os.environ.get("DISCORD_GUILD_ID", "").strip()
        if not guild_id:
            print("Pass --guild-id or set DISCORD_GUILD_ID.", file=sys.stderr)
            return 2

        end = datetime.now(timezone.utc)
        start = scan_start(guild_id, end, args.days)
        if start is None:
            print("Guild ID must be a Discord snowflake.", file=sys.stderr)
            return 2
        options = ScanOptions(
            guild_id=guild_id,
            start=start,
            end=end,
            bucket_seconds=BUCKET_SECONDS[args.bucket],
            include_threads=args.include_threads,
            include_archived_threads=args.include_archived_threads,
            include_private_archived_threads=args.include_private_archived_threads,
            include_all_private_archived_threads=args.include_all_private_archived_threads,
            exclude_bots=args.exclude_bots,
        )

        recording_client = RecordingDiscordClient(DiscordClient(token))
        client = recording_client

    result = ActivityScanner(client, options).scan()
    if recording_client is not None and args.save_raw:
        save_raw_snapshot(recording_client.snapshot(options), args.save_raw)
    if args.scan_only:
        print(f"Saved raw Discord data to {args.save_raw}", file=sys.stderr)
        return 0

    if args.json:
        report = result_to_json(result, ascii_only=args.ascii)
    elif args.text:
        report = format_text_report(result, ascii_only=args.ascii)
    else:
        report = format_html_report(result)
    print(report)

    report_channel_id = (
        args.post_channel_id or os.environ.get("DISCORD_REPORT_CHANNEL_ID", "").strip()
    )
    if report_channel_id:
        if not token:
            print("Set DISCORD_BOT_TOKEN before posting to Discord.", file=sys.stderr)
            return 2
        text_report = format_text_report(result, ascii_only=args.ascii)
        post_report(DiscordClient(token), report_channel_id, text_report)

    return 0


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scan Discord message activity and write an HTML channel report."
    )
    parser.add_argument("--guild-id", help="Discord server ID. Defaults to DISCORD_GUILD_ID.")
    parser.add_argument(
        "--days",
        type=positive_int,
        help="Limit the scan to the most recent number of days. Default: server lifetime.",
    )
    parser.add_argument(
        "--bucket",
        choices=sorted(BUCKET_SECONDS),
        default="week",
        help="How much time each sparkline cell covers. Default: week.",
    )
    parser.add_argument(
        "--no-threads",
        dest="include_threads",
        action="store_false",
        help="Only count messages posted directly in top-level channels.",
    )
    parser.add_argument(
        "--no-archived-threads",
        dest="include_archived_threads",
        action="store_false",
        help="Only count active threads.",
    )
    parser.add_argument(
        "--include-private-archived-threads",
        dest="include_private_archived_threads",
        action="store_true",
        help="Count joined private archived threads the bot can list. This is the default.",
    )
    parser.add_argument(
        "--no-private-archived-threads",
        dest="include_private_archived_threads",
        action="store_false",
        help="Skip joined private archived threads.",
    )
    parser.add_argument(
        "--include-all-private-archived-threads",
        action="store_true",
        help="Try to count all private archived threads. Requires Manage Threads.",
    )
    parser.add_argument(
        "--exclude-bots",
        action="store_true",
        help="Skip messages authored by bots.",
    )
    output_group = parser.add_mutually_exclusive_group()
    output_group.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of HTML.",
    )
    output_group.add_argument(
        "--text",
        action="store_true",
        help="Print the compact text table instead of HTML.",
    )
    parser.add_argument(
        "--ascii",
        action="store_true",
        help="Use ASCII characters in text and JSON sparklines.",
    )
    parser.add_argument(
        "--post-channel-id",
        help="Post the report to this Discord channel after printing it.",
    )
    parser.add_argument(
        "--save-raw",
        help="Write the raw Discord responses downloaded during the scan to this JSON file.",
    )
    parser.add_argument(
        "--from-raw",
        help="Generate the report from a previously saved raw JSON file.",
    )
    parser.add_argument(
        "--scan-only",
        action="store_true",
        help="Download raw Discord data and write --save-raw without printing a report.",
    )
    parser.set_defaults(
        include_threads=True,
        include_archived_threads=True,
        include_private_archived_threads=True,
    )
    return parser.parse_args(argv)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def scan_start(guild_id: str, end: datetime, days: int | None) -> datetime | None:
    guild_created_at = snowflake_time(guild_id)
    if guild_created_at is None:
        return None
    if days is not None:
        return max(guild_created_at, end - timedelta(days=days))
    return guild_created_at


def snapshot_scan_start(scan_start: datetime, scan_end: datetime, days: int | None) -> datetime:
    if days is None:
        return scan_start
    return max(scan_start, scan_end - timedelta(days=days))


def post_report(client: DiscordClient, channel_id: str, report: str) -> None:
    for chunk in split_discord_messages(report, limit=1850):
        client.create_message(channel_id, f"```text\n{chunk}\n```")


if __name__ == "__main__":
    raise SystemExit(main())
