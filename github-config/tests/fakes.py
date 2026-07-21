from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from github_config.api import ApiError, Response


class FakeApi:
    def __init__(
        self,
        *,
        responses: Mapping[tuple[str, str], Any] | None = None,
        lists: Mapping[str, list[Any]] | None = None,
        default_status: int = 403,
    ) -> None:
        self.responses = dict(responses or {})
        self.lists = dict(lists or {})
        self.default_status = default_status
        self.requests: list[tuple[str, str, Any]] = []
        self.api_version = "2026-03-10"
        self.api_url = "https://api.github.test"

    def request(self, method: str, path: str, body: Any = None) -> Response:
        self.requests.append((method, path, body))
        key = (method, path)
        if key not in self.responses:
            raise ApiError(method, path, self.default_status, "not available")
        value = self.responses[key]
        if isinstance(value, BaseException):
            raise value
        if isinstance(value, Response):
            return value
        return Response(200, value, {})

    def get_all(self, path: str, *, item_key: str | None = None) -> list[Any]:
        self.requests.append(("GET_ALL", path, item_key))
        if path not in self.lists:
            raise ApiError("GET", path, self.default_status, "not available")
        return list(self.lists[path])

    def graphql(
        self,
        document: str,
        variables: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        match = re.search(r"\b(?:query|mutation)\s+([A-Za-z_][A-Za-z0-9_]*)", document)
        operation = match.group(1) if match else "anonymous"
        body = dict(variables or {})
        self.requests.append(("GRAPHQL", operation, body))
        key = ("GRAPHQL", operation)
        if key not in self.responses:
            raise ApiError(
                "POST", f"/graphql#{operation}", self.default_status, "not available"
            )
        value = self.responses[key]
        if isinstance(value, BaseException):
            raise value
        if not isinstance(value, Mapping):
            raise TypeError(
                f"Fake GraphQL operation {operation} did not return a mapping"
            )
        return value
