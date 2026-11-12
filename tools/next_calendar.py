#!/usr/bin/env python3
"""Check whether the next-season calendar is already present."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--expected-year", type=int, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    present = False
    reason = "missing"
    if args.snapshot.exists():
        try:
            document = json.loads(args.snapshot.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            document = None
        if isinstance(document, dict):
            try:
                found_year = int(document.get("season", -1))
                races = document.get("races")
            except (TypeError, ValueError):
                found_year = -1
                races = None
            if (
                found_year == args.expected_year
                and isinstance(races, list)
                and races
            ):
                present = True
                reason = "already-present"
            else:
                reason = "invalid-snapshot"

    should_collect = args.force or not present
    print(
        f"should_collect={'true' if should_collect else 'false'} "
        f"present={'true' if present else 'false'} reason={reason}"
    )
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            handle.write(
                f"should_collect={'true' if should_collect else 'false'}\n"
            )
            handle.write(f"present={'true' if present else 'false'}\n")
            handle.write(f"reason={reason}\n")


if __name__ == "__main__":
    main()
