from __future__ import annotations

import base64
import copy
import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from nacl.public import PublicKey, SealedBox

from .api import ApiClient


@dataclass(frozen=True)
class FieldChange:
    path: str
    before: Any
    after: Any
    action: str = "update"
    sensitive: bool = False


@dataclass
class Operation:
    method: str
    endpoint: str
    changes: list[FieldChange]
    body: dict[str, Any] | list[Any] | None = None
    phase: int = 50
    blocked_reason: str | None = None
    capture_id: tuple[str, ...] | None = None
    capture_response_path: tuple[str | int, ...] = ("id",)
    capture_response_values: list[tuple[tuple[str, ...], tuple[str | int, ...]]] = (
        field(default_factory=list)
    )
    endpoint_id_references: list[tuple[str, tuple[str, ...]]] = field(
        default_factory=list
    )
    body_id_references: list[tuple[tuple[str | int, ...], tuple[str, ...]]] = field(
        default_factory=list
    )
    body_id_list_references: list[
        tuple[tuple[str | int, ...], list[tuple[str, ...]]]
    ] = field(default_factory=list)
    environment_fields: list[tuple[tuple[str | int, ...], str]] = field(
        default_factory=list
    )
    secret_environment: str | None = None
    secret_public_key_endpoint: str | None = None
    repository_names: set[str] = field(default_factory=set)
    graphql_document: str | None = None
    warning_reason: str | None = None

    def preflight(
        self,
        available_ids: set[tuple[str, ...]],
        *,
        check_environment: bool = False,
    ) -> str | None:
        if self.blocked_reason:
            return self.blocked_reason
        references = [key for _, key in self.endpoint_id_references]
        references.extend(key for _, key in self.body_id_references)
        references.extend(
            key for _, keys in self.body_id_list_references for key in keys
        )
        missing = sorted({key for key in references if key not in available_ids})
        if missing:
            names = ", ".join("/".join(key) for key in missing)
            return f"no GitHub ID is known for {names}"
        if check_environment:
            environment_names = [name for _, name in self.environment_fields]
            if self.secret_environment is not None:
                environment_names.append(self.secret_environment)
            missing_environment = sorted(
                {name for name in environment_names if os.environ.get(name) is None}
            )
            if missing_environment:
                names = ", ".join(missing_environment)
                return f"environment variable(s) are not set: {names}"
        return None

    def execute(self, api: ApiClient, ids: dict[tuple[str, ...], int | str]) -> None:
        if self.blocked_reason:
            raise RuntimeError(
                f"Cannot apply {self.changes[0].path}: {self.blocked_reason}"
            )
        body = copy.deepcopy(self.body)
        endpoint = self.endpoint
        for placeholder, id_key in self.endpoint_id_references:
            if id_key not in ids:
                raise RuntimeError(
                    f"Cannot apply {self.changes[0].path}: no GitHub ID is known for {'/'.join(id_key)}"
                )
            endpoint = endpoint.replace(placeholder, str(ids[id_key]))
        if self.body_id_references:
            if body is None or not isinstance(body, dict):
                raise RuntimeError(
                    f"Internal error: {self.endpoint} has ID references without a request body"
                )
            for body_path, id_key in self.body_id_references:
                if id_key not in ids:
                    raise RuntimeError(
                        f"Cannot apply {self.changes[0].path}: no GitHub ID is known for {'/'.join(id_key)}"
                    )
                _set_nested(body, body_path, ids[id_key])
        if self.body_id_list_references:
            if body is None or not isinstance(body, dict):
                raise RuntimeError(
                    f"Internal error: {self.endpoint} has ID list references without a request body"
                )
            for body_path, id_keys in self.body_id_list_references:
                missing = [key for key in id_keys if key not in ids]
                if missing:
                    names = ", ".join("/".join(key) for key in missing)
                    raise RuntimeError(
                        f"Cannot apply {self.changes[0].path}: no GitHub ID is known for {names}"
                    )
                _set_nested(body, body_path, [ids[key] for key in id_keys])
        for body_path, environment_name in self.environment_fields:
            if body is None or not isinstance(body, dict):
                raise RuntimeError(
                    f"Internal error: {self.endpoint} has environment fields without a request body"
                )
            _set_nested(
                body,
                body_path,
                _environment_value(environment_name, self.changes[0].path),
            )
        if self.secret_environment is not None:
            if (
                body is None
                or not isinstance(body, dict)
                or self.secret_public_key_endpoint is None
            ):
                raise RuntimeError(
                    f"Internal error: {self.endpoint} has incomplete secret metadata"
                )
            public_key_response = api.request(
                "GET", self.secret_public_key_endpoint
            ).data
            if not isinstance(public_key_response, dict):
                raise RuntimeError(
                    f"GitHub did not return a public key for {self.changes[0].path}"
                )
            body["encrypted_value"] = _encrypt_secret(
                _environment_value(self.secret_environment, self.changes[0].path),
                str(public_key_response["key"]),
            )
            body["key_id"] = str(public_key_response["key_id"])
        if self.graphql_document is not None:
            if body is not None and not isinstance(body, Mapping):
                raise RuntimeError(
                    f"Internal error: GraphQL operation {endpoint} has a non-mapping variables value"
                )
            response_data = api.graphql(self.graphql_document, body)
        else:
            response_data = api.request(self.method, endpoint, body).data
        if self.capture_id is not None:
            captured_id = _get_nested(response_data, self.capture_response_path)
            if not isinstance(captured_id, (int, str)):
                raise RuntimeError(
                    f"GitHub did not return an ID at {_format_path(self.capture_response_path)} "
                    f"after {self.method} {endpoint}"
                )
            ids[self.capture_id] = captured_id
        for capture_key, response_path in self.capture_response_values:
            captured_value = _get_nested(response_data, response_path)
            if not isinstance(captured_value, (int, str)):
                raise TypeError(
                    f"GitHub did not return a value at {_format_path(response_path)} "
                    f"after {self.method} {endpoint}"
                )
            ids[capture_key] = captured_value


def sort_operations(operations: list[Operation]) -> list[Operation]:
    return sorted(
        operations,
        key=lambda operation: (
            operation.phase,
            operation.changes[0].path.casefold()
            if operation.changes
            else operation.endpoint.casefold(),
        ),
    )


def preflight_operations(
    operations: list[Operation],
    ids: Mapping[tuple[str, ...], int | str],
    *,
    check_environment: bool = False,
) -> None:
    available_ids = set(ids)
    for operation in operations:
        reason = operation.preflight(available_ids, check_environment=check_environment)
        if reason is not None:
            operation.blocked_reason = reason
        if operation.capture_id is not None:
            available_ids.add(operation.capture_id)
        available_ids.update(
            capture_key for capture_key, _ in operation.capture_response_values
        )


def _environment_value(name: str, path: str) -> str:
    value = os.environ.get(name)
    if value is None:
        raise RuntimeError(
            f"Cannot apply {path}: environment variable {name} is not set"
        )
    return value


def _encrypt_secret(value: str, public_key: str) -> str:
    key = PublicKey(base64.b64decode(public_key))
    encrypted = SealedBox(key).encrypt(value.encode())
    return base64.b64encode(encrypted).decode()


def _set_nested(root: dict[str, Any], path: tuple[str | int, ...], value: Any) -> None:
    target: Any = root
    for part in path[:-1]:
        if isinstance(part, int):
            if not isinstance(target, list) or part >= len(target):
                raise RuntimeError(
                    f"Internal error: request body field {_format_path(path)} is not a list"
                )
            target = target[part]
        else:
            if not isinstance(target, dict):
                raise TypeError(
                    f"Internal error: request body field {_format_path(path)} is not a mapping"
                )
            target = target.setdefault(part, {})
    final = path[-1]
    if isinstance(final, int):
        if not isinstance(target, list) or final >= len(target):
            raise RuntimeError(
                f"Internal error: request body field {_format_path(path)} is not a list"
            )
        target[final] = value
    else:
        if not isinstance(target, dict):
            raise TypeError(
                f"Internal error: request body field {_format_path(path)} is not a mapping"
            )
        target[final] = value


def _get_nested(root: Any, path: tuple[str | int, ...]) -> Any:
    value = root
    for part in path:
        if isinstance(part, int):
            if not isinstance(value, list) or part >= len(value):
                return None
            value = value[part]
        else:
            if not isinstance(value, Mapping) or part not in value:
                return None
            value = value[part]
    return value


def _format_path(path: tuple[str | int, ...]) -> str:
    return ".".join(str(part) for part in path)
