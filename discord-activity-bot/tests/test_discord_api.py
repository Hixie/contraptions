from __future__ import annotations

import unittest
from typing import Any

from discord_activity_bot.discord_api import DiscordClient


class DiscordClientTests(unittest.TestCase):
    def test_create_message_disables_mentions(self) -> None:
        class RecordingClient(DiscordClient):
            def __init__(self) -> None:
                self.calls: list[tuple[str, str, dict[str, Any] | None]] = []

            def request(
                self,
                method: str,
                path: str,
                *,
                params: dict[str, Any] | None = None,
                json_body: dict[str, Any] | None = None,
            ) -> Any:
                self.calls.append((method, path, json_body))
                return {}

        client = RecordingClient()
        client.create_message("channel", "hello @everyone")

        payload = client.calls[0][2]
        self.assertEqual(client.calls[0][0], "POST")
        self.assertEqual(client.calls[0][1], "/channels/channel/messages")
        self.assertIsNotNone(payload)
        self.assertEqual(payload["allowed_mentions"], {"parse": []})


if __name__ == "__main__":
    unittest.main()
