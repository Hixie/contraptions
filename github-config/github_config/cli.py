from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from .api import DEFAULT_API_VERSION, ApiClient, GitHubApi, ambient_api
from .config import ConfigError, dump_config, load_config
from .display import print_plan
from .exporter import Exporter
from .operations import Operation, preflight_operations
from .planner import Planner

_AUTHENTICATION_HELP = (
    "Authentication: set GH_TOKEN or GITHUB_TOKEN. If both are set, they must "
    "contain the same token. If neither is set, github-config uses `gh auth token`."
)


def main(argv: Sequence[str] | None = None) -> None:
    raise SystemExit(run(argv))


def run(
    argv: Sequence[str] | None = None,
    *,
    stdout: TextIO = sys.stdout,
    stderr: TextIO = sys.stderr,
) -> int:
    parser = _parser()
    arguments = list(sys.argv[1:] if argv is None else argv)
    if not arguments:
        parser.print_help(file=stdout)
        return 0
    args = parser.parse_args(arguments)
    try:
        if args.command == "export":
            api = _api_from_args(args)
            snapshot = Exporter(api).export(args.org)
            output = dump_config(
                snapshot.config,
                comments=args.comments,
                read_only_fields=snapshot.comment_read_only_fields,
                caveats=snapshot.comment_caveats,
            )
            if args.output == "-":
                stdout.write(output)
            else:
                _write_file(Path(args.output), output)
                print(f"Wrote {args.output}", file=stderr)
            if snapshot.unavailable:
                print(
                    f"Warning: {len(snapshot.unavailable)} API sections were not accessible; "
                    "they are listed under _observed.unavailable and are unmanaged.",
                    file=stderr,
                )
            return 0

        desired = load_config(args.config)
        api = _api_from_args(args)
        snapshot = Exporter(api).export(args.org)
        planner = Planner(api, snapshot, args.org, force=args.force)
        operations = planner.plan(desired)
        preflight_operations(
            operations,
            snapshot.ids,
            check_environment=args.command == "apply",
        )
        print_plan(args.org, operations, stream=stdout, color=args.color)
        blocked = [operation for operation in operations if operation.blocked_reason]
        if planner.forced_changes:
            print(
                f"Warning: --force ignored {len(planner.forced_changes)} "
                "read-only or one-way field change(s).",
                file=stderr,
            )
        if args.command == "diff":
            if blocked:
                return 1
            return 2 if operations else 0
        if not operations:
            return 0
        if blocked:
            print(
                f"Refusing to apply: {len(blocked)} planned operation(s) are blocked.",
                file=stderr,
            )
            return 1
        if not args.yes and not _confirm(
            args.org, len(operations), stdin=sys.stdin, stdout=stderr
        ):
            print("No changes applied.", file=stderr)
            return 1
        _apply(api, snapshot.ids, operations, stderr)
        print(f"Applied {len(operations)} API request(s) to {args.org}.", file=stderr)
        return 0
    except (ConfigError, RuntimeError, TypeError) as error:
        print(f"github-config: {error}", file=stderr)
        return 1
    except KeyboardInterrupt:
        print("github-config: interrupted", file=stderr)
        return 130


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="github-config",
        description="Export, compare, and apply GitHub organization configuration.",
        epilog=_AUTHENTICATION_HELP,
    )
    parser.add_argument(
        "--api-url",
        help="GitHub API base URL. Defaults to GH_API_URL, GITHUB_API_URL, or the GH_HOST API.",
    )
    parser.add_argument(
        "--api-version",
        default=os.environ.get("GITHUB_API_VERSION", DEFAULT_API_VERSION),
        help="GitHub REST API version.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser(
        "export",
        help="Export every accessible non-content setting.",
        epilog=_AUTHENTICATION_HELP,
    )
    export.add_argument("org", help="GitHub organization login.")
    export.add_argument(
        "-o",
        "--output",
        default="-",
        help="Output YAML file, or - for standard output.",
    )
    export.add_argument(
        "--comments",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Add an explanation, possible values, and GitHub documentation link before every key (default: enabled).",
    )

    diff = subparsers.add_parser(
        "diff",
        help="Show how a configuration differs from GitHub.",
        epilog=_AUTHENTICATION_HELP,
    )
    _add_plan_arguments(diff)

    apply = subparsers.add_parser(
        "apply",
        help="Apply a configuration to GitHub.",
        epilog=_AUTHENTICATION_HELP,
    )
    _add_plan_arguments(apply)
    apply.add_argument(
        "-y",
        "--yes",
        action="store_true",
        help="Apply without an interactive confirmation.",
    )
    return parser


def _add_plan_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("config", help="github-config YAML file.")
    parser.add_argument("org", help="GitHub organization login.")
    parser.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="Colorize the human-readable diff.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help=(
            "Ignore changed read-only fields and authorize unsafe one-way or "
            "incomplete-state changes."
        ),
    )


def _api_from_args(args: argparse.Namespace) -> GitHubApi:
    api = ambient_api()
    if args.api_url:
        api.api_url = args.api_url.rstrip("/")
    api.api_version = args.api_version
    return api


def _write_file(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", dir=path.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            file.write(value)
        os.replace(temporary_name, path)
    except BaseException:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def _confirm(org: str, count: int, *, stdin: TextIO, stdout: TextIO) -> bool:
    if not stdin.isatty():
        raise RuntimeError("apply needs --yes when standard input is not a terminal")
    print(
        f"Apply {count} API request(s) to {org}? [y/N] ",
        end="",
        file=stdout,
        flush=True,
    )
    return stdin.readline().strip().casefold() in ("y", "yes")


def _apply(
    api: ApiClient,
    ids: dict[tuple[str, ...], int | str],
    operations: Sequence[Operation],
    stderr: TextIO,
) -> None:
    for index, operation in enumerate(operations, start=1):
        print(
            f"[{index}/{len(operations)}] {operation.method} {operation.changes[0].path}",
            file=stderr,
        )
        try:
            operation.execute(api, ids)
        except (RuntimeError, TypeError) as error:
            raise RuntimeError(
                f"apply stopped after {index - 1} of {len(operations)} API requests: {error}"
            ) from None
