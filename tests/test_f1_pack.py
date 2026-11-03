#!/usr/bin/env python3
"""Tests for the compact F1P2 binary package."""

from __future__ import annotations

import sys
import unittest
import zlib
from pathlib import Path

TEST_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TEST_DIR.parent / "tools"))
import build_f1_pack


def sample_document() -> dict[str, object]:
    races = []
    for round_number in range(1, 21):
        podium = None
        if round_number == 1:
            podium = [
                {
                    "position": 1,
                    "name": "Russell",
                    "code": "RUS",
                    "result": "1:23:06.801",
                },
                {
                    "position": 2,
                    "name": "Antonelli",
                    "code": "ANT",
                    "result": "+2.974",
                },
                {
                    "position": 3,
                    "name": "Leclerc",
                    "code": "LEC",
                    "result": "+15.519",
                },
            ]
        races.append(
            {
                "round": round_number,
                "country": "Australia",
                "race_name": "Australian Grand Prix",
                "meeting_start_utc": "2026-03-06",
                "meeting_end_utc": "2026-03-08T23:59:59Z",
                "race_start_epoch": 1772982000 + round_number,
                "race_beijing": "03/08 12:00",
                "circuit_short_name": "Melbourne",
                "circuit_location": "Melbourne",
                "stats": {
                    "circuit_location": "Melbourne",
                    "length_km": "5.278",
                    "laps": "58",
                    "distance_km": "306.124",
                    "first_grand_prix": "1996",
                    "fastest_lap_time": "1:19.813",
                    "fastest_lap_driver": "Charles Leclerc",
                    "fastest_lap_year": "2024",
                },
                "podium": podium,
            }
        )
    return {
        "schema": "wake-epaper-f1-snapshot/1",
        "season": 2026,
        "race_count": len(races),
        "podium_count": 1,
        "published_at": "2026-11-03T00:00:00Z",
        "races": races,
    }


class F1PackTests(unittest.TestCase):
    def test_header_and_crc(self) -> None:
        pack = build_f1_pack.build_pack(sample_document(), 2026)
        values = build_f1_pack.HEADER.unpack_from(pack)
        self.assertEqual(values[0], b"F1P2")
        self.assertEqual(values[1], 2)
        self.assertEqual(values[2], build_f1_pack.HEADER.size)
        self.assertEqual(values[3], 2026)
        self.assertEqual(values[4], 20)
        self.assertEqual(values[5], build_f1_pack.RACE_RECORD.size)
        payload = pack[build_f1_pack.HEADER.size :]
        self.assertEqual(zlib.crc32(payload) & 0xFFFFFFFF, values[8])

    def test_first_record_contains_podium(self) -> None:
        pack = build_f1_pack.build_pack(sample_document(), 2026)
        record = build_f1_pack.RACE_RECORD.unpack_from(
            pack, build_f1_pack.HEADER.size
        )
        self.assertEqual(record[0], 1)
        self.assertEqual(record[1] & 1, 1)
        self.assertEqual(record[6].rstrip(b"\0"), b"Australian Grand Prix")
        self.assertEqual(record[7].rstrip(b"\0"), b"Australia")
        self.assertEqual(record[-9].rstrip(b"\0"), b"RUS")
        self.assertEqual(record[-3].rstrip(b"\0"), b"LEC")


if __name__ == "__main__":
    unittest.main()

