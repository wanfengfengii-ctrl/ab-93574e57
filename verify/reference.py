"""Independent brute-force reference implementation.

Used by the unit tests and by the verify service to cross-check the
dynamic-programming optimizer.  Deliberately naive: enumerate every command
combination, simulate the window, and rank feasible plans by the mission's
lexicographic objective order.
"""

from __future__ import annotations

from itertools import product


def _inside(point, rect) -> bool:
    return (
        rect["minX"] <= point[0] <= rect["maxX"]
        and rect["minY"] <= point[1] <= rect["maxY"]
    )


def reachable_state_counts(request):
    """Distinct reachable momenta after each slot.

    Returns ``[c0, c1, ..., ck]`` where ``c0 == 1`` (the initial state) and
    ``ci`` is the number of reachable states after slot ``i``.  The list stops
    before the first slot whose reachable set is empty, so the last entry is
    always non-zero.
    """

    frontier = {tuple(request["initialMomentum"])}
    counts = [1]
    for slot in request["slots"]:
        dx, dy = slot["disturbance"]
        next_frontier = set()
        for x, y in frontier:
            for command in slot["commands"]:
                point = (
                    x + dx + command["correction"][0],
                    y + dy + command["correction"][1],
                )
                if _inside(point, request["safeRegion"]):
                    next_frontier.add(point)
        if not next_frontier:
            break
        frontier = next_frontier
        counts.append(len(frontier))
    return counts


def brute_force_optimum(request):
    """Best feasible ``(total_energy, mode_switches, id_sequence)`` or None."""

    best = None
    slots = request["slots"]
    safe = request["safeRegion"]
    target = request["targetRegion"]
    for combo in product(*(slot["commands"] for slot in slots)):
        momentum = tuple(request["initialMomentum"])
        energy = 0
        switches = 0
        ids = []
        previous_mode = None
        feasible = True
        for slot, command in zip(slots, combo):
            momentum = (
                momentum[0] + slot["disturbance"][0] + command["correction"][0],
                momentum[1] + slot["disturbance"][1] + command["correction"][1],
            )
            if not _inside(momentum, safe):
                feasible = False
                break
            energy += command["energy"]
            if previous_mode is not None and command["mode"] != previous_mode:
                switches += 1
            previous_mode = command["mode"]
            ids.append(command["id"])
        if not feasible or not _inside(momentum, target):
            continue
        candidate = (energy, switches, tuple(ids))
        if best is None or candidate < best:
            best = candidate
    return best
