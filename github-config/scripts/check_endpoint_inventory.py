from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from github_config.endpoint_inventory import (
    MANAGED_EXACT_PATHS,
    MANAGED_PREFIXES,
    unclassified_configuration_paths,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check github-config's endpoint inventory against GitHub's OpenAPI description."
    )
    parser.add_argument("description", type=Path)
    args = parser.parse_args()
    description: Any = json.loads(args.description.read_text(encoding="utf-8"))
    paths = description.get("paths", {})
    missing = sorted(
        path
        for path in (*MANAGED_PREFIXES, *MANAGED_EXACT_PATHS)
        if path not in paths
        and not any(
            candidate == path or candidate.startswith(f"{path}/") for candidate in paths
        )
    )
    unclassified = sorted(unclassified_configuration_paths(description))
    if missing:
        print("Managed endpoint families missing from OpenAPI:")
        for path in missing:
            print(f"  {path}")
    if unclassified:
        print("Unclassified readable and writable endpoint families:")
        for path in unclassified:
            print(f"  {path}")
    return 1 if missing or unclassified else 0


if __name__ == "__main__":
    raise SystemExit(main())
