"""Request validation that pinpoints the offending field.

Every violation is reported as ``{"field": <path>, "message": <why>}`` with
JSON-ish paths such as ``slots[3].commands[1].energy`` so the duty operator
can jump straight to the contradictory value instead of guessing.
"""

from __future__ import annotations

import math
from typing import Any

MIN_SLOTS = 8
MAX_SLOTS = 20
MIN_COMMANDS = 2
MAX_COMMANDS = 5
MAX_COORD = 1_000_000
MAX_COMMAND_ID = 1_000_000_000
MAX_ENERGY = 1_000_000_000
MAX_MODE_LENGTH = 64
MAX_ERRORS = 50

_REGION_KEYS = ("minX", "maxX", "minY", "maxY")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_finite_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def validate_request(data: Any):
    """Validate a decoded JSON body.

    Returns ``(normalized_request, errors)``.  When ``errors`` is non-empty
    the request is rejected and ``normalized_request`` is ``None``.
    """

    errors = []

    def fail(field: str, message: str) -> None:
        if len(errors) < MAX_ERRORS:
            errors.append({"field": field, "message": message})

    if not isinstance(data, dict):
        fail("$", "request body must be a JSON object")
        return None, errors

    _check_vector(data, "initialMomentum", fail)
    _check_region(data, "safeRegion", fail)
    _check_region(data, "targetRegion", fail)
    _check_slots(data, fail)

    if errors:
        return None, errors
    return _normalize(data), []


def _check_vector(parent: dict, key: str, fail, *, field: str = None) -> None:
    field = field or key
    if key not in parent:
        fail(field, "is required")
        return
    value = parent[key]
    if not isinstance(value, list) or len(value) != 2:
        fail(field, "must be an array of exactly two integers")
        return
    for axis, component in enumerate(value):
        if not _is_int(component):
            fail(f"{field}[{axis}]", "must be an integer")
        elif abs(component) > MAX_COORD:
            fail(f"{field}[{axis}]", f"must be within ±{MAX_COORD}")


def _check_region(data: dict, key: str, fail) -> None:
    if key not in data:
        fail(key, "is required")
        return
    region = data[key]
    if not isinstance(region, dict):
        fail(key, "must be an object with minX, maxX, minY, maxY")
        return
    values = {}
    for axis in _REGION_KEYS:
        if axis not in region:
            fail(f"{key}.{axis}", "is required")
            continue
        value = region[axis]
        if not _is_int(value):
            fail(f"{key}.{axis}", "must be an integer")
        elif abs(value) > MAX_COORD:
            fail(f"{key}.{axis}", f"must be within ±{MAX_COORD}")
        else:
            values[axis] = value
    if len(values) == len(_REGION_KEYS):
        if values["minX"] > values["maxX"]:
            fail(
                key,
                f"contradictory bounds: minX ({values['minX']}) "
                f"is greater than maxX ({values['maxX']})",
            )
        if values["minY"] > values["maxY"]:
            fail(
                key,
                f"contradictory bounds: minY ({values['minY']}) "
                f"is greater than maxY ({values['maxY']})",
            )


def _check_slots(data: dict, fail) -> None:
    if "slots" not in data:
        fail("slots", "is required")
        return
    slots = data["slots"]
    if not isinstance(slots, list):
        fail("slots", "must be an array of slot objects")
        return
    if not MIN_SLOTS <= len(slots) <= MAX_SLOTS:
        fail(
            "slots",
            f"must contain between {MIN_SLOTS} and {MAX_SLOTS} slots, "
            f"got {len(slots)}",
        )
    for index, slot in enumerate(slots):
        prefix = f"slots[{index}]"
        if not isinstance(slot, dict):
            fail(prefix, "must be an object with disturbance and commands")
            continue
        _check_vector(slot, "disturbance", fail, field=f"{prefix}.disturbance")
        _check_commands(slot, prefix, fail)


def _check_commands(slot: dict, prefix: str, fail) -> None:
    field = f"{prefix}.commands"
    if "commands" not in slot:
        fail(field, "is required")
        return
    commands = slot["commands"]
    if not isinstance(commands, list):
        fail(field, "must be an array of command objects")
        return
    if not MIN_COMMANDS <= len(commands) <= MAX_COMMANDS:
        fail(
            field,
            f"must contain between {MIN_COMMANDS} and {MAX_COMMANDS} commands, "
            f"got {len(commands)}",
        )
    seen_ids = set()
    for position, command in enumerate(commands):
        cprefix = f"{field}[{position}]"
        if not isinstance(command, dict):
            fail(cprefix, "must be an object with id, mode, correction, energy")
            continue
        if "id" not in command:
            fail(f"{cprefix}.id", "is required")
        else:
            cid = command["id"]
            if not _is_int(cid):
                fail(f"{cprefix}.id", "must be an integer")
            elif abs(cid) > MAX_COMMAND_ID:
                fail(f"{cprefix}.id", f"must be within ±{MAX_COMMAND_ID}")
            elif cid in seen_ids:
                fail(
                    f"{cprefix}.id",
                    f"duplicates command id {cid} already used in this slot",
                )
            else:
                seen_ids.add(cid)
        if "mode" not in command:
            fail(f"{cprefix}.mode", "is required")
        else:
            mode = command["mode"]
            if not isinstance(mode, str) or not mode:
                fail(f"{cprefix}.mode", "must be a non-empty string")
            elif len(mode) > MAX_MODE_LENGTH:
                fail(f"{cprefix}.mode", f"must be at most {MAX_MODE_LENGTH} characters")
        _check_vector(command, "correction", fail, field=f"{cprefix}.correction")
        if "energy" not in command:
            fail(f"{cprefix}.energy", "is required")
        else:
            energy = command["energy"]
            if not _is_finite_number(energy):
                fail(f"{cprefix}.energy", "must be a non-negative number")
            elif energy < 0:
                fail(f"{cprefix}.energy", "must be non-negative")
            elif energy > MAX_ENERGY:
                fail(f"{cprefix}.energy", f"must be at most {MAX_ENERGY}")


def _normalize(data: dict) -> dict:
    """Rebuild a clean request structure from already-validated input."""

    return {
        "initialMomentum": [int(v) for v in data["initialMomentum"]],
        "safeRegion": {k: int(data["safeRegion"][k]) for k in _REGION_KEYS},
        "targetRegion": {k: int(data["targetRegion"][k]) for k in _REGION_KEYS},
        "slots": [
            {
                "disturbance": [int(v) for v in slot["disturbance"]],
                "commands": [
                    {
                        "id": int(command["id"]),
                        "mode": command["mode"],
                        "correction": [int(v) for v in command["correction"]],
                        "energy": command["energy"]
                        if isinstance(command["energy"], int)
                        else float(command["energy"]),
                    }
                    for command in slot["commands"]
                ],
            }
            for slot in data["slots"]
        ],
    }
