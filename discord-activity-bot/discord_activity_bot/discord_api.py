from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


DISCORD_API_BASE = "https://discord.com/api/v10"
RETRYABLE_STATUSES = {500, 502, 503, 504}


@dataclass
class DiscordApiError(RuntimeError):
    status: int
    body: str
    path: str

    def __str__(self) -> str:
        detail = self.body.strip() or "empty response body"
        return f"Discord API returned {self.status} for {self.path}: {detail[:500]}"


class DiscordClient:
    def __init__(
        self,
        token: str,
        *,
        base_url: str = DISCORD_API_BASE,
        user_agent: str = "discord-activity-bot/0.1",
        max_retries: int = 5,
    ) -> None:
        self.token = token.strip()
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.max_retries = max_retries

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        query = ""
        if params:
            query = "?" + urllib.parse.urlencode(
                {key: value for key, value in params.items() if value is not None}
            )
        url = f"{self.base_url}{path}{query}"
        body = None
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bot {self.token}",
            "User-Agent": self.user_agent,
        }
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            headers["Content-Type"] = "application/json"

        attempt = 0
        while True:
            request = urllib.request.Request(url, data=body, headers=headers, method=method)
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    raw = response.read().decode("utf-8")
                    if not raw:
                        return None
                    return json.loads(raw)
            except urllib.error.HTTPError as error:
                raw_error = error.read().decode("utf-8", errors="replace")
                if error.code == 429 and attempt < self.max_retries:
                    time.sleep(self._rate_limit_delay(error.headers, raw_error))
                    attempt += 1
                    continue
                if error.code in RETRYABLE_STATUSES and attempt < self.max_retries:
                    time.sleep(min(2**attempt, 30))
                    attempt += 1
                    continue
                raise DiscordApiError(error.code, raw_error, path) from error
            except urllib.error.URLError as error:
                if attempt < self.max_retries:
                    time.sleep(min(2**attempt, 30))
                    attempt += 1
                    continue
                raise RuntimeError(f"Could not reach Discord API: {error.reason}") from error

    @staticmethod
    def _rate_limit_delay(headers: Any, raw_error: str) -> float:
        header_value = headers.get("Retry-After") if headers else None
        if header_value:
            try:
                return max(float(header_value), 0.25)
            except ValueError:
                pass
        try:
            payload = json.loads(raw_error)
            retry_after = float(payload.get("retry_after", 5))
            return max(retry_after, 0.25)
        except (TypeError, ValueError, json.JSONDecodeError):
            return 5.0

    def create_message(self, channel_id: str, content: str) -> Any:
        return self.request(
            "POST",
            f"/channels/{channel_id}/messages",
            json_body={"content": content, "allowed_mentions": {"parse": []}},
        )
