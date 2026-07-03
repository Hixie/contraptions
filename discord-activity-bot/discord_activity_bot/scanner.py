from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from .discord_api import DiscordApiError, DiscordClient


CHANNEL_TYPE_NAMES = {
    0: "text",
    2: "voice",
    4: "category",
    5: "announcement",
    10: "announcement-thread",
    11: "public-thread",
    12: "private-thread",
    13: "stage",
    15: "forum",
    16: "media",
}

DIRECT_MESSAGE_CHANNEL_TYPES = {0, 2, 5, 13}
THREAD_PARENT_CHANNEL_TYPES = {0, 5, 15, 16}
REPORT_CHANNEL_TYPES = DIRECT_MESSAGE_CHANNEL_TYPES | {15, 16}
DISCORD_EPOCH_MS = 1420070400000


@dataclass
class ScanOptions:
    guild_id: str
    start: datetime
    end: datetime
    bucket_seconds: int
    include_threads: bool = True
    include_archived_threads: bool = True
    include_private_archived_threads: bool = False
    include_all_private_archived_threads: bool = False
    exclude_bots: bool = False

    @property
    def bucket_count(self) -> int:
        seconds = max((self.end - self.start).total_seconds(), 1)
        return max(math.ceil(seconds / self.bucket_seconds), 1)


@dataclass
class MessageStats:
    counts: list[int]
    message_count: int = 0
    saw_messages: bool = False
    first_message_at: datetime | None = None
    last_message_at: datetime | None = None
    participant_counts: Counter[str] = field(default_factory=Counter)
    participant_labels: dict[str, str] = field(default_factory=dict)

    def add_message(
        self,
        timestamp: datetime,
        participant: tuple[str, str] | None,
    ) -> None:
        self.message_count += 1
        if self.first_message_at is None or timestamp < self.first_message_at:
            self.first_message_at = timestamp
        if self.last_message_at is None or timestamp > self.last_message_at:
            self.last_message_at = timestamp
        if participant is None:
            return
        participant_id, label = participant
        self.participant_counts[participant_id] += 1
        self.participant_labels.setdefault(participant_id, label)


@dataclass
class ChannelReport:
    channel_id: str
    path: str
    kind: str
    position: int
    counts: list[int]
    created_at: datetime | None = None
    message_count: int = 0
    scanned_sources: int = 0
    thread_count: int = 0
    first_message_at: datetime | None = None
    last_message_at: datetime | None = None
    participant_counts: Counter[str] = field(default_factory=Counter)
    participant_labels: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def add_stats(self, stats: MessageStats) -> None:
        self.counts = [left + right for left, right in zip(self.counts, stats.counts)]
        self.message_count += stats.message_count
        self.scanned_sources += 1
        if stats.first_message_at is not None:
            if self.first_message_at is None or stats.first_message_at < self.first_message_at:
                self.first_message_at = stats.first_message_at
        if stats.last_message_at is not None:
            if self.last_message_at is None or stats.last_message_at > self.last_message_at:
                self.last_message_at = stats.last_message_at
        self.participant_counts.update(stats.participant_counts)
        for participant_id, label in stats.participant_labels.items():
            self.participant_labels.setdefault(participant_id, label)


@dataclass
class ScanResult:
    guild_id: str
    start: datetime
    end: datetime
    bucket_seconds: int
    include_threads: bool
    include_archived_threads: bool
    include_private_archived_threads: bool
    include_all_private_archived_threads: bool
    exclude_bots: bool
    channels: list[ChannelReport]
    warnings: list[str]


class ActivityScanner:
    def __init__(self, client: DiscordClient, options: ScanOptions) -> None:
        self.client = client
        self.options = options
        self.warnings: list[str] = []

    def scan(self) -> ScanResult:
        channels = self.client.request("GET", f"/guilds/{self.options.guild_id}/channels")
        if not isinstance(channels, list):
            raise RuntimeError("Discord returned an unexpected channel list.")

        reports = self._make_channel_reports(channels)
        for channel in self._sorted_channels(channels):
            channel_id = str(channel.get("id", ""))
            if channel.get("type") in DIRECT_MESSAGE_CHANNEL_TYPES and channel_id in reports:
                self._scan_source_into_report(
                    reports[channel_id],
                    channel_id,
                    "channel",
                    last_message_id=channel.get("last_message_id"),
                )

        if self.options.include_threads:
            self._scan_threads(channels, reports)

        return ScanResult(
            guild_id=self.options.guild_id,
            start=self.options.start,
            end=self.options.end,
            bucket_seconds=self.options.bucket_seconds,
            include_threads=self.options.include_threads,
            include_archived_threads=self.options.include_archived_threads,
            include_private_archived_threads=self.options.include_private_archived_threads,
            include_all_private_archived_threads=(
                self.options.include_all_private_archived_threads
            ),
            exclude_bots=self.options.exclude_bots,
            channels=list(reports.values()),
            warnings=self.warnings,
        )

    def _make_channel_reports(self, channels: list[dict[str, Any]]) -> dict[str, ChannelReport]:
        categories = {
            str(channel.get("id")): str(channel.get("name", "unnamed"))
            for channel in channels
            if channel.get("type") == 4
        }
        reports: dict[str, ChannelReport] = {}
        for channel in self._sorted_channels(channels):
            channel_type = channel.get("type")
            if channel_type not in REPORT_CHANNEL_TYPES:
                continue
            channel_id = str(channel.get("id"))
            reports[channel_id] = ChannelReport(
                channel_id=channel_id,
                path=channel_path(channel, categories),
                kind=CHANNEL_TYPE_NAMES.get(channel_type, f"type-{channel_type}"),
                position=int(channel.get("position") or 0),
                counts=[0] * self.options.bucket_count,
                created_at=snowflake_time(channel_id),
            )
        return reports

    @staticmethod
    def _sorted_channels(channels: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            channels,
            key=lambda channel: (
                int(channel.get("position") or 0),
                str(channel.get("parent_id") or ""),
                str(channel.get("name") or ""),
            ),
        )

    def _scan_threads(
        self,
        channels: list[dict[str, Any]],
        reports: dict[str, ChannelReport],
    ) -> None:
        seen_thread_ids: set[str] = set()
        for thread in self._active_threads():
            self._scan_thread(thread, reports, seen_thread_ids)

        if not self.options.include_archived_threads:
            return

        for channel in self._sorted_channels(channels):
            if channel.get("type") not in THREAD_PARENT_CHANNEL_TYPES:
                continue
            parent_id = str(channel.get("id", ""))
            if parent_id not in reports:
                continue
            for thread in self._archived_threads(parent_id, kind="public"):
                self._scan_thread(thread, reports, seen_thread_ids)
            if self.options.include_private_archived_threads:
                for thread in self._archived_threads(parent_id, kind="joined-private"):
                    self._scan_thread(thread, reports, seen_thread_ids)
            if self.options.include_all_private_archived_threads:
                for thread in self._archived_threads(parent_id, kind="all-private"):
                    self._scan_thread(thread, reports, seen_thread_ids)

    def _active_threads(self) -> list[dict[str, Any]]:
        try:
            payload = self.client.request(
                "GET", f"/guilds/{self.options.guild_id}/threads/active"
            )
        except DiscordApiError as error:
            self.warnings.append(f"Could not list active threads: {error}")
            return []
        threads = payload.get("threads", []) if isinstance(payload, dict) else []
        return threads if isinstance(threads, list) else []

    def _archived_threads(self, parent_id: str, *, kind: str) -> Iterable[dict[str, Any]]:
        if kind == "public":
            path = f"/channels/{parent_id}/threads/archived/public"
            label = "public"
        elif kind == "joined-private":
            path = f"/channels/{parent_id}/users/@me/threads/archived/private"
            label = "joined private"
        elif kind == "all-private":
            path = f"/channels/{parent_id}/threads/archived/private"
            label = "all private"
        else:
            raise ValueError(f"unknown archived thread kind: {kind}")

        before: str | None = None
        if kind != "joined-private":
            before = discord_timestamp(self.options.end)
        while True:
            params: dict[str, str | int] = {"limit": 100}
            if before is not None:
                params["before"] = before
            try:
                payload = self.client.request(
                    "GET",
                    path,
                    params=params,
                )
            except DiscordApiError as error:
                if error.status in {400, 403, 404}:
                    self.warnings.append(
                        f"Could not list {label} archived threads for {parent_id}: {error}"
                    )
                    return
                raise

            threads = payload.get("threads", []) if isinstance(payload, dict) else []
            if not threads:
                return

            oldest_archive: datetime | None = None
            oldest_thread_id: str | None = None
            for thread in threads:
                thread_id = str(thread.get("id") or "")
                if thread_id:
                    oldest_thread_id = thread_id
                archive_time = thread_archive_time(thread)
                if archive_time is None or archive_time >= self.options.start:
                    yield thread
                if archive_time is not None:
                    if oldest_archive is None or archive_time < oldest_archive:
                        oldest_archive = archive_time

            if not payload.get("has_more"):
                return
            if kind == "joined-private":
                if oldest_thread_id is None:
                    return
                before = oldest_thread_id
                continue
            if oldest_archive is None:
                return
            if oldest_archive < self.options.start:
                return
            before = discord_timestamp(oldest_archive)

    def _scan_thread(
        self,
        thread: dict[str, Any],
        reports: dict[str, ChannelReport],
        seen_thread_ids: set[str],
    ) -> None:
        thread_id = str(thread.get("id", ""))
        if not thread_id or thread_id in seen_thread_ids:
            return
        seen_thread_ids.add(thread_id)

        parent_id = str(thread.get("parent_id", ""))
        parent_report = reports.get(parent_id)
        if parent_report is None:
            self.warnings.append(
                f"Thread {thread_id} has no visible parent channel in the report."
            )
            return
        parent_report.thread_count += 1
        name = str(thread.get("name") or thread_id)
        self._scan_source_into_report(
            parent_report,
            thread_id,
            f"thread {name}",
            last_message_id=thread.get("last_message_id"),
        )

    def _scan_source_into_report(
        self,
        report: ChannelReport,
        source_id: str,
        source_label: str,
        *,
        last_message_id: Any = None,
    ) -> None:
        try:
            stats = self._message_counts(source_id)
        except DiscordApiError as error:
            if error.status in {400, 403, 404}:
                note = f"Skipped {source_label}: {error}"
                report.notes.append(note)
                self.warnings.append(f"{report.path}: {note}")
                return
            raise
        if not stats.saw_messages and last_message_id:
            note = (
                f"{source_label} returned no messages, but Discord reports a last "
                "message. Check View Channel and Read Message History permissions."
            )
            report.notes.append(note)
            self.warnings.append(f"{report.path}: {note}")
        report.add_stats(stats)

    def _message_counts(self, channel_id: str) -> MessageStats:
        stats = MessageStats(counts=[0] * self.options.bucket_count)
        before: str | None = None

        while True:
            messages = self.client.request(
                "GET",
                f"/channels/{channel_id}/messages",
                params={"limit": 100, "before": before},
            )
            if not messages:
                break
            if not isinstance(messages, list):
                raise RuntimeError(f"Discord returned unexpected messages for {channel_id}.")

            stats.saw_messages = True
            oldest_timestamp: datetime | None = None
            for message in messages:
                timestamp = parse_discord_time(str(message.get("timestamp", "")))
                if timestamp is None:
                    continue
                if oldest_timestamp is None or timestamp < oldest_timestamp:
                    oldest_timestamp = timestamp
                if timestamp < self.options.start or timestamp > self.options.end:
                    continue
                if self.options.exclude_bots and message_author_is_bot(message):
                    continue
                index = bucket_index(
                    timestamp,
                    self.options.start,
                    self.options.end,
                    self.options.bucket_seconds,
                )
                if index is None:
                    continue
                stats.counts[index] += 1
                stats.add_message(timestamp, message_participant(message))

            before = str(messages[-1].get("id", ""))
            if not before or oldest_timestamp is None or oldest_timestamp < self.options.start:
                break

        return stats


def channel_path(channel: dict[str, Any], categories: dict[str, str]) -> str:
    name = str(channel.get("name") or "unnamed")
    category_name = categories.get(str(channel.get("parent_id") or ""))
    kind = channel.get("type")
    marker = "voice:" if kind == 2 else "stage:" if kind == 13 else "#"
    if category_name:
        return f"{category_name}/{marker}{name}"
    return f"{marker}{name}"


def bucket_index(
    timestamp: datetime,
    start: datetime,
    end: datetime,
    bucket_seconds: int,
) -> int | None:
    if timestamp < start or timestamp > end:
        return None
    count = max(math.ceil((end - start).total_seconds() / bucket_seconds), 1)
    index = int((timestamp - start).total_seconds() // bucket_seconds)
    return min(index, count - 1)


def parse_discord_time(value: str) -> datetime | None:
    if not value:
        return None
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def snowflake_time(value: str) -> datetime | None:
    try:
        snowflake = int(value)
    except (TypeError, ValueError):
        return None
    timestamp_ms = (snowflake >> 22) + DISCORD_EPOCH_MS
    return datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)


def discord_timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def thread_archive_time(thread: dict[str, Any]) -> datetime | None:
    metadata = thread.get("thread_metadata")
    if not isinstance(metadata, dict):
        return None
    return parse_discord_time(str(metadata.get("archive_timestamp") or ""))


def message_author_is_bot(message: dict[str, Any]) -> bool:
    author = message.get("author")
    return isinstance(author, dict) and bool(author.get("bot"))


def message_participant(message: dict[str, Any]) -> tuple[str, str] | None:
    author = message.get("author")
    if not isinstance(author, dict):
        return None
    participant_id = str(author.get("id") or "")
    if not participant_id:
        return None
    return participant_id, message_participant_label(message, author)


def message_participant_label(message: dict[str, Any], author: dict[str, Any]) -> str:
    member = message.get("member")
    if isinstance(member, dict):
        nick = str(member.get("nick") or "").strip()
        if nick:
            return nick
    for field_name in ("global_name", "username"):
        label = str(author.get(field_name) or "").strip()
        if label:
            return label
    return str(author.get("id") or "unknown")
