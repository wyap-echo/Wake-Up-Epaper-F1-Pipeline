#!/usr/bin/env python3
"""Collect Formula 1 race metadata and transparent circuit maps.

The tool intentionally uses only the Python standard library. It downloads the
official season page and each race page, caches the source HTML, normalizes the
data, and creates a ZIP suitable for handing to the firmware developer.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import html as html_lib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


BASE_URL = "https://www.formula1.com"
MEDIA_BASE = "https://media.formula1.com/image/upload"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/141.0 Safari/537.36 WakeUpEpaper-F1Collector/1.0"
)
BEIJING = timezone(timedelta(hours=8), name="Asia/Shanghai")
SESSION_DURATIONS = {
    "p1": 60 * 60,
    "p2": 60 * 60,
    "p3": 60 * 60,
    "sq": 45 * 60,
    "s": 60 * 60,
    "q": 60 * 60,
    "r": 120 * 60,
}


@dataclass
class RaceCard:
    round: int
    slug: str
    path: str
    country: str
    track_name: str
    date_label: str
    podium: list[dict[str, Any]] | None = None


class CollectorError(RuntimeError):
    pass


def configure_console() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def clean_text(value: str) -> str:
    value = re.sub(r"<[^>]+>", " ", value)
    return " ".join(html_lib.unescape(value).split())


def header_key(value: str) -> str:
    aliases = {
        "pos.": "position",
        "no.": "number",
        "driver": "driver",
        "team": "team",
        "time / gap": "time",
        "time / retired": "time",
        "laps": "laps",
        "pts.": "points",
        "grand prix": "grand_prix",
        "date": "date",
        "winner": "driver",
    }
    normalized = clean_text(value).lower()
    return aliases.get(normalized, safe_slug(normalized).replace("-", "_"))


def parse_html_table(
    page_html: str,
    required_headers: set[str],
) -> tuple[list[str], list[dict[str, str]]]:
    for table_html in re.findall(r"<table[\s\S]*?</table>", page_html):
        header_cells = [
            clean_text(value)
            for value in re.findall(
                r"<th[^>]*>([\s\S]*?)</th>",
                table_html,
                flags=re.I,
            )
        ]
        if not required_headers.issubset(set(header_cells)):
            continue
        body_match = re.search(
            r"<tbody[^>]*>([\s\S]*?)</tbody>",
            table_html,
            flags=re.I,
        )
        if not body_match:
            continue
        entries: list[dict[str, str]] = []
        for row_html in re.findall(
            r"<tr[^>]*>([\s\S]*?)</tr>",
            body_match.group(1),
            flags=re.I,
        ):
            cells = [
                clean_text(value)
                for value in re.findall(
                    r"<td[^>]*>([\s\S]*?)</td>",
                    row_html,
                    flags=re.I,
                )
            ]
            if len(cells) != len(header_cells):
                continue
            entries.append(
                {
                    header_key(header): value
                    for header, value in zip(header_cells, cells)
                }
            )
        return header_cells, entries
    return [], []


def split_driver(value: str) -> tuple[str, str]:
    text = clean_text(value)
    match = re.search(r"\b([A-Z0-9]{3})\b\s*$", text)
    if not match:
        return text, ""
    return text[: match.start()].strip(), match.group(1)


def parse_results_table(
    page_html: str,
    required_headers: set[str] | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    headers, raw_entries = parse_html_table(
        page_html,
        required_headers or {"Pos.", "Driver"},
    )
    entries: list[dict[str, Any]] = []
    for raw in raw_entries:
        entry: dict[str, Any] = dict(raw)
        if "driver" in entry and "code" not in entry:
            name, code = split_driver(entry["driver"])
            entry["name"] = name
            entry["code"] = code
        if "position" in entry:
            position = entry["position"].strip()
            entry["position"] = int(position) if position.isdigit() else position
        entries.append(entry)
    return headers, entries


def result_session_code(value: str) -> str:
    normalized = html_lib.unescape(value).lower()
    practice = re.search(
        r"(?:/practice/|practice\?[^#]*session=)(\d+)",
        normalized,
    )
    if practice:
        return f"p{practice.group(1)}"
    if "sprint-qualifying" in normalized or "sprint-shootout" in normalized:
        return "sq"
    if "/sprint-results" in normalized or re.search(
        r"(?:/sprint(?:/|$)|^sprint\\?)",
        normalized,
    ):
        return "s"
    if "qualifying" in normalized:
        return "q"
    if "race-result" in normalized or normalized.startswith("race?"):
        return "r"
    return ""


def result_page_urls(
    race_html: str,
    year: int,
    slug: str,
) -> tuple[dict[str, str], dict[str, bool]]:
    urls: dict[str, str] = {}
    for path in re.findall(
        rf"/en/results/{year}/races/[^\"\\\s]+",
        race_html,
    ):
        code = result_session_code(path)
        if code:
            urls.setdefault(code, BASE_URL + path)

    availability: dict[str, bool] = {}
    patterns = (
        r'\\"text\\":\\"([^"]+)\\",\\"value\\":\\"([^"]+)\\",'
        r'\\"isSessionResult\\":true,\\"isAvailable\\":(true|false)',
        r'"text":"([^"]+)","value":"([^"]+)",'
        r'"isSessionResult":true,"isAvailable":(true|false)',
    )
    for pattern in patterns:
        for match in re.finditer(pattern, race_html):
            value = html_lib.unescape(match.group(2))
            code = result_session_code(value)
            if code:
                availability[code] = (
                    availability.get(code, False)
                    or match.group(3).lower() == "true"
                )
    return urls, availability


def collect_meeting_results(
    race_html: str,
    race: dict[str, Any],
    downloader: Downloader,
    year: int,
    previous: dict[str, Any] | None = None,
) -> None:
    urls, availability = result_page_urls(
        race_html,
        year,
        str(race["slug"]),
    )
    previous_results = (
        previous.get("results", {})
        if isinstance(previous, dict)
        and isinstance(previous.get("results"), dict)
        else {}
    )
    previous_sessions = (
        {
            str(session.get("code", "")): session
            for session in previous.get("sessions", [])
            if isinstance(session, dict)
        }
        if isinstance(previous, dict)
        and isinstance(previous.get("sessions"), list)
        else {}
    )
    results: dict[str, Any] = {
        str(code): result
        for code, result in previous_results.items()
        if isinstance(result, dict) and result.get("entries")
    }
    for session in race.get("sessions", []):
        code = str(session.get("code", ""))
        session["result_available"] = bool(availability.get(code, False))
        session["result_url"] = urls.get(code, "")
        session["result_count"] = 0
        session["result_complete"] = False
        previous_session = previous_sessions.get(code, {})
        previous_result = results.get(code)
        if previous_result and previous_session.get("result_complete"):
            session["result_available"] = bool(
                previous_session.get("result_available", True)
            )
            session["result_url"] = str(
                previous_session.get("result_url", session["result_url"])
            )
            session["result_count"] = int(
                previous_session.get("result_count", len(previous_result["entries"]))
            )
            session["result_complete"] = True
            continue
        if not session["result_available"] or not session["result_url"]:
            continue
        path = session["result_url"].split(f"/results/{year}/races/", 1)[-1]
        page = downloader.get(
            session["result_url"],
            Path("results") / year.__str__() / f"{path}.html",
        )
        headers, entries = parse_results_table(
            page.decode("utf-8", errors="replace")
        )
        if not entries:
            continue
        results[code] = {
            "name": str(session.get("name", "")),
            "url": session["result_url"],
            "page_sha256": sha256_bytes(page),
            "columns": headers,
            "entries": entries,
        }
        session["result_count"] = len(entries)
        session["result_complete"] = True

    race["results"] = results
    race_result = results.get("r")
    if race_result and not race.get("podium"):
        podium = []
        for entry in race_result["entries"]:
            position = entry.get("position")
            if isinstance(position, int) and 1 <= position <= 3:
                podium.append(
                    {
                        "position": position,
                        "name": entry.get("name", ""),
                        "code": entry.get("code", ""),
                        "result": entry.get("time", ""),
                    }
                )
        if len(podium) == 3:
            race["podium"] = podium
            race["winner_time"] = str(podium[0]["result"])


def parse_podium(card_html: str) -> list[dict[str, Any]] | None:
    text = clean_text(card_html)
    podium: list[dict[str, Any]] = []
    for position in range(1, 4):
        match = re.search(
            rf"\b{position}\s*(?:st|nd|rd)\b\s+(.+?)\s+"
            r"\b([A-Z]{3})\b\s+(\S+)",
            text,
        )
        if not match:
            return None
        podium.append(
            {
                "position": position,
                "name": match.group(1).strip(),
                "code": match.group(2),
                "result": match.group(3),
            }
        )
    return podium


def safe_slug(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-").lower()
    return value or "race"


def parse_iso(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def parse_offset(value: str) -> timezone:
    match = re.fullmatch(r"([+-])(\d{2}):(\d{2})", value.strip())
    if not match:
        raise ValueError(f"Unsupported GMT offset: {value!r}")
    sign = -1 if match.group(1) == "-" else 1
    delta = timedelta(hours=int(match.group(2)), minutes=int(match.group(3)))
    return timezone(sign * delta)


class Downloader:
    def __init__(self, cache_root: Path, refresh: bool, delay: float) -> None:
        self.cache_root = cache_root
        self.refresh = refresh
        self.delay = max(0.0, delay)
        self.network_requests = 0
        self.cache_hits = 0

    def get(self, url: str, cache_path: Path) -> bytes:
        cache_file = self.cache_root / cache_path
        if cache_file.exists() and not self.refresh:
            self.cache_hits += 1
            print(f"  [cache] {cache_path}")
            return cache_file.read_bytes()

        cache_file.parent.mkdir(parents=True, exist_ok=True)
        last_error: Exception | None = None
        for attempt in range(1, 4):
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,image/svg+xml,*/*",
                    "Accept-Language": "en-GB,en;q=0.9",
                    "Cache-Control": "no-cache",
                },
            )
            try:
                print(f"  [download] {url}")
                with urllib.request.urlopen(request, timeout=45) as response:
                    data = response.read()
                    if not data:
                        raise CollectorError("empty response")
                    cache_file.write_bytes(data)
                    self.network_requests += 1
                    if self.delay:
                        time.sleep(self.delay)
                    return data
            except (
                urllib.error.URLError,
                urllib.error.HTTPError,
                TimeoutError,
                CollectorError,
            ) as exc:
                last_error = exc
                if attempt < 3:
                    wait = attempt * 2
                    print(f"  [retry {attempt}/2] {exc}; waiting {wait}s")
                    time.sleep(wait)
        raise CollectorError(f"Unable to download {url}: {last_error}")


def parse_race_cards(season_html: str, year: int) -> list[RaceCard]:
    cards = re.findall(
        r'<a class="group" data-f1rd-a7s-click="event_tile_click"'
        r"[\s\S]*?</a>",
        season_html,
    )
    races: dict[str, RaceCard] = {}
    for card in cards:
        round_match = re.search(r"ROUND\s+(\d+)", card, flags=re.I)
        context_match = re.search(
            r'data-f1rd-a7s-context="([^"]+)"', card
        )
        path_match = re.search(
            rf'href="(/en/racing/{year}/[^"]+)"', card
        )
        if not (round_match and context_match and path_match):
            continue
        path = path_match.group(1)
        if path in races:
            continue
        try:
            context = json.loads(
                base64.b64decode(context_match.group(1)).decode("utf-8")
            )
        except (ValueError, UnicodeDecodeError):
            context = {}
        date_match = re.search(
            r"(?i)(\d{1,2}\s*-\s*\d{1,2}\s+[A-Z]{3}|"
            r"\d{1,2}\s+[A-Z]{3}\s*-\s*\d{1,2}\s+[A-Z]{3})",
            card,
        )
        slug = path.rsplit("/", 1)[-1]
        races[path] = RaceCard(
            round=int(round_match.group(1)),
            slug=slug,
            path=path,
            country=str(context.get("trackCountry", "")),
            track_name=str(context.get("trackName", "")),
            date_label=date_match.group(1) if date_match else "",
            podium=parse_podium(card),
        )
    if not races:
        raise CollectorError("No championship race cards were found")
    return sorted(races.values(), key=lambda item: item.round)


def parse_json_ld(race_html: str) -> dict[str, Any]:
    blocks = re.findall(
        r'<script[^>]*type="application/ld\+json"[^>]*>'
        r"([\s\S]*?)</script>",
        race_html,
    )
    for block in blocks:
        try:
            value = json.loads(html_lib.unescape(block))
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict) and value.get("subEvent"):
            return value
    raise CollectorError("Race page has no usable JSON-LD schedule")


def escaped_string(race_html: str, key: str) -> str:
    pattern = rf'\\"{re.escape(key)}\\":\\"([^"\\]*)\\"'
    match = re.search(pattern, race_html)
    if not match:
        direct = re.search(rf'"{re.escape(key)}":"([^"\\]*)"', race_html)
        return direct.group(1) if direct else ""
    return match.group(1)


def escaped_object(race_html: str, key: str) -> dict[str, Any]:
    marker = rf'\"{re.escape(key)}\":'
    index = race_html.find(marker)
    if index < 0:
        return {}
    start = race_html.find("{", index + len(marker))
    if start < 0:
        return {}
    end = race_html.find("}", start)
    if end < 0:
        return {}
    fragment = race_html[start : end + 1]
    decoded = fragment.replace('\\"', '"')
    try:
        value = json.loads(decoded)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def parse_statistics(race_html: str) -> dict[str, str]:
    labels = (
        "Circuit Length",
        "First Grand Prix",
        "Number of Laps",
        "Fastest lap time",
        "Race Distance",
    )
    values: dict[str, str] = {}
    for label in labels:
        pattern = (
            rf"<dt[^>]*>{re.escape(label)}</dt>\s*"
            r"<dd[^>]*>([\s\S]*?)</dd>"
        )
        match = re.search(pattern, race_html)
        if match:
            values[label] = clean_text(match.group(1))
    holder = re.search(
        r"Fastest lap time</dt><dd[^>]*>[^<]*</dd>"
        r'<span[^>]*>([^<]+)</span>',
        race_html,
    )
    if holder:
        values["Fastest lap holder"] = clean_text(holder.group(1))
    return values


def session_code(name: str) -> str:
    lowered = name.lower()
    match = re.search(r"practice\s+(\d+)", lowered)
    if match:
        return f"p{match.group(1)}"
    if "sprint qualifying" in lowered or "sprint shootout" in lowered:
        return "sq"
    if "sprint" in lowered:
        return "s"
    if "qualifying" in lowered:
        return "q"
    if lowered.startswith("race"):
        return "r"
    return safe_slug(name)[:12]


def extract_meeting(race_html: str, card: RaceCard, year: int) -> dict[str, Any]:
    event = parse_json_ld(race_html)
    offset_text = escaped_string(race_html, "gmtOffset")
    if not offset_text:
        offset_text = escaped_string(race_html, "meetingGmtOffset")
    local_tz = parse_offset(offset_text) if offset_text else timezone.utc

    sessions: list[dict[str, Any]] = []
    for value in event.get("subEvent", []):
        if not isinstance(value, dict) or not value.get("startDate"):
            continue
        name = str(value.get("name", "")).strip()
        start_utc = parse_iso(str(value["startDate"]))
        end_text = value.get("endDate")
        end_utc = parse_iso(str(end_text)) if end_text else None
        code = session_code(name)
        if end_utc is None:
            end_utc = start_utc + timedelta(
                seconds=SESSION_DURATIONS.get(code, 60 * 60)
            )
        start_local = start_utc.astimezone(local_tz)
        sessions.append(
            {
                "code": code,
                "name": name.split(" - ", 1)[0],
                "start_local": start_local.isoformat(),
                "start_local_display": start_local.strftime("%m/%d %H:%M"),
                "start_utc": start_utc.isoformat().replace("+00:00", "Z"),
                "end_utc": (
                    end_utc.isoformat().replace("+00:00", "Z")
                    if end_utc
                    else ""
                ),
                "start_epoch": int(start_utc.timestamp()),
                "end_epoch": int(end_utc.timestamp()),
                "state": str(value.get("eventStatus", "")),
            }
        )
    sessions.sort(key=lambda item: item["start_epoch"])
    race_session = next(
        (item for item in sessions if item["code"] == "r"), None
    )
    if race_session is None:
        raise CollectorError(f"No race session found for {card.slug}")

    stats = escaped_object(race_html, "trackStats")
    html_stats = parse_statistics(race_html)
    map_info = escaped_object(race_html, "circuitImage")
    public_id = str(map_info.get("public_id", "")).strip()

    race_start_utc = datetime.fromtimestamp(
        race_session["start_epoch"], tz=timezone.utc
    )
    race_local = race_start_utc.astimezone(local_tz)
    race_beijing = race_start_utc.astimezone(BEIJING)
    meeting_start_text = escaped_string(race_html, "meetingStartDate")
    meeting_end_text = escaped_string(race_html, "meetingEndDate")

    track_length = str(stats.get("trackLength", "")).replace(" km", "")
    if not track_length:
        track_length = (
            html_stats.get("Circuit Length", "").replace("km", "").strip()
        )
    laps = str(
        stats.get("scheduledLapCount")
        or escaped_string(race_html, "scheduledLapCount")
        or html_stats.get("Number of Laps", "")
    )
    distance = str(
        stats.get("scheduledDistance")
        or escaped_string(race_html, "scheduledDistance")
        or html_stats.get("Race Distance", "").replace("km", "").strip()
    )

    return {
        "round": card.round,
        "slug": card.slug,
        "country": str(
            stats.get("circuitCountry")
            or card.country
            or ""
        ),
        "race_name": (
            escaped_string(race_html, "meetingName")
            or str(event.get("name", "")).replace("FORMULA 1 ", "").title()
        ),
        "official_name": (
            escaped_string(race_html, "meetingOfficialName")
            or str(event.get("name", ""))
        ),
        "circuit_short_name": str(
            stats.get("circuitShortName")
            or escaped_string(race_html, "circuitShortName")
            or card.track_name
        ),
        "circuit_official_name": str(
            stats.get("circuitOfficialName")
            or escaped_string(race_html, "circuitOfficialName")
            or card.track_name
        ),
        "circuit_location": str(
            stats.get("circuitLocation")
            or escaped_string(race_html, "circuitLocation")
            or ""
        ),
        "meeting_start_utc": meeting_start_text,
        "meeting_end_utc": meeting_end_text,
        "timezone": escaped_string(race_html, "meetingTimezone"),
        "gmt_offset": offset_text,
        "race_start_utc": race_session["start_utc"],
        "race_start_epoch": race_session["start_epoch"],
        "race_start_local": race_local.isoformat(),
        "race_local_date": race_local.strftime("%m/%d"),
        "race_local_time": race_local.strftime("%H:%M"),
        "race_beijing": race_beijing.strftime("%m/%d %H:%M"),
        "sessions": sessions,
        "podium": card.podium,
        "winner_time": (
            str(card.podium[0]["result"]) if card.podium else ""
        ),
        "stats": {
            "length_km": track_length,
            "laps": laps,
            "distance_km": distance,
            "first_grand_prix": str(
                stats.get("venueFirstSeason")
                or html_stats.get("First Grand Prix", "")
            ),
            "fastest_lap_time": str(
                stats.get("fastestLapTime")
                or html_stats.get("Fastest lap time", "")
            ),
            "fastest_lap_driver": str(
                stats.get("fastestLapDriver", "")
                or html_stats.get("Fastest lap holder", "").split("(")[0].strip()
            ),
            "fastest_lap_year": str(
                stats.get("fastestLapSeason", "")
                or (
                    re.search(
                        r"\((\d{4})\)",
                        html_stats.get("Fastest lap holder", ""),
                    ).group(1)
                    if re.search(
                        r"\((\d{4})\)",
                        html_stats.get("Fastest lap holder", ""),
                    )
                    else ""
                )
            ),
            "fastest_lap_team": str(stats.get("fastestLapTeam", "")),
            "direction": str(stats.get("direction", "")),
        },
        "map_public_id": public_id,
        "source_url": BASE_URL + card.path,
        "season": year,
    }


def map_filename(race: dict[str, Any], extension: str) -> str:
    return (
        f"{int(race['round']):02d}-"
        f"{safe_slug(str(race['slug']))}-outline{extension}"
    )


def download_map(
    downloader: Downloader,
    race: dict[str, Any],
    race_dir: Path,
) -> tuple[str, str]:
    public_id = str(race.get("map_public_id", "")).strip()
    if not public_id:
        return "", "official page did not expose circuitImage.public_id"
    encoded_id = "/".join(
        urllib.parse.quote(part, safe="") for part in public_id.split("/")
    )
    svg_url = (
        f"{MEDIA_BASE}/c_lfill,w_1200/v1740000001/"
        f"{encoded_id}.svg"
    )
    filename = map_filename(race, ".svg")
    target = race_dir / "maps" / filename
    try:
        data = downloader.get(
            svg_url, Path("maps") / filename
        )
        if b"<svg" not in data[:500]:
            raise CollectorError("response is not SVG")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        race["map_asset"] = f"maps/{filename}"
        race["map_sha256"] = sha256_bytes(data)
        return str(race["map_asset"]), ""
    except CollectorError as svg_error:
        fallback_id = re.sub(r"blackoutline$", "detailed", public_id)
        fallback_encoded = "/".join(
            urllib.parse.quote(part, safe="")
            for part in fallback_id.split("/")
        )
        fallback_url = (
            f"{MEDIA_BASE}/c_fit,h_704/v1740000001/"
            f"{fallback_encoded}.webp"
        )
        fallback_name = map_filename(race, ".webp")
        fallback_target = race_dir / "maps" / fallback_name
        try:
            data = downloader.get(
                fallback_url,
                Path("maps") / fallback_name,
            )
            fallback_target.parent.mkdir(parents=True, exist_ok=True)
            fallback_target.write_bytes(data)
            race["map_asset"] = f"maps/{fallback_name}"
            race["map_sha256"] = sha256_bytes(data)
            return (
                str(race["map_asset"]),
                f"outline SVG failed ({svg_error}); used detailed WebP",
            )
        except CollectorError as webp_error:
            return "", f"SVG: {svg_error}; WebP: {webp_error}"


def write_summary(
    path: Path,
    year: int,
    races: list[dict[str, Any]],
    warnings: list[str],
    season_url: str,
) -> None:
    lines = [
        f"F1 {year} collector output",
        f"Season source: {season_url}",
        f"Races: {len(races)}",
        "",
        "Round  Race                         Local race time       Beijing time",
        "-----  ---------------------------  --------------------  --------------",
    ]
    for race in races:
        podium = ""
        if race.get("podium"):
            podium = " / ".join(
                f"{entry['code']} {entry['result']}"
                for entry in race["podium"]
            )
        lines.append(
            f"{int(race['round']):>5}  "
            f"{str(race['race_name'])[:27]:<27}  "
            f"{str(race['race_start_local'])[:19]:<20}  "
            f"{str(race['race_beijing'])}"
            + (f"  {podium}" if podium else "")
        )
    if warnings:
        lines.extend(["", "Warnings:"])
        lines.extend(f"- {warning}" for warning in warnings)
    lines.extend(
        [
            "",
            "The XML/HTML source is an official web page, not a public API.",
            "Keep this ZIP as the attachment requested by the firmware project.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_zip(output_dir: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(
        zip_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for path in sorted(output_dir.rglob("*")):
            if path.is_file() and path != zip_path:
                archive.write(path, path.relative_to(output_dir))


def default_output(year: int) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return Path(f"f1-collector-runs/f1-{year}-pack-{stamp}").resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Collect official F1 season data and transparent track maps."
    )
    parser.add_argument("--year", type=int, default=2026)
    parser.add_argument(
        "--output",
        type=Path,
        help="Exact output directory. Default: timestamped f1-collector-runs folder.",
    )
    parser.add_argument(
        "--cache-dir",
        type=Path,
        default=Path("f1-collector-cache"),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore cached pages and download again.",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=0.35,
        help="Delay between network requests in seconds.",
    )
    parser.add_argument(
        "--only-round",
        type=int,
        help="Collect only one round. Useful for testing.",
    )
    parser.add_argument(
        "--skip-results",
        action="store_true",
        help="Collect calendar metadata without result pages.",
    )
    parser.add_argument(
        "--existing",
        type=Path,
        help="Reuse complete session results from an existing snapshot.",
    )
    return parser.parse_args()


def main() -> int:
    configure_console()
    args = parse_args()
    year = int(args.year)
    if year < 2020 or year > 2100:
        raise SystemExit("--year must be between 2020 and 2100")

    output_dir = (
        args.output.resolve() if args.output else default_output(year)
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    cache_dir = args.cache_dir.resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    downloader = Downloader(cache_dir, args.refresh, args.delay)
    season_url = f"{BASE_URL}/en/racing/{year}"

    print(f"Wake-Up E-paper F1 collector: {year}")
    print(f"Season source: {season_url}")
    print(f"Output: {output_dir}")

    existing_races: dict[int, dict[str, Any]] = {}
    if args.existing and args.existing.exists():
        try:
            existing_document = json.loads(
                args.existing.read_text(encoding="utf-8")
            )
            existing_races = {
                int(race["round"]): race
                for race in existing_document.get("races", [])
                if isinstance(race, dict) and "round" in race
            }
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            existing_races = {}

    season_bytes = downloader.get(
        season_url, Path("season") / f"{year}.html"
    )
    season_html = season_bytes.decode("utf-8", errors="replace")
    season_hash = sha256_bytes(season_bytes)
    cards = parse_race_cards(season_html, year)
    if args.only_round is not None:
        cards = [card for card in cards if card.round == args.only_round]
        if not cards:
            raise CollectorError(
                f"Round {args.only_round} was not present on the season page"
            )
    print(f"Found {len(cards)} championship rounds.")

    results_archive: dict[str, Any] | None = None
    if not args.skip_results:
        archive_url = f"{BASE_URL}/en/results/{year}/races"
        archive_bytes = downloader.get(
            archive_url,
            Path("results") / str(year) / "races.html",
        )
        archive_headers, archive_entries = parse_results_table(
            archive_bytes.decode("utf-8", errors="replace"),
            {"Grand Prix", "Winner"},
        )
        results_archive = {
            "url": archive_url,
            "page_sha256": sha256_bytes(archive_bytes),
            "columns": archive_headers,
            "entries": archive_entries,
        }

    races: list[dict[str, Any]] = []
    warnings: list[str] = []
    for card in cards:
        print(
            f"[{card.round:02d}/{len(cards):02d}] "
            f"{card.slug} ({card.track_name or card.country})"
        )
        try:
            race_bytes = downloader.get(
                BASE_URL + card.path,
                Path("races") / f"{year}-{card.round:02d}-{safe_slug(card.slug)}.html",
            )
            race_html = race_bytes.decode("utf-8", errors="replace")
            race = extract_meeting(race_html, card, year)
            race["page_sha256"] = sha256_bytes(race_bytes)
            if not args.skip_results:
                collect_meeting_results(
                    race_html,
                    race,
                    downloader,
                    year,
                    existing_races.get(card.round),
                )
            map_asset, map_warning = download_map(
                downloader, race, output_dir
            )
            if map_warning:
                warnings.append(f"Round {card.round} map: {map_warning}")
            if not map_asset:
                race["map_asset"] = ""
            races.append(race)
            print(
                f"  [ok] {race['race_name']} | "
                f"{race['race_local_date']} {race['race_local_time']} local | "
                f"{map_asset or 'no map'}"
            )
        except Exception as exc:
            warnings.append(f"Round {card.round} failed: {exc}")
            print(f"  [error] {exc}")

    races.sort(key=lambda item: int(item["round"]))
    missing_rounds = sorted(
        set(card.round for card in cards)
        - set(int(race["round"]) for race in races)
    )
    if missing_rounds:
        warnings.append(f"Missing rounds: {missing_rounds}")

    generated_at = datetime.now(timezone.utc).isoformat().replace(
        "+00:00", "Z"
    )
    pack = {
        "schema": "wake-epaper-f1/1",
        "season": year,
        "generated_at": generated_at,
        "source": {
            "season_url": season_url,
            "season_page_sha256": season_hash,
            "note": (
                "Normalized from official Formula 1 web pages. "
                "This is not an official F1 API."
            ),
        },
        "race_count": len(races),
        "races": races,
        "results_archive": results_archive,
        "warnings": warnings,
    }
    season_json = json.dumps(pack, ensure_ascii=False, indent=2) + "\n"
    (output_dir / "season.json").write_text(season_json, encoding="utf-8")
    write_summary(
        output_dir / "summary.txt",
        year,
        races,
        warnings,
        season_url,
    )
    zip_path = output_dir.with_suffix(".zip")
    build_zip(output_dir, zip_path)

    print()
    print(f"Collected: {len(races)} races")
    print(f"Network downloads: {downloader.network_requests}")
    print(f"Cache hits: {downloader.cache_hits}")
    print(f"Attachment ZIP: {zip_path}")
    if warnings:
        print(f"Warnings: {len(warnings)} (see summary.txt)")
    return 0 if races and not missing_rounds else 2


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nCancelled.")
        raise SystemExit(130)
    except Exception as error:
        print(f"\nFAILED: {error}", file=sys.stderr)
        raise SystemExit(1)
