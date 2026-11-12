#!/usr/bin/env python3
"""Tests for the next-season calendar stop condition."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
TOOL = TEST_DIR.parent / "tools" / "next_calendar.py"


class NextCalendarTests(unittest.TestCase):
    def invoke(self, path: Path, *extra: str) -> str:
        with tempfile.NamedTemporaryFile(delete=False) as output:
            output_path = Path(output.name)
        self.addCleanup(output_path.unlink, missing_ok=True)
        env = dict(os.environ)
        env["GITHUB_OUTPUT"] = str(output_path)
        completed = subprocess.run(
            [
                sys.executable,
                str(TOOL),
                str(path),
                "--expected-year",
                "2027",
                *extra,
            ],
            env=env,
            check=True,
            text=True,
            capture_output=True,
        )
        return completed.stdout + output_path.read_text(encoding="utf-8")

    def test_existing_next_calendar_stops_collection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "f1-2027.json"
            path.write_text(
                json.dumps({"season": 2027, "races": [{"round": 1}]}),
                encoding="utf-8",
            )
            output = self.invoke(path)
        self.assertIn("should_collect=false", output)
        self.assertIn("reason=already-present", output)

    def test_force_refresh_overrides_existing_calendar(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "f1-2027.json"
            path.write_text(
                json.dumps({"season": 2027, "races": [{"round": 1}]}),
                encoding="utf-8",
            )
            output = self.invoke(path, "--force")
        self.assertIn("should_collect=true", output)

    def test_missing_calendar_is_collected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "f1-2028.json"
            output = self.invoke(path)
        self.assertIn("should_collect=true", output)
        self.assertIn("reason=missing", output)


if __name__ == "__main__":
    unittest.main()
