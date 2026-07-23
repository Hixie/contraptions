from __future__ import annotations

from collections.abc import Iterable, Mapping, MutableMapping
from typing import Any


def pick(value: Mapping[str, Any], fields: Iterable[str]) -> dict[str, Any]:
    return {field: value[field] for field in fields if field in value}


def get_path(root: Mapping[str, Any], dotted_path: str, default: Any = None) -> Any:
    value: Any = root
    for part in dotted_path.split("."):
        if not isinstance(value, Mapping) or part not in value:
            return default
        value = value[part]
    return value


def set_path(root: MutableMapping[str, Any], dotted_path: str, value: Any) -> None:
    target: MutableMapping[str, Any] = root
    parts = dotted_path.split(".")
    for part in parts[:-1]:
        child = target.setdefault(part, {})
        if not isinstance(child, MutableMapping):
            raise TypeError(f"Cannot set {dotted_path}: {part} is not a mapping")
        target = child
    target[parts[-1]] = value


def sorted_mapping(items: Mapping[str, Any]) -> dict[str, Any]:
    return {key: items[key] for key in sorted(items, key=str.casefold)}


def without_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: without_none(child)
            for key, child in value.items()
            if child is not None
        }
    if isinstance(value, list):
        return [without_none(child) for child in value]
    return value
