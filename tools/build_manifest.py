#!/usr/bin/env python3
"""Build the device-facing manifest from generated F1P2 packs."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import zlib
from pathlib import Path
from typing import Any

from build_f1_pack import HEADER, MAGIC, RACE_RECORD


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def describe(path: Path, role: str) -> dict[str, Any]:
    data = path.read_bytes()
    if len(data) < HEADER.size:
        fail(f"{path} is too short")
    (
        magic,
        version,
        header_size,
        year,
        record_count,
        record_size,
        flags,
        payload_bytes,
        payload_crc,
        generated,
        _reserved,
    ) = HEADER.unpack_from(data)
    if magic != MAGIC or version not in {2, 3}:
        fail(f"{path} has an unsupported F1 pack header")
    if header_size != HEADER.size or record_size != RACE_RECORD.size:
        fail(f"{path} has incompatible sizes")
    if payload_bytes != record_count * record_size:
        fail(f"{path} payload length does not match its records")
    results_bytes = _reserved if version == 3 else 0
    payload = data[header_size:]
    if len(payload) != payload_bytes + results_bytes:
        fail(f"{path} file length does not match its header")
    if zlib.crc32(payload) & 0xFFFFFFFF != payload_crc:
        fail(f"{path} CRC check failed")
    result_count = 0
    if results_bytes:
        try:
            results_document = json.loads(
                zlib.decompress(payload[payload_bytes:]).decode("ascii")
            )
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            zlib.error,
        ) as exc:
            fail(f"{path} result data is invalid: {exc}")
        if (
            results_document.get("schema") != "wake-epaper-f1-results/1"
            or int(results_document.get("season", -1)) != year
        ):
            fail(f"{path} result data metadata is invalid")
        result_count = sum(
            len(session.get("entries", []))
            for race in results_document.get("races", [])
            for session in race.get("sessions", [])
        )
    return {
        "role": role,
        "year": year,
        "file": path.name,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "records": record_count,
        "has_podium": bool(flags & 1),
        "has_full_results": bool(flags & 2),
        "pack_version": version,
        "result_entry_count": result_count,
        "results_bytes": results_bytes,
        "generated_utc": generated,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current", type=Path, required=True)
    parser.add_argument("--next", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    document = {
        "schema": "wake-epaper-f1-manifest/1",
        "current": describe(args.current, "current"),
        "next": describe(args.next, "next") if args.next else None,
    }
    changed = True
    if args.output.exists():
        try:
            previous = json.loads(args.output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous = None
        if isinstance(previous, dict):
            previous.pop("published_at", None)
            changed = previous != document
    if changed:
        document["published_at"] = (
            dt.datetime.now(dt.timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".tmp")
        temporary.write_text(
            json.dumps(document, indent=2) + "\n",
            encoding="utf-8",
        )
        temporary.replace(args.output)
    print(f"{'wrote' if changed else 'unchanged'} {args.output}")


if __name__ == "__main__":
    main()

