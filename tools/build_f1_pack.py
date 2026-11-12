#!/usr/bin/env python3
"""Build a compact F1P2 schedule/results pack from a stable F1 snapshot."""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import unicodedata
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MAGIC = b"F1P2"
PACK_VERSION = 3
FLAG_HAS_PODIUM = 1 << 0
FLAG_HAS_FULL_RESULTS = 1 << 1

HEADER = struct.Struct("<4sHHHHHHIIII")
RACE_RECORD = struct.Struct(
    "<"
    "BBHiii"          # round, flags, reserved, race/meeting start/end
    "48s24s12s"       # race name, country, Beijing display
    "32s32s"          # circuit short name, location
    "10s6s10s6s"      # length, laps, distance, first Grand Prix
    "12s32s6s"        # fastest lap, driver, year
    "4s24s12s"        # podium 1: code, name, result
    "4s24s12s"        # podium 2
    "4s24s12s"        # podium 3
)


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def ascii_text(value: Any, size: int) -> bytes:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", errors="ignore").decode("ascii")
    return text[: size - 1].encode("ascii")


def parse_utc_epoch(value: Any) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        if len(text) == 10:
            parsed = parsed.replace(tzinfo=timezone.utc)
        else:
            fail(f"UTC timestamp has no timezone: {value!r}")
    return int(parsed.timestamp())


def build_pack(document: dict[str, Any], expected_year: int) -> bytes:
    if document.get("schema") not in {
        "wake-epaper-f1-snapshot/1",
        "wake-epaper-f1-snapshot/2",
    }:
        fail(f"unsupported snapshot schema: {document.get('schema')!r}")
    if int(document.get("season", -1)) != expected_year:
        fail(
            f"season mismatch: expected {expected_year}, "
            f"got {document.get('season')}"
        )
    races = document.get("races")
    if not isinstance(races, list) or not 20 <= len(races) <= 30:
        fail(f"unexpected race count: {len(races) if isinstance(races, list) else '?'}")

    records: list[bytes] = []
    podium_count = 0
    for race in sorted(races, key=lambda item: int(item["round"])):
        round_number = int(race["round"])
        podium = race.get("podium")
        flags = FLAG_HAS_PODIUM if podium else 0
        if podium:
            podium_count += 1
            if len(podium) != 3:
                fail(f"round {round_number} has incomplete podium data")
            podium_fields: list[bytes] = []
            for expected, entry in enumerate(podium, start=1):
                if int(entry.get("position", -1)) != expected:
                    fail(f"round {round_number} podium order is invalid")
                podium_fields.extend(
                    [
                        ascii_text(entry.get("code"), 4),
                        ascii_text(entry.get("name"), 24),
                        ascii_text(entry.get("result"), 12),
                    ]
                )
        else:
            podium_fields = [b""] * 9

        stats = race.get("stats") if isinstance(race.get("stats"), dict) else {}
        records.append(
            RACE_RECORD.pack(
                round_number,
                flags,
                0,
                int(race["race_start_epoch"]),
                parse_utc_epoch(race.get("meeting_start_utc")),
                parse_utc_epoch(race.get("meeting_end_utc")),
                ascii_text(race.get("race_name"), 48),
                ascii_text(race.get("country"), 24),
                ascii_text(race.get("race_beijing"), 12),
                ascii_text(race.get("circuit_short_name"), 32),
                ascii_text(race.get("circuit_location"), 32),
                ascii_text(stats.get("length_km"), 10),
                ascii_text(stats.get("laps"), 6),
                ascii_text(stats.get("distance_km"), 10),
                ascii_text(stats.get("first_grand_prix"), 6),
                ascii_text(stats.get("fastest_lap_time"), 12),
                ascii_text(stats.get("fastest_lap_driver"), 32),
                ascii_text(stats.get("fastest_lap_year"), 6),
                *podium_fields,
            )
        )

    payload = b"".join(records)
    results_races: list[dict[str, Any]] = []
    for race in sorted(races, key=lambda item: int(item["round"])):
        session_results: list[dict[str, Any]] = []
        complete_sessions = {
            str(session.get("code", ""))
            for session in race.get("sessions", [])
            if session.get("result_complete")
        }
        for code, result in sorted(race.get("results", {}).items()):
            if code not in complete_sessions:
                continue
            entries = result.get("entries", [])
            if not entries:
                continue
            session_results.append(
                {
                    "code": code,
                    "name": str(result.get("name", "")),
                    "end_epoch": parse_utc_epoch(
                        next(
                            (
                                session.get("end_utc")
                                for session in race.get("sessions", [])
                                if session.get("code") == code
                            ),
                            "",
                        )
                    ),
                    "entries": [
                        [
                            entry.get("position"),
                            str(entry.get("number", "")),
                            entry.get("code", ""),
                            entry.get("name", ""),
                            entry.get("team", ""),
                            entry.get("time", ""),
                            entry.get("laps", ""),
                            entry.get("points", ""),
                        ]
                        for entry in entries
                    ],
                }
            )
        if session_results:
            results_races.append(
                {"round": int(race["round"]), "sessions": session_results}
            )
    results_document = {
        "schema": "wake-epaper-f1-results/1",
        "season": expected_year,
        "races": results_races,
    }
    results_blob = zlib.compress(
        json.dumps(
            results_document,
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("ascii"),
        9,
    )
    all_payload = payload + results_blob
    published = str(document.get("published_at", ""))
    generated = parse_utc_epoch(published)
    header = HEADER.pack(
        MAGIC,
        PACK_VERSION,
        HEADER.size,
        expected_year,
        len(records),
        RACE_RECORD.size,
        (FLAG_HAS_PODIUM if podium_count else 0)
        | (FLAG_HAS_FULL_RESULTS if results_races else 0),
        len(payload),
        zlib.crc32(all_payload) & 0xFFFFFFFF,
        generated,
        len(results_blob),
    )
    pack = header + all_payload
    return pack


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--expected-year", type=int, required=True)
    args = parser.parse_args()

    try:
        document = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {args.input}: {exc}")
    if not isinstance(document, dict):
        fail("snapshot document must be an object")

    pack = build_pack(document, args.expected_year)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_bytes(pack)
    temporary.replace(args.output)
    print(
        f"wrote {args.output} ({len(pack)} bytes), "
        f"{document['race_count']} races, "
        f"{document['podium_count']} podiums, "
        f"record={RACE_RECORD.size} bytes, "
        f"sha256={hashlib.sha256(pack).hexdigest()}"
    )


if __name__ == "__main__":
    main()

