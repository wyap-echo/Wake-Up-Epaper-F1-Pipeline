#!/usr/bin/env python3
"""Regression tests for official F1 calendar parsing."""

from __future__ import annotations

import base64
import json
import sys
import unittest
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR))
sys.path.insert(0, str(TEST_DIR.parent / "tools"))
import f1_collector


COMPLETED_CARD = """
<a class="group" href="/en/racing/2026/australia">
  <span>ROUND 1</span>
  <span>06 - 08 Mar</span>
  <p>Australia</p>
  <span>FORMULA 1 AUSTRALIAN GRAND PRIX 2026</span>
  <div>
    <span><span>1</span><span>st</span></span>
    <span>Russell</span><span>RUS</span><span>1:23:06.801</span>
  </div>
  <div>
    <span><span>2</span><span>nd</span></span>
    <span>Antonelli</span><span>ANT</span><span>+2.974</span>
  </div>
  <div>
    <span><span>3</span><span>rd</span></span>
    <span>Leclerc</span><span>LEC</span><span>+15.519</span>
  </div>
</a>
"""

UPCOMING_CARD = """
<a class="group" href="/en/racing/2027/australia">
  <span>ROUND 3</span>
  <span>02 - 04 Apr</span>
  <p>Australia</p>
  <span>FORMULA 1 AUSTRALIAN GRAND PRIX 2027</span>
</a>
"""

RESULT_TABLE = """
<table>
  <thead><tr>
    <th>Pos.</th><th>No.</th><th>Driver</th><th>Team</th>
    <th>Time / Gap</th><th>Laps</th>
  </tr></thead>
  <tbody>
    <tr><td>1</td><td>16</td><td>Charles Leclerc LEC</td>
      <td>Ferrari</td><td>1:18.128</td><td>29</td></tr>
    <tr><td>2</td><td>12</td><td>Kimi Antonelli ANT</td>
      <td>Mercedes</td><td>+0.659s</td><td>30</td></tr>
  </tbody>
</table>
"""

RACE_PAGE = (
    r'\"text\":\"Practice 1\",\"value\":\"practice?meeting=1300'
    r'\u0026session=1\",\"isSessionResult\":true,\"isAvailable\":true'
    r'<a href="/en/results/2026/races/1300/las-vegas/practice/1">'
    r'<a href="/en/results/2026/races/1300/las-vegas/race-result">'
)


class F1CollectorTests(unittest.TestCase):
    def test_parse_completed_podium(self) -> None:
        podium = f1_collector.parse_podium(COMPLETED_CARD)
        self.assertIsNotNone(podium)
        assert podium is not None
        self.assertEqual(
            [entry["code"] for entry in podium], ["RUS", "ANT", "LEC"]
        )
        self.assertEqual(podium[0]["result"], "1:23:06.801")
        self.assertEqual(podium[2]["result"], "+15.519")

    def test_upcoming_race_has_no_podium(self) -> None:
        self.assertIsNone(f1_collector.parse_podium(UPCOMING_CARD))

    def test_parse_race_card_metadata_and_result(self) -> None:
        context = base64.b64encode(
            json.dumps(
                {
                    "raceName": "FORMULA 1",
                    "trackCountry": "Australia",
                    "trackName": "Melbourne",
                }
            ).encode("utf-8")
        ).decode("ascii")
        html = COMPLETED_CARD.replace(
            '<a class="group"',
            '<a class="group" data-f1rd-a7s-click="event_tile_click" '
            f'data-f1rd-a7s-context="{context}"',
        )
        cards = f1_collector.parse_race_cards(html, 2026)
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0].country, "Australia")
        self.assertEqual(cards[0].track_name, "Melbourne")
        self.assertIsNotNone(cards[0].podium)

    def test_parse_complete_result_table(self) -> None:
        headers, entries = f1_collector.parse_results_table(RESULT_TABLE)
        self.assertEqual(headers[0], "Pos.")
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["position"], 1)
        self.assertEqual(entries[0]["name"], "Charles Leclerc")
        self.assertEqual(entries[0]["code"], "LEC")
        self.assertEqual(entries[0]["time"], "1:18.128")

    def test_result_availability_and_urls(self) -> None:
        urls, availability = f1_collector.result_page_urls(
            RACE_PAGE,
            2026,
            "las-vegas",
        )
        self.assertTrue(availability["p1"])
        self.assertEqual(
            urls["p1"],
            "https://www.formula1.com/en/results/2026/races/"
            "1300/las-vegas/practice/1",
        )
        self.assertEqual(f1_collector.result_session_code("race?meeting=1"), "r")


if __name__ == "__main__":
    unittest.main()
