#!/usr/bin/env python3
"""Validate and publish a stable F1 data snapshot."""

from __future__ import annotations

import argparse
import datetime as dt
import json
from pathlib import Path
from typing import Any


SCHEMA = "wake-epaper-f1/1"
PUBLISHED_SCHEMA = "wake-epaper-f1-snapshot/2"
SESSION_DURATIONS = {
    "p1": 60 * 60,
    "p2": 60 * 60,
    "p3": 60 * 60,
    "sq": 45 * 60,
    "s": 60 * 60,
    "q": 60 * 60,
    "r": 120 * 60,
}


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def normalize_sessions(
    race: dict[str, Any],
    round_number: int,
) -> list[dict[str, Any]]:
    sessions = race.get("sessions", [])
    if not isinstance(sessions, list):
        fail(f"round {round_number} sessions must be a list")
    normalized: list[dict[str, Any]] = []
    seen_codes: set[str] = set()
    for session in sessions:
        if not isinstance(session, dict):
            fail(f"round {round_number} session is not an object")
        code = str(session.get("code", "")).strip()
        name = str(session.get("name", "")).strip()
        try:
            start_epoch = int(session["start_epoch"])
            end_epoch = int(
                session.get(
                    "end_epoch",
                    start_epoch + SESSION_DURATIONS.get(code, 60 * 60),
                )
            )
        except (KeyError, TypeError, ValueError):
            fail(f"round {round_number} session has invalid times")
        if not code or code in seen_codes:
            fail(f"round {round_number} has an invalid session code")
        if end_epoch <= start_epoch:
            fail(f"round {round_number} session {code} has invalid duration")
        seen_codes.add(code)
        normalized.append(
            {
                "code": code,
                "name": name,
                "start_utc": str(session.get("start_utc", "")),
                "end_utc": str(session.get("end_utc", "")),
                "start_local": str(session.get("start_local", "")),
                "start_local_display": str(
                    session.get("start_local_display", "")
                ),
                "start_epoch": start_epoch,
                "end_epoch": end_epoch,
                "state": str(session.get("state", "")),
                "result_available": bool(
                    session.get("result_available", False)
                ),
                "result_url": str(session.get("result_url", "")),
                "result_count": int(session.get("result_count", 0)),
                "result_complete": bool(
                    session.get("result_complete", False)
                ),
            }
        )
    return sorted(normalized, key=lambda item: item["start_epoch"])


def normalize_results(
    race: dict[str, Any],
    round_number: int,
) -> dict[str, Any]:
    source = race.get("results", {})
    if not isinstance(source, dict):
        fail(f"round {round_number} results must be an object")
    normalized: dict[str, Any] = {}
    for code, result in source.items():
        if not isinstance(result, dict):
            fail(f"round {round_number} result {code} is not an object")
        entries = result.get("entries")
        if not isinstance(entries, list) or not entries:
            fail(f"round {round_number} result {code} has no entries")
        normalized_entries: list[dict[str, Any]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                fail(f"round {round_number} result {code} entry is invalid")
            position = entry.get("position")
            if not isinstance(position, (int, str)):
                fail(
                    f"round {round_number} result {code} "
                    "entry has no position"
                )
            name = str(entry.get("name", "")).strip()
            driver_code = str(entry.get("code", "")).strip()
            if not name:
                fail(
                    f"round {round_number} result {code} "
                    "entry has no driver name"
                )
            normalized_entry = {
                "position": position,
                "number": str(entry.get("number", "")),
                "name": name,
                "code": driver_code,
                "team": str(entry.get("team", "")),
                "time": str(entry.get("time", "")),
                "laps": str(entry.get("laps", "")),
                "points": str(entry.get("points", "")),
            }
            for column in ("q1", "q2", "q3", "retired"):
                if column in entry:
                    normalized_entry[column] = str(entry[column])
            normalized_entries.append(normalized_entry)
        normalized[str(code)] = {
            "name": str(result.get("name", "")),
            "url": str(result.get("url", "")),
            "page_sha256": str(result.get("page_sha256", "")),
            "columns": [
                str(column) for column in result.get("columns", [])
            ],
            "entries": normalized_entries,
        }
    return normalized


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

        sessions = normalize_sessions(race, round_number)
        results = normalize_results(race, round_number)
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
                "sessions": sessions,
                "podium": podium,
                "winner_time": (
                    str(race.get("winner_time", "")) if podium else ""
                ),
                "results": results,
                "source_url": str(race.get("source_url", "")),
            }
        )

    expected_rounds = set(range(1, len(races) + 1))
    if rounds != expected_rounds:
        fail(
            f"rounds are not contiguous: missing={sorted(expected_rounds - rounds)}, "
            f"extra={sorted(rounds - expected_rounds)}"
        )

    archive_source = document.get("results_archive")
    archive_entries: list[dict[str, str]] = []
    archive_columns: list[str] = []
    archive_url = ""
    archive_sha = ""
    if isinstance(archive_source, dict):
        archive_url = str(archive_source.get("url", ""))
        archive_sha = str(archive_source.get("page_sha256", ""))
        archive_columns = [
            str(value) for value in archive_source.get("columns", [])
        ]
        raw_archive_entries = archive_source.get("entries", [])
        if isinstance(raw_archive_entries, list):
            archive_entries = [
                {str(key): str(value) for key, value in entry.items()}
                for entry in raw_archive_entries
                if isinstance(entry, dict)
            ]

    session_count = sum(len(race["sessions"]) for race in normalized)
    completed_session_count = sum(
        bool(session["result_complete"])
        for race in normalized
        for session in race["sessions"]
    )
    result_entry_count = sum(
        len(result["entries"])
        for race in normalized
        for result in race["results"].values()
    )
    return {
        "schema": PUBLISHED_SCHEMA,
        "season": year,
        "source_url": str(source["season_url"]),
        "source_page_sha256": str(source.get("season_page_sha256", "")),
        "race_count": len(normalized),
        "podium_count": seen_podium_races,
        "session_count": session_count,
        "completed_session_count": completed_session_count,
        "result_entry_count": result_entry_count,
        "results_archive": {
            "url": archive_url,
            "page_sha256": archive_sha,
            "columns": archive_columns,
            "entries": archive_entries,
        },
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

