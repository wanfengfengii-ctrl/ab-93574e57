"""Dynamic-programming compiler for momentum-unload plans.

The optimizer sweeps the window slot by slot and keeps, for every reachable
(momentum, last-mode) pair, the best prefix under the mission's priority
order:

    1. total energy          (sum of the chosen commands' energy)
    2. mode switches         (slots i > 1 whose mode differs from slot i-1)
    3. command-id sequence   (lexicographic comparison)

Because every objective is additive and the comparison is lexicographic, a
single best prefix per (momentum, last-mode) key is sufficient: any common
suffix preserves the ordering of equal-length id sequences, so the dynamic
program is exact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

SAFE_REGION_BLOCKED = "SAFE_REGION_BLOCKED"
TARGET_REGION_UNREACHABLE = "TARGET_REGION_UNREACHABLE"

# Safety guard against pathological instances: maximum number of
# (momentum, mode) frontier entries explored per slot.
MAX_FRONTIER_ENTRIES = 1_000_000


@dataclass(frozen=True)
class Plan:
    """Optimal plan for a feasible request."""

    command_ids: tuple
    modes: tuple
    momenta: tuple  # N+1 entries; momenta[0] is the initial momentum
    total_energy: Any
    mode_switches: int


@dataclass(frozen=True)
class Infeasible:
    """Diagnosis returned when no safe path reaches the target region."""

    reason: str  # SAFE_REGION_BLOCKED | TARGET_REGION_UNREACHABLE
    last_reachable_slot: int
    reachable_state_count: int


class StateSpaceLimitExceeded(RuntimeError):
    """Raised when the reachable frontier grows beyond the safety guard."""

    def __init__(self, slot: int) -> None:
        super().__init__(
            f"reachable state space exceeded the safety limit of "
            f"{MAX_FRONTIER_ENTRIES} entries at slot {slot}"
        )
        self.slot = slot


def _inside(point, rect) -> bool:
    return (
        rect["minX"] <= point[0] <= rect["maxX"]
        and rect["minY"] <= point[1] <= rect["maxY"]
    )


def compile_plan(request: dict):
    """Compile an optimal plan, or explain why none exists.

    ``request`` must already be normalized by ``app.validation``.
    Returns a ``Plan`` on success, an ``Infeasible`` diagnosis otherwise.
    """

    slots = request["slots"]
    safe = request["safeRegion"]
    target = request["targetRegion"]
    initial = (request["initialMomentum"][0], request["initialMomentum"][1])

    # frontier: (momentum, last_mode) -> (energy, switches, id_sequence)
    frontier = {(initial, None): (0, 0, ())}
    parents = []  # one back-pointer table per slot, for reconstruction

    for index, slot in enumerate(slots, start=1):
        dx, dy = slot["disturbance"]
        next_frontier = {}
        next_parents = {}
        for (momentum, last_mode), (energy, switches, ids) in frontier.items():
            base_x = momentum[0] + dx
            base_y = momentum[1] + dy
            for command in slot["commands"]:
                next_momentum = (
                    base_x + command["correction"][0],
                    base_y + command["correction"][1],
                )
                if not _inside(next_momentum, safe):
                    continue
                candidate = (
                    energy + command["energy"],
                    switches
                    + (1 if last_mode is not None and command["mode"] != last_mode else 0),
                    ids + (command["id"],),
                )
                key = (next_momentum, command["mode"])
                current = next_frontier.get(key)
                if current is None or candidate < current:
                    next_frontier[key] = candidate
                    next_parents[key] = ((momentum, last_mode), command)
        if not next_frontier:
            return Infeasible(
                reason=SAFE_REGION_BLOCKED,
                last_reachable_slot=index - 1,
                reachable_state_count=len({state for state, _ in frontier}),
            )
        if len(next_frontier) > MAX_FRONTIER_ENTRIES:
            raise StateSpaceLimitExceeded(index)
        frontier = next_frontier
        parents.append(next_parents)

    finalists = {key: cost for key, cost in frontier.items() if _inside(key[0], target)}
    if not finalists:
        return Infeasible(
            reason=TARGET_REGION_UNREACHABLE,
            last_reachable_slot=len(slots),
            reachable_state_count=len({state for state, _ in frontier}),
        )

    best_key = min(finalists, key=lambda key: finalists[key])
    best_cost = finalists[best_key]

    # Reconstruct the chosen commands and the momentum trajectory.
    chosen = []
    key = best_key
    for slot_parents in reversed(parents):
        previous_key, command = slot_parents[key]
        chosen.append(command)
        key = previous_key
    chosen.reverse()

    momenta = [initial]
    momentum = initial
    for slot, command in zip(slots, chosen):
        momentum = (
            momentum[0] + slot["disturbance"][0] + command["correction"][0],
            momentum[1] + slot["disturbance"][1] + command["correction"][1],
        )
        momenta.append(momentum)

    return Plan(
        command_ids=tuple(command["id"] for command in chosen),
        modes=tuple(command["mode"] for command in chosen),
        momenta=tuple(momenta),
        total_energy=best_cost[0],
        mode_switches=best_cost[1],
    )
