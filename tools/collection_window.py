#!/usr/bin/env python3
"""Decide whether the current F1 session has entered a result window."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any


def fail(message: str) -> None:
    raise SystemExit(f"error: {message}")


def session_state(
    snapshot: dict[str, Any],
    now_epoch: int,
    delay_minutes: int,
    grace_hours: int,
) -> dict[str, Any]:
    races = snapshot.get("races")
    if not isinstance(races, list):
        fail("snapshot has no races")

    incomplete: list[dict[str, Any]] = []
    for race in races:
        if not isinstance(race, dict):
            continue
        sessions = race.get("sessions")
        if not isinstance(sessions, list):
            continue
        for session in sessions:
            if not isinstance(session, dict) or session.get("result_complete"):
                continue
            try:
                end_epoch = int(session["end_epoch"])
            except (KeyError, TypeError, ValueError):
                continue
            incomplete.append(
                {
                    "round": int(race.get("round", 0)),
                    "code": str(session.get("code", "")),
                    "name": str(session.get("name", "")),
                    "end_epoch": end_epoch,
                    "window_start": end_epoch + delay_minutes * 60,
                    "window_end": end_epoch + grace_hours * 3600,
                    "final_end": end_epoch + 24 * 3600,
                }
            )

    if not incomplete and all(
        not isinstance(race.get("sessions"), list) or not race.get("sessions")
        for race in races
        if isinstance(race, dict)
    ):
        return {
            "should_collect": True,
            "reason": "bootstrap-sessions",
            "round": 0,
            "session": "",
            "next_due_epoch": 0,
        }

    due = [
        session
        for session in incomplete
        if session["window_start"] <= now_epoch <= session["window_end"]
    ]
    if not due:
        due = [
            session
            for session in incomplete
            if session["final_end"] - 3600 <= now_epoch <= session["final_end"]
        ]
        if due:
            due[0]["reason"] = "final-retry"

    if due:
        selected = min(due, key=lambda item: item["end_epoch"])
        return {
            "should_collect": True,
            "reason": selected.get("reason", "after-session"),
            "round": selected["round"],
            "session": selected["code"],
            "next_due_epoch": 0,
        }

    future = sorted(
        session["window_start"]
        for session in incomplete
        if session["window_start"] > now_epoch
    )
    return {
        "should_collect": False,
        "reason": "outside-session-window",
        "round": 0,
        "session": "",
        "next_due_epoch": future[0] if future else 0,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--now-epoch", type=int, required=True)
    parser.add_argument("--delay-minutes", type=int, default=15)
    parser.add_argument("--grace-hours", type=int, default=6)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    if args.now_epoch <= 0:
        fail("--now-epoch must be positive")
    if args.delay_minutes < 0:
        fail("--delay-minutes must not be negative")
    if args.grace_hours <= 0:
        fail("--grace-hours must be positive")

    try:
        snapshot = json.loads(args.snapshot.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"cannot read {args.snapshot}: {exc}")
    if not isinstance(snapshot, dict):
        fail("snapshot must be an object")

    state = session_state(
        snapshot,
        args.now_epoch,
        args.delay_minutes,
        args.grace_hours,
    )
    if args.force:
        state["should_collect"] = True
        state["reason"] = "manual-force"
    result = "true" if state["should_collect"] else "false"
    print(
        f"should_collect={result} reason={state['reason']} "
        f"round={state['round']} session={state['session']} "
        f"next_due_epoch={state['next_due_epoch']}"
    )
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with Path(output).open("a", encoding="utf-8") as handle:
            for key, value in state.items():
                if isinstance(value, bool):
                    value = "true" if value else "false"
                handle.write(f"{key}={value}\n")


if __name__ == "__main__":
    main()
