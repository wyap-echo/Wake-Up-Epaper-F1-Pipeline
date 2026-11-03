#!/usr/bin/env python3
"""Validate and publish a stable F1 data snapshot."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


SCHEMA = "wake-epaper-f1/1"
PUBLISHED_SCHEMA = "wake-epaper-f1-snapshot/1"


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def stable_snapshot(document: dict[str, Any], year: int) -> dict[str, Any]:
    if document.get("schema") != SCHEMA:
        fail(f"unsupported source schema: {document.get('schema')!r}")
    if int(document.get("season", -1)) != year:
        fail(f"season mismatch: expected {year}, got {document.get('season')}")
    source = document.get("source")
    if not isinstance(source, dict) or not source.get("season_url"):
        fail("source.season_url is missing")

    races = document.get("races")
    if not isinstance(races, list) or not 20 <= len(races) <= 30:
        fail(f"unexpected race count: {len(races) if isinstance(races, list) else '?'}")
    if int(document.get("race_count", -1)) != len(races):
        fail("race_count does not match races")

    normalized: list[dict[str, Any]] = []
    rounds: set[int] = set()
    seen_podium_races = 0
    for index, race in enumerate(races):
        if not isinstance(race, dict):
            fail(f"race {index} is not an object")
        try:
            round_number = int(race["round"])
            country = str(race["country"]).strip()
            race_start_epoch = int(race["race_start_epoch"])
            beijing = str(race["race_beijing"]).strip()
            race_name = str(race["race_name"]).strip()
        except (KeyError, TypeError, ValueError) as exc:
            fail(f"race {index} is missing required metadata: {exc}")
        if round_number <= 0 or round_number in rounds:
            fail(f"invalid or duplicate round: {round_number}")
        if not country or not beijing or not race_name:
            fail(f"round {round_number} has empty display metadata")
        rounds.add(round_number)

        podium = race.get("podium")
        if podium is not None:
            if not isinstance(podium, list) or len(podium) != 3:
                fail(f"round {round_number} podium must contain three entries")
            for expected_position, entry in enumerate(podium, start=1):
                if not isinstance(entry, dict):
                    fail(f"round {round_number} podium entry is not an object")
                if int(entry.get("position", -1)) != expected_position:
                    fail(f"round {round_number} podium positions are invalid")
                if not str(entry.get("code", "")).strip():
                    fail(f"round {round_number} podium code is empty")
                if not str(entry.get("result", "")).strip():
                    fail(f"round {round_number} podium result is empty")
            seen_podium_races += 1

        normalized.append(
            {
                "round": round_number,
                "country": country,
                "race_name": race_name,
                "meeting_start_utc": str(race.get("meeting_start_utc", "")),
                "meeting_end_utc": str(race.get("meeting_end_utc", "")),
                "timezone": str(race.get("timezone", "")),
                "gmt_offset": str(race.get("gmt_offset", "")),
                "race_start_epoch": race_start_epoch,
                "race_start_local": str(race.get("race_start_local", "")),
                "race_beijing": beijing,
                "circuit_short_name": str(
                    race.get("circuit_short_name", "")
                ),
                "circuit_location": str(race.get("circuit_location", "")),
                "stats": race.get("stats", {}),
                "podium": podium,
                "winner_time": (
                    str(race.get("winner_time", "")) if podium else ""
                ),
                "source_url": str(race.get("source_url", "")),
            }
        )

    expected_rounds = set(range(1, len(races) + 1))
    if rounds != expected_rounds:
        fail(
            f"rounds are not contiguous: missing={sorted(expected_rounds - rounds)}, "
            f"extra={sorted(rounds - expected_rounds)}"
        )

    return {
        "schema": PUBLISHED_SCHEMA,
        "season": year,
        "source_url": str(source["season_url"]),
        "source_page_sha256": str(source.get("season_page_sha256", "")),
        "race_count": len(normalized),
        "podium_count": seen_podium_races,
        "races": sorted(normalized, key=lambda item: item["round"]),
    }


def comparable(document: dict[str, Any]) -> dict[str, Any]:
    result = dict(document)
    result.pop("published_at", None)
    result.pop("source_page_sha256", None)
    return result


def write_if_changed(path: Path, document: dict[str, Any]) -> bool:
    if path.exists():
        try:
            previous = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = None
        if isinstance(previous, dict) and comparable(previous) == comparable(document):
            return False

    document["published_at"] = (
        dt.datetime.now(dt.timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(document, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--expected-year", type=int, required=True)
    args = parser.parse_args()

    try:
        source = json.loads(args.input.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {args.input}: {exc}")
    if not isinstance(source, dict):
        fail("source document must be an object")

    snapshot = stable_snapshot(source, args.expected_year)
    changed = write_if_changed(args.output, snapshot)
    print(
        f"{'wrote' if changed else 'unchanged'} {args.output} "
        f"({snapshot['race_count']} races, "
        f"{snapshot['podium_count']} podiums)"
    )


if __name__ == "__main__":
    main()

