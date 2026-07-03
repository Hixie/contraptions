from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .discord_api import DiscordApiError, DiscordClient
from .scanner import ScanOptions, parse_discord_time


RAW_SNAPSHOT_VERSION = 1


@dataclass
class RawSnapshot:
    guild_id: str
    scan_start: datetime
    scan_end: datetime
    include_threads: bool
    include_archived_threads: bool
    include_private_archived_threads: bool
    include_all_private_archived_threads: bool
    downloads: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": RAW_SNAPSHOT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "scan": {
                "guild_id": self.guild_id,
                "start": self.scan_start.isoformat(),
                "end": self.scan_end.isoformat(),
                "include_threads": self.include_threads,
                "include_archived_threads": self.include_archived_threads,
                "include_private_archived_threads": self.include_private_archived_threads,
                "include_all_private_archived_threads": (
                    self.include_all_private_archived_threads
                ),
            },
            "downloads": self.downloads,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> RawSnapshot:
        if payload.get("version") != RAW_SNAPSHOT_VERSION:
            raise ValueError("unsupported raw snapshot version")
        scan = payload.get("scan")
        if not isinstance(scan, dict):
            raise ValueError("raw snapshot is missing scan metadata")
        scan_start = parse_discord_time(str(scan.get("start") or ""))
        scan_end = parse_discord_time(str(scan.get("end") or ""))
        if scan_start is None or scan_end is None:
            raise ValueError("raw snapshot has invalid scan timestamps")
        downloads = payload.get("downloads")
        if not isinstance(downloads, list):
            raise ValueError("raw snapshot is missing downloads")
        return cls(
            guild_id=str(scan.get("guild_id") or ""),
            scan_start=scan_start,
            scan_end=scan_end,
            include_threads=bool(scan.get("include_threads")),
            include_archived_threads=bool(scan.get("include_archived_threads")),
            include_private_archived_threads=bool(
                scan.get("include_private_archived_threads")
            ),
            include_all_private_archived_threads=bool(
                scan.get("include_all_private_archived_threads")
            ),
            downloads=downloads,
        )


class RecordingDiscordClient:
    def __init__(self, client: DiscordClient) -> None:
        self.client = client
        self.downloads: list[dict[str, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        request = raw_request(method, path, params=params, json_body=json_body)
        try:
            response = self.client.request(
                method,
                path,
                params=params,
                json_body=json_body,
            )
        except DiscordApiError as error:
            self.downloads.append(
                {
                    **request,
                    "ok": False,
                    "status": error.status,
                    "body": error.body,
                }
            )
            raise
        self.downloads.append({**request, "ok": True, "response": copy.deepcopy(response)})
        return response

    def snapshot(self, options: ScanOptions) -> RawSnapshot:
        return RawSnapshot(
            guild_id=options.guild_id,
            scan_start=options.start,
            scan_end=options.end,
            include_threads=options.include_threads,
            include_archived_threads=options.include_archived_threads,
            include_private_archived_threads=options.include_private_archived_threads,
            include_all_private_archived_threads=options.include_all_private_archived_threads,
            downloads=copy.deepcopy(self.downloads),
        )


class ReplayDiscordClient:
    def __init__(self, snapshot: RawSnapshot) -> None:
        self.responses: dict[str, list[dict[str, Any]]] = {}
        for download in snapshot.downloads:
            key = request_key(download)
            self.responses.setdefault(key, []).append(download)

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        key = request_key(raw_request(method, path, params=params, json_body=json_body))
        matches = self.responses.get(key)
        if not matches:
            raise RuntimeError(f"raw snapshot has no response for {method} {path}")
        download = matches.pop(0)
        if not download.get("ok"):
            raise DiscordApiError(
                int(download.get("status") or 0),
                str(download.get("body") or ""),
                path,
            )
        return copy.deepcopy(download.get("response"))


def save_raw_snapshot(snapshot: RawSnapshot, path: str) -> None:
    Path(path).write_text(json.dumps(snapshot.to_dict(), indent=2), encoding="utf-8")


def load_raw_snapshot(path: str) -> RawSnapshot:
    return RawSnapshot.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def raw_request(
    method: str,
    path: str,
    *,
    params: dict[str, Any] | None,
    json_body: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "method": method,
        "path": path,
        "params": clean_mapping(params),
        "json_body": clean_mapping(json_body),
    }


def request_key(download: dict[str, Any]) -> str:
    return json.dumps(
        {
            "method": download.get("method"),
            "path": download.get("path"),
            "params": clean_mapping(download.get("params")),
            "json_body": clean_mapping(download.get("json_body")),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def clean_mapping(value: dict[str, Any] | None) -> dict[str, Any]:
    if not value:
        return {}
    return {key: item for key, item in sorted(value.items()) if item is not None}
