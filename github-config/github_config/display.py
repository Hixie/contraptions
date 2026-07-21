from __future__ import annotations

import json
import sys
from collections import Counter
from collections.abc import Iterable
from typing import Any, TextIO

from .operations import FieldChange, Operation

_COLORS = {
    "add": "\033[32m",
    "remove": "\033[31m",
    "update": "\033[33m",
    "blocked": "\033[31;1m",
    "reset": "\033[0m",
    "dim": "\033[2m",
}


def print_plan(
    org: str,
    operations: Iterable[Operation],
    *,
    stream: TextIO = sys.stdout,
    color: str = "auto",
) -> None:
    operations = list(operations)
    use_color = color == "always" or (color == "auto" and stream.isatty())
    print(f"GitHub configuration diff for {org}", file=stream)
    print(file=stream)
    if not operations:
        print("No changes.", file=stream)
        return
    counts: Counter[str] = Counter()
    for operation in operations:
        for change in operation.changes:
            action = "blocked" if operation.blocked_reason else change.action
            counts[action] += 1
            _print_change(change, action, stream, use_color)
        if operation.blocked_reason:
            prefix = _paint("!", "blocked", use_color)
            print(f"    {prefix} {operation.blocked_reason}", file=stream)
        elif operation.warning_reason:
            prefix = _paint("!", "update", use_color)
            print(f"    {prefix} Warning: {operation.warning_reason}", file=stream)
    print(file=stream)
    parts = []
    for action, label in (
        ("add", "addition"),
        ("update", "update"),
        ("remove", "removal"),
        ("blocked", "blocked"),
    ):
        count = counts[action]
        if count:
            plural = "s" if count != 1 else ""
            parts.append(f"{count} {label}{plural}")
    print("Plan: " + ", ".join(parts) + ".", file=stream)


def _print_change(
    change: FieldChange, action: str, stream: TextIO, use_color: bool
) -> None:
    symbol = {"add": "+", "remove": "-", "update": "~", "blocked": "!"}[action]
    print(f"{_paint(symbol, action, use_color)} {change.path}", file=stream)
    if change.sensitive:
        print(f"    {_paint('<write-only value>', 'dim', use_color)}", file=stream)
    elif action == "add":
        print(f"    {_format(change.after)}", file=stream)
    elif action == "remove":
        print(f"    {_format(change.before)}", file=stream)
    else:
        print(f"    {_format(change.before)}  ->  {_format(change.after)}", file=stream)


def _format(value: Any) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(", ", ": ")
    )


def _paint(value: str, color: str, enabled: bool) -> str:
    return f"{_COLORS[color]}{value}{_COLORS['reset']}" if enabled else value
