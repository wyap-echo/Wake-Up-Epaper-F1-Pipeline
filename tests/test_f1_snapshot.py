#!/usr/bin/env python3
"""Tests for stable snapshot sessions and complete result tables."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR.parent / "tools"))
import f1_snapshot


def source_document() -> dict[str, object]:
    races = []
    for round_number in range(1, 21):
        races.append(
            {
                "round": round_number,
                "country": "Australia",
                "race_name": "Australian Grand Prix",
                "race_start_epoch": 1_800_000_000 + round_number,
                "race_beijing": "03/08 12:00",
                "sessions": [
                    {
                        "code": "r",
                        "name": "Race",
                        "start_epoch": 1_800_000_000,
                        "end_epoch": 1_800_007_200,
                        "result_available": True,
                        "result_complete": True,
                        "result_count": 1,
                    }
                ],
                "podium": None,
                "results": {
                    "r": {
                        "name": "Race",
                        "entries": [
                            {
                                "position": 1,
                                "number": "63",
                                "name": "George Russell",
                                "code": "RUS",
                                "team": "Mercedes",
                                "time": "1:23:06.801",
                                "laps": "58",
                                "points": "25",
                            }
                        ],
                    }
                },
            }
        )
    return {
        "schema": "wake-epaper-f1/1",
        "season": 2026,
        "source": {"season_url": "https://www.formula1.com/en/racing/2026"},
        "race_count": len(races),
        "races": races,
        "results_archive": {
            "url": "https://www.formula1.com/en/results/2026/races",
            "columns": ["Grand Prix", "Winner"],
            "entries": [{"grand_prix": "Australia", "driver": "George Russell RUS"}],
        },
    }


class F1SnapshotTests(unittest.TestCase):
    def test_preserves_sessions_and_result_entries(self) -> None:
        snapshot = f1_snapshot.stable_snapshot(source_document(), 2026)
        self.assertEqual(snapshot["schema"], "wake-epaper-f1-snapshot/2")
        self.assertEqual(snapshot["session_count"], 20)
        self.assertEqual(snapshot["completed_session_count"], 20)
        self.assertEqual(snapshot["result_entry_count"], 20)
        race = snapshot["races"][0]
        self.assertEqual(race["sessions"][0]["end_epoch"], 1_800_007_200)
        self.assertEqual(race["results"]["r"]["entries"][0]["code"], "RUS")


if __name__ == "__main__":
    unittest.main()
