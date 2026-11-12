#!/usr/bin/env python3
"""Tests for session-aware collection timing."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR.parent / "tools"))
import collection_window


def snapshot(end_epoch: int, complete: bool = False) -> dict[str, object]:
    return {
        "schema": "wake-epaper-f1-snapshot/2",
        "season": 2026,
        "races": [
            {
                "round": 21,
                "sessions": [
                    {
                        "code": "p1",
                        "name": "Practice 1",
                        "end_epoch": end_epoch,
                        "result_complete": complete,
                    }
                ],
            }
        ],
    }


class CollectionWindowTests(unittest.TestCase):
    def test_waits_for_delay_after_session(self) -> None:
        end = 1_800_000_000
        state = collection_window.session_state(
            snapshot(end), end + 10 * 60, 15, 6
        )
        self.assertFalse(state["should_collect"])
        self.assertEqual(state["reason"], "outside-session-window")

    def test_collects_inside_regular_retry_window(self) -> None:
        end = 1_800_000_000
        state = collection_window.session_state(
            snapshot(end), end + 90 * 60, 15, 6
        )
        self.assertTrue(state["should_collect"])
        self.assertEqual(state["reason"], "after-session")
        self.assertEqual(state["session"], "p1")

    def test_collects_final_retry_after_long_delay(self) -> None:
        end = 1_800_000_000
        state = collection_window.session_state(
            snapshot(end), end + 23 * 3600, 15, 6
        )
        self.assertTrue(state["should_collect"])
        self.assertEqual(state["reason"], "final-retry")

    def test_completed_session_is_skipped(self) -> None:
        end = 1_800_000_000
        state = collection_window.session_state(
            snapshot(end, complete=True), end + 90 * 60, 15, 6
        )
        self.assertFalse(state["should_collect"])

    def test_snapshot_without_sessions_bootstraps(self) -> None:
        state = collection_window.session_state(
            {
                "schema": "wake-epaper-f1-snapshot/1",
                "races": [{"round": 1}],
            },
            1_800_000_000,
            15,
            6,
        )
        self.assertTrue(state["should_collect"])
        self.assertEqual(state["reason"], "bootstrap-sessions")


if __name__ == "__main__":
    unittest.main()
