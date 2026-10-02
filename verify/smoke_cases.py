"""Business smoke scenarios submitted by the verify service.

Each feasible scenario is small enough for the brute-force reference to
score exhaustively, so the API's optimum is checked against ground truth
computed independently of the production solver.
"""

from __future__ import annotations

import copy


def _command(cid, mode, correction, energy):
    return {"id": cid, "mode": mode, "correction": list(correction), "energy": energy}


def _slot(disturbance, commands):
    return {"disturbance": list(disturbance), "commands": commands}


def feasible_request():
    """A drifting window where per-slot greedy energy choices leave the safe
    region; only a globally planned window stays feasible.

    Optimum: ids [1,1,1,1,1,1,1,2], energy 36, one mode switch.
    """

    return {
        "initialMomentum": [0, 0],
        "safeRegion": {"minX": -6, "maxX": 6, "minY": -6, "maxY": 6},
        "targetRegion": {"minX": -1, "maxX": 1, "minY": -1, "maxY": 1},
        "slots": [
            _slot(
                (2, 0),
                [
                    _command(1, "ATLAS", (-2, 0), 5),
                    _command(2, "BREEZE", (-1, 0), 1),
                ],
            )
            for _ in range(8)
        ],
    }


def tie_break_request():
    """Every command costs the same energy; ties must be broken by mode
    switches first and then by the command-id sequence.

    Optimum: ids [10]*8, energy 16, zero switches.
    """

    return {
        "initialMomentum": [0, 0],
        "safeRegion": {"minX": -2, "maxX": 2, "minY": -2, "maxY": 2},
        "targetRegion": {"minX": 0, "maxX": 0, "minY": 0, "maxY": 0},
        "slots": [
            _slot(
                (0, 0),
                [
                    _command(10, "ATLAS", (0, 0), 2),
                    _command(11, "BREEZE", (0, 0), 2),
                    _command(12, "ATLAS", (0, 0), 2),
                ],
            )
            for _ in range(8)
        ],
    }


def infeasible_blocked_request():
    """The safe region cuts the window off after slot 3."""

    return {
        "initialMomentum": [0, 0],
        "safeRegion": {"minX": -3, "maxX": 3, "minY": -3, "maxY": 3},
        "targetRegion": {"minX": -3, "maxX": 3, "minY": -3, "maxY": 3},
        "slots": [
            _slot(
                (2, 0),
                [
                    _command(1, "ATLAS", (-1, 0), 1),
                    _command(2, "ATLAS", (-1, 0), 4),
                ],
            )
            for _ in range(8)
        ],
    }


def infeasible_target_request():
    """The window stays safe but can never re-enter the target region."""

    request = infeasible_blocked_request()
    request["safeRegion"] = {"minX": -10, "maxX": 10, "minY": -10, "maxY": 10}
    request["targetRegion"] = {"minX": 0, "maxX": 0, "minY": 0, "maxY": 0}
    return request


def invalid_requests():
    """Contradictory inputs; each must yield HTTP 400 with the field located."""

    cases = []

    too_few = feasible_request()
    too_few["slots"] = too_few["slots"][:7]
    cases.append(("slot count below minimum", too_few, "slots"))

    contradictory = feasible_request()
    contradictory["safeRegion"] = {"minX": 5, "maxX": -5, "minY": -6, "maxY": 6}
    cases.append(("contradictory safe-region bounds", contradictory, "safeRegion"))

    duplicate = feasible_request()
    duplicate["slots"][0]["commands"][1]["id"] = 1
    cases.append(("duplicate command id", duplicate, "slots[0].commands[1].id"))

    negative = feasible_request()
    negative["slots"][0]["commands"][0]["energy"] = -1
    cases.append(("negative energy", negative, "slots[0].commands[0].energy"))

    short_momentum = feasible_request()
    short_momentum["initialMomentum"] = [0]
    cases.append(("initial momentum not 2D", short_momentum, "initialMomentum"))

    float_disturbance = feasible_request()
    float_disturbance["slots"][3]["disturbance"] = [1.5, 0]
    cases.append(
        ("non-integer disturbance", float_disturbance, "slots[3].disturbance[0]")
    )

    # Defensive: never hand callers aliases of shared nested structures.
    return [(name, copy.deepcopy(body), field) for name, body, field in cases]
