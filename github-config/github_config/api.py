from __future__ import annotations

import json
import os
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from . import __version__

DEFAULT_API_VERSION = "2026-03-10"


@dataclass(frozen=True)
class Response:
    status: int
    data: Any
    headers: Mapping[str, str]


class ApiError(RuntimeError):
    def __init__(
        self,
        method: str,
        path: str,
        status: int,
        message: str,
        documentation_url: str | None = None,
    ) -> None:
        self.method = method
        self.path = path
        self.status = status
        self.message = message
        self.documentation_url = documentation_url
        suffix = f" ({documentation_url})" if documentation_url else ""
        super().__init__(
            f"GitHub API {method} {path} returned {status}: {message}{suffix}"
        )


class GraphqlData(dict[str, Any]):
    def __init__(
        self, data: Mapping[str, Any], errors: list[str] | None = None
    ) -> None:
        super().__init__(data)
        self.errors = tuple(errors or ())


class ApiClient(Protocol):
    api_url: str
    api_version: str

    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | list[Any] | None = None,
    ) -> Response: ...

    def get_all(self, path: str, *, item_key: str | None = None) -> list[Any]: ...

    def graphql(
        self,
        document: str,
        variables: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...


class GitHubApi:
    def __init__(
        self,
        token: str,
        *,
        api_url: str = "https://api.github.com",
        api_version: str = DEFAULT_API_VERSION,
    ) -> None:
        self._token = token
        self.api_url = api_url.rstrip("/")
        self.api_version = api_version

    def request(
        self,
        method: str,
        path: str,
        body: Mapping[str, Any] | list[Any] | None = None,
    ) -> Response:
        url = path if path.startswith("https://") else f"{self.api_url}{path}"
        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self._token}",
            "User-Agent": f"github-config/{__version__}",
            "X-GitHub-Api-Version": self.api_version,
        }
        if body is not None:
            data = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request) as raw_response:
                raw_body = raw_response.read()
                return Response(
                    status=raw_response.status,
                    data=_decode_body(
                        raw_body, raw_response.headers.get("Content-Type", "")
                    ),
                    headers=dict(raw_response.headers.items()),
                )
        except urllib.error.HTTPError as error:
            raw_body = error.read()
            payload = _decode_body(raw_body, error.headers.get("Content-Type", ""))
            message = error.reason
            documentation_url = None
            if isinstance(payload, dict):
                message = str(payload.get("message", message))
                documentation_url = payload.get("documentation_url")
                errors = payload.get("errors")
                if errors:
                    message = f"{message}: {errors}"
            raise ApiError(
                method,
                _display_path(url, self.api_url),
                error.code,
                message,
                documentation_url,
            ) from None
        except urllib.error.URLError as error:
            raise RuntimeError(
                f"GitHub API {method} {_display_path(url, self.api_url)} failed: {error.reason}"
            ) from None

    def get(self, path: str) -> Any:
        return self.request("GET", path).data

    def get_all(self, path: str, *, item_key: str | None = None) -> list[Any]:
        next_path: str | None = _with_per_page(path)
        items: list[Any] = []
        while next_path:
            response = self.request("GET", next_path)
            page = response.data
            if item_key is not None:
                if not isinstance(page, dict) or not isinstance(
                    page.get(item_key), list
                ):
                    raise RuntimeError(
                        f"GitHub API GET {path} did not return a {item_key!r} list"
                    )
                items.extend(page[item_key])
            elif isinstance(page, list):
                items.extend(page)
            else:
                raise RuntimeError(f"GitHub API GET {path} did not return a list")
            next_path = _next_link(response.headers)
        return items

    def graphql(
        self,
        document: str,
        variables: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        operation = _graphql_operation_name(document)
        response = self.request(
            "POST",
            _graphql_url(self.api_url),
            {"query": document, "variables": dict(variables or {})},
        )
        payload = response.data
        if not isinstance(payload, Mapping):
            raise TypeError(
                f"GitHub GraphQL operation {operation} did not return a mapping"
            )
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            messages = [
                str(error.get("message", error))
                if isinstance(error, Mapping)
                else str(error)
                for error in errors
            ]
            data = payload.get("data")
            if _graphql_operation_kind(document) == "query" and isinstance(
                data, Mapping
            ):
                return GraphqlData(data, messages)
            raise ApiError(
                "POST",
                f"/graphql#{operation}",
                response.status,
                "; ".join(messages),
            )
        data = payload.get("data")
        if not isinstance(data, Mapping):
            raise TypeError(f"GitHub GraphQL operation {operation} did not return data")
        return data


def ambient_api() -> GitHubApi:
    api_url = os.environ.get("GH_API_URL") or os.environ.get("GITHUB_API_URL")
    host = os.environ.get("GH_HOST", "github.com")
    if api_url is None:
        api_url = (
            "https://api.github.com"
            if host == "github.com"
            else f"https://{host}/api/v3"
        )
    gh_token = os.environ.get("GH_TOKEN") or None
    github_token = os.environ.get("GITHUB_TOKEN") or None
    if gh_token is not None and github_token is not None and gh_token != github_token:
        raise RuntimeError(
            "GH_TOKEN and GITHUB_TOKEN are both set but have different values; "
            "unset one or make them identical"
        )
    token = gh_token or github_token
    if token is None:
        token = _token_from_gh(host)
    return GitHubApi(
        token,
        api_url=api_url,
        api_version=os.environ.get("GITHUB_API_VERSION", DEFAULT_API_VERSION),
    )


def quote(value: str | int) -> str:
    return urllib.parse.quote(str(value), safe="")


def with_query(path: str, **values: str | int | bool | None) -> str:
    separator = "&" if "?" in path else "?"
    encoded = urllib.parse.urlencode(
        {key: value for key, value in values.items() if value is not None}
    )
    return f"{path}{separator}{encoded}" if encoded else path


def _token_from_gh(host: str) -> str:
    try:
        result = subprocess.run(
            ["gh", "auth", "token", "--hostname", host],
            check=True,
            capture_output=True,
            text=True,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "No ambient GitHub token was found. Set GH_TOKEN or GITHUB_TOKEN, or install and authenticate gh."
        ) from None
    except subprocess.CalledProcessError as error:
        detail = error.stderr.strip() or "gh auth token failed"
        raise RuntimeError(
            f"No ambient GitHub token was found. Set GH_TOKEN or GITHUB_TOKEN, or authenticate gh: {detail}"
        ) from None
    token = result.stdout.strip()
    if not token:
        raise RuntimeError("gh auth token returned an empty token")
    return token


def _decode_body(raw_body: bytes, content_type: str) -> Any:
    if not raw_body:
        return None
    text = raw_body.decode("utf-8", errors="replace")
    if "json" in content_type.lower() or text[:1] in ("{", "["):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
    return text


def _display_path(url: str, api_url: str) -> str:
    return url.removeprefix(api_url)


def _graphql_url(api_url: str) -> str:
    if api_url.endswith("/api/v3"):
        return f"{api_url.removesuffix('/v3')}/graphql"
    return f"{api_url.rstrip('/')}/graphql"


def _graphql_operation_name(document: str) -> str:
    words = document.replace("(", " ").replace("{", " ").split()
    for kind in ("query", "mutation"):
        try:
            index = words.index(kind)
        except ValueError:
            continue
        if index + 1 < len(words):
            return words[index + 1]
    return "anonymous"


def _graphql_operation_kind(document: str) -> str:
    words = document.replace("(", " ").replace("{", " ").split()
    return next((word for word in words if word in {"query", "mutation"}), "query")


def _with_per_page(path: str) -> str:
    parsed = urllib.parse.urlsplit(path)
    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    if not any(key == "per_page" for key, _ in query):
        query.append(("per_page", "100"))
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(query),
            parsed.fragment,
        )
    )


def _next_link(headers: Mapping[str, str]) -> str | None:
    link = next(
        (value for key, value in headers.items() if key.lower() == "link"), None
    )
    if not link:
        return None
    for entry in link.split(","):
        pieces = [piece.strip() for piece in entry.split(";")]
        if len(pieces) > 1 and 'rel="next"' in pieces[1:]:
            return pieces[0].removeprefix("<").removesuffix(">")
    return None
